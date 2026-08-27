"""collections - CSV text as input, and the table-shaped modes: filter, pivot, running, outliers, summarize, to_csv."""

import pytest

import leftbrain as lb
from leftbrain.core import collections_

CSV = """region,rep,amount,date
north,Asha,"1,200.50",2026-01-05
south,Bo,890.00,2026-01-06
north,Asha,430.25,2026-01-07
east,Chen,2100.00,2026-01-08
south,Bo,75.75,2026-01-09
"""

ROWS = [
    {"region": "north", "rep": "Asha", "amount": "1200.50", "date": "2026-01-05"},
    {"region": "south", "rep": "Bo", "amount": 890, "date": "2026-01-06"},
    {"region": "north", "rep": "Asha", "amount": 430.25, "date": "2026-01-07"},
    {"region": "east", "rep": "Chen", "amount": "2100.00", "date": "2026-01-08"},
    {"region": "south", "rep": "Bo", "amount": "75.75", "date": "2026-01-09"},
]

AMOUNT = {"type": "number", "count": 5, "nulls": 0, "sum": "4696.5", "avg": "939.3", "min": "75.75", "max": "2100", "median": "890"}


# --- loading CSV text --------------------------------------------------------


def test_summarize_csv_is_exact_and_states_the_header_and_types():
    r = lb.collections_tool("summarize", items=CSV)
    assert r["ok"], r
    assert r["result"]["fields"]["amount"] == AMOUNT
    assert r["result"]["count"] == 5
    assert r["result"]["types"] == {"region": "text", "rep": "text", "amount": "number", "date": "date"}
    assert "first row read as the header (no numeric, date or boolean cells in it)" in r["assumptions"]
    assert "inferred types: region=text, rep=text, amount=number, date=date" in r["assumptions"]
    assert "delimiter ',' detected" in r["assumptions"]


def test_csv_text_and_records_load_the_same_table():
    from_csv = lb.collections_tool("summarize", items=CSV)["result"]
    from_records = lb.collections_tool("summarize", items=ROWS)["result"]
    assert from_csv["fields"] == from_records["fields"]
    assert from_csv["types"] == from_records["types"]


def test_has_header_overrides_the_detected_rule():
    r = lb.collections_tool("summarize", items="a,b\n1,2\n3,4\n", has_header=False)
    assert r["ok"] and list(r["result"]["fields"]) == ["col1", "col2"]
    assert r["result"]["fields"]["col1"] == {"type": "text", "count": 3, "nulls": 0, "distinct": 3}
    assert "no header row: the first row holds data, so columns are named col1..colN" not in r["assumptions"]
    r = lb.collections_tool("summarize", items="1,2\n3,4\n")
    assert list(r["result"]["fields"]) == ["col1", "col2"]
    assert "no header row: the first row holds data, so columns are named col1..colN" in r["assumptions"]
    assert r["result"]["fields"]["col2"]["sum"] == "6"
    r = lb.collections_tool("summarize", items="10,20\n1,2\n", has_header=True)
    assert list(r["result"]["fields"]) == ["10", "20"] and r["result"]["fields"]["10"]["sum"] == "1"


def test_blank_rows_and_na_cells_are_skipped_and_reported():
    csv = "name,score\nAnn,10\n,\nBob,N/A\n\nCid,30\nDee,-\n"
    r = lb.collections_tool("summarize", items=csv)
    assert r["ok"], r
    assert r["result"]["count"] == 4
    assert r["result"]["fields"]["score"] == {"type": "number", "count": 2, "nulls": 2, "sum": "40", "avg": "20", "min": "10", "max": "30", "median": "20"}
    assert "2 blank rows skipped" in r["assumptions"]
    assert "cells reading 'N/A', '-' treated as empty (2)" in r["assumptions"]


def test_currency_symbols_and_percentages_are_numbers():
    r = lb.collections_tool("summarize", items="item,price,growth\nA,₹1200,12%\nB,₹800,8%\n")
    f = r["result"]["fields"]
    assert f["price"]["sum"] == "2000" and f["growth"]["sum"] == "0.2"
    assert any(a.startswith("field 'growth':") and "%" in a for a in r["assumptions"])


