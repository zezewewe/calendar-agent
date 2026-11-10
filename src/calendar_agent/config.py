from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


class VisionModel(Enum):
    """Vision-capable model-router aliases the extractor can call (it must accept images).

    ``EXTRACT_MODEL`` selects one by alias or by member name (case-insensitive); the
    default is :attr:`RAUS`. Switch it when a route is down or slow — e.g. ``ds-flash``
    answers in seconds where ``vision-raus`` (a local reasoning model) can take ~30s.
    The aliases live in model-router ``config.yaml``; add new ones here.
    """

    RAUS = "vision-raus"
    DEFAULT = "vision-default"
    FLASH = "ds-flash"
    FREE = "vision-free"
    FREE_DEEPSEEK = "vision-free-deepseek"
    FREE_MIMO = "vision-free-mimo"
    CLAUDE = "vision-claude-code"
    CLAUDE_FAST = "vision-claude-code-fast"

    @classmethod
    def parse(cls, value: str | None) -> "VisionModel":
        """Return the model for an alias or member name; default to :attr:`RAUS` when blank."""
        if value is None or not value.strip():
            return cls.RAUS
        key = value.strip()
        for member in cls:
            if key == member.value or key.upper() == member.name:
                return member
        known = ", ".join(member.value for member in cls)
        raise ValueError(f"EXTRACT_MODEL {value!r} is not a known vision model (choose: {known})")


@dataclass(frozen=True)
class Config:
    router_url: str = "http://localhost:4000"
    router_api_key: str = ""
    extract_model: str = VisionModel.RAUS.value
    timezone: str = "Asia/Singapore"
    default_duration_min: int = 60
    calendar_backend: str = "webcalendar"
    webcalendar_mcp_url: str = "http://localhost:8080/mcp.php"
    webcalendar_mcp_token: str = ""
    family_members: dict[str, str] = field(default_factory=dict)
    telegram_bot_token: str = ""
    telegram_allowed_users: frozenset[str] = field(default_factory=frozenset)
    # When set, the bot's menu button opens this URL as a Telegram Mini App (the calendar UI).
    calendar_web_url: str = ""
    # Identity the MCP server (`calendar-agent mcp`) acts as.
    mcp_user_id: str = "mcp"
    state_db: str = "state.db"
    # HTTP channel + ZeLabs gateway identity
    http_host: str = "127.0.0.1"
    http_port: int = 8099
    zelabs_route_id: str = "calendar"
    zelabs_identity_key: str = ""
    zelabs_key_file: str = ""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def menu_button_url(self) -> str:
        """The Mini App URL for the chat menu button, or "" when it is unusable.

        Telegram only accepts https web-app URLs, so an http or malformed
        ``calendar_web_url`` is dropped (the Telegram front end warns) rather than
        crashing the bot at startup.
        """
        url = self.calendar_web_url.strip()
        return url if urlparse(url).scheme == "https" else ""

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
            extract_model=VisionModel.parse(e("EXTRACT_MODEL", cls.extract_model)).value,
            timezone=e("TIMEZONE", cls.timezone),
            calendar_backend=e("CALENDAR_BACKEND", cls.calendar_backend).lower(),
            webcalendar_mcp_url=e("WEBCALENDAR_MCP_URL", cls.webcalendar_mcp_url),
            webcalendar_mcp_token=e("WEBCALENDAR_MCP_TOKEN", ""),
            family_members=members,
            telegram_bot_token=e("TELEGRAM_BOT_TOKEN", ""),
            telegram_allowed_users=users,
            calendar_web_url=e("CALENDAR_WEB_URL", ""),
            mcp_user_id=e("MCP_USER_ID", cls.mcp_user_id),
            state_db=e("STATE_DB", cls.state_db),
            http_host=e("HTTP_HOST", cls.http_host),
            http_port=int(e("HTTP_PORT", str(cls.http_port))),
            zelabs_route_id=e("ZELABS_ROUTE_ID", cls.zelabs_route_id),
            zelabs_identity_key=e("ZELABS_IDENTITY_KEY", ""),
            zelabs_key_file=e("ZELABS_KEY_FILE", ""),
        )
