"""scale - proportional scaling of one quantity, and everything tied to it.

Examples
  recipe for 4 -> 7 servings          scale(from_qty=4, to_qty=7, entities=[...])
  price per kg -> per 250 g           scale(from_qty=1, from_unit="kg", to_qty=250, to_unit="g", entities=[{"name":"price","qty":480}])
  3 workers take 5 days -> 12 workers scale(from_qty=3, to_qty=12, mode="inverse", entities=[{"name":"days","qty":5}])
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Any

from ..contract import ToolError, ok, tool
from .convert import _norm_unit, _parse_value, ureg


def _mixed(fr: Fraction) -> str:
    if fr.denominator == 1:
        return str(fr.numerator)
    whole, rem = divmod(abs(fr.numerator), fr.denominator)
    sign = "-" if fr < 0 else ""
    return f"{sign}{whole} {rem}/{fr.denominator}" if whole else f"{sign}{rem}/{fr.denominator}"


def _q(fr: Fraction, precision: int) -> dict[str, Any]:
    dec = float(fr)
    return {
        "value": round(dec, precision),
        "exact": str(fr) if fr.denominator != 1 else str(fr.numerator),
        "mixed": _mixed(fr),
        "ceil": math.ceil(fr),
        "floor": math.floor(fr),
        "rounded": int(round(dec)),
    }


@tool
def scale(**params: Any) -> dict[str, Any]:
    """Scale from one quantity to another and apply the factor to every entity."""
    p = {k: v for k, v in params.items() if v is not None}
    mode = (p.get("mode") or "linear").lower()
    if mode not in ("linear", "inverse"):
        raise ToolError("mode must be 'linear' (direct proportion) or 'inverse' (inverse proportion)")
    precision = int(p.get("precision", 6))
    assumptions: list[str] = []
    warnings: list[str] = []

    if p.get("factor") is not None:
        factor = _parse_value(p["factor"])
        from_qty = Fraction(1)
        to_qty = factor
        assumptions.append("explicit factor supplied")
    else:
        if p.get("from_qty") is None:
            raise ToolError("'from_qty' is required (or pass 'factor')")
        from_qty = _parse_value(p["from_qty"])
        if from_qty == 0:
            raise ToolError("from_qty cannot be zero")
        from_unit, to_unit = p.get("from_unit"), p.get("to_unit")
        to_qty = _parse_value(p["to_qty"]) if p.get("to_qty") is not None else None
        if to_qty is None:
            if to_unit is None:
                raise ToolError("'to_qty' (or 'to_unit') is required")
            to_qty = Fraction(1)
            assumptions.append(f"to_qty defaulted to 1 {to_unit}")
        if from_unit and to_unit and str(from_unit).strip().lower() != str(to_unit).strip().lower():
            reg = ureg()
            su = _norm_unit(from_unit, p.get("assume"), "from_unit", assumptions)
            du = _norm_unit(to_unit, p.get("assume"), "to_unit", assumptions)
            try:
                f = (1 * reg.Unit(du)).to(reg.Unit(su)).magnitude
            except Exception as e:
                raise ToolError(f"cannot relate {from_unit} to {to_unit}: {e}") from None
            conv = Fraction(str(f)).limit_denominator(10**12)
            to_qty_in_from = to_qty * conv
            assumptions.append(f"{float(to_qty):g} {to_unit} = {float(to_qty_in_from):g} {from_unit}")
            to_qty = to_qty_in_from
        elif (from_unit is None) != (to_unit is None):
            warnings.append("only one of from_unit/to_unit given; treated as the same unit")
        factor = (to_qty / from_qty) if mode == "linear" else (from_qty / to_qty)

    entities_in = p.get("entities") or []
    if isinstance(entities_in, dict):
        entities_in = [{"name": k, "qty": v} for k, v in entities_in.items()]
    entities = []
    for i, e in enumerate(entities_in):
        if not isinstance(e, dict) or "qty" not in e:
            raise ToolError(f"entities[{i}] must be {{'name':..., 'qty':..., 'unit'?:...}}")
        qty = _parse_value(e["qty"])
        scaled = qty * factor
        entry: dict[str, Any] = {
            "name": e.get("name", f"item{i + 1}"),
            "original": _q(qty, precision),
            "scaled": _q(scaled, precision),
        }
        if e.get("unit"):
            entry["unit"] = e["unit"]
        if p.get("factor") is None:
            entry["per_unit"] = _q(qty / from_qty, precision)
        if e.get("integer"):
            entry["scaled"]["value"] = entry["scaled"]["ceil"]
            warnings.append(f"{entry['name']} rounded up to a whole number")
        entities.append(entry)

    out: dict[str, Any] = {
        "factor": _q(factor, precision),
        "mode": mode,
        "from": {"qty": float(from_qty), **({"unit": p["from_unit"]} if p.get("from_unit") else {})},
        "to": {"qty": float(to_qty), **({"unit": p["to_unit"]} if p.get("to_unit") else {})},
        "entities": entities,
        "percent_change": round((float(factor) - 1) * 100, 4) if mode == "linear" else None,
    }
    if mode == "inverse":
        assumptions.append("inverse proportion: doubling 'from' halves each entity")
    return ok(out, assumptions=assumptions, warnings=warnings)
