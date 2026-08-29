"""Adversarial sweep of `convert`, in the failure classes #52 found in `math`."""

import pytest

from leftbrain.core.convert import convert

RATES = {"USD": 1, "INR": 83.5}

# --- A/E. rounding that said half-up and did half-even ----------------------------


@pytest.mark.parametrize(("value", "decimals", "want"), [(1.005, 2, 1.01), (0.125, 2, 0.13), (2.5, 0, 3.0), (0.005, 2, 0.01)])
def test_currency_rounds_half_up_as_it_says(value, decimals, want):
    r = convert("currency", value=value, from_unit="USD", to_unit="USD", rates=RATES, decimals=decimals)
    assert r["result"]["value"] == want, r["result"]


# --- B. case is meaning for units ----------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "dst", "want"),
    [("Mb/s", "Mbps", 1.0), ("MB/s", "Mb/s", 8.0), ("mPa", "Pa", 0.001), ("Mm", "m", 1e6), ("Pb", "petabit", 1.0), ("mWh", "Wh", 0.001), ("MWh", "Wh", 1e6), ("A", "mA", 1000.0)],
)
def test_si_prefix_and_bit_byte_case_is_kept(src, dst, want):
    r = convert("units", value=1, from_unit=src, to_unit=dst)
    assert r["ok"] and r["result"]["value"] == pytest.approx(want), (src, dst, r)


@pytest.mark.parametrize("unit", ["S", "H", "T"])
def test_a_letter_that_names_two_quantities_is_refused(unit):
    """`S` is siemens and seconds, `H` henry and hours, `T` tesla and tonnes."""
    r = convert("units", value=1, from_unit=unit, to_unit="m")
    assert r["ok"] is False and r["error"] == "ambiguous", (unit, r)


def test_a_decimal_comma_is_read_as_the_other_modes_read_it():
    r = convert("units", value="1,5", from_unit="km", to_unit="m")
    assert r["result"]["value"] == 1500.0 and any("decimal" in a for a in r["assumptions"]), r
    assert convert("units", value="1,500", from_unit="km", to_unit="m")["result"]["value"] == 1500000.0


@pytest.mark.parametrize(("src", "dst", "want"), [("US gallon", "L", 3.785411784), ("imperial gallon", "L", 4.54609), ("ft2", "m2", 0.09290304), ("PSI", "bar", 0.0689475729)])
def test_known_units_spelled_the_usual_ways(src, dst, want):
    r = convert("units", value=1, from_unit=src, to_unit=dst)
    assert r["ok"] and r["result"]["value"] == pytest.approx(want, rel=1e-9), (src, r)


def test_temperature_spellings():
    assert convert("temperature", value=100, from_unit="degrees C", to_unit="F")["result"]["value"] == 212.0
    assert convert("temperature", value=0, from_unit="deg K", to_unit="C")["result"]["value"] == -273.15


# --- A. factor_exact is exact or absent ----------------------------------------------------------


def test_factor_exact_is_only_claimed_when_it_is_exact():
    assert convert("units", value=1, from_unit="mi", to_unit="km")["result"]["factor_exact"] == "25146/15625"
    assert "factor_exact" not in convert("units", value=1, from_unit="deg", to_unit="rad")["result"]
    assert "factor_exact" not in convert("units", value=1, from_unit="psi", to_unit="Pa")["result"]


def test_precision_is_bounded():
    assert convert("temperature", value=20, from_unit="C", to_unit="F", precision=3)["result"]["value"] == 68.0
    for bad in (0, 16, -2, "abc", 2.7):
        r = convert("units", value=3, from_unit="ft", to_unit="m", precision=bad)
        assert r["ok"] is False and "precision" in r["message"] and "Format specifier" not in r["message"], (bad, r)
    assert convert("units", value=3, from_unit="ft", to_unit="m", precision=15)["result"]["value"] == 0.9144


# --- B/E. auto routing ---------------------------------------------------------------------------------


def test_auto_tries_the_unit_registry_before_calling_it_a_currency():
    r = convert("auto", value=1, from_unit="PSI", to_unit="BAR")
    assert r["ok"] and r["result"]["value"] == pytest.approx(0.0689475729), r
    r = convert("auto", value=100, from_unit="usd", to_unit="inr", rates=RATES)
    assert r["ok"] and r["result"]["value"] == 8350.0, r


# --- D. parameters a mode does not read are refused ----------------------------------------------------


def test_each_mode_declares_only_what_it_reads():
    r = convert("units", value=1, from_unit="mi", to_unit="km", decimals=2)
    assert r["ok"] is False and "decimals" in r["message"], r
    r = convert("currency", value=1, from_unit="USD", to_unit="INR", rates=RATES, delta=True)
    assert r["ok"] is False and "delta" in r["message"], r
    r = convert("temperature", value=1, from_unit="km", to_unit="m")
    assert r["ok"] is False and "temperature" in r["message"], r


# --- C. raw exception text -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "want"),
    [
        (lambda: convert("fuel_economy", value=1e-30, from_unit="mpg_us", to_unit="l_per_100km"), "decimals"),
        (lambda: convert("currency", value=1, from_unit="USD", to_unit="INR", rates=RATES, decimals=-1), "decimals"),
        (lambda: convert("currency", value=1, from_unit="USD", to_unit="INR", rates={"USD": 0, "INR": 83.5}), "USD"),
        (lambda: convert("currency", value=100, from_unit="USD", to_unit="INR", rates={"USD": 1, "INR": -83.5}), "INR"),
        (lambda: convert("units", value=1, from_unit=5, to_unit="km"), "from_unit"),
        (lambda: convert("units", value=1, from_unit="lb", to_unit="lbf"), "mass"),
    ],
)
def test_bad_inputs_are_refused_in_words(call, want):
    r = call()
    assert r["ok"] is False, r
    assert want in r["message"], r["message"]
    for leak in ("InvalidOperation", "TypeError", "ZeroDivisionError", "ValueError", "invalid literal", "Cannot convert from", "scaling factor"):
        assert leak not in r["message"], r["message"]


def test_a_single_letter_unit_says_which_of_the_two_it_read():
    """`C` is Celsius and the coulomb; `F` is Fahrenheit and the farad."""
    r = convert("temperature", value=100, from_unit="C", to_unit="F")
    assert r["result"]["value"] == 212.0
    assert any("coulomb" in a for a in r["assumptions"]) and any("farad" in a for a in r["assumptions"]), r["assumptions"]
    assert convert("units", value=1, from_unit="coulomb", to_unit="millicoulomb")["result"]["value"] == 1000.0


@pytest.mark.parametrize(
    ("constant", "to", "want"),
    [("gravitational_constant", "m**3/(kg*s**2)", 6.6743e-11), ("speed_of_light", "m/s", 299792458.0),
     ("planck_constant", "J*s", 6.62607015e-34), ("boltzmann_constant", "J/K", 1.380649e-23),
     ("molar_gas_constant", "J/(mol*K)", 8.314462618), ("standard_gravity", "m/s**2", 9.80665)],
)
def test_the_physical_constants_are_reachable_as_units(constant, to, want):
    r = convert("units", value=1, from_unit=constant, to_unit=to)
    assert r["ok"] and r["result"]["value"] == pytest.approx(want, rel=1e-9), (constant, r)
