"""SyllabusSync: FastAPI server running the Telegram bot and a small web UI.

Run locally:   uvicorn main:app --reload     (with RUN_POLLING=true in .env)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from html import escape

from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from telegram import Update

import config
from announcement_parser import URL_RE
from bot_handler import build_application, fmt_range
from calendar_service import CalendarConfigError, create_calendar_event
from web_ui import form_page, login_page

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # don't log the bot token in request URLs
log = logging.getLogger("syllabussync")

WEBHOOK_PATH = "/telegram/webhook"
SESSION_COOKIE = "ss_session"


# ---------------------------------------------------------------------------
# Lifespan: start/stop the Telegram bot alongside the web server
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    tg = None
    if config.TELEGRAM_BOT_TOKEN:
        tg = build_application()
        await tg.initialize()
        if config.RUN_POLLING:
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


def _form(request: Request, status: int = 200, **kwargs) -> HTMLResponse:
    return HTMLResponse(
        form_page(show_logout=bool(config.WEB_UI_PASSWORD), timezone=config.DEFAULT_TIMEZONE, **kwargs),
        status_code=status,
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not _is_authed(request):
        return HTMLResponse(login_page())
    return _form(request)


@app.post("/login")
async def login(request: Request, passcode: str = Form("")):
    if not config.WEB_UI_PASSWORD:
        return RedirectResponse("/", status_code=303)
    if not _password_ok(passcode):
        await asyncio.sleep(1)  # slow down guessing
        return HTMLResponse(login_page("Wrong passcode."), status_code=401)
    resp = RedirectResponse("/", status_code=303)
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
# Manual event creation
# ---------------------------------------------------------------------------


@app.post("/events")
async def create_event(
    request: Request,
    title: str = Form(...),
    start: str = Form(...),
    duration: int = Form(60),
    notes: str = Form(""),
    passcode: str | None = Form(None),
):
    json_mode = _wants_json(request)
    values = {"title": title, "start": start, "duration": duration, "notes": notes}

    def fail(msg: str, status: int):
        if json_mode:
            return JSONResponse({"ok": False, "error": msg}, status_code=status)
        return _form(request, status, message=msg, ok=False, values=values)

    if not _is_authed(request, passcode):
        if json_mode:
            return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
        return HTMLResponse(login_page("Please enter the passcode first."), status_code=401)

    title = title.strip()
    if not title:
        return fail("Title is required.", 400)
    try:
        start_dt = datetime.fromisoformat(start).replace(tzinfo=config.TZ)
    except ValueError:
        return fail("Start must look like 2026-11-20T15:00.", 400)
    if not 5 <= duration <= 720:
        return fail("Duration must be between 5 and 720 minutes.", 400)
    end_dt = start_dt + timedelta(minutes=duration)

    link_match = URL_RE.search(notes)
    location = link_match.group(0) if link_match else ""

    try:
        created = await asyncio.to_thread(
            create_calendar_event, title, start_dt, end_dt, notes.strip(), location
        )
    except CalendarConfigError as exc:
        log.error("Calendar not configured: %s", exc)
        return fail(f"Server calendar setup problem: {exc}", 500)
    except Exception:
        log.exception("Failed to create event from web UI")
        return fail("Google Calendar rejected the event. Check the server logs.", 502)

    verb = "Updated existing event" if created.updated_existing else "Added"
    if json_mode:
        return JSONResponse(
            {
                "ok": True,
                "updated_existing": created.updated_existing,
                "id": created.id,
                "title": created.title,
                "start": created.start.isoformat(),
                "end": created.end.isoformat(),
                "html_link": created.html_link,
            },
            status_code=201,
        )
    msg = f"✅ {verb}: <strong>{escape(title)}</strong><br>{escape(fmt_range(start_dt, end_dt))}"
    if created.html_link:
        msg += f'<br><a href="{escape(created.html_link)}" target="_blank" rel="noopener">Open in Google Calendar</a>'
    return _form(request, 200, message=msg, ok=True, message_is_html=True)
