"""The `holidays` tool was retired in 0.5.0 (#80, #86, #91); what must stay true afterwards.

It answered "which festival is when" from tables nobody could vouch for. `datetime` keeps the
public-holiday calendar for working-day arithmetic, because that part of the dataset is what
it says it is; the festival layer is gone rather than kept as a source of confident wrong dates.
"""

import json

from starlette.testclient import TestClient

from leftbrain import TOOLS
from leftbrain.core.datetimex import datetime_tool
from leftbrain.keys import KeyStore
from leftbrain.mcp_server import server
from leftbrain.scopes import CATALOGUE, parse_scope
from leftbrain.serve import build_app
from leftbrain.web.config import WebConfig

SECRET = "test-secret-0123456789"


def test_the_tool_is_gone_everywhere_a_tool_is_listed():
    assert "holidays" not in CATALOGUE and len(CATALOGUE) == 17
    assert "holidays" not in TOOLS
    assert "holidays" not in [t.name for t in server._tool_manager.list_tools()]
    from leftbrain import toolref

    assert "holidays" not in {t.name for t in toolref.CATALOGUE} and "holidays" not in toolref.specs()


def test_the_docs_no_longer_publish_it(tmp_path):
    cfg = WebConfig(client_id=None, client_secret=None, secret=SECRET, base_url=None, open_signup=False)
    with TestClient(build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"), web_config=cfg)) as c:
        assert c.get("/docs/tools/holidays").status_code == 404
        listed = c.get("/docs/tools", headers={"Accept": "application/json"}).json()
        names = {t["name"] for t in next(v for v in listed.values() if isinstance(v, list))}
        assert "holidays" not in names and len(names) == 17


def test_a_key_scoped_to_the_retired_tool_still_loads_and_lists_what_it_may_call(tmp_path):
    """Scopes stored before the retirement name it; they must keep loading, minus the tool."""
    store = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    key, info = store.create("a@b.co", scope=parse_scope("math"))
    store.db.run("UPDATE keys SET scope=? WHERE prefix=?", (json.dumps({"tools": {"math": None, "holidays": ["list", "check"]}}), info.prefix))
    loaded = store.get_by_prefix(info.prefix).scope
    assert loaded.allows("math", "eval") and loaded.summary() == "2 tools"
    cfg = WebConfig(client_id=None, client_secret=None, secret=SECRET, base_url=None, open_signup=False)
    with TestClient(build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"), web_config=cfg)) as c:
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                   headers={"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream"})
        assert r.status_code == 200
        body = json.loads(r.text.split("data: ", 1)[1].split("\r\n")[0]) if r.text.startswith("event:") else r.json()
        assert [t["name"] for t in body["result"]["tools"]] == ["math"]


def test_a_new_scope_naming_it_is_refused_with_the_tools_that_exist():
    try:
        parse_scope("math,holidays:list", strict=True)
    except ValueError as e:
        assert "holidays" in str(e) and "datetime" in str(e)
    else:
        raise AssertionError("a scope naming a retired tool should be refused when strict")


def test_business_days_still_skip_public_holidays_by_region():
    """The calendar survived: 2 October 2026 (Gandhi Jayanti, a Friday) is not a working day in India."""
    r = datetime_tool("business_days", start="2026-09-28", end="2026-10-09", region="IN")
    assert r["ok"], r
    skipped = {h["date"] for h in r["result"]["holidays_skipped"]}
    assert "2026-10-02" in skipped
    assert r["result"]["business_days"] == 9


def test_a_festival_anchor_is_refused_with_the_reason():
    r = datetime_tool("add", value={"festival": "Saptami", "year": 2026, "region": "IN", "subdiv": "WB"}, amount=-3, unit="days")
    assert r["ok"] is False and r["error"] == "invalid_input"
    assert "retired" in r["message"] and "holidays" in r["message"]
