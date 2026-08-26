"""External tools built on free public APIs (no keys): Open-Meteo, Frankfurter, Nominatim, OSRM."""

from __future__ import annotations

import time
from typing import Any

from ..contract import ToolError, ok, tool

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

USER_AGENT = "leftbrain/0.1 (+https://github.com/Lazy-Coder-03/leftbrain)"
TIMEOUT = 15.0

_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle", 56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain", 66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains", 80: "slight rain showers",
    81: "moderate rain showers", 82: "violent rain showers", 85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


def _client() -> Any:
    if httpx is None:
        raise ToolError("external tools need httpx: pip install 'leftbrain[external]'", code="unsupported")
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def _get(url: str, params: dict[str, Any] | None = None) -> Any:
    with _client() as c:
        try:
            r = c.get(url, params=params)
        except httpx.HTTPError as e:
            raise ToolError(f"network error: {e}", code="network") from None
    if r.status_code >= 400:
        raise ToolError(f"{url} returned HTTP {r.status_code}: {r.text[:200]}", code="upstream")
    try:
        return r.json()
    except ValueError:
        raise ToolError("upstream returned non-JSON", code="upstream") from None


def geocode(place: str, limit: int = 1) -> list[dict[str, Any]]:
    data = _get("https://geocoding-api.open-meteo.com/v1/search", {"name": place, "count": limit, "language": "en", "format": "json"})
    results = data.get("results") or []
    if not results:
        data = _get("https://nominatim.openstreetmap.org/search", {"q": place, "format": "jsonv2", "limit": limit})
        results = [{"name": d.get("display_name"), "latitude": float(d["lat"]), "longitude": float(d["lon"]), "country": None, "timezone": None} for d in data]
    return [{"name": r.get("name"), "lat": r.get("latitude"), "lon": r.get("longitude"), "country": r.get("country"), "admin1": r.get("admin1"), "timezone": r.get("timezone"), "population": r.get("population")} for r in results]


def _resolve_point(p: dict[str, Any]) -> tuple[float, float, dict[str, Any], list[str]]:
    assumptions: list[str] = []
    if p.get("lat") is not None and p.get("lon") is not None:
        return float(p["lat"]), float(p["lon"]), {"lat": float(p["lat"]), "lon": float(p["lon"])}, assumptions
    place = p.get("place") or p.get("location") or p.get("city")
    if not place:
        raise ToolError("'place' or lat/lon is required")
    hits = geocode(str(place), 1)
    if not hits:
        raise ToolError(f"could not geocode {place!r}")
    h = hits[0]
    assumptions.append(f"'{place}' geocoded to {h['name']}, {h.get('admin1') or ''} {h.get('country') or ''} ({h['lat']:.4f}, {h['lon']:.4f})".replace("  ", " "))
    return float(h["lat"]), float(h["lon"]), h, assumptions


