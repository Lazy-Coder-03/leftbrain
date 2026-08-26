"""Tests for scale, convert, holidays, numbers, text, collections, validate, random, geo, encode."""

import leftbrain as lb

# --- scale -----------------------------------------------------------------

def test_scale_recipe_exact_fractions():
    r = lb.scale_tool(from_qty=4, to_qty=7, entities=[{"name": "flour", "qty": "2.5"}, {"name": "eggs", "qty": 3, "integer": True}])
    flour, eggs = r["result"]["entities"]
    assert r["result"]["factor"]["exact"] == "7/4"
    assert flour["scaled"]["mixed"] == "4 3/8"
    assert eggs["scaled"]["value"] == 6  # ceil(5.25)


def test_scale_with_unit_conversion():
    r = lb.scale_tool(from_qty=1, from_unit="kg", to_qty=250, to_unit="g", entities=[{"name": "price", "qty": 480}])
    assert r["result"]["entities"][0]["scaled"]["value"] == 120


def test_scale_inverse():
    r = lb.scale_tool(from_qty=3, to_qty=12, mode="inverse", entities=[{"name": "days", "qty": 5}])
    assert r["result"]["entities"][0]["scaled"]["exact"] == "5/4"


# --- convert ---------------------------------------------------------------

def test_convert_units_and_ambiguity():
    assert abs(lb.convert_tool(value=5, from_unit="km", to_unit="miles")["result"]["value"] - 3.10686) < 1e-4
    r = lb.convert_tool(value=2, from_unit="ton", to_unit="kg")
    assert r["error"] == "ambiguous" and "metric_ton" in r["needs"]["options"]
    assert lb.convert_tool(value=2, from_unit="ton", to_unit="kg", assume="common")["result"]["value"] == 2000


def test_convert_temperature():
    assert lb.convert_tool(value=100, from_unit="C", to_unit="F")["result"]["value"] == 212
    assert lb.convert_tool(value=10, from_unit="C", to_unit="F", delta=True)["result"]["value"] == 18


def test_convert_currency_requires_rates():
    assert lb.convert_tool(value=100, from_unit="USD", to_unit="INR")["error"] == "needs_rates"
    assert lb.convert_tool(value=100, from_unit="USD", to_unit="INR", rate=83.5)["result"]["value"] == 8350
    r = lb.convert_tool(value=100, from_unit="EUR", to_unit="INR", rates={"USD": 1, "EUR": 0.9, "INR": 83.5})
    assert abs(r["result"]["value"] - 9277.78) < 0.01


# --- holidays --------------------------------------------------------------

def test_holidays():
    r = lb.holidays_tool("check", region="IN", date="2026-01-26")
    assert r["result"]["is_holiday"] and "Republic" in r["result"]["name"]
    assert lb.holidays_tool("list", region="US", year=2026, month=7)["result"]["count"] >= 1


# --- numbers ---------------------------------------------------------------

def test_numbers():
    assert lb.numbers_tool("compare", values=["9.11", "9.9"])["result"]["max"]["input"] == "9.9"
    assert lb.numbers_tool("round", value="2.675", decimals=2)["result"]["value"] == "2.68"
    assert lb.numbers_tool("round", value="2.5", decimals=0, rounding="half_even")["result"]["value"] == "2"
    assert lb.numbers_tool("format", value=12345678.5, locale="en_IN", style="currency", currency="INR")["result"]["formatted"] == "₹1,23,45,678.50"
    assert lb.numbers_tool("format", value=1234567.891, locale="de_DE", decimals=2)["result"]["formatted"] == "1.234.567,89"
    alloc = lb.numbers_tool("allocate", total=100, parts=3)["result"]
    assert alloc["sum_of_shares"] == "100" and [i["share"] for i in alloc["items"]] == ["33.34", "33.33", "33.33"]
    assert lb.numbers_tool("parse", value="₹1.2 Cr")["result"]["value"] == "12000000"
    words = lb.numbers_tool("to_words", value=123456.5, system="indian", currency="INR")["result"]["words"]
    assert words == "One lakh twenty-three thousand four hundred fifty-six rupees and fifty paise only"
    assert lb.numbers_tool("to_words", value=1000000)["result"]["words"] == "one million"


# --- text ------------------------------------------------------------------

def test_text():
    assert lb.text_tool("count", text="strawberry", what="occurrences", substring="r")["result"]["count"] == 3
    assert lb.text_tool("sort", items=["file10", "file2", "File1"])["result"]["sorted"] == ["File1", "file2", "file10"]
    d = lb.text_tool("diff", a="a b c", b="a B c d", granularity="word")["result"]
    assert d["added"] == 2 and d["removed"] == 1
    ex = lb.text_tool("extract", text="mail x@y.io, GST 27AAPFU0939F1ZV", what=["emails", "gstin"])["result"]
    assert ex["emails"] == ["x@y.io"] and ex["gstin"] == ["27AAPFU0939F1ZV"]
    assert lb.text_tool("regex_replace", pattern=r"\d+", text="a1b22", replacement="#")["result"]["text"] == "a#b#"
    assert lb.text_tool("regex_match", pattern="[", text="x")["error"] == "invalid_input"


# --- collections -----------------------------------------------------------

