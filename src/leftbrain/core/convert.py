"""convert - units, temperature and currency, exactly and unambiguously."""

from __future__ import annotations

import re
from fractions import Fraction
from typing import Any

import pint

from ..contract import Ambiguous, ToolError, fail, ok, tool

MODES = ("units", "temperature", "currency", "auto")

_ureg: pint.UnitRegistry | None = None


def ureg() -> pint.UnitRegistry:
    global _ureg
    if _ureg is None:
        _ureg = pint.UnitRegistry(autoconvert_offset_to_baseunit=False)
        _ureg.define("lakh = 100000 = L")
        _ureg.define("crore = 10000000 = Cr")
    return _ureg


_AMBIGUOUS: dict[str, list[str]] = {
    "ton": ["metric_ton", "short_ton", "long_ton"],
    "tons": ["metric_ton", "short_ton", "long_ton"],
    "gallon": ["us_gallon", "imperial_gallon"],
    "gallons": ["us_gallon", "imperial_gallon"],
    "gal": ["us_gallon", "imperial_gallon"],
    "pint": ["us_pint", "imperial_pint"],
    "pints": ["us_pint", "imperial_pint"],
    "pt": ["us_pint", "imperial_pint", "point"],
    "quart": ["us_quart", "imperial_quart"],
    "quarts": ["us_quart", "imperial_quart"],
    "qt": ["us_quart", "imperial_quart"],
    "cup": ["us_cup", "metric_cup"],
    "cups": ["us_cup", "metric_cup"],
    "oz": ["ounce", "fluid_ounce"],
    "ounce": ["ounce", "fluid_ounce"],
    "ounces": ["ounce", "fluid_ounce"],
    "fl oz": ["us_fluid_ounce", "imperial_fluid_ounce"],
    "fluid_ounce": ["us_fluid_ounce", "imperial_fluid_ounce"],
    "nm": ["nanometer", "nautical_mile"],
    "calorie": ["calorie", "kilocalorie"],
    "calories": ["calorie", "kilocalorie"],
    "cal": ["calorie", "kilocalorie"],
    "tbsp": ["us_tablespoon", "metric_tablespoon"],
    "tablespoon": ["us_tablespoon", "metric_tablespoon"],
    "tsp": ["us_teaspoon", "metric_teaspoon"],
    "teaspoon": ["us_teaspoon", "metric_teaspoon"],
    "gill": ["us_gill", "imperial_gill"],
    "hundredweight": ["short_hundredweight", "long_hundredweight"],
    "cwt": ["short_hundredweight", "long_hundredweight"],
    "b": ["byte", "bit"],
    "kb": ["kilobyte", "kibibyte", "kilobit"],
    "mb": ["megabyte", "mebibyte", "megabit"],
    "gb": ["gigabyte", "gibibyte", "gigabit"],
    "tb": ["terabyte", "tebibyte", "terabit"],
}
# Defaults used when the caller passes assume="common"
_COMMON_DEFAULT = {
    "mile": "mile", "miles": "mile",
    "ton": "metric_ton", "tons": "metric_ton",
    "oz": "ounce", "ounce": "ounce", "ounces": "ounce",
    "calorie": "kilocalorie", "calories": "kilocalorie", "cal": "kilocalorie",
    "gallon": "us_gallon", "gallons": "us_gallon", "gal": "us_gallon",
    "cup": "us_cup", "cups": "us_cup", "pint": "us_pint", "pints": "us_pint",
    "quart": "us_quart", "quarts": "us_quart", "tbsp": "us_tablespoon", "tsp": "us_teaspoon",
    "kb": "kilobyte", "mb": "megabyte", "gb": "gigabyte", "tb": "terabyte", "b": "byte",
    "nm": "nanometer",
}
_ALIASES: dict[str, str] = {
    "kmph": "kilometer/hour", "kph": "kilometer/hour", "km/hr": "kilometer/hour", "kmh": "kilometer/hour",
    "mph": "mile/hour", "m/s": "meter/second", "mps": "meter/second", "knot": "knot", "knots": "knot",
    "sqft": "foot**2", "sq ft": "foot**2", "sq. ft": "foot**2", "square feet": "foot**2", "square foot": "foot**2",
    "sqm": "meter**2", "sq m": "meter**2", "square meter": "meter**2", "square metre": "meter**2", "square meters": "meter**2",
    "sq km": "kilometer**2", "sqkm": "kilometer**2", "square kilometer": "kilometer**2",
    "sq mi": "mile**2", "square mile": "mile**2", "square miles": "mile**2",
    "sq yd": "yard**2", "square yard": "yard**2", "sq in": "inch**2", "square inch": "inch**2",
    "cubic meter": "meter**3", "cubic metre": "meter**3", "cbm": "meter**3", "cum": "meter**3",
    "cubic foot": "foot**3", "cubic feet": "foot**3", "cft": "foot**3", "cu ft": "foot**3",
    "cc": "centimeter**3", "cm3": "centimeter**3", "cubic cm": "centimeter**3", "ml": "milliliter", "mL": "milliliter",
    "ltr": "liter", "litre": "liter", "litres": "liter", "liters": "liter", "l": "liter",
    "lbs": "pound", "lb": "pound", "kgs": "kilogram", "kg": "kilogram", "gm": "gram", "gms": "gram", "g": "gram", "mg": "milligram",
    "tonne": "metric_ton", "tonnes": "metric_ton", "mt": "metric_ton", "metric ton": "metric_ton",
    "c": "degC", "°c": "degC", "celsius": "degC", "centigrade": "degC", "degc": "degC", "deg c": "degC",
    "f": "degF", "°f": "degF", "fahrenheit": "degF", "degf": "degF", "deg f": "degF",
    "k": "kelvin", "kelvin": "kelvin", "°r": "degR", "rankine": "degR",
    "kwh": "kilowatt_hour", "mwh": "megawatt_hour", "wh": "watt_hour", "hp": "horsepower", "bhp": "horsepower",
    "psi": "psi", "bar": "bar", "atm": "atmosphere", "mmhg": "mmHg", "kpa": "kilopascal", "mpa": "megapascal", "pa": "pascal",
    "in": "inch", "inch": "inch", "inches": "inch", "ft": "foot", "feet": "foot", "yd": "yard", "yards": "yard",
    "mi": "mile", "km": "kilometer", "m": "meter", "cm": "centimeter", "mm": "millimeter", "um": "micrometer", "micron": "micrometer",
    "nmi": "nautical_mile", "nautical mile": "nautical_mile", "nautical miles": "nautical_mile",
    "kib": "kibibyte", "mib": "mebibyte", "gib": "gibibyte", "tib": "tebibyte", "pib": "pebibyte",
    "kbps": "kilobit/second", "mbps": "megabit/second", "gbps": "gigabit/second", "kb/s": "kilobyte/second", "mb/s": "megabyte/second",
    "bit": "bit", "bits": "bit", "byte": "byte", "bytes": "byte", "pb": "petabyte",
    "sec": "second", "secs": "second", "s": "second", "min": "minute", "mins": "minute", "hr": "hour", "hrs": "hour", "h": "hour",
    "day": "day", "days": "day", "wk": "week", "week": "week", "weeks": "week", "fortnight": "fortnight",
    "month": "month", "months": "month", "yr": "year", "year": "year", "years": "year",
    "acre": "acre", "acres": "acre", "hectare": "hectare", "hectares": "hectare", "ha": "hectare",
    "bigha": "bigha", "katha": "katha", "cent": "cent_area", "ground": "ground", "guntha": "guntha", "ankanam": "ankanam",
    "rpm": "revolutions_per_minute", "deg": "degree", "degree": "degree", "degrees": "degree", "rad": "radian", "radian": "radian",
    "mpg": "mile/gallon_us", "kmpl": "kilometer/liter", "km/l": "kilometer/liter", "l/100km": "liter/(100*kilometer)",
    "lakh": "lakh", "lakhs": "lakh", "crore": "crore", "crores": "crore",
}
_INDIAN_LAND = {
    "cent_area": "40.468564224 meter**2",
    "ground": "222.967 meter**2",
    "guntha": "101.171 meter**2",
    "ankanam": "6.6890 meter**2",
    "katha": "66.89 meter**2",
    "bigha": "1337.8 meter**2",
}
_TEMP_UNITS = {
    "degC", "degF", "kelvin", "degR", "delta_degC", "delta_degF",
    "degree_Celsius", "degree_Fahrenheit", "degree_Rankine", "degree_Reaumur",
    "delta_degree_Celsius", "delta_degree_Fahrenheit",
}
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _define_land(reg: pint.UnitRegistry) -> None:
    for name, defn in _INDIAN_LAND.items():
        if name not in reg:
            reg.define(f"{name} = {defn}")


