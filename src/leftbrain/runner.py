"""Run a tool call in a worker process that can actually be killed (#28 §1, step 3).

The hosted instance went down for 35 minutes on one `math.eval 9^9^9^9`. The reason is not
that the call was slow: it is that CPython cannot interrupt it. `int.__pow__`, `sre`'s
matching loop and `difflib`'s inner loops are C code that never returns to the eval loop, so

* an async exception queued with `PyThreadState_SetAsyncExc` is never delivered - it waits
  for a bytecode boundary that does not come;
* `SIGALRM` is the same, and only runs on the main thread;
* the daemon thread and `Thread.join(timeout)` that `math` used stopped *waiting* while the
  thread kept *working*, which is why a call with `timeout=5` was still computing at 9.53 s;
* and none of it helps anyway, because the runaway holds the GIL. mcp already runs a sync
  tool on a worker thread, and that thread starves the event loop, `/healthz` and every
  other key's request just the same.

A process can be killed. So the modes that run caller-supplied work go to a pool of them,
each with a wall-clock deadline the parent enforces and kernel limits the child cannot talk
its way out of. Layer 0 (#28 §2) still refuses the obvious bombs in microseconds; this is
the backstop for what it cannot estimate.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import sys
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from .contract import fail

log = logging.getLogger("leftbrain")

__all__ = ["configure", "isolation_active", "run_guarded", "shutdown", "settings"]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


class Settings:
    """Read from the environment on first use, so a test can set them and re-configure."""

    def __init__(self) -> None:
        #: Wall clock a single call may take before its worker is terminated.
        self.timeout = _env_float("LEFTBRAIN_COMPUTE_TIMEOUT", 15.0)
        #: How long a call waits for a free worker before it is told the server is busy.
        #: Shorter than the compute deadline on purpose: under load a burst should be
        #: refused quickly rather than stack up 15-second waits.
        self.queue_timeout = _env_float("LEFTBRAIN_QUEUE_TIMEOUT", 5.0)
        #: Concurrent workers. One per CPU: past that they only take time from each other.
        self.max_inflight = _env_int("LEFTBRAIN_MAX_INFLIGHT", os.cpu_count() or 2)
        #: Calls a worker handles before it is replaced, so SymPy's caches stay bounded.
        self.max_tasks = _env_int("LEFTBRAIN_WORKER_MAX_TASKS", 200)
        #: Address space a worker may map. A result too big to send dies in the child.
        self.memory_limit = _env_int("LEFTBRAIN_WORKER_MEMORY_BYTES", 1_500_000_000)
        #: CPU seconds the kernel allows, a little above the wall clock so the normal
        #: path is always the graceful one and this only fires if the parent timer does not.
        self.cpu_limit = _env_int("LEFTBRAIN_WORKER_CPU_SECONDS", int(self.timeout) + 2)

    @property
    def enabled(self) -> bool:
        return os.environ.get("LEFTBRAIN_COMPUTE_ISOLATION", "1").strip().lower() not in ("0", "false", "no", "off", "")


settings = Settings()

_pool: Any = None
_lock = threading.Lock()
_unavailable: str | None = None


def _limit_child() -> None:
    """Kernel limits the child cannot exceed, whatever the parent's timer does."""
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows has no resource module
        return
    for what, value in (
        (resource.RLIMIT_CPU, (settings.cpu_limit, settings.cpu_limit + 1)),
        (resource.RLIMIT_AS, (settings.memory_limit, settings.memory_limit)),
    ):
        try:
            resource.setrlimit(what, value)
        except (ValueError, OSError):  # pragma: no cover - platform-dependent
            pass


def _context() -> Any:
    """`forkserver` where it exists: `fork` is unsafe in a threaded server, and `spawn` is slow.

    Python 3.12 still defaults to `fork` on POSIX, which is why this is set explicitly - a
    fork of a process holding locks in other threads deadlocks the child sooner or later.
    """
    if sys.platform == "win32":
        return multiprocessing.get_context("spawn")
    try:
        ctx = multiprocessing.get_context("forkserver")
        ctx.set_forkserver_preload(["leftbrain"])
        return ctx
    except (ValueError, RuntimeError):  # pragma: no cover - platform-dependent
        return multiprocessing.get_context("spawn")


