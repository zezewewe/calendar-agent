from __future__ import annotations

import sys

from .agent import Agent
from .calendars import SharedCalendarFactory
from .calendars.webcalendar import WebCalendarCalendar
from .channels import http, telegram
from .config import Config
from .extract import RouterExtractor
from .identity import load_master_key
from .pool import AgentPool
from .store import Store


def _extractor(cfg: Config) -> RouterExtractor:
    return RouterExtractor(cfg.router_url, cfg.router_api_key, cfg.extract_model, cfg.tz)


def _calendar(cfg: Config) -> WebCalendarCalendar:
    if cfg.calendar_backend != "webcalendar":
        raise SystemExit("CALENDAR_BACKEND must be 'webcalendar'")
    return WebCalendarCalendar(cfg.webcalendar_mcp_url, cfg.webcalendar_mcp_token, cfg.tz, cfg.family_members)


def main() -> None:
    cfg = Config.from_env()
    if cfg.calendar_backend != "webcalendar":
        raise SystemExit("CALENDAR_BACKEND must be 'webcalendar'")
    args = sys.argv[1:]

    if args[:1] == ["serve"]:  # HTTP channel for the ZeLabs gateway
        insecure = "--insecure" in args
        master = cfg.zelabs_identity_key or load_master_key()
        if not insecure and not master:
            raise SystemExit(
                "No ZELABS_IDENTITY_KEY / ZELABS_KEY_FILE found. Run behind the ZeLabs gateway "
                "or pass --insecure for local testing."
            )
        calendars = SharedCalendarFactory(_calendar(cfg))
        pool = AgentPool(_extractor(cfg), calendars, Store(cfg.state_db), cfg.default_duration_min)
        mode = "insecure (local only)" if insecure else "gateway-verified"
        print(f"calendar-agent: http://{cfg.http_host}:{cfg.http_port} route={cfg.zelabs_route_id} {mode}")
        http.run(pool, cfg.tz, cfg.zelabs_route_id, master, insecure, cfg.http_host, cfg.http_port)
        return

    if args[:1] == ["mcp"]:  # MCP server (stdio) wrapping the same Agent
        from .mcp import serve
        serve(Agent(_extractor(cfg), _calendar(cfg), Store(cfg.state_db), cfg.default_duration_min),
              cfg.mcp_user_id, cfg.tz)
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
