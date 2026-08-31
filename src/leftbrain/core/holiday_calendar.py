"""The public-holiday calendar behind `datetime`'s business-day arithmetic.

This is what survived the `holidays` tool. That tool answered "which festival is when" from
tables it could not vouch for, and was retired in 0.5.0 rather than kept as a source of
confident wrong dates (#80, #86, #91). Counting working days needs only the public-holiday
tables, which are the part of the upstream dataset that is what it says it is - so those stay,
behind `region` / `subdiv` on `datetime`'s `add`, `diff` and `business_days`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import holidays as _hol

from ..contract import Ambiguous, ToolError, Unsupported

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

def coverage(code: str) -> dict[str, Any]:
    """What this country's calendar is, and how far it reaches.

    Every date came back with no indication of its source or its range, so a date the dataset
    tabulates and one from a year it does not cover were indistinguishable - and the second
    was an empty list, which reads exactly like "no holidays" (#90).
    """
    try:
        entry = _hol.country_holidays(code)
    except Exception:  # noqa: BLE001 - a country we cannot introspect still answers `list`
        return {"start_year": None, "end_year": None, "languages": (), "default_language": None}
    return {
        "start_year": getattr(entry, "start_year", None),
        "end_year": getattr(entry, "end_year", None),
        "languages": tuple(getattr(entry, "supported_languages", ()) or ()),
        "default_language": getattr(entry, "default_language", None),
    }

def _check_years(code: str, years: list[int]) -> None:
    """Refuse a year the source does not reach, rather than answering an empty list.

    An empty list because the tables stop in 2100 reads exactly like an empty list because
    nothing falls in that year, and only one of those is a fact about holidays (#90).
    """
    reach = coverage(code)
    lo, hi = reach["start_year"], reach["end_year"]
    if lo is None or hi is None:
        return
    outside = sorted(y for y in years if y < lo or y > hi)
    if outside:
        raise Unsupported(
            f"the calendar for {code} covers {lo}-{hi}; {', '.join(str(y) for y in outside)} "
            f"{'is' if len(outside) == 1 else 'are'} outside it, and an empty list would read as 'no holidays'",
            details={"region": code, "covers": {"from": lo, "to": hi}, "outside": outside},
            hint=f"Ask for a year between {lo} and {hi}.",
        )

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
                notes: list[str] | None = None, language: str | None = None) -> dict[date, str]:
    code = _country(region, notes)
    _check_years(code, sorted(years))
    kwargs: dict[str, Any] = {"years": sorted(years)}
    if language:
        kwargs["language"] = language
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
