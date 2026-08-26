import json

from starlette.testclient import TestClient

from leftbrain.keys import KeyStore
from leftbrain.serve import build_app
from leftbrain.web.config import WebConfig


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
    app = build_app(
        include_external=False,
        keys_db=str(tmp_path / "k.sqlite3"),
        web_config=WebConfig(None, None, "s" * 20, None, True),
    )
    with TestClient(app) as c:
        assert c.get("/healthz").json()["ok"]
        assert c.get("/").json()["auth"] == "keys"
        assert c.post("/mcp", json={}).status_code == 401
        r = c.post("/keys/signup", json={"email": "dev@example.com"})
        assert r.status_code == 201
        key = r.json()["key"]
        assert key.startswith("lblz_") and r.json()["prefix"] == key[:13]
        assert r.json()["daily_quota"] == 5000 and r.json()["rpm"] == 60
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


def test_create_for_owner_cap_and_owns(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"))
    made = [store.create_for_owner("Me@Example.com", f"key {i}") for i in range(3)]
    assert all(raw and raw.startswith("lblz_") for raw, _ in made)
    assert made[0][1].owner == "me@example.com" and made[0][1].note == "key 0"
    raw, reason = store.create_for_owner("me@example.com", None)
    assert raw is None and "3 active" in reason
    prefix = made[0][1].prefix
    assert store.owns("me@example.com", prefix) and not store.owns("other@example.com", prefix)
    assert store.set_disabled(prefix, True)
    raw, info = store.create_for_owner("me@example.com", "")  # slot freed, empty name -> None
    assert raw and info.note is None


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


def test_reveal_roundtrip_and_ownership(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret="s" * 20)
    assert store.can_reveal
    raw, info = store.create("Me@Example.com", note="laptop")
    assert info.revealable and "revealable" not in info.to_dict()
    assert store.reveal("me@example.com", info.prefix) == raw
    assert store.reveal("  ME@Example.com ", info.prefix) == raw  # owners are normalised
    assert store.reveal("other@example.com", info.prefix) is None
    assert store.reveal("me@example.com", "lblz_nosuch1") is None
    assert store.set_disabled(info.prefix, True)
    assert store.reveal("me@example.com", info.prefix) is None  # revoked keys never come back


def test_reveal_needs_a_secret_and_does_not_survive_rotation(tmp_path):
    db = str(tmp_path / "k.sqlite3")
    plain = KeyStore(db)
    assert not plain.can_reveal
    _, info = plain.create("a@b.co")
    assert not info.revealable and plain.reveal("a@b.co", info.prefix) is None

    later = KeyStore(db, secret="s" * 20)  # encryption switched on after that key was issued
    assert later.reveal("a@b.co", info.prefix) is None
    assert later.get_by_prefix(info.prefix).revealable is False
    raw2, info2 = later.create("a@b.co")
    assert later.reveal("a@b.co", info2.prefix) == raw2

    rotated = KeyStore(db, secret="a-completely-different-secret")
    assert rotated.reveal("a@b.co", info2.prefix) is None  # unrevealable...
    assert rotated.verify_and_count(raw2).ok  # ...but still valid for authentication


def test_migration_adds_secret_enc_to_an_existing_database(tmp_path):
    import sqlite3

    path = str(tmp_path / "old.sqlite3")
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE keys (key_hash TEXT PRIMARY KEY, prefix TEXT NOT NULL, owner TEXT NOT NULL,"
        " note TEXT, created_at TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0,"
        " daily_quota INTEGER NOT NULL, rpm INTEGER NOT NULL, last_used TEXT)"
    )
    con.commit()
    con.close()

    store = KeyStore(path, secret="s" * 20)
    assert "secret_enc" in {c["name"] for c in store.db.all("PRAGMA table_info(keys)")}
    raw, info = store.create("a@b.co")
    assert store.reveal("a@b.co", info.prefix) == raw
    KeyStore(path, secret="s" * 20)  # the migration is guarded: opening it again is a no-op