def test_a_mixed_field_is_text_and_booleans_and_dates_are_typed():
    r = lb.collections_tool("summarize", items="k,v,ok,when\na,1,yes,2026-01-01\nb,two,no,2026-03-15\nc,3,true,\n")
    assert r["result"]["types"] == {"k": "text", "v": "text", "ok": "bool", "when": "date"}
    assert r["result"]["fields"]["v"] == {"type": "text", "count": 3, "nulls": 0, "distinct": 3}
    assert r["result"]["fields"]["ok"] == {"type": "bool", "count": 3, "nulls": 0, "true": 2, "false": 1}
    assert r["result"]["fields"]["when"] == {"type": "date", "count": 2, "nulls": 1, "min": "2026-01-01", "max": "2026-03-15"}


def test_semicolon_and_tab_delimiters_are_sniffed_and_can_be_forced():
    r = lb.collections_tool("summarize", items="a;b\n1;2\n3;4\n")
    assert r["ok"] and r["result"]["fields"]["b"]["sum"] == "6"
    assert "delimiter ';' detected" in r["assumptions"]
    r = lb.collections_tool("summarize", items="a\tb\n1\t2\n")
    assert r["result"]["fields"]["b"]["sum"] == "2"
    r = lb.collections_tool("summarize", items="a|b\n1|2\n", delimiter="|")
    assert r["ok"] and r["result"]["fields"]["b"]["sum"] == "2" and not any("detected" in a for a in r["assumptions"])
    assert not lb.collections_tool("summarize", items="a,b\n1,2\n", delimiter=";;")["ok"]


def test_the_row_cap_is_a_clear_error():
    big = "n\n" + "\n".join(str(i) for i in range(5001)) + "\n"
    r = lb.collections_tool("summarize", items=big)
    assert not r["ok"] and "5,000" in r["message"]
    assert lb.collections_tool("summarize", items="n\n" + "\n".join(str(i) for i in range(5000)))["ok"]
    r = lb.collections_tool("sort_by", items=big, key="n")
    assert not r["ok"] and "5,000" in r["message"]
    assert not lb.collections_tool("summarize", items=[{"n": i} for i in range(5001)])["ok"]


def test_items_must_be_records_or_csv_text():
    assert lb.collections_tool("summarize")["error"] == "invalid_input"
    assert not lb.collections_tool("summarize", items={"a": 1})["ok"]
    assert not lb.collections_tool("summarize", items=[1, 2, 3])["ok"]
    assert not lb.collections_tool("summarize", items=[])["ok"]
    r = lb.collections_tool("summarize", items="   \n")
    assert not r["ok"] and "empty" in r["message"]
    assert not lb.collections_tool("summarize", items="a,b\n1,2,3\n")["ok"]
    assert not lb.collections_tool("summarize", items="a,a\n1,2\n")["ok"]


def test_unknown_fields_are_named_in_the_error():
    r = lb.collections_tool("summarize", items=CSV, columns=["amount", "total"])
    assert not r["ok"] and "total" in r["message"] and "amount" in r["message"]


# --- CSV text into the existing modes ----------------------------------------


def test_existing_modes_take_csv_text_and_state_how_it_was_read():
    r = lb.collections_tool("sort_by", items=CSV, key="amount", order="desc")
    assert r["ok"], r
    assert [x["amount"] for x in r["result"]["items"]] == ["2100", "1200.5", "890", "430.25", "75.75"]
    assert r["result"]["items"][0] == {"region": "east", "rep": "Chen", "amount": "2100", "date": "2026-01-08"}
    assert r["assumptions"][:3] == ["delimiter ',' detected", "first row read as the header (no numeric, date or boolean cells in it)", "inferred types: region=text, rep=text, amount=number, date=date"]
    assert r["assumptions"][-1] == "stable multi-key sort; None sorts last; strings case-insensitive"
    g = lb.collections_tool("group_by", items=CSV, key="region", agg_field="amount", agg=["sum"], include_items=False)["result"]
    assert {x["key"]: x["agg"]["sum"] for x in g["groups"]} == {"east": "2100", "north": "1630.75", "south": "965.75"}
    assert lb.collections_tool("paginate", items=CSV, page=2, per_page=2)["result"]["items"][0]["rep"] == "Asha"
    assert lb.collections_tool("aggregate", items=CSV, field="amount", ops=["sum"])["result"]["sum"] == "4696.50"
    assert lb.collections_tool("pick_fields", items=CSV, fields=["rep"])["result"]["items"][3] == {"rep": "Chen"}
    assert lb.collections_tool("find_duplicates", items=CSV, key="rep")["result"]["duplicate_groups"] == 2
    assert lb.collections_tool("chunk", items=CSV, size=2)["result"]["sizes"] == [2, 2, 1]
    f = lb.collections_tool("flatten", data="a,b\n1,2\n")["result"]
    assert f == {"items": [{"a": "1", "b": "2"}], "count": 1}


