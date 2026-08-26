"""API key store for the hosted server: issue, verify, quota, usage.

Keys look like ``lblz_<40 url-safe chars>``. Only a SHA-256 hash is stored.

Backends (chosen from the DSN):
    sqlite      ``leftbrain-keys.sqlite3`` / ``sqlite:///path``   - single instance
    postgres    ``postgres://user:pw@host/db``                    - Northflank/Neon/Render/Cloud Run
                (needs ``pip install 'leftbrain[postgres]'``)

Env: ``LEFTBRAIN_KEYS_URL`` (any DSN) or ``LEFTBRAIN_KEYS_DB`` (sqlite path);
Northflank's ``DATABASE_URL`` is honoured automatically.

CLI::

    leftbrain-keys create --owner you@example.com [--daily 5000] [--rpm 60] [--note "..."]
    leftbrain-keys list | disable <prefix> | enable <prefix> | revoke <prefix>
    leftbrain-keys usage [<prefix>] [--days 7] | set <prefix> --daily N --rpm N | stats
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

DEFAULT_DB = "leftbrain-keys.sqlite3"
DEFAULT_DAILY = int(os.environ.get("LEFTBRAIN_DEFAULT_DAILY_QUOTA", "5000"))
DEFAULT_RPM = int(os.environ.get("LEFTBRAIN_DEFAULT_RPM", "60"))
KEY_PREFIX = "lblz_"
PREFIX_LEN = len(KEY_PREFIX) + 8  # shown/stored identifier, e.g. lblz_pI5brWOG
SIGNUPS_PER_IP_PER_DAY = int(os.environ.get("LEFTBRAIN_SIGNUPS_PER_IP_PER_DAY", "3"))
MAX_ACTIVE_KEYS_PER_EMAIL = int(os.environ.get("LEFTBRAIN_MAX_KEYS_PER_EMAIL", "3"))

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS keys (
        key_hash    TEXT PRIMARY KEY,
        prefix      TEXT NOT NULL,
        owner       TEXT NOT NULL,
        note        TEXT,
        created_at  TEXT NOT NULL,
        disabled    INTEGER NOT NULL DEFAULT 0,
        daily_quota INTEGER NOT NULL,
        rpm         INTEGER NOT NULL,
        last_used   TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_keys_owner ON keys(owner)",
    "CREATE INDEX IF NOT EXISTS idx_keys_prefix ON keys(prefix)",
    """CREATE TABLE IF NOT EXISTS usage (
        key_hash TEXT NOT NULL,
        day      TEXT NOT NULL,
        count    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (key_hash, day)
    )""",
    """CREATE TABLE IF NOT EXISTS signups (
        ip    TEXT NOT NULL,
        day   TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (ip, day)
    )""",
]


def default_dsn() -> str:
    """Resolve the key-store DSN from the environment."""
    return os.environ.get("LEFTBRAIN_KEYS_URL") or os.environ.get("DATABASE_URL") or os.environ.get("LEFTBRAIN_KEYS_DB") or DEFAULT_DB


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class KeyInfo:
    prefix: str
    owner: str
    note: str | None
    created_at: str
    disabled: bool
    daily_quota: int
    rpm: int
    last_used: str | None
    used_today: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "remaining_today": max(0, self.daily_quota - self.used_today)}


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    status: int = 200
    key: KeyInfo | None = None
    remaining: int | None = None
    retry_after: int | None = None


class _DB:
    """Tiny adapter so the store logic is written once for SQLite and Postgres."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.pg = dsn.startswith(("postgres://", "postgresql://"))
        self._conn: Any = None
        self._connect()

    def _connect(self) -> None:
        if self.pg:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError:  # pragma: no cover
                raise SystemExit("Postgres key store needs psycopg: pip install 'leftbrain[postgres]'") from None
            self._conn = psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)
        else:
            path = self.dsn[len("sqlite:///"):] if self.dsn.startswith("sqlite:///") else self.dsn
            self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.pg else sql

    def run(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        try:
            cur = self._conn.execute(self._sql(sql), params)
        except Exception:
            if not self.pg:
                raise
            self._connect()  # dropped connection: reconnect once
            cur = self._conn.execute(self._sql(sql), params)
        return cur.rowcount if cur.rowcount is not None else 0

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        row = self._conn.execute(self._sql(sql), params).fetchone()
        return dict(row) if row is not None else None

    def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute(self._sql(sql), params).fetchall()]

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        row = self._conn.execute(self._sql(sql), params).fetchone()
        if row is None:
            return None
        return list(dict(row).values())[0] if self.pg else row[0]


