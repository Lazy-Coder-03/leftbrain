"""convert - units, temperature, currency, fuel economy, cooking measures and sizes, exactly and unambiguously."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from typing import Any

import pint

from ..contract import Ambiguous, TooLarge, ToolError, Unsupported, fail, ok, tool
from .numbers import _dec_str, parse_number

MODES = ("units", "temperature", "currency", "fuel_economy", "cooking", "sizes", "auto")

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


#: Absolute zero in each scale a reading can be given in. A *difference* may of course be
#: colder than this, which is why `delta=true` skips the check (#28 SS2c).
_ABSOLUTE_ZERO = {"degC": -273.15, "degree_Celsius": -273.15, "degF": -459.67, "degree_Fahrenheit": -459.67, "kelvin": 0.0, "degR": 0.0, "degree_Rankine": 0.0}


def _check_absolute_zero(value: float, unit: str) -> None:
    floor = _ABSOLUTE_ZERO.get(unit)
    if floor is None or value >= floor:
        return
    raise ToolError(
        f"{value} {unit} is below absolute zero ({floor} {unit}), so it is not a temperature",
        details={"value": value, "unit": unit, "absolute_zero": floor},
        hint="Pass delta=true if this is a temperature *difference* rather than a reading.",
    )


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


#: Pint converts through `float`, so a magnitude past its range is an `OverflowError`
#: rather than an answer. Refused with the number named instead (#28 SS4).
MAX_MAGNITUDE = Fraction(10) ** 300


def _check_magnitude(value: Fraction) -> None:
    if abs(value) <= MAX_MAGNITUDE:
        return
    raise TooLarge(
        "the value is too large to convert; unit conversion works up to about 1e300",
        details={"limit": "1e300"},
        hint="Scale the number down and convert the smaller magnitude.",
    )


def _parse_value(v: Any) -> Fraction:
    if isinstance(v, bool):
        raise ToolError("value must be a number")
    if isinstance(v, int):
        return Fraction(v)
    if isinstance(v, float):
        # JSON has no infinity, but a client that writes 1e400 hands us one, and
        # `Fraction('inf')` is a bare ValueError (#28 SS4).
        if v != v or v in (float("inf"), float("-inf")):
            raise ToolError(
                "value is infinite or not a number; a conversion needs a finite value",
                details={"value": str(v)},
                hint="Numbers above about 1e308 cannot be written as a JSON number - pass a string instead.",
            )
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
    _check_magnitude(val)
    src = _norm_unit(p.get("from_unit"), assume, "from_unit", assumptions)
    dst = _norm_unit(p.get("to_unit"), assume, "to_unit", assumptions)
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
                _check_absolute_zero(float(val), src)
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
    src = str(p.get("from_unit") or "").strip().upper()
    dst = str(p.get("to_unit") or "").strip().upper()
    if not (_CURRENCY_RE.match(src) and _CURRENCY_RE.match(dst)):
        raise ToolError("currency codes must be 3-letter ISO codes like USD, INR, EUR")
    rates = p.get("rates")
    rate = p.get("rate")
    assumptions: list[str] = []
    if rate is not None:
        r = _parse_value(rate)
        if r <= 0:
            raise ToolError(
                f"rate {float(r)} is not a exchange rate; a rate is how many {dst} one {src} buys, which is positive",
                details={"rate": float(r)},
                hint="Check the direction of the conversion rather than negating the rate.",
            )
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


# --------------------------------------------------------------------------- #
# fuel economy, cooking measures, sizes - Decimal throughout
# --------------------------------------------------------------------------- #

_PREC = 40


def _decimals(p: dict[str, Any], default: int) -> int:
    decimals = int(p.get("decimals", default))
    if not 0 <= decimals <= 10:
        raise ToolError("decimals must be between 0 and 10")
    return decimals


def _quant(d: Decimal, decimals: int) -> Decimal:
    return d.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def _jsonnum(d: Decimal) -> int | float:
    return int(d) if d == d.to_integral() else float(d)


def _key(u: Any, field: str) -> str:
    if u is None:
        raise ToolError(f"'{field}' is required")
    s = re.sub(r"\s+", " ", str(u).strip().lower())
    if not s:
        raise ToolError(f"'{field}' is empty")
    return s


def _positive_value(p: dict[str, Any]) -> Decimal:
    val, _ = parse_number(p.get("value", 1))
    if val <= 0:
        raise ToolError("value must be greater than zero")
    return val


# --- fuel economy -------------------------------------------------------------

_MILE_KM = Decimal("1.609344")  # international mile, exact
_US_GALLON_L = Decimal("3.785411784")  # US liquid gallon, exact
_UK_GALLON_L = Decimal("4.54609")  # imperial gallon, exact
_FUEL_UNITS = ("mpg_us", "mpg_uk", "km_per_l", "l_per_100km")
_FUEL_ALIASES: dict[str, str] = {
    "mpg_us": "mpg_us", "mpg (us)": "mpg_us", "mpg us": "mpg_us", "us mpg": "mpg_us", "mpgus": "mpg_us",
    "mi/gal (us)": "mpg_us", "miles per us gallon": "mpg_us", "miles per gallon (us)": "mpg_us",
    "mpg_uk": "mpg_uk", "mpg (uk)": "mpg_uk", "mpg uk": "mpg_uk", "uk mpg": "mpg_uk", "mpguk": "mpg_uk",
    "mpg_imperial": "mpg_uk", "mpg (imperial)": "mpg_uk", "imperial mpg": "mpg_uk", "mpg imperial": "mpg_uk",
    "mi/gal (uk)": "mpg_uk", "miles per imperial gallon": "mpg_uk", "miles per gallon (uk)": "mpg_uk",
    "km_per_l": "km_per_l", "km/l": "km_per_l", "kmpl": "km_per_l", "kml": "km_per_l", "kpl": "km_per_l",
    "km per l": "km_per_l", "km per litre": "km_per_l", "km per liter": "km_per_l",
    "kilometres per litre": "km_per_l", "kilometers per liter": "km_per_l",
    "l_per_100km": "l_per_100km", "l/100km": "l_per_100km", "l/100 km": "l_per_100km", "l per 100 km": "l_per_100km",
    "l per 100km": "l_per_100km", "litres per 100 km": "l_per_100km", "liters per 100 km": "l_per_100km",
    "litres/100km": "l_per_100km", "liters/100km": "l_per_100km", "lp100km": "l_per_100km", "l/100": "l_per_100km",
}
_FUEL_AMBIGUOUS = {"mpg", "mi/gal", "miles per gallon", "mile per gallon"}
_FUEL_LABEL = {"mpg_us": "mpg (US)", "mpg_uk": "mpg (UK)", "km_per_l": "km/L", "l_per_100km": "L/100 km"}


def _fuel_unit(u: Any, field: str) -> str:
    key = _key(u, field)
    if key in _FUEL_AMBIGUOUS:
        raise Ambiguous(f"'{u}' is per US gallon or per imperial gallon - a 20% difference; say which", field=field, options=["mpg_us", "mpg_uk"])
    if key not in _FUEL_ALIASES:
        raise ToolError(f"'{u}' is not a fuel-economy unit; use one of {', '.join(_FUEL_UNITS)}")
    return _FUEL_ALIASES[key]


def _fuel_economy(p: dict[str, Any]) -> dict[str, Any]:
    src = _fuel_unit(p.get("from_unit"), "from_unit")
    dst = _fuel_unit(p.get("to_unit"), "to_unit")
    val = _positive_value(p)
    decimals = _decimals(p, 2)
    assumptions: list[str] = []
    with localcontext() as ctx:
        ctx.prec = _PREC
        km_per_l = {
            "mpg_us": lambda v: v * _MILE_KM / _US_GALLON_L,
            "mpg_uk": lambda v: v * _MILE_KM / _UK_GALLON_L,
            "km_per_l": lambda v: v,
            "l_per_100km": lambda v: Decimal(100) / v,
        }[src](val)
        out_val = {
            "mpg_us": lambda k: k * _US_GALLON_L / _MILE_KM,
            "mpg_uk": lambda k: k * _UK_GALLON_L / _MILE_KM,
            "km_per_l": lambda k: k,
            "l_per_100km": lambda k: Decimal(100) / k,
        }[dst](km_per_l)
    if "mpg_us" in (src, dst):
        assumptions.append(f"US gallon = {_US_GALLON_L} L; mile = {_MILE_KM} km")
    if "mpg_uk" in (src, dst):
        assumptions.append(f"imperial gallon = {_UK_GALLON_L} L; mile = {_MILE_KM} km")
    if (src == "l_per_100km") != (dst == "l_per_100km"):
        assumptions.append(
            "L/100 km is an inverse quantity (100 ÷ km/L): doubling the mpg halves the L/100 km, but equal mpg steps are not equal fuel savings - "
            "30→40 mpg (US) saves 1.96 L/100 km, 40→50 mpg saves only 1.18"
        )
    rounded = _quant(out_val, decimals)
    assumptions.append(f"rounded half-up to {decimals} decimals")
    return ok(
        {
            "value": _jsonnum(rounded),
            "unit": dst,
            "display": f"{_dec_str(rounded)} {_FUEL_LABEL[dst]}",
            "from": {"value": _jsonnum(val), "unit": src},
            "km_per_l": _jsonnum(_quant(km_per_l, 6)),
        },
        assumptions=assumptions,
    )


# --- cooking ------------------------------------------------------------------

_US_FL_OZ_ML = Decimal("29.5735295625")  # exact
_UK_FL_OZ_ML = Decimal("28.4130625")  # exact
_OZ_G = Decimal("28.349523125")  # avoirdupois ounce, exact
_LB_G = Decimal("453.59237")  # exact
#: ml per cup / tablespoon / teaspoon / fluid ounce, by cup system.
_CUP_SYSTEMS: dict[str, dict[str, Decimal]] = {
    "us": {"cup": Decimal(240), "tbsp": Decimal(15), "tsp": Decimal(5), "fl_oz": _US_FL_OZ_ML},
    "metric": {"cup": Decimal(250), "tbsp": Decimal(15), "tsp": Decimal(5), "fl_oz": _UK_FL_OZ_ML},
    "uk": {"cup": Decimal(250), "tbsp": Decimal(15), "tsp": Decimal(5), "fl_oz": _UK_FL_OZ_ML},
    "au": {"cup": Decimal(250), "tbsp": Decimal(20), "tsp": Decimal(5), "fl_oz": _UK_FL_OZ_ML},
}
_CUP_NOTES = {
    "us": "US cup = 240 ml (the FDA/legal cup; the customary cup is 236.6 ml), tbsp = 15 ml, tsp = 5 ml, fl oz = 29.5735295625 ml",
    "metric": "metric cup = 250 ml, tbsp = 15 ml, tsp = 5 ml, fl oz = 28.4130625 ml (imperial)",
    "uk": "UK cup = 250 ml (modern UK recipes use the metric cup; the old imperial cup of 284 ml is not used), tbsp = 15 ml, tsp = 5 ml, fl oz = 28.4130625 ml (imperial)",
    "au": "Australian cup = 250 ml, tbsp = 20 ml (not 15), tsp = 5 ml, fl oz = 28.4130625 ml (imperial)",
}
_COOKING_VOLUME = ("cup", "tbsp", "tsp", "ml", "l", "fl_oz")
_COOKING_MASS = ("g", "kg", "oz_weight", "lb")
_COOKING_ALIASES: dict[str, str] = {
    "cup": "cup", "cups": "cup", "c": "cup",
    "tbsp": "tbsp", "tbs": "tbsp", "tbl": "tbsp", "tablespoon": "tbsp", "tablespoons": "tbsp",
    "tsp": "tsp", "teaspoon": "tsp", "teaspoons": "tsp",
    "ml": "ml", "millilitre": "ml", "milliliter": "ml", "millilitres": "ml", "milliliters": "ml",
    "l": "l", "litre": "l", "liter": "l", "litres": "l", "liters": "l",
    "fl_oz": "fl_oz", "fl oz": "fl_oz", "fl. oz": "fl_oz", "fl.oz": "fl_oz", "floz": "fl_oz", "fluid ounce": "fl_oz", "fluid ounces": "fl_oz",
    "g": "g", "gram": "g", "grams": "g", "gm": "g", "gms": "g",
    "kg": "kg", "kilogram": "kg", "kilograms": "kg", "kgs": "kg",
    "oz_weight": "oz_weight", "oz weight": "oz_weight", "oz (weight)": "oz_weight", "ounce (weight)": "oz_weight", "ounces (weight)": "oz_weight",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
}
_COOKING_AMBIGUOUS = {"oz", "ounce", "ounces"}
#: Grams per 240 ml US cup, typical published values (spooned-and-levelled flour, packed brown sugar).
_DENSITY_G_PER_CUP: dict[str, int] = {
    "water": 240,
    "milk": 245,
    "cream": 240,
    "yogurt": 245,
    "oil": 218,
    "honey": 340,
    "maple_syrup": 320,
    "flour": 120,
    "cornstarch": 128,
    "cocoa": 85,
    "sugar": 200,
    "brown_sugar": 220,
    "powdered_sugar": 120,
    "butter": 227,
    "peanut_butter": 270,
    "rice": 185,
    "oats": 90,
    "salt": 288,
}
_INGREDIENT_ALIASES: dict[str, str] = {
    "all-purpose flour": "flour", "all purpose flour": "flour", "ap flour": "flour", "plain flour": "flour", "wheat flour": "flour", "maida": "flour",
    "granulated sugar": "sugar", "white sugar": "sugar",
    "packed brown sugar": "brown_sugar", "brown sugar (packed)": "brown_sugar",
    "icing sugar": "powdered_sugar", "confectioners sugar": "powdered_sugar", "confectioners' sugar": "powdered_sugar", "confectioner's sugar": "powdered_sugar",
    "vegetable oil": "oil", "olive oil": "oil", "cooking oil": "oil", "canola oil": "oil", "sunflower oil": "oil",
    "cocoa powder": "cocoa", "unsweetened cocoa": "cocoa",
    "rolled oats": "oats", "old-fashioned oats": "oats", "oatmeal": "oats",
    "white rice": "rice", "uncooked rice": "rice", "long grain rice": "rice", "basmati": "rice", "basmati rice": "rice",
    "table salt": "salt",
    "heavy cream": "cream", "whipping cream": "cream", "double cream": "cream",
    "greek yogurt": "yogurt", "yoghurt": "yogurt", "curd": "yogurt",
    "corn starch": "cornstarch", "cornflour": "cornstarch", "corn flour": "cornstarch",
    "whole milk": "milk", "skim milk": "milk",
}


def _cooking_unit(u: Any, field: str) -> str:
    key = _key(u, field)
    if key in _COOKING_AMBIGUOUS:
        raise Ambiguous(f"'{u}' is a weight or a fluid measure; say which", field=field, options=["oz_weight", "fl_oz"])
    if key not in _COOKING_ALIASES:
        raise ToolError(f"'{u}' is not a cooking measure; use one of {', '.join(_COOKING_VOLUME + _COOKING_MASS)}")
    return _COOKING_ALIASES[key]


def _ingredient(p: dict[str, Any]) -> str:
    raw = p.get("ingredient")
    options = sorted(_DENSITY_G_PER_CUP)
    if raw is None or not str(raw).strip():
        raise Ambiguous("mass <-> volume depends on the ingredient; name one from the built-in density table", field="ingredient", options=options)
    key = re.sub(r"\s+", " ", str(raw).strip().lower())
    key = _INGREDIENT_ALIASES.get(key, key)
    key = re.sub(r"[\s-]+", "_", key)
    if key not in _DENSITY_G_PER_CUP:
        raise Ambiguous(f"no density on file for '{raw}'; pick the closest from the table or measure by weight", field="ingredient", options=options)
    return key


def _cooking(p: dict[str, Any]) -> dict[str, Any]:
    src = _cooking_unit(p.get("from_unit"), "from_unit")
    dst = _cooking_unit(p.get("to_unit"), "to_unit")
    val, _ = parse_number(p.get("value", 1))
    if val < 0:
        raise ToolError("value must not be negative")
    decimals = _decimals(p, 2)
    system = str(p.get("cup") or "us").strip().lower()
    if system not in _CUP_SYSTEMS:
        raise ToolError(f"cup must be one of {', '.join(_CUP_SYSTEMS)}")
    assumptions: list[str] = []
    warnings: list[str] = []
    ml_per = {**_CUP_SYSTEMS[system], "ml": Decimal(1), "l": Decimal(1000)}
    g_per = {"g": Decimal(1), "kg": Decimal(1000), "oz_weight": _OZ_G, "lb": _LB_G}
    if {src, dst} & set(_CUP_SYSTEMS[system]):
        assumptions.append(_CUP_NOTES[system] + ("" if p.get("cup") else " - pass cup=metric|uk|au for a 250 ml cup"))
    src_vol, dst_vol = src in _COOKING_VOLUME, dst in _COOKING_VOLUME
    extra: dict[str, Any] = {}
    with localcontext() as ctx:
        ctx.prec = _PREC
        if src_vol == dst_vol:
            table = ml_per if src_vol else g_per
            out_val = val * table[src] / table[dst]
            if p.get("ingredient"):
                assumptions.append("ingredient not needed for a same-kind conversion; ignored")
        else:
            ing = _ingredient(p)
            grams = _DENSITY_G_PER_CUP[ing]
            density = Decimal(grams) / Decimal(240)
            if src_vol:
                out_val = val * ml_per[src] * density / g_per[dst]
            else:
                out_val = val * g_per[src] / density / ml_per[dst]
            assumptions.append(f"{ing}: {grams} g per 240 ml (US cup) = {_dec_str(_quant(density, 4))} g/ml")
            warnings.append("ingredient densities are approximate: flour and sugar vary 10-20% with how they are scooped, packed or sifted; weigh when it matters")
            extra = {"ingredient": ing, "density_g_per_ml": _jsonnum(_quant(density, 4))}
        if "oz_weight" in (src, dst):
            assumptions.append(f"avoirdupois ounce = {_OZ_G} g")
        if "lb" in (src, dst):
            assumptions.append(f"pound = {_LB_G} g")
    rounded = _quant(out_val, decimals)
    assumptions.append(f"rounded half-up to {decimals} decimals")
    out: dict[str, Any] = {"value": _jsonnum(rounded), "unit": dst, "display": f"{_dec_str(rounded)} {dst}", "from": {"value": _jsonnum(val), "unit": src}, **extra}
    if {src, dst} & set(_CUP_SYSTEMS[system]):
        out["cup_system"] = system
    return ok(out, assumptions=assumptions, warnings=warnings)


# --- sizes --------------------------------------------------------------------

_SHOE_CHART = "generic adult shoe chart (US men = UK + 1, US women = US men + 1.5, EU and foot length in cm from the common athletic-footwear table)"
#: (US men, UK, EU, foot length cm). US women = US men + 1.5.
_SHOE_ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("4", "3", "36", "22"), ("4.5", "3.5", "36.5", "22.5"), ("5", "4", "37.5", "23"), ("5.5", "4.5", "38", "23.5"),
    ("6", "5", "38.5", "24"), ("6.5", "5.5", "39", "24.5"), ("7", "6", "40", "25"), ("7.5", "6.5", "40.5", "25.5"),
    ("8", "7", "41", "26"), ("8.5", "7.5", "42", "26.5"), ("9", "8", "42.5", "27"), ("9.5", "8.5", "43", "27.5"),
    ("10", "9", "44", "28"), ("10.5", "9.5", "44.5", "28.5"), ("11", "10", "45", "29"), ("11.5", "10.5", "45.5", "29.5"),
    ("12", "11", "46", "30"), ("12.5", "11.5", "47", "30.5"), ("13", "12", "47.5", "31"), ("13.5", "12.5", "48", "31.5"),
    ("14", "13", "48.5", "32"), ("15", "14", "49.5", "33"),
)
_SHOE_TABLE: tuple[dict[str, Decimal], ...] = tuple(
    {"us_men": Decimal(m), "us_women": Decimal(m) + Decimal("1.5"), "uk": Decimal(uk), "eu": Decimal(eu), "cm": Decimal(cm)}
    for m, uk, eu, cm in _SHOE_ROWS
)
_SHOE_UNITS = ("us_men", "us_women", "uk", "eu", "cm")
_SHOE_ALIASES: dict[str, str] = {
    "us_men": "us_men", "us men": "us_men", "us men's": "us_men", "us mens": "us_men", "us (men)": "us_men", "us m": "us_men",
    "us_women": "us_women", "us women": "us_women", "us women's": "us_women", "us womens": "us_women", "us (women)": "us_women", "us w": "us_women",
    "uk": "uk", "gb": "uk", "eu": "eu", "eur": "eu", "europe": "eu", "cm": "cm", "centimetres": "cm", "centimeters": "cm", "foot length": "cm", "mondopoint": "cm",
}
_CLOTHING_UNITS = ("alpha", "chest_cm", "waist_cm")
_CLOTHING_ALIASES: dict[str, str] = {
    "alpha": "alpha", "letter": "alpha", "size": "alpha", "s-xl": "alpha",
    "chest_cm": "chest_cm", "chest": "chest_cm", "bust": "chest_cm", "bust_cm": "chest_cm", "chest cm": "chest_cm", "bust cm": "chest_cm",
    "waist_cm": "waist_cm", "waist": "waist_cm", "waist cm": "waist_cm",
}
_ALPHA_ALIASES = {"xxs": "XS", "xs": "XS", "s": "S", "small": "S", "m": "M", "medium": "M", "l": "L", "large": "L", "xl": "XL", "xxl": "XXL", "2xl": "XXL", "3xl": "3XL", "xxxl": "3XL"}
#: The US chart is the generic inch-based retail chart (converted at 2.54 cm/in); the EU chart is the
#: EN 13402-3 letter code, which is defined on chest/bust only. Bands are (min, max) in the chart's own unit.
_CLOTHING_CHARTS: dict[tuple[str, str], dict[str, Any]] = {
    ("us", "men"): {
        "name": "generic US men's chart (chest / waist in inches, 2.54 cm per inch)",
        "unit": "in",
        "chest": {"XS": ("32", "34"), "S": ("34", "36"), "M": ("38", "40"), "L": ("42", "44"), "XL": ("46", "48"), "XXL": ("50", "52")},
        "waist": {"XS": ("26", "28"), "S": ("28", "30"), "M": ("32", "34"), "L": ("36", "38"), "XL": ("40", "42"), "XXL": ("44", "46")},
    },
    ("us", "women"): {
        "name": "generic US women's (misses) chart (bust / waist in inches, 2.54 cm per inch)",
        "unit": "in",
        "chest": {"XS": ("32", "33"), "S": ("34", "35"), "M": ("36", "37"), "L": ("38.5", "40"), "XL": ("42", "44"), "XXL": ("46", "48")},
        "waist": {"XS": ("24", "25"), "S": ("26", "27"), "M": ("28", "29"), "L": ("30.5", "32"), "XL": ("34", "36"), "XXL": ("38", "40")},
    },
    ("eu", "men"): {
        "name": "EN 13402-3 men's letter codes (chest in cm)",
        "unit": "cm",
        "chest": {"XS": ("78", "86"), "S": ("86", "94"), "M": ("94", "102"), "L": ("102", "110"), "XL": ("110", "118"), "XXL": ("118", "129"), "3XL": ("129", "141")},
        "waist": None,
    },
    ("eu", "women"): {
        "name": "EN 13402-3 women's letter codes (bust in cm)",
        "unit": "cm",
        "chest": {"XS": ("74", "82"), "S": ("82", "90"), "M": ("90", "98"), "L": ("98", "106"), "XL": ("106", "118"), "XXL": ("118", "131"), "3XL": ("131", "146")},
        "waist": None,
    },
}
_CLOTHING_REGIONS = ("us", "eu")
_GENDERS = ("men", "women")
_GENDER_ALIASES = {"men": "men", "man": "men", "male": "men", "m": "men", "mens": "men", "men's": "men", "women": "women", "woman": "women", "female": "women", "f": "women", "womens": "women", "women's": "women", "ladies": "women"}
_IN_CM = Decimal("2.54")


def _gender(p: dict[str, Any], *, required: bool) -> str | None:
    raw = p.get("gender")
    if raw is None:
        if required:
            raise Ambiguous("men's and women's charts differ; say which with gender", field="gender", options=list(_GENDERS))
        return None
    key = str(raw).strip().lower()
    if key not in _GENDER_ALIASES:
        raise ToolError(f"gender must be one of {', '.join(_GENDERS)}")
    return _GENDER_ALIASES[key]


def _shoe_unit(u: Any, field: str, gender: str | None) -> str:
    key = _key(u, field)
    if key in ("us", "usa", "american"):
        if gender:
            return f"us_{gender}"
        raise Ambiguous(f"'{u}' shoe sizes differ for men and women by 1.5; say which (or pass gender)", field=field, options=["us_men", "us_women"])
    if key not in _SHOE_ALIASES:
        raise ToolError(f"'{u}' is not a shoe-size scale; use one of {', '.join(_SHOE_UNITS)}")
    return _SHOE_ALIASES[key]


def _shoes(p: dict[str, Any]) -> dict[str, Any]:
    gender = _gender(p, required=False)
    src = _shoe_unit(p.get("from_unit"), "from_unit", gender)
    dst = _shoe_unit(p.get("to_unit"), "to_unit", gender)
    val = _positive_value(p)
    assumptions: list[str] = []
    warnings = [f"shoe sizes are approximate and vary by brand and last; chart: {_SHOE_CHART}"]
    if p.get("region"):
        assumptions.append("region ignored for shoes: the scale names (us_men, us_women, uk, eu, cm) already carry it")
    column = [row[src] for row in _SHOE_TABLE]
    if not column[0] <= val <= column[-1]:
        raise ToolError(f"{_dec_str(val)} {src} is outside the chart ({_dec_str(column[0])} to {_dec_str(column[-1])} {src})")
    row = min(_SHOE_TABLE, key=lambda r: (abs(r[src] - val), r[src]))
    if row[src] != val:
        warnings.append(f"no chart row for {_dec_str(val)} {src}; nearest is {_dec_str(row[src])} {src}")
    return ok(
        {
            "value": _jsonnum(row[dst]),
            "unit": dst,
            "display": f"{_dec_str(row[dst])} {dst}" + (" (foot length)" if dst == "cm" else ""),
            "from": {"value": _jsonnum(val), "unit": src},
            "row": {k: _jsonnum(v) for k, v in row.items()},
            "chart": _SHOE_CHART,
        },
        assumptions=assumptions,
        warnings=warnings,
    )


def _clothing_unit(u: Any, field: str) -> str:
    key = _key(u, field)
    if key not in _CLOTHING_ALIASES:
        raise ToolError(f"'{u}' is not a clothing measure; use one of {', '.join(_CLOTHING_UNITS)}")
    return _CLOTHING_ALIASES[key]


def _band_cm(chart: dict[str, Any], measure: str, alpha: str, decimals: int) -> dict[str, int | float]:
    lo, hi = (Decimal(x) for x in chart[measure][alpha])
    if chart["unit"] == "in":
        lo, hi = lo * _IN_CM, hi * _IN_CM
    return {"min": _jsonnum(_quant(lo, decimals)), "max": _jsonnum(_quant(hi, decimals))}


def _clothing(p: dict[str, Any]) -> dict[str, Any]:
    src = _clothing_unit(p.get("from_unit"), "from_unit")
    dst = _clothing_unit(p.get("to_unit"), "to_unit")
    region = p.get("region")
    if region is None:
        raise Ambiguous("S-XL means a different chest on the US and EU charts; say which with region", field="region", options=list(_CLOTHING_REGIONS))
    region = str(region).strip().lower()
    if region not in _CLOTHING_REGIONS:
        raise ToolError(f"region must be one of {', '.join(_CLOTHING_REGIONS)} for clothing")
    gender = _gender(p, required=True)
    chart = _CLOTHING_CHARTS[(region, gender)]
    decimals = _decimals(p, 1)
    for u in (src, dst):
        if u == "waist_cm" and chart["waist"] is None:
            raise Unsupported(f"{chart['name']} defines letter codes on chest/bust only; use region=us for waist")
    warnings = [f"clothing sizes are approximate and vary by brand and cut; chart: {chart['name']}"]
    assumptions: list[str] = []
    if src == "alpha":
        raw = str(p.get("value", "")).strip().lower()
        if raw not in _ALPHA_ALIASES or _ALPHA_ALIASES[raw] not in chart["chest"]:
            raise ToolError(f"'{p.get('value')}' is not a size on this chart; use one of {', '.join(chart['chest'])}")
        alpha = _ALPHA_ALIASES[raw]
        if alpha != raw.upper():
            assumptions.append(f"'{p.get('value')}' read as {alpha}")
        from_out: Any = alpha
    else:
        val = _positive_value(p)
        measure = "chest" if src == "chest_cm" else "waist"
        bands = {a: tuple(Decimal(x) * (_IN_CM if chart["unit"] == "in" else 1) for x in b) for a, b in chart[measure].items()}
        names = list(bands)
        alpha = next((a for a in names if bands[a][0] <= val < bands[a][1] or (a == names[-1] and val == bands[a][1])), None)
        if alpha is None:
            if val < bands[names[0]][0] or val > bands[names[-1]][1]:
                raise ToolError(f"{_dec_str(val)} cm is outside the chart ({_dec_str(_quant(bands[names[0]][0], decimals))} to {_dec_str(_quant(bands[names[-1]][1], decimals))} cm {measure})")
            alpha = min(names, key=lambda a: min(abs(bands[a][0] - val), abs(bands[a][1] - val)))
            warnings.append(f"{_dec_str(val)} cm falls between two bands on this chart; nearest is {alpha}")
        from_out = _jsonnum(val)
    row: dict[str, Any] = {"alpha": alpha, "chest_cm": _band_cm(chart, "chest", alpha, decimals)}
    if chart["waist"] is not None:
        row["waist_cm"] = _band_cm(chart, "waist", alpha, decimals)
    value = alpha if dst == "alpha" else row[dst]
    if src != "alpha" and dst != "alpha" and src != dst:
        warnings.append(f"{src} to {dst} goes through the letter size, so it is the chart's band, not a body proportion")
    if dst != "alpha":
        assumptions.append(f"band bounds rounded half-up to {decimals} decimals")
    return ok(
        {
            "value": value,
            "unit": dst,
            "display": alpha if dst == "alpha" else f"{_dec_str(Decimal(str(value['min'])))}-{_dec_str(Decimal(str(value['max'])))} cm {dst[:-3]}",
            "from": {"value": from_out, "unit": src},
            "row": row,
            "chart": chart["name"],
        },
        assumptions=assumptions,
        warnings=warnings,
    )


def _sizes(p: dict[str, Any]) -> dict[str, Any]:
    category = p.get("category")
    if category is None:
        raise Ambiguous("say whether this is a shoe or a clothing size with category", field="category", options=["shoe", "clothing"])
    category = str(category).strip().lower()
    if category in ("shoe", "shoes", "footwear"):
        return _shoes(p)
    if category in ("clothing", "clothes", "apparel", "garment"):
        return _clothing(p)
    raise ToolError("category must be shoe or clothing")


@tool
def convert(mode: str = "auto", **params: Any) -> dict[str, Any]:
    """Convert units, temperatures, currencies, fuel economy, cooking measures or sizes. Refuses ambiguous units."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    if mode == "fuel_economy":
        return _fuel_economy(p)
    if mode == "cooking":
        return _cooking(p)
    if mode == "sizes":
        return _sizes(p)
    src = str(p.get("from_unit") or "")
    dst = str(p.get("to_unit") or "")
    if mode == "currency" or (mode == "auto" and _CURRENCY_RE.match(src.strip().upper()) and _CURRENCY_RE.match(dst.strip().upper()) and src.strip().upper() not in ("DEG", "RAD") and len(src.strip()) == 3 and src.strip().isupper()):
        return _currency(p)
    return _units(p)

