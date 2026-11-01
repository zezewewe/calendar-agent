"""One Agent per user, sharing the stateless extractor and the proposals store.

The `Agent` keeps a single `Calendar`, so multi-user access means building an
Agent bound to the caller's calendar on each request. The extractor (model-router)
and the `Store` (keyed by `user_id`) are shared; only the calendar differs.
"""
from __future__ import annotations

from typing import Protocol

from .agent import Agent
from .extract import Extractor
from .store import Store


class CalendarForUser(Protocol):
    def for_user(self, email: str): ...


class AgentPool:
    def __init__(self, extractor: Extractor, calendars: CalendarForUser, store: Store, default_duration_min: int = 60):
        self._x, self._cals, self._store, self._dur = extractor, calendars, store, default_duration_min

    def for_user(self, email: str) -> Agent:
        return Agent(self._x, self._cals.for_user(email), self._store, self._dur)
