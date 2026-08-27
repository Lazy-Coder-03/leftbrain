"""What a call cost, carried back with the answer (#28 §6, and §1's step 5).

`url_check` already reported `latency_ms`; nothing else did, so an agent could not tell a 5 ms
answer from a 19 s one that nearly timed out, and nobody could see a slow mode without watching
the server. Every response now carries a `meta` block, and the same numbers go out as headers so
they are visible without parsing the body.

`meta` never affects `ok`. It is the regression alarm for the 15-second cut as much as it is
telemetry: **a response whose `compute_ms` exceeds its own deadline is a timeout that did not
fire** — measurably the case before #41, when a call with `timeout=5` was still computing at
9.53 s. Once the worker enforces the ceiling that cannot happen, and an alert on it says
immediately if anyone regresses to thread-join semantics.
"""

from __future__ import annotations

import contextvars
import secrets
import time
from typing import Any

__all__ = [
    "REQUEST_ID_HEADER",
    "LATENCY_HEADER",
    "current_quota",
    "current_request_id",
    "meta_for",
    "new_request_id",
]

#: Set by the HTTP auth middleware, so a tool result can carry the caller's remaining budget
#: without every layer having to pass it down.
current_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("leftbrain_request_id", default=None)
current_quota: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar("leftbrain_quota", default=None)

REQUEST_ID_HEADER = "x-request-id"
LATENCY_HEADER = "x-leftbrain-latency-ms"


def new_request_id() -> str:
    """Short, unguessable, and long enough not to collide within a log's retention."""
    return secrets.token_hex(8)


def meta_for(
    tool: str,
    mode: str | None,
    envelope: Any,
    *,
    started: float,
    version: str,
) -> dict[str, Any] | None:
    """The `meta` block for one call, or ``None`` when the result is not an envelope."""
    if not isinstance(envelope, dict) or "ok" not in envelope:
        return None
    latency_ms = round((time.perf_counter() - started) * 1000)
    result = envelope.get("result")
    meta: dict[str, Any] = {
        "tool": tool,
        "latency_ms": latency_ms,
        # What the engine itself spent, when the call went through a worker. Equal to the
        # latency for a tool that runs in-process, minus the MCP layer's own overhead.
        "compute_ms": envelope.pop("compute_ms", latency_ms),
        "version": version,
        # True whenever a cap trimmed the answer, so a caller reading a list knows it is
        # not the whole list without checking each mode's own flag (#28 §2f).
        "truncated": bool(isinstance(result, dict) and result.get("truncated")),
    }
    if mode is not None:  # a call that left `mode` out is answered by the tool's default
        meta["mode"] = mode
    request_id = current_request_id.get()
    if request_id:
        meta["request_id"] = request_id
    quota = current_quota.get()
    if quota:
        meta["quota"] = quota
    upstream = envelope.pop("upstream_ms", None)
    if upstream is not None:
        meta["upstream_ms"] = upstream
    return meta
