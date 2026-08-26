"""datetime - clocks, calendars and timezones, done exactly.

Modes: now, convert_tz, parse, add, diff, weekday, nth_weekday, business_days,
overlap, duration_sum, recurrence, cron_next, age, fiscal.

Principles:
* IANA zone names only.  Abbreviations like "IST" or "CST" are ambiguous and
  are refused with the concrete options (a few unambiguous ones are accepted
  with an explicit assumption).
* Numeric dates like 03/04/2025 are refused unless a locale disambiguates
  DD/MM from MM/DD.
* Every returned instant carries its offset, zone and weekday so the caller
  never re-derives them.
"""

from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, available_timezones

from dateutil import parser as duparser
from dateutil.relativedelta import relativedelta
from dateutil.rrule import rrulestr

from ..contract import Ambiguous, ToolError, ok, tool

MODES = (
    "now",
    "convert_tz",
    "parse",
    "add",
    "diff",
    "weekday",
    "nth_weekday",
    "business_days",
    "overlap",
    "duration_sum",
    "recurrence",
    "cron_next",
    "age",
    "fiscal",
)

# --------------------------------------------------------------------------- #
# Timezones
# --------------------------------------------------------------------------- #

_ABBREV: dict[str, list[str]] = {
    "IST": ["Asia/Kolkata", "Asia/Jerusalem", "Europe/Dublin"],
    "EST": ["America/New_York", "America/Panama", "America/Jamaica"],
    "EDT": ["America/New_York"],
    "ET": ["America/New_York"],
    "CST": ["America/Chicago", "Asia/Shanghai", "America/Havana"],
    "CDT": ["America/Chicago"],
    "CT": ["America/Chicago"],
    "MST": ["America/Denver", "America/Phoenix"],
    "MDT": ["America/Denver"],
    "MT": ["America/Denver"],
    "PST": ["America/Los_Angeles", "Asia/Manila"],
    "PDT": ["America/Los_Angeles"],
    "PT": ["America/Los_Angeles"],
    "AKST": ["America/Anchorage"],
    "HST": ["Pacific/Honolulu"],
    "AST": ["America/Halifax", "Asia/Riyadh", "America/Puerto_Rico"],
    "BST": ["Europe/London", "Asia/Dhaka"],
    "CET": ["Europe/Berlin", "Europe/Paris", "Europe/Madrid", "Europe/Rome", "Europe/Amsterdam"],
    "CEST": ["Europe/Berlin", "Europe/Paris", "Europe/Madrid", "Europe/Rome", "Europe/Amsterdam"],
    "EET": ["Europe/Athens", "Europe/Helsinki", "Europe/Kyiv", "Africa/Cairo"],
    "WET": ["Europe/Lisbon"],
    "MSK": ["Europe/Moscow"],
    "SAST": ["Africa/Johannesburg"],
    "EAT": ["Africa/Nairobi"],
    "WAT": ["Africa/Lagos"],
    "CAT": ["Africa/Maputo"],
    "GST": ["Asia/Dubai"],
    "PKT": ["Asia/Karachi"],
    "NPT": ["Asia/Kathmandu"],
    "SGT": ["Asia/Singapore"],
    "MYT": ["Asia/Kuala_Lumpur"],
    "WIB": ["Asia/Jakarta"],
    "ICT": ["Asia/Bangkok"],
    "PHT": ["Asia/Manila"],
    "HKT": ["Asia/Hong_Kong"],
    "JST": ["Asia/Tokyo"],
    "KST": ["Asia/Seoul"],
    "AEST": ["Australia/Sydney", "Australia/Brisbane"],
    "AEDT": ["Australia/Sydney"],
    "ACST": ["Australia/Adelaide", "Australia/Darwin"],
    "AWST": ["Australia/Perth"],
    "NZST": ["Pacific/Auckland"],
    "NZDT": ["Pacific/Auckland"],
    "BRT": ["America/Sao_Paulo"],
    "ART": ["America/Argentina/Buenos_Aires"],
}
_OFFSET_RE = re.compile(r"^(?:UTC|GMT)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$", re.I)
_lower_zones: dict[str, str] | None = None


def _zones_lower() -> dict[str, str]:
    global _lower_zones
    if _lower_zones is None:
        _lower_zones = {z.lower(): z for z in available_timezones()}
    return _lower_zones


def resolve_tz(name: Any) -> tuple[tzinfo, str, list[str]]:
    """Resolve a timezone spec to (tzinfo, canonical name, assumptions)."""
    if name is None:
        raise ToolError("timezone is required (IANA name such as 'Asia/Kolkata')")
    if isinstance(name, tzinfo):
        return name, str(name), []
    s = str(name).strip()
    if not s:
        raise ToolError("timezone is empty")
    up = s.upper()
    if up in ("UTC", "Z", "GMT", "ETC/UTC", "ETC/GMT", "UTC+0", "UTC-0", "UTC0", "GMT+0"):
        return UTC, "UTC", [] if up in ("UTC", "Z") else [f"{s} read as UTC"]
    if s.lower() == "local":
        tz = datetime.now().astimezone().tzinfo
        return tz, "local", ["'local' is the machine running leftbrain, not the user's location"]
    m = _OFFSET_RE.match(s)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        hh, mm = int(m.group(2)), int(m.group(3) or 0)
        if hh > 14 or mm > 59:
            raise ToolError(f"invalid UTC offset {s!r}")
        delta = sign * timedelta(hours=hh, minutes=mm)
        label = f"UTC{'+' if sign > 0 else '-'}{hh:02d}:{mm:02d}"
        return timezone(delta, label), label, ["fixed offset; no daylight-saving rules applied"]
    zl = _zones_lower()
    key = s.replace(" ", "_").lower()
    if key in zl:
        return ZoneInfo(zl[key]), zl[key], []
    if up in _ABBREV:
        opts = _ABBREV[up]
        if len(opts) == 1:
            return ZoneInfo(opts[0]), opts[0], [f"{up} read as {opts[0]}"]
        raise Ambiguous(
            f"'{up}' is an ambiguous abbreviation; use an IANA zone name",
            field="timezone",
            options=opts,
        )
    from .geo_offline import lookup_zone  # lazy: avoids import cycle

    zones = lookup_zone(s)
    if len(zones) == 1:
        return ZoneInfo(zones[0]), zones[0], [f"'{s}' read as {zones[0]}"]
    if len(zones) > 1:
        raise Ambiguous(f"'{s}' spans several timezones", field="timezone", options=zones)
    raise ToolError(f"unknown timezone {s!r}; use an IANA name like 'Asia/Kolkata' or 'Europe/London'")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

