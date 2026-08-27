"""The failure half of the envelope: codes, `retryable`, and no leaked internals.

A client that reads only `ok: false` retries; an identical retry of a 15 s timeout multiplies
the load that caused it. So every failure says whether retrying could ever help, and a stack
trace is a server-side log line, not something every caller receives.
"""

import pytest

from leftbrain.contract import (
    Ambiguous,
    Busy,
    ResourceExhausted,
    Timeout,
    TooLarge,
    ToolError,
    Unsupported,
    debug_enabled,
    fail,
    schema_rejection,
    tool,
)

# --- codes and retryable -----------------------------------------------------


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        ("invalid_input", False),
        ("ambiguous", False),
        ("unsupported", False),
        ("too_large", False),
        ("timeout", False),
        ("resource_exhausted", False),
        ("forbidden", False),
        ("busy", True),
        ("internal", True),
    ],
)
def test_every_code_says_whether_an_identical_retry_could_help(code, retryable):
    assert fail(code, "…")["retryable"] is retryable


def test_an_unknown_code_is_not_retryable_and_an_explicit_flag_wins():
    assert fail("needs_rates", "…")["retryable"] is False
    assert fail("timeout", "…", retryable=True)["retryable"] is True


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (ToolError("…"), "invalid_input"),
        (Unsupported("…"), "unsupported"),
        (Timeout("…"), "timeout"),
        (TooLarge("…"), "too_large"),
        (ResourceExhausted("…"), "resource_exhausted"),
        (Busy("…"), "busy"),
    ],
)
def test_raising_a_tool_error_produces_its_code(exc, code):
    @tool
    def boom():
        raise exc

    assert boom()["error"] == code


def test_ambiguous_still_carries_needs_options():
    @tool
    def boom():
        raise Ambiguous("which ton?", field="from_unit", options=["metric ton", "short ton"])

    r = boom()
    assert r["error"] == "ambiguous" and r["retryable"] is False
    assert r["needs"] == {"field": "from_unit", "options": ["metric ton", "short ton"]}


def test_details_and_hint_travel_with_the_failure():
    r = fail(
        "timeout",
        "math.eval was stopped after 15.0s",
        details={"tool": "math", "mode": "eval", "limit_seconds": 15},
        hint="Reduce the exponent.",
    )
    assert r["details"]["limit_seconds"] == 15
    assert r["hint"] == "Reduce the exponent."
    assert list(r)[:3] == ["ok", "error", "message"]


def test_a_tool_error_can_raise_details_and_a_hint_too():
    @tool
    def boom():
        raise TooLarge("result too large", details={"digits": 10**9}, hint="Try a smaller exponent.")

    r = boom()
    assert r["error"] == "too_large" and r["retryable"] is False
    assert r["details"] == {"digits": 10**9} and r["hint"] == "Try a smaller exponent."


# --- the traceback stays on the server ---------------------------------------


@tool
def _crashes():
    raise RuntimeError("secret path /srv/leftbrain/x.py")


def test_an_internal_error_ships_no_traceback_by_default(monkeypatch):
    monkeypatch.delenv("LEFTBRAIN_DEBUG", raising=False)
    r = _crashes()
    assert r["error"] == "internal" and r["retryable"] is True
    assert "trace" not in r


def test_the_traceback_comes_back_only_with_the_debug_flag(monkeypatch):
    monkeypatch.setenv("LEFTBRAIN_DEBUG", "1")
    assert "trace" in _crashes()


@pytest.mark.parametrize("value", ["", "0", "false", "no", "  "])
def test_the_debug_flag_is_off_for_the_obvious_off_values(monkeypatch, value):
    monkeypatch.setenv("LEFTBRAIN_DEBUG", value)
    assert debug_enabled() is False


def test_an_internal_error_is_logged_server_side(caplog):
    with caplog.at_level("ERROR", logger="leftbrain"):
        _crashes()
    assert "RuntimeError" in caplog.text


# --- a call that never reaches the tool --------------------------------------

_MISSING = [
    {"type": "missing", "loc": ("value",), "msg": "Field required", "input": {}},
    {"type": "missing", "loc": ("from_unit",), "msg": "Field required", "input": {}},
]
_WRONG = [
    {"type": "list_type", "loc": ("where",), "msg": "Input should be a valid list", "input": "a > 0"},
]


def test_missing_parameters_become_a_contract_failure_naming_them():
    r = schema_rejection("convert", _MISSING)
    assert r["ok"] is False and r["error"] == "invalid_input" and r["retryable"] is False
    assert r["needs"] == {"missing": ["value", "from_unit"]}
    assert "value, from_unit" in r["message"]
    assert r["details"]["tool"] == "convert"


def test_a_wrong_type_is_reported_per_parameter_without_echoing_the_value():
    r = schema_rejection("collections", _WRONG)
    assert r["error"] == "invalid_input" and "needs" not in r
    assert r["details"]["parameters"] == [
        {"parameter": "where", "problem": "Input should be a valid list"}
    ]
    assert "a > 0" not in r["message"] and "a > 0" not in str(r["details"])


def test_a_nested_parameter_keeps_its_path():
    r = schema_rejection("collections", [{"type": "int_type", "loc": ("where", 0, "value"), "msg": "no"}])
    assert r["details"]["parameters"][0]["parameter"] == "where.0.value"


def test_no_pydantic_documentation_link_reaches_the_caller():
    errors = [{**_MISSING[0], "url": "https://errors.pydantic.dev/2.13/v/missing"}]
    assert "pydantic" not in str(schema_rejection("convert", errors))
