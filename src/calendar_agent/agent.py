"""Channel-agnostic core: Inbound -> Proposal (no side effects) -> confirm() writes to the calendar."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from .calendars import Calendar
from .extract import Extraction, Extractor
from .models import ExistingEvent, Inbound, Proposal, ProposedEvent
from .store import Store


def resolve_end(e: ProposedEvent, default_minutes: int) -> datetime:
    if e.end:
        return e.end
    if e.all_day:
        return datetime.combine(e.start.date() + timedelta(days=1), datetime.min.time(), tzinfo=e.start.tzinfo)
    return e.start + timedelta(minutes=default_minutes)


def overlaps(a_start: datetime, a_end: datetime, b: ExistingEvent) -> bool:
    return a_start < b.end and b.start < a_end


def _snapshot(title: str, start: datetime, end: datetime | None, all_day: bool, location, notes) -> str:
    """The ProposedEvent JSON used to recreate an event if an update/delete is undone."""
    return ProposedEvent(title=title, start=start, end=end, all_day=all_day, location=location, notes=notes).model_dump_json()


class Agent:
    def __init__(self, extractor: Extractor, calendar: Calendar, store: Store, default_duration_min: int = 60):
        self._x, self._cal, self._store, self._dur = extractor, calendar, store, default_duration_min

    def _build(self, proposal_id: str, user_id: str, ext: Extraction, intent: str = "create") -> Proposal:
        events = sorted(ext.events, key=lambda e: e.start)
        conflicts: dict[int, list[ExistingEvent]] = {}
        actionable = [(i, e) for i, e in enumerate(events) if e.action != "delete"]
        if actionable:
            ends = [resolve_end(e, self._dur) for _, e in actionable]
            existing = self._cal.list_events(min(e.start for _, e in actionable), max(ends))
            for (i, e), end in zip(actionable, ends):
                # A change must not conflict with the very event it replaces.
                hits = [x for x in existing if x.id != e.target_id and overlaps(e.start, end, x)]
                if hits:
                    conflicts[i] = hits
        p = Proposal(id=proposal_id, user_id=user_id, events=events, conflicts=conflicts,
                     questions=ext.questions, intent=intent)
        if events:
            self._store.save(p)
        return p

    def propose(self, inbound: Inbound) -> Proposal:
        ext = self._x.extract(inbound)
        if ext.intent in ("update", "delete", "query"):
            return self._change(inbound, ext)
        return self._build(uuid.uuid4().hex[:12], inbound.user_id, ext, "create")

    def _change(self, inbound: Inbound, ext: Extraction) -> Proposal:
        """List the events the instruction covers, then ask the model which to change."""
        start = ext.range_start or inbound.received_at
        end = ext.range_end or (start + timedelta(days=30))
        existing = self._cal.list_events(start, end)
        pid = uuid.uuid4().hex[:12]
        if ext.intent == "query":
            return Proposal(id=pid, user_id=inbound.user_id, events=[], listing=existing,
                            questions=ext.questions, intent="query")
        if not existing:
            return Proposal(id=pid, user_id=inbound.user_id, events=[],
                            questions=["I couldn't find any events to change there."], intent=ext.intent)
        plan = self._x.plan(inbound.text, existing, inbound.received_at)
        return self._build(pid, inbound.user_id, plan, ext.intent)

    def revise(self, proposal_id: str, user_id: str, instruction: str, now: datetime) -> Proposal | None:
        """Edit a still-pending proposal. Returns the new proposal, or None if gone/not yours.

        The old proposal is claimed (removed) only after the revision succeeded, so a failed
        LLM call leaves the original confirmable.
        """
        old = self._store.get(proposal_id)
        if old is None or old.user_id != user_id:
            return None
        ext = self._x.revise(old, instruction, now)
        if self._store.take(proposal_id) is None:  # confirmed/cancelled while we were thinking
            return None
        return self._build(uuid.uuid4().hex[:12], user_id, ext)

    def agenda(self, start: datetime, end: datetime) -> list[ExistingEvent]:
        return self._cal.list_events(start, end)

    def link_message(self, chat_id: str, message_id: str, proposal_id: str) -> None:
        self._store.link_message(chat_id, message_id, proposal_id)

    def proposal_for_message(self, chat_id: str, message_id: str) -> str | None:
        return self._store.proposal_for_message(chat_id, message_id)

    def confirm(self, proposal_id: str, user_id: str) -> list[str] | None:
        """Apply the change set. Returns created ids, or None if the proposal is gone/not yours."""
        p = self._store.get(proposal_id)
        if p is None or p.user_id != user_id:
            return None
        p = self._store.take(proposal_id)  # atomic claim; None if a concurrent confirm won
        if p is None:
            return None
        ids: list[str] = []
        for e in p.events:
            if e.action == "delete":
                if not e.target_id:
                    continue
                self._cal.delete_event(e.target_id)
                self._store.journal_remove(p.id, user_id, e.target_id, _snapshot(e.title, e.start, e.end, e.all_day, e.location, e.notes))
            elif e.action == "update":
                if e.target_id:
                    self._cal.delete_event(e.target_id)
                    if e.previous is not None:
                        old = e.previous
                        self._store.journal_remove(p.id, user_id, e.target_id, _snapshot(old.title, old.start, old.end, old.all_day, old.location, old.notes))
                new_id = self._cal.create_event(e, resolve_end(e, self._dur), user_id=user_id)
                ids.append(new_id)
                self._store.journal_add(p.id, user_id, new_id)
            else:
                new_id = self._cal.create_event(e, resolve_end(e, self._dur), user_id=user_id)
                ids.append(new_id)
                self._store.journal_add(p.id, user_id, new_id)
        return ids

    def cancel(self, proposal_id: str, user_id: str) -> bool:
        p = self._store.get(proposal_id)
        return bool(p and p.user_id == user_id and self._store.take(proposal_id))

    def undo(self, proposal_id: str, user_id: str | None = None) -> int:
        """Reverse what this proposal did. Pass `user_id` to scope it to one user."""
        rows = self._store.take_journal(proposal_id, user_id)
        n = 0
        for kind, event_id, snapshot in rows:
            if kind == "add":
                self._cal.delete_event(event_id)
                n += 1
            elif snapshot:
                restored = ProposedEvent.model_validate_json(snapshot)
                self._cal.create_event(restored, resolve_end(restored, self._dur))
                n += 1
        return n
