from __future__ import annotations

import sys

from .agent import Agent
from .calendars.google import GoogleCalendar
from .calendars.peruser import GoogleCalendarFactory
from .calendars.registry import CalendarRegistry
from .channels import http, telegram
from .config import Config
from .extract import RouterExtractor
from .identity import load_master_key
from .pool import AgentPool
from .store import Store


def _extractor(cfg: Config) -> RouterExtractor:
    return RouterExtractor(cfg.router_url, cfg.router_api_key, cfg.extract_model, cfg.tz)


def main() -> None:
    cfg = Config.from_env()
    args = sys.argv[1:]
    if args == ["auth"]:  # one-time Google OAuth, then exit
        GoogleCalendar(cfg.google_client_secret_file, cfg.google_token_file, cfg.google_calendar_id, cfg.tz)
        print(f"Saved {cfg.google_token_file}")
        return

    if args[:1] == ["serve"]:  # HTTP channel for the ZeLabs gateway, one calendar per user
        insecure = "--insecure" in args
        master = cfg.zelabs_identity_key or load_master_key()
        if not insecure and not master:
            raise SystemExit(
                "No ZELABS_IDENTITY_KEY / ZELABS_KEY_FILE found. Run behind the ZeLabs gateway "
                "or pass --insecure for local testing."
            )
        pool = AgentPool(
            _extractor(cfg),
            GoogleCalendarFactory(
                cfg.google_client_secret_file,
                cfg.google_token_file,
                CalendarRegistry(cfg.calendar_registry_db),
                cfg.tz,
                cfg.share_role,
            ),
            Store(cfg.state_db),
            cfg.default_duration_min,
        )
        mode = "insecure (local only)" if insecure else "gateway-verified"
        print(f"calendar-agent: http://{cfg.http_host}:{cfg.http_port} route={cfg.zelabs_route_id} {mode}")
        http.run(pool, cfg.tz, cfg.zelabs_route_id, master, insecure, cfg.http_host, cfg.http_port)
        return

    agent = Agent(
        _extractor(cfg),
        GoogleCalendar(cfg.google_client_secret_file, cfg.google_token_file, cfg.google_calendar_id, cfg.tz),
        Store(cfg.state_db),
        cfg.default_duration_min,
    )
    telegram.run(cfg, agent)


if __name__ == "__main__":
    main()
