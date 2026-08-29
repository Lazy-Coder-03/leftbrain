"""Adversarial sweep of `collections`, in the failure classes #52 found in `math`."""

import time

import pytest

from leftbrain.core.collections_ import collections

# --- G. numbers from CSV against numbers from JSON ------------------------------------


def test_a_csv_number_equals_the_same_json_number():
    r = collections("set_ops", a="id\n1\n2\n", b=[{"id": 1}, {"id": 2}], key="id", op="intersection")
    assert r["result"]["count"] == 2, r
    r = collections("set_ops", a="id\n1\n2\n", b=[1, 2], op="intersection")
    assert r["ok"] and r["result"]["count"] == 0 and any("records" in w for w in r["warnings"]), r


def test_rows_without_the_key_are_not_equal_to_each_other():
    r = collections("set_ops", a=[{"id": 1}, {"x": 2}], b=[{"y": 3}], key="id", op="intersection")
    assert r["result"]["count"] == 0 and r["warnings"], r


def test_one_and_true_and_the_string_one_stay_apart_but_int_and_float_do_not():
    r = collections("set_ops", a=[1, "1", True], b=[1.0], op="intersection")
    assert r["result"]["result"] == [1]


# --- B. identifiers are not numbers -------------------------------------------------------


def test_leading_zeros_survive():
    r = collections("to_csv", items="zip,phone\n02134,0123456789\n10001,9876543210\n")
    assert "02134" in r["result"]["csv"] and "0123456789" in r["result"]["csv"], r["result"]["csv"]
    assert any("leading zero" in a for a in r["assumptions"]), r["assumptions"]


def test_one_reading_of_a_decimal_comma_across_modes():
    assert collections("aggregate", items=["12,34"], ops="sum")["result"]["sum"] == "12.34"


def test_numeric_strings_sorting_as_text_is_said():
    r = collections("sort_by", items=[{"v": "10"}, {"v": "9"}, {"v": "100"}], key="v")
    assert any("text" in a and "number" in a for a in r["assumptions"]), r["assumptions"]
    r = collections("sort_by", items=[{"a": 2}, {"a": 1}], key="zzz")
    assert r["ok"] and any("zzz" in w for w in r["warnings"]), r


def test_filter_refuses_a_numeric_comparison_on_a_mixed_column():
    r = collections("filter", items=[{"v": 10}, {"v": "x"}, {"v": 9}], where=[{"field": "v", "op": "gt", "value": 9}])
    assert r["ok"] is False and "text" in r["message"], r
    r = collections("filter", items=[{"b": True}, {"b": False}], where=[{"field": "b", "op": "eq", "value": "maybe"}])
    assert r["ok"] is False and "true or false" in r["message"], r


# --- F. size pre-checks ----------------------------------------------------------------------


def test_chunk_arguments():
    r = collections("chunk", items=[1, 2, 3], size=-1)
    assert r["ok"] is False and "at least 1" in r["message"], r
    r = collections("chunk", items=[1, 2, 3], size=0)
    assert r["ok"] is False and "at least 1" in r["message"]
    started = time.monotonic()
    r = collections("chunk", items=[1, 2], n=10**9)
    assert time.monotonic() - started < 1.0 and r["ok"] is False and r["error"] == "too_large", r


def test_deep_or_huge_structures_are_refused_in_milliseconds():
    deep: list = []
    for _ in range(10000):
        deep = [deep]
    started = time.monotonic()
    r = collections("flatten", data=deep)
    assert time.monotonic() - started < 1.0 and r["ok"] is False and r["error"] == "too_large" and "RecursionError" not in r["message"], r
    started = time.monotonic()
    r = collections("unflatten", data={"a[100000000]": 1})
    assert time.monotonic() - started < 1.0 and r["ok"] is False and r["error"] == "too_large", r


# --- C. raw exception text ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda: collections("unflatten", data={"a[0]": 1, "a.b": 2}),
        lambda: collections("paginate", items=[1], page="two"),
        lambda: collections("running", items=[{"v": 1}], column="v", decimals="x"),
    ],
)
def test_bad_inputs_are_refused_in_words(call):
    r = call()
    assert r["ok"] is False and r["error"] == "invalid_input", r
    for leak in ("TypeError", "ValueError", "invalid literal", "list indices"):
        assert leak not in r["message"], r["message"]


def test_nan_is_not_a_number_here():
    r = collections("aggregate", items=[1, float("nan")], ops="sum")
    assert r["ok"] and r["result"]["sum"] == "1", r
    r = collections("summarize", items=[{"v": float("nan"), "w": 2}, {"v": 1, "w": 3}])
    assert r["ok"] and r["result"]["fields"]["v"]["nulls"] == 1 and r["result"]["fields"]["v"]["sum"] == "1", r
    assert collections("sort_by", items=[{"v": float("nan")}, {"v": 0}], key="v")["ok"]


# --- E. colliding keys ------------------------------------------------------------------------------


def test_colliding_keys_are_refused_whichever_order_they_come_in():
    r = collections("flatten", data={"a.b": 1, "a": {"b": 2}})
    assert r["ok"] is False and "a.b" in r["message"], r
    r = collections("unflatten", data={"a.b": 2, "a": 1})
    assert r["ok"] is False and "'a'" in r["message"], r


def test_find_duplicates_mentions_near_misses():
    r = collections("find_duplicates", items=["a", "A", " a", "b"])
    assert r["result"]["has_duplicates"] is False and any("case_insensitive" in a for a in r["assumptions"]), r


def test_string_flags_read_the_word_false():
    r = collections("summarize", items="a,b\n1,2\n", has_header="false")
    assert r["ok"] and r["result"]["count"] == 2, r
