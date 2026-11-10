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


def rawx(intent="create", range=None, events=(), questions=()):
    return json.dumps({"events": list(events), "questions": list(questions), "intent": intent, "range": range})


def change(action, target_id, title="X", start="2026-11-07T08:00", **kw):
    return {"action": action, "target_id": target_id, "title": title, "start": start, "end": None,
            "all_day": False, "location": None, "notes": None, "confidence": "high", **kw}


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

    def create_event(self, event, end, user_id=None):
        self.created.append((event, end))
        return f"id{len(self.created)}"

    def update_event(self, event_id, event, end, user_id=None):
        self.deleted.append(event_id)
        return self.create_event(event, end, user_id)

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


def test_revise_replaces_proposal_and_rechecks_conflicts():
    clash = ExistingEvent(id="x", title="Gym", start=datetime(2026, 11, 5, 19, 0, tzinfo=TZ), end=datetime(2026, 11, 5, 20, 0, tzinfo=TZ))
    c = FakeClient(raw(ev()), raw(ev(start="2026-11-05T21:00")))
    a = make_agent(c, FakeCal([clash]))
    p = a.propose(inbound())
    assert 0 in p.conflicts
    p2 = a.revise(p.id, "u1", "make it 9pm", NOW)
    assert p2.id != p.id and p2.events[0].start.hour == 21 and p2.conflicts == {}
    assert a.confirm(p.id, "u1") is None  # old one is gone
    assert "Instruction: make it 9pm" in c.calls[1]["messages"][1]["content"]
    assert a.confirm(p2.id, "u1")


def test_failed_revise_keeps_original_confirmable():
    a = make_agent(FakeClient(raw(ev()), "garbage", "still garbage"), FakeCal())
    p = a.propose(inbound())
    with pytest.raises(ValueError):
        a.revise(p.id, "u1", "later", NOW)
    assert a.confirm(p.id, "u1")


def test_revise_wrong_user_or_missing():
    a = make_agent(FakeClient(raw(ev())), FakeCal())
    p = a.propose(inbound())
    assert a.revise(p.id, "other", "x", NOW) is None
    assert a.revise("nope", "u1", "x", NOW) is None


def test_recurrence_expands_weekly_saturdays_in_november():
    # Nov 1 2026 is a Sunday; the series must land on the four Saturdays, not Sunday.
    x = parse_extraction(raw(ev(start="2026-11-01T10:00", recurrence={"weekdays": ["SA"], "until": "2026-11-30", "count": None})), TZ)
    starts = [e.start for e in x.events]
    assert [s.day for s in starts] == [7, 14, 21, 28]
    assert all(s.weekday() == 5 and s.hour == 10 for s in starts)


def test_recurrence_count_bounds_series_to_n_weekdays():
    x = parse_extraction(raw(ev(start="2026-11-02T09:00", recurrence={"weekdays": ["MO", "WE"], "until": None, "count": 3})), TZ)
    assert [e.start.day for e in x.events] == [2, 4, 9]
    assert all(e.start.weekday() in (0, 2) for e in x.events)


def test_recurrence_without_until_falls_back_to_end_of_month():
    x = parse_extraction(raw(ev(start="2026-11-03T08:00", recurrence={"weekdays": ["SA"], "until": None, "count": None})), TZ)
    assert [e.start.day for e in x.events] == [7, 14, 21, 28]


def test_recurrence_preserves_duration_on_every_occurrence():
    x = parse_extraction(raw(ev(start="2026-11-07T10:00", end="2026-11-07T11:30", recurrence={"weekdays": ["SA"], "until": "2026-11-30", "count": None})), TZ)
    assert all(e.end - e.start == timedelta(minutes=90) for e in x.events)


def test_recurrence_caps_runaway_series_and_flags_it():
    x = parse_extraction(raw(ev(start="2026-01-01T09:00", recurrence={"weekdays": ["MO", "TU", "WE", "TH", "FR", "SA", "SU"], "until": None, "count": 5000})), TZ)
    assert len(x.events) == 366
    assert any("repeats a lot" in q for q in x.questions)


