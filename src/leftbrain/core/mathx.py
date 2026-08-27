"""math - exact symbolic and numeric mathematics, powered by SymPy.

Covers arithmetic, trigonometry, complex numbers, calculus (derivatives,
integrals, limits, series, ODEs), equation solving, linear algebra and
statistics.  Every answer comes back in exact form *and* decimal form so the
caller never has to round anything itself.

Safety: expressions are parsed with a locked-down namespace (no builtins, no
attribute access) and evaluated under a timeout.
"""

from __future__ import annotations

import ast
import math as _pymath
import re
import sys
import threading
from collections.abc import Callable
from fractions import Fraction
from typing import Any

import sympy as sp
from sympy.core.function import AppliedUndef
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_application,
    implicit_multiplication,
    parse_expr,
    standard_transformations,
)

from ..contract import Ambiguous, Timeout, TooLarge, ToolError, Unsupported, ok, tool

MODES = (
    "eval",
    "exact",
    "simplify",
    "expand",
    "factor",
    "solve",
    "diff",
    "integrate",
    "limit",
    "series",
    "ode",
    "matrix",
    "stats",
    "convert_form",
    "plot_points",
)
DEFAULT_TIMEOUT = 20.0
MAX_EXPR_LEN = 5000

#: Digits a result can be rendered with at all. CPython refuses to ``str()`` an integer
#: longer than this (``sys.set_int_max_str_digits``), so a bigger answer cannot be returned
#: even if it were computed - and computing it is what took the hosted server down (#28 §1).
MAX_RESULT_DIGITS = sys.get_int_max_str_digits() or 4300
#: Significant digits the decimal form may be asked for.
MAX_PRECISION = 5000
#: Terms in a series expansion.
MAX_SERIES_ORDER = 50
#: Polynomial degree `solve` will fall back to numeric roots for.
MAX_NUMERIC_DEGREE = 200

# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

_TRANSFORMS = standard_transformations + (
    convert_xor,
    implicit_multiplication,
    implicit_application,
)

_DEG = sp.pi / 180


def _log10(x: Any) -> Any:
    return sp.log(x, 10)


def _log2(x: Any) -> Any:
    return sp.log(x, 2)


def _nroot(x: Any, n: Any) -> Any:
    return sp.root(x, n)


_TRIG = ("sin", "cos", "tan", "cot", "sec", "csc")
_INV_TRIG = ("asin", "acos", "atan", "acot", "asec", "acsc")

_SAFE_NAMES: dict[str, Any] = {
    # constants
    "pi": sp.pi,
    "E": sp.E,
    "e": sp.E,
    "I": sp.I,
    "i": sp.I,
    "oo": sp.oo,
    "inf": sp.oo,
    "infinity": sp.oo,
    "nan": sp.nan,
    "GoldenRatio": sp.GoldenRatio,
    "phi": sp.GoldenRatio,
    "EulerGamma": sp.EulerGamma,
    "deg": _DEG,
    # trig / hyperbolic
    **{n: getattr(sp, n) for n in _TRIG},
    **{n: getattr(sp, n) for n in _INV_TRIG},
    "atan2": sp.atan2,
    "sinh": sp.sinh,
    "cosh": sp.cosh,
    "tanh": sp.tanh,
    "coth": sp.coth,
    "sech": sp.sech,
    "csch": sp.csch,
    "asinh": sp.asinh,
    "acosh": sp.acosh,
    "atanh": sp.atanh,
    "acoth": sp.acoth,
    # exp / log / roots / powers
    "exp": sp.exp,
    "log": sp.log,
    "ln": sp.log,
    "log10": _log10,
    "log2": _log2,
    "sqrt": sp.sqrt,
    "cbrt": sp.cbrt,
    "root": _nroot,
    "Pow": sp.Pow,
    # rounding / misc numeric
    "Abs": sp.Abs,
    "abs": sp.Abs,
    "floor": sp.floor,
    "ceiling": sp.ceiling,
    "ceil": sp.ceiling,
    "round": sp.Function("round"),  # replaced below
    "sign": sp.sign,
    "Min": sp.Min,
    "Max": sp.Max,
    "min": sp.Min,
    "max": sp.Max,
    "Mod": sp.Mod,
    "mod": sp.Mod,
    "gcd": sp.gcd,
    "lcm": sp.lcm,
    "factorial": sp.factorial,
    "binomial": sp.binomial,
    "choose": sp.binomial,
    "gamma": sp.gamma,
    "beta": sp.beta,
    "erf": sp.erf,
    "erfc": sp.erfc,
    "zeta": sp.zeta,
    "fibonacci": sp.fibonacci,
    "isprime": sp.isprime,
    "prime": sp.prime,
    "primefactors": sp.primefactors,
    "factorint": sp.factorint,
    "divisors": sp.divisors,
    "totient": sp.totient,
    "harmonic": sp.harmonic,
    # complex
    "re": sp.re,
    "im": sp.im,
    "arg": sp.arg,
    "conjugate": sp.conjugate,
    "polar": sp.polar_lift,
    # numbers / structures
    "Rational": sp.Rational,
    "Integer": sp.Integer,
    "Float": sp.Float,
    "Symbol": sp.Symbol,
    "Function": sp.Function,
    "Matrix": sp.Matrix,
    "eye": sp.eye,
    "zeros": sp.zeros,
    "ones": sp.ones,
    "Eq": sp.Eq,
    "Ne": sp.Ne,
    "Lt": sp.Lt,
    "Le": sp.Le,
    "Gt": sp.Gt,
    "Ge": sp.Ge,
    "And": sp.And,
    "Or": sp.Or,
    "Not": sp.Not,
    "Piecewise": sp.Piecewise,
    # calculus inside expressions
    "Sum": sp.Sum,
    "sum": sp.Sum,
    "Product": sp.Product,
    "product": sp.Product,
    "Integral": sp.Integral,
    "Derivative": sp.Derivative,
    "diff": sp.diff,
    "integrate": sp.integrate,
    "limit": sp.limit,
    "series": sp.series,
    "simplify": sp.simplify,
    "expand": sp.expand,
    "factor": sp.factor,
    "N": sp.N,
    "nsimplify": sp.nsimplify,
    # no builtins
    "__builtins__": {},
}
class _Round(sp.Function):
    """``round(x, n)`` that waits until ``x`` is numeric — so it survives parsing with symbols in it and fires once ``vars`` are substituted. Half-up on the decimal value, returned exactly (``round(2.675, 2)`` is ``67/25``, i.e. 2.68)."""

    nargs = (1, 2)

    @classmethod
    def eval(cls, x: Any, n: Any = sp.S.Zero) -> Any:
        if not (x.is_number and n.is_number and n.is_integer):
            return None
        from decimal import ROUND_HALF_UP, Decimal

        places = int(n)
        # a literal like 2.675 is a binary Float underneath (2.67499999…); its printed value is what the caller meant
        d = Decimal(str(x)) if isinstance(x, sp.Float) else Decimal(str(sp.N(x, 40)))
        q = d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        return sp.Rational(str(q))


_Round.__name__ = _Round.__qualname__ = "round"  # so an unevaluated call prints as round(y, 1), not _Round(y, 1)
_SAFE_NAMES["round"] = _Round

_FORBIDDEN = re.compile(
    r"(__|\bimport\b|\blambda\b|\bexec\b|\beval\b|\bopen\b|\bgetattr\b|\bsetattr\b"
    r"|\bglobals\b|\blocals\b|\bcompile\b|\bvars\b|\bdir\b|\bwhile\b|\bfor\b|\bdef\b"
    r"|\bclass\b|\bwith\b|\byield\b|\breturn\b|\.\s*[A-Za-z_]|;|:=|\"|'|`|\\)"
)

_UNICODE_MAP = {
    "×": "*",
    "÷": "/",
    "−": "-",
    "–": "-",
    "²": "^2",
    "³": "^3",
    "√": "sqrt",  # bare `√2` is bracketed by _preprocess; see _ROOT_BARE
    "π": "pi",
    "∞": "oo",
    "≤": "<=",
    "≥": ">=",
    "≠": "!=",
    "·": "*",
    "ⅇ": "E",
    "ℯ": "E",
    "θ": "theta",
    "α": "alpha",
    "β": "beta_",
    "λ": "lambda_",
    "μ": "mu",
    "σ": "sigma",
    "ω": "omega",
    "Δ": "Delta",
}

