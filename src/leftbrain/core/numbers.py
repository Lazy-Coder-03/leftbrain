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
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    localcontext,
)
from fractions import Fraction
from typing import Any

from ..contract import (
    TooLarge,
    ToolError,
    Unsupported,
    check_params,
    exclusive,
    flag,
    ok,
    tool,
    whole,
)

#: Digits the largest term of a generated sequence may have.
MAX_TERM_DIGITS = 1000
#: Significant digits any value here may carry. Decimal's default context is 28, past which
#: every operation rounds silently and `quantize` raises.
MAX_DIGITS = 1200
#: Parts an allocation may be split into. Each part is ~120 bytes of response and the
#: response is capped at 256 KB.
MAX_PARTS = 2000
#: The spaces a French or Swiss document groups digits with.
_THIN_SPACES = str.maketrans({" ": "", " ": "", " ": "", " ": ""})


def _log10_abs(v: Any) -> float:
    """log10 of a magnitude, 0 for values at or below 1 - they never grow a term."""
    try:
        return max(0.0, math.log10(abs(float(v)))) if v else 0.0
    except (ValueError, OverflowError):  # pragma: no cover - defensive
        return 0.0


MODES = ("compare", "round", "format", "allocate", "sequence", "parse", "to_words", "semver")

#: What each mode reads. Anything else in a call is a caller's mistake, not a default
#: to fall back on (#28 SS2a). Kept honest by tests/test_mode_params.py, which derives
#: the same map from the code and fails when the two drift.
MODE_PARAMS: dict[str, frozenset[str]] = {
    "compare": frozenset({"a", "b", "values"}),
    "round": frozenset({"decimals", "method", "nearest", "rounding", "significant", "value"}),
    "format": frozenset({"accounting", "currency", "decimals", "locale", "style", "value"}),
    "allocate": frozenset({"decimals", "labels", "method", "n", "parts", "percentages", "ratios", "total", "weights"}),
    "sequence": frozenset({"count", "end", "kind", "n", "ratio", "start", "step", "type"}),
    "parse": frozenset({"value", "values"}),
    "to_words": frozenset({"currency", "suffix_only", "system", "value"}),
    "semver": frozenset({"a", "b", "values"}),
}

_SUFFIX = {"k": 3, "thousand": 3, "m": 6, "mn": 6, "million": 6, "b": 9, "bn": 9, "billion": 9, "t": 12, "tn": 12, "trillion": 12, "l": 5, "lac": 5, "lakh": 5, "lakhs": 5, "cr": 7, "crore": 7, "crores": 7}
_CURRENCY_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "rs": "INR", "rs.": "INR", "inr": "INR", "usd": "USD"}


#: Characters a document uses where a keyboard would type `-`.
#: A number written the way every language prints a large or small one.
_SCIENTIFIC = re.compile(r"[+-]?\d+(?:\.\d+)?e[+-]?\d+")

