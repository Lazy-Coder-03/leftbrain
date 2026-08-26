"""finance: money maths that has to reconcile to the paisa."""

from decimal import Decimal

import leftbrain as lb
from leftbrain.core import finance

# --- emi -------------------------------------------------------------------


def test_emi_classic_home_loan():
    r = lb.finance_tool("emi", principal=1_000_000, rate=8.5, rate_period="annual", months=240)
    assert r["ok"], r
    res = r["result"]
    assert res["emi"] == "8678.23" and res["months"] == 240
    assert res["monthly_rate_percent"] == "0.7083333333"
    # totals come from the rounded schedule, so they reconcile exactly
    assert Decimal(res["total_payment"]) == Decimal(res["total_interest"]) + Decimal("1000000")
    assert abs(Decimal(res["total_interest"]) - Decimal("1082775.20")) < Decimal("10")
    assert "schedule" not in res


def test_emi_schedule_reconciles_to_zero():
    r = lb.finance_tool("emi", principal=120_000, rate=12, rate_period="annual", months=12, schedule=True)
    rows = r["result"]["schedule"]
    assert len(rows) == 12 and rows[0]["month"] == 1
    assert rows[0]["interest"] == "1200.00" and rows[-1]["closing"] == "0.00"
    assert sum(Decimal(x["principal"]) for x in rows) == Decimal("120000.00")
    assert sum(Decimal(x["payment"]) for x in rows) == Decimal(r["result"]["total_payment"])


def test_emi_zero_rate_and_years():
    r = lb.finance_tool("emi", principal=120_000, rate=0, rate_period="annual", years=1)
    assert r["result"]["emi"] == "10000.00" and r["result"]["total_interest"] == "0.00"


def test_emi_monthly_rate_is_not_divided_again():
    r = lb.finance_tool("emi", principal=100_000, rate=1, rate_period="monthly", months=12)
    assert r["result"]["monthly_rate_percent"] == "1" and r["result"]["emi"] == "8884.88"


def test_emi_refuses_to_guess_the_rate_period():
    r = lb.finance_tool("emi", principal=100_000, rate=8.5, months=12)
    assert not r["ok"] and r["error"] == "ambiguous"
    assert r["needs"] == {"field": "rate_period", "options": ["annual", "monthly"]}


def test_emi_rejects_bad_inputs():
    assert lb.finance_tool("emi", principal=-5, rate=8, rate_period="annual", months=12)["error"] == "invalid_input"
    assert lb.finance_tool("emi", principal=100, rate=8, rate_period="annual")["error"] == "invalid_input"
    assert lb.finance_tool("emi", principal=100, rate=8, rate_period="annual", months=2000)["error"] == "invalid_input"


# --- compound --------------------------------------------------------------


def test_compound_annual_and_monthly():
    r = lb.finance_tool("compound", principal=100_000, rate=10, rate_period="annual", years=3)
    assert r["result"]["future_value"] == "133100.00" and r["result"]["interest_earned"] == "33100.00"
    assert "compounded annually" in " ".join(r["assumptions"])
    m = lb.finance_tool("compound", principal=100_000, rate=12, rate_period="annual", years=1, compounding="monthly")
    assert m["result"]["future_value"] == "112682.50"
    assert m["result"]["effective_annual_rate_percent"] == "12.6825"


def test_compound_with_contributions():
    r = lb.finance_tool("compound", principal=100_000, rate=12, rate_period="annual", months=12, compounding="monthly", contribution=1000)
    res = r["result"]
    assert res["future_value"] == "125365.01" and res["total_contributed"] == "12000.00"
    assert Decimal(res["interest_earned"]) == Decimal(res["future_value"]) - Decimal("100000") - Decimal("12000")
    begin = lb.finance_tool("compound", principal=0, rate=12, rate_period="annual", months=12, compounding="monthly", contribution=1000, contribution_timing="begin")
    assert Decimal(begin["result"]["future_value"]) > Decimal("12682.50")


def test_compound_continuous():
    r = lb.finance_tool("compound", principal=1000, rate=5, rate_period="annual", years=2, compounding="continuous")
    assert r["result"]["future_value"] == "1105.17"
    bad = lb.finance_tool("compound", principal=1000, rate=5, rate_period="annual", years=2, compounding="continuous", contribution=10)
    assert not bad["ok"] and bad["error"] == "unsupported"


def test_compound_needs_rate_period_and_a_term():
    assert lb.finance_tool("compound", principal=1000, rate=5, years=2)["error"] == "ambiguous"
    assert lb.finance_tool("compound", principal=1000, rate=5, rate_period="annual")["error"] == "invalid_input"
    assert lb.finance_tool("compound", principal=1000, rate=5, rate_period="annual", years=2, compounding="hourly")["error"] == "invalid_input"


