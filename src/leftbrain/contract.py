"""The response contract every leftbrain tool follows.

Every tool returns a plain dict shaped like::

    {
      "ok": true,
      "result": ...,              # the answer, JSON-serialisable
      "steps": [...],             # optional: how it was derived
      "assumptions": [...],       # interpretations made on the caller's behalf
      "warnings": [...],          # things the caller should know
    }

or, when the input was ambiguous and guessing would be dangerous::

    {
      "ok": false,
      "error": "ambiguous",
      "message": "...",
      "needs": {"field": "unit", "options": ["metric ton", "short ton"]},
    }

or, on a genuine failure::

    {"ok": false, "error": "invalid_input" | "unsupported" | "timeout" | "internal", "message": "..."}

Rules:
* never return ``null`` as a result - fail loudly instead;
* never guess when two readings are both plausible - return ``needs``;
* always surface an interpretation in ``assumptions`` when one was made.
"""

from __future__ import annotations

import functools
import traceback
from collections.abc import Callable
from typing import Any

__all__ = [
    "Ambiguous",
    "ToolError",
    "ok",
    "fail",
    "tool",
]


class ToolError(Exception):
    """Raised inside a tool to produce a structured failure."""

    code = "invalid_input"

    def __init__(self, message: str, code: str | None = None, **extra: Any):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.extra = extra


class Ambiguous(ToolError):
    """Raised when the input has more than one plausible reading.

    ``field`` names the parameter that needs clarifying and ``options`` lists
    the concrete alternatives the caller can pick from.
    """

    code = "ambiguous"

    def __init__(self, message: str, field: str, options: list[Any]):
        super().__init__(message, needs={"field": field, "options": options})


class Unsupported(ToolError):
    code = "unsupported"


class Timeout(ToolError):
    code = "timeout"


def ok(
    result: Any,
    *,
    steps: list[str] | None = None,
    assumptions: list[str] | None = None,
    warnings: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "result": result}
    if steps:
        out["steps"] = steps
    out["assumptions"] = assumptions or []
    out["warnings"] = warnings or []
    out.update(extra)
    return out


def fail(code: str, message: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "error": code, "message": message}
    out.update(extra)
    return out


def tool(fn: Callable[..., Any]) -> Callable[..., dict[str, Any]]:
    """Decorator: convert exceptions raised by a core function into the contract.

    Core functions may either return an ``ok(...)`` dict directly or raise
    :class:`ToolError` / :class:`Ambiguous`.  Anything else is reported as an
    ``internal`` error with the exception type - never swallowed silently.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except ToolError as e:
            return fail(e.code, e.message, **e.extra)
        except (ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError) as e:
            return fail("invalid_input", f"{type(e).__name__}: {e}")
        except Exception as e:  # pragma: no cover - defensive
            return fail(
                "internal",
                f"{type(e).__name__}: {e}",
                trace=traceback.format_exc(limit=3),
            )

    return wrapper
