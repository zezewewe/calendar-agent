"""Per-user calendars from one agent Google account.

The agent's existing OAuth token stays the only credential. Each user gets a
secondary calendar that the agent owns and shares (ACL, `share_role`) with the
user's Gmail, so the events show up inside that user's own Google Calendar with
no per-user consent screen. `for_user(email)` is the factory the HTTP channel
calls once it has a verified email.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

from .google import GoogleCalendar
from .registry import CalendarRegistry


class GoogleCalendarFactory:
    def __init__(
        self,
        client_secret_file: str,
        token_file: str,
        registry: CalendarRegistry,
        tz: ZoneInfo,
        share_role: str = "writer",
        service=None,
    ):
        self._cs, self._tk, self._tz = client_secret_file, token_file, tz
        self._registry, self._role = registry, share_role
        # One service for every per-user calendar: they are all owned by the agent account.
        self._svc = service or GoogleCalendar._build(client_secret_file, token_file)

    def for_user(self, email: str) -> GoogleCalendar:
        cal_id = self._registry.get(email) or self._provision(email)
        return GoogleCalendar(self._cs, self._tk, cal_id, self._tz, service=self._svc)

    def _provision(self, email: str) -> str:
        """Create a secondary calendar and share it with the user (idempotent per email)."""
        cal = self._svc.calendars().insert(body={"summary": "Calendar Agent", "timeZone": self._tz.key}).execute()
        cal_id = cal["id"]
        self._svc.acl().insert(
            calendarId=cal_id,
            body={"role": self._role, "scope": {"type": "user", "value": email}},
        ).execute()
        self._registry.set(email, cal_id)
        return cal_id
