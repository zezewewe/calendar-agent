import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from calendar_agent.agent import Agent
from calendar_agent.extract import RouterExtractor, parse_extraction
from calendar_agent.models import Attachment, ExistingEvent, Inbound
from calendar_agent.store import Store

TZ = ZoneInfo("Asia/Singapore")
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=TZ)


def raw(*events, questions=()):
    return json.dumps({"events": list(events), "questions": list(questions)})


def ev(title="Dinner", start="2026-11-05T19:00", end=None, **kw):
    return {"title": title, "start": start, "end": end, "all_day": False, "location": None,
            "notes": None, "confidence": "high", "rationale": None, **kw}


class FakeClient:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.replies.pop(0)))])


class FakeCal:
    def __init__(self, existing=()):
        self.existing, self.created, self.deleted = list(existing), [], []

    def list_events(self, s, e):
        return self.existing

    def create_event(self, event, end):
        self.created.append((event, end))
        return f"id{len(self.created)}"

    def delete_event(self, i):
        self.deleted.append(i)


def make_agent(client, cal):
    return Agent(RouterExtractor("", "", "m", TZ, client=client), cal, Store(":memory:"), 60)


def inbound(text="dinner thu 7pm", **kw):
    return Inbound(user_id="u1", text=text, received_at=NOW, **kw)


def test_parse_attaches_local_timezone():
    x = parse_extraction(raw(ev()), TZ)
    assert x.events[0].start == datetime(2026, 11, 5, 19, 0, tzinfo=TZ)
    assert x.events[0].end is None


def test_parse_date_only_is_all_day():
    x = parse_extraction(raw(ev(start="2026-11-05")), TZ)
    assert x.events[0].all_day


def test_parse_end_past_midnight_rolls_over():
    x = parse_extraction(raw(ev(start="2026-11-05T22:00", end="2026-11-05T01:00")), TZ)
    assert x.events[0].end == datetime(2026, 11, 6, 1, 0, tzinfo=TZ)


def test_parse_fenced_json():
    assert parse_extraction("```json\n" + raw(ev()) + "\n```", TZ).events


def test_extractor_retries_once_on_bad_json():
    c = FakeClient("sorry, here you go", raw(ev()))
    assert RouterExtractor("", "", "m", TZ, client=c).extract(inbound()).events
    assert len(c.calls) == 2


def test_images_sent_as_data_urls_and_now_in_prompt():
    c = FakeClient(raw())
    RouterExtractor("", "", "m", TZ, client=c).extract(inbound(attachments=[Attachment(mime="image/png", data=b"\x89PNG")]))
    msgs = c.calls[0]["messages"]
    assert "2026-10-31 12:00" in msgs[0]["content"] and "Saturday" in msgs[0]["content"]
    assert msgs[1]["content"][-1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_propose_does_not_touch_calendar_until_confirm():
    cal = FakeCal()
    a = make_agent(FakeClient(raw(ev())), cal)
    p = a.propose(inbound())
    assert len(p.events) == 1 and cal.created == []
    assert a.confirm(p.id, "u1") == ["id1"]
    ev0, end = cal.created[0]
    assert end - ev0.start == timedelta(minutes=60)  # default duration


def test_conflict_detected_and_adjacent_is_not_conflict():
    clash = ExistingEvent(id="x", title="Gym", start=datetime(2026, 11, 5, 18, 30, tzinfo=TZ), end=datetime(2026, 11, 5, 19, 30, tzinfo=TZ))
    touch = ExistingEvent(id="y", title="Prev", start=datetime(2026, 11, 5, 18, 0, tzinfo=TZ), end=datetime(2026, 11, 5, 19, 0, tzinfo=TZ))
    p = make_agent(FakeClient(raw(ev())), FakeCal([clash, touch])).propose(inbound())
    assert [c.id for c in p.conflicts[0]] == ["x"]


def test_confirm_is_idempotent_and_user_scoped():
    cal = FakeCal()
    a = make_agent(FakeClient(raw(ev())), cal)
    p = a.propose(inbound())
    assert a.confirm(p.id, "someone-else") is None
    assert a.confirm(p.id, "u1") == ["id1"]
    assert a.confirm(p.id, "u1") is None  # double tap
    assert len(cal.created) == 1


def test_cancel_and_undo():
    cal = FakeCal()
    a = make_agent(FakeClient(raw(ev()), raw(ev())), cal)
    p = a.propose(inbound())
    assert a.cancel(p.id, "u1") and a.confirm(p.id, "u1") is None
    p2 = a.propose(inbound())
    a.confirm(p2.id, "u1")
    assert a.undo(p2.id) == 1 and cal.deleted == ["id1"]


def test_no_events_means_nothing_stored():
    a = make_agent(FakeClient(raw(questions=["No event found"])), FakeCal())
    p = a.propose(inbound("hello"))
    assert p.events == [] and p.questions
    assert a.confirm(p.id, "u1") is None
