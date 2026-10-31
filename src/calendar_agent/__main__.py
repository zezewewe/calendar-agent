from __future__ import annotations

import sys

from .agent import Agent
from .calendars.google import GoogleCalendar
from .channels import telegram
from .config import Config
from .extract import RouterExtractor
from .store import Store


def main() -> None:
    cfg = Config.from_env()
    if sys.argv[1:] == ["auth"]:  # one-time Google OAuth, then exit
        GoogleCalendar(cfg.google_client_secret_file, cfg.google_token_file, cfg.google_calendar_id, cfg.tz)
        print(f"Saved {cfg.google_token_file}")
        return
    agent = Agent(
        RouterExtractor(cfg.router_url, cfg.router_api_key, cfg.extract_model, cfg.tz),
        GoogleCalendar(cfg.google_client_secret_file, cfg.google_token_file, cfg.google_calendar_id, cfg.tz),
        Store(cfg.state_db),
        cfg.default_duration_min,
    )
    telegram.run(cfg, agent)


if __name__ == "__main__":
    main()