def _norm_unit(u: Any, assume: str | None, field: str, assumptions: list[str]) -> str:
    if u is None:
        raise ToolError(f"'{field}' is required")
    s = str(u).strip()
    if not s:
        raise ToolError(f"'{field}' is empty")
    key = s.lower().replace("²", "**2").replace("³", "**3").replace("^", "**")
    key = re.sub(r"\s+", " ", key)
    if key in _AMBIGUOUS:
        if assume == "common" and key in _COMMON_DEFAULT:
            assumptions.append(f"'{s}' read as {_COMMON_DEFAULT[key]}")
            return _COMMON_DEFAULT[key]
        raise Ambiguous(
            f"'{s}' is ambiguous; specify which one (or pass assume='common')",
            field=field,
            options=_AMBIGUOUS[key],
        )
    if key in _ALIASES:
        return _ALIASES[key]
    if s in ("F", "C", "K"):
        return {"F": "degF", "C": "degC", "K": "kelvin"}[s]
    return s.replace("²", "**2").replace("³", "**3").replace("^", "**")


def _parse_value(v: Any) -> Fraction:
    if isinstance(v, bool):
        raise ToolError("value must be a number")
    if isinstance(v, int):
        return Fraction(v)
    if isinstance(v, float):
        return Fraction(repr(v))
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("_", "")
        try:
            return Fraction(s)
        except ValueError:
            raise ToolError(f"value {v!r} is not a number") from None
    raise ToolError(f"value {v!r} is not a number")