_DASHES = str.maketrans({"\u2212": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2796": "-"})


#: The largest and smallest magnitudes an IEEE double can hold. Past them a conversion that
#: goes through `float` - which is what pint does - has no answer to give.
FLOAT_MAX = Decimal("1.7976931348623157e308")
FLOAT_MIN_SUBNORMAL = Decimal("5e-324")


def saturate_to_float(d: Decimal, what: str = "value") -> tuple[float, str | None]:
    """A float, and a note when the magnitude did not survive the conversion.

    Past the top of the double range the honest answer is infinity, and below the bottom it
    is zero - but either one handed back in silence is a wrong number wearing the shape of a
    right one, so the note always accompanies it.
    """
    if not d.is_finite():
        return float(d), f"the {what} is already infinite, so the result is too"
    magnitude = abs(d)
    if magnitude > FLOAT_MAX:
        sign = "" if d > 0 else "-"
        return float(f"{sign}inf"), (
            f"the {what} is larger than the largest representable number (about 1.8e308), "
            f"so it is reported as {sign}infinity and its magnitude is lost"
        )
    if magnitude != 0 and magnitude < FLOAT_MIN_SUBNORMAL:
        return 0.0, (
            f"the {what} is smaller than the smallest representable number (about 5e-324), "
            f"so it is reported as zero and its precision is lost"
        )
    return float(d), None


def parse_number(v: Any) -> tuple[Decimal, list[str]]:
    """Parse '1,23,456.78', '₹1.2L', '3.4 Cr', '2.5k', '12%', '(500)' into a Decimal."""
    with localcontext() as ctx:
        ctx.prec = MAX_DIGITS
        ctx.traps[Inexact] = True  # nothing here may round: a number that would is too long
        try:
            return _parse_number(v)
        except Inexact:
            raise TooLarge(f"the number has more than {MAX_DIGITS:,} significant digits", hint=f"Use at most {MAX_DIGITS:,} digits.") from None
        except Overflow:
            raise TooLarge("the number's exponent is past what can be computed with (about 10^999999)", hint="Use a smaller exponent.") from None


def parse_percent(v: Any) -> tuple[Decimal, list[str]]:
    """A value that is a percentage already: `"12%"` is 12, not 0.12.

    parse_number divides a `%` value by 100, which is right for a plain number and wrong for
    a field that is a percentage by definition.
    """
    if isinstance(v, str) and v.strip().endswith("%"):
        d, notes = parse_number(v.strip()[:-1])
        return d, notes + [f"{v.strip()} read as {_dec_str(d)} percent"]
    return parse_number(v)


def _finite(d: Decimal, name: str) -> Decimal:
    if not d.is_finite():
        raise ToolError(f"{name} is infinite; there is no answer for it", hint="Give a finite number.")
    return d


def _parse_number(v: Any) -> tuple[Decimal, list[str]]:
    assumptions: list[str] = []
    if isinstance(v, bool):
        raise ToolError("booleans are not numbers")
    if isinstance(v, int):
        return Decimal(v), assumptions
    if isinstance(v, float):
        # JSON has no infinity, so a client that wrote 1e400 hands us `inf`. Passing that
        # on as a successful answer with nothing said is the silence this fixes.
        if v != v:
            raise ToolError("value is NaN, which is not a number this can work with")
        if v in (float("inf"), float("-inf")):
            sign = "" if v > 0 else "-"
            return Decimal(f"{sign}Infinity"), assumptions + [
                f"the value arrived as {sign}infinity: it was written larger than a JSON number "
                f"can hold (about 1.8e308), so its magnitude is lost"
            ]
        return Decimal(repr(v)), assumptions
    if isinstance(v, Decimal):
        return v, assumptions
    if isinstance(v, Fraction):
        return Decimal(v.numerator) / Decimal(v.denominator), assumptions
    s = str(v).strip().lower()
    # U+2212 MINUS SIGN, and the dashes a word processor substitutes for one, are what a
    # number copied out of a document actually contains (#28 SS3.13).
    s = s.translate(_DASHES)
    if not s:
        raise ToolError("empty number")
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
        assumptions.append("parentheses read as negative (accounting style)")
    sign = ""
    if s[:1] in "+-":  # `-₹500`: the sign may come before the symbol
        sign, s = s[0], s[1:].strip()
    for sym in sorted(_CURRENCY_SYMBOLS, key=len, reverse=True):
        if s.startswith(sym):
            s = s[len(sym):].strip()
            break
    s = sign + s.replace("_", "").translate(_THIN_SPACES)
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    if _SCIENTIFIC.fullmatch(s):
        # `Decimal` holds `1e400` exactly - the suffix regex below simply never let it try,
        # so a number written the way every language prints large ones was "unparseable".
        d = Decimal(s)
        if neg:
            d = -d
        return (d / 100, assumptions + ["% read as /100"]) if pct else (d, assumptions)
    m = re.fullmatch(r"([+-]?[\d,.]+)([a-z]+)?", s)
    if not m:
        raise ToolError(f"cannot parse number {v!r}")
    num, suf = m.group(1), m.group(2)
    num, notes = _separators(num, v)
    assumptions += notes
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


#: Digits grouped the Western way (`1,234,567`) or the Indian way (`12,34,567`).
_WESTERN = re.compile(r"[+-]?\d{1,3}(?:[,.]\d{3})+")
_INDIAN = re.compile(r"[+-]?\d{1,2}(?:,\d{2})*,\d{3}")


def _separators(num: str, original: Any) -> tuple[str, list[str]]:
    """Resolve `,` and `.` in a digit string to a plain decimal, or refuse.

    Western (`1,234.56`), Indian (`12,34,567.89`) and European (`1.234,56`) groupings are
    read; commas that group nothing (`1,2345`, `10,000,00`) are refused rather than guessed.
    """
    if "," not in num and num.count(".") <= 1:
        return num, []
    if "," not in num:  # `12.345.678`: dots as grouping, the European way
        if _WESTERN.fullmatch(num):
            return num.replace(".", ""), ["dots read as digit grouping"]
        raise ToolError(f"cannot parse number {original!r}: more than one decimal point")
    if "." in num:
        int_part, dot, frac = num.rpartition(".")
        if "," not in frac and "," in int_part and (_WESTERN.fullmatch(int_part) or _INDIAN.fullmatch(int_part)):
            return int_part.replace(",", "") + dot + frac, []  # `1,234.56` / `12,34,567.89`
        int_part, comma, frac = num.rpartition(",")
        if "," not in int_part and _WESTERN.fullmatch(int_part) and frac.isdigit():
            return int_part.replace(".", "") + "." + frac, ["dots read as digit grouping and the comma as the decimal separator (European)"]
        raise ToolError(f"cannot parse number {original!r}: commas and dots do not form a grouping this recognises (1,234.56 or 1.234,56)")
    if _WESTERN.fullmatch(num) or _INDIAN.fullmatch(num):
        return num.replace(",", ""), []
    if re.fullmatch(r"[+-]?\d+,\d{1,2}", num):  # European decimal comma e.g. 1234,56
        return num.replace(",", "."), ["comma read as decimal separator"]
    raise ToolError(
        f"cannot parse number {original!r}: the commas do not group the digits in threes (1,234,567) or twos-then-three (12,34,567)",
        hint="Write the number without separators, or with a single decimal point.",
    )


def _dec_str(d: Decimal) -> str:
    if not d.is_finite():
        return str(d)
    if not d:
        return "0"  # never "-0"
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, len(d.as_tuple().digits) + 1)  # normalize() is a context operation and would round
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
        if a.is_finite() and b.is_finite():
            out["difference"] = _dec_str(b - a)
            if a != 0:
                # over |a|, as finance.percent does, so -100 -> -50 is +50% in both places
                out["percent_change_a_to_b"] = _dec_str(((b - a) / abs(a) * 100).quantize(Decimal("0.0001")))
            else:
                assumptions.append("percent change from zero is undefined, so it is omitted; the difference is the only meaningful figure")
    return ok(out, assumptions=assumptions)


_SEMVER_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$")


def parse_semver(v: Any) -> tuple[dict[str, Any], list[str]]:
    """``"v1.10"`` -> the parsed fields and the assumptions made (missing minor/patch read as 0)."""
    if isinstance(v, bool) or isinstance(v, (int, float)):
        # 1.10 as a number is 1.1, and this mode exists to keep 1.10 above 1.9
        raise ToolError(f"pass versions as strings: {v!r} as a number cannot tell 1.10 from 1.1")
    s = str(v).strip()
    m = _SEMVER_RE.match(s)
    if not m:
        raise ToolError(f"{s!r} is not a version (expected MAJOR.MINOR.PATCH with optional -prerelease and +build)")
    major, minor, patch, pre, build = m.groups()
    if any(part and len(part) > 1 and part.startswith("0") for part in (major, minor, patch)):
        raise ToolError(f"{s!r}: SemVer forbids a leading zero in a numeric part")
    assumptions = []
    if minor is None or patch is None:
        assumptions.append(f"{s} read as {major}.{minor or 0}.{patch or 0}")
    if pre is not None and any(not part or (part.isdigit() and part != "0" and part.startswith("0")) for part in pre.split(".")):
        raise ToolError(f"{s!r}: pre-release identifiers must be non-empty and numeric ones must not have leading zeros")
    fields = {"major": int(major), "minor": int(minor or 0), "patch": int(patch or 0), "prerelease": pre, "build": build}
    fields["normalized"] = f"{fields['major']}.{fields['minor']}.{fields['patch']}" + (f"-{pre}" if pre else "") + (f"+{build}" if build else "")
    return fields, assumptions


def _semver_key(f: dict[str, Any]) -> tuple[Any, ...]:
    """SemVer 2.0 §11: numeric parts, then a release outranks any pre-release, then pre-release identifiers left to right — numerics numerically, others in ASCII order, numeric before alphanumeric, a shorter prefix first. Build metadata is not part of precedence."""
    pre = f["prerelease"]
    if pre is None:
        return (f["major"], f["minor"], f["patch"], 1, ())
    ids = tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in pre.split("."))
    return (f["major"], f["minor"], f["patch"], 0, ids)


