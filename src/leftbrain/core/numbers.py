"""numbers - comparison, rounding, locale formatting, exact allocation, words."""

from __future__ import annotations

import math
import re
from decimal import (
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    Decimal,
    InvalidOperation,
)
from fractions import Fraction
from typing import Any

from ..contract import ToolError, ok, tool

MODES = ("compare", "round", "format", "allocate", "sequence", "parse", "to_words")

_SUFFIX = {"k": 3, "thousand": 3, "m": 6, "mn": 6, "million": 6, "b": 9, "bn": 9, "billion": 9, "t": 12, "tn": 12, "trillion": 12, "l": 5, "lac": 5, "lakh": 5, "lakhs": 5, "cr": 7, "crore": 7, "crores": 7}
_CURRENCY_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "rs": "INR", "rs.": "INR", "inr": "INR", "usd": "USD"}


def parse_number(v: Any) -> tuple[Decimal, list[str]]:
    """Parse '1,23,456.78', '₹1.2L', '3.4 Cr', '2.5k', '12%', '(500)' into a Decimal."""
    assumptions: list[str] = []
    if isinstance(v, bool):
        raise ToolError("booleans are not numbers")
    if isinstance(v, int):
        return Decimal(v), assumptions
    if isinstance(v, float):
        return Decimal(repr(v)), assumptions
    if isinstance(v, Decimal):
        return v, assumptions
    if isinstance(v, Fraction):
        return Decimal(v.numerator) / Decimal(v.denominator), assumptions
    s = str(v).strip().lower()
    if not s:
        raise ToolError("empty number")
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
        assumptions.append("parentheses read as negative (accounting style)")
    for sym in sorted(_CURRENCY_SYMBOLS, key=len, reverse=True):
        if s.startswith(sym):
            s = s[len(sym):].strip()
            break
    s = s.replace("_", "").replace(" ", "")
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    m = re.fullmatch(r"([+-]?[\d,.]+)([a-z]+)?", s)
    if not m:
        raise ToolError(f"cannot parse number {v!r}")
    num, suf = m.group(1), m.group(2)
    if num.count(",") and num.count(".") > 1:
        raise ToolError(f"cannot parse number {v!r}")
    if "," in num and "." not in num and re.fullmatch(r"[+-]?\d{1,3}(,\d{3})*", num) is None and re.fullmatch(r"[+-]?\d{1,2}(,\d{2})*,\d{3}", num) is None:
        # European decimal comma e.g. 1234,56
        if re.fullmatch(r"[+-]?\d+,\d{1,2}", num):
            num = num.replace(",", ".")
            assumptions.append("comma read as decimal separator")
    num = num.replace(",", "")
    try:
        d = Decimal(num)
    except InvalidOperation:
        raise ToolError(f"cannot parse number {v!r}") from None
    if suf:
        if suf not in _SUFFIX:
            raise ToolError(f"unknown suffix {suf!r} in {v!r}")
        d = d * (Decimal(10) ** _SUFFIX[suf])
        assumptions.append(f"'{suf}' read as ×10^{_SUFFIX[suf]}")
    if pct:
        d = d / 100
        assumptions.append("% read as /100")
    if neg:
        d = -d
    return d, assumptions


def _dec_str(d: Decimal) -> str:
    s = format(d.normalize(), "f") if d == d.to_integral() or abs(d) < Decimal("1e15") else format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


# --------------------------------------------------------------------------- #


