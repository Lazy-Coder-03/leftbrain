"""Per-tool call counts: what turns "then narrow it" into a decision instead of a guess."""

from starlette.testclient import TestClient

from leftbrain.keys import KeyStore
from leftbrain.scopes import parse_scope
from leftbrain.serve import build_app
from leftbrain.web import auth
from leftbrain.web.config import WebConfig

SECRET = "test-secret-0123456789"
USER = auth.User(login="octo", email="octo@example.com", avatar_url=None)
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def make_app(tmp_path):
    cfg = WebConfig(client_id=None, client_secret=None, secret=SECRET,
                    base_url="https://leftbrain.test", open_signup=False)
    return build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"), web_config=cfg)


def call(client, raw, name, **arguments):
    return client.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {raw}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })


def signed_in(app):
    c = TestClient(app)
    c.cookies.set(auth.SESSION_COOKIE, auth.sign_session(SECRET, USER))
    return c


def test_counts_start_empty(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    _, info = store.create("a@b.co")
    assert store.tool_counts(info.prefix) == {}


def test_a_call_is_counted_against_its_tool_and_its_key(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw_a, a = store.create("a@b.co")
    raw_b, b = store.create("a@b.co")
    with TestClient(make_app(tmp_path)) as c:
        call(c, raw_a, "math", expr="2+2")
        call(c, raw_a, "math", expr="3+3")
        call(c, raw_a, "convert", mode="units", value=1, from_unit="m", to_unit="cm")
        call(c, raw_b, "math", expr="4+4")
    assert store.tool_counts(a.prefix) == {"math": 2, "convert": 1}
    assert store.tool_counts(b.prefix) == {"math": 1}


def test_a_call_refused_by_scope_is_not_counted_as_usage(tmp_path):
    """It never ran, so counting it would put a number beside a tool the key cannot reach."""
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, info = store.create("a@b.co", scope=parse_scope(["math"]))
    with TestClient(make_app(tmp_path)) as c:
        call(c, raw, "math", expr="2+2")
        refused = call(c, raw, "convert", mode="units", value=1, from_unit="m", to_unit="cm")
        assert "forbidden" in refused.text
    assert store.tool_counts(info.prefix) == {"math": 1}


def test_a_token_counts_against_the_key_it_names(tmp_path):
    from leftbrain.oauth.store import OAuthStore

    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    _, info = store.create("a@b.co")
    row = store.db.one("SELECT key_hash FROM keys WHERE prefix=?", (info.prefix,))
    OAuthStore(store).save_token("tok-1", kind="access", client_id="c1",
                                 key_hash=row["key_hash"], scopes=["mcp"], resource=None, ttl=3600)
    with TestClient(make_app(tmp_path)) as c:
        call(c, "tok-1", "math", expr="2+2")
    assert store.tool_counts(info.prefix) == {"math": 1}


def test_revoking_a_key_takes_its_counts_with_it(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, info = store.create("a@b.co")
    with TestClient(make_app(tmp_path)) as c:
        call(c, raw, "math", expr="2+2")
    assert store.tool_counts(info.prefix) == {"math": 1}
    store.revoke(info.prefix)
    assert store.tool_counts(info.prefix) == {}


def test_the_scope_editor_shows_a_count_beside_every_tool(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, info = store.create_for_owner(USER.email, "mine")
    app = make_app(tmp_path)
    with signed_in(app) as c:
        call(c, raw, "math", expr="2+2")
        page = c.get(f"/dashboard/keys/{info.prefix}/scope")
        assert page.status_code == 200
        assert "1 call" in page.text     # math, called once
        assert "0 calls" in page.text    # and a tool it has never reached for


def test_counting_never_costs_the_caller_their_answer(tmp_path):
    """A counter is not worth failing a request over."""
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, info = store.create("a@b.co")
    with TestClient(make_app(tmp_path)) as c:
        store.db.run("DROP TABLE tool_usage")
        r = call(c, raw, "math", expr="2+2")
        assert r.status_code == 200 and "4" in r.text
    assert store.tool_counts(info.prefix) == {}
