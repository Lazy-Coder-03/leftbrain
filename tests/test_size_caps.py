"""Layer 0: refuse the enormous before computing it, and say so when a result was trimmed.

Issue #28 §2e/§2f/§2g. Every case here reached the engine before: `9^9^9^9` froze the hosted
server for 35 minutes, `numbers.allocate parts=1000000` returned 116 MB, and
`datetime.recurrence count=1000000` silently returned 100 occurrences as though that were all
of them. A pre-check costs microseconds; the point is that none of these get as far as the work.
"""

import time

import pytest

from leftbrain.core.collections_ import collections
from leftbrain.core.datetimex import datetime_tool
from leftbrain.core.holidays_ import holidays
from leftbrain.core.mathx import math as math_tool
from leftbrain.core.numbers import numbers
from leftbrain.core.text import text
from leftbrain.core.validate import validate

TOO_BIG = ("too_large", "invalid_input")


def refused(response):
    return response["ok"] is False and response["error"] in TOO_BIG


# --- §2g: math result size, estimated from the expression -------------------


@pytest.mark.parametrize("expr", ["9^9^9^9", "2^(2^100)", "2^1000000", "9**9**9**9"])
def test_a_power_tower_is_refused_before_it_is_evaluated(expr):
    started = time.monotonic()
    r = math_tool("eval", expr=expr)
    assert refused(r), r
    assert time.monotonic() - started < 1.0, "the estimate must not evaluate the expression"
    assert "digit" in r["message"]


@pytest.mark.parametrize("expr", ["factorial(factorial(20))", "gamma(10**10)", "exp(10^20)"])
def test_an_astronomical_factorial_or_exponential_is_refused_too(expr):
    started = time.monotonic()
    assert refused(math_tool("eval", expr=expr))
    assert time.monotonic() - started < 1.0


@pytest.mark.parametrize("expr", ["2^100", "factorial(100)", "2^10000", "x^y", "sin(pi/4)"])
def test_expressions_that_fit_are_untouched(expr):
    # `angle` is ignored by everything here without trigonometry in it, and `sin(pi/4)` is
    # refused without one since #69 - which is this test's subject only by accident.
    assert math_tool("eval", expr=expr, angle="rad")["ok"], expr


def test_the_refusal_is_too_large_and_not_retryable():
    r = math_tool("eval", expr="9^9^9^9")
    assert r["error"] == "too_large" and r["retryable"] is False
    assert r["details"]["estimated_digits"] and r["hint"]


def test_precision_is_capped():
    assert refused(math_tool("eval", expr="1/3", precision=100000))
    assert math_tool("eval", expr="1/3", precision=50)["ok"]


def test_series_order_is_capped():
    assert refused(math_tool("series", expr="1/sin(x)", var="x", order=200))
    assert math_tool("series", expr="sin(x)", var="x", order=8)["ok"]


# --- §2e: size caps ---------------------------------------------------------


def test_geometric_terms_are_capped_by_their_size_not_only_their_count():
    """n is capped at 10 000; 2^10000 as the last term is 15 MB of digits."""
    assert refused(numbers("sequence", kind="geometric", start=2, ratio=2, n=10000))
    assert numbers("sequence", kind="geometric", start=2, ratio=2, n=50)["ok"]


def test_allocate_parts_are_capped():
    assert refused(numbers("allocate", total=100, parts=1000000))
    assert numbers("allocate", total=100, parts=3)["ok"]


def test_regex_replace_refuses_an_enormous_output():
    assert refused(text("regex_replace", text="a" * 10000, pattern="a", replacement="x" * 1000))
    assert text("regex_replace", text="a" * 100, pattern="a", replacement="xx")["ok"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"a": "line\n" * 100000, "b": "line\n" * 100000},
        {"a": "w " * 30000, "b": "w " * 30000, "granularity": "word"},
    ],
)
def test_diff_refuses_more_units_than_it_can_compare(kwargs):
    started = time.monotonic()
    assert refused(text("diff", **kwargs))
    assert time.monotonic() - started < 5.0, "difflib is quadratic; the cap must come first"


def test_pivot_refuses_a_runaway_column_count():
    rows = [{"r": i, "c": f"col{i}", "v": 1} for i in range(5000)]
    assert refused(collections("pivot", items=rows, by="r", pivot_columns="c", column="v", agg="sum"))
    small = [{"r": i % 3, "c": f"col{i % 4}", "v": 1} for i in range(40)]
    assert collections("pivot", items=small, by="r", pivot_columns="c", column="v", agg="sum")["ok"]


def test_business_days_refuses_a_century():
    assert refused(datetime_tool("business_days", start="2026-08-01", end="2126-08-01", region="IN"))
    assert datetime_tool("business_days", start="2026-10-01", end="2026-10-31", region="IN")["ok"]


def test_a_long_list_of_timezones_is_refused():
    zones = ["UTC"] * 500
    assert refused(datetime_tool("now", tz=zones))
    assert refused(datetime_tool("convert_tz", value="2026-01-01 10:00", from_tz="UTC", to_tz=zones))
    assert datetime_tool("now", tz=["UTC", "Asia/Kolkata"])["ok"]


# --- §2f: truncation that says so -------------------------------------------


def test_recurrence_says_when_it_stopped_early():
    r = datetime_tool("recurrence", rule="FREQ=DAILY", start="2026-08-27", count=1000000)
    assert r["ok"] and len(r["result"]["occurrences"]) == 100
    assert r["result"]["truncated"] is True
    assert any("trunc" in w for w in r["warnings"]), r["warnings"]


def test_holidays_next_caps_n_and_says_so():
    r = holidays("next", region="IN", n=100000)
    assert refused(r) or (r["ok"] and r["warnings"])


def test_regex_match_reports_the_real_total_not_the_limit():
    r = text("regex_match", text="ab" * 10000, pattern="(a)(b)")
    assert r["ok"]
    assert r["result"]["count"] == 10000, "count is the total, not the number returned"
    assert r["result"]["returned"] == 1000 and r["result"]["truncated"] is True
    assert any("trunc" in w for w in r["warnings"])


# --- §1 step 2: recursion in a schema is input, not a crash -----------------


def test_a_self_referential_schema_is_invalid_input_not_a_crash():
    r = validate("json_schema", schema={"$ref": "#"}, data=1)
    assert r["ok"] is False and r["error"] == "invalid_input"
    assert "RecursionError" not in r["message"] and "trace" not in r


def test_a_deeply_nested_schema_is_refused_by_depth():
    schema = {"type": "integer"}
    for _ in range(200):
        schema = {"allOf": [schema]}
    r = validate("json_schema", schema=schema, data=1)
    assert r["ok"] is False and r["error"] in TOO_BIG
    assert "RecursionError" not in r["message"]


def test_an_ordinary_nested_schema_still_validates():
    schema = {"type": "object", "properties": {"a": {"type": "object", "properties": {"b": {"type": "integer"}}}}}
    assert validate("json_schema", schema=schema, data={"a": {"b": 1}})["result"]["valid"] is True
