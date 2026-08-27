"""The MCP wrappers are the contract agents actually see, so their parameter names are tested.

Ranges are named `start`/`end` (datetime), `lower`/`upper` (math bounds), `point` (the value a
limit approaches) and `origin`/`destination` (geo). The old `from`/`from_`/`to` spellings are gone:
a client that still sends them gets a TypeError from the signature, or an error naming the new
parameter from the core function underneath.
"""

import pytest

pytest.importorskip("mcp", reason="MCP wrappers need the optional 'mcp' package")

from leftbrain import mcp_server as mcp  # noqa: E402

# --- the new names, over the wrapper path ------------------------------------


def test_datetime_diff_takes_start_and_end():
    r = mcp.datetime(mode="diff", start="2026-08-26", end="2026-12-25")
    assert r["ok"] and r["result"]["total"]["days"] == 121
    assert r["result"]["start"]["date"] == "2026-08-26" and r["result"]["end"]["date"] == "2026-12-25"
    assert r["result"]["direction"] == "end is after start"


def test_datetime_business_days_and_cron_next_take_start():
    b = mcp.datetime(mode="business_days", start="2026-10-01", end="2026-10-31", region="IN")
    assert b["ok"] and b["result"]["business_days"] == 20
    assert b["result"]["start"] == "2026-10-01" and b["result"]["end"] == "2026-10-31"
    c = mcp.datetime(mode="cron_next", expr="0 9 * * 1-5", tz="Asia/Kolkata", n=2, start="2026-08-28T10:00")
    assert [x["date"] for x in c["result"]["next"]] == ["2026-08-31", "2026-09-01"]


def test_math_bounds_are_lower_upper_and_a_limit_approaches_point():
    assert mcp.math(mode="integrate", expr="x^2", var="x", lower=0, upper=1)["result"]["value"] == "1/3"
    assert mcp.math(mode="limit", expr="sin(x)/x", var="x", point=0)["result"]["decimal"] == "1"
    assert mcp.math(mode="convert_form", expr="0.375", form="fraction")["result"]["value"] == "3/8"


def test_geo_distance_takes_origin_and_destination():
    r = mcp.geo(mode="distance", origin="Kolkata", destination="London")
    assert r["ok"] and 7900 < r["result"]["km"] < 8000
    assert set(r["result"]["origin"]) == {"lat", "lon"} and set(r["result"]["destination"]) == {"lat", "lon"}


# --- the old names are gone --------------------------------------------------

@pytest.mark.parametrize(
    ("fn", "kwargs"),
    [
        (mcp.datetime, {"mode": "diff", "from_": "2026-08-26", "to": "2026-12-25"}),
        (mcp.datetime, {"mode": "business_days", "from_": "2026-10-01", "to": "2026-10-31"}),
        (mcp.datetime, {"mode": "cron_next", "expr": "0 9 * * 1-5", "from_": "2026-08-28T10:00"}),
        (mcp.math, {"mode": "integrate", "expr": "x^2", "var": "x", "from_": 0, "to": 1}),
        (mcp.math, {"mode": "limit", "expr": "sin(x)/x", "var": "x", "to": 0}),
        (mcp.math, {"mode": "convert_form", "expr": "0.375", "to": "fraction"}),
        (mcp.geo, {"mode": "distance", "from_": "Kolkata", "to": "London"}),
    ],
)
def test_the_retired_from_and_to_parameters_are_no_longer_in_the_signature(fn, kwargs):
    with pytest.raises(TypeError):
        fn(**kwargs)


def test_a_range_with_no_start_is_refused_by_the_new_name():
    r = mcp.datetime(mode="diff")
    assert not r["ok"] and r["error"] == "invalid_input" and "'start'" in r["message"]


def test_datetime_value_accepts_an_integer_timestamp_over_mcp():
    r = mcp.datetime(mode="convert_tz", value=1787232546, to_tz="Asia/Kolkata")
    assert r["ok"] and r["result"]["converted"]["unix"] == 1787232546
    assert mcp.datetime(mode="parse", value="1787232546")["result"]["unix"] == 1787232546
