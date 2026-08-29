"""Adversarial sweep of `finance`, in the failure classes #52 found in `math`."""

from decimal import Decimal

import pytest

from leftbrain.core.finance import finance

# --- B. a rate that is already a percentage, written with its sign ------------


def test_a_rate_written_with_a_percent_sign_is_that_percentage():
    """`rate="12%"` was read as 0.12%: parse_number divided by 100 and the note was dropped."""
    r = finance("emi", principal=100000, rate="12%", rate_period="annual", months=12)
    assert r["ok"] and r["result"]["emi"] == "8884.88", r
    assert finance("gst", amount=1000, rate="18%", amount_is="exclusive")["result"]["gst"] == "180.00"
    assert finance("percent", op="of", percent="15%", value=200)["result"]["value"] == "30"
    assert finance("compound", principal=100000, rate="10%", rate_period="annual", years=1)["result"]["future_value"] == "110000.00"
    assert finance("npv_irr", cashflows=[-100, 110], rate="10%")["result"]["npv"] == "0.00"
    assert finance("percent", op="split", total=1000, tip="10%", people=3)["result"]["tip_amount"] == "100.00"
    assert finance("percent", op="discount", price=1000, discounts=["20%", "10%"])["result"]["stacked"]["final"] == "720.00"


# --- E. IRR: exists but not found; NPV withheld when no IRR exists -----------


def test_two_irrs_are_both_found():
    """100x² - 230x + 132 = 0 has roots at 10% and 20%; bisection saw the same sign at both ends."""
    r = finance("npv_irr", cashflows=[-100, 230, -132], rate=10)
    assert r["ok"], r
    assert r["result"]["npv"] == "0.00"
    assert r["result"]["irr_percent"] == "10" and r["result"]["irrs_percent"] == ["10", "20"], r["result"]
    assert r["warnings"]


def test_npv_is_answered_even_when_no_irr_exists():
    r = finance("npv_irr", cashflows=[100, 110, 120], rate=10)
    assert r["ok"], r
    assert r["result"]["npv"] == "299.17" and r["result"]["irr_percent"] is None
    assert any("never change sign" in a for a in r["assumptions"])
    r = finance("npv_irr", cashflows=[100, 110, 120])
    assert r["ok"] is False and "never change sign" in r["message"]


def test_a_single_irr_is_unchanged():
    r = finance("npv_irr", cashflows=[-1000, 500, 500, 500], rate=10)
    assert r["result"]["irr_percent"] == "23.3752" and "irrs_percent" not in r["result"]


# --- H. a rounded-up instalment clears the loan early ------------------------


def test_the_schedule_stops_when_the_balance_reaches_zero():
    r = finance("emi", principal=100000, rate=12, rate_period="annual", months=1200, schedule=True)
    assert r["ok"], r
    rows = r["result"]["schedule"]
    assert all(Decimal(row["closing"]) >= 0 and Decimal(row["payment"]) > 0 for row in rows), rows[-1]
    assert rows[-1]["closing"] == "0.00" and len(rows) < 1200
    assert r["result"]["months"] == 1200 and r["result"]["months_paid"] == len(rows)
    assert Decimal(r["result"]["last_payment"]) > 0
    assert any("early" in a for a in r["assumptions"]), r["assumptions"]
    assert Decimal(r["result"]["total_payment"]) == sum(Decimal(row["payment"]) for row in rows)


def test_an_ordinary_schedule_still_runs_to_term():
    r = finance("emi", principal=120000, rate=12, rate_period="annual", months=12, schedule=True)
    assert len(r["result"]["schedule"]) == 12 and r["result"]["schedule"][-1]["closing"] == "0.00" and "months_paid" not in r["result"]


# --- C. raw exception text ----------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda: finance("emi", principal=float("inf"), rate=12, rate_period="annual", months=12),
        lambda: finance("compound", principal=float("inf"), rate=10, rate_period="annual", years=1),
        lambda: finance("npv_irr", cashflows=[-100, float("inf")], rate=10),
        lambda: finance("gst", amount=float("inf"), rate=18, amount_is="exclusive"),
        lambda: finance("gst", amount=float("inf"), rate=18, amount_is="inclusive"),
        lambda: finance("cagr", start_value=float("inf"), end_value=1, years=1),
        lambda: finance("cagr", start_value=1, end_value=float("inf"), years=1),
    ],
)
def test_an_infinite_amount_is_refused_in_words(call):
    r = call()
    assert r["ok"] is False and r["error"] == "invalid_input", r
    assert "infinit" in r["message"].lower() and "Error" not in r["message"] and "'F'" not in r["message"], r["message"]


def test_extreme_but_valid_cagr_inputs():
    r = finance("cagr", start_value=1, end_value="1e40", years=1)
    assert r["ok"] and r["result"]["cagr_percent"] == "9" * 40 + "00", r  # (10^40 - 1) * 100
    for years in ("0.000001", 1e-10):
        r = finance("cagr", start_value=1, end_value=2, years=years)
        assert r["ok"] is False and r["error"] == "too_large" and "Overflow" not in r["message"] and "InvalidOperation" not in r["message"], r


def test_a_rate_below_the_precision_of_the_calculation():
    r = finance("emi", principal=100000, rate="1e-45", rate_period="annual", months=12)
    assert r["ok"] and r["result"]["emi"] == "8333.33", r
    assert any("treated as 0" in a for a in r["assumptions"])


@pytest.mark.parametrize(
    "call",
    [
        lambda: finance("emi", principal=100000, rate=12, rate_period="annual", months=12, decimals="two"),
        lambda: finance("compound", principal=100000, rate=10, rate_period="annual", years=1, decimals="two"),
        lambda: finance("npv_irr", cashflows=[-100, 110], decimals="two"),
    ],
)
def test_decimals_must_be_a_whole_number(call):
    r = call()
    assert r["ok"] is False and "whole number" in r["message"] and "invalid literal" not in r["message"], r


# --- D. dropped parameters and dropped readings -----------------------------


def test_compound_notes_a_clash_of_months_and_years():
    r = finance("compound", principal=100000, rate=10, rate_period="annual", months=6, years=1, compounding="monthly")
    assert r["ok"] and any("'years'" in a and "'months'" in a for a in r["assumptions"]), r["assumptions"]


def test_percent_change_honours_decimals():
    r = finance("percent", op="change", a=1, b=2, decimals=2)
    assert r["result"]["percent_change"] == "100"
    r = finance("percent", op="change", a=3, b=4, decimals=2)
    assert r["result"]["percent_change"] == "33.33"


def test_schedule_flag_reads_the_word_false():
    r = finance("emi", principal=100000, rate=12, rate_period="annual", months=12, schedule="false")
    assert r["ok"] and "schedule" not in r["result"]
    r = finance("emi", principal=100000, rate=12, rate_period="annual", months=12, schedule="sometimes")
    assert r["ok"] is False and "schedule" in r["message"]


def test_how_an_amount_was_read_is_reported():
    r = finance("npv_irr", cashflows=[-100, "1,10"], rate=10)
    assert any("comma read as decimal" in a for a in r["assumptions"]), r["assumptions"]
    r = finance("emi", principal="10L", rate=12, rate_period="annual", months=12)
    assert any("×10^5" in a for a in r["assumptions"]), r["assumptions"]
    r = finance("cagr", start_value="1L", end_value="2L", years=5)
    assert r["ok"] and any("×10^5" in a for a in r["assumptions"])
