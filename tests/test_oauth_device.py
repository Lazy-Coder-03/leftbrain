"""RFC 8628: an agent with no browser hands its human a short code, never a secret."""

from starlette.testclient import TestClient

from leftbrain.keys import MAX_ACTIVE_KEYS_PER_EMAIL, KeyStore
from leftbrain.serve import build_app
from leftbrain.web import auth
from leftbrain.web.config import WebConfig

BASE = "https://leftbrain.test"
SECRET = "test-secret-0123456789"
USER = auth.User(login="octo", email="octo@example.com", avatar_url=None)
WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def make_app(tmp_path):
    cfg = WebConfig(client_id=None, client_secret=None, secret=SECRET,
                    base_url=BASE, open_signup=False)
    return build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"), web_config=cfg)


def a_client(c, name="leftbrain-agent"):
    return c.post("/register", json={
        "redirect_uris": ["http://localhost/callback"], "client_name": name,
        "token_endpoint_auth_method": "none",
    }).json()["client_id"]


def start_device(c, client_id, scope="mcp"):
    return c.post("/oauth/device_authorization", data={"client_id": client_id, "scope": scope})


def poll(c, client_id, device_code):
    return c.post("/token", data={
        "grant_type": DEVICE_GRANT, "device_code": device_code, "client_id": client_id,
    })


def sign_in(c, user=USER):
    c.cookies.set(auth.SESSION_COOKIE, auth.sign_session(SECRET, user))
    return auth.csrf_token(SECRET, user)


def settle(c, user_code, approve=True, tools=("math",), user=USER):
    form = {"csrf": sign_in(c, user), "user_code": user_code, "scope_form": "1", "scope": list(tools)}
    if approve:
        form["approve"] = "1"
    return c.post("/device", data=form, headers={"user-agent": WINDOWS}, follow_redirects=False)


def tool_names(response):
    import json

    chunks = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
    for chunk in chunks or [response.text]:
        try:
            tools = (json.loads(chunk).get("result") or {}).get("tools")
        except ValueError:
            continue
        if isinstance(tools, list):
            return [t.get("name") for t in tools]
    return []


# -- discovery --------------------------------------------------------------


def test_the_metadata_advertises_the_device_grant(tmp_path):
    """What a client checks before it offers the flow at all."""
    with TestClient(make_app(tmp_path)) as c:
        m = c.get("/.well-known/oauth-authorization-server").json()
        assert m["device_authorization_endpoint"] == f"{BASE}/oauth/device_authorization"
        assert DEVICE_GRANT in m["grant_types_supported"]


# -- the agent's half -------------------------------------------------------


