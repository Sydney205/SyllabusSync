"""SyllabusSync: FastAPI server running the Telegram bot and a small web UI.

Run locally:   uvicorn main:app --reload     (with RUN_POLLING=true in .env)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from telegram import Update

import bot_state
import config
from announcement_parser import URL_RE
from bot_handler import build_application
from calendar_service import (
    CalendarConfigError,
    CalendarEvent,
    EventNotFound,
    create_calendar_event,
    delete_calendar_event,
    get_calendar_event,
    list_calendar_events,
    update_calendar_event,
)
from labels import DEFAULT_LABEL, LABELS, normalize_label
from web_ui import DONE_MESSAGES, STATIC_DIR, calendar_page, event_form_page, login_page, parse_view_args

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # don't log the bot token in request URLs
log = logging.getLogger("syllabussync")

WEBHOOK_PATH = "/telegram/webhook"
SESSION_COOKIE = "ss_session"


# ---------------------------------------------------------------------------
# Lifespan: start/stop the Telegram bot alongside the web server
# ---------------------------------------------------------------------------


class WebhookInUseError(RuntimeError):
    """Polling was requested for a bot token that a deployed server is using via webhook."""


async def _refuse_to_steal_webhook(tg) -> None:
    """Stop local polling from silently disconnecting a deployed (webhook) bot.

    Starting to poll deletes the token's webhook, so the deployed server would stop
    receiving announcements until it restarts. Refuse unless FORCE_POLLING=true.
    """
    try:
        info = await tg.bot.get_webhook_info()
    except Exception as exc:  # network trouble: don't block startup, polling will retry anyway
        log.warning("Couldn't check for an existing Telegram webhook (%s); continuing.", exc)
        return
    if not info.url:
        return
    if config.FORCE_POLLING:
        log.warning(
            "FORCE_POLLING=true: removing the webhook at %s. The deployed bot will stop receiving "
            "messages until it restarts.",
            info.url,
        )
        return
    raise WebhookInUseError(
        "\n\n"
        "  This bot token already has a webhook: %s\n"
        "  Another server (probably your deployed one) is receiving its messages.\n"
        "  Starting in polling mode would delete that webhook and take its messages away.\n\n"
        "  Fix: use a separate test bot token in your local .env (BotFather > /newbot).\n"
        "  Or, if you really mean to take over this bot, set FORCE_POLLING=true.\n" % info.url
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    tg = None
    if config.TELEGRAM_BOT_TOKEN:
        tg = build_application()
        await tg.initialize()
        if config.RUN_POLLING:
            try:
                await _refuse_to_steal_webhook(tg)
            except Exception:
                await tg.shutdown()
                raise
            # start_polling removes any existing webhook first.
            await tg.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
            log.info("Telegram bot running in POLLING mode.")
        elif config.PUBLIC_BASE_URL:
            url = config.PUBLIC_BASE_URL + WEBHOOK_PATH
            await tg.bot.set_webhook(
                url=url,
                secret_token=config.TELEGRAM_WEBHOOK_SECRET or None,
                allowed_updates=Update.ALL_TYPES,
            )
            log.info("Telegram webhook registered at %s", url)
        else:
            log.warning(
                "Webhook mode but PUBLIC_BASE_URL is not set; register the webhook manually "
                "or set RUN_POLLING=true for local testing."
            )
        await tg.start()
    else:
        log.warning("TELEGRAM_BOT_TOKEN not set: Telegram bot disabled (web UI still works).")

    app.state.tg = tg
    try:
        yield
    finally:
        if tg is not None:
            if config.RUN_POLLING and tg.updater:
                await tg.updater.stop()
            await tg.stop()
            await tg.shutdown()


app = FastAPI(title="SyllabusSync", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Health + Telegram webhook
# ---------------------------------------------------------------------------


@app.get("/health")
async def health(request: Request):
    tg = request.app.state.tg
    return {
        "status": "ok",
        "telegram": "disabled" if tg is None else ("polling" if config.RUN_POLLING else "webhook"),
        "calendar_id_set": bool(config.CALENDAR_ID),
        "timezone": config.DEFAULT_TIMEZONE,
        "quiet_mode": bot_state.get_quiet_mode(),
    }


@app.post(WEBHOOK_PATH)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    tg = request.app.state.tg
    if tg is None or config.RUN_POLLING:
        raise HTTPException(status_code=503, detail="Webhook mode is not active.")
    if config.TELEGRAM_WEBHOOK_SECRET and not secrets.compare_digest(
        x_telegram_bot_api_secret_token or "", config.TELEGRAM_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=403, detail="Bad secret token.")

    data = await request.json()
    update = Update.de_json(data, tg.bot)
    await tg.update_queue.put(update)  # processed in the background; Telegram gets a fast 200
    return {"ok": True}


# ---------------------------------------------------------------------------
# Web UI auth (signed cookie derived from WEB_UI_PASSWORD)
# ---------------------------------------------------------------------------


def _session_token() -> str:
    return hmac.new(
        config.WEB_UI_PASSWORD.encode(), b"syllabussync-web-session-v1", hashlib.sha256
    ).hexdigest()


def _password_ok(candidate: str | None) -> bool:
    return bool(candidate) and secrets.compare_digest(candidate.encode(), config.WEB_UI_PASSWORD.encode())


def _is_authed(request: Request, passcode: str | None = None) -> bool:
    if not config.WEB_UI_PASSWORD:
        return True
    cookie = request.cookies.get(SESSION_COOKIE, "")
    if cookie and secrets.compare_digest(cookie, _session_token()):
        return True
    return _password_ok(passcode or request.headers.get("x-passcode"))


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


def _unauthorized(request: Request):
    if _wants_json(request):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    return login_page(request, "Please enter the passcode first.", status_code=401)


@app.post("/login")
async def login(request: Request, passcode: str = Form("")):
    if not config.WEB_UI_PASSWORD:
        return RedirectResponse("/admin", status_code=303)
    if not _password_ok(passcode):
        await asyncio.sleep(1)  # slow down guessing
        return login_page(request, "Wrong passcode.", status_code=401)
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE,
        _session_token(),
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="strict",
        secure=_is_https(request),
    )
    return resp


@app.post("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ---------------------------------------------------------------------------
# Helpers shared by the pages and the event endpoints
# ---------------------------------------------------------------------------

EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")


def _now() -> datetime:
    return datetime.now(config.TZ)


def _safe_return_to(value: str | None) -> str:
    """Only allow redirects back into the admin area (prevents open redirects)."""
    if value and value.startswith("/admin") and not value.startswith("//") and "\\" not in value:
        return value
    return "/admin"


def _with_done(url: str, code: str) -> str:
    url = url.split("#", 1)[0]
    return f"{url}{'&' if '?' in url else '?'}done={code}"


async def _load_events() -> tuple[list[CalendarEvent], str | None]:
    """Return (events, error_message). Never raises, so pages can still render."""
    try:
        return await asyncio.to_thread(list_calendar_events), None
    except CalendarConfigError as exc:
        log.error("Calendar not configured: %s", exc)
        return [], "The calendar isn't set up on the server yet."
    except Exception:
        log.exception("Failed to list calendar events")
        return [], "Couldn't load the schedule from Google Calendar. Please try again shortly."


def _event_json(ev: CalendarEvent) -> dict:
    lbl = LABELS[ev.label]
    return {
        "id": ev.id,
        "title": ev.title,
        "label": ev.label,
        "color": lbl.color,
        "color_id": lbl.color_id,
        "start": ev.start.isoformat(),
        "end": ev.end.isoformat(),
        "all_day": ev.all_day,
        "meeting_link": ev.meeting_link,
        "location": ev.location,
        "notes": ev.notes,
    }


def _validate_event_form(title: str, start: str, duration: int, notes: str, label: str):
    """Return ((title, start_dt, end_dt, description, location, label), None) or (None, error)."""
    title = title.strip()
    if not title:
        return None, "Title is required."
    try:
        start_dt = datetime.fromisoformat(start).replace(tzinfo=config.TZ)
    except ValueError:
        return None, "Start must look like 2026-11-20T15:00."
    if not 5 <= duration <= 720:
        return None, "Duration must be between 5 and 720 minutes."
    label_name = normalize_label(label or DEFAULT_LABEL)
    if label_name is None:
        return None, f"Label must be one of: {', '.join(LABELS)}."
    link_match = URL_RE.search(notes)
    location = link_match.group(0).rstrip(".,") if link_match else ""
    end_dt = start_dt + timedelta(minutes=duration)
    return (title, start_dt, end_dt, notes.strip(), location, label_name), None


async def _render_calendar(
    request: Request,
    *,
    admin: bool,
    view: str | None = None,
    date_str: str | None = None,
    label: str | None = None,
    status_code: int = 200,
    **kwargs,
):
    now = _now()
    view, anchor, label_filter = parse_view_args(view, date_str, label, now.date())
    events, error = await _load_events()
    return calendar_page(
        request,
        events,
        admin=admin,
        view=view,
        anchor=anchor,
        label_filter=label_filter,
        now=now,
        timezone_name=config.DEFAULT_TIMEZONE,
        error=error,
        quiet=bot_state.quiet_status() if admin else None,
        show_logout=admin and bool(config.WEB_UI_PASSWORD),
        status_code=503 if error and status_code == 200 else status_code,
        **kwargs,
    )


def _check_event_id(event_id: str) -> None:
    if not EVENT_ID_RE.match(event_id):
        raise HTTPException(status_code=404, detail="Unknown event.")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def public_calendar(
    request: Request, view: str | None = None, date: str | None = None, label: str | None = None
):
    """Public calendar (month grid or schedule). Anyone can view it."""
    return await _render_calendar(request, admin=False, view=view, date_str=date, label=label)


@app.get("/api/events")
async def api_events(label: str | None = None):
    """Public JSON list of every event (oldest first). Optional ?label=Exam filter."""
    events, error = await _load_events()
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=503)
    wanted = normalize_label(label)
    if label and wanted is None:
        return JSONResponse({"ok": False, "error": f"Unknown label. Use one of: {', '.join(LABELS)}."}, status_code=400)
    return {
        "ok": True,
        "timezone": config.DEFAULT_TIMEZONE,
        "labels": {l.name: {"color": l.color, "color_id": l.color_id} for l in LABELS.values()},
        "events": [_event_json(e) for e in events if not wanted or e.label == wanted],
    }


@app.get("/admin", response_class=HTMLResponse)
async def admin_calendar(
    request: Request,
    view: str | None = None,
    date: str | None = None,
    label: str | None = None,
    done: str | None = None,
):
    if not _is_authed(request):
        return login_page(request)
    return await _render_calendar(
        request, admin=True, view=view, date_str=date, label=label, message=DONE_MESSAGES.get(done or "")
    )


@app.get("/admin/events/new", response_class=HTMLResponse)
async def new_event_page(request: Request, date: str | None = None, label: str | None = None):
    if not _is_authed(request):
        return login_page(request)
    try:
        day = datetime.fromisoformat(date).date() if date else _now().date()
    except ValueError:
        day = _now().date()
    values = {"start": f"{day.isoformat()}T09:00", "label": normalize_label(label) or DEFAULT_LABEL}
    return event_form_page(request, mode="create", values=values, timezone_name=config.DEFAULT_TIMEZONE)


@app.get("/admin/events/{event_id}/edit", response_class=HTMLResponse)
async def edit_event_page(request: Request, event_id: str):
    if not _is_authed(request):
        return login_page(request)
    _check_event_id(event_id)
    try:
        ev = await asyncio.to_thread(get_calendar_event, event_id)
    except EventNotFound:
        return await _render_calendar(request, admin=True, status_code=404, message="That event no longer exists.", ok=False)
    except Exception:
        log.exception("Failed to load event %s", event_id)
        return await _render_calendar(
            request, admin=True, status_code=502, message="Couldn't load that event from Google Calendar.", ok=False
        )

    if ev.all_day:
        start_value, duration = ev.start.strftime("%Y-%m-%dT09:00"), 60
    else:
        start_value = ev.start.strftime("%Y-%m-%dT%H:%M")
        duration = int((ev.end - ev.start).total_seconds() // 60)
    values = {
        "title": ev.title,
        "start": start_value,
        "duration": duration,
        "label": ev.label,
        "notes": "\n\n".join(p for p in (ev.meeting_link, ev.notes) if p),
    }
    return event_form_page(
        request, mode="edit", event_id=event_id, values=values,
        timezone_name=config.DEFAULT_TIMEZONE, was_all_day=ev.all_day,
    )


@app.post("/admin/quiet")
async def toggle_quiet_mode(request: Request, quiet: str = Form(""), return_to: str = Form("/admin")):
    """Turn the Telegram bot's group quiet mode on/off (quiet=on|off)."""
    if not _is_authed(request):
        return _unauthorized(request)
    value = quiet.strip().lower()
    if value not in {"on", "off", "true", "false"}:
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": "quiet must be 'on' or 'off'."}, status_code=400)
        raise HTTPException(status_code=400, detail="quiet must be 'on' or 'off'.")
    on = value in {"on", "true"}
    bot_state.set_quiet_mode(on, "web UI")
    if _wants_json(request):
        return {"ok": True, **bot_state.quiet_status()}
    return RedirectResponse(_with_done(_safe_return_to(return_to), "quiet_on" if on else "quiet_off"), status_code=303)


