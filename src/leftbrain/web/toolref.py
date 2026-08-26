"""Per-tool reference pages, generated from a catalogue and verified by running it.

Every example in :data:`CATALOGUE` is executed against the real core function when a
page is built, and the exact response is embedded in the page.  Docs therefore cannot
drift from behaviour: if a tool changes, the page changes with it (and
``tests/test_toolref.py`` fails if a documented success starts failing, or a
documented failure starts succeeding).

Examples whose output depends on the current instant are marked ``volatile=True`` and
carry a note; everything else is pinned with explicit dates or a seed.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from ..core import collections_, datetimex, geo_offline, holidays_, mathx, random_
from ..core import convert as convert_mod
from ..core import encode as encode_mod
from ..core import numbers as numbers_mod
from ..core import scale as scale_mod
from ..core import text as text_mod
from ..core import validate as validate_mod
from .docs import render_markdown
from .tools_list import TOOLS

#: Responses longer than this are elided in the page (the call still returns them all).
MAX_JSON_LINES = 140


@dataclass(frozen=True)
class Param:
    """One row of a mode's parameter table."""

    name: str
    type: str
    required: bool
    meaning: str
    default: str = "—"


@dataclass(frozen=True)
class Example:
    """One `tools/call` request, run for real when the page is built."""

    caption: str
    args: dict[str, Any]
    volatile: bool = False


@dataclass(frozen=True)
class Mode:
    """One mode of one tool."""

    name: str
    purpose: str
    description: str
    params: tuple[Param, ...] = ()
    examples: tuple[Example, ...] = ()
    failures: tuple[Example, ...] = ()
    never_fails: str = ""


@dataclass(frozen=True)
class ToolDoc:
    """One tool: intro, when to use it, and every mode."""

    name: str
    tagline: str
    intro: str
    when: tuple[str, ...]
    fn: Callable[..., dict[str, Any]]
    modes: tuple[Mode, ...]
    related: str
    mode_key: str = "mode"
    extra_modes: tuple[str, ...] = field(default=())


# --------------------------------------------------------------------------- #
# Execution + rendering
# --------------------------------------------------------------------------- #


def run_example(tool: ToolDoc, example: Example) -> dict[str, Any]:
    """Call the real core function with the example's arguments."""
    result = tool.fn(**example.args)
    if isinstance(result, dict):
        result.pop("trace", None)  # a traceback is noise in a docs page
    return result


def _json_block(payload: Any) -> str:
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    lines = text.split("\n")
    if len(lines) > MAX_JSON_LINES:
        hidden = len(lines) - MAX_JSON_LINES
        lines = lines[:MAX_JSON_LINES] + [f"  ... {hidden} more lines (elided here, not in the response)"]
    return "```json\n" + "\n".join(lines) + "\n```"


def _cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _params_table(params: tuple[Param, ...]) -> list[str]:
    if not params:
        return ["This mode takes no parameters beyond `mode`.", ""]
    out = ["| name | type | required | meaning | default |", "| --- | --- | --- | --- | --- |"]
    for p in params:
        out.append(
            f"| `{_cell(p.name)}` | {_cell(p.type)} | {'yes' if p.required else 'no'} "
            f"| {_cell(p.meaning)} | {_cell(p.default)} |"
        )
    out.append("")
    return out


def _example_block(tool: ToolDoc, example: Example) -> list[str]:
    request = {"name": tool.name, "arguments": example.args}
    response = run_example(tool, example)
    out = [example.caption, "", _json_block(request), "", _json_block(response), ""]
    if example.volatile:
        out += ["*Time-dependent: the response above was captured when this page was built.*", ""]
    return out


def _mode_markdown(tool: ToolDoc, mode: Mode) -> list[str]:
    out = [f'<h2 id="{mode.name}">{mode.name}</h2>', "", mode.description, ""]
    out += ["### Parameters", ""]
    out += _params_table(mode.params)
    out += ["### Examples", ""]
    for example in mode.examples:
        out += _example_block(tool, example)
    out += ["### Fails when", ""]
    if mode.failures:
        for example in mode.failures:
            out += _example_block(tool, example)
    else:
        out += [mode.never_fails or "No input reaches an error path in this mode.", ""]
    return out


CONTRACT_NOTE = (
    '<div class="callout">Every call returns <code>{ok: true, result, assumptions[], warnings[]}</code>, '
    "or <code>{ok: false, error, message}</code> with an optional <code>needs</code> block when the input "
    "was ambiguous. Read <code>assumptions</code>: it says how an under-specified input was interpreted. "
    "When <code>needs.options</code> is present, pick one and call again.</div>"
)

_PAGE_LEAD = (
    "Each example below shows the `tools/call` request first and the exact response underneath. "
    "Responses are produced by running the real tool when this page is built, so they cannot drift "
    "from what the server returns."
)


def tool_markdown(tool: ToolDoc) -> str:
    parts = [f"# {tool.name}", "", tool.intro, "", "## When to use", ""]
    parts += [f"- {line}" for line in tool.when]
    parts += ["", CONTRACT_NOTE, "", _PAGE_LEAD, "", "## Modes", ""]
    parts += ["| mode | what it does |", "| --- | --- |"]
    for mode in tool.modes:
        parts.append(f"| [`{mode.name}`](#{mode.name}) | {_cell(mode.purpose)} |")
    parts.append("")
    for mode in tool.modes:
        parts += _mode_markdown(tool, mode)
    parts += ["## Related tools", "", tool.related, ""]
    return "\n".join(parts)


def index_markdown() -> str:
    parts = [
        "# Tools",
        "",
        "Twelve tools, one shape. Every tool takes a `mode` and returns "
        "`{ok, result, assumptions[], warnings[]}` on success, or `{ok: false, error, message}` "
        "— with a `needs` block — when the input was ambiguous and guessing would be dangerous.",
        "",
        CONTRACT_NOTE,
        "",
        "Each page below documents every mode: what it does, its parameters, worked examples, and "
        "the inputs that make it fail.",
        "",
    ]
    described = {t.name: t for t in CATALOGUE}
    for name, desc, modes in TOOLS:
        doc = described.get(name)
        parts.append(f'<h2 id="{name}"><a href="/docs/tools/{name}">{name}</a></h2>')
        parts.append("")
        parts.append(desc + ".")
        parts.append("")
        if doc is not None:
            listed = " · ".join(f"[`{m.name}`](/docs/tools/{name}#{m.name})" for m in doc.modes)
            parts.append(f"**Modes:** {listed}")
        else:  # pragma: no cover - every tool is catalogued
            parts.append(f"**Modes:** {modes.rstrip(' …')}")
        parts.append("")
        parts.append(f"[Read the {name} reference →](/docs/tools/{name})")
        parts.append("")
    return "\n".join(parts)


@lru_cache(maxsize=1)
def index_page() -> tuple[str, str]:
    return "Tools", render_markdown(index_markdown())


@lru_cache(maxsize=16)
def tool_page(name: str) -> tuple[str, str] | None:
    tool = by_name(name)
    if tool is None:
        return None
    return tool.name, render_markdown(tool_markdown(tool))


def by_name(name: str) -> ToolDoc | None:
    return next((t for t in CATALOGUE if t.name == name), None)


def tool_names() -> list[str]:
    return [t.name for t in CATALOGUE]


# --------------------------------------------------------------------------- #
# math
# --------------------------------------------------------------------------- #

