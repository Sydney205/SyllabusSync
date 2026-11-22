# SyllabusSync

A FastAPI server that:

- listens to a **Telegram bot**. When an approved admin posts a **Live class** announcement, the bot adds it to a shared **Google Calendar** and (unless quiet mode is on) replies with what it scheduled.
- serves a **Google-Calendar-style web UI**: a public month/schedule view at `/`, and a passcode-protected admin view at `/admin` to create, edit and delete events with four labels.
- exposes `GET /health` for hosting health checks.

```
main.py                FastAPI app: pages, /events, /admin/quiet, /api/events, /health, /telegram/webhook
bot_handler.py         Telegram handlers (admin filter, Live-class-only rule, quiet mode, /quiet, /whoami)
bot_state.py           Runtime quiet-mode setting (starts from BOT_QUIET_MODE, saved to bot_state.json)
labels.py              The four labels, their colours / Google colorIds, and text classification
announcement_parser.py Turns announcement text into events (course, date, time, link)
calendar_service.py    Service-account auth + create/list/get/update/delete helpers
web_ui.py              Month grid / schedule view models and template rendering
templates/*.html       calendar.html (public + admin), event_form.html, login.html, offline.html,
                       _macros.html (calendar pieces), _icons.html (SVG icon set)
static/styles.css      Google-Calendar-style responsive styles + animations (Geist font)
static/app.js          Registers the service worker, install button, offline banner
static/sw.js           Service worker (offline support); served at /sw.js
static/icons/          App icons and logo images (generated, see below)
assets/logo-source.jpg The original logo the icons are cut from
tools/make_icons.py    Regenerates static/icons/ from the logo (needs Pillow)
config.py              Reads environment variables / .env
tests/                 Parser and label tests using real Miva announcement samples
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
- **Live classes only.** Every event the bot creates gets the label "Live class" (Google `colorId` 9, blue). If an admin's message is about an exam, meeting or other event (e.g. a "📢Exam Reminder📢" block, "class reps meeting tomorrow 4pm"), the bot creates nothing and replies that those go in through the web UI. Explicit class wording ("live lesson", "lecture", "revision class") wins, so a class that mentions upcoming exams is still a class.

## Labels

| Label | Web UI colour | Google Calendar `colorId` |
|---|---|---|
| Exam | Red `#DC2626` | 11 (Tomato) |
| Meeting | Yellow/Gold `#D97706` | 5 (Banana) |
| Event | Green `#059669` | 10 (Basil) |
| Live class | Blue `#2563EB` | 9 (Blueberry) |

New events default to **Live class**. The label is saved on the event in two places: its colour, and a private property. Events with neither (made before labels existed) show as Live class. Click a label in the sidebar to show only that label.

## Quiet mode (Telegram groups)

- **Off** (`BOT_QUIET_MODE=false`, the default): the bot replies in the group with ✅ confirmations, "I only add Live classes" notices and requests for missing dates.
- **On**: the bot still reads admin messages and syncs Live classes, but **never posts in groups or supergroups**. Private chats with the bot still get replies.
- You can flip it without redeploying:
  - **Web UI:** in `/admin`, use the "Telegram quiet mode" switch in the sidebar.
  - **Telegram (admins only):** send `/quiet on`, `/quiet off`, or `/quiet` to see the current status. Send it to the bot in a DM to get a reply; in a group it replies only when quiet mode ends up off.