# ---------------------------------------------------------------------------
# Event create / update / delete (passcode-protected)
# ---------------------------------------------------------------------------


def _redirect_to_event_month(start_dt: datetime, code: str) -> RedirectResponse:
    return RedirectResponse(f"/admin?date={start_dt.date().isoformat()}&done={code}", status_code=303)


@app.post("/events")
async def create_event(
    request: Request,
    title: str = Form(""),
    start: str = Form(""),
    duration: int = Form(60),
    notes: str = Form(""),
    label: str = Form(DEFAULT_LABEL),
    passcode: str | None = Form(None),
):
    if not _is_authed(request, passcode):
        return _unauthorized(request)
    json_mode = _wants_json(request)
    values = {"title": title, "start": start, "duration": duration, "notes": notes, "label": label}

    def fail(msg: str, status: int):
        if json_mode:
            return JSONResponse({"ok": False, "error": msg}, status_code=status)
        return event_form_page(
            request, mode="create", values=values, timezone_name=config.DEFAULT_TIMEZONE,
            message=msg, status_code=status,
        )

    parsed, error = _validate_event_form(title, start, duration, notes, label)
    if error:
        return fail(error, 400)
    title, start_dt, end_dt, description, location, label = parsed

    try:
        created = await asyncio.to_thread(
            create_calendar_event, title, start_dt, end_dt, description, location, label
        )
    except CalendarConfigError as exc:
        log.error("Calendar not configured: %s", exc)
        return fail(f"Server calendar setup problem: {exc}", 500)
    except Exception:
        log.exception("Failed to create event from web UI")
        return fail("Google Calendar rejected the event. Check the server logs.", 502)

    if json_mode:
        return JSONResponse(
            {
                "ok": True,
                "updated_existing": created.updated_existing,
                "id": created.id,
                "title": created.title,
                "label": created.label,
                "color_id": LABELS[created.label].color_id,
                "start": created.start.isoformat(),
                "end": created.end.isoformat(),
                "html_link": created.html_link,
            },
            status_code=201,
        )
    return _redirect_to_event_month(start_dt, "created")


