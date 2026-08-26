"""HTML site for leftbrain-serve: landing, GitHub login, key dashboard, docs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from .. import __version__
from .config import WebConfig

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.globals["version"] = __version__


def build_web(store: Any, cfg: WebConfig) -> list[Any]:
    """Routes for the site. Later tasks append handlers here."""
    from . import views

    return [
        *views.routes(store, cfg),
        Mount("/static", app=StaticFiles(directory=str(HERE / "static")), name="static"),
    ]
