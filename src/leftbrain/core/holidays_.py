"""holidays - public holiday calendars for 150+ countries (offline dataset)."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

import holidays as _hol

from ..contract import Ambiguous, TooLarge, ToolError, check_params, ok, tool, whole

MODES = ("list", "check", "next", "countries", "subdivisions", "categories")

#: What each mode reads. Anything else in a call is a caller's mistake, not a default
#: to fall back on (#28 SS2a). Kept honest by tests/test_mode_params.py, which derives
#: the same map from the code and fails when the two drift. One set per mode.
MODE_PARAMS: dict[str, frozenset[str]] = {
    "list": frozenset({"categories", "country", "month", "region", "state", "subdiv", "year", "years"}),
    "check": frozenset({"categories", "country", "date", "date_locale", "locale", "region", "state", "subdiv", "value"}),
    "next": frozenset({"categories", "country", "date", "date_locale", "locale", "n", "region", "state", "subdiv"}),
    "countries": frozenset(),
    "subdivisions": frozenset({"country", "region"}),
    "categories": frozenset({"country", "region"}),
}

#: Past this year, lunar and observed-date rules are projections rather than calendars.
_ESTIMATED_AFTER = 2075

#: Upcoming holidays `next` will return; the search window is two calendar years.
MAX_NEXT = 100

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})


#: Codes that mean different countries under different conventions. The `holidays` package
#: resolves each of these its own way, and its way is not the IOC's: `BAH` is its abbreviation
#: for BAHrain, while the IOC gives BAH to the Bahamas and Bahrain BRN - and `BRN` is ISO-3 for
#: Brunei, which is where the package sends it. Both readings are defensible and only one can
#: win, so the resolution is stated out loud rather than picked in silence (#83).
_COLLIDING_CODES = {
    "BAH": "BAH is this dataset's abbreviation for Bahrain; the IOC gives BAH to the Bahamas, which is 'BS' or 'BHS' here",
    "BRN": "BRN is ISO-3 for Brunei, which is how it resolves here; the IOC gives BRN to Bahrain, which is 'BH'",
}


def country_name(code: str) -> str | None:
    """The English name for an alpha-2 code, for saying which country a code resolved to."""
    from .geo_offline import _tables  # lazy: avoids an import cycle

    return _tables()[1].get(code)


def _country(region: Any, notes: list[str] | None = None) -> str:
    """The ISO 3166-1 alpha-2 code the `holidays` library files a country under.

    Country names ("India", "Türkiye") and alpha-3 codes ("IND") are accepted and reduced
    to the alpha-2 code, which is what `subdivisions` and datetime's `region` expect.

    ``notes`` collects an assumption whenever the input was not already that code. Resolving
    `BAH` to Bahrain and echoing `region: "BH"` reads as normalisation, not as a different
    country, and wrong-country data that looks successful is worse than a refusal (#83).
    """
    from .geo_offline import country_code  # lazy: avoids an import cycle

    def resolved(code: str) -> str:
        if notes is not None and str(region).strip().upper() != code:
            name = country_name(code)
            notes.append(f"{str(region).strip()!r} read as {code}{f' ({name})' if name else ''}")
            warning = _COLLIDING_CODES.get(str(region).strip().upper())
            if warning:
                notes.append(warning)
        return code

    raw = str(region).strip()
    code = raw.upper()
    if code == "UK":
        code = "GB"
        return resolved(code)
    from holidays.registry import COUNTRIES  # (class, ISO-2, aliases...) per country

    for entry in COUNTRIES.values():
        if code in entry[2:]:  # an alpha-3 alias; the plain country list carries these too
            return resolved(entry[1])
    supported = _hol.list_supported_countries()
    if code in supported:
        return resolved(code)
    named = country_code(raw)
    if named and named in supported:
        return resolved(named)
    raise ToolError(f"unsupported region {region!r}; use an ISO code such as 'IN', 'US', 'GB'")


#: What the underlying dataset filters to when nobody says otherwise.
DEFAULT_CATEGORY = "public"


def supported_categories(code: str) -> tuple[str, ...]:
    """The category values this country actually accepts.

    There is no single enum to publish: `optional` is valid for IN and rejected outright for
    US, so the legal set has to be asked for per country rather than written into the schema
    (#75). This is what the `categories` mode returns and what a wrong guess is measured against.
    """
    try:
        return tuple(_hol.country_holidays(code).supported_categories)
    except Exception:  # noqa: BLE001 - a country we cannot introspect simply has nothing to offer
        return (DEFAULT_CATEGORY,)


def _checked_categories(code: str, categories: Any) -> tuple[str, ...]:
    """Validate against what this country supports, refusing in the contract's own shape.

    A wrong guess used to come back as raw text from the upstream package - "Category is not
    supported: unofficial, bank, ..." - with no `needs`, which is exactly the affordance an
    agent would have used to recover (#75).
    """
    wanted = tuple(categories) if isinstance(categories, (list, tuple)) else (categories,)
    wanted = tuple(str(c).strip().lower() for c in wanted if str(c).strip())
    allowed = supported_categories(code)
    unknown = [c for c in wanted if c not in allowed]
    if unknown:
        raise Ambiguous(
            f"{code} does not have {', '.join(sorted(set(unknown)))} holidays; it has {', '.join(allowed)}",
            field="categories",
            options=list(allowed),
        )
    return wanted


def holiday_map(region: str, years: set[int] | list[int], subdiv: str | None = None, categories: Any = None,
                notes: list[str] | None = None) -> dict[date, str]:
    code = _country(region, notes)
    kwargs: dict[str, Any] = {"years": sorted(years)}
    if subdiv:
        subdiv = str(subdiv).strip().upper()
        supported = _hol.list_supported_countries().get(code, [])
        if subdiv not in supported:
            raise ToolError(f"unknown subdivision {subdiv!r} for {code}; options: {', '.join(supported) or 'none'}")
        kwargs["subdiv"] = subdiv
    wanted = _checked_categories(code, categories) if categories else ()
    if wanted:
        kwargs["categories"] = wanted
    elif notes is not None:
        # The filter that was applied whether or not anyone asked for it. `check` returned
        # `is_holiday: false` for 2026-10-18 in West Bengal - the middle of Durga Puja - because
        # `optional` was excluded, and said nothing at all about having excluded it. Omitting
        # `subdiv` already produced an assumption; this was the one narrowing that stayed
        # hidden, and it is the one that turns a factual question into a confident wrong
        # answer (#72).
        rest = [c for c in supported_categories(code) if c != DEFAULT_CATEGORY]
        extra = f"; {code} also has {', '.join(rest)} - pass categories to include them" if rest else ""
        notes.append(f"{DEFAULT_CATEGORY} holidays only{extra}")
    try:
        h = _hol.country_holidays(code, **kwargs)
    except (NotImplementedError, KeyError, ValueError) as e:
        raise ToolError(f"holiday lookup failed for {code}: {e}") from None
    return {d: str(n) for d, n in h.items()}


def _region(p: dict[str, Any], notes: list[str] | None = None) -> str:
    region = p.get("region") or p.get("country")
    if not region:
        raise ToolError("'region' (ISO country code) is required")
    return _country(region, notes)


def _no_data(code: str, years: list[int]) -> str:
    span = f"{min(years)}" if len(years) == 1 else f"{min(years)}-{max(years)}"
    return f"the holiday calendar has no data for {code} in {span}; this is not the same as 'no holidays'"


def _mode_countries(p: dict[str, Any]) -> dict[str, Any]:
    """Every country, as an entry rather than a bare code.

    This used to be ~500 flat strings mixing ISO-2, ISO-3 and the dataset's own abbreviations
    with nothing to tell them apart and no names - so an agent looking for the Bahamas found
    `BAH`, used it, and got Bahrain's calendar (#83). One row per country, with its aliases
    beside it, makes the code to use obvious and the collisions visible.
    """
    from holidays.registry import COUNTRIES

    rows = []
    for entry in COUNTRIES.values():
        code, aliases = entry[1], list(entry[2:])
        row = {"code": code, "name": country_name(code), "aliases": aliases}
        collision = next((_COLLIDING_CODES[a] for a in aliases if a in _COLLIDING_CODES), None)
        if collision:
            row["note"] = collision
        rows.append(row)
    rows.sort(key=lambda r: r["code"])
    return ok({"countries": rows, "count": len(rows)},
              assumptions=["'code' is the value to pass as region; 'aliases' also resolve to it"])


def _mode_categories(p: dict[str, Any]) -> dict[str, Any]:
    """Which category values this country accepts - the enum that cannot live in the schema."""
    notes: list[str] = []
    code = _region(p, notes)
    allowed = supported_categories(code)
    return ok(
        {"region": code, "name": country_name(code), "categories": list(allowed), "default": DEFAULT_CATEGORY},
        assumptions=notes + [f"{DEFAULT_CATEGORY} is used when 'categories' is not given"],
    )


def _mode_subdivisions(p: dict[str, Any]) -> dict[str, Any]:
    code = _region(p)
    return ok({"region": code, "subdivisions": _hol.list_supported_countries().get(code, [])})


def _date_locale(p: dict[str, Any], notes: list[str]) -> Any:
    """The locale used to read an ambiguous *date*, under either name.

    It was called `locale`, which is why `hi` was tried and refused: nothing here localises a
    holiday's name, and the parameter has only ever disambiguated `03/04/2026`. The honest
    name is `date_locale`; `locale` still works and says what it actually did (#76).
    """
    if p.get("date_locale") is not None:
        return p["date_locale"]
    if p.get("locale") is not None:
        notes.append("'locale' here sets how an ambiguous date is read (DD/MM vs MM/DD), not the language of holiday names; it is 'date_locale'")
        return p["locale"]
    return None


def _mode_check(p: dict[str, Any]) -> dict[str, Any]:
    from .datetimex import parse_dt  # lazy import

    notes: list[str] = []
    code = _region(p, notes)
    subdiv = p.get("subdiv") or p.get("state")
    d, _, a = parse_dt(p.get("date") or p.get("value") or "today", locale=_date_locale(p, notes), field="date")
    hm = holiday_map(code, {d.year}, subdiv, p.get("categories"), notes)
    name = hm.get(d.date())
    # An empty calendar for the year is not the same as "not a holiday".
    warnings = [_no_data(code, [d.year])] if not hm else []
    observed = None
    if name is None and not p.get("categories"):
        # A bare `false` for a date the dataset does know about is the worst answer this tool
        # can give: the agent says "October 18 is not a holiday in West Bengal" in the middle
        # of Durga Puja. Saying the filter was applied is necessary but easy to skim past, so
        # the near miss is reported outright (#72, #80).
        wider = supported_categories(code)
        if len(wider) > 1:
            observed = holiday_map(code, {d.year}, subdiv, list(wider)).get(d.date())
            if observed:
                warnings.append(
                    f"{d.date().isoformat()} is {observed!r} under {code}'s other categories "
                    f"({', '.join(c for c in wider if c != DEFAULT_CATEGORY)}), which the "
                    f"{DEFAULT_CATEGORY}-only default excluded; pass categories to include it"
                )
    out = {"date": d.date().isoformat(), "is_holiday": name is not None, "name": name, "weekday": d.strftime("%A"), "is_weekend": d.weekday() >= 5}
    if observed:
        out["observed_elsewhere"] = observed
    return ok(out, assumptions=a + notes, warnings=warnings)


def _mode_next(p: dict[str, Any]) -> dict[str, Any]:
    from .datetimex import parse_dt  # lazy import

    notes: list[str] = []
    code = _region(p, notes)
    subdiv = p.get("subdiv") or p.get("state")
    d, _, a = parse_dt(p.get("date") or "today", locale=_date_locale(p, notes), field="date")
    n = whole(p.get("n", 5), "n", lo=1)
    if n > MAX_NEXT:
        raise TooLarge(
            f"n is {n:,}; the most this mode returns is {MAX_NEXT}",
            details={"n": n, "limit": MAX_NEXT},
            hint=f"Ask for at most {MAX_NEXT}, or use mode 'list' with a year.",
        )
    hm = holiday_map(code, {d.year, d.year + 1}, subdiv, p.get("categories"), notes)
    found = [{"date": k.isoformat(), "name": v, "weekday": k.strftime("%A"), "days_away": (k - d.date()).days} for k, v in sorted(hm.items()) if k >= d.date()]
    upcoming = found[:n]
    # The window is this year and the next, so asking for more than it holds is not
    # an empty answer - say the list is short because the calendar ran out (#28 SS2f).
    short = [f"only {len(upcoming)} holidays fall in the {d.year}-{d.year + 1} window; {n} were asked for"] if len(upcoming) < n else []
    return ok({"date": d.date().isoformat(), "next": upcoming, "count": len(upcoming)}, assumptions=a + notes, warnings=short)


def _month(raw: Any) -> int:
    if isinstance(raw, str) and raw.strip().lower() in _MONTHS:
        return _MONTHS[raw.strip().lower()]
    month = whole(raw, "month")
    if not 1 <= month <= 12:
        raise ToolError(
            f"month {month} is not a month; months run from 1 to 12",
            details={"month": month},
            hint="Leave 'month' out to get the whole year.",
        )
    return month


def _mode_list(p: dict[str, Any]) -> dict[str, Any]:
    notes: list[str] = []
    code = _region(p, notes)
    subdiv = p.get("subdiv") or p.get("state")
    if p.get("years") is not None:
        raw_years = p["years"] if isinstance(p["years"], list) else [p["years"]]
    elif p.get("year") is not None:
        raw_years = [p["year"]]
    else:
        raw_years = [date.today().year]
    years = [whole(y, "year", lo=1, hi=9999) for y in raw_years]
    hm = holiday_map(code, set(years), subdiv, p.get("categories"), notes)
    month = _month(p["month"]) if p.get("month") is not None else None
    items = [{"date": k.isoformat(), "name": v, "weekday": k.strftime("%A")} for k, v in sorted(hm.items()) if (month is None or k.month == month)]
    long_weekends = []
    # `month` used to filter `holidays` and leave `long_weekends` as the whole year, so a
    # month with no holidays came back with a year's worth of long weekends (#28 SS2c).
    for k in sorted(k for k in hm if month is None or k.month == month):
        if k.weekday() == 4 or k.weekday() == 0:
            long_weekends.append({"date": k.isoformat(), "name": hm[k], "spans": f"{(k - timedelta(days=k.weekday() - 4 if k.weekday() == 4 else 2)).isoformat()} to {(k + timedelta(days=2 if k.weekday() == 4 else 0)).isoformat()}"})
    out = {"region": code, "subdiv": subdiv, "years": years, "count": len(items), "holidays": items, "long_weekends": long_weekends}
    assumptions = list(notes) if subdiv else [*notes, "national holidays only; pass subdiv (e.g. state code) for regional ones" if _hol.list_supported_countries().get(code) else ""]
    # An empty list because the calendar has no data for that year reads exactly like an
    # empty list because there are no holidays. Say which (#28 SS3.13).
    warnings = []
    if not hm:
        warnings.append(_no_data(code, years))
    elif any(y > _ESTIMATED_AFTER for y in years):
        warnings.append(f"years after {_ESTIMATED_AFTER} are estimated: lunar and observed-date rules are not fixed that far ahead")
    return ok(out, assumptions=[x for x in assumptions if x], warnings=warnings)


@tool
def holidays(mode: str = "list", **params: Any) -> dict[str, Any]:
    """Public holidays. Modes: list, check, next, countries, subdivisions, categories."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    check_params("holidays", mode, p, MODE_PARAMS)
    return {"list": _mode_list, "check": _mode_check, "next": _mode_next, "countries": _mode_countries,
            "subdivisions": _mode_subdivisions, "categories": _mode_categories}[mode](p)


#: Worked examples for the reference page, one list per mode. Every one of them is
#: executed when /docs/tools/holidays is built and sorted by the result into
#: "Examples" (the call succeeded) and "Fails when" (it did not), so a fixture never
#: states an expectation of its own. Mark anything whose output depends on the
#: current instant with "volatile": True.
EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "list": [
        {
            "caption": "A month outside 1..12 is refused rather than answered with an empty list.",
            "args": {"mode": "list", "region": "IN", "year": 2026, "month": 13},
        },
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
    "categories": [
        {
            "caption": "Which category values a country accepts. The set differs per country, so it cannot live in the schema.",
            "args": {"mode": "categories", "region": "IN"},
        },
        {
            "caption": "The US has no 'optional' category, which is why a guess that works for India fails here.",
            "args": {"mode": "categories", "region": "US"},
        },
        {
            "caption": "A region that does not exist.",
            "args": {"mode": "categories", "region": "ZZ"},
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
