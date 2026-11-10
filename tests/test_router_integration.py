"""Live integration test against a running model-router.

Opt-in: it makes real network calls, so it is skipped unless a router is
configured. Either export the variables, or create a `.env` in the repo root
(copied from `.env.example`):

    set -a; . ./.env; set +a; pytest tests/test_router_integration.py -v

The unit tests in `test_core.py` stay offline; this file is the only one that
reaches out to the model-router.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from calendar_agent.config import Config
from calendar_agent.extract import RouterExtractor
from calendar_agent.models import ExistingEvent, Inbound

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_env_file() -> None:
    if os.environ.get("ROUTER_API_KEY") or not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file()

pytestmark = pytest.mark.skipif(
    not (os.environ.get("ROUTER_URL") and os.environ.get("ROUTER_API_KEY")),
    reason="live model-router not configured (set ROUTER_URL and ROUTER_API_KEY, or add .env)",
)


def test_extracts_event_from_text_via_live_router():
    from openai import APIConnectionError, APITimeoutError, OpenAI

    cfg = Config.from_env()
    # A bounded client: the configured model may be a slow/flaky free route, and
    # the extractor's default client has no timeout, which would hang pytest.
    client = OpenAI(base_url=cfg.router_url, api_key=cfg.router_api_key, timeout=30)
    extractor = RouterExtractor(cfg.router_url, cfg.router_api_key, cfg.extract_model, cfg.tz, client=client)
    now = datetime(2026, 11, 1, 12, 0, tzinfo=cfg.tz)  # a Sunday
    try:
        ext = extractor.extract(Inbound(user_id="t", text="Dentist next Friday 3pm", received_at=now))
    except (APITimeoutError, APIConnectionError) as e:
        pytest.skip(f"model-router/model {cfg.extract_model!r} did not answer: {e}")

    assert ext.events, f"router returned no events (questions: {ext.questions})"
    event = ext.events[0]
    assert event.start.tzinfo is not None  # local zone attached by the code, not the model
    assert event.start.weekday() == 4  # Friday
    assert event.start.hour == 15
    assert "dentist" in event.title.lower()


def _extractor():
    from openai import OpenAI

    cfg = Config.from_env()
    return cfg, RouterExtractor(cfg.router_url, cfg.router_api_key, cfg.extract_model, cfg.tz,
                                client=OpenAI(base_url=cfg.router_url, api_key=cfg.router_api_key, timeout=30))


def test_change_request_is_not_classified_as_a_create():
    from openai import APIConnectionError, APITimeoutError

    cfg, ex = _extractor()
    try:
        ext = ex.extract(Inbound(user_id="t", text="move my Saturday gym session to 8am", received_at=datetime(2026, 11, 1, 12, 0, tzinfo=cfg.tz)))
    except (APITimeoutError, APIConnectionError) as e:
        pytest.skip(f"model-router did not answer: {e}")
    assert ext.intent in ("update", "delete"), f"a change should not be intent=create (got {ext.intent})"


def test_plan_attaches_a_change_to_the_real_event_id():
    from openai import APIConnectionError, APITimeoutError

    cfg, ex = _extractor()
    gym = ExistingEvent(id="42", title="Gym", start=datetime(2026, 11, 7, 9, 0, tzinfo=cfg.tz), end=datetime(2026, 11, 7, 10, 0, tzinfo=cfg.tz))
    try:
        ext = ex.plan("move the gym to 8am", [gym], datetime(2026, 11, 1, 12, 0, tzinfo=cfg.tz))
    except (APITimeoutError, APIConnectionError) as e:
        pytest.skip(f"model-router did not answer: {e}")
    assert ext.events, f"router returned no change (questions: {ext.questions})"
    assert ext.events[0].target_id == "42" and ext.events[0].action == "update"
