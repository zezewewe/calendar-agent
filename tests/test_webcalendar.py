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
    def __init__(self, tools=(), **responses):
        self.tools = set(tools)
        self.responses = responses
        self.calls = []

    def __call__(self, payload):
        if payload.get("method") == "tools/list":
            self.calls.append(("tools/list", {}))
            return {"jsonrpc": "2.0", "id": payload["id"],
                    "result": {"tools": [{"name": n} for n in sorted(self.tools)]}}
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


def test_update_falls_back_to_delete_and_add_when_no_update_tool():
    mcp = FakeMcp(delete_event={"success": True, "event_id": 9}, add_event={"success": True, "event_id": 10})
    cal = calendar(mcp)
    event = ProposedEvent(title="Dinner", start=datetime(2026, 11, 5, 19, 0, tzinfo=TZ))
    new_id = cal.update_event("9", event, datetime(2026, 11, 5, 20, 0, tzinfo=TZ), user_id="u1")

    assert new_id == "10"
    assert [name for name, _ in mcp.calls] == ["tools/list", "delete_event", "add_event"]


def test_update_uses_the_update_tool_when_the_server_has_one():
    mcp = FakeMcp(tools=["update_event"], update_event={"success": True, "event_id": 9})
    cal = calendar(mcp)
    event = ProposedEvent(title="Dinner", start=datetime(2026, 11, 5, 19, 0, tzinfo=TZ))
    assert cal.update_event("9", event, datetime(2026, 11, 5, 20, 0, tzinfo=TZ), user_id="u1") == "9"
    assert [name for name, _ in mcp.calls] == ["tools/list", "update_event"]
    assert mcp.calls[-1][1]["event_id"] == 9


class StaticExtractor:
    def extract(self, inbound, known=None):
        return Extraction(events=[ProposedEvent(title="Lunch", start=datetime(2026, 11, 5, 13, 0, tzinfo=TZ))])


def test_recurring_event_uses_add_recurring_event():
    mcp = FakeMcp(add_recurring_event={"success": True, "event_id": 55})
    event = ProposedEvent(title="Class", start=datetime(2026, 11, 7, 10, 0, tzinfo=TZ), rrule="FREQ=WEEKLY;BYDAY=SA;UNTIL=20261130")
    assert calendar(mcp).create_event(event, datetime(2026, 11, 7, 11, 0, tzinfo=TZ), user_id="u1") == "55"
    name, arguments = mcp.calls[0]
    assert name == "add_recurring_event" and arguments["rrule"] == "FREQ=WEEKLY;BYDAY=SA;UNTIL=20261130"


def test_find_conflicts_uses_the_backend_check():
    mcp = FakeMcp(check_conflicts={"conflicts": [{"id": 3, "name": "Gym", "date": "20261107", "time": 90000, "duration": 60}]})
    hits = calendar(mcp).find_conflicts(datetime(2026, 11, 7, 10, 0, tzinfo=TZ), datetime(2026, 11, 7, 11, 0, tzinfo=TZ))
    assert [h.id for h in hits] == ["3"]
    assert mcp.calls[0][0] == "check_conflicts"


def test_busy_blocks_use_get_availability():
    mcp = FakeMcp(get_availability={"busy": [{"id": 4, "name": "Work", "date": "20261107", "time": 90000, "duration": 480}]})
    blocks = calendar(mcp).busy_blocks(datetime(2026, 11, 7, tzinfo=TZ), datetime(2026, 11, 8, tzinfo=TZ))
    assert [b.id for b in blocks] == ["4"]
    assert mcp.calls[0][0] == "get_availability"


def test_agent_confirmation_passes_recognized_caller_to_shared_calendar():
    mcp = FakeMcp(list_events={"events": []}, check_conflicts={"conflicts": []}, add_event={"success": True, "event_id": 11})
    cal = calendar(mcp)
    agent = Agent(StaticExtractor(), cal, Store(":memory:"), 60)
    proposal = agent.propose(Inbound(user_id="u1", text="lunch", received_at=NOW))

    assert agent.confirm(proposal.id, "u1") == ["11"]
    assert "[family-member:u1]" in mcp.calls[-1][1]["description"]
    assert SharedCalendarFactory(cal).for_user("another-user") is cal