@app.post("/events/{event_id}")
@app.put("/events/{event_id}")
async def update_event(
    request: Request,
    event_id: str,
    title: str = Form(""),
    start: str = Form(""),
    duration: int = Form(60),
    notes: str = Form(""),
    label: str = Form(DEFAULT_LABEL),
    passcode: str | None = Form(None),
):
    if not _is_authed(request, passcode):
        return _unauthorized(request)
    _check_event_id(event_id)
    json_mode = _wants_json(request)
    values = {"title": title, "start": start, "duration": duration, "notes": notes, "label": label}

    def fail(msg: str, status: int):
        if json_mode:
            return JSONResponse({"ok": False, "error": msg}, status_code=status)
        return event_form_page(
            request, mode="edit", event_id=event_id, values=values,
            timezone_name=config.DEFAULT_TIMEZONE, message=msg, status_code=status,
        )

    parsed, error = _validate_event_form(title, start, duration, notes, label)
    if error:
        return fail(error, 400)
    title, start_dt, end_dt, description, location, label = parsed

    try:
        await asyncio.to_thread(
            update_calendar_event, event_id, title, start_dt, end_dt, description, location, label
        )
    except EventNotFound:
        return fail("That event no longer exists. It may have been deleted.", 404)
    except Exception:
        log.exception("Failed to update event %s", event_id)
        return fail("Google Calendar rejected the change. Check the server logs.", 502)

    if json_mode:
        return {"ok": True, "id": event_id, "label": label, "color_id": LABELS[label].color_id}
    return _redirect_to_event_month(start_dt, "updated")


@app.post("/events/{event_id}/delete")
@app.delete("/events/{event_id}")
async def delete_event(request: Request, event_id: str):
    passcode, return_to = None, None
    if request.method == "POST":
        form = await request.form()
        passcode, return_to = form.get("passcode"), form.get("return_to")
    if not _is_authed(request, passcode):
        return _unauthorized(request)
    _check_event_id(event_id)
    json_mode = _wants_json(request) or request.method == "DELETE"

    try:
        await asyncio.to_thread(delete_calendar_event, event_id)
    except Exception:
        log.exception("Failed to delete event %s", event_id)
        msg = "Google Calendar refused to delete the event. Check the server logs."
        if json_mode:
            return JSONResponse({"ok": False, "error": msg}, status_code=502)
        return await _render_calendar(request, admin=True, status_code=502, message=msg, ok=False)

    if json_mode:
        return {"ok": True, "id": event_id}
    return RedirectResponse(_with_done(_safe_return_to(return_to), "deleted"), status_code=303)
