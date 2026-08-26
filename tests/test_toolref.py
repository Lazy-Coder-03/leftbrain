"""The tool reference documents behaviour, so the catalogue is tested against behaviour.

Every catalogued example is executed: a documented success must succeed, a documented
failure must fail with an `error`. Every mode a core module exposes must be catalogued,
so a new mode cannot ship undocumented.
"""

import pytest
from starlette.testclient import TestClient

from leftbrain.core import collections_, datetimex, encode, geo_offline, holidays_, mathx, random_
from leftbrain.core import convert as convert_mod
from leftbrain.core import numbers as numbers_mod
from leftbrain.core import text as text_mod
from leftbrain.core import validate as validate_mod
from leftbrain.serve import build_app
from leftbrain.web import toolref
from leftbrain.web.config import WebConfig
from leftbrain.web.tools_list import TOOLS

# scale has no MODES tuple: its accepted values are checked inside scale() itself.
MODULE_MODES = {
    "math": mathx.MODES,
    "datetime": datetimex.MODES,
    "scale": ("linear", "inverse"),
    "convert": convert_mod.MODES,
    "holidays": holidays_.MODES,
    "numbers": numbers_mod.MODES,
    "text": text_mod.MODES,
    "collections": collections_.MODES,
    "validate": validate_mod.MODES,
    "random": random_.MODES,
    "geo_offline": geo_offline.MODES,
    "encode": encode.MODES,
}

ALL_EXAMPLES = [
    (tool, mode, example, expect_ok)
    for tool in toolref.CATALOGUE
    for mode in tool.modes
    for example, expect_ok in [(e, True) for e in mode.examples] + [(e, False) for e in mode.failures]
]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("toolref")
    cfg = WebConfig(client_id=None, client_secret=None, secret="test-secret-0123456789", base_url=None, open_signup=False)
    app = build_app(include_external=False, keys_db=str(tmp / "k.sqlite3"), web_config=cfg)
    with TestClient(app) as c:
        yield c


def test_catalogue_covers_every_tool():
    assert [t.name for t in toolref.CATALOGUE] == [name for name, _d, _m in TOOLS]
    assert len(toolref.CATALOGUE) == 12


@pytest.mark.parametrize("tool", toolref.CATALOGUE, ids=lambda t: t.name)
def test_every_mode_of_every_tool_is_documented(tool):
    """A new mode in a core module must gain a catalogue entry or this fails."""
    documented = [m.name for m in tool.modes]
    assert documented == sorted(documented, key=lambda n: documented.index(n))  # no duplicates below
    assert len(set(documented)) == len(documented), f"duplicate mode entries for {tool.name}"
    assert set(documented) == set(MODULE_MODES[tool.name]), (
        f"{tool.name}: documented {sorted(documented)} vs implemented {sorted(MODULE_MODES[tool.name])}"
    )


@pytest.mark.parametrize("tool", toolref.CATALOGUE, ids=lambda t: t.name)
def test_every_mode_has_examples_and_a_failure_story(tool):
    for mode in tool.modes:
        assert mode.examples, f"{tool.name}.{mode.name} has no examples"
        assert len(mode.examples) >= 2 or mode.name in ("countries", "jwt_decode"), (
            f"{tool.name}.{mode.name} needs at least two success examples"
        )
        assert mode.purpose and mode.description
        # either real failure examples, or an explicit statement that none exist
        assert mode.failures or mode.never_fails, f"{tool.name}.{mode.name} documents no failure path"


@pytest.mark.parametrize(("tool", "mode", "example", "expect_ok"), ALL_EXAMPLES, ids=lambda x: getattr(x, "name", None))
def test_every_example_behaves_as_documented(tool, mode, example, expect_ok):
    result = toolref.run_example(tool, example)  # must not raise
    assert isinstance(result, dict)
    where = f"{tool.name}.{mode.name}: {example.caption}"
    if expect_ok:
        assert result.get("ok") is True, f"documented success failed — {where} -> {result.get('message')}"
        assert "result" in result, where
    else:
        assert result.get("ok") is False, f"documented failure succeeded — {where}"
        assert result.get("error"), f"failure carries no error code — {where}"
        assert result.get("message"), f"failure carries no message — {where}"


@pytest.mark.parametrize("tool", toolref.CATALOGUE, ids=lambda t: t.name)
def test_tool_page_renders_with_an_anchor_per_mode(client, tool):
    r = client.get(f"/docs/tools/{tool.name}")
    assert r.status_code == 200
    assert f"<h1>{tool.name}</h1>" in r.text
    for mode in tool.modes:
        assert f'<h2 id="{mode.name}">' in r.text, f"{tool.name} is missing an anchor for {mode.name}"
    assert "When to use" in r.text and "Related tools" in r.text
    assert "&quot;ok&quot;: true" in r.text  # embedded live responses, not placeholders
    assert "&quot;ok&quot;: false" in r.text  # …including the failures
    assert f'href="/docs/tools/{tool.name}" class="cur"' in r.text  # sidebar marks the open tool


def test_tools_index_lists_every_tool(client):
    r = client.get("/docs/tools")
    assert r.status_code == 200 and "<h1>Tools</h1>" in r.text
    for name, _desc, _modes in TOOLS:
        assert f'href="/docs/tools/{name}"' in r.text, name
        assert f'id="{name}"' in r.text
    assert r.text.count('href="/docs/tools/') >= 12
    assert 'href="/docs/tools" class="cur"' in r.text


def test_unknown_tool_is_a_branded_404(client):
    r = client.get("/docs/tools/telepathy", headers={"Accept": "text/html"})
    assert r.status_code == 404
    assert "twelve tools" in r.text and 'class="brand"' in r.text


def test_pages_have_no_raw_markdown_artefacts(client):
    for name in ("math", "convert", "validate"):
        text = client.get(f"/docs/tools/{name}").text
        body = text.split('<article class="doc">')[1]
        assert "| ---" not in body and "\n## " not in body
        assert "```" not in body
        assert "<table>" in body and "<code" in body


def test_examples_that_depend_on_the_clock_are_marked():
    """Anything embedding "now" must be flagged, or the page would silently go stale."""
    volatile_modes = {
        ("datetime", "now"),
        ("geo_offline", "tz_for_place"),
        ("geo_offline", "tz_for_coords"),
        ("geo_offline", "country"),
        ("geo_offline", "zone_info"),
        ("random", "uuid"),
        ("random", "token"),
    }
    for tool in toolref.CATALOGUE:
        for mode in tool.modes:
            if (tool.name, mode.name) in volatile_modes:
                assert all(e.volatile for e in mode.examples), f"{tool.name}.{mode.name}"
            else:
                unmarked = [e for e in mode.examples if not e.volatile]
                assert unmarked or not mode.examples, f"{tool.name}.{mode.name}"


def test_no_documented_parameter_uses_a_retired_name():
    """`from`/`from_` and the bare `to` were renamed; nothing may reintroduce them."""
    retired = {"from", "from_", "to"}
    for tool in toolref.CATALOGUE:
        for mode in tool.modes:
            assert not retired & {p.name for p in mode.params}, f"{tool.name}.{mode.name} parameters"
            for example in mode.examples + mode.failures:
                assert not retired & set(example.args), f"{tool.name}.{mode.name}: {example.caption}"


def test_index_and_tool_pages_are_cached():
    first = toolref.tool_page("math")
    assert toolref.tool_page("math") is first
    assert toolref.tool_page("nope") is None
