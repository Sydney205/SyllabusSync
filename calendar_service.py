"""Google Calendar access via a Service Account.

The service account must be given "Make changes to events" access on the
shared calendar (Calendar settings -> Share with specific people).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config

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


def _load_credentials() -> service_account.Credentials:
    if config.GOOGLE_CREDENTIALS_JSON:
        try:
            info = json.loads(config.GOOGLE_CREDENTIALS_JSON)
        except json.JSONDecodeError as exc:
            raise CalendarConfigError(f"GOOGLE_CREDENTIALS_JSON is not valid JSON: {exc}") from exc
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    path = config.GOOGLE_CREDENTIALS_FILE
    if os.path.exists(path):
        return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)

    raise CalendarConfigError(
        "No Google credentials found. Set GOOGLE_CREDENTIALS_JSON or place "
        f"a service account key at {path!r}."
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

    service = get_service()
    event_id = _event_id(title, start_dt)
    body = {
        "id": event_id,
        "summary": title,
        "description": description,
        "location": location,
        "status": "confirmed",
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

    log.info("Calendar event %s: %s @ %s", "updated" if updated_existing else "created", title, start_dt)
    return CreatedEvent(
        id=result["id"],
        title=title,
        start=start_dt,
        end=end_dt,
        html_link=result.get("htmlLink", ""),
        updated_existing=updated_existing,
    )
