"""holidays - public holiday calendars for 150+ countries (offline dataset)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import holidays as _hol

from ..contract import TooLarge, ToolError, ok, tool

MODES = ("list", "check", "next", "countries", "subdivisions")

#: Upcoming holidays `next` will return; the search window is two calendar years.
MAX_NEXT = 100


def _country(region: str) -> Any:
    code = str(region).strip().upper()
    if code == "UK":
        code = "GB"
    if code not in _hol.list_supported_countries():
        # allow country names
        for c, _subs in _hol.list_supported_countries(include_aliases=True).items():
            if str(c).upper() == code:
                return c
        raise ToolError(f"unsupported region {region!r}; use an ISO code such as 'IN', 'US', 'GB'")
    return code


def holiday_map(region: str, years: set[int] | list[int], subdiv: str | None = None, categories: Any = None) -> dict[date, str]:
    code = _country(region)
    kwargs: dict[str, Any] = {"years": sorted(years)}
    if subdiv:
        subdiv = str(subdiv).strip().upper()
        supported = _hol.list_supported_countries().get(code, [])
        if subdiv not in supported:
            raise ToolError(f"unknown subdivision {subdiv!r} for {code}; options: {', '.join(supported) or 'none'}")
        kwargs["subdiv"] = subdiv
    if categories:
        kwargs["categories"] = tuple(categories) if isinstance(categories, (list, tuple)) else (categories,)
    try:
        h = _hol.country_holidays(code, **kwargs)
    except (NotImplementedError, KeyError, ValueError) as e:
        raise ToolError(f"holiday lookup failed for {code}: {e}") from None
    return {d: str(n) for d, n in h.items()}


@tool
def holidays(mode: str = "list", **params: Any) -> dict[str, Any]:
    """Public holidays. Modes: list, check, next, countries, subdivisions."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    if mode == "countries":
        return ok({"countries": sorted(_hol.list_supported_countries().keys())})
    region = p.get("region") or p.get("country")
    if not region:
        raise ToolError("'region' (ISO country code) is required")
    code = _country(region)
    if mode == "subdivisions":
        return ok({"region": code, "subdivisions": _hol.list_supported_countries().get(code, [])})
    subdiv = p.get("subdiv") or p.get("state")
    from .datetimex import parse_dt  # lazy import

    if mode == "check":
        d, _, a = parse_dt(p.get("date") or p.get("value") or "today", locale=p.get("locale"), field="date")
        hm = holiday_map(code, {d.year}, subdiv, p.get("categories"))
        name = hm.get(d.date())
        return ok({"date": d.date().isoformat(), "is_holiday": name is not None, "name": name, "weekday": d.strftime("%A"), "is_weekend": d.weekday() >= 5}, assumptions=a)
    if mode == "next":
        d, _, a = parse_dt(p.get("date") or "today", locale=p.get("locale"), field="date")
        n = int(p.get("n", 5))
        if n > MAX_NEXT:
            raise TooLarge(
                f"n is {n:,}; the most this mode returns is {MAX_NEXT}",
                details={"n": n, "limit": MAX_NEXT},
                hint=f"Ask for at most {MAX_NEXT}, or use mode 'list' with a year.",
            )
        hm = holiday_map(code, {d.year, d.year + 1}, subdiv, p.get("categories"))
        found = [{"date": k.isoformat(), "name": v, "weekday": k.strftime("%A"), "days_away": (k - d.date()).days} for k, v in sorted(hm.items()) if k >= d.date()]
        upcoming = found[:n]
        # The window is this year and the next, so asking for more than it holds is not
        # an empty answer - say the list is short because the calendar ran out (#28 SS2f).
        short = [f"only {len(upcoming)} holidays fall in the {d.year}-{d.year + 1} window; {n} were asked for"] if len(upcoming) < n else []
        return ok({"date": d.date().isoformat(), "next": upcoming, "count": len(upcoming)}, assumptions=a, warnings=short)
    years = p.get("years") or [p.get("year") or date.today().year]
    if isinstance(years, (int, str)):
        years = [int(years)]
    years = [int(y) for y in years]
    hm = holiday_map(code, set(years), subdiv, p.get("categories"))
    month = p.get("month")
    items = [{"date": k.isoformat(), "name": v, "weekday": k.strftime("%A")} for k, v in sorted(hm.items()) if (month is None or k.month == int(month))]
    long_weekends = []
    for k in sorted(hm):
        if k.weekday() == 4 or k.weekday() == 0:
            long_weekends.append({"date": k.isoformat(), "name": hm[k], "spans": f"{(k - timedelta(days=k.weekday() - 4 if k.weekday() == 4 else 2)).isoformat()} to {(k + timedelta(days=2 if k.weekday() == 4 else 0)).isoformat()}"})
    out = {"region": code, "subdiv": subdiv, "years": years, "count": len(items), "holidays": items, "long_weekends": long_weekends}
    assumptions = [] if subdiv else ["national holidays only; pass subdiv (e.g. state code) for regional ones" if _hol.list_supported_countries().get(code) else ""]
    return ok(out, assumptions=[x for x in assumptions if x])