@tool
def weather(mode: str = "current", **params: Any) -> dict[str, Any]:
    """Weather via Open-Meteo. Modes: current | forecast (days) | historical (date) | summary."""
    p = {k: v for k, v in params.items() if v is not None}
    if mode not in ("current", "forecast", "historical", "summary"):
        raise ToolError("mode must be current, forecast, historical or summary")
    lat, lon, loc, assumptions = _resolve_point(p)
    units = (p.get("units") or "metric").lower()
    temp_unit = "fahrenheit" if units == "imperial" else "celsius"
    wind_unit = "mph" if units == "imperial" else "kmh"
    tz = p.get("tz") or "auto"
    if mode == "historical":
        date = p.get("date")
        if not date:
            raise ToolError("historical needs 'date' (YYYY-MM-DD)")
        data = _get("https://archive-api.open-meteo.com/v1/archive", {"latitude": lat, "longitude": lon, "start_date": date, "end_date": p.get("end_date") or date, "daily": "temperature_2m_max,temperature_2m_min,temperature_2m_mean,precipitation_sum,rain_sum,windspeed_10m_max,weathercode", "timezone": tz, "temperature_unit": temp_unit, "windspeed_unit": wind_unit})
        d = data.get("daily", {})
        days = [{"date": d["time"][i], "t_max": d["temperature_2m_max"][i], "t_min": d["temperature_2m_min"][i], "t_mean": d["temperature_2m_mean"][i], "precipitation_mm": d["precipitation_sum"][i], "wind_max": d["windspeed_10m_max"][i], "conditions": _WMO.get(d["weathercode"][i])} for i in range(len(d.get("time", [])))]
        return ok({"location": loc, "timezone": data.get("timezone"), "days": days, "units": units}, assumptions=assumptions)
    days = int(p.get("days", 7 if mode in ("forecast", "summary") else 1))
    if days < 1 or days > 16:
        raise ToolError("days must be 1..16")
    data = _get("https://api.open-meteo.com/v1/forecast", {"latitude": lat, "longitude": lon, "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m,wind_direction_10m,cloud_cover,pressure_msl", "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,sunrise,sunset,uv_index_max", "forecast_days": days, "timezone": tz, "temperature_unit": temp_unit, "wind_speed_unit": wind_unit})
    cur = data.get("current", {})
    current = {"time": cur.get("time"), "temperature": cur.get("temperature_2m"), "feels_like": cur.get("apparent_temperature"), "humidity_pct": cur.get("relative_humidity_2m"), "precipitation_mm": cur.get("precipitation"), "conditions": _WMO.get(cur.get("weather_code")), "wind_speed": cur.get("wind_speed_10m"), "wind_direction_deg": cur.get("wind_direction_10m"), "cloud_cover_pct": cur.get("cloud_cover"), "pressure_hpa": cur.get("pressure_msl"), "is_day": bool(cur.get("is_day"))}
    d = data.get("daily", {})
    daily = [{"date": d["time"][i], "conditions": _WMO.get(d["weather_code"][i]), "t_max": d["temperature_2m_max"][i], "t_min": d["temperature_2m_min"][i], "precipitation_mm": d["precipitation_sum"][i], "precipitation_probability_pct": d["precipitation_probability_max"][i], "wind_max": d["wind_speed_10m_max"][i], "sunrise": d["sunrise"][i], "sunset": d["sunset"][i], "uv_index_max": d["uv_index_max"][i]} for i in range(len(d.get("time", [])))]
    out: dict[str, Any] = {"location": loc, "timezone": data.get("timezone"), "units": units, "current": current}
    if mode != "current":
        out["daily"] = daily
    if mode == "summary":
        out["summary"] = f"{current['conditions']}, {current['temperature']}° (feels {current['feels_like']}°), humidity {current['humidity_pct']}%, wind {current['wind_speed']} {wind_unit}. Next {len(daily)} days: " + "; ".join(f"{x['date'][5:]} {x['conditions']} {x['t_min']}–{x['t_max']}°" for x in daily)
    return ok(out, assumptions=assumptions + [f"units: {units} ({temp_unit}, {wind_unit})", "source: Open-Meteo"])


@tool
def fx_rate(**params: Any) -> dict[str, Any]:
    """Exchange rates via Frankfurter (ECB reference rates). base, symbols/to, date, amount."""
    p = {k: v for k, v in params.items() if v is not None}
    base = str(p.get("base") or p.get("from_unit") or p.get("from") or "USD").upper()
    to = p.get("symbols") or p.get("to") or p.get("to_unit")
    if isinstance(to, str):
        to = [x.strip().upper() for x in to.split(",")]
    elif isinstance(to, list):
        to = [str(x).upper() for x in to]
    date = p.get("date") or "latest"
    q: dict[str, Any] = {"base": base}
    if to:
        q["symbols"] = ",".join(to)
    data = _get(f"https://api.frankfurter.dev/v1/{date}", q)
    rates = data.get("rates") or {}
    if to and not rates:
        raise ToolError(f"no rates returned for {base}->{to}; unsupported currency?", code="upstream")
    out: dict[str, Any] = {"base": base, "date": data.get("date"), "rates": rates}
    amount = p.get("amount") or p.get("value")
    if amount is not None and to and len(to) == 1 and to[0] in rates:
        out["converted"] = {"amount": float(amount), "from": base, "to": to[0], "rate": rates[to[0]], "value": round(float(amount) * rates[to[0]], 4)}
    out["rates_table_for_convert"] = {base: 1.0, **rates}
    return ok(out, assumptions=["ECB reference rates (Frankfurter); mid-market, not a retail quote", f"date: {data.get('date')}" + (" (latest available business day)" if date == "latest" else "")])


