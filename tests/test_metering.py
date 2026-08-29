"""#62: the daily quota billed HTTP requests, not tool calls.

`AuthMiddleware` metered every request under a protected prefix, so a hosted connector that
re-`initialize`d before each `tools/call` spent two units per call, a connect spent four
before any work, and a call the tool then refused for bad input had already been charged.

The split these tests pin: **rpm sees every request** — it is abuse protection and a
handshake is still traffic — while **the daily quota counts work actually done**.
"""

import json

import pytest
from starlette.testclient import TestClient

from leftbrain.keys import KeyStore
from leftbrain.scopes import parse_scope
from leftbrain.serve import build_app
from leftbrain.web.config import WebConfig

ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}


def rpc(c, key, method, **params):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        body["params"] = params
    return c.post("/mcp", headers={"Authorization": f"Bearer {key}", **ACCEPT}, json=body)


def envelope(response):
    text = response.text
    if response.headers["content-type"].startswith("text/event-stream"):
        line = next(ln for ln in text.splitlines() if ln.startswith("data:"))
        msg = json.loads(line[5:])
    else:
        msg = json.loads(text)
    assert "error" not in msg, msg
    return msg["result"]["structuredContent"]


@pytest.fixture
def keyed(tmp_path):
    """An app with a key store, and a second handle on the same database to read counters."""

    def build(daily_quota=1000, rpm=1000):
        db = str(tmp_path / "k.sqlite3")
        app = build_app(include_external=False, keys_db=db, web_config=WebConfig(None, None, "s" * 20, None, True))
        store = KeyStore(db)
        raw, info = store.create("a@b.co", daily_quota=daily_quota, rpm=rpm)
        return app, store, raw, info.prefix

    return build


def used(store, prefix):
    return store.get_by_prefix(prefix).used_today


# --- 1. one tool call is one unit ------------------------------------------------------


def test_a_single_tool_call_costs_exactly_one(keyed):
    app, store, key, prefix = keyed()
    with TestClient(app) as c:
        assert envelope(rpc(c, key, "tools/call", name="math", arguments={"mode": "eval", "expr": "1+1"}))["ok"]
        assert used(store, prefix) == 1
        assert envelope(rpc(c, key, "tools/call", name="math", arguments={"mode": "eval", "expr": "2+2"}))["ok"]
        assert used(store, prefix) == 2


def test_a_client_that_re_initializes_before_every_call_still_pays_one(keyed):
    """The reported shape: Claude web's connector re-handshakes, and each call cost two."""
    app, store, key, prefix = keyed()
    with TestClient(app) as c:
        for _ in range(3):
            rpc(c, key, "initialize", **INIT)
            rpc(c, key, "tools/call", name="math", arguments={"mode": "eval", "expr": "1+1"})
        assert used(store, prefix) == 3


# --- 2. the handshake is free ----------------------------------------------------------


@pytest.mark.parametrize(("method", "params"), [("initialize", INIT), ("tools/list", None)])
def test_protocol_traffic_does_not_touch_the_daily_quota(keyed, method, params):
    app, store, key, prefix = keyed()
    with TestClient(app) as c:
        r = rpc(c, key, method, **(params or {}))
        assert r.status_code == 200
        assert used(store, prefix) == 0


def test_a_whole_connect_costs_nothing(keyed):
    """Four requests before any work accounted for the 8 units missing at the first call."""
    app, store, key, prefix = keyed()
    with TestClient(app) as c:
        rpc(c, key, "initialize", **INIT)
        rpc(c, key, "tools/list")
        c.get("/keys/me", headers={"Authorization": f"Bearer {key}"})
        assert used(store, prefix) == 0


# --- 3. a refused call is not billed ---------------------------------------------------


def test_bad_input_is_not_billed(keyed):
    app, store, key, prefix = keyed()
    with TestClient(app) as c:
        out = envelope(rpc(c, key, "tools/call", name="math", arguments={"mode": "eval", "expr": "2+"}))
        assert out["ok"] is False and out["error"] == "invalid_input"
        assert used(store, prefix) == 0


def test_an_ambiguous_call_is_not_billed(keyed):
    """`needs` is a refusal too: the server declined to guess between degrees and radians."""
    app, store, key, prefix = keyed()
    with TestClient(app) as c:
        out = envelope(rpc(c, key, "tools/call", name="math", arguments={"mode": "eval", "expr": "sin(30)"}))
        assert out["ok"] is False and out["error"] == "ambiguous"
        assert used(store, prefix) == 0