def test_set_ops_compares_two_csv_texts_on_a_key():
    r = lb.collections_tool("set_ops", a="sku,qty\nA1,2\nB2,1\n", b="sku,qty\nB2,9\nC3,4\n", op="difference", key="sku")
    assert r["ok"], r
    assert r["result"]["result"] == [{"sku": "A1", "qty": "2"}]
    assert [x["sku"] for x in r["result"]["only_in_b"]] == ["C3"]
    assert "a: delimiter ',' detected" in r["assumptions"] and "b: inferred types: sku=text, qty=number" in r["assumptions"]


def test_a_blank_cell_in_csv_is_null_and_unflatten_still_wants_an_object():
    r = lb.collections_tool("sort_by", items="name,qty\nb,10\na,9\nc,\nd,100\n", key="qty")
    assert [x["name"] for x in r["result"]["items"]] == ["a", "b", "d", "c"]
    assert r["result"]["items"][-1] == {"name": "c", "qty": None}
    assert not lb.collections_tool("unflatten", data="a.b,c\n1,2\n")["ok"]


def test_record_input_is_untouched_by_the_csv_path():
    r = lb.collections_tool("sort_by", items=[{"n": "b", "a": "1,200"}, {"n": "a", "a": 2}], key="a")
    assert r["assumptions"] == ["stable multi-key sort; None sorts last; strings case-insensitive"]
    assert r["result"]["items"] == [{"n": "a", "a": 2}, {"n": "b", "a": "1,200"}]
    assert lb.collections_tool("chunk", items=[[1, 2], [3]], size=1)["result"]["chunks"] == [[[1, 2]], [[3]]]


# --- filter ------------------------------------------------------------------


def test_filter_compares_in_the_inferred_type():
    r = lb.collections_tool("filter", items=CSV, where=[{"field": "amount", "op": "gte", "value": "500"}])
    assert r["ok"], r
    assert r["result"]["count"] == 3 and r["result"]["removed"] == 2
    assert r["result"]["items"][0] == {"region": "north", "rep": "Asha", "amount": "1200.5", "date": "2026-01-05"}
    assert "every predicate must hold (AND); text comparisons are case-sensitive" in r["assumptions"]
    r = lb.collections_tool("filter", items=ROWS, where=[{"field": "region", "op": "in", "value": ["north", "east"]}, {"field": "rep", "op": "contains", "value": "sh"}])
    assert [x["rep"] for x in r["result"]["items"]] == ["Asha", "Asha"]
    r = lb.collections_tool("filter", items=CSV, where=[{"field": "date", "op": "gt", "value": "2026-01-07"}])
    assert r["result"]["count"] == 2
    r = lb.collections_tool("filter", items=CSV, where=[{"field": "rep", "op": "starts_with", "value": "B"}], columns=["rep", "amount"])
    assert r["result"]["items"] == [{"rep": "Bo", "amount": "890"}, {"rep": "Bo", "amount": "75.75"}]
    r = lb.collections_tool("filter", items="name,qty\nb,10\na,\nc,7\n", where=[{"field": "qty", "op": "empty"}])
    assert [x["name"] for x in r["result"]["items"]] == ["a"]
    r = lb.collections_tool("filter", items="name,qty\nb,10\na,\nc,7\n", where=[{"field": "qty", "op": "ne", "value": 10}])
    assert [x["name"] for x in r["result"]["items"]] == ["a", "c"]
    r = lb.collections_tool("filter", items="name,ok\nb,yes\na,no\n", where=[{"field": "ok", "op": "eq", "value": "true"}])
    assert [x["name"] for x in r["result"]["items"]] == ["b"]


