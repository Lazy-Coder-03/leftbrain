"""#84 and #78: `sort_by` dropped `order`, and one concept had several names.

#84 is the dangerous one. `order: "desc"` worked with the singular `key` and was ignored with
the plural `keys` — and `keys` is the only way to sort on more than one field. An agent asked
for "the top 3 departments by spend" took the first three rows of a silently *ascending* sort
and reported the three lowest. The call succeeded, the data was well formed, and nothing in the
response contradicted it. Anyone spot-checking `sort_by` with `key` would have concluded `order`
was fine.
"""

import pytest

from leftbrain.core.collections_ import collections

ROWS = [{"d": "b", "v": 2}, {"d": "a", "v": 3}, {"d": "a", "v": 1}]


def values(r, field="v"):
    assert r["ok"], r
    return [i[field] for i in r["result"]["items"]]


# --- #84: `order` must mean the same thing under either spelling -----------------------


@pytest.mark.parametrize("spec", [{"key": "v"}, {"keys": ["v"]}, {"keys": [{"field": "v"}]}])
@pytest.mark.parametrize(("order", "expected"), [("desc", [3, 2, 1]), ("descending", [3, 2, 1]), ("asc", [1, 2, 3])])
def test_order_applies_however_the_key_was_spelled(spec, order, expected):
    assert values(collections("sort_by", items=[{"v": 1}, {"v": 3}, {"v": 2}], order=order, **spec)) == expected


def test_a_multi_key_descending_sort_is_actually_descending():
    """The issue's own worked example: ascending was b/2, a/3, a/1 reversed into a/1, a/3, b/2."""
    r = collections("sort_by", items=ROWS, keys=["d", "v"], order="desc")
    assert [(i["d"], i["v"]) for i in r["result"]["items"]] == [("b", 2), ("a", 3), ("a", 1)]


def test_the_top_of_a_descending_sort_is_the_top():
    """The reported consequence: taking the first N gave the N lowest."""
    spend = [{"dept": "a", "spend": 10}, {"dept": "b", "spend": 90}, {"dept": "c", "spend": 50}]
    top = collections("sort_by", items=spend, keys=["spend"], order="desc")["result"]["items"][:2]
    assert [i["dept"] for i in top] == ["b", "c"]


# --- #84: mixed directions were not expressible at all ---------------------------------


def test_a_minus_prefix_means_descending_on_that_key():
    r = collections("sort_by", items=ROWS, keys=["d", "-v"])
    assert [(i["d"], i["v"]) for i in r["result"]["items"]] == [("a", 3), ("a", 1), ("b", 2)]
    assert any("'-v' read as 'v' descending" in a for a in r["assumptions"]), r["assumptions"]


def test_a_field_really_called_minus_v_still_wins():
    """The prefix is only read as a direction when nothing actually has that name."""
    rows = [{"-v": 1}, {"-v": 3}, {"-v": 2}]
    r = collections("sort_by", items=rows, keys=["-v"])
    assert [i["-v"] for i in r["result"]["items"]] == [1, 2, 3]
    assert not any("descending" in a for a in r["assumptions"]), r["assumptions"]


def test_per_key_directions_are_expressible():
    r = collections("sort_by", items=ROWS, keys=[{"field": "d", "order": "asc"}, {"field": "v", "order": "desc"}])
    assert [(i["d"], i["v"]) for i in r["result"]["items"]] == [("a", 3), ("a", 1), ("b", 2)]


def test_a_per_key_order_beats_the_call_level_one():
    r = collections("sort_by", items=[{"v": 1}, {"v": 3}], keys=[{"field": "v", "order": "asc"}], order="desc")
    assert values(r) == [1, 3]


@pytest.mark.parametrize("order", ["down", "reverse", "DESCENDIN"])
def test_an_order_that_cannot_be_applied_is_refused_not_ignored(order):
    r = collections("sort_by", items=[{"v": 1}], keys=["v"], order=order)
    assert not r["ok"] and r["error"] == "invalid_input" and "order must be one of" in r["message"]


def test_order_is_case_insensitive_as_it_always_was():
    assert values(collections("sort_by", items=[{"v": 1}, {"v": 3}], key="v", order="DESC")) == [3, 1]


# --- #78: records are `items` in some modes and `data` in others -----------------------


@pytest.mark.parametrize("mode", ["sort_by", "paginate", "chunk", "find_duplicates", "pick_fields"])
def test_records_are_accepted_under_either_name(mode):
    extra = {"sort_by": {"keys": ["v"]}, "chunk": {"size": 2}, "pick_fields": {"fields": ["v"]}}.get(mode, {})
    rows = [{"v": 1}, {"v": 2}]
    assert collections(mode, items=rows, **extra)["ok"]
    assert collections(mode, data=rows, **extra)["ok"], mode


def test_group_by_takes_the_grouping_key_under_either_name():
    rows = [{"dept": "a", "sal": 1}, {"dept": "a", "sal": 2}, {"dept": "b", "sal": 4}]
    by_key = collections("group_by", items=rows, key="dept", agg="sum", agg_field="sal")
    by_by = collections("group_by", data=rows, by="dept", agg="sum", agg_field="sal")
    assert by_key["ok"] and by_by["ok"]
    assert by_key["result"]["groups"] == by_by["result"]["groups"]


def test_group_by_says_what_field_is_for_rather_than_only_that_key_is_missing():
    """`field` is in group_by's accepted list and is the *aggregation* column, so supplying it
    as the grouping key cost a second round-trip to a bare "'key' is required"."""
    r = collections("group_by", items=[{"dept": "a", "sal": 1}], field="dept", agg="sum", agg_field="sal")
    assert not r["ok"]
    assert "by" in r["message"] and "aggregate" in r["message"], r["message"]


def test_csv_text_still_works_under_both_names():
    csv = "d,v\nb,2\na,3\n"
    assert collections("sort_by", items=csv, keys=["v"], order="desc")["ok"]
    assert collections("sort_by", data=csv, keys=["v"], order="desc")["ok"]
