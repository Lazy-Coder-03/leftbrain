"""An agent may propose putting privileges down. Only its owner may action it."""

from starlette.testclient import TestClient

from leftbrain.keys import KeyStore
from leftbrain.oauth.store import OAuthStore
from leftbrain.scopes import narrows, parse_scope
from leftbrain.serve import build_app
from leftbrain.web import auth
from leftbrain.web.config import WebConfig

BASE = "https://leftbrain.test"
SECRET = "test-secret-0123456789"
USER = auth.User(login="octo", email="octo@example.com", avatar_url=None)
OTHER = auth.User(login="mallory", email="mallory@example.com", avatar_url=None)
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def make_app(tmp_path):
    cfg = WebConfig(client_id=None, client_secret=None, secret=SECRET,
                    base_url=BASE, open_signup=False)
    return build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"), web_config=cfg)


def propose(client, credential, tools):
    return client.post("/keys/me/scope", json={"tools": tools},
                       headers={"Authorization": f"Bearer {credential}"})


def path_of(approve_url):
    return "/" + approve_url.split("/", 3)[3]


def sign_in(c, user=USER):
    c.cookies.set(auth.SESSION_COOKIE, auth.sign_session(SECRET, user))
    return auth.csrf_token(SECRET, user)


def decide(c, path, approve=True, user=USER):
    form = {"csrf": sign_in(c, user)}
    if approve:
        form["approve"] = "1"
    return c.post(path, data=form, follow_redirects=False)


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


# -- the rule itself --------------------------------------------------------


def test_narrows_accepts_a_subset_and_refuses_a_superset():
    assert narrows(None, parse_scope(["math"]))  # unscoped: anything is narrower
    assert narrows(parse_scope(["math", "convert"]), parse_scope(["math"]))
    assert narrows(parse_scope(["math"]), parse_scope(["math"]))  # equal is not wider
    assert narrows(parse_scope(["numbers"]), parse_scope(["numbers:round"]))
    assert not narrows(parse_scope(["math"]), parse_scope(["math", "convert"]))
    assert not narrows(parse_scope(["numbers:round"]), parse_scope(["numbers"]))
    assert not narrows(parse_scope(["numbers:round"]), parse_scope(["numbers:round", "numbers:format"]))
    assert narrows(parse_scope(["numbers:round", "numbers:format"]), parse_scope(["numbers:round"]))


# -- proposing --------------------------------------------------------------