_DAYFIRST = {
    "IN", "GB", "UK", "EU", "AU", "NZ", "DE", "FR", "IT", "ES", "PT", "NL", "BE", "BR", "ZA", "SG",
    "MY", "ID", "PK", "BD", "LK", "AE", "SA", "RU", "MX", "AR", "CH", "AT", "SE", "NO", "DK", "FI",
    "PL", "IE", "HK", "NG", "KE", "EG", "TR", "VN", "TH", "GR", "CZ", "IL", "CL", "CO", "PE",
}
_MONTHFIRST = {"US", "CA", "PH", "FM", "PW"}
_YEARFIRST = {"JP", "CN", "KR", "TW", "HU", "LT", "MN", "ISO"}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WD_ABBR = {w[:3]: i for i, w in enumerate(_WEEKDAYS)}
_NUMERIC_DATE = re.compile(r"^\s*(\d{1,2})([/.-])(\d{1,2})\2(\d{2}|\d{4})(\s+.*|T.*)?\s*$")
_UNIT_WORDS = {
    "second": "seconds", "sec": "seconds", "s": "seconds",
    "minute": "minutes", "min": "minutes",
    "hour": "hours", "hr": "hours", "h": "hours",
    "day": "days", "d": "days",
    "week": "weeks", "wk": "weeks", "w": "weeks",
    "fortnight": "fortnights",
    "month": "months", "mo": "months",
    "quarter": "quarters",
    "year": "years", "yr": "years", "y": "years",
}


def _locale_order(locale: str | None) -> tuple[bool | None, bool]:
    """-> (dayfirst or None if unknown, yearfirst)."""
    if not locale:
        return None, False
    code = re.split(r"[_-]", str(locale).strip())[-1].upper()
    if code in _DAYFIRST:
        return True, False
    if code in _MONTHFIRST:
        return False, False
    if code in _YEARFIRST:
        return None, True
    if code in ("DMY",):
        return True, False
    if code in ("MDY",):
        return False, False
    raise ToolError(f"unknown locale {locale!r}; use a country code such as 'IN', 'US', 'GB'")


def _unit(word: str) -> str:
    w = word.lower().rstrip("s") if word.lower() not in ("s",) else "s"
    if w in _UNIT_WORDS:
        return _UNIT_WORDS[w]
    if word.lower() in _UNIT_WORDS:
        return _UNIT_WORDS[word.lower()]
    raise ToolError(f"unknown time unit {word!r}")


def _delta(amount: float, unit: str) -> relativedelta:
    unit = _unit(unit)
    if unit == "fortnights":
        return relativedelta(weeks=2 * amount)
    if unit == "quarters":
        return relativedelta(months=3 * int(amount))
    if unit in ("months", "years"):
        if amount != int(amount):
            raise ToolError(f"{unit} must be a whole number")
        return relativedelta(**{unit: int(amount)})
    return relativedelta(**{unit: amount})


_TIME_SUFFIX = re.compile(
    r"^(?P<base>.*?)(?:\s+at)?\s+(?P<h>\d{1,2})(?::(?P<m>\d{2}))?(?::(?P<s>\d{2}))?\s*(?P<ampm>am|pm)?$",
    re.I,
)