def _compare(p: dict[str, Any]) -> dict[str, Any]:
    vals = p.get("values")
    if vals is None and p.get("a") is not None and p.get("b") is not None:
        vals = [p["a"], p["b"]]
    if not isinstance(vals, list) or len(vals) < 2:
        raise ToolError("compare needs 'values' (list of 2+ numbers) or 'a' and 'b'")
    parsed = []
    assumptions: list[str] = []
    for v in vals:
        d, a = parse_number(v)
        parsed.append((d, v))
        assumptions += [x for x in a if x not in assumptions]
    order = sorted(parsed, key=lambda t: t[0])
    ascending = [{"input": v, "value": _dec_str(d)} for d, v in order]
    chain = []
    for i, (d, v) in enumerate(order):
        if i:
            prev = order[i - 1][0]
            chain.append("=" if d == prev else "<")
        chain.append(str(v))
    out: dict[str, Any] = {
        "ascending": ascending,
        "descending": list(reversed(ascending)),
        "max": {"input": order[-1][1], "value": _dec_str(order[-1][0])},
        "min": {"input": order[0][1], "value": _dec_str(order[0][0])},
        "ordering": " ".join(chain),
        "all_equal": all(d == order[0][0] for d, _ in order),
    }
    if len(parsed) == 2:
        a, b = parsed[0][0], parsed[1][0]
        out["relation"] = "a < b" if a < b else ("a > b" if a > b else "a = b")
        out["difference"] = _dec_str(b - a)
        if a != 0:
            out["percent_change_a_to_b"] = _dec_str(((b - a) / a * 100).quantize(Decimal("0.0001")))
    return ok(out, assumptions=assumptions)


_ROUND_MODES = {"half_up": ROUND_HALF_UP, "half_even": ROUND_HALF_EVEN, "bankers": ROUND_HALF_EVEN, "half_down": ROUND_HALF_DOWN, "floor": ROUND_FLOOR, "ceil": ROUND_CEILING, "ceiling": ROUND_CEILING, "truncate": ROUND_DOWN, "down": ROUND_DOWN}


def _round(p: dict[str, Any]) -> dict[str, Any]:
    d, assumptions = parse_number(p.get("value"))
    mode = (p.get("rounding") or p.get("method") or "half_up").lower()
    if mode not in _ROUND_MODES:
        raise ToolError(f"rounding must be one of {', '.join(_ROUND_MODES)}")
    rm = _ROUND_MODES[mode]
    if p.get("significant") is not None:
        sig = int(p["significant"])
        if sig < 1:
            raise ToolError("significant must be >= 1")
        if d == 0:
            res = Decimal(0)
        else:
            exp = d.adjusted() - sig + 1
            res = d.quantize(Decimal(1).scaleb(exp), rounding=rm)
        assumptions.append(f"{sig} significant figures, {mode}")
    elif p.get("nearest") is not None:
        step, _ = parse_number(p["nearest"])
        if step <= 0:
            raise ToolError("nearest must be positive")
        res = (d / step).quantize(Decimal(1), rounding=rm) * step
        assumptions.append(f"to nearest {_dec_str(step)}, {mode}")
    else:
        decimals = int(p.get("decimals", 0))
        res = d.quantize(Decimal(1).scaleb(-decimals), rounding=rm)
        assumptions.append(f"{decimals} decimals, {mode}" + (" (Python's round() uses half_even; pass rounding='half_even' to match)" if mode == "half_up" else ""))
    return ok({"value": _dec_str(res), "number": float(res), "original": _dec_str(d)}, assumptions=assumptions)


_LOCALE = {
    # group sizes (first, rest), thousands sep, decimal sep
    "en_IN": ((3, 2), ",", "."), "hi_IN": ((3, 2), ",", "."), "IN": ((3, 2), ",", "."),
    "en_US": ((3, 3), ",", "."), "US": ((3, 3), ",", "."), "en_GB": ((3, 3), ",", "."), "GB": ((3, 3), ",", "."), "UK": ((3, 3), ",", "."),
    "en_AU": ((3, 3), ",", "."), "en_CA": ((3, 3), ",", "."), "ja_JP": ((3, 3), ",", "."), "zh_CN": ((3, 3), ",", "."), "ko_KR": ((3, 3), ",", "."),
    "de_DE": ((3, 3), ".", ","), "DE": ((3, 3), ".", ","), "es_ES": ((3, 3), ".", ","), "it_IT": ((3, 3), ".", ","), "pt_BR": ((3, 3), ".", ","), "nl_NL": ((3, 3), ".", ","), "id_ID": ((3, 3), ".", ","), "tr_TR": ((3, 3), ".", ","),
    "fr_FR": ((3, 3), " ", ","), "FR": ((3, 3), " ", ","), "ru_RU": ((3, 3), " ", ","), "pl_PL": ((3, 3), " ", ","), "sv_SE": ((3, 3), " ", ","), "cs_CZ": ((3, 3), " ", ","),
    "de_CH": ((3, 3), "’", "."), "CH": ((3, 3), "’", "."),
}
_CCY_SYMBOL = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥", "KRW": "₩", "AUD": "A$", "CAD": "C$", "SGD": "S$", "AED": "د.إ", "CHF": "CHF", "BRL": "R$", "RUB": "₽", "THB": "฿", "VND": "₫", "NGN": "₦", "ZAR": "R", "PHP": "₱", "TRY": "₺"}


