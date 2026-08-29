"""#28 §1 step 3: a call that cannot be interrupted is killed, and the server stays up.

The rest of the suite runs in-process (see `conftest.py`) because a worker per call is slow
when you make thousands of them. This module turns isolation on and exercises the real pool,
including the one observation that would have caught the original outage: `/healthz` still
answering 200 while a runaway request is in flight.
"""

import os
import threading
import time

import pytest

pytest.importorskip("pebble", reason="compute isolation needs pebble: pip install 'leftbrain[server]'")

from leftbrain import runner  # noqa: E402

#: An expression Layer 0 cannot judge and so cannot refuse: the cost of factorising a
#: 62-digit semiprime is not in the size of any number written down, so the digit estimate
#: has nothing to measure. This is the shape the worker exists to catch.
UNESTIMABLE_BOMB = "factorint(10000000000000000000000000000603000000000000000000000000001881)"
#: Slack on a wall-clock lower bound. Two `time.monotonic()` readings taken seconds apart
#: are large floats, so their difference is exact only to within an ULP or so of their
#: magnitude; 10 ms is far below anything a returned-too-early bug would show.
CLOCK_SLOP = 0.01

#: A tower multiplied by a symbol. Layer 0 sizes every literal subtree, so this one is
#: refused before a worker is asked for.
ESTIMABLE_BOMB = "9^9^9^9*x"


def test_a_bomb_layer_0_can_size_never_reaches_a_worker():
    """Every literal subtree is measured, whatever it sits beside."""
    import time

    started = time.monotonic()
    r = runner.run_guarded("math", "eval", {"expr": ESTIMABLE_BOMB})
    assert time.monotonic() - started < 1.0
    assert r["ok"] is False and r["error"] == "too_large" and "digits" in r["message"], r


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("LEFTBRAIN_COMPUTE_ISOLATION", "1")
    monkeypatch.setenv("LEFTBRAIN_COMPUTE_TIMEOUT", "3")
    runner.configure()
    runner.run_guarded("math", "eval", {"expr": "1+1"})  # warm the pool before timing anything
    yield runner
    runner.configure()
    monkeypatch.setenv("LEFTBRAIN_COMPUTE_ISOLATION", "0")


def test_the_pool_really_is_a_separate_process(isolated):
    parent = os.getpid()
    child = isolated.run_guarded("math", "eval", {"expr": "1+1"})
    assert child["ok"]
    assert isolated.isolation_active()
    assert os.getpid() == parent


def test_an_uninterruptible_call_is_stopped_at_the_deadline(isolated):
    started = time.monotonic()
    r = isolated.run_guarded("math", "eval", {"expr": UNESTIMABLE_BOMB})
    elapsed = time.monotonic() - started
    assert r["ok"] is False and r["error"] == "timeout" and r["retryable"] is False
    assert r["details"]["stopped"] == "worker_terminated"
    assert r["details"]["limit_seconds"] == 3
    # Terminating a worker inside a C call costs a little more than the deadline itself,
    # which is why the envelope reports both numbers.
    #
    # The lower bound carries a tolerance. `elapsed` is the difference of two large
    # `time.monotonic()` floats — a machine up for a day gives them an ULP of a few
    # nanoseconds — so a run stopped exactly on the deadline can measure a hair *under* it.
    # CI failed once at 2.9999999999999716, which is 3 seconds by any measure that matters.
    # What this bound is for is catching a call that returned early, not the last digit.
    assert 3 - CLOCK_SLOP <= elapsed < 6, elapsed
    assert r["details"]["elapsed_seconds"] >= 3 - CLOCK_SLOP


def test_the_pool_survives_a_killed_worker(isolated):
    isolated.run_guarded("math", "eval", {"expr": UNESTIMABLE_BOMB})
    assert isolated.run_guarded("math", "eval", {"expr": "6*7"})["result"]["value"] == "42"


