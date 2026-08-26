"""Route handlers for the web site."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from . import templates
from .config import WebConfig


def wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def render(request: Request, name: str, status: int = 200, **ctx: Any) -> Response:
    return templates.TemplateResponse(request, name, ctx, status_code=status)


def error_page(request: Request, status: int, title: str, message: str) -> Response:
    return render(request, "error.html", status, title=title, message=message)


def routes(store: Any, cfg: WebConfig) -> list[Any]:
    return []


async def landing(request: Request, store: Any, cfg: WebConfig) -> Response:
    return render(request, "error.html", 200, title="leftbrain", message="Landing page coming in Task 7.", page="landing", user=None)
