"""Template rendering helpers for the server-rendered web UI."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path
from urllib.parse import urlencode

from starlette.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# Short, fixed status messages shown after a redirect (/admin?done=created).
# Using codes instead of free text means nobody can craft a link that shows a fake message.
DONE_MESSAGES = {
    "created": "Event added to the calendar.",
    "updated": "Event updated.",
    "deleted": "Event deleted.",
}


def login_page(request, error: str | None = None, status_code: int = 200):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error},
        status_code=status_code,
    )


# ---------------------------------------------------------------------------
# Event cards
# ---------------------------------------------------------------------------

_COURSE_CODE_RE = re.compile(r"^((?:[A-Z]{2,}-)?[A-Z]{2,4}\s?\d{3}[A-Z]?)\b")


def _clock(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _day_label(d, today) -> str:
    if d == today:
        return "Today"
    if d == today + timedelta(days=1):
        return "Tomorrow"
    if d == today - timedelta(days=1):
        return "Yesterday"
    fmt = f"%A, {d.day} %B" + (" %Y" if d.year != today.year else "")
    return d.strftime(fmt)


def _add_to_calendar_url(ev) -> str:
    """Google Calendar 'create event' link that works for anyone, not just calendar members."""
    if ev.all_day:
        dates = f"{ev.start:%Y%m%d}/{ev.end:%Y%m%d}"
    else:
        utc = timezone.utc
        dates = f"{ev.start.astimezone(utc):%Y%m%dT%H%M%SZ}/{ev.end.astimezone(utc):%Y%m%dT%H%M%SZ}"
    details = "\n\n".join(p for p in (ev.meeting_link, ev.notes) if p)
    params = {"action": "TEMPLATE", "text": ev.title, "dates": dates, "details": details}
    if ev.meeting_link or ev.location:
        params["location"] = ev.meeting_link or ev.location
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def _card(ev, now: datetime) -> dict:
    code_match = _COURSE_CODE_RE.match(ev.title)
    title = re.sub(r"\s*\(revision\)\s*$", "", ev.title, flags=re.I)
    return {
        "id": ev.id,
        "title": title,
        "full_title": ev.title,
        "code": code_match.group(1) if code_match else "",
        "is_revision": "revision" in ev.title.lower(),
        "live": ev.start <= now < ev.end,
        "time_label": "All day" if ev.all_day else f"{_clock(ev.start)} – {_clock(ev.end)}",
        "month": ev.start.strftime("%b"),
        "day": ev.start.day,
        "weekday": ev.start.strftime("%a"),
        "meeting_link": ev.meeting_link,
        "location": ev.location,
        "notes": ev.notes,
        "add_url": _add_to_calendar_url(ev),
        "iso_start": ev.start.isoformat(),
    }


def _group_by_day(events, now: datetime) -> list[dict]:
    today = now.date()
    return [
        {"label": _day_label(day, today), "cards": [_card(e, now) for e in items]}
        for day, items in groupby(events, key=lambda e: e.start.date())
    ]


def _schedule_context(events, now: datetime) -> dict:
    events = events or []
    upcoming = [e for e in events if e.end > now]
    past = sorted((e for e in events if e.end <= now), key=lambda e: e.start, reverse=True)
    return {
        "upcoming_groups": _group_by_day(upcoming, now),
        "past_groups": _group_by_day(past, now),
        "upcoming_count": len(upcoming),
        "past_count": len(past),
    }


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def events_page(request, events, *, now: datetime, error: str | None = None, timezone_name: str = ""):
    """Public schedule (GET /)."""
    return templates.TemplateResponse(
        request=request,
        name="events.html",
        context={**_schedule_context(events, now), "error": error, "timezone": timezone_name, "admin": False},
        status_code=503 if error else 200,
    )


def admin_page(
    request,
    events,
    *,
    now: datetime,
    timezone_name: str,
    list_error: str | None = None,
    message: str | None = None,
    ok: bool = True,
    values: dict | None = None,
    show_logout: bool = False,
    status_code: int = 200,
):
    """Admin dashboard (GET /admin): create form + every event with edit/delete."""
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            **_schedule_context(events, now),
            "error": list_error,
            "message": message,
            "ok": ok,
            "values": values or {},
            "open_form": bool(values),  # keep the create form expanded after a validation error
            "show_logout": show_logout,
            "timezone": timezone_name,
            "admin": True,
        },
        status_code=status_code,
    )


def edit_page(
    request,
    *,
    event_id: str,
    values: dict,
    timezone_name: str,
    message: str | None = None,
    was_all_day: bool = False,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="edit.html",
        context={
            "event_id": event_id,
            "values": values,
            "message": message,
            "was_all_day": was_all_day,
            "timezone": timezone_name,
        },
        status_code=status_code,
    )