def _semver(p: dict[str, Any]) -> dict[str, Any]:
    vals = p.get("values")
    if vals is None and p.get("a") is not None and p.get("b") is not None:
        vals = [p["a"], p["b"]]
    if not isinstance(vals, list) or len(vals) < 2:
        raise ToolError("semver needs 'values' (list of 2+ version strings) or 'a' and 'b'")
    parsed = []
    assumptions: list[str] = []
    for v in vals:
        f, a = parse_semver(v)
        parsed.append((f, v))
        assumptions += [x for x in a if x not in assumptions]
    if any(f["build"] for f, _ in parsed):
        assumptions.append("build metadata (+…) is ignored when ordering, as SemVer 2.0 requires")
    order = sorted(parsed, key=lambda t: _semver_key(t[0]))
    ascending = [{"input": v, **f} for f, v in order]
    chain = []
    for i, (f, v) in enumerate(order):
        if i:
            chain.append("=" if _semver_key(f) == _semver_key(order[i - 1][0]) else "<")
        chain.append(str(v))
    out: dict[str, Any] = {
        "ascending": ascending,
        "descending": list(reversed(ascending)),
        "max": ascending[-1],
        "min": ascending[0],
        "ordering": " ".join(chain),
        "all_equal": all(_semver_key(f) == _semver_key(order[0][0]) for f, _ in order),
    }
    if len(parsed) == 2:
        ka, kb = _semver_key(parsed[0][0]), _semver_key(parsed[1][0])
        out["relation"] = "a < b" if ka < kb else ("a > b" if ka > kb else "a = b")
    return ok(out, assumptions=assumptions)


