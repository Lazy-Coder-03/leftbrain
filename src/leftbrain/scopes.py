"""Per-key tool scopes: which tools, and which of their modes, a key may call.

A scope is a whitelist. ``None`` is no scope at all - every tool, every mode - and is what
every key has unless its owner narrows it. Stored on the key as JSON::

    {"tools": {"math": null, "holidays": ["list", "check"], "weather": null}}

A tool mapped to ``null`` allows all of its modes; a list allows only those. The same
shape is accepted back by :func:`parse_scope`, along with the CLI's text form
(``"math,datetime,holidays:list+check"``) and the dashboard's checkbox values
(``["math", "holidays:list", "holidays:check"]``).

Enforcement happens twice. :func:`enforce` wraps every MCP tool and answers a call the
key's scope does not cover with the contract's ``forbidden`` error, reading the scope
from :data:`current_scope`, which the HTTP auth middleware sets for the request. The
middleware also trims ``tools/list`` so a scoped key sees only what it may call.
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .contract import fail


def _catalogue() -> dict[str, tuple[str, ...]]:
    """Every tool of the core and external servers, in the order the servers publish them, with its modes."""
    from .core import collections_, datetimex, encode, geo_offline, holidays_, mathx, random_
    from .core import color as color_mod
    from .core import convert as convert_mod
    from .core import finance as finance_mod
    from .core import numbers as numbers_mod
    from .core import scale as scale_mod
    from .core import text as text_mod
    from .core import validate as validate_mod
    from .external import tools as external_tools

    return {
        "math": tuple(mathx.MODES),
        "datetime": tuple(datetimex.MODES),
        "scale": tuple(scale_mod.MODES),
        "convert": tuple(convert_mod.MODES),
        "holidays": tuple(holidays_.MODES),
        "numbers": tuple(numbers_mod.MODES),
        "finance": tuple(finance_mod.MODES),
        "text": tuple(text_mod.MODES),
        "collections": tuple(collections_.MODES),
        "validate": tuple(validate_mod.MODES),
        "random": tuple(random_.MODES),
        "geo_offline": tuple(geo_offline.MODES),
        "encode": tuple(encode.MODES),
        "color": tuple(color_mod.MODES),
        "weather": tuple(external_tools.WEATHER_MODES),
        "fx_rate": (),
        "geo": tuple(external_tools.GEO_MODES),
        "url_check": (),
    }


CATALOGUE: dict[str, tuple[str, ...]] = _catalogue()


@dataclass(frozen=True)
class Scope:
    """The tools a key may call; a tool mapped to ``None`` may use every mode, else only the listed ones."""

    tools: dict[str, tuple[str, ...] | None]

    def allows(self, tool: str, mode: str | None) -> bool:
        if tool not in self.tools:
            return False
        modes = self.tools[tool]
        return modes is None or mode in modes

    def to_dict(self) -> dict[str, list[str] | None]:
        return {t: (list(m) if m is not None else None) for t, m in self.tools.items()}

    def to_json(self) -> str:
        return json.dumps({"tools": self.to_dict()}, separators=(",", ":"))

    def summary(self) -> str:
        """Short enough for a table cell: ``holidays: list, check`` for one tool, ``3 tools`` for more."""
        if len(self.tools) == 1:
            (tool, modes), = self.tools.items()
            return tool if modes is None else f"{tool}: {', '.join(modes)}"
        return f"{len(self.tools)} tools"

    def listing(self) -> str:
        """Every tool, with its modes in brackets when they are limited: ``math, holidays (list, check)``."""
        return ", ".join(t if m is None else f"{t} ({', '.join(m)})" for t, m in self.tools.items())


def summarize(scope: Scope | None) -> str:
    return "all tools" if scope is None else scope.summary()


def _from_map(raw: dict[str, Any], *, strict: bool) -> Scope | None:
    tools: dict[str, tuple[str, ...] | None] = {}
    for tool, modes in raw.items():
        if strict and tool not in CATALOGUE:
            raise ValueError(f"unknown tool '{tool}'; tools: {', '.join(CATALOGUE)}")
        if modes is None:
            tools[tool] = None
            continue
        if not isinstance(modes, (list, tuple)) or not all(isinstance(m, str) for m in modes):
            raise ValueError(f"'{tool}' must map to null or a list of mode names")
        known = CATALOGUE.get(tool, ())
        if strict:
            if not known:
                raise ValueError(f"'{tool}' has no modes to choose from; name the tool on its own")
            for m in modes:
                if m not in known:
                    raise ValueError(f"unknown mode '{m}' for {tool}; modes: {', '.join(known)}")
        ordered = tuple(m for m in known if m in modes) if known else tuple(dict.fromkeys(modes))
        if not ordered:
            raise ValueError(f"'{tool}' names no modes; leave it out, or name at least one")
        tools[tool] = None if known and set(ordered) == set(known) else ordered
    if not tools:
        raise ValueError("a scope needs at least one tool")
    if strict and all(t in tools and tools[t] is None for t in CATALOGUE) and len(tools) == len(CATALOGUE):
        return None  # every tool with every mode is no restriction at all
    return Scope(tools)


def _from_text(text: str) -> dict[str, Any]:
    """``"math,datetime,holidays:list+check"`` -> the map form."""
    raw: dict[str, Any] = {}
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        tool, sep, modes = item.partition(":")
        tool = tool.strip()
        if sep:
            listed = [m.strip() for m in modes.split("+") if m.strip()]
            if not listed:
                raise ValueError(f"'{tool}:' names no modes; write {tool} alone for every mode, or {tool}:mode+mode")
            if tool in raw and raw[tool] is None:
                continue  # already named on its own: every mode
            raw[tool] = raw.get(tool, []) + listed
        else:
            raw[tool] = None
    return raw


def _from_items(items: list[str]) -> dict[str, Any]:
    """The dashboard's checkbox values: ``tool`` or ``tool:mode``. A tool with no mode entries means every mode."""
    raw: dict[str, Any] = {}
    modes_of: dict[str, list[str]] = {}
    for item in items:
        tool, sep, mode = str(item).partition(":")
        if sep:
            modes_of.setdefault(tool, []).append(mode)
        else:
            raw.setdefault(tool, None)
    for tool, modes in modes_of.items():
        raw[tool] = modes
    return raw