def test_filter_rejects_bad_predicates():
    assert not lb.collections_tool("filter", items=CSV)["ok"]
    assert not lb.collections_tool("filter", items=CSV, where=[])["ok"]
    r = lb.collections_tool("filter", items=CSV, where=[{"field": "amount", "op": "like", "value": "1"}])
    assert not r["ok"] and "like" in r["message"]
    assert not lb.collections_tool("filter", items=CSV, where=[{"field": "total", "op": "eq", "value": "1"}])["ok"]
    assert not lb.collections_tool("filter", items=CSV, where=[{"field": "region", "op": "in", "value": "north"}])["ok"]
    assert not lb.collections_tool("filter", items=CSV, where=[{"field": "amount", "op": "gt", "value": None}])["ok"]
    assert not lb.collections_tool("filter", items="name,ok\nb,yes\na,no\n", where=[{"field": "ok", "op": "gt", "value": "no"}])["ok"]


# --- pivot -------------------------------------------------------------------


def test_pivot_with_row_and_column_totals():
    r = lb.collections_tool("pivot", items=CSV, by="region", pivot_columns="rep", column="amount")
    assert r["ok"], r
    assert r["result"]["columns"] == ["region", "Asha", "Bo", "Chen", "total"]
    assert r["result"]["rows"] == [
        {"region": "east", "Asha": None, "Bo": None, "Chen": "2100", "total": "2100"},
        {"region": "north", "Asha": "1630.75", "Bo": None, "Chen": None, "total": "1630.75"},
        {"region": "south", "Asha": None, "Bo": "965.75", "Chen": None, "total": "965.75"},
    ]
    assert r["result"]["totals"] == {"Asha": "1630.75", "Bo": "965.75", "Chen": "2100", "total": "4696.5"}
    assert r["result"]["row_count"] == 3
    assert "cells are sum of 'amount'" in r["assumptions"]


def test_pivot_counts_without_a_metric_and_takes_other_aggregates():
    r = lb.collections_tool("pivot", items=ROWS, by="region", pivot_columns="rep", agg="count")
    assert r["result"]["rows"][1] == {"region": "north", "Asha": 2, "Bo": None, "Chen": None, "total": 2}
    assert r["result"]["totals"]["total"] == 5
    assert "cells are row counts" in r["assumptions"]
    r = lb.collections_tool("pivot", items=CSV, by=["region", "rep"], pivot_columns="date", column="amount", agg="max")
    assert r["result"]["columns"][:2] == ["region", "rep"] and r["result"]["rows"][0]["total"] == "2100"
    r = lb.collections_tool("pivot", items=CSV, by="rep", pivot_columns="region", column="amount", agg="avg", decimals=1)
    assert r["result"]["rows"][0] == {"rep": "Asha", "east": None, "north": "815.4", "south": None, "total": "815.4"}


def test_pivot_refusals():
    assert not lb.collections_tool("pivot", items=CSV, by="region", column="amount")["ok"]
    assert not lb.collections_tool("pivot", items=CSV, pivot_columns="rep", column="amount")["ok"]
    assert not lb.collections_tool("pivot", items=CSV, by="region", pivot_columns="rep", agg="sum")["ok"]
    assert not lb.collections_tool("pivot", items=CSV, by="region", pivot_columns="rep", column="rep")["ok"]
    assert not lb.collections_tool("pivot", items=CSV, by="region", pivot_columns="rep", column="amount", agg=["sum", "avg"])["ok"]
    assert not lb.collections_tool("pivot", items=CSV, by="region", pivot_columns="rep", column="amount", agg="stdev")["ok"]
    r = lb.collections_tool("pivot", items="k,v,n\na,k,1\n", by="k", pivot_columns="v", column="n")
    assert not r["ok"] and "collide" in r["message"]


# --- running -----------------------------------------------------------------


