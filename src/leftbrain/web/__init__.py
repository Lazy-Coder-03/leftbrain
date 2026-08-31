"""HTML site for leftbrain-serve: landing, GitHub login, key dashboard, docs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from starlette.responses import Response
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from .. import __version__
from ..scopes import NETWORK_TOOLS
from .config import WebConfig

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.globals["version"] = __version__
#: which rows of the scope grid reach the internet - the dashboard, the create-key form and
#: both consent pages include the same partial, so it is a global and not a per-view argument (#103)
templates.env.globals["network_tools"] = NETWORK_TOOLS


def _asset_stamp() -> str:
    """Short content hash of the static assets: cache-bust on every change, not only on releases."""
    import hashlib

    h = hashlib.sha256()
    for name in ("site.css", "site.js", "logo.svg"):
        try:
            h.update((HERE / "static" / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:10]


templates.env.globals["asset_v"] = _asset_stamp()


class CachedStaticFiles(StaticFiles):
    """Static assets, cached for a day.

    Safe because every link in ``base.html`` carries a ``?v=<version>`` stamp, so a
    release busts the cache; only an unstamped direct hit can serve a stale file.
    """

    CACHE_CONTROL = "public, max-age=86400"

    def file_response(self, full_path: Any, stat_result: os.stat_result, scope: Any, status_code: int = 200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code=status_code)
        response.headers.setdefault("cache-control", self.CACHE_CONTROL)
        return response


def build_web(store: Any, cfg: WebConfig) -> list[Any]:
    """Routes for the site. Later tasks append handlers here."""
    from . import views

    return [
        *views.routes(store, cfg),
        Mount("/static", app=CachedStaticFiles(directory=str(HERE / "static")), name="static"),
    ]