_ROUND_MODES = {"half_up": ROUND_HALF_UP, "half_even": ROUND_HALF_EVEN, "bankers": ROUND_HALF_EVEN, "half_down": ROUND_HALF_DOWN, "floor": ROUND_FLOOR, "ceil": ROUND_CEILING, "ceiling": ROUND_CEILING, "truncate": ROUND_DOWN, "down": ROUND_DOWN}


def _round(p: dict[str, Any]) -> dict[str, Any]:
    d, assumptions = parse_number(p.get("value"))
    _finite(d, "value")
    mode = str(p.get("rounding") or p.get("method") or "half_up").lower()
    if mode not in _ROUND_MODES:
        raise ToolError(f"rounding must be one of {', '.join(_ROUND_MODES)}")
    rm = _ROUND_MODES[mode]
    clash = exclusive(p, "significant", "nearest", "decimals")
    if clash:
        assumptions.append(clash)
    if p.get("significant") is not None:
        sig = whole(p["significant"], "significant", lo=1, hi=MAX_DIGITS)
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
        decimals = whole(p.get("decimals", 0), "decimals", lo=-MAX_DIGITS, hi=MAX_DIGITS)
        res = d.quantize(Decimal(1).scaleb(-decimals), rounding=rm)
        assumptions.append(f"{decimals} decimals, {mode}" + (" (Python's round() uses half_even; pass rounding='half_even' to match)" if mode == "half_up" else ""))
    number, note = saturate_to_float(res, "rounded value")
    if note:
        assumptions.append(note)
    return ok({"value": _dec_str(res), "number": number, "original": _dec_str(d)}, assumptions=assumptions)


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


_STYLES = ("number", "currency", "percent", "compact")


