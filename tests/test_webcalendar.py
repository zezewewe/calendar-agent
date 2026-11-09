from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from calendar_agent.agent import Agent
from calendar_agent.calendars import SharedCalendarFactory
from calendar_agent.calendars.webcalendar import WebCalendarCalendar
from calendar_agent.extract import Extraction
from calendar_agent.models import Inbound, ProposedEvent
from calendar_agent.store import Store

TZ = ZoneInfo("Asia/Singapore")
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=TZ)


class FakeMcp:
    def __init__(self, **responses):
        self.responses = responses
        self.calls = []

    def __call__(self, payload):
        params = payload["params"]
        self.calls.append((params["name"], params["arguments"]))
        response = self.responses[params["name"]]
        return {"jsonrpc": "2.0", "id": payload["id"], "result": response}


def calendar(mcp):
    return WebCalendarCalendar("http://cal.test/mcp.php", "secret", TZ, {"u1": "Alice"}, transport=mcp)


def test_create_event_uses_gmt_and_tags_the_confirmed_member():
    mcp = FakeMcp(add_event={"success": True, "event_id": 42})
    event = ProposedEvent(
        title="Dinner",
        start=datetime(2026, 11, 5, 19, 0, tzinfo=TZ),
        location="Home",
        notes="Bring dessert",
    )
    event_id = calendar(mcp).create_event(event, datetime(2026, 11, 5, 20, 30, tzinfo=TZ), user_id="u1")

    assert event_id == "42"
    assert mcp.calls == [("add_event", {
        "name": "Dinner",
        "date": "20261105",
        "time": "110000",  # 19:00 at UTC+8
        "duration": 90,
        "description": "Bring dessert\n\nFamily member: Alice [family-member:u1]",
        "location": "Home",
    })]


def test_all_day_event_stays_on_its_local_date():
    mcp = FakeMcp(add_event={"success": True, "event_id": 7})
    event = ProposedEvent(title="Holiday", start=datetime(2026, 11, 5, 0, 0, tzinfo=TZ), all_day=True)
    calendar(mcp).create_event(event, datetime(2026, 11, 6, 0, 0, tzinfo=TZ), user_id="u1")

    _, arguments = mcp.calls[0]
    assert arguments["date"] == "20261105"
    assert arguments["time"] == "-1"
    assert arguments["duration"] == 0


def test_list_events_parses_timed_and_untimed_entries():
    mcp = FakeMcp(list_events={"events": [
        {"id": 1, "name": "School", "date": "20261105", "time": 90000, "duration": 60},
        {"id": 2, "name": "Holiday", "date": "20261106", "time": "-1", "duration": 0},
    ]})
    cal = calendar(mcp)
    events = cal.list_events(datetime(2026, 11, 5, tzinfo=TZ), datetime(2026, 11, 7, tzinfo=TZ))

    assert mcp.calls[0][1] == {"start_date": "20261105", "end_date": "20261107"}
    assert events[0].id == "1" and events[0].start.hour == 9
    assert (events[0].end - events[0].start).total_seconds() == 3600
    assert events[1].start == datetime(2026, 11, 6, tzinfo=TZ)
    assert (events[1].end - events[1].start).days == 1


def test_delete_event_and_tool_errors():
    mcp = FakeMcp(delete_event={"success": True, "event_id": 9})
    calendar(mcp).delete_event("9")
    assert mcp.calls[-1] == ("delete_event", {"event_id": 9})

    with pytest.raises(ValueError, match="must be numeric"):
        calendar(mcp).delete_event("not-numeric")
    mcp.responses["delete_event"] = {"error": "Not authorized"}
    with pytest.raises(RuntimeError, match="Not authorized"):
        calendar(mcp).delete_event("9")


class StaticExtractor:
    def extract(self, inbound):
        return Extraction(events=[ProposedEvent(title="Lunch", start=datetime(2026, 11, 5, 13, 0, tzinfo=TZ))])


def test_agent_confirmation_passes_recognized_caller_to_shared_calendar():
    mcp = FakeMcp(list_events={"events": []}, add_event={"success": True, "event_id": 11})
    cal = calendar(mcp)
    agent = Agent(StaticExtractor(), cal, Store(":memory:"), 60)
    proposal = agent.propose(Inbound(user_id="u1", text="lunch", received_at=NOW))

    assert agent.confirm(proposal.id, "u1") == ["11"]
    assert "[family-member:u1]" in mcp.calls[-1][1]["description"]
    assert SharedCalendarFactory(cal).for_user("another-user") is cal