#: Worked examples for the reference page, one list per mode. Every one of them is
#: executed when /docs/tools/convert is built and sorted by the result into
#: "Examples" (the call succeeded) and "Fails when" (it did not), so a fixture never
#: states an expectation of its own. Mark anything whose output depends on the
#: current instant with "volatile": True.
EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "units": [
        {
            "caption": "Kilometres to miles, with the statute-mile assumption stated and the exact factor returned.",
            "args": {"mode": "units", "value": 5, "from_unit": "km", "to_unit": "miles"},
        },
        {
            "caption": "Square feet to square metres — `sqft` is understood as an alias.",
            "args": {"mode": "units", "value": 1500, "from_unit": "sqft", "to_unit": "sqm"},
        },
        {
            "caption": "Decimal to binary bytes, spelled out so the 7% difference is not a surprise.",
            "args": {"mode": "units", "value": 1, "from_unit": "gigabyte", "to_unit": "gibibyte"},
        },
        {
            "caption": "An ambiguous unit resolved on purpose with `assume`, and the reading recorded.",
            "args": {"mode": "units", "value": 1, "from_unit": "ton", "to_unit": "kg", "assume": "common"},
        },
        {
            "caption": "An Indian land unit, defined in the registry.",
            "args": {"mode": "units", "value": 1, "from_unit": "bigha", "to_unit": "sqft"},
        },
        {
            "caption": "`ton` is three different masses. The options come back in `needs.options`.",
            "args": {"mode": "units", "value": 1, "from_unit": "ton", "to_unit": "kg"},
        },
        {
            "caption": "`gallon` is US or imperial — a 20% difference.",
            "args": {"mode": "units", "value": 1, "from_unit": "gallon", "to_unit": "liter"},
        },
        {
            "caption": "`oz` is a mass or a volume depending on what is being measured.",
            "args": {"mode": "units", "value": 8, "from_unit": "oz", "to_unit": "g"},
        },
        {
            "caption": "`GB` may be decimal bytes, binary bytes or bits.",
            "args": {"mode": "units", "value": 1, "from_unit": "GB", "to_unit": "MB"},
        },
        {
            "caption": "Dimensions that do not relate.",
            "args": {"mode": "units", "value": 5, "from_unit": "km", "to_unit": "kg"},
        },
        {
            "caption": "An unknown unit.",
            "args": {"mode": "units", "value": 5, "from_unit": "blorg", "to_unit": "km"},
        },
        {
            "caption": "`to_unit` is required.",
            "args": {"mode": "units", "value": 5, "from_unit": "km"},
        },
    ],
    "temperature": [
        {
            "caption": "A reading below absolute zero is refused; pass delta=true if it is a temperature difference.",
            "args": {"mode": "temperature", "value": -500, "from_unit": "C", "to_unit": "K"},
        },
        {
            "caption": "An absolute reading.",
            "args": {"mode": "temperature", "value": 100, "from_unit": "C", "to_unit": "F"},
        },
        {
            "caption": "The same number as a difference — a different answer, and the tool says which it used.",
            "args": {"mode": "temperature", "value": 100, "from_unit": "C", "to_unit": "F", "delta": True},
        },
        {
            "caption": "Body temperature into kelvin.",
            "args": {"mode": "temperature", "value": 98.6, "from_unit": "F", "to_unit": "K"},
        },
        {
            "caption": "A temperature cannot become a length.",
            "args": {"mode": "temperature", "value": 100, "from_unit": "C", "to_unit": "km"},
        },
        {
            "caption": "`from_unit` is required.",
            "args": {"mode": "temperature", "value": 100, "to_unit": "F"},
        },
    ],
    "currency": [
        {
            "caption": "A direct rate.",
            "args": {"mode": "currency", "value": 100, "from_unit": "USD", "to_unit": "INR", "rate": 83.42},
        },
        {
            "caption": "A rate table with a base currency — the cross rate is derived.",
            "args": {"mode": "currency", "value": 250, "from_unit": "EUR", "to_unit": "INR", "rates": {"USD": 1, "EUR": 0.92, "INR": 83.42}, "base": "USD"},
        },
        {
            "caption": "A zero-decimal currency.",
            "args": {"mode": "currency", "value": 100, "from_unit": "USD", "to_unit": "JPY", "rate": 147.2, "decimals": 0},
        },
        {
            "caption": "No rate and no table: the tool refuses to invent one and says where to get it.",
            "args": {"mode": "currency", "value": 100, "from_unit": "USD", "to_unit": "INR"},
        },
        {
            "caption": "Currency codes must be three letters.",
            "args": {"mode": "currency", "value": 100, "from_unit": "DOLLAR", "to_unit": "INR", "rate": 83.42},
        },
        {
            "caption": "A rate table that does not cover both sides.",
            "args": {"mode": "currency", "value": 100, "from_unit": "AUD", "to_unit": "INR", "rates": {"USD": 1, "EUR": 0.92}},
        },
    ],
    "fuel_economy": [
        {
            "caption": "US mpg to litres per 100 km — the inverse relation is spelled out.",
            "args": {"mode": "fuel_economy", "value": 30, "from_unit": "mpg_us", "to_unit": "l_per_100km"},
        },
        {
            "caption": "A European figure into imperial mpg.",
            "args": {"mode": "fuel_economy", "value": 6.5, "from_unit": "l_per_100km", "to_unit": "mpg_uk"},
        },
        {
            "caption": "km/L into US mpg, four decimals.",
            "args": {"mode": "fuel_economy", "value": 15, "from_unit": "km/l", "to_unit": "mpg_us", "decimals": 4},
        },
        {
            "caption": "`mpg` alone is US or imperial — a 20% difference — so both come back as options.",
            "args": {"mode": "fuel_economy", "value": 30, "from_unit": "mpg", "to_unit": "l_per_100km"},
        },
        {
            "caption": "Zero has no inverse.",
            "args": {"mode": "fuel_economy", "value": 0, "from_unit": "mpg_us", "to_unit": "l_per_100km"},
        },
    ],
    "cooking": [
        {
            "caption": "A cup of flour in grams, with the density used and the cup system declared.",
            "args": {"mode": "cooking", "value": 1, "from_unit": "cup", "to_unit": "g", "ingredient": "flour"},
        },
        {
            "caption": "Grams of sugar back into cups.",
            "args": {"mode": "cooking", "value": 200, "from_unit": "g", "to_unit": "cups", "ingredient": "sugar"},
        },
        {
            "caption": "An Australian tablespoon is 20 ml, not 15.",
            "args": {"mode": "cooking", "value": 2, "from_unit": "tbsp", "to_unit": "ml", "cup": "au"},
        },
        {
            "caption": "Mass to volume without an ingredient: the table comes back as options.",
            "args": {"mode": "cooking", "value": 1, "from_unit": "cup", "to_unit": "g"},
        },
        {
            "caption": "An ingredient the table does not know.",
            "args": {"mode": "cooking", "value": 1, "from_unit": "cup", "to_unit": "g", "ingredient": "quinoa"},
        },
        {
            "caption": "`oz` is a weight or a fluid measure.",
            "args": {"mode": "cooking", "value": 8, "from_unit": "oz", "to_unit": "g", "ingredient": "butter"},
        },
    ],
    "sizes": [
        {
            "caption": "A US men's shoe size in EU, with the whole chart row.",
            "args": {"mode": "sizes", "category": "shoe", "value": 9, "from_unit": "us_men", "to_unit": "eu"},
        },
        {
            "caption": "Foot length to a US women's size.",
            "args": {"mode": "sizes", "category": "shoe", "value": 25, "from_unit": "cm", "to_unit": "us_women"},
        },
        {
            "caption": "A 100 cm chest on the US men's chart.",
            "args": {"mode": "sizes", "category": "clothing", "value": 100, "from_unit": "chest_cm", "to_unit": "alpha", "region": "us", "gender": "men"},
        },
        {
            "caption": "What bust an EU women's M covers.",
            "args": {"mode": "sizes", "category": "clothing", "value": "M", "from_unit": "alpha", "to_unit": "chest_cm", "region": "eu", "gender": "women"},
        },
        {
            "caption": "A plain `us` shoe size is men's or women's — 1.5 sizes apart.",
            "args": {"mode": "sizes", "category": "shoe", "value": 9, "from_unit": "us", "to_unit": "eu"},
        },
        {
            "caption": "Clothing without a region: the chart is never guessed.",
            "args": {"mode": "sizes", "category": "clothing", "value": 100, "from_unit": "chest_cm", "to_unit": "alpha", "gender": "men"},
        },
        {
            "caption": "`category` is required.",
            "args": {"mode": "sizes", "value": 9, "from_unit": "us_men", "to_unit": "eu"},
        },
    ],
    "auto": [
        {
            "caption": "Two unit names: the unit path.",
            "args": {"mode": "auto", "value": 10, "from_unit": "km", "to_unit": "mi"},
        },
        {
            "caption": "Two ISO codes and a rate: the currency path.",
            "args": {"mode": "auto", "value": 100, "from_unit": "USD", "to_unit": "INR", "rate": 83.42},
        },
        {
            "caption": "Detected as currency, but with no rate supplied.",
            "args": {"mode": "auto", "value": 100, "from_unit": "USD", "to_unit": "INR"},
        },
        {
            "caption": "Detected as units, and still refused when ambiguous.",
            "args": {"mode": "auto", "value": 1, "from_unit": "ton", "to_unit": "kg"},
        },
    ],
}
