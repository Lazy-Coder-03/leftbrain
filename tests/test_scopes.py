"""Per-key tool scopes (#27): which tools, and which of their modes, a key may call.

A scope is a whitelist stored on the key. It is enforced twice: at call time, where a
disallowed tool or mode returns the contract's ``forbidden`` error, and on ``tools/list``,
where the key sees only the tools it may call. A key with no scope behaves exactly as before.
"""

import json
import re
import sqlite3

import pytest
from starlette.testclient import TestClient
from test_toolref import MODULE_MODES
from test_web import csrf_from, login_via_github, new_key, oauth_app

from leftbrain.keys import KeyStore
from leftbrain.scopes import CATALOGUE, Scope, current_scope, enforce, parse_scope, summarize
from leftbrain.serve import build_app
from leftbrain.web.config import WebConfig

ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def rpc(c, path, key, method, **params):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        body["params"] = params
    return c.post(path, headers={"Authorization": f"Bearer {key}", **ACCEPT}, json=body)


def result_of(response):
    """The JSON-RPC message inside either wire form: a plain JSON body or one SSE ``data:`` line."""
    text = response.text
    if response.headers["content-type"].startswith("text/event-stream"):
        lines = [ln for ln in text.splitlines() if ln.startswith("data:")]
        assert len(lines) == 1, text
        return json.loads(lines[0][5:])
    return json.loads(text)


def tool_names(response):
    return [t["name"] for t in result_of(response)["result"]["tools"]]


def contract(response):
    msg = result_of(response)
    assert "error" not in msg, msg  # a scope refusal is a result, never a transport error
    return msg["result"]


def keyed_app(tmp_path, **kw):
    cfg = WebConfig(None, None, "s" * 20, None, True)
    return build_app(include_external=True, keys_db=str(tmp_path / "k.sqlite3"), web_config=cfg, **kw)


# --- the catalogue and the pure scope type ------------------------------------


def test_catalogue_mirrors_the_modules_and_covers_the_external_tools():
    assert dict(CATALOGUE) == {name: tuple(modes) for name, modes in MODULE_MODES.items()}
    assert list(CATALOGUE)[:2] == ["math", "datetime"] and list(CATALOGUE)[-4:] == ["weather", "fx_rate", "geo", "url_check"]
    assert CATALOGUE["fx_rate"] == () and CATALOGUE["url_check"] == ()


def test_parse_scope_round_trips_dict_json_and_text_forms():
    s = parse_scope({"tools": {"math": None, "holidays": ["list", "check"]}})
    assert isinstance(s, Scope) and s.tools == {"math": None, "holidays": ("list", "check")}
    assert json.loads(s.to_json()) == {"tools": {"math": None, "holidays": ["list", "check"]}}
    assert parse_scope(s.to_json()) == s  # the stored form comes back identical
    assert parse_scope(s.to_dict()) == s  # so does the /keys/me form (the bare map)
    assert parse_scope("math,holidays:list+check") == s
    assert parse_scope(" math , holidays : list + check ") == s  # whitespace is noise
    assert parse_scope(["math", "holidays:list", "holidays:check"]) == s  # the dashboard's checkbox values
    assert parse_scope(["holidays:list"]) == Scope({"holidays": ("list",)})  # a mode implies its tool


def test_parse_scope_none_means_every_tool():
    assert parse_scope(None) is None and parse_scope("") is None and parse_scope("all") is None
    assert parse_scope({"tools": None}) is None
    # every tool with every mode is no restriction at all, so it is stored as none
    assert parse_scope(",".join(CATALOGUE)) is None
    assert parse_scope([f"{t}:{m}" for t, modes in CATALOGUE.items() for m in modes] + list(CATALOGUE)) is None
    # naming all of one tool's modes is the same as naming the tool
    assert parse_scope("holidays:list+check+next+countries+subdivisions") == Scope({"holidays": None})


@pytest.mark.parametrize(
    "bad, offender",
    [
        ("math,nope", "nope"),
        ("holidays:list+fly", "fly"),
        ({"tools": {"weather": ["current", "yesterday"]}}, "yesterday"),
        ({"tools": {"fx_rate": ["spot"]}}, "fx_rate"),  # has no modes to pick from
        (["geo:teleport"], "teleport"),
        ({"tools": "math"}, "tools"),
        ("holidays:", "holidays"),
    ],
)
def test_parse_scope_names_the_offender(bad, offender):
    with pytest.raises(ValueError) as e:
        parse_scope(bad)
    assert offender in str(e.value)


