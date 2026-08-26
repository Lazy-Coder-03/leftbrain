"""Quick end-to-end smoke run over every core tool. Run: python scripts/smoke.py"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import leftbrain as lb  # noqa: E402


def show(label: str, r: dict) -> None:
    s = json.dumps(r, default=str)
    print(f"[{'OK ' if r.get('ok') else 'ERR'}] {label}: {s[:300]}")


TESTS = [
    ("math eval", lambda: lb.math_tool("eval", expr="15% of 200 + sqrt(2)^2")),
    ("math trig no angle", lambda: lb.math_tool("eval", expr="sin(30)")),
    ("math trig deg", lambda: lb.math_tool("eval", expr="sin(30)", angle="deg")),
    ("math complex", lambda: lb.math_tool("eval", expr="(3+4i)*(1-2i)")),
    ("math exact", lambda: lb.math_tool("exact", expr="0.75 + 1/3")),
    ("math solve", lambda: lb.math_tool("solve", equations=["x^2 + 1 = 0"])),
    ("math solve real", lambda: lb.math_tool("solve", equations=["x^2 - 4 = 0"], domain="real")),
    ("math solve system", lambda: lb.math_tool("solve", equations=["x + y = 10", "x - y = 2"])),
    ("math diff", lambda: lb.math_tool("diff", expr="sin(x)*exp(x)")),
    ("math integrate", lambda: lb.math_tool("integrate", expr="x^2", **{"from": 0, "to": 3})),
    ("math limit", lambda: lb.math_tool("limit", expr="sin(x)/x", to=0)),
    ("math series", lambda: lb.math_tool("series", expr="exp(x)", order=4)),
    ("math ode", lambda: lb.math_tool("ode", equation="y'' + y = 0", func="y(x)")),
    ("math matrix det", lambda: lb.math_tool("matrix", op="det", A=[[1, 2], [3, 4]])),
    ("math matrix eig", lambda: lb.math_tool("matrix", op="eig", A=[[2, 0], [0, 3]])),
    ("math stats", lambda: lb.math_tool("stats", op="describe", data=[1, 2, 3, 4, 10])),
    ("math polar", lambda: lb.math_tool("convert_form", expr="3+4i", to="polar")),
    ("math plot", lambda: lb.math_tool("plot_points", expr="x^2", range=[-2, 2], n=5)),
    ("math safety", lambda: lb.math_tool("eval", expr="__import__('os').system('dir')")),
    ("math safety2", lambda: lb.math_tool("eval", expr="open('x')")),
    ("dt now", lambda: lb.datetime_tool("now", tz="Asia/Kolkata")),
    ("dt IST ambiguous", lambda: lb.datetime_tool("now", tz="IST")),
    ("dt convert", lambda: lb.datetime_tool("convert_tz", value="2026-03-08 09:30", from_tz="Asia/Kolkata", to_tz=["America/New_York", "Europe/London"])),
    ("dt parse ambiguous", lambda: lb.datetime_tool("parse", value="03/04/2025")),
    ("dt parse IN", lambda: lb.datetime_tool("parse", value="03/04/2025", locale="IN")),
    ("dt parse rel", lambda: lb.datetime_tool("parse", value="next friday at 5pm", ref_date="2026-08-26", tz="Asia/Kolkata")),
    ("dt add months", lambda: lb.datetime_tool("add", value="2026-01-31", amount=1, unit="month")),
    ("dt add bdays", lambda: lb.datetime_tool("add", value="2026-08-26", amount=10, unit="business_days", region="IN")),
    ("dt diff", lambda: lb.datetime_tool("diff", **{"from": "2024-02-29", "to": "2026-08-26"})),
    ("dt weekday", lambda: lb.datetime_tool("weekday", value="2026-08-26")),
    ("dt nth", lambda: lb.datetime_tool("nth_weekday", year=2026, month=9, weekday="tuesday", n=2)),
    ("dt bdays", lambda: lb.datetime_tool("business_days", region="IN", **{"from": "2026-10-01", "to": "2026-10-31"})),
    ("dt overlap", lambda: lb.datetime_tool("overlap", a={"start": "2026-08-26T09:00", "end": "2026-08-26T11:00"}, b={"start": "2026-08-26T10:30", "end": "2026-08-26T12:00"})),
    ("dt dursum", lambda: lb.datetime_tool("duration_sum", ranges=[{"start": "2026-08-26T09:00", "end": "2026-08-26T13:00"}, {"start": "2026-08-26T14:00", "end": "2026-08-26T18:30"}])),
    ("dt recurrence", lambda: lb.datetime_tool("recurrence", rule="every 2nd tuesday", start="2026-09-01", count=3)),
    ("dt cron", lambda: lb.datetime_tool("cron_next", expr="0 9 * * 1-5", tz="Asia/Kolkata", n=3, **{"from": "2026-08-28T10:00"})),
    ("dt age", lambda: lb.datetime_tool("age", dob="1995-06-15", on="2026-08-26")),
    ("dt fiscal", lambda: lb.datetime_tool("fiscal", value="2026-08-26", region="IN")),
    ("dt tz city", lambda: lb.datetime_tool("now", tz="Mumbai")),
    ("scale recipe", lambda: lb.scale_tool(from_qty=4, to_qty=7, entities=[{"name": "flour", "qty": "2.5", "unit": "cup"}, {"name": "eggs", "qty": 3, "integer": True}])),
    ("scale price", lambda: lb.scale_tool(from_qty=1, from_unit="kg", to_qty=250, to_unit="g", entities=[{"name": "price", "qty": 480}])),
    ("scale inverse", lambda: lb.scale_tool(from_qty=3, to_qty=12, mode="inverse", entities=[{"name": "days", "qty": 5}])),
    ("convert km", lambda: lb.convert_tool(value=5, from_unit="km", to_unit="miles")),
    ("convert ambiguous", lambda: lb.convert_tool(value=2, from_unit="ton", to_unit="kg")),
    ("convert temp", lambda: lb.convert_tool(value=100, from_unit="C", to_unit="F")),
    ("convert sqft", lambda: lb.convert_tool(value=1200, from_unit="sqft", to_unit="sqm")),
    ("convert ccy", lambda: lb.convert_tool(value=100, from_unit="USD", to_unit="INR", rate=83.5)),
    ("convert ccy norates", lambda: lb.convert_tool(value=100, from_unit="USD", to_unit="INR")),
    ("holidays", lambda: lb.holidays_tool("list", region="IN", year=2026, month=10)),
    ("holidays check", lambda: lb.holidays_tool("check", region="IN", date="2026-01-26")),
    ("numbers compare", lambda: lb.numbers_tool("compare", values=["9.11", "9.9"])),
    ("numbers round", lambda: lb.numbers_tool("round", value="2.675", decimals=2)),
    ("numbers format IN", lambda: lb.numbers_tool("format", value=12345678.5, locale="en_IN", style="currency", currency="INR")),
    ("numbers allocate", lambda: lb.numbers_tool("allocate", total=100, parts=3)),
    ("numbers words", lambda: lb.numbers_tool("to_words", value=123456.5, system="indian", currency="INR")),
    ("numbers parse", lambda: lb.numbers_tool("parse", value="₹1.2 Cr")),
    ("text count", lambda: lb.text_tool("count", text="The quick brown fox. Jumps over!", what="all")),
    ("text occ", lambda: lb.text_tool("count", text="strawberry", what="occurrences", substring="r")),
    ("text regex", lambda: lb.text_tool("regex_match", pattern=r"(\w+)@(\w+)\.com", text="a@b.com c@d.com")),
    ("text sort", lambda: lb.text_tool("sort", items=["file10", "file2", "File1"])),
    ("text diff", lambda: lb.text_tool("diff", a="a b c", b="a B c d", granularity="word")),
    ("text extract", lambda: lb.text_tool("extract", text="mail me at x@y.io or +91 98765 43210, GST 27AAPFU0939F1ZV", what=["emails", "phones", "gstin"])),
    ("coll setops", lambda: lb.collections_tool("set_ops", op="compare", a=[1, 2, 3], b=[3, 4])),
    ("coll group", lambda: lb.collections_tool("group_by", items=[{"d": "eng", "s": 10}, {"d": "eng", "s": 20}, {"d": "ops", "s": 5}], key="d", agg_field="s", agg=["sum", "avg"])),
    ("coll flatten", lambda: lb.collections_tool("flatten", data={"a": {"b": [1, {"c": 2}]}})),
    ("coll sort_by", lambda: lb.collections_tool("sort_by", items=[{"n": "b", "a": 2}, {"n": "a", "a": 2}, {"n": "c", "a": 1}], keys=[{"field": "a", "order": "desc"}, {"field": "n"}])),
    ("validate assert", lambda: lb.validate_tool("assert", data={"leave": {"days": 3, "balance": 2}, "date": "2026-09-01"}, rules=[{"path": "leave.days", "op": "lte", "value": 2}, {"path": "date", "op": "after", "value": "2026-08-26"}])),
    ("validate gstin", lambda: lb.validate_tool("id", kind="gstin", value="27AAPFU0939F1ZV")),
    ("validate pan", lambda: lb.validate_tool("id", kind="pan", value="ABCPE1234F")),
    ("validate luhn", lambda: lb.validate_tool("id", kind="card", value="4111 1111 1111 1111")),
    ("validate iban", lambda: lb.validate_tool("id", kind="iban", value="GB82 WEST 1234 5698 7654 32")),
    ("validate email", lambda: lb.validate_tool("email", value="Sayantan@Example.com")),
    ("validate phone", lambda: lb.validate_tool("phone", value="98765 43210", region="IN")),
    ("validate sql", lambda: lb.validate_tool("sql_parse", sql="DELETE FROM employees")),
    ("validate schema", lambda: lb.validate_tool("json_schema", schema={"type": "object", "required": ["a"]}, data={})),
    ("random uuid", lambda: lb.random_tool("uuid", version=7)),
    ("random int", lambda: lb.random_tool("int", min=1, max=6, n=3, seed=42)),
    ("random token", lambda: lb.random_tool("token", kind="password", length=12)),
    ("geo tz", lambda: lb.geo_tool("tz_for_place", place="Mumbai")),
    ("geo tz ambiguous", lambda: lb.geo_tool("tz_for_place", place="USA")),
    ("geo dist", lambda: lb.geo_tool("distance", **{"from": "Kolkata", "to": "London"})),
    ("geo country", lambda: lb.geo_tool("country", value="India")),
    ("encode hash", lambda: lb.encode_tool("hash", text="hello", algo="sha256")),
    ("encode b64", lambda: lb.encode_tool("base64", action="decode", text="aGVsbG8=")),
]

EXPECTED_SOFT_FAILS = {"ambiguous", "needs_rates"}
EXPECTED_REJECTIONS = {"math safety", "math safety2"}


def main() -> int:
    fails = 0
    for label, fn in TESTS:
        try:
            r = fn()
            show(label, r)
            if label in EXPECTED_REJECTIONS:
                if r.get("ok"):
                    fails += 1
                    print("   ^ SHOULD HAVE BEEN REJECTED")
            elif not r.get("ok") and r.get("error") not in EXPECTED_SOFT_FAILS:
                fails += 1
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"[EXC] {label}: {type(e).__name__}: {e}")
            traceback.print_exc(limit=3)
    print(f"\nunexpected failures: {fails} / {len(TESTS)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