def _relative(text: str, ref: datetime) -> tuple[datetime, bool, list[str]] | None:
    """Parse natural relative phrases against ``ref``. Returns (dt, date_only, assumptions)."""
    s = text.strip().lower()
    s = re.sub(r"\s+", " ", s)
    assumptions: list[str] = []
    time_part: tuple[int, int, int] | None = None
    tm = _TIME_SUFFIX.match(s)
    if tm and (tm.group("ampm") or tm.group("m")) and tm.group("base"):
        h = int(tm.group("h"))
        ampm = (tm.group("ampm") or "").lower()
        if ampm == "pm" and h < 12:
            h += 12
        if ampm == "am" and h == 12:
            h = 0
        time_part = (h, int(tm.group("m") or 0), int(tm.group("s") or 0))
        s = tm.group("base").strip()
    day0 = ref.replace(hour=0, minute=0, second=0, microsecond=0)

    result: datetime | None = None
    date_only = True
    if s in ("now",):
        result, date_only = ref, False
    elif s == "today":
        result = day0
    elif s == "tomorrow":
        result = day0 + timedelta(days=1)
    elif s == "yesterday":
        result = day0 - timedelta(days=1)
    elif s in ("day after tomorrow", "overmorrow"):
        result = day0 + timedelta(days=2)
    elif s == "day before yesterday":
        result = day0 - timedelta(days=2)
    else:
        m = re.fullmatch(r"(?:in|after)\s+(\d+(?:\.\d+)?|an?)\s+([a-z]+)", s)
        if m:
            n = 1.0 if m.group(1) in ("a", "an") else float(m.group(1))
            u = _unit(m.group(2))
            result = ref + _delta(n, u)
            date_only = u in ("days", "weeks", "months", "years", "fortnights", "quarters")
            if date_only:
                result = result.replace(hour=0, minute=0, second=0, microsecond=0)
        m = m or re.fullmatch(r"(\d+(?:\.\d+)?|an?)\s+([a-z]+)\s+(?:ago|before|earlier|back)", s)
        if result is None and m:
            n = 1.0 if m.group(1) in ("a", "an") else float(m.group(1))
            u = _unit(m.group(2))
            result = ref - _delta(n, u)
            date_only = u in ("days", "weeks", "months", "years", "fortnights", "quarters")
            if date_only:
                result = result.replace(hour=0, minute=0, second=0, microsecond=0)
        if result is None:
            m = re.fullmatch(r"(\d+(?:\.\d+)?|an?)\s+([a-z]+)\s+(?:from now|from today|later|hence)", s)
            if m:
                n = 1.0 if m.group(1) in ("a", "an") else float(m.group(1))
                u = _unit(m.group(2))
                result = ref + _delta(n, u)
                date_only = u in ("days", "weeks", "months", "years", "fortnights", "quarters")
                if date_only:
                    result = result.replace(hour=0, minute=0, second=0, microsecond=0)
        if result is None:
            m = re.fullmatch(r"(next|this|last|previous|coming)?\s*([a-z]+)", s)
            if m and (m.group(2) in _WEEKDAYS or m.group(2) in _WD_ABBR):
                wd = _WEEKDAYS.index(m.group(2)) if m.group(2) in _WEEKDAYS else _WD_ABBR[m.group(2)]
                q = m.group(1) or ""
                today_wd = day0.weekday()
                if q in ("next", "coming"):
                    ahead = (wd - today_wd) % 7 or 7
                    result = day0 + timedelta(days=ahead)
                    assumptions.append(f"'{q} {m.group(2)}' = first {m.group(2)} strictly after {day0.date()}")
                elif q in ("last", "previous"):
                    back = (today_wd - wd) % 7 or 7
                    result = day0 - timedelta(days=back)
                elif q == "this":
                    result = day0 + timedelta(days=wd - today_wd)
                    assumptions.append(f"'this {m.group(2)}' = the {m.group(2)} of the current Mon-Sun week")
                else:
                    ahead = (wd - today_wd) % 7
                    result = day0 + timedelta(days=ahead)
                    assumptions.append(f"'{m.group(2)}' = first {m.group(2)} on or after {day0.date()}")
        if result is None:
            m = re.fullmatch(r"(next|last|previous|this)\s+(week|month|year|quarter)", s)
            if m:
                n = {"next": 1, "last": -1, "previous": -1, "this": 0}[m.group(1)]
                result = day0 + _delta(n, m.group(2))
                assumptions.append(f"'{s}' = same day shifted by {n} {m.group(2)}")
        if result is None:
            m = re.fullmatch(
                r"(start|beginning|first day|end|last day)\s+of\s+(?:(this|next|last|the)\s+)?(week|month|year|quarter)",
                s,
            )
            if m:
                which = m.group(1)
                shift = {"next": 1, "last": -1, None: 0, "this": 0, "the": 0}[m.group(2)]
                period = m.group(3)
                base = day0 + _delta(shift, period)
                if period == "week":
                    start = base - timedelta(days=base.weekday())
                    end = start + timedelta(days=6)
                    assumptions.append("week = Monday..Sunday")
                elif period == "month":
                    start = base.replace(day=1)
                    end = start + relativedelta(months=1, days=-1)
                elif period == "year":
                    start = base.replace(month=1, day=1)
                    end = base.replace(month=12, day=31)
                else:
                    qm = 3 * ((base.month - 1) // 3) + 1
                    start = base.replace(month=qm, day=1)
                    end = start + relativedelta(months=3, days=-1)
                result = start if which in ("start", "beginning", "first day") else end
        if result is None:
            m = re.fullmatch(r"([a-z]+)\s+(next|last)\s+week", s)
            if m and m.group(1) in _WEEKDAYS:
                wd = _WEEKDAYS.index(m.group(1))
                n = 1 if m.group(2) == "next" else -1
                monday = day0 - timedelta(days=day0.weekday()) + timedelta(weeks=n)
                result = monday + timedelta(days=wd)
    if result is None:
        return None
    if time_part:
        result = result.replace(hour=time_part[0], minute=time_part[1], second=time_part[2], microsecond=0)
        date_only = False
    return result, date_only, assumptions


def parse_dt(
    value: Any,
    *,
    tz: Any = None,
    locale: str | None = None,
    ref: datetime | None = None,
    field: str = "date",
) -> tuple[datetime, bool, list[str]]:
    """Parse ``value`` into (datetime, date_only, assumptions).

    Accepts ISO 8601, unix timestamps, common written forms and relative
    phrases. Refuses ambiguous numeric dates unless ``locale`` is given.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ToolError(f"'{field}' is required")
    assumptions: list[str] = []
    tzobj: tzinfo | None = None
    tzname = None
    if tz is not None:
        tzobj, tzname, a = resolve_tz(tz)
        assumptions += a

    if isinstance(value, datetime):
        dt, date_only = value, False
    elif isinstance(value, date):
        dt, date_only = datetime(value.year, value.month, value.day), True
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
        if v > 1e12:
            v /= 1000.0
            assumptions.append("timestamp read as milliseconds")
        dt = datetime.fromtimestamp(v, tz=UTC)
        date_only = False
        assumptions.append("unix timestamp read as UTC")
    else:
        s = str(value).strip()
        now = ref or datetime.now(tzobj or UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=tzobj or UTC)
        rel = _relative(s, now)
        if rel:
            dt, date_only, a = rel
            assumptions += a
            if tzobj and dt.tzinfo is None:
                dt = dt.replace(tzinfo=tzobj)
            return dt, date_only, assumptions
        m = _NUMERIC_DATE.match(s)
        dayfirst, yearfirst = _locale_order(locale)
        if m:
            a_, b_ = int(m.group(1)), int(m.group(3))
            if a_ <= 12 and b_ <= 12 and a_ != b_ and dayfirst is None:
                y = m.group(4)
                y = int(y) if len(y) == 4 else 2000 + int(y)
                raise Ambiguous(
                    f"'{s}' could be day/month or month/day; pass locale (e.g. 'IN' or 'US')",
                    field="locale",
                    options=[
                        {"reading": "DD/MM/YYYY", "locale": "IN", "iso": f"{y:04d}-{b_:02d}-{a_:02d}"},
                        {"reading": "MM/DD/YYYY", "locale": "US", "iso": f"{y:04d}-{a_:02d}-{b_:02d}"},
                    ],
                )
            if dayfirst is None:
                dayfirst = a_ > 12 or (a_ == b_)
                if a_ > 12:
                    assumptions.append("read as DD/MM (first field > 12)")
                elif b_ > 12:
                    dayfirst = False
                    assumptions.append("read as MM/DD (second field > 12)")
            else:
                assumptions.append(f"read as {'DD/MM' if dayfirst else 'MM/DD'} per locale {locale}")
        date_only = not re.search(r"\d:\d|T\d|\d\s*(am|pm)\b|noon|midnight|\d{2}\d{2}Z", s, re.I) and not (
            re.fullmatch(r"\d{8}T?\d{0,6}", s)
        )
        try:
            iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
            dt = datetime.fromisoformat(iso)
            if len(s) == 10:
                date_only = True
        except ValueError:
            try:
                dt = duparser.parse(s, dayfirst=bool(dayfirst), yearfirst=yearfirst, fuzzy=False)
            except (ValueError, OverflowError) as e:
                raise ToolError(f"could not parse {field} {s!r}: {e}") from None
            if re.search(r"\b(noon|midnight)\b", s, re.I):
                date_only = False
    if tzobj is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=tzobj)
    elif tzobj is not None and dt.tzinfo is not None and tzname:
        dt = dt.astimezone(tzobj)
    return dt, date_only, assumptions


def _info(dt: datetime, date_only: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "iso": dt.date().isoformat() if date_only else dt.isoformat(),
        "date": dt.date().isoformat(),
        "weekday": dt.strftime("%A"),
    }
    if not date_only:
        out["time"] = dt.strftime("%H:%M:%S")
        if dt.tzinfo is not None:
            off = dt.utcoffset() or timedelta(0)
            sign = "+" if off >= timedelta(0) else "-"
            off = abs(off)
            out["utc_offset"] = f"{sign}{off.seconds // 3600:02d}:{(off.seconds // 60) % 60:02d}"
            out["tz"] = getattr(dt.tzinfo, "key", None) or str(dt.tzinfo)
            out["unix"] = int(dt.timestamp())
            dst = dt.dst()
            out["is_dst"] = bool(dst) if dst is not None else None
        else:
            out["tz"] = None
    return out


# --------------------------------------------------------------------------- #
# Business days / holidays
# --------------------------------------------------------------------------- #

_WD_INDEX = {**{w: i for i, w in enumerate(_WEEKDAYS)}, **_WD_ABBR}


def _weekend(spec: Any) -> set[int]:
    if spec is None:
        return {5, 6}
    if isinstance(spec, str):
        spec = re.split(r"[,\s/]+", spec.strip())
    out = set()
    for w in spec:
        if isinstance(w, int):
            out.add(w % 7)
        else:
            k = str(w).strip().lower()
            if k not in _WD_INDEX:
                raise ToolError(f"unknown weekday {w!r}")
            out.add(_WD_INDEX[k])
    return out


def _holiday_set(region: str | None, subdiv: str | None, years: set[int], extra: Any) -> dict[date, str]:
    out: dict[date, str] = {}
    if region:
        from .holidays_ import holiday_map

        out.update(holiday_map(region, years, subdiv))
    for h in extra or []:
        if isinstance(h, dict):
            d, _, _ = parse_dt(h.get("date"), field="extra_holidays.date")
            out[d.date()] = str(h.get("name", "holiday"))
        else:
            d, _, _ = parse_dt(h, field="extra_holidays")
            out[d.date()] = "holiday"
    return out


def _is_business(d: date, weekend: set[int], hols: dict[date, str]) -> bool:
    return d.weekday() not in weekend and d not in hols


# --------------------------------------------------------------------------- #
# Cron
# --------------------------------------------------------------------------- #

_CRON_ALIASES = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}
_MONTH_NAMES = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_DOW_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def _cron_field(field: str, lo: int, hi: int, names: dict[str, int] | None = None) -> set[int]:
    out: set[int] = set()
    for part in field.split(","):
        part = part.strip().lower()
        step = 1
        if "/" in part:
            part, st = part.split("/", 1)
            step = int(st)
            if step <= 0:
                raise ToolError("cron step must be positive")
        if part in ("*", "?"):
            rng = range(lo, hi + 1)
        else:
            if "-" in part:
                a, b = part.split("-", 1)
            else:
                a = b = part
            a = names.get(a, a) if names else a
            b = names.get(b, b) if names else b
            try:
                a, b = int(a), int(b)
            except ValueError:
                raise ToolError(f"bad cron field {field!r}") from None
            if a < lo or b > hi or a > b:
                raise ToolError(f"cron value out of range in {field!r}")
            rng = range(a, b + 1)
        out.update(rng[::step])
    return out


def _cron_next(expr: str, start: datetime, n: int) -> list[datetime]:
    expr = _CRON_ALIASES.get(expr.strip().lower(), expr.strip())
    parts = expr.split()
    if len(parts) != 5:
        raise ToolError("cron expression must have 5 fields: minute hour day month weekday")
    minutes = _cron_field(parts[0], 0, 59)
    hours = _cron_field(parts[1], 0, 23)
    dom = _cron_field(parts[2], 1, 31)
    months = _cron_field(parts[3], 1, 12, _MONTH_NAMES)
    dow_raw = parts[4].replace("7", "0")
    dow = _cron_field(dow_raw, 0, 6, _DOW_NAMES)
    dom_any, dow_any = parts[2] in ("*", "?"), parts[4] in ("*", "?")
    out: list[datetime] = []
    cur = (start + timedelta(minutes=1)).replace(second=0, microsecond=0)
    day = cur.date()
    limit = day + timedelta(days=366 * 5)
    while day <= limit and len(out) < n:
        if day.month in months:
            cron_dow = (day.weekday() + 1) % 7  # cron: 0 = Sunday
            dom_ok, dow_ok = day.day in dom, cron_dow in dow
            day_ok = (dom_ok and dow_ok) if (not dom_any and not dow_any and False) else None
            if dom_any and dow_any:
                day_ok = True
            elif dom_any:
                day_ok = dow_ok
            elif dow_any:
                day_ok = dom_ok
            else:
                day_ok = dom_ok or dow_ok
            if day_ok:
                for h in sorted(hours):
                    for mi in sorted(minutes):
                        cand = datetime(day.year, day.month, day.day, h, mi, tzinfo=cur.tzinfo)
                        if cand >= cur:
                            out.append(cand)
                            if len(out) >= n:
                                return out
        day += timedelta(days=1)
    return out


# --------------------------------------------------------------------------- #
# Recurrence phrases -> RRULE
# --------------------------------------------------------------------------- #

_ORD = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "last": -1}


def _phrase_to_rrule(phrase: str) -> str | None:
    s = phrase.strip().lower()
    s = re.sub(r"\s+", " ", s)
    if s.upper().startswith("RRULE:") or s.upper().startswith("FREQ="):
        return phrase
    m = re.fullmatch(r"every (\d+ )?(day|week|month|year)s?", s)
    if m:
        n = int((m.group(1) or "1").strip())
        return f"FREQ={ {'day': 'DAILY', 'week': 'WEEKLY', 'month': 'MONTHLY', 'year': 'YEARLY'}[m.group(2)] };INTERVAL={n}"
    if s in ("every weekday", "weekdays", "every working day"):
        return "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    if s in ("every weekend", "weekends"):
        return "FREQ=WEEKLY;BYDAY=SA,SU"
    m = re.fullmatch(r"every (?:(\d+|other) weeks? on )?((?:[a-z]+(?:,| and |, and | )?)+)", s)
    if m:
        days = [d for d in re.split(r",|\band\b|\s", m.group(2)) if d]
        if all(d[:3] in _WD_ABBR for d in days):
            interval = 2 if m.group(1) == "other" else int(m.group(1) or 1)
            byday = ",".join(d[:2].upper() for d in days)
            return f"FREQ=WEEKLY;INTERVAL={interval};BYDAY={byday}"
    m = re.fullmatch(r"every (first|second|third|fourth|last|1st|2nd|3rd|4th) ([a-z]+)(?: of (?:the |every )?month)?", s)
    if m and m.group(2)[:3] in _WD_ABBR:
        return f"FREQ=MONTHLY;BYDAY={m.group(2)[:2].upper()};BYSETPOS={_ORD[m.group(1)]}"
    m = re.fullmatch(r"(?:on the |every )?(\d{1,2})(?:st|nd|rd|th)? (?:of )?(?:every |each )?month", s)
    if m:
        return f"FREQ=MONTHLY;BYMONTHDAY={int(m.group(1))}"
    if s in ("every last day of the month", "last day of every month", "month end", "every month end"):
        return "FREQ=MONTHLY;BYMONTHDAY=-1"
    return None


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #


def _mode_now(p: dict[str, Any]) -> dict[str, Any]:
    tz, name, a = resolve_tz(p.get("tz") or "UTC")
    now = datetime.now(tz)
    out = _info(now)
    out["iso_week"] = now.isocalendar()[1]
    out["day_of_year"] = now.timetuple().tm_yday
    out["utc"] = now.astimezone(UTC).isoformat()
    return ok(out, assumptions=a or ([] if p.get("tz") else ["no tz given; reported in UTC"]))


def _mode_convert_tz(p: dict[str, Any]) -> dict[str, Any]:
    value = p.get("value") or p.get("datetime") or p.get("date")
    src = p.get("from_tz") or p.get("from")
    dst = p.get("to_tz") or p.get("to")
    if dst is None:
        raise ToolError("to_tz is required")
    ref = None
    assumptions: list[str] = []
    if value in (None, "now"):
        dt = datetime.now(UTC)
        date_only = False
        assumptions.append("no value given; converted the current instant")
    else:
        dt, date_only, a = parse_dt(value, tz=src, locale=p.get("locale"), ref=ref, field="value")
        assumptions += a
    if dt.tzinfo is None:
        raise ToolError("value has no timezone; pass from_tz")
    if date_only:
        raise ToolError("value has no time component; a bare date cannot be converted between zones")
    targets = dst if isinstance(dst, list) else [dst]
    results = []
    for t in targets:
        tz, name, a = resolve_tz(t)
        assumptions += [x for x in a if x not in assumptions]
        conv = dt.astimezone(tz)
        entry = _info(conv)
        entry["tz"] = name
        entry["day_shift"] = (conv.date() - dt.date()).days
        results.append(entry)
    src_info = _info(dt)
    out = {"source": src_info, "converted": results[0] if len(results) == 1 else results}
    return ok(out, assumptions=assumptions)


def _mode_parse(p: dict[str, Any]) -> dict[str, Any]:
    value = p.get("value") or p.get("date") or p.get("text")
    ref = None
    if p.get("ref_date") is not None:
        ref, _, _ = parse_dt(p["ref_date"], tz=p.get("tz"), locale=p.get("locale"), field="ref_date")
    dt, date_only, a = parse_dt(value, tz=p.get("tz"), locale=p.get("locale"), ref=ref, field="value")
    out = _info(dt, date_only)
    out["date_only"] = date_only
    out["components"] = {
        "year": dt.year, "month": dt.month, "day": dt.day,
        **({} if date_only else {"hour": dt.hour, "minute": dt.minute, "second": dt.second}),
    }
    out["iso_week"] = dt.isocalendar()[1]
    out["day_of_year"] = dt.timetuple().tm_yday
    return ok(out, assumptions=a)


def _mode_add(p: dict[str, Any]) -> dict[str, Any]:
    value = p.get("value") or p.get("date") or "now"
    amount = p.get("amount")
    unit = p.get("unit")
    if amount is None or unit is None:
        raise ToolError("add needs 'amount' and 'unit'")
    amount = float(amount)
    dt, date_only, a = parse_dt(value, tz=p.get("tz"), locale=p.get("locale"), field="value")
    warnings: list[str] = []
    steps: list[str] = []
    u = unit.lower().replace(" ", "_")
    if u.rstrip("s") in ("business_day", "working_day", "workday"):
        n = int(amount)
        weekend = _weekend(p.get("weekend"))
        years = {dt.year - 1, dt.year, dt.year + 1, dt.year + int(n // 200) + 1}
        hols = _holiday_set(p.get("region"), p.get("subdiv"), years, p.get("extra_holidays"))
        d = dt
        step = 1 if n >= 0 else -1
        skipped: list[str] = []
        remaining = abs(n)
        while remaining:
            d = d + timedelta(days=step)
            if _is_business(d.date(), weekend, hols):
                remaining -= 1
            elif d.date() in hols:
                skipped.append(f"{d.date()} {hols[d.date()]}")
        out = _info(d, date_only)
        out["holidays_skipped"] = skipped
        if not p.get("region"):
            a.append("no region given; only weekends skipped (pass region='IN' etc. for public holidays)")
        return ok(out, assumptions=a, steps=[f"{n:+d} business days from {dt.date()}"])
    res = dt + _delta(amount, unit)
    if _unit(unit) in ("months", "years", "quarters") and res.day != dt.day:
        warnings.append(f"day clamped to month end ({dt.day} -> {res.day})")
    if _unit(unit) in ("hours", "minutes", "seconds"):
        date_only = False
    steps.append(f"{dt.isoformat()} + {amount:g} {unit}")
    if dt.tzinfo is not None and getattr(dt.tzinfo, "key", None) and _unit(unit) in ("hours", "minutes", "seconds"):
        # wall-clock arithmetic across DST: recompute via UTC for correctness
        res = (dt.astimezone(UTC) + _delta(amount, unit)).astimezone(dt.tzinfo)
        steps.append("elapsed-time arithmetic done in UTC to respect DST")
    out = _info(res, date_only)
    return ok(out, assumptions=a, warnings=warnings, steps=steps)


def _mode_diff(p: dict[str, Any]) -> dict[str, Any]:
    a_raw = p.get("from") or p.get("start") or p.get("a")
    b_raw = p.get("to") or p.get("end") or p.get("b") or "now"
    unit = (p.get("unit") or "auto").lower()
    tz = p.get("tz")
    d1, do1, a1 = parse_dt(a_raw, tz=tz, locale=p.get("locale"), field="from")
    d2, do2, a2 = parse_dt(b_raw, tz=tz, locale=p.get("locale"), field="to")
    assumptions = a1 + [x for x in a2 if x not in a1]
    if (d1.tzinfo is None) != (d2.tzinfo is None):
        if d1.tzinfo is None:
            d1 = d1.replace(tzinfo=d2.tzinfo)
        else:
            d2 = d2.replace(tzinfo=d1.tzinfo)
        assumptions.append("one side had no timezone; assumed the other's")
    delta = d2 - d1
    sign = -1 if delta < timedelta(0) else 1
    lo, hi = (d1, d2) if sign > 0 else (d2, d1)
    rd = relativedelta(hi, lo)
    total_sec = abs(delta.total_seconds())
    out: dict[str, Any] = {
        "from": _info(d1, do1),
        "to": _info(d2, do2),
        "sign": sign,
        "direction": "to is after from" if sign > 0 else ("same instant" if delta == timedelta(0) else "to is before from"),
        "calendar": {
            "years": rd.years, "months": rd.months, "days": rd.days,
            "hours": rd.hours, "minutes": rd.minutes, "seconds": rd.seconds,
        },
        "total": {
            "seconds": total_sec,
            "minutes": total_sec / 60,
            "hours": total_sec / 3600,
            "days": total_sec / 86400,
            "weeks": total_sec / 604800,
            "months_approx": rd.years * 12 + rd.months + rd.days / 30.4375,
            "years_approx": rd.years + rd.months / 12 + rd.days / 365.25,
        },
        "whole_months": rd.years * 12 + rd.months,
        "human": _human_delta(rd),
    }
    if unit in ("business_days", "working_days", "workdays"):
        weekend = _weekend(p.get("weekend"))
        years = set(range(lo.year, hi.year + 1))
        hols = _holiday_set(p.get("region"), p.get("subdiv"), years, p.get("extra_holidays"))
        n = 0
        d = lo.date()
        while d < hi.date():
            if _is_business(d, weekend, hols):
                n += 1
            d += timedelta(days=1)
        out["value"] = sign * n
        out["unit"] = "business_days"
        assumptions.append("business days counted from 'from' (inclusive) up to 'to' (exclusive)")
        if not p.get("region"):
            assumptions.append("no region given; only weekends excluded")
    elif unit != "auto":
        key = _unit(unit)
        if key in ("months", "years"):
            out["value"] = sign * (out["whole_months"] if key == "months" else rd.years)
            out["value_fractional"] = sign * out["total"][key + "_approx"]
            assumptions.append(f"'{key}' reported as whole calendar {key}; fractional estimate uses mean month/year length")
        elif key == "fortnights":
            out["value"] = sign * total_sec / (14 * 86400)
        elif key == "quarters":
            out["value"] = sign * (out["whole_months"] // 3)
        else:
            out["value"] = sign * out["total"][key]
        out["unit"] = key
    return ok(out, assumptions=assumptions)


def _human_delta(rd: relativedelta) -> str:
    parts = []
    for k in ("years", "months", "days", "hours", "minutes", "seconds"):
        v = getattr(rd, k)
        if v:
            parts.append(f"{v} {k if v != 1 else k[:-1]}")
    return ", ".join(parts) or "0 seconds"


def _mode_weekday(p: dict[str, Any]) -> dict[str, Any]:
    value = p.get("value") or p.get("date") or "today"
    dt, date_only, a = parse_dt(value, tz=p.get("tz"), locale=p.get("locale"), field="value")
    iso = dt.isocalendar()
    out = {
        "date": dt.date().isoformat(),
        "weekday": dt.strftime("%A"),
        "weekday_iso": dt.isoweekday(),
        "weekday_index_mon0": dt.weekday(),
        "is_weekend": dt.weekday() >= 5,
        "iso_week": iso[1],
        "iso_year": iso[0],
        "day_of_year": dt.timetuple().tm_yday,
        "days_in_month": calendar.monthrange(dt.year, dt.month)[1],
        "is_leap_year": calendar.isleap(dt.year),
        "quarter": (dt.month - 1) // 3 + 1,
        "month_name": dt.strftime("%B"),
        "week_of_month": (dt.day + date(dt.year, dt.month, 1).weekday() - 1) // 7 + 1,
    }
    return ok(out, assumptions=a + ["weekend = Saturday/Sunday"])


def _mode_nth_weekday(p: dict[str, Any]) -> dict[str, Any]:
    year, month = p.get("year"), p.get("month")
    if year is None or month is None:
        base = p.get("value") or p.get("date") or "today"
        dt, _, _ = parse_dt(base, tz=p.get("tz"), field="value")
        year, month = year or dt.year, month or dt.month
    year, month = int(year), int(month) if not isinstance(month, str) or month.isdigit() else month
    if isinstance(month, str):
        month = _MONTH_NAMES.get(month.lower()[:3])
        if not month:
            raise ToolError("unknown month name")
    wd_raw = p.get("weekday")
    if wd_raw is None:
        raise ToolError("nth_weekday needs 'weekday'")
    wd = _WD_INDEX.get(str(wd_raw).lower()) if not isinstance(wd_raw, int) else wd_raw % 7
    if wd is None:
        raise ToolError(f"unknown weekday {wd_raw!r}")
    n = p.get("n", 1)
    n = _ORD.get(str(n).lower(), n)
    n = int(n)
    if n == 0 or n > 5 or n < -5:
        raise ToolError("n must be 1..5 or -1..-5 (negative counts from the end)")
    first = date(year, month, 1)
    days_in = calendar.monthrange(year, month)[1]
    if n > 0:
        offset = (wd - first.weekday()) % 7
        d = first + timedelta(days=offset + 7 * (n - 1))
    else:
        last = date(year, month, days_in)
        offset = (last.weekday() - wd) % 7
        d = last - timedelta(days=offset + 7 * (-n - 1))
    if d.month != month:
        raise ToolError(f"there is no {n}th {_WEEKDAYS[wd]} in {year}-{month:02d}")
    return ok({"date": d.isoformat(), "weekday": d.strftime("%A"), "n": n, "year": year, "month": month})


def _mode_business_days(p: dict[str, Any]) -> dict[str, Any]:
    a_raw, b_raw = p.get("from") or p.get("start"), p.get("to") or p.get("end")
    if a_raw is None or b_raw is None:
        raise ToolError("business_days needs 'from' and 'to'")
    d1, _, a1 = parse_dt(a_raw, tz=p.get("tz"), locale=p.get("locale"), field="from")
    d2, _, a2 = parse_dt(b_raw, tz=p.get("tz"), locale=p.get("locale"), field="to")
    assumptions = a1 + [x for x in a2 if x not in a1]
    lo, hi = sorted([d1.date(), d2.date()])
    include_start = p.get("include_start", True)
    include_end = p.get("include_end", True)
    weekend = _weekend(p.get("weekend"))
    hols = _holiday_set(p.get("region"), p.get("subdiv"), set(range(lo.year, hi.year + 1)), p.get("extra_holidays"))
    d = lo if include_start else lo + timedelta(days=1)
    end = hi if include_end else hi - timedelta(days=1)
    n = 0
    weekend_days = 0
    skipped: list[dict[str, str]] = []
    dates: list[str] = []
    while d <= end:
        if d.weekday() in weekend:
            weekend_days += 1
        elif d in hols:
            skipped.append({"date": d.isoformat(), "name": hols[d]})
        else:
            n += 1
            if len(dates) < 200:
                dates.append(d.isoformat())
        d += timedelta(days=1)
    calendar_days = (end - lo).days + 1 if include_start else (end - lo).days
    out = {
        "business_days": n,
        "calendar_days": max(calendar_days, 0),
        "weekend_days": weekend_days,
        "holidays_skipped": skipped,
        "from": lo.isoformat(),
        "to": hi.isoformat(),
    }
    if len(dates) <= 200 and n <= 200:
        out["dates"] = dates
    assumptions.append(
        f"range is {'inclusive' if include_start else 'exclusive'} of start and "
        f"{'inclusive' if include_end else 'exclusive'} of end (Excel NETWORKDAYS = both inclusive)"
    )
    if not p.get("region"):
        assumptions.append("no region given; only weekends excluded (pass region='IN' etc. for public holidays)")
    return ok(out, assumptions=assumptions)


def _range(r: Any, tz: Any, locale: Any, name: str) -> tuple[datetime, datetime, list[str]]:
    if not isinstance(r, dict) or "start" not in r or "end" not in r:
        raise ToolError(f"{name} must be {{'start': ..., 'end': ...}}")
    s, _, a1 = parse_dt(r["start"], tz=tz, locale=locale, field=f"{name}.start")
    e, _, a2 = parse_dt(r["end"], tz=tz, locale=locale, field=f"{name}.end")
    if (s.tzinfo is None) != (e.tzinfo is None):
        raise ToolError(f"{name}: start and end must both have (or both lack) a timezone")
    if e < s:
        raise ToolError(f"{name}: end is before start")
    return s, e, a1 + a2


def _mode_overlap(p: dict[str, Any]) -> dict[str, Any]:
    a_s, a_e, aa = _range(p.get("a"), p.get("tz"), p.get("locale"), "a")
    b_s, b_e, ab = _range(p.get("b"), p.get("tz"), p.get("locale"), "b")
    if (a_s.tzinfo is None) != (b_s.tzinfo is None):
        raise ToolError("a and b must both have (or both lack) timezones")
    start, end = max(a_s, b_s), min(a_e, b_e)
    overlaps = start < end
    touches = start == end
    if a_s <= b_s and a_e >= b_e:
        relation = "a contains b"
    elif b_s <= a_s and b_e >= a_e:
        relation = "a within b"
    elif overlaps:
        relation = "a overlaps b" if a_s < b_s else "b overlaps a"
    elif touches:
        relation = "a meets b" if a_e == b_s else "b meets a"
    else:
        relation = "a before b" if a_e < b_s else "a after b"
    out: dict[str, Any] = {"overlaps": overlaps, "relation": relation}
    if overlaps:
        secs = (end - start).total_seconds()
        out["overlap"] = {"start": start.isoformat(), "end": end.isoformat(), "seconds": secs, "hours": secs / 3600, "days": secs / 86400}
    else:
        gap = (max(a_s, b_s) - min(a_e, b_e)).total_seconds()
        out["gap"] = {"seconds": gap, "hours": gap / 3600, "days": gap / 86400}
    return ok(out, assumptions=aa + ab + ["intervals are half-open: [start, end)"])


def _mode_duration_sum(p: dict[str, Any]) -> dict[str, Any]:
    ranges = p.get("ranges") or p.get("items")
    if not isinstance(ranges, list) or not ranges:
        raise ToolError("duration_sum needs 'ranges': [{start, end}, ...]")
    parsed = []
    assumptions: list[str] = []
    for i, r in enumerate(ranges):
        s, e, a = _range(r, p.get("tz"), p.get("locale"), f"ranges[{i}]")
        parsed.append((s, e, r))
        assumptions += [x for x in a if x not in assumptions]
    total = sum((e - s).total_seconds() for s, e, _ in parsed)
    per = []
    for s, e, r in parsed:
        secs = (e - s).total_seconds()
        per.append({"start": s.isoformat(), "end": e.isoformat(), "seconds": secs, "hours": secs / 3600, "hhmm": _hhmm(secs), **({"label": r["label"]} if isinstance(r, dict) and r.get("label") else {})})
    warnings = []
    srt = sorted(parsed, key=lambda t: t[0])
    for (s1, e1, _), (s2, e2, _) in zip(srt, srt[1:], strict=False):
        if s2 < e1:
            warnings.append(f"ranges overlap: {s1.isoformat()}–{e1.isoformat()} and {s2.isoformat()}–{e2.isoformat()} (double counted)")
    out = {
        "total": {"seconds": total, "minutes": total / 60, "hours": total / 3600, "days": total / 86400, "hhmm": _hhmm(total), "human": _human_delta(relativedelta(seconds=int(total)))},
        "count": len(per),
        "average_hours": total / 3600 / len(per),
        "longest_hours": max(x["hours"] for x in per),
        "shortest_hours": min(x["hours"] for x in per),
        "items": per,
    }
    return ok(out, assumptions=assumptions, warnings=warnings)


def _hhmm(secs: float) -> str:
    secs = int(round(secs))
    return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}"


def _mode_recurrence(p: dict[str, Any]) -> dict[str, Any]:
    rule = p.get("rule") or p.get("rrule")
    if not rule:
        raise ToolError("recurrence needs 'rule' (RRULE string or phrase like 'every 2nd tuesday')")
    rr = _phrase_to_rrule(rule)
    if rr is None:
        raise ToolError(f"could not understand recurrence {rule!r}; pass an RRULE like FREQ=WEEKLY;BYDAY=MO")
    start_raw = p.get("start") or p.get("dtstart") or "today"
    start, _, a = parse_dt(start_raw, tz=p.get("tz"), locale=p.get("locale"), field="start")
    count = p.get("count")
    until = p.get("until")
    limit = int(p.get("limit", 100))
    if limit > 1000:
        raise ToolError("limit must be <= 1000")
    rr_text = rr if rr.upper().startswith("RRULE:") else "RRULE:" + rr
    if until:
        u, _, _ = parse_dt(until, tz=p.get("tz"), locale=p.get("locale"), field="until")
        if (u.tzinfo is None) != (start.tzinfo is None):
            u = u.replace(tzinfo=start.tzinfo)
        rr_text += ";UNTIL=" + (u.strftime("%Y%m%dT%H%M%SZ") if u.tzinfo else u.strftime("%Y%m%dT%H%M%S"))
        if u.tzinfo:
            u_utc = u.astimezone(UTC)
            rr_text = rr_text.replace(u.strftime("%Y%m%dT%H%M%SZ"), u_utc.strftime("%Y%m%dT%H%M%SZ"))
    elif count:
        rr_text += f";COUNT={int(count)}"
    try:
        rule_obj = rrulestr(rr_text, dtstart=start)
    except (ValueError, KeyError) as e:
        raise ToolError(f"invalid RRULE {rr!r}: {e}") from None
    dates = []
    for i, d in enumerate(rule_obj):
        if i >= limit:
            break
        dates.append(_info(d, False)["iso"] if not p.get("dates_only", True) else d.date().isoformat())
    warnings = [] if (until or count or len(dates) < limit) else [f"truncated to {limit} occurrences; pass count/until"]
    return ok({"rrule": rr_text, "occurrences": dates, "count": len(dates)}, assumptions=a, warnings=warnings)


def _mode_cron_next(p: dict[str, Any]) -> dict[str, Any]:
    expr = p.get("expr") or p.get("cron")
    if not expr:
        raise ToolError("cron_next needs 'expr' such as '0 9 * * 1-5'")
    tz, name, a = resolve_tz(p.get("tz") or "UTC")
    start_raw = p.get("from") or p.get("start")
    if start_raw:
        start, _, a2 = parse_dt(start_raw, tz=tz, field="from")
        a += a2
    else:
        start = datetime.now(tz)
        a.append("no 'from' given; started from now")
    n = int(p.get("n", 5))
    if n < 1 or n > 500:
        raise ToolError("n must be 1..500")
    res = _cron_next(str(expr), start, n)
    if not res:
        raise ToolError("no matching times within the next 5 years")
    return ok({"expr": expr, "tz": name, "next": [_info(d) for d in res]}, assumptions=a + ["standard 5-field cron; day-of-month OR day-of-week when both are set"])


def _mode_age(p: dict[str, Any]) -> dict[str, Any]:
    dob_raw = p.get("dob") or p.get("birth_date") or p.get("from") or p.get("value")
    if not dob_raw:
        raise ToolError("age needs 'dob'")
    dob, _, a = parse_dt(dob_raw, locale=p.get("locale"), field="dob")
    on_raw = p.get("on") or p.get("to") or "today"
    on, _, a2 = parse_dt(on_raw, locale=p.get("locale"), field="on")
    d0, d1 = dob.date(), on.date()
    if d1 < d0:
        raise ToolError("'on' is before the date of birth")
    rd = relativedelta(d1, d0)
    try:
        nb = d0.replace(year=d1.year)
    except ValueError:  # Feb 29
        nb = d0.replace(year=d1.year, day=28)
        a.append("Feb-29 birthday celebrated on Feb-28 in non-leap years")
    if nb < d1:
        try:
            nb = d0.replace(year=d1.year + 1)
        except ValueError:
            nb = d0.replace(year=d1.year + 1, day=28)
    out = {
        "years": rd.years, "months": rd.months, "days": rd.days,
        "total_days": (d1 - d0).days,
        "total_months": rd.years * 12 + rd.months,
        "human": f"{rd.years} years, {rd.months} months, {rd.days} days",
        "next_birthday": nb.isoformat(),
        "days_to_next_birthday": (nb - d1).days,
        "turning": rd.years + 1 if nb != d1 else rd.years,
        "on": d1.isoformat(),
    }
    return ok(out, assumptions=a + a2)


_FY_START = {"IN": 4, "GB": 4, "UK": 4, "JP": 4, "CA": 4, "NZ": 4, "AU": 7, "EG": 7, "PK": 7, "BD": 7, "US": 10, "SG": 4, "HK": 4, "ZA": 3, "AE": 1, "DE": 1, "FR": 1, "CN": 1, "BR": 1}
_FY_LABEL = {"US": "end", "AU": "end", "NZ": "end", "EG": "end", "PK": "end", "BD": "end"}


def _mode_fiscal(p: dict[str, Any]) -> dict[str, Any]:
    value = p.get("value") or p.get("date") or "today"
    dt, _, a = parse_dt(value, tz=p.get("tz"), locale=p.get("locale"), field="value")
    region = (p.get("region") or "").upper()
    start_month = p.get("fy_start_month")
    if start_month is None:
        if region in _FY_START:
            start_month = _FY_START[region]
            a.append(f"fiscal year for {region} starts in month {start_month}" + (" (US federal; many US companies use January)" if region == "US" else ""))
        else:
            start_month = 1
            a.append("no region/fy_start_month given; calendar year assumed")
    start_month = int(start_month)
    if not 1 <= start_month <= 12:
        raise ToolError("fy_start_month must be 1..12")
    d = dt.date()
    fy_start_year = d.year if d.month >= start_month else d.year - 1
    if start_month == 1:
        fy_start_year = d.year
    fy_start = date(fy_start_year, start_month, 1)
    fy_end = fy_start + relativedelta(years=1, days=-1)
    q = ((d.month - start_month) % 12) // 3 + 1
    q_start = fy_start + relativedelta(months=3 * (q - 1))
    q_end = q_start + relativedelta(months=3, days=-1)
    if start_month == 1:
        label = f"FY{fy_start_year}"
    elif _FY_LABEL.get(region) == "end":
        label = f"FY{fy_end.year}"
    else:
        label = f"FY{fy_start_year}-{str(fy_end.year)[2:]}"
    out = {
        "fiscal_year": label,
        "fy_start": fy_start.isoformat(),
        "fy_end": fy_end.isoformat(),
        "quarter": f"Q{q}",
        "quarter_start": q_start.isoformat(),
        "quarter_end": q_end.isoformat(),
        "day_of_fy": (d - fy_start).days + 1,
        "days_left_in_fy": (fy_end - d).days,
        "fy_start_month": start_month,
    }
    return ok(out, assumptions=a)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


@tool
def datetime_tool(mode: str = "now", **params: Any) -> dict[str, Any]:
    """Dates, times, timezones and calendars. See :data:`MODES`."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    return {
        "now": _mode_now,
        "convert_tz": _mode_convert_tz,
        "parse": _mode_parse,
        "add": _mode_add,
        "diff": _mode_diff,
        "weekday": _mode_weekday,
        "nth_weekday": _mode_nth_weekday,
        "business_days": _mode_business_days,
        "overlap": _mode_overlap,
        "duration_sum": _mode_duration_sum,
        "recurrence": _mode_recurrence,
        "cron_next": _mode_cron_next,
        "age": _mode_age,
        "fiscal": _mode_fiscal,
    }[mode](p)