- Runtime changes are saved to `bot_state.json`. On hosts with a temporary disk (Render's free tier) the file is wiped when the service restarts or redeploys, and quiet mode goes back to `BOT_QUIET_MODE`. Set that variable to the value you want by default.

**Testing quiet mode:**
1. With quiet mode **off**, post a Live class announcement in your group as an admin. The bot replies ✅ and the event appears (blue) on the calendar. Post an exam reminder and the bot replies that it skipped it.
2. Turn quiet mode **on** (switch or `/quiet on`). Post another Live class announcement. The event still appears on the calendar, and the group gets **no** message. The server log shows `Quiet mode: not replying in chat ...`.
3. DM the bot the same announcement. You get the ✅ reply, because quiet mode only silences groups.
4. Turn it **off** again and check that group replies come back.

## Install as an app (PWA)

Students can install SyllabusSync on their phone or computer, and it opens like a normal app:

- **Android (Chrome, Edge, Samsung Internet) and desktop Chrome/Edge:** tap the **Install** button in the top bar, or use the browser menu's **Install app** / **Add to Home screen**.
- **iPhone / iPad (Safari):** tap **Install** to see the steps. In short, tap the **Share** button, choose **Add to Home Screen**, then tap **Add**.

How it works:
- `/manifest.webmanifest` gives the app's name, icons, colours, and shortcuts for the Schedule and Month views.
- `/sw.js` is the service worker. It saves the public calendar each time it's opened, so the last schedule still shows **offline**, with an "You're offline" bar. Admin pages and anything that changes data always use the network.
- Installing requires **HTTPS**, which Render and Railway provide. On your own computer, `http://localhost` also works.
- After a deploy, installed apps pick up the new version the next time they're opened.

**Changing the logo:** replace `assets/logo-source.jpg`, update the crop numbers at the top of `tools/make_icons.py` if the layout differs, then run `pip install pillow` and `python tools/make_icons.py`. Pillow is only needed for this step, so it isn't in `requirements.txt`.

## Look and feel

- Font: [Geist](https://vercel.com/font), loaded from Google Fonts in `templates/base.html`. It falls back to the system font when offline.
- Icons: inline SVGs from [Lucide](https://lucide.dev) (ISC license) in `templates/_icons.html`. Use them in a template with `{{ icon("clock") }}`.
- Animations: popovers scale in (on phones they slide up as a bottom sheet), schedule days fade in one after another, "Live now" badges pulse, and buttons and switches react when pressed. They're turned off for anyone whose device is set to reduce motion.
- Light and dark mode follow the device setting.

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

- Open http://localhost:8000 for the public calendar, and http://localhost:8000/admin to create, edit and delete events.
- Send an announcement to your bot in Telegram. It should reply with ✅ and the event should show up in the calendar.
- To run the tests: `pip install pytest` then `pytest -q`.

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
   | `BOT_QUIET_MODE` | `false` (or `true` to keep the bot silent in groups by default) |

4. Deploy. On startup the app calls Telegram's `setWebhook` with `PUBLIC_BASE_URL/telegram/webhook` and your secret. Requests that don't carry the secret header are rejected with 403.
5. To check it worked, open `https://YOUR_URL/health`, then visit `https://api.telegram.org/bot<TOKEN>/getWebhookInfo`.

Run a **single instance/worker**. On free tiers the service may sleep when idle. Telegram retries webhooks for a while, so the first message after a sleep can be slow, but it won't be lost.

## API

Public:
- `GET /?view=month|agenda&date=2026-11-01&label=Exam` is the calendar page.
- `GET /api/events` returns every event as JSON with `label`, `color` and `color_id`. Filter it with `?label=Exam`.
- `GET /health` returns `{"status": "ok", "quiet_mode": false, ...}`.

Admin (needs the session cookie, an `X-Passcode` header or a `passcode` form field):
- `POST /events` creates an event. Form fields: `title`, `start` = `2026-11-20T15:00`, `duration` in minutes, `notes`, and `label` (`Exam`, `Meeting`, `Event` or `Live class`, default `Live class`).
- `POST` or `PUT /events/{id}` updates an event. It takes the same fields.
- `POST /events/{id}/delete` or `DELETE /events/{id}` deletes an event.
- `POST /admin/quiet` with `quiet=on` or `quiet=off` turns quiet mode on or off.

Send `Accept: application/json` to get JSON back instead of HTML.
