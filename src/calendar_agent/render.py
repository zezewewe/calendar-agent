"""Human-readable proposal text (plain, no markup so any chat channel can show it)."""
from __future__ import annotations

from .models import Proposal


def render(p: Proposal) -> str:
    if not p.events:
        return "I couldn't find an event in that.\n" + "\n".join(f"- {q}" for q in p.questions)
    lines = [f"I found {len(p.events)} event(s):", ""]
    for i, e in enumerate(p.events):
        when = e.start.strftime("%a %d %b") if e.all_day else e.start.strftime("%a %d %b, %H:%M")
        if e.end and not e.all_day:
            when += e.end.strftime("-%H:%M")
        flag = " (unsure)" if e.confidence == "low" else ""
        lines.append(f"{i + 1}. {e.title} - {when}{flag}")
        if e.location:
            lines.append(f"   at {e.location}")
        for c in p.conflicts.get(i, []):
            lines.append(f"   CONFLICT: {c.title} ({c.start:%H:%M}-{c.end:%H:%M})")
    if p.questions:
        lines += ["", "Check:"] + [f"- {q}" for q in p.questions]
    return "\n".join(lines)
