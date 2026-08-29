"""Client ID Metadata Documents: a client identifies itself by an HTTPS URL it hosts.

Accepting one means leftbrain fetches a URL chosen by an unauthenticated stranger, which is a
server-side request forgery primitive if left unguarded — the MCP security guidance and the
CIMD draft both say so in as many words. The fence: HTTPS only, no private or loopback
destinations, no redirects followed, a short timeout and a size cap.

CIMD is not optional politeness. Claude Code identifies this way, and Claude selects it only
when the metadata advertises it, so a server without it re-registers every client on every
connection.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

from .redirects import LoopbackTolerantClient

TIMEOUT_S = 5.0
#: Well under Claude's 10 s budget for the whole token call, and far more than a static
#: JSON document needs.
MAX_BYTES = 64 * 1024


def _is_public(host: str) -> bool:
    """Whether every address this host resolves to is on the public internet.

    Every address, not the first: a name that resolves to one public and one internal
    address is the trick, not an accident.
    """
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:  # pragma: no cover - getaddrinfo returned something unparseable
            return False
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast or address.is_unspecified):
            return False
    return True


def safe_client_metadata_url(client_id: str, *, allow_insecure: bool) -> str | None:
    """The URL to fetch, or None when this client id must not be fetched at all."""
    if not client_id or "://" not in client_id:
        return None
    try:
        parsed = urlparse(client_id)
    except ValueError:
        return None
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        return None
    if allow_insecure:
        return client_id  # local development only, and announced at startup
    if parsed.scheme != "https":
        return None
    if not _is_public(parsed.hostname):
        return None
    return client_id


async def fetch_client_metadata(
    client_id: str, *, allow_insecure: bool, transport: Any = None
) -> LoopbackTolerantClient | None:
    """The client a CIMD URL describes, or None if anything at all is off about it."""
    url = safe_client_metadata_url(client_id, allow_insecure=allow_insecure)
    if url is None:
        return None
    import httpx

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False, transport=transport) as http:
            response = await http.get(url, headers={"accept": "application/json"})
            if response.status_code != 200 or len(response.content) > MAX_BYTES:
                return None
            document = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    # the document must claim the URL it was fetched from, or it is borrowing an identity
    if not isinstance(document, dict) or document.get("client_id") != client_id:
        return None
    try:
        client = LoopbackTolerantClient.model_validate(document)
    except ValueError:
        return None
    # a CIMD client is public by definition: it holds no secret and authenticates as `none`
    client.client_secret = None
    client.token_endpoint_auth_method = "none"
    return client
