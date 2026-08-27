"""The tool reference is generated from the servers, so the tests check the sources agree.

Three things feed a reference page — the MCP input schema, the wrapper docstring and the core
module's `EXAMPLES` — and every one of them can drift from the others. These tests fail when
they do: a mode missing from a docstring, a parameter the wrapper no longer accepts, a mode
with no worked example, an example that no longer succeeds.
"""

import asyncio

import pytest
from starlette.testclient import TestClient

from leftbrain.core import collections_, datetimex, encode, geo_offline, holidays_, mathx, random_
from leftbrain.core import color as color_mod
from leftbrain.core import convert as convert_mod
from leftbrain.core import finance as finance_mod
from leftbrain.core import numbers as numbers_mod
from leftbrain.core import scale as scale_mod
from leftbrain.core import text as text_mod
from leftbrain.core import validate as validate_mod
from leftbrain.external import tools as external_tools
from leftbrain.serve import build_app
from leftbrain.web import toolref
from leftbrain.web.config import WebConfig
from leftbrain.web.tools_list import TOOLS

MODULE_MODES = {
    "math": mathx.MODES,
    "datetime": datetimex.MODES,
    "scale": scale_mod.MODES,
    "convert": convert_mod.MODES,
    "holidays": holidays_.MODES,
    "numbers": numbers_mod.MODES,
    "finance": finance_mod.MODES,
    "text": text_mod.MODES,
    "collections": collections_.MODES,
    "validate": validate_mod.MODES,
    "random": random_.MODES,
    "geo_offline": geo_offline.MODES,
    "encode": encode.MODES,
    "color": color_mod.MODES,
    "weather": external_tools.WEATHER_MODES,
    "geo": external_tools.GEO_MODES,
    "fx_rate": (),
    "url_check": (),
}

#: Modes with no failure path at all, and why. Anything else needs a failing example.
NO_FAILURE_MODES = {
    ("holidays", "countries"): "takes no parameters, so there is nothing to get wrong",
    ("validate", "email"): "an unusable address is an answer (valid: false), not an error",
    ("validate", "url"): "an unusable URL is an answer (valid: false), not an error",
    ("validate", "ip"): "an unparseable address is an answer (valid: false), not an error",
}

#: Modes whose output embeds the current instant, so every example must be marked volatile.
VOLATILE_MODES = {
    ("datetime", "now"),
    ("geo_offline", "tz_for_place"),
    ("geo_offline", "tz_for_coords"),
    ("geo_offline", "country"),
    ("geo_offline", "zone_info"),
    ("random", "uuid"),
    ("random", "token"),
}

#: Modes that legitimately have a single worked example.
SINGLE_EXAMPLE_MODES = {("holidays", "countries"), ("encode", "jwt_decode")}

ALL_MODES = [(tool, mode) for tool in toolref.CATALOGUE for mode in tool.modes]
EVERY_MODE = [(tool, mode) for tool in toolref.CATALOGUE + toolref.EXTERNAL_CATALOGUE for mode in tool.modes]
ALL_EXAMPLES = [(tool, mode, example) for tool, mode in ALL_MODES for example in toolref.examples_of(tool, mode)]


def _ids(item):
    return getattr(item, "name", None)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("toolref")
    cfg = WebConfig(client_id=None, client_secret=None, secret="test-secret-0123456789", base_url=None, open_signup=False)
    app = build_app(include_external=False, keys_db=str(tmp / "k.sqlite3"), web_config=cfg)
    with TestClient(app) as c:
        yield c


# --- the catalogue and the servers describe the same tools -------------------


def test_catalogue_covers_every_tool():
    assert [t.name for t in toolref.CATALOGUE] == [name for name, _d, _m in TOOLS]
    assert len(toolref.CATALOGUE) == 14
    assert [t.name for t in toolref.EXTERNAL_CATALOGUE] == ["weather", "fx_rate", "geo", "url_check"]
    assert set(toolref.specs()) == {t.name for t in toolref.CATALOGUE + toolref.EXTERNAL_CATALOGUE}


def test_the_tool_registry_matches_what_the_server_publishes():
    """`specs()` reads the internal registry; the wire uses the async `list_tools()`."""
    from leftbrain.mcp_server import server as core_server

    published = {t.name: t.input_schema for t in asyncio.run(core_server.list_tools())}
    for name, schema in published.items():
        assert toolref.specs()[name].schema == schema, name


@pytest.mark.parametrize("tool", toolref.CATALOGUE + toolref.EXTERNAL_CATALOGUE, ids=_ids)
def test_every_mode_of_every_tool_is_documented(tool):
    """A new mode in a core module must gain a catalogue entry or this fails."""
    documented = [m.name for m in tool.modes]
    assert len(set(documented)) == len(documented), f"duplicate mode entries for {tool.name}"
    assert set(documented) == set(MODULE_MODES[tool.name]), (
        f"{tool.name}: documented {sorted(documented)} vs implemented {sorted(MODULE_MODES[tool.name])}"
    )