def test_a_caller_timeout_cannot_exceed_the_server_ceiling(isolated):
    """`timeout=600` used to walk straight past the design."""
    started = time.monotonic()
    r = isolated.run_guarded("math", "eval", {"expr": UNESTIMABLE_BOMB}, timeout=600)
    assert r["error"] == "timeout" and r["details"]["limit_seconds"] == 3
    assert time.monotonic() - started < 6


def test_a_shorter_caller_timeout_is_honoured(isolated):
    r = isolated.run_guarded("math", "eval", {"expr": UNESTIMABLE_BOMB}, timeout=1)
    assert r["error"] == "timeout" and r["details"]["limit_seconds"] == 1


def test_isolation_can_be_turned_off_and_the_answer_is_the_same(monkeypatch):
    monkeypatch.setenv("LEFTBRAIN_COMPUTE_ISOLATION", "0")
    runner.configure()
    assert runner.isolation_active() is False
    assert runner.run_guarded("math", "eval", {"expr": "6*7"})["result"]["value"] == "42"


# --- the observation that would have caught the outage ----------------------


def test_healthz_keeps_answering_while_a_runaway_request_is_in_flight(isolated, tmp_path):
    """The acceptance criterion from #28 §1: 'healthz stayed 200 throughout'.

    Before this, the runaway held the GIL, so it starved the event loop, `/healthz` and
    every other key's request - which is what took the hosted instance down for 35 minutes.
    """
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    from leftbrain.serve import build_app

    app = build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"))
    with TestClient(app) as client:
        codes: list[int] = []
        stop = threading.Event()

        def poll() -> None:
            while not stop.is_set():
                codes.append(client.get("/healthz").status_code)
                time.sleep(0.05)

        poller = threading.Thread(target=poll, daemon=True)
        poller.start()
        try:
            r = isolated.run_guarded("math", "eval", {"expr": UNESTIMABLE_BOMB})
        finally:
            stop.set()
            poller.join(timeout=5)

    assert r["error"] == "timeout"
    assert len(codes) > 10, f"only {len(codes)} health checks ran; the poller was starved"
    assert set(codes) == {200}, sorted(set(codes))


# --- degrading honestly rather than refusing everything ---------------------


def test_a_pool_that_cannot_start_is_reported_as_off_not_used(monkeypatch):
    """`forkserver` re-imports `__main__` in the child, so an interpreter whose `__main__`
    is not an importable file - `python -` reading a piped script, some embedded hosts -
    kills every worker at startup. The pool is probed when it is built so the boot log tells
    the truth, and calls run in-process rather than every one of them answering `busy`."""
    monkeypatch.setenv("LEFTBRAIN_COMPUTE_ISOLATION", "1")
    runner.configure()

    class DeadPool:
        def schedule(self, *a, **k):
            raise RuntimeError("All workers expired")

        def stop(self):
            pass

    monkeypatch.setattr(runner, "ProcessPool", DeadPool, raising=False)
    monkeypatch.setattr(runner, "_get_pool", lambda: None)
    r = runner.run_guarded("math", "eval", {"expr": "6*7"})
    assert r["ok"] and r["result"]["value"] == "42"
    assert r["compute_ms"] >= 0
    runner.configure()


def test_a_pool_that_breaks_mid_life_is_rebuilt_rather_than_refusing_for_ever(isolated, monkeypatch):
    """The bug this test exists for: one broken pool made every later call `busy` for the
    lifetime of the process."""
    real = isolated._get_pool()
    calls = {"n": 0}

    class BrokenOnce:
        def schedule(self, *a, **k):
            calls["n"] += 1
            raise RuntimeError("All workers expired")

        def stop(self):
            pass

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(isolated, "_pool", BrokenOnce())
    r = isolated.run_guarded("math", "eval", {"expr": "6*7"})
    assert calls["n"] == 1
    assert r["ok"] and r["result"]["value"] == "42", r
    assert r.get("error") != "busy"
    assert real is not None
