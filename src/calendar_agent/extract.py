"""Turn messy input (text, screenshots, forwarded chats) into ProposedEvents via the model-router."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError

from .models import Inbound, ProposedEvent


class Extraction(BaseModel):
    events: list[ProposedEvent] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


class Extractor(Protocol):
    def extract(self, inbound: Inbound) -> Extraction: ...


# The model returns *local wall-clock* strings with no offset; we attach the zone ourselves.
# Models are unreliable at UTC offsets and DST, and this keeps that logic in one tested place.
_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["events", "questions"],
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "start", "end", "all_day", "location", "notes", "confidence", "rationale"],
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "string", "description": "YYYY-MM-DDTHH:MM, local time. Date only (YYYY-MM-DD) if all-day."},
                    "end": {"type": ["string", "null"]},
                    "all_day": {"type": "boolean"},
                    "location": {"type": ["string", "null"]},
                    "notes": {"type": ["string", "null"]},
                    "confidence": {"enum": ["high", "medium", "low"]},
                    "rationale": {"type": ["string", "null"]},
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
- Output local wall-clock times, no UTC offset. start/end format: YYYY-MM-DDTHH:MM (or YYYY-MM-DD when all_day).
- In a chat, use the *final agreed* plan, not earlier proposals that were changed. Ignore small talk.
- One event per distinct occasion. A recurring series is only its next occurrence; mention the recurrence in notes.
- If a date, time or even the year is missing, make your best guess with confidence "low" AND add a short
  question to `questions`. Never invent a location or end time; use null.
- Nothing event-like in the input -> events: [] and a question saying so.
- Reply with JSON only."""


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

    def _call(self, messages: list[dict]) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_schema", "json_schema": {"name": "events", "schema": _SCHEMA, "strict": True}},
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


def parse_extraction(raw: str, tz: ZoneInfo) -> Extraction:
    data = _loads(raw)
    events: list[ProposedEvent] = []
    for e in data.get("events", []):
        start, date_only = _local(e["start"], tz)
        end = _local(e["end"], tz)[0] if e.get("end") else None
        all_day = bool(e.get("all_day")) or date_only
        if end is not None and end <= start:
            # "10pm-1am": the end rolled past midnight. Otherwise the end is unusable; drop it.
            rolled = end + timedelta(days=1)
            end = rolled if not all_day and rolled - start <= timedelta(hours=24) else None
        events.append(
            ProposedEvent(
                title=e["title"].strip(),
                start=start,
                end=end,
                all_day=all_day,
                location=e.get("location"),
                notes=e.get("notes"),
                confidence=e.get("confidence", "medium"),
                rationale=e.get("rationale"),
            )
        )
    return Extraction(events=events, questions=[str(q) for q in data.get("questions", [])])
