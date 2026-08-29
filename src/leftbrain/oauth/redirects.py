"""Redirect-URI comparison: exact, except that a loopback address may vary its port.

RFC 8252 section 7.3 requires a native client's loopback redirect to be matched without its
port, because the port is chosen at runtime. Claude Code registers ``http://localhost/callback``
and comes back on something like ``http://localhost:3118/callback``; Cursor and VS Code do the
same. Everything else is compared as an exact string, so this widens the port and nothing else.
"""

from __future__ import annotations

from urllib.parse import urlparse

from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull
from pydantic import AnyUrl

#: Hosts RFC 8252 treats as loopback. `urlparse().hostname` lower-cases and strips the
#: brackets from an IPv6 literal, so `http://[::1]:7000/cb` arrives here as `::1`.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_loopback(uri: str) -> bool:
    try:
        return urlparse(uri).hostname in LOOPBACK_HOSTS
    except ValueError:
        return False


def redirect_uri_matches(registered: str, presented: str) -> bool:
    if registered == presented:
        return True
    try:
        r, p = urlparse(registered), urlparse(presented)
    except ValueError:
        return False
    if r.hostname not in LOOPBACK_HOSTS or p.hostname not in LOOPBACK_HOSTS:
        return False
    # the port is the only component allowed to differ, and only between loopback addresses
    return (r.scheme, r.hostname, r.path) == (p.scheme, p.hostname, p.path)


class LoopbackTolerantClient(OAuthClientInformationFull):
    """A registered client whose loopback redirect may vary its port.

    The SDK's own `validate_redirect_uri` compares exactly, and runs inside the `/authorize`
    handler before any of our code is reached. That rejects Claude Code outright: it registers
    `http://localhost/callback` and returns on an ephemeral port, which RFC 8252 section 7.3
    requires an authorization server to accept. Overriding the model's own method is the way
    to reach that check, since the handler asks the client object rather than the provider.
    """

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        if redirect_uri is None:
            return super().validate_redirect_uri(redirect_uri)
        for registered in self.redirect_uris or []:
            if redirect_uri_matches(str(registered), str(redirect_uri)):
                return redirect_uri
        raise InvalidRedirectUriError(f"Redirect URI '{redirect_uri}' not registered for client")
