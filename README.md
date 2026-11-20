# SyllabusSync

A FastAPI server that:

- listens to a **Telegram bot**. When an approved admin posts a class announcement, the bot adds it to a shared **Google Calendar** and replies with what it scheduled.
- serves a **web form** at `/` where you can add a class by hand. The form can be protected with a passcode.
- exposes `GET /health` for hosting health checks.

```
main.py                FastAPI app: lifespan (bot start/stop), /, /login, /events, /health, /telegram/webhook
bot_handler.py         Telegram handlers (admin filter, replies, /whoami)
announcement_parser.py Turns announcement text into events (course, date, time, link)
calendar_service.py    Service-account auth + create_calendar_event(...)
web_ui.py              HTML/CSS for the form (no frameworks)
config.py              Reads environment variables / .env
tests/test_parser.py   Parser tests using real Miva announcement samples
```

## What the bot understands

```
📢Live Lesson Reminder📢
COS 102 - Problem Solving
🗓Date: Tuesday 20th October, 2026
🕓 Time: 3:00PM - 4:00PM (WAT)
🔗 Meeting link: https://meet.google.com/oxo-dctc-hsw
```

- You can paste or forward several reminders in one message. Each one becomes its own event.
- If several courses sit under one reminder and only the first has a `Date:` line, the later courses use the same date.
- When the reminder header contains "Revision", ` (Revision)` is added to the event title.
- Looser one-liners also work, e.g. `Live class: Data Structures tomorrow 3pm - 4:30pm <link>`.
- If an announcement is missing its date or time, the bot replies asking for it.
- The bot ignores messages from non-admins, as well as ordinary chatter, stickers and photos without captions.
- Re-posting the same announcement updates the existing event instead of adding a duplicate. Duplicates are matched on title and start time.

## 1. One-time setup

### Google Calendar + service account
1. Go to [Google Cloud Console](https://console.cloud.google.com/). Create a project, then enable the **Google Calendar API**.
2. Go to **IAM & Admin → Service Accounts → Create service account**. No roles are needed.
3. Open the service account, then go to **Keys → Add key → JSON**. Save the file as `service_account.json` in this folder. It is already in `.gitignore`.
4. In Google Calendar, open the shared calendar's **Settings → Share with specific people**. Add the service account's email (`...@...iam.gserviceaccount.com`) with **Make changes to events**.
5. On the same settings page, under **Integrate calendar**, copy the **Calendar ID** into `CALENDAR_ID`.

### Telegram bot
1. Message [@BotFather](https://t.me/BotFather) and send `/newbot`. Copy the token into `TELEGRAM_BOT_TOKEN`.
2. **For group use:** send BotFather `/setprivacy`, pick your bot, then choose **Disable**. Without this, the bot can't see normal group messages. If the bot was already in the group, remove it and add it back.
3. Start the bot, message it privately, and send `/whoami` to get your numeric user ID. Put your ID (and any other admins' IDs) into `ADMIN_USER_IDS`.

> Note: when an admin posts *anonymously* in a group, Telegram hides who they are, so the bot ignores the post. Admins should post as themselves, or forward the announcement to the bot privately.

## 2. Run locally (polling mode)

Use the official Python from python.org (the `py` launcher on Windows). The MSYS2/MinGW Python can't install `cryptography`.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                # then edit .env (keep RUN_POLLING=true)
uvicorn main:app --reload --port 8000
```

- Open http://localhost:8000 to use the web form.
- Send an announcement to your bot in Telegram. It should reply with ✅ and the event should show up in the calendar.
- To run the parser tests: `pip install pytest` then `pytest -q`.

Polling and webhooks can't both be active for the same bot. Starting in polling mode removes any existing webhook. Before you test locally again, stop the cloud deployment, or create a separate test bot.

## 3. Deploy with webhooks (Render or Railway)

1. Push the repo to GitHub. `.env` and `service_account.json` are git-ignored, so they won't be pushed.
2. Create a **Web Service** from the repo:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"`
   - **Health check path:** `/health`
3. Set these environment variables:

   | Variable | Value |
   |---|---|
   | `GOOGLE_CREDENTIALS_JSON` | The entire contents of `service_account.json`, pasted as a single line |
   | `CALENDAR_ID` | Your calendar ID |
   | `DEFAULT_TIMEZONE` | `Africa/Lagos` |
   | `TELEGRAM_BOT_TOKEN` | Bot token |
   | `ADMIN_USER_IDS` | e.g. `123456789,987654321` |
   | `RUN_POLLING` | `false` |
   | `TELEGRAM_WEBHOOK_SECRET` | A long random string (letters, digits, `_` and `-` only) |
   | `PUBLIC_BASE_URL` | e.g. `https://syllabussync.up.railway.app`. Render sets this automatically through `RENDER_EXTERNAL_URL`. |
   | `WEB_UI_PASSWORD` | Your passcode |

4. Deploy. On startup the app calls Telegram's `setWebhook` with `PUBLIC_BASE_URL/telegram/webhook` and your secret. Requests that don't carry the secret header are rejected with 403.
5. To check it worked, open `https://YOUR_URL/health`, then visit `https://api.telegram.org/bot<TOKEN>/getWebhookInfo`.

Run a **single instance/worker**. On free tiers the service may sleep when idle. Telegram retries webhooks for a while, so the first message after a sleep can be slow, but it won't be lost.

## API

- `POST /events` (form fields: `title`, `start` = `2026-11-20T15:00`, `duration` in minutes, `notes`)
  - Returns HTML by default. Send `Accept: application/json` to get JSON back.
  - Scripts can authenticate with an `X-Passcode` header or a `passcode` form field.
- `GET /health` returns `{"status": "ok", ...}`