def test_collections():
    s = lb.collections_tool("set_ops", op="compare", a=[1, 2, 3], b=[3, 4])["result"]
    assert s["only_in_a"] == [1, 2] and s["only_in_b"] == [4] and s["in_both"] == [3]
    g = lb.collections_tool("group_by", items=[{"d": "eng", "s": 10}, {"d": "eng", "s": 20}, {"d": "ops", "s": 5}], key="d", agg_field="s", agg=["sum"])["result"]
    assert {x["key"]: x["agg"]["sum"] for x in g["groups"]} == {"eng": "30", "ops": "5"}
    f = lb.collections_tool("flatten", data={"a": {"b": [1, {"c": 2}]}})["result"]["flat"]
    assert f == {"a.b[0]": 1, "a.b[1].c": 2}
    assert lb.collections_tool("unflatten", data=f)["result"]["data"] == {"a": {"b": [1, {"c": 2}]}}
    srt = lb.collections_tool("sort_by", items=[{"n": "b", "a": 2}, {"n": "a", "a": 2}, {"n": "c", "a": 1}], keys=[{"field": "a", "order": "desc"}, {"field": "n"}])["result"]["items"]
    assert [x["n"] for x in srt] == ["a", "b", "c"]
    assert lb.collections_tool("paginate", items=list(range(45)), page=3, per_page=20)["result"]["items"] == list(range(40, 45))


# --- validate --------------------------------------------------------------

def test_validate_assert_scoring():
    r = lb.validate_tool("assert", data={"leave": {"days": 3, "balance": 2}, "date": "2026-09-01"}, rules=[{"path": "leave.days", "op": "lte", "value": 2}, {"path": "date", "op": "after", "value": "2026-08-26"}])["result"]
    assert r["passed"] == 1 and r["failed"] == 1 and r["score"] == 0.5


def test_validate_ids():
    assert lb.validate_tool("id", kind="gstin", value="27AAPFU0939F1ZV")["result"]["valid"]
    assert not lb.validate_tool("id", kind="gstin", value="27AAPFU0939F1ZX")["result"]["valid"]
    assert lb.validate_tool("id", kind="pan", value="ABCPE1234F")["result"]["holder_type"] == "individual"
    assert lb.validate_tool("id", kind="card", value="4111 1111 1111 1111")["result"]["brand"] == "visa"
    assert not lb.validate_tool("id", kind="card", value="4111 1111 1111 1112")["result"]["valid"]
    assert lb.validate_tool("id", kind="iban", value="GB82 WEST 1234 5698 7654 32")["result"]["valid"]
    assert lb.validate_tool("id", kind="isbn", value="978-0-306-40615-7")["result"]["valid"]
    assert lb.validate_tool("id", kind="ean", value="4006381333931")["result"]["valid"]
    assert lb.validate_tool("id", kind="ifsc", value="SBIN0001234")["result"]["valid"]
    assert lb.validate_tool("id", kind="aadhaar", value="2234 5678 9014")["result"]["valid"] in (True, False)


def test_validate_misc():
    assert lb.validate_tool("email", value="A@b.co")["result"]["valid"]
    assert not lb.validate_tool("email", value="a..b@c.com")["result"]["valid"]
    assert lb.validate_tool("phone", value="98765 43210", region="IN")["result"]["e164"] == "+919876543210"
    assert not lb.validate_tool("url", value="example.com")["result"]["valid"]
    sql = lb.validate_tool("sql_parse", sql="DELETE FROM employees")["result"]
    assert not sql["read_only"] and sql["statements"][0]["unbounded"]
    assert lb.validate_tool("sql_parse", sql="SELECT * FROM t WHERE id = 1")["result"]["read_only"]
    assert not lb.validate_tool("json_schema", schema={"type": "object", "required": ["a"]}, data={})["result"]["valid"]


# --- random ----------------------------------------------------------------

def test_random():
    assert lb.random_tool("uuid", version=7)["result"]["uuid"][14] == "7"
    a = lb.random_tool("int", min=1, max=6, n=5, seed=42)["result"]["values"]
    b = lb.random_tool("int", min=1, max=6, n=5, seed=42)["result"]["values"]
    assert a == b and all(1 <= x <= 6 for x in a)
    assert len(lb.random_tool("token", kind="hex", length=16)["result"]["token"]) == 16
    assert lb.random_tool("pick", items=[1, 2, 3], n=3)["result"]["count"] == 3


# --- geo -------------------------------------------------------------------

def test_geo():
    assert lb.geo_tool("tz_for_place", place="Mumbai")["result"]["zone"] == "Asia/Kolkata"
    assert lb.geo_tool("tz_for_place", place="USA")["error"] == "ambiguous"
    d = lb.geo_tool("distance", **{"from": "Kolkata", "to": "London"})["result"]
    assert 7900 < d["km"] < 8000
    assert lb.geo_tool("country", value="India")["result"]["single_timezone"]


# --- encode ----------------------------------------------------------------

def test_encode():
    assert lb.encode_tool("hash", text="hello", algo="sha256")["result"]["hex"] == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert lb.encode_tool("base64", action="decode", text="aGVsbG8=")["result"]["text"] == "hello"
    assert lb.encode_tool("checksum", text="hello", algo="crc32")["result"]["hex"] == "3610a686"
    assert lb.encode_tool("hmac", text="hello", key="k", algo="sha256")["result"]["hex"]


def test_contract_shape_everywhere():
    for name, fn in lb.TOOLS.items():
        r = fn("__not_a_mode__") if name != "scale" else fn()
        assert r["ok"] is False and "error" in r and "message" in r, name
