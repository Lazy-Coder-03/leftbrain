"""`/docs/tools` answers a machine as well as a person.

The reference pages are built for someone reading them. An agent deciding whether leftbrain
can answer its question should not have to scrape HTML, and `tools/list` over `/mcp` needs a
key and reports no modes. Same routes, content-negotiated, the way `/` already works.
"""

import pytest

pytest.importorskip("starlette")

from starlette.testclient import TestClient  # noqa: E402

from leftbrain import (
    __version__,  # noqa: E402
    toolref,  # noqa: E402
)
from leftbrain.serve import build_app  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEFTBRAIN_SECRET", "x" * 32)
    with TestClient(build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"))) as c:
        yield c


def test_the_index_lists_every_tool_including_the_network_ones(client):
    body = client.get("/docs/tools").json()
    assert body["count"] == 17 and body["version"] == __version__
    names = [t["name"] for t in body["tools"]]
    assert "math" in names and "url_check" in names
    assert [t["name"] for t in body["tools"] if t["network"]] == ["weather", "fx_rate", "geo", "url_check"]


def test_the_index_carries_the_contract_so_a_client_knows_what_to_expect(client):
    contract = client.get("/docs/tools").json()["contract"]
    assert contract["retryable"] == ["busy"]
    assert "too_large" in contract["errors"] and "invalid_input" in contract["errors"]


def test_a_browser_still_gets_the_page(client):
    r = client.get("/docs/tools", headers={"Accept": "text/html"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")


def test_one_tool_gives_every_mode_with_its_parameters(client):
    body = client.get("/docs/tools/finance").json()
    from leftbrain.core.finance import MODES as FINANCE_MODES

    assert [m["name"] for m in body["modes"]] == list(FINANCE_MODES)
    emi = next(m for m in body["modes"] if m["name"] == "emi")
    principal = next(p for p in emi["parameters"] if p["name"] == "principal")
    assert principal["required"] is True and principal["doc"]
    assert emi["examples"] and all("mode" in e for e in emi["examples"])


def test_a_tool_with_no_modes_documents_its_parameters_once(client):
    body = client.get("/docs/tools/url_check").json()
    assert body["network"] is True and "modes" not in body
    assert [p["name"] for p in body["parameters"]] == ["url", "method"]


def test_no_default_is_null_rather_than_the_table_s_em_dash(client):
    body = client.get("/docs/tools/finance").json()
    emi = next(m for m in body["modes"] if m["name"] == "emi")
    principal = next(p for p in emi["parameters"] if p["name"] == "principal")
    assert principal["default"] is None


def test_an_unknown_tool_is_a_contract_shaped_404(client):
    r = client.get("/docs/tools/nope")
    assert r.status_code == 404 and r.json()["ok"] is False and "nope" in r.json()["message"]


def test_the_root_endpoint_advertises_it(client):
    assert client.get("/").json()["tools"] == "/docs/tools"


def test_it_needs_no_key(client):
    """Discovery has to work before you have one."""
    assert client.get("/docs/tools").status_code == 200
    assert client.get("/docs/tools/math").status_code == 200


def test_every_tool_in_the_index_can_be_fetched(client):
    for entry in client.get("/docs/tools").json()["tools"]:
        r = client.get(entry["docs_url"])
        assert r.status_code == 200 and r.json()["name"] == entry["name"], entry["name"]


def test_an_example_argument_json_cannot_spell_is_still_sendable(client):
    """`encode.json stringify` has an example whose argument is `1e999` — it exists precisely
    because JSON cannot represent that, so the reference could not send it as a float."""
    body = client.get("/docs/tools/encode").json()
    stringify = next(m for m in body["modes"] if m["name"] == "json")
    assert any("Infinity" in str(e) for e in stringify["examples"])


def test_the_json_and_the_catalogue_cannot_drift():
    """Both are built from the same objects, so this is a guard against one growing a copy."""
    assert {t["name"] for t in toolref.catalogue_json()["tools"]} == set(toolref.tool_names())
