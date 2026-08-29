"""Persistence for the OAuth authorization server, alongside the key store.

Every issued credential is bound to an ordinary key row, so revoking the key on the
dashboard revokes the tokens with it and no second revocation path exists.
"""

from __future__ import annotations

import json
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
