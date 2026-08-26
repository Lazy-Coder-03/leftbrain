"""geo_offline - timezone for a place, great-circle distance, country zones.

Entirely offline: built on the tzdata tables shipped with Python's ``tzdata``
package (zone1970.tab, zone.tab, iso3166.tab) plus a curated alias list for
major cities that are not tzdata zone names (Mumbai, Bengaluru, Manchester…).
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from importlib.resources import files
from typing import Any

from ..contract import Ambiguous, ToolError, ok, tool

MODES = ("tz_for_place", "tz_for_coords", "distance", "country", "zone_info")

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
    "boston": "America/New_York", "washington": "America/New_York", "washington dc": "America/New_York", "dc": "America/New_York",
    "miami": "America/New_York", "atlanta": "America/New_York", "philadelphia": "America/New_York", "orlando": "America/New_York",
    "tampa": "America/New_York", "charlotte": "America/New_York", "pittsburgh": "America/New_York", "baltimore": "America/New_York",
    "san francisco": "America/Los_Angeles", "sf": "America/Los_Angeles", "seattle": "America/Los_Angeles",
    "san diego": "America/Los_Angeles", "san jose": "America/Los_Angeles", "silicon valley": "America/Los_Angeles",
    "portland": "America/Los_Angeles", "las vegas": "America/Los_Angeles", "sacramento": "America/Los_Angeles", "oakland": "America/Los_Angeles",
    "dallas": "America/Chicago", "houston": "America/Chicago", "austin": "America/Chicago", "san antonio": "America/Chicago",
    "minneapolis": "America/Chicago", "st louis": "America/Chicago", "new orleans": "America/Chicago", "kansas city": "America/Chicago",
    "milwaukee": "America/Chicago", "nashville": "America/Chicago", "memphis": "America/Chicago", "oklahoma city": "America/Chicago",
    "salt lake city": "America/Denver", "albuquerque": "America/Denver", "colorado springs": "America/Denver",
    "montreal": "America/Toronto", "ottawa": "America/Toronto", "quebec": "America/Toronto", "calgary": "America/Edmonton",
    "honolulu": "Pacific/Honolulu", "hawaii": "Pacific/Honolulu", "alaska": "America/Anchorage",
    # UK / Europe
    "manchester": "Europe/London", "birmingham": "Europe/London", "edinburgh": "Europe/London", "glasgow": "Europe/London",
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
_COUNTRY_ALIASES = {"uk": "GB", "usa": "US", "united states": "US", "america": "US", "uae": "AE", "south korea": "KR", "russia": "RU", "vietnam": "VN", "iran": "IR", "syria": "SY", "laos": "LA", "czechia": "CZ", "czech republic": "CZ", "taiwan": "TW", "hong kong": "HK", "macau": "MO", "bolivia": "BO", "venezuela": "VE", "tanzania": "TZ", "moldova": "MD", "north korea": "KP", "turkey": "TR", "türkiye": "TR", "netherlands": "NL", "holland": "NL", "ivory coast": "CI", "cote d'ivoire": "CI", "brunei": "BN", "cape verde": "CV", "micronesia": "FM", "palestine": "PS", "kosovo": "XK", "eswatini": "SZ", "swaziland": "SZ", "myanmar": "MM", "burma": "MM"}


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
            for c in codes:
                by_country.setdefault(c, [])
                if zone not in by_country[c]:
                    by_country[c].append(zone)
    return zones, countries, by_country


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
    if raw in zones or raw.replace(" ", "_") in zones:
        return [raw.replace(" ", "_")]
    key = _norm(raw)
    if key in _CITY_ALIASES:
        return [_CITY_ALIASES[key]]
    idx = _name_index()
    if key in idx:
        return list(idx[key])
    code = _COUNTRY_ALIASES.get(key) or (raw.upper() if len(raw) == 2 and raw.upper() in countries else None)
    if code is None:
        for c, name in countries.items():
            if _norm(name) == key:
                code = c
                break
    if code and code in by_country:
        return list(by_country[code])
    # prefix match on city names (e.g. "kolkata city")
    hits = [z for k, zs in idx.items() if key.startswith(k) or k.startswith(key) for z in zs] if len(key) >= 4 else []
    return list(dict.fromkeys(hits))[:8]


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


def _point(v: Any, name: str, assumptions: list[str]) -> tuple[float, float]:
    if isinstance(v, dict) and "lat" in v and ("lon" in v or "lng" in v):
        return float(v["lat"]), float(v.get("lon", v.get("lng")))
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return float(v[0]), float(v[1])
    if isinstance(v, str):
        m = re.fullmatch(r"\s*([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)\s*", v)
        if m:
            return float(m.group(1)), float(m.group(2))
        zs = lookup_zone(v)
        if len(zs) == 1:
            zones, _, _ = _tables()
            meta = zones.get(zs[0])
            if meta and meta["lat"] is not None:
                assumptions.append(f"'{v}' approximated by the coordinates of its timezone's reference city ({zs[0]})")
                return meta["lat"], meta["lon"]
        if len(zs) > 1:
            raise Ambiguous(f"'{v}' is not specific enough; pass coordinates", field=name, options=zs)
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
    return ok(entries[0] if len(entries) == 1 else {"zones": entries, "count": len(entries)}, assumptions=[f"'{place}' matched via alias table" if _norm(str(place)) in _CITY_ALIASES else ""] if len(zs) == 1 else [])


def _tz_for_coords(p: dict[str, Any]) -> dict[str, Any]:
    assumptions: list[str] = []
    lat, lon = _point({"lat": p.get("lat"), "lon": p.get("lon", p.get("lng"))} if p.get("lat") is not None else p.get("point"), "point", assumptions)
    zones, _, _ = _tables()
    best = sorted(((haversine_km(lat, lon, m["lat"], m["lon"]), z) for z, m in zones.items() if m["lat"] is not None))[:3]
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
    key = _norm(q)
    code = _COUNTRY_ALIASES.get(key) or (q.upper() if q.upper() in countries else None)
    if code is None:
        for c, name in countries.items():
            if _norm(name) == key:
                code = c
                break
    if code is None:
        raise ToolError(f"unknown country {q!r}")
    zs = by_country.get(code, [])
    return ok({"code": code, "name": countries.get(code), "zones": [_zone_entry(z) for z in zs], "zone_count": len(zs), "single_timezone": len(zs) == 1})


def _zone_info(p: dict[str, Any]) -> dict[str, Any]:
    z = p.get("zone") or p.get("value")
    zones, _, _ = _tables()
    from zoneinfo import available_timezones

    if not z or str(z) not in available_timezones():
        raise ToolError("'zone' must be a valid IANA zone name")
    return ok(_zone_entry(str(z)))


@tool
def geo_offline(mode: str = "tz_for_place", **params: Any) -> dict[str, Any]:
    """Offline geo helpers. Modes: tz_for_place, tz_for_coords, distance, country, zone_info."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
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
        {
            "caption": "`place` is required.",
            "args": {"mode": "tz_for_place"},
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
            "caption": "Coordinates are required.",
            "args": {"mode": "tz_for_coords"},
        },
        {
            "caption": "Coordinates must be numbers.",
            "args": {"mode": "tz_for_coords", "lat": "north", "lon": 88.36},
        },
    ],
    "distance": [
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
        {
            "caption": "An unknown place.",
            "args": {"mode": "distance", "origin": "Atlantis", "destination": "Kolkata"},
        },
        {
            "caption": "`destination` is required.",
            "args": {"mode": "distance", "origin": [19.076, 72.8777]},
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
        {
            "caption": "`country` is required.",
            "args": {"mode": "country"},
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
            "caption": "A zone name that does not exist.",
            "args": {"mode": "zone_info", "zone": "Asia/Gotham"},
        },
        {
            "caption": "An abbreviation is not a zone name.",
            "args": {"mode": "zone_info", "zone": "IST"},
        },
        {
            "caption": "`zone` is required.",
            "args": {"mode": "zone_info"},
        },
    ],
}
