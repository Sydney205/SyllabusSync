"""Central configuration loaded from environment variables (and a local .env file)."""

from __future__ import annotations

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_set(raw: str | None) -> set[int]:
    ids: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if part:
            try:
                ids.add(int(part))
            except ValueError:
                raise ValueError(f"ADMIN_USER_IDS contains a non-numeric value: {part!r}") from None
    return ids


# --- Google Calendar ---
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "service_account.json")
CALENDAR_ID = os.getenv("CALENDAR_ID", "").strip()
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Africa/Lagos").strip() or "Africa/Lagos"
TZ = ZoneInfo(DEFAULT_TIMEZONE)
DEFAULT_DURATION_MINUTES = int(os.getenv("DEFAULT_DURATION_MINUTES", "60"))

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_USER_IDS = _int_set(os.getenv("ADMIN_USER_IDS"))
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
# Public base URL of the deployed app, e.g. https://syllabussync.onrender.com
# When set (and not polling), the webhook is registered automatically on startup.
PUBLIC_BASE_URL = (
    os.getenv("PUBLIC_BASE_URL")
    or os.getenv("RENDER_EXTERNAL_URL")  # provided automatically by Render
    or ""
).strip().rstrip("/")
RUN_POLLING = _bool("RUN_POLLING", False)
# Polling with a token that already has a webhook would silently take messages away from the
# deployed bot, so startup refuses unless this is explicitly set to true.
FORCE_POLLING = _bool("FORCE_POLLING", False)

# --- Web UI ---
WEB_UI_PASSWORD = os.getenv("WEB_UI_PASSWORD", "")