def test_empty_recurrence_stays_a_single_event():
    x = parse_extraction(raw(ev(start="2026-11-05T19:00", recurrence={"weekdays": [], "until": None, "count": None})), TZ)
    assert len(x.events) == 1


def test_update_intent_lists_then_applies_change_and_undo_restores():
    existing = ExistingEvent(id="42", title="Gym", start=datetime(2026, 11, 7, 9, 0, tzinfo=TZ), end=datetime(2026, 11, 7, 10, 0, tzinfo=TZ))
    cal = FakeCal([existing])
    extract = rawx(intent="update", range={"start": "2026-11-01", "end": "2026-11-30"})
    plan = json.dumps({"events": [change("update", "42", title="Gym", start="2026-11-07T08:00")], "questions": []})
    a = make_agent(FakeClient(extract, plan), cal)

    p = a.propose(inbound("move my Saturday gym to 8am"))
    assert p.intent == "update" and len(p.events) == 1
    assert p.events[0].action == "update" and p.events[0].target_id == "42"
    assert cal.created == []  # nothing written before confirm

    assert a.confirm(p.id, "u1") == ["id1"]
    assert cal.deleted == ["42"] and len(cal.created) == 1
    assert a.undo(p.id, "u1") == 2  # remove new, recreate old
    assert cal.deleted == ["42", "id1"] and len(cal.created) == 2


def test_delete_intent_removes_matched_event_and_undo_recreates_it():
    e1 = ExistingEvent(id="7", title="Standup", start=datetime(2026, 11, 3, 9, 0, tzinfo=TZ), end=datetime(2026, 11, 3, 9, 30, tzinfo=TZ))
    cal = FakeCal([e1])
    plan = json.dumps({"events": [change("delete", "7", title="Standup", start="2026-11-03T09:00")], "questions": []})
    a = make_agent(FakeClient(rawx(intent="delete", range={"start": "2026-11-01", "end": "2026-11-30"}), plan), cal)

    p = a.propose(inbound("delete the standup"))
    assert p.events[0].action == "delete" and p.events[0].target_id == "7"
    assert a.confirm(p.id, "u1") == [] and cal.deleted == ["7"]
    assert a.undo(p.id, "u1") == 1 and len(cal.created) == 1


def test_query_lists_events_and_is_not_confirmable():
    e1 = ExistingEvent(id="7", title="Standup", start=datetime(2026, 11, 3, 9, 0, tzinfo=TZ), end=datetime(2026, 11, 3, 9, 30, tzinfo=TZ))
    a = make_agent(FakeClient(rawx(intent="query", range={"start": "2026-11-01", "end": "2026-11-30"})), FakeCal([e1]))
    p = a.propose(inbound("what's on in November?"))
    assert p.intent == "query" and p.listing and not p.events
    assert a.confirm(p.id, "u1") is None


def test_change_with_no_matching_events_asks_instead_of_writing():
    a = make_agent(FakeClient(rawx(intent="delete", range={"start": "2026-11-01", "end": "2026-11-30"})), FakeCal())
    p = a.propose(inbound("delete my November meetings"))
    assert p.events == [] and p.questions


def test_plan_ignores_hallucinated_target_id():
    e1 = ExistingEvent(id="7", title="Standup", start=datetime(2026, 11, 3, 9, 0, tzinfo=TZ), end=datetime(2026, 11, 3, 9, 30, tzinfo=TZ))
    plan = json.dumps({"events": [change("delete", "does-not-exist")], "questions": []})
    a = make_agent(FakeClient(rawx(intent="delete", range={"start": "2026-11-01", "end": "2026-11-30"}), plan), FakeCal([e1]))
    assert a.propose(inbound("delete it")).events == []


def test_message_link_roundtrip():
    a = make_agent(FakeClient(), FakeCal())
    a.link_message("c", "7", "pid")
    assert a.proposal_for_message("c", "7") == "pid" and a.proposal_for_message("c", "8") is None