def _group(int_part: str, sizes: tuple[int, int], sep: str) -> str:
    first, rest = sizes
    if len(int_part) <= first:
        return int_part
    head, tail = int_part[:-first], int_part[-first:]
    chunks = []
    while len(head) > rest:
        chunks.insert(0, head[-rest:])
        head = head[:-rest]
    if head:
        chunks.insert(0, head)
    return sep.join(chunks + [tail])


def _format(p: dict[str, Any]) -> dict[str, Any]:
    d, assumptions = parse_number(p.get("value"))
    locale = str(p.get("locale") or "en_US")
    lk = locale.replace("-", "_")
    if lk not in _LOCALE:
        lk2 = lk.split("_")[-1].upper()
        if lk2 not in _LOCALE:
            raise ToolError(f"unsupported locale {locale!r}; try en_IN, en_US, en_GB, de_DE, fr_FR")
        lk = lk2
    sizes, gsep, dsep = _LOCALE[lk]
    style = (p.get("style") or "number").lower()
    decimals = p.get("decimals")
    currency = (p.get("currency") or "").upper()
    if style == "currency" and decimals is None:
        decimals = 0 if currency in ("JPY", "KRW", "VND") else 2
    if style == "percent":
        d = d * 100
        if decimals is None:
            decimals = 2
    if style == "compact":
        indian = lk.endswith("IN")
        absd = abs(d)
        if indian:
            table = [(Decimal(10**7), "Cr"), (Decimal(10**5), "L"), (Decimal(10**3), "K")]
        else:
            table = [(Decimal(10**12), "T"), (Decimal(10**9), "B"), (Decimal(10**6), "M"), (Decimal(10**3), "K")]
        for div, suf in table:
            if absd >= div:
                v = (d / div).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                txt = _dec_str(v) + suf
                if currency:
                    txt = _CCY_SYMBOL.get(currency, currency + " ") + txt
                return ok({"formatted": txt, "value": _dec_str(d)}, assumptions=assumptions + [("Indian" if indian else "international") + " compact notation"])
        decimals = decimals if decimals is not None else 0
    if decimals is None:
        exp = -d.as_tuple().exponent if d.as_tuple().exponent < 0 else 0
        decimals = min(exp, 10)
    q = d.quantize(Decimal(1).scaleb(-int(decimals)), rounding=ROUND_HALF_UP)
    sign = "-" if q < 0 else ""
    s = format(abs(q), "f")
    int_part, _, frac = s.partition(".")
    body = _group(int_part, sizes, gsep)
    if frac:
        body += dsep + frac
    if style == "percent":
        body += "%"
    if style == "currency":
        sym = _CCY_SYMBOL.get(currency, currency)
        body = f"{sym}{body}" if len(sym) <= 2 else f"{sym} {body}"
    if p.get("accounting") and sign:
        formatted = f"({body})"
    else:
        formatted = sign + body
    out = {"formatted": formatted, "value": _dec_str(d), "locale": lk, "decimals": int(decimals)}
    return ok(out, assumptions=assumptions + [f"rounded half-up to {decimals} decimals"])


