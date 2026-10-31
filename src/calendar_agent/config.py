from __future__ import annotations

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
    google_client_secret_file: str = "client_secret.json"
    google_token_file: str = "token.json"
    google_calendar_id: str = "primary"
    telegram_bot_token: str = ""
    telegram_allowed_users: frozenset[str] = field(default_factory=frozenset)
    state_db: str = "state.db"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        users = frozenset(u.strip() for u in e("TELEGRAM_ALLOWED_USERS", "").split(",") if u.strip())
        return cls(
            router_url=e("ROUTER_URL", cls.router_url),
            router_api_key=e("ROUTER_API_KEY", ""),
            extract_model=e("EXTRACT_MODEL", cls.extract_model),
            timezone=e("TIMEZONE", cls.timezone),
            google_client_secret_file=e("GOOGLE_CLIENT_SECRET_FILE", cls.google_client_secret_file),
            google_token_file=e("GOOGLE_TOKEN_FILE", cls.google_token_file),
            google_calendar_id=e("GOOGLE_CALENDAR_ID", cls.google_calendar_id),
            telegram_bot_token=e("TELEGRAM_BOT_TOKEN", ""),
            telegram_allowed_users=users,
            state_db=e("STATE_DB", cls.state_db),
        )
