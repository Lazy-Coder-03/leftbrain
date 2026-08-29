"""Adversarial sweep of `validate`, in the failure classes #52 found in `math`."""

import time

import pytest

from leftbrain.core.validate import validate

# --- F. the pattern guard had a door round the side --------------------------------------


def test_assert_matches_is_guarded_like_regex_match():
    started = time.monotonic()
    r = validate("assert", data={"a": "a" * 30 + "b"}, rules=[{"path": "a", "op": "matches", "value": "(a+)+$"}])
    assert time.monotonic() - started < 1.0, "must be refused, not run"
    assert r["ok"] is False and r["error"] == "unsupported", r
    r = validate("assert", data={"a": "x"}, rules=[{"path": "a", "op": "matches", "value": "(unclosed"}])
    assert r["ok"] and r["result"]["results"][0]["passed"] is False and "pattern" in r["result"]["results"][0]["reason"], r


def test_regex_mode_reports_the_new_shapes():
    r = validate("regex", pattern="(a{1,60}){1,60}$")
    assert r["ok"] and r["result"]["backtracking_risk"], r


def test_json_schema_patterns_are_guarded_too():
    started = time.monotonic()
    r = validate("json_schema", schema={"type": "string", "pattern": "^(a{1,60}){1,60}$"}, data="a" * 30 + "b")
    assert time.monotonic() - started < 1.0 and r["ok"] is False, r


# --- E. sql_parse: writes that read as reads -----------------------------------------------------


def test_sql_writes_hidden_in_other_statements():
    r = validate("sql_parse", sql="EXEC xp_cmdshell 'dir'", dialect="tsql")
    assert r["result"]["statement_count"] == 1 and r["result"]["read_only"] is False, r
    r = validate("sql_parse", sql="WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d")
    assert r["result"]["read_only"] is False, r
    r = validate("sql_parse", sql="SELECT * INTO new_t FROM t")
    assert r["result"]["read_only"] is False and "new_t" in r["result"]["statements"][0]["tables_write"], r
    assert validate("sql_parse", sql="SELECT 1")["result"]["read_only"] is True


# --- E. url / phone ---------------------------------------------------------------------------------


def test_url_edges():
    r = validate("url", value="http://example.com:99999/")
    assert r["ok"] and r["result"]["valid"] is False and "port" in r["result"]["reason"] and "ValueError" not in r["result"]["reason"], r
    r = validate("url", value="http://example.com/\nx")
    assert r["result"]["valid"] is False and "control" in r["result"]["reason"], r
    assert validate("url", value="tel:")["result"]["valid"] is False
    r = validate("url", value="javascript:alert(1)")
    assert r["result"]["valid"] is False and "javascript" in r["result"]["reason"], r


def test_phone_extensions_and_unassigned_codes():
    r = validate("phone", value="+1 212 555 0100 x123")
    assert r["result"]["valid"] is True and r["result"]["extension"] == "123", r
    r = validate("phone", value="+999 12345678")
    assert r["result"]["valid"] is False and "country code" in r["result"]["reason"], r
    r = validate("phone", value="+39 06 1234 5678")
    assert r["result"]["valid"] is True and r["result"]["country_code"] == "39" and any("shape" in w for w in r["warnings"]), r


# --- E. ids that disagree with each other ---------------------------------------------------------------


def test_gstin_applies_the_pan_rule_to_its_embedded_pan():
    r = validate("id", kind="gstin", value="19ABCDE1234F1ZX")
    assert r["result"]["valid"] is False and "holder type" in r["result"]["reason"], r


def test_a_card_of_zeros_is_not_a_card():
    r = validate("id", kind="card", value="0000 0000 0000 0000")
    assert r["result"]["valid"] is False, r
    assert validate("id", kind="card", value="４１１１１１１１１１１１１１１１")["result"]["brand"] == "visa"


def test_json_schema_email_format_agrees_with_the_email_mode():
    r = validate("json_schema", schema={"type": "string", "format": "email"}, data="a@b")
    assert r["result"]["valid"] is False, r
    r = validate("json_schema", schema={"$ref": "http://example.com/schema.json"}, data=1)
    assert r["ok"] is False and r["error"] == "unsupported" and "Unresolvable" not in r["message"], r


# --- E. assert verdicts ----------------------------------------------------------------------------------


def test_assert_verdicts_say_what_they_mean():
    r = validate("assert", data={"items": []}, rules=[{"path": "items", "op": "each", "value": {"path": "q", "op": "gt", "value": 0}}])
    assert "empty" in r["result"]["results"][0]["reason"], r
    r = validate("assert", data={}, rules=[{"path": "nope", "op": "ne", "value": 1}])
    assert r["result"]["results"][0]["passed"] is True and "missing" in r["result"]["results"][0]["reason"], r
    r = validate("assert", data={"a": ["1", "2", "x"]}, rules=[{"path": "a", "op": "sum_eq", "value": 3}])
    assert r["result"]["results"][0]["passed"] is False and "'x'" in r["result"]["results"][0]["reason"], r
    r = validate("assert", data={"a": True}, rules=[{"path": "a", "op": "eq", "value": 1}])
    assert r["result"]["results"][0]["passed"] is False
    r = validate("assert", data={"a": [1]}, rules=[{"path": "a", "op": "len_gt"}])
    assert "TypeError" not in r["result"]["results"][0]["reason"] and "value" in r["result"]["results"][0]["reason"], r
    r = validate("assert", data={"a": 1}, rules=["a exists"])
    assert r["ok"] is False and "object" in r["message"] and "AttributeError" not in r["message"], r


@pytest.mark.parametrize("call", [lambda: validate("sql_parse", sql="SELECT 1", dialect="klingon"), lambda: validate("sql_parse", sql=1)])
def test_bad_inputs_are_refused_in_words(call):
    r = call()
    assert r["ok"] is False and r["error"] == "invalid_input", r
    for leak in ("TypeError", "ValueError", "has no len"):
        assert leak not in r["message"], r["message"]
