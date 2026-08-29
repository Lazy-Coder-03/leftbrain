"""Persistence for the OAuth authorization server, alongside the key store.

Every issued credential is bound to an ordinary key row, so revoking the key on the
dashboard revokes the tokens with it and no second revocation path exists.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.shared.auth import OAuthClientInformationFull

from ..keys import _hash, _now


class OAuthStore:
    """OAuth records living in the key store's database, sharing its connection and lock."""

    def __init__(self, keys: Any) -> None:
        self.keys = keys
        self.db = keys.db

    # -- clients -------------------------------------------------------------

    def save_client(self, client: OAuthClientInformationFull) -> None:
        """Store a registration, replacing any earlier one under the same id.

        The whole record is kept as JSON and re-parsed on load, so a field the SDK adds
        later survives a round trip without a migration.
        """
        uris = json.dumps([str(u) for u in (client.redirect_uris or [])])
        secret_hash = _hash(client.client_secret) if client.client_secret else None
        with self.keys._lock:
            self.db.run(
                "INSERT INTO oauth_clients(client_id, secret_hash, name, redirect_uris, metadata, created_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(client_id) DO UPDATE SET "
                "secret_hash=excluded.secret_hash, name=excluded.name, "
                "redirect_uris=excluded.redirect_uris, metadata=excluded.metadata",
                (client.client_id, secret_hash, client.client_name, uris, client.model_dump_json(), _now()),
            )

    def load_client(self, client_id: str) -> OAuthClientInformationFull | None:
        row = self.db.one("SELECT metadata FROM oauth_clients WHERE client_id = ?", (client_id,))
        if not row:
            return None
        return OAuthClientInformationFull.model_validate_json(row["metadata"])

    # -- consent -------------------------------------------------------------

    @staticmethod
    def _owner(email: str) -> str:
        """Owners are stored lowered and stripped by `keys`; consent must agree or never match."""
        return (email or "").strip().lower()

    def record_consent(self, owner: str, client_id: str, key_prefix: str) -> None:
        """Remember that this owner approved this client, and which key it was given.

        Checked before anything is forwarded to GitHub: the per-client consent registry is
        the confused-deputy mitigation. It is also what makes a reconnection reuse the key
        it already minted instead of eating another of the owner's slots.
        """
        with self.keys._lock:
            self.db.run(
                "INSERT INTO oauth_consents(owner, client_id, key_prefix, granted_at) VALUES (?,?,?,?) "
                "ON CONFLICT(owner, client_id) DO UPDATE SET key_prefix=excluded.key_prefix, "
                "granted_at=excluded.granted_at",
                (self._owner(owner), client_id, key_prefix, _now()),
            )

    def consent_for(self, owner: str, client_id: str) -> str | None:
        row = self.db.one(
            "SELECT key_prefix FROM oauth_consents WHERE owner=? AND client_id=?",
            (self._owner(owner), client_id),
        )
        return row["key_prefix"] if row else None

    # -- authorization codes -------------------------------------------------

    @staticmethod
    def _expiry(seconds: int) -> str:
        """One fixed ISO shape, so expiry compares correctly as text in SQLite and Postgres."""
        return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(timespec="seconds")

    def save_code(
        self, code: str, *, client_id: str, key_hash: str, owner: str, scopes: list[str],
        code_challenge: str, redirect_uri: str, redirect_uri_provided: bool,
        resource: str | None, ttl: int = 60,
    ) -> None:
        with self.keys._lock:
            self.db.run(
                "INSERT INTO oauth_codes(code_hash, client_id, key_hash, owner, scopes, code_challenge, "
                "redirect_uri, redirect_uri_provided, resource, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (_hash(code), client_id, key_hash, owner, json.dumps(scopes), code_challenge,
                 redirect_uri, 1 if redirect_uri_provided else 0, resource, self._expiry(ttl)),
            )

    def take_code(self, code: str) -> dict[str, Any] | None:
        """Read a code and delete it under the same lock: single use, replay included.

        An expired code is consumed too, so a replay cannot tell "expired" from "already
        spent" by whether a second attempt behaves differently.
        """
        h = _hash(code)
        with self.keys._lock:
            row = self.db.one("SELECT * FROM oauth_codes WHERE code_hash = ?", (h,))
            self.db.run("DELETE FROM oauth_codes WHERE code_hash = ?", (h,))
        if not row or row["expires_at"] <= _now():
            return None
        return {
            **row,
            "scopes": json.loads(row["scopes"]),
            "redirect_uri_provided": bool(row["redirect_uri_provided"]),
        }

    # -- tokens --------------------------------------------------------------

    def save_token(
        self, token: str, *, kind: str, client_id: str, key_hash: str,
        scopes: list[str], resource: str | None, ttl: int,
    ) -> None:
        with self.keys._lock:
            self.db.run(
                "INSERT INTO oauth_tokens(token_hash, kind, client_id, key_hash, scopes, resource, expires_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (_hash(token), kind, client_id, key_hash, json.dumps(scopes), resource, self._expiry(ttl)),
            )

    def load_token(self, token: str, kind: str) -> dict[str, Any] | None:
        row = self.db.one(
            "SELECT * FROM oauth_tokens WHERE token_hash=? AND kind=? AND expires_at > ?",
            (_hash(token), kind, _now()),
        )
        return {**row, "scopes": json.loads(row["scopes"])} if row else None

    def revoke_token(self, token: str) -> None:
        with self.keys._lock:
            self.db.run("DELETE FROM oauth_tokens WHERE token_hash = ?", (_hash(token),))

    def revoke_client_tokens(self, client_id: str, key_hash: str) -> None:
        """RFC 7009: revoking one credential revokes its sibling, so both kinds go together."""
        with self.keys._lock:
            self.db.run("DELETE FROM oauth_tokens WHERE client_id=? AND key_hash=?", (client_id, key_hash))
