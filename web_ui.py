"""Tiny server-rendered HTML pages for the web UI (no external frameworks)."""

from __future__ import annotations

from html import escape

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1d2330;--muted:#5d6676;--line:#dde1e7;
--accent:#2457d6;--accent-text:#fff;--ok-bg:#e7f6ec;--ok:#1d6b39;--err-bg:#fdecec;--err:#a32020}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1b2029;--text:#e7eaf0;--muted:#9aa3b2;
--line:#2c3340;--accent:#5b8cff;--accent-text:#0b1020;--ok-bg:#163322;--ok:#7fd69c;--err-bg:#3a1a1a;--err:#ff9b9b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:520px;margin:0 auto;padding:32px 16px 48px}
h1{font-size:1.5rem;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px}
label{display:block;font-weight:600;font-size:.9rem;margin:16px 0 6px}
label:first-child{margin-top:0}
input,textarea{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--text);font:inherit}
textarea{min-height:96px;resize:vertical}
input:focus,textarea:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:transparent}
.hint{color:var(--muted);font-size:.8rem;margin-top:4px}
button{margin-top:20px;width:100%;padding:12px;border:0;border-radius:8px;background:var(--accent);
color:var(--accent-text);font:inherit;font-weight:600;cursor:pointer}
.msg{border-radius:8px;padding:12px 14px;margin-bottom:16px}
.ok{background:var(--ok-bg);color:var(--ok)}
.err{background:var(--err-bg);color:var(--err)}
.msg a{color:inherit}
.foot{text-align:center;margin-top:16px;font-size:.85rem}
.link{background:none;border:0;padding:0;margin:0;width:auto;color:var(--muted);text-decoration:underline;font-weight:400}
"""


def _page(body: str, title: str = "SyllabusSync") -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{CSS}</style></head>
<body><main>{body}</main></body></html>"""


def _banner(message: str | None, ok: bool, raw_html: bool = False) -> str:
    if not message:
        return ""
    content = message if raw_html else escape(message)
    return f'<div class="msg {"ok" if ok else "err"}" role="status">{content}</div>'


def login_page(error: str | None = None) -> str:
    return _page(
        f"""
<h1>SyllabusSync</h1>
<p class="sub">Enter the passcode to add class events.</p>
{_banner(error, ok=False)}
<form class="card" method="post" action="/login">
  <label for="passcode">Passcode</label>
  <input id="passcode" name="passcode" type="password" autocomplete="current-password" required autofocus>
  <button type="submit">Unlock</button>
</form>"""
    )


def form_page(
    message: str | None = None,
    ok: bool = True,
    message_is_html: bool = False,
    values: dict | None = None,
    show_logout: bool = False,
    timezone: str = "",
) -> str:
    v = {k: escape(str(val)) for k, val in (values or {}).items()}
    logout = (
        '<form class="foot" method="post" action="/logout"><button class="link" type="submit">Lock</button></form>'
        if show_logout
        else ""
    )
    return _page(
        f"""
<h1>SyllabusSync</h1>
<p class="sub">Add a class to the shared Google Calendar.</p>
{_banner(message, ok, raw_html=message_is_html)}
<form class="card" method="post" action="/events">
  <label for="title">Title</label>
  <input id="title" name="title" required maxlength="200" placeholder="COS 102 - Problem Solving" value="{v.get('title', '')}">

  <label for="start">Date &amp; start time</label>
  <input id="start" name="start" type="datetime-local" required value="{v.get('start', '')}">
  <div class="hint">Times are in {escape(timezone)}.</div>

  <label for="duration">Duration (minutes)</label>
  <input id="duration" name="duration" type="number" min="5" max="720" step="5" required value="{v.get('duration', '60')}">

  <label for="notes">Meeting link / notes</label>
  <textarea id="notes" name="notes" placeholder="https://meet.google.com/...">{v.get('notes', '')}</textarea>

  <button type="submit">Add to calendar</button>
</form>
{logout}"""
    )
