"""Google Calendar access via a Service Account.

The service account must be given "Make changes to events" access on the
shared calendar (Calendar settings -> Share with specific people).
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config
from labels import DEFAULT_LABEL, LABELS, label_from_event, normalize_label

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

_service = None
_service_lock = threading.Lock()


class CalendarConfigError(RuntimeError):
    """Raised when credentials or calendar settings are missing/invalid."""


@dataclass
class CreatedEvent:
    id: str
    title: str
    start: datetime
    end: datetime
    html_link: str
    updated_existing: bool  # True if an identical event already existed and was refreshed
    label: str = DEFAULT_LABEL


def _require_label(label: str | None) -> str:
    name = normalize_label(label)
    if name is None:
        raise ValueError(f"Unknown label {label!r}. Use one of: {', '.join(LABELS)}.")
    return name


def _label_fields(label: str) -> dict:
    """Event fields that store the label: Google colour + our own private property."""
    return {
        "colorId": LABELS[label].color_id,
        "extendedProperties": {"private": {"syllabussyncLabel": label}},
    }


def _load_credentials() -> service_account.Credentials:
    if config.GOOGLE_CREDENTIALS_JSON:
        try:
            info = json.loads(config.GOOGLE_CREDENTIALS_JSON)
        except json.JSONDecodeError as exc:
            raise CalendarConfigError(f"GOOGLE_CREDENTIALS_JSON is not valid JSON: {exc}") from exc
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    path = (config.GOOGLE_CREDENTIALS_FILE or "").strip()

    # Common mistake: pasting the key JSON into GOOGLE_CREDENTIALS_FILE. Accept it, and
    # never echo the value back, because it contains the private key.
    if path.startswith("{"):
        try:
            info = json.loads(path)
        except json.JSONDecodeError:
            raise CalendarConfigError(
                "GOOGLE_CREDENTIALS_FILE looks like JSON but isn't valid. It should be a file path; "
                "put the key's JSON in GOOGLE_CREDENTIALS_JSON instead."
            ) from None
        log.warning("GOOGLE_CREDENTIALS_FILE contains the key JSON; please rename it to GOOGLE_CREDENTIALS_JSON.")
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    if path and os.path.exists(path):
        return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)

    shown = path if len(path) <= 200 else path[:200] + "…"
    raise CalendarConfigError(
        "No Google credentials found. Set GOOGLE_CREDENTIALS_JSON, or set GOOGLE_CREDENTIALS_FILE "
        f"to the key file's path (currently {shown!r}, which doesn't exist)."
    )


def get_service():
    """Return a cached Calendar API client (built on first use)."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                if not config.CALENDAR_ID:
                    raise CalendarConfigError("CALENDAR_ID is not set.")
                creds = _load_credentials()
                _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return _service


def _localize(dt: datetime) -> datetime:
    """Attach the default timezone to naive datetimes."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=config.TZ)
    return dt


def _event_id(title: str, start: datetime) -> str:
    """Deterministic event id so re-posting the same announcement doesn't duplicate it.

    Google requires ids made of base32hex chars (0-9, a-v); hex digits qualify.
    """
    key = f"{title.strip().lower()}|{start.isoformat()}|{config.CALENDAR_ID}"
    return "ss" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:30]


def create_calendar_event(
    title: str,
    start_dt: datetime,
    end_dt: datetime,
    description: str = "",
    location: str = "",
    label: str = DEFAULT_LABEL,
) -> CreatedEvent:
    """Create (or refresh, if it already exists) an event on the shared calendar.

    This is a blocking call; from async code wrap it in ``asyncio.to_thread``.
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("Event title is required.")
    start_dt, end_dt = _localize(start_dt), _localize(end_dt)
    if end_dt <= start_dt:
        raise ValueError("End time must be after start time.")
    label = _require_label(label)

    service = get_service()
    event_id = _event_id(title, start_dt)
    body = {
        "id": event_id,
        "summary": title,
        "description": description,
        "location": location,
        "status": "confirmed",
        **_label_fields(label),
        "start": {"dateTime": start_dt.isoformat(), "timeZone": config.DEFAULT_TIMEZONE},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": config.DEFAULT_TIMEZONE},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 60},
                {"method": "popup", "minutes": 10},
            ],
        },
    }

    updated_existing = False
    try:
        result = service.events().insert(calendarId=config.CALENDAR_ID, body=body).execute()
    except HttpError as exc:
        if exc.resp is not None and exc.resp.status == 409:
            # Same title + start already exists (possibly deleted): refresh it instead.
            result = (
                service.events()
                .update(calendarId=config.CALENDAR_ID, eventId=event_id, body=body)
                .execute()
            )
            updated_existing = True
        else:
            raise

    invalidate_events_cache()
    log.info("Calendar event %s: %s @ %s", "updated" if updated_existing else "created", title, start_dt)
    return CreatedEvent(
        id=result["id"],
        title=title,
        start=start_dt,
        end=end_dt,
        html_link=result.get("htmlLink", ""),
        updated_existing=updated_existing,
        label=label,
    )


# ---------------------------------------------------------------------------
# Listing events (public schedule page)
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_BOILERPLATE_RE = re.compile(r"^(meeting link\s*:.*|added by syllabussync\.?)$", re.I)