#: Worked examples for the reference page, one list per mode. Every one of them is
#: executed when /docs/tools/holidays is built and sorted by the result into
#: "Examples" (the call succeeded) and "Fails when" (it did not), so a fixture never
#: states an expectation of its own. Mark anything whose output depends on the
#: current instant with "volatile": True.
EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "list": [
        {
            "caption": "India, one month, with the year's long weekends alongside.",
            "args": {"mode": "list", "region": "IN", "year": 2025, "month": 8},
        },
        {
            "caption": "The US, November.",
            "args": {"mode": "list", "region": "US", "year": 2025, "month": 11},
        },
        {
            "caption": "West Bengal's regional holidays, which the national list does not contain.",
            "args": {"mode": "list", "region": "IN", "year": 2025, "month": 10, "subdiv": "WB"},
        },
        {
            "caption": "An unsupported country code.",
            "args": {"mode": "list", "region": "XX", "year": 2025},
        },
        {
            "caption": "An unknown subdivision — the valid codes come back in the message.",
            "args": {"mode": "list", "region": "IN", "year": 2025, "subdiv": "ZZ"},
        },
    ],
    "check": [
        {
            "caption": "A national holiday.",
            "args": {"mode": "check", "region": "IN", "date": "2025-08-15"},
        },
        {
            "caption": "The next day, which is not.",
            "args": {"mode": "check", "region": "IN", "date": "2025-08-16"},
        },
        {
            "caption": "A date that is only a holiday in one state.",
            "args": {"mode": "check", "region": "IN", "date": "2025-10-20", "subdiv": "WB"},
        },
        {
            "caption": "An ambiguous numeric date, refused exactly as `datetime` refuses it.",
            "args": {"mode": "check", "region": "IN", "date": "03/04/2025"},
        },
        {
            "caption": "An unparseable date.",
            "args": {"mode": "check", "region": "IN", "date": "diwali"},
        },
    ],
    "next": [
        {
            "caption": "`n` beyond the cap is refused; the search window is only two calendar years.",
            "args": {"mode": "next", "region": "IN", "n": 100000},
        },
        {
            "caption": "The next three Indian holidays after a fixed date.",
            "args": {"mode": "next", "region": "IN", "date": "2025-08-01", "n": 3},
        },
        {
            "caption": "The same question for the UK, crossing into the following year.",
            "args": {"mode": "next", "region": "GB", "date": "2025-12-20", "n": 3},
        },
        {
            "caption": "An unsupported region.",
            "args": {"mode": "next", "region": "Atlantis", "date": "2025-08-01"},
        },
    ],
    "countries": [
        {
            "caption": "Every supported country code.",
            "args": {"mode": "countries"},
        },
    ],
    "subdivisions": [
        {
            "caption": "India's state codes.",
            "args": {"mode": "subdivisions", "region": "IN"},
        },
        {
            "caption": "The UK's four nations.",
            "args": {"mode": "subdivisions", "region": "GB"},
        },
        {
            "caption": "An unsupported region.",
            "args": {"mode": "subdivisions", "region": "XX"},
        },
    ],
}
