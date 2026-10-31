from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ..models import ExistingEvent, ProposedEvent

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


class GoogleCalendar:
    def __init__(self, client_secret_file: str, token_file: str, calendar_id: str, tz: ZoneInfo, service=None):
        self._id, self._tz = calendar_id, tz
        self._svc = service or self._build(client_secret_file, token_file)

    @staticmethod
    def _build(client_secret_file: str, token_file: str):
        import os

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file(token_file, SCOPES) if os.path.exists(token_file) else None
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                creds = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES).run_local_server(port=0)
            with open(token_file, "w") as f:
                f.write(creds.to_json())
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    def list_events(self, start: datetime, end: datetime) -> list[ExistingEvent]:
        out: list[ExistingEvent] = []
        page = None
        while True:
            r = (
                self._svc.events()
                .list(calendarId=self._id, timeMin=start.isoformat(), timeMax=end.isoformat(),
                      singleEvents=True, orderBy="startTime", pageToken=page)
                .execute()
            )
            for e in r.get("items", []):
                if e.get("status") == "cancelled" or e.get("transparency") == "transparent":
                    continue  # cancelled / marked "free": not a real conflict
                s, en = self._when(e["start"]), self._when(e["end"])
                out.append(ExistingEvent(id=e["id"], title=e.get("summary", "(no title)"), start=s, end=en))
            page = r.get("nextPageToken")
            if not page:
                return out

    def _when(self, d: dict) -> datetime:
        if "dateTime" in d:
            return datetime.fromisoformat(d["dateTime"]).astimezone(self._tz)
        return datetime.fromisoformat(d["date"]).replace(tzinfo=self._tz)  # all-day

    def create_event(self, event: ProposedEvent, end: datetime) -> str:
        body: dict = {"summary": event.title}
        if event.all_day:
            body["start"] = {"date": event.start.date().isoformat()}
            body["end"] = {"date": end.date().isoformat()}
        else:
            body["start"] = {"dateTime": event.start.isoformat(), "timeZone": self._tz.key}
            body["end"] = {"dateTime": end.isoformat(), "timeZone": self._tz.key}
        if event.location:
            body["location"] = event.location
        if event.notes:
            body["description"] = event.notes
        return self._svc.events().insert(calendarId=self._id, body=body).execute()["id"]

    def delete_event(self, event_id: str) -> None:
        self._svc.events().delete(calendarId=self._id, eventId=event_id).execute()
