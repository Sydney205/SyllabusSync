"""Telegram bot: turns admin announcements into Google Calendar events."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from telegram import LinkPreviewOptions, Update
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import config
from announcement_parser import parse_announcement
from calendar_service import CalendarConfigError, create_calendar_event

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


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    text = (
        "Hi! I'm SyllabusSync.\n\n"
        "When an approved admin posts a class announcement (course, Date:, Time:, link), "
        "I add it to the shared Google Calendar.\n\n"
        "/whoami – show your Telegram user ID (for ADMIN_USER_IDS)"
    )
    if is_admin(update):
        text += "\n\nYou're an admin: forward or paste an announcement here to schedule it."
    await update.effective_message.reply_text(text)


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Only answer in private chats so the bot never spams a group.
    if update.effective_chat.type != ChatType.PRIVATE or update.effective_user is None:
        return
    status = "is" if is_admin(update) else "is NOT"
    await update.effective_message.reply_text(
        f"Your Telegram user ID: {update.effective_user.id}\n"
        f"This ID {status} in ADMIN_USER_IDS."
    )


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

    lines: list[str] = []
    for ev in result.events:
        try:
            created = await asyncio.to_thread(
                create_calendar_event, ev.title, ev.start, ev.end, ev.description, ev.link
            )
        except CalendarConfigError as exc:
            log.error("Calendar not configured: %s", exc)
            lines.append(f"⚠️ {ev.title}: calendar isn't configured on the server ({exc})")
            continue
        except Exception:
            log.exception("Failed to create event %s", ev.title)
            lines.append(f"⚠️ {ev.title}: Google Calendar rejected it, check the server logs")
            continue
        verb = "Updated" if created.updated_existing else "Scheduled"
        line = f"✅ {verb}: {ev.title}\n    🗓 {fmt_range(ev.start, ev.end)}"
        if ev.link:
            line += f"\n    🔗 {ev.link}"
        lines.append(line)

    if result.problems:
        lines.append(
            "❓ I couldn't schedule these. Please repost with a "
            "'Date: Tuesday 20th October, 2026' and 'Time: 3:00PM - 4:00PM' line:\n"
            + "\n".join(f"    • {p}" for p in result.problems)
        )

    if not lines:
        lines.append(
            "❓ That looks like a class announcement, but I couldn't find the course, date and time. "
            "Please include e.g. 'COS 102 - Problem Solving', 'Date: ...' and 'Time: ...'."
        )

    await message.reply_text("\n\n".join(lines), link_preview_options=NO_PREVIEW)


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
    app.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            on_message,
        )
    )
    app.add_error_handler(on_error)
    return app