def test_parse_scope_refuses_an_empty_list():
    with pytest.raises(ValueError, match="at least one tool"):
        parse_scope([])
    with pytest.raises(ValueError, match="at least one tool"):
        parse_scope({"tools": {}})


def test_allows_and_summary():
    s = parse_scope("math,datetime,holidays:list+check")
    assert s.allows("math", "eval") and s.allows("math", None) and s.allows("datetime", "now")
    assert s.allows("holidays", "list") and not s.allows("holidays", "next") and not s.allows("holidays", None)
    assert not s.allows("numbers", "compare") and not s.allows("fx_rate", None)
    assert parse_scope("fx_rate").allows("fx_rate", None)
    assert s.summary() == "3 tools" and summarize(s) == "3 tools"
    assert summarize(None) == "all tools"
    assert parse_scope("holidays:list+check").summary() == "holidays: list, check"
    assert parse_scope("holidays").summary() == "holidays"
    assert s.listing() == "math, datetime, holidays (list, check)"


def test_enforce_reads_the_context_and_returns_the_contract_error():
    calls = []

    @enforce("holidays")
    def holidays(mode: str = "list", region: str | None = None):
        """mode: list | check"""
        calls.append((mode, region))
        return {"ok": True, "result": []}

    assert holidays.__doc__ == "mode: list | check" and holidays.__name__ == "holidays"
    assert holidays(region="IN")["ok"] and calls == [("list", "IN")]  # no scope in context: untouched
    token = current_scope.set(parse_scope("math,holidays:check"))
    try:
        assert holidays(mode="check", region="IN")["ok"]
        out = holidays(mode="next", region="IN")
        assert out == {"ok": False, "error": "forbidden", "message": "this key may not call holidays mode 'next'; allowed: check", "retryable": False}
        out = holidays(region="IN")  # the default mode counts as a mode
        assert out["error"] == "forbidden" and "mode 'list'" in out["message"]
        assert holidays("check")["ok"]  # positional mode is seen too
    finally:
        current_scope.reset(token)
    token = current_scope.set(parse_scope("math"))
    try:
        out = holidays(mode="check")
        assert out["error"] == "forbidden" and out["message"] == "this key may not call holidays; allowed: math"
    finally:
        current_scope.reset(token)
    assert calls == [("list", "IN"), ("check", "IN"), ("check", None)]


def test_wrapping_left_the_published_schemas_and_docstrings_alone():
    from mcp.server.mcpserver.utilities.func_metadata import func_metadata

    from leftbrain.external.mcp_server import server as external
    from leftbrain.mcp_server import server as core

    for server in (core, external):
        for tool in server._tool_manager.list_tools():
            assert tool.fn.__wrapped__ is not None, tool.name  # every tool is enforced
            bare = func_metadata(tool.fn.__wrapped__, skip_names=["ctx"])
            assert tool.fn_metadata.arg_model.model_json_schema() == bare.arg_model.model_json_schema(), tool.name
            assert tool.description == (tool.fn.__wrapped__.__doc__ or ""), tool.name


# --- the store --------------------------------------------------------------


