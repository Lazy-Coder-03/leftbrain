import json

from starlette.testclient import TestClient

from leftbrain.keys import KeyStore
from leftbrain.serve import build_app


def test_keystore_lifecycle(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"))
    raw, info = store.create("a@b.co", daily_quota=3, rpm=100)
    assert raw.startswith("lblz_") and info.prefix == raw[:13]
    for i in range(3):
        v = store.verify_and_count(raw)
        assert v.ok and v.remaining == 2 - i
    v = store.verify_and_count(raw)
    assert not v.ok and v.status == 429 and "quota" in v.reason
    assert store.set_disabled(info.prefix, True)
    assert store.verify_and_count(raw).status == 403
    assert store.verify_and_count("lblz_nope").status == 401
    assert store.set_limits(info.prefix, daily_quota=10) and store.get_by_prefix(info.prefix).daily_quota == 10
    assert store.stats()["keys"] == 1
    assert store.revoke(info.prefix) and store.get_by_prefix(info.prefix) is None


def test_keystore_rpm(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"))
    raw, _ = store.create("a@b.co", daily_quota=100, rpm=2)
    assert store.verify_and_count(raw).ok and store.verify_and_count(raw).ok
    v = store.verify_and_count(raw)
    assert not v.ok and v.status == 429 and v.retry_after


def test_signup_limits(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"))
    assert store.signup("bad", "1.1.1.1")[0] is None
    keys = [store.signup(f"u{i}@x.io", "1.1.1.1")[0] for i in range(4)]
    assert all(keys[:3]) and keys[3] is None  # 3 per IP per day


def test_http_server_with_keys(tmp_path):
    app = build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"))
    with TestClient(app) as c:
        assert c.get("/healthz").json()["ok"]
        assert c.get("/").json()["auth"] == "keys"
        assert c.post("/mcp", json={}).status_code == 401
        r = c.post("/keys/signup", json={"email": "dev@example.com"})
        assert r.status_code == 201
        key = r.json()["key"]
        me = c.get("/keys/me", headers={"Authorization": f"Bearer {key}"})
        assert me.status_code == 200 and me.json()["result"]["owner"] == "dev@example.com"
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
        r = c.post("/mcp", json=init, headers={"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream"})
        assert r.status_code == 200 and "x-ratelimit-remaining-today" in r.headers
        assert "leftbrain" in r.text
        bad = c.post("/mcp", json=init, headers={"Authorization": "Bearer lblz_wrong", "Accept": "application/json, text/event-stream"})
        assert bad.status_code == 401 and json.loads(bad.text)["error"] == "unknown key"


def test_http_server_static_key():
    app = build_app(include_external=False, api_key="s3cret")
    with TestClient(app) as c:
        assert c.get("/keys/me").status_code == 401
        assert c.get("/keys/me", headers={"X-API-Key": "s3cret"}).json()["result"]["quota"] == "unlimited"
        assert c.post("/keys/signup", json={"email": "a@b.co"}).status_code == 404


def test_keystore_postgres_if_configured():
    import os

    import pytest

    url = os.environ.get("LEFTBRAIN_TEST_PG_URL")
    if not url:
        pytest.skip("set LEFTBRAIN_TEST_PG_URL=postgres://... to run")
    store = KeyStore(url)
    assert store.backend == "postgres"
    raw, info = store.create("pg@b.co", daily_quota=2, rpm=100)
    try:
        assert store.verify_and_count(raw).ok and store.verify_and_count(raw).ok
        assert store.verify_and_count(raw).status == 429
        assert store.get_by_prefix(info.prefix).used_today == 2
    finally:
        store.revoke(info.prefix)
