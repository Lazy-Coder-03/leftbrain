"""#28 §2c/§2d: inputs that were never checked, and approximations that should be refusals.

Every case here came back `ok: true` with a confident number. That is worse than an error:
an agent has no way to tell `distance Delhi → Mumbai = 0 km` from a real answer, and no
reason to look twice.
"""

import pytest

from leftbrain.core.convert import convert
from leftbrain.core.datetimex import datetime_tool
from leftbrain.core.geo_offline import geo_offline
from leftbrain.core.mathx import math as math_tool

# --- §2c: ranges and reversals ----------------------------------------------


@pytest.mark.parametrize(("lat", "lon"), [(91, 0), (-91, 0), (0, 181), (0, -181)])
def test_a_coordinate_off_the_globe_is_refused(lat, lon):
    r = geo_offline("tz_for_coords", lat=lat, lon=lon)
    assert r["ok"] is False and r["error"] == "invalid_input"
    assert "lat" in r["message"] or "lon" in r["message"]


def test_distance_checks_both_ends():
    r = geo_offline("distance", origin=[91, 0], destination=[0, 0])
    assert r["ok"] is False and r["error"] == "invalid_input"


def test_a_coordinate_on_the_pole_is_still_fine():
    assert geo_offline("tz_for_coords", lat=90, lon=180)["ok"]
    assert geo_offline("tz_for_coords", lat=22.57, lon=88.36)["ok"]


def test_business_days_reports_a_reversed_range_like_diff_does():
    """`diff` says sign -1 and names the direction; `business_days` silently swapped them."""
    r = datetime_tool("business_days", start="2026-12-31", end="2026-01-01")
    assert r["ok"]
    assert r["result"]["direction"] == "end is before start" and r["result"]["sign"] == -1
    assert any("before" in x for x in r["assumptions"] + r["warnings"])


def test_an_ordinary_range_is_unchanged():
    r = datetime_tool("business_days", start="2026-10-01", end="2026-10-31", region="IN")
    assert r["result"]["business_days"] == 20 and r["result"]["sign"] == 1


def test_below_absolute_zero_is_refused():
    r = convert("temperature", value=-500, from_unit="C", to_unit="K")
    assert r["ok"] is False and r["error"] == "invalid_input"
    assert "absolute zero" in r["message"]


def test_a_temperature_difference_may_be_negative():
    """`delta=true` is a difference, not a reading, so -500 °C of change is legitimate."""
    assert convert("temperature", value=-500, from_unit="C", to_unit="K", delta=True)["ok"]


def test_absolute_zero_itself_converts():
    assert convert("temperature", value=-273.15, from_unit="C", to_unit="K")["ok"]


def test_a_negative_exchange_rate_is_refused():
    r = convert("currency", value=100, from_unit="USD", to_unit="INR", rate=-83)
    assert r["ok"] is False and r["error"] == "invalid_input"
    assert "rate" in r["message"]


@pytest.mark.parametrize(
    ("value", "why"),
    [("2026-03-08 02:30", "does not exist"), ("2026-11-01 01:30", "happens twice")],
)
def test_a_dst_gap_or_fold_is_reported(value, why):
    r = datetime_tool("convert_tz", value=value, from_tz="America/New_York", to_tz="UTC")
    said = " ".join(r.get("warnings", []) + r.get("assumptions", []) + [str(r.get("message", ""))])
    assert "exist" in said or "ambiguous" in said or "twice" in said, (value, said)


def test_an_ordinary_wall_time_says_nothing_about_dst():
    r = datetime_tool("convert_tz", value="2026-06-15 09:30", from_tz="America/New_York", to_tz="UTC")
    assert r["ok"] and not any("exist" in w or "twice" in w for w in r["warnings"])


# --- §2d: approximations that should be refusals ----------------------------


def test_an_unknown_place_is_not_approximated_by_a_timezone_centroid():
    """Delhi → Mumbai came back as 0 km: both resolved to Asia/Kolkata's reference city."""
    r = geo_offline("distance", origin="Delhi", destination="Mumbai")
    assert r["ok"] is False or r["result"]["km"] > 1000
    if r["ok"] is False:
        assert r["error"] in ("ambiguous", "invalid_input")
        assert "coordinates" in str(r.get("needs", {})) or "coordinates" in r["message"]


def test_a_known_place_still_resolves():
    r = geo_offline("distance", origin="Kolkata", destination="London")
    assert r["ok"] and 7900 < r["result"]["km"] < 8000


