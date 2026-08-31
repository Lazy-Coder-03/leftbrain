"""The OAuth endpoints over HTTP, driven the way a real client drives them."""

from starlette.testclient import TestClient

from leftbrain.keys import KeyStore
from leftbrain.oauth.store import OAuthStore
from leftbrain.scopes import parse_scope
from leftbrain.serve import build_app
from leftbrain.web.config import WebConfig

BASE = "https://leftbrain.test"
SECRET = "test-secret-0123456789"
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def make_app(tmp_path, files=False, **over):
    cfg = WebConfig(**{
        "client_id": None, "client_secret": None, "secret": SECRET,
        "base_url": BASE, "open_signup": False, **over,
    })
    return build_app(include_external=False, include_files=files, keys_db=str(tmp_path / "k.sqlite3"), web_config=cfg)


def test_the_protected_resource_document_names_this_server(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        doc = c.get("/.well-known/oauth-protected-resource/mcp")
        assert doc.status_code == 200
        body = doc.json()
        # Claude requires `resource` to equal the MCP URL exactly as the user types it
        assert body["resource"] == f"{BASE}/mcp"
        assert body["authorization_servers"] == [BASE]
        assert body["resource_documentation"].endswith("/docs/agents/auth")


def test_every_mounted_endpoint_has_its_own_protected_resource_document(tmp_path):
    """RFC 9728 §3.3: the client checks `resource` against the URL it is connecting to (#101)."""
    with TestClient(make_app(tmp_path, files=True)) as c:
        core = c.get("/.well-known/oauth-protected-resource/mcp").json()
        ext = c.get("/.well-known/oauth-protected-resource/files/mcp")
        assert ext.status_code == 200
        assert core["resource"] == f"{BASE}/mcp"
        assert ext.json()["resource"] == f"{BASE}/files/mcp"
        # the same authorization server and the same agent document behind both
        assert ext.json()["authorization_servers"] == core["authorization_servers"] == [BASE]
        assert ext.json()["resource_documentation"] == core["resource_documentation"]


def test_an_endpoint_that_is_not_mounted_has_no_document(tmp_path):
    with TestClient(make_app(tmp_path, files=False)) as c:
        assert c.get("/.well-known/oauth-protected-resource/files/mcp").status_code == 404
        assert c.get("/.well-known/oauth-protected-resource/external/mcp").status_code == 404  # retired (#100)


def test_a_401_on_a_second_mount_points_at_its_own_document(tmp_path):
    """Measured on 0.4.1: the pointer named /mcp's document whichever mount was asked for, and
    Claude Code refused the second mount with "does not match expected" (#101)."""
    with TestClient(make_app(tmp_path, files=True)) as c:
        for endpoint in ("/mcp", "/files/mcp"):
            r = c.post(endpoint, json={})
            assert r.status_code == 401
            doc = f"{BASE}/.well-known/oauth-protected-resource{endpoint}"
            assert f'resource_metadata="{doc}"' in r.headers["www-authenticate"], endpoint
            assert r.json()["how_to_authorize"]["if_you_have_a_browser"] == doc, endpoint
            # and the document the pointer names declares that endpoint, not another
            assert c.get(doc.removeprefix(BASE)).json()["resource"] == f"{BASE}{endpoint}"


def test_the_401_message_never_names_a_closed_signup(tmp_path):
    """0.4.1 said "get one at POST /keys/signup" whenever a key store existed; that route is
    404 unless signup is open, so the hosted server sent every agent to a closed door (#104)."""
    with TestClient(make_app(tmp_path, open_signup=False)) as c:
        body = c.post("/mcp", json={}).json()
        assert "/keys/signup" not in body["message"]
        assert "/login" in body["message"]
        assert c.post("/keys/signup", json={"email": "a@b.co"}).status_code == 404
    with TestClient(make_app(tmp_path, open_signup=True)) as c:
        assert "POST /keys/signup" in c.post("/mcp", json={}).json()["message"]


def test_the_no_browser_route_names_registration_first(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        how = c.post("/mcp", json={}).json()["how_to_authorize"]
        assert how["if_you_have_no_browser"].startswith(f"POST {BASE}/register")
        assert how["if_you_have_no_browser"].endswith("/oauth/device_authorization")


def test_a_401_off_the_mcp_paths_points_at_the_core_document(tmp_path):
    """/keys/me is protected too; it has no document of its own, so it names the core one."""
    with TestClient(make_app(tmp_path, files=True)) as c:
        r = c.get("/keys/me")
        assert r.status_code == 401
        assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in r.headers["www-authenticate"]


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


def test_the_agent_auth_document_is_served_and_reachable(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        page = c.get("/docs/agents/auth", headers={"Accept": "text/html"})
        assert page.status_code == 200
        for needle in ("device_authorization", "user_code", "/register",
                       "oauth-protected-resource", "lblz_", "/keys/me/scope"):
            assert needle in page.text, needle
        # narrowing is told as an instruction, and the one-way rule is stated plainly
        assert "only narrow" in page.text.lower() or "cannot widen" in page.text.lower()
        # and the standard pointer to it resolves
        prm = c.get("/.well-known/oauth-protected-resource/mcp").json()
        assert prm["resource_documentation"].endswith("/docs/agents/auth")


def test_the_agent_document_is_listed_in_the_docs_nav(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        assert "/docs/agents/auth" in c.get("/docs", headers={"Accept": "text/html"}).text


def test_the_cimd_escape_hatch_is_off_and_announces_itself_when_on(tmp_path, monkeypatch, capsys):
    make_app(tmp_path)
    assert "CIMD_ALLOW_INSECURE" not in capsys.readouterr().out
    monkeypatch.setenv("LEFTBRAIN_CIMD_ALLOW_INSECURE", "1")
    make_app(tmp_path)
    printed = capsys.readouterr().out
    assert "LEFTBRAIN_CIMD_ALLOW_INSECURE is on" in printed
    assert "private addresses" in printed


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


def a_key_and_token(tmp_path, token="tok-1", **create):
    keys = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, info = keys.create("a@b.co", **create)
    row = keys.db.one("SELECT key_hash FROM keys WHERE prefix=?", (info.prefix,))
    OAuthStore(keys).save_token(token, kind="access", client_id="c1",
                                key_hash=row["key_hash"], scopes=["mcp"], resource=None, ttl=3600)
    return keys, raw, info


def call(client, credential, body=None):
    return client.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {credential}"},
                       json=body or LIST)


def tool_names(response):
    """The tool names in a `tools/list` reply, in either wire form.

    Substring matching on the body does not work: `math`'s own description mentions
    `convert_form`, so "convert" is present even when the tool is not.
    """
    import json

    chunks = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
    for chunk in chunks or [response.text]:
        try:
            result = (json.loads(chunk).get("result") or {}).get("tools")
        except ValueError:
            continue
        if isinstance(result, list):
            return [t.get("name") for t in result]
    return []


def test_a_token_is_accepted_where_a_key_is(tmp_path):
    a_key_and_token(tmp_path, daily_quota=1000)
    with TestClient(make_app(tmp_path)) as c:
        r = call(c, "tok-1")
        assert r.status_code == 200
        # a `tools/list` is protocol traffic, not work, and no longer costs a unit (#62)
        assert r.headers["x-ratelimit-remaining-today"] == "1000"
        assert r.headers["x-ratelimit-limit-day"] == "1000"


def test_a_key_and_a_token_share_one_quota(tmp_path):
    _, raw, _ = a_key_and_token(tmp_path, daily_quota=2)
    work = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "math", "arguments": {"mode": "eval", "expr": "1+1"}}}
    with TestClient(make_app(tmp_path)) as c:
        # the header counts the call it rides on, so two calls spend the budget and the
        # third is refused - whichever credential made them (#62)
        assert call(c, "tok-1", work).headers["x-ratelimit-remaining-today"] == "1"
        assert call(c, raw, work).headers["x-ratelimit-remaining-today"] == "0"
        assert call(c, "tok-1", work).status_code == 429


def test_a_scoped_token_sees_only_its_tools(tmp_path):
    a_key_and_token(tmp_path, scope=parse_scope(["math"]))
    with TestClient(make_app(tmp_path)) as c:
        assert tool_names(call(c, "tok-1")) == ["math"]


def test_a_scoped_token_is_refused_outside_its_scope_by_contract(tmp_path):
    """Not a transport error: the caller gets the same `forbidden` envelope a key gets."""
    a_key_and_token(tmp_path, scope=parse_scope(["math"]))
    with TestClient(make_app(tmp_path)) as c:
        r = call(c, "tok-1", {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "convert", "arguments": {"mode": "units", "value": 1, "from_unit": "m", "to_unit": "cm"}},
        })
        assert r.status_code == 200
        assert "forbidden" in r.text