EVENTS_CACHE_SECONDS = 60
MAX_LISTED_EVENTS = 1000

_cache: tuple[float, list["CalendarEvent"]] | None = None
_cache_lock = threading.Lock()


@dataclass
class CalendarEvent:
    id: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    meeting_link: str
    notes: str
    location: str
    label: str = DEFAULT_LABEL


class EventNotFound(LookupError):
    """The event id doesn't exist on the shared calendar."""


def update_calendar_event(
    event_id: str,
    title: str,
    start_dt: datetime,
    end_dt: datetime,
    description: str = "",
    location: str = "",
    label: str = DEFAULT_LABEL,
) -> None:
    """Change an existing event's title, time, label, description and location (blocking)."""
    title = (title or "").strip()
    if not title:
        raise ValueError("Event title is required.")
    start_dt, end_dt = _localize(start_dt), _localize(end_dt)
    if end_dt <= start_dt:
        raise ValueError("End time must be after start time.")
    label = _require_label(label)
    body = {
        **_label_fields(label),
        "summary": title,
        "description": description,
        "location": location,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": config.DEFAULT_TIMEZONE, "date": None},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": config.DEFAULT_TIMEZONE, "date": None},
    }
    try:
        get_service().events().patch(calendarId=config.CALENDAR_ID, eventId=event_id, body=body).execute()
    except HttpError as exc:
        if exc.resp is not None and exc.resp.status in (404, 410):
            raise EventNotFound(event_id) from exc
        raise
    invalidate_events_cache()
    log.info("Calendar event updated: %s (%s)", title, event_id)


def delete_calendar_event(event_id: str) -> None:
    """Delete an event (blocking). Deleting an already-deleted event is not an error."""
    try:
        get_service().events().delete(calendarId=config.CALENDAR_ID, eventId=event_id).execute()
    except HttpError as exc:
        if exc.resp is None or exc.resp.status not in (404, 410):
            raise
    invalidate_events_cache()
    log.info("Calendar event deleted: %s", event_id)


def get_calendar_event(event_id: str) -> CalendarEvent:
    """Fetch one event fresh from Google (blocking)."""
    try:
        item = get_service().events().get(calendarId=config.CALENDAR_ID, eventId=event_id).execute()
    except HttpError as exc:
        if exc.resp is not None and exc.resp.status in (404, 410):
            raise EventNotFound(event_id) from exc
        raise
    ev = _to_event(item)
    if ev is None:
        raise EventNotFound(event_id)
    return ev


def invalidate_events_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def _parse_when(when: dict) -> tuple[datetime, bool]:
    if "dateTime" in when:
        dt = datetime.fromisoformat(when["dateTime"].replace("Z", "+00:00"))
        return dt.astimezone(config.TZ), False
    d = date.fromisoformat(when["date"])
    return datetime(d.year, d.month, d.day, tzinfo=config.TZ), True


def _clean_description(raw: str) -> str:
    """Turn a (possibly HTML) event description into plain notes, minus our boilerplate."""
    text = re.sub(r"<br\s*/?>|</p>|</div>", "\n", raw or "", flags=re.I)
    text = html.unescape(_TAG_RE.sub("", text))
    lines = [l.strip() for l in text.splitlines()]
    kept = [l for l in lines if l and not _BOILERPLATE_RE.match(l)]
    return "\n".join(kept)


def _to_event(item: dict) -> CalendarEvent | None:
    if item.get("status") == "cancelled" or "start" not in item:
        return None
    start, all_day = _parse_when(item["start"])
    end, _ = _parse_when(item.get("end", item["start"]))
    description = item.get("description", "")
    location = item.get("location", "")

    link = item.get("hangoutLink", "")
    if not link:
        m = _URL_RE.search(location) or _URL_RE.search(html.unescape(description))
        link = m.group(0).rstrip(".,") if m else ""

    notes = _clean_description(description)
    if link:
        notes = "\n".join(l for l in notes.splitlines() if l != link)

    return CalendarEvent(
        id=item["id"],
        title=item.get("summary") or "(untitled)",
        start=start,
        end=end,
        all_day=all_day,
        meeting_link=link,
        notes=notes,
        location="" if location == link else location,
        label=label_from_event(
            item.get("extendedProperties", {}).get("private", {}).get("syllabussyncLabel"),
            item.get("colorId"),
        ),
    )


def list_calendar_events() -> list[CalendarEvent]:
    """All events on the shared calendar, oldest first (cached for a minute).

    Blocking call; from async code wrap it in ``asyncio.to_thread``.
    """
    global _cache
    with _cache_lock:
        if _cache and time.monotonic() - _cache[0] < EVENTS_CACHE_SECONDS:
            return _cache[1]

    service = get_service()
    events: list[CalendarEvent] = []
    page_token = None
    while len(events) < MAX_LISTED_EVENTS:
        resp = (
            service.events()
            .list(
                calendarId=config.CALENDAR_ID,
                singleEvents=True,  # expand recurring events into occurrences
                orderBy="startTime",
                maxResults=250,
                pageToken=page_token,
            )
            .execute()
        )
        for item in resp.get("items", []):
            ev = _to_event(item)
            if ev:
                events.append(ev)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    with _cache_lock:
        _cache = (time.monotonic(), events)
    return events
