"""Turn messy input (text, screenshots, forwarded chats) into ProposedEvents via the model-router."""
from __future__ import annotations

import base64
import json
from datetime import date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError

from .models import ExistingEvent, Inbound, Intent, Proposal, ProposedEvent
from .recurrence import occurrences


class Extraction(BaseModel):
    events: list[ProposedEvent] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    intent: Intent = "create"
    # For update/delete/query: the window the instruction spans, as local datetimes.
    range_start: datetime | None = None
    range_end: datetime | None = None


class Extractor(Protocol):
    def extract(self, inbound: Inbound) -> Extraction: ...

    def revise(self, current: Proposal, instruction: str, now: datetime) -> Extraction: ...

    def plan(self, instruction: str, existing: list[ExistingEvent], now: datetime) -> Extraction: ...


# The model returns *local wall-clock* strings with no offset; we attach the zone ourselves.
# Models are unreliable at UTC offsets and DST, and this keeps that logic in one tested place.
_RANGE = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["start", "end"],
    "properties": {
        "start": {"type": "string", "description": "YYYY-MM-DD, first day the instruction covers"},
        "end": {"type": "string", "description": "YYYY-MM-DD, last day the instruction covers (inclusive)"},
    },
}

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["events", "questions", "intent", "range"],
    "properties": {
        "intent": {"enum": ["create", "update", "delete", "query"]},
        "range": _RANGE,
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "start", "end", "all_day", "location", "notes", "confidence", "rationale", "recurrence"],
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "string", "description": "YYYY-MM-DDTHH:MM, local time. Date only (YYYY-MM-DD) if all-day."},
                    "end": {"type": ["string", "null"]},
                    "all_day": {"type": "boolean"},
                    "location": {"type": ["string", "null"]},
                    "notes": {"type": ["string", "null"]},
                    "confidence": {"enum": ["high", "medium", "low"]},
                    "rationale": {"type": ["string", "null"]},
                    "recurrence": {
                        "type": ["object", "null"],
                        "additionalProperties": False,
                        "required": ["weekdays", "until", "count"],
                        "properties": {
                            "weekdays": {"type": "array", "items": {"enum": ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]}},
                            "until": {"type": ["string", "null"], "description": "YYYY-MM-DD, last date inclusive; null if using count"},
                            "count": {"type": ["integer", "null"], "minimum": 1},
                        },
                    },
                },
            },
        },
        "questions": {"type": "array", "items": {"type": "string"}},
    },
}

_SYSTEM = """You extract calendar events from what the user forwards: typed notes, \
screenshots of posters/invites/tickets, and WhatsApp-style chat excerpts.

Rules:
- Current local time is {now} ({weekday}), timezone {tz}. Resolve relative dates ("next Fri", "tmr 3pm") from it.
- First set `intent`: "create" to add new events, "update" to change existing ones, "delete" to remove
  existing ones, "query" to just see what is on the calendar. "change/move/reschedule/rename/delete/clear"
  are update/delete, not create. When intent is update/delete/query, set `range` to the days it covers
  (YYYY-MM-DD) and leave `events` empty - the code matches the real events. Only intent "create" fills `events`.
- Output local wall-clock times, no UTC offset. start/end format: YYYY-MM-DDTHH:MM (or YYYY-MM-DD when all_day).
- In a chat, use the *final agreed* plan, not earlier proposals that were changed. Ignore small talk.
- One event per distinct occasion. For a repeating plan ("every Saturday in November", "weekly Mon/Wed",
  "daily next week") set `recurrence` and `start` to the *first* occurrence. Do NOT list the dates yourself:
  give `weekdays` (MO..SU) plus `until` (YYYY-MM-DD, inclusive) or `count`. For a single event set
  `recurrence` to null. Do not drop the series down to one date.
- If a date, time or even the year is missing, make your best guess with confidence "low" AND add a short
  question to `questions`. Never invent a location or end time; use null.
- Nothing event-like in the input -> events: [] and a question saying so.
- Reply with JSON only."""


# Change set against real events the code has already listed (ids are supplied, never invented).
_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["events", "questions"],
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "target_id", "title", "start", "end", "all_day", "location", "notes", "confidence"],
                "properties": {
                    "action": {"enum": ["update", "delete"]},
                    "target_id": {"type": "string", "description": "the id of the existing event to change"},
                    "title": {"type": "string", "description": "the new title (or the old one for delete)"},
                    "start": {"type": "string", "description": "YYYY-MM-DDTHH:MM local, the new time (or old for delete)"},
                    "end": {"type": ["string", "null"]},
                    "all_day": {"type": "boolean"},
                    "location": {"type": ["string", "null"]},
                    "notes": {"type": ["string", "null"]},
                    "confidence": {"enum": ["high", "medium", "low"]},
                },
            },
        },
        "questions": {"type": "array", "items": {"type": "string"}},
    },
}

