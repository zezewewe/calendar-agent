"""HTTP front end: the same Agent flow the Telegram channel drives, reused
behind the ZeLabs gateway.

The caller is identified by the Cloudflare Access email the gateway forwards
(`X-Auth-Email`), trusted only when the per-route `X-Zelabs-Identity` HMAC
checks out (see `identity.py`). The email picks the user's calendar via
`AgentPool`, so each user only ever proposes/confirms against their own events.

Endpoints:
  GET  /              the tab page (served through the gateway at /apps/calendar/)
  GET  /api/health
  POST /api/propose   {text, attachments:[{mime,data_base64}]} -> Proposal
  POST /api/revise    {proposal_id, instruction}               -> Proposal
  POST /api/confirm   {proposal_id}                            -> {ids:[...]}
  POST /api/cancel    {proposal_id}                            -> {cancelled:bool}
  POST /api/undo      {proposal_id}                            -> {removed:int}
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..identity import HEADER_EMAIL, HEADER_IDENTITY, verify
from ..models import Attachment, Inbound

# Minimal single page served at "/" (the gateway mounts it at /apps/calendar/).
# All calls are relative so the app works both behind the gateway and locally.
_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Calendar</title><style>
:root{color-scheme:dark}body{font:16px/1.4 system-ui,sans-serif;margin:0;padding:20px;background:#0b0d12;color:#e8eaf0}
h1{font-size:20px;margin:0 0 14px}textarea{width:100%;min-height:90px;padding:12px;border-radius:12px;border:1px solid #2a2f3a;background:#12151c;color:inherit;font:inherit}
button{margin:10px 8px 0 0;padding:10px 16px;border:0;border-radius:10px;background:#3b6ef5;color:#fff;font:inherit}
button.ghost{background:#232936}#out{margin-top:16px;white-space:pre-wrap}div.ev{padding:8px 0;border-bottom:1px solid #1d222c}
</style></head><body>
<h1>Calendar</h1>
<textarea id=t placeholder="Forward a note, a poster, or a chat excerpt…"></textarea>
<button id=go>Find events</button>
<div id=out></div>
<script>
const out=document.getElementById('out');
async function api(p,b){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
  const j=await r.json().catch(()=>({error:'HTTP '+r.status}));if(!r.ok)throw new Error(j.error||('HTTP '+r.status));return j;}
function show(p){out.innerHTML='';
  if(!p.events||!p.events.length){out.textContent=(p.questions&&p.questions.length?p.questions:['No events found.']).join('\\n');return;}
  p.events.forEach((e,i)=>{const d=document.createElement('div');d.className='ev';
    d.textContent=(i+1)+'. '+e.title+' — '+e.start+(e.confidence==='low'?' (unsure)':'');out.appendChild(d);});
  const ok=document.createElement('button');ok.textContent='Add to calendar';
  const no=document.createElement('button');no.className='ghost';no.textContent='Cancel';out.append(ok,no);
  ok.onclick=async()=>{try{const r=await api('api/confirm',{proposal_id:p.id});out.textContent='Added '+r.ids.length+' event(s).';
    const un=document.createElement('button');un.className='ghost';un.textContent='Undo';
    un.onclick=async()=>{const u=await api('api/undo',{proposal_id:p.id});out.textContent='Removed '+u.removed+' event(s).';};out.appendChild(un);
  }catch(e){out.textContent='Failed: '+e.message;}};
  no.onclick=async()=>{await api('api/cancel',{proposal_id:p.id});out.textContent='Cancelled.';};}
document.getElementById('go').onclick=async()=>{out.textContent='Reading…';
  try{show(await api('api/propose',{text:document.getElementById('t').value}));}catch(e){out.textContent='Failed: '+e.message;}};
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    pool = None
    tz = None
    route_id = "calendar"
    master_key: str | None = None
    insecure = False

    def log_message(self, *args) -> None:  # keep the channel quiet; the gateway logs
        pass

    # -- helpers -----------------------------------------------------------------
    def _send(self, code: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _email(self) -> str | None:
        email = self.headers.get(HEADER_EMAIL)
        if self.insecure:  # local dev only; the gateway is the real boundary
            return email or "local@dev"
        if not email:
            return None
        return email if verify(self.route_id, self.headers.get(HEADER_IDENTITY), self.master_key) else None

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    # -- routes ------------------------------------------------------------------
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/health":
            self._send(200, {"ok": True})
        elif path in ("/", "/index.html"):
            self._send_html(_PAGE)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        email = self._email()
        if email is None:
            self._send(401, {"error": f"unauthenticated: need {HEADER_EMAIL} + valid {HEADER_IDENTITY}"})
            return
        try:
            body = self._body()
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "invalid JSON body"})
            return
        agent = self.pool.for_user(email)
        try:
            self._dispatch(path, agent, email, body)
        except KeyError as e:
            self._send(400, {"error": f"missing field {e}"})
        except Exception as e:  # noqa: BLE001 - surface the failure to the client
            self._send(500, {"error": str(e)})

    def _dispatch(self, path: str, agent, email: str, body: dict) -> None:
        pid = body.get("proposal_id")
        if path == "/api/propose":
            atts = [Attachment(mime=a["mime"], data=base64.b64decode(a["data_base64"])) for a in body.get("attachments", [])]
            inbound = Inbound(user_id=email, text=body.get("text", ""), attachments=atts, received_at=datetime.now(self.tz))
            self._send(200, agent.propose(inbound).model_dump(mode="json"))
        elif path == "/api/revise":
            p = agent.revise(pid, email, body["instruction"], datetime.now(self.tz))
            self._send(200, p.model_dump(mode="json")) if p else self._send(409, {"error": "proposal gone or not yours"})
        elif path == "/api/confirm":
            ids = agent.confirm(pid, email)
            self._send(200, {"ids": ids}) if ids is not None else self._send(409, {"error": "proposal gone or not yours"})
        elif path == "/api/cancel":
            self._send(200, {"cancelled": agent.cancel(pid, email)})
        elif path == "/api/undo":
            self._send(200, {"removed": agent.undo(pid, email)})
        else:
            self._send(404, {"error": "not found"})


def make_server(pool, tz, route_id="calendar", master_key=None, insecure=False, host="127.0.0.1", port=0) -> ThreadingHTTPServer:
    """Build (but do not start) the server. `port=0` picks a free port (tests)."""
    handler = type(
        "Handler",
        (Handler,),
        {"pool": pool, "tz": tz, "route_id": route_id, "master_key": master_key, "insecure": insecure},
    )
    return ThreadingHTTPServer((host, port), handler)


def run(pool, tz, route_id="calendar", master_key=None, insecure=False, host="127.0.0.1", port=8099) -> None:
    server = make_server(pool, tz, route_id, master_key, insecure, host, port)
    server.serve_forever()