@tool
def geo(mode: str = "geocode", **params: Any) -> dict[str, Any]:
    """Online geo: geocode (place -> coordinates), reverse (lat,lon -> address), route (driving distance/time)."""
    p = {k: v for k, v in params.items() if v is not None}
    if mode == "geocode":
        place = p.get("place") or p.get("query")
        if not place:
            raise ToolError("'place' is required")
        hits = geocode(str(place), int(p.get("limit", 5)))
        if not hits:
            raise ToolError(f"no results for {place!r}")
        return ok({"best": hits[0], "results": hits}, assumptions=["Open-Meteo geocoding with Nominatim fallback"])
    if mode == "reverse":
        lat, lon = p.get("lat"), p.get("lon")
        if lat is None or lon is None:
            raise ToolError("reverse needs lat and lon")
        data = _get("https://nominatim.openstreetmap.org/reverse", {"lat": lat, "lon": lon, "format": "jsonv2"})
        return ok({"display_name": data.get("display_name"), "address": data.get("address"), "type": data.get("type"), "lat": float(data.get("lat", lat)), "lon": float(data.get("lon", lon))}, assumptions=["OpenStreetMap Nominatim"])
    if mode == "route":
        a_lat, a_lon, a_loc, aa = _resolve_point({"place": p.get("from"), "lat": (p.get("from") or {}).get("lat") if isinstance(p.get("from"), dict) else None, "lon": (p.get("from") or {}).get("lon") if isinstance(p.get("from"), dict) else None})
        b_lat, b_lon, b_loc, ab = _resolve_point({"place": p.get("to"), "lat": (p.get("to") or {}).get("lat") if isinstance(p.get("to"), dict) else None, "lon": (p.get("to") or {}).get("lon") if isinstance(p.get("to"), dict) else None})
        profile = p.get("profile") or "driving"
        data = _get(f"https://router.project-osrm.org/route/v1/{profile}/{a_lon},{a_lat};{b_lon},{b_lat}", {"overview": "false"})
        routes = data.get("routes") or []
        if not routes:
            raise ToolError("no route found", code="upstream")
        r = routes[0]
        return ok({"distance_km": round(r["distance"] / 1000, 2), "duration_min": round(r["duration"] / 60, 1), "duration_human": f"{int(r['duration'] // 3600)}h {int((r['duration'] % 3600) // 60)}m", "from": a_loc, "to": b_loc, "profile": profile}, assumptions=aa + ab + ["OSRM demo server; no live traffic"])
    raise ToolError("mode must be geocode, reverse or route")


@tool
def url_check(**params: Any) -> dict[str, Any]:
    """Real HTTP check of a URL: status, redirect chain, content-type, size, latency."""
    p = {k: v for k, v in params.items() if v is not None}
    url = p.get("url") or p.get("value")
    if not url:
        raise ToolError("'url' is required")
    url = str(url).strip()
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    method = (p.get("method") or "HEAD").upper()
    if httpx is None:
        raise ToolError("external tools need httpx: pip install 'leftbrain[external]'", code="unsupported")
    t0 = time.perf_counter()
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as c:
        try:
            r = c.request(method, url)
            if method == "HEAD" and r.status_code in (405, 403, 400):
                r = c.get(url)
        except httpx.HTTPError as e:
            return ok({"url": url, "reachable": False, "error": f"{type(e).__name__}: {e}", "latency_ms": round((time.perf_counter() - t0) * 1000)})
    chain = [{"url": str(h.url), "status": h.status_code} for h in r.history] + [{"url": str(r.url), "status": r.status_code}]
    return ok({"url": url, "final_url": str(r.url), "status": r.status_code, "ok": 200 <= r.status_code < 400, "reachable": True, "redirects": len(r.history), "chain": chain, "content_type": r.headers.get("content-type"), "content_length": r.headers.get("content-length"), "server": r.headers.get("server"), "latency_ms": round((time.perf_counter() - t0) * 1000), "https": str(r.url).startswith("https://")})