_PLAN_SYSTEM = """You edit an existing calendar. You are given the events currently on it (with their ids) \
and an instruction. Return only the events the instruction asks to change.

Rules:
- Current local time is {now} ({weekday}), timezone {tz}.
- Each returned event: `action` "update" (change fields) or "delete" (remove), and `target_id` copied exactly
  from the listed event. Never invent an id.
- For "update", fill title/start/end/all_day/location/notes with the NEW values; keep the fields the
  instruction does not mention as they were. For "delete", echo the listed event's fields.
- Output local wall-clock times, no UTC offset. start/end format: YYYY-MM-DDTHH:MM (or YYYY-MM-DD when all_day).
- If no listed event matches, return events: [] and a short question. Reply with JSON only."""


class RouterExtractor:
    """OpenAI-compatible client pointed at model-router (or any OpenAI-style endpoint)."""

    def __init__(self, base_url: str, api_key: str, model: str, tz: ZoneInfo, client=None):
        if client is None:
            from openai import OpenAI

            client = OpenAI(base_url=base_url, api_key=api_key)
        self._client, self._model, self._tz = client, model, tz

    def extract(self, inbound: Inbound) -> Extraction:
        raw = self._call(self._messages(inbound))
        try:
            return parse_extraction(raw, self._tz)
        except (ValueError, ValidationError) as first:
            # One repair attempt: show the model its own output and the error.
            retry = self._messages(inbound) + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"That was invalid ({first}). Reply again with valid JSON only."},
            ]
            return parse_extraction(self._call(retry), self._tz)

    def revise(self, current: Proposal, instruction: str, now: datetime) -> Extraction:
        """Apply a user's edit ("make it 8pm", "drop the second one") to a proposal."""
        listing = json.dumps(
            [{"title": e.title, "start": e.start.strftime("%Y-%m-%dT%H:%M"),
              "end": e.end.strftime("%Y-%m-%dT%H:%M") if e.end else None, "all_day": e.all_day,
              "location": e.location, "notes": e.notes, "confidence": "high", "rationale": None}
             for e in current.events], indent=1)
        n = now.astimezone(self._tz)
        system = _SYSTEM.format(now=n.strftime("%Y-%m-%d %H:%M"), weekday=n.strftime("%A"), tz=self._tz.key)
        system += "\n\nYou are EDITING an existing proposal. Return the full updated events list: change only what " \
                  "the instruction asks, keep everything else identical, remove events if told to, add if told to. " \
                  "Set intent to \"create\" and range to null in your reply."
        user = f"Current events:\n{listing}\n\nInstruction: {instruction}"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        raw = self._call(messages)
        try:
            return parse_extraction(raw, self._tz)
        except (ValueError, ValidationError) as first:
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": f"That was invalid ({first}). Reply again with valid JSON only."}]
            return parse_extraction(self._call(messages), self._tz)

    def plan(self, instruction: str, existing: list[ExistingEvent], now: datetime) -> Extraction:
        """Choose which listed events to update/delete for an instruction, with their ids."""
        listing = json.dumps(
            [{"id": e.id, "title": e.title, "start": e.start.strftime("%Y-%m-%dT%H:%M"),
              "end": e.end.strftime("%Y-%m-%dT%H:%M"), "all_day": e.all_day,
              "location": e.location, "notes": e.notes}
             for e in existing], indent=1)
        n = now.astimezone(self._tz)
        system = _PLAN_SYSTEM.format(now=n.strftime("%Y-%m-%d %H:%M"), weekday=n.strftime("%A"), tz=self._tz.key)
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": f"Events on the calendar:\n{listing}\n\nInstruction: {instruction}"}]
        raw = self._call(messages, _PLAN_SCHEMA, "change_set")
        try:
            return parse_plan(raw, self._tz, {e.id: e for e in existing})
        except (ValueError, ValidationError) as first:
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": f"That was invalid ({first}). Reply again with valid JSON only."}]
            return parse_plan(self._call(messages, _PLAN_SCHEMA, "change_set"), self._tz, {e.id: e for e in existing})

    def _call(self, messages: list[dict], schema: dict | None = None, name: str = "events") -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_schema", "json_schema": {"name": name, "schema": schema or _SCHEMA, "strict": True}},
        )
        return resp.choices[0].message.content or ""

    def _messages(self, inbound: Inbound) -> list[dict]:
        now = inbound.received_at.astimezone(self._tz)
        system = _SYSTEM.format(now=now.strftime("%Y-%m-%d %H:%M"), weekday=now.strftime("%A"), tz=self._tz.key)
        parts: list[dict] = []
        if inbound.text:
            parts.append({"type": "text", "text": inbound.text})
        for a in inbound.attachments:
            url = f"data:{a.mime};base64,{base64.b64encode(a.data).decode()}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
        if not parts:
            parts.append({"type": "text", "text": "(empty message)"})
        return [{"role": "system", "content": system}, {"role": "user", "content": parts}]


