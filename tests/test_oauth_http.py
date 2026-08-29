"""The OAuth endpoints over HTTP, driven the way a real client drives them."""

from starlette.testclient import TestClient

from leftbrain.serve import build_app
from leftbrain.web.config import WebConfig

BASE = "https://leftbrain.test"
SECRET = "test-secret-0123456789"


def make_app(tmp_path, **over):
    cfg = WebConfig(**{
        "client_id": None, "client_secret": None, "secret": SECRET,
        "base_url": BASE, "open_signup": False, **over,
    })
    return build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"), web_config=cfg)


def test_the_protected_resource_document_names_this_server(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        doc = c.get("/.well-known/oauth-protected-resource/mcp")
        assert doc.status_code == 200
        body = doc.json()
        # Claude requires `resource` to equal the MCP URL exactly as the user types it
        assert body["resource"] == f"{BASE}/mcp"
        assert body["authorization_servers"] == [BASE]
        assert body["resource_documentation"].endswith("/docs/agents/auth")


def test_the_authorization_server_metadata_is_what_claude_checks_for(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        m = c.get("/.well-known/oauth-authorization-server").json()
        assert m["issuer"] == BASE
        assert m["code_challenge_methods_supported"] == ["S256"]
        # Claude selects CIMD only when BOTH of these are present; the SDK ships neither
        assert m["client_id_metadata_document_supported"] is True
        assert "none" in m["token_endpoint_auth_methods_supported"]
        # and never asks for a refresh token unless offline_access is offered
        assert "offline_access" in m["scopes_supported"]
        assert m["registration_endpoint"] == f"{BASE}/register"
        assert m["revocation_endpoint"] == f"{BASE}/revoke"
        assert m["authorization_endpoint"] == f"{BASE}/authorize"
        assert m["token_endpoint"] == f"{BASE}/token"


def test_the_discovery_paths_claude_code_probes_all_answer(tmp_path):
    """Measured against a live session: it probes these before it ever calls /mcp."""
    with TestClient(make_app(tmp_path)) as c:
        assert c.get("/.well-known/oauth-protected-resource/mcp").status_code == 200
        assert c.get("/.well-known/oauth-authorization-server").status_code == 200


def test_an_unauthenticated_mcp_call_points_at_the_metadata(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = c.post("/mcp", json={})
        assert r.status_code == 401
        challenge = r.headers["www-authenticate"]
        assert challenge.startswith("Bearer ")
        assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in challenge
        body = r.json()
        # the existing three fields are untouched
        assert body["ok"] is False and body["error"] and body["message"]
        how = body["how_to_authorize"]
        assert how["documentation"] == f"{BASE}/docs/agents/auth"
        assert how["if_you_have_no_browser"].endswith("/oauth/device_authorization")
        assert BASE in how["tell_your_user"]
        assert how["static_key_alternative"] == f"{BASE}/dashboard"


def test_registration_answers_instead_of_404ing(tmp_path):
    """The failure a Claude Code session hit: POST /register fell through to the catch-all."""
    with TestClient(make_app(tmp_path)) as c:
        r = c.post("/register", json={
            "redirect_uris": ["http://localhost/callback"],
            "client_name": "Claude Code",
            "token_endpoint_auth_method": "none",
        })
        assert r.status_code == 201
        registered = r.json()
        assert registered["client_id"]
        assert registered["client_name"] == "Claude Code"


def test_a_registered_client_survives_a_restart(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        client_id = c.post("/register", json={
            "redirect_uris": ["http://localhost/callback"], "client_name": "Cursor",
            "token_endpoint_auth_method": "none",
        }).json()["client_id"]
    with TestClient(make_app(tmp_path)) as c:
        # a fresh app over the same database still knows it, because registration is stored
        assert c.post("/token", data={
            "grant_type": "authorization_code", "code": "nope", "client_id": client_id,
            "redirect_uri": "http://localhost:1/callback", "code_verifier": "x" * 43,
        }).status_code == 400  # reaches the handler rather than "invalid client"


def test_oauth_is_absent_without_a_secret(tmp_path):
    with TestClient(make_app(tmp_path, secret=None)) as c:
        assert c.get("/.well-known/oauth-authorization-server").status_code == 404
        assert c.post("/register", json={"redirect_uris": ["http://localhost/cb"]}).status_code == 404


def test_oauth_is_absent_without_a_base_url(tmp_path):
    """RFC 8414 compares the issuer by exact string, so a guessed one is worse than none."""
    with TestClient(make_app(tmp_path, base_url=None)) as c:
        assert c.get("/.well-known/oauth-authorization-server").status_code == 404
        r = c.post("/mcp", json={})
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"  # no pointer we cannot honour
        assert "how_to_authorize" not in r.json()


def test_the_existing_key_path_is_untouched(tmp_path):
    from leftbrain.keys import KeyStore

    raw, _ = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET).create("a@b.co")
    with TestClient(make_app(tmp_path)) as c:
        r = c.post("/mcp", headers={
            "Authorization": f"Bearer {raw}",
            "Accept": "application/json, text/event-stream",
        }, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        assert r.status_code == 200
        assert r.headers["x-ratelimit-remaining-today"]
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {raw}"}).status_code == 200
