"""Key-less demo endpoint used by the landing page: a few tools, throttled per IP.

The demo runs the *real* core functions, so it is deliberately narrow: only the
tools, modes and argument names the landing page actually uses are accepted
(``DEMO_MODES`` / ``DEMO_ARGS``).  Anything else - an unlisted mode such as
``text``'s ``regex_match`` (arbitrary caller-supplied regex), an unknown key, an
oversized string - is refused before a core function ever sees it.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Any

from ..core.convert import convert
from ..core.datetimex import datetime_tool
from ..core.numbers import numbers
from ..core.text import text

DEMO_TOOLS: dict[str, Callable[..., dict[str, Any]]] = {"numbers": numbers, "convert": convert, "datetime": datetime_tool, "text": text}

# Allow-list: exactly the modes and argument names static/site.js sends.
DEMO_MODES: dict[str, set[str]] = {"numbers": {"compare"}, "convert": {"units"}, "datetime": {"diff"}, "text": {"count"}}
DEMO_ARGS: dict[str, set[str]] = {"numbers": {"values"}, "convert": {"value", "from_unit", "to"}, "datetime": {"from", "to"}, "text": {"text"}}

MAX_BODY = 8192  # bytes; checked against content-length before the body is read
MAX_STRING = 2000  # characters per string argument (or list item)
MAX_ITEMS = 50  # entries per list argument


class Throttle:
    def __init__(self, limit: int = 30, window: float = 60.0) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._last_prune = 0.0

    def allow(self, ip: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            if now - self._last_prune >= self.window:  # forget idle IPs, at most once per window
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < self.window}
                self._last_prune = now
            hits = [t for t in self._hits.get(ip, []) if now - t < self.window]
            if len(hits) >= self.limit:
                self._hits[ip] = hits
                return False, math.ceil(self.window - (now - hits[0]))
            hits.append(now)
            self._hits[ip] = hits
            return True, 0


def _bad(message: str) -> dict[str, Any]:
    return {"ok": False, "error": "invalid_input", "message": message}


def _too_big(name: str, value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        return _bad(f"'{name}' is too long for the demo (max {MAX_STRING} characters)") if len(value) > MAX_STRING else None
    if isinstance(value, list):
        if len(value) > MAX_ITEMS:
            return _bad(f"'{name}' has too many items for the demo (max {MAX_ITEMS})")
        return next((e for e in (_too_big(name, v) for v in value) if e), None)
    if isinstance(value, dict):
        return _bad(f"'{name}' must be a string, number or list in the demo")
    return None


def validate(tool: str, args: Any) -> dict[str, Any] | None:
    """``None`` when the call is allowed, else a contract-shaped ``invalid_input``.

    The caller (``views.demo``) turns a non-``None`` return into a 400.
    """
    modes = DEMO_MODES.get(tool)
    if modes is None:
        return _bad(f"demo supports {', '.join(DEMO_TOOLS)}")
    if not isinstance(args, dict):
        return _bad("send a JSON object with a mode and the tool's arguments")
    mode = args.get("mode")
    if not isinstance(mode, str) or mode not in modes:
        return _bad(f"demo {tool} supports mode {' or '.join(sorted(modes))} only; use an API key for the other modes")
    allowed = DEMO_ARGS[tool]
    extra = sorted(k for k in args if k != "mode" and k not in allowed)
    if extra:
        return _bad(f"demo {tool} accepts only {', '.join(sorted(allowed))}; unexpected {', '.join(extra)}")
    for name in allowed:
        err = _too_big(name, args.get(name))
        if err:
            return err
    return None


def run(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    bad = validate(tool, args)
    if bad is not None:
        return bad
    call = dict(args)
    mode = str(call.pop("mode"))
    return DEMO_TOOLS[tool](mode, **call)
