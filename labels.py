"""The four schedule labels, their colours, and text classification for the bot."""

from __future__ import annotations

import re
from dataclasses import dataclass

LIVE_CLASS = "Live class"


@dataclass(frozen=True)
class Label:
    name: str
    color: str  # hex used by the web UI
    color_id: str  # Google Calendar event colorId
    slug: str  # CSS-friendly name


LABELS: dict[str, Label] = {
    "Exam": Label("Exam", "#DC2626", "11", "exam"),
    "Meeting": Label("Meeting", "#D97706", "5", "meeting"),
    "Event": Label("Event", "#059669", "10", "event"),
    LIVE_CLASS: Label(LIVE_CLASS, "#2563EB", "9", "live-class"),
}
DEFAULT_LABEL = LIVE_CLASS

_BY_COLOR_ID = {lbl.color_id: lbl.name for lbl in LABELS.values()}
_BY_LOWER = {name.lower(): name for name in LABELS}


def normalize_label(value: str | None) -> str | None:
    """Return the canonical label name for user input (case-insensitive), or None."""
    if not value:
        return None
    return _BY_LOWER.get(value.strip().lower())


def label_from_event(private_label: str | None, color_id: str | None) -> str:
    """Work out an existing calendar event's label.

    Prefer the label we stored on the event, then its colour. Events with neither were
    created before labels existed (by the bot), so they count as Live classes.
    """
    return normalize_label(private_label) or _BY_COLOR_ID.get(color_id or "") or DEFAULT_LABEL


# ---------------------------------------------------------------------------
# Classifying announcement text (used by the Telegram bot)
# ---------------------------------------------------------------------------

_STRONG_CLASS = re.compile(
    r"\blive[\s-]+(lessons?|class(es)?|sessions?)\b|\blectures?\b|\btutorials?\b"
    r"|\brevision\s+(class(es)?|lessons?|sessions?)\b",
    re.I,
)
_EXAM = re.compile(
    r"\b(exams?|examinations?|tests?|quiz(zes)?|cbt|mid-?terms?|assessments?)\b", re.I
)
_MEETING = re.compile(
    r"\b(meetings?|town\s?halls?|stand-?ups?|consultations?|orientations?|meet-?ups?)\b", re.I
)
_EVENT = re.compile(
    r"\b(events?|webinars?|seminars?|parties|party|hangouts?|ceremon(y|ies)|programmes?|programs?"
    r"|conferences?|workshops?|celebrations?|matriculations?|convocations?|competitions?)\b",
    re.I,
)
_WEAK_CLASS = re.compile(r"\bclass(es)?\b|\blessons?\b|\b(?:[A-Z]{2,}-)?[A-Z]{2,4}\s?\d{3}\b")


def classify_text(text: str) -> str:
    """Guess which label an announcement is about.

    Order matters: explicit class phrases ("live lesson", "lecture") win, so a class
    that mentions upcoming exams is still a class. Then exam, meeting and event words.
    Then weaker class hints (the word "class", a course code). Anything else is an Event.
    """
    if _STRONG_CLASS.search(text):
        return LIVE_CLASS
    if _EXAM.search(text):
        return "Exam"
    if _MEETING.search(text):
        return "Meeting"
    if _EVENT.search(text):
        return "Event"
    if _WEAK_CLASS.search(text):
        return LIVE_CLASS
    return "Event"
