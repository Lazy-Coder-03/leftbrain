import json

from starlette.testclient import TestClient

from leftbrain.keys import MAX_ACTIVE_KEYS_PER_EMAIL, KeyStore
from leftbrain.serve import build_app
from leftbrain.web.config import WebConfig


def test_keystore_lifecycle(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"))
    raw, info = store.create("a@b.co", daily_quota=3, rpm=100)
    assert raw.startswith("lblz_") and info.prefix == raw[:13]
    for i in range(3):
        # `verify` checks the budget; `charge` spends it, once per tool call that did work.
        # They were one step, so every HTTP request cost a unit whether or not it was a
        # call - and a client that re-handshakes paid twice for each one (#62).
        v = store.verify(raw)
        assert v.ok and v.remaining == 3 - i
        store.charge(info.prefix)
    v = store.verify(raw)
    assert not v.ok and v.status == 429 and "quota" in v.reason
    assert store.set_disabled(info.prefix, True)
    assert store.verify(raw).status == 403
    assert store.verify("lblz_nope").status == 401
    assert store.set_limits(info.prefix, daily_quota=10) and store.get_by_prefix(info.prefix).daily_quota == 10
    assert store.stats()["keys"] == 1
    assert store.revoke(info.prefix) and store.get_by_prefix(info.prefix) is None


def test_keystore_rpm(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"))
    raw, _ = store.create("a@b.co", daily_quota=100, rpm=2)
    assert store.verify(raw).ok and store.verify(raw).ok
    v = store.verify(raw)
    assert not v.ok and v.status == 429 and v.retry_after


def test_signup_limits(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"))
    assert store.signup("bad", "1.1.1.1")[0] is None
    keys = [store.signup(f"u{i}@x.io", "1.1.1.1")[0] for i in range(4)]
    assert all(keys[:3]) and keys[3] is None  # 3 per IP per day


def test_a_verdict_will_not_take_its_key_positionally():
    """The root cause of the 429-as-500 bug, closed off rather than only tested for.

    `Verdict(False, reason, 429, info)` silently put a KeyInfo in `message`. Everything
    after `status` is keyword-only now, so that call is a TypeError at the call site
    instead of a 500 in front of a user.
    """
    import pytest

    from leftbrain.keys import Verdict

    with pytest.raises(TypeError):
        Verdict(False, "rate limit", 429, "a message")
    ok = Verdict(False, "rate limit", 429, message="a message", remaining=0)
    assert ok.message == "a message" and ok.key is None


def test_a_throttled_key_gets_429_over_http_not_500(tmp_path):
    """The Verdict carrying the key positionally landed it in `message`, which is not
    serialisable, so every rate limit and every exhausted quota answered 500."""
    store = KeyStore(str(tmp_path / "k.sqlite3"))
    quota_key, quota_info = store.create("a@b.co", daily_quota=1, rpm=100)
    rpm_key, _ = store.create("a@b.co", daily_quota=100, rpm=1)
    app = build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"),
                    web_config=WebConfig(client_id=None, client_secret=None, secret=None,
                                         base_url=None, open_signup=False))
    with TestClient(app) as c:
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {quota_key}"}).status_code == 200
        store.charge(quota_info.prefix)  # /keys/me is not a tool call and no longer spends one itself (#62)
        spent = c.get("/keys/me", headers={"Authorization": f"Bearer {quota_key}"})
        assert spent.status_code == 429
        assert "quota" in spent.json()["error"] and spent.headers["retry-after"]

        assert c.get("/keys/me", headers={"Authorization": f"Bearer {rpm_key}"}).status_code == 200
        limited = c.get("/keys/me", headers={"Authorization": f"Bearer {rpm_key}"})
        assert limited.status_code == 429
        assert "rate limit" in limited.json()["error"] and limited.headers["retry-after"]


def test_the_key_cap_is_five_and_still_reads_the_environment(monkeypatch):
    """Five because a connector now takes a slot; still a number an operator can change."""
    import importlib

    from leftbrain import keys as keys_mod

    assert keys_mod.MAX_ACTIVE_KEYS_PER_EMAIL == 5
    monkeypatch.setenv("LEFTBRAIN_MAX_KEYS_PER_EMAIL", "9")
    try:
        assert importlib.reload(keys_mod).MAX_ACTIVE_KEYS_PER_EMAIL == 9
    finally:
        monkeypatch.delenv("LEFTBRAIN_MAX_KEYS_PER_EMAIL")
        importlib.reload(keys_mod)
    assert keys_mod.MAX_ACTIVE_KEYS_PER_EMAIL == 5


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
        from leftbrain.keys import DEFAULT_DAILY, DEFAULT_RPM

        assert r.json()["daily_quota"] == DEFAULT_DAILY and r.json()["rpm"] == DEFAULT_RPM
        me = c.get("/keys/me", headers={"Authorization": f"Bearer {key}"})
        assert me.status_code == 200 and me.json()["result"]["owner"] == "dev@example.com"
        assert me.json()["result"]["expires_at"]  # self-serve keys get the default lifetime, not forever
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
    made = [store.create_for_owner("Me@Example.com", f"key {i}") for i in range(MAX_ACTIVE_KEYS_PER_EMAIL)]
    assert all(raw and raw.startswith("lblz_") for raw, _ in made)
    assert made[0][1].owner == "me@example.com" and made[0][1].note == "key 0"
    raw, reason = store.create_for_owner("me@example.com", None)
    assert raw is None and f"{MAX_ACTIVE_KEYS_PER_EMAIL} active" in reason
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
        # `charge` is the only statement in the store that Postgres and SQLite could spell
        # differently (INSERT ... SELECT ... ON CONFLICT), so this is where it is checked.
        assert store.verify(raw).ok
        store.charge(info.prefix)
        assert store.verify(raw).ok
        store.charge(info.prefix)
        assert store.verify(raw).status == 429
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


def test_legacy_keys_are_flagged_and_do_not_hold_a_slot(tmp_path):
    db = str(tmp_path / "k.sqlite3")
    plain = KeyStore(db)
    _, old = plain.create("a@b.co", note="self-serve signup")  # hash only, pre-reveal
    assert plain.get_by_prefix(old.prefix).legacy is False  # without a secret nothing is legacy

    store = KeyStore(db, secret="s" * 20)
    info = store.get_by_prefix(old.prefix)
    assert info.legacy and not info.revealable and info.usable and not info.holds_slot
    assert "legacy" not in info.to_dict()
    assert store._active_count("a@b.co") == 0  # the legacy key does not block the cap
    made = [store.create_for_owner("a@b.co", f"k{i}") for i in range(MAX_ACTIVE_KEYS_PER_EMAIL)]
    assert all(raw for raw, _ in made) and all(not i.legacy and i.holds_slot for _, i in made)
    raw, reason = store.create_for_owner("a@b.co", "one too many")
    assert raw is None and "active keys" in reason
    # revoking the legacy row frees nothing, since it held nothing
    assert store.set_disabled(old.prefix, True)
    assert store.create_for_owner("a@b.co", "still capped")[0] is None


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
    assert rotated.verify(raw2).ok  # ...but still valid for authentication


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


def _expire(store, prefix, iso):
    """Backdate a key's expiry directly; the public API only ever sets one in the future."""
    store.db.run("UPDATE keys SET expires_at=? WHERE prefix=?", (iso, prefix))


def test_create_with_a_lifetime_sets_expires_at(tmp_path):
    from datetime import UTC, datetime, timedelta

    store = KeyStore(str(tmp_path / "k.sqlite3"))
    raw, info = store.create("a@b.co", lifetime_days=30)
    assert info.expires_at and not info.expired and info.days_left == 30  # rounds up: a fresh 30-day key has 30 days
    due = datetime.fromisoformat(info.expires_at)
    assert abs((due - datetime.now(UTC)) - timedelta(days=30)) < timedelta(minutes=1)
    assert store.get_by_prefix(info.prefix).expires_at == info.expires_at
    d = info.to_dict()
    assert d["expires_at"] == info.expires_at and d["expired"] is False
    _, forever = store.create("a@b.co")  # the default is no expiry
    assert forever.expires_at is None and forever.days_left is None and forever.to_dict()["expired"] is False


def test_expired_key_is_rejected_with_403_and_a_dated_message(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"))
    raw, info = store.create("a@b.co", lifetime_days=1)
    assert store.verify(raw).ok
    _expire(store, info.prefix, "2026-01-02T03:04:05.000000+00:00")
    v = store.verify(raw)
    assert not v.ok and v.status == 403 and v.reason == "expired"
    assert "expired on 2026-01-02" in v.message and "/dashboard" in v.message
    assert store.get_by_prefix(info.prefix).expired


def test_expired_keys_do_not_count_towards_the_active_cap(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"))
    made = [store.create_for_owner("me@example.com", f"k{i}", lifetime_days=30) for i in range(MAX_ACTIVE_KEYS_PER_EMAIL)]
    assert store.create_for_owner("me@example.com", "full")[0] is None
    _expire(store, made[0][1].prefix, "2026-01-01T00:00:00.000000+00:00")
    raw, info = store.create_for_owner("me@example.com", "replacement", lifetime_days=None)
    assert raw and info.expires_at is None
    assert store.stats()["active"] == MAX_ACTIVE_KEYS_PER_EMAIL


def test_expired_key_is_not_revealed(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret="s" * 20)
    raw, info = store.create("a@b.co", lifetime_days=7)
    assert store.reveal("a@b.co", info.prefix) == raw
    _expire(store, info.prefix, "2026-01-01T00:00:00.000000+00:00")
    assert store.reveal("a@b.co", info.prefix) is None


def test_set_expiry_extends_or_removes(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"))
    raw, info = store.create("a@b.co", lifetime_days=1)
    _expire(store, info.prefix, "2026-01-01T00:00:00.000000+00:00")
    assert store.verify(raw).status == 403
    assert store.set_expiry(info.prefix, 90)
    assert store.verify(raw).ok and store.get_by_prefix(info.prefix).days_left == 90
    assert store.set_expiry(info.prefix, None)
    assert store.get_by_prefix(info.prefix).expires_at is None
    assert not store.set_expiry("lblz_nosuch1", 30)


def test_parse_lifetime():
    from leftbrain.keys import parse_lifetime

    assert parse_lifetime("90d") == 90 and parse_lifetime("30") == 30 and parse_lifetime("never") is None
    import pytest

    for bad in ("", "0d", "-3d", "soon", "1.5d"):
        with pytest.raises(ValueError):
            parse_lifetime(bad)


def test_migration_adds_expires_at_to_an_existing_database(tmp_path):
    import sqlite3

    path = str(tmp_path / "old.sqlite3")
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE keys (key_hash TEXT PRIMARY KEY, prefix TEXT NOT NULL, owner TEXT NOT NULL,"
        " note TEXT, created_at TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0,"
        " daily_quota INTEGER NOT NULL, rpm INTEGER NOT NULL, last_used TEXT, secret_enc TEXT)"
    )
    con.execute("INSERT INTO keys VALUES ('h', 'lblz_oldkey01', 'a@b.co', NULL, '2026-01-01T00:00:00+00:00', 0, 5, 5, NULL, NULL)")
    con.commit()
    con.close()

    store = KeyStore(path)
    old = store.get_by_prefix("lblz_oldkey01")
    assert old.expires_at is None and not old.expired  # pre-expiry keys never expire
    KeyStore(path)  # guarded: re-running is a no-op


def test_set_limits_all_and_cli_set_all(tmp_path, capsys):
    import json as _json

    import pytest

    from leftbrain.keys import main

    db = str(tmp_path / "k.sqlite3")
    store = KeyStore(db)
    _, old1 = store.create("a@b.co", daily_quota=5000)
    _, old2 = store.create("c@d.co", daily_quota=5000, rpm=30)
    _, partner = store.create("p@q.co", daily_quota=50000, rpm=300)
    assert store.set_limits_all() == 0  # nothing asked, nothing touched
    assert store.set_limits_all(daily_quota=1000, from_daily=5000) == 2  # the migration: only the old default moves
    assert store.get_by_prefix(partner.prefix).daily_quota == 50000
    assert {store.get_by_prefix(k.prefix).daily_quota for k in (old1, old2)} == {1000}
    assert store.get_by_prefix(old2.prefix).rpm == 30  # rpm untouched when not asked
    assert store.set_limits_all(rpm=90) == 3  # every key, not just some
    assert {k.rpm for k in store.list()} == {90}

    main(["--db", db, "set", "--all", "--daily", "1000", "--from-daily", "50000"])
    assert capsys.readouterr().out.strip() == "updated 1 key"
    main(["--db", db, "set", "--all", "--daily", "1000"])
    assert capsys.readouterr().out.strip() == "updated 3 keys"
    main(["--db", db, "list"])
    assert {_json.loads(line)["daily_quota"] for line in capsys.readouterr().out.splitlines()} == {1000}
    main(["--db", db, "set", "--all", "--daily", "1000", "--from-daily", "5000"])
    assert capsys.readouterr().out.strip() == "updated 0 keys"
    # one key or all of them, never neither and never both; expiry stays per-key
    for argv in (
        ["set", "--daily", "1"],  # neither a prefix nor --all
        ["set", "--all", old1.prefix, "--daily", "1"],  # both
        ["set", "--all", "--expires", "never", "--daily", "1"],  # expiry is per key
        ["set", "--all"],  # nothing to change
        ["set", old1.prefix, "--from-daily", "5", "--daily", "1"],  # the filter is --all only
    ):
        with pytest.raises(SystemExit):
            main(["--db", db, *argv])
    main(["--db", db, "set", old1.prefix, "--daily", "7"])  # the single-key form is unchanged
    assert capsys.readouterr().out.strip() == "ok" and store.get_by_prefix(old1.prefix).daily_quota == 7


def test_cli_create_expires_and_set(tmp_path, capsys):
    import json as _json

    from leftbrain.keys import main

    db = str(tmp_path / "k.sqlite3")
    main(["--db", db, "create", "--owner", "a@b.co", "--expires", "30d"])
    out = capsys.readouterr().out
    made = _json.loads(out[: out.index("\n\n")])
    assert made["expires_at"] and made["expired"] is False
    main(["--db", db, "create", "--owner", "a@b.co", "--expires", "never"])
    err = capsys.readouterr()
    assert _json.loads(err.out[: err.out.index("\n\n")])["expires_at"] is None
    assert "never expire" in (err.out + err.err).lower()
    main(["--db", db, "set", made["prefix"], "--expires", "never"])
    assert capsys.readouterr().out.strip() == "ok"
    main(["--db", db, "list"])
    rows = [_json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert all(r["expires_at"] is None for r in rows)