class KeyStore:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or default_dsn()
        self.db = _DB(self.dsn)
        self.backend = "postgres" if self.db.pg else "sqlite"
        self._lock = threading.Lock()
        self._rpm_window: dict[str, list[float]] = {}
        for stmt in _SCHEMA:
            self.db.run(stmt)

    # -- issue / manage ------------------------------------------------------

    def create(self, owner: str, *, note: str | None = None, daily_quota: int = DEFAULT_DAILY, rpm: int = DEFAULT_RPM) -> tuple[str, KeyInfo]:
        raw = KEY_PREFIX + secrets.token_urlsafe(30)
        prefix = raw[:PREFIX_LEN]
        now = _now()
        with self._lock:
            self.db.run("INSERT INTO keys(key_hash, prefix, owner, note, created_at, disabled, daily_quota, rpm) VALUES (?,?,?,?,?,0,?,?)", (_hash(raw), prefix, owner.strip().lower(), note, now, daily_quota, rpm))
        return raw, KeyInfo(prefix, owner, note, now, False, daily_quota, rpm, None)

    def _info(self, row: dict[str, Any]) -> KeyInfo:
        used = self.db.scalar("SELECT count FROM usage WHERE key_hash=? AND day=?", (row["key_hash"], _today()))
        return KeyInfo(row["prefix"], row["owner"], row["note"], row["created_at"], bool(row["disabled"]), row["daily_quota"], row["rpm"], row["last_used"], int(used or 0))

    def get_by_prefix(self, prefix: str) -> KeyInfo | None:
        row = self.db.one("SELECT * FROM keys WHERE prefix = ?", (prefix,))
        return self._info(row) if row else None

    def list(self, owner: str | None = None) -> list[KeyInfo]:
        rows = self.db.all("SELECT * FROM keys WHERE owner = ? ORDER BY created_at DESC", (owner,)) if owner else self.db.all("SELECT * FROM keys ORDER BY created_at DESC")
        return [self._info(r) for r in rows]

    def set_disabled(self, prefix: str, disabled: bool) -> bool:
        with self._lock:
            return self.db.run("UPDATE keys SET disabled=? WHERE prefix=?", (1 if disabled else 0, prefix)) > 0

    def revoke(self, prefix: str) -> bool:
        with self._lock:
            row = self.db.one("SELECT key_hash FROM keys WHERE prefix = ?", (prefix,))
            if not row:
                return False
            self.db.run("DELETE FROM usage WHERE key_hash=?", (row["key_hash"],))
            self.db.run("DELETE FROM keys WHERE key_hash=?", (row["key_hash"],))
        return True

    def set_limits(self, prefix: str, *, daily_quota: int | None = None, rpm: int | None = None) -> bool:
        sets: list[str] = []
        args: list[Any] = []
        if daily_quota is not None:
            sets.append("daily_quota=?")
            args.append(daily_quota)
        if rpm is not None:
            sets.append("rpm=?")
            args.append(rpm)
        if not sets:
            return False
        args.append(prefix)
        with self._lock:
            return self.db.run(f"UPDATE keys SET {', '.join(sets)} WHERE prefix=?", tuple(args)) > 0

    # -- verify + meter ------------------------------------------------------

    def verify_and_count(self, raw_key: str) -> Verdict:
        if not raw_key or not raw_key.startswith(KEY_PREFIX):
            return Verdict(False, "invalid key", 401)
        h = _hash(raw_key)
        row = self.db.one("SELECT * FROM keys WHERE key_hash = ?", (h,))
        if not row:
            return Verdict(False, "unknown key", 401)
        if row["disabled"]:
            return Verdict(False, "key disabled", 403)
        info = self._info(row)
        now = time.monotonic()
        with self._lock:
            window = [t for t in self._rpm_window.get(h, []) if now - t < 60]
            if len(window) >= info.rpm:
                self._rpm_window[h] = window
                return Verdict(False, f"rate limit: {info.rpm} requests/minute", 429, info, retry_after=int(60 - (now - window[0])) + 1)
            if info.used_today >= info.daily_quota:
                return Verdict(False, f"daily quota of {info.daily_quota} exhausted; resets at 00:00 UTC", 429, info, remaining=0, retry_after=self._seconds_to_midnight())
            window.append(now)
            self._rpm_window[h] = window
            self.db.run("INSERT INTO usage(key_hash, day, count) VALUES (?,?,1) ON CONFLICT(key_hash, day) DO UPDATE SET count = usage.count + 1", (h, _today()))
            self.db.run("UPDATE keys SET last_used=? WHERE key_hash=?", (_now(), h))
        info.used_today += 1
        return Verdict(True, key=info, remaining=info.daily_quota - info.used_today)

    @staticmethod
    def _seconds_to_midnight() -> int:
        now = datetime.now(UTC)
        return 86400 - (now.hour * 3600 + now.minute * 60 + now.second)

    # -- self-serve signup ---------------------------------------------------

    def signup(self, email: str, ip: str, *, daily_quota: int = DEFAULT_DAILY, rpm: int = DEFAULT_RPM) -> tuple[str | None, str]:
        email = (email or "").strip().lower()
        if "@" not in email or "." not in email.split("@")[-1] or len(email) > 254:
            return None, "a valid email is required"
        day = _today()
        with self._lock:
            n = self.db.scalar("SELECT count FROM signups WHERE ip=? AND day=?", (ip, day))
            if n and int(n) >= SIGNUPS_PER_IP_PER_DAY:
                return None, f"signup limit reached for this address today ({SIGNUPS_PER_IP_PER_DAY}/day)"
            existing = int(self.db.scalar("SELECT COUNT(*) FROM keys WHERE owner=? AND disabled=0", (email,)) or 0)
            if existing >= MAX_ACTIVE_KEYS_PER_EMAIL:
                return None, f"this email already has {MAX_ACTIVE_KEYS_PER_EMAIL} active keys; disable one first"
            self.db.run("INSERT INTO signups(ip, day, count) VALUES (?,?,1) ON CONFLICT(ip, day) DO UPDATE SET count = signups.count + 1", (ip, day))
        raw, _ = self.create(email, note="self-serve signup", daily_quota=daily_quota, rpm=rpm)
        return raw, "ok"

    def usage(self, prefix: str | None = None, days: int = 7) -> list[dict[str, Any]]:
        base = "SELECT k.prefix, k.owner, u.day, u.count FROM usage u JOIN keys k ON k.key_hash=u.key_hash"
        if prefix:
            return self.db.all(base + " WHERE k.prefix=? ORDER BY u.day DESC, u.count DESC LIMIT ?", (prefix, days * 1000))
        return self.db.all(base + " ORDER BY u.day DESC, u.count DESC LIMIT ?", (days * 1000,))

    def stats(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "keys": int(self.db.scalar("SELECT COUNT(*) FROM keys") or 0),
            "active": int(self.db.scalar("SELECT COUNT(*) FROM keys WHERE disabled=0") or 0),
            "requests_today": int(self.db.scalar("SELECT COALESCE(SUM(count),0) FROM usage WHERE day=?", (_today(),)) or 0),
        }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="leftbrain-keys", description="Manage API keys for the hosted leftbrain server")
    ap.add_argument("--db", default=None, help="SQLite path or postgres:// DSN (default: LEFTBRAIN_KEYS_URL / DATABASE_URL / LEFTBRAIN_KEYS_DB)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="issue a new key")
    c.add_argument("--owner", required=True)
    c.add_argument("--note")
    c.add_argument("--daily", type=int, default=DEFAULT_DAILY)
    c.add_argument("--rpm", type=int, default=DEFAULT_RPM)
    ls = sub.add_parser("list")
    ls.add_argument("--owner")
    for name in ("disable", "enable", "revoke"):
        s = sub.add_parser(name)
        s.add_argument("prefix")
    st = sub.add_parser("set", help="change limits")
    st.add_argument("prefix")
    st.add_argument("--daily", type=int)
    st.add_argument("--rpm", type=int)
    u = sub.add_parser("usage")
    u.add_argument("prefix", nargs="?")
    u.add_argument("--days", type=int, default=7)
    sub.add_parser("stats")
    args = ap.parse_args(argv)

    store = KeyStore(args.db)
    if args.cmd == "create":
        raw, info = store.create(args.owner, note=args.note, daily_quota=args.daily, rpm=args.rpm)
        print(json.dumps({"key": raw, **info.to_dict()}, indent=2))
        print("\nStore this key now - it cannot be shown again.")
    elif args.cmd == "list":
        for k in store.list(args.owner):
            print(json.dumps(k.to_dict()))
    elif args.cmd in ("disable", "enable"):
        print("ok" if store.set_disabled(args.prefix, args.cmd == "disable") else "no such key")
    elif args.cmd == "revoke":
        print("revoked" if store.revoke(args.prefix) else "no such key")
    elif args.cmd == "set":
        print("ok" if store.set_limits(args.prefix, daily_quota=args.daily, rpm=args.rpm) else "nothing changed")
    elif args.cmd == "usage":
        for row in store.usage(args.prefix, args.days):
            print(json.dumps(row, default=str))
    elif args.cmd == "stats":
        print(json.dumps(store.stats(), indent=2))


if __name__ == "__main__":
    main()
