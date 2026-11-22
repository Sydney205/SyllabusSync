"""Runtime bot settings that can be changed without redeploying (currently: quiet mode).

The starting value comes from BOT_QUIET_MODE. Changes made from the web UI or the
/quiet command are saved to a small JSON file (BOT_STATE_FILE, default bot_state.json)
so they survive restarts. On hosts with a temporary disk (e.g. Render's free tier)
that file is wiped on redeploy/spin-down, and the value falls back to BOT_QUIET_MODE.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

import config

log = logging.getLogger(__name__)

_lock = threading.Lock()
_state: dict | None = None


def _load() -> dict:
    global _state
    if _state is None:
        state = {"quiet_mode": config.BOT_QUIET_MODE, "changed_by": "BOT_QUIET_MODE", "changed_at": None}
        try:
            with open(config.BOT_STATE_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved.get("quiet_mode"), bool):
                state.update({k: saved.get(k) for k in ("quiet_mode", "changed_by", "changed_at")})
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable %s: %s", config.BOT_STATE_FILE, exc)
        _state = state
    return _state


def get_quiet_mode() -> bool:
    with _lock:
        return bool(_load()["quiet_mode"])


def quiet_status() -> dict:
    with _lock:
        return dict(_load())


def set_quiet_mode(on: bool, changed_by: str) -> None:
    with _lock:
        state = _load()
        state.update(
            quiet_mode=bool(on),
            changed_by=changed_by,
            changed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        tmp = config.BOT_STATE_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, config.BOT_STATE_FILE)
        except OSError as exc:  # read-only disk: keep the in-memory value
            log.warning("Couldn't save %s (%s); quiet mode will reset on restart.", config.BOT_STATE_FILE, exc)
    log.info("Quiet mode %s (by %s)", "ON" if on else "OFF", changed_by)
