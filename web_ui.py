"""View models + template rendering for the Google-Calendar-style web UI."""

from __future__ import annotations

import calendar
import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path
from urllib.parse import urlencode

from starlette.templating import Jinja2Templates

from labels import DEFAULT_LABEL, LABELS

ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def _static_version() -> str:
    """Short hash of the stylesheet, appended as ?v=... so browsers and CDNs fetch the
    new CSS after every deploy instead of serving a stale cached copy."""
    try:
        return hashlib.sha1((STATIC_DIR / "styles.css").read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


templates.env.globals["static_version"] = _static_version()

VIEWS = ("month", "agenda")
MAX_CHIPS_PER_DAY = 3
WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]  # Google Calendar's default week

# Fixed status messages shown after a redirect (/admin?done=created). Codes, not free
# text, so nobody can craft a link that shows a fake message.
DONE_MESSAGES = {
    "created": "Event added to the calendar.",
    "updated": "Event updated.",
    "deleted": "Event deleted.",
    "quiet_on": "Quiet mode is on. The bot won't post in Telegram groups.",
    "quiet_off": "Quiet mode is off. The bot will confirm in Telegram groups.",
}


def label_options() -> list[dict]:
    return [{"name": l.name, "slug": l.slug, "color": l.color} for l in LABELS.values()]


def login_page(request, error: str | None = None, status_code: int = 200):
    return templates.TemplateResponse(
        request=request, name="login.html", context={"error": error}, status_code=status_code
    )


# ---------------------------------------------------------------------------
# Event cards
# ---------------------------------------------------------------------------


