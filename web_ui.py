"""Template rendering helpers for the server-rendered web UI."""

from __future__ import annotations

from pathlib import Path

from markupsafe import Markup
from starlette.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def login_page(request, error: str | None = None, status_code: int = 200):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error},
        status_code=status_code,
    )


def form_page(
    request,
    *,
    status_code: int = 200,
    message: str | None = None,
    ok: bool = True,
    message_is_html: bool = False,
    values: dict | None = None,
    show_logout: bool = False,
    timezone: str = "",
):
    if message_is_html and message is not None:
        message = Markup(message)
    return templates.TemplateResponse(
        request=request,
        name="form.html",
        context={
            "message": message,
            "ok": ok,
            "values": values or {},
            "show_logout": show_logout,
            "timezone": timezone,
        },
        status_code=status_code,
    )