def _loads(raw: str) -> dict:
    s = raw.strip()
    if s.startswith("```"):  # some free models fence their JSON despite instructions
        s = s.strip("`").removeprefix("json").strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"not JSON: {e}") from e


def _local(s: str, tz: ZoneInfo) -> tuple[datetime, bool]:
    """Parse model output as local time. Returns (aware datetime, date_only)."""
    s = s.strip()
    date_only = len(s) == 10
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:  # model ignored instructions; trust its instant, show in our zone
        return dt.astimezone(tz), date_only
    return dt.replace(tzinfo=tz), date_only


def _rule(e: dict) -> tuple[list[str], date | None, int | None] | None:
    """A recurrence rule from one raw event, or None for a single event."""
    r = e.get("recurrence")
    if not r:
        return None
    weekdays = [str(w).upper() for w in (r.get("weekdays") or [])]
    if not weekdays:
        return None
    until = r.get("until")
    count = r.get("count")
    return weekdays, (date.fromisoformat(until) if until else None), (int(count) if count else None)


def parse_extraction(raw: str, tz: ZoneInfo) -> Extraction:
    data = _loads(raw)
    events: list[ProposedEvent] = []
    questions = [str(q) for q in data.get("questions", [])]
    for e in data.get("events", []):
        start, date_only = _local(e["start"], tz)
        end = _local(e["end"], tz)[0] if e.get("end") else None
        all_day = bool(e.get("all_day")) or date_only
        if end is not None and end <= start:
            # "10pm-1am": the end rolled past midnight. Otherwise the end is unusable; drop it.
            rolled = end + timedelta(days=1)
            end = rolled if not all_day and rolled - start <= timedelta(hours=24) else None

        def make(when: datetime, span: timedelta | None = None) -> ProposedEvent:
            return ProposedEvent(
                title=e["title"].strip(),
                start=when,
                end=when + span if span is not None else None,
                all_day=all_day,
                location=e.get("location"),
                notes=e.get("notes"),
                confidence=e.get("confidence", "medium"),
                rationale=e.get("rationale"),
            )

        rule = _rule(e)
        if rule is None:
            events.append(make(start, (end - start) if end else None))
            continue

        weekdays, until, count = rule
        dates, capped = occurrences(weekdays, start.date(), until, count)
        if capped:
            questions.append(f"'{e['title'].strip()}' repeats a lot; I kept the first {len(dates)}.")
        if not dates:  # e.g. an empty/invalid rule - fall back to the single occurrence
            events.append(make(start, (end - start) if end else None))
            continue
        span = (end - start) if end else None
        for day in dates:
            events.append(make(datetime.combine(day, start.timetz()), span))
    range_start, range_end = _range(data.get("range"), tz)
    intent = data.get("intent", "create")
    return Extraction(events=events, questions=questions, intent=intent, range_start=range_start, range_end=range_end)


def _range(raw: dict | None, tz: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    if not raw:
        return None, None
    start = _local(raw["start"], tz)[0] if raw.get("start") else None
    end = _local(raw["end"], tz)[0] if raw.get("end") else None
    if start is not None and end is not None:
        end = end + timedelta(days=1)  # make the last day inclusive for a list_events window
    return start, end


def parse_plan(raw: str, tz: ZoneInfo, existing: dict[str, ExistingEvent]) -> Extraction:
    """Parse a change set, attaching each change to the real event it targets."""
    data = _loads(raw)
    events: list[ProposedEvent] = []
    for e in data.get("events", []):
        prior = existing.get(str(e.get("target_id") or ""))
        if prior is None:
            continue  # the model hallucinated an id; ignore it rather than touch the wrong event
        start, date_only = _local(e["start"], tz)
        end = _local(e["end"], tz)[0] if e.get("end") else None
        events.append(ProposedEvent(
            title=(e.get("title") or prior.title).strip(),
            start=start,
            end=end,
            all_day=bool(e.get("all_day")) or date_only,
            location=e.get("location"),
            notes=e.get("notes"),
            confidence=e.get("confidence", "medium"),
            action=e["action"],
            target_id=prior.id,
            previous=prior,
        ))
    return Extraction(events=events, questions=[str(q) for q in data.get("questions", [])])
