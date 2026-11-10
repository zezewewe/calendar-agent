"""HTTP channel: gateway identity, per-caller dispatch, health and the tab page."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from calendar_agent.channels import http
from calendar_agent.extract import Extraction
from calendar_agent.identity import route_secret, verify
from calendar_agent.models import ProposedEvent
from calendar_agent.pool import AgentPool
from calendar_agent.store import Store

TZ = ZoneInfo("Asia/Singapore")


class FakeCal:
    def __init__(self, existing=()):
        self.existing, self.created, self.deleted = list(existing), [], []

    def list_events(self, s, e):
        return self.existing

    def create_event(self, event, end, user_id=None):
        self.created.append((event, end))
        return f"id{len(self.created)}"

    def update_event(self, event_id, event, end, user_id=None):
        self.deleted.append(event_id)
        return self.create_event(event, end, user_id)

    def delete_event(self, i):
        self.deleted.append(i)


class FakeCalendarFactory:
    def __init__(self):
        self._cals: dict[str, FakeCal] = {}

    def for_user(self, email):
        return self._cals.setdefault(email.lower(), FakeCal())


class FakeExtractor:
    def extract(self, inbound, known=None):
        return Extraction(events=[ProposedEvent(title="Lunch", start=datetime(2026, 11, 5, 13, 0, tzinfo=TZ), confidence="high")])

    def revise(self, current, instruction, now):
        return self.extract(None)

    def summarise(self, instruction, events, now):
        return ""


def test_identity_verifies_only_the_route_secret():
    master = "s3cret-master"
    good = route_secret(master, "calendar")
    assert verify("calendar", good, master)
    assert not verify("calendar", route_secret(master, "other"), master)  # another route's secret
    assert not verify("calendar", "nonsense", master)
    assert not verify("calendar", good, None)  # no master key configured


def _post(port, path, body, email=None, secret=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    if email:
        req.add_header("X-Auth-Email", email)
    if secret:
        req.add_header("X-Zelabs-Identity", secret)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _serve(pool, master):
    srv = http.make_server(pool, TZ, "calendar", master, insecure=False, port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def test_http_rejects_without_gateway_identity():
    _, port = _serve(AgentPool(FakeExtractor(), FakeCalendarFactory(), Store(":memory:")), "master")
    assert _post(port, "/api/propose", {"text": "hi"})[0] == 401
    assert _post(port, "/api/propose", {"text": "hi"}, email="a@example.com", secret="wrong")[0] == 401


def test_http_keeps_users_isolated():
    master = "master"
    secret = route_secret(master, "calendar")
    factory = FakeCalendarFactory()
    pool = AgentPool(FakeExtractor(), factory, Store(":memory:"), 60)
    _, port = _serve(pool, master)

    st, alice = _post(port, "/api/propose", {"text": "lunch fri 1pm"}, email="alice@example.com", secret=secret)
    assert st == 200 and alice["events"]
    assert _post(port, "/api/confirm", {"proposal_id": alice["id"]}, email="alice@example.com", secret=secret)[0] == 200

    st, bob = _post(port, "/api/propose", {"text": "coffee tue 3pm"}, email="bob@example.com", secret=secret)
    assert st == 200
    assert _post(port, "/api/confirm", {"proposal_id": bob["id"]}, email="bob@example.com", secret=secret)[0] == 200

    assert len(factory.for_user("alice@example.com").created) == 1
    assert len(factory.for_user("bob@example.com").created) == 1

    assert _post(port, "/api/confirm", {"proposal_id": bob["id"]}, email="alice@example.com", secret=secret)[0] == 409
    st, res = _post(port, "/api/undo", {"proposal_id": bob["id"]}, email="alice@example.com", secret=secret)
    assert st == 200 and res["removed"] == 0
    assert not factory.for_user("bob@example.com").deleted
    assert _post(port, "/api/undo", {"proposal_id": bob["id"]}, email="bob@example.com", secret=secret)[1] == {"removed": 1}


def test_http_health_needs_no_identity():
    _, port = _serve(AgentPool(FakeExtractor(), FakeCalendarFactory(), Store(":memory:")), "master")
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health") as r:
        assert json.loads(r.read()) == {"ok": True}


def test_http_serves_the_tab_page():
    _, port = _serve(AgentPool(FakeExtractor(), FakeCalendarFactory(), Store(":memory:")), "master")
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
        assert r.status == 200
        assert r.headers["Content-Type"].startswith("text/html")
        assert b"<h1>Calendar</h1>" in r.read()
