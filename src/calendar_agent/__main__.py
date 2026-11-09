from __future__ import annotations

import sys

from .agent import Agent
from .calendars import SharedCalendarFactory
from .calendars.google import GoogleCalendar
from .calendars.peruser import GoogleCalendarFactory
from .calendars.webcalendar import WebCalendarCalendar
from .calendars.registry import CalendarRegistry
from .channels import http, telegram
from .config import Config
from .extract import RouterExtractor
from .identity import load_master_key
from .pool import AgentPool
from .store import Store


def _extractor(cfg: Config) -> RouterExtractor:
    return RouterExtractor(cfg.router_url, cfg.router_api_key, cfg.extract_model, cfg.tz)


def _calendar(cfg: Config):
    if cfg.calendar_backend == "webcalendar":
        return WebCalendarCalendar(cfg.webcalendar_mcp_url, cfg.webcalendar_mcp_token, cfg.tz, cfg.family_members)
    if cfg.calendar_backend == "google":
        return GoogleCalendar(cfg.google_client_secret_file, cfg.google_token_file, cfg.google_calendar_id, cfg.tz)
    raise SystemExit("CALENDAR_BACKEND must be 'webcalendar' or 'google'")


def main() -> None:
    cfg = Config.from_env()
    if cfg.calendar_backend not in {"webcalendar", "google"}:
        raise SystemExit("CALENDAR_BACKEND must be 'webcalendar' or 'google'")
    args = sys.argv[1:]
    if args == ["auth"]:  # one-time Google OAuth, then exit
        if cfg.calendar_backend != "google":
            raise SystemExit("calendar-agent auth is only needed with CALENDAR_BACKEND=google")
        GoogleCalendar(cfg.google_client_secret_file, cfg.google_token_file, cfg.google_calendar_id, cfg.tz)
        print(f"Saved {cfg.google_token_file}")
        return

    if args[:1] == ["serve"]:  # HTTP channel for the ZeLabs gateway
        insecure = "--insecure" in args
        master = cfg.zelabs_identity_key or load_master_key()
        if not insecure and not master:
            raise SystemExit(
                "No ZELABS_IDENTITY_KEY / ZELABS_KEY_FILE found. Run behind the ZeLabs gateway "
                "or pass --insecure for local testing."
            )
        calendars = (
            SharedCalendarFactory(_calendar(cfg))
            if cfg.calendar_backend == "webcalendar"
            else GoogleCalendarFactory(
                cfg.google_client_secret_file,
                cfg.google_token_file,
                CalendarRegistry(cfg.calendar_registry_db),
                cfg.tz,
                cfg.share_role,
            )
        )
        pool = AgentPool(_extractor(cfg), calendars, Store(cfg.state_db), cfg.default_duration_min)
        mode = "insecure (local only)" if insecure else "gateway-verified"
        print(f"calendar-agent: http://{cfg.http_host}:{cfg.http_port} route={cfg.zelabs_route_id} {mode}")
        http.run(pool, cfg.tz, cfg.zelabs_route_id, master, insecure, cfg.http_host, cfg.http_port)
        return

    agent = Agent(
        _extractor(cfg),
        _calendar(cfg),
        Store(cfg.state_db),
        cfg.default_duration_min,
    )
    telegram.run(cfg, agent)


if __name__ == "__main__":
    main()