def _format(p: dict[str, Any]) -> dict[str, Any]:
    d, assumptions = parse_number(p.get("value"))
    _finite(d, "value")
    locale = str(p.get("locale") or "en_US")
    lk = locale.replace("-", "_")
    if lk not in _LOCALE:
        lk2 = lk.split("_")[-1].upper()
        if lk2 not in _LOCALE:
            raise ToolError(f"unsupported locale {locale!r}; try en_IN, en_US, en_GB, de_DE, fr_FR")
        lk = lk2
    sizes, gsep, dsep = _LOCALE[lk]
    style = str(p.get("style") or "number").lower()
    if style not in _STYLES:
        raise ToolError(f"style must be one of {', '.join(_STYLES)}, not {style!r}")
    decimals = p.get("decimals")
    if decimals is not None:
        decimals = whole(decimals, "decimals", lo=0, hi=MAX_DIGITS)
    currency = str(p.get("currency") or "").upper()
    if currency and style not in ("currency", "compact"):
        assumptions.append(f"currency {currency} is not shown with style='{style}'; use style='currency'")
    accounting = flag(p.get("accounting", False), "accounting")
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
        for i, (div, suf) in enumerate(table):
            if absd >= div:
                v = (d / div).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if i and abs(v) * div >= table[i - 1][0]:  # 999,999 rounds to 1000K, which is 1M
                    div, suf = table[i - 1]
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
    if accounting and sign:
        formatted = f"({body})"
    else:
        formatted = sign + body
    out = {"formatted": formatted, "value": _dec_str(d), "locale": lk, "decimals": int(decimals)}
    return ok(out, assumptions=assumptions + [f"rounded half-up to {decimals} decimals"])


def _allocate(p: dict[str, Any]) -> dict[str, Any]:
    total, assumptions = parse_number(p.get("total"))
    _finite(total, "total")
    decimals = whole(p.get("decimals", 2), "decimals", lo=0, hi=20)
    unit = Decimal(1).scaleb(-decimals)
    clash = exclusive(p, "weights", "ratios", "percentages", "parts", "n")
    if clash:
        assumptions.append(clash)
    source = next((k for k in ("weights", "ratios", "percentages") if p.get(k) is not None), None)
    weights_in = p.get(source) if source else None
    labels = p.get("labels")
    if weights_in is None:
        if p.get("parts") is None and p.get("n") is None:
            raise ToolError("allocate needs 'weights' (list) or 'parts' (int)")
        n = whole(p["parts"] if p.get("parts") is not None else p["n"], "parts", lo=1, hi=MAX_PARTS)
        weights = [Fraction(1)] * n
        assumptions.append(f"split equally into {n} parts")
    else:
        if isinstance(weights_in, dict):
            labels = list(weights_in.keys())
            weights_in = list(weights_in.values())
        if not isinstance(weights_in, list) or not weights_in:
            raise ToolError(f"{source} must be a non-empty list of numbers, not {weights_in!r}")
        weights = []
        for w in weights_in:
            dw, _ = parse_percent(w) if source == "percentages" else parse_number(w)
            _finite(dw, source)
            if dw < 0:
                raise ToolError("weights must be non-negative")
            weights.append(Fraction(dw))
        if source == "percentages" and abs(sum(weights) - 100) > Fraction(1, 1000):
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


MAX_TERMS = 10_000
#: log10 of the golden ratio: F(n) has about n·0.209 digits.
_LOG10_PHI = math.log10((1 + 5**0.5) / 2)