_PCT_OF = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*of\s*")
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_ANGLE_NOTATION = re.compile(r"([\w.()]+)\s*∠\s*([\w.()°-]+)")
_ARC = re.compile(r"\barc(sin|cos|tan|cot|sec|csc)\b")
_MOD_INFIX = re.compile(r"(\([^()]*\)|[\w.]+)\s+mod\s+(\([^()]*\)|[\w.]+)")


#: `√` followed by a number or an identifier, rather than by a bracket.
_ROOT_BARE = re.compile(r"√\s*(\d+(?:\.\d+)?|[A-Za-z_]\w*)")


def _preprocess(src: str) -> tuple[str, list[str]]:
    """Normalise human/LLM-written math into parser-friendly text."""
    assumptions: list[str] = []
    s = src.strip()
    # `√` becomes `sqrt`, and implicit multiplication then reads `sqrt2` as one symbol -
    # so `√2 × π ÷ 3` produced a symbol called sqrt2 (#28 SS3.10). Bracket the operand while
    # the sign is still there and unambiguous.
    s = _ROOT_BARE.sub(r"sqrt(\1)", s)
    for k, v in _UNICODE_MAP.items():
        s = s.replace(k, v)
    s = _ARC.sub(r"a\1", s)
    if "∠" in s:
        s = _ANGLE_NOTATION.sub(r"((\1)*exp(I*(\2)))", s)
        assumptions.append("r∠θ read as r·e^(iθ)")
    if "°" in s:
        s = s.replace("°", "*deg")
        assumptions.append("° converted to radians via ·π/180")
    if "%" in s:
        s2 = _PCT_OF.sub(r"(\1/100)*", s)
        s2 = _PCT.sub(r"(\1/100)", s2)
        if s2 != s:
            assumptions.append("% read as /100")
        s = s2
    s = _MOD_INFIX.sub(r"Mod(\1, \2)", s)
    if re.search(r"(?<![A-Za-z_])e(?![A-Za-z_0-9])", s) and not re.search(r"\d[eE][+-]?\d", s):
        assumptions.append("e read as Euler's number")
    if re.search(r"(?<![A-Za-z_])i(?![A-Za-z_0-9])", s):
        assumptions.append("i read as the imaginary unit")
    return s, assumptions


# --------------------------------------------------------------------------- #
# Result-size estimate (#28 SS2g)
#
# SymPy evaluates `Integer ** Integer` while *parsing*, so `9^9^9^9` never returns and
# no timeout inside the tool can stop it - CPython delivers async exceptions only at a
# bytecode boundary and `int.__pow__` never reaches one. The only fix that works at this
# layer is to not start: walk the expression as a Python AST first and estimate, in
# log10 space, how many digits the answer would have. It costs microseconds and never
# multiplies anything.
# --------------------------------------------------------------------------- #

_ASTRONOMICAL = float("inf")
#: Digits a *value* may have before we stop materialising it and switch to estimating.
_VALUE_DIGITS = 15
#: An exponent with more digits than this makes the result beyond any bound worth naming.
_UNBOUNDED_EXPONENT_DIGITS = 12
#: Functions that grow fast enough for their argument alone to decide the answer's size.
_GROWERS = ("factorial", "gamma", "exp")
_LOG10_E = _pymath.log10(_pymath.e)


def _digits(v: float) -> float:
    """log10 of a magnitude, floored at 0 - a value below 1 does not shrink a result."""
    return max(0.0, _pymath.log10(abs(v))) if v else 0.0


