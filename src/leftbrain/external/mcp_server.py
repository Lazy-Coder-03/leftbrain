"""The four network tools: weather, fx_rate, geo, url_check.

They are registered onto the main ``leftbrain`` server by default - one product, one
connection (#100) - and ``register`` is how. The ``leftbrain-external`` command still exists
for a process that should carry only these four; everything it serves is also on ``leftbrain``.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import __version__
from ..mcp_contract import ContractMCPServer
from ..scopes import enforce
from . import tools

NETWORK_INSTRUCTIONS = "Network-backed facts the model cannot know: live weather, exchange rates, geocoding, URL reachability. Results are as-of the moment of the call."


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


@enforce("weather")
def weather(mode: str = "current", place: str | None = None, lat: float | None = None, lon: float | None = None, days: int | None = None, date: str | None = None, end_date: str | None = None, units: str | None = None, tz: str | None = None) -> dict[str, Any]:
    """Live weather for a place or coordinates (Open-Meteo, no key).
    mode: current | forecast (days 1-16) | historical (date, end_date; back to 1940) | summary (one-line + week).
    units: metric | imperial.
    """
    return tools.weather(mode, **_clean(dict(place=place, lat=lat, lon=lon, days=days, date=date, end_date=end_date, units=units, tz=tz)))


@enforce("fx_rate")
def fx_rate(base: str = "USD", to: str | list[str] | None = None, date: str | None = None, amount: float | None = None) -> dict[str, Any]:
    """Exchange rates (ECB reference via Frankfurter). base="USD", to="INR" or ["INR","EUR"], date="2026-01-15" for historical.
    Returns rates_table_for_convert to pass straight into the core convert tool as rates=.
    """
    return tools.fx_rate(**_clean(dict(base=base, to=to, date=date, amount=amount)))


@enforce("geo")
def geo(mode: str = "geocode", place: str | None = None, lat: float | None = None, lon: float | None = None, origin: str | list[Any] | dict[str, Any] | None = None, destination: str | list[Any] | dict[str, Any] | None = None, profile: str | None = None, limit: int | None = None) -> dict[str, Any]:
    """Online geo lookups that need the network.
    mode: geocode (place -> lat/lon/timezone) | reverse (lat/lon -> address) | route (driving
    distance and time between origin and destination)
    """
    return tools.geo(mode, **_clean(dict(place=place, lat=lat, lon=lon, origin=origin, destination=destination, profile=profile, limit=limit)))


@enforce("url_check")
def url_check(url: str, method: str | None = None) -> dict[str, Any]:
    """Actually fetch a URL: status code, redirect chain, final URL, content-type, latency. Use before claiming a link works."""
    return tools.url_check(**_clean(dict(url=url, method=method)))


#: Name and function, in the order the tools are published.
NETWORK_TOOLS: tuple[tuple[str, Any], ...] = (("weather", weather), ("fx_rate", fx_rate), ("geo", geo), ("url_check", url_check))


def register(server: Any) -> None:
    """Publish the four network tools on ``server``, in order."""
    for name, fn in NETWORK_TOOLS:
        server.tool(name=name)(fn)


server = ContractMCPServer(
    "leftbrain-external",
    title="leftbrain external",
    instructions=NETWORK_INSTRUCTIONS,
    version=__version__,
)
register(server)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="leftbrain-external", description="the four network tools alone; `leftbrain` serves them too")
    ap.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    args = ap.parse_args(argv)
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.settings.host = args.host
        server.settings.port = args.port
        server.run(transport=args.transport)


if __name__ == "__main__":
    main()