MATH = ToolDoc(
    name="math",
    tagline="Exact arithmetic and symbolic algebra",
    intro=(
        "`math` is SymPy behind the leftbrain contract. Answers come back in exact form *and* "
        "decimal form *and* LaTeX together, so the caller never rounds, re-derives or re-types "
        "anything. Expressions are parsed in a locked-down namespace — no builtins, no attribute "
        "access, no imports — and run under a timeout."
    ),
    when=(
        "Before stating any number: percentages, fractions, powers, roots, interest, ratios.",
        "Trigonometry — and always pass `angle`, because `sin(30)` means two different things.",
        "Algebra and calculus: `solve`, `diff`, `integrate`, `limit`, `series`, `ode`.",
        "Linear algebra (`matrix`) and exact descriptive statistics (`stats`).",
        "Not for dates or unit conversion: use `datetime` and `convert` instead.",
    ),
    fn=mathx.math,
    related=(
        "[`numbers`](/docs/tools/numbers) for rounding rules, locale formatting and exact "
        "allocation · [`convert`](/docs/tools/convert) for units · [`scale`](/docs/tools/scale) "
        "for proportional scaling."
    ),
    modes=(
        Mode(
            name="eval",
            purpose="Evaluate an expression to exact, decimal and LaTeX form.",
            description=(
                "The default entry point. Parses and evaluates an arithmetic or symbolic expression, "
                "simplifying symbolic results and expanding numeric ones. Human notation is understood: "
                "`15% of 200`, `12^2`, `×`, `÷`, `√`, `π`, `∞`, `3∠45` for a phasor, `°` for degrees. "
                "The result carries `exact`, `decimal`, `latex` and — for rationals — `numerator` and "
                "`denominator`; complex results add modulus and argument."
            ),
            params=(
                Param("expr", "string", True, "The expression to evaluate."),
                Param("angle", "`rad` \\| `deg`", False, "Mandatory whenever the expression contains trigonometry."),
                Param("vars", "object", False, "Values substituted before evaluating, e.g. `{'a': 3}`."),
                Param("precision", "integer", False, "Significant digits in the decimal form.", "15"),
                Param("timeout", "number", False, "Seconds before the computation is abandoned.", "20"),
            ),
            examples=(
                Example(
                    "A percentage of an amount. The `%` reading is reported back in `assumptions`.",
                    {"mode": "eval", "expr": "15% of 2400"},
                ),
                Example(
                    "Trigonometry in degrees. The exact form survives; the decimal is there too.",
                    {"mode": "eval", "expr": "sin(30) + cos(60)", "angle": "deg"},
                ),
                Example(
                    "Substituting variables before evaluating.",
                    {"mode": "eval", "expr": "sqrt(a^2 + b^2)", "vars": {"a": 3, "b": 4}},
                ),
                Example(
                    "Complex arithmetic, described with modulus and argument.",
                    {"mode": "eval", "expr": "(3 + 4i) * (1 - 2i)"},
                ),
            ),
            failures=(
                Example(
                    "Trigonometry without `angle`. Degrees and radians differ by a factor of 57, so the tool refuses to pick one.",
                    {"mode": "eval", "expr": "sin(30)"},
                ),
                Example(
                    "An unknown function is rejected instead of being read as implicit multiplication.",
                    {"mode": "eval", "expr": "foo(2) + 1"},
                ),
                Example(
                    "Anything that looks like code execution is refused by the parser guard.",
                    {"mode": "eval", "expr": "__import__(1)"},
                ),
                Example("`expr` is required.", {"mode": "eval"}),
            ),
        ),
        Mode(
            name="exact",
            purpose="Evaluate without ever showing a decimal.",
            description=(
                "Same parsing as `eval`, but the result is forced through `nsimplify` and the decimal "
                "form is dropped. Use it when a decimal would be a lie — recovering `3/10` from "
                "`0.1 + 0.2`, or keeping a radical as a radical."
            ),
            params=(
                Param("expr", "string", True, "The expression to evaluate."),
                Param("angle", "`rad` \\| `deg`", False, "Mandatory whenever the expression contains trigonometry."),
                Param("vars", "object", False, "Values substituted before evaluating."),
                Param("precision", "integer", False, "Significant digits used internally.", "15"),
            ),
            examples=(
                Example(
                    "Float noise recovered as the rational the caller meant.",
                    {"mode": "exact", "expr": "0.1 + 0.2"},
                ),
                Example("Fractions stay fractions.", {"mode": "exact", "expr": "1/3 + 1/6"}),
                Example("A radical stays a radical.", {"mode": "exact", "expr": "sqrt(50)"}),
            ),
            failures=(
                Example("An incomplete expression fails to parse.", {"mode": "exact", "expr": "2 +"}),
                Example("`angle` is still mandatory for trigonometry.", {"mode": "exact", "expr": "tan(45)"}),
            ),
        ),
        Mode(
            name="simplify",
            purpose="Reduce an expression to its simplest equivalent form.",
            description=(
                "Runs SymPy's `simplify` over the parsed expression. Trigonometric identities, "
                "cancelling factors and collected terms all come out. `angle` defaults to `rad` here "
                "because a symbolic identity does not depend on the unit."
            ),
            params=(
                Param("expr", "string", True, "The expression to simplify."),
                Param("angle", "`rad` \\| `deg`", False, "Interpretation of trig arguments.", "`rad`"),
                Param("precision", "integer", False, "Significant digits in the decimal form.", "15"),
            ),
            examples=(
                Example("A removable factor cancels.", {"mode": "simplify", "expr": "(x^2 - 1)/(x - 1)"}),
                Example("A Pythagorean identity collapses to 1.", {"mode": "simplify", "expr": "sin(x)^2 + cos(x)^2"}),
            ),
            failures=(
                Example("`expr` is required.", {"mode": "simplify"}),
                Example("A malformed operator sequence fails to parse.", {"mode": "simplify", "expr": "x^^2"}),
            ),
        ),
        Mode(
            name="expand",
            purpose="Multiply out products and powers.",
            description=(
                "Distributes products over sums and expands integer powers. The inverse of `factor`; "
                "use it to get a polynomial in standard form before comparing two expressions."
            ),
            params=(
                Param("expr", "string", True, "The expression to expand."),
                Param("angle", "`rad` \\| `deg`", False, "Interpretation of trig arguments.", "`rad`"),
                Param("precision", "integer", False, "Significant digits in the decimal form.", "15"),
            ),
            examples=(
                Example("A binomial cube.", {"mode": "expand", "expr": "(x + 1)^3"}),
                Example("A difference of squares, expanded.", {"mode": "expand", "expr": "(a + b)*(a - b)"}),
            ),
            failures=(
                Example("Unbalanced parentheses.", {"mode": "expand", "expr": "(x + 1"}),
                Example("`expr` is required.", {"mode": "expand"}),
            ),
        ),
        Mode(
            name="factor",
            purpose="Factorise a polynomial over the rationals.",
            description=(
                "Factors a polynomial into irreducible factors over the rationals. Returns the factored "
                "form in `value` and LaTeX; if nothing factors, the input comes back unchanged."
            ),
            params=(
                Param("expr", "string", True, "The expression to factor."),
                Param("angle", "`rad` \\| `deg`", False, "Interpretation of trig arguments.", "`rad`"),
                Param("precision", "integer", False, "Significant digits in the decimal form.", "15"),
            ),
            examples=(
                Example("A quadratic with integer roots.", {"mode": "factor", "expr": "x^2 - 5*x + 6"}),
                Example("A difference of cubes.", {"mode": "factor", "expr": "a^3 - b^3"}),
            ),
            failures=(
                Example("A statement separator is not allowed in an expression.", {"mode": "factor", "expr": "x; y"}),
                Example("`expr` is required.", {"mode": "factor"}),
            ),
        ),
        Mode(
            name="solve",
            purpose="Solve equations, systems and inequalities.",
            description=(
                "Solves one equation or a system of them. Write equations as strings with a single `=` "
                "(`x^2 - 5*x + 6 = 0`); relational operators produce an inequality solution set instead. "
                "If there are more unknowns than equations the tool refuses to guess which to solve for "
                "and returns `needs.options`. `domain` narrows the search to real, integer or positive "
                "solutions — with no domain, variables are complex."
            ),
            params=(
                Param("equations", "string[]", False, "The equations. A single string is accepted.", "—"),
                Param("expr", "string", False, "Alternative to `equations` for one equation.", "—"),
                Param("vars", "string[]", False, "Which unknowns to solve for. Required when there are more unknowns than equations."),
                Param("domain", "`complex` \\| `real` \\| `integer` \\| `positive`", False, "Assumption applied to every unknown.", "`complex`"),
                Param("precision", "integer", False, "Significant digits in the decimal forms.", "15"),
            ),
            examples=(
                Example("A quadratic: both roots, exact and decimal.", {"mode": "solve", "equations": ["x^2 - 5*x + 6 = 0"]}),
                Example("A linear system in two unknowns.", {"mode": "solve", "equations": ["x + y = 10", "x - y = 2"]}),
                Example(
                    "Restricting the domain to the reals — the complex roots are dropped and the empty result is flagged in `warnings`.",
                    {"mode": "solve", "equations": ["x^2 + 1 = 0"], "domain": "real"},
                ),
                Example("An inequality returns a solution set, not a list of roots.", {"mode": "solve", "equations": ["x^2 - 4 > 0"], "vars": ["x"]}),
            ),
            failures=(
                Example(
                    "One equation, two unknowns: the tool asks which variable you want rather than picking alphabetically.",
                    {"mode": "solve", "equations": ["x + y = 10"]},
                ),
                Example("Neither `equations` nor `expr` was given.", {"mode": "solve"}),
                Example("An unknown domain.", {"mode": "solve", "equations": ["x = 1"], "domain": "quaternion"}),
            ),
        ),
        Mode(
            name="diff",
            purpose="Differentiate, optionally evaluated at a point.",
            description=(
                "Differentiates `expr` with respect to `var`, `order` times, and simplifies the result. "
                "Pass `at` to also evaluate the derivative at a point. With several free symbols and no "
                "`var`, the tool refuses to guess and lists the candidates."
            ),
            params=(
                Param("expr", "string", True, "The expression to differentiate."),
                Param("var", "string", False, "The variable. Inferred when the expression has exactly one free symbol."),
                Param("order", "integer", False, "How many times to differentiate.", "1"),
                Param("at", "number \\| string", False, "Also evaluate the derivative at this point."),
                Param("angle", "`rad` \\| `deg`", False, "Interpretation of trig arguments.", "`rad`"),
            ),
            examples=(
                Example("A first derivative; `var` is inferred.", {"mode": "diff", "expr": "x^3 + 2*x"}),
                Example("A second derivative, evaluated at a point.", {"mode": "diff", "expr": "x^3", "var": "x", "order": 2, "at": 4}),
                Example("A partial derivative of a two-variable expression.", {"mode": "diff", "expr": "x^2*y + y^3", "var": "y"}),
            ),
            failures=(
                Example("Two free symbols and no `var`: the tool lists them instead of choosing.", {"mode": "diff", "expr": "x*y"}),
                Example("`expr` is required.", {"mode": "diff"}),
            ),
        ),
        Mode(
            name="integrate",
            purpose="Definite and indefinite integrals.",
            description=(
                "With `from` and `to`, computes a definite integral; without them, an indefinite one "
                "(the result carries an explicit `+ C`). If no closed form exists, the definite case "
                "falls back to a numeric value and says so in `warnings`. Passing only one bound is an "
                "error, not a guess."
            ),
            params=(
                Param("expr", "string", True, "The integrand."),
                Param("var", "string", False, "The variable of integration. Inferred when unambiguous."),
                Param("from", "number \\| string", False, "Lower bound. Required together with `to`."),
                Param("to", "number \\| string", False, "Upper bound. Required together with `from`."),
                Param("precision", "integer", False, "Significant digits in the decimal form.", "15"),
            ),
            examples=(
                Example("An indefinite integral, returned with `+ C`.", {"mode": "integrate", "expr": "x^2", "var": "x"}),
                Example("A definite integral with symbolic bounds.", {"mode": "integrate", "expr": "sin(x)", "var": "x", "from": 0, "to": "pi"}),
            ),
            failures=(
                Example("Half a range is not a range.", {"mode": "integrate", "expr": "x^2", "var": "x", "from": 0}),
                Example("`expr` is required.", {"mode": "integrate"}),
            ),
        ),
        Mode(
            name="limit",
            purpose="Limits, one-sided or two-sided.",
            description=(
                "Evaluates the limit of `expr` as `var` approaches `to`. Without `side` the limit is "
                "two-sided; when the two sides disagree the response stays `ok` but reports "
                "`exists: false` together with both one-sided limits. `to` accepts `oo` for infinity."
            ),
            params=(
                Param("expr", "string", True, "The expression."),
                Param("var", "string", False, "The variable. Inferred when unambiguous."),
                Param("to", "number \\| string", False, "The point approached; `oo` for infinity.", "0"),
                Param("side", "`+` \\| `-` \\| `left` \\| `right`", False, "One-sided limit.", "two-sided"),
            ),
            examples=(
                Example("The classic removable singularity.", {"mode": "limit", "expr": "sin(x)/x", "var": "x", "to": 0}),
                Example("A one-sided limit that diverges.", {"mode": "limit", "expr": "1/x", "var": "x", "to": 0, "side": "+"}),
                Example(
                    "A two-sided limit that does not exist: still `ok`, with both sides reported.",
                    {"mode": "limit", "expr": "1/x", "var": "x", "to": 0},
                ),
            ),
            failures=(
                Example("An unrecognised `side`.", {"mode": "limit", "expr": "1/x", "var": "x", "to": 0, "side": "up"}),
                Example("`expr` is required.", {"mode": "limit"}),
            ),
        ),
        Mode(
            name="series",
            purpose="Taylor / Laurent series expansion.",
            description=(
                "Expands `expr` about `at` up to `order`. The response carries both the series with its "
                "`O(...)` term (in `value`) and the bare polynomial (in `polynomial`), so the caller can "
                "use whichever it needs."
            ),
            params=(
                Param("expr", "string", True, "The expression to expand."),
                Param("var", "string", False, "The variable. Inferred when unambiguous."),
                Param("at", "number \\| string", False, "Point to expand about.", "0"),
                Param("order", "integer", False, "Order of the expansion.", "6"),
            ),
            examples=(
                Example("The exponential series to fifth order.", {"mode": "series", "expr": "exp(x)", "var": "x", "order": 5}),
                Example("A logarithm expanded about zero.", {"mode": "series", "expr": "log(1 + x)", "var": "x", "at": 0, "order": 4}),
            ),
            failures=(
                Example("Two free symbols and no `var`.", {"mode": "series", "expr": "exp(x*y)", "order": 3}),
                Example("`expr` is required.", {"mode": "series"}),
            ),
        ),
        Mode(
            name="ode",
            purpose="Solve an ordinary differential equation.",
            description=(
                "Solves an ODE written in ordinary notation: primes (`y'`, `y''`) and `dy/dx` are both "
                "understood. `func` names the unknown function and its independent variable. Initial "
                "conditions go in `ics` keyed by `y(0)` / `y'(0)`. The response includes SymPy's "
                "classification of the equation."
            ),
            params=(
                Param("equation", "string", True, "The differential equation, e.g. `y'' + y = 0`."),
                Param("func", "string", False, "The unknown function, `y` or `y(x)`.", "`y(x)`"),
                Param("ics", "object", False, "Initial conditions, e.g. `{'y(0)': 1, \"y'(0)\": 0}`."),
                Param("precision", "integer", False, "Significant digits in the decimal form.", "15"),
            ),
            examples=(
                Example("A first-order equation with a free constant.", {"mode": "ode", "equation": "y' = y", "func": "y(x)"}),
                Example("A second-order equation pinned down by initial conditions.", {"mode": "ode", "equation": "y'' + y = 0", "func": "y(x)", "ics": {"y(0)": 1, "y'(0)": 0}}),
            ),
            failures=(
                Example("`equation` is required.", {"mode": "ode"}),
                Example("`func` must name a function, not an expression.", {"mode": "ode", "equation": "y' = y", "func": "2x"}),
                Example("Initial-condition keys must look like `y(0)` or `y'(0)`.", {"mode": "ode", "equation": "y' = y", "func": "y(x)", "ics": {"y0": 1}}),
            ),
        ),
        Mode(
            name="matrix",
            purpose="Linear algebra: determinant, inverse, eigenvectors, solving.",
            description=(
                "One matrix operation per call, chosen with `op`: `det`, `inv`, `transpose`, `rank`, "
                "`trace`, `rref`, `nullspace`, `eig`, `solve`, `mul`, `add`, `sub`, `pow`. Matrices are "
                "nested lists (a flat list is read as a single row). Entries may be numbers or "
                "expressions, and everything stays exact — no floating-point drift in a determinant."
            ),
            params=(
                Param("op", "string", False, "The operation.", "`det`"),
                Param("A", "number[][] \\| string", True, "The matrix."),
                Param("B", "number[][]", False, "Second matrix, for `mul`, `add`, `sub`."),
                Param("b", "number[]", False, "Right-hand side, for `op: solve`."),
                Param("n", "integer", False, "Exponent, for `op: pow`.", "2"),
                Param("precision", "integer", False, "Significant digits in the decimal forms.", "15"),
            ),
            examples=(
                Example("A determinant, exactly.", {"mode": "matrix", "op": "det", "A": [[1, 2], [3, 4]]}),
                Example("An inverse, as exact rationals.", {"mode": "matrix", "op": "inv", "A": [[4, 7], [2, 6]]}),
                Example("Eigenvalues, eigenvectors and the characteristic polynomial.", {"mode": "matrix", "op": "eig", "A": [[2, 1], [1, 2]]}),
                Example("Solving `A·x = b`.", {"mode": "matrix", "op": "solve", "A": [[2, 1], [1, 3]], "b": [5, 10]}),
            ),
            failures=(
                Example("A determinant needs a square matrix.", {"mode": "matrix", "op": "det", "A": [[1, 2, 3], [4, 5, 6]]}),
                Example("A singular matrix has no inverse.", {"mode": "matrix", "op": "inv", "A": [[1, 2], [2, 4]]}),
                Example("Inner dimensions must agree for multiplication.", {"mode": "matrix", "op": "mul", "A": [[1, 2], [3, 4]], "B": [[1, 2, 3]]}),
                Example("An unknown operation lists the valid ones.", {"mode": "matrix", "op": "eigenfrobnicate", "A": [[1, 0], [0, 1]]}),
                Example("`A` is required.", {"mode": "matrix", "op": "det"}),
            ),
        ),
        Mode(
            name="stats",
            purpose="Exact descriptive statistics and regression.",
            description=(
                "Statistics over a list of numbers, computed with exact rationals rather than floats. "
                "`op` selects the statistic: `describe`, `sum`, `mean`, `median`, `mode`, `stdev`, "
                "`pstdev`, `variance`, `pvariance`, `min`, `max`, `range`, `percentile`, `quartiles`, "
                "`zscore`, `geometric_mean`, `harmonic_mean`, `weighted_mean`, `cumsum`, `corr`, "
                "`covariance`, `regress`. `describe` returns both the sample (n−1) and population (n) "
                "spread so nobody has to guess which one was used."
            ),
            params=(
                Param("op", "string", False, "The statistic to compute.", "`describe`"),
                Param("data", "number[]", True, "The sample."),
                Param("y", "number[]", False, "Second series, for `corr`, `covariance`, `regress`."),
                Param("weights", "number[]", False, "Weights, for `weighted_mean`."),
                Param("percentile", "number", False, "0..100, for `op: percentile`."),
                Param("value", "number", False, "The observation, for `op: zscore`."),
                Param("predict", "number", False, "An x-value to predict, for `op: regress`."),
            ),
            examples=(
                Example("A full description, with sample and population spread side by side.", {"mode": "stats", "op": "describe", "data": [2, 4, 4, 4, 5, 5, 7, 9]}),
                Example("Least-squares regression with a prediction.", {"mode": "stats", "op": "regress", "data": [1, 2, 3, 4], "y": [2, 4, 6, 9], "predict": 5}),
                Example("A percentile, with the interpolation rule stated.", {"mode": "stats", "op": "percentile", "data": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "percentile": 90}),
            ),
            failures=(
                Example("An empty sample.", {"mode": "stats", "op": "describe", "data": []}),
                Example("A sample standard deviation needs at least two points.", {"mode": "stats", "op": "stdev", "data": [5]}),
                Example("An unknown statistic lists the valid ones.", {"mode": "stats", "op": "vibe", "data": [1, 2, 3]}),
                Example("Paired series must be the same length.", {"mode": "stats", "op": "corr", "data": [1, 2, 3], "y": [1, 2]}),
            ),
        ),
        Mode(
            name="convert_form",
            purpose="Re-present one value: polar, rectangular, fraction, scientific, LaTeX.",
            description=(
                "Takes one expression and returns it in a different representation, chosen with `to`: "
                "`polar`, `rect`, `latex`, `decimal`, `fraction`, `scientific`, `percent`. This is a "
                "presentation change, not a computation — the value is unchanged."
            ),
            params=(
                Param("expr", "string", True, "The value to re-present."),
                Param("to", "string", False, "Target form.", "`decimal`"),
                Param("significant", "integer", False, "Significant digits, for `to: scientific`.", "6"),
                Param("tolerance", "number", False, "Rounding tolerance, for `to: fraction`."),
                Param("precision", "integer", False, "Significant digits in the decimal forms.", "15"),
            ),
            examples=(
                Example("A complex number in polar form, with the phasor notation spelled out.", {"mode": "convert_form", "expr": "3 + 4i", "to": "polar"}),
                Example("A decimal recovered as an exact fraction.", {"mode": "convert_form", "expr": "0.375", "to": "fraction"}),
                Example("Scientific notation to three significant figures.", {"mode": "convert_form", "expr": "0.000123456", "to": "scientific", "significant": 3}),
            ),
            failures=(
                Example("Scientific notation is undefined for a complex number.", {"mode": "convert_form", "expr": "3 + 4i", "to": "scientific"}),
                Example("An unknown target form.", {"mode": "convert_form", "expr": "2", "to": "binary"}),
                Example("`expr` is required.", {"mode": "convert_form", "to": "polar"}),
            ),
        ),
        Mode(
            name="plot_points",
            purpose="Sample a function into (x, y) pairs for charting.",
            description=(
                "Evaluates `expr` at `n` evenly spaced points across `range` and returns the pairs plus "
                "the observed y-extent. Points where the function is undefined or non-real are skipped "
                "and counted in `warnings` rather than silently returned as nulls."
            ),
            params=(
                Param("expr", "string", True, "The function to sample."),
                Param("var", "string", False, "The variable. Inferred when unambiguous."),
                Param("range", "[number, number]", False, "Start and end of the sampled interval.", "`[-10, 10]`"),
                Param("n", "integer", False, "Number of samples, 2..10000.", "50"),
            ),
            examples=(
                Example("Five points of a parabola.", {"mode": "plot_points", "expr": "x^2", "var": "x", "range": [-2, 2], "n": 5}),
                Example("A pole at zero: the undefined sample is skipped and reported.", {"mode": "plot_points", "expr": "1/x", "var": "x", "range": [-1, 1], "n": 5}),
            ),
            failures=(
                Example("`range` must have exactly two entries.", {"mode": "plot_points", "expr": "x^2", "range": [0]}),
                Example("One point is not a plot.", {"mode": "plot_points", "expr": "x^2", "n": 1}),
                Example("`expr` is required.", {"mode": "plot_points"}),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# datetime
# --------------------------------------------------------------------------- #

DATETIME = ToolDoc(
    name="datetime",
    tagline="Dates, durations, time zones, business days",
    intro=(
        "A model has no clock and no calendar. `datetime` supplies both, and refuses the two "
        "inputs that silently produce wrong answers everywhere else: timezone abbreviations "
        "(`IST` is three different zones) and bare numeric dates (`03/04/2025` is two different "
        "days). Every returned instant carries its offset, zone and weekday so nothing has to be "
        "re-derived."
    ),
    when=(
        "Any time the current date or time matters — `now` is the only way to get it.",
        "Converting between zones, which needs IANA names (`Asia/Kolkata`, not `IST`).",
        "Parsing user-written dates, including relative phrases like `next friday 5pm`.",
        "Deadlines and SLAs: `add`/`diff` with `business_days` and a holiday region.",
        "Schedules: `recurrence` for RRULEs and phrases, `cron_next` for cron expressions.",
        "Ages, fiscal periods, overlaps and timesheet totals.",
    ),
    fn=datetimex.datetime_tool,
    related=(
        "[`holidays`](/docs/tools/holidays) for the calendars behind `region` · "
        "[`geo_offline`](/docs/tools/geo_offline) to turn a city name into an IANA zone before "
        "converting · [`numbers`](/docs/tools/numbers) for formatting the durations you get back."
    ),
    modes=(
        Mode(
            name="now",
            purpose="The current instant in a given zone.",
            description=(
                "Returns the current instant with its ISO string, date, weekday, time, UTC offset, "
                "zone, unix timestamp, DST flag, ISO week and day of year. With no `tz` the answer is "
                "UTC and says so in `assumptions`. This is the only mode whose output changes between "
                "calls; everything else can be pinned by passing explicit dates."
            ),
            params=(
                Param("tz", "string", False, "IANA zone name, a fixed `UTC+05:30` offset, or `local`.", "`UTC`"),
            ),
            examples=(
                Example("The current instant in a named zone.", {"mode": "now", "tz": "Asia/Kolkata"}, volatile=True),
                Example("No zone: UTC, with the assumption recorded.", {"mode": "now"}, volatile=True),
                Example("A fixed offset works too, but carries no daylight-saving rules.", {"mode": "now", "tz": "UTC+05:30"}, volatile=True),
            ),
            failures=(
                Example("`IST` is Indian, Israeli and Irish Standard Time. The tool lists the candidates instead of picking one.", {"mode": "now", "tz": "IST"}),
                Example("An unknown zone name.", {"mode": "now", "tz": "Mars/Olympus"}),
            ),
        ),
        Mode(
            name="convert_tz",
            purpose="Move one instant between time zones.",
            description=(
                "Converts an instant from one zone to another, or to several at once by passing a list "
                "as `to_tz`. The response repeats the source instant and gives each target with its "
                "offset and a `day_shift` (−1, 0 or +1) so nobody has to work out whether the date "
                "moved. A bare date is refused: midnight in which zone?"
            ),
            params=(
                Param("value", "string", False, "The instant to convert. `now` or omitted uses the current instant.", "`now`"),
                Param("from_tz", "string", False, "Zone of `value` when it carries no offset."),
                Param("to_tz", "string \\| string[]", True, "Target zone, or a list of them."),
                Param("locale", "string", False, "Country code used to read numeric dates, e.g. `IN`."),
            ),
            examples=(
                Example("A New York meeting time in Indian Standard Time.", {"mode": "convert_tz", "value": "2025-03-09T09:30:00", "from_tz": "America/New_York", "to_tz": "Asia/Kolkata"}),
                Example("One instant fanned out to a whole team, each with its own day shift.", {"mode": "convert_tz", "value": "2025-11-04T18:00:00", "from_tz": "Europe/London", "to_tz": ["Asia/Kolkata", "America/Los_Angeles", "Australia/Sydney"]}),
                Example("An offset already in the string needs no `from_tz`.", {"mode": "convert_tz", "value": "2025-06-01T10:00:00+05:30", "to_tz": "UTC"}),
            ),
            failures=(
                Example("A date with no time cannot be converted — midnight where?", {"mode": "convert_tz", "value": "2025-06-01", "from_tz": "UTC", "to_tz": "Asia/Tokyo"}),
                Example("A naive timestamp with no `from_tz`.", {"mode": "convert_tz", "value": "2025-06-01T10:00:00", "to_tz": "Asia/Tokyo"}),
                Example("An abbreviation as the source zone.", {"mode": "convert_tz", "value": "2025-06-01T10:00:00", "from_tz": "IST", "to_tz": "UTC"}),
                Example("`to_tz` is required.", {"mode": "convert_tz", "value": "2025-06-01T10:00:00+00:00"}),
            ),
        ),
        Mode(
            name="parse",
            purpose="Turn any written date into a normalised instant.",
            description=(
                "Accepts ISO 8601, unix timestamps (seconds or milliseconds), written forms "
                "(`14 Aug 2025`, `Aug 14, 2025`) and relative phrases (`tomorrow`, `next friday 5pm`, "
                "`3 weeks ago`, `end of month`). Relative phrases resolve against `ref_date` when given, "
                "otherwise against now. Numeric dates like `03/04/2025` are refused unless `locale` says "
                "which order to read, and the refusal spells out both readings as ISO dates."
            ),
            params=(
                Param("value", "string \\| number", True, "The date to parse."),
                Param("tz", "string", False, "Zone attached to a naive result."),
                Param("locale", "string", False, "Country code deciding DD/MM vs MM/DD, e.g. `IN`, `US`."),
                Param("ref_date", "string", False, "Anchor for relative phrases.", "now"),
            ),
            examples=(
                Example("An ISO date. `date_only` says no time was supplied.", {"mode": "parse", "value": "2025-03-04"}),
                Example("The same numeric date read two ways — first the Indian reading.", {"mode": "parse", "value": "03/04/2025", "locale": "IN"}),
                Example("…and the US reading of exactly the same string.", {"mode": "parse", "value": "03/04/2025", "locale": "US"}),
                Example("A relative phrase anchored to an explicit reference date.", {"mode": "parse", "value": "next friday 5pm", "ref_date": "2025-08-26T10:00:00", "tz": "Asia/Kolkata"}),
                Example("A unix timestamp in milliseconds is detected and reported.", {"mode": "parse", "value": 1755180000000}),
            ),
            failures=(
                Example("`03/04/2025` with no locale: both readings are returned in `needs.options` with their ISO dates, so the caller can pick.", {"mode": "parse", "value": "03/04/2025"}),
                Example("Text that is not a date at all.", {"mode": "parse", "value": "sometime next quarter-ish"}),
                Example("A date that does not exist.", {"mode": "parse", "value": "31/02/2025"}),
                Example("An unknown locale code.", {"mode": "parse", "value": "03/04/2025", "locale": "XX"}),
                Example("`value` is required.", {"mode": "parse"}),
            ),
        ),
        Mode(
            name="add",
            purpose="Add or subtract a duration, including business days.",
            description=(
                "Adds `amount` of `unit` to a date. Calendar units (`months`, `years`) clamp to the end "
                "of the month and warn when they do — 31 Jan + 1 month is 28 Feb, not 3 March. Elapsed "
                "units (`hours`, `minutes`, `seconds`) are computed through UTC so a DST transition "
                "cannot swallow an hour. `unit: business_days` walks the calendar, skipping weekends and "
                "— when `region` is given — public holidays, listing each one skipped."
            ),
            params=(
                Param("value", "string", False, "Starting date.", "`now`"),
                Param("amount", "number", True, "How much to add; negative subtracts."),
                Param("unit", "string", True, "`seconds`, `minutes`, `hours`, `days`, `weeks`, `fortnights`, `months`, `quarters`, `years`, `business_days`."),
                Param("region", "string", False, "ISO country code whose public holidays to skip, for `business_days`."),
                Param("subdiv", "string", False, "State/province code for regional holidays."),
                Param("weekend", "string[]", False, "Which weekdays count as weekend.", "`[saturday, sunday]`"),
                Param("extra_holidays", "string[] \\| object[]", False, "Extra non-working dates."),
                Param("tz", "string", False, "Zone applied to `value`."),
                Param("locale", "string", False, "Country code for reading numeric dates."),
            ),
            examples=(
                Example("Month arithmetic that clamps, with the clamp reported in `warnings`.", {"mode": "add", "value": "2025-01-31", "amount": 1, "unit": "months"}),
                Example("Three business days in India, listing the public holiday it stepped over.", {"mode": "add", "value": "2025-08-13", "amount": 3, "unit": "business_days", "region": "IN"}),
                Example("Elapsed hours across a US DST spring-forward: the wall clock jumps, the elapsed time does not.", {"mode": "add", "value": "2025-03-09T00:30:00", "tz": "America/New_York", "amount": 3, "unit": "hours"}),
                Example("Subtracting, with a negative amount.", {"mode": "add", "value": "2025-08-26", "amount": -2, "unit": "weeks"}),
            ),
            failures=(
                Example("`amount` and `unit` are both required.", {"mode": "add", "value": "2025-08-26", "amount": 3}),
                Example("An unknown unit.", {"mode": "add", "value": "2025-08-26", "amount": 3, "unit": "fortnite"}),
                Example("Fractional months have no defined meaning.", {"mode": "add", "value": "2025-08-26", "amount": 1.5, "unit": "months"}),
            ),
        ),
        Mode(
            name="diff",
            purpose="The distance between two instants, every way at once.",
            description=(
                "Returns the gap between `from` and `to` as a calendar breakdown (years/months/days/…), "
                "as totals in every unit, as whole months, and as a human string — plus a `sign` and a "
                "plain-English `direction`, so a negative result cannot be misread. Pass "
                "`unit: business_days` (with an optional `region`) to count working days instead."
            ),
            params=(
                Param("from", "string", True, "Start instant."),
                Param("to", "string", False, "End instant.", "`now`"),
                Param("unit", "string", False, "Report one unit in `value`; `business_days` counts working days.", "`auto`"),
                Param("region", "string", False, "ISO country code for holidays, with `business_days`."),
                Param("weekend", "string[]", False, "Which weekdays are non-working.", "`[saturday, sunday]`"),
                Param("tz", "string", False, "Zone applied to both sides."),
                Param("locale", "string", False, "Country code for reading numeric dates."),
            ),
            examples=(
                Example("Calendar breakdown and totals between two dates.", {"mode": "diff", "from": "2025-01-01", "to": "2025-03-15"}),
                Example("Working days between the same two dates, excluding Indian public holidays.", {"mode": "diff", "from": "2025-01-01", "to": "2025-03-15", "unit": "business_days", "region": "IN"}),
                Example("A backwards range: `sign` is −1 and `direction` says so in words.", {"mode": "diff", "from": "2025-03-15T18:00:00", "to": "2025-03-15T09:30:00"}),
            ),
            failures=(
                Example("`from` is required.", {"mode": "diff", "to": "2025-01-01"}),
                Example("An unknown unit.", {"mode": "diff", "from": "2025-01-01", "to": "2025-02-01", "unit": "moons"}),
                Example("An ambiguous numeric date on either side is refused, exactly as in `parse`.", {"mode": "diff", "from": "01/02/2025", "to": "2025-03-01"}),
            ),
        ),
        Mode(
            name="weekday",
            purpose="Everything calendar-shaped about one date.",
            description=(
                "Given a date, returns the weekday (name, ISO number and Monday-zero index), whether it "
                "is a weekend, the ISO week and ISO year, day of year, days in that month, leap-year "
                "flag, quarter, month name and week of month. One call instead of six derivations."
            ),
            params=(
                Param("value", "string", False, "The date.", "`today`"),
                Param("tz", "string", False, "Zone applied to `value`."),
                Param("locale", "string", False, "Country code for reading numeric dates."),
            ),
            examples=(
                Example("A public holiday that happens to fall on a Friday.", {"mode": "weekday", "value": "2025-08-15"}),
                Example("A leap day, with `is_leap_year` and `days_in_month` confirming it.", {"mode": "weekday", "value": "2024-02-29"}),
            ),
            failures=(
                Example("A date that does not exist in that month.", {"mode": "weekday", "value": "31/02/2025"}),
                Example("An ambiguous numeric date.", {"mode": "weekday", "value": "03/04/2025"}),
            ),
        ),
        Mode(
            name="nth_weekday",
            purpose="The nth (or last) weekday of a month.",
            description=(
                "Resolves rules like “fourth Thursday of November” or “last Friday of the quarter”. "
                "`n` may be an ordinal word (`first`, `last`) or a number; negative counts back from the "
                "end of the month. If the month has no such weekday, that is an error rather than a "
                "silently clamped date."
            ),
            params=(
                Param("year", "integer", False, "Year. Taken from `value`, or today, when omitted."),
                Param("month", "integer \\| string", False, "Month number or name."),
                Param("weekday", "string \\| integer", True, "Weekday name, abbreviation, or Monday-zero index."),
                Param("n", "integer \\| string", False, "Which one: 1..5, −1..−5, or `first`/`last`.", "1"),
                Param("value", "string", False, "Date whose year/month to use when `year`/`month` are omitted."),
            ),
            examples=(
                Example("US Thanksgiving 2025: the fourth Thursday of November.", {"mode": "nth_weekday", "year": 2025, "month": 11, "weekday": "thursday", "n": 4}),
                Example("The last Friday of February 2025, counting backwards.", {"mode": "nth_weekday", "year": 2025, "month": 2, "weekday": "friday", "n": -1}),
                Example("Ordinal words work too, and the month can be a name.", {"mode": "nth_weekday", "year": 2025, "month": "September", "weekday": "monday", "n": "first"}),
            ),
            failures=(
                Example("February 2025 has only four Fridays.", {"mode": "nth_weekday", "year": 2025, "month": 2, "weekday": "friday", "n": 5}),
                Example("`weekday` is required.", {"mode": "nth_weekday", "year": 2025, "month": 2}),
                Example("`n` cannot be zero.", {"mode": "nth_weekday", "year": 2025, "month": 2, "weekday": "friday", "n": 0}),
                Example("An unknown weekday name.", {"mode": "nth_weekday", "year": 2025, "month": 2, "weekday": "sunsday"}),
            ),
        ),
        Mode(
            name="business_days",
            purpose="Count working days in a range, holidays included.",
            description=(
                "Counts working days between two dates, both ends inclusive by default (matching Excel’s "
                "NETWORKDAYS). Weekends are configurable — pass `weekend: [friday, saturday]` for the "
                "Gulf working week — and a `region` (plus optional `subdiv`) pulls in that country’s "
                "public holidays. The response lists the holidays it skipped and, for short ranges, "
                "every working date."
            ),
            params=(
                Param("from", "string", True, "Start of the range."),
                Param("to", "string", True, "End of the range."),
                Param("region", "string", False, "ISO country code whose public holidays to exclude."),
                Param("subdiv", "string", False, "State/province code for regional holidays."),
                Param("weekend", "string[]", False, "Non-working weekdays.", "`[saturday, sunday]`"),
                Param("extra_holidays", "string[] \\| object[]", False, "Extra non-working dates, e.g. a company shutdown."),
                Param("include_start", "boolean", False, "Count the start date.", "`true`"),
                Param("include_end", "boolean", False, "Count the end date.", "`true`"),
            ),
            examples=(
                Example("Working days in an Indian August, with the Independence Day holiday named.", {"mode": "business_days", "from": "2025-08-11", "to": "2025-08-22", "region": "IN"}),
                Example("A Friday/Saturday weekend, as used across the Gulf.", {"mode": "business_days", "from": "2025-08-11", "to": "2025-08-22", "weekend": ["friday", "saturday"], "region": "AE"}),
                Example("Regional holidays via `subdiv`, plus a company shutdown day of your own.", {"mode": "business_days", "from": "2025-10-01", "to": "2025-10-10", "region": "IN", "subdiv": "WB", "extra_holidays": ["2025-10-06"]}),
            ),
            failures=(
                Example("Both ends are required.", {"mode": "business_days", "from": "2025-08-01"}),
                Example("An unknown weekday in `weekend`.", {"mode": "business_days", "from": "2025-08-01", "to": "2025-08-10", "weekend": ["funday"]}),
                Example("An unsupported holiday region.", {"mode": "business_days", "from": "2025-08-01", "to": "2025-08-10", "region": "XX"}),
            ),
        ),
        Mode(
            name="overlap",
            purpose="Do two intervals overlap, and by how much?",
            description=(
                "Compares two half-open intervals `[start, end)` and names their relation — `a contains b`, "
                "`a overlaps b`, `a meets b`, `a before b` and so on. When they overlap you get the "
                "overlapping window and its length; when they do not, you get the gap. Both intervals "
                "must agree about whether they carry a timezone."
            ),
            params=(
                Param("a", "object", True, "First interval, `{start, end}`."),
                Param("b", "object", True, "Second interval, `{start, end}`."),
                Param("tz", "string", False, "Zone applied to naive endpoints."),
                Param("locale", "string", False, "Country code for reading numeric dates."),
            ),
            examples=(
                Example("Two meetings that collide, with the colliding window returned.", {"mode": "overlap", "a": {"start": "2025-08-26T09:00:00", "end": "2025-08-26T10:30:00"}, "b": {"start": "2025-08-26T10:00:00", "end": "2025-08-26T11:00:00"}}),
                Example("Two that do not, with the gap between them.", {"mode": "overlap", "a": {"start": "2025-08-26T09:00:00", "end": "2025-08-26T10:00:00"}, "b": {"start": "2025-08-26T14:00:00", "end": "2025-08-26T15:00:00"}}),
                Example("Containment is named, not just detected.", {"mode": "overlap", "a": {"start": "2025-08-26T09:00:00", "end": "2025-08-26T18:00:00"}, "b": {"start": "2025-08-26T11:00:00", "end": "2025-08-26T12:00:00"}}),
            ),
            failures=(
                Example("An interval must be an object with `start` and `end`.", {"mode": "overlap", "a": "2025-08-26", "b": {"start": "2025-08-26T09:00:00", "end": "2025-08-26T10:00:00"}}),
                Example("An interval that ends before it starts.", {"mode": "overlap", "a": {"start": "2025-08-26T12:00:00", "end": "2025-08-26T09:00:00"}, "b": {"start": "2025-08-26T09:00:00", "end": "2025-08-26T10:00:00"}}),
                Example("One side aware, the other naive.", {"mode": "overlap", "a": {"start": "2025-08-26T09:00:00+05:30", "end": "2025-08-26T10:00:00+05:30"}, "b": {"start": "2025-08-26T09:30:00", "end": "2025-08-26T10:30:00"}}),
            ),
        ),
        Mode(
            name="duration_sum",
            purpose="Total a list of intervals — timesheets, shifts, sessions.",
            description=(
                "Adds up a list of `{start, end}` intervals and reports the total in seconds, minutes, "
                "hours, days, `HH:MM` and words, plus per-interval rows, the average, and the longest "
                "and shortest. Overlapping intervals are still summed — but each overlap is named in "
                "`warnings` so double-counting is never silent."
            ),
            params=(
                Param("ranges", "object[]", True, "Intervals, each `{start, end}` with an optional `label`."),
                Param("tz", "string", False, "Zone applied to naive endpoints."),
                Param("locale", "string", False, "Country code for reading numeric dates."),
            ),
            examples=(
                Example("Three labelled work sessions, totalled.", {"mode": "duration_sum", "ranges": [{"label": "morning", "start": "2025-08-26T09:15:00", "end": "2025-08-26T12:00:00"}, {"label": "afternoon", "start": "2025-08-26T13:00:00", "end": "2025-08-26T17:30:00"}, {"label": "evening", "start": "2025-08-26T20:00:00", "end": "2025-08-26T21:45:00"}]}),
                Example("Two sessions that overlap: the total still adds them, and `warnings` names the double count.", {"mode": "duration_sum", "ranges": [{"start": "2025-08-26T09:00:00", "end": "2025-08-26T11:00:00"}, {"start": "2025-08-26T10:30:00", "end": "2025-08-26T12:00:00"}]}),
            ),
            failures=(
                Example("`ranges` is required and must be a non-empty list.", {"mode": "duration_sum"}),
                Example("Every entry needs both a `start` and an `end`.", {"mode": "duration_sum", "ranges": [{"start": "2025-08-26T09:00:00"}]}),
                Example("An interval that runs backwards.", {"mode": "duration_sum", "ranges": [{"start": "2025-08-26T11:00:00", "end": "2025-08-26T09:00:00"}]}),
            ),
        ),
        Mode(
            name="recurrence",
            purpose="Expand a recurring schedule into dates.",
            description=(
                "Takes an RFC 5545 RRULE, or a plain-English phrase it converts to one — `every weekday`, "
                "`every other tuesday`, `every 2nd tuesday`, `every 15th of month`, `month end` — and "
                "lists the occurrences from `start`. Bound it with `count` or `until`; otherwise output "
                "stops at `limit` and says so in `warnings`. The RRULE actually used is echoed back."
            ),
            params=(
                Param("rule", "string", True, "RRULE string or a recognised phrase."),
                Param("start", "string", False, "First candidate date (DTSTART).", "`today`"),
                Param("count", "integer", False, "Stop after this many occurrences."),
                Param("until", "string", False, "Stop at this date."),
                Param("limit", "integer", False, "Hard cap on returned occurrences, max 1000.", "100"),
                Param("dates_only", "boolean", False, "Return dates rather than full timestamps.", "`true`"),
            ),
            examples=(
                Example("A phrase turned into an RRULE and expanded.", {"mode": "recurrence", "rule": "every 2nd tuesday", "start": "2025-01-01", "count": 5}),
                Example("A raw RRULE for a three-day-a-week standup.", {"mode": "recurrence", "rule": "FREQ=WEEKLY;BYDAY=MO,WE,FR", "start": "2025-01-01", "count": 6}),
                Example("Month ends, bounded by `until`.", {"mode": "recurrence", "rule": "month end", "start": "2025-01-01", "until": "2025-06-30"}),
            ),
            failures=(
                Example("A phrase the converter does not recognise — it asks for an RRULE rather than guessing.", {"mode": "recurrence", "rule": "every blue moon", "start": "2025-01-01"}),
                Example("`rule` is required.", {"mode": "recurrence", "start": "2025-01-01"}),
                Example("`limit` is capped at 1000.", {"mode": "recurrence", "rule": "every day", "start": "2025-01-01", "limit": 5000}),
                Example("A malformed RRULE.", {"mode": "recurrence", "rule": "FREQ=FORTNIGHTLY", "start": "2025-01-01", "count": 3}),
            ),
        ),
        Mode(
            name="cron_next",
            purpose="The next fire times of a cron expression.",
            description=(
                "Evaluates a standard five-field cron expression (`minute hour day month weekday`) and "
                "returns the next `n` times it fires, in the zone you name. `@daily`, `@weekly`, "
                "`@monthly`, `@hourly`, `@yearly` are understood. The day-of-month/day-of-week OR rule "
                "— the one everybody gets wrong — is applied and stated in `assumptions`."
            ),
            params=(
                Param("expr", "string", True, "Cron expression or `@`-alias."),
                Param("tz", "string", False, "Zone the schedule runs in.", "`UTC`"),
                Param("from", "string", False, "Start searching after this instant.", "now"),
                Param("n", "integer", False, "How many fire times to return, 1..500.", "5"),
            ),
            examples=(
                Example("Weekday mornings in Kolkata.", {"mode": "cron_next", "expr": "0 9 * * 1-5", "tz": "Asia/Kolkata", "from": "2025-08-15T00:00:00", "n": 3}),
                Example("An alias, and a step field.", {"mode": "cron_next", "expr": "@monthly", "from": "2025-01-15T00:00:00", "n": 3}),
                Example("Every 15 minutes during office hours.", {"mode": "cron_next", "expr": "*/15 9-10 * * *", "from": "2025-08-15T09:00:00", "n": 4}),
            ),
            failures=(
                Example("A cron expression must have five fields.", {"mode": "cron_next", "expr": "0 9 * *"}),
                Example("A value outside its field’s range.", {"mode": "cron_next", "expr": "99 9 * * *"}),
                Example("`expr` is required.", {"mode": "cron_next"}),
            ),
        ),
        Mode(
            name="age",
            purpose="Age on a given date, and the next birthday.",
            description=(
                "Years, months and days between a date of birth and a reference date, plus total days, "
                "total months, the next birthday, days until it and the age being turned. February 29 "
                "birthdays are moved to February 28 in non-leap years, and that choice is stated in "
                "`assumptions` rather than assumed."
            ),
            params=(
                Param("dob", "string", True, "Date of birth."),
                Param("on", "string", False, "Date to compute the age on.", "`today`"),
                Param("locale", "string", False, "Country code for reading numeric dates."),
            ),
            examples=(
                Example("An age on a fixed date.", {"mode": "age", "dob": "1990-02-14", "on": "2025-08-26"}),
                Example("A leap-day birthday in a non-leap year.", {"mode": "age", "dob": "2000-02-29", "on": "2025-03-01"}),
            ),
            failures=(
                Example("`dob` is required.", {"mode": "age", "on": "2025-08-26"}),
                Example("The reference date precedes the birth date.", {"mode": "age", "dob": "2025-08-26", "on": "1990-01-01"}),
            ),
        ),
        Mode(
            name="fiscal",
            purpose="Which fiscal year and quarter a date falls in.",
            description=(
                "Maps a date onto a fiscal calendar. `region` selects a known convention — India and the "
                "UK start in April, Australia in July, the US federal year in October — or set "
                "`fy_start_month` directly. Returns the FY label, its start and end, the quarter with "
                "its bounds, the day of the fiscal year and the days remaining."
            ),
            params=(
                Param("value", "string", False, "The date to place.", "`today`"),
                Param("region", "string", False, "ISO country code selecting a known FY convention."),
                Param("fy_start_month", "integer", False, "First month of the fiscal year, 1..12.", "1 (calendar year)"),
                Param("tz", "string", False, "Zone applied to `value`."),
            ),
            examples=(
                Example("India: an April-to-March fiscal year.", {"mode": "fiscal", "value": "2025-08-26", "region": "IN"}),
                Example("The US federal year, which starts in October and is labelled by its end.", {"mode": "fiscal", "value": "2025-08-26", "region": "US"}),
                Example("An explicit start month for a company that does not follow its country.", {"mode": "fiscal", "value": "2025-08-26", "fy_start_month": 7}),
            ),
            failures=(
                Example("`fy_start_month` must be a real month.", {"mode": "fiscal", "value": "2025-08-26", "fy_start_month": 13}),
                Example("An unparseable date.", {"mode": "fiscal", "value": "the third quarter"}),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# scale
# --------------------------------------------------------------------------- #

SCALE = ToolDoc(
    name="scale",
    tagline="Scale numbers and recipes proportionally",
    intro=(
        "One quantity changes and everything tied to it has to change with it: a recipe for 4 "
        "becomes a recipe for 7, a price per kilogram becomes a price per 250 g, three workers "
        "become twelve. `scale` computes the factor once and applies it to every entity, returning "
        "each result as a decimal, an exact fraction, a mixed number, and its floor/ceiling — so "
        "“1 ¾ cups” and “round up to 2 eggs” are both already there."
    ),
    when=(
        "Recipe and batch scaling, where fractions matter more than decimals.",
        "Unit-price conversion: `from_unit`/`to_unit` fold the unit change into the factor.",
        "Inverse proportion — more workers, fewer days — with `mode: inverse`.",
        "Any “hold the ratios constant” calculation across several line items at once.",
    ),
    fn=scale_mod.scale,
    related=(
        "[`convert`](/docs/tools/convert) when only the unit changes · "
        "[`numbers`](/docs/tools/numbers) `allocate` when a total must be split so the parts sum "
        "exactly · [`math`](/docs/tools/math) for anything that is not a proportion."
    ),
    extra_modes=("linear", "inverse"),
    modes=(
        Mode(
            name="linear",
            purpose="Direct proportion: double the input, double every entity.",
            description=(
                "The default. The factor is `to_qty / from_qty`; every entity is multiplied by it. "
                "Supply `from_unit` and `to_unit` and the unit change is folded into the factor "
                "(1 kg → 250 g gives a factor of ¼). Supply `factor` directly to skip the ratio "
                "entirely. Each entity comes back with `original`, `scaled` and `per_unit`, each "
                "carrying a decimal, an exact fraction, a mixed number, a floor and a ceiling; set "
                "`integer: true` on an entity that cannot be fractional and it is rounded up with a "
                "warning."
            ),
            params=(
                Param("from_qty", "number \\| string", True, "The quantity you have. Not required if `factor` is given."),
                Param("to_qty", "number \\| string", False, "The quantity you want.", "1, if `to_unit` is given"),
                Param("from_unit", "string", False, "Unit of `from_qty`."),
                Param("to_unit", "string", False, "Unit of `to_qty`; the conversion is folded into the factor."),
                Param("factor", "number \\| string", False, "Use this factor instead of a ratio."),
                Param("entities", "object[] \\| object", False, "Things to scale: `{name, qty, unit?, integer?}`, or a `{name: qty}` map."),
                Param("precision", "integer", False, "Decimal places in the `value` fields.", "6"),
                Param("assume", "string", False, "Pass `common` to resolve ambiguous units to their usual reading."),
            ),
            examples=(
                Example(
                    "A recipe for 4 rescaled to 7 servings. Note the mixed numbers and the egg rounded up.",
                    {"mode": "linear", "from_qty": 4, "to_qty": 7, "entities": [{"name": "flour", "qty": 2, "unit": "cups"}, {"name": "butter", "qty": 150, "unit": "g"}, {"name": "eggs", "qty": 2, "integer": True}]},
                ),
                Example(
                    "A price per kilogram restated per 250 g: the unit change becomes the factor.",
                    {"mode": "linear", "from_qty": 1, "from_unit": "kg", "to_qty": 250, "to_unit": "g", "entities": [{"name": "price", "qty": 480}]},
                ),
                Example(
                    "An explicit factor, with entities given as a plain map.",
                    {"mode": "linear", "factor": 1.15, "entities": {"salary": 62000, "bonus": 8000}},
                ),
            ),
            failures=(
                Example("`from_qty` (or `factor`) is required.", {"mode": "linear", "to_qty": 7, "entities": [{"name": "flour", "qty": 2}]}),
                Example("A zero base has no factor.", {"mode": "linear", "from_qty": 0, "to_qty": 7, "entities": [{"name": "flour", "qty": 2}]}),
                Example("Every entity needs a `qty`.", {"mode": "linear", "from_qty": 4, "to_qty": 7, "entities": [{"name": "flour"}]}),
                Example("Units that cannot be related to each other.", {"mode": "linear", "from_qty": 1, "from_unit": "kg", "to_qty": 1, "to_unit": "liter", "entities": [{"name": "price", "qty": 480}]}),
                Example("An ambiguous unit is refused here exactly as in `convert`.", {"mode": "linear", "from_qty": 1, "from_unit": "oz", "to_qty": 100, "to_unit": "g", "entities": [{"name": "price", "qty": 480}]}),
                Example("`mode` must be `linear` or `inverse`.", {"mode": "quadratic", "from_qty": 4, "to_qty": 7, "entities": [{"name": "flour", "qty": 2}]}),
            ),
        ),
        Mode(
            name="inverse",
            purpose="Inverse proportion: double the input, halve every entity.",
            description=(
                "For quantities that move the other way. The factor becomes `from_qty / to_qty`, so "
                "tripling the workers divides the days by three. Everything else — entities, exact "
                "fractions, `integer` rounding — behaves as in `linear`. `percent_change` is `null` "
                "here, because a percentage change of an inverse relationship invites misreading."
            ),
            params=(
                Param("from_qty", "number \\| string", True, "The quantity you have."),
                Param("to_qty", "number \\| string", True, "The quantity you want."),
                Param("entities", "object[] \\| object", False, "Things that move inversely: `{name, qty, unit?, integer?}`."),
                Param("from_unit", "string", False, "Unit of `from_qty`."),
                Param("to_unit", "string", False, "Unit of `to_qty`."),
                Param("precision", "integer", False, "Decimal places in the `value` fields.", "6"),
            ),
            examples=(
                Example("Three workers take five days; twelve workers take a quarter of that.", {"mode": "inverse", "from_qty": 3, "to_qty": 12, "entities": [{"name": "days", "qty": 5}]}),
                Example("Doubling the line speed shortens every downstream time.", {"mode": "inverse", "from_qty": 2, "to_qty": 5, "entities": {"hours_per_batch": 6, "operators_hours": 18}}),
            ),
            failures=(
                Example("An inverse relationship cannot target zero.", {"mode": "inverse", "from_qty": 3, "to_qty": 0, "entities": [{"name": "days", "qty": 5}]}),
                Example("Quantities must be numbers.", {"mode": "inverse", "from_qty": "a few", "to_qty": 12, "entities": [{"name": "days", "qty": 5}]}),
                Example("`to_qty` is required when no `factor` is given.", {"mode": "inverse", "from_qty": 3, "entities": [{"name": "days", "qty": 5}]}),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# convert
# --------------------------------------------------------------------------- #

CONVERT = ToolDoc(
    name="convert",
    tagline="Units and currencies, with the ambiguities refused",
    intro=(
        "Unit conversion on top of Pint, with one rule the rest of the world skips: a unit with two "
        "meanings is never guessed. `ton` (metric, short, long), `gallon` (US, imperial), `oz` (mass "
        "or fluid), `cup`, `pint`, `calorie`, `KB`/`GB` (decimal, binary, bits) all come back as "
        "`ambiguous` with the concrete options, unless you pass `assume: common`. Everything else "
        "converts exactly, with the conversion factor returned alongside the result."
    ),
    when=(
        "Any length, mass, area, volume, speed, energy, power, pressure, data or time conversion.",
        "Temperatures, where an absolute reading and a temperature difference are not the same sum.",
        "Currency, which needs a rate you supply — this tool never invents an exchange rate.",
        "Indian land units (bigha, katha, cent, ground, guntha, ankanam) and lakh/crore scaling.",
    ),
    fn=convert_mod.convert,
    related=(
        "[`scale`](/docs/tools/scale) when a unit change has to ripple through several line items · "
        "[`numbers`](/docs/tools/numbers) `format` to present the result for a locale · "
        "the `fx_rate` tool in `leftbrain-external` for live exchange rates."
    ),
    modes=(
        Mode(
            name="units",
            purpose="Convert between physical units.",
            description=(
                "Converts `value` from one unit to another. Aliases and human spellings are accepted "
                "(`sqft`, `kmph`, `lbs`, `cbm`, `mAh`-style compounds, `lakh`, `crore`), and the "
                "response includes the conversion `factor` — plus `factor_exact` when the factor is a "
                "clean rational, so `1 mile = 1609.344 m` can be checked rather than trusted. "
                "Assumptions that matter are surfaced: statute miles, SI vs binary bytes, the length "
                "of a “month”."
            ),
            params=(
                Param("value", "number \\| string", False, "The quantity to convert.", "1"),
                Param("from_unit", "string", True, "Source unit."),
                Param("to_unit", "string", True, "Target unit."),
                Param("assume", "string", False, "`common` resolves an ambiguous unit to its usual reading instead of failing."),
                Param("precision", "integer", False, "Significant digits in the result.", "10"),
            ),
            examples=(
                Example("Kilometres to miles, with the statute-mile assumption stated and the exact factor returned.", {"mode": "units", "value": 5, "from_unit": "km", "to_unit": "miles"}),
                Example("Square feet to square metres — `sqft` is understood as an alias.", {"mode": "units", "value": 1500, "from_unit": "sqft", "to_unit": "sqm"}),
                Example("Decimal to binary bytes, spelled out so the 7% difference is not a surprise.", {"mode": "units", "value": 1, "from_unit": "gigabyte", "to_unit": "gibibyte"}),
                Example("An ambiguous unit resolved on purpose with `assume`, and the reading recorded.", {"mode": "units", "value": 1, "from_unit": "ton", "to_unit": "kg", "assume": "common"}),
                Example("An Indian land unit, defined in the registry.", {"mode": "units", "value": 1, "from_unit": "bigha", "to_unit": "sqft"}),
            ),
            failures=(
                Example("`ton` is three different masses. The options come back in `needs.options`.", {"mode": "units", "value": 1, "from_unit": "ton", "to_unit": "kg"}),
                Example("`gallon` is US or imperial — a 20% difference.", {"mode": "units", "value": 1, "from_unit": "gallon", "to_unit": "liter"}),
                Example("`oz` is a mass or a volume depending on what is being measured.", {"mode": "units", "value": 8, "from_unit": "oz", "to_unit": "g"}),
                Example("`GB` may be decimal bytes, binary bytes or bits.", {"mode": "units", "value": 1, "from_unit": "GB", "to_unit": "MB"}),
                Example("Dimensions that do not relate.", {"mode": "units", "value": 5, "from_unit": "km", "to_unit": "kg"}),
                Example("An unknown unit.", {"mode": "units", "value": 5, "from_unit": "blorg", "to_unit": "km"}),
                Example("`to_unit` is required.", {"mode": "units", "value": 5, "from_unit": "km"}),
            ),
        ),
        Mode(
            name="temperature",
            purpose="Absolute temperatures and temperature differences.",
            description=(
                "Temperature conversion, where the distinction that trips everyone up is explicit: an "
                "absolute reading (25 °C is 77 °F) and a difference (a rise of 25 °C is a rise of 45 °F) "
                "are different sums. Absolute is the default and is stated in `assumptions`; pass "
                "`delta: true` for a difference. `C`, `F`, `K`, `°C`, `celsius`, `degF` and friends all "
                "resolve."
            ),
            params=(
                Param("value", "number \\| string", False, "The temperature.", "1"),
                Param("from_unit", "string", True, "Source scale: `C`, `F`, `K`, `degR`…"),
                Param("to_unit", "string", True, "Target scale."),
                Param("delta", "boolean", False, "Treat the value as a difference, not a reading.", "`false`"),
                Param("precision", "integer", False, "Significant digits in the result.", "10"),
            ),
            examples=(
                Example("An absolute reading.", {"mode": "temperature", "value": 100, "from_unit": "C", "to_unit": "F"}),
                Example("The same number as a difference — a different answer, and the tool says which it used.", {"mode": "temperature", "value": 100, "from_unit": "C", "to_unit": "F", "delta": True}),
                Example("Body temperature into kelvin.", {"mode": "temperature", "value": 98.6, "from_unit": "F", "to_unit": "K"}),
            ),
            failures=(
                Example("A temperature cannot become a length.", {"mode": "temperature", "value": 100, "from_unit": "C", "to_unit": "km"}),
                Example("`from_unit` is required.", {"mode": "temperature", "value": 100, "to_unit": "F"}),
            ),
        ),
        Mode(
            name="currency",
            purpose="Convert money using a rate you supply.",
            description=(
                "Converts an amount between ISO 4217 currency codes. There is no built-in rate table and "
                "no network call: pass `rate` (1 `from_unit` = `rate` `to_unit`) or a `rates` map, and "
                "the rate used is echoed in `assumptions`. Without either, the call fails with "
                "`needs_rates` and tells you how to get one. The result carries both the rounded value "
                "and `value_exact` as a fraction, so a chain of conversions never accumulates rounding "
                "error."
            ),
            params=(
                Param("value", "number \\| string", False, "The amount.", "1"),
                Param("from_unit", "string", True, "Source ISO 4217 code, e.g. `USD`."),
                Param("to_unit", "string", True, "Target ISO 4217 code."),
                Param("rate", "number", False, "Direct rate: 1 `from_unit` = `rate` `to_unit`."),
                Param("rates", "object", False, "Rate table keyed by currency code."),
                Param("base", "string", False, "Base currency of the `rates` table."),
                Param("decimals", "integer", False, "Decimal places in the rounded value.", "2"),
                Param("date", "string", False, "Echoed back as `as_of`; the tool does not use it to look anything up."),
            ),
            examples=(
                Example("A direct rate.", {"mode": "currency", "value": 100, "from_unit": "USD", "to_unit": "INR", "rate": 83.42}),
                Example("A rate table with a base currency — the cross rate is derived.", {"mode": "currency", "value": 250, "from_unit": "EUR", "to_unit": "INR", "rates": {"USD": 1, "EUR": 0.92, "INR": 83.42}, "base": "USD"}),
                Example("A zero-decimal currency.", {"mode": "currency", "value": 100, "from_unit": "USD", "to_unit": "JPY", "rate": 147.2, "decimals": 0}),
            ),
            failures=(
                Example("No rate and no table: the tool refuses to invent one and says where to get it.", {"mode": "currency", "value": 100, "from_unit": "USD", "to_unit": "INR"}),
                Example("Currency codes must be three letters.", {"mode": "currency", "value": 100, "from_unit": "DOLLAR", "to_unit": "INR", "rate": 83.42}),
                Example("A rate table that does not cover both sides.", {"mode": "currency", "value": 100, "from_unit": "AUD", "to_unit": "INR", "rates": {"USD": 1, "EUR": 0.92}}),
            ),
        ),
        Mode(
            name="auto",
            purpose="Pick units or currency from the arguments.",
            description=(
                "The default mode. If both units look like ISO 4217 currency codes — three upper-case "
                "letters — the call is treated as a currency conversion; otherwise it is a unit "
                "conversion. Everything else behaves exactly as in the mode it dispatches to, "
                "ambiguity refusals included. Name the mode explicitly when you want to be certain "
                "which path you get."
            ),
            params=(
                Param("value", "number \\| string", False, "The quantity or amount.", "1"),
                Param("from_unit", "string", True, "Source unit or currency code."),
                Param("to_unit", "string", True, "Target unit or currency code."),
                Param("rate", "number", False, "Rate, when the arguments resolve to a currency conversion."),
                Param("rates", "object", False, "Rate table, as in `currency`."),
                Param("assume", "string", False, "`common`, as in `units`."),
            ),
            examples=(
                Example("Two unit names: the unit path.", {"mode": "auto", "value": 10, "from_unit": "km", "to_unit": "mi"}),
                Example("Two ISO codes and a rate: the currency path.", {"mode": "auto", "value": 100, "from_unit": "USD", "to_unit": "INR", "rate": 83.42}),
            ),
            failures=(
                Example("Detected as currency, but with no rate supplied.", {"mode": "auto", "value": 100, "from_unit": "USD", "to_unit": "INR"}),
                Example("Detected as units, and still refused when ambiguous.", {"mode": "auto", "value": 1, "from_unit": "ton", "to_unit": "kg"}),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# holidays
# --------------------------------------------------------------------------- #

HOLIDAYS = ToolDoc(
    name="holidays",
    tagline="Public holidays by country and region",
    intro=(
        "Public holiday calendars for 150-plus countries, offline. A model's holiday knowledge is "
        "stale and hallucinates regional ones; this dataset is generated from published rules, so "
        "moving feasts and state-specific days are right. Regions are ISO country codes; states and "
        "provinces go in `subdiv`, and without one you get national holidays only — said out loud in "
        "`assumptions` rather than left to be discovered."
    ),
    when=(
        "Before promising a delivery or SLA date in a country you do not live in.",
        "Planning around long weekends — `list` returns them already computed.",
        "Checking whether one specific date is a working day.",
        "Feeding `region`/`subdiv` into `datetime`'s `business_days` and `add`.",
    ),
    fn=holidays_.holidays,
    related=(
        "[`datetime`](/docs/tools/datetime) `business_days` and `add` consume the same `region` and "
        "`subdiv` · [`geo_offline`](/docs/tools/geo_offline) `country` to resolve a country name to "
        "its ISO code."
    ),
    modes=(
        Mode(
            name="list",
            purpose="Every holiday in a year, plus the long weekends.",
            description=(
                "Lists the holidays for one or more years, optionally filtered to a single month. "
                "Alongside the list you get `long_weekends`: every holiday that falls on a Friday or a "
                "Monday, with the span it creates — the thing people actually want when they ask for a "
                "holiday list."
            ),
            params=(
                Param("region", "string", True, "ISO country code (`IN`, `US`, `GB`); `UK` is accepted as `GB`."),
                Param("year", "integer", False, "The year.", "current year"),
                Param("years", "integer[]", False, "Several years at once."),
                Param("month", "integer", False, "Filter the list to one month; long weekends still cover the year."),
                Param("subdiv", "string", False, "State or province code for regional holidays."),
                Param("categories", "string[]", False, "Holiday categories, where the country's calendar defines them."),
            ),
            examples=(
                Example("India, one month, with the year's long weekends alongside.", {"mode": "list", "region": "IN", "year": 2025, "month": 8}),
                Example("The US, November.", {"mode": "list", "region": "US", "year": 2025, "month": 11}),
                Example("West Bengal's regional holidays, which the national list does not contain.", {"mode": "list", "region": "IN", "year": 2025, "month": 10, "subdiv": "WB"}),
            ),
            failures=(
                Example("`region` is required.", {"mode": "list", "year": 2025}),
                Example("An unsupported country code.", {"mode": "list", "region": "XX", "year": 2025}),
                Example("An unknown subdivision — the valid codes come back in the message.", {"mode": "list", "region": "IN", "year": 2025, "subdiv": "ZZ"}),
            ),
        ),
        Mode(
            name="check",
            purpose="Is this one date a public holiday?",
            description=(
                "Answers for a single date: whether it is a holiday, its name if so, its weekday, and "
                "whether it falls on a weekend. The date goes through the same parser as `datetime`, so "
                "an ambiguous numeric date is refused here too."
            ),
            params=(
                Param("region", "string", True, "ISO country code."),
                Param("date", "string", False, "The date to check.", "`today`"),
                Param("subdiv", "string", False, "State or province code."),
                Param("locale", "string", False, "Country code deciding DD/MM vs MM/DD in `date`."),
            ),
            examples=(
                Example("A national holiday.", {"mode": "check", "region": "IN", "date": "2025-08-15"}),
                Example("The next day, which is not.", {"mode": "check", "region": "IN", "date": "2025-08-16"}),
                Example("A date that is only a holiday in one state.", {"mode": "check", "region": "IN", "date": "2025-10-20", "subdiv": "WB"}),
            ),
            failures=(
                Example("An ambiguous numeric date, refused exactly as `datetime` refuses it.", {"mode": "check", "region": "IN", "date": "03/04/2025"}),
                Example("An unparseable date.", {"mode": "check", "region": "IN", "date": "diwali"}),
                Example("`region` is required.", {"mode": "check", "date": "2025-08-15"}),
            ),
        ),
        Mode(
            name="next",
            purpose="The upcoming holidays from a date.",
            description=(
                "Returns the next `n` holidays on or after a date, each with its weekday and how many "
                "days away it is. The window spans the given year and the next, so a query late in "
                "December still returns January's holidays."
            ),
            params=(
                Param("region", "string", True, "ISO country code."),
                Param("date", "string", False, "Start looking from here.", "`today`"),
                Param("n", "integer", False, "How many holidays to return.", "5"),
                Param("subdiv", "string", False, "State or province code."),
            ),
            examples=(
                Example("The next three Indian holidays after a fixed date.", {"mode": "next", "region": "IN", "date": "2025-08-01", "n": 3}),
                Example("The same question for the UK, crossing into the following year.", {"mode": "next", "region": "GB", "date": "2025-12-20", "n": 3}),
            ),
            failures=(
                Example("`region` is required.", {"mode": "next", "date": "2025-08-01"}),
                Example("An unsupported region.", {"mode": "next", "region": "Atlantis", "date": "2025-08-01"}),
            ),
        ),
        Mode(
            name="countries",
            purpose="Every supported country code.",
            description=(
                "Lists every ISO country code the dataset covers — about 150. Call it once to find out "
                "whether a country is supported before building a `region` into a workflow. The listing "
                "below is trimmed for length; the call returns all of them."
            ),
            params=(),
            examples=(
                Example("Every supported country code.", {"mode": "countries"}),
            ),
            failures=(),
            never_fails="This mode takes no parameters and always succeeds.",
        ),
        Mode(
            name="subdivisions",
            purpose="The state/province codes a country supports.",
            description=(
                "Lists the `subdiv` codes valid for a country. Countries with no regional calendar "
                "return an empty list — which is the answer to “does this country have state "
                "holidays?”, not an error."
            ),
            params=(
                Param("region", "string", True, "ISO country code."),
            ),
            examples=(
                Example("India's state codes.", {"mode": "subdivisions", "region": "IN"}),
                Example("The UK's four nations.", {"mode": "subdivisions", "region": "GB"}),
            ),
            failures=(
                Example("`region` is required.", {"mode": "subdivisions"}),
                Example("An unsupported region.", {"mode": "subdivisions", "region": "XX"}),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# numbers
# --------------------------------------------------------------------------- #

NUMBERS = ToolDoc(
    name="numbers",
    tagline="Compare, round, format and allocate exactly",
    intro=(
        "Everything numeric that models get wrong for reasons that are not arithmetic: comparing "
        "`9.11` with `9.9` as decimals rather than version strings, rounding with a *stated* rule "
        "instead of whatever the language does, grouping digits the Indian way, and splitting an "
        "amount so the parts sum to the total exactly. Every value is parsed and computed as a "
        "`Decimal`, never a float, and inputs like `₹1.2 Cr`, `2.5k`, `(500)` and `12%` are understood."
    ),
    when=(
        "Comparing or ordering numbers that arrive as strings.",
        "Rounding when the tie-breaking rule matters — invoices, tax, prices.",
        "Formatting for a locale or a currency, including Indian lakh/crore grouping.",
        "Splitting a total across parts or weights so the shares reconcile to the cent.",
        "Reading messy human numbers, and spelling amounts out in words for documents.",
    ),
    fn=numbers_mod.numbers,
    related=(
        "[`math`](/docs/tools/math) for the arithmetic itself · "
        "[`convert`](/docs/tools/convert) for units and currency conversion · "
        "[`collections`](/docs/tools/collections) `aggregate` for sums across records."
    ),
    modes=(
        Mode(
            name="compare",
            purpose="Order two or more numbers, exactly.",
            description=(
                "Parses every value as a decimal and returns them sorted ascending and descending, the "
                "min and max, a readable `ordering` chain, and — for exactly two values — the relation, "
                "the difference and the percentage change. This is the `9.11 < 9.9` test: string "
                "comparison and version-number instinct both get it wrong."
            ),
            params=(
                Param("values", "any[]", True, "Two or more values. Strings, numbers, `₹1.2 Cr`, `2.5k`, `12%`, `(500)`."),
                Param("a", "any", False, "First value, as an alternative to `values`."),
                Param("b", "any", False, "Second value, as an alternative to `values`."),
            ),
            examples=(
                Example("The canonical case.", {"mode": "compare", "values": ["9.11", "9.9"]}),
                Example("Mixed human notation, all reduced to decimals before ordering.", {"mode": "compare", "values": ["1.2 Cr", "₹15,00,000", "2.5k", "0.03 bn"]}),
                Example("Two values give a relation, a difference and a percentage change.", {"mode": "compare", "a": "1,250.50", "b": "1,499.99"}),
            ),
            failures=(
                Example("Comparison needs at least two values.", {"mode": "compare", "values": ["9.11"]}),
                Example("A value that is not a number.", {"mode": "compare", "values": ["nine point one", "9.9"]}),
                Example("Neither `values` nor `a`/`b`.", {"mode": "compare"}),
            ),
        ),
        Mode(
            name="round",
            purpose="Round with an explicitly named rule.",
            description=(
                "Rounds to decimal places, to significant figures (`significant`) or to a step "
                "(`nearest`, e.g. 0.05 for cash rounding). `rounding` names the tie-break: `half_up`, "
                "`half_even` (bankers'), `half_down`, `floor`, `ceil`, `truncate`. The default is "
                "`half_up` — and the response points out that Python's own `round()` is `half_even`, "
                "which is why 2.5 and 0.5 disagree between systems."
            ),
            params=(
                Param("value", "number \\| string", True, "The value to round."),
                Param("decimals", "integer", False, "Decimal places.", "0"),
                Param("significant", "integer", False, "Round to this many significant figures instead."),
                Param("nearest", "number \\| string", False, "Round to the nearest multiple of this."),
                Param("rounding", "string", False, "Tie-break rule.", "`half_up`"),
            ),
            examples=(
                Example("Half-up: the rule most humans mean.", {"mode": "round", "value": "2.5", "decimals": 0}),
                Example("Bankers' rounding on the same value gives a different answer.", {"mode": "round", "value": "2.5", "decimals": 0, "rounding": "half_even"}),
                Example("Three significant figures.", {"mode": "round", "value": "1234.5678", "significant": 3}),
                Example("Cash rounding to the nearest five cents.", {"mode": "round", "value": "12.327", "nearest": "0.05"}),
            ),
            failures=(
                Example("An unknown rounding rule lists the valid ones.", {"mode": "round", "value": "2.5", "rounding": "cosmic"}),
                Example("Zero significant figures is meaningless.", {"mode": "round", "value": "2.5", "significant": 0}),
                Example("A step must be positive.", {"mode": "round", "value": "2.5", "nearest": 0}),
                Example("An unparseable value.", {"mode": "round", "value": "two and a half"}),
            ),
        ),
        Mode(
            name="format",
            purpose="Present a number for a locale, currency or style.",
            description=(
                "Groups digits the way a locale does — `12,34,567.89` for `en_IN`, `1.234.567,89` for "
                "`de_DE`, thin-space groups for `fr_FR` — and applies a `style`: `number`, `currency`, "
                "`percent` or `compact`. Compact notation follows the locale too: `1.2 Cr` for India, "
                "`12M` elsewhere. `accounting: true` wraps negatives in parentheses."
            ),
            params=(
                Param("value", "number \\| string", True, "The value to format."),
                Param("locale", "string", False, "`en_IN`, `en_US`, `de_DE`, `fr_FR`, `de_CH`, `ja_JP`…", "`en_US`"),
                Param("style", "`number` \\| `currency` \\| `percent` \\| `compact`", False, "Presentation style.", "`number`"),
                Param("currency", "string", False, "ISO code, for `style: currency` or `compact`."),
                Param("decimals", "integer", False, "Decimal places.", "style-dependent"),
                Param("accounting", "boolean", False, "Show negatives in parentheses.", "`false`"),
            ),
            examples=(
                Example("Indian digit grouping — two-digit groups above the thousand.", {"mode": "format", "value": 12345678.9, "locale": "en_IN"}),
                Example("The same number for Germany, where the separators swap.", {"mode": "format", "value": 12345678.9, "locale": "de_DE"}),
                Example("Currency, with the symbol and the right number of decimals.", {"mode": "format", "value": "1234567.891", "locale": "en_IN", "style": "currency", "currency": "INR"}),
                Example("Compact Indian notation.", {"mode": "format", "value": 12345678, "locale": "en_IN", "style": "compact", "currency": "INR"}),
                Example("A percentage, and an accounting-style negative.", {"mode": "format", "value": "-0.0725", "style": "percent", "accounting": True}),
            ),
            failures=(
                Example("An unsupported locale.", {"mode": "format", "value": 1234.5, "locale": "xx_YY"}),
                Example("An unparseable value.", {"mode": "format", "value": "lots"}),
            ),
        ),
        Mode(
            name="allocate",
            purpose="Split a total so the parts sum exactly to it.",
            description=(
                "Divides a total into parts whose shares add up to the total exactly — no missing cent, "
                "no extra one. Split equally with `parts`, or proportionally with `weights` "
                "(a list, or a `{label: weight}` map) or `percentages`. Leftover minor units go to the "
                "largest fractional remainders by default; `method: first` or `last` puts them all in "
                "one place instead. Each item reports its exact unrounded share and whether it was "
                "adjusted, so the arithmetic is auditable."
            ),
            params=(
                Param("total", "number \\| string", True, "The amount to divide."),
                Param("parts", "integer", False, "Split equally into this many parts."),
                Param("weights", "number[] \\| object", False, "Proportional weights, or a `{label: weight}` map."),
                Param("percentages", "number[]", False, "Weights that must sum to 100."),
                Param("labels", "string[]", False, "Names for the parts."),
                Param("decimals", "integer", False, "Minor-unit precision.", "2"),
                Param("method", "`largest_remainder` \\| `first` \\| `last`", False, "Where leftover units go.", "`largest_remainder`"),
            ),
            examples=(
                Example("100 split three ways: two parts get 33.33, one gets 33.34, and the total is exact.", {"mode": "allocate", "total": 100, "parts": 3}),
                Example("A labelled weighted split.", {"mode": "allocate", "total": "10000", "weights": {"alice": 3, "bob": 2, "carol": 1}}),
                Example("Percentages, validated to sum to 100.", {"mode": "allocate", "total": "1250.75", "percentages": [50, 30, 20], "labels": ["rent", "food", "savings"]}),
                Example("The same split with the remainder forced onto the first part instead.", {"mode": "allocate", "total": 100, "parts": 3, "method": "first"}),
            ),
            failures=(
                Example("Percentages that do not add to 100.", {"mode": "allocate", "total": 100, "percentages": [50, 30, 10]}),
                Example("Neither `weights` nor `parts`.", {"mode": "allocate", "total": 100}),
                Example("Labels that do not match the weights.", {"mode": "allocate", "total": 100, "weights": [1, 2, 3], "labels": ["a", "b"]}),
                Example("A negative weight.", {"mode": "allocate", "total": 100, "weights": [3, -1]}),
                Example("Weights that are all zero.", {"mode": "allocate", "total": 100, "weights": [0, 0]}),
                Example("An unknown distribution method.", {"mode": "allocate", "total": 100, "parts": 3, "method": "random"}),
            ),
        ),
        Mode(
            name="sequence",
            purpose="Generate a numeric sequence and its sum.",
            description=(
                "Builds a sequence and returns its terms, count, sum and last term — all as exact "
                "decimals. `kind` selects the family: `arithmetic`, `geometric`, `range`, `fibonacci`, "
                "`primes`, `squares`. Arithmetic sequences take either `n` or an `end`; sequences are "
                "capped at 10 000 terms."
            ),
            params=(
                Param("kind", "string", False, "`arithmetic`, `geometric`, `range`, `fibonacci`, `primes`, `squares`.", "`arithmetic`"),
                Param("start", "number \\| string", False, "First term.", "0 (1 for geometric)"),
                Param("step", "number \\| string", False, "Common difference, for `arithmetic` and `range`.", "1"),
                Param("ratio", "number \\| string", False, "Common ratio, for `geometric`.", "2"),
                Param("end", "number \\| string", False, "Last value, for `arithmetic` and `range`."),
                Param("n", "integer", False, "Number of terms, 1..10000."),
            ),
            examples=(
                Example("An arithmetic sequence by count.", {"mode": "sequence", "kind": "arithmetic", "start": 100, "step": 25, "n": 6}),
                Example("A range defined by its endpoints, with a fractional step.", {"mode": "sequence", "kind": "range", "start": "0", "end": "2", "step": "0.5"}),
                Example("Fibonacci, exact.", {"mode": "sequence", "kind": "fibonacci", "n": 12}),
                Example("A geometric sequence — compound growth without float drift.", {"mode": "sequence", "kind": "geometric", "start": "1000", "ratio": "1.08", "n": 5}),
            ),
            failures=(
                Example("An unknown kind.", {"mode": "sequence", "kind": "harmonic", "n": 5}),
                Example("An arithmetic sequence needs `n` or `end`.", {"mode": "sequence", "kind": "arithmetic", "start": 1, "step": 2}),
                Example("A zero step never reaches the end.", {"mode": "sequence", "kind": "arithmetic", "start": 1, "step": 0, "end": 10}),
                Example("The term cap is 10 000.", {"mode": "sequence", "kind": "fibonacci", "n": 20000}),
            ),
        ),
        Mode(
            name="parse",
            purpose="Read a messy human number into a decimal.",
            description=(
                "Turns written numbers into decimals and says how it read them. Currency symbols, "
                "Indian and international digit grouping, magnitude suffixes (`k`, `M`, `bn`, `L`, "
                "`Cr`), trailing percent signs and accounting parentheses are all understood, and each "
                "interpretation lands in `assumptions`. Pass `values` to parse a batch in one call."
            ),
            params=(
                Param("value", "any", False, "One value to parse."),
                Param("values", "any[]", False, "Several values; the result becomes a list."),
            ),
            examples=(
                Example("An Indian crore amount with a currency symbol.", {"mode": "parse", "value": "₹1.2 Cr"}),
                Example("A batch, each with its reading explained.", {"mode": "parse", "values": ["(500)", "12%", "1,23,456.78", "2.5k", "1234,56"]}),
            ),
            failures=(
                Example("Words are not numbers.", {"mode": "parse", "value": "twelve"}),
                Example("Separators that cannot be reconciled.", {"mode": "parse", "value": "1,23.45.6"}),
                Example("An unknown magnitude suffix.", {"mode": "parse", "value": "5 zillion"}),
                Example("`value` is required.", {"mode": "parse"}),
            ),
        ),
        Mode(
            name="to_words",
            purpose="Spell an amount out in words.",
            description=(
                "Writes a number in words, in the international system (thousand / million / billion) "
                "or the Indian one (thousand / lakh / crore). With `currency` the output becomes the "
                "cheque-and-invoice form — “Rupees … only”, with the minor units named — and the "
                "rounding of those minor units is stated in `assumptions`."
            ),
            params=(
                Param("value", "number \\| string", True, "The amount."),
                Param("system", "`international` \\| `indian`", False, "Numbering system.", "`international`"),
                Param("currency", "string", False, "ISO code; switches to the currency phrasing."),
                Param("suffix_only", "boolean", False, "Append “only”, as invoices do.", "`true`"),
            ),
            examples=(
                Example("International grouping.", {"mode": "to_words", "value": 1234567, "system": "international"}),
                Example("The same number in the Indian system.", {"mode": "to_words", "value": 1234567, "system": "indian"}),
                Example("Invoice phrasing with minor units.", {"mode": "to_words", "value": "125430.75", "system": "indian", "currency": "INR"}),
                Example("A negative with a fractional part.", {"mode": "to_words", "value": "-42.5"}),
            ),
            failures=(
                Example("An unknown numbering system.", {"mode": "to_words", "value": 1234, "system": "roman"}),
                Example("An unparseable value.", {"mode": "to_words", "value": "a lot"}),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# text
# --------------------------------------------------------------------------- #

_LOG_A = "2025-08-26 09:00 INFO started\n2025-08-26 09:05 WARN retrying\n2025-08-26 09:06 INFO ready"
_LOG_B = "2025-08-26 09:00 INFO started\n2025-08-26 09:05 ERROR timed out\n2025-08-26 09:06 INFO ready\n2025-08-26 09:07 INFO done"
_CONTACT = (
    "Ping ops@example.com or billing@mailinator.com, docs at https://leftbrain.dev/docs, "
    "invoice ₹1,25,000 due 2025-09-15, GST 19ABCDE1234F1ZX, call +91 98765 43210. #urgent @sayantan"
)

TEXT = ToolDoc(
    name="text",
    tagline="Count, match, diff and reshape text by codepoint",
    intro=(
        "Text operations a tokeniser cannot do. Counts are by Unicode codepoint, so the “how many r "
        "in strawberry” class of question is answered by counting rather than by guessing, and an "
        "emoji that renders as one glyph is reported as the several codepoints and many bytes it "
        "really is. Regex, diffs, natural sorting, dedupe and entity extraction round it out."
    ),
    when=(
        "Counting characters, words, lines, sentences or occurrences of a substring.",
        "Running or testing a regex — including checking that a pattern compiles at all.",
        "Producing an exact diff between two versions of a string.",
        "Sorting strings in natural order (`file2` before `file10`) or removing near-duplicates.",
        "Pulling emails, URLs, money, dates, PAN/GSTIN and other entities out of free text.",
    ),
    fn=text_mod.text,
    related=(
        "[`collections`](/docs/tools/collections) for the same operations over records rather than "
        "strings · [`validate`](/docs/tools/validate) to check the identifiers `extract` finds · "
        "[`encode`](/docs/tools/encode) for hashing and encoding the text itself."
    ),
    modes=(
        Mode(
            name="count",
            purpose="Count characters, words, lines — or one substring.",
            description=(
                "With `what: all` (the default) returns every count at once: characters, characters "
                "without spaces, letters, digits, words, unique words, lines, non-empty lines, "
                "sentences, paragraphs, UTF-8 bytes and a rough token estimate. Ask for one statistic "
                "by name, or use `what: occurrences` with a `substring` to count and locate a specific "
                "string, optionally overlapping."
            ),
            params=(
                Param("text", "string", True, "The text to measure."),
                Param("what", "string", False, "`all`, `occurrences`, or the name of one statistic.", "`all`"),
                Param("substring", "string", False, "The needle, for `what: occurrences`."),
                Param("case_sensitive", "boolean", False, "Match case when counting occurrences.", "`true`"),
                Param("overlapping", "boolean", False, "Count overlapping matches.", "`false`"),
            ),
            examples=(
                Example("How many `r` in strawberry — counted, with positions.", {"mode": "count", "text": "strawberry", "what": "occurrences", "substring": "r"}),
                Example("Codepoints versus bytes: a family emoji is one glyph, seven codepoints and 25 bytes.", {"mode": "count", "text": "Café 👨‍👩‍👧‍👦"}),
                Example("Overlapping matches, which a plain `count()` misses.", {"mode": "count", "text": "aaaa", "what": "occurrences", "substring": "aa", "overlapping": True}),
                Example("Just one statistic.", {"mode": "count", "text": _LOG_A, "what": "lines"}),
            ),
            failures=(
                Example("`text` is required.", {"mode": "count"}),
                Example("An unknown statistic lists the valid ones.", {"mode": "count", "text": "abc", "what": "vowels"}),
                Example("Counting occurrences needs a `substring`.", {"mode": "count", "text": "abc", "what": "occurrences"}),
            ),
        ),
        Mode(
            name="regex_match",
            purpose="Find every match, with positions and groups.",
            description=(
                "Runs a regular expression over the text and returns each match with its span, "
                "positional groups and named groups, plus whether the pattern matched the whole string. "
                "Flags are given as letters: `i`, `m`, `s`, `x`, `u`, `a`. Output is capped by `limit` "
                "and the cap is reported in `warnings`."
            ),
            params=(
                Param("text", "string", True, "The text to search."),
                Param("pattern", "string", True, "A Python regular expression."),
                Param("flags", "string", False, "Any of `imsxua`."),
                Param("limit", "integer", False, "Maximum matches returned.", "1000"),
            ),
            examples=(
                Example("Every four-digit run in a line.", {"mode": "regex_match", "text": "Order 1234 shipped 2025-08-26 to PIN 560001", "pattern": "\\d{4}"}),
                Example("Named groups come back separately.", {"mode": "regex_match", "text": "2025-08-26", "pattern": "(?P<year>\\d{4})-(?P<month>\\d{2})-(?P<day>\\d{2})"}),
                Example("Case-insensitive matching with a flag.", {"mode": "regex_match", "text": "Error: ERROR while erroring", "pattern": "error", "flags": "i"}),
            ),
            failures=(
                Example("A pattern that does not compile, with the position of the problem.", {"mode": "regex_match", "text": "abc", "pattern": "([a-z"}),
                Example("An unknown flag letter.", {"mode": "regex_match", "text": "abc", "pattern": "a", "flags": "z"}),
                Example("`pattern` is required.", {"mode": "regex_match", "text": "abc"}),
            ),
        ),
        Mode(
            name="regex_replace",
            purpose="Substitute matches, counting what changed.",
            description=(
                "Replaces matches and reports how many substitutions were made and whether anything "
                "changed at all — the part a blind `sub()` never tells you. Backreferences (`\\1`) and "
                "named references work in the replacement; `count` limits how many are replaced."
            ),
            params=(
                Param("text", "string", True, "The text to transform."),
                Param("pattern", "string", True, "A Python regular expression."),
                Param("replacement", "string", True, "Replacement, with `\\1`-style backreferences."),
                Param("flags", "string", False, "Any of `imsxua`."),
                Param("count", "integer", False, "Replace at most this many; 0 means all.", "0"),
            ),
            examples=(
                Example("Masking digits.", {"mode": "regex_replace", "text": "call 98765 43210 now", "pattern": "\\d", "replacement": "#"}),
                Example("Reordering with backreferences.", {"mode": "regex_replace", "text": "2025-08-26", "pattern": "(\\d{4})-(\\d{2})-(\\d{2})", "replacement": "\\3/\\2/\\1"}),
                Example("Only the first two, and the count proves it.", {"mode": "regex_replace", "text": "a a a a", "pattern": "a", "replacement": "b", "count": 2}),
            ),
            failures=(
                Example("`replacement` is required — an empty string is fine, but it must be given.", {"mode": "regex_replace", "text": "abc", "pattern": "a"}),
                Example("A backreference to a group that does not exist.", {"mode": "regex_replace", "text": "abc", "pattern": "a", "replacement": "\\9"}),
                Example("A pattern that does not compile.", {"mode": "regex_replace", "text": "abc", "pattern": "a(", "replacement": "x"}),
            ),
        ),
        Mode(
            name="diff",
            purpose="An exact diff between two texts.",
            description=(
                "Compares two strings by line, word or character and returns a similarity ratio, the "
                "number of units added and removed, an operation list with both sides and their ranges, "
                "and — for line granularity — a unified diff. Use it instead of asking a model whether "
                "two documents differ."
            ),
            params=(
                Param("a", "string", True, "The original text."),
                Param("b", "string", True, "The changed text."),
                Param("granularity", "`line` \\| `word` \\| `char`", False, "Unit of comparison.", "`line`"),
            ),
            examples=(
                Example("A line diff, with the unified patch included.", {"mode": "diff", "a": _LOG_A, "b": _LOG_B}),
                Example("A word-level diff of a single sentence.", {"mode": "diff", "a": "the quick brown fox", "b": "the quiet brown dog", "granularity": "word"}),
            ),
            failures=(
                Example("An unknown granularity.", {"mode": "diff", "a": "x", "b": "y", "granularity": "sentence"}),
                Example("Both sides are required.", {"mode": "diff", "a": "x"}),
            ),
        ),
        Mode(
            name="sort",
            purpose="Sort strings naturally and case-insensitively.",
            description=(
                "Sorts a list with natural ordering by default, so `file2` comes before `file10`, and "
                "case-insensitively, so `Apple` and `apple` sit together. Mixed types are ordered "
                "deterministically — numbers, then strings, then nulls — and `changed` tells you "
                "whether the input was already sorted."
            ),
            params=(
                Param("items", "any[]", True, "The list to sort."),
                Param("key", "string", False, "Field to sort on, when the items are objects."),
                Param("order", "`asc` \\| `desc`", False, "Sort direction.", "`asc`"),
                Param("natural", "boolean", False, "Digit runs compare numerically.", "`true`"),
                Param("case_insensitive", "boolean", False, "Fold case before comparing.", "`true`"),
            ),
            examples=(
                Example("Natural ordering: `file2` before `file10`.", {"mode": "sort", "items": ["file10.txt", "file2.txt", "File1.txt", "file20.txt"]}),
                Example("Sorting objects by a field, descending.", {"mode": "sort", "items": [{"n": "b", "v": 2}, {"n": "a", "v": 10}, {"n": "c", "v": 7}], "key": "v", "order": "desc"}),
                Example("Turning natural ordering off gives plain lexicographic order.", {"mode": "sort", "items": ["file10.txt", "file2.txt"], "natural": False}),
            ),
            failures=(
                Example("`items` must be a list.", {"mode": "sort", "items": "a,b,c"}),
            ),
            never_fails="",
        ),
        Mode(
            name="dedupe",
            purpose="Remove duplicates and report which ones went.",
            description=(
                "Removes duplicates while preserving order, and returns what it removed and where the "
                "first occurrence was — so a dedupe can be reviewed rather than trusted. Whitespace is "
                "normalised by default (`\" a  b \"` equals `\"a b\"`); `case_insensitive` folds case; "
                "`key` dedupes objects on one field."
            ),
            params=(
                Param("items", "any[]", True, "The list to dedupe."),
                Param("key", "string", False, "Field to compare, when the items are objects."),
                Param("case_insensitive", "boolean", False, "Fold case before comparing.", "`false`"),
                Param("normalize_whitespace", "boolean", False, "Collapse runs of whitespace before comparing.", "`true`"),
            ),
            examples=(
                Example("Case and whitespace variations collapsed, with each duplicate traced back.", {"mode": "dedupe", "items": ["Apple", "  apple ", "APPLE", "banana", "banana"], "case_insensitive": True}),
                Example("Deduping records on one field.", {"mode": "dedupe", "items": [{"id": 1, "n": "a"}, {"id": 2, "n": "b"}, {"id": 1, "n": "c"}], "key": "id"}),
            ),
            failures=(
                Example("`items` must be a list.", {"mode": "dedupe", "items": {"a": 1}}),
            ),
        ),
        Mode(
            name="extract",
            purpose="Pull entities out of free text.",
            description=(
                "Runs a library of regexes over the text and returns what it found, deduplicated and in "
                "order. Kinds: `emails`, `urls`, `phones`, `numbers`, `dates`, `times`, `hashtags`, "
                "`mentions`, `ips`, `money`, `pan`, `gstin`, `uuids`. Extraction is regex-based and "
                "says so — check anything it finds with [`validate`](/docs/tools/validate) before "
                "trusting it."
            ),
            params=(
                Param("text", "string", True, "The text to scan."),
                Param("what", "string \\| string[]", False, "One kind, a list of kinds, or `all`.", "`all`"),
                Param("unique", "boolean", False, "Collapse repeated hits.", "`true`"),
            ),
            examples=(
                Example("A few specific kinds.", {"mode": "extract", "text": _CONTACT, "what": ["emails", "urls", "money"]}),
                Example("Everything the library knows about, in one pass.", {"mode": "extract", "text": _CONTACT}),
            ),
            failures=(
                Example("An unknown kind lists the valid ones.", {"mode": "extract", "text": "abc", "what": "vehicles"}),
                Example("`text` is required.", {"mode": "extract", "what": "emails"}),
            ),
        ),
        Mode(
            name="find",
            purpose="Locate a substring with line numbers and context.",
            description=(
                "Finds every occurrence of a substring and returns its offset, line number and "
                "surrounding context — the grep-shaped answer, rather than a yes/no. Matching is "
                "case-insensitive by default; results stop at 200 hits."
            ),
            params=(
                Param("text", "string", True, "The text to search."),
                Param("substring", "string", True, "The string to find."),
                Param("case_sensitive", "boolean", False, "Match case.", "`false`"),
                Param("context", "integer", False, "Characters of context on each side.", "40"),
            ),
            examples=(
                Example("Case-insensitive search with line numbers.", {"mode": "find", "text": _LOG_B, "substring": "info", "context": 12}),
                Example("The same search, case-sensitive, finds fewer.", {"mode": "find", "text": _LOG_B, "substring": "info", "case_sensitive": True}),
            ),
            failures=(
                Example("`substring` is required.", {"mode": "find", "text": "abc"}),
                Example("`text` is required.", {"mode": "find", "substring": "abc"}),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# collections
# --------------------------------------------------------------------------- #

_ORDERS = [
    {"id": "A-1", "region": "north", "amount": "1200.50", "rep": {"name": "Asha"}},
    {"id": "A-2", "region": "south", "amount": "890.00", "rep": {"name": "Bo"}},
    {"id": "A-3", "region": "north", "amount": "430.25", "rep": {"name": "Asha"}},
    {"id": "A-4", "region": "east", "amount": "2100.00", "rep": {"name": "Chen"}},
    {"id": "A-5", "region": "south", "amount": "75.75", "rep": {"name": "Bo"}},
]

COLLECTIONS = ToolDoc(
    name="collections",
    tagline="Set logic, grouping and reshaping over lists and records",
    intro=(
        "Exact list and record logic — the operations a model starts quietly dropping items from "
        "somewhere past twenty entries. Compare two lists and get both differences named, group "
        "records with real aggregates computed as decimals, sort on several keys, find duplicates, "
        "paginate, chunk, and flatten or rebuild nested JSON. Fields are addressed with dotted paths "
        "(`rep.name`, `items[0].sku`)."
    ),
    when=(
        "“What is in list A but not list B?” — and the reverse, in the same answer.",
        "Grouping records by a field with sum/avg/min/max that must be exact.",
        "Multi-key sorting, deduplication and duplicate hunting over records.",
        "Reshaping JSON: flatten for a spreadsheet, unflatten from a form payload.",
        "Pagination and chunking before handing work to another system.",
    ),
    fn=collections_.collections,
    related=(
        "[`text`](/docs/tools/text) `sort`/`dedupe` for plain strings · "
        "[`numbers`](/docs/tools/numbers) for formatting the aggregates · "
        "[`validate`](/docs/tools/validate) `assert` to check the records themselves."
    ),
    modes=(
        Mode(
            name="set_ops",
            purpose="Compare two lists: union, intersection, differences.",
            description=(
                "Compares two lists and always returns the full picture — `only_in_a`, `only_in_b`, "
                "`in_both`, counts, and whether the two are equal as sets — regardless of which `op` "
                "you asked for. `op` additionally puts one specific result in `result`. Objects are "
                "compared structurally, or on one field via `key`. Duplicates inside a list are "
                "collapsed, and that is stated in `assumptions`."
            ),
            params=(
                Param("a", "any[]", True, "First list."),
                Param("b", "any[]", True, "Second list."),
                Param("op", "`compare` \\| `union` \\| `intersection` \\| `difference` \\| `symmetric_difference`", False, "Which result to highlight.", "`compare`"),
                Param("key", "string", False, "Dotted path to compare on, for objects."),
                Param("case_insensitive", "boolean", False, "Fold case on string comparisons.", "`false`"),
            ),
            examples=(
                Example("Two lists of SKUs: both directions of difference at once.", {"mode": "set_ops", "a": ["A1", "B2", "C3", "D4"], "b": ["B2", "D4", "E5"]}),
                Example("A union, with the duplicate collapse reported.", {"mode": "set_ops", "a": ["x", "y", "y"], "b": ["y", "z"], "op": "union"}),
                Example("Comparing records on one field rather than whole objects.", {"mode": "set_ops", "a": [{"sku": "A1", "qty": 2}, {"sku": "B2", "qty": 1}], "b": [{"sku": "B2", "qty": 9}], "op": "difference", "key": "sku"}),
            ),
            failures=(
                Example("Both sides must be lists.", {"mode": "set_ops", "a": ["x"], "b": "y"}),
                Example("An unknown operation.", {"mode": "set_ops", "a": ["x"], "b": ["y"], "op": "xor"}),
            ),
        ),
        Mode(
            name="group_by",
            purpose="Group records by a field, with aggregates.",
            description=(
                "Groups records on a dotted path and counts each group. Add `agg_field` and `agg` to "
                "compute aggregates per group — `count`, `count_distinct`, `sum`, `avg`, `min`, `max`, "
                "`first`, `last`, `list` — as exact decimals returned as strings, so currency totals do "
                "not drift. Members are included unless `include_items` is false."
            ),
            params=(
                Param("items", "object[]", True, "The records."),
                Param("key", "string", True, "Dotted path to group on."),
                Param("agg_field", "string", False, "Dotted path the aggregates are computed over."),
                Param("agg", "string \\| string[]", False, "Which aggregates to compute.", "`[count]`"),
                Param("include_items", "boolean", False, "Include each group's members.", "`true`"),
            ),
            examples=(
                Example("Sales by region, with an exact decimal sum and average.", {"mode": "group_by", "items": _ORDERS, "key": "region", "agg_field": "amount", "agg": ["sum", "avg", "count"], "include_items": False}),
                Example("Grouping on a nested path, keeping the members.", {"mode": "group_by", "items": _ORDERS, "key": "rep.name", "include_items": True}),
            ),
            failures=(
                Example("`key` is required.", {"mode": "group_by", "items": _ORDERS}),
                Example("`items` must be a list.", {"mode": "group_by", "items": {"a": 1}, "key": "a"}),
                Example("An unknown aggregate.", {"mode": "group_by", "items": _ORDERS, "key": "region", "agg_field": "amount", "agg": ["median"]}),
            ),
        ),
        Mode(
            name="aggregate",
            purpose="Aggregate one field across every record.",
            description=(
                "The whole-list version of `group_by`'s aggregates: `count`, `count_distinct`, `sum`, "
                "`avg`, `min`, `max`, `first`, `last`, `list`. Numeric aggregates are computed as "
                "decimals and returned as strings; non-numeric values are ignored for them and that is "
                "stated in `assumptions`. Omit `field` to aggregate the items themselves."
            ),
            params=(
                Param("items", "any[]", True, "The records or values."),
                Param("field", "string", False, "Dotted path to aggregate; omit to use the items themselves."),
                Param("ops", "string \\| string[]", False, "Which aggregates to compute.", "`[count, sum, avg, min, max]`"),
            ),
            examples=(
                Example("Totals across a field.", {"mode": "aggregate", "items": _ORDERS, "field": "amount"}),
                Example("Distinct values of a field.", {"mode": "aggregate", "items": _ORDERS, "field": "region", "ops": ["count", "count_distinct", "list"]}),
            ),
            failures=(
                Example("`items` must be a list.", {"mode": "aggregate", "items": "1,2,3"}),
                Example("An unknown aggregate.", {"mode": "aggregate", "items": _ORDERS, "field": "amount", "ops": ["stdev"]}),
            ),
        ),
        Mode(
            name="pick_fields",
            purpose="Project records down to the fields you need.",
            description=(
                "Builds a narrower record from each input, pulling values with dotted paths. Missing "
                "paths become `null` rather than raising. `rename` maps a path to an output name, and "
                "`short_names` uses the last path segment as the key."
            ),
            params=(
                Param("items", "object[]", True, "The records."),
                Param("fields", "string \\| string[]", True, "Dotted paths to keep."),
                Param("rename", "object", False, "Map of path to output name."),
                Param("short_names", "boolean", False, "Use the last path segment as the key.", "`false`"),
            ),
            examples=(
                Example("Flattening a nested field into a table-shaped row.", {"mode": "pick_fields", "items": _ORDERS, "fields": ["id", "rep.name", "amount"], "short_names": True}),
                Example("Renaming as you project; a missing path becomes null.", {"mode": "pick_fields", "items": _ORDERS, "fields": ["id", "rep.email"], "rename": {"rep.email": "contact"}}),
            ),
            failures=(
                Example("`fields` is required.", {"mode": "pick_fields", "items": _ORDERS}),
                Example("`items` must be a list.", {"mode": "pick_fields", "items": {"id": 1}, "fields": ["id"]}),
            ),
        ),
        Mode(
            name="flatten",
            purpose="Flatten nested JSON — or nested lists.",
            description=(
                "Given an object, produces a single-level map whose keys are dotted paths "
                "(`rep.name`, `tags[0]`) — the shape a CSV or a form encoder wants. Given a list, "
                "flattens nested lists instead. `depth` limits how far it descends; `separator` changes "
                "the joining character."
            ),
            params=(
                Param("data", "object \\| any[]", True, "The structure to flatten."),
                Param("depth", "integer", False, "Maximum levels to descend.", "unlimited"),
                Param("separator", "string", False, "Key separator.", "`.`"),
                Param("flatten_lists", "boolean", False, "Index into lists as well as objects.", "`true`"),
            ),
            examples=(
                Example("A nested object flattened to dotted keys.", {"mode": "flatten", "data": {"order": {"id": "A-1", "rep": {"name": "Asha"}}, "tags": ["rush", "gift"]}}),
                Example("Limiting the depth leaves deeper structures intact.", {"mode": "flatten", "data": {"order": {"id": "A-1", "rep": {"name": "Asha"}}}, "depth": 2}),
                Example("A list of lists, flattened.", {"mode": "flatten", "data": [1, [2, [3, 4]], 5]}),
            ),
            failures=(
                Example("`data` must be a list or an object.", {"mode": "flatten", "data": "a.b.c"}),
                Example("`data` is required.", {"mode": "flatten"}),
            ),
        ),
        Mode(
            name="unflatten",
            purpose="Rebuild nested JSON from dotted keys.",
            description=(
                "The inverse of `flatten`: turns a map of dotted keys back into nested objects and "
                "arrays. Bracketed indices (`items[0].sku`) create lists, and gaps are filled with "
                "nulls rather than shifting entries."
            ),
            params=(
                Param("data", "object", True, "A flat object with dotted keys."),
                Param("separator", "string", False, "Key separator.", "`.`"),
            ),
            examples=(
                Example("Dotted keys back into a nested object.", {"mode": "unflatten", "data": {"order.id": "A-1", "order.rep.name": "Asha", "order.total": 1200.5}}),
                Example("Bracketed indices rebuild arrays.", {"mode": "unflatten", "data": {"items[0].sku": "A1", "items[1].sku": "B2", "items[1].qty": 3}}),
            ),
            failures=(
                Example("`data` must be a flat object.", {"mode": "unflatten", "data": ["a.b"]}),
                Example("`data` is required.", {"mode": "unflatten"}),
            ),
        ),
        Mode(
            name="paginate",
            purpose="Slice a list into a page, with navigation flags.",
            description=(
                "Returns one page of a list together with everything a caller needs to move around it: "
                "total items, total pages, `has_next`, `has_prev` and the 1-based `range` covered. "
                "A page beyond the end returns an empty slice, not an error — the flags say what "
                "happened."
            ),
            params=(
                Param("items", "any[]", True, "The full list."),
                Param("page", "integer", False, "1-based page number.", "1"),
                Param("per_page", "integer", False, "Items per page.", "20"),
            ),
            examples=(
                Example("The middle page of five items, three to a page.", {"mode": "paginate", "items": _ORDERS, "page": 2, "per_page": 3}),
                Example("A page past the end: empty, and the flags explain it.", {"mode": "paginate", "items": _ORDERS, "page": 9, "per_page": 3}),
            ),
            failures=(
                Example("Page numbers start at 1.", {"mode": "paginate", "items": _ORDERS, "page": 0}),
                Example("`per_page` must be at least 1.", {"mode": "paginate", "items": _ORDERS, "per_page": 0}),
                Example("`items` must be a list.", {"mode": "paginate", "items": "abc"}),
            ),
        ),
        Mode(
            name="find_duplicates",
            purpose="Find repeats and where they are.",
            description=(
                "Reports every value that occurs more than once, with all of its indices and its count "
                "— so a duplicate can be located, not just detected. `key` looks at one field of each "
                "record; `case_insensitive` folds case on strings."
            ),
            params=(
                Param("items", "any[]", True, "The list to inspect."),
                Param("key", "string", False, "Dotted path to compare, for objects."),
                Param("case_insensitive", "boolean", False, "Fold case on string comparisons.", "`false`"),
            ),
            examples=(
                Example("Repeated addresses, matched without regard to case.", {"mode": "find_duplicates", "items": ["a@x.com", "b@x.com", "A@X.com", "c@x.com", "b@x.com"], "case_insensitive": True}),
                Example("Duplicate records on one field.", {"mode": "find_duplicates", "items": _ORDERS, "key": "rep.name"}),
            ),
            failures=(
                Example("`items` must be a list.", {"mode": "find_duplicates", "items": "abc"}),
            ),
        ),
        Mode(
            name="sort_by",
            purpose="Stable multi-key sort over records.",
            description=(
                "Sorts records by several keys at once, each with its own direction: "
                "`keys: [{field: region}, {field: amount, order: desc}]`. The sort is stable, nulls "
                "sort last, numeric strings compare as numbers, and other strings compare "
                "case-insensitively. `changed` says whether the order actually moved."
            ),
            params=(
                Param("items", "object[]", True, "The records."),
                Param("keys", "object[] \\| string[]", False, "Sort keys: `{field, order}` or bare field names."),
                Param("key", "string", False, "A single sort field, as a shorthand for `keys`."),
                Param("order", "`asc` \\| `desc`", False, "Direction for the `key` shorthand.", "`asc`"),
            ),
            examples=(
                Example("Region ascending, then amount descending within each region.", {"mode": "sort_by", "items": _ORDERS, "keys": [{"field": "region"}, {"field": "amount", "order": "desc"}]}),
                Example("The single-key shorthand.", {"mode": "sort_by", "items": _ORDERS, "key": "amount", "order": "desc"}),
            ),
            failures=(
                Example("Sort keys are required.", {"mode": "sort_by", "items": _ORDERS}),
                Example("`items` must be a list.", {"mode": "sort_by", "items": "abc", "key": "id"}),
            ),
        ),
        Mode(
            name="chunk",
            purpose="Split a list into batches.",
            description=(
                "Splits a list either into chunks of a fixed `size` — the last one may be shorter — or "
                "into exactly `n` chunks whose sizes differ by at most one. Returns the chunks and "
                "their sizes, which is what a batching loop actually needs."
            ),
            params=(
                Param("items", "any[]", True, "The list to split."),
                Param("size", "integer", False, "Maximum items per chunk."),
                Param("n", "integer", False, "Number of chunks; sizes differ by at most 1."),
            ),
            examples=(
                Example("Fixed-size batches.", {"mode": "chunk", "items": [1, 2, 3, 4, 5, 6, 7], "size": 3}),
                Example("A fixed number of near-equal chunks.", {"mode": "chunk", "items": [1, 2, 3, 4, 5, 6, 7], "n": 3}),
            ),
            failures=(
                Example("One of `size` or `n` is required.", {"mode": "chunk", "items": [1, 2, 3]}),
                Example("`items` must be a list.", {"mode": "chunk", "items": "abc", "size": 2}),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# validate
# --------------------------------------------------------------------------- #

_LEAVE_DOC = {
    "employee": {"id": "E-19", "email": "asha@example.com"},
    "leave": {"type": "casual", "days": 3, "start": "2025-09-01"},
    "approvals": ["manager"],
}
_LEAVE_SCHEMA = {
    "type": "object",
    "required": ["employee", "leave"],
    "properties": {
        "employee": {
            "type": "object",
            "required": ["id", "email"],
            "properties": {"id": {"type": "string"}, "email": {"type": "string", "format": "email"}},
        },
        "leave": {
            "type": "object",
            "required": ["type", "days"],
            "properties": {
                "type": {"enum": ["casual", "sick", "earned"]},
                "days": {"type": "integer", "minimum": 1, "maximum": 2},
            },
        },
    },
}

VALIDATE = ToolDoc(
    name="validate",
    tagline="Check instead of judge",
    intro=(
        "The objective replacement for an LLM judge. `assert` evaluates a list of "
        "`{path, op, value}` rules over a JSON document and returns pass/fail per rule, the actual "
        "value that was seen, and a weighted score — reproducible, explainable and free. Around it "
        "sit JSON Schema validation, checksum-verified identifiers (card, IBAN, GSTIN, PAN, Aadhaar, "
        "ISBN, EAN, VIN…), email/URL/phone/IP syntax, and a SQL parser that flags writes before you "
        "run them."
    ),
    when=(
        "Scoring a document or an agent's output against explicit rules, instead of asking a model.",
        "Validating a payload against a JSON Schema before sending it on.",
        "Verifying an identifier by its checksum — not by whether it looks plausible.",
        "Checking an email, URL, phone number or IP without a network round trip.",
        "Inspecting SQL before execution: what it writes, which tables, whether it has a WHERE.",
    ),
    fn=validate_mod.validate,
    related=(
        "[`text`](/docs/tools/text) `extract` to find identifiers before checking them · "
        "[`collections`](/docs/tools/collections) to reshape a document into the paths your rules "
        "expect · [`encode`](/docs/tools/encode) `jwt_decode` for token claims."
    ),
    modes=(
        Mode(
            name="json_schema",
            purpose="Validate a document against a JSON Schema.",
            description=(
                "Validates `data` against `schema` using the draft the schema declares, with format "
                "checking on. Every violation comes back with its JSON path, the validator that "
                "failed and the message — a failing document is a successful call (`ok: true`, "
                "`valid: false`), because “the document is invalid” is an answer, not an error. A "
                "schema that is itself malformed *is* an error."
            ),
            params=(
                Param("schema", "object", True, "The JSON Schema."),
                Param("data", "any", False, "The document to validate."),
            ),
            examples=(
                Example("A document that fails two constraints, each located by path.", {"mode": "json_schema", "schema": _LEAVE_SCHEMA, "data": _LEAVE_DOC}),
                Example("The same schema against a document that passes.", {"mode": "json_schema", "schema": _LEAVE_SCHEMA, "data": {"employee": {"id": "E-19", "email": "asha@example.com"}, "leave": {"type": "sick", "days": 2}}}),
            ),
            failures=(
                Example("`schema` is required.", {"mode": "json_schema", "data": {"a": 1}}),
                Example("A schema that is not a valid schema.", {"mode": "json_schema", "schema": {"type": "nonsense"}, "data": {}}),
            ),
        ),
        Mode(
            name="assert",
            purpose="Score a document against explicit rules.",
            description=(
                "Evaluates rules of the form `{path, op, value}` over a document. `path` is a dotted "
                "path (`leave.days`, `items[0].sku`), `op` is one of `eq`, `ne`, `gt`, `gte`, `lt`, "
                "`lte`, `between`, `in`, `not_in`, `contains`, `contains_all`, `contains_any`, "
                "`starts_with`, `ends_with`, `matches`, `type`, `exists`, `missing`, `empty`, "
                "`not_empty`, `len_eq`/`len_gt`/`len_lt`/`len_between`, `before`, `after`, "
                "`on_or_after`, `is_email`, `is_url`, `is_date`, `is_uuid`, `unique`, `sum_eq`, "
                "`each`. Numbers compare numerically even when they arrive as strings, and dates "
                "compare as dates. Each rule may carry an `id`, a `message` shown when it fails and a "
                "`weight`; the response gives per-rule results, the failures on their own, and a "
                "weighted `score`."
            ),
            params=(
                Param("data", "any", False, "The document the rules are evaluated against."),
                Param("rules", "object[] \\| object", True, "The rules: `{path, op, value, id?, message?, weight?}`."),
            ),
            examples=(
                Example("A policy check that fails one rule, with a score and a human message.", {"mode": "assert", "data": _LEAVE_DOC, "rules": [{"id": "days", "path": "leave.days", "op": "lte", "value": 2, "message": "casual leave is capped at 2 days", "weight": 3}, {"id": "type", "path": "leave.type", "op": "in", "value": ["casual", "sick", "earned"]}, {"id": "email", "path": "employee.email", "op": "is_email"}, {"id": "start", "path": "leave.start", "op": "after", "value": "2025-08-31"}]}),
                Example("String numbers compared as numbers, and a list checked for uniqueness.", {"mode": "assert", "data": {"total": "1200.50", "skus": ["A1", "B2", "A1"]}, "rules": [{"path": "total", "op": "gt", "value": 1000}, {"path": "skus", "op": "unique"}, {"path": "skus", "op": "len_eq", "value": 3}]}),
                Example("`each` applies a sub-rule to every element of a list.", {"mode": "assert", "data": {"lines": [{"qty": 2}, {"qty": 0}]}, "rules": [{"path": "lines", "op": "each", "value": {"path": "qty", "op": "gt", "value": 0}}]}),
            ),
            failures=(
                Example("`rules` is required and must be non-empty.", {"mode": "assert", "data": _LEAVE_DOC}),
                Example("An unknown operator.", {"mode": "assert", "data": {"a": 1}, "rules": [{"path": "a", "op": "frobnicate", "value": 1}]}),
                Example("`between` needs a two-element range.", {"mode": "assert", "data": {"a": 1}, "rules": [{"path": "a", "op": "between", "value": 5}]}),
                Example("An unknown type name.", {"mode": "assert", "data": {"a": 1}, "rules": [{"path": "a", "op": "type", "value": "decimal"}]}),
            ),
        ),
        Mode(
            name="id",
            purpose="Verify an identifier by its checksum.",
            description=(
                "Checks an identifier against its real check-digit algorithm, not a regex that “looks "
                "right”. `kind` picks the scheme: `card`/`luhn`, `iban` (mod-97 plus country length), "
                "`gstin`, `pan`, `aadhaar` (Verhoeff), `isbn`, `ean`/`upc`/`gtin`, `ifsc`, `vin`, "
                "`uuid`, `upi`, `pincode`, `ssn`, `verhoeff`, `mod97`. An identifier that fails its "
                "checksum is a successful call reporting `valid: false` — only an unknown `kind` or a "
                "missing value is an error. Card numbers come back masked, never echoed in full."
            ),
            params=(
                Param("kind", "string", True, "Which scheme to check against."),
                Param("value", "string", True, "The identifier."),
            ),
            examples=(
                Example("A card number that passes Luhn, with its brand detected and the value masked.", {"mode": "id", "kind": "card", "value": "4111 1111 1111 1111"}),
                Example("One digit changed: the same call, `valid: false`.", {"mode": "id", "kind": "card", "value": "4111 1111 1111 1112"}),
                Example("An IBAN, checked by mod-97 and by its country's expected length.", {"mode": "id", "kind": "iban", "value": "GB82 WEST 1234 5698 7654 32"}),
                Example("A GSTIN, whose check character is recomputed and whose embedded PAN is returned.", {"mode": "id", "kind": "gstin", "value": "19ABCDE1234F1ZX"}),
                Example("A PAN, with the holder type decoded from its fourth character.", {"mode": "id", "kind": "pan", "value": "ABCDE1234F"}),
                Example("An Aadhaar number verified by the Verhoeff algorithm and returned masked.", {"mode": "id", "kind": "aadhaar", "value": "2345 6789 0124"}),
            ),
            failures=(
                Example("An unknown scheme lists the supported ones.", {"mode": "id", "kind": "passport", "value": "X1234567"}),
                Example("`value` is required.", {"mode": "id", "kind": "card"}),
            ),
        ),
        Mode(
            name="email",
            purpose="Check an address's syntax and shape.",
            description=(
                "Validates the syntax of an email address, splits it into local part and domain, "
                "normalises the domain to lower case, checks the 64-character local-part limit and "
                "flags known disposable domains. This is a syntax check only — deliverability is not "
                "tested, and the response says so. An invalid address is reported as `valid: false`; "
                "this mode never returns `ok: false`."
            ),
            params=(
                Param("value", "string", True, "The address to check."),
            ),
            examples=(
                Example("A normal address, normalised.", {"mode": "email", "value": "Asha.Roy@Example.COM"}),
                Example("A disposable domain, flagged but still syntactically valid.", {"mode": "email", "value": "throwaway@mailinator.com"}),
                Example("A malformed address: a successful call with `valid: false` and a reason.", {"mode": "email", "value": "asha@@example..com"}),
            ),
            failures=(),
            never_fails=(
                "Nothing makes this mode return `ok: false`. A missing or malformed address is "
                "reported as `valid: false` with a `reason`, because “this address is invalid” is the "
                "answer you asked for."
            ),
        ),
        Mode(
            name="url",
            purpose="Parse and check a URL.",
            description=(
                "Parses a URL into scheme, host, port, path, query and fragment, notes whether the host "
                "is an IP literal, extracts the TLD and reports whether the scheme is secure. Accepted "
                "schemes are `http`, `https`, `ftp`, `ftps`, `mailto`, `tel` and `file`. Like `email`, "
                "this is syntax only and never returns `ok: false`."
            ),
            params=(
                Param("value", "string", True, "The URL to check."),
            ),
            examples=(
                Example("A full URL, decomposed.", {"mode": "url", "value": "https://leftbrain.dev:8443/docs/tools?q=math#eval"}),
                Example("A missing scheme is reported, not repaired.", {"mode": "url", "value": "leftbrain.dev/docs"}),
                Example("An IP literal host is detected as one.", {"mode": "url", "value": "http://192.168.1.10:8080/health"}),
            ),
            failures=(),
            never_fails=(
                "Nothing makes this mode return `ok: false`. An unusable URL comes back as "
                "`valid: false` with a `reason`."
            ),
        ),
        Mode(
            name="phone",
            purpose="Check a phone number against a country's format.",
            description=(
                "Validates a phone number and normalises it to E.164. A number starting with `+` "
                "(or `00`) is checked against the country code it carries and the region is guessed "
                "back; a national number needs a `region` so the tool knows which rules to apply — "
                "asking for one rather than assuming the caller's. Trunk prefixes are stripped and "
                "that is reported. A number that does not fit the pattern is `valid: false`, not an "
                "error; a missing or unsupported `region` is."
            ),
            params=(
                Param("value", "string", True, "The number, in any punctuation."),
                Param("region", "string", False, "ISO country code, required for national numbers."),
            ),
            examples=(
                Example("An E.164 number: the region is inferred, not asked for.", {"mode": "phone", "value": "+91 98765 43210"}),
                Example("A national number with an explicit region and a trunk prefix.", {"mode": "phone", "value": "098765 43210", "region": "IN"}),
                Example("A number that does not match its region's pattern.", {"mode": "phone", "value": "12345 67890", "region": "IN"}),
            ),
            failures=(
                Example("A national number with no region: the tool asks rather than assuming a country.", {"mode": "phone", "value": "9876543210"}),
                Example("An unsupported region.", {"mode": "phone", "value": "9876543210", "region": "ZZ"}),
            ),
        ),
        Mode(
            name="ip",
            purpose="Parse an IP address or CIDR network.",
            description=(
                "Parses an IPv4 or IPv6 address and reports whether it is private, loopback, "
                "multicast, globally routable or reserved, along with its compressed and exploded "
                "forms. A value containing `/` is read as a network and returns its size and first and "
                "last addresses. Never returns `ok: false`."
            ),
            params=(
                Param("value", "string", True, "An IP address or CIDR network."),
            ),
            examples=(
                Example("A private IPv4 address.", {"mode": "ip", "value": "192.168.1.10"}),
                Example("A CIDR network, with its size and bounds.", {"mode": "ip", "value": "10.0.0.0/24"}),
                Example("IPv6, compressed and exploded.", {"mode": "ip", "value": "2001:db8::1"}),
                Example("Something that is not an address at all.", {"mode": "ip", "value": "300.1.1.1"}),
            ),
            failures=(),
            never_fails=(
                "Nothing makes this mode return `ok: false`. An unparseable value comes back as "
                "`valid: false` with the parser's reason."
            ),
        ),
        Mode(
            name="sql_parse",
            purpose="Inspect SQL before running it.",
            description=(
                "Parses SQL with sqlglot and reports, per statement: the statement type, whether it "
                "writes, which tables it reads and writes, the columns referenced, whether it has a "
                "`WHERE` and a `LIMIT`, and a normalised form. `UPDATE`/`DELETE` without a `WHERE` and "
                "any `DROP`/`TRUNCATE` are raised in `warnings`. Syntactically invalid SQL is a "
                "successful call reporting `valid: false` — that is the answer you wanted."
            ),
            params=(
                Param("sql", "string", True, "One or more SQL statements."),
                Param("dialect", "string", False, "sqlglot dialect, e.g. `postgres`, `mysql`, `snowflake`.", "generic"),
            ),
            examples=(
                Example("A read-only query: tables, columns and `read_only: true`.", {"mode": "sql_parse", "sql": "SELECT o.id, c.name FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.total > 100 LIMIT 50"}),
                Example("An unbounded DELETE, flagged in `warnings` before it runs.", {"mode": "sql_parse", "sql": "DELETE FROM sessions"}),
                Example("Invalid SQL: a successful call that says the SQL is invalid.", {"mode": "sql_parse", "sql": "SELCT * FROM t WHERE"}),
            ),
            failures=(
                Example("`sql` is required.", {"mode": "sql_parse"}),
                Example("An unknown dialect.", {"mode": "sql_parse", "sql": "SELECT 1", "dialect": "klingon"}),
            ),
        ),
        Mode(
            name="regex",
            purpose="Check that a pattern compiles.",
            description=(
                "Compiles a regular expression and reports its group count and named groups, or why it "
                "failed and at which character. Use it to validate a user-supplied pattern before "
                "handing it to [`text`](/docs/tools/text). A pattern that does not compile is "
                "`valid: false`, not an error."
            ),
            params=(
                Param("pattern", "string", True, "The regular expression to compile."),
            ),
            examples=(
                Example("A valid pattern with named groups.", {"mode": "regex", "pattern": "(?P<year>\\d{4})-(?P<month>\\d{2})"}),
                Example("An invalid pattern, with the failure position.", {"mode": "regex", "pattern": "([a-z"}),
            ),
            failures=(
                Example("`pattern` is required.", {"mode": "regex"}),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# random
# --------------------------------------------------------------------------- #

RANDOM = ToolDoc(
    name="random",
    tagline="Real randomness, seeded when you want it back",
    intro=(
        "A language model cannot produce randomness; asked for a “random” number it returns the same "
        "few. This tool draws from the operating system's entropy — or, when you pass a `seed`, from "
        "a deterministic generator, so the same seed always yields the same draw and a test can "
        "reproduce it. Tokens, passwords and UUIDs are always drawn from `secrets` and ignore the "
        "seed on purpose; that is stated in every response."
    ),
    when=(
        "UUIDs, tokens, passwords and OTPs that must be unguessable.",
        "Sampling, shuffling, picking a winner or assigning A/B groups.",
        "Anything that must be reproducible later: pass a `seed` and record it.",
        "Never for cryptographic keys you intend to keep — generate those where they will live.",
    ),
    fn=random_.random_tool,
    related=(
        "[`encode`](/docs/tools/encode) to hash or encode what you generate · "
        "[`collections`](/docs/tools/collections) `chunk` for deterministic batching, when you want "
        "splitting without randomness."
    ),
    modes=(
        Mode(
            name="uuid",
            purpose="Generate UUIDs, v4 or time-ordered v7.",
            description=(
                "Generates version 4 (fully random) or version 7 (time-ordered, so they sort by "
                "creation and index well as a primary key) UUIDs. `format` returns them canonical, "
                "bare hex or upper case. UUIDs are always drawn from the system entropy source and "
                "are never affected by `seed`."
            ),
            params=(
                Param("n", "integer", False, "How many to generate, 1..10000.", "1"),
                Param("version", "`4` \\| `7`", False, "4 is random; 7 is time-ordered.", "4"),
                Param("format", "`canonical` \\| `hex` \\| `upper`", False, "Output form.", "`canonical`"),
            ),
            examples=(
                Example("One v4 UUID.", {"mode": "uuid"}, volatile=True),
                Example("Three time-ordered v7 UUIDs in bare hex — note the shared prefix.", {"mode": "uuid", "version": 7, "n": 3, "format": "hex"}, volatile=True),
            ),
            failures=(
                Example("Only versions 4 and 7 are offered.", {"mode": "uuid", "version": 5}),
                Example("`n` must be at least 1.", {"mode": "uuid", "n": 0}),
            ),
        ),
        Mode(
            name="int",
            purpose="Random integers in an inclusive range.",
            description=(
                "Draws integers between `min` and `max`, inclusive at both ends — stated in "
                "`assumptions`, because half-open ranges are the usual bug. `unique: true` draws "
                "without replacement. With a `seed` the draw is reproducible and the response says so; "
                "without one it comes from system entropy."
            ),
            params=(
                Param("min", "integer", False, "Lower bound, inclusive.", "1"),
                Param("max", "integer", False, "Upper bound, inclusive.", "100"),
                Param("n", "integer", False, "How many to draw, 1..10000.", "1"),
                Param("unique", "boolean", False, "Draw without replacement.", "`false`"),
                Param("seed", "string \\| integer", False, "Makes the draw reproducible."),
            ),
            examples=(
                Example("Five dice rolls, seeded — this exact list comes back every time.", {"mode": "int", "min": 1, "max": 6, "n": 5, "seed": "demo"}),
                Example("Six distinct numbers from 1 to 49, seeded.", {"mode": "int", "min": 1, "max": 49, "n": 6, "unique": True, "seed": "lotto-2025"}),
                Example("Unseeded: a fresh draw from system entropy every call.", {"mode": "int", "min": 1, "max": 100}, volatile=True),
            ),
            failures=(
                Example("The range must not be inverted.", {"mode": "int", "min": 10, "max": 1}),
                Example("Too few values in the range to draw that many distinct ones.", {"mode": "int", "min": 1, "max": 3, "n": 5, "unique": True}),
                Example("`n` is capped at 10 000.", {"mode": "int", "min": 1, "max": 10, "n": 999999}),
            ),
        ),
        Mode(
            name="float",
            purpose="Random floats in a range.",
            description=(
                "Draws uniform floats between `min` and `max`, optionally rounded to `decimals`. Seed "
                "it for reproducibility. For money, draw with `decimals` set rather than rounding "
                "afterwards."
            ),
            params=(
                Param("min", "number", False, "Lower bound.", "0.0"),
                Param("max", "number", False, "Upper bound.", "1.0"),
                Param("n", "integer", False, "How many to draw, 1..10000.", "1"),
                Param("decimals", "integer", False, "Round each value to this many places."),
                Param("seed", "string \\| integer", False, "Makes the draw reproducible."),
            ),
            examples=(
                Example("Four seeded prices, rounded as they are drawn.", {"mode": "float", "min": 10, "max": 20, "n": 4, "decimals": 2, "seed": "demo"}),
                Example("A single seeded value, unrounded.", {"mode": "float", "seed": "demo"}),
            ),
            failures=(
                Example("The range must not be inverted.", {"mode": "float", "min": 5, "max": 1}),
                Example("`n` must be at least 1.", {"mode": "float", "n": 0}),
            ),
        ),
        Mode(
            name="pick",
            purpose="Choose from a list, with or without weights.",
            description=(
                "Picks `n` items from a list. By default picks are unique (without replacement); set "
                "`unique: false` to allow repeats. `weights` makes the draw proportional — and weighted "
                "unique draws remove each chosen item and its weight before the next pick. Both the "
                "weighting and the replacement rule are reported in `assumptions`."
            ),
            params=(
                Param("items", "any[]", True, "The pool to pick from."),
                Param("n", "integer", False, "How many to pick.", "1"),
                Param("unique", "boolean", False, "Pick without replacement.", "`true`"),
                Param("weights", "number[]", False, "Relative weights, one per item."),
                Param("seed", "string \\| integer", False, "Makes the pick reproducible."),
            ),
            examples=(
                Example("Two winners from a list, seeded.", {"mode": "pick", "items": ["asha", "bo", "chen", "dev", "eve"], "n": 2, "seed": "raffle-1"}),
                Example("A weighted draw with replacement.", {"mode": "pick", "items": ["gold", "silver", "bronze"], "weights": [1, 3, 6], "n": 5, "unique": False, "seed": "loot"}),
            ),
            failures=(
                Example("The pool must be a non-empty list.", {"mode": "pick", "items": []}),
                Example("Weights must line up with items.", {"mode": "pick", "items": ["a", "b", "c"], "weights": [1, 2]}),
                Example("You cannot draw more unique items than exist.", {"mode": "pick", "items": ["a", "b"], "n": 5}),
            ),
        ),
        Mode(
            name="shuffle",
            purpose="Shuffle a list, reproducibly if seeded.",
            description=(
                "Returns a shuffled copy of the list; the input is not modified. With the same `seed` "
                "you get the same order every time, which is what makes a randomised experiment "
                "auditable."
            ),
            params=(
                Param("items", "any[]", True, "The list to shuffle."),
                Param("seed", "string \\| integer", False, "Makes the shuffle reproducible."),
            ),
            examples=(
                Example("A seeded shuffle.", {"mode": "shuffle", "items": ["a", "b", "c", "d", "e"], "seed": "demo"}),
                Example("A different seed, a different order — from the same input.", {"mode": "shuffle", "items": ["a", "b", "c", "d", "e"], "seed": "other"}),
            ),
            failures=(
                Example("`items` must be a list.", {"mode": "shuffle", "items": "abcde"}),
            ),
        ),
        Mode(
            name="token",
            purpose="Cryptographically secure tokens, passwords and OTPs.",
            description=(
                "Generates secure random strings from the OS entropy source. `kind` picks the "
                "alphabet: `urlsafe`, `hex`, `alnum`, `alpha`, `digits`, `upper`, `lower`, `password` "
                "(mixed classes, guaranteed to contain all four), `readable` (no look-alike "
                "characters), `bytes` (hex of that many bytes) or `otp`. Seeding is deliberately "
                "ignored here — a reproducible secret is not a secret — and every response says so."
            ),
            params=(
                Param("kind", "string", False, "Alphabet or token type.", "`urlsafe`"),
                Param("length", "integer", False, "Characters (or bytes for `kind: bytes`), 1..4096.", "32"),
                Param("n", "integer", False, "How many tokens.", "1"),
            ),
            examples=(
                Example("A URL-safe API token.", {"mode": "token", "kind": "urlsafe", "length": 32}, volatile=True),
                Example("A password with all four character classes guaranteed.", {"mode": "token", "kind": "password", "length": 16}, volatile=True),
                Example("A six-digit OTP.", {"mode": "token", "kind": "otp", "length": 6}, volatile=True),
                Example("Human-readable codes with no confusable characters.", {"mode": "token", "kind": "readable", "length": 8, "n": 3}, volatile=True),
            ),
            failures=(
                Example("An unknown alphabet lists the valid ones.", {"mode": "token", "kind": "runes"}),
                Example("`length` must be between 1 and 4096.", {"mode": "token", "kind": "hex", "length": 0}),
            ),
        ),
        Mode(
            name="bool",
            purpose="Weighted coin flips.",
            description=(
                "Draws booleans that are true with probability `p`. With `n` greater than one the "
                "response includes `true_count`, which is what a simulation actually wants. Seed it "
                "for a reproducible run."
            ),
            params=(
                Param("p", "number", False, "Probability of `true`, 0..1.", "0.5"),
                Param("n", "integer", False, "How many flips, 1..10000.", "1"),
                Param("seed", "string \\| integer", False, "Makes the run reproducible."),
            ),
            examples=(
                Example("Ten seeded flips at 30%, with the count of trues.", {"mode": "bool", "p": 0.3, "n": 10, "seed": "demo"}),
                Example("A single seeded fair flip.", {"mode": "bool", "seed": "demo"}),
            ),
            failures=(
                Example("A probability outside 0..1.", {"mode": "bool", "p": 1.5}),
                Example("`n` must be at least 1.", {"mode": "bool", "n": 0}),
            ),
        ),
        Mode(
            name="sample",
            purpose="Take a sample, or split into A/B groups.",
            description=(
                "With `k`, draws a sample of that size without replacement. With `groups` — a count or "
                "a list of names — shuffles and deals the items round-robin into buckets whose sizes "
                "differ by at most one, which is what an A/B split should do. Seed it and the "
                "assignment is reproducible for anyone auditing the experiment."
            ),
            params=(
                Param("items", "any[]", True, "The population."),
                Param("k", "integer", False, "Sample size, when not splitting into groups.", "1"),
                Param("groups", "integer \\| string[]", False, "Number of groups, or their names."),
                Param("seed", "string \\| integer", False, "Makes the assignment reproducible."),
            ),
            examples=(
                Example("A seeded sample of three.", {"mode": "sample", "items": ["u1", "u2", "u3", "u4", "u5", "u6", "u7"], "k": 3, "seed": "audit-2025"}),
                Example("A named A/B/C split, balanced to within one.", {"mode": "sample", "items": ["u1", "u2", "u3", "u4", "u5", "u6", "u7"], "groups": ["control", "variant_a", "variant_b"], "seed": "exp-42"}),
            ),
            failures=(
                Example("`k` cannot exceed the population.", {"mode": "sample", "items": ["a", "b"], "k": 5}),
                Example("The population must be a non-empty list.", {"mode": "sample", "items": [], "k": 1}),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# geo_offline
# --------------------------------------------------------------------------- #

GEO = ToolDoc(
    name="geo_offline",
    tagline="Time zones, distances and bearings, fully offline",
    intro=(
        "Built on the tzdata tables that ship with Python plus a curated alias list for cities that "
        "are not zone names — Mumbai, Bengaluru, Manchester, Silicon Valley. No network, no API key, "
        "no rate limit. Use it to turn “Mumbai” into `Asia/Kolkata` before any timezone conversion, "
        "and to get great-circle distance and bearing between two points."
    ),
    when=(
        "Resolving a city or country to an IANA zone before calling `datetime convert_tz`.",
        "Finding the zone nearest a pair of coordinates.",
        "Great-circle distance and compass bearing between two places or coordinates.",
        "Listing every zone a country spans — the thing that makes “what time is it in the US?” unanswerable.",
        "Not for driving distance, geocoding or address lookup; this dataset has none of those.",
    ),
    fn=geo_offline.geo_offline,
    related=(
        "[`datetime`](/docs/tools/datetime) `convert_tz` consumes the zone names this returns · "
        "[`convert`](/docs/tools/convert) to restate a distance in other units."
    ),
    modes=(
        Mode(
            name="tz_for_place",
            purpose="The IANA zone for a city or country.",
            description=(
                "Resolves a place name to an IANA timezone and returns that zone's current offset, "
                "abbreviation, DST state, countries, reference coordinates and local time. Exact zone "
                "names, curated city aliases and country names are all accepted. A place that spans "
                "several zones is refused with the candidates listed — pass `all: true` to get them "
                "all instead."
            ),
            params=(
                Param("place", "string", True, "City, country or IANA zone name."),
                Param("all", "boolean", False, "Return every matching zone instead of refusing an ambiguous one.", "`false`"),
            ),
            examples=(
                Example("A city alias resolved to its zone.", {"mode": "tz_for_place", "place": "Mumbai"}, volatile=True),
                Example("An exact zone name passes straight through.", {"mode": "tz_for_place", "place": "Europe/Berlin"}, volatile=True),
                Example("A country that spans several zones, with `all` set.", {"mode": "tz_for_place", "place": "Portugal", "all": True}, volatile=True),
            ),
            failures=(
                Example("A country spanning many zones, without `all`: the candidates come back in `needs.options`.", {"mode": "tz_for_place", "place": "Australia"}),
                Example("A place the dataset does not know.", {"mode": "tz_for_place", "place": "Atlantis"}),
                Example("`place` is required.", {"mode": "tz_for_place"}),
            ),
        ),
        Mode(
            name="tz_for_coords",
            purpose="The zone nearest a pair of coordinates.",
            description=(
                "Finds the timezone whose reference city is closest to the given coordinates, and "
                "returns the two next-closest as alternatives with their distances. This is a "
                "nearest-city heuristic, not a boundary lookup — the response warns as much, and near "
                "a border you should verify with a shapefile-based service."
            ),
            params=(
                Param("lat", "number", True, "Latitude in decimal degrees."),
                Param("lon", "number", True, "Longitude in decimal degrees."),
                Param("point", "object \\| number[] \\| string", False, "Alternative to `lat`/`lon`: `{lat, lon}`, `[lat, lon]` or `\"lat,lon\"`."),
            ),
            examples=(
                Example("Coordinates in eastern India.", {"mode": "tz_for_coords", "lat": 22.5726, "lon": 88.3639}, volatile=True),
                Example("Coordinates in New York.", {"mode": "tz_for_coords", "lat": 40.7128, "lon": -74.006}, volatile=True),
            ),
            failures=(
                Example("Coordinates are required.", {"mode": "tz_for_coords"}),
                Example("Coordinates must be numbers.", {"mode": "tz_for_coords", "lat": "north", "lon": 88.36}),
            ),
        ),
        Mode(
            name="distance",
            purpose="Great-circle distance and bearing between two points.",
            description=(
                "Haversine distance between two points, returned in kilometres, miles, nautical miles "
                "and metres, with the initial bearing in degrees and as a compass point. Endpoints may "
                "be `{lat, lon}`, `[lat, lon]`, `\"lat,lon\"` or a place name — and a place name is "
                "approximated by its timezone's reference city, which the response says out loud. This "
                "is straight-line distance, never driving distance."
            ),
            params=(
                Param("from", "object \\| number[] \\| string", True, "Origin: coordinates or a place name."),
                Param("to", "object \\| number[] \\| string", True, "Destination: coordinates or a place name."),
            ),
            examples=(
                Example("Mumbai to Delhi, by coordinates.", {"mode": "distance", "from": [19.076, 72.8777], "to": [28.6139, 77.209]}),
                Example("Coordinates as strings, Bengaluru to Chennai.", {"mode": "distance", "from": "12.9716,77.5946", "to": "13.0827,80.2707"}),
                Example("Place names, with the approximation stated in `assumptions`.", {"mode": "distance", "from": "Kolkata", "to": "London"}),
            ),
            failures=(
                Example("A place name that spans several zones is not specific enough to be a point.", {"mode": "distance", "from": "Australia", "to": "Kolkata"}),
                Example("An unknown place.", {"mode": "distance", "from": "Atlantis", "to": "Kolkata"}),
                Example("`to` is required.", {"mode": "distance", "from": [19.076, 72.8777]}),
            ),
        ),
        Mode(
            name="country",
            purpose="Every zone a country spans.",
            description=(
                "Resolves a country by ISO code, name or common alias and lists all of its time zones "
                "with their current offsets, plus `single_timezone` — the flag that decides whether "
                "“what time is it there?” has one answer."
            ),
            params=(
                Param("country", "string", True, "ISO code, country name, or a common alias like `UK` or `USA`."),
            ),
            examples=(
                Example("A single-zone country.", {"mode": "country", "country": "IN"}, volatile=True),
                Example("A country resolved by name, spanning two zones.", {"mode": "country", "country": "New Zealand"}, volatile=True),
            ),
            failures=(
                Example("An unknown country.", {"mode": "country", "country": "Freedonia"}),
                Example("`country` is required.", {"mode": "country"}),
            ),
        ),
        Mode(
            name="zone_info",
            purpose="Details of one IANA zone.",
            description=(
                "Returns the current offset, abbreviation, DST state, member countries, reference "
                "coordinates, tzdata comment and current local time for one zone. Only real IANA names "
                "are accepted — abbreviations are refused here as everywhere else."
            ),
            params=(
                Param("zone", "string", True, "An IANA zone name, e.g. `Asia/Kolkata`."),
            ),
            examples=(
                Example("A zone with no daylight saving.", {"mode": "zone_info", "zone": "Asia/Kolkata"}, volatile=True),
                Example("A zone that does observe it.", {"mode": "zone_info", "zone": "America/New_York"}, volatile=True),
            ),
            failures=(
                Example("A zone name that does not exist.", {"mode": "zone_info", "zone": "Asia/Gotham"}),
                Example("An abbreviation is not a zone name.", {"mode": "zone_info", "zone": "IST"}),
                Example("`zone` is required.", {"mode": "zone_info"}),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# encode
# --------------------------------------------------------------------------- #

_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJ1c2VyLTQyIiwibmFtZSI6IkFkYSIsImlhdCI6MTY5OTk5NjQwMCwiZXhwIjoxNzAwMDAwMDAwfQ."
    "c2lnbmF0dXJlLW5vdC12ZXJpZmllZA"
)

ENCODE = ToolDoc(
    name="encode",
    tagline="Hashes, HMACs, checksums and encodings",
    intro=(
        "Models hallucinate hashes with complete confidence — a plausible-looking 64 hex characters "
        "that is not the SHA-256 of anything. This tool computes them. Hashes, HMACs with constant-"
        "time comparison, CRC/Adler checksums, Base64 (standard and URL-safe), hex, URL and HTML "
        "escaping, JWT claim inspection and JSON parse/format all live here."
    ),
    when=(
        "Any hash, HMAC, checksum, Base64 or URL encoding — never write one from memory.",
        "Verifying a webhook signature: pass `expected` and get a constant-time comparison.",
        "Inspecting a JWT's claims without a library (the signature is *not* verified).",
        "Validating, pretty-printing or minifying JSON, with the exact error position when it fails.",
    ),
    fn=encode_mod.encode,
    related=(
        "[`validate`](/docs/tools/validate) for checksum-verified identifiers and JSON Schema · "
        "[`text`](/docs/tools/text) for counting and transforming the text itself · "
        "[`random`](/docs/tools/random) for the secrets you are about to hash."
    ),
    modes=(
        Mode(
            name="hash",
            purpose="Hash text or bytes with a named algorithm.",
            description=(
                "Hashes input with `md5`, `sha1`, `sha224`, `sha256`, `sha384`, `sha512`, `sha3_256`, "
                "`sha3_512`, `blake2b` or `blake2s`, returning both hex and Base64 digests plus the "
                "byte length that was hashed. Input may be text, or raw bytes given as "
                "`bytes_base64`/`bytes_hex`. A non-string input is serialised as compact JSON with "
                "sorted keys, and that choice is reported in `assumptions` — it is what makes the "
                "hash reproducible."
            ),
            params=(
                Param("text", "string \\| any", False, "The input. Non-strings become compact sorted JSON."),
                Param("algo", "string", False, "Digest algorithm.", "`sha256`"),
                Param("encoding", "string", False, "Text encoding before hashing.", "`utf-8`"),
                Param("bytes_base64", "string", False, "Raw bytes as Base64, instead of `text`."),
                Param("bytes_hex", "string", False, "Raw bytes as hex, instead of `text`."),
            ),
            examples=(
                Example("SHA-256 of a string, in hex and Base64.", {"mode": "hash", "text": "hello world"}),
                Example("A different algorithm on the same input.", {"mode": "hash", "text": "hello world", "algo": "md5"}),
                Example("Hashing an object — serialised deterministically, and it says so.", {"mode": "hash", "text": {"b": 2, "a": 1}}),
                Example("Hashing raw bytes given as hex.", {"mode": "hash", "bytes_hex": "deadbeef", "algo": "sha1"}),
            ),
            failures=(
                Example("An unknown algorithm lists the supported ones.", {"mode": "hash", "text": "hello", "algo": "sha999"}),
                Example("Some input is required.", {"mode": "hash", "algo": "sha256"}),
                Example("`bytes_hex` must actually be hex.", {"mode": "hash", "bytes_hex": "zzzz"}),
            ),
        ),
        Mode(
            name="hmac",
            purpose="Keyed digests, with constant-time verification.",
            description=(
                "Computes an HMAC over the input with a secret key. Pass `expected` and the response "
                "adds `matches`, compared in constant time against both the hex and Base64 forms — the "
                "correct way to verify a webhook signature. The key is never echoed back."
            ),
            params=(
                Param("key", "string", True, "The secret key."),
                Param("text", "string \\| any", False, "The message."),
                Param("algo", "string", False, "Digest algorithm.", "`sha256`"),
                Param("expected", "string", False, "A signature to compare against, in hex or Base64."),
                Param("key_base64", "boolean", False, "Decode `key` from Base64 first.", "`false`"),
            ),
            examples=(
                Example("An HMAC-SHA256 signature.", {"mode": "hmac", "key": "s3cret", "text": "payload-1"}),
                Example("Verifying a signature: `matches` is computed in constant time.", {"mode": "hmac", "key": "s3cret", "text": "payload-1", "expected": "874582d507bf2715cab202a7b899745887fba3a1935da6699029a96c6a82e770"}),
                Example("A different digest algorithm.", {"mode": "hmac", "key": "s3cret", "text": "payload-1", "algo": "sha512"}),
            ),
            failures=(
                Example("`key` is required.", {"mode": "hmac", "text": "payload-1"}),
                Example("An unknown algorithm.", {"mode": "hmac", "key": "s3cret", "text": "x", "algo": "sha999"}),
                Example("Some message is required.", {"mode": "hmac", "key": "s3cret"}),
            ),
        ),
        Mode(
            name="checksum",
            purpose="CRC32 and Adler-32 checksums.",
            description=(
                "Computes a non-cryptographic checksum — `crc32` or `adler32` — returning the value as "
                "an unsigned integer and as eight hex digits. For integrity against corruption, not "
                "against tampering; use `hash` or `hmac` for that."
            ),
            params=(
                Param("text", "string \\| any", False, "The input."),
                Param("algo", "`crc32` \\| `adler32`", False, "Checksum algorithm.", "`crc32`"),
                Param("bytes_hex", "string", False, "Raw bytes as hex, instead of `text`."),
            ),
            examples=(
                Example("CRC32 of a string.", {"mode": "checksum", "text": "hello world"}),
                Example("Adler-32 of the same input.", {"mode": "checksum", "text": "hello world", "algo": "adler32"}),
            ),
            failures=(
                Example("Only CRC32 and Adler-32 are checksums here.", {"mode": "checksum", "text": "hello", "algo": "md5"}),
                Example("Some input is required.", {"mode": "checksum", "algo": "crc32"}),
            ),
        ),
        Mode(
            name="base64",
            purpose="Base64 encode and decode, standard or URL-safe.",
            description=(
                "Encodes or decodes Base64. `urlsafe` switches the alphabet to `-_`, and "
                "`strip_padding` removes trailing `=`. Decoding re-adds missing padding, detects the "
                "URL-safe alphabet automatically, and — when the decoded bytes are not valid UTF-8 — "
                "returns hex with a warning rather than mangling them."
            ),
            params=(
                Param("action", "`encode` \\| `decode`", False, "Direction.", "`encode`"),
                Param("text", "string \\| any", True, "The text to encode, or the Base64 to decode."),
                Param("urlsafe", "boolean", False, "Use the `-_` alphabet.", "`false`"),
                Param("strip_padding", "boolean", False, "Drop trailing `=` when encoding.", "`false`"),
            ),
            examples=(
                Example("Encoding.", {"mode": "base64", "action": "encode", "text": "leftbrain ✓"}),
                Example("Decoding the same string back.", {"mode": "base64", "action": "decode", "text": "bGVmdGJyYWluIOKckw=="}),
                Example("URL-safe and unpadded, for a query string.", {"mode": "base64", "action": "encode", "text": "sub?a=1&b=2", "urlsafe": True, "strip_padding": True}),
                Example("Decoding bytes that are not UTF-8: hex, with a warning, instead of mojibake.", {"mode": "base64", "action": "decode", "text": "3q2+7w=="}),
            ),
            failures=(
                Example("An unknown action.", {"mode": "base64", "action": "flip", "text": "abc"}),
                Example("Input that is not valid Base64.", {"mode": "base64", "action": "decode", "text": "a"}),
            ),
        ),
        Mode(
            name="hex",
            purpose="Hex encode and decode.",
            description=(
                "Encodes text to lower-case hex, or decodes hex back to text. Spaces and a `0x` prefix "
                "are tolerated when decoding. Bytes that are not valid UTF-8 come back as Base64 with a "
                "warning rather than as broken text. Note that `decode` is the fallback: any `action` "
                "other than `encode` is treated as a decode rather than rejected."
            ),
            params=(
                Param("action", "`encode` \\| `decode`", False, "Direction.", "`encode`"),
                Param("text", "string \\| any", True, "The text to encode, or the hex to decode."),
            ),
            examples=(
                Example("Encoding.", {"mode": "hex", "action": "encode", "text": "leftbrain"}),
                Example("Decoding, with separators tolerated.", {"mode": "hex", "action": "decode", "text": "6c 65 66 74 62 72 61 69 6e"}),
            ),
            failures=(
                Example("Input that is not hex.", {"mode": "hex", "action": "decode", "text": "zzzz"}),
                Example("An odd number of hex digits.", {"mode": "hex", "action": "decode", "text": "abc"}),
                Example("Some input is required.", {"mode": "hex", "action": "encode"}),
            ),
        ),
        Mode(
            name="url",
            purpose="Percent-encode and decode URL components.",
            description=(
                "Percent-encodes a string for use in a URL. By default `/` is preserved, as it should "
                "be in a path; pass `plus: true` for the `application/x-www-form-urlencoded` form, "
                "where spaces become `+` and `/` is escaped. `safe` overrides which characters survive."
            ),
            params=(
                Param("action", "`encode` \\| `decode`", False, "Direction.", "`encode`"),
                Param("text", "string", True, "The string to encode or decode."),
                Param("plus", "boolean", False, "Form encoding: spaces become `+`.", "`false`"),
                Param("safe", "string", False, "Characters left unescaped.", "`/` for encode"),
            ),
            examples=(
                Example("Path-style encoding: the slash survives.", {"mode": "url", "action": "encode", "text": "reports/Q3 2025/summary&final.pdf"}),
                Example("Form-style encoding of the same string.", {"mode": "url", "action": "encode", "text": "reports/Q3 2025/summary&final.pdf", "plus": True}),
                Example("Decoding.", {"mode": "url", "action": "decode", "text": "q%3Dleft%20brain%26page%3D2"}),
            ),
            failures=(
                Example("An unknown action.", {"mode": "url", "action": "flip", "text": "abc"}),
            ),
        ),
        Mode(
            name="html",
            purpose="Escape and unescape HTML entities.",
            description=(
                "Escapes `&`, `<`, `>` and — unless `quote` is false — quotes, so a string can be "
                "placed in HTML safely. `unescape` reverses it, including named and numeric entities. "
                "This is entity escaping, not sanitisation: it does not make untrusted markup safe to "
                "render as markup."
            ),
            params=(
                Param("action", "`escape` \\| `unescape`", False, "Direction.", "`escape`"),
                Param("text", "string", True, "The string to escape or unescape."),
                Param("quote", "boolean", False, "Also escape quotes.", "`true`"),
            ),
            examples=(
                Example("Escaping markup and quotes.", {"mode": "html", "action": "escape", "text": "<b class=\"x\">Tom & Jerry</b>"}),
                Example("Unescaping entities, named and numeric.", {"mode": "html", "action": "unescape", "text": "caf&eacute; &amp; cr&#232;me"}),
            ),
            failures=(
                Example("An unknown action.", {"mode": "html", "action": "flip", "text": "abc"}),
            ),
        ),
        Mode(
            name="jwt_decode",
            purpose="Read a JWT's header and claims.",
            description=(
                "Splits a JWT, Base64url-decodes the header and payload, and renders `exp`, `iat` and "
                "`nbf` as ISO timestamps with an `expired` flag. The signature is **not** verified — "
                "every response says so in `warnings`, and the claims must be treated as untrusted "
                "input until something else checks the signature."
            ),
            params=(
                Param("token", "string", True, "The JWT."),
            ),
            examples=(
                Example("An expired token: claims decoded, timestamps rendered, signature untouched.", {"mode": "jwt_decode", "token": _JWT}),
            ),
            failures=(
                Example("A JWT has three dot-separated parts.", {"mode": "jwt_decode", "token": "abc"}),
                Example("Three parts, but not Base64url JSON.", {"mode": "jwt_decode", "token": "a.b.c"}),
                Example("`token` is required.", {"mode": "jwt_decode"}),
            ),
        ),
        Mode(
            name="json",
            purpose="Parse, pretty-print or minify JSON.",
            description=(
                "`action: parse` validates a JSON string and, when it fails, returns the message with "
                "the exact line, column and character offset — as a successful call, because “this "
                "JSON is invalid, here” is the answer. `format` and `minify` go the other way, turning "
                "a value into indented or compact text, optionally with sorted keys."
            ),
            params=(
                Param("action", "`parse` \\| `format` \\| `minify`", False, "What to do.", "`parse`"),
                Param("text", "string", False, "The JSON text, for `parse`."),
                Param("data", "any", False, "The value to serialise, for `format` and `minify`."),
                Param("indent", "integer", False, "Indent width, for `format`.", "2"),
                Param("sort_keys", "boolean", False, "Sort object keys on output.", "`false`"),
            ),
            examples=(
                Example("Valid JSON, parsed.", {"mode": "json", "action": "parse", "text": "{\"a\": 1, \"b\": [2, 3]}"}),
                Example("Invalid JSON: the error is located to line and column.", {"mode": "json", "action": "parse", "text": "{\"a\": 1,}"}),
                Example("Pretty-printing with sorted keys.", {"mode": "json", "action": "format", "data": {"b": 2, "a": 1}, "sort_keys": True}),
                Example("Minifying.", {"mode": "json", "action": "minify", "data": {"a": 1, "b": [2, 3]}}),
            ),
            failures=(
                Example("An unknown action.", {"mode": "json", "action": "lint", "text": "{}"}),
            ),
        ),
    ),
)


#: Every tool, in the order the site lists them.
CATALOGUE: tuple[ToolDoc, ...] = (
    MATH,
    DATETIME,
    SCALE,
    CONVERT,
    HOLIDAYS,
    NUMBERS,
    TEXT,
    COLLECTIONS,
    VALIDATE,
    RANDOM,
    GEO,
    ENCODE,
)