def _sequence(p: dict[str, Any]) -> dict[str, Any]:
    kind = str(p.get("kind") or p.get("type") or "arithmetic").lower()
    assumptions: list[str] = []
    warnings: list[str] = []
    n = p.get("n") if p.get("n") is not None else p.get("count")
    if n is not None:
        n = whole(n, "n", lo=1, hi=MAX_TERMS)
    for name, kinds in (("step", ("arithmetic", "range")), ("ratio", ("geometric",)), ("end", ("arithmetic", "range"))):
        if p.get(name) is not None and kind not in kinds:
            assumptions.append(f"'{name}' is not used by a {kind} sequence; ignored")

    def num(value: Any, key: str) -> Decimal:
        d, notes = parse_number(value)
        assumptions.extend(x for x in notes if x not in assumptions)
        return _finite(d, key)

    if kind == "arithmetic":
        start, step = num(p.get("start", 0), "start"), num(p.get("step", 1), "step")
        if n is None and p.get("end") is None:
            raise ToolError("arithmetic needs 'n' or 'end'")
        clash = exclusive(p, "n", "count", "end")
        if clash:
            assumptions.append(clash)
        if n is None:
            end = num(p["end"], "end")
            if step == 0:
                raise ToolError("step cannot be zero")
            n = int(((end - start) / step).to_integral_value(rounding=ROUND_FLOOR)) + 1
            if n < 1:
                n = 0
                warnings.append(f"step {_dec_str(step)} points away from end {_dec_str(end)}, so there are no terms")
            if n > MAX_TERMS:
                raise TooLarge(f"the sequence would have {n:,} terms; the most that can be returned is {MAX_TERMS:,}", details={"terms": n, "limit": MAX_TERMS}, hint="Use a larger step or a nearer end.")
        seq = [start + step * i for i in range(n)]
    elif kind == "geometric":
        start, ratio = num(p.get("start", 1), "start"), num(p.get("ratio", 2), "ratio")
        if n is None:
            raise ToolError("geometric needs 'n'")
        # `n` is capped at 10 000 but the *terms* are not: 2, ratio 2, n 10 000 ends at
        # 2^10000 and the response is 15 MB of digits (#28 SS2e).
        last_digits = _log10_abs(start) + (n - 1) * _log10_abs(ratio)
        if last_digits > MAX_TERM_DIGITS:
            raise TooLarge(
                f"the last term would have about {int(last_digits):,} digits; "
                f"the limit is {MAX_TERM_DIGITS:,}",
                details={"estimated_digits": int(last_digits), "limit_digits": MAX_TERM_DIGITS, "n": n},
                hint="Lower 'n' or 'ratio'.",
            )
        seq, cur = [], start
        for _ in range(n):
            seq.append(cur)
            cur *= ratio
    elif kind == "fibonacci":
        n = n or 10
        last_digits = (n - 1) * _LOG10_PHI
        if last_digits > MAX_TERM_DIGITS:
            raise TooLarge(
                f"F({n - 1}) would have about {int(last_digits):,} digits; the limit is {MAX_TERM_DIGITS:,}",
                details={"estimated_digits": int(last_digits), "limit_digits": MAX_TERM_DIGITS, "n": n},
                hint="Lower 'n'.",
            )
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
        start, end, step = num(p.get("start", 0), "start"), num(p.get("end", 10), "end"), num(p.get("step", 1), "step")
        if step == 0:
            raise ToolError("step cannot be zero")
        count = int(((end - start) / step).to_integral_value(rounding=ROUND_FLOOR)) + 1
        if count < 1:
            count = 0
            warnings.append(f"step {_dec_str(step)} points away from end {_dec_str(end)}, so there are no terms")
        if count > MAX_TERMS:
            raise TooLarge(f"the range would have {count:,} terms; the most that can be returned is {MAX_TERMS:,}", details={"terms": count, "limit": MAX_TERMS}, hint="Use a larger step or a nearer end.")
        seq = [start + step * i for i in range(count)]
    else:
        raise ToolError("kind must be arithmetic, geometric, fibonacci, primes, squares or range")
    total = sum(seq, Decimal(0))
    return ok(
        {"kind": kind, "count": len(seq), "terms": [_dec_str(x) for x in seq], "sum": _dec_str(total), "last": _dec_str(seq[-1]) if seq else None},
        assumptions=assumptions,
        warnings=warnings,
    )


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


#: The short scale, as far as it has everyday names.
_SCALES = ["", "thousand", "million", "billion", "trillion", "quadrillion", "quintillion", "sextillion", "septillion", "octillion", "nonillion", "decillion"]


def _words_international(n: int) -> str:
    if n == 0:
        return "zero"
    if n >= 1000 ** len(_SCALES):
        raise Unsupported(
            f"numbers of 10^{3 * len(_SCALES)} and above have no name in the short scale here (it stops at decillion); system='indian' names any size in crores of crores",
            hint="Pass system='indian'.",
        )
    parts, i = [], 0
    while n:
        n, chunk = divmod(n, 1000)
        if chunk:
            parts.insert(0, (_below_thousand(chunk) + (" " + _SCALES[i] if _SCALES[i] else "")).strip())
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


#: Currency words: (major, minor, plural of minor). None: the unit has no minor.
_CURRENCY_WORDS = {"INR": ("rupee", "paise", "paise"), "USD": ("dollar", "cent", "cents"), "EUR": ("euro", "cent", "cents"), "GBP": ("pound", "penny", "pence"), "AED": ("dirham", "fils", "fils"), "SGD": ("dollar", "cent", "cents"), "JPY": ("yen", None, None)}
_INVARIANT = ("yen",)


