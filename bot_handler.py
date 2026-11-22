"""Telegram bot: turns admin "Live class" announcements into Google Calendar events.

Rules:
  * Only users in ADMIN_USER_IDS are listened to; everyone else is ignored silently.
  * Only Live classes are created. Exams, meetings and other events are declined with a
    pointer to the web UI.
  * Quiet mode (BOT_QUIET_MODE, /quiet, or the web UI toggle): the bot still syncs Live
    classes but never posts anything in group chats. Private chats still get replies.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from telegram import LinkPreviewOptions, Update
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import bot_state
import config
from announcement_parser import parse_announcement
from calendar_service import CalendarConfigError, create_calendar_event
from labels import LIVE_CLASS, classify_text

log = logging.getLogger(__name__)

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def fmt_range(start: datetime, end: datetime) -> str:
    def t(dt: datetime) -> str:
        return dt.strftime("%I:%M %p").lstrip("0")

    day = f"{start:%a} {start.day} {start:%b %Y}"
    return f"{day}, {t(start)} – {t(end)}"


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id in config.ADMIN_USER_IDS


def is_private(update: Update) -> bool:
    return update.effective_chat is not None and update.effective_chat.type == ChatType.PRIVATE


def may_reply(update: Update) -> bool:
    """Quiet mode silences the bot in groups/channels; private chats always get replies."""
    return is_private(update) or not bot_state.get_quiet_mode()


async def reply(update: Update, text: str) -> None:
    if not may_reply(update):
        log.info("Quiet mode: not replying in chat %s", update.effective_chat.id)
        return
    await update.effective_message.reply_text(text, link_preview_options=NO_PREVIEW)


def _admin_url() -> str:
    return f"{config.PUBLIC_BASE_URL}/admin" if config.PUBLIC_BASE_URL else "the SyllabusSync web page (/admin)"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private(update):
        return
    text = (
        "Hi! I'm SyllabusSync.\n\n"
        "When an approved admin posts a Live class announcement (course, Date:, Time:, link), "
        "I add it to the shared Google Calendar. Exams, meetings and other events go in through the web page.\n\n"
        "/whoami – show your Telegram user ID (for ADMIN_USER_IDS)"
    )
    if is_admin(update):
        text += (
            "\n/quiet on | off – stop or allow my replies in groups (I keep syncing either way)"
            "\n\nYou're an admin: forward or paste an announcement here to schedule it."
        )
    await update.effective_message.reply_text(text)


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Only answer in private chats so the bot never spams a group.
    if not is_private(update) or update.effective_user is None:
        return
    status = "is" if is_admin(update) else "is NOT"
    await update.effective_message.reply_text(
        f"Your Telegram user ID: {update.effective_user.id}\n"
        f"This ID {status} in ADMIN_USER_IDS."
    )


async def cmd_quiet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/quiet, /quiet on, /quiet off (admins only)."""
    if not is_admin(update):
        return
    arg = (context.args[0].lower() if context.args else "")
    if arg in {"on", "true", "1", "yes"}:
        bot_state.set_quiet_mode(True, f"Telegram /quiet ({update.effective_user.id})")
    elif arg in {"off", "false", "0", "no"}:
        bot_state.set_quiet_mode(False, f"Telegram /quiet ({update.effective_user.id})")
    elif arg:
        await reply(update, "Usage: /quiet on  |  /quiet off  |  /quiet (show status)")
        return

    if bot_state.get_quiet_mode():
        text = "🔕 Quiet mode is ON: I'll keep syncing Live classes but won't post in groups."
    else:
        text = "🔔 Quiet mode is OFF: I'll confirm what I schedule in the chat."
    await reply(update, text)  # suppressed in groups while quiet mode is on


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not is_admin(update):
        return  # silently ignore everyone else

    text = message.text or message.caption
    if not text:
        return

    result = parse_announcement(text)
    if not result.relevant:
        return  # ordinary chatter from an admin

    # Live classes only: anything that reads like an exam, meeting or general event is declined.
    classes, declined = [], []
    for ev in result.events:
        label = classify_text(ev.context or ev.title)
        (classes if label == LIVE_CLASS else declined).append((ev.title, label, ev))
    problems = []
    for problem, ctx in zip(result.problems, result.problem_contexts):
        label = classify_text(ctx)
        if label == LIVE_CLASS:
            problems.append(problem)
        else:
            declined.append((problem.split(":")[0], label, None))
    if not result.events and not result.problems:
        label = classify_text(text)
        if label != LIVE_CLASS:
            declined.append(("This announcement", label, None))

    lines: list[str] = []
    for title, _, ev in classes:
        try:
            created = await asyncio.to_thread(
                create_calendar_event, ev.title, ev.start, ev.end, ev.description, ev.link, LIVE_CLASS
            )
        except CalendarConfigError as exc:
            log.error("Calendar not configured: %s", exc)
            lines.append(f"⚠️ {title}: the calendar isn't set up on the server yet")
            continue
        except Exception:
            log.exception("Failed to create event %s", title)
            lines.append(f"⚠️ {title}: Google Calendar rejected it, check the server logs")
            continue
        verb = "Updated" if created.updated_existing else "Scheduled"
        line = f"✅ {verb} Live class: {title}\n    🗓 {fmt_range(ev.start, ev.end)}"
        if ev.link:
            line += f"\n    🔗 {ev.link}"
        lines.append(line)

    if declined:
        items = "\n".join(f"    • {title} ({label})" for title, label, _ in declined)
        lines.append(
            "ℹ️ I only add Live classes, so I skipped:\n"
            f"{items}\n"
            f"Please add exams, meetings and other events on {_admin_url()}."
        )
        log.info("Declined non-class items: %s", [(t, l) for t, l, _ in declined])

    if problems:
        lines.append(
            "❓ I couldn't schedule these. Please repost with a "
            "'Date: Tuesday 20th October, 2026' and 'Time: 3:00PM - 4:00PM' line:\n"
            + "\n".join(f"    • {p}" for p in problems)
        )

    if not lines:
        lines.append(
            "❓ That looks like a class announcement, but I couldn't find the course, date and time. "
            "Please include e.g. 'COS 102 - Problem Solving', 'Date: ...' and 'Time: ...'."
        )

    await reply(update, "\n\n".join(lines))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error while processing update", exc_info=context.error)


def build_application() -> Application:
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")
    if not config.ADMIN_USER_IDS:
        log.warning("ADMIN_USER_IDS is empty: the bot will ignore every announcement.")

    builder = Application.builder().token(config.TELEGRAM_BOT_TOKEN)
    if not config.RUN_POLLING:
        builder = builder.updater(None)  # webhook mode: FastAPI feeds the updates
    app = builder.build()

    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("quiet", cmd_quiet))
    app.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            on_message,
        )
    )
    app.add_error_handler(on_error)
    return app
