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
      "retryable": false,
    }

or, on a genuine failure::

    {
      "ok": false,
      "error": "too_large",       # one of CODES
      "message": "...",           # what happened, in a sentence
      "details": {...},           # optional: the numbers behind the message
      "retryable": false,         # would an identical retry ever help?
      "hint": "...",              # optional: what to change
    }

``retryable`` is on every failure because a client that reads only ``ok: false`` retries, and an
identical retry of a call that hit a limit multiplies the load that caused it.

Rules:
* never return ``null`` as a result - fail loudly instead;
* never guess when two readings are both plausible - return ``needs``;
* always surface an interpretation in ``assumptions`` when one was made;
* never ship a stack trace to a caller - log it and set ``LEFTBRAIN_DEBUG`` to see it.
"""

from __future__ import annotations

import functools
import logging
import os
import traceback
from collections.abc import Callable
from typing import Any

__all__ = [
    "Ambiguous",
    "Busy",
    "CODES",
    "ResourceExhausted",
    "Timeout",
    "TooLarge",
    "ToolError",
    "Unsupported",
    "debug_enabled",
    "fail",
    "ok",
    "schema_rejection",
    "tool",
]

log = logging.getLogger("leftbrain")

#: Every failure code, and whether an *identical* retry could ever succeed.
CODES: dict[str, bool] = {
    "invalid_input": False,  # the call is wrong; the same call stays wrong
    "ambiguous": False,  # pick one of needs.options and call again - a different call
    "unsupported": False,  # the mode cannot do this at all
    "too_large": False,  # a pre-check refused it before any work started
    "timeout": False,  # ran to the deadline and was stopped
    "resource_exhausted": False,  # hit a memory or CPU limit
    "forbidden": False,  # the key may not call this tool or mode
    "busy": True,  # nothing was computed; the server was saturated
    "internal": True,  # something broke unexpectedly
}

_OFF = {"", "0", "false", "no", "off"}


def debug_enabled() -> bool:
    """True when ``LEFTBRAIN_DEBUG`` asks for server internals in the response."""
    return os.environ.get("LEFTBRAIN_DEBUG", "").strip().lower() not in _OFF


class ToolError(Exception):
    """Raised inside a tool to produce a structured failure.

    ``details`` and ``hint`` land in the envelope; anything else in ``extra`` is passed to
    :func:`fail` unchanged (``needs`` is the usual one).
    """

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
    """The work ran to its deadline and was stopped."""

    code = "timeout"


class TooLarge(ToolError):
    """A pre-check refused the input because the result would be enormous.

    Raised *before* any real work, so it costs microseconds - unlike :class:`Timeout`,
    which means the deadline was actually spent.
    """

    code = "too_large"


class ResourceExhausted(ToolError):
    """A memory or CPU limit was hit rather than a wall-clock one."""

    code = "resource_exhausted"


class Busy(ToolError):
    """The server was saturated and nothing was computed - the one failure worth retrying."""

    code = "busy"


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


def fail(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    retryable: bool | None = None,
    hint: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a failure envelope. ``retryable`` defaults to what :data:`CODES` says about ``code``."""
    out: dict[str, Any] = {"ok": False, "error": code, "message": message}
    out.update(extra)
    if details:
        out["details"] = details
    out["retryable"] = CODES.get(code, False) if retryable is None else retryable
    if hint:
        out["hint"] = hint
    return out


def schema_rejection(tool_name: str, errors: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn a pydantic ``ValidationError.errors()`` into the contract envelope.

    A call whose arguments fail the input schema never reaches the tool, but the client still
    gets the shape every other answer has: which parameters are missing, which are the wrong
    type, and nothing else - the rejected values are the caller's data, and the pydantic
    documentation link is noise to an agent.
    """
    missing: list[str] = []
    parameters: list[dict[str, str]] = []
    for err in errors:
        name = ".".join(str(part) for part in err.get("loc", ()))
        problem = str(err.get("msg", "invalid"))
        if err.get("type") == "missing":
            missing.append(name)
            parameters.append({"parameter": name, "problem": "missing"})
        else:
            parameters.append({"parameter": name, "problem": problem})
    parts = []
    if missing:
        parts.append(f"missing required parameter(s): {', '.join(missing)}")
    parts += [f"{p['parameter']} - {p['problem']}" for p in parameters if p["problem"] != "missing"]
    extra = {"needs": {"missing": missing}} if missing else {}
    return fail(
        "invalid_input",
        f"{tool_name}: {'; '.join(parts)}",
        details={"tool": tool_name, "parameters": parameters},
        hint="Correct the parameters and call again; the tool's schema lists what each one accepts.",
        **extra,
    )


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
        except Exception as e:
            # The trace names server files; it belongs in the log, not in every caller's response.
            # Three frames, not the whole stack: a RecursionError otherwise logs thousands.
            trace = traceback.format_exc(limit=3)
            log.error("%s raised %s\n%s", getattr(fn, "__name__", "tool"), type(e).__name__, trace)
            out = fail("internal", f"{type(e).__name__}: {e}")
            if debug_enabled():
                out["trace"] = trace
            return out

    return wrapper
