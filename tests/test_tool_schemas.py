"""#71: every parameter must carry a type an MCP client can serialise against.

A parameter annotated `Any` renders as `anyOf: [{}, {"type": "null"}]` — an empty schema with
no type in it. A client with nothing to serialise against emitted the argument unquoted:

    InputValidationError: called with input that could not be parsed as JSON.
    You sent: {"mode": "hash", "algo": "sha256", "text": abc}

The call died *inside the client* and never reached the server, so none of the contract's
machinery applied: no envelope, no `needs`, no hint, and a retry produced the same malformed
call. It made `encode`'s headline purpose unreachable for any text containing a letter.
"""

import pytest
from pydantic import TypeAdapter

pytest.importorskip("mcp", reason="MCP wrappers need the optional 'mcp' package")

from leftbrain import mcp_server as mcp  # noqa: E402
from leftbrain.mcp_server import server  # noqa: E402


def parameters():
    """(tool, parameter, schema) for every declared parameter."""
    for name, tool in server._tool_manager._tools.items():
        for param, spec in (tool.parameters or {}).get("properties", {}).items():
            yield name, param, spec


def test_no_parameter_carries_an_empty_schema():
    """The sweep. 32 parameters across 8 tools were declared this way, not the 5 reported."""
    empty = [f"{tool}.{param}" for tool, param, spec in parameters() if any(v == {} for v in (spec.get("anyOf") or []))]
    assert empty == [], empty


def test_no_parameter_is_left_untyped_by_any_other_spelling():
    """`{}` is one way to say nothing; a bare `anyOf` of nothing, or no type at all, is another."""
    untyped = []
    for tool, param, spec in parameters():
        variants = spec.get("anyOf") or [spec]
        if not any("type" in v or "$ref" in v or "enum" in v or "const" in v for v in variants):
            untyped.append(f"{tool}.{param}")
    assert untyped == [], untyped


#: The parameters named in the report, and the capability each one blocked.
REPORTED = [
    ("encode", "text"), ("geo_offline", "origin"), ("geo_offline", "destination"),
    ("validate", "value"), ("numbers", "value"), ("math", "value"),
]


@pytest.mark.parametrize(("tool", "param"), REPORTED)
def test_every_reported_parameter_now_admits_a_string(tool, param):
    spec = next(s for t, p, s in parameters() if (t, p) == (tool, param))
    types = {v.get("type") for v in (spec.get("anyOf") or [spec])}
    assert "string" in types, (tool, param, spec)


# --- the calls the report says were unreachable ----------------------------------------


def test_hashing_text_works_the_way_it_was_reported_failing():
    r = mcp.encode(mode="hash", algo="sha256", text="abc")
    assert r["ok"]
    assert r["result"]["hex"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_the_typed_route_still_agrees_with_it():
    """`bytes_hex` was typed all along, which is how the report proved the server was fine."""
    typed = mcp.encode(mode="hash", algo="sha256", bytes_hex="616263")
    assert typed["result"]["hex"] == mcp.encode(mode="hash", algo="sha256", text="abc")["result"]["hex"]


def test_distance_by_place_name_is_reachable():
    r = mcp.geo(mode="distance", origin="Kolkata", destination="London")
    assert r["ok"] and 7900 < r["result"]["km"] < 8000


def test_validating_an_alphabetic_value_is_reachable():
    """Digit-only ids happened to work, which is what made this look intermittent."""
    assert mcp.validate(mode="email", value="a@b.co")["result"]["valid"] is True
    assert mcp.validate(mode="id", kind="uuid", value="0b8a2f34-1c4d-4b8e-9f2a-6d5c7e8f9a0b")["ok"]
    assert mcp.validate(mode="id", kind="pan", value="ABCDE1234F")["ok"]


def test_parsing_a_written_number_is_reachable():
    r = mcp.numbers(mode="parse", value="1.2 Cr")
    assert r["ok"] and float(r["result"]["value"]) == 12_000_000


# --- what the types must not break -----------------------------------------------------


@pytest.mark.parametrize("annotation", [mcp.Quantity, mcp.Document])
@pytest.mark.parametrize("value", ["0.1", "1/3", "1.2 Cr", "12%"])
def test_a_numeric_string_stays_a_string(annotation, value):
    """leftbrain reads "0.1" as the exact decimal 1/10. A union that coerced it to a float
    would hand the tool the binary approximation and lose the whole point of the server."""
    assert TypeAdapter(annotation).validate_python(value) == value


@pytest.mark.parametrize("annotation", [mcp.Quantity, mcp.Document])
@pytest.mark.parametrize("value", [5, 5.5, 0])
def test_a_number_stays_the_number_it_arrived_as(annotation, value):
    out = TypeAdapter(annotation).validate_python(value)
    assert out == value and type(out) is type(value)


def test_a_json_document_still_reaches_the_tool_as_a_document():
    """Through MCP `text` really does receive a dict — a document that arrived already parsed.
    That is why these params could not simply be narrowed to `str`."""
    assert mcp.encode(mode="json", action="parse", text={"a": 1})["result"]["valid"] is True
    assert mcp.encode(mode="json", action="parse", text=[1, 2])["result"]["data"] == [1, 2]


def test_the_exact_decimal_promise_survives_the_round_trip():
    r = mcp.numbers(mode="compare", values=["9.11", "9.9"])
    assert r["ok"] and r["result"]["max"]["value"] == "9.9"
    assert mcp.finance(mode="percent", op="of", percent="12.5", value="240")["ok"]


# --- #64: a parameter with no description tells an agent nothing before it calls --------
#
# A client that defers tool schemas shows a preview when deciding whether to load the full
# definition. Ours read as `A?: any, B?: any, angle?: any…` — thirty untyped optionals with
# nothing to choose between them, beside rival tools showing real descriptions. #71 gave them
# types; this gives them meanings, from the same text the documentation site already carries.


def test_every_parameter_of_every_tool_says_what_it_is_for():
    missing = [f"{tool}.{param}" for tool, param, spec in parameters() if not spec.get("description")]
    assert missing == [], missing


def test_mode_lists_what_the_tool_can_do():
    for tool, param, spec in parameters():
        if param == "mode":
            assert spec["description"].startswith("What this call does: "), tool
            assert "|" in spec["description"] or tool in {"random"}, tool


def test_a_parameter_documented_per_mode_carries_each_modes_wording():
    """The schema is flat across modes, so a caller reading one sentence cannot tell which
    mode it belongs to unless it is labelled."""
    angle = next(s for t, p, s in parameters() if (t, p) == ("math", "angle"))
    assert "eval:" in angle["description"] and "trigonometry" in angle["description"]


def test_the_descriptions_come_from_the_reference_rather_than_a_second_copy():
    from leftbrain import toolref

    documented = {p.name for doc in toolref.CATALOGUE if doc.name == "math" for m in doc.modes for p in m.params}
    published = {p for t, p, _s in parameters() if t == "math"} - {"mode", "timeout"}
    assert published <= documented, published - documented
