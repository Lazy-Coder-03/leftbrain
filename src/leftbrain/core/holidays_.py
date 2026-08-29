"""holidays - public holiday calendars for 150+ countries (offline dataset)."""

from __future__ import annotations

import calendar
import difflib
import hashlib
import re
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

import holidays as _hol

from ..contract import Ambiguous, TooLarge, ToolError, Unsupported, check_params, ok, tool, whole

MODES = ("list", "check", "next", "countries", "subdivisions", "festival", "upcoming", "compare", "categories")

#: What each mode reads. Anything else in a call is a caller's mistake, not a default
#: to fall back on (#28 SS2a). Kept honest by tests/test_mode_params.py, which derives
#: the same map from the code and fails when the two drift. One set per mode.
MODE_PARAMS: dict[str, frozenset[str]] = {
    "list": frozenset({"categories", "country", "format", "language", "month", "region", "state", "subdiv", "year", "years"}),
    "check": frozenset({"categories", "country", "date", "date_locale", "language", "locale", "region", "state", "subdiv", "value"}),
    "next": frozenset({"categories", "country", "date", "date_locale", "language", "locale", "n", "region", "state", "subdiv"}),
    "countries": frozenset(),
    "subdivisions": frozenset({"country", "region"}),
    "categories": frozenset({"country", "region"}),
    "festival": frozenset({"categories", "country", "language", "name", "region", "state", "subdiv", "year"}),
    "upcoming": frozenset({"categories", "country", "date", "date_locale", "end", "language", "locale", "n", "region", "start", "state", "subdiv"}),
    "compare": frozenset({"categories", "country", "language", "month", "region", "regions", "subdivs", "year"}),
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


#: The dataset's per-country category names, mapped onto one vocabulary that means the same
#: thing everywhere. The upstream sets are whatever each country's maintainer modelled -
#: `optional` exists for India and not for the US - so a caller could not ask "is this a bank
#: holiday" without first learning that country's names (#89). Anything unrecognised is
#: reported as itself rather than forced into a bucket it may not belong in.
CLASSIFICATIONS: dict[str, str] = {
    "public": "public_holiday",
    "government": "government_holiday",
    "bank": "bank_holiday",
    "half_day": "half_day",
    "optional": "optional_holiday",
    "optional_women": "optional_holiday",
    "religious": "religious_festival",
    "catholic": "religious_festival",
    "christian": "religious_festival",
    "hebrew": "religious_festival",
    "islamic": "religious_festival",
    "chinese": "religious_festival",
    "hindu": "religious_festival",
    "buddhist": "religious_festival",
    "school": "observance",
    "unofficial": "observance",
    "workday": "working_day",
    "armed_forces": "observance",
}


def classify(category: str) -> str:
    """One name for a kind of day, whatever this country calls it."""
    return CLASSIFICATIONS.get(str(category).lower(), f"unclassified:{category}")


#: Kinds of day that mean the office is shut. `optional_holiday` deliberately is not one:
#: whether it is taken off is the caller's question, not the dataset's.
CLOSES_OFFICES = frozenset({"public_holiday", "government_holiday", "bank_holiday"})


def observances(code: str, day: date, subdiv: str | None, language: str | None = None) -> list[dict[str, str]]:
    """Everything the dataset marks on one date, each with what kind of day it is.

    `is_holiday` and `is_weekend` had to carry every question and could not: 2026-10-18 in
    West Bengal came back `is_holiday: false, is_weekend: true`, which is true, useless, and
    silent about the date being Durga Puja Saptami (#95).
    """
    found: list[dict[str, str]] = []
    for category in supported_categories(code):
        try:
            named = holiday_map(code, {day.year}, subdiv, [category], None, language).get(day)
        except ToolError:
            continue
        if not named:
            continue
        for name in str(named).split("; "):
            if not any(f["name"] == name for f in found):
                found.append({"name": name, "category": category, "classification": classify(category)})
    return found


@lru_cache(maxsize=256)
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


def provenance(code: str, language: str | None = None, categories: Any = None) -> dict[str, Any]:
    """Where a date came from: the source, its range, and how it was read."""
    reach = coverage(code)
    return {
        "source": f"python-holidays {_hol.__version__}",
        "calendar": "gregorian",
        "covers": {"from": reach["start_year"], "to": reach["end_year"]},
        "language": language or reach["default_language"],
        "categories": list(categories) if categories else [DEFAULT_CATEGORY],
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


def _language(p: dict[str, Any], code: str, notes: list[str]) -> str | None:
    """The language for holiday *names*, which is a real capability and was never exposed.

    `locale` was refused for `hi` with "use a country code" - but the dataset carries Hindi,
    Bengali, Tamil and eight more for India, and that is plainly what someone passing `hi`
    wanted. `locale` sets how a *date* is read; `language` sets what the names come back in
    (#76).
    """
    wanted = p.get("language")
    if wanted is None:
        return None
    available = coverage(code)["languages"]
    key = str(wanted).strip()
    if not available:
        raise Unsupported(
            f"{code}'s holiday names are not translated; they come back in the source's own language",
            details={"region": code, "languages": []},
        )
    match = next((a for a in available if a.lower() == key.lower()), None)
    if match is None:  # `hi` should find `hi_IN`, and `en` the first English there is
        match = next((a for a in available if a.lower().split("_")[0] == key.lower().split("_")[0]), None)
        if match:
            notes.append(f"language {key!r} read as {match!r}")
    if match is None:
        raise Ambiguous(
            f"{code} has no holiday names in {key!r}",
            field="language",
            options=list(available),
        )
    return match


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


#: Names a caller will reach for, mapped onto what this dataset calls the same festival.
#: Every entry was checked against the data rather than assumed: West Bengal 2026 files the
#: Durga Puja days as `Dussehra (Saptami)` and so on, and Kali Puja only as
#: `Naraka Chaturdashi`. A miss is answered with near misses (#87), so this table only has to
#: carry the ones that are common *and* spelled differently - it is not a festival list, and
#: it never invents a date.
FESTIVAL_ALIASES: dict[str, str] = {
    "durga puja": "dussehra",
    "durgotsav": "dussehra",
    "durga pujo": "dussehra",
    "kali puja": "naraka chaturdashi",
    "shyama puja": "naraka chaturdashi",
    "saraswati puja": "basant panchami",
    "sri panchami": "basant panchami",
    "bengali new year": "pohela boishakh",
    "poila boishakh": "pohela boishakh",
    "bhai phota": "bhai duj",
    "bhai phonta": "bhai duj",
    "chhath puja": "chhat puja",
}


def _festival_key(name: str) -> str:
    """A festival's name reduced for matching: case, punctuation and the day in brackets gone."""
    base = re.sub(r"\([^)]*\)", " ", str(name))
    return re.sub(r"[^a-z0-9]+", " ", base.lower()).strip()


def _words(name: str) -> str:
    """A name reduced for matching, brackets and all."""
    return re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()


def _grouped(hm: dict[date, str]) -> dict[str, list[tuple[date, str]]]:
    """Every entry filed under its festival, so a multi-day festival is one thing.

    Durga Puja came back as unrelated rows sharing a prefix - `Dussehra (Saptami)`,
    `Dussehra (Mahanavami); Dussehra (Mahashtami)` - and an agent asked for "the Durga Puja
    dates" had to reassemble that from string prefixes (#88).
    """
    out: dict[str, list[tuple[date, str]]] = {}
    for day, joined in sorted(hm.items()):
        for name in str(joined).split("; "):
            out.setdefault(_festival_key(name), []).append((day, name))
    return out


def _day_name(name: str) -> str | None:
    """`Dussehra (Saptami)` -> `Saptami`: the named day within a festival."""
    match = re.search(r"\(([^)]*)\)", str(name))
    return match.group(1).strip() if match else None


#: What `list` can hand back besides JSON rows.
FORMATS = ("json", "ics", "csv")


def _ics_escape(text: str) -> str:
    """RFC 5545 escaping: backslash, semicolon, comma and newline are all special."""
    return str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _uid(code: str, subdiv: str | None, row: dict[str, Any]) -> str:
    """A stable event id. Not `hash()`: PYTHONHASHSEED is randomised per process, so the same
    calendar exported twice would carry different UIDs and a client would add the events again
    instead of updating them."""
    seed = "|".join([code, str(subdiv), str(row["date"]), str(row["name"])])
    return hashlib.sha1(seed.encode()).hexdigest()[:16]


def _as_ics(rows: list[dict[str, Any]], code: str, subdiv: str | None) -> str:
    """An all-day VEVENT per entry, which is what a holiday is.

    DTEND is exclusive in RFC 5545, so a one-day event ends on the following day. Getting that
    wrong is the classic off-by-one that makes every imported holiday a day short.
    """
    name = f"{code}{'/' + subdiv if subdiv else ''} holidays"
    out = [
        "BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//leftbrain//holidays {_hol.__version__}//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{_ics_escape(name)}",
    ]
    for row in rows:
        day = date.fromisoformat(row["date"])
        out += [
            "BEGIN:VEVENT",
            f"UID:{row['date']}-{_uid(code, subdiv, row)}@leftbrain",
            f"DTSTAMP:{day.strftime('%Y%m%d')}T000000Z",
            f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{_ics_escape(row['name'])}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


def _as_csv(rows: list[dict[str, Any]]) -> str:
    """Reuses `collections.to_csv`, so the formula escaping is the one already tested."""
    from .collections_ import collections as collections_tool

    answered = collections_tool("to_csv", items=rows or [{"date": "", "name": "", "weekday": ""}])
    if not answered["ok"]:  # pragma: no cover - to_csv refusing well-formed records
        raise ToolError(answered["message"])
    return answered["result"]["csv"] if rows else "date,name,weekday\r\n"


def festival_dates(name: str, year: int, region: str, subdiv: str | None = None,
                   categories: Any = None, notes: list[str] | None = None) -> list[tuple[date, str]]:
    """Every dated entry for one festival, or an `Ambiguous` naming the near misses.

    Shared with `datetime` so a festival can anchor date arithmetic without that module
    learning anything about holidays (#92).
    """
    notes = notes if notes is not None else []
    code = _country(region, notes)
    wanted = str(name).strip()
    chosen = categories or list(supported_categories(code))
    hm = holiday_map(code, {int(year)}, subdiv, chosen, notes, None)
    groups = _grouped(hm)
    full_names = {k: " ".join(_words(n) for _d, n in entries) for k, entries in groups.items()}
    key = _festival_key(wanted)
    if key in FESTIVAL_ALIASES:
        notes.append(f"{wanted!r} looked up as {FESTIVAL_ALIASES[key]!r}, which is what this dataset calls it")
        key = FESTIVAL_ALIASES[key]
    matches = [k for k in groups if k == key]
    if not matches:
        # A named day inside a festival - "Saptami" is `Dussehra (Saptami)` - and it has to be
        # tried before the substring pass, which would otherwise match the whole of Dussehra
        # and hand back four dates for a question about one.
        day_named = [(d, n) for entries in groups.values() for d, n in entries if _words(_day_name(n) or "") == key]
        if day_named:
            return sorted(set(day_named))
        matches = [k for k in groups if key in k or k in key or key in full_names[k]]
    if not matches:
        close = difflib.get_close_matches(key, list(groups), n=5, cutoff=0.5)
        named = sorted({n for k in (close or list(groups)[:8]) for _d, n in groups[k]})
        raise Ambiguous(
            f"{code}{'/' + subdiv if subdiv else ''} has no festival or day matching {wanted!r} in {year}",
            field="name",
            options=named,
        )
    return sorted({(d, n) for k in matches for d, n in groups[k]})


def _mode_festival(p: dict[str, Any]) -> dict[str, Any]:
    """A festival by name, with its named days in order (#87, #88)."""
    notes: list[str] = []
    code = _region(p, notes)
    wanted = p.get("name")
    if not wanted:
        raise ToolError("'name' is required: the festival to look up")
    subdiv = p.get("subdiv") or p.get("state")
    language = _language(p, code, notes)
    year = whole(p.get("year", date.today().year), "year", lo=1, hi=9999)
    categories = p.get("categories") or list(supported_categories(code))
    if not p.get("categories"):
        notes.append(f"every category searched ({', '.join(categories)}); a festival is often not a {DEFAULT_CATEGORY} holiday")
    hm = holiday_map(code, {year}, subdiv, categories, notes, language)
    groups = _grouped(hm)
    key = _festival_key(wanted)
    if key in FESTIVAL_ALIASES:
        notes.append(f"{str(wanted)!r} looked up as {FESTIVAL_ALIASES[key]!r}, which is what this dataset calls it")
        key = FESTIVAL_ALIASES[key]
    #: the whole name as well as the grouping key, because `Diwali (Deepavali)` keeps the
    #: common name in the brackets that grouping strips
    full_names = {k: " ".join(_words(name) for _day, name in entries) for k, entries in groups.items()}
    matches = [k for k in groups if k == key]
    if not matches:
        matches = [k for k in groups if key in k or k in key or key in full_names[k]]
    if not matches:
        # An empty result reads as "there is no such festival", which is a claim about the
        # world rather than about this dataset. The near misses say which it is (#87).
        close = difflib.get_close_matches(key, list(groups), n=5, cutoff=0.5)
        close += [k for k in groups if any(part in full_names[k] for part in key.split() if len(part) > 3)][:5]
        raise Ambiguous(
            f"{code}{'/' + subdiv if subdiv else ''} has no festival matching {str(wanted)!r} in {year}",
            field="name",
            options=sorted({groups[k][0][1] for k in (close or list(groups)[:8])}),
        )
    entries = sorted({(day, name) for k in matches for day, name in groups[k]})
    days = [{"date": day.isoformat(), "name": name, "day": _day_name(name), "weekday": day.strftime("%A")} for day, name in entries]
    shared = [d["date"] for d in days if [x["date"] for x in days].count(d["date"]) > 1]
    warnings = []
    if shared:
        warnings.append(f"{len(set(shared))} date(s) carry more than one named day; tithis can overlap, but verify against a local calendar")
    return ok(
        {
            "festival": entries[0][1].split(" (")[0],
            "region": code, "subdiv": subdiv, "year": year,
            "days": days, "count": len(days),
            "span": {"start": days[0]["date"], "end": days[-1]["date"]},
            "provenance": provenance(code, language, categories),
        },
        assumptions=notes,
        warnings=warnings,
    )


def _mode_upcoming(p: dict[str, Any]) -> dict[str, Any]:
    """What is coming up, across every category rather than only public holidays (#87)."""
    from .datetimex import parse_dt

    notes: list[str] = []
    code = _region(p, notes)
    subdiv = p.get("subdiv") or p.get("state")
    language = _language(p, code, notes)
    start, _, a = parse_dt(p.get("start") or p.get("date") or "today", locale=_date_locale(p, notes), field="start")
    if p.get("end") is not None:
        end, _, _ = parse_dt(p["end"], locale=_date_locale(p, notes), field="end")
        end = end.date()
    else:
        end = date(start.year + 1, start.month, start.day) if start.month != 2 or start.day != 29 else date(start.year + 1, 3, 1)
        notes.append("no 'end': the twelve months from 'start'")
    categories = p.get("categories") or list(supported_categories(code))
    if not p.get("categories"):
        notes.append(f"every category searched ({', '.join(categories)})")
    hm = holiday_map(code, {start.year, end.year}, subdiv, categories, notes, language)
    n = whole(p.get("n", 20), "n", lo=1, hi=MAX_NEXT)
    rows = []
    for day, joined in sorted(hm.items()):
        if start.date() <= day <= end:
            for name in str(joined).split("; "):
                rows.append({"date": day.isoformat(), "name": name, "day": _day_name(name), "weekday": day.strftime("%A"), "days_away": (day - start.date()).days})
    warnings = [f"{len(rows) - n} more in the window; raise 'n'"] if len(rows) > n else []
    return ok(
        {"start": start.date().isoformat(), "end": end.isoformat(), "festivals": rows[:n], "count": min(len(rows), n),
         "truncated": len(rows) > n, "provenance": provenance(code, language, categories)},
        assumptions=a + notes, warnings=warnings,
    )


def _mode_compare(p: dict[str, Any]) -> dict[str, Any]:
    """The same dates across regions, as a table rather than two lists to reconcile (#94)."""
    notes: list[str] = []
    where = p.get("subdivs") or p.get("regions")
    if not where or not isinstance(where, list) or len(where) < 2:
        raise ToolError("'subdivs' (or 'regions') needs two or more to compare")
    year = whole(p.get("year", date.today().year), "year", lo=1, hi=9999)
    month = _month(p["month"]) if p.get("month") is not None else None
    across_regions = p.get("regions") is not None
    base = _region(p, notes) if not across_regions else None
    language = _language(p, base, notes) if base else None
    # Categories are per country - `optional` exists for India and not for the US - so a shared
    # default taken from the first one is refused by the rest. Each place gets its own unless
    # the caller named a set explicitly.
    asked = p.get("categories")
    columns, maps, used = [], {}, {}
    for one in where:
        code = _country(one, notes) if across_regions else base
        subdiv = None if across_regions else str(one).upper()
        label = str(one).upper()
        columns.append(label)
        categories = asked or list(supported_categories(code))
        used[label] = categories
        maps[label] = holiday_map(code, {year}, subdiv, categories, notes, language)
    if not asked and len({tuple(v) for v in used.values()}) > 1:
        notes.append("every category searched in each place, and they differ: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in used.items()))
    elif not asked:
        notes.append(f"every category searched ({', '.join(next(iter(used.values())))})")
    categories = asked or sorted({c for v in used.values() for c in v})
    every = sorted({d for m in maps.values() for d in m if month is None or d.month == month})
    rows = []
    for day in every:
        observed = {c: maps[c].get(day) for c in columns}
        rows.append({
            "date": day.isoformat(),
            "weekday": day.strftime("%A"),
            "observed_in": [c for c in columns if observed[c]],
            "not_in": [c for c in columns if not observed[c]],
            "names": {c: observed[c] for c in columns if observed[c]},
            "everywhere": all(observed.values()),
        })
    return ok(
        {"compared": columns, "year": year, "month": month, "dates": rows, "count": len(rows),
         "shared": sum(1 for r in rows if r["everywhere"]),
         "provenance": provenance(base or _country(str(where[0])), language, categories)},
        assumptions=notes,
    )


def _mode_categories(p: dict[str, Any]) -> dict[str, Any]:
    """Which category values this country accepts - the enum that cannot live in the schema."""
    notes: list[str] = []
    code = _region(p, notes)
    allowed = supported_categories(code)
    return ok(
        {"region": code, "name": country_name(code), "categories": list(allowed), "default": DEFAULT_CATEGORY,
         "languages": list(coverage(code)["languages"]), "default_language": coverage(code)["default_language"],
         "provenance": provenance(code)},
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
    language = _language(p, code, notes)
    hm = holiday_map(code, {d.year}, subdiv, p.get("categories"), notes, language)
    name = hm.get(d.date())
    # An empty calendar for the year is not the same as "not a holiday".
    warnings = [_no_data(code, [d.year])] if not hm else []
    marked = observances(code, d.date(), subdiv, language)
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
    weekend = d.weekday() >= 5
    closes = any(m["classification"] in CLOSES_OFFICES for m in marked)
    out = {
        "date": d.date().isoformat(),
        "is_holiday": name is not None,  # unchanged: what the selected categories say
        "name": name,
        "weekday": d.strftime("%A"),
        "is_weekend": weekend,
        # `is_holiday` alone could not answer "is anything marked here" or "is the office
        # shut", and a holiday landing on a Sunday was indistinguishable from one that did
        # not. `day_off` is the question most callers actually mean (#95).
        "is_observed": bool(marked),
        "day_off": bool(weekend or closes),
        "observances": marked,
        "provenance": provenance(code, language, p.get("categories")),
    }
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
    language = _language(p, code, notes)
    hm = holiday_map(code, {d.year, d.year + 1}, subdiv, p.get("categories"), notes, language)
    found = [{"date": k.isoformat(), "name": v, "weekday": k.strftime("%A"), "days_away": (k - d.date()).days} for k, v in sorted(hm.items()) if k >= d.date()]
    upcoming = found[:n]
    # The window is this year and the next, so asking for more than it holds is not
    # an empty answer - say the list is short because the calendar ran out (#28 SS2f).
    short = [f"only {len(upcoming)} holidays fall in the {d.year}-{d.year + 1} window; {n} were asked for"] if len(upcoming) < n else []
    return ok({"date": d.date().isoformat(), "next": upcoming, "count": len(upcoming), "provenance": provenance(code, language, p.get("categories"))}, assumptions=a + notes, warnings=short)


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
    language = _language(p, code, notes)
    hm = holiday_map(code, set(years), subdiv, p.get("categories"), notes, language)
    month = _month(p["month"]) if p.get("month") is not None else None
    items = [{"date": k.isoformat(), "name": v, "weekday": k.strftime("%A")} for k, v in sorted(hm.items()) if (month is None or k.month == month)]
    long_weekends = []
    # `month` used to filter `holidays` and leave `long_weekends` as the whole year, so a
    # month with no holidays came back with a year's worth of long weekends (#28 SS2c).
    for k in sorted(k for k in hm if month is None or k.month == month):
        if k.weekday() == 4 or k.weekday() == 0:
            long_weekends.append({"date": k.isoformat(), "name": hm[k], "spans": f"{(k - timedelta(days=k.weekday() - 4 if k.weekday() == 4 else 2)).isoformat()} to {(k + timedelta(days=2 if k.weekday() == 4 else 0)).isoformat()}"})
    fmt = str(p.get("format") or "json").lower()
    if fmt not in FORMATS:
        raise ToolError(f"format must be one of {', '.join(FORMATS)}")
    out: dict[str, Any] = {"region": code, "subdiv": subdiv, "years": years, "count": len(items), "provenance": provenance(code, language, p.get("categories"))}
    if fmt == "json":
        out.update({"holidays": items, "long_weekends": long_weekends})
    else:
        # The thing people actually do with a holiday calendar is put it in a calendar, or
        # open it in a spreadsheet. Both were the caller's job to write (#93).
        out["format"] = fmt
        out["content"] = _as_ics(items, code, subdiv) if fmt == "ics" else _as_csv(items)
        out["media_type"] = "text/calendar" if fmt == "ics" else "text/csv"
        out["filename"] = f"{code.lower()}{'-' + subdiv.lower() if subdiv else ''}-{'-'.join(str(y) for y in years)}.{fmt}"
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
            "subdivisions": _mode_subdivisions, "categories": _mode_categories, "festival": _mode_festival,
            "upcoming": _mode_upcoming, "compare": _mode_compare}[mode](p)


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
    "festival": [
        {
            "caption": "Durga Puja's days, in order. The dataset files them under 'Dussehra'; the substitution is stated.",
            "args": {"mode": "festival", "region": "IN", "subdiv": "WB", "name": "Durga Puja", "year": 2026},
        },
        {
            "caption": "Kali Puja exists here only as 'Naraka Chaturdashi'.",
            "args": {"mode": "festival", "region": "IN", "subdiv": "WB", "name": "Kali Puja", "year": 2026},
        },
        {
            "caption": "A festival this dataset does not carry: refused with the near misses, not an empty list.",
            "args": {"mode": "festival", "region": "IN", "subdiv": "WB", "name": "Jagadhatri Puja", "year": 2026},
        },
    ],
    "upcoming": [
        {
            "caption": "What falls in October in West Bengal, across every category.",
            "args": {"mode": "upcoming", "region": "IN", "subdiv": "WB", "start": "2026-10-01", "end": "2026-10-31"},
        },
        {
            "caption": "The next few, from a date, over the following twelve months.",
            "args": {"mode": "upcoming", "region": "IN", "subdiv": "WB", "start": "2026-06-01", "n": 5},
        },
        {
            "caption": "A year the source does not reach is refused, because an empty list would read as 'nothing happens'.",
            "args": {"mode": "upcoming", "region": "IN", "start": "2200-01-01", "end": "2200-12-31"},
        },
    ],
    "compare": [
        {
            "caption": "October in West Bengal against Assam: which dates each observes.",
            "args": {"mode": "compare", "region": "IN", "subdivs": ["WB", "AS"], "year": 2026, "month": 10},
        },
        {
            "caption": "Across countries instead of states.",
            "args": {"mode": "compare", "regions": ["IN", "US"], "year": 2026, "month": 1},
        },
        {
            "caption": "One place is not a comparison.",
            "args": {"mode": "compare", "region": "IN", "subdivs": ["WB"], "year": 2026},
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