def _sig(x: float, precision: int) -> float:
    if x == 0 or x != x or x in (float("inf"), float("-inf")):
        return x
    from math import floor, log10

    return round(x, precision - int(floor(log10(abs(x)))) - 1)


def _fmt(x: float, precision: int) -> str:
    r = _sig(x, precision)
    s = f"{r:.{precision}g}"
    if "e" in s and abs(r) >= 1e-4 and abs(r) < 1e15:
        s = f"{r:.{precision}f}".rstrip("0").rstrip(".")
    return s


def _units(p: dict[str, Any]) -> dict[str, Any]:
    reg = ureg()
    _define_land(reg)
    assumptions: list[str] = []
    warnings: list[str] = []
    assume = p.get("assume")
    val = _parse_value(p.get("value", 1))
    src = _norm_unit(p.get("from_unit") or p.get("from"), assume, "from_unit", assumptions)
    dst = _norm_unit(p.get("to_unit") or p.get("to"), assume, "to_unit", assumptions)
    precision = int(p.get("precision", 10))
    try:
        u_src, u_dst = reg.Unit(src), reg.Unit(dst)
    except Exception as e:
        raise ToolError(f"unknown unit: {e}") from None
    is_temp = str(u_src) in _TEMP_UNITS or str(u_dst) in _TEMP_UNITS
    if is_temp:
        if str(u_src) not in _TEMP_UNITS or str(u_dst) not in _TEMP_UNITS:
            raise ToolError(f"cannot convert {src} to {dst}: only temperature-to-temperature is meaningful")
        try:
            if p.get("delta"):
                src_d = "delta_" + src if src in ("degC", "degF") else src
                dst_d = "delta_" + dst if dst in ("degC", "degF") else dst
                q = reg.Quantity(float(val), src_d).to(dst_d)
                assumptions.append("temperature difference (delta), not an absolute reading")
            else:
                q = reg.Quantity(float(val), src).to(dst)
                assumptions.append("absolute temperature (pass delta=true for a temperature difference)")
        except pint.errors.PintError as e:
            raise ToolError(f"temperature conversion failed: {e}") from None
        out_val = float(q.magnitude)
    else:
        try:
            q = (val.numerator * u_src / val.denominator).to(u_dst)
        except pint.errors.DimensionalityError as e:
            raise ToolError(f"cannot convert {src} to {dst}: {e}") from None
        out_val = float(q.magnitude)
        if src in ("mile", "miles") or dst in ("mile", "miles"):
            assumptions.append("statute mile (1609.344 m)")
        if any(k in (src, dst) for k in ("kilobyte", "megabyte", "gigabyte", "terabyte", "petabyte")):
            assumptions.append("SI bytes (1 kB = 1000 B); use KiB/MiB/GiB for 1024-based")
        if any(k in (src, dst) for k in ("month", "year")):
            assumptions.append("month = 1/12 Julian year (30.4375 days); year = 365.25 days")
    out: dict[str, Any] = {
        "value": _sig(out_val, precision),
        "unit": str(u_dst),
        "display": f"{_fmt(out_val, precision)} {u_dst:~P}",
        "from": {"value": float(val), "unit": str(u_src)},
    }
    if not is_temp:
        try:
            factor = (1 * u_src).to(u_dst).magnitude
            out["factor"] = _sig(float(factor), 12)
            fr = Fraction(str(factor)).limit_denominator(10**9)
            if abs(float(fr) - factor) < 1e-12 * max(1, abs(factor)):
                out["factor_exact"] = f"{fr.numerator}/{fr.denominator}" if fr.denominator != 1 else str(fr.numerator)
        except Exception:  # pragma: no cover
            pass
    return ok(out, assumptions=assumptions, warnings=warnings)


