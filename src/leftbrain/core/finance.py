"""finance - EMI, compound growth, CAGR, NPV/IRR, GST split, percentages. Pure Decimal."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Any

from ..contract import Ambiguous, ToolError, Unsupported, ok, tool
from .numbers import _ROUND_MODES, _dec_str, parse_number

MODES = ("emi", "compound", "cagr", "npv_irr", "gst", "percent")

MAX_MONTHS = 1200  # a century of monthly instalments
MAX_CASHFLOWS = 10_000
#: Years a compounding term may cover. Beyond this the power overflows Decimal, and the
#: question stopped being about money long before.
MAX_YEARS = 10_000
_PERIODS_PER_YEAR = {"annual": 1, "yearly": 1, "semiannual": 2, "quarterly": 4, "monthly": 12, "weekly": 52, "daily": 365}
_PREC = 40


def _num(p: dict[str, Any], key: str, *, required: bool = True, positive: bool = False, nonneg: bool = False) -> Decimal | None:
    if p.get(key) is None:
        if required:
            raise ToolError(f"'{key}' is required")
        return None
    d, _ = parse_number(p[key])
    if positive and d <= 0:
        raise ToolError(f"'{key}' must be greater than zero")
    if nonneg and d < 0:
        raise ToolError(f"'{key}' must not be negative")
    return d


def _money(d: Decimal, decimals: int, rounding: str) -> Decimal:
    return d.quantize(Decimal(1).scaleb(-decimals), rounding=_ROUND_MODES[rounding])


def _rounding(p: dict[str, Any]) -> tuple[int, str]:
    decimals = int(p.get("decimals", 2))
    if not 0 <= decimals <= 6:
        raise ToolError("decimals must be between 0 and 6")
    rounding = str(p.get("rounding", "half_up")).lower()
    if rounding not in _ROUND_MODES:
        raise ToolError(f"rounding must be one of {', '.join(_ROUND_MODES)}")
    return decimals, rounding


def _rate_per_period(p: dict[str, Any], periods_per_year: int) -> tuple[Decimal, Decimal, list[str]]:
    """The stated rate as (annual %, per-period fraction), refusing to guess what the % is per."""
    rate = _num(p, "rate", nonneg=True)
    period = p.get("rate_period")
    if period is None:
        raise Ambiguous("'rate' could be per year or per month - say which with rate_period", "rate_period", ["annual", "monthly"])
    period = str(period).lower()
    if period not in ("annual", "yearly", "monthly"):
        raise ToolError("rate_period must be annual or monthly")
    annual = rate if period != "monthly" else rate * 12
    per_period = (rate / 100 / periods_per_year) if period != "monthly" else (rate / 100 * 12 / periods_per_year)
    return annual, per_period, [f"rate {_dec_str(rate)}% read as {'per year' if period != 'monthly' else 'per month'}"]


def _term_months(p: dict[str, Any]) -> int:
    if p.get("months") is not None:
        n = _num(p, "months")
        if n != n.to_integral() or n < 1:
            raise ToolError("months must be a whole number of at least 1")
        months = int(n)
    elif p.get("years") is not None:
        y = _num(p, "years", positive=True)
        months = int((y * 12).to_integral_value())
        if months < 1 or (y * 12) != months:
            raise ToolError("years must be a whole number of months (e.g. 1.5)")
    else:
        raise ToolError("'months' or 'years' is required")
    if months > MAX_MONTHS:
        raise ToolError(f"term is capped at {MAX_MONTHS} months")
    return months


# --------------------------------------------------------------------------- #


def _emi(p: dict[str, Any]) -> dict[str, Any]:
    principal = _num(p, "principal", positive=True)
    _, r, assumptions = _rate_per_period(p, 12)
    n = _term_months(p)
    decimals, rounding = _rounding(p)
    with localcontext() as ctx:
        ctx.prec = _PREC
        if r == 0:
            emi_exact = principal / n
        else:
            g = (1 + r) ** n
            emi_exact = principal * r * g / (g - 1)
        emi = _money(emi_exact, decimals, rounding)
        rows: list[dict[str, Any]] = []
        opening = principal
        total_interest = Decimal(0)
        total_paid = Decimal(0)
        for m in range(1, n + 1):
            interest = _money(opening * r, decimals, rounding)
            if m == n:
                repay = opening  # the last instalment clears whatever is left, so closing is exactly 0
                payment = repay + interest
            else:
                repay = emi - interest
                payment = emi
            closing = opening - repay
            rows.append({"month": m, "opening": _fmt(opening, decimals), "payment": _fmt(payment, decimals), "interest": _fmt(interest, decimals), "principal": _fmt(repay, decimals), "closing": _fmt(closing, decimals)})
            total_interest += interest
            total_paid += payment
            opening = closing
    out: dict[str, Any] = {
        "emi": _fmt(emi, decimals),
        "emi_exact": _dec_str(emi_exact.quantize(Decimal("1e-10"))),
        "months": n,
        "monthly_rate_percent": _dec_str((r * 100).quantize(Decimal("1e-10"))),
        "total_payment": _fmt(total_paid, decimals),
        "total_interest": _fmt(total_interest, decimals),
        "last_payment": rows[-1]["payment"],
    }
    if p.get("schedule"):
        out["schedule"] = rows
    assumptions.append(f"instalments rounded {rounding} to {decimals} decimals; totals are the sum of the rounded schedule, and the last instalment absorbs the rounding")
    return ok(out, assumptions=assumptions)


def _fmt(d: Decimal, decimals: int) -> str:
    return format(d.quantize(Decimal(1).scaleb(-decimals)), "f")


def _compound(p: dict[str, Any]) -> dict[str, Any]:
    principal = _num(p, "principal", nonneg=True)
    compounding = str(p.get("compounding", "annual")).lower()
    assumptions: list[str] = []
    if compounding == "continuous":
        m = 1
    elif compounding in _PERIODS_PER_YEAR:
        m = _PERIODS_PER_YEAR[compounding]
    else:
        raise ToolError(f"compounding must be one of {', '.join(_PERIODS_PER_YEAR)}, continuous")
    if "compounding" not in p:
        assumptions.append("compounded annually (no compounding given)")
    annual, i, rate_notes = _rate_per_period(p, m)
    assumptions = rate_notes + assumptions
    if p.get("years") is not None:
        years = _num(p, "years", positive=True)
    elif p.get("months") is not None:
        years = _num(p, "months", positive=True) / 12
    else:
        raise ToolError("'years' or 'months' is required")
    if years > MAX_YEARS:
        # (1 + i) ** (m * years) overflows Decimal long before this is a sensible question;
        # it used to surface as `internal` plus a bare InvalidOperation (#28 SS4).
        raise ToolError(
            f"a term of {float(years):,.0f} years is past what compound interest can be computed over; "
            f"the limit is {MAX_YEARS:,}",
            details={"years": float(years), "limit": MAX_YEARS},
            hint="Use a term of a few hundred years or less.",
        )
    contribution = _num(p, "contribution", required=False, nonneg=True) or Decimal(0)
    timing = str(p.get("contribution_timing", "end")).lower()
    if timing not in ("end", "begin"):
        raise ToolError("contribution_timing must be end or begin")
    decimals, rounding = _rounding(p)
    with localcontext() as ctx:
        ctx.prec = _PREC
        if compounding == "continuous":
            if contribution:
                raise Unsupported("contributions are not supported with continuous compounding; pick daily or monthly")
            fv = principal * (annual / 100 * years).exp()
            periods = None
            ear = (annual / 100).exp() - 1
        else:
            periods_exact = years * m
            if periods_exact != periods_exact.to_integral():
                raise ToolError(f"the term must be a whole number of {compounding} periods ({_dec_str(periods_exact)} is not)")
            periods = int(periods_exact)
            growth = (1 + i) ** periods
            fv = principal * growth
            if contribution:
                annuity = contribution * periods if i == 0 else contribution * (growth - 1) / i
                if timing == "begin":
                    annuity *= 1 + i
                fv += annuity
            ear = (1 + i) ** m - 1
        contributed = contribution * (periods or 0)
        fv_r = _money(fv, decimals, rounding)
    out = {
        "future_value": _fmt(fv_r, decimals),
        "principal": _fmt(principal, decimals),
        "total_contributed": _fmt(contributed, decimals),
        "interest_earned": _fmt(fv_r - principal - contributed, decimals),
        "annual_rate_percent": _dec_str(annual),
        "effective_annual_rate_percent": _dec_str((ear * 100).quantize(Decimal("1e-4"))),
        "compounding": compounding,
        "years": _dec_str(years),
    }
    if periods is not None:
        out["periods"] = periods
    if contribution:
        out["contribution_timing"] = timing
        assumptions.append(f"a contribution of {_dec_str(contribution)} is added at the {timing} of each {compounding} period")
    return ok(out, assumptions=assumptions)


def _cagr(p: dict[str, Any]) -> dict[str, Any]:
    start = _num(p, "start_value", positive=True)
    end = _num(p, "end_value", positive=True)
    years = _num(p, "years", positive=True)
    with localcontext() as ctx:
        ctx.prec = _PREC
        multiple = end / start
        cagr = multiple ** (1 / years) - 1
        total = multiple - 1
    return ok({
        "cagr_percent": _dec_str((cagr * 100).quantize(Decimal("1e-4"))),
        "total_growth_percent": _dec_str((total * 100).quantize(Decimal("1e-4"))),
        "multiple": _dec_str(multiple.quantize(Decimal("1e-6"))),
        "years": _dec_str(years),
    }, steps=[f"({_dec_str(end)} / {_dec_str(start)}) ^ (1 / {_dec_str(years)}) - 1"])


def _npv_at(flows: list[Decimal], r: Decimal) -> Decimal:
    return sum(cf / (1 + r) ** t for t, cf in enumerate(flows))


def _irr(flows: list[Decimal]) -> Decimal | None:
    """Bisection on NPV between -99.99% and 1000% per period - deterministic, no seed."""
    lo, hi = Decimal("-0.9999"), Decimal("10")
    f_lo, f_hi = _npv_at(flows, lo), _npv_at(flows, hi)
    if f_lo == 0:
        return lo
    if f_hi == 0:
        return hi
    if (f_lo < 0) == (f_hi < 0):
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = _npv_at(flows, mid)
        if f_mid == 0 or hi - lo < Decimal("1e-12"):
            return mid
        if (f_mid < 0) == (f_lo < 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2


def _npv_irr(p: dict[str, Any]) -> dict[str, Any]:
    raw = p.get("cashflows")
    if not isinstance(raw, list) or len(raw) < 2:
        raise ToolError("'cashflows' must be a list of at least two amounts, the first at time 0")
    if len(raw) > MAX_CASHFLOWS:
        raise ToolError(f"cashflows are capped at {MAX_CASHFLOWS}")
    flows = [parse_number(v)[0] for v in raw]
    if not (any(f < 0 for f in flows) and any(f > 0 for f in flows)):
        raise ToolError("cashflows never change sign, so there is no internal rate of return")
    decimals, rounding = _rounding(p)
    assumptions = ["cashflows are one per period, the first at time 0; the rate is per period (use a monthly rate for monthly flows)"]
    out: dict[str, Any] = {"periods": len(flows) - 1}
    with localcontext() as ctx:
        ctx.prec = _PREC
        rate = _num(p, "rate", required=False)
        if rate is not None:
            r = rate / 100
            if r <= -1:
                raise ToolError("rate must be above -100%")
            out["npv"] = _fmt(_money(_npv_at(flows, r), decimals, rounding), decimals)
            out["rate_percent"] = _dec_str(rate)
        irr = _irr(flows)
        if irr is None:
            raise ToolError("no internal rate of return between -99.99% and 1000% per period")
        out["irr_percent"] = _dec_str((irr * 100).quantize(Decimal("1e-4")))
    # Descartes' rule of signs: a cashflow that changes sign k times can have up to k IRRs,
    # and the one found is whichever the search reached first. Reporting it alone made a
    # multi-root problem look like a single answer (#28 SS3.12).
    changes = sum(1 for a, b in zip(flows, flows[1:], strict=False) if a and b and (a > 0) != (b > 0))
    out["sign_changes"] = changes
    warnings = []
    if changes > 1:
        warnings.append(
            f"the cashflows change sign {changes} times, so by Descartes' rule there may be up to "
            f"{changes} IRRs; the one reported is the first the search found, and IRR is not a "
            f"reliable comparison here - use NPV at your cost of capital"
        )
    return ok(out, assumptions=assumptions, warnings=warnings)


def _gst(p: dict[str, Any]) -> dict[str, Any]:
    amount = _num(p, "amount", nonneg=True)
    rate = _num(p, "rate", nonneg=True)
    amount_is = p.get("amount_is")
    if amount_is is None:
        raise Ambiguous("is 'amount' inclusive or exclusive of GST? say which with amount_is", "amount_is", ["inclusive", "exclusive"])
    amount_is = str(amount_is).lower()
    if amount_is not in ("inclusive", "exclusive"):
        raise ToolError("amount_is must be inclusive or exclusive")
    supply = str(p.get("supply", "intra")).lower()
    if supply not in ("intra", "inter"):
        raise ToolError("supply must be intra (CGST + SGST) or inter (IGST)")
    decimals, rounding = _rounding(p)
    assumptions: list[str] = []
    if "supply" not in p:
        assumptions.append("intra-state supply assumed (CGST + SGST); pass supply=inter for IGST")
    warnings: list[str] = []
    with localcontext() as ctx:
        ctx.prec = _PREC
        if amount_is == "inclusive":
            base_exact = amount / (1 + rate / 100)
            gst_exact = amount - base_exact
        else:
            base_exact = amount
            gst_exact = amount * rate / 100
        base = _money(base_exact, decimals, rounding)
        gst = _money(gst_exact, decimals, rounding)
        total = base + gst if amount_is == "exclusive" else amount
        if amount_is == "inclusive":
            base = total - gst  # the base is what remains after the rounded tax, so the three reconcile
        out: dict[str, Any] = {"base": _fmt(base, decimals), "gst": _fmt(gst, decimals), "total": _fmt(total, decimals), "rate_percent": _dec_str(rate), "amount_is": amount_is, "supply": supply}
        out["gst_exact"] = _dec_str(gst_exact.quantize(Decimal("1e-6")))
        if supply == "inter":
            out["igst"] = _fmt(gst, decimals)
        else:
            half = _money(gst_exact / 2, decimals, rounding)
            out["cgst"] = _fmt(half, decimals)
            out["sgst"] = _fmt(half, decimals)
            out["cgst_rate_percent"] = out["sgst_rate_percent"] = _dec_str(rate / 2)
            diff = half * 2 - gst
            if diff:
                out["rounding_difference"] = _fmt(diff, decimals)
                warnings.append(f"CGST + SGST ({_fmt(half * 2, decimals)}) differs from the rounded total tax ({_fmt(gst, decimals)}) by {_fmt(diff, decimals)} because each half is rounded on its own")
    return ok(out, assumptions=assumptions, warnings=warnings)


def _percent(p: dict[str, Any]) -> dict[str, Any]:
    op = str(p.get("op", "")).lower()
    if op == "change":
        a, b = _num(p, "a"), _num(p, "b")
        if a == 0:
            raise ToolError("percent change from zero is undefined; the difference is the only meaningful figure")
        with localcontext() as ctx:
            ctx.prec = _PREC
            change = (b - a) / abs(a) * 100
        out = {"a": _dec_str(a), "b": _dec_str(b), "difference": _dec_str(b - a), "percent_change": _dec_str(change.quantize(Decimal("1e-6"))), "percentage_points": _dec_str(b - a)}
        return ok(out, assumptions=["if a and b are themselves percentages, 'percentage_points' is the honest figure and 'percent_change' is the relative one"])
    if op == "of":
        pct, value = _num(p, "percent"), _num(p, "value")
        with localcontext() as ctx:
            ctx.prec = _PREC
            v = value * pct / 100
        return ok({"value": _dec_str(v), "percent": _dec_str(pct), "of": _dec_str(value)})
    if op == "discount":
        price = _num(p, "price", nonneg=True)
        raw = p.get("discounts")
        if raw is None and p.get("percent") is not None:
            raw = [p["percent"]]
        if not isinstance(raw, list) or not raw:
            raise ToolError("'discounts' must be a list of percentages, e.g. [20, 10]")
        discounts = [parse_number(v)[0] for v in raw]
        if any(d < 0 or d > 100 for d in discounts):
            raise ToolError("each discount must be between 0 and 100 percent")
        decimals, rounding = _rounding(p)
        with localcontext() as ctx:
            ctx.prec = _PREC
            stacked = price
            for d in discounts:
                stacked *= 1 - d / 100
            additive = price * (1 - min(sum(discounts), Decimal(100)) / 100)
            out = {
                "price": _fmt(price, decimals),
                "discounts_percent": [_dec_str(d) for d in discounts],
                "stacked": {"final": _fmt(_money(stacked, decimals, rounding), decimals), "saved": _fmt(_money(price - stacked, decimals, rounding), decimals), "effective_percent": _dec_str(((1 - stacked / price) * 100).quantize(Decimal("1e-6"))) if price else "0"},
                "additive": {"final": _fmt(_money(additive, decimals, rounding), decimals), "saved": _fmt(_money(price - additive, decimals, rounding), decimals), "effective_percent": _dec_str(min(sum(discounts), Decimal(100)))},
            }
        return ok(out, assumptions=["'stacked' applies each discount to the already-discounted price (how shops do it); 'additive' adds the percentages first (how people expect it)"])
    if op == "split":
        total = _num(p, "total", nonneg=True)
        people = _num(p, "people")
        if people != people.to_integral() or people < 1:
            raise ToolError("people must be a whole number of at least 1")
        tip = _num(p, "tip", required=False, nonneg=True) or Decimal(0)
        decimals, rounding = _rounding(p)
        from .numbers import numbers as numbers_tool

        with localcontext() as ctx:
            ctx.prec = _PREC
            tip_amount = _money(total * tip / 100, decimals, rounding)
            grand = total + tip_amount
        alloc = numbers_tool("allocate", total=str(grand), parts=int(people), decimals=decimals)
        if not alloc["ok"]:
            raise ToolError(alloc["message"])
        shares = [item["share"] for item in alloc["result"]["items"]]
        return ok({"total": _fmt(total, decimals), "tip_percent": _dec_str(tip), "tip_amount": _fmt(tip_amount, decimals), "total_with_tip": _fmt(grand, decimals), "people": int(people), "shares": shares, "sum_of_shares": alloc["result"]["sum_of_shares"]}, assumptions=["shares are split with the largest-remainder method so they add up to the bill exactly; the first shares carry any extra minor unit"])
    raise ToolError("op must be one of change, of, discount, split")


@tool
def finance(mode: str = "emi", **params: Any) -> dict[str, Any]:
    """Money maths. Modes: emi, compound, cagr, npv_irr, gst, percent."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    return {"emi": _emi, "compound": _compound, "cagr": _cagr, "npv_irr": _npv_irr, "gst": _gst, "percent": _percent}[mode](p)


