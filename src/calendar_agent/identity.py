"""Trust the Cloudflare Access identity the ZeLabs gateway forwards.

Cloudflare Access answers *who is this*; the gateway verifies the edge JWT and
forwards `X-Auth-Email` / `X-Auth-User` to upstreams. Because anything that can
reach an upstream's port could forge those headers, the gateway also forwards a
per-route secret, `X-Zelabs-Identity` = HMAC-SHA256(master, "route:" + id). An
app knows only its own route id and the master key, so it can check that a
request really came through the gateway before trusting the email.

Mirrors `zelabs/src/identity.ts` exactly. The master key is `ZELABS_IDENTITY_KEY`
or the generated 0600 file (default `~/.ows/zelabs.identity.key`).
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

HEADER_EMAIL = "X-Auth-Email"
HEADER_IDENTITY = "X-Zelabs-Identity"


def load_master_key(env: os._Environ | dict[str, str] | None = None) -> str | None:
    e = os.environ if env is None else env
    direct = e.get("ZELABS_IDENTITY_KEY")
    if direct:
        return direct
    path = e.get("ZELABS_KEY_FILE") or str(Path(e.get("HOME") or Path.home()) / ".ows" / "zelabs.identity.key")
    try:
        key = Path(path).read_text().strip()
    except OSError:
        return None
    return key or None


def route_secret(master_key: str, route_id: str) -> str:
    return hmac.new(master_key.encode(), f"route:{route_id}".encode(), hashlib.sha256).hexdigest()


def verify(route_id: str, provided: str | None, master_key: str | None) -> bool:
    """Constant-time check that `provided` is this route's gateway secret."""
    if not master_key:
        return False
    return hmac.compare_digest(route_secret(master_key, route_id), (provided or "").strip())
