"""#28 §4: a predictable input must be caught and phrased, not reported as a Python exception.

`ZeroDivisionError: Fraction(1, 0)` tells the caller what broke inside leftbrain. It does not
tell them what they did wrong or what to do instead, which is the only thing a message is for.
Each case here is an input a mode should have expected.
"""

import re

import pytest

from leftbrain.core.collections_ import collections
from leftbrain.core.convert import convert
from leftbrain.core.datetimex import datetime_tool
from leftbrain.core.finance import finance
from leftbrain.core.mathx import math as math_tool
from leftbrain.core.scale import scale

#: Any of these in a message means an exception escaped instead of being phrased.
LEAKED = re.compile(
    r"ZeroDivisionError|OverflowError|InvalidOperation|RecursionError|AttributeError"
    r"|TypeError:|ValueError:|KeyError:|IndexError:|Traceback|set_int_max_str_digits|Fraction\("
)

CASES = [
    ("unflatten a key that is both a value and a prefix", lambda: collections("unflatten", data={"a": 1, "a.b": 2})),
    ("compound over a million years", lambda: finance("compound", principal=1000, rate=5, rate_period="annual", years=1000000, compounding="daily")),
    ("scale to zero", lambda: scale(mode="inverse", from_qty=3, to_qty=0, entities=[{"name": "days", "qty": 5}])),
    ("add a billion years", lambda: datetime_tool("add", value="2026-01-01", amount=10**9, unit="years")),
    ("convert an infinite value", lambda: convert("units", value=float("inf"), from_unit="km", to_unit="mi")),
]


@pytest.mark.parametrize(("name", "call"), CASES, ids=[c[0] for c in CASES])
def test_a_predictable_input_is_phrased_not_raised(name, call):
    r = call()
    assert r["ok"] is False, name
    assert not LEAKED.search(r["message"]), f"{name}: {r['message']}"
    assert "trace" not in r


@pytest.mark.parametrize(("name", "call"), CASES, ids=[c[0] for c in CASES])
def test_none_of_them_is_an_internal_error(name, call):
    """`internal` means leftbrain broke. These are all things the caller can fix."""
    assert call()["error"] != "internal", name


def test_the_unflatten_clash_names_the_key():
    r = collections("unflatten", data={"a": 1, "a.b": 2})
    assert r["error"] == "invalid_input"
    assert "'a'" in r["message"] and "prefix" in r["message"]


def test_scale_to_zero_names_the_parameter():
    r = scale(mode="inverse", from_qty=3, to_qty=0, entities=[{"name": "days", "qty": 5}])
    assert "to_qty" in r["message"] and "0" in r["message"]


def test_a_date_beyond_the_calendar_says_so():
    r = datetime_tool("add", value="2026-01-01", amount=10**9, unit="years")
    assert r["error"] in ("invalid_input", "too_large")
    assert "calendar" in r["message"] and "1,000,000,000" in r["message"]


def test_compound_over_a_million_years_is_refused_as_input():
    r = finance("compound", principal=1000, rate=5, rate_period="annual", years=1000000, compounding="daily")
    assert r["error"] == "invalid_input" and r["retryable"] is False


def test_an_infinite_value_is_refused_with_words():
    """A client that writes `1e400` as a JSON number hands us `inf`, not a big number."""
    r = convert("units", value=float("inf"), from_unit="km", to_unit="mi")
    assert r["ok"] is False and "infinite" in r["message"]


def test_an_absurd_magnitude_is_rendered_rather_than_refused():
    """Changed deliberately: 1e400 km *is* 1e403 m, and the factor is exact. Only `value`,
    a JSON number, has a range - so the exact answer is reported beside it and the loss is
    named, rather than the whole call being refused for something that is representable."""
    r = convert("units", value="1e400", from_unit="km", to_unit="m")
    assert r["ok"]
    assert r["result"]["value_exact"].startswith("1000000")
    assert r["result"]["value"] == float("inf")
    assert any("largest representable" in w for w in r["warnings"])


def test_a_tiny_magnitude_is_reported_as_zero_and_says_why():
    r = convert("units", value="1e-400", from_unit="km", to_unit="m")
    assert r["ok"] and r["result"]["value"] == 0.0
    assert r["result"]["value_exact"] and any("smallest representable" in w for w in r["warnings"])


def test_an_ordinary_conversion_gains_no_extra_fields():
    r = convert("units", value=5, from_unit="km", to_unit="m")
    assert r["result"]["value"] == 5000.0 and "value_exact" not in r["result"] and not r["warnings"]


# --- the ones that still work must keep working -----------------------------


def test_ordinary_calls_are_untouched():
    assert collections("unflatten", data={"a.b": 1, "a.c": 2})["result"]["data"] == {"a": {"b": 1, "c": 2}}
    assert finance("compound", principal=1000, rate=5, rate_period="annual", years=10)["ok"]
    assert scale(mode="inverse", from_qty=3, to_qty=12, entities=[{"name": "days", "qty": 5}])["ok"]
    assert datetime_tool("add", value="2026-01-01", amount=3, unit="years")["result"]["date"] == "2029-01-01"
    assert convert("units", value=5, from_unit="km", to_unit="m")["result"]["value"] == 5000.0


def test_math_no_longer_mentions_the_interpreter_limit():
    """The digit estimate refuses first, so the `sys.set_int_max_str_digits` text is unreachable."""
    r = math_tool("eval", expr="2^1000000")
    assert r["ok"] is False and not LEAKED.search(r["message"])
