"""Redirect-URI comparison: exact, except that a loopback address may vary its port.

RFC 8252 section 7.3 requires a native client's loopback redirect to be matched without its
port, because the port is chosen at runtime. Claude Code registers ``http://localhost/callback``
and comes back on something like ``http://localhost:3118/callback``; Cursor and VS Code do the
same. Everything else is compared as an exact string, so this widens the port and nothing else.
"""

from __future__ import annotations

from urllib.parse import urlparse

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
