"""The authorization-server provider the MCP SDK drives.

Every token is issued against an ordinary key, so the answer to "what may this connector do,
and how much of it" lives in one place: the key's scope and its quota. The SDK never renders
these model subclasses back to a client, which is what makes it safe to carry the key binding
on them.
"""

from __future__ import annotations

import secrets
import time
from typing import Any
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from .store import OAuthStore

ACCESS_TTL = 3600  # one hour
REFRESH_TTL = 30 * 86400  # thirty days
CODE_TTL = 60


class _StoredCode(AuthorizationCode):
    key_hash: str


class _StoredRefresh(RefreshToken):
    key_hash: str
    resource: str | None = None


class _StoredAccess(AccessToken):
    key_hash: str


class LeftbrainOAuthProvider:
    """Implements the SDK's `OAuthAuthorizationServerProvider` over `OAuthStore`."""

    def __init__(self, oauth: OAuthStore, keys: Any, *, consent_path: str = "/oauth/consent") -> None:
        self.oauth = oauth
        self.keys = keys
        self.consent_path = consent_path

    # -- clients -------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.oauth.load_client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.oauth.save_client(client_info)

    # -- authorize -----------------------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Hand the browser to leftbrain's own consent screen.

        Nothing is granted here and no cookie is set. The confused-deputy mitigation requires
        consent to be recorded before any credential or signed state exists, so this step only
        carries the request across to a page a human has to look at.
        """
        request = {
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "explicit": "1" if params.redirect_uri_provided_explicitly else "0",
            "code_challenge": params.code_challenge,
            "scopes": " ".join(params.scopes or []),
            "state": params.state or "",
            "resource": params.resource or "",
        }
        return f"{self.consent_path}?{urlencode(request)}"

    def issue_code(
        self, *, client_id: str, key_hash: str, owner: str, scopes: list[str],
        code_challenge: str, redirect_uri: str, redirect_uri_provided: bool, resource: str | None,
    ) -> str:
        """Mint a single-use authorization code. Called by the consent view once a human approves."""
        code = secrets.token_urlsafe(32)  # 256 bits, well over RFC 6749's 128-bit floor
        self.oauth.save_code(
            code, client_id=client_id, key_hash=key_hash, owner=owner, scopes=scopes,
            code_challenge=code_challenge, redirect_uri=redirect_uri,
            redirect_uri_provided=redirect_uri_provided, resource=resource, ttl=CODE_TTL,
        )
        return code

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> _StoredCode | None:
        row = self.oauth.take_code(authorization_code)
        if not row or row["client_id"] != client.client_id:
            return None
        return _StoredCode(
            code=authorization_code,
            scopes=row["scopes"],
            expires_at=time.time() + CODE_TTL,
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=AnyUrl(row["redirect_uri"]),
            redirect_uri_provided_explicitly=row["redirect_uri_provided"],
            resource=row["resource"],
            subject=row["owner"],
            key_hash=row["key_hash"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        key_hash = getattr(authorization_code, "key_hash", None)
        if not key_hash:  # pragma: no cover - only reachable if a code bypassed issue_code
            raise TokenError("invalid_grant", "authorization code is not bound to a key")
        return self._issue_pair(
            client.client_id, key_hash, authorization_code.scopes, authorization_code.resource
        )

    # -- refresh -------------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> _StoredRefresh | None:
        row = self.oauth.load_token(refresh_token, "refresh")
        if not row or row["client_id"] != client.client_id:
            return None
        return _StoredRefresh(
            token=refresh_token, client_id=row["client_id"], scopes=row["scopes"],
            key_hash=row["key_hash"], resource=row["resource"],
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        key_hash = getattr(refresh_token, "key_hash", None)
        if not key_hash:  # pragma: no cover - as above
            raise TokenError("invalid_grant", "refresh token is not bound to a key")
        # OAuth 2.1 requires rotation for a public client, so the presented token dies here
        self.oauth.revoke_token(refresh_token.token)
        return self._issue_pair(
            client.client_id, key_hash, scopes or refresh_token.scopes,
            getattr(refresh_token, "resource", None),
        )

    # -- access tokens -------------------------------------------------------

    async def load_access_token(self, token: str) -> _StoredAccess | None:
        row = self.oauth.load_token(token, "access")
        if not row:
            return None
        return _StoredAccess(
            token=token, client_id=row["client_id"], scopes=row["scopes"],
            resource=row["resource"], key_hash=row["key_hash"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        key_hash = getattr(token, "key_hash", None)
        if key_hash:
            self.oauth.revoke_client_tokens(token.client_id, key_hash)
        else:  # pragma: no cover - a token the SDK built without our subclass
            self.oauth.revoke_token(token.token)

    # -- shared --------------------------------------------------------------

    def _issue_pair(self, client_id: str, key_hash: str, scopes: list[str], resource: str | None) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.oauth.save_token(access, kind="access", client_id=client_id, key_hash=key_hash,
                              scopes=scopes, resource=resource, ttl=ACCESS_TTL)
        self.oauth.save_token(refresh, kind="refresh", client_id=client_id, key_hash=key_hash,
                              scopes=scopes, resource=resource, ttl=REFRESH_TTL)
        return OAuthToken(access_token=access, refresh_token=refresh,
                          expires_in=ACCESS_TTL, scope=" ".join(scopes))