def _value_of(node: ast.AST) -> float | int | None:
    """What a literal-only node evaluates to, or ``None`` when it is not literal or too big.

    Nothing here can produce a large intermediate: every operation is size-checked in
    log space before it runs.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, (int, float)) and not isinstance(node.value, bool) else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _value_of(node.operand)
        return None if v is None else (-v if isinstance(node.op, ast.USub) else v)
    if isinstance(node, ast.BinOp):
        a, b = _value_of(node.left), _value_of(node.right)
        if a is None or b is None:
            return None
        op = type(node.op)
        try:
            if op is ast.Add:
                return a + b
            if op is ast.Sub:
                return a - b
            if op is ast.Mult:
                return a * b if _digits(a) + _digits(b) <= _VALUE_DIGITS else None
            if op is ast.Div:
                return a / b if b else None
            if op is ast.Pow:
                return a**b if b <= 0 or b * _digits(a) <= _VALUE_DIGITS else None
        except (ArithmeticError, ValueError, TypeError):
            return None
    return None


def _size_of(node: ast.AST) -> float | None:
    """Estimated digits in what ``node`` evaluates to.

    ``None`` when the node is not literal - a symbol, an unknown function - and the size
    therefore cannot be estimated at all; ``inf`` when it is past any useful bound.
    """
    value = _value_of(node)
    if value is not None:
        return _digits(value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _size_of(node.operand)
    if isinstance(node, ast.BinOp):
        left, right = _size_of(node.left), _size_of(node.right)
        if left is None or right is None:
            return None
        op = type(node.op)
        if op in (ast.Add, ast.Sub):
            return max(left, right) + 1
        if op is ast.Mult:
            return left + right
        if op is ast.Div:
            return left
        if op is ast.Pow:
            exponent = _value_of(node.right)
            if exponent is None:  # the exponent itself is already too big to write down
                return _ASTRONOMICAL if right > _UNBOUNDED_EXPONENT_DIGITS else 10**right * left
            return 0.0 if exponent <= 0 else exponent * left
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _GROWERS:
        if len(node.args) != 1:
            return None
        n = _value_of(node.args[0])
        if n is None:
            return None if _size_of(node.args[0]) is None else _ASTRONOMICAL
        if node.func.id == "exp":
            return max(0.0, n * _LOG10_E)
        n = n - 1 if node.func.id == "gamma" else n  # gamma(n) = (n-1)!
        if n < 2:
            return 0.0
        # Stirling: log10(n!) = n*log10(n/e) + log10(2*pi*n)/2
        return n * (_pymath.log10(n) - _LOG10_E) + _pymath.log10(2 * _pymath.pi * n) / 2
    return None


def _check_result_size(s: str) -> None:
    """Refuse an expression whose answer could not be returned, before evaluating it."""
    try:
        tree = ast.parse(s.replace("^", "**"), mode="eval")
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return  # not Python-shaped (implicit multiplication, factorials); SymPy will judge it
    try:
        digits = _size_of(tree.body)
    except RecursionError:
        return
    if digits is None or digits <= MAX_RESULT_DIGITS:
        return
    estimated = f"more than 10^{_UNBOUNDED_EXPONENT_DIGITS}" if digits == _ASTRONOMICAL else int(digits)
    raise TooLarge(
        f"the result would have {estimated if isinstance(estimated, str) else format(estimated, ',')} digits; "
        f"the most that can be returned is {MAX_RESULT_DIGITS:,}",
        details={"estimated_digits": estimated, "limit_digits": MAX_RESULT_DIGITS},
        hint="Reduce the exponent, or evaluate a smaller sub-expression.",
    )


def _bounded(raw: Any, field: str, cap: int, unit: str) -> int:
    """A whole number in 1..cap. Over the cap is `too_large`; anything else is a bad value."""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise ToolError(f"{field} must be a whole number of {unit}") from None
    if n < 1:
        raise ToolError(f"{field} must be at least 1")
    if n > cap:
        raise TooLarge(
            f"{field} is {n:,} {unit}; the most this mode returns is {cap:,}",
            details={field: n, "limit": cap},
            hint=f"Ask for at most {cap:,} {unit}.",
        )
    return n


def _check_safe(s: str) -> None:
    if len(s) > MAX_EXPR_LEN:
        raise ToolError(f"expression too long (> {MAX_EXPR_LEN} chars)")
    m = _FORBIDDEN.search(s)
    if m:
        raise ToolError(f"disallowed token in expression: {m.group(0)!r}")
    _check_result_size(s)


def _parse(
    src: str,
    *,
    angle: str | None = None,
    local: dict[str, Any] | None = None,
) -> tuple[Any, list[str]]:
    if not isinstance(src, str) or not src.strip():
        raise ToolError("expression is empty")
    s, assumptions = _preprocess(src)
    _check_safe(s)
    unknown = sorted({m.group(1) for m in re.finditer(r"(?<![\w.])([A-Za-z_]\w*)\s*\(", s) if m.group(1) not in _SAFE_NAMES and m.group(1) not in (local or {})})
    if unknown:
        raise ToolError(f"unknown function(s): {', '.join(unknown)}")
    try:
        expr = parse_expr(
            s,
            local_dict=dict(local or {}),
            global_dict=dict(_SAFE_NAMES),
            transformations=_TRANSFORMS,
        )
    except (SyntaxError, TypeError, ValueError, AttributeError, sp.SympifyError) as e:
        raise ToolError(f"could not parse {src!r}: {e}") from None
    except Exception as e:  # tokenizer errors etc.
        raise ToolError(f"could not parse {src!r}: {type(e).__name__}: {e}") from None

    if isinstance(expr, sp.Basic):
        undef = [f for f in expr.atoms(AppliedUndef)]
        if undef and not (local and any(str(f.func) in local for f in undef)):
            names = sorted({str(f.func) for f in undef})
            raise ToolError(f"unknown function(s): {', '.join(names)}")
        expr, a2 = _apply_angle(expr, angle, s)
        assumptions += a2
    return expr, assumptions


def _uses_trig(expr: Any) -> bool:
    if not isinstance(expr, sp.Basic):
        return False
    funcs = {type(f) for f in expr.atoms(sp.Function)}
    trig = {getattr(sp, n) for n in _TRIG + _INV_TRIG}
    return bool(funcs & trig)


def _apply_angle(expr: Any, angle: str | None, src: str) -> tuple[Any, list[str]]:
    """Convert trig arguments/results when the caller works in degrees."""
    if not _uses_trig(expr):
        return expr, []
    if "deg" in src:  # explicit ° already handled
        if angle == "deg":
            return expr, ["° present in expression; angle='deg' ignored for those terms"]
        return expr, []
    if angle is None:
        raise Ambiguous(
            "expression uses trigonometry; specify angle='rad' or angle='deg'",
            field="angle",
            options=["rad", "deg"],
        )
    if angle == "rad":
        return expr, []
    if angle != "deg":
        raise ToolError("angle must be 'rad' or 'deg'")
    for n in _TRIG:
        f = getattr(sp, n)
        expr = expr.replace(f, lambda a, f=f: f(a * _DEG))
    for n in _INV_TRIG:
        f = getattr(sp, n)
        expr = expr.replace(f, lambda a, f=f: f(a) / _DEG)
    return expr, ["trig evaluated in degrees"]


# --------------------------------------------------------------------------- #
# Execution helpers
# --------------------------------------------------------------------------- #


def _run(fn: Callable[[], Any], timeout: float) -> Any:
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["v"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise Timeout(f"computation exceeded {timeout:g}s")
    if "e" in box:
        raise box["e"]
    return box["v"]


def _clean_decimal(x: Any) -> str:
    s = str(x)
    if re.fullmatch(r"-?\d+\.\d*0+", s):
        s = s.rstrip("0").rstrip(".")
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _describe(expr: Any, precision: int = 15) -> dict[str, Any]:
    """Standard multi-form description of a SymPy object."""
    out: dict[str, Any] = {"value": sp.sstr(expr)}
    try:
        out["latex"] = sp.latex(expr)
    except Exception:  # pragma: no cover
        pass

    if isinstance(expr, sp.MatrixBase):
        out["type"] = "matrix"
        out["shape"] = list(expr.shape)
        out["rows"] = [[sp.sstr(v) for v in row] for row in expr.tolist()]
        try:
            out["rows_decimal"] = [
                [_clean_decimal(sp.N(v, precision)) for v in row] for row in expr.tolist()
            ]
        except Exception:  # pragma: no cover
            pass
        return out

    if isinstance(expr, (list, tuple)):
        out["type"] = "list"
        out["items"] = [_describe(v, precision) for v in expr]
        return out

    if isinstance(expr, dict):
        out["type"] = "mapping"
        out["items"] = {sp.sstr(k): _describe(v, precision) for k, v in expr.items()}
        return out

    if isinstance(expr, sp.Set):
        out["type"] = "set"
        if isinstance(expr, sp.FiniteSet):
            out["items"] = [_describe(v, precision) for v in expr]
        return out

    if isinstance(expr, sp.Basic):
        if expr.is_Relational or isinstance(expr, sp.logic.boolalg.Boolean):
            out["type"] = "relation"
            return out
        free = expr.free_symbols
        if free:
            out["type"] = "expression"
            out["free_symbols"] = sorted(str(s) for s in free)
            return out
        if expr.is_number:
            exact = sp.nsimplify(expr) if expr.is_Float else expr
            out["exact"] = sp.sstr(exact)
            try:
                dec = sp.N(expr, precision)
                if dec.is_real:
                    out["decimal"] = _clean_decimal(dec)
                    out["type"] = "number"
                    if exact.is_Rational and not exact.is_Integer:
                        out["fraction"] = sp.sstr(exact)
                        out["numerator"] = int(exact.p)
                        out["denominator"] = int(exact.q)
                    if exact.is_Integer:
                        out["integer"] = int(exact)
                else:
                    re_, im_ = sp.N(sp.re(expr), precision), sp.N(sp.im(expr), precision)
                    out["type"] = "complex"
                    out["decimal"] = f"{_clean_decimal(re_)} + {_clean_decimal(im_)}i".replace(
                        "+ -", "- "
                    )
                    out["re"] = _clean_decimal(re_)
                    out["im"] = _clean_decimal(im_)
                    out["modulus"] = _clean_decimal(sp.N(sp.Abs(expr), precision))
                    arg = sp.N(sp.arg(expr), precision)
                    out["arg_rad"] = _clean_decimal(arg)
                    out["arg_deg"] = _clean_decimal(sp.N(arg / _DEG, precision))
            except Exception:  # pragma: no cover
                out["type"] = "number"
            return out
    out["type"] = type(expr).__name__
    return out


def _symbol(name: str | None, expr: Any, field: str = "var") -> sp.Symbol:
    """Resolve the variable to operate on, refusing to guess when ambiguous."""
    free = sorted(expr.free_symbols, key=str) if isinstance(expr, sp.Basic) else []
    if name:
        for s in free:
            if str(s) == name:
                return s
        return sp.Symbol(name)
    if len(free) == 1:
        return free[0]
    if not free:
        return sp.Symbol("x")
    raise Ambiguous(
        f"expression has several variables; specify {field}",
        field=field,
        options=[str(s) for s in free],
    )


def _num(x: Any) -> Any:
    """Coerce a JSON number/string into an exact SymPy number."""
    if isinstance(x, bool):
        raise ToolError("booleans are not numbers")
    if isinstance(x, int):
        return sp.Integer(x)
    if isinstance(x, float):
        return sp.Rational(Fraction(repr(x)))
    if isinstance(x, str):
        e, _ = _parse(x)
        return e
    if isinstance(x, sp.Basic):
        return x
    raise ToolError(f"not a number: {x!r}")


# --------------------------------------------------------------------------- #
# Mode implementations
# --------------------------------------------------------------------------- #


def _check_defined(expr: Any, src: str) -> None:
    """Refuse an expression with no value.

    `1/0` and `tan(pi/2)` came back as SymPy's complex infinity, rendered `zoo` with a
    decimal of `nan + nani`. That is a wrong answer wearing the shape of a right one:
    division by zero and a trig pole are undefined, and the caller needs to be told so
    rather than handed a NaN to carry (#28 SS2d).
    """
    if not isinstance(expr, sp.Basic) or not expr.has(sp.zoo, sp.nan):
        return
    raise ToolError(
        f"{src} is undefined - it divides by zero or hits a pole (SymPy calls the result "
        f"complex infinity)",
        details={"expr": src},
        hint="Take a limit instead: mode='limit' reports what the expression approaches.",
    )


def _mode_eval(p: dict[str, Any], exact_only: bool) -> dict[str, Any]:
    expr, assumptions = _parse(p["expr"], angle=p.get("angle"))
    subs: dict[str, Any] = {}
    if p.get("vars"):
        for k, v in p["vars"].items():
            subs[sp.Symbol(k)] = _num(v)
        expr = expr.subs(subs)
    if exact_only:
        expr = sp.nsimplify(expr, rational=True)
    elif isinstance(expr, sp.Basic):
        expr = sp.simplify(expr) if expr.free_symbols else sp.expand(expr)
    _check_defined(expr, p["expr"])
    d = _describe(expr, p.get("precision", 15))
    if exact_only:
        d.pop("decimal", None)
    return ok(d, assumptions=assumptions)


def _mode_transform(p: dict[str, Any], fn: Callable[[Any], Any]) -> dict[str, Any]:
    expr, assumptions = _parse(p["expr"], angle=p.get("angle") or "rad")
    return ok(_describe(fn(expr), p.get("precision", 15)), assumptions=assumptions)


def _split_eq(s: str) -> Any:
    """Turn 'lhs = rhs' into Eq(lhs, rhs); pass relationals through."""
    if "==" in s:
        s = s.replace("==", "=")
    if re.search(r"(?<![<>!=])=(?!=)", s):
        parts = re.split(r"(?<![<>!=])=(?!=)", s)
        if len(parts) != 2:
            raise ToolError(f"equation must contain exactly one '=': {s!r}")
        lhs, _ = _parse(parts[0], angle="rad")
        rhs, _ = _parse(parts[1], angle="rad")
        return sp.Eq(lhs, rhs)
    e, _ = _parse(s, angle="rad")
    return e


def _mode_solve(p: dict[str, Any]) -> dict[str, Any]:
    raw = p.get("equations") or ([p["expr"]] if p.get("expr") else None)
    if not raw:
        raise ToolError("solve needs 'equations' (list of strings) or 'expr'")
    if isinstance(raw, str):
        raw = [raw]
    domain = (p.get("domain") or "complex").lower()
    assumptions: list[str] = []
    eqs = [_split_eq(s) for s in raw]
    free: set[sp.Symbol] = set()
    for e in eqs:
        free |= e.free_symbols
    names = p.get("vars")
    if names:
        if isinstance(names, str):
            names = [names]
        syms = [sp.Symbol(n) for n in names]
    else:
        syms = sorted(free, key=str)
        if len(syms) > len(eqs):
            raise Ambiguous(
                "more unknowns than equations; specify vars to solve for",
                field="vars",
                options=[str(s) for s in syms],
            )
    if domain == "real":
        real_syms = {s: sp.Symbol(str(s), real=True) for s in syms}
        eqs = [e.subs(real_syms) for e in eqs]
        syms = [real_syms[s] for s in syms]
        assumptions.append("variables assumed real")
    elif domain in ("complex", "c"):
        assumptions.append("variables assumed complex (use domain='real' to restrict)")
    elif domain in ("integer", "z"):
        int_syms = {s: sp.Symbol(str(s), integer=True) for s in syms}
        eqs = [e.subs(int_syms) for e in eqs]
        syms = [int_syms[s] for s in syms]
        assumptions.append("variables assumed integer")
    elif domain in ("positive",):
        pos = {s: sp.Symbol(str(s), positive=True) for s in syms}
        eqs = [e.subs(pos) for e in eqs]
        syms = [pos[s] for s in syms]
        assumptions.append("variables assumed positive")
    else:
        raise ToolError("domain must be one of complex, real, integer, positive")

    precision = p.get("precision", 15)
    is_ineq = any(e.is_Relational and not isinstance(e, sp.Eq) for e in eqs)
    try:
        if is_ineq:
            if len(syms) != 1:
                raise Unsupported("inequalities are solved for a single variable")
            sol = sp.reduce_inequalities(eqs, syms[0])
            return ok({"solution": _describe(sol, precision), "count": None}, assumptions=assumptions)
        sols = sp.solve(eqs, syms, dict=True)
    except NotImplementedError:
        if len(eqs) == 1 and len(syms) == 1:
            dom = sp.S.Reals if domain == "real" else sp.S.Complexes
            sset = sp.solveset(eqs[0], syms[0], domain=dom)
            return ok(
                {"solution": _describe(sset, precision), "count": None},
                assumptions=assumptions,
                warnings=["returned as a set; solve() could not enumerate roots"],
            )
        raise Unsupported("no closed-form solution found") from None

    solutions = []
    for s in sols:
        entry = {}
        for k, v in s.items():
            entry[str(k)] = _describe(v, precision)
        solutions.append(entry)
    warnings = []
    if not solutions:
        # A degree-40 polynomial has 40 complex roots. "no solutions found" said the
        # opposite of the truth; what solve() means is "no closed form" (#28 SS2d).
        numeric = _numeric_roots(eqs, syms, precision)
        if numeric is not None:
            return ok(
                {"solutions": numeric, "count": len(numeric)},
                assumptions=assumptions + ["no closed form exists, so the roots were found numerically (nroots)"],
                warnings=["these are numeric approximations, not exact values"],
            )
        raise Unsupported(
            "no closed form exists for this equation and its roots could not be found numerically",
            details={"equations": [str(e) for e in eqs]},
            hint="Try mode='solve' on a simpler form, or evaluate the expression at points with plot_points.",
        )
    return ok({"solutions": solutions, "count": len(solutions)}, assumptions=assumptions, warnings=warnings)


def _numeric_roots(eqs: list[Any], syms: list[Any], precision: int) -> list[dict[str, Any]] | None:
    """Roots of a single univariate polynomial, when no closed form exists."""
    if len(eqs) != 1 or len(syms) != 1:
        return None
    eq = eqs[0]
    expr = eq.lhs - eq.rhs if isinstance(eq, sp.Eq) else eq
    try:
        poly = sp.Poly(expr, syms[0])
    except (sp.PolynomialError, sp.GeneratorsNeeded, TypeError):
        return None
    if poly.degree() < 1 or poly.degree() > MAX_NUMERIC_DEGREE:
        return None
    try:
        roots = poly.nroots(n=min(precision, 15), maxsteps=100)
    except Exception:
        return None
    return [{str(syms[0]): _describe(r, precision)} for r in roots]


def _mode_diff(p: dict[str, Any]) -> dict[str, Any]:
    expr, assumptions = _parse(p["expr"], angle=p.get("angle") or "rad")
    var = _symbol(p.get("var"), expr)
    order = int(p.get("order", 1))
    res = sp.diff(expr, var, order)
    d = _describe(sp.simplify(res), p.get("precision", 15))
    if p.get("at") is not None:
        d["at"] = _describe(res.subs(var, _num(p["at"])), p.get("precision", 15))
    return ok(d, assumptions=assumptions, steps=[f"d^{order}/d{var}^{order} of {sp.sstr(expr)}"])


def _mode_integrate(p: dict[str, Any]) -> dict[str, Any]:
    expr, assumptions = _parse(p["expr"], angle=p.get("angle") or "rad")
    var = _symbol(p.get("var"), expr)
    lo, hi = p.get("lower"), p.get("upper")
    precision = p.get("precision", 15)
    warnings: list[str] = []
    if (lo is None) != (hi is None):
        raise ToolError("definite integral needs both 'lower' and 'upper'")
    if lo is None:
        res = sp.integrate(expr, var)
        if isinstance(res, sp.Integral) or res.has(sp.Integral):
            warnings.append("no closed form found; returned unevaluated integral")
        d = _describe(res, precision)
        d["value"] = d["value"] + " + C"
        d["latex"] = d.get("latex", "") + " + C"
        return ok(d, assumptions=assumptions, warnings=warnings)
    a, b = _num(lo), _num(hi)
    res = sp.integrate(expr, (var, a, b))
    if isinstance(res, sp.Integral) or res.has(sp.Integral):
        warnings.append("no closed form; value below is numeric")
        res = sp.Integral(expr, (var, a, b)).evalf(precision)
    d = _describe(res, precision)
    return ok(d, assumptions=assumptions, warnings=warnings, steps=[f"∫_{a}^{b} {sp.sstr(expr)} d{var}"])


def _mode_limit(p: dict[str, Any]) -> dict[str, Any]:
    expr, assumptions = _parse(p["expr"], angle=p.get("angle") or "rad")
    var = _symbol(p.get("var"), expr)
    point = _num(p.get("point", 0))
    side = p.get("side")
    dir_ = {"+": "+", "-": "-", "right": "+", "left": "-", None: "+-"}.get(side)
    if dir_ is None:
        raise ToolError("side must be '+', '-', 'left', 'right' or omitted")
    try:
        res = sp.limit(expr, var, point, dir_)
    except ValueError as e:
        if "two-sided" in str(e) or "does not exist" in str(e):
            left = sp.limit(expr, var, point, "-")
            right = sp.limit(expr, var, point, "+")
            return ok(
                {"exists": False, "left": _describe(left), "right": _describe(right)},
                assumptions=assumptions,
                warnings=["two-sided limit does not exist"],
            )
        raise
    d = _describe(res, p.get("precision", 15))
    d["exists"] = True
    return ok(d, assumptions=assumptions)


def _mode_series(p: dict[str, Any]) -> dict[str, Any]:
    expr, assumptions = _parse(p["expr"], angle=p.get("angle") or "rad")
    var = _symbol(p.get("var"), expr)
    at = _num(p.get("at", 0))
    order = _bounded(p.get("order", 6), "order", MAX_SERIES_ORDER, "terms")
    s = sp.series(expr, var, at, order)
    d = _describe(s, p.get("precision", 15))
    d["polynomial"] = sp.sstr(s.removeO())
    d["polynomial_latex"] = sp.latex(s.removeO())
    return ok(d, assumptions=assumptions)


_PRIMES = re.compile(r"\b([A-Za-z_]\w*)('+)")


def _mode_ode(p: dict[str, Any]) -> dict[str, Any]:
    eq_src = p.get("equation") or p.get("expr")
    if not eq_src:
        raise ToolError("ode needs 'equation'")
    func = p.get("func") or "y(x)"
    m = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*(?:\(\s*([A-Za-z_]\w*)\s*\))?\s*", func)
    if not m:
        raise ToolError("func must look like 'y' or 'y(x)'")
    fname, xname = m.group(1), m.group(2) or "x"
    x = sp.Symbol(xname)
    f = sp.Function(fname)
    s = eq_src
    s = re.sub(rf"d\^?2\s*{fname}\s*/\s*d{xname}\^?2", f"Derivative({fname}({xname}),({xname},2))", s)
    s = re.sub(rf"d{fname}\s*/\s*d{xname}", f"Derivative({fname}({xname}),{xname})", s)

    def _prime(mm: re.Match[str]) -> str:
        name, primes = mm.group(1), len(mm.group(2))
        if name != fname:
            return mm.group(0)
        return f"Derivative({fname}({xname}),({xname},{primes}))"

    s = _PRIMES.sub(_prime, s)
    s = re.sub(rf"\b{fname}\b(?!\s*\()", f"{fname}({xname})", s)
    local = {fname: f, xname: x}
    if "=" in s:
        lhs, rhs = s.split("=", 1)
        eq = sp.Eq(_parse(lhs, angle="rad", local=local)[0], _parse(rhs, angle="rad", local=local)[0])
    else:
        eq = sp.Eq(_parse(s, angle="rad", local=local)[0], 0)
    ics = None
    if p.get("ics"):
        ics = {}
        for k, v in p["ics"].items():
            km = re.fullmatch(rf"\s*{fname}('*)\s*\(\s*([^)]+)\)\s*", k)
            if not km:
                raise ToolError(f"initial condition key must look like y(0) or y'(0): {k!r}")
            n = len(km.group(1))
            at = _num(km.group(2))
            key = f(x).diff(x, n).subs(x, at) if n else f(at)
            ics[key] = _num(v)
    sol = sp.dsolve(eq, f(x), ics=ics)
    hints = sp.classify_ode(eq, f(x))
    d = _describe(sol, p.get("precision", 15))
    d["classification"] = list(hints[:5])
    return ok(d, steps=[f"equation: {sp.sstr(eq)}"])


def _matrix(rows: Any, name: str) -> sp.Matrix:
    if rows is None:
        raise ToolError(f"matrix '{name}' is required")
    if isinstance(rows, str):
        e, _ = _parse(rows)
        if not isinstance(e, sp.MatrixBase):
            raise ToolError(f"'{name}' did not parse as a matrix")
        return sp.Matrix(e)
    if not isinstance(rows, list) or not rows:
        raise ToolError(f"'{name}' must be a non-empty nested list")
    if not isinstance(rows[0], list):
        rows = [rows]  # a vector
    return sp.Matrix([[_num(v) for v in r] for r in rows])


def _mode_matrix(p: dict[str, Any]) -> dict[str, Any]:
    op = (p.get("op") or "det").lower()
    precision = p.get("precision", 15)
    A = _matrix(p.get("A") if p.get("A") is not None else p.get("expr"), "A")
    if op == "det":
        if not A.is_square:
            raise ToolError("determinant needs a square matrix")
        return ok(_describe(A.det(), precision))
    if op == "inv":
        if not A.is_square or A.det() == 0:
            raise ToolError("matrix is singular; no inverse")
        return ok(_describe(A.inv(), precision))
    if op in ("transpose", "T"):
        return ok(_describe(A.T, precision))
    if op == "rank":
        return ok({"value": A.rank(), "type": "number"})
    if op == "trace":
        return ok(_describe(A.trace(), precision))
    if op == "rref":
        R, pivots = A.rref()
        d = _describe(R, precision)
        d["pivots"] = list(pivots)
        return ok(d)
    if op == "nullspace":
        return ok(_describe([v for v in A.nullspace()], precision))
    if op == "eig":
        vals = A.eigenvals()
        vecs = A.eigenvects()
        return ok(
            {
                "eigenvalues": [
                    {"value": _describe(v, precision), "multiplicity": int(m)} for v, m in vals.items()
                ],
                "eigenvectors": [
                    {
                        "eigenvalue": _describe(v, precision),
                        "vectors": [_describe(vec, precision) for vec in vs],
                    }
                    for v, _m, vs in vecs
                ],
                "characteristic_polynomial": sp.sstr(A.charpoly().as_expr()),
            }
        )
    if op == "solve":
        b = _matrix(p.get("b"), "b")
        if b.shape[0] != A.shape[0]:
            b = b.T
        try:
            x = A.LUsolve(b) if A.is_square else A.solve_least_squares(b)
        except Exception:
            sol = sp.linsolve((A, b))
            return ok(_describe(sol, precision), warnings=["system is singular; general solution returned"])
        return ok(_describe(x, precision))
    if op in ("mul", "add", "sub"):
        B = _matrix(p.get("B"), "B")
        if op == "mul":
            if A.shape[1] != B.shape[0]:
                raise ToolError(f"shape mismatch for multiplication: {A.shape} x {B.shape}")
            return ok(_describe(A * B, precision))
        if A.shape != B.shape:
            raise ToolError(f"shape mismatch: {A.shape} vs {B.shape}")
        return ok(_describe(A + B if op == "add" else A - B, precision))
    if op == "pow":
        n = int(p.get("n", 2))
        return ok(_describe(A**n, precision))
    raise ToolError(
        "op must be one of det, inv, transpose, rank, trace, rref, nullspace, eig, solve, mul, add, sub, pow"
    )


def _fractions(data: Any) -> list[Fraction]:
    if not isinstance(data, list) or not data:
        raise ToolError("data must be a non-empty list of numbers")
    out = []
    for v in data:
        if isinstance(v, bool):
            raise ToolError("booleans are not numbers")
        if isinstance(v, int):
            out.append(Fraction(v))
        elif isinstance(v, float):
            out.append(Fraction(repr(v)))
        elif isinstance(v, str):
            try:
                out.append(Fraction(v.strip().replace(",", "")))
            except ValueError:
                e, _ = _parse(v)
                out.append(Fraction(str(sp.Rational(e))))
        else:
            raise ToolError(f"not a number: {v!r}")
    return out


def _frac_out(x: Fraction | Any, precision: int = 15) -> dict[str, Any]:
    if isinstance(x, Fraction):
        return _describe(sp.Rational(x.numerator, x.denominator), precision)
    return _describe(x, precision)


def _percentile(xs: list[Fraction], q: Fraction) -> Fraction:
    """Linear interpolation (numpy/Excel PERCENTILE.INC convention)."""
    if not 0 <= q <= 100:
        raise ToolError("percentile must be within 0..100")
    xs = sorted(xs)
    n = len(xs)
    h = (n - 1) * q / 100
    lo = int(h)
    if lo + 1 >= n:
        return xs[-1]
    return xs[lo] + (h - lo) * (xs[lo + 1] - xs[lo])


def _mode_stats(p: dict[str, Any]) -> dict[str, Any]:
    op = (p.get("op") or "describe").lower()
    precision = p.get("precision", 15)
    xs = _fractions(p.get("data"))
    n = len(xs)
    total = sum(xs)
    mean = total / n

    def var(sample: bool) -> Fraction:
        d = n - 1 if sample else n
        if d == 0:
            raise ToolError("sample variance needs at least 2 data points")
        return sum((x - mean) ** 2 for x in xs) / d

    def sd(sample: bool) -> Any:
        v = var(sample)
        return sp.sqrt(sp.Rational(v.numerator, v.denominator))

    def median() -> Fraction:
        s = sorted(xs)
        m = n // 2
        return s[m] if n % 2 else (s[m - 1] + s[m]) / 2

    if op == "describe":
        s = sorted(xs)
        out = {
            "count": n,
            "sum": _frac_out(total, precision),
            "mean": _frac_out(mean, precision),
            "median": _frac_out(median(), precision),
            "min": _frac_out(s[0], precision),
            "max": _frac_out(s[-1], precision),
            "range": _frac_out(s[-1] - s[0], precision),
            "q1": _frac_out(_percentile(xs, Fraction(25)), precision),
            "q3": _frac_out(_percentile(xs, Fraction(75)), precision),
        }
        if n > 1:
            out["stdev_sample"] = _frac_out(sd(True), precision)
            out["stdev_population"] = _frac_out(sd(False), precision)
            out["variance_sample"] = _frac_out(var(True), precision)
            out["variance_population"] = _frac_out(var(False), precision)
        return ok(out, assumptions=["stdev/variance reported for both sample (n-1) and population (n)"])
    if op in ("sum",):
        return ok(_frac_out(total, precision))
    if op == "mean":
        return ok(_frac_out(mean, precision))
    if op == "median":
        return ok(_frac_out(median(), precision))
    if op == "mode":
        from collections import Counter

        c = Counter(xs)
        top = max(c.values())
        modes = [k for k, v in c.items() if v == top]
        return ok({"modes": [_frac_out(m, precision) for m in modes], "frequency": top})
    if op in ("stdev", "stdev_sample", "sd"):
        return ok(_frac_out(sd(True), precision), assumptions=["sample standard deviation (n-1)"])
    if op in ("pstdev", "stdev_population"):
        return ok(_frac_out(sd(False), precision), assumptions=["population standard deviation (n)"])
    if op in ("variance", "variance_sample"):
        return ok(_frac_out(var(True), precision), assumptions=["sample variance (n-1)"])
    if op in ("pvariance", "variance_population"):
        return ok(_frac_out(var(False), precision), assumptions=["population variance (n)"])
    if op in ("min", "max", "range"):
        s = sorted(xs)
        v = {"min": s[0], "max": s[-1], "range": s[-1] - s[0]}[op]
        return ok(_frac_out(v, precision))
    if op == "percentile":
        q = p.get("percentile", p.get("p"))
        if q is None:
            raise ToolError("percentile needs 'percentile' (0..100)")
        return ok(
            _frac_out(_percentile(xs, Fraction(str(q))), precision),
            assumptions=["linear interpolation (Excel PERCENTILE.INC / numpy default)"],
        )
    if op == "quartiles":
        return ok(
            {
                "q1": _frac_out(_percentile(xs, Fraction(25)), precision),
                "q2": _frac_out(median(), precision),
                "q3": _frac_out(_percentile(xs, Fraction(75)), precision),
                "iqr": _frac_out(_percentile(xs, Fraction(75)) - _percentile(xs, Fraction(25)), precision),
            },
            assumptions=["linear interpolation (Excel PERCENTILE.INC / numpy default)"],
        )
    if op == "zscore":
        if p.get("value") is None:
            raise ToolError("zscore needs 'value'")
        v = _fractions([p["value"]])[0]
        z = (sp.Rational(v - mean) / sd(True)) if n > 1 else sp.nan
        return ok(_describe(sp.simplify(z), precision), assumptions=["sample standard deviation (n-1)"])
    if op in ("geometric_mean", "harmonic_mean"):
        if any(x <= 0 for x in xs):
            raise ToolError(f"{op} needs strictly positive data")
        if op == "harmonic_mean":
            return ok(_frac_out(n / sum(1 / x for x in xs), precision))
        prod = sp.Integer(1)
        for x in xs:
            prod *= sp.Rational(x.numerator, x.denominator)
        return ok(_describe(sp.root(prod, n), precision))
    if op == "weighted_mean":
        ws = _fractions(p.get("weights"))
        if len(ws) != n:
            raise ToolError("weights must match data length")
        return ok(_frac_out(sum(x * w for x, w in zip(xs, ws, strict=True)) / sum(ws), precision))
    if op == "cumsum":
        acc, out = Fraction(0), []
        for x in xs:
            acc += x
            out.append(_frac_out(acc, precision))
        return ok(out)
    if op in ("corr", "regress", "covariance"):
        ys = _fractions(p.get("y") or p.get("data2"))
        if len(ys) != n:
            raise ToolError("x and y must have the same length")
        my = sum(ys) / n
        sxy = sum((x - mean) * (y - my) for x, y in zip(xs, ys, strict=True))
        sxx = sum((x - mean) ** 2 for x in xs)
        syy = sum((y - my) ** 2 for y in ys)
        if op == "covariance":
            return ok(_frac_out(sxy / (n - 1), precision), assumptions=["sample covariance (n-1)"])
        if sxx == 0 or syy == 0:
            raise ToolError("correlation undefined: zero variance")
        r = sp.Rational(sxy) / sp.sqrt(sp.Rational(sxx) * sp.Rational(syy))
        if op == "corr":
            return ok(_describe(sp.simplify(r), precision), assumptions=["Pearson correlation"])
        slope = sxy / sxx
        intercept = my - slope * mean
        r2 = Fraction(sxy * sxy) / (sxx * syy)
        out = {
            "slope": _frac_out(slope, precision),
            "intercept": _frac_out(intercept, precision),
            "r_squared": _frac_out(r2, precision),
            "equation": f"y = {float(slope):.6g}*x + {float(intercept):.6g}",
        }
        if p.get("predict") is not None:
            xv = _fractions([p["predict"]])[0]
            out["prediction"] = _frac_out(slope * xv + intercept, precision)
        return ok(out, assumptions=["ordinary least squares"])
    raise ToolError(
        "op must be one of describe, sum, mean, median, mode, stdev, pstdev, variance, pvariance, "
        "min, max, range, percentile, quartiles, zscore, geometric_mean, harmonic_mean, "
        "weighted_mean, cumsum, corr, covariance, regress"
    )


def _mode_convert_form(p: dict[str, Any]) -> dict[str, Any]:
    expr, assumptions = _parse(p["expr"], angle=p.get("angle") or "rad")
    form = (p.get("form") or "decimal").lower()
    precision = p.get("precision", 15)
    if form == "polar":
        r = sp.simplify(sp.Abs(expr))
        th = sp.simplify(sp.arg(expr))
        return ok(
            {
                "r": _describe(r, precision),
                "theta_rad": _describe(th, precision),
                "theta_deg": _describe(sp.simplify(th / _DEG), precision),
                "notation": f"{_clean_decimal(sp.N(r, 6))}∠{_clean_decimal(sp.N(th / _DEG, 6))}°",
            },
            assumptions=assumptions,
        )
    if form in ("rect", "rectangular", "cartesian"):
        e = sp.expand_complex(expr)
        d = _describe(e, precision)
        d["re"] = _describe(sp.re(e), precision)
        d["im"] = _describe(sp.im(e), precision)
        return ok(d, assumptions=assumptions)
    if form == "latex":
        return ok({"latex": sp.latex(expr), "value": sp.sstr(expr)}, assumptions=assumptions)
    if form == "decimal":
        return ok(_describe(sp.N(expr, precision), precision), assumptions=assumptions)
    if form in ("fraction", "rational", "exact"):
        tol = p.get("tolerance")
        e = sp.nsimplify(expr, rational=True, tolerance=tol)
        return ok(_describe(e, precision), assumptions=assumptions + (["tolerance applied"] if tol else []))
    if form == "scientific":
        v = sp.N(expr, precision)
        if not v.is_real:
            raise ToolError("scientific notation needs a real number")
        sig = int(p.get("significant", 6))
        return ok({"value": f"{float(v):.{sig - 1}e}", "decimal": _clean_decimal(v)}, assumptions=assumptions)
    if form == "percent":
        v = sp.N(expr * 100, precision)
        return ok({"value": f"{_clean_decimal(v)}%", "decimal": _clean_decimal(v)}, assumptions=assumptions)
    raise ToolError("form must be one of polar, rect, latex, decimal, fraction, scientific, percent")


def _mode_plot_points(p: dict[str, Any]) -> dict[str, Any]:
    expr, assumptions = _parse(p["expr"], angle=p.get("angle") or "rad")
    var = _symbol(p.get("var"), expr)
    rng = p.get("range") or [-10, 10]
    if not (isinstance(rng, list) and len(rng) == 2):
        raise ToolError("range must be [start, end]")
    a, b = float(_num(rng[0])), float(_num(rng[1]))
    n = int(p.get("n", 50))
    if n < 2 or n > 10000:
        raise ToolError("n must be between 2 and 10000")
    try:
        f = sp.lambdify(var, expr, modules=["math"])
    except Exception:
        f = None
    pts: list[list[float]] = []
    skipped = 0
    for k in range(n):
        x = a + (b - a) * k / (n - 1)
        try:
            y = f(x) if f else float(expr.subs(var, x).evalf())
            if isinstance(y, complex) or y != y or abs(y) == float("inf"):
                skipped += 1
                continue
            pts.append([round(x, 12), round(float(y), 12)])
        except Exception:
            skipped += 1
    ys = [pt[1] for pt in pts]
    out = {"points": pts, "count": len(pts)}
    if ys:
        out["y_min"], out["y_max"] = min(ys), max(ys)
    warnings = [f"{skipped} point(s) skipped (undefined or non-real)"] if skipped else []
    return ok(out, assumptions=assumptions, warnings=warnings)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


@tool
def math(mode: str = "eval", **params: Any) -> dict[str, Any]:
    """Exact math. See :data:`MODES` for the available modes.

    Common parameters:
      expr        - the expression (string)
      angle       - 'rad' | 'deg'  (required whenever trig appears)
      precision   - significant digits for the decimal form (default 15)
      timeout     - seconds (default 20)
    """
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    timeout = float(params.pop("timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
    p = {k: v for k, v in params.items() if v is not None}
    if "expression" in p and "expr" not in p:
        p["expr"] = p.pop("expression")
    needs_expr = mode not in ("solve", "ode", "matrix", "stats")
    if needs_expr and not p.get("expr"):
        raise ToolError(f"mode '{mode}' needs 'expr'")
    if "precision" in p:
        p["precision"] = _bounded(p["precision"], "precision", MAX_PRECISION, "significant digits")

    dispatch: dict[str, Callable[[], dict[str, Any]]] = {
        "eval": lambda: _mode_eval(p, exact_only=False),
        "exact": lambda: _mode_eval(p, exact_only=True),
        "simplify": lambda: _mode_transform(p, sp.simplify),
        "expand": lambda: _mode_transform(p, sp.expand),
        "factor": lambda: _mode_transform(p, sp.factor),
        "solve": lambda: _mode_solve(p),
        "diff": lambda: _mode_diff(p),
        "integrate": lambda: _mode_integrate(p),
        "limit": lambda: _mode_limit(p),
        "series": lambda: _mode_series(p),
        "ode": lambda: _mode_ode(p),
        "matrix": lambda: _mode_matrix(p),
        "stats": lambda: _mode_stats(p),
        "convert_form": lambda: _mode_convert_form(p),
        "plot_points": lambda: _mode_plot_points(p),
    }
    return _run(dispatch[mode], timeout)

#: Worked examples for the reference page, one list per mode. Every one of them is
#: executed when /docs/tools/math is built and sorted by the result into
#: "Examples" (the call succeeded) and "Fails when" (it did not), so a fixture never
#: states an expectation of its own. Mark anything whose output depends on the
#: current instant with "volatile": True.
EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "eval": [
        {
            "caption": "Division by zero is undefined, not complex infinity rendered as NaN.",
            "args": {"mode": "eval", "expr": "1/0"},
        },
        {
            "caption": "A percentage of an amount. The `%` reading is reported back in `assumptions`.",
            "args": {"mode": "eval", "expr": "15% of 2400"},
        },
        {
            "caption": "Trigonometry in degrees. The exact form survives; the decimal is there too.",
            "args": {"mode": "eval", "expr": "sin(30) + cos(60)", "angle": "deg"},
        },
        {
            "caption": "Substituting variables before evaluating.",
            "args": {"mode": "eval", "expr": "sqrt(a^2 + b^2)", "vars": {"a": 3, "b": 4}},
        },
        {
            "caption": "Complex arithmetic, described with modulus and argument.",
            "args": {"mode": "eval", "expr": "(3 + 4i) * (1 - 2i)"},
        },
        {
            "caption": "Trigonometry without `angle`. Degrees and radians differ by a factor of 57, so the tool refuses to pick one.",
            "args": {"mode": "eval", "expr": "sin(30)"},
        },
        {
            "caption": "An unknown function is rejected instead of being read as implicit multiplication.",
            "args": {"mode": "eval", "expr": "foo(2) + 1"},
        },
        {
            "caption": "Anything that looks like code execution is refused by the parser guard.",
            "args": {"mode": "eval", "expr": "__import__(1)"},
        },
        {
            "caption": "A power tower is refused from the expression alone, before anything is evaluated - the answer would have more digits than can be written down.",
            "args": {"mode": "eval", "expr": "9^9^9^9"},
        },
    ],
    "exact": [
        {
            "caption": "Float noise recovered as the rational the caller meant.",
            "args": {"mode": "exact", "expr": "0.1 + 0.2"},
        },
        {
            "caption": "Fractions stay fractions.",
            "args": {"mode": "exact", "expr": "1/3 + 1/6"},
        },
        {
            "caption": "A radical stays a radical.",
            "args": {"mode": "exact", "expr": "sqrt(50)"},
        },
        {
            "caption": "An incomplete expression fails to parse.",
            "args": {"mode": "exact", "expr": "2 +"},
        },
        {
            "caption": "`angle` is still mandatory for trigonometry.",
            "args": {"mode": "exact", "expr": "tan(45)"},
        },
    ],
    "simplify": [
        {
            "caption": "A removable factor cancels.",
            "args": {"mode": "simplify", "expr": "(x^2 - 1)/(x - 1)"},
        },
        {
            "caption": "A Pythagorean identity collapses to 1.",
            "args": {"mode": "simplify", "expr": "sin(x)^2 + cos(x)^2"},
        },
        {
            "caption": "A malformed operator sequence fails to parse.",
            "args": {"mode": "simplify", "expr": "x^^2"},
        },
    ],
    "expand": [
        {
            "caption": "A binomial cube.",
            "args": {"mode": "expand", "expr": "(x + 1)^3"},
        },
        {
            "caption": "A difference of squares, expanded.",
            "args": {"mode": "expand", "expr": "(a + b)*(a - b)"},
        },
        {
            "caption": "Unbalanced parentheses.",
            "args": {"mode": "expand", "expr": "(x + 1"},
        },
    ],
    "factor": [
        {
            "caption": "A quadratic with integer roots.",
            "args": {"mode": "factor", "expr": "x^2 - 5*x + 6"},
        },
        {
            "caption": "A difference of cubes.",
            "args": {"mode": "factor", "expr": "a^3 - b^3"},
        },
        {
            "caption": "A statement separator is not allowed in an expression.",
            "args": {"mode": "factor", "expr": "x; y"},
        },
    ],
    "solve": [
        {
            "caption": "A quadratic: both roots, exact and decimal.",
            "args": {"mode": "solve", "equations": ["x^2 - 5*x + 6 = 0"]},
        },
        {
            "caption": "A linear system in two unknowns.",
            "args": {"mode": "solve", "equations": ["x + y = 10", "x - y = 2"]},
        },
        {
            "caption": "Restricting the domain to the reals — the complex roots are dropped and the empty result is flagged in `warnings`.",
            "args": {"mode": "solve", "equations": ["x^2 + 1 = 0"], "domain": "real"},
        },
        {
            "caption": "An inequality returns a solution set, not a list of roots.",
            "args": {"mode": "solve", "equations": ["x^2 - 4 > 0"], "vars": ["x"]},
        },
        {
            "caption": "One equation, two unknowns: the tool asks which variable you want rather than picking alphabetically.",
            "args": {"mode": "solve", "equations": ["x + y = 10"]},
        },
        {
            "caption": "An unknown domain.",
            "args": {"mode": "solve", "equations": ["x = 1"], "domain": "quaternion"},
        },
    ],
    "diff": [
        {
            "caption": "A first derivative; `var` is inferred.",
            "args": {"mode": "diff", "expr": "x^3 + 2*x"},
        },
        {
            "caption": "A second derivative, evaluated at a point.",
            "args": {"mode": "diff", "expr": "x^3", "var": "x", "order": 2, "at": 4},
        },
        {
            "caption": "A partial derivative of a two-variable expression.",
            "args": {"mode": "diff", "expr": "x^2*y + y^3", "var": "y"},
        },
        {
            "caption": "Two free symbols and no `var`: the tool lists them instead of choosing.",
            "args": {"mode": "diff", "expr": "x*y"},
        },
    ],
    "integrate": [
        {
            "caption": "An indefinite integral, returned with `+ C`.",
            "args": {"mode": "integrate", "expr": "x^2", "var": "x"},
        },
        {
            "caption": "A definite integral with symbolic bounds.",
            "args": {"mode": "integrate", "expr": "sin(x)", "var": "x", "lower": 0, "upper": "pi"},
        },
        {
            "caption": "Half a range is not a range.",
            "args": {"mode": "integrate", "expr": "x^2", "var": "x", "lower": 0},
        },
    ],
    "limit": [
        {
            "caption": "The classic removable singularity.",
            "args": {"mode": "limit", "expr": "sin(x)/x", "var": "x", "point": 0},
        },
        {
            "caption": "A one-sided limit that diverges.",
            "args": {"mode": "limit", "expr": "1/x", "var": "x", "point": 0, "side": "+"},
        },
        {
            "caption": "A two-sided limit that does not exist: still `ok`, with both sides reported.",
            "args": {"mode": "limit", "expr": "1/x", "var": "x", "point": 0},
        },
        {
            "caption": "An unrecognised `side`.",
            "args": {"mode": "limit", "expr": "1/x", "var": "x", "point": 0, "side": "up"},
        },
    ],
    "series": [
        {
            "caption": "The exponential series to fifth order.",
            "args": {"mode": "series", "expr": "exp(x)", "var": "x", "order": 5},
        },
        {
            "caption": "A logarithm expanded about zero.",
            "args": {"mode": "series", "expr": "log(1 + x)", "var": "x", "at": 0, "order": 4},
        },
        {
            "caption": "Two free symbols and no `var`.",
            "args": {"mode": "series", "expr": "exp(x*y)", "order": 3},
        },
    ],
    "ode": [
        {
            "caption": "A first-order equation with a free constant.",
            "args": {"mode": "ode", "equation": "y' = y", "func": "y(x)"},
        },
        {
            "caption": "A second-order equation pinned down by initial conditions.",
            "args": {"mode": "ode", "equation": "y'' + y = 0", "func": "y(x)", "ics": {"y(0)": 1, "y'(0)": 0}},
        },
        {
            "caption": "`func` must name a function, not an expression.",
            "args": {"mode": "ode", "equation": "y' = y", "func": "2x"},
        },
        {
            "caption": "Initial-condition keys must look like `y(0)` or `y'(0)`.",
            "args": {"mode": "ode", "equation": "y' = y", "func": "y(x)", "ics": {"y0": 1}},
        },
    ],
    "matrix": [
        {
            "caption": "A determinant, exactly.",
            "args": {"mode": "matrix", "op": "det", "A": [[1, 2], [3, 4]]},
        },
        {
            "caption": "An inverse, as exact rationals.",
            "args": {"mode": "matrix", "op": "inv", "A": [[4, 7], [2, 6]]},
        },
        {
            "caption": "Eigenvalues, eigenvectors and the characteristic polynomial.",
            "args": {"mode": "matrix", "op": "eig", "A": [[2, 1], [1, 2]]},
        },
        {
            "caption": "Solving `A·x = b`.",
            "args": {"mode": "matrix", "op": "solve", "A": [[2, 1], [1, 3]], "b": [5, 10]},
        },
        {
            "caption": "A determinant needs a square matrix.",
            "args": {"mode": "matrix", "op": "det", "A": [[1, 2, 3], [4, 5, 6]]},
        },
        {
            "caption": "A singular matrix has no inverse.",
            "args": {"mode": "matrix", "op": "inv", "A": [[1, 2], [2, 4]]},
        },
        {
            "caption": "Inner dimensions must agree for multiplication.",
            "args": {"mode": "matrix", "op": "mul", "A": [[1, 2], [3, 4]], "B": [[1, 2, 3]]},
        },
        {
            "caption": "An unknown operation lists the valid ones.",
            "args": {"mode": "matrix", "op": "eigenfrobnicate", "A": [[1, 0], [0, 1]]},
        },
    ],
    "stats": [
        {
            "caption": "A full description, with sample and population spread side by side.",
            "args": {"mode": "stats", "op": "describe", "data": [2, 4, 4, 4, 5, 5, 7, 9]},
        },
        {
            "caption": "Least-squares regression with a prediction.",
            "args": {"mode": "stats", "op": "regress", "data": [1, 2, 3, 4], "y": [2, 4, 6, 9], "predict": 5},
        },
        {
            "caption": "A percentile, with the interpolation rule stated.",
            "args": {"mode": "stats", "op": "percentile", "data": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "percentile": 90},
        },
        {
            "caption": "A sample standard deviation needs at least two points.",
            "args": {"mode": "stats", "op": "stdev", "data": [5]},
        },
        {
            "caption": "An unknown statistic lists the valid ones.",
            "args": {"mode": "stats", "op": "vibe", "data": [1, 2, 3]},
        },
        {
            "caption": "Paired series must be the same length.",
            "args": {"mode": "stats", "op": "corr", "data": [1, 2, 3], "y": [1, 2]},
        },
    ],
    "convert_form": [
        {
            "caption": "A complex number in polar form, with the phasor notation spelled out.",
            "args": {"mode": "convert_form", "expr": "3 + 4i", "form": "polar"},
        },
        {
            "caption": "A decimal recovered as an exact fraction.",
            "args": {"mode": "convert_form", "expr": "0.375", "form": "fraction"},
        },
        {
            "caption": "Scientific notation to three significant figures.",
            "args": {"mode": "convert_form", "expr": "0.000123456", "form": "scientific", "significant": 3},
        },
        {
            "caption": "Scientific notation is undefined for a complex number.",
            "args": {"mode": "convert_form", "expr": "3 + 4i", "form": "scientific"},
        },
        {
            "caption": "An unknown target form.",
            "args": {"mode": "convert_form", "expr": "2", "form": "binary"},
        },
    ],
    "plot_points": [
        {
            "caption": "Five points of a parabola.",
            "args": {"mode": "plot_points", "expr": "x^2", "var": "x", "range": [-2, 2], "n": 5},
        },
        {
            "caption": "A pole at zero: the undefined sample is skipped and reported.",
            "args": {"mode": "plot_points", "expr": "1/x", "var": "x", "range": [-1, 1], "n": 5},
        },
        {
            "caption": "`range` must have exactly two entries.",
            "args": {"mode": "plot_points", "expr": "x^2", "range": [0]},
        },
        {
            "caption": "One point is not a plot.",
            "args": {"mode": "plot_points", "expr": "x^2", "n": 1},
        },
    ],
}
