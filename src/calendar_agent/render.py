"""Human-readable proposal text (plain, no markup so any chat channel can show it)."""
from __future__ import annotations

import re

from .models import ExistingEvent, Proposal

_MEMBER = re.compile(r"\[family-member:([^\]]+)\]")
_VERB = {"create": "ADD", "update": "CHANGE", "delete": "REMOVE"}


def clean_notes(notes: str | None) -> str:
    """Notes with the internal member marker stripped, for display."""
    return _MEMBER.sub("", notes or "").strip()


def member_label(notes: str | None, members: dict[str, str]) -> str:
    match = _MEMBER.search(notes or "")
    if not match:
        return ""
    member_id = match.group(1)
    return f" ({members.get(member_id, member_id)})"


def _when(event) -> str:
    if event.all_day:
        return event.start.strftime("%a %d %b")
    text = event.start.strftime("%a %d %b, %H:%M")
    if event.end:
        text += event.end.strftime("-%H:%M")
    return text


def render_agenda(events: list[ExistingEvent], members: dict[str, str] | None = None) -> str:
    members = members or {}
    if not events:
        return "Nothing on the calendar for that period."
    lines = [f"{len(events)} event(s):", ""]
    for e in events:
        lines.append(f"- {_when(e)}  {e.title}{member_label(e.notes, members)}")
    return "\n".join(lines)


def render(p: Proposal, members: dict[str, str] | None = None) -> str:
    members = members or {}
    if p.intent == "query":
        body = render_agenda(p.listing, members) if p.listing else "Nothing on the calendar for that period."
        return f"{p.answer}\n\n{body}" if p.answer else body
    if not p.events:
        head = "I couldn't find an event in that."
        return head + ("\n" + "\n".join(f"- {q}" for q in p.questions) if p.questions else "")

    lines = [f"Here's what I'll do ({len(p.events)} change(s)):", ""]
    for i, e in enumerate(p.events):
        flag = " (unsure)" if e.confidence == "low" else ""
        lines.append(f"{i + 1}. {_VERB.get(e.action, 'ADD')}: {e.title} - {_when(e)}{flag}")
        if e.location:
            lines.append(f"   at {e.location}")
        for c in p.conflicts.get(i, []):
            lines.append(f"   CONFLICT: {c.title} ({c.start:%H:%M}-{c.end:%H:%M})")
    if p.questions:
        lines += ["", "Check:"] + [f"- {q}" for q in p.questions]
    return "\n".join(lines)