def test_store_create_set_and_list_with_a_scope(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"))
    scope = parse_scope("math,holidays:list+check")
    raw, info = store.create("a@b.co", scope=scope)
    assert info.scope == scope and info.to_dict()["tools"] == {"math": None, "holidays": ["list", "check"]}
    assert store.get_by_prefix(info.prefix).scope == scope
    assert store.verify_and_count(raw).key.scope == scope
    _, open_info = store.create("a@b.co")
    assert open_info.scope is None and open_info.to_dict()["tools"] is None
    assert {k.prefix: k.scope for k in store.list("a@b.co")} == {info.prefix: scope, open_info.prefix: None}
    assert store.set_scope(info.prefix, parse_scope("numbers"))
    assert store.get_by_prefix(info.prefix).scope == Scope({"numbers": None})
    assert store.set_scope(info.prefix, None) and store.get_by_prefix(info.prefix).scope is None
    assert not store.set_scope("lblz_nosuch1", None)
    raw2, info2 = store.create_for_owner("a@b.co", "scoped", scope=scope)
    assert raw2 and info2.scope == scope


def test_migration_adds_scope_to_an_existing_database(tmp_path):
    path = str(tmp_path / "old.sqlite3")
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE keys (key_hash TEXT PRIMARY KEY, prefix TEXT NOT NULL, owner TEXT NOT NULL,"
        " note TEXT, created_at TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0,"
        " daily_quota INTEGER NOT NULL, rpm INTEGER NOT NULL, last_used TEXT, secret_enc TEXT, expires_at TEXT)"
    )
    con.execute("INSERT INTO keys VALUES ('h', 'lblz_oldkey01', 'a@b.co', NULL, '2026-01-01T00:00:00+00:00', 0, 5, 5, NULL, NULL, NULL)")
    con.commit()
    con.close()

    store = KeyStore(path)
    assert "scope" in {c["name"] for c in store.db.all("PRAGMA table_info(keys)")}
    old = store.get_by_prefix("lblz_oldkey01")
    assert old.scope is None and old.to_dict()["tools"] is None  # pre-scope keys keep every tool
    KeyStore(path)  # guarded: re-running is a no-op


def test_a_stored_scope_naming_a_retired_tool_still_loads(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"))
    _, info = store.create("a@b.co", scope=parse_scope("math"))
    store.db.run("UPDATE keys SET scope=? WHERE prefix=?", (json.dumps({"tools": {"math": None, "abacus": ["count"]}}), info.prefix))
    loaded = store.get_by_prefix(info.prefix).scope
    assert loaded.allows("math", "eval") and loaded.tools["abacus"] == ("count",) and loaded.summary() == "2 tools"


# --- enforcement over HTTP ----------------------------------------------------


@pytest.mark.parametrize("json_response", [False, True], ids=["sse", "json"])
def test_scoped_key_over_http(tmp_path, json_response):
    with TestClient(keyed_app(tmp_path, json_response=json_response)) as c:
        store = KeyStore(str(tmp_path / "k.sqlite3"))
        scoped, _ = store.create("a@b.co", scope=parse_scope("numbers,holidays:list+check,weather"))
        open_key, _ = store.create("a@b.co")

        r = rpc(c, "/mcp", scoped, "tools/list")
        assert r.status_code == 200 and tool_names(r) == ["holidays", "numbers"]
        assert r.headers["content-type"].startswith("application/json" if json_response else "text/event-stream")
        if json_response:
            assert int(r.headers["content-length"]) == len(r.content)
        else:
            assert r.text.startswith("event: message\r\ndata: ") and r.text.endswith("\r\n\r\n")
        assert tool_names(rpc(c, "/external/mcp", scoped, "tools/list")) == ["weather"]
        assert len(tool_names(rpc(c, "/mcp", open_key, "tools/list"))) == 14
        assert len(tool_names(rpc(c, "/external/mcp", open_key, "tools/list"))) == 4

        ok = contract(rpc(c, "/mcp", scoped, "tools/call", name="numbers", arguments={"mode": "compare", "values": ["9.11", "9.9"]}))
        assert ok["isError"] is False and ok["structuredContent"]["ok"] and ok["structuredContent"]["result"]["max"]["input"] == "9.9"
        assert contract(rpc(c, "/mcp", scoped, "tools/call", name="holidays", arguments={"mode": "list", "region": "IN", "year": 2026}))["structuredContent"]["ok"]

        denied = contract(rpc(c, "/mcp", scoped, "tools/call", name="math", arguments={"expr": "1+1"}))
        assert denied["isError"] is False
        contract_only = {k: v for k, v in denied["structuredContent"].items() if k != "meta"}
        assert contract_only == {"ok": False, "error": "forbidden", "message": "this key may not call math; allowed: numbers, holidays (list, check), weather", "retryable": False}
        # every response now carries what it cost, and what the key has left (#28 §6)
        meta = denied["structuredContent"]["meta"]
        assert meta["tool"] == "math" and meta["version"] and isinstance(meta["latency_ms"], int)
        assert meta["quota"]["daily_quota"] == 1000 and meta["request_id"]
        assert json.loads(denied["content"][0]["text"])["error"] == "forbidden"
        denied = contract(rpc(c, "/mcp", scoped, "tools/call", name="holidays", arguments={"mode": "next", "region": "IN"}))
        assert denied["structuredContent"]["error"] == "forbidden" and "mode 'next'" in denied["structuredContent"]["message"]
        assert "allowed: list, check" in denied["structuredContent"]["message"]
        denied = contract(rpc(c, "/external/mcp", scoped, "tools/call", name="fx_rate", arguments={"base": "USD", "to": "INR"}))
        assert denied["structuredContent"]["error"] == "forbidden"

        assert contract(rpc(c, "/mcp", open_key, "tools/call", name="math", arguments={"expr": "1+1"}))["structuredContent"]["ok"]
        assert "x-ratelimit-remaining-today" in rpc(c, "/mcp", scoped, "tools/list").headers

        me = c.get("/keys/me", headers={"Authorization": f"Bearer {scoped}"}).json()["result"]
        assert me["tools"] == {"numbers": None, "holidays": ["list", "check"], "weather": None}
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {open_key}"}).json()["result"]["tools"] is None


def test_scope_changes_apply_on_the_next_call(tmp_path):
    with TestClient(keyed_app(tmp_path)) as c:
        store = KeyStore(str(tmp_path / "k.sqlite3"))
        raw, info = store.create("a@b.co", scope=parse_scope("math"))
        assert contract(rpc(c, "/mcp", raw, "tools/call", name="math", arguments={"expr": "1+1"}))["structuredContent"]["ok"]
        store.set_scope(info.prefix, parse_scope("numbers"))
        assert contract(rpc(c, "/mcp", raw, "tools/call", name="math", arguments={"expr": "1+1"}))["structuredContent"]["error"] == "forbidden"
        assert tool_names(rpc(c, "/mcp", raw, "tools/list")) == ["numbers"]
        store.set_scope(info.prefix, None)
        assert contract(rpc(c, "/mcp", raw, "tools/call", name="math", arguments={"expr": "1+1"}))["structuredContent"]["ok"]


def test_static_key_and_unscoped_key_bodies_pass_through_untouched(tmp_path):
    cfg = WebConfig(None, None, "s" * 20, None, True)
    with TestClient(build_app(include_external=False, api_key="s3cret", keys_db=str(tmp_path / "k.sqlite3"), web_config=cfg)) as c:
        raw, _ = KeyStore(str(tmp_path / "k.sqlite3")).create("a@b.co")
        via_static = rpc(c, "/mcp", "s3cret", "tools/list")
        via_key = rpc(c, "/mcp", raw, "tools/list")
        assert via_static.content == via_key.content and len(tool_names(via_key)) == 14


def test_a_scoped_key_whose_body_is_not_a_tools_list_is_replayed_intact(tmp_path):
    with TestClient(keyed_app(tmp_path)) as c:
        raw, _ = KeyStore(str(tmp_path / "k.sqlite3")).create("a@b.co", scope=parse_scope("numbers"))
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
        r = c.post("/mcp", json=init, headers={"Authorization": f"Bearer {raw}", **ACCEPT})
        assert r.status_code == 200 and result_of(r)["result"]["serverInfo"]["name"] == "leftbrain"
        r = c.post("/mcp", content=b"{not json", headers={"Authorization": f"Bearer {raw}", **ACCEPT})
        assert r.status_code == 400  # the transport's own verdict, not ours


# --- the dashboard --------------------------------------------------------------


def test_dashboard_create_form_lists_every_tool_and_mode(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        page = c.get("/dashboard").text
        form = page.split('action="/dashboard/keys"')[1].split("</form>")[0]
        assert "<details" in form and 'name="scope_form"' in form
        for tool, modes in CATALOGUE.items():
            assert f'name="scope" value="{tool}" data-tool="{tool}" checked' in form, tool
            for m in modes:
                assert f'name="scope" value="{tool}:{m}" data-of="{tool}" checked' in form, (tool, m)
        assert "all tools" in form


def test_dashboard_creates_a_scoped_key_and_shows_its_summary(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        csrf = csrf_from(c.get("/dashboard").text)
        r = c.post("/dashboard/keys", data={"name": "scoped", "csrf": csrf, "scope_form": "1", "scope": ["holidays", "holidays:list", "holidays:check", "numbers"]})
        assert r.status_code == 200
        key = r.text.split('<code id="new-key">')[1].split("</code>")[0]
        me = c.get("/keys/me", headers={"Authorization": f"Bearer {key}"}).json()["result"]
        assert me["tools"] == {"holidays": ["list", "check"], "numbers": None}
        row = c.get("/dashboard").text.split(key[:13], 1)[1].split("</tr>")[0]
        assert "2 tools" in row and f'href="/dashboard/keys/{key[:13]}/scope"' in row and "Edit scope" in row
        assert 'title="holidays (list, check), numbers"' in row
        assert tool_names(rpc(c, "/mcp", key, "tools/list")) == ["holidays", "numbers"]
        # a single tool is spelled out on the row
        r = c.post("/dashboard/keys", data={"name": "one", "csrf": csrf, "scope_form": "1", "scope": ["holidays", "holidays:list"]})
        key2 = r.text.split('<code id="new-key">')[1].split("</code>")[0]
        assert "holidays: list" in c.get("/dashboard").text.split(key2[:13], 1)[1].split("</tr>")[0]
        # everything ticked is no restriction; a form without the grid (scripted post) is too
        every = {"name": "open", "csrf": csrf, "scope_form": "1", "scope": [*CATALOGUE, *[f"{t}:{m}" for t, ms in CATALOGUE.items() for m in ms]]}
        key3 = c.post("/dashboard/keys", data=every).text.split('<code id="new-key">')[1].split("</code>")[0]
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {key3}"}).json()["result"]["tools"] is None
        assert "all tools" in c.get("/dashboard").text.split(key3[:13], 1)[1].split("</tr>")[0]


def test_dashboard_mode_boxes_only_count_when_their_tool_is_ticked(tmp_path):
    """Without script support an unticked tool still posts its (ticked) mode boxes; they must not smuggle the tool back in."""
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        csrf = csrf_from(c.get("/dashboard").text)
        r = c.post("/dashboard/keys", data={"name": "x", "csrf": csrf, "scope_form": "1", "scope": ["numbers", "holidays:list"]})
        key = r.text.split('<code id="new-key">')[1].split("</code>")[0]
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {key}"}).json()["result"]["tools"] == {"numbers": None}
        # a tool ticked with none of its modes ticked means every mode
        r = c.post("/dashboard/keys", data={"name": "y", "csrf": csrf, "scope_form": "1", "scope": "holidays"})
        key = r.text.split('<code id="new-key">')[1].split("</code>")[0]
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {key}"}).json()["result"]["tools"] == {"holidays": None}
        # nothing ticked at all is refused, not silently "all tools"
        r = c.post("/dashboard/keys", data={"name": "z", "csrf": csrf, "scope_form": "1"})
        assert r.status_code == 200 and "new-key" not in r.text and "at least one tool" in r.text


def test_dashboard_edit_scope_page_and_post(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        csrf = csrf_from(c.get("/dashboard").text)
        key = new_key(c, "laptop", csrf)
        prefix = key[:13]
        assert contract(rpc(c, "/mcp", key, "tools/call", name="math", arguments={"expr": "1+1"}))["structuredContent"]["ok"]

        page = c.get(f"/dashboard/keys/{prefix}/scope")
        assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
        assert prefix in page.text and f'action="/dashboard/keys/{prefix}/scope"' in page.text
        assert page.text.count(" checked") == sum(1 + len(m) for m in CATALOGUE.values())  # an open key: everything ticked

        r = c.post(f"/dashboard/keys/{prefix}/scope", data={"csrf": csrf, "scope_form": "1", "scope": ["numbers", "holidays", "holidays:check"]}, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/dashboard" and r.headers["cache-control"] == "no-store"
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {key}"}).json()["result"]["tools"] == {"numbers": None, "holidays": ["check"]}
        assert contract(rpc(c, "/mcp", key, "tools/call", name="math", arguments={"expr": "1+1"}))["structuredContent"]["error"] == "forbidden"
        assert tool_names(rpc(c, "/mcp", key, "tools/list")) == ["holidays", "numbers"]
        assert "2 tools" in c.get("/dashboard").text.split(prefix, 1)[1].split("</tr>")[0]

        page = c.get(f"/dashboard/keys/{prefix}/scope").text  # pre-filled with the current scope
        assert 'value="numbers" data-tool="numbers" checked' in page and 'value="holidays" data-tool="holidays" checked' in page
        assert 'value="holidays:check" data-of="holidays" checked' in page and 'value="holidays:list" data-of="holidays" checked' not in page
        assert 'value="math" data-tool="math" checked' not in page and 'value="math:eval" data-of="math" checked' not in page

        # back to every tool
        r = c.post(f"/dashboard/keys/{prefix}/scope", data={"csrf": csrf, "scope_form": "1", "scope": list(CATALOGUE)}, follow_redirects=False)
        assert r.status_code == 302
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {key}"}).json()["result"]["tools"] is None
        assert contract(rpc(c, "/mcp", key, "tools/call", name="math", arguments={"expr": "1+1"}))["structuredContent"]["ok"]

        # nothing ticked is refused on the edit page too, and the key is left alone
        r = c.post(f"/dashboard/keys/{prefix}/scope", data={"csrf": csrf, "scope_form": "1"})
        assert r.status_code == 200 and "at least one tool" in r.text
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {key}"}).json()["result"]["tools"] is None


def test_dashboard_edit_scope_refuses_csrf_and_other_owners(tmp_path):
    from leftbrain.keys import KeyStore as Store

    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        csrf = csrf_from(c.get("/dashboard").text)
        key = new_key(c, "mine", csrf)
        prefix = key[:13]
        assert c.post(f"/dashboard/keys/{prefix}/scope", data={"scope": "math"}).status_code == 403
        assert c.post(f"/dashboard/keys/{prefix}/scope", data={"csrf": "bogus", "scope": "math"}).status_code == 403
        _, theirs = Store(str(tmp_path / "k.sqlite3")).create("someone@else.example")
        assert c.get(f"/dashboard/keys/{theirs.prefix}/scope").status_code == 403
        assert c.post(f"/dashboard/keys/{theirs.prefix}/scope", data={"csrf": csrf, "scope": "math"}).status_code == 403
        assert c.get("/dashboard/keys/lblz_nosuch1/scope").status_code == 403
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {key}"}).json()["result"]["tools"] is None
    with TestClient(oauth_app(tmp_path)) as c:  # signed out: the page is a redirect to login, the post too
        assert c.get(f"/dashboard/keys/{prefix}/scope", follow_redirects=False).status_code == 302
        assert c.post(f"/dashboard/keys/{prefix}/scope", data={"scope": "math"}, follow_redirects=False).status_code == 302


def test_dashboard_scope_grid_is_plain_html_with_the_behaviour_in_site_js():
    from leftbrain.web import HERE

    js = (HERE / "static" / "site.js").read_text(encoding="utf-8")
    css = (HERE / "static" / "site.css").read_text(encoding="utf-8")
    assert "data-tool" in js and "data-of" in js and ".scope" in css
    grid = (HERE / "templates" / "_scope_grid.html").read_text(encoding="utf-8")
    assert "<script" not in grid and re.search(r'type="checkbox" name="scope"', grid)


# --- the CLI --------------------------------------------------------------------


def test_cli_create_and_set_tools(tmp_path, capsys):
    from leftbrain.keys import main

    db = str(tmp_path / "k.sqlite3")
    main(["--db", db, "create", "--owner", "a@b.co", "--tools", "math,datetime,holidays:list+check"])
    out = capsys.readouterr().out
    made = json.loads(out[: out.index("\n\n")])
    assert made["tools"] == {"math": None, "datetime": None, "holidays": ["list", "check"]}
    main(["--db", db, "list"])
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["tools"] == made["tools"]
    main(["--db", db, "set", made["prefix"], "--tools", "numbers"])
    assert capsys.readouterr().out.strip() == "ok"
    assert KeyStore(db).get_by_prefix(made["prefix"]).to_dict()["tools"] == {"numbers": None}
    main(["--db", db, "set", made["prefix"], "--all-tools"])
    assert capsys.readouterr().out.strip() == "ok"
    assert KeyStore(db).get_by_prefix(made["prefix"]).to_dict()["tools"] is None
    main(["--db", db, "set", made["prefix"], "--daily", "5", "--tools", "math"])  # combines with the limits
    assert capsys.readouterr().out.strip() == "ok"
    info = KeyStore(db).get_by_prefix(made["prefix"])
    assert info.daily_quota == 5 and info.to_dict()["tools"] == {"math": None}


def test_cli_usage_errors_name_the_offending_tool_or_mode(tmp_path, capsys):
    from leftbrain.keys import main

    db = str(tmp_path / "k.sqlite3")
    with pytest.raises(SystemExit):
        main(["--db", db, "create", "--owner", "a@b.co", "--tools", "math,abacus"])
    assert "abacus" in capsys.readouterr().err
    _, info = KeyStore(db).create("a@b.co")
    with pytest.raises(SystemExit):
        main(["--db", db, "set", info.prefix, "--tools", "holidays:fly"])
    assert "fly" in capsys.readouterr().err
    for argv in (
        ["set", info.prefix, "--tools", "math", "--all-tools"],  # one or the other
        ["set", "--all", "--daily", "1", "--tools", "math"],  # scope is per key
        ["set", "--all", "--daily", "1", "--all-tools"],
    ):
        with pytest.raises(SystemExit):
            main(["--db", db, *argv])
    assert KeyStore(db).get_by_prefix(info.prefix).scope is None
