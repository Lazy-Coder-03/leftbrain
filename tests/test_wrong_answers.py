"""#28 §3.3–3.13: answers that were confidently wrong.

None of these failed. Each returned `ok: true` and a value a caller would act on.
"""

import pytest

from leftbrain.core.collections_ import collections
from leftbrain.core.datetimex import datetime_tool
from leftbrain.core.encode import encode
from leftbrain.core.finance import finance
from leftbrain.core.mathx import math as math_tool

# --- 3.4 encode.json stringify emitted invalid JSON -------------------------


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_stringify_never_emits_something_no_parser_accepts(value):
    """`Infinity` and `NaN` are not JSON; every strict parser downstream rejects them."""
    r = encode("json", action="stringify", data={"a": value})
    assert r["ok"] is False and r["error"] == "invalid_input"
    assert "JSON cannot spell" in r["message"] and r["hint"]


def test_ordinary_stringify_is_unchanged():
    assert encode("json", action="stringify", data={"a": 1})["result"]["text"] == '{\n  "a": 1\n}'


# --- 3.3 encode.json parse was broken through MCP ---------------------------


def test_json_parse_accepts_a_document_that_arrived_already_parsed():
    """Through MCP `text` is `Any`, so `{"a": 1}` reaches the tool as a dict, not a string."""
    assert encode("json", action="parse", text={"a": 1})["result"]["valid"] is True
    assert encode("json", action="parse", text=[1, 2])["result"]["data"] == [1, 2]


def test_json_parse_still_reads_a_string():
    assert encode("json", action="parse", text='{"a": 1}')["result"]["data"] == {"a": 1}
    assert encode("json", action="parse", text="{oops}")["result"]["valid"] is False


# --- 3.5 two-digit years silently became 20xx -------------------------------


@pytest.mark.parametrize(("value", "year"), [("01/02/03", 2003), ("01/02/49", 2049), ("01/02/50", 1950), ("01/02/99", 1999)])
def test_a_two_digit_year_uses_a_documented_pivot(value, year):
    """`01/02/50` silently became 2050. For a date of birth that is a century out."""
    r = datetime_tool("parse", value=value, locale="IN")
    assert r["ok"] and r["result"]["date"].startswith(str(year)), (value, r["result"]["date"])
    assert any("two-digit year" in a for a in r["assumptions"]), r["assumptions"]


def test_a_four_digit_year_records_no_pivot():
    r = datetime_tool("parse", value="01/02/2003", locale="IN")
    assert not any("two-digit year" in a for a in r["assumptions"])


# --- 3.6 sub-daily recurrences collapsed to duplicate dates -----------------


def test_a_sub_daily_rule_returns_distinct_times():
    r = datetime_tool("recurrence", rule="FREQ=MINUTELY", start="2026-01-01T00:00:00", count=4)
    assert r["ok"]
    occurrences = r["result"]["occurrences"]
    assert len(set(occurrences)) == 4, occurrences
    assert "00:01" in " ".join(occurrences)


def test_a_daily_rule_still_returns_plain_dates():
    r = datetime_tool("recurrence", rule="FREQ=DAILY", start="2026-01-01", count=3)
    assert r["result"]["occurrences"] == ["2026-01-01", "2026-01-02", "2026-01-03"]


# --- 3.7 a rename collision silently dropped a field ------------------------


def test_colliding_rename_targets_are_refused():
    r = collections("pick_fields", items=[{"a": 1, "b": 2}], fields=["a", "b"], rename={"a": "z", "b": "z"})
    assert r["ok"] is False and r["error"] == "invalid_input" and "z" in r["message"]


def test_an_ordinary_rename_still_works():
    r = collections("pick_fields", items=[{"a": 1, "b": 2}], fields=["a", "b"], rename={"a": "x"})
    assert r["result"]["items"] == [{"x": 1, "b": 2}]


# --- 3.8 a missing key made every row a duplicate ---------------------------


def test_rows_without_the_key_are_not_duplicates_of_each_other():
    r = collections("find_duplicates", items=[{"a": 1}, {"a": 2}], key="nope")
    assert r["ok"] and r["result"]["has_duplicates"] is False
    assert r["warnings"]


def test_real_duplicates_are_still_found():
    r = collections("find_duplicates", items=[{"a": 1}, {"a": 1}, {"a": 2}], key="a")
    assert r["result"]["has_duplicates"] is True and len(r["result"]["duplicates"]) == 1


# --- 3.9 two participants in one timezone were refused ----------------------


def test_two_people_in_the_same_city_can_both_have_working_hours():
    r = datetime_tool(
        "free_slots",
        participants=[
            {"tz": "Asia/Kolkata", "windows": [{"start": "09:00", "end": "18:00"}]},
            {"tz": "Asia/Kolkata", "windows": [{"start": "10:00", "end": "17:00"}]},
        ],
        date="2026-09-01",
        duration_minutes=60,
    )
    assert r["ok"], r.get("message")


def test_explicit_labels_are_still_honoured():
    r = datetime_tool(
        "free_slots",
        participants=[
            {"tz": "Asia/Kolkata", "label": "Asha", "windows": [{"start": "09:00", "end": "18:00"}]},
            {"tz": "Asia/Kolkata", "label": "Ravi", "windows": [{"start": "10:00", "end": "17:00"}]},
        ],
        date="2026-09-01",
        duration_minutes=60,
    )
    assert r["ok"]


# --- 3.10 √ only bound with parentheses -------------------------------------


@pytest.mark.parametrize(("expr", "want"), [("√9", "3"), ("√2 + √9", "sqrt(2) + 3"), ("√(2)", "sqrt(2)")])
def test_the_root_sign_binds_without_parentheses(expr, want):
    r = math_tool("eval", expr=expr)
    assert r["ok"] and r["result"]["value"] == want, (expr, r["result"]["value"])


def test_the_root_sign_binds_to_an_identifier():
    assert math_tool("eval", expr="√x")["result"]["value"] == "sqrt(x)"


# --- 3.11 set_ops conflated 1 and true --------------------------------------


def test_a_boolean_is_not_the_number_one():
    r = collections("set_ops", a=[1, "1", 1, True, None], b=[1], op="difference")
    only_a = r["result"]["only_in_a"]
    assert True in only_a and "1" in only_a and None in only_a
    assert r["result"]["in_both"] == [1]


def test_ordinary_set_ops_are_unchanged():
    r = collections("set_ops", a=[1, 2, 3], b=[2, 3, 4], op="intersection")
    assert r["result"]["in_both"] == [2, 3]


# --- 3.12 several sign changes, one IRR, no warning -------------------------


def test_several_sign_changes_are_flagged():
    r = finance("npv_irr", cashflows=[-1000, 3000, -2500, 800], rate=10)
    assert r["ok"]
    said = " ".join(r["warnings"] + r["assumptions"])
    assert "sign" in said and "IRR" in said


def test_a_conventional_cashflow_says_nothing_extra():
    r = finance("npv_irr", cashflows=[-1000, 400, 400, 400], rate=10)
    assert not any("sign change" in w for w in r["warnings"])