def _clock(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")


def _short_clock(dt: datetime) -> str:
    """Google-style compact time: 3pm, 4:30pm."""
    s = dt.strftime("%I:%M%p").lstrip("0").lower()
    return s.replace(":00", "")


def _long_date(d: date) -> str:
    return d.strftime(f"%A, {d.day} %B %Y")


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
    lbl = LABELS.get(ev.label) or LABELS[DEFAULT_LABEL]
    return {
        "id": ev.id,
        "dom_id": "ev-" + re.sub(r"[^A-Za-z0-9_-]", "", ev.id),
        "title": ev.title,
        "label": lbl.name,
        "slug": lbl.slug,
        "color": lbl.color,
        "all_day": ev.all_day,
        "live": ev.start <= now < ev.end,
        "past": ev.end <= now,
        "short_time": "" if ev.all_day else _short_clock(ev.start),
        "time_label": "All day" if ev.all_day else f"{_clock(ev.start)} – {_clock(ev.end)}",
        "date_label": _long_date(ev.start.date()),
        "meeting_link": ev.meeting_link,
        "location": ev.location,
        "notes": ev.notes,
        "add_url": _add_to_calendar_url(ev),
        "iso_start": ev.start.isoformat(),
    }


# ---------------------------------------------------------------------------
# Calendar page
# ---------------------------------------------------------------------------


def parse_view_args(view: str | None, date_str: str | None, label: str | None, today: date):
    view = view if view in VIEWS else "month"
    try:
        anchor = date.fromisoformat(date_str) if date_str else today
    except ValueError:
        anchor = today
    label = label if label in LABELS else None
    return view, anchor, label


def _shift_month(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _url(base: str, **params) -> str:
    clean = {k: v for k, v in params.items() if v}
    return base + ("?" + urlencode(clean) if clean else "")


def calendar_page(
    request,
    events,
    *,
    admin: bool,
    view: str,
    anchor: date,
    label_filter: str | None,
    now: datetime,
    timezone_name: str,
    error: str | None = None,
    message: str | None = None,
    ok: bool = True,
    quiet: dict | None = None,
    show_logout: bool = False,
    status_code: int = 200,
):
    base = "/admin" if admin else "/"
    today = now.date()
    month_start = anchor.replace(day=1)
    month_end = _shift_month(month_start, 1)  # exclusive

    visible = [e for e in (events or []) if not label_filter or e.label == label_filter]
    by_day: dict[date, list] = {}
    for e in visible:
        by_day.setdefault(e.start.date(), []).append(e)

    # Month grid: whole weeks (Sunday first) covering the month.
    grid_start = month_start - timedelta(days=(month_start.weekday() + 1) % 7)
    weeks, cursor = [], grid_start
    while cursor < month_end or len(weeks) < 5:
        week = []
        for _ in range(7):
            day_events = [_card(e, now) for e in by_day.get(cursor, [])]
            week.append(
                {
                    "date": cursor,
                    "day": cursor.day,
                    "in_month": cursor.month == anchor.month,
                    "is_today": cursor == today,
                    "chips": day_events[:MAX_CHIPS_PER_DAY],
                    "more": max(0, len(day_events) - MAX_CHIPS_PER_DAY),
                    "agenda_url": _url(base, view="agenda", date=cursor.isoformat(), label=label_filter)
                    + f"#day-{cursor.isoformat()}",
                    "create_url": f"/admin/events/new?date={cursor.isoformat()}" if admin else "",
                }
            )
            cursor += timedelta(days=1)
        weeks.append(week)

    # Agenda: every day in the month that has events.
    month_events = sorted((e for e in visible if month_start <= e.start.date() < month_end), key=lambda e: e.start)
    agenda = [
        {
            "date": d,
            "dom_id": f"day-{d.isoformat()}",
            "weekday": d.strftime("%a"),
            "day": d.day,
            "month": d.strftime("%b"),
            "is_today": d == today,
            "past": d < today,
            "cards": [_card(e, now) for e in items],
        }
        for d, items in groupby(month_events, key=lambda e: e.start.date())
    ]

    # Every event that can be clicked on this page gets a details popover.
    shown_ids, popovers = set(), []
    for e in visible:
        if grid_start <= e.start.date() < cursor or month_start <= e.start.date() < month_end:
            if e.id not in shown_ids:
                shown_ids.add(e.id)
                popovers.append(_card(e, now))

    nav = {
        "prev": _url(base, view=view, date=_shift_month(month_start, -1).isoformat(), label=label_filter),
        "next": _url(base, view=view, date=_shift_month(month_start, 1).isoformat(), label=label_filter),
        "today": _url(base, view=view, label=label_filter),
        "month": _url(base, view="month", date=anchor.isoformat(), label=label_filter),
        "agenda": _url(base, view="agenda", date=anchor.isoformat(), label=label_filter),
    }
    filters = [
        {
            **opt,
            "active": label_filter == opt["name"],
            "url": _url(
                base, view=view, date=anchor.isoformat(),
                label=None if label_filter == opt["name"] else opt["name"],
            ),
            "count": sum(1 for e in events or [] if e.label == opt["name"] and month_start <= e.start.date() < month_end),
        }
        for opt in label_options()
    ]

    return templates.TemplateResponse(
        request=request,
        name="calendar.html",
        context={
            "admin": admin,
            "view": view,
            "title": anchor.strftime("%B %Y"),
            "weekdays": WEEKDAYS,
            "weeks": weeks,
            "agenda": agenda,
            "popovers": popovers,
            "nav": nav,
            "filters": filters,
            "label_filter": label_filter,
            "clear_filter_url": _url(base, view=view, date=anchor.isoformat()),
            "error": error,
            "message": message,
            "ok": ok,
            "quiet": quiet,
            "show_logout": show_logout,
            "timezone": timezone_name,
            "return_to": _url(base, view=view, date=anchor.isoformat(), label=label_filter),
        },
        status_code=status_code,
    )


# ---------------------------------------------------------------------------
# Create / edit form
# ---------------------------------------------------------------------------


def event_form_page(
    request,
    *,
    mode: str,  # "create" or "edit"
    values: dict,
    timezone_name: str,
    event_id: str | None = None,
    message: str | None = None,
    was_all_day: bool = False,
    status_code: int = 200,
):
    values = {"label": DEFAULT_LABEL, "duration": 60, **values}
    return templates.TemplateResponse(
        request=request,
        name="event_form.html",
        context={
            "mode": mode,
            "event_id": event_id,
            "values": values,
            "labels": label_options(),
            "message": message,
            "was_all_day": was_all_day,
            "timezone": timezone_name,
            "action": f"/events/{event_id}" if mode == "edit" else "/events",
        },
        status_code=status_code,
    )
