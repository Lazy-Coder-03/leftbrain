"""Client ID Metadata Documents: leftbrain fetches a URL a stranger supplied, so it is guarded.

CIMD is how Claude Code identifies itself, and the spec's own security guidance calls out that
an authorization server accepting one takes a URL as input from an unknown client and fetches
it. That is a server-side request forgery primitive unless it is fenced.
"""

import asyncio

import httpx
import pytest

from leftbrain.oauth.cimd import fetch_client_metadata, safe_client_metadata_url


def run(coro):
    return asyncio.run(coro)


def serving(document, status=200):
    def handler(request):
        return httpx.Response(status, json=document)

    return httpx.MockTransport(handler)


A_DOCUMENT = {
    "client_id": "https://app.example/client.json",
    "client_name": "Example App",
    "redirect_uris": ["http://localhost/callback"],
    "token_endpoint_auth_method": "none",
}


# -- what may be fetched at all ---------------------------------------------


@pytest.mark.parametrize("client_id", [
    "http://example.com/client.json",             # not https
    "https://localhost/client.json",              # loopback
    "https://127.0.0.1/client.json",
    "https://[::1]/client.json",
    "https://10.0.0.5/client.json",               # private
    "https://192.168.1.1/client.json",
    "https://172.16.0.1/client.json",
    "https://169.254.169.254/latest/meta-data",   # the cloud metadata endpoint
    "ftp://example.com/client.json",
    "not-a-url",
    "",
])
def test_a_dangerous_client_id_is_refused(client_id):
    assert safe_client_metadata_url(client_id, allow_insecure=False) is None


def resolving_to(*addresses):
    """A `getaddrinfo` stub, so the policy is tested without asking the network anything."""
    def getaddrinfo(host, port, *a, **kw):
        return [(2, 1, 6, "", (address, port)) for address in addresses]

    return getaddrinfo


def test_an_ordinary_https_client_id_is_allowed(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", resolving_to("104.18.32.1"))
    url = "https://claude.ai/oauth/claude-code-client-metadata"
    assert safe_client_metadata_url(url, allow_insecure=False) == url


def test_a_host_resolving_to_any_internal_address_is_refused(monkeypatch):
    """One public answer and one internal answer is the trick, not an accident."""
    monkeypatch.setattr("socket.getaddrinfo", resolving_to("104.18.32.1", "169.254.169.254"))
    assert safe_client_metadata_url("https://sneaky.example/c.json", allow_insecure=False) is None
    monkeypatch.setattr("socket.getaddrinfo", resolving_to("::1"))
    assert safe_client_metadata_url("https://sneaky.example/c.json", allow_insecure=False) is None
    monkeypatch.setattr("socket.getaddrinfo", resolving_to())
    assert safe_client_metadata_url("https://sneaky.example/c.json", allow_insecure=False) is None


def test_loopback_is_allowed_only_when_explicitly_opted_in():
    """A development escape hatch, off by default and announced when it is on."""
    assert safe_client_metadata_url("http://localhost:9000/c.json", allow_insecure=False) is None
    assert safe_client_metadata_url("http://localhost:9000/c.json", allow_insecure=True)


def test_a_host_that_does_not_resolve_is_refused(monkeypatch):
    """Stubbed rather than relying on a real NXDOMAIN: a hijacking resolver answers anyway."""
    def refuses(host, port, *a, **kw):
        raise OSError("Name or service not known")

    monkeypatch.setattr("socket.getaddrinfo", refuses)
    assert safe_client_metadata_url("https://app.example/client.json", allow_insecure=False) is None


# -- what the document has to say -------------------------------------------


@pytest.fixture
def reachable(monkeypatch):
    """Stub the network-policy check, which has its own tests above.

    Without this every test below passes because the host does not resolve, rather than
    because the document was judged — a green that proves nothing about the parsing.
    """
    monkeypatch.setattr("leftbrain.oauth.cimd._is_public", lambda host: True)


def test_a_document_is_read_and_the_client_is_public(reachable):
    client = run(fetch_client_metadata(
        "https://app.example/client.json", allow_insecure=False, transport=serving(A_DOCUMENT)))
    assert client is not None
    assert client.client_id == "https://app.example/client.json"
    assert client.client_name == "Example App"
    # a CIMD client holds no secret and authenticates as `none`
    assert client.client_secret is None
    assert client.token_endpoint_auth_method == "none"


def test_a_cimd_client_still_tolerates_a_loopback_port(reachable):
    """Claude Code identifies by CIMD and returns on an ephemeral port; both rules must hold."""
    from pydantic import AnyUrl

    client = run(fetch_client_metadata(
        "https://app.example/client.json", allow_insecure=False, transport=serving(A_DOCUMENT)))
    assert str(client.validate_redirect_uri(AnyUrl("http://localhost:3118/callback")))


def test_a_document_claiming_a_different_client_id_is_refused(reachable):
    """Otherwise a document could borrow another client's identity by being fetched here."""
    impostor = {**A_DOCUMENT, "client_id": "https://claude.ai/oauth/claude-code-client-metadata"}
    assert run(fetch_client_metadata(
        "https://app.example/client.json", allow_insecure=False, transport=serving(impostor))) is None


def test_a_document_that_is_not_a_registration_is_refused(reachable):
    assert run(fetch_client_metadata(
        "https://app.example/c.json", allow_insecure=False,
        transport=serving({"client_id": "https://app.example/c.json"}))) is not None
    assert run(fetch_client_metadata(
        "https://app.example/c.json", allow_insecure=False, transport=serving(["not", "a", "map"]))) is None


def test_a_failing_or_oversized_document_is_refused(reachable):
    assert run(fetch_client_metadata(
        "https://app.example/c.json", allow_insecure=False,
        transport=serving(A_DOCUMENT, status=500))) is None
    huge = {**A_DOCUMENT, "pad": "y" * 200_000}
    assert run(fetch_client_metadata(
        "https://app.example/c.json", allow_insecure=False, transport=serving(huge))) is None


def test_a_redirect_is_not_followed(reachable):
    """A safe-looking host that redirects to an internal one is the whole trick."""
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    assert run(fetch_client_metadata(
        "https://app.example/c.json", allow_insecure=False,
        transport=httpx.MockTransport(handler))) is None


def test_a_transport_error_is_refused_not_raised(reachable):
    def handler(request):
        raise httpx.ConnectError("no route to host")

    assert run(fetch_client_metadata(
        "https://app.example/c.json", allow_insecure=False,
        transport=httpx.MockTransport(handler))) is None