def test_a_proposal_changes_nothing_until_a_human_approves(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, info = store.create("a@b.co", scope=parse_scope(["math", "convert"]))
    with TestClient(make_app(tmp_path)) as c:
        r = propose(c, raw, ["math"])
        assert r.status_code == 202
        result = r.json()["result"]
        assert result["status"] == "pending_approval"
        assert result["approve_url"].startswith(f"{BASE}/keys/scope-request/")
        assert "approve at" in result["tell_your_user"].lower()
        assert result["expires_in"] == 900 and result["check"] == "GET /keys/me"
    assert store.get_by_prefix(info.prefix).scope.allows("convert", "units")


def test_a_widening_is_refused_at_once_and_never_becomes_a_request(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, info = store.create("a@b.co", scope=parse_scope(["math"]))
    with TestClient(make_app(tmp_path)) as c:
        r = propose(c, raw, ["math", "convert"])
        assert r.status_code == 403
        body = r.json()
        assert body["error"] == "forbidden"
        assert "narrow" in body["message"] and "/dashboard" in body["message"]
    assert not store.get_by_prefix(info.prefix).scope.allows("convert", "units")


def test_an_oauth_token_proposes_the_same_way(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    _, info = store.create_for_owner(USER.email, "mine", scope=parse_scope(["math", "convert"]))
    row = store.db.one("SELECT key_hash FROM keys WHERE prefix=?", (info.prefix,))
    OAuthStore(store).save_token("tok-1", kind="access", client_id="c1",
                                 key_hash=row["key_hash"], scopes=["mcp"], resource=None, ttl=3600)
    with TestClient(make_app(tmp_path)) as c:
        assert propose(c, "tok-1", ["math"]).status_code == 202
        assert propose(c, "tok-1", ["math", "convert", "text"]).status_code == 403


def test_an_unauthenticated_proposal_is_refused(tmp_path):
    KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET).create("a@b.co")
    with TestClient(make_app(tmp_path)) as c:
        assert c.post("/keys/me/scope", json={"tools": ["math"]}).status_code == 401


def test_an_empty_or_unknown_tool_list_is_a_clear_error(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, info = store.create("a@b.co")
    with TestClient(make_app(tmp_path)) as c:
        empty = propose(c, raw, [])
        assert empty.status_code == 400 and "at least one tool" in empty.json()["message"]
        unknown = propose(c, raw, ["nosuchtool"])
        assert unknown.status_code == 400 and "nosuchtool" in unknown.json()["message"]
    assert store.get_by_prefix(info.prefix).scope is None  # neither became a narrowing


# -- approving --------------------------------------------------------------


def test_approving_applies_it_and_the_next_call_is_narrower(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, info = store.create_for_owner(USER.email, "mine", scope=parse_scope(["math", "convert"]))
    with TestClient(make_app(tmp_path)) as c:
        url = propose(c, raw, ["math"]).json()["result"]["approve_url"]
        assert decide(c, path_of(url)).status_code in (200, 302, 303)
        listed = c.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {raw}"}, json=LIST)
        assert tool_names(listed) == ["math"]
    assert not store.get_by_prefix(info.prefix).scope.allows("convert", "units")


def test_the_approval_page_shows_the_change_with_call_counts(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, _ = store.create_for_owner(USER.email, "mine", scope=parse_scope(["math", "convert"]))
    with TestClient(make_app(tmp_path)) as c:
        c.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {raw}"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "math", "arguments": {"expr": "2+2"}}})
        url = propose(c, raw, ["math"]).json()["result"]["approve_url"]
        sign_in(c)
        page = c.get(path_of(url))
        assert page.status_code == 200
        assert "convert" in page.text          # what is being given up
        assert "0 calls" in page.text          # and the argument for giving it up
        assert "1 call" in page.text
        assert page.headers["x-frame-options"] == "DENY"


def test_declining_discards_the_proposal(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, info = store.create_for_owner(USER.email, "mine", scope=parse_scope(["math", "convert"]))
    with TestClient(make_app(tmp_path)) as c:
        path = path_of(propose(c, raw, ["math"]).json()["result"]["approve_url"])
        decide(c, path, approve=False)
        assert store.get_by_prefix(info.prefix).scope.allows("convert", "units")
        assert decide(c, path).status_code == 404  # and it is spent


def test_a_proposal_is_single_use(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, _ = store.create_for_owner(USER.email, "mine", scope=parse_scope(["math", "convert"]))
    with TestClient(make_app(tmp_path)) as c:
        path = path_of(propose(c, raw, ["math"]).json()["result"]["approve_url"])
        assert decide(c, path).status_code in (200, 302, 303)
        assert decide(c, path).status_code == 404


def test_only_the_owner_may_see_or_action_it(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, _ = store.create_for_owner(USER.email, "mine", scope=parse_scope(["math", "convert"]))
    with TestClient(make_app(tmp_path)) as c:
        path = path_of(propose(c, raw, ["math"]).json()["result"]["approve_url"])
        assert c.get(path, follow_redirects=False).status_code in (302, 303)  # signed out
        sign_in(c, OTHER)
        # 404 rather than 403: a forbidden would confirm the request exists
        assert c.get(path).status_code == 404
        assert decide(c, path, user=OTHER).status_code == 404


def test_a_stale_proposal_that_became_a_widening_is_refused_at_approval(tmp_path):
    """The owner narrowed further meanwhile, so the pending proposal is now wider."""
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, info = store.create_for_owner(USER.email, "mine", scope=parse_scope(["math", "convert"]))
    with TestClient(make_app(tmp_path)) as c:
        path = path_of(propose(c, raw, ["math", "convert"]).json()["result"]["approve_url"])
        store.set_scope(info.prefix, parse_scope(["math"]))
        assert decide(c, path).status_code == 409
    assert not store.get_by_prefix(info.prefix).scope.allows("convert", "units")


def test_the_approval_page_refuses_a_forged_csrf_token(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, info = store.create_for_owner(USER.email, "mine", scope=parse_scope(["math", "convert"]))
    with TestClient(make_app(tmp_path)) as c:
        path = path_of(propose(c, raw, ["math"]).json()["result"]["approve_url"])
        sign_in(c)
        assert c.post(path, data={"csrf": "forged", "approve": "1"}).status_code == 403
    assert store.get_by_prefix(info.prefix).scope.allows("convert", "units")


def test_an_unknown_request_id_is_404(tmp_path):
    KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET).create_for_owner(USER.email, "mine")
    with TestClient(make_app(tmp_path)) as c:
        sign_in(c)
        assert c.get("/keys/scope-request/never-issued").status_code == 404


def test_revoking_the_key_takes_its_pending_proposals(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, info = store.create_for_owner(USER.email, "mine", scope=parse_scope(["math", "convert"]))
    with TestClient(make_app(tmp_path)) as c:
        path = path_of(propose(c, raw, ["math"]).json()["result"]["approve_url"])
        store.revoke(info.prefix)
        sign_in(c)
        assert c.get(path).status_code == 404
