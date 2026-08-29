"""A scope that grants nothing: it must answer promptly and explain itself honestly."""

import pytest
from starlette.testclient import TestClient

from leftbrain.keys import KeyStore
from leftbrain.scopes import CATALOGUE, denial, parse_scope
from leftbrain.serve import build_app
from leftbrain.web import auth
from leftbrain.web.config import WebConfig

SECRET = "test-secret-0123456789"
USER = auth.User(login="octo", email="octo@example.com", avatar_url=None)
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

#: A scope naming a tool this build does not ship — a key scoped to `files` on a server
#: started without the files extra. `strict=False` is how the store loads such a row.
GHOST = parse_scope({"tools": {"files": None}}, strict=False)


def make_app(tmp_path):
    cfg = WebConfig(client_id=None, client_secret=None, secret=SECRET,
                    base_url="https://leftbrain.test", open_signup=False)
    return build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"), web_config=cfg)


def test_the_fixture_really_names_nothing_this_build_has():
    assert "files" not in CATALOGUE and GHOST is not None and GHOST.tools == {"files": None}


# -- an empty selection is not "everything" ---------------------------------


def test_an_empty_selection_is_never_read_as_every_tool():
    """The one mistake here that fails open."""
    with pytest.raises(ValueError, match="at least one tool"):
        parse_scope([])
    with pytest.raises(ValueError, match="at least one tool"):
        parse_scope({"tools": {}})


# -- what such a key does ---------------------------------------------------


def test_a_scope_that_matches_nothing_lists_no_tools_and_does_not_hang(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, _ = store.create("a@b.co", scope=GHOST)
    with TestClient(make_app(tmp_path)) as c:
        r = c.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {raw}"}, json=LIST)
        assert r.status_code == 200
        assert '"tools":[]' in r.text.replace(" ", "")


def test_a_call_under_such_a_scope_is_refused_without_naming_a_phantom_tool(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, _ = store.create("a@b.co", scope=GHOST)
    with TestClient(make_app(tmp_path)) as c:
        r = c.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {raw}"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "math", "arguments": {"expr": "2+2"}},
        })
        assert r.status_code == 200
        assert "does not provide" in r.text and "/dashboard" in r.text
        assert "allowed: files" not in r.text  # never offer a tool nobody here can call


def test_the_refusal_wording_is_unchanged_when_some_tools_do_exist():
    message = denial(parse_scope(["math"]), "convert", "units")["message"]
    assert message == "this key may not call convert; allowed: math"


def test_a_scope_naming_no_real_tool_says_so(tmp_path):
    message = denial(GHOST, "math", "eval")["message"]
    assert "this server does not provide" in message and "files" in message
    assert "/dashboard" in message


def test_a_mode_refusal_is_unchanged():
    scope = parse_scope(["numbers:round"])
    assert denial(scope, "numbers", "format")["message"] == "this key may not call numbers mode 'format'; allowed: round"


# -- and what its owner sees ------------------------------------------------


def test_allows_nothing_is_true_only_for_such_a_key(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    _, plain = store.create("a@b.co")
    _, scoped = store.create("a@b.co", scope=parse_scope(["math"]))
    _, ghost = store.create("a@b.co", scope=GHOST)
    assert not store.get_by_prefix(plain.prefix).allows_nothing
    assert not store.get_by_prefix(scoped.prefix).allows_nothing
    assert store.get_by_prefix(ghost.prefix).allows_nothing


def test_the_dashboard_warns_that_such_a_key_can_call_nothing(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    store.create_for_owner(USER.email, "ghost", scope=GHOST)
    store.create_for_owner(USER.email, "fine", scope=parse_scope(["math"]))
    with TestClient(make_app(tmp_path)) as c:
        c.cookies.set(auth.SESSION_COOKIE, auth.sign_session(SECRET, USER))
        page = c.get("/dashboard")
        assert page.status_code == 200
        assert page.text.count("can call nothing") == 1  # the affected row, and only it


def test_keys_me_says_so_too_so_an_agent_learns_why_it_sees_no_tools(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    raw, _ = store.create("a@b.co", scope=GHOST)
    ok, _ = store.create("a@b.co", scope=parse_scope(["math"]))
    with TestClient(make_app(tmp_path)) as c:
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {raw}"}).json()["result"]["allows_nothing"] is True
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {ok}"}).json()["result"]["allows_nothing"] is False
