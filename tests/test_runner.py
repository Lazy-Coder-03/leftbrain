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

#: An expression Layer 0 cannot judge and so cannot refuse: the tower is literal, but
#: multiplying it by a symbol makes the estimate return "unknown" rather than "enormous".
#: This is exactly the shape the worker exists to catch.
UNESTIMABLE_BOMB = "9^9^9^9*x"


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
    assert 3 <= elapsed < 6, elapsed
    assert r["details"]["elapsed_seconds"] >= 3


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