@pytest.mark.parametrize("tool", toolref.CATALOGUE + toolref.EXTERNAL_CATALOGUE, ids=_ids)
def test_every_mode_is_declared_in_the_wrapper_docstring(tool):
    """The docstring is what an agent reads, so it cannot fall behind the implementation."""
    declared = set(toolref.docstring_modes(tool.name).order)
    implemented = set(MODULE_MODES[tool.name])
    assert implemented - declared == set(), f"{tool.name}: docstring never mentions {sorted(implemented - declared)}"
    assert declared - implemented == set(), f"{tool.name}: docstring invents {sorted(declared - implemented)}"


@pytest.mark.parametrize("tool", toolref.CATALOGUE + toolref.EXTERNAL_CATALOGUE, ids=_ids)
def test_every_published_parameter_is_documented_exactly_once_per_mode(tool):
    """Adding a parameter to a wrapper must add it to a mode's table, or the docs are incomplete."""
    schema = toolref.specs()[tool.name].schema
    published = set(schema.get("properties", {})) - {"mode"}
    documented: set[str] = set(p.name for p in tool.params)
    for mode in tool.modes:
        names = [p.name for p in mode.params]
        assert len(set(names)) == len(names), f"{tool.name}.{mode.name} lists a parameter twice"
        documented |= set(names)
        toolref.rows(tool, mode.params, f"{tool.name}.{mode.name}")  # raises on an unknown name
    assert published - documented == set(), f"{tool.name}: undocumented parameters {sorted(published - documented)}"
    assert documented - published == set(), f"{tool.name}: documents {sorted(documented - published)}, which the tool does not accept"


def test_documenting_a_parameter_the_tool_does_not_accept_fails_the_build():
    tool = toolref.by_name("math")
    with pytest.raises(KeyError, match="telepathy"):
        toolref.rows(tool, (toolref.Param("telepathy", "not a real argument"),), "math.eval")


@pytest.mark.parametrize(("tool", "mode"), EVERY_MODE, ids=lambda x: getattr(x, "name", None))
def test_every_mode_reads_as_prose(tool, mode):
    assert mode.description, f"{tool.name}.{mode.name} has no description"
    assert toolref.purpose_of(tool, mode), f"{tool.name}.{mode.name} has no one-liner for the mode index"


# --- the examples ------------------------------------------------------------


@pytest.mark.parametrize(("tool", "mode"), ALL_MODES, ids=lambda x: getattr(x, "name", None))
def test_every_mode_has_a_working_example_and_a_failing_one(tool, mode):
    examples = toolref.examples_of(tool, mode)
    assert examples, f"{tool.name}.{mode.name} has no examples in {tool.name}'s EXAMPLES"
    key = (tool.name, mode.name)
    if key not in SINGLE_EXAMPLE_MODES:
        assert len(examples) >= 2, f"{tool.name}.{mode.name} needs more than one example"
    results = [toolref.run_example(tool, e) for e in examples]
    assert any(toolref.succeeded(r) for r in results), f"{tool.name}.{mode.name}: no example succeeds any more"
    if key in NO_FAILURE_MODES:
        assert all(toolref.succeeded(r) for r in results), f"{tool.name}.{mode.name} is listed as never failing"
        assert mode.never_fails, f"{tool.name}.{mode.name} must say why it never fails"
    else:
        assert not mode.never_fails, f"{tool.name}.{mode.name} claims it never fails; add it to NO_FAILURE_MODES"
        assert any(not toolref.succeeded(r) for r in results), f"{tool.name}.{mode.name}: no example fails"


@pytest.mark.parametrize(("tool", "mode", "example"), ALL_EXAMPLES, ids=lambda x: getattr(x, "name", None))
def test_every_example_returns_the_contract_or_a_protocol_error(tool, mode, example):
    result = toolref.run_example(tool, example)  # must not raise
    assert isinstance(result, dict)
    where = f"{tool.name}.{mode.name}: {example.caption}"
    if toolref.succeeded(result):
        assert "result" in result, where
    elif result.get("isError"):
        assert toolref._message(result), where  # a schema rejection still has to say something
    else:
        assert result.get("error") and result.get("message"), f"failure carries no error code — {where}"


@pytest.mark.parametrize(("tool", "mode"), ALL_MODES, ids=lambda x: getattr(x, "name", None))
def test_derived_failures_really_fail(tool, mode):
    """The generator only adds a probe to "Fails when" when the call actually failed."""
    ran = [(e, toolref.run_example(tool, e)) for e in toolref.examples_of(tool, mode)]
    failures = toolref.failures_of(tool, mode, ran)
    assert not any(toolref.succeeded(r) for _e, r in failures), f"{tool.name}.{mode.name}"
    messages = [toolref._message(r) for _e, r in failures]
    assert len(set(messages)) == len(messages), f"{tool.name}.{mode.name} repeats a failure message"
    if (tool.name, mode.name) not in NO_FAILURE_MODES:
        assert failures, f"{tool.name}.{mode.name} documents no failure path"