def test_a_device_request_returns_a_code_a_human_can_type(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = start_device(c, a_client(c))
        assert r.status_code == 200
        body = r.json()
        assert body["verification_uri"] == f"{BASE}/device"
        assert body["expires_in"] == 600 and body["interval"] == 5
        assert len(body["user_code"]) == 9 and body["user_code"][4] == "-"
        assert body["verification_uri_complete"].endswith(body["user_code"])
        assert body["device_code"] != body["user_code"]


def test_the_user_code_avoids_characters_that_are_misread(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        client_id = a_client(c)
        codes = [start_device(c, client_id).json()["user_code"] for _ in range(25)]
    assert not set("".join(codes)) & set("01IO")
    assert len(set(codes)) == len(codes)  # and they are not predictable


def test_an_unregistered_client_gets_nothing(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        assert start_device(c, "never-registered").status_code == 400
        assert c.post("/oauth/device_authorization", data={}).status_code == 400


def test_polling_before_approval_says_pending_then_the_token_arrives(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        client_id = a_client(c)
        started = start_device(c, client_id).json()

        pending = poll(c, client_id, started["device_code"])
        assert pending.status_code == 400 and pending.json()["error"] == "authorization_pending"

        assert settle(c, started["user_code"]).status_code in (200, 302, 303)

        granted = poll(c, client_id, started["device_code"])
        assert granted.status_code == 200, granted.text
        access = granted.json()["access_token"]
        called = c.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {access}"}, json=LIST)
        assert called.status_code == 200
        assert tool_names(called) == ["math"]  # the scope chosen at the device page


def test_a_device_code_is_spent_once(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        client_id = a_client(c)
        started = start_device(c, client_id).json()
        settle(c, started["user_code"])
        assert poll(c, client_id, started["device_code"]).status_code == 200
        assert poll(c, client_id, started["device_code"]).status_code == 400


def test_declining_at_the_device_page_denies_the_agent(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        client_id = a_client(c)
        started = start_device(c, client_id).json()
        settle(c, started["user_code"], approve=False)
        denied = poll(c, client_id, started["device_code"])
        assert denied.status_code == 400 and denied.json()["error"] == "access_denied"


def test_another_clients_device_code_is_not_honoured(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        mine, theirs = a_client(c, "mine"), a_client(c, "theirs")
        started = start_device(c, mine).json()
        settle(c, started["user_code"])
        assert poll(c, theirs, started["device_code"]).status_code == 400


def test_an_unknown_device_code_is_refused(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        assert poll(c, a_client(c), "never-issued").status_code == 400


# -- the human's half -------------------------------------------------------


def test_the_device_page_needs_a_signed_in_human(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = c.get("/device", follow_redirects=False)
        assert r.status_code in (302, 303) and "/login" in r.headers["location"]


def test_the_device_page_prefills_the_code_from_the_complete_uri(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        started = start_device(c, a_client(c)).json()
        sign_in(c)
        page = c.get("/device", params={"code": started["user_code"]})
        assert page.status_code == 200 and started["user_code"] in page.text
        assert page.headers["x-frame-options"] == "DENY"


def test_an_unknown_user_code_grants_nothing(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        a_client(c)
        assert settle(c, "ZZZZ-ZZZZ").status_code == 400
    assert KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET).list(USER.email) == []


def test_the_device_page_refuses_a_forged_csrf_token(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        client_id = a_client(c)
        started = start_device(c, client_id).json()
        sign_in(c)
        r = c.post("/device", data={"csrf": "forged", "user_code": started["user_code"],
                                    "approve": "1", "scope_form": "1", "scope": "math"})
        assert r.status_code == 403
        assert poll(c, client_id, started["device_code"]).json()["error"] == "authorization_pending"


def test_approving_mints_a_named_key_that_counts_against_the_cap(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        started = start_device(c, a_client(c, "leftbrain-agent")).json()
        settle(c, started["user_code"])
    made = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET).list(USER.email)
    assert len(made) == 1 and made[0].holds_slot
    # the approving browser was Windows, but the device grant exists so that the approval can
    # happen away from the agent's machine, so the key is named for the grant, not the OS (#104)
    assert made[0].note == "leftbrain-agent · device"


def test_an_unregistered_client_is_told_to_register_first(tmp_path):
    """Measured on 0.4.1: a bare {"error": "invalid_client"} after the 401 had sent the agent
    straight here, with no mention of /register anywhere in the exchange (#104)."""
    with TestClient(make_app(tmp_path)) as c:
        r = start_device(c, "never-registered")
        assert r.status_code == 400
        body = r.json()
        assert body["error"] == "invalid_client"
        assert f"POST {BASE}/register" in body["error_description"]
        assert f"{BASE}/docs/agents/auth" in body["error_description"]


def test_at_the_cap_the_device_page_says_so_and_mints_nothing(tmp_path):
    keys = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    for i in range(MAX_ACTIVE_KEYS_PER_EMAIL):
        keys.create_for_owner(USER.email, f"k{i}")
    with TestClient(make_app(tmp_path)) as c:
        started = start_device(c, a_client(c)).json()
        r = settle(c, started["user_code"])
        assert r.status_code == 409 and "revoke" in r.text
    assert len(keys.list(USER.email)) == MAX_ACTIVE_KEYS_PER_EMAIL


def test_approving_with_no_tools_ticked_grants_nothing_not_everything(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        started = start_device(c, a_client(c)).json()
        r = settle(c, started["user_code"], tools=())
        assert r.status_code == 400 and "at least one tool" in r.text
    assert KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET).list(USER.email) == []


def test_reconnecting_the_same_client_reuses_its_key(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        client_id = a_client(c)
        for _ in range(3):
            started = start_device(c, client_id).json()
            settle(c, started["user_code"])
    assert len(KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET).list(USER.email)) == 1


# -- and the other grants still work ----------------------------------------


def test_the_authorization_code_grant_is_untouched(tmp_path):
    """The token endpoint is wrapped, so the SDK's own grants must still reach it."""
    with TestClient(make_app(tmp_path)) as c:
        bad = c.post("/token", data={
            "grant_type": "authorization_code", "code": "nope", "client_id": a_client(c),
            "redirect_uri": "http://localhost:3118/callback", "code_verifier": "x" * 50,
        })
        assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"