# --- cagr ------------------------------------------------------------------


def test_cagr():
    r = lb.finance_tool("cagr", start_value=100, end_value=200, years=5)
    assert r["result"]["cagr_percent"] == "14.8698" and r["result"]["multiple"] == "2" and r["result"]["total_growth_percent"] == "100"
    down = lb.finance_tool("cagr", start_value=200, end_value=150, years=2)
    assert down["result"]["cagr_percent"].startswith("-13.39")
    assert lb.finance_tool("cagr", start_value=0, end_value=200, years=5)["error"] == "invalid_input"
    assert lb.finance_tool("cagr", start_value=100, end_value=200, years=0)["error"] == "invalid_input"


# --- npv_irr ---------------------------------------------------------------


def test_npv_and_irr():
    r = lb.finance_tool("npv_irr", cashflows=[-1000, 500, 500, 500], rate=10)
    res = r["result"]
    assert res["npv"] == "243.43" and res["rate_percent"] == "10"
    assert abs(Decimal(res["irr_percent"]) - Decimal("23.3752")) < Decimal("0.001")
    assert res["periods"] == 3 and "per period" in " ".join(r["assumptions"])


def test_irr_alone_and_no_sign_change():
    r = lb.finance_tool("npv_irr", cashflows=[-100, 110])
    assert r["ok"] and r["result"]["irr_percent"] == "10" and "npv" not in r["result"]
    bad = lb.finance_tool("npv_irr", cashflows=[100, 110, 120])
    assert not bad["ok"] and "sign" in bad["message"]
    assert lb.finance_tool("npv_irr", cashflows=[-100])["error"] == "invalid_input"


# --- gst -------------------------------------------------------------------


def test_gst_inclusive_intra_state():
    r = lb.finance_tool("gst", amount=1180, rate=18, amount_is="inclusive")
    res = r["result"]
    assert res["base"] == "1000.00" and res["gst"] == "180.00" and res["total"] == "1180.00"
    assert res["cgst"] == "90.00" and res["sgst"] == "90.00" and "igst" not in res
    assert "intra-state" in " ".join(r["assumptions"])


def test_gst_exclusive_inter_state_and_remainder():
    r = lb.finance_tool("gst", amount=1000, rate=18, amount_is="exclusive", supply="inter")
    assert r["result"]["igst"] == "180.00" and r["result"]["total"] == "1180.00" and "cgst" not in r["result"]
    odd = lb.finance_tool("gst", amount=999, rate=5, amount_is="exclusive")
    res = odd["result"]
    assert res["gst"] == "49.95" and res["cgst"] == "24.98" and res["sgst"] == "24.98"
    assert res["rounding_difference"] == "0.01" and res["gst_exact"] == "49.95" and odd["warnings"]


def test_gst_refuses_to_guess_inclusive():
    r = lb.finance_tool("gst", amount=1000, rate=18)
    assert r["error"] == "ambiguous" and r["needs"]["options"] == ["inclusive", "exclusive"]
    assert lb.finance_tool("gst", amount=1000, rate=18, amount_is="inclusive", supply="offshore")["error"] == "invalid_input"


# --- percent ---------------------------------------------------------------


def test_percent_change_and_points():
    r = lb.finance_tool("percent", op="change", a=50, b=75)
    assert r["result"]["percent_change"] == "50" and r["result"]["difference"] == "25"
    pts = lb.finance_tool("percent", op="change", a=10, b=12.5)
    assert pts["result"]["percentage_points"] == "2.5" and pts["result"]["percent_change"] == "25"
    assert lb.finance_tool("percent", op="change", a=0, b=5)["error"] == "invalid_input"


def test_percent_of_discount_split():
    assert lb.finance_tool("percent", op="of", percent=15, value=200)["result"]["value"] == "30"
    d = lb.finance_tool("percent", op="discount", price=1000, discounts=[20, 10])["result"]
    assert d["stacked"]["final"] == "720.00" and d["additive"]["final"] == "700.00" and d["stacked"]["effective_percent"] == "28"
    s = lb.finance_tool("percent", op="split", total=1000, tip=10, people=3)["result"]
    assert s["total_with_tip"] == "1100.00" and s["shares"] == ["366.67", "366.67", "366.66"]
    assert lb.finance_tool("percent", op="split", total=1000, people=0)["error"] == "invalid_input"
    assert lb.finance_tool("percent", op="halve", value=2)["error"] == "invalid_input"


def test_finance_registered_everywhere():
    assert "finance" in lb.TOOLS and finance.MODES == ("emi", "compound", "cagr", "npv_irr", "gst", "percent")
    assert set(finance.EXAMPLES) == set(finance.MODES)
    assert lb.finance_tool("nope")["error"] == "invalid_input"