#: Worked examples for the reference page, one list per mode. Every one of them is
#: executed when /docs/tools/finance is built and sorted by the result into
#: "Examples" and "Fails when", so a fixture never states an expectation of its own.
EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "emi": [
        {
            "caption": "A ₹10 lakh home loan at 8.5% for 20 years.",
            "args": {"mode": "emi", "principal": 1000000, "rate": 8.5, "rate_period": "annual", "years": 20},
        },
        {
            "caption": "A one-year loan with the full schedule; the last instalment clears the balance exactly.",
            "args": {"mode": "emi", "principal": 120000, "rate": 12, "rate_period": "annual", "months": 12, "schedule": True},
        },
        {
            "caption": "A rate quoted per month is not divided by twelve again.",
            "args": {"mode": "emi", "principal": 100000, "rate": 1, "rate_period": "monthly", "months": 12},
        },
        {
            "caption": "Whole-rupee instalments, rounded up the way many Indian lenders do.",
            "args": {"mode": "emi", "principal": 500000, "rate": 9, "rate_period": "annual", "years": 5, "decimals": 0, "rounding": "ceil"},
        },
        {
            "caption": "Is 8.5 per year or per month? The tool will not guess.",
            "args": {"mode": "emi", "principal": 100000, "rate": 8.5, "months": 12},
        },
        {
            "caption": "No term.",
            "args": {"mode": "emi", "principal": 100000, "rate": 8.5, "rate_period": "annual"},
        },
    ],
    "compound": [
        {
            "caption": "₹1 lakh at 10% for three years, compounded annually.",
            "args": {"mode": "compound", "principal": 100000, "rate": 10, "rate_period": "annual", "years": 3},
        },
        {
            "caption": "Monthly compounding, with the effective annual rate it implies.",
            "args": {"mode": "compound", "principal": 100000, "rate": 12, "rate_period": "annual", "years": 1, "compounding": "monthly"},
        },
        {
            "caption": "A SIP: ₹1,000 a month for a year on top of the opening balance.",
            "args": {"mode": "compound", "principal": 100000, "rate": 12, "rate_period": "annual", "months": 12, "compounding": "monthly", "contribution": 1000},
        },
        {
            "caption": "Continuous compounding.",
            "args": {"mode": "compound", "principal": 1000, "rate": 5, "rate_period": "annual", "years": 2, "compounding": "continuous"},
        },
        {
            "caption": "The rate's period is not stated.",
            "args": {"mode": "compound", "principal": 1000, "rate": 5, "years": 2},
        },
        {
            "caption": "Contributions cannot be combined with continuous compounding.",
            "args": {"mode": "compound", "principal": 1000, "rate": 5, "rate_period": "annual", "years": 2, "compounding": "continuous", "contribution": 10},
        },
    ],
    "cagr": [
        {
            "caption": "Doubling in five years.",
            "args": {"mode": "cagr", "start_value": 100, "end_value": 200, "years": 5},
        },
        {
            "caption": "A decline is a negative rate.",
            "args": {"mode": "cagr", "start_value": 200, "end_value": 150, "years": 2},
        },
        {
            "caption": "A zero start has no growth rate.",
            "args": {"mode": "cagr", "start_value": 0, "end_value": 200, "years": 5},
        },
    ],
    "npv_irr": [
        {
            "caption": "An investment of 1,000 returning 500 a year for three years, discounted at 10%.",
            "args": {"mode": "npv_irr", "cashflows": [-1000, 500, 500, 500], "rate": 10},
        },
        {
            "caption": "IRR alone.",
            "args": {"mode": "npv_irr", "cashflows": [-100, 110]},
        },
        {
            "caption": "Cash flows that never change sign have no IRR.",
            "args": {"mode": "npv_irr", "cashflows": [100, 110, 120]},
        },
    ],
    "gst": [
        {
            "caption": "An invoice total of ₹1,180 that already includes 18% GST, intra-state.",
            "args": {"mode": "gst", "amount": 1180, "rate": 18, "amount_is": "inclusive"},
        },
        {
            "caption": "Adding 18% IGST to a ₹1,000 inter-state supply.",
            "args": {"mode": "gst", "amount": 1000, "rate": 18, "amount_is": "exclusive", "supply": "inter"},
        },
        {
            "caption": "When the halves do not add up to the rounded total, the difference is reported.",
            "args": {"mode": "gst", "amount": 999, "rate": 5, "amount_is": "exclusive"},
        },
        {
            "caption": "Inclusive or exclusive? Not guessed.",
            "args": {"mode": "gst", "amount": 1000, "rate": 18},
        },
    ],
    "percent": [
        {
            "caption": "From 50 to 75 is a 50% rise.",
            "args": {"mode": "percent", "op": "change", "a": 50, "b": 75},
        },
        {
            "caption": "From 10% to 12.5%: 2.5 percentage points, a 25% relative change.",
            "args": {"mode": "percent", "op": "change", "a": 10, "b": 12.5},
        },
        {
            "caption": "15% of 200.",
            "args": {"mode": "percent", "op": "of", "percent": 15, "value": 200},
        },
        {
            "caption": "20% off, then a further 10% off, is not 30% off.",
            "args": {"mode": "percent", "op": "discount", "price": 1000, "discounts": [20, 10]},
        },
        {
            "caption": "A ₹1,000 bill plus a 10% tip, split three ways to the paisa.",
            "args": {"mode": "percent", "op": "split", "total": 1000, "tip": 10, "people": 3},
        },
        {
            "caption": "Percent change from zero is undefined.",
            "args": {"mode": "percent", "op": "change", "a": 0, "b": 5},
        },
        {
            "caption": "An unknown operation.",
            "args": {"mode": "percent", "op": "halve", "value": 2},
        },
    ],
}
