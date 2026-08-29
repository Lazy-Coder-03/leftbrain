"""geo_offline - timezone for a place, great-circle distance, country zones.

Entirely offline: built on the tzdata tables shipped with Python's ``tzdata``
package (zone1970.tab, zone.tab, iso3166.tab) plus a curated alias list for
major cities that are not tzdata zone names (Mumbai, Bengaluru, Manchester…).
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from functools import lru_cache
from importlib.resources import files
from typing import Any

from ..contract import Ambiguous, ToolError, check_params, ok, tool

MODES = ("tz_for_place", "tz_for_coords", "distance", "country", "zone_info")

#: What each mode reads. Anything else in a call is a caller's mistake, not a default
#: to fall back on (#28 SS2a). Kept honest by tests/test_mode_params.py, which derives
#: the same map from the code and fails when the two drift.
#: Names that used to work. Kept only so the refusal can say what replaced them.
RENAMED_PARAMS = {"from": "'origin'", "from_": "'origin'", "to": "'destination'"}

MODE_PARAMS: dict[str, frozenset[str]] = {
    "tz_for_place": frozenset({"all", "city", "place", "value"}),
    "tz_for_coords": frozenset({"lat", "lng", "lon", "point"}),
    "distance": frozenset({"a", "b", "destination", "origin"}),
    "country": frozenset({"code", "country", "value"}),
    "zone_info": frozenset({"value", "zone"}),
}

_CITY_ALIASES: dict[str, str] = {
    # India
    "mumbai": "Asia/Kolkata", "bombay": "Asia/Kolkata", "delhi": "Asia/Kolkata", "new delhi": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata", "bengaluru": "Asia/Kolkata", "chennai": "Asia/Kolkata", "madras": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata", "pune": "Asia/Kolkata", "ahmedabad": "Asia/Kolkata", "jaipur": "Asia/Kolkata",
    "lucknow": "Asia/Kolkata", "surat": "Asia/Kolkata", "kanpur": "Asia/Kolkata", "nagpur": "Asia/Kolkata",
    "indore": "Asia/Kolkata", "bhopal": "Asia/Kolkata", "patna": "Asia/Kolkata", "chandigarh": "Asia/Kolkata",
    "kochi": "Asia/Kolkata", "cochin": "Asia/Kolkata", "goa": "Asia/Kolkata", "noida": "Asia/Kolkata",
    "gurgaon": "Asia/Kolkata", "gurugram": "Asia/Kolkata", "calcutta": "Asia/Kolkata", "india": "Asia/Kolkata",
    "coimbatore": "Asia/Kolkata", "visakhapatnam": "Asia/Kolkata", "vizag": "Asia/Kolkata", "bhubaneswar": "Asia/Kolkata",
    "guwahati": "Asia/Kolkata", "thiruvananthapuram": "Asia/Kolkata", "trivandrum": "Asia/Kolkata", "mysore": "Asia/Kolkata",
    "vadodara": "Asia/Kolkata", "nashik": "Asia/Kolkata", "ranchi": "Asia/Kolkata", "raipur": "Asia/Kolkata", "dehradun": "Asia/Kolkata",
    # US / Canada
    "new york city": "America/New_York", "nyc": "America/New_York", "manhattan": "America/New_York", "brooklyn": "America/New_York",
    "boston": "America/New_York", "washington dc": "America/New_York", "dc": "America/New_York",
    "miami": "America/New_York", "atlanta": "America/New_York", "philadelphia": "America/New_York", "orlando": "America/New_York",
    "tampa": "America/New_York", "charlotte": "America/New_York", "pittsburgh": "America/New_York", "baltimore": "America/New_York",
    "san francisco": "America/Los_Angeles", "sf": "America/Los_Angeles", "seattle": "America/Los_Angeles",
    "san diego": "America/Los_Angeles", "san jose": "America/Los_Angeles", "silicon valley": "America/Los_Angeles",
    "las vegas": "America/Los_Angeles", "sacramento": "America/Los_Angeles", "oakland": "America/Los_Angeles",
    "dallas": "America/Chicago", "houston": "America/Chicago", "austin": "America/Chicago", "san antonio": "America/Chicago",
    "minneapolis": "America/Chicago", "st louis": "America/Chicago", "new orleans": "America/Chicago", "kansas city": "America/Chicago",
    "milwaukee": "America/Chicago", "nashville": "America/Chicago", "memphis": "America/Chicago", "oklahoma city": "America/Chicago",
    "salt lake city": "America/Denver", "albuquerque": "America/Denver", "colorado springs": "America/Denver",
    "montreal": "America/Toronto", "ottawa": "America/Toronto", "quebec": "America/Toronto", "calgary": "America/Edmonton",
    "honolulu": "Pacific/Honolulu", "hawaii": "Pacific/Honolulu", "alaska": "America/Anchorage",
    # UK / Europe
    "manchester": "Europe/London", "edinburgh": "Europe/London", "glasgow": "Europe/London",
    "liverpool": "Europe/London", "leeds": "Europe/London", "bristol": "Europe/London", "cambridge": "Europe/London", "oxford": "Europe/London",
    "uk": "Europe/London", "england": "Europe/London", "scotland": "Europe/London", "wales": "Europe/London", "britain": "Europe/London", "united kingdom": "Europe/London",
    "munich": "Europe/Berlin", "frankfurt": "Europe/Berlin", "hamburg": "Europe/Berlin", "cologne": "Europe/Berlin", "stuttgart": "Europe/Berlin", "germany": "Europe/Berlin",
    "milan": "Europe/Rome", "naples": "Europe/Rome", "turin": "Europe/Rome", "florence": "Europe/Rome", "venice": "Europe/Rome", "italy": "Europe/Rome",
    "barcelona": "Europe/Madrid", "valencia": "Europe/Madrid", "seville": "Europe/Madrid", "spain": "Europe/Madrid",
    "lyon": "Europe/Paris", "marseille": "Europe/Paris", "nice": "Europe/Paris", "toulouse": "Europe/Paris", "france": "Europe/Paris",
    "geneva": "Europe/Zurich", "basel": "Europe/Zurich", "bern": "Europe/Zurich", "switzerland": "Europe/Zurich",
    "rotterdam": "Europe/Amsterdam", "the hague": "Europe/Amsterdam", "netherlands": "Europe/Amsterdam", "holland": "Europe/Amsterdam",
    "antwerp": "Europe/Brussels", "belgium": "Europe/Brussels", "krakow": "Europe/Warsaw", "poland": "Europe/Warsaw",
    "porto": "Europe/Lisbon", "portugal": "Europe/Lisbon", "gothenburg": "Europe/Stockholm", "sweden": "Europe/Stockholm",
    "norway": "Europe/Oslo", "denmark": "Europe/Copenhagen", "finland": "Europe/Helsinki", "austria": "Europe/Vienna",
    "czech republic": "Europe/Prague", "czechia": "Europe/Prague", "hungary": "Europe/Budapest", "greece": "Europe/Athens",
    "ireland": "Europe/Dublin", "cork": "Europe/Dublin", "turkey": "Europe/Istanbul", "ankara": "Europe/Istanbul",
    "st petersburg": "Europe/Moscow", "saint petersburg": "Europe/Moscow", "ukraine": "Europe/Kyiv", "romania": "Europe/Bucharest",
    # Middle East
    "abu dhabi": "Asia/Dubai", "sharjah": "Asia/Dubai", "uae": "Asia/Dubai", "united arab emirates": "Asia/Dubai",
    "jeddah": "Asia/Riyadh", "mecca": "Asia/Riyadh", "saudi arabia": "Asia/Riyadh", "doha": "Asia/Qatar", "qatar": "Asia/Qatar",
    "manama": "Asia/Bahrain", "kuwait city": "Asia/Kuwait", "muscat": "Asia/Muscat", "oman": "Asia/Muscat",
    "tel aviv": "Asia/Jerusalem", "israel": "Asia/Jerusalem", "amman": "Asia/Amman", "beirut": "Asia/Beirut", "tehran": "Asia/Tehran", "iran": "Asia/Tehran",
    # Asia-Pacific
    "malaysia": "Asia/Kuala_Lumpur", "penang": "Asia/Kuala_Lumpur", "bali": "Asia/Makassar", "denpasar": "Asia/Makassar",
    "indonesia": "Asia/Jakarta", "surabaya": "Asia/Jakarta", "bandung": "Asia/Jakarta", "thailand": "Asia/Bangkok", "phuket": "Asia/Bangkok",
    "chiang mai": "Asia/Bangkok", "hanoi": "Asia/Ho_Chi_Minh", "saigon": "Asia/Ho_Chi_Minh", "vietnam": "Asia/Ho_Chi_Minh",
    "philippines": "Asia/Manila", "cebu": "Asia/Manila", "osaka": "Asia/Tokyo", "kyoto": "Asia/Tokyo", "japan": "Asia/Tokyo", "nagoya": "Asia/Tokyo",
    "south korea": "Asia/Seoul", "korea": "Asia/Seoul", "busan": "Asia/Seoul", "beijing": "Asia/Shanghai", "shenzhen": "Asia/Shanghai",
    "guangzhou": "Asia/Shanghai", "china": "Asia/Shanghai", "chengdu": "Asia/Shanghai", "hangzhou": "Asia/Shanghai", "wuhan": "Asia/Shanghai",
    "taiwan": "Asia/Taipei", "canberra": "Australia/Sydney", "gold coast": "Australia/Brisbane", "wellington": "Pacific/Auckland",
    "christchurch": "Pacific/Auckland", "new zealand": "Pacific/Auckland", "lahore": "Asia/Karachi", "islamabad": "Asia/Karachi",
    "pakistan": "Asia/Karachi", "bangladesh": "Asia/Dhaka", "chittagong": "Asia/Dhaka", "sri lanka": "Asia/Colombo", "nepal": "Asia/Kathmandu",
    "myanmar": "Asia/Yangon", "cambodia": "Asia/Phnom_Penh", "kazakhstan": "Asia/Almaty",
    # Africa
    "cape town": "Africa/Johannesburg", "durban": "Africa/Johannesburg", "pretoria": "Africa/Johannesburg", "south africa": "Africa/Johannesburg",
    "egypt": "Africa/Cairo", "alexandria": "Africa/Cairo", "nigeria": "Africa/Lagos", "abuja": "Africa/Lagos", "kenya": "Africa/Nairobi",
    "ethiopia": "Africa/Addis_Ababa", "ghana": "Africa/Accra", "morocco": "Africa/Casablanca", "tanzania": "Africa/Dar_es_Salaam",
    # Latin America
    "rio de janeiro": "America/Sao_Paulo", "rio": "America/Sao_Paulo", "brazil": "America/Sao_Paulo", "brasilia": "America/Sao_Paulo",
    "argentina": "America/Argentina/Buenos_Aires", "chile": "America/Santiago", "peru": "America/Lima", "colombia": "America/Bogota",
    "medellin": "America/Bogota", "guadalajara": "America/Mexico_City", "monterrey": "America/Monterrey",
}
#: Names that mean more than one place. `Washington` answered New York with a footnote;
#: the state is on Pacific time, and nobody typing `LA` means Laos without Los Angeles offered.
_AMBIGUOUS_PLACES: dict[str, list[str]] = {
    "washington": ["America/New_York", "America/Los_Angeles"],
    "portland": ["America/Los_Angeles", "America/New_York"],
    "birmingham": ["Europe/London", "America/Chicago"],
    "georgia": ["Asia/Tbilisi", "America/New_York"],
    "la": ["America/Los_Angeles", "Asia/Vientiane"],
}
_COUNTRY_ALIASES = {"united kingdom": "GB", "great britain": "GB", "britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB", "northern ireland": "GB", "timor leste": "TL", "east timor": "TL", "cabo verde": "CV", "viet nam": "VN", "russian federation": "RU", "aland": "AX", "aland islands": "AX", "uk": "GB", "usa": "US", "united states": "US", "america": "US", "uae": "AE", "south korea": "KR", "russia": "RU", "vietnam": "VN", "iran": "IR", "syria": "SY", "laos": "LA", "czechia": "CZ", "czech republic": "CZ", "taiwan": "TW", "hong kong": "HK", "macau": "MO", "bolivia": "BO", "venezuela": "VE", "tanzania": "TZ", "moldova": "MD", "north korea": "KP", "turkey": "TR", "turkiye": "TR", "netherlands": "NL", "holland": "NL", "ivory coast": "CI", "cote d'ivoire": "CI", "brunei": "BN", "cape verde": "CV", "micronesia": "FM", "palestine": "PS", "kosovo": "XK", "eswatini": "SZ", "swaziland": "SZ", "myanmar": "MM", "burma": "MM"}


def _coord(s: str) -> float:
    """±DDMM[SS] or ±DDDMM[SS] -> decimal degrees."""
    sign = -1 if s[0] == "-" else 1
    body = s[1:]
    if len(body) in (4, 5):  # DDMM / DDDMM
        deg_len = len(body) - 2
        deg, mins, secs = int(body[:deg_len]), int(body[deg_len:]), 0
    else:  # DDMMSS / DDDMMSS
        deg_len = len(body) - 4
        deg, mins, secs = int(body[:deg_len]), int(body[deg_len:deg_len + 2]), int(body[deg_len + 2:])
    return sign * (deg + mins / 60 + secs / 3600)


@lru_cache(maxsize=1)
def _tables() -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, list[str]]]:
    root = files("tzdata.zoneinfo")
    zones: dict[str, dict[str, Any]] = {}
    countries: dict[str, str] = {}
    by_country: dict[str, list[str]] = {}
    for line in root.joinpath("iso3166.tab").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            code, name = line.split("\t", 1)
            countries[code] = name.strip()
    for fname in ("zone1970.tab", "zone.tab"):
        for line in root.joinpath(fname).read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            codes = parts[0].split(",")
            m = re.fullmatch(r"([+-]\d+)([+-]\d+)", parts[1])
            lat, lon = (_coord(m.group(1)), _coord(m.group(2))) if m else (None, None)
            zone = parts[2]
            comment = parts[3].strip() if len(parts) > 3 else ""
            if zone not in zones:
                zones[zone] = {"zone": zone, "countries": codes, "lat": lat, "lon": lon, "comment": comment}
            elif fname == "zone.tab" and codes[0] == zones[zone]["countries"][0]:
                # zone1970.tab shares Asia/Dubai between AE, OM, RE, SC and TF and comments on
                # the islands; zone.tab's own line for the first country is about that country
                zones[zone]["comment"] = comment
            for c in codes:
                by_country.setdefault(c, [])
                if zone not in by_country[c]:
                    by_country[c].append(zone)
    return zones, countries, by_country


@lru_cache(maxsize=1)
def _links() -> dict[str, str]:
    """Backward-compatibility names -> the zone they point at (`Asia/Calcutta` -> `Asia/Kolkata`).

    tzdata ships a link as a byte-identical copy of its target, so the copies pair up.
    """
    from zoneinfo import available_timezones

    zones, _, _ = _tables()
    root = files("tzdata.zoneinfo")

    def digest(z: str) -> str | None:
        try:
            return hashlib.sha1(root.joinpath(*z.split("/")).read_bytes()).hexdigest()
        except (FileNotFoundError, OSError, TypeError):
            return None

    by_hash = {h: z for z in zones if (h := digest(z))}
    out: dict[str, str] = {}
    for z in available_timezones():
        if z in zones:
            continue
        target = by_hash.get(digest(z) or "")
        if target:
            out[z] = target
    return out


@lru_cache(maxsize=1)
def _zones_lower() -> dict[str, str]:
    from zoneinfo import available_timezones

    return {z.lower(): z for z in available_timezones()}


def canonical_zone(name: str) -> tuple[str, str | None] | None:
    """(zone, link-note) for an IANA name in any case, or None when it is not one."""
    z = _zones_lower().get(name.strip().replace(" ", "_").lower())
    if z is None:
        return None
    target = _links().get(z)
    if target:
        return target, f"'{z}' is a backward-compatibility link to {target}"
    return z, None


@lru_cache(maxsize=1)
def _name_index() -> dict[str, list[str]]:
    zones, _, _ = _tables()
    idx: dict[str, list[str]] = {}
    for z in zones:
        parts = z.split("/")
        for part in parts[1:]:
            key = part.replace("_", " ").lower()
            idx.setdefault(key, [])
            if z not in idx[key]:
                idx[key].append(z)
    return idx


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))  # Réunion and Reunion are one place
    s = s.strip().lower().replace("_", " ").replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"^(st\.|saint)\s", "st ", s)
    return s


def lookup_zone(place: str) -> list[str]:
    """Return candidate IANA zones for a place name (city, country, or zone)."""
    if not place:
        return []
    zones, countries, by_country = _tables()
    raw = place.strip()
    exact = canonical_zone(raw)
    if exact:
        return [exact[0]]
    key = _norm(raw)
    if key in _AMBIGUOUS_PLACES:
        return list(_AMBIGUOUS_PLACES[key])
    if key in _CITY_ALIASES:
        return [_CITY_ALIASES[key]]
    idx = _name_index()
    if key in idx:
        return list(idx[key])
    code = country_code(raw)
    if code and code in by_country:
        return list(by_country[code])
    # prefix match on city names (e.g. "kolkata city")
    hits = [z for k, zs in idx.items() if key.startswith(k) or k.startswith(key) for z in zs] if len(key) >= 4 else []
    return list(dict.fromkeys(hits))[:8]


def country_code(q: str) -> str | None:
    """ISO 3166 code for a country given by code or by name, in any case, accents or not."""
    _, countries, _ = _tables()
    key = _norm(q)
    code = _COUNTRY_ALIASES.get(key) or (q.strip().upper() if len(q.strip()) == 2 and q.strip().upper() in countries else None)
    if code is None:
        for c, name in countries.items():
            if _norm(name) == key:
                return c
    return code


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _compass(b: float) -> str:
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[int((b + 11.25) // 22.5) % 16]


def _zone_entry(z: str) -> dict[str, Any]:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    zones, countries, _ = _tables()
    meta = zones.get(z, {"zone": z, "countries": [], "lat": None, "lon": None, "comment": ""})
    now = datetime.now(ZoneInfo(z))
    off = now.utcoffset()
    secs = int(off.total_seconds()) if off else 0
    sign = "+" if secs >= 0 else "-"
    secs = abs(secs)
    return {
        "zone": z,
        "utc_offset_now": f"{sign}{secs // 3600:02d}:{(secs % 3600) // 60:02d}",
        "abbreviation_now": now.tzname(),
        "is_dst_now": bool(now.dst()),
        "countries": [{"code": c, "name": countries.get(c, c)} for c in meta["countries"]],
        "coordinates": {"lat": meta["lat"], "lon": meta["lon"]} if meta["lat"] is not None else None,
        "note": meta["comment"] or None,
        "local_time_now": now.isoformat(),
    }


def _on_globe(lat: float, lon: float, name: str) -> tuple[float, float]:
    """Coordinates that exist. `lat=91` used to return a timezone and a 10 118 km distance."""
    if not -90 <= lat <= 90:
        raise ToolError(
            f"{name} latitude {lat} is off the globe; latitude runs from -90 to 90",
            details={name: {"lat": lat, "lon": lon}},
            hint="Check whether lat and lon are the wrong way round.",
        )
    if not -180 <= lon <= 180:
        raise ToolError(
            f"{name} longitude {lon} is off the globe; longitude runs from -180 to 180",
            details={name: {"lat": lat, "lon": lon}},
            hint="Check whether lat and lon are the wrong way round.",
        )
    return lat, lon


def _degrees(v: Any, name: str, what: str) -> float:
    if isinstance(v, bool) or v is None:
        raise ToolError(f"{name}: {what} must be a number, not {v!r}")
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ToolError(f"{name}: {what} must be a number in degrees, not {v!r}") from None


def _point(v: Any, name: str, assumptions: list[str]) -> tuple[float, float]:
    if isinstance(v, dict) and ("lat" in v or "lon" in v or "lng" in v):
        if v.get("lat") is None:
            raise ToolError(f"{name}: lat is required alongside lon")
        if v.get("lon", v.get("lng")) is None:
            raise ToolError(f"{name}: lon is required alongside lat")
        return _on_globe(_degrees(v["lat"], name, "lat"), _degrees(v.get("lon", v.get("lng")), name, "lon"), name)
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return _on_globe(_degrees(v[0], name, "lat"), _degrees(v[1], name, "lon"), name)
    if isinstance(v, str):
        m = re.fullmatch(r"\s*([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)\s*", v)
        if m:
            return _on_globe(float(m.group(1)), float(m.group(2)), name)
        zs = lookup_zone(v)
        if len(zs) > 1:
            raise Ambiguous(f"'{v}' is not specific enough; pass coordinates", field=name, options=zs)
        if len(zs) == 1:
            zones, _, _ = _tables()
            meta = zones.get(zs[0])
            # The coordinates on a zone belong to its *reference city*, so they are the right
            # answer for "Kolkata" and the wrong one for "Delhi" - which also resolves to
            # Asia/Kolkata. Substituting the centroid put Delhi and Mumbai 0 km apart and
            # said so only in an assumption nobody reads before trusting the number (#28 SS2d).
            if meta and meta["lat"] is not None and _norm(zs[0].rsplit("/", 1)[-1].replace("_", " ")) == _norm(v):
                return _on_globe(meta["lat"], meta["lon"], name)
            raise Ambiguous(
                f"'{v}' is not a place leftbrain has coordinates for; its timezone "
                f"({zs[0]}) locates the zone's reference city, not '{v}'",
                field=name,
                options=[f"{{\"lat\": …, \"lon\": …}} for {v}", f"geocode '{v}' first with the external geo tool"],
            )
    raise ToolError(f"{name} must be {{lat, lon}}, [lat, lon], 'lat,lon' or a known place name")


def _distance(p: dict[str, Any]) -> dict[str, Any]:
    assumptions: list[str] = []
    a = _point(p.get("origin") or p.get("a"), "origin", assumptions)
    b = _point(p.get("destination") or p.get("b"), "destination", assumptions)
    km = haversine_km(*a, *b)
    br = _bearing(*a, *b)
    return ok({"km": round(km, 3), "miles": round(km / 1.609344, 3), "nautical_miles": round(km / 1.852, 3), "meters": round(km * 1000, 1), "bearing_deg": round(br, 2), "compass": _compass(br), "origin": {"lat": a[0], "lon": a[1]}, "destination": {"lat": b[0], "lon": b[1]}}, assumptions=assumptions + ["great-circle (haversine) distance, not driving distance"])


def _tz_for_place(p: dict[str, Any]) -> dict[str, Any]:
    place = p.get("place") or p.get("city") or p.get("value")
    if not place:
        raise ToolError("'place' is required")
    zs = lookup_zone(str(place))
    if not zs:
        raise ToolError(f"unknown place {place!r}; pass an IANA zone or coordinates")
    if len(zs) > 1 and not p.get("all"):
        raise Ambiguous(f"'{place}' spans {len(zs)} timezones", field="place", options=zs)
    entries = [_zone_entry(z) for z in zs]
    assumptions: list[str] = []
    if len(zs) == 1:
        exact = canonical_zone(str(place))
        if exact and exact[1]:
            assumptions.append(exact[1])
        elif _norm(str(place)) in _CITY_ALIASES:
            assumptions.append(f"'{place}' matched via alias table")
    return ok(entries[0] if len(entries) == 1 else {"zones": entries, "count": len(entries)}, assumptions=assumptions)


#: Reference points beyond tzdata's one-city-per-zone. India's only reference is Kolkata, so
#: New Delhi (800 km away) was nearer Kathmandu and Bengaluru nearer Colombo. (lat, lon, zone)
_EXTRA_REFERENCES: list[tuple[float, float, str]] = [
    (28.61, 77.21, "Asia/Kolkata"), (19.08, 72.88, "Asia/Kolkata"), (12.97, 77.59, "Asia/Kolkata"), (13.08, 80.27, "Asia/Kolkata"),
    (17.39, 78.49, "Asia/Kolkata"), (23.02, 72.57, "Asia/Kolkata"), (18.52, 73.86, "Asia/Kolkata"), (26.91, 75.79, "Asia/Kolkata"),
    (26.85, 80.95, "Asia/Kolkata"), (21.15, 79.09, "Asia/Kolkata"), (30.73, 76.78, "Asia/Kolkata"), (9.93, 76.27, "Asia/Kolkata"),
    (25.59, 85.14, "Asia/Kolkata"), (26.14, 91.74, "Asia/Kolkata"), (8.52, 76.94, "Asia/Kolkata"), (34.08, 74.80, "Asia/Kolkata"),
    (39.90, 116.40, "Asia/Shanghai"), (23.13, 113.26, "Asia/Shanghai"), (30.57, 104.07, "Asia/Shanghai"), (34.34, 108.94, "Asia/Shanghai"),
    (45.75, 126.65, "Asia/Shanghai"), (36.06, 103.83, "Asia/Shanghai"), (25.04, 102.71, "Asia/Shanghai"), (29.65, 91.13, "Asia/Shanghai"),
    (43.83, 87.62, "Asia/Urumqi"), (31.55, 74.34, "Asia/Karachi"), (33.69, 73.04, "Asia/Karachi"), (34.02, 71.52, "Asia/Karachi"),
    (24.71, 46.68, "Asia/Riyadh"), (21.49, 39.19, "Asia/Riyadh"), (35.69, 51.39, "Asia/Tehran"), (29.60, 52.53, "Asia/Tehran"),
    (38.07, 46.30, "Asia/Tehran"), (36.29, 59.61, "Asia/Tehran"), (33.31, 44.37, "Asia/Baghdad"), (36.19, 44.01, "Asia/Baghdad"),
    (41.01, 28.98, "Europe/Istanbul"), (39.93, 32.86, "Europe/Istanbul"), (38.42, 27.14, "Europe/Istanbul"), (37.87, 32.48, "Europe/Istanbul"),
    (55.76, 37.62, "Europe/Moscow"), (59.94, 30.32, "Europe/Moscow"), (55.03, 82.92, "Asia/Novosibirsk"), (56.84, 60.60, "Asia/Yekaterinburg"),
    (43.12, 131.89, "Asia/Vladivostok"), (52.30, 104.30, "Asia/Irkutsk"), (30.04, 31.24, "Africa/Cairo"), (31.20, 29.92, "Africa/Cairo"),
    (6.52, 3.38, "Africa/Lagos"), (9.06, 7.49, "Africa/Lagos"), (12.00, 8.52, "Africa/Lagos"), (-1.29, 36.82, "Africa/Nairobi"),
    (-4.04, 39.67, "Africa/Nairobi"), (9.03, 38.74, "Africa/Addis_Ababa"), (-26.20, 28.05, "Africa/Johannesburg"), (-33.93, 18.42, "Africa/Johannesburg"),
    (-29.86, 31.02, "Africa/Johannesburg"), (15.60, 32.53, "Africa/Khartoum"), (-6.79, 39.28, "Africa/Dar_es_Salaam"), (-4.44, 15.27, "Africa/Kinshasa"),
    (-6.21, 106.85, "Asia/Jakarta"), (-7.25, 112.75, "Asia/Jakarta"), (3.60, 98.67, "Asia/Jakarta"), (-8.65, 115.22, "Asia/Makassar"),
    (13.76, 100.50, "Asia/Bangkok"), (18.79, 98.98, "Asia/Bangkok"), (21.03, 105.85, "Asia/Ho_Chi_Minh"), (10.82, 106.63, "Asia/Ho_Chi_Minh"),
    (16.87, 96.20, "Asia/Yangon"), (23.81, 90.41, "Asia/Dhaka"), (22.36, 91.78, "Asia/Dhaka"), (27.72, 85.32, "Asia/Kathmandu"),
    (6.93, 79.85, "Asia/Colombo"), (3.14, 101.69, "Asia/Kuala_Lumpur"), (14.60, 120.98, "Asia/Manila"), (10.32, 123.90, "Asia/Manila"),
    (35.68, 139.69, "Asia/Tokyo"), (34.69, 135.50, "Asia/Tokyo"), (43.06, 141.35, "Asia/Tokyo"), (33.59, 130.40, "Asia/Tokyo"),
    (37.57, 126.98, "Asia/Seoul"), (35.18, 129.08, "Asia/Seoul"), (25.03, 121.57, "Asia/Taipei"), (22.63, 120.30, "Asia/Taipei"),
    (-33.87, 151.21, "Australia/Sydney"), (-37.81, 144.96, "Australia/Melbourne"), (-27.47, 153.03, "Australia/Brisbane"), (-31.95, 115.86, "Australia/Perth"),
    (-34.93, 138.60, "Australia/Adelaide"), (-12.46, 130.84, "Australia/Darwin"), (-42.88, 147.33, "Australia/Hobart"), (-36.85, 174.76, "Pacific/Auckland"),
    (-43.53, 172.64, "Pacific/Auckland"), (40.71, -74.01, "America/New_York"), (42.36, -71.06, "America/New_York"), (25.76, -80.19, "America/New_York"),
    (33.75, -84.39, "America/New_York"), (39.95, -75.17, "America/New_York"), (38.90, -77.04, "America/New_York"), (42.33, -83.05, "America/Detroit"),
    (41.88, -87.63, "America/Chicago"), (29.76, -95.37, "America/Chicago"), (32.78, -96.80, "America/Chicago"), (44.98, -93.27, "America/Chicago"),
    (39.74, -104.99, "America/Denver"), (40.76, -111.89, "America/Denver"), (33.45, -112.07, "America/Phoenix"), (34.05, -118.24, "America/Los_Angeles"),
    (37.77, -122.42, "America/Los_Angeles"), (47.61, -122.33, "America/Los_Angeles"), (36.17, -115.14, "America/Los_Angeles"), (45.52, -122.68, "America/Los_Angeles"),
    (61.22, -149.90, "America/Anchorage"), (21.31, -157.86, "Pacific/Honolulu"), (43.65, -79.38, "America/Toronto"), (45.50, -73.57, "America/Toronto"),
    (49.28, -123.12, "America/Vancouver"), (51.05, -114.07, "America/Edmonton"), (49.90, -97.14, "America/Winnipeg"), (19.43, -99.13, "America/Mexico_City"),
    (20.67, -103.35, "America/Mexico_City"), (25.69, -100.32, "America/Monterrey"), (-23.55, -46.63, "America/Sao_Paulo"), (-22.91, -43.17, "America/Sao_Paulo"),
    (-15.79, -47.88, "America/Sao_Paulo"), (-12.97, -38.51, "America/Bahia"), (-3.12, -60.02, "America/Manaus"), (-34.60, -58.38, "America/Argentina/Buenos_Aires"),
    (-31.42, -64.18, "America/Argentina/Cordoba"), (-33.45, -70.67, "America/Santiago"), (-12.05, -77.04, "America/Lima"), (4.71, -74.07, "America/Bogota"),
    (10.48, -66.88, "America/Caracas"), (-0.18, -78.47, "America/Guayaquil"), (-16.50, -68.15, "America/La_Paz"), (51.51, -0.13, "Europe/London"),
    (53.48, -2.24, "Europe/London"), (55.95, -3.19, "Europe/London"), (52.52, 13.41, "Europe/Berlin"), (48.14, 11.58, "Europe/Berlin"),
    (48.86, 2.35, "Europe/Paris"), (43.30, 5.37, "Europe/Paris"), (40.42, -3.70, "Europe/Madrid"), (41.39, 2.17, "Europe/Madrid"),
    (41.90, 12.50, "Europe/Rome"), (45.46, 9.19, "Europe/Rome"), (52.37, 4.90, "Europe/Amsterdam"), (50.85, 4.35, "Europe/Brussels"),
    (47.38, 8.54, "Europe/Zurich"), (48.21, 16.37, "Europe/Vienna"), (52.23, 21.01, "Europe/Warsaw"), (50.08, 14.44, "Europe/Prague"),
    (59.33, 18.07, "Europe/Stockholm"), (59.91, 10.75, "Europe/Oslo"), (55.68, 12.57, "Europe/Copenhagen"), (60.17, 24.94, "Europe/Helsinki"),
    (38.72, -9.14, "Europe/Lisbon"), (53.35, -6.26, "Europe/Dublin"), (37.98, 23.73, "Europe/Athens"), (50.45, 30.52, "Europe/Kyiv"),
    (44.43, 26.10, "Europe/Bucharest"), (47.50, 19.04, "Europe/Budapest"), (25.20, 55.27, "Asia/Dubai"), (24.45, 54.38, "Asia/Dubai"),
    (23.59, 58.41, "Asia/Muscat"), (25.29, 51.53, "Asia/Qatar"), (29.38, 47.98, "Asia/Kuwait"), (32.08, 34.78, "Asia/Jerusalem"),
    (31.95, 35.93, "Asia/Amman"), (33.89, 35.50, "Asia/Beirut"), (33.51, 36.29, "Asia/Damascus"), (43.24, 76.95, "Asia/Almaty"),
    (41.30, 69.24, "Asia/Tashkent"), (34.53, 69.17, "Asia/Kabul"), (33.57, -7.59, "Africa/Casablanca"), (36.75, 3.06, "Africa/Algiers"),
    (36.81, 10.18, "Africa/Tunis"), (5.60, -0.19, "Africa/Accra"), (14.69, -17.44, "Africa/Dakar"), (-18.88, 47.51, "Indian/Antananarivo"),
    (-15.42, 28.28, "Africa/Lusaka"), (-17.83, 31.05, "Africa/Harare"), (-25.97, 32.57, "Africa/Maputo"), (-8.84, 13.23, "Africa/Luanda"),
]


def _tz_for_coords(p: dict[str, Any]) -> dict[str, Any]:
    assumptions: list[str] = []
    given = p.get("lat") is not None or p.get("lon") is not None or p.get("lng") is not None
    lat, lon = _point({"lat": p.get("lat"), "lon": p.get("lon", p.get("lng"))} if given else p.get("point"), "point", assumptions)
    zones, _, _ = _tables()
    refs = [(m["lat"], m["lon"], z) for z, m in zones.items() if m["lat"] is not None] + _EXTRA_REFERENCES
    ranked = sorted((haversine_km(lat, lon, rlat, rlon), z) for rlat, rlon, z in refs)
    best: list[tuple[float, str]] = []
    for d, z in ranked:  # the three nearest *distinct* zones
        if all(z != b for _, b in best):
            best.append((d, z))
        if len(best) == 3:
            break
    if not best:
        raise ToolError("no zone data available")
    entry = _zone_entry(best[0][1])
    entry["distance_to_reference_km"] = round(best[0][0], 1)
    entry["alternatives"] = [{"zone": z, "km": round(d, 1)} for d, z in best[1:]]
    return ok(entry, assumptions=assumptions, warnings=["nearest-reference-city heuristic; near borders verify with a boundary-aware lookup"])


def _country(p: dict[str, Any]) -> dict[str, Any]:
    q = str(p.get("value") or p.get("country") or p.get("code") or "").strip()
    if not q:
        raise ToolError("'country' is required")
    zones, countries, by_country = _tables()
    code = country_code(q)
    if code is None:
        raise ToolError(f"unknown country {q!r}")
    zs = by_country.get(code, [])
    warnings = [f"tzdata lists no zones for {code} ({countries.get(code) or q}); its time is kept under a neighbour's zone"] if not zs else []
    return ok({"code": code, "name": countries.get(code), "zones": [_zone_entry(z) for z in zs], "zone_count": len(zs), "single_timezone": len(zs) == 1}, warnings=warnings)


def _zone_info(p: dict[str, Any]) -> dict[str, Any]:
    z = p.get("zone") or p.get("value")
    exact = canonical_zone(str(z)) if z else None
    if not exact:
        raise ToolError("'zone' must be a valid IANA zone name")
    return ok(_zone_entry(exact[0]), assumptions=[exact[1]] if exact[1] else [])


@tool
def geo_offline(mode: str = "tz_for_place", **params: Any) -> dict[str, Any]:
    """Offline geo helpers. Modes: tz_for_place, tz_for_coords, distance, country, zone_info."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    check_params("geo_offline", mode, p, MODE_PARAMS, RENAMED_PARAMS)
    return {"tz_for_place": _tz_for_place, "tz_for_coords": _tz_for_coords, "distance": _distance, "country": _country, "zone_info": _zone_info}[mode](p)

