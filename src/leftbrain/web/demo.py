"""Key-less demo endpoint used by the landing page: a few tools, throttled per IP."""

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


class Throttle:
    def __init__(self, limit: int = 30, window: float = 60.0) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, ip: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(ip, []) if now - t < self.window]
            if len(hits) >= self.limit:
                self._hits[ip] = hits
                return False, math.ceil(self.window - (now - hits[0]))
            hits.append(now)
            self._hits[ip] = hits
            if len(self._hits) > 5000:  # forget idle IPs
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < self.window}
            return True, 0


def run(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    fn = DEMO_TOOLS[tool]
    mode = str(args.pop("mode", "") or "")
    return fn(mode, **args) if mode else fn(**args)