def parse_scope(value: Any, *, strict: bool = True) -> Scope | None:
    """A ``Scope`` from any accepted form, or ``None`` for "every tool".

    Accepts ``None``/``""``/``"all"``, the stored JSON (as text or dict, wrapped in
    ``{"tools": ...}`` or bare), the CLI text form and a list of ``tool``/``tool:mode``
    strings. With ``strict`` (the default) every tool and mode is checked against the
    catalogue and the offender is named; the store loads with ``strict=False`` so a scope
    that names a tool this build no longer has still loads (and still allows nothing extra).
    """
    if value is None:
        return None
    if isinstance(value, Scope):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() == "all":
            return None
        if text.startswith("{"):
            value = json.loads(text)
        else:
            return _from_map(_from_text(text), strict=strict)
    if isinstance(value, (list, tuple)):
        if not value:
            raise ValueError("a scope needs at least one tool")
        return _from_map(_from_items(list(value)), strict=strict)
    if not isinstance(value, dict):
        raise ValueError("a scope is a mapping of tool names to modes")
    if "tools" in value and all(k == "tools" for k in value):
        inner = value["tools"]
        if inner is None:
            return None
        if not isinstance(inner, dict):
            raise ValueError("'tools' must map tool names to null or a list of modes")
        return _from_map(inner, strict=strict)
    return _from_map(value, strict=strict)


#: The calling key's scope for the current request; ``None`` when the key (or the auth mode) has none.
current_scope: contextvars.ContextVar[Scope | None] = contextvars.ContextVar("leftbrain_scope", default=None)


def denial(scope: Scope, tool: str, mode: str | None) -> dict[str, Any] | None:
    """The contract error a call outside ``scope`` gets, or ``None`` when it is allowed."""
    if tool not in scope.tools:
        return fail("forbidden", f"this key may not call {tool}; allowed: {scope.listing()}")
    modes = scope.tools[tool]
    if modes is None or mode in modes:
        return None
    return fail("forbidden", f"this key may not call {tool} mode '{mode}'; allowed: {', '.join(modes)}")


def enforce(tool_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate an MCP tool wrapper so a call outside the request's scope returns ``forbidden`` instead of running.

    Sits under ``@server.tool(...)``. ``functools.wraps`` keeps the name, docstring and
    signature the server (and the docs generator) read; the ``mode`` the core function
    would see - including its default - is what the scope is checked against.
    """

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        sig = inspect.signature(fn)
        has_mode = "mode" in sig.parameters
        default_mode = sig.parameters["mode"].default if has_mode else None

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            scope = current_scope.get()
            if scope is not None:
                mode = default_mode
                if has_mode:
                    bound = sig.bind_partial(*args, **kwargs)
                    mode = bound.arguments.get("mode", default_mode)
                denied = denial(scope, tool_name, mode)
                if denied is not None:
                    return denied
            return fn(*args, **kwargs)

        return wrapper

    return decorate


def allowed_tools(scope: Scope | None, names: list[str]) -> list[str]:
    """``names`` trimmed to what ``scope`` permits, order kept; every name when there is no scope."""
    if scope is None:
        return names
    return [n for n in names if n in scope.tools]
