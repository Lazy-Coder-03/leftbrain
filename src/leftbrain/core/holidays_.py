"""holidays - public holiday calendars for 150+ countries (offline dataset)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import holidays as _hol

from ..contract import ToolError, ok, tool

MODES = ("list", "check", "next", "countries", "subdivisions")


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
        d, _, a = parse_dt(p.get("date") or p.get("from") or "today", locale=p.get("locale"), field="date")
        n = int(p.get("n", 5))
        hm = holiday_map(code, {d.year, d.year + 1}, subdiv, p.get("categories"))
        upcoming = [{"date": k.isoformat(), "name": v, "weekday": k.strftime("%A"), "days_away": (k - d.date()).days} for k, v in sorted(hm.items()) if k >= d.date()][:n]
        return ok({"from": d.date().isoformat(), "next": upcoming}, assumptions=a)
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