def _allocate(p: dict[str, Any]) -> dict[str, Any]:
    total, assumptions = parse_number(p.get("total"))
    decimals = int(p.get("decimals", 2))
    unit = Decimal(1).scaleb(-decimals)
    weights_in = p.get("weights") or p.get("ratios") or p.get("percentages")
    labels = p.get("labels")
    if weights_in is None:
        n = int(p.get("parts") or p.get("n") or 0)
        if n < 1:
            raise ToolError("allocate needs 'weights' (list) or 'parts' (int)")
        weights = [Fraction(1)] * n
        assumptions.append(f"split equally into {n} parts")
    else:
        if isinstance(weights_in, dict):
            labels = list(weights_in.keys())
            weights_in = list(weights_in.values())
        weights = []
        for w in weights_in:
            dw, _ = parse_number(w)
            if dw < 0:
                raise ToolError("weights must be non-negative")
            weights.append(Fraction(dw))
        if p.get("percentages") is not None and abs(sum(weights) - 100) > Fraction(1, 1000):
            raise ToolError(f"percentages sum to {float(sum(weights))}, not 100")
    if sum(weights) == 0:
        raise ToolError("weights sum to zero")
    n = len(weights)
    if labels and len(labels) != n:
        raise ToolError("labels must match the number of weights")
    total_units = (total / unit).to_integral_value(rounding=ROUND_HALF_UP)
    if total_units != total / unit:
        assumptions.append(f"total rounded to {decimals} decimals before allocation")
    tu = Fraction(int(total_units))
    wsum = sum(weights)
    raw = [tu * w / wsum for w in weights]
    floors = [math.floor(r) for r in raw]
    remainder = int(tu) - sum(floors)
    # largest remainder: hand the leftover units to the biggest fractional parts (stable by index)
    order = sorted(range(n), key=lambda i: (-(raw[i] - floors[i]), i))
    shares = floors[:]
    for i in order[:remainder]:
        shares[i] += 1
    method = (p.get("method") or "largest_remainder").lower()
    if method == "first":
        shares = floors[:]
        shares[0] += remainder
    elif method == "last":
        shares = floors[:]
        shares[-1] += remainder
    elif method != "largest_remainder":
        raise ToolError("method must be largest_remainder, first or last")
    items = []
    for i, s in enumerate(shares):
        amt = Decimal(s) * unit
        items.append({"label": labels[i] if labels else f"part{i + 1}", "weight": _dec_str(Decimal(weights[i].numerator) / Decimal(weights[i].denominator)), "share": _dec_str(amt), "amount": float(amt), "exact_unrounded": _dec_str(Decimal(raw[i].numerator) / Decimal(raw[i].denominator) * unit), "adjusted": s != floors[i]})
    out = {"total": _dec_str(total), "sum_of_shares": _dec_str(sum(Decimal(s) for s in shares) * unit), "items": items, "leftover_units_distributed": remainder, "method": method}
    return ok(out, assumptions=assumptions + [f"shares sum exactly to the total; leftover {unit} units went to the parts with the largest fractional remainder" if method == "largest_remainder" else f"leftover units given to the {method} part"])


def _sequence(p: dict[str, Any]) -> dict[str, Any]:
    kind = (p.get("kind") or p.get("type") or "arithmetic").lower()
    n = p.get("n") or p.get("count")
    if n is not None:
        n = int(n)
        if n < 1 or n > 10000:
            raise ToolError("n must be 1..10000")
    if kind == "arithmetic":
        start, _ = parse_number(p.get("start", 0))
        step, _ = parse_number(p.get("step", 1))
        if n is None and p.get("end") is None:
            raise ToolError("arithmetic needs 'n' or 'end'")
        if n is None:
            end, _ = parse_number(p["end"])
            if step == 0:
                raise ToolError("step cannot be zero")
            n = int(((end - start) / step).to_integral_value(rounding=ROUND_FLOOR)) + 1
            if n < 1:
                n = 0
            if n > 10000:
                raise ToolError("sequence longer than 10000 terms")
        seq = [start + step * i for i in range(n)]
    elif kind == "geometric":
        start, _ = parse_number(p.get("start", 1))
        ratio, _ = parse_number(p.get("ratio", 2))
        if n is None:
            raise ToolError("geometric needs 'n'")
        seq, cur = [], start
        for _ in range(n):
            seq.append(cur)
            cur *= ratio
    elif kind == "fibonacci":
        n = n or 10
        seq = [Decimal(0), Decimal(1)]
        while len(seq) < n:
            seq.append(seq[-1] + seq[-2])
        seq = seq[:n]
    elif kind == "primes":
        n = n or 10
        seq, c = [], 2
        while len(seq) < n:
            if all(c % q for q in range(2, int(c**0.5) + 1)):
                seq.append(Decimal(c))
            c += 1
    elif kind == "squares":
        n = n or 10
        seq = [Decimal(i * i) for i in range(1, n + 1)]
    elif kind == "range":
        start, _ = parse_number(p.get("start", 0))
        end, _ = parse_number(p.get("end", 10))
        step, _ = parse_number(p.get("step", 1))
        seq, cur = [], start
        while (cur <= end if step > 0 else cur >= end) and len(seq) <= 10000:
            seq.append(cur)
            cur += step
    else:
        raise ToolError("kind must be arithmetic, geometric, fibonacci, primes, squares or range")
    total = sum(seq, Decimal(0))
    return ok({"kind": kind, "count": len(seq), "terms": [_dec_str(x) for x in seq], "sum": _dec_str(total), "last": _dec_str(seq[-1]) if seq else None})


