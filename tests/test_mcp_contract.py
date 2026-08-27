"""A call that fails the input schema never reaches the tool - it must still answer in the contract.

Before this, `convert` with nothing and `collections` with `where` as a string came back as an MCP
transport error carrying a pydantic dump and an errors.pydantic.dev link. An agent cannot act on
that: it is not the shape every other answer has.
"""

import anyio
import pytest

pytest.importorskip("mcp", reason="the MCP servers need the optional 'mcp' package")

from leftbrain import mcp_server as mcp  # noqa: E402


def call(name: str, args: dict):
    result = anyio.run(lambda: mcp.server.call_tool(name, args))
    assert result.structured_content is not None
    return result


def test_missing_required_parameters_answer_in_the_contract():
    result = call("convert", {})
    envelope = result.structured_content
    assert envelope["ok"] is False
    assert envelope["error"] == "invalid_input" and envelope["retryable"] is False
    assert envelope["needs"] == {"missing": ["value", "from_unit", "to_unit"]}
    assert "pydantic" not in result.content[0].text


def test_a_parameter_of_the_wrong_type_answers_in_the_contract():
    envelope = call("collections", {"mode": "filter", "items": [{"a": 1}], "where": "a > 0"}).structured_content
    assert envelope["ok"] is False and envelope["error"] == "invalid_input"
    assert [p["parameter"] for p in envelope["details"]["parameters"]] == ["where"]
    assert envelope["details"]["tool"] == "collections"


def test_the_same_json_is_repeated_as_text_for_text_only_clients():
    import json

    result = call("convert", {})
    assert json.loads(result.content[0].text) == result.structured_content
    assert result.is_error is False  # a contract failure is a result, not a transport error


def test_a_working_call_is_untouched():
    envelope = call("math", {"mode": "eval", "expr": "1/3"}).structured_content
    assert envelope["ok"] is True and envelope["result"]["value"] == "1/3"


def test_an_unknown_tool_is_still_a_transport_error():
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError):
        anyio.run(lambda: mcp.server.call_tool("nosuchtool", {}))
