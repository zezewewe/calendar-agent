"""WebCalendar's HTTP MCP endpoint behind the shared Calendar interface.

WebCalendar returns tool results directly under JSON-RPC ``result`` (rather
than the usual MCP content array), so this intentionally uses a small JSON-RPC
client instead of an SDK. Its write tools accept GMT date/time values, while
list_events returns local values in the WebCalendar user's configured timezone.
"""
from __future__ import annotations

import json
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from ..models import ExistingEvent, ProposedEvent


class WebCalendarCalendar:
    def __init__(
        self,
        mcp_url: str,
        token: str,
        tz: ZoneInfo,
        member_names: dict[str, str] | None = None,
        transport: Callable[[dict], dict] | None = None,
    ):
        if not mcp_url:
            raise ValueError("WEBCALENDAR_MCP_URL is required for the webcalendar backend")
        if not token:
            raise ValueError("WEBCALENDAR_MCP_TOKEN is required for the webcalendar backend")
        self._url, self._token, self._tz = mcp_url, token, tz
        self._members = member_names or {}
        self._transport = transport or self._post

    def _post(self, payload: dict) -> dict:
        request = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-MCP-Token": self._token},
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - configured endpoint
            return json.load(response)

    def _call(self, tool: str, arguments: dict) -> dict:
        request_id = uuid.uuid4().hex
        message = self._transport({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        })
        if message.get("id") != request_id:
            raise RuntimeError("WebCalendar returned a mismatched JSON-RPC response id")
        if "error" in message:
            raise RuntimeError(f"WebCalendar {tool} failed: {message['error']}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"WebCalendar {tool} returned no result object")
        if "error" in result:
            raise RuntimeError(f"WebCalendar {tool} failed: {result['error']}")
        return result

    def list_events(self, start: datetime, end: datetime) -> list[ExistingEvent]:
        result = self._call("list_events", {
            "start_date": start.astimezone(self._tz).strftime("%Y%m%d"),
            "end_date": end.astimezone(self._tz).strftime("%Y%m%d"),
        })
        return [self._existing(e) for e in result.get("events", [])]

    def _existing(self, raw: dict) -> ExistingEvent:
        day = datetime.strptime(str(raw["date"]), "%Y%m%d").date()
        raw_time = str(raw.get("time", "-1"))
        if raw_time == "-1":
            start = datetime.combine(day, datetime.min.time(), tzinfo=self._tz)
            end = start + timedelta(days=1)
        else:
            start = datetime.strptime(f"{day:%Y%m%d}{raw_time.zfill(6)}", "%Y%m%d%H%M%S").replace(tzinfo=self._tz)
            end = start + timedelta(minutes=max(0, int(raw.get("duration") or 0)))
        return ExistingEvent(
            id=str(raw["id"]),
            title=raw.get("name") or "(no title)",
            start=start,
            end=end,
            all_day=(raw_time == "-1"),
            location=raw.get("location") or None,
            notes=raw.get("description") or None,
        )

    def create_event(self, event: ProposedEvent, end: datetime, user_id: str | None = None) -> str:
        description = self._description(event.notes, user_id)
        arguments = {
            "name": event.title,
            "date": event.start.astimezone(self._tz).strftime("%Y%m%d"),
            "time": "-1" if event.all_day else event.start.astimezone(timezone.utc).strftime("%H%M%S"),
            "duration": 0 if event.all_day else max(0, round((end - event.start).total_seconds() / 60)),
            "description": description,
            "location": event.location or "",
        }
        if not event.all_day:
            # add_event expects both date and time in GMT, including the date.
            utc_start = event.start.astimezone(timezone.utc)
            arguments["date"] = utc_start.strftime("%Y%m%d")
        result = self._call("add_event", arguments)
        if "event_id" not in result:
            raise RuntimeError("WebCalendar add_event returned no event_id")
        return str(result["event_id"])

    def _description(self, notes: str | None, user_id: str | None) -> str:
        if user_id is None:
            return notes or ""
        member_id = " ".join(str(user_id).splitlines())
        name = self._members.get(user_id, member_id)
        marker = f"Family member: {name} [family-member:{member_id}]"
        return f"{notes.rstrip()}\n\n{marker}" if notes and notes.strip() else marker

    def delete_event(self, event_id: str) -> None:
        try:
            numeric_id = int(event_id)
        except ValueError as e:
            raise ValueError(f"WebCalendar event id must be numeric, got {event_id!r}") from e
        result = self._call("delete_event", {"event_id": numeric_id})
        if result.get("success") is not True:
            raise RuntimeError(f"WebCalendar delete_event failed: {result}")
