"""#28 §6: every answer says what it cost.

`url_check` reported `latency_ms`; nothing else did, so an agent could not tell a 5 ms answer
from a 19 s one that nearly timed out. `meta` never affects `ok` — and it is the regression
alarm for the 15-second cut as much as it is telemetry.
"""

import json

import anyio
import pytest

pytest.importorskip("mcp", reason="the MCP servers need the optional 'mcp' package")

from leftbrain import __version__  # noqa: E402
from leftbrain import mcp_server as mcp
from leftbrain.observe import (  # noqa: E402
    LATENCY_HEADER,
    REQUEST_ID_HEADER,
    current_quota,
    current_request_id,
)


def call(name: str, args: dict):
    return anyio.run(lambda: mcp.server.call_tool(name, args))


def meta_of(name: str, args: dict) -> dict:
    return call(name, args).structured_content["meta"]


def test_a_successful_answer_says_what_it_cost():
    meta = meta_of("math", {"mode": "eval", "expr": "1/3"})
    assert meta["tool"] == "math" and meta["mode"] == "eval"
    assert isinstance(meta["latency_ms"], int) and meta["latency_ms"] >= 0
    assert isinstance(meta["compute_ms"], int) and meta["compute_ms"] >= 0
    assert meta["version"] == __version__ and meta["truncated"] is False


def test_a_failure_carries_meta_too():
    """A refusal is the answer most worth timing: it is the one an agent may retry."""
    meta = meta_of("math", {"mode": "eval", "expr": "9^9^9^9"})
    assert meta["tool"] == "math" and isinstance(meta["latency_ms"], int)


def test_a_call_without_a_mode_does_not_invent_one():
    assert "mode" not in meta_of("math", {"expr": "1+1"})


def test_truncated_is_lifted_out_of_the_result():
    """So a caller reading a list knows it is not the whole list, without checking each mode."""
    meta = meta_of("text", {"mode": "regex_match", "text": "ab" * 10000, "pattern": "(a)(b)"})
    assert meta["truncated"] is True
    assert meta_of("text", {"mode": "regex_match", "text": "ab", "pattern": "(a)(b)"})["truncated"] is False


def test_the_text_block_and_the_structured_result_agree():
    result = call("math", {"mode": "eval", "expr": "1/3"})
    assert json.loads(result.content[0].text) == result.structured_content


def test_a_schema_rejection_is_measured_as_well():
    meta = call("convert", {}).structured_content["meta"]
    assert meta["tool"] == "convert" and isinstance(meta["latency_ms"], int)


def test_meta_never_changes_the_answer():
    envelope = call("math", {"mode": "eval", "expr": "6*7"}).structured_content
    assert envelope["ok"] is True and envelope["result"]["value"] == "42"
    assert "compute_ms" not in envelope["result"] and "compute_ms" not in envelope


def test_the_request_id_and_quota_ride_along_when_the_server_set_them():
    token = current_request_id.set("abc123")
    quota = current_quota.set({"remaining_today": 7, "daily_quota": 1000, "rpm": 60})
    try:
        meta = meta_of("math", {"mode": "eval", "expr": "1+1"})
    finally:
        current_request_id.reset(token)
        current_quota.reset(quota)
    assert meta["request_id"] == "abc123" and meta["quota"]["remaining_today"] == 7


def test_nothing_is_claimed_when_the_server_set_nothing():
    meta = meta_of("math", {"mode": "eval", "expr": "1+1"})
    assert "quota" not in meta


# --- the regression alarm ---------------------------------------------------


def test_compute_ms_can_never_exceed_the_deadline():
    """#28 §1's own alarm: a response whose `compute_ms` is past its deadline is a timeout
    that did not fire — measurably the case before the worker landed, when a call with
    `timeout=5` was still computing at 9.53 s."""
    from leftbrain import runner

    meta = meta_of("math", {"mode": "eval", "expr": "factorial(2000)"})
    assert meta["compute_ms"] <= runner.settings.timeout * 1000


# --- over HTTP --------------------------------------------------------------


def test_the_headers_carry_the_id_and_the_latency(tmp_path, monkeypatch):
    pytest.importorskip("starlette")
    monkeypatch.setenv("LEFTBRAIN_SECRET", "x" * 32)
    from starlette.testclient import TestClient

    from leftbrain.serve import build_app

    with TestClient(build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"))) as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert len(r.headers[REQUEST_ID_HEADER]) == 16
        assert int(r.headers[LATENCY_HEADER]) >= 0


def test_a_caller_supplied_request_id_is_kept(tmp_path, monkeypatch):
    """So one id spans both sides of a trace."""
    pytest.importorskip("starlette")
    monkeypatch.setenv("LEFTBRAIN_SECRET", "x" * 32)
    from starlette.testclient import TestClient

    from leftbrain.serve import build_app

    with TestClient(build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"))) as client:
        r = client.get("/healthz", headers={REQUEST_ID_HEADER: "trace-me"})
        assert r.headers[REQUEST_ID_HEADER] == "trace-me"
