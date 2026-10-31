"""Plain data types shared by every layer. No I/O here."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    mime: str  # e.g. image/png
    data: bytes


class Inbound(BaseModel):
    """One thing the user sent: text, images, or both (e.g. a forwarded chat)."""

    user_id: str
    text: str = ""
    attachments: list[Attachment] = Field(default_factory=list)
    received_at: datetime  # timezone-aware; anchors relative dates


class ProposedEvent(BaseModel):
    title: str
    start: datetime  # timezone-aware
    end: datetime | None = None  # None -> default duration applied later
    all_day: bool = False
    location: str | None = None
    notes: str | None = None
    confidence: Literal["high", "medium", "low"] = "medium"
    # Why the model chose these values / what it was unsure about.
    rationale: str | None = None


class ExistingEvent(BaseModel):
    id: str
    title: str
    start: datetime
    end: datetime


class Proposal(BaseModel):
    id: str
    user_id: str
    events: list[ProposedEvent]
    # index into `events` -> existing events it overlaps
    conflicts: dict[int, list[ExistingEvent]] = Field(default_factory=dict)
    # Things the model could not determine; shown to the user.
    questions: list[str] = Field(default_factory=list)