def test_examples_that_depend_on_the_clock_are_marked():
    """Anything embedding "now" must be flagged, or the page would silently go stale."""
    for tool in toolref.CATALOGUE:
        for mode in tool.modes:
            # a failing call answers with an error message, which never embeds the clock
            working = [e for e in toolref.examples_of(tool, mode) if toolref.succeeded(toolref.run_example(tool, e))]
            if (tool.name, mode.name) in VOLATILE_MODES:
                assert all(e.volatile for e in working), f"{tool.name}.{mode.name}"
            else:
                assert any(not e.volatile for e in working), f"{tool.name}.{mode.name}"


def test_no_documented_parameter_uses_a_retired_name():
    """`from`/`from_` and the bare `to` were renamed in the core tools; nothing may reintroduce them."""
    retired = {"from", "from_", "to"}
    for tool in toolref.CATALOGUE:
        for mode in tool.modes:
            assert not retired & {p.name for p in mode.params}, f"{tool.name}.{mode.name} parameters"
            for example in toolref.examples_of(tool, mode):
                assert not retired & set(example.args), f"{tool.name}.{mode.name}: {example.caption}"


# --- the rendered pages ------------------------------------------------------


@pytest.mark.parametrize("tool", toolref.CATALOGUE, ids=_ids)
def test_tool_page_renders_with_an_anchor_per_mode(client, tool):
    r = client.get(f"/docs/tools/{tool.name}")
    assert r.status_code == 200
    assert f"<h1>{tool.name}</h1>" in r.text
    for mode in tool.modes:
        assert f'<h2 id="{mode.name}">' in r.text, f"{tool.name} is missing an anchor for {mode.name}"
    assert "When to use" in r.text and "Related tools" in r.text
    assert "&quot;ok&quot;: true" in r.text  # embedded live responses, not placeholders
    assert "&quot;ok&quot;: false" in r.text or "isError" in r.text  # …including the failures
    assert f'href="/docs/tools/{tool.name}" class="cur"' in r.text  # sidebar marks the open tool


@pytest.mark.parametrize("tool", toolref.EXTERNAL_CATALOGUE, ids=_ids)
def test_network_tool_page_documents_without_calling_out(client, tool):
    r = client.get(f"/docs/tools/{tool.name}")
    assert r.status_code == 200
    assert f"<h1>{tool.name}</h1>" in r.text
    assert "network tool" in r.text.lower()
    assert "&quot;ok&quot;: true" not in r.text  # nothing was executed
    for mode in tool.modes:
        assert f'<h2 id="{mode.name}">' in r.text
    if not tool.modes:
        assert "<h2>Parameters</h2>" in r.text
    assert "<table>" in r.text  # the schema-derived parameter table


def test_tools_index_lists_every_tool(client):
    r = client.get("/docs/tools")
    assert r.status_code == 200 and "<h1>Tools</h1>" in r.text
    for tool in toolref.CATALOGUE + toolref.EXTERNAL_CATALOGUE:
        assert f'href="/docs/tools/{tool.name}"' in r.text, tool.name
        assert f'id="{tool.name}"' in r.text
    assert r.text.count('href="/docs/tools/') >= 16
    assert 'href="/docs/tools" class="cur"' in r.text
    assert "Network tools" in r.text


def test_unknown_tool_is_a_branded_404(client):
    r = client.get("/docs/tools/telepathy", headers={"Accept": "text/html"})
    assert r.status_code == 404
    assert "fourteen tools" in r.text and 'class="brand"' in r.text


def test_pages_have_no_raw_markdown_artefacts(client):
    for name in ("math", "convert", "validate", "weather", "fx_rate"):
        text = client.get(f"/docs/tools/{name}").text
        body = text.split('<article class="doc">')[1]
        assert "| ---" not in body and "\n## " not in body
        assert "```" not in body and ":::" not in body
        assert "<table>" in body and "<code" in body


@pytest.mark.parametrize("tool", [t for t in toolref.CATALOGUE if t.modes], ids=_ids)
def test_every_example_says_which_half_you_send(client, tool):
    """Request and response are colour-coded, so a reader never confuses input with output."""
    body = client.get(f"/docs/tools/{tool.name}").text.split('<article class="doc">')[1]
    assert '<div class="io io-req"><span class="io-label">Request · tools/call</span>' in body
    assert '<div class="io io-res"><span class="io-label">Response · you get this back</span>' in body
    # one labelled response for every labelled request — the failures are paired too
    assert body.count('class="io io-req"') == body.count('class="io io-res"')


def test_the_failure_examples_are_labelled_too(client):
    """"Fails when" comes from the same generator, so its blocks carry the same labels."""
    math = toolref.by_name("math")
    markdown = "\n".join(toolref._mode_markdown(math, math.modes[0]))
    fails = markdown.split("### Fails when")[1]
    assert ":::request tools/call" in fails and ":::response" in fails


def test_the_tools_index_has_no_colour_legend(client):
    body = client.get("/docs/tools").text.split('<article class="doc">')[1]
    assert "blue blocks" not in body and "green blocks" not in body


def test_index_and_tool_pages_are_cached():
    first = toolref.tool_page("math")
    assert toolref.tool_page("math") is first
    assert toolref.tool_page("nope") is None