def _get_pool() -> Any:
    """The pool, started on first use. ``None`` when isolation cannot be provided."""
    global _pool, _unavailable
    if _pool is not None or _unavailable is not None:
        return _pool
    with _lock:
        if _pool is not None or _unavailable is not None:
            return _pool
        try:
            from pebble import CONSTS, ProcessPool
        except ImportError:
            _unavailable = "pebble is not installed"
            log.warning("compute isolation off: %s (pip install 'leftbrain[server]')", _unavailable)
            return None
        # pebble sends SIGTERM and waits `term_timeout` (3s by default) before SIGKILL. A
        # worker inside `int.__pow__` can never run a signal handler, so for the workload
        # this exists to stop that wait is always spent in full. Half a second is enough for
        # the cases that *can* exit cleanly, and keeps the worst case at deadline + 0.5s -
        # which is what has to sit below the ingress timeout.
        CONSTS.term_timeout = _env_float("LEFTBRAIN_WORKER_TERM_GRACE", 0.5)
        try:
            _pool = ProcessPool(
                max_workers=settings.max_inflight,
                max_tasks=settings.max_tasks,
                initializer=_limit_child,
                context=_context(),
            )
        except Exception as e:  # pragma: no cover - defensive
            _unavailable = f"{type(e).__name__}: {e}"
            log.warning("compute isolation off: %s", _unavailable)
            return None
    return _pool


def configure() -> None:
    """Re-read the environment and drop any running pool. For tests and for start-up."""
    global settings, _unavailable
    shutdown()
    settings = Settings()
    _unavailable = None


def shutdown() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            try:
                _pool.stop()
                _pool.join(timeout=5)
            except Exception:  # pragma: no cover - best effort at exit
                pass
            _pool = None


def isolation_active() -> bool:
    """True when a call would really run in a killable process."""
    return settings.enabled and _get_pool() is not None


def _call(tool: str, mode: str, params: dict[str, Any]) -> dict[str, Any]:
    """The child's whole job: look the tool up by name and run it."""
    import leftbrain

    return leftbrain.TOOLS[tool](mode, **params)


def run_guarded(tool: str, mode: str, params: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
    """Run one tool call under a deadline that is enforced by killing the worker.

    Falls back to running in this process when isolation is off or unavailable - the answer
    is the same, only the guarantee is missing, and that is said in the log rather than
    silently.
    """
    import leftbrain

    if not settings.enabled:
        return leftbrain.TOOLS[tool](mode, **params)
    pool = _get_pool()
    if pool is None:
        return leftbrain.TOOLS[tool](mode, **params)
    deadline = settings.timeout if timeout is None else min(float(timeout), settings.timeout)
    started = time.monotonic()
    try:
        future = pool.schedule(_call, args=(tool, mode, params), timeout=deadline)
    except Exception as e:  # pool stopping, or full beyond its queue
        log.warning("could not schedule %s.%s: %s", tool, mode, e)
        return fail(
            "busy",
            "the server could not take this call right now; nothing was computed",
            details={"tool": tool, "mode": mode},
            hint="Retry in a moment.",
        )
    try:
        return future.result(timeout=deadline + settings.queue_timeout)
    except FutureTimeout:
        future.cancel()
        elapsed = round(time.monotonic() - started, 2)
        return fail(
            "timeout",
            f"{tool}.{mode} was stopped after {deadline:g}s - it did not finish within the limit "
            f"and the worker was terminated",
            details={
                "tool": tool, "mode": mode, "limit_seconds": deadline,
                # Wall clock the caller actually waited: terminating a worker that is inside
                # an uninterruptible C call takes a moment longer than the deadline itself.
                "elapsed_seconds": elapsed, "stopped": "worker_terminated",
            },
            hint="Narrow the input; the same call will take just as long again.",
        )
    except MemoryError:
        return fail(
            "resource_exhausted",
            f"{tool}.{mode} ran out of memory and its worker was terminated",
            details={"tool": tool, "mode": mode, "limit_bytes": settings.memory_limit},
            hint="Narrow the input - fewer items, a shorter range, a smaller result.",
        )
    except Exception as e:
        # A worker that died for any other reason: the OOM killer, a segfault in a C
        # extension, the pool being stopped underneath us. The caller may retry this one.
        log.exception("worker running %s.%s died", tool, mode)
        return fail(
            "internal",
            f"the worker running {tool}.{mode} stopped unexpectedly ({type(e).__name__})",
            details={"tool": tool, "mode": mode},
            retryable=True,
        )