def test_division_by_zero_is_input_not_complex_infinity():
    r = math_tool("eval", expr="1/0")
    assert r["ok"] is False and r["error"] == "invalid_input"
    assert "zero" in r["message"]


def test_a_trig_pole_is_refused_rather_than_returned_as_nan():
    r = math_tool("eval", expr="tan(pi/2)", angle="rad")
    assert r["ok"] is False and r["error"] == "invalid_input"


def test_ordinary_trig_and_division_are_unaffected():
    assert math_tool("eval", expr="1/3")["result"]["value"] == "1/3"
    assert math_tool("eval", expr="tan(pi/4)", angle="rad")["ok"]


def test_a_polynomial_with_no_closed_form_gets_numeric_roots():
    """40 complex roots exist; reporting `solutions: []` said the opposite."""
    r = math_tool("solve", expr="x^40 - 3*x^17 + x^3 - 7 = 0")
    if r["ok"]:
        assert r["result"]["count"] > 0
        assert any("numeric" in x for x in r["assumptions"] + r["warnings"])
    else:
        assert r["error"] == "unsupported" and "closed form" in r["message"]


# --- two the first pass through §2c/§2d missed ------------------------------


def test_zero_to_the_zero_says_which_convention_it_used():
    """SymPy returns 1, which is the usual convention and not the only one: as a limit,
    x^y at the origin depends on the path taken."""
    r = math_tool("eval", expr="0^0")
    assert r["ok"] and r["result"]["value"] == "1"
    assert any("0^0" in a for a in r["assumptions"])


def test_an_ordinary_power_claims_nothing():
    assert math_tool("eval", expr="2^3")["assumptions"] == []


def test_an_irrational_gets_the_fraction_the_mode_exists_to_produce():
    """`nsimplify(pi, rational=True)` hands `pi` straight back, so `form: fraction`
    returned its own input."""
    r = math_tool("convert_form", expr="pi", form="fraction")
    assert r["ok"] and "/" in r["result"]["value"]
    assert r["result"]["approximate"] is True and r["result"]["absolute_error"]
    assert r["warnings"] and any("irrational" in a for a in r["assumptions"])


def test_a_tolerance_that_cannot_be_met_is_said_so():
    r = math_tool("convert_form", expr="pi", form="fraction", tolerance=1e-40)
    assert r["ok"] is False and r["error"] == "unsupported"


def test_a_value_that_really_is_rational_is_exact_and_says_nothing_extra():
    r = math_tool("convert_form", expr="0.375", form="fraction")
    assert r["result"]["value"] == "3/8" and "approximate" not in r["result"]


# --- extreme magnitudes: render what can be rendered, saturate only the rest -----


def test_scientific_notation_parses_however_large():
    """`Decimal("1e400")` is exact; the parser's suffix regex simply never let it try."""
    from leftbrain.core.numbers import numbers

    assert numbers("parse", value="1e400")["result"]["value"].startswith("1000000")
    assert numbers("parse", value="-1e400")["result"]["value"].startswith("-1")
    assert numbers("parse", value="1.5E-30")["ok"]


def test_a_value_that_arrived_as_infinity_says_the_magnitude_was_already_lost():
    """JSON has no infinity, so a client that wrote 1e400 hands the tool `inf`."""
    from leftbrain.core.numbers import numbers

    r = numbers("parse", value=float("inf"))
    assert r["ok"] and r["result"]["value"] == "Infinity"
    assert any("infinity" in a for a in r["assumptions"])


def test_saturation_names_the_limit_it_hit():
    from decimal import Decimal

    from leftbrain.core.numbers import saturate_to_float

    assert saturate_to_float(Decimal("1e400"))[0] == float("inf")
    assert saturate_to_float(Decimal("-1e400"))[0] == float("-inf")
    assert saturate_to_float(Decimal("1e-400")) == (0.0, saturate_to_float(Decimal("1e-400"))[1])
    assert "smallest representable" in saturate_to_float(Decimal("1e-400"))[1]
    assert saturate_to_float(Decimal("3.5")) == (3.5, None)


def test_money_too_big_to_be_money_is_refused_not_crashed():
    from leftbrain.core.finance import finance

    r = finance("compound", principal=1e300, rate=5, rate_period="annual", years=100)
    assert r["ok"] is False and r["error"] == "too_large" and "InvalidOperation" not in r["message"]