def test_an_unknown_token_is_still_401(tmp_path):
    a_key_and_token(tmp_path)
    with TestClient(make_app(tmp_path)) as c:
        assert call(c, "nope").status_code == 401
        assert call(c, "").status_code == 401


def test_a_token_reaches_keys_me_as_its_key(tmp_path):
    _, _, info = a_key_and_token(tmp_path)
    with TestClient(make_app(tmp_path)) as c:
        r = c.get("/keys/me", headers={"Authorization": "Bearer tok-1"})
        assert r.status_code == 200
        assert r.json()["result"]["prefix"] == info.prefix


def test_the_existing_key_path_is_untouched(tmp_path):
    raw, _ = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET).create("a@b.co")
    with TestClient(make_app(tmp_path)) as c:
        r = c.post("/mcp", headers={
            "Authorization": f"Bearer {raw}",
            "Accept": "application/json, text/event-stream",
        }, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        assert r.status_code == 200
        assert r.headers["x-ratelimit-remaining-today"]
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {raw}"}).status_code == 200


# -- the whole flow, driven the way a client drives it -----------------------


def pkce():
    import base64
    import hashlib
    import secrets

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def register_client(c, name="Claude Code", redirect="http://localhost/callback"):
    return c.post("/register", json={
        "redirect_uris": [redirect], "client_name": name, "token_endpoint_auth_method": "none",
    }).json()["client_id"]


def sign_in(c):
    from leftbrain.web import auth

    user = auth.User(login="octo", email="octo@example.com", avatar_url=None)
    c.cookies.set(auth.SESSION_COOKIE, auth.sign_session(SECRET, user))
    return auth.csrf_token(SECRET, user)


def approve(c, client_id, challenge, tools=("math",), redirect="http://localhost:3118/callback"):
    from urllib.parse import parse_qs, urlparse

    csrf = sign_in(c)
    r = c.post("/oauth/consent", follow_redirects=False, headers={"user-agent": WINDOWS}, data={
        "csrf": csrf, "client_id": client_id, "redirect_uri": redirect, "explicit": "1",
        "code_challenge": challenge, "scopes": "mcp", "state": "xyz", "resource": "",
        "approve": "1", "scope_form": "1", "scope": list(tools),
    })
    assert r.status_code in (302, 303), r.text
    return parse_qs(urlparse(r.headers["location"]).query)["code"][0]


WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130"


def test_a_client_may_request_every_scope_we_advertise(tmp_path):
    """Claude reads scopes_supported and asks for all of it, offline_access included.

    A client that registers without naming a scope is given `default_scopes`, and
    /authorize then refuses anything the client does not hold — so advertising a scope
    that is not in the default is advertising one we will reject. Live Claude web failed
    here with oauth_error=invalid_scope.
    """
    with TestClient(make_app(tmp_path)) as c:
        advertised = c.get("/.well-known/oauth-authorization-server").json()["scopes_supported"]
        assert "offline_access" in advertised
        client_id = register_client(c)  # no scope named, exactly as Claude registers
        _, challenge = pkce()
        r = c.get("/authorize", follow_redirects=False, params={
            "client_id": client_id, "redirect_uri": "http://localhost:3118/callback",
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "xyz",
            "scope": " ".join(advertised),
        })
        assert r.status_code in (302, 303)
        location = r.headers["location"]
        assert "invalid_scope" not in location, location
        assert location.startswith("/oauth/consent?")


def test_a_scope_we_do_not_advertise_is_still_refused(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        client_id = register_client(c)
        _, challenge = pkce()
        r = c.get("/authorize", follow_redirects=False, params={
            "client_id": client_id, "redirect_uri": "http://localhost:3118/callback",
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "xyz", "scope": "mcp admin",
        })
        assert "invalid_scope" in r.headers["location"]


def test_authorize_hands_the_browser_to_the_consent_screen(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        client_id = register_client(c)
        _, challenge = pkce()
        r = c.get("/authorize", follow_redirects=False, params={
            "client_id": client_id, "redirect_uri": "http://localhost:3118/callback",
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "xyz", "scope": "mcp",
        })
        assert r.status_code in (302, 303)
        assert r.headers["location"].startswith("/oauth/consent?")


def test_register_consent_token_then_call_a_tool(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        client_id = register_client(c)
        verifier, challenge = pkce()
        code = approve(c, client_id, challenge, tools=("math",))

        token = c.post("/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": client_id,
            "redirect_uri": "http://localhost:3118/callback", "code_verifier": verifier,
        })
        assert token.status_code == 200, token.text
        granted = token.json()
        assert granted["token_type"] == "Bearer" and granted["expires_in"] == 3600
        access = granted["access_token"]

        listed = call(c, access)
        assert listed.status_code == 200
        assert tool_names(listed) == ["math"]  # the scope chosen at consent, enforced on the wire


def test_a_refresh_token_buys_a_new_access_token(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        client_id = register_client(c)
        verifier, challenge = pkce()
        code = approve(c, client_id, challenge)
        first = c.post("/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": client_id,
            "redirect_uri": "http://localhost:3118/callback", "code_verifier": verifier,
        }).json()

        again = c.post("/token", data={
            "grant_type": "refresh_token", "refresh_token": first["refresh_token"],
            "client_id": client_id,
        })
        assert again.status_code == 200, again.text
        assert again.json()["access_token"] != first["access_token"]
        assert call(c, again.json()["access_token"]).status_code == 200


def test_the_wrong_pkce_verifier_is_refused(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        client_id = register_client(c)
        _, challenge = pkce()
        code = approve(c, client_id, challenge)
        bad = c.post("/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": client_id,
            "redirect_uri": "http://localhost:3118/callback", "code_verifier": "x" * 60,
        })
        assert bad.status_code == 400
        assert bad.json()["error"] in ("invalid_grant", "invalid_request")


def test_a_code_cannot_be_spent_twice(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        client_id = register_client(c)
        verifier, challenge = pkce()
        code = approve(c, client_id, challenge)
        body = {
            "grant_type": "authorization_code", "code": code, "client_id": client_id,
            "redirect_uri": "http://localhost:3118/callback", "code_verifier": verifier,
        }
        assert c.post("/token", data=body).status_code == 200
        assert c.post("/token", data=body).status_code == 400


def test_revoking_the_key_on_the_dashboard_kills_the_connector(tmp_path):
    """Acceptance criterion 4: revoke stops it working immediately, with no OAuth step."""
    with TestClient(make_app(tmp_path)) as c:
        client_id = register_client(c)
        verifier, challenge = pkce()
        code = approve(c, client_id, challenge)
        access = c.post("/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": client_id,
            "redirect_uri": "http://localhost:3118/callback", "code_verifier": verifier,
        }).json()["access_token"]
        assert call(c, access).status_code == 200

        keys = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
        minted = keys.list("octo@example.com")[0]
        assert minted.note == "Claude Code · Windows"
        keys.revoke(minted.prefix)

        assert call(c, access).status_code == 401

