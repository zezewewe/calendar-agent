from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from calendar_agent.agent import Agent
from calendar_agent.extract import Extraction
from calendar_agent.mcp import _handlers, handle
from calendar_agent.models import ProposedEvent
from calendar_agent.store import Store

TZ = ZoneInfo("Asia/Singapore")


class Ext:
    def extract(self, inbound, known=None):
        return Extraction(events=[ProposedEvent(title="Lunch", start=datetime(2026, 11, 5, 13, 0, tzinfo=TZ))])

    def plan(self, instruction, existing, now):
        return Extraction()

    def summarise(self, instruction, events, now):
        return "ok"

    def revise(self, current, instruction, now):
        return Extraction()


class Cal:
    def __init__(self):
        self.existing, self.created, self.deleted = [], [], []

    def list_events(self, s, e):
        return self.existing

    def create_event(self, event, end, user_id=None):
        self.created.append(event)
        return f"id{len(self.created)}"

    def update_event(self, event_id, event, end, user_id=None):
        self.deleted.append(event_id)
        return self.create_event(event, end, user_id)

    def delete_event(self, i):
        self.deleted.append(i)


def make():
    cal = Cal()
    return Agent(Ext(), cal, Store(":memory:"), 60), cal


def _call(handlers, name, arguments):
    return handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": name, "arguments": arguments}}, handlers)


def test_initialize_and_tools_list():
    handlers = _handlers(make()[0], "mcp", TZ)
    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, handlers)
    assert init["result"]["serverInfo"]["name"] == "calendar-agent"
    tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, handlers)["result"]["tools"]
    assert {t["name"] for t in tools} == {"agenda", "list_events", "propose_change", "confirm", "undo"}


def test_propose_confirm_undo_roundtrip_nothing_written_until_confirm():
    agent, cal = make()
    handlers = _handlers(agent, "mcp", TZ)

    text = _call(handlers, "propose_change", {"text": "lunch thursday"})["result"]["content"][0]["text"]
    assert "Lunch" in text and cal.created == []
    proposal_id = text.split("proposal_id: ")[1].strip()

    assert _call(handlers, "confirm", {"proposal_id": proposal_id})["result"]["content"][0]["text"].startswith("Applied")
    assert len(cal.created) == 1
    assert _call(handlers, "undo", {"proposal_id": proposal_id})["result"]["content"][0]["text"] == "Reverted 1 change(s)."


def test_unknown_tool_returns_an_error():
    handlers = _handlers(make()[0], "mcp", TZ)
    assert _call(handlers, "nope", {})["error"]["code"] == -32602
