"""API key store for the hosted server: issue, verify, quota, usage.

Keys look like ``lblz_<40 url-safe chars>``. A SHA-256 hash is what authenticates a
call; when ``LEFTBRAIN_SECRET`` is set the store also keeps a Fernet-encrypted copy
of the key so its owner can be shown it again (dashboard "Show", docs examples).
Rotating ``LEFTBRAIN_SECRET`` leaves older keys valid but no longer revealable.

Backends (chosen from the DSN):
    sqlite      ``leftbrain-keys.sqlite3`` / ``sqlite:///path``   - single instance
    postgres    ``postgres://user:pw@host/db``                    - Northflank/Neon/Render/Cloud Run
                (needs ``pip install 'leftbrain[postgres]'``)

Env: ``LEFTBRAIN_KEYS_URL`` (any DSN) or ``LEFTBRAIN_KEYS_DB`` (sqlite path);
Northflank's ``DATABASE_URL`` is honoured automatically.

CLI::

    leftbrain-keys create --owner you@example.com [--daily 1000] [--rpm 60] [--expires 90d|never] [--note "..."]
    leftbrain-keys list | disable <prefix> | enable <prefix> | revoke <prefix>
    leftbrain-keys usage [<prefix>] [--days 7] | set <prefix> --daily N --rpm N --expires 90d|never | stats

Keys may carry an expiry (``expires_at``, UTC ISO). An expired key is refused with 403
and no longer counts towards the owner's active-key cap; ``never`` is allowed but is a
liability if the key leaks, so the dashboard and CLI warn when it is chosen.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import secrets
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

DEFAULT_DB = "leftbrain-keys.sqlite3"
DEFAULT_DAILY = int(os.environ.get("LEFTBRAIN_DEFAULT_DAILY_QUOTA", "1000"))
DEFAULT_RPM = int(os.environ.get("LEFTBRAIN_DEFAULT_RPM", "60"))
KEY_PREFIX = "lblz_"
PREFIX_LEN = len(KEY_PREFIX) + 8  # shown/stored identifier, e.g. lblz_pI5brWOG
SIGNUPS_PER_IP_PER_DAY = int(os.environ.get("LEFTBRAIN_SIGNUPS_PER_IP_PER_DAY", "3"))
MAX_ACTIVE_KEYS_PER_EMAIL = int(os.environ.get("LEFTBRAIN_MAX_KEYS_PER_EMAIL", "3"))
LIFETIME_CHOICES = (30, 90, 365)  # days offered at creation; None means never
DEFAULT_LIFETIME_DAYS = 90
EXPIRY_WARNING_DAYS = 7  # "expires soon" once this close
NEVER_EXPIRES_WARNING = "Keys that never expire are a liability if leaked; prefer a lifetime and rotate."
# ISO strings in one fixed shape compare correctly as text, in SQLite and Postgres alike
_ACTIVE = "disabled=0 AND (expires_at IS NULL OR expires_at > ?)"

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
        last_used   TEXT,
        secret_enc  TEXT,
        expires_at  TEXT
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


def _fernet(secret: str | None) -> Any:
    """A Fernet built from ``LEFTBRAIN_SECRET``, or None when reveal is unavailable."""
    if not secret:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:  # pragma: no cover - reveal degrades, auth is unaffected
        return None
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _now() -> str:
    # microseconds, so two keys made in the same second still sort newest-first
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _expiry(lifetime_days: int | None) -> str | None:
    if lifetime_days is None:
        return None
    return (datetime.now(UTC) + timedelta(days=lifetime_days)).isoformat(timespec="microseconds")


def parse_lifetime(text: str) -> int | None:
    """``"90d"`` / ``"90"`` -> 90; ``"never"`` -> None. Anything else is a ValueError."""
    t = (text or "").strip().lower()
    if t == "never":
        return None
    digits = t[:-1] if t.endswith("d") else t
    if not digits.isdigit() or int(digits) < 1:
        raise ValueError("lifetime must be a whole number of days like 90d, or never")
    return int(digits)


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
    revealable: bool = False  # a decryptable copy of the key exists
    expires_at: str | None = None  # UTC ISO; None never expires

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= _now()

    @property
    def days_left(self) -> int | None:
        """Days until expiry, rounded up (a fresh 30-day key has 30; 0 once it has passed); None when it never expires."""
        if self.expires_at is None:
            return None
        left = datetime.fromisoformat(self.expires_at) - datetime.now(UTC)
        return max(0, math.ceil(left.total_seconds() / 86400))

    @property
    def usable(self) -> bool:
        return not self.disabled and not self.expired

    @property
    def expiring_soon(self) -> bool:
        return self.days_left is not None and self.days_left <= EXPIRY_WARNING_DAYS and not self.expired

    def to_dict(self) -> dict[str, Any]:
        fields = {k: v for k, v in self.__dict__.items() if k != "revealable"}
        return {**fields, "expired": self.expired, "remaining_today": max(0, self.daily_quota - self.used_today)}


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    status: int = 200
    message: str = ""  # a fuller explanation for the caller, when the reason alone is terse
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
    def __init__(self, dsn: str | None = None, *, secret: str | None = None) -> None:
        self.dsn = dsn or default_dsn()
        self.db = _DB(self.dsn)
        self.backend = "postgres" if self.db.pg else "sqlite"
        self._lock = threading.Lock()
        self._rpm_window: dict[str, list[float]] = {}
        self._crypto = _fernet(secret)
        for stmt in _SCHEMA:
            self.db.run(stmt)
        self._migrate()

    @property
    def can_reveal(self) -> bool:
        return self._crypto is not None

    def _migrate(self) -> None:
        """Add columns that older databases predate. Guarded, so it is safe to re-run."""
        added = ("secret_enc", "expires_at")
        if self.db.pg:
            for col in added:
                self.db.run(f"ALTER TABLE keys ADD COLUMN IF NOT EXISTS {col} TEXT")
            return
        have = {c["name"] for c in self.db.all("PRAGMA table_info(keys)")}
        for col in added:
            if col not in have:
                self.db.run(f"ALTER TABLE keys ADD COLUMN {col} TEXT")

    def _encrypt(self, raw: str) -> str | None:
        return self._crypto.encrypt(raw.encode()).decode() if self._crypto else None

    # -- issue / manage ------------------------------------------------------

    def create(self, owner: str, *, note: str | None = None, daily_quota: int = DEFAULT_DAILY, rpm: int = DEFAULT_RPM, lifetime_days: int | None = None) -> tuple[str, KeyInfo]:
        raw = KEY_PREFIX + secrets.token_urlsafe(30)
        prefix = raw[:PREFIX_LEN]
        now = _now()
        enc = self._encrypt(raw)
        expires_at = _expiry(lifetime_days)
        with self._lock:
            self.db.run("INSERT INTO keys(key_hash, prefix, owner, note, created_at, disabled, daily_quota, rpm, secret_enc, expires_at) VALUES (?,?,?,?,?,0,?,?,?,?)", (_hash(raw), prefix, owner.strip().lower(), note, now, daily_quota, rpm, enc, expires_at))
        return raw, KeyInfo(prefix, owner, note, now, False, daily_quota, rpm, None, revealable=enc is not None, expires_at=expires_at)

    def _info(self, row: dict[str, Any]) -> KeyInfo:
        used = self.db.scalar("SELECT count FROM usage WHERE key_hash=? AND day=?", (row["key_hash"], _today()))
        return KeyInfo(row["prefix"], row["owner"], row["note"], row["created_at"], bool(row["disabled"]), row["daily_quota"], row["rpm"], row["last_used"], int(used or 0), revealable=bool(row.get("secret_enc")) and self.can_reveal, expires_at=row.get("expires_at"))

    def _active_count(self, owner: str) -> int:
        return int(self.db.scalar(f"SELECT COUNT(*) FROM keys WHERE owner=? AND {_ACTIVE}", (owner, _now())) or 0)

    def get_by_prefix(self, prefix: str) -> KeyInfo | None:
        row = self.db.one("SELECT * FROM keys WHERE prefix = ?", (prefix,))
        return self._info(row) if row else None

    def list(self, owner: str | None = None) -> list[KeyInfo]:
        owner = (owner or "").strip().lower() or None  # owners are stored normalised
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

    def set_expiry(self, prefix: str, lifetime_days: int | None) -> bool:
        """Expire the key ``lifetime_days`` from now, or never (None). Works on an expired key too."""
        with self._lock:
            return self.db.run("UPDATE keys SET expires_at=? WHERE prefix=?", (_expiry(lifetime_days), prefix)) > 0

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
        if info.expired:
            return Verdict(False, "expired", 403, message=f"key expired on {info.expires_at[:10]}; create a new one at /dashboard")
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
            existing = self._active_count(email)
            if existing >= MAX_ACTIVE_KEYS_PER_EMAIL:
                return None, f"this email already has {MAX_ACTIVE_KEYS_PER_EMAIL} active keys; disable one first"
            self.db.run("INSERT INTO signups(ip, day, count) VALUES (?,?,1) ON CONFLICT(ip, day) DO UPDATE SET count = signups.count + 1", (ip, day))
        raw, _ = self.create(email, note="self-serve signup", daily_quota=daily_quota, rpm=rpm, lifetime_days=DEFAULT_LIFETIME_DAYS)
        return raw, "ok"

    def create_for_owner(self, email: str, name: str | None, *, daily_quota: int = DEFAULT_DAILY, rpm: int = DEFAULT_RPM, lifetime_days: int | None = DEFAULT_LIFETIME_DAYS) -> tuple[str | None, Any]:
        """Dashboard key creation: verified owner, enforce the active-key cap, no IP throttle."""
        email = (email or "").strip().lower()
        with self._lock:
            if self._active_count(email) >= MAX_ACTIVE_KEYS_PER_EMAIL:
                return None, f"you already have {MAX_ACTIVE_KEYS_PER_EMAIL} active keys; revoke one first"
        note = (name or "").strip()[:40] or None
        return self.create(email, note=note, daily_quota=daily_quota, rpm=rpm, lifetime_days=lifetime_days)

    def owns(self, email: str, prefix: str) -> bool:
        row = self.db.one("SELECT owner FROM keys WHERE prefix = ?", (prefix,))
        return bool(row) and row["owner"] == (email or "").strip().lower()

    def reveal(self, owner: str, prefix: str) -> str | None:
        """The full key back, for its owner only.

        None when the caller does not own it, the key is disabled or expired, the store
        has no secret configured, the key predates encryption, or ``LEFTBRAIN_SECRET``
        has been rotated since the key was issued.
        """
        if self._crypto is None:
            return None
        row = self.db.one(f"SELECT owner, secret_enc FROM keys WHERE prefix = ? AND {_ACTIVE}", (prefix, _now()))
        if not row or not row["secret_enc"]:
            return None
        if row["owner"] != (owner or "").strip().lower():
            return None
        try:
            return self._crypto.decrypt(str(row["secret_enc"]).encode()).decode()
        except Exception:
            return None  # rotated secret or corrupt ciphertext: the key still authenticates

    def usage(self, prefix: str | None = None, days: int = 7) -> list[dict[str, Any]]:
        base = "SELECT k.prefix, k.owner, u.day, u.count FROM usage u JOIN keys k ON k.key_hash=u.key_hash"
        if prefix:
            return self.db.all(base + " WHERE k.prefix=? ORDER BY u.day DESC, u.count DESC LIMIT ?", (prefix, days * 1000))
        return self.db.all(base + " ORDER BY u.day DESC, u.count DESC LIMIT ?", (days * 1000,))

    def stats(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "keys": int(self.db.scalar("SELECT COUNT(*) FROM keys") or 0),
            "active": int(self.db.scalar(f"SELECT COUNT(*) FROM keys WHERE {_ACTIVE}", (_now(),)) or 0),
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
    c.add_argument("--expires", type=parse_lifetime, default=365, metavar="90d|never", help="lifetime in days, or never (default 365d)")
    ls = sub.add_parser("list")
    ls.add_argument("--owner")
    for name in ("disable", "enable", "revoke"):
        s = sub.add_parser(name)
        s.add_argument("prefix")
    st = sub.add_parser("set", help="change limits")
    st.add_argument("prefix")
    st.add_argument("--daily", type=int)
    st.add_argument("--rpm", type=int)
    st.add_argument("--expires", type=parse_lifetime, default=argparse.SUPPRESS, metavar="90d|never", help="new expiry counted from now, or never")
    u = sub.add_parser("usage")
    u.add_argument("prefix", nargs="?")
    u.add_argument("--days", type=int, default=7)
    sub.add_parser("stats")
    args = ap.parse_args(argv)

    store = KeyStore(args.db, secret=os.environ.get("LEFTBRAIN_SECRET"))
    if args.cmd == "create":
        raw, info = store.create(args.owner, note=args.note, daily_quota=args.daily, rpm=args.rpm, lifetime_days=args.expires)
        print(json.dumps({"key": raw, **info.to_dict()}, indent=2))
        print("\nStore this key now." if store.can_reveal else "\nStore this key now - it cannot be shown again.")
        if args.expires is None:
            print("Warning: " + NEVER_EXPIRES_WARNING, file=sys.stderr)
    elif args.cmd == "list":
        for k in store.list(args.owner):
            print(json.dumps(k.to_dict()))
    elif args.cmd in ("disable", "enable"):
        print("ok" if store.set_disabled(args.prefix, args.cmd == "disable") else "no such key")
    elif args.cmd == "revoke":
        print("revoked" if store.revoke(args.prefix) else "no such key")
    elif args.cmd == "set":
        changed = store.set_limits(args.prefix, daily_quota=args.daily, rpm=args.rpm)
        if hasattr(args, "expires"):
            changed = store.set_expiry(args.prefix, args.expires) or changed
            if args.expires is None:
                print("Warning: " + NEVER_EXPIRES_WARNING, file=sys.stderr)
        print("ok" if changed else "nothing changed")
    elif args.cmd == "usage":
        for row in store.usage(args.prefix, args.days):
            print(json.dumps(row, default=str))
    elif args.cmd == "stats":
        print(json.dumps(store.stats(), indent=2))


if __name__ == "__main__":
    main()