def _to_words(p: dict[str, Any]) -> dict[str, Any]:
    d, assumptions = parse_number(p.get("value"))
    _finite(d, "value")
    if len(d.as_tuple().digits) > MAX_TERM_DIGITS:
        raise TooLarge(f"the number has more than {MAX_TERM_DIGITS:,} digits; that is past what can be spelled out", hint=f"Use at most {MAX_TERM_DIGITS:,} digits.")
    system = str(p.get("system") or "international").lower()
    if system in ("indian", "in", "lakh"):
        fn = _words_indian
    elif system in ("international", "western", "us", "short"):
        fn = _words_international
    else:
        raise ToolError("system must be 'international' or 'indian'")
    neg = d < 0
    d = abs(d)
    whole_part = int(d)
    frac = d - whole_part
    currency = str(p.get("currency") or "").upper()
    if currency:
        known = currency in _CURRENCY_WORDS
        major, minor, minors = _CURRENCY_WORDS.get(currency, (currency, "cent", "cents"))
        minor_n = int((frac * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        if minor_n == 100:
            whole_part, minor_n = whole_part + 1, 0
        plural = whole_part != 1 and major not in _INVARIANT and known  # a code like XYZ is not pluralised
        text = f"{fn(whole_part)} {major}{'s' if plural else ''}"
        if minor_n and minor:
            text += f" and {fn(minor_n)} {minor if minor_n == 1 else minors}"
        if flag(p.get("suffix_only", True), "suffix_only"):
            text += " only"
        assumptions.append(f"minor units rounded to 2 decimals ({currency})")
    else:
        text = fn(whole_part)
        if frac:
            digits = _dec_str(frac)[2:]
            text += " point " + " ".join(_ONES[int(c)] if c != "0" else "zero" for c in digits)
    if neg:
        text = "minus " + text
    if currency:
        text = text[0].upper() + text[1:]  # not .capitalize(), which would lower-case a code
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
        number, note = saturate_to_float(d, "value")
        out.append({"input": v, "value": _dec_str(d), "number": number})
        assumptions += [x for x in a + ([note] if note else []) if x not in assumptions]
    return ok(out[0] if single else out, assumptions=assumptions)


@tool
def numbers(mode: str = "compare", **params: Any) -> dict[str, Any]:
    """Number utilities. Modes: compare, round, format, allocate, sequence, parse, to_words, semver."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    check_params("numbers", mode, p, MODE_PARAMS)
    with localcontext() as ctx:
        ctx.prec = MAX_DIGITS
        try:
            return {"compare": _compare, "round": _round, "format": _format, "allocate": _allocate, "sequence": _sequence, "parse": _parse, "to_words": _to_words, "semver": _semver}[mode](p)
        except (InvalidOperation, Overflow):
            # past 1,200 digits even quantize gives up; that is a size, not an internal error
            raise TooLarge(f"the result would have more than {MAX_DIGITS:,} significant digits", hint="Use smaller numbers or fewer decimals.") from None
        except DivisionByZero:
            raise ToolError("the calculation divides by zero") from None

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
            "caption": "Two rounding instructions cannot both apply; the one used is named in `assumptions` rather than chosen silently.",
            "args": {"mode": "round", "value": 123.456, "significant": 2, "decimals": 5},
        },
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
            "caption": "A million parts is refused: the response would be 116 MB.",
            "args": {"mode": "allocate", "total": 100, "parts": 1000000},
        },
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
            "caption": "The term cap is by size as well as by count: ratio 2 over 10 000 terms ends at 2^10000.",
            "args": {"mode": "sequence", "kind": "geometric", "start": 2, "ratio": 2, "n": 10000},
        },
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
    "semver": [
        {
            "caption": "Versions are not decimals: 1.10 comes after 1.9.",
            "args": {"mode": "semver", "a": "1.9", "b": "1.10"},
        },
        {
            "caption": "Pre-releases ordered by the SemVer 2.0 rules — numeric identifiers numerically, a release above every pre-release.",
            "args": {"mode": "semver", "values": ["1.0.0", "1.0.0-rc.1", "1.0.0-beta.11", "1.0.0-beta.2", "1.0.0-alpha"]},
        },
        {
            "caption": "Build metadata is carried through but never decides the order.",
            "args": {"mode": "semver", "values": ["v2.1.0+build.7", "2.1.0+build.9", "2.0.9"]},
        },
        {
            "caption": "Something that is not a version.",
            "args": {"mode": "semver", "values": ["1.2.3", "latest"]},
        },
    ],
}
