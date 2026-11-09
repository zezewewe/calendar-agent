from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Config:
    router_url: str = "http://localhost:4000"
    router_api_key: str = ""
    extract_model: str = "vision-free"
    timezone: str = "Asia/Singapore"
    default_duration_min: int = 60
    calendar_backend: str = "google"
    webcalendar_mcp_url: str = "http://localhost:8080/mcp.php"
    webcalendar_mcp_token: str = ""
    family_members: dict[str, str] = field(default_factory=dict)
    google_client_secret_file: str = "client_secret.json"
    google_token_file: str = "token.json"
    google_calendar_id: str = "primary"
    telegram_bot_token: str = ""
    telegram_allowed_users: frozenset[str] = field(default_factory=frozenset)
    state_db: str = "state.db"
    # per-user calendars (HTTP/ZeLabs channel): one agent account, one calendar per user
    calendar_registry_db: str = "calendars.db"
    share_role: str = "writer"
    # HTTP channel + ZeLabs gateway identity
    http_host: str = "127.0.0.1"
    http_port: int = 8099
    zelabs_route_id: str = "calendar"
    zelabs_identity_key: str = ""
    zelabs_key_file: str = ""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        users = frozenset(u.strip() for u in e("TELEGRAM_ALLOWED_USERS", "").split(",") if u.strip())
        try:
            members = json.loads(e("FAMILY_MEMBERS", "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError("FAMILY_MEMBERS must be a JSON object mapping caller ids to display names") from exc
        if not isinstance(members, dict) or not all(isinstance(v, str) for v in members.values()):
            raise ValueError("FAMILY_MEMBERS must be a JSON object mapping caller ids to display names")
        return cls(
            router_url=e("ROUTER_URL", cls.router_url),
            router_api_key=e("ROUTER_API_KEY", ""),
            extract_model=e("EXTRACT_MODEL", cls.extract_model),
            timezone=e("TIMEZONE", cls.timezone),
            calendar_backend=e("CALENDAR_BACKEND", cls.calendar_backend).lower(),
            webcalendar_mcp_url=e("WEBCALENDAR_MCP_URL", cls.webcalendar_mcp_url),
            webcalendar_mcp_token=e("WEBCALENDAR_MCP_TOKEN", ""),
            family_members=members,
            google_client_secret_file=e("GOOGLE_CLIENT_SECRET_FILE", cls.google_client_secret_file),
            google_token_file=e("GOOGLE_TOKEN_FILE", cls.google_token_file),
            google_calendar_id=e("GOOGLE_CALENDAR_ID", cls.google_calendar_id),
            telegram_bot_token=e("TELEGRAM_BOT_TOKEN", ""),
            telegram_allowed_users=users,
            state_db=e("STATE_DB", cls.state_db),
            calendar_registry_db=e("CALENDAR_REGISTRY_DB", cls.calendar_registry_db),
            share_role=e("SHARE_ROLE", cls.share_role),
            http_host=e("HTTP_HOST", cls.http_host),
            http_port=int(e("HTTP_PORT", str(cls.http_port))),
            zelabs_route_id=e("ZELABS_ROUTE_ID", cls.zelabs_route_id),
            zelabs_identity_key=e("ZELABS_IDENTITY_KEY", ""),
            zelabs_key_file=e("ZELABS_KEY_FILE", ""),
        )