#: Worked examples for the reference page, one list per mode. Every one of them is
#: executed when /docs/tools/geo_offline is built and sorted by the result into
#: "Examples" (the call succeeded) and "Fails when" (it did not), so a fixture never
#: states an expectation of its own. Mark anything whose output depends on the
#: current instant with "volatile": True.
EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "tz_for_place": [
        {
            "caption": "A city alias resolved to its zone.",
            "args": {"mode": "tz_for_place", "place": "Mumbai"},
            "volatile": True,
        },
        {
            "caption": "An exact zone name passes straight through.",
            "args": {"mode": "tz_for_place", "place": "Europe/Berlin"},
            "volatile": True,
        },
        {
            "caption": "A country that spans several zones, with `all` set.",
            "args": {"mode": "tz_for_place", "place": "Portugal", "all": True},
            "volatile": True,
        },
        {
            "caption": "A country spanning many zones, without `all`: the candidates come back in `needs.options`.",
            "args": {"mode": "tz_for_place", "place": "Australia"},
        },
        {
            "caption": "A place the dataset does not know.",
            "args": {"mode": "tz_for_place", "place": "Atlantis"},
        },
    ],
    "tz_for_coords": [
        {
            "caption": "Coordinates in eastern India.",
            "args": {"mode": "tz_for_coords", "lat": 22.5726, "lon": 88.3639},
            "volatile": True,
        },
        {
            "caption": "Coordinates in New York.",
            "args": {"mode": "tz_for_coords", "lat": 40.7128, "lon": -74.006},
            "volatile": True,
        },
        {
            "caption": "Coordinates must be numbers.",
            "args": {"mode": "tz_for_coords", "lat": "north", "lon": 88.36},
        },
    ],
    "distance": [
        {
            "caption": "A place leftbrain only knows a timezone for is refused rather than approximated - Delhi and Mumbai both sit in Asia/Kolkata, which put them 0 km apart.",
            "args": {"mode": "distance", "origin": "Delhi", "destination": "Mumbai"},
        },
        {
            "caption": "Mumbai to Delhi, by coordinates.",
            "args": {"mode": "distance", "origin": [19.076, 72.8777], "destination": [28.6139, 77.209]},
        },
        {
            "caption": "Coordinates as strings, Bengaluru to Chennai.",
            "args": {"mode": "distance", "origin": "12.9716,77.5946", "destination": "13.0827,80.2707"},
        },
        {
            "caption": "Place names, with the approximation stated in `assumptions`.",
            "args": {"mode": "distance", "origin": "Kolkata", "destination": "London"},
        },
        {
            "caption": "A place name that spans several zones is not specific enough to be a point.",
            "args": {"mode": "distance", "origin": "Australia", "destination": "Kolkata"},
        },
    ],
    "country": [
        {
            "caption": "A single-zone country.",
            "args": {"mode": "country", "country": "IN"},
            "volatile": True,
        },
        {
            "caption": "A country resolved by name, spanning two zones.",
            "args": {"mode": "country", "country": "New Zealand"},
            "volatile": True,
        },
        {
            "caption": "An unknown country.",
            "args": {"mode": "country", "country": "Freedonia"},
        },
    ],
    "zone_info": [
        {
            "caption": "A zone with no daylight saving.",
            "args": {"mode": "zone_info", "zone": "Asia/Kolkata"},
            "volatile": True,
        },
        {
            "caption": "A zone that does observe it.",
            "args": {"mode": "zone_info", "zone": "America/New_York"},
            "volatile": True,
        },
        {
            "caption": "`zone` is required.",
            "args": {"mode": "zone_info"},
        },
    ],
}