def _currency(p: dict[str, Any]) -> dict[str, Any]:
    val = _parse_value(p.get("value", 1))
    src = str(p.get("from_unit") or p.get("from") or "").strip().upper()
    dst = str(p.get("to_unit") or p.get("to") or "").strip().upper()
    if not (_CURRENCY_RE.match(src) and _CURRENCY_RE.match(dst)):
        raise ToolError("currency codes must be 3-letter ISO codes like USD, INR, EUR")
    rates = p.get("rates")
    rate = p.get("rate")
    assumptions: list[str] = []
    if rate is not None:
        r = _parse_value(rate)
        assumptions.append(f"used caller-supplied rate 1 {src} = {float(r)} {dst}")
    elif isinstance(rates, dict):
        up = {str(k).upper(): _parse_value(v) for k, v in rates.items()}
        base = str(p.get("base") or "").upper()
        if src in up and dst in up:
            r = up[dst] / up[src]
            assumptions.append(f"rates table interpreted as amounts per 1 unit of base{(' ' + base) if base else ''}")
        elif base == src and dst in up:
            r = up[dst]
        elif base == dst and src in up:
            r = 1 / up[src]
        else:
            raise ToolError(f"rates table lacks {src} and/or {dst}")
    else:
        return fail(
            "needs_rates",
            "currency conversion needs live rates: fetch them with leftbrain-external fx_rate "
            "(or pass rates={'USD':1,'INR':83.5,...} / rate=83.5)",
            needs={"field": "rates", "options": ["pass 'rate' (1 from = rate to)", "pass 'rates' table", "call fx_rate first"]},
        )
    decimals = int(p.get("decimals", 2))
    amount = val * r
    q = Fraction(1, 10**decimals)
    rounded = round(amount / q) * q
    return ok(
        {
            "value": float(rounded),
            "value_exact": f"{amount.numerator}/{amount.denominator}" if amount.denominator != 1 else str(amount.numerator),
            "unit": dst,
            "display": f"{float(rounded):,.{decimals}f} {dst}",
            "from": {"value": float(val), "unit": src},
            "rate": float(r),
            "as_of": p.get("date"),
        },
        assumptions=assumptions + [f"rounded half-up to {decimals} decimals"],
    )


@tool
def convert(mode: str = "auto", **params: Any) -> dict[str, Any]:
    """Convert units, temperatures or currencies. Refuses ambiguous units."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    src = str(p.get("from_unit") or p.get("from") or "")
    dst = str(p.get("to_unit") or p.get("to") or "")
    if mode == "currency" or (mode == "auto" and _CURRENCY_RE.match(src.strip().upper()) and _CURRENCY_RE.match(dst.strip().upper()) and src.strip().upper() not in ("DEG", "RAD") and len(src.strip()) == 3 and src.strip().isupper()):
        return _currency(p)
    return _units(p)
