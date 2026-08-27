"""Catastrophic backtracking is refused before the engine starts (#28 §1).

`text.regex_match` with `(a+)+$` over `"a"*40 + "b"` never returns. Nothing can stop it once
`sre` is running: it is a C loop that never reaches a bytecode boundary, so no signal and no
async exception is delivered until it finishes, which is never. The only place to act is before
`re.compile` is handed the pattern.
"""

import time

import pytest

from leftbrain.core.text import redos_risk, text
from leftbrain.core.validate import validate

# Each of these makes stdlib `re` backtrack exponentially.
CATASTROPHIC = [
    r"(a+)+$",
    r"^(a|a?)+$",
    r"(a|aa)+$",
    r"(a*)*",
    r"(a?)*",
    r"([a-z]+)*",
    r"(?:x+)+",
    r"(a{2,})+",
]

# Ordinary patterns that must keep working - the guard is worthless if it refuses these.
ORDINARY = [
    r"(a)(b)",
    r"(a|b)+",
    r"(foo|bar)+",
    r"\d+",
    r"[a-z]+@[a-z]+\.[a-z]{2,}",
    r"(ab)*",
    r"^(\d|-)+$",
    r"(?P<year>\d{4})-(?P<month>\d{2})",
    r"\((\d{3})\)\s*\d{3}-\d{4}",
    r"(https?|ftp)://\S+",
    r"(a{2,4})+",
    r"^\s*#\s*(TODO|FIXME)\b",
]


@pytest.mark.parametrize("pattern", CATASTROPHIC)
def test_the_guard_recognises_a_runaway_pattern(pattern):
    assert redos_risk(pattern), pattern


@pytest.mark.parametrize("pattern", ORDINARY)
def test_the_guard_leaves_ordinary_patterns_alone(pattern):
    assert redos_risk(pattern) is None, pattern


# --- through the tools ------------------------------------------------------


def test_regex_match_refuses_instead_of_hanging():
    started = time.monotonic()
    r = text("regex_match", text="a" * 40 + "b", pattern="(a+)+$")
    assert time.monotonic() - started < 1.0
    assert r["ok"] is False and r["error"] == "unsupported" and r["retryable"] is False
    assert "backtrack" in r["message"] and r["hint"]
    assert r["details"]["pattern"] == "(a+)+$"


def test_regex_match_refuses_the_nullable_alternation():
    r = text("regex_match", text="a" * 35 + "!", pattern="^(a|a?)+$")
    assert r["ok"] is False and r["error"] == "unsupported"


def test_regex_replace_is_guarded_too():
    started = time.monotonic()
    r = text("regex_replace", text="a" * 40 + "b", pattern="(a|aa)+$", replacement="x")
    assert time.monotonic() - started < 1.0
    assert r["ok"] is False and r["error"] == "unsupported"


def test_an_ordinary_match_still_works():
    r = text("regex_match", text="ab ab", pattern="(a)(b)")
    assert r["ok"] and r["result"]["count"] == 2


def test_an_invalid_pattern_still_reads_as_invalid_input():
    r = text("regex_match", text="x", pattern="(unclosed")
    assert r["ok"] is False and r["error"] == "invalid_input" and "invalid regex" in r["message"]


# --- validate: judge the pattern, do not run it -----------------------------


def test_validate_regex_reports_the_risk_rather_than_refusing():
    """`validate.regex` exists to judge a pattern, so it must still answer."""
    r = validate("regex", pattern="(a+)+$")
    assert r["ok"] and r["result"]["valid"] is True
    assert r["result"]["backtracking_risk"]
    assert any("backtrack" in w for w in r["warnings"])


def test_validate_regex_says_nothing_about_a_safe_pattern():
    r = validate("regex", pattern=r"^\d{4}-\d{2}$")
    assert r["ok"] and r["result"]["backtracking_risk"] is None and not r["warnings"]


# --- validate.json_schema: the same engine, reached through jsonschema ------


def test_a_schema_pattern_that_backtracks_is_refused():
    started = time.monotonic()
    r = validate("json_schema", schema={"type": "string", "pattern": "(a+)+$"}, data="a" * 30 + "b")
    assert time.monotonic() - started < 1.0
    assert r["ok"] is False and r["error"] == "unsupported"
    assert "pattern" in r["message"]


def test_a_nested_schema_pattern_is_found_too():
    schema = {"type": "object", "properties": {"a": {"patternProperties": {"(x+)+$": {"type": "string"}}}}}
    r = validate("json_schema", schema=schema, data={"a": {}})
    assert r["ok"] is False and r["error"] == "unsupported"


def test_an_ordinary_schema_pattern_still_validates():
    r = validate("json_schema", schema={"type": "string", "pattern": r"^\d{4}$"}, data="2026")
    assert r["ok"] and r["result"]["valid"] is True