_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _below_thousand(n: int) -> str:
    if n == 0:
        return ""
    parts = []
    if n >= 100:
        parts.append(_ONES[n // 100] + " hundred")
        n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else ""))
    elif n:
        parts.append(_ONES[n])
    return " ".join(parts)


def _words_international(n: int) -> str:
    if n == 0:
        return "zero"
    scales = ["", "thousand", "million", "billion", "trillion", "quadrillion"]
    parts, i = [], 0
    while n:
        n, chunk = divmod(n, 1000)
        if chunk:
            parts.insert(0, (_below_thousand(chunk) + (" " + scales[i] if scales[i] else "")).strip())
        i += 1
    return " ".join(parts)


def _words_indian(n: int) -> str:
    if n == 0:
        return "zero"
    parts = []
    crore, n = divmod(n, 10**7)
    if crore:
        parts.append((_words_indian(crore) if crore >= 100 else _below_thousand(crore)) + " crore")
    lakh, n = divmod(n, 10**5)
    if lakh:
        parts.append(_below_thousand(lakh) + " lakh")
    thousand, n = divmod(n, 1000)
    if thousand:
        parts.append(_below_thousand(thousand) + " thousand")
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def _to_words(p: dict[str, Any]) -> dict[str, Any]:
    d, assumptions = parse_number(p.get("value"))
    system = (p.get("system") or "international").lower()
    if system in ("indian", "in", "lakh"):
        fn = _words_indian
    elif system in ("international", "western", "us", "short"):
        fn = _words_international
    else:
        raise ToolError("system must be 'international' or 'indian'")
    neg = d < 0
    d = abs(d)
    whole = int(d)
    frac = d - whole
    currency = (p.get("currency") or "").upper()
    if currency:
        major, minor = {"INR": ("rupee", "paise"), "USD": ("dollar", "cent"), "EUR": ("euro", "cent"), "GBP": ("pound", "pence"), "AED": ("dirham", "fils"), "SGD": ("dollar", "cent"), "JPY": ("yen", None)}.get(currency, (currency, "cent"))
        minor_n = int((frac * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        if minor_n == 100:
            whole, minor_n = whole + 1, 0
        text = f"{fn(whole)} {major}{'s' if whole != 1 and major not in ('yen', 'pence', 'paise') else ''}"
        if minor_n and minor:
            text += f" and {fn(minor_n)} {minor}"
        text = text.capitalize()
        if p.get("suffix_only", True):
            text += " only"
        if currency == "INR":
            text = text.replace("Rupee", "Rupees") if whole != 1 else text
        assumptions.append(f"minor units rounded to 2 decimals ({currency})")
    else:
        text = fn(whole)
        if frac:
            digits = _dec_str(frac)[2:]
            text += " point " + " ".join(_ONES[int(c)] if c != "0" else "zero" for c in digits)
    if neg:
        text = "minus " + text
    return ok({"words": text, "value": _dec_str(d if not neg else -d), "system": system}, assumptions=assumptions)


def _parse(p: dict[str, Any]) -> dict[str, Any]:
    vals = p.get("values")
    single = vals is None
    if single:
        vals = [p.get("value")]
    out = []
    assumptions: list[str] = []
    for v in vals:
        d, a = parse_number(v)
        out.append({"input": v, "value": _dec_str(d), "number": float(d)})
        assumptions += [x for x in a if x not in assumptions]
    return ok(out[0] if single else out, assumptions=assumptions)


@tool
def numbers(mode: str = "compare", **params: Any) -> dict[str, Any]:
    """Number utilities. Modes: compare, round, format, allocate, sequence, parse, to_words."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    return {"compare": _compare, "round": _round, "format": _format, "allocate": _allocate, "sequence": _sequence, "parse": _parse, "to_words": _to_words}[mode](p)

#: Worked examples for the reference page, one list per mode. Every one of them is
#: executed when /docs/tools/numbers is built and sorted by the result into
#: "Examples" (the call succeeded) and "Fails when" (it did not), so a fixture never
#: states an expectation of its own. Mark anything whose output depends on the
#: current instant with "volatile": True.
EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "compare": [
        {
            "caption": "The canonical case.",
            "args": {"mode": "compare", "values": ["9.11", "9.9"]},
        },
        {
            "caption": "Mixed human notation, all reduced to decimals before ordering.",
            "args": {"mode": "compare", "values": ["1.2 Cr", "₹15,00,000", "2.5k", "0.03 bn"]},
        },
        {
            "caption": "Two values give a relation, a difference and a percentage change.",
            "args": {"mode": "compare", "a": "1,250.50", "b": "1,499.99"},
        },
        {
            "caption": "A value that is not a number.",
            "args": {"mode": "compare", "values": ["nine point one", "9.9"]},
        },
    ],
    "round": [
        {
            "caption": "Half-up: the rule most humans mean.",
            "args": {"mode": "round", "value": "2.5", "decimals": 0},
        },
        {
            "caption": "Bankers' rounding on the same value gives a different answer.",
            "args": {"mode": "round", "value": "2.5", "decimals": 0, "rounding": "half_even"},
        },
        {
            "caption": "Three significant figures.",
            "args": {"mode": "round", "value": "1234.5678", "significant": 3},
        },
        {
            "caption": "Cash rounding to the nearest five cents.",
            "args": {"mode": "round", "value": "12.327", "nearest": "0.05"},
        },
        {
            "caption": "An unknown rounding rule lists the valid ones.",
            "args": {"mode": "round", "value": "2.5", "rounding": "cosmic"},
        },
        {
            "caption": "Zero significant figures is meaningless.",
            "args": {"mode": "round", "value": "2.5", "significant": 0},
        },
        {
            "caption": "A step must be positive.",
            "args": {"mode": "round", "value": "2.5", "nearest": 0},
        },
        {
            "caption": "An unparseable value.",
            "args": {"mode": "round", "value": "two and a half"},
        },
    ],
    "format": [
        {
            "caption": "Indian digit grouping — two-digit groups above the thousand.",
            "args": {"mode": "format", "value": 12345678.9, "locale": "en_IN"},
        },
        {
            "caption": "The same number for Germany, where the separators swap.",
            "args": {"mode": "format", "value": 12345678.9, "locale": "de_DE"},
        },
        {
            "caption": "Currency, with the symbol and the right number of decimals.",
            "args": {"mode": "format", "value": "1234567.891", "locale": "en_IN", "style": "currency", "currency": "INR"},
        },
        {
            "caption": "Compact Indian notation.",
            "args": {"mode": "format", "value": 12345678, "locale": "en_IN", "style": "compact", "currency": "INR"},
        },
        {
            "caption": "A percentage, and an accounting-style negative.",
            "args": {"mode": "format", "value": "-0.0725", "style": "percent", "accounting": True},
        },
        {
            "caption": "An unsupported locale.",
            "args": {"mode": "format", "value": 1234.5, "locale": "xx_YY"},
        },
        {
            "caption": "An unparseable value.",
            "args": {"mode": "format", "value": "lots"},
        },
    ],
    "allocate": [
        {
            "caption": "100 split three ways: two parts get 33.33, one gets 33.34, and the total is exact.",
            "args": {"mode": "allocate", "total": 100, "parts": 3},
        },
        {
            "caption": "A labelled weighted split.",
            "args": {"mode": "allocate", "total": "10000", "weights": {"alice": 3, "bob": 2, "carol": 1}},
        },
        {
            "caption": "Percentages, validated to sum to 100.",
            "args": {"mode": "allocate", "total": "1250.75", "percentages": [50, 30, 20], "labels": ["rent", "food", "savings"]},
        },
        {
            "caption": "The same split with the remainder forced onto the first part instead.",
            "args": {"mode": "allocate", "total": 100, "parts": 3, "method": "first"},
        },
        {
            "caption": "Percentages that do not add to 100.",
            "args": {"mode": "allocate", "total": 100, "percentages": [50, 30, 10]},
        },
        {
            "caption": "Neither `weights` nor `parts`.",
            "args": {"mode": "allocate", "total": 100},
        },
        {
            "caption": "Labels that do not match the weights.",
            "args": {"mode": "allocate", "total": 100, "weights": [1, 2, 3], "labels": ["a", "b"]},
        },
        {
            "caption": "A negative weight.",
            "args": {"mode": "allocate", "total": 100, "weights": [3, -1]},
        },
        {
            "caption": "Weights that are all zero.",
            "args": {"mode": "allocate", "total": 100, "weights": [0, 0]},
        },
        {
            "caption": "An unknown distribution method.",
            "args": {"mode": "allocate", "total": 100, "parts": 3, "method": "random"},
        },
    ],
    "sequence": [
        {
            "caption": "An arithmetic sequence by count.",
            "args": {"mode": "sequence", "kind": "arithmetic", "start": 100, "step": 25, "n": 6},
        },
        {
            "caption": "A range defined by its endpoints, with a fractional step.",
            "args": {"mode": "sequence", "kind": "range", "start": "0", "end": "2", "step": "0.5"},
        },
        {
            "caption": "Fibonacci, exact.",
            "args": {"mode": "sequence", "kind": "fibonacci", "n": 12},
        },
        {
            "caption": "A geometric sequence — compound growth without float drift.",
            "args": {"mode": "sequence", "kind": "geometric", "start": "1000", "ratio": "1.08", "n": 5},
        },
        {
            "caption": "An unknown kind.",
            "args": {"mode": "sequence", "kind": "harmonic", "n": 5},
        },
        {
            "caption": "A zero step never reaches the end.",
            "args": {"mode": "sequence", "kind": "arithmetic", "start": 1, "step": 0, "end": 10},
        },
        {
            "caption": "The term cap is 10 000.",
            "args": {"mode": "sequence", "kind": "fibonacci", "n": 20000},
        },
    ],
    "parse": [
        {
            "caption": "An Indian crore amount with a currency symbol.",
            "args": {"mode": "parse", "value": "₹1.2 Cr"},
        },
        {
            "caption": "A batch, each with its reading explained.",
            "args": {"mode": "parse", "values": ["(500)", "12%", "1,23,456.78", "2.5k", "1234,56"]},
        },
        {
            "caption": "Words are not numbers.",
            "args": {"mode": "parse", "value": "twelve"},
        },
        {
            "caption": "Separators that cannot be reconciled.",
            "args": {"mode": "parse", "value": "1,23.45.6"},
        },
        {
            "caption": "An unknown magnitude suffix.",
            "args": {"mode": "parse", "value": "5 zillion"},
        },
    ],
    "to_words": [
        {
            "caption": "International grouping.",
            "args": {"mode": "to_words", "value": 1234567, "system": "international"},
        },
        {
            "caption": "The same number in the Indian system.",
            "args": {"mode": "to_words", "value": 1234567, "system": "indian"},
        },
        {
            "caption": "Invoice phrasing with minor units.",
            "args": {"mode": "to_words", "value": "125430.75", "system": "indian", "currency": "INR"},
        },
        {
            "caption": "A negative with a fractional part.",
            "args": {"mode": "to_words", "value": "-42.5"},
        },
        {
            "caption": "An unknown numbering system.",
            "args": {"mode": "to_words", "value": 1234, "system": "roman"},
        },
        {
            "caption": "An unparseable value.",
            "args": {"mode": "to_words", "value": "a lot"},
        },
    ],
}