def test_a_call_outside_the_keys_scope_is_not_billed(keyed):
    app, store, key, prefix = keyed()
    store.set_scope(prefix, parse_scope({"numbers": None}))
    with TestClient(app) as c:
        out = envelope(rpc(c, key, "tools/call", name="math", arguments={"mode": "eval", "expr": "1+1"}))
        assert out["ok"] is False and out["error"] == "forbidden"
        assert used(store, prefix) == 0


def test_a_call_the_key_may_make_is_still_billed(keyed):
    app, store, key, prefix = keyed()
    store.set_scope(prefix, parse_scope({"numbers": None}))
    with TestClient(app) as c:
        assert envelope(rpc(c, key, "tools/call", name="numbers", arguments={"mode": "compare", "values": ["9.11", "9.9"]}))["ok"]
        assert used(store, prefix) == 1


# --- 4. rpm still sees every request ---------------------------------------------------


def test_rpm_counts_requests_the_quota_does_not(keyed):
    """The difference, pinned: three handshakes cost nothing daily and still trip a rpm of 2."""
    app, store, key, prefix = keyed(rpm=2)
    with TestClient(app) as c:
        assert rpc(c, key, "initialize", **INIT).status_code == 200
        assert rpc(c, key, "initialize", **INIT).status_code == 200
        third = rpc(c, key, "initialize", **INIT)
        assert third.status_code == 429 and "rate limit" in third.text
        assert used(store, prefix) == 0


# --- the quota still runs out, and still says so truthfully ----------------------------


def test_the_daily_quota_still_stops_calls(keyed):
    app, store, key, prefix = keyed(daily_quota=2)
    with TestClient(app) as c:
        for _ in range(2):
            assert envelope(rpc(c, key, "tools/call", name="math", arguments={"mode": "eval", "expr": "1+1"}))["ok"]
        assert used(store, prefix) == 2
        spent = rpc(c, key, "tools/call", name="math", arguments={"mode": "eval", "expr": "1+1"})
        assert spent.status_code == 429 and "quota" in spent.text


def test_the_header_and_meta_both_count_the_call_they_ride_on(keyed):
    """The response start is held until the body, so the header cannot quote the budget from
    before the tool ran while `meta.quota` quotes it from after."""
    app, store, key, prefix = keyed(daily_quota=10)
    with TestClient(app) as c:
        r = rpc(c, key, "tools/call", name="math", arguments={"mode": "eval", "expr": "1+1"})
        assert envelope(r)["meta"]["quota"]["remaining_today"] == 9
        assert r.headers["x-ratelimit-remaining-today"] == "9"
        assert used(store, prefix) == 1


@pytest.mark.parametrize("json_response", [False, True])
def test_the_two_agree_on_both_wire_forms(tmp_path, json_response):
    """SSE sends its headers before the body and plain JSON does not; neither may disagree
    with itself, which is the whole reason the start message is deferred."""
    db = str(tmp_path / "k.sqlite3")
    app = build_app(include_external=False, keys_db=db, json_response=json_response,
                    web_config=WebConfig(None, None, "s" * 20, None, True))
    key, _ = KeyStore(db).create("a@b.co", daily_quota=10, rpm=1000)
    with TestClient(app) as c:
        r = rpc(c, key, "tools/call", name="math", arguments={"mode": "eval", "expr": "1+1"})
        assert envelope(r)["meta"]["quota"]["remaining_today"] == 9
        assert r.headers["x-ratelimit-remaining-today"] == "9"


def test_the_reported_budget_falls_by_one_per_billed_call(keyed):
    """What the reporter watched: 992 -> 990 -> 988. It must now fall by exactly one."""
    app, store, key, prefix = keyed(daily_quota=10)
    with TestClient(app) as c:
        seen = []
        for _ in range(3):
            r = rpc(c, key, "tools/call", name="math", arguments={"mode": "eval", "expr": "1+1"})
            seen.append(envelope(r)["meta"]["quota"]["remaining_today"])
        assert seen == [9, 8, 7], seen


def test_a_refused_call_does_not_move_the_reported_budget(keyed):
    app, store, key, prefix = keyed(daily_quota=10)
    with TestClient(app) as c:
        for _ in range(2):
            r = rpc(c, key, "tools/call", name="math", arguments={"mode": "eval", "expr": "2+"})
            assert envelope(r)["meta"]["quota"]["remaining_today"] == 10
            assert r.headers["x-ratelimit-remaining-today"] == "10"
        assert used(store, prefix) == 0


def test_protocol_traffic_reports_an_untouched_budget(keyed):
    app, store, key, prefix = keyed(daily_quota=10)
    with TestClient(app) as c:
        assert rpc(c, key, "tools/list").headers["x-ratelimit-remaining-today"] == "10"