def test_running_totals_overall_and_per_group():
    r = lb.collections_tool("running", items=CSV, column="amount")
    assert r["ok"], r
    assert [x["running"] for x in r["result"]["items"]] == ["1200.5", "2090.5", "2520.75", "4620.75", "4696.5"]
    assert r["result"]["items"][0] == {"region": "north", "rep": "Asha", "amount": "1200.5", "date": "2026-01-05", "running": "1200.5"}
    assert r["result"]["total"] == "4696.5" and r["result"]["column"] == "amount" and r["result"]["count"] == 5
    assert "rows are accumulated in the order given; a blank cell adds nothing" in r["assumptions"]
    r = lb.collections_tool("running", items=CSV, column="amount", by="region", columns=["region", "amount"])
    assert [x["running"] for x in r["result"]["items"]] == ["1200.5", "890", "1630.75", "2100", "965.75"]
    assert r["result"]["items"][0] == {"region": "north", "amount": "1200.5", "running": "1200.5"}
    assert r["result"]["by"] == ["region"]
    assert r["result"]["totals"] == [{"region": "north", "total": "1630.75"}, {"region": "south", "total": "965.75"}, {"region": "east", "total": "2100"}]
    r = lb.collections_tool("running", items="d,v\n1,1.5\n2,\n3,2\n", column="v")
    assert [x["running"] for x in r["result"]["items"]] == ["1.5", "1.5", "3.5"]


def test_the_only_numeric_field_is_assumed_and_two_are_ambiguous():
    r = lb.collections_tool("running", items=CSV)
    assert r["ok"] and "'amount' used: it is the only numeric field" in r["assumptions"]
    r = lb.collections_tool("running", items="a,b\n1,2\n3,4\n")
    assert r["error"] == "ambiguous" and r["needs"] == {"field": "column", "options": ["a", "b"]}
    r = lb.collections_tool("outliers", items="a,b\n1,2\n3,4\n5,6\n7,8\n")
    assert r["error"] == "ambiguous" and r["needs"]["options"] == ["a", "b"]
    assert not lb.collections_tool("running", items="a,b\nx,y\n")["ok"]
    r = lb.collections_tool("running", items=CSV, column="rep")
    assert not r["ok"] and "not numeric" in r["message"]


# --- outliers ----------------------------------------------------------------


def test_outliers_by_the_iqr_rule_with_fences():
    csv = "day,sales\n" + "\n".join(f"d{i},{v}" for i, v in enumerate([1, 2, 3, 4, 5, 6, 7, 8, 9, 100]))
    r = lb.collections_tool("outliers", items=csv, column="sales")
    assert r["ok"], r
    res = r["result"]
    assert (res["q1"], res["q3"], res["iqr"]) == ("3", "8", "5")
    assert (res["lower_fence"], res["upper_fence"]) == ("-4.5", "15.5")
    assert res["outliers"] == [{"row": 10, "value": "100", "side": "high", "day": "d9", "sales": "100"}]
    assert res["outlier_count"] == 1 and res["count"] == 10 and res["multiplier"] == "1.5"
    assert any(a.startswith("Tukey hinges") for a in r["assumptions"])
    r = lb.collections_tool("outliers", items=[{"v": v} for v in (10, 12, 11, 13, 12, -50)])
    assert r["result"]["outliers"] == [{"row": 6, "value": "-50", "side": "low", "v": "-50"}]
    assert (r["result"]["q1"], r["result"]["q3"]) == ("10", "12")


def test_outliers_need_four_values_and_round_the_fences_only_when_asked():
    r = lb.collections_tool("outliers", items="v\n1\n2\n3\n", column="v")
    assert not r["ok"] and "at least 4" in r["message"]
    assert not lb.collections_tool("outliers", items="v\n1\n2\n3\n\n", column="v")["ok"]
    r = lb.collections_tool("outliers", items="v\n1.5\n2.25\n2.5\n3.75\n4\n40\n", column="v", decimals=2)
    assert (r["result"]["q1"], r["result"]["q3"], r["result"]["iqr"]) == ("2.25", "4.00", "1.75")
    assert (r["result"]["lower_fence"], r["result"]["upper_fence"]) == ("-0.38", "6.63")
    assert r["result"]["outliers"][0]["value"] == "40"
    assert "computed values rounded to 2 decimals, half-up" in r["assumptions"]


# --- summarize / decimals ----------------------------------------------------


def test_summarize_can_be_limited_to_some_fields():
    r = lb.collections_tool("summarize", items=CSV, columns=["amount"])
    assert list(r["result"]["fields"]) == ["amount"] and r["result"]["types"] == {"amount": "number"}


def test_decimals_round_the_computed_values_half_up_and_not_the_data():
    r = lb.collections_tool("summarize", items=CSV, decimals=2)
    assert r["result"]["fields"]["amount"]["avg"] == "939.30" and r["result"]["fields"]["amount"]["max"] == "2100.00"
    assert "computed values rounded to 2 decimals, half-up" in r["assumptions"]
    r = lb.collections_tool("running", items=CSV, column="amount", decimals=0)
    assert r["result"]["items"][2]["amount"] == "430.25" and r["result"]["items"][2]["running"] == "2521"
    assert r["result"]["total"] == "4697"


# --- to_csv ------------------------------------------------------------------


def test_to_csv_writes_records_in_plain_decimal_form():
    r = lb.collections_tool("to_csv", items=ROWS)
    assert r["ok"], r
    lines = r["result"]["csv"].splitlines()
    assert lines[0] == "region,rep,amount,date" and lines[1] == "north,Asha,1200.5,2026-01-05"
    assert r["result"]["count"] == 5 and r["result"]["columns"] == ["region", "rep", "amount", "date"]
    assert any("plain decimal form" in a for a in r["assumptions"])


def test_to_csv_restricts_columns_takes_a_delimiter_and_round_trips():
    r = lb.collections_tool("to_csv", items=ROWS, columns=["rep", "amount"], delimiter=";")
    assert r["result"]["csv"].splitlines()[:2] == ["rep;amount", "Asha;1200.5"]
    assert not lb.collections_tool("to_csv", items=ROWS, columns=["nope"])["ok"]
    assert not lb.collections_tool("to_csv", items=ROWS, delimiter="ab")["ok"]
    back = lb.collections_tool("to_csv", items=r["result"]["csv"], delimiter=";")["result"]["csv"]
    assert back == r["result"]["csv"]
    r = lb.collections_tool("to_csv", items=[{"a": "x,y", "b": None, "c": True}])
    assert r["result"]["csv"] == 'a,b,c\n"x,y",,true\n'


# --- echoed rows -------------------------------------------------------------


def test_long_results_are_truncated_with_a_warning_except_to_csv():
    rows = [{"i": i} for i in range(600)]
    r = lb.collections_tool("filter", items=rows, where=[{"field": "i", "op": "gte", "value": 0}])
    assert len(r["result"]["items"]) == 500 and r["result"]["count"] == 600
    assert r["warnings"] == ["showing the first 500 of 600 rows"]
    r = lb.collections_tool("running", items=rows, column="i")
    assert len(r["result"]["items"]) == 500 and r["result"]["total"] == "179700"
    assert len(lb.collections_tool("to_csv", items=rows)["result"]["csv"].splitlines()) == 601
    assert lb.collections_tool("to_csv", items=rows)["warnings"] == []


# --- wiring ------------------------------------------------------------------


def test_the_modes_are_wired_and_documented():
    assert collections_.MODES == ("set_ops", "group_by", "pick_fields", "flatten", "unflatten", "paginate", "find_duplicates", "sort_by", "aggregate", "chunk", "filter", "pivot", "running", "outliers", "summarize", "to_csv")
    assert set(collections_.EXAMPLES) == set(collections_.MODES)
    assert lb.TOOLS["collections"] is lb.collections_tool
    mcp = pytest.importorskip("leftbrain.mcp_server")
    r = mcp.collections(mode="pivot", items=CSV, by="region", pivot_columns="rep", column="amount", decimals=2)
    assert r["ok"] and r["result"]["totals"]["total"] == "4696.50"
    r = mcp.collections(mode="group_by", items=CSV, key="region", agg_field="amount", agg="sum", include_items=False)
    assert r["ok"] and r["result"]["groups"][0]["agg"]["sum"] == "2100"
    r = mcp.collections(mode="to_csv", items=ROWS, columns=["rep"], delimiter="\t")
    assert r["ok"] and r["result"]["csv"].splitlines()[1] == "Asha"
