"""Per-tool reference pages, generated from the MCP servers and verified by running them.

Nothing here restates what the code already says. Three sources feed a page:

* **The published input schema** (``leftbrain.mcp_server`` / ``leftbrain.external.mcp_server``)
  gives every parameter's name, JSON type, whether the schema itself requires it, and its
  default. Renaming a parameter in a wrapper renames it in the docs; documenting a parameter
  the wrapper does not accept raises at build time and fails the test suite.
* **The wrapper docstring** gives the authoritative list of modes and, where it is written as
  ``- name (args) - prose``, the one-liner for the mode index.
* **The core module's ``EXAMPLES``** gives the worked calls. Each one is executed while the
  page is built and filed under "Examples" or "Fails when" according to what came back, so a
  fixture never claims an outcome. The generator then probes each mode with nothing but the
  mode, and with each required parameter removed or mistyped, and adds those real responses
  to "Fails when" (deduplicated by error message).

The catalogue below therefore carries only prose: what a tool is for, what each mode does, and
what each parameter means to a human. To add a tool, add a ``ToolDoc``; to add a mode, add a
``Mode`` and a ``mode:`` entry in the wrapper docstring; to add an example, append to the core
module's ``EXAMPLES``. ``tests/test_toolref.py`` fails if any of the three drift apart.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from ..contract import schema_rejection
from ..core import collections_, datetimex, geo_offline, holidays_, mathx, random_
from ..core import color as color_mod
from ..core import convert as convert_mod
from ..core import encode as encode_mod
from ..core import finance as finance_mod
from ..core import numbers as numbers_mod
from ..core import scale as scale_mod
from ..core import text as text_mod
from ..core import validate as validate_mod
from .docs import render_markdown
from .tools_list import TOOLS

#: Responses longer than this are elided in the page (the call still returns them all).
MAX_JSON_LINES = 140

#: At most this many generator-derived failures are added to a mode, after the hand-written ones.
MAX_DERIVED_FAILURES = 2


# --------------------------------------------------------------------------- #
# Catalogue types - prose only; everything factual is read from the server
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Param:
    """What one parameter means to a human.

    Its name is matched against the tool's published schema, which supplies the JSON type and
    the default. ``required`` marks a parameter this *mode* cannot work without: the schema is
    flat across every mode of a tool, so it cannot know that. ``default`` records the value the
    tool falls back to when the schema declares none (wrappers default to ``None`` and let the
    core module choose).
    """

    name: str
    doc: str
    required: bool = False
    default: str = ""


@dataclass(frozen=True)
class Mode:
    """One mode of one tool."""

    name: str
    description: str
    params: tuple[Param, ...] = ()
    #: Overrides the one-liner parsed out of the wrapper docstring, where it says more.
    purpose: str = ""
    #: Set only when no input can make the mode return ``ok: false`` - and say why.
    never_fails: str = ""


@dataclass(frozen=True)
class ToolDoc:
    """One tool: intro, when to use it, and every mode."""

    name: str
    intro: str
    when: tuple[str, ...]
    related: str
    modes: tuple[Mode, ...] = ()
    #: ``mode -> [{"caption", "args", "volatile"?}]``, imported from the core module.
    examples: Mapping[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: For a tool with no modes: its parameters, documented once.
    params: tuple[Param, ...] = ()
    #: Network tools are documented from schema and docstring; their examples are not executed.
    network: bool = False


@dataclass(frozen=True)
class Example:
    """One `tools/call` request, run for real when the page is built."""

    caption: str
    args: dict[str, Any]
    volatile: bool = False
    #: True when the generator derived this call rather than a human writing it.
    derived: bool = False


@dataclass(frozen=True)
class Row:
    """One rendered row of a mode's parameter table."""

    name: str
    type: str
    required: bool
    doc: str
    default: str


# --------------------------------------------------------------------------- #
# The MCP servers are the source of truth
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ToolSpec:
    """What the MCP layer publishes for one tool."""

    name: str
    schema: dict[str, Any]
    doc: str
    fn: Callable[..., Any]
    arg_model: Any


@lru_cache(maxsize=1)
def specs() -> dict[str, ToolSpec]:
    """Every tool both MCP servers publish, keyed by tool name.

    ``server._tool_manager`` is the registry behind the async ``server.list_tools()``; reading
    it directly keeps the docs build synchronous. ``tests/test_toolref.py`` asserts the two
    agree, so an SDK change cannot quietly desynchronise the page from the wire.
    """
    from ..external.mcp_server import server as external_server
    from ..mcp_server import server as core_server

    out: dict[str, ToolSpec] = {}
    for server in (core_server, external_server):
        for tool in server._tool_manager.list_tools():
            out[tool.name] = ToolSpec(
                name=tool.name,
                schema=tool.parameters,
                doc=inspect.cleandoc(tool.description or ""),
                fn=tool.fn,
                arg_model=tool.fn_metadata.arg_model,
            )
    return out


def _type_name(prop: dict[str, Any]) -> str:
    """The JSON type of one schema property, as the docs spell it."""
    if "enum" in prop:
        return " \\| ".join(f"`{v}`" for v in prop["enum"])
    if "anyOf" in prop:
        names = [n for n in (_type_name(x) for x in prop["anyOf"]) if n not in ("null", "any")]
        return " \\| ".join(dict.fromkeys(names)) or "any"
    kind = prop.get("type")
    if kind == "array":
        inner = _type_name(prop.get("items") or {})
        return "array" if inner in ("any", "array") else f"{inner}[]"
    return kind or "any"


def _default_label(prop: dict[str, Any], param: Param) -> str:
    value = prop.get("default")
    if value is not None:
        return f"`{value}`" if isinstance(value, str) else f"`{json.dumps(value)}`"
    return param.default or "—"


def rows(tool: ToolDoc, params: tuple[Param, ...], where: str) -> list[Row]:
    """A parameter table: names and prose from here, everything else from the schema."""
    schema = specs()[tool.name].schema
    props, needed = schema.get("properties", {}), set(schema.get("required", ()))
    out = []
    for param in params:
        prop = props.get(param.name)
        if prop is None:  # a rename in the wrapper, or a typo here
            raise KeyError(f"{where} documents '{param.name}', which the MCP tool does not accept")
        out.append(Row(param.name, _type_name(prop), param.required or param.name in needed, param.doc, _default_label(prop, param)))
    return out


# --------------------------------------------------------------------------- #
# The wrapper docstring is the source of truth for the mode list
# --------------------------------------------------------------------------- #

_IDENT = re.compile(r"[a-z_][a-z0-9_]*")
_MODE_LINE = re.compile(r"^\s*mode:\s*(.*)$")


@dataclass(frozen=True)
class DocModes:
    """The modes a wrapper docstring declares, in the order it declares them."""

    order: tuple[str, ...]
    #: A prose one-liner, for modes written as ``- name (args) - what it does``.
    summary: Mapping[str, str]


def _split_top(text: str, sep: str = "|") -> list[str]:
    """Split on `sep`, ignoring separators nested inside brackets."""
    out, depth, buf = [], 0, []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return [x.strip() for x in out if x.strip()]


def _find_top(text: str, needle: str) -> int:
    depth = 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and text.startswith(needle, i):
            return i
    return -1


@lru_cache(maxsize=32)
def docstring_modes(tool_name: str) -> DocModes:
    """Parse the ``mode: a | b | c`` line and the ``- mode: …`` bullets out of a wrapper docstring."""
    lines = specs()[tool_name].doc.split("\n")
    order: list[str] = []
    summary: dict[str, str] = {}

    def note(name: str, prose: str = "") -> None:
        if name not in order:
            order.append(name)
        if prose:
            summary[name] = prose

    for i, line in enumerate(lines):
        m = _MODE_LINE.match(line)
        if not m:
            continue
        decl, j = m.group(1).strip(), i + 1
        while decl.endswith("|") and j < len(lines):  # the list may wrap onto the next line
            decl, j = decl + " " + lines[j].strip(), j + 1
        for item in _split_top(decl):
            ident = _IDENT.match(item)
            if ident:
                note(ident.group(0))
        break

    for line in lines:
        body = line.strip()
        if not body.startswith("- "):
            continue
        body = body[2:].strip()
        colon, dash = _find_top(body, ":"), _find_top(body, " - ")
        cut = min(x for x in (colon, dash) if x >= 0) if (colon >= 0 or dash >= 0) else -1
        head = body if cut < 0 else body[:cut]
        # only the "name (args) - prose" form carries prose; "name: expr, var" lists arguments
        prose = body[cut + 3 :].strip() if cut >= 0 and cut == dash else ""
        for item in _split_top(head):
            for name in item.split("(")[0].split("/"):
                name = name.strip()
                if _IDENT.fullmatch(name):
                    note(name, prose)
    return DocModes(tuple(order), summary)


def purpose_of(tool: ToolDoc, mode: Mode) -> str:
    """The one-liner for the mode index: the catalogue's where it says more, else the docstring's.

    Docstring bullets are written lowercase for the agent reading them; the index is a table of
    sentences, so the first letter is raised here rather than in the docstring.
    """
    if mode.purpose:
        return mode.purpose
    summary = docstring_modes(tool.name).summary.get(mode.name, "")
    return summary[:1].upper() + summary[1:]


# --------------------------------------------------------------------------- #
# Execution - every response in the page is a real one
# --------------------------------------------------------------------------- #

def _protocol_error(tool_name: str, message: str) -> dict[str, Any]:
    """What a client sees when a call never reaches the tool: an MCP error result, not the contract."""
    return {"isError": True, "content": [{"type": "text", "text": f"Error executing tool {tool_name}: {message}"}]}


def call_tool(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one ``tools/call`` the way the server does: validate against the schema, then dispatch."""
    spec = specs()[tool_name]
    unknown = sorted(set(args) - set(spec.schema.get("properties", {})))
    if unknown:
        return _protocol_error(tool_name, f"unexpected keyword argument(s): {', '.join(unknown)}")
    from pydantic import ValidationError

    try:
        validated = spec.arg_model.model_validate(args)
    except ValidationError as exc:
        # ContractMCPServer answers a schema rejection in the contract; the page must show that.
        return schema_rejection(tool_name, exc.errors())
    result = spec.fn(**validated.model_dump_one_level())
    if isinstance(result, dict):
        result.pop("trace", None)  # a traceback is noise in a docs page
    return result


def run_example(tool: ToolDoc, example: Example) -> dict[str, Any]:
    """Call the real tool with the example's arguments."""
    return call_tool(tool.name, example.args)


def succeeded(response: dict[str, Any]) -> bool:
    return response.get("ok") is True


def _message(response: dict[str, Any]) -> str:
    if "message" in response:
        return str(response["message"])
    return "".join(part.get("text", "") for part in response.get("content", []))


def examples_of(tool: ToolDoc, mode: Mode) -> list[Example]:
    return [
        Example(caption=f["caption"], args=f["args"], volatile=bool(f.get("volatile")))
        for f in tool.examples.get(mode.name, [])
    ]


_WRONG_TYPES: tuple[tuple[Any, str], ...] = (([], "array"), ({}, "object"), ("not-a-number", "string"), (7, "integer"))


def _accepted_types(prop: dict[str, Any]) -> set[str]:
    if "anyOf" in prop:
        return {t for x in prop["anyOf"] for t in _accepted_types(x)}
    kind = prop.get("type")
    if kind == "number":
        return {"number", "integer"}
    return {kind} if kind else {"any"}


def _wrong_typed(prop: dict[str, Any]) -> Any:
    """A value the schema must reject, or ``None`` when the property accepts anything."""
    accepted = _accepted_types(prop)
    if "any" in accepted:
        return None
    return next((value for value, kind in _WRONG_TYPES if kind not in accepted), None)


def derived_failures(tool: ToolDoc, mode: Mode, working: dict[str, Any] | None) -> list[Example]:
    """Calls the generator makes on its own: no arguments, then each required one broken."""
    schema = specs()[tool.name].schema
    props, schema_required = schema.get("properties", {}), list(schema.get("required", ()))
    probes = [Example("Called with nothing but the mode.", {"mode": mode.name}, derived=True)]
    base = dict(working or {})
    for name in schema_required:
        wrong = _wrong_typed(props.get(name, {}))
        if wrong is not None and base:
            probes.append(
                Example(
                    f"`{name}` given a value of the wrong type — the schema rejects the call before the tool runs.",
                    {**base, name: wrong},
                    derived=True,
                )
            )
    for name in dict.fromkeys([p.name for p in mode.params if p.required] + schema_required):
        if name in base and name != "mode":
            probes.append(Example(f"`{name}` left out.", {k: v for k, v in base.items() if k != name}, derived=True))
    return probes


def failures_of(tool: ToolDoc, mode: Mode, ran: list[tuple[Example, dict[str, Any]]]) -> list[tuple[Example, dict[str, Any]]]:
    """Every documented failure: the hand-written ones, then derived ones with a new message."""
    out: list[tuple[Example, dict[str, Any]]] = []
    seen: set[str] = set()
    for example, response in ran:
        if succeeded(response):
            continue
        key = _message(response)
        if key not in seen:
            seen.add(key)
            out.append((example, response))
    working = next((e.args for e, r in ran if succeeded(r)), None)
    added = 0
    for probe in derived_failures(tool, mode, working):
        if added >= MAX_DERIVED_FAILURES:
            break
        response = call_tool(tool.name, probe.args)
        key = _message(response)
        if succeeded(response) or key in seen:
            continue
        seen.add(key)
        out.append((probe, response))
        added += 1
    return out


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _json_block(payload: Any) -> str:
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    lines = text.split("\n")
    if len(lines) > MAX_JSON_LINES:
        hidden = len(lines) - MAX_JSON_LINES
        lines = lines[:MAX_JSON_LINES] + [f"  ... {hidden} more lines (elided here, not in the response)"]
    return "```json\n" + "\n".join(lines) + "\n```"


def _cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _params_table(table: list[Row]) -> list[str]:
    if not table:
        return ["This mode takes no parameters beyond `mode`.", ""]
    out = ["| name | type | required | meaning | default |", "| --- | --- | --- | --- | --- |"]
    for row in table:
        out.append(
            f"| `{_cell(row.name)}` | {_cell(row.type)} | {'yes' if row.required else 'no'} "
            f"| {_cell(row.doc)} | {_cell(row.default)} |"
        )
    out.append("")
    return out


def _example_block(tool: ToolDoc, example: Example, response: dict[str, Any]) -> list[str]:
    """Caption, then the call you send, then the answer it came back with — each one labelled.

    The `:::request` / `:::response` containers are the same ones the hand-written pages use;
    `render_markdown` turns them into the colour-coded blocks (see `docs.py`).
    """
    request = {"name": tool.name, "arguments": example.args}
    out = [
        example.caption,
        "",
        ":::request tools/call",
        _json_block(request),
        ":::",
        "",
        ":::response",
        _json_block(response),
        ":::",
        "",
    ]
    if example.volatile:
        out += ["*Time-dependent: the response above was captured when this page was built.*", ""]
    return out


def _mode_markdown(tool: ToolDoc, mode: Mode) -> list[str]:
    out = [f'<h2 id="{mode.name}">{mode.name}</h2>', "", mode.description, ""]
    out += ["### Parameters", ""]
    out += _params_table(rows(tool, mode.params, f"{tool.name}.{mode.name}"))
    if tool.network:
        out += ["### Examples", "", NETWORK_NOTE, ""]
        return out
    ran = [(example, run_example(tool, example)) for example in examples_of(tool, mode)]
    out += ["### Examples", ""]
    for example, response in ran:
        if succeeded(response):
            out += _example_block(tool, example, response)
    out += ["### Fails when", ""]
    failures = failures_of(tool, mode, ran)
    if failures:
        for example, response in failures:
            out += _example_block(tool, example, response)
    else:
        out += [mode.never_fails or "No input reaches an error path in this mode.", ""]
    return out


CONTRACT_NOTE = (
    '<div class="callout">Every call returns <code>{ok: true, result, assumptions[], warnings[]}</code>, '
    "or <code>{ok: false, error, message, retryable}</code> with an optional <code>needs</code> block when "
    "the input was ambiguous. Read <code>assumptions</code>: it says how an under-specified input was "
    "interpreted. When <code>needs.options</code> is present, pick one and call again. "
    "<code>retryable</code> says whether an identical retry could ever succeed — it is <code>false</code> "
    "for everything except <code>busy</code> and <code>internal</code>.</div>"
)

NETWORK_NOTE = (
    '<div class="callout">Network tool; examples are not executed when this page is built, so none are '
    "shown. The parameters above come from the server's published schema and the modes from the tool's "
    "own description — both are read from the running server, not transcribed.</div>"
)

_NETWORK_LEAD = (
    "A network tool: it reaches the internet, so nothing below is executed when the page is built and no "
    "responses are embedded. The modes and parameters *are* read from the running server: the "
    "table for each mode is its published input schema, and the mode list is the tool's own "
    "description."
)

_PAGE_LEAD = (
    "Each example below shows the `tools/call` request first and the exact response underneath.  "
    "Responses are produced by running the real tool when this page is built, so they cannot drift "
    "from what the server returns.  A failure that never reaches the tool — a missing or mistyped "
    "argument — is rejected against the input schema, and comes back in the same contract envelope "
    "as everything else: `error: \"invalid_input\"`, the offending parameters under `details`, and "
    "`needs.missing` when something required was left out."
)


def tool_markdown(tool: ToolDoc) -> str:
    parts = [f"# {tool.name}", "", tool.intro, "", "## When to use", ""]
    parts += [f"- {line}" for line in tool.when]
    parts += ["", CONTRACT_NOTE, "", _NETWORK_LEAD if tool.network else _PAGE_LEAD, ""]
    if not tool.modes:  # a single-shape tool: no mode index, one parameter table
        parts += ["## Parameters", ""]
        parts += _params_table(rows(tool, tool.params, tool.name))
        return "\n".join(parts + ["## Related tools", "", tool.related, ""])
    parts += ["## Modes", ""]
    parts += ["| mode | what it does |", "| --- | --- |"]
    for mode in tool.modes:
        parts.append(f"| [`{mode.name}`](#{mode.name}) | {_cell(purpose_of(tool, mode))} |")
    parts.append("")
    for mode in tool.modes:
        parts += _mode_markdown(tool, mode)
    parts += ["## Related tools", "", tool.related, ""]
    return "\n".join(parts)


def _index_entry(tool: ToolDoc, description: str) -> list[str]:
    listed = " · ".join(f"[`{m.name}`](/docs/tools/{tool.name}#{m.name})" for m in tool.modes)
    return [
        f'<h2 id="{tool.name}"><a href="/docs/tools/{tool.name}">{tool.name}</a></h2>',
        "",
        description,
        "",
        f"**Modes:** {listed}" if listed else "",
        "",
        f"[Read the {tool.name} reference →](/docs/tools/{tool.name})",
        "",
    ]


def index_markdown() -> str:
    parts = [
        "# Tools",
        "",
        "Fourteen tools, one shape. Every tool takes a `mode` and returns "
        "`{ok, result, assumptions[], warnings[]}` on success, or "
        "`{ok: false, error, message, retryable}` — with a `needs` block — when the input was "
        "ambiguous and guessing would be dangerous.",
        "",
        CONTRACT_NOTE,
        "",
        "Each page below documents every mode: what it does, its parameters, worked examples, and "
        "the inputs that make it fail.",
        "",
    ]
    described = {t.name: t for t in CATALOGUE}
    for name, desc, _modes in TOOLS:
        parts += _index_entry(described[name], desc + ".")
    parts += [
        "## Network tools",
        "",
        "Served from `/external/mcp` instead of `/mcp`. They reach the internet, so their answers are "
        "as-of the moment of the call and their examples are not executed when this page is built.",
        "",
    ]
    for tool in EXTERNAL_CATALOGUE:
        parts += _index_entry(tool, specs()[tool.name].doc.split("\n")[0])
    return "\n".join(parts)


@lru_cache(maxsize=1)
def index_page() -> tuple[str, str]:
    return "Tools", render_markdown(index_markdown())


@lru_cache(maxsize=32)
def tool_page(name: str) -> tuple[str, str] | None:
    tool = by_name(name)
    if tool is None:
        return None
    return tool.name, render_markdown(tool_markdown(tool))


def by_name(name: str) -> ToolDoc | None:
    return next((t for t in CATALOGUE + EXTERNAL_CATALOGUE if t.name == name), None)


def tool_names() -> list[str]:
    return [t.name for t in CATALOGUE + EXTERNAL_CATALOGUE]


# --------------------------------------------------------------------------- #
# math
# --------------------------------------------------------------------------- #

MATH = ToolDoc(
    name="math",
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
    related=(
        "[`numbers`](/docs/tools/numbers) for rounding rules, locale formatting and exact "
        "allocation · [`convert`](/docs/tools/convert) for units · [`scale`](/docs/tools/scale) "
        "for proportional scaling."
    ),
    examples=mathx.EXAMPLES,
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
                Param("expr", "The expression to evaluate.", required=True),
                Param("angle", "Mandatory whenever the expression contains trigonometry."),
                Param("vars", "Values substituted before evaluating, e.g. `{'a': 3}`."),
                Param("precision", "Significant digits in the decimal form.", default="15"),
                Param("timeout", "Seconds before the computation is abandoned.", default="20"),
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
                Param("expr", "The expression to evaluate.", required=True),
                Param("angle", "Mandatory whenever the expression contains trigonometry."),
                Param("vars", "Values substituted before evaluating."),
                Param("precision", "Significant digits used internally.", default="15"),
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
                Param("expr", "The expression to simplify.", required=True),
                Param("angle", "Interpretation of trig arguments.", default="`rad`"),
                Param("precision", "Significant digits in the decimal form.", default="15"),
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
                Param("expr", "The expression to expand.", required=True),
                Param("angle", "Interpretation of trig arguments.", default="`rad`"),
                Param("precision", "Significant digits in the decimal form.", default="15"),
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
                Param("expr", "The expression to factor.", required=True),
                Param("angle", "Interpretation of trig arguments.", default="`rad`"),
                Param("precision", "Significant digits in the decimal form.", default="15"),
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
                Param("equations", "The equations. A single string is accepted.", default="—"),
                Param("expr", "Alternative to `equations` for one equation.", default="—"),
                Param("vars", "Which unknowns to solve for. Required when there are more unknowns than equations."),
                Param("domain", "Assumption applied to every unknown.", default="`complex`"),
                Param("precision", "Significant digits in the decimal forms.", default="15"),
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
                Param("expr", "The expression to differentiate.", required=True),
                Param("var", "The variable. Inferred when the expression has exactly one free symbol."),
                Param("order", "How many times to differentiate.", default="1"),
                Param("at", "Also evaluate the derivative at this point."),
                Param("angle", "Interpretation of trig arguments.", default="`rad`"),
            ),
        ),
        Mode(
            name="integrate",
            purpose="Definite and indefinite integrals.",
            description=(
                "With `lower` and `upper`, computes a definite integral; without them, an indefinite one "
                "(the result carries an explicit `+ C`). If no closed form exists, the definite case "
                "falls back to a numeric value and says so in `warnings`. Passing only one bound is an "
                "error, not a guess."
            ),
            params=(
                Param("expr", "The integrand.", required=True),
                Param("var", "The variable of integration. Inferred when unambiguous."),
                Param("lower", "Lower bound. Required together with `upper`."),
                Param("upper", "Upper bound. Required together with `lower`."),
                Param("precision", "Significant digits in the decimal form.", default="15"),
            ),
        ),
        Mode(
            name="limit",
            purpose="Limits, one-sided or two-sided.",
            description=(
                "Evaluates the limit of `expr` as `var` approaches `point`. Without `side` the limit is "
                "two-sided; when the two sides disagree the response stays `ok` but reports "
                "`exists: false` together with both one-sided limits. `point` accepts `oo` for infinity."
            ),
            params=(
                Param("expr", "The expression.", required=True),
                Param("var", "The variable. Inferred when unambiguous."),
                Param("point", "The point approached; `oo` for infinity.", default="0"),
                Param("side", "One-sided limit.", default="two-sided"),
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
                Param("expr", "The expression to expand.", required=True),
                Param("var", "The variable. Inferred when unambiguous."),
                Param("at", "Point to expand about.", default="0"),
                Param("order", "Order of the expansion.", default="6"),
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
                Param("equation", "The differential equation, e.g. `y'' + y = 0`.", required=True),
                Param("func", "The unknown function, `y` or `y(x)`.", default="`y(x)`"),
                Param("ics", "Initial conditions, e.g. `{'y(0)': 1, \"y'(0)\": 0}`."),
                Param("precision", "Significant digits in the decimal form.", default="15"),
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
                Param("op", "The operation.", default="`det`"),
                Param("A", "The matrix.", required=True),
                Param("B", "Second matrix, for `mul`, `add`, `sub`."),
                Param("b", "Right-hand side, for `op: solve`."),
                Param("n", "Exponent, for `op: pow`.", default="2"),
                Param("precision", "Significant digits in the decimal forms.", default="15"),
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
                Param("op", "The statistic to compute.", default="`describe`"),
                Param("data", "The sample.", required=True),
                Param("y", "Second series, for `corr`, `covariance`, `regress`."),
                Param("weights", "Weights, for `weighted_mean`."),
                Param("percentile", "0..100, for `op: percentile`."),
                Param("value", "The observation, for `op: zscore`."),
                Param("predict", "An x-value to predict, for `op: regress`."),
            ),
        ),
        Mode(
            name="convert_form",
            purpose="Re-present one value: polar, rectangular, fraction, scientific, LaTeX.",
            description=(
                "Takes one expression and returns it in a different representation, chosen with `form`: "
                "`polar`, `rect`, `latex`, `decimal`, `fraction`, `scientific`, `percent`. This is a "
                "presentation change, not a computation — the value is unchanged."
            ),
            params=(
                Param("expr", "The value to re-present.", required=True),
                Param("form", "Target form.", default="`decimal`"),
                Param("significant", "Significant digits, for `form: scientific`.", default="6"),
                Param("tolerance", "Rounding tolerance, for `form: fraction`."),
                Param("precision", "Significant digits in the decimal forms.", default="15"),
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
                Param("expr", "The function to sample.", required=True),
                Param("var", "The variable. Inferred when unambiguous."),
                Param("range", "Start and end of the sampled interval.", default="`[-10, 10]`"),
                Param("n", "Number of samples, 2..10000.", default="50"),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# datetime
# --------------------------------------------------------------------------- #

DATETIME = ToolDoc(
    name="datetime",
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
    related=(
        "[`holidays`](/docs/tools/holidays) for the calendars behind `region` · "
        "[`geo_offline`](/docs/tools/geo_offline) to turn a city name into an IANA zone before "
        "converting · [`numbers`](/docs/tools/numbers) for formatting the durations you get back."
    ),
    examples=datetimex.EXAMPLES,
    modes=(
        Mode(
            name="now",
            description=(
                "Returns the current instant with its ISO string, date, weekday, time, UTC offset, "
                "zone, unix timestamp, DST flag, ISO week and day of year. With no `tz` the answer is "
                "UTC and says so in `assumptions`. Pass a list as `tz` for several zones in one call: "
                "the result is then `utc` plus one full entry per zone under `zones`, each with the "
                "`label` you gave it, if any. This is the only mode whose output changes between "
                "calls; everything else can be pinned by passing explicit dates."
            ),
            params=(
                Param("tz", "IANA zone name, a fixed `UTC+05:30` offset, or `local` — or a list of them, where an entry may be `{\"tz\": ..., \"label\": ...}`.", default="`UTC`"),
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
                Param("value", "The instant to convert. `now` or omitted uses the current instant.", default="`now`"),
                Param("from_tz", "Zone of `value` when it carries no offset."),
                Param("to_tz", "Target zone, or a list of them; an entry may be `{\"tz\": ..., \"label\": ...}` and the label is echoed back.", required=True),
                Param("locale", "Country code used to read numeric dates, e.g. `IN`."),
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
                Param("value", "The date to parse.", required=True),
                Param("tz", "Zone attached to a naive result."),
                Param("locale", "Country code deciding DD/MM vs MM/DD, e.g. `IN`, `US`."),
                Param("ref_date", "Anchor for relative phrases.", default="now"),
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
                Param("value", "Starting date.", default="`now`"),
                Param("amount", "How much to add; negative subtracts.", required=True),
                Param("unit", "`seconds`, `minutes`, `hours`, `days`, `weeks`, `fortnights`, `months`, `quarters`, `years`, `business_days`.", required=True),
                Param("region", "ISO country code whose public holidays to skip, for `business_days`."),
                Param("subdiv", "State/province code for regional holidays."),
                Param("weekend", "Which weekdays count as weekend.", default="`[saturday, sunday]`"),
                Param("extra_holidays", "Extra non-working dates."),
                Param("tz", "Zone applied to `value`."),
                Param("locale", "Country code for reading numeric dates."),
            ),
        ),
        Mode(
            name="diff",
            purpose="The distance between two instants, every way at once.",
            description=(
                "Returns the gap between `start` and `end` as a calendar breakdown (years/months/days/…), "
                "as totals in every unit, as whole months, and as a human string — plus a `sign` and a "
                "plain-English `direction`, so a negative result cannot be misread. Pass "
                "`unit: business_days` (with an optional `region`) to count working days instead."
            ),
            params=(
                Param("start", "Start instant.", required=True),
                Param("end", "End instant.", default="`now`"),
                Param("unit", "Report one unit in `value`; `business_days` counts working days.", default="`auto`"),
                Param("region", "ISO country code for holidays, with `business_days`."),
                Param("weekend", "Which weekdays are non-working.", default="`[saturday, sunday]`"),
                Param("tz", "Zone applied to both sides."),
                Param("locale", "Country code for reading numeric dates."),
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
                Param("value", "The date.", default="`today`"),
                Param("tz", "Zone applied to `value`."),
                Param("locale", "Country code for reading numeric dates."),
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
                Param("year", "Year. Taken from `value`, or today, when omitted."),
                Param("month", "Month number or name."),
                Param("weekday", "Weekday name, abbreviation, or Monday-zero index.", required=True),
                Param("n", "Which one: 1..5, −1..−5, or `first`/`last`.", default="1"),
                Param("value", "Date whose year/month to use when `year`/`month` are omitted."),
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
                Param("start", "Start of the range.", required=True),
                Param("end", "End of the range.", required=True),
                Param("region", "ISO country code whose public holidays to exclude."),
                Param("subdiv", "State/province code for regional holidays."),
                Param("weekend", "Non-working weekdays.", default="`[saturday, sunday]`"),
                Param("extra_holidays", "Extra non-working dates, e.g. a company shutdown."),
                Param("include_start", "Count the start date.", default="`true`"),
                Param("include_end", "Count the end date.", default="`true`"),
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
                Param("a", "First interval, `{start, end}`.", required=True),
                Param("b", "Second interval, `{start, end}`.", required=True),
                Param("tz", "Zone applied to naive endpoints."),
                Param("locale", "Country code for reading numeric dates."),
            ),
        ),
        Mode(
            name="free_slots",
            purpose="Common free slots for people in different time zones.",
            description=(
                "Takes two or more participants, each with an IANA zone and one or more availability "
                "windows — a weekly pattern like `09:00`–`17:00` on `[mon, …, fri]`, or a one-off local "
                "range like `2026-09-01T09:00`–`2026-09-01T12:00` — lays every window on that person's "
                "own calendar through `zoneinfo`, intersects them all in UTC, and returns the slots that "
                "fit `duration`, earliest UTC first. Every slot is shown in each participant's local time "
                "and in UTC, `per_day` totals the overlap per UTC date, and a window that spans a DST "
                "change is expanded to its real length with a note in `assumptions`. No common time is "
                "still `ok: true` with `slots: []` and a warning naming the participants who never overlap."
            ),
            params=(
                Param("participants", "Two or more of `{tz, label?, windows: [{start, end, days?}]}`. A window with `HH:MM` ends repeats on `days` (every day when omitted); one with full timestamps is a single occurrence.", required=True),
                Param("duration", "Meeting length in minutes.", default="`granularity`"),
                Param("granularity", "Step between candidate starts, in minutes.", default="30"),
                Param("start", "First UTC date to search.", default="`today`"),
                Param("end", "Last UTC date to search, inclusive; at most 92 days.", default="`start` + 7 days"),
                Param("limit", "Maximum slots returned, 1..500; the rest are counted in `warnings`.", default="20"),
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
                Param("ranges", "Intervals, each `{start, end}` with an optional `label`.", required=True),
                Param("tz", "Zone applied to naive endpoints."),
                Param("locale", "Country code for reading numeric dates."),
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
                Param("rule", "RRULE string or a recognised phrase.", required=True),
                Param("start", "First candidate date (DTSTART).", default="`today`"),
                Param("count", "Stop after this many occurrences."),
                Param("until", "Stop at this date."),
                Param("limit", "Hard cap on returned occurrences, max 1000.", default="100"),
                Param("dates_only", "Return dates rather than full timestamps.", default="`true`"),
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
                Param("expr", "Cron expression or `@`-alias.", required=True),
                Param("tz", "Zone the schedule runs in.", default="`UTC`"),
                Param("start", "Start searching after this instant.", default="now"),
                Param("n", "How many fire times to return, 1..500.", default="5"),
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
                Param("dob", "Date of birth.", required=True),
                Param("on", "Date to compute the age on.", default="`today`"),
                Param("locale", "Country code for reading numeric dates."),
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
                Param("value", "The date to place.", default="`today`"),
                Param("region", "ISO country code selecting a known FY convention."),
                Param("fy_start_month", "First month of the fiscal year, 1..12.", default="1 (calendar year)"),
                Param("tz", "Zone applied to `value`."),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# scale
# --------------------------------------------------------------------------- #

SCALE = ToolDoc(
    name="scale",
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
    related=(
        "[`convert`](/docs/tools/convert) when only the unit changes · "
        "[`numbers`](/docs/tools/numbers) `allocate` when a total must be split so the parts sum "
        "exactly · [`math`](/docs/tools/math) for anything that is not a proportion."
    ),
    examples=scale_mod.EXAMPLES,
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
                Param("from_qty", "The quantity you have. Not required if `factor` is given.", required=True),
                Param("to_qty", "The quantity you want.", default="1, if `to_unit` is given"),
                Param("from_unit", "Unit of `from_qty`."),
                Param("to_unit", "Unit of `to_qty`; the conversion is folded into the factor."),
                Param("factor", "Use this factor instead of a ratio."),
                Param("entities", "Things to scale: `{name, qty, unit?, integer?}`, or a `{name: qty}` map."),
                Param("precision", "Decimal places in the `value` fields.", default="6"),
                Param("assume", "Pass `common` to resolve ambiguous units to their usual reading."),
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
                Param("from_qty", "The quantity you have.", required=True),
                Param("to_qty", "The quantity you want.", required=True),
                Param("entities", "Things that move inversely: `{name, qty, unit?, integer?}`."),
                Param("from_unit", "Unit of `from_qty`."),
                Param("to_unit", "Unit of `to_qty`."),
                Param("precision", "Decimal places in the `value` fields.", default="6"),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# convert
# --------------------------------------------------------------------------- #

CONVERT = ToolDoc(
    name="convert",
    intro=(
        "Unit conversion on top of Pint, with one rule the rest of the world skips: a unit with two "
        "meanings is never guessed. `ton` (metric, short, long), `gallon` (US, imperial), `oz` (mass "
        "or fluid), `cup`, `pint`, `calorie`, `KB`/`GB` (decimal, binary, bits) all come back as "
        "`ambiguous` with the concrete options, unless you pass `assume: common`. Everything else "
        "converts exactly, with the conversion factor returned alongside the result. Three families "
        "that a generic unit registry gets wrong have modes of their own: fuel economy (L/100 km is an "
        "inverse quantity), cooking measures (a cup of flour and a cup of sugar weigh different "
        "amounts) and shoe/clothing sizes (table lookups, named and flagged as approximate)."
    ),
    when=(
        "Any length, mass, area, volume, speed, energy, power, pressure, data or time conversion.",
        "Temperatures, where an absolute reading and a temperature difference are not the same sum.",
        "Currency, which needs a rate you supply — this tool never invents an exchange rate.",
        "Indian land units (bigha, katha, cent, ground, guntha, ankanam) and lakh/crore scaling.",
        "mpg ↔ L/100 km ↔ km/L, with US and imperial gallons kept apart.",
        "Recipe scaling that crosses cups and grams, with the ingredient's density stated.",
        "Shoe and clothing sizes across US, UK and EU charts.",
    ),
    related=(
        "[`scale`](/docs/tools/scale) when a unit change has to ripple through several line items · "
        "[`numbers`](/docs/tools/numbers) `format` to present the result for a locale · "
        "the `fx_rate` tool in `leftbrain-external` for live exchange rates."
    ),
    examples=convert_mod.EXAMPLES,
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
                Param("value", "The quantity to convert.", default="1"),
                Param("from_unit", "Source unit.", required=True),
                Param("to_unit", "Target unit.", required=True),
                Param("assume", "`common` resolves an ambiguous unit to its usual reading instead of failing."),
                Param("precision", "Significant digits in the result.", default="10"),
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
                Param("value", "The temperature.", default="1"),
                Param("from_unit", "Source scale: `C`, `F`, `K`, `degR`…", required=True),
                Param("to_unit", "Target scale.", required=True),
                Param("delta", "Treat the value as a difference, not a reading.", default="`false`"),
                Param("precision", "Significant digits in the result.", default="10"),
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
                Param("value", "The amount.", default="1"),
                Param("from_unit", "Source ISO 4217 code, e.g. `USD`.", required=True),
                Param("to_unit", "Target ISO 4217 code.", required=True),
                Param("rate", "Direct rate: 1 `from_unit` = `rate` `to_unit`."),
                Param("rates", "Rate table keyed by currency code."),
                Param("base", "Base currency of the `rates` table."),
                Param("decimals", "Decimal places in the rounded value.", default="2"),
                Param("date", "Echoed back as `as_of`; the tool does not use it to look anything up."),
            ),
        ),
        Mode(
            name="fuel_economy",
            purpose="mpg (US or UK), km/L and L/100 km.",
            description=(
                "Converts between `mpg_us`, `mpg_uk`, `km_per_l` and `l_per_100km` with exact "
                "constants (mile 1.609344 km, US gallon 3.785411784 L, imperial gallon 4.54609 L). "
                "A bare `mpg` is refused with both gallons as options — they differ by 20%. L/100 km "
                "is an inverse quantity, and any conversion that crosses it says so in "
                "`assumptions`: doubling the mpg halves the L/100 km, but a 10 mpg improvement saves "
                "far more fuel at 20 mpg than at 50. The result carries `km_per_l` as the common "
                "intermediate so a chain of conversions can be checked."
            ),
            params=(
                Param("value", "The fuel economy figure; must be positive.", default="1"),
                Param("from_unit", "`mpg_us`, `mpg_uk`, `km_per_l` or `l_per_100km` (aliases `km/l`, `kmpl`, `l/100km`, `mpg (uk)`…).", required=True),
                Param("to_unit", "Target figure, same choices.", required=True),
                Param("decimals", "Decimal places in the rounded value.", default="2"),
            ),
        ),
        Mode(
            name="cooking",
            purpose="Cups, spoons, ml and grams by ingredient density.",
            description=(
                "Converts kitchen measures: volume (`cup`, `tbsp`, `tsp`, `ml`, `l`, `fl_oz`) and mass "
                "(`g`, `kg`, `oz_weight`, `lb`). Volume to volume and mass to mass need nothing else. "
                "Crossing between them needs `ingredient`, looked up in a built-in density table "
                "(water, milk, cream, yogurt, oil, honey, maple syrup, flour, cornstarch, cocoa, sugar, "
                "brown sugar, powdered sugar, butter, peanut butter, rice, oats, salt); a missing or "
                "unknown ingredient comes back as `ambiguous` with the table as `needs.options`, and "
                "the grams-per-cup used are stated in `assumptions`. The cup system defaults to US "
                "(240 ml cup, 15 ml tbsp) and is declared; `cup: metric|uk|au` switches to the 250 ml "
                "cup — and the Australian 20 ml tablespoon. `oz` alone is refused: weight or fluid."
            ),
            params=(
                Param("value", "The quantity.", default="1"),
                Param("from_unit", "Source measure.", required=True),
                Param("to_unit", "Target measure.", required=True),
                Param("ingredient", "Required for mass ↔ volume: `flour`, `sugar`, `butter`… (`plain flour`, `icing sugar` and similar spellings resolve)."),
                Param("cup", "Cup system: `us`, `metric`, `uk` or `au`.", default="`us`"),
                Param("decimals", "Decimal places in the rounded value.", default="2"),
            ),
        ),
        Mode(
            name="sizes",
            purpose="Shoe and clothing size charts.",
            description=(
                "Table lookups, not arithmetic, so every result carries a warning that sizes are "
                "approximate and names the chart. `category: shoe` converts between `us_men`, "
                "`us_women`, `uk`, `eu` and `cm` (foot length) on a generic adult chart (US men = UK + 1, "
                "US women = US men + 1.5); a plain `us` is refused unless `gender` is given, and a value "
                "between rows snaps to the nearest half size with a warning. `category: clothing` maps "
                "`alpha` (XS–XXL) to `chest_cm` and `waist_cm` bands and back for a chart chosen by "
                "`region` and `gender`, both required: `us` is the generic inch-based retail chart, `eu` "
                "the EN 13402-3 letter codes (chest/bust only — `waist_cm` there is `unsupported`). The "
                "whole chart `row` is returned alongside the value."
            ),
            params=(
                Param("value", "The size: a number, or a letter size (`M`, `XL`) when `from_unit` is `alpha`.", default="1"),
                Param("from_unit", "Shoes: `us_men`, `us_women`, `uk`, `eu`, `cm`. Clothing: `alpha`, `chest_cm`, `waist_cm`.", required=True),
                Param("to_unit", "Target scale, same choices.", required=True),
                Param("category", "`shoe` or `clothing`.", required=True),
                Param("region", "Clothing chart: `us` or `eu`. Shoes carry the region in the scale name."),
                Param("gender", "`men` or `women`; required for clothing, resolves a plain `us` shoe size."),
                Param("decimals", "Decimal places for cm bands (clothing).", default="1"),
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
                "which path you get; `fuel_economy`, `cooking` and `sizes` are never chosen by `auto`."
            ),
            params=(
                Param("value", "The quantity or amount.", default="1"),
                Param("from_unit", "Source unit or currency code.", required=True),
                Param("to_unit", "Target unit or currency code.", required=True),
                Param("rate", "Rate, when the arguments resolve to a currency conversion."),
                Param("rates", "Rate table, as in `currency`."),
                Param("assume", "`common`, as in `units`."),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# holidays
# --------------------------------------------------------------------------- #

HOLIDAYS = ToolDoc(
    name="holidays",
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
    related=(
        "[`datetime`](/docs/tools/datetime) `business_days` and `add` consume the same `region` and "
        "`subdiv` · [`geo_offline`](/docs/tools/geo_offline) `country` to resolve a country name to "
        "its ISO code."
    ),
    examples=holidays_.EXAMPLES,
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
                Param("region", "ISO country code (`IN`, `US`, `GB`); `UK` is accepted as `GB`.", required=True),
                Param("year", "The year.", default="current year"),
                Param("years", "Several years at once."),
                Param("month", "Filter the list to one month; long weekends still cover the year."),
                Param("subdiv", "State or province code for regional holidays."),
                Param("categories", "Holiday categories, where the country's calendar defines them."),
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
                Param("region", "ISO country code.", required=True),
                Param("date", "The date to check.", default="`today`"),
                Param("subdiv", "State or province code."),
                Param("locale", "Country code deciding DD/MM vs MM/DD in `date`."),
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
                Param("region", "ISO country code.", required=True),
                Param("date", "Start looking from here.", default="`today`"),
                Param("n", "How many holidays to return.", default="5"),
                Param("subdiv", "State or province code."),
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
                Param("region", "ISO country code.", required=True),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# numbers
# --------------------------------------------------------------------------- #

NUMBERS = ToolDoc(
    name="numbers",
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
    related=(
        "[`math`](/docs/tools/math) for the arithmetic itself · "
        "[`convert`](/docs/tools/convert) for units and currency conversion · "
        "[`collections`](/docs/tools/collections) `aggregate` for sums across records."
    ),
    examples=numbers_mod.EXAMPLES,
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
                Param("values", "Two or more values. Strings, numbers, `₹1.2 Cr`, `2.5k`, `12%`, `(500)`.", required=True),
                Param("a", "First value, as an alternative to `values`."),
                Param("b", "Second value, as an alternative to `values`."),
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
                Param("value", "The value to round.", required=True),
                Param("decimals", "Decimal places.", default="0"),
                Param("significant", "Round to this many significant figures instead."),
                Param("nearest", "Round to the nearest multiple of this."),
                Param("rounding", "Tie-break rule.", default="`half_up`"),
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
                Param("value", "The value to format.", required=True),
                Param("locale", "`en_IN`, `en_US`, `de_DE`, `fr_FR`, `de_CH`, `ja_JP`…", default="`en_US`"),
                Param("style", "Presentation style.", default="`number`"),
                Param("currency", "ISO code, for `style: currency` or `compact`."),
                Param("decimals", "Decimal places.", default="style-dependent"),
                Param("accounting", "Show negatives in parentheses.", default="`false`"),
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
                Param("total", "The amount to divide.", required=True),
                Param("parts", "Split equally into this many parts."),
                Param("weights", "Proportional weights, or a `{label: weight}` map."),
                Param("percentages", "Weights that must sum to 100."),
                Param("labels", "Names for the parts."),
                Param("decimals", "Minor-unit precision.", default="2"),
                Param("method", "Where leftover units go.", default="`largest_remainder`"),
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
                Param("kind", "`arithmetic`, `geometric`, `range`, `fibonacci`, `primes`, `squares`.", default="`arithmetic`"),
                Param("start", "First term.", default="0 (1 for geometric)"),
                Param("step", "Common difference, for `arithmetic` and `range`.", default="1"),
                Param("ratio", "Common ratio, for `geometric`.", default="2"),
                Param("end", "Last value, for `arithmetic` and `range`."),
                Param("n", "Number of terms, 1..10000."),
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
                Param("value", "One value to parse."),
                Param("values", "Several values; the result becomes a list."),
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
                Param("value", "The amount.", required=True),
                Param("system", "Numbering system.", default="`international`"),
                Param("currency", "ISO code; switches to the currency phrasing."),
                Param("suffix_only", "Append “only”, as invoices do.", default="`true`"),
            ),
        ),
        Mode(
            name="semver",
            purpose="Order version strings the SemVer way.",
            description=(
                "Compares or sorts version strings as versions, not decimals — `1.10` is newer than "
                "`1.9`. Precedence follows SemVer 2.0: major, minor, patch numerically; a release "
                "above every pre-release of it; pre-release identifiers left to right, numeric ones "
                "numerically and before alphanumeric ones, a shorter prefix first. Build metadata "
                "(`+…`) is returned but never decides the order. A leading `v` and a missing minor or "
                "patch are tolerated and recorded in `assumptions`."
            ),
            params=(
                Param("values", "Two or more version strings."),
                Param("a", "First version, with `b`; gives a `relation`."),
                Param("b", "Second version."),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# finance
# --------------------------------------------------------------------------- #

FINANCE = ToolDoc(
    name="finance",
    intro=(
        "Money arithmetic, done the way a bank's back office does it rather than the way a "
        "language model approximates it: instalments that come from the rounded schedule and "
        "reconcile to zero, GST halves whose rounding difference is reported instead of hidden, "
        "an IRR found by bisection rather than recalled. Every figure is a `Decimal`. The two "
        "readings that silently ruin money maths — is the rate per year or per month, is the "
        "amount inclusive of tax — are never guessed: leave them out and the tool asks."
    ),
    when=(
        "Quoting an EMI, total interest or an amortisation schedule.",
        "Projecting compound growth, with or without a monthly contribution.",
        "Turning a start and end value into an annualised growth rate.",
        "Valuing a cash-flow series at a discount rate, or finding the rate that makes it break even.",
        "Splitting an Indian invoice amount into base and CGST/SGST or IGST.",
        "Any percentage question where points and percent, or stacked and added discounts, get confused.",
    ),
    related=(
        "[`numbers`](/docs/tools/numbers) for rounding rules, locale formatting and exact allocation · "
        "[`math`](/docs/tools/math) for the formulas themselves · "
        "[`convert`](/docs/tools/convert) `currency` when the money is in another currency."
    ),
    examples=finance_mod.EXAMPLES,
    modes=(
        Mode(
            name="emi",
            purpose="Loan instalment, total interest and the amortisation schedule.",
            description=(
                "The equal monthly instalment for a reducing-balance loan, from the standard formula "
                "`P·r·(1+r)ⁿ / ((1+r)ⁿ−1)`. The instalment is rounded with the stated rule, then the "
                "schedule is built month by month with that rounded figure, and the last instalment "
                "clears the remaining balance exactly — so `total_interest` and `total_payment` are the "
                "sums of what would actually be paid, not the formula times *n*. `decimals: 0` with "
                "`rounding: ceil` gives whole-rupee instalments rounded up, as many Indian lenders do. "
                "`schedule: true` returns every row."
            ),
            params=(
                Param("principal", "Loan amount.", required=True),
                Param("rate", "Interest rate in percent.", required=True),
                Param("rate_period", "`annual` or `monthly` — what the rate is per. Required; never inferred.", required=True),
                Param("months", "Term in months (or give `years`)."),
                Param("years", "Term in years; may be fractional if it is a whole number of months."),
                Param("schedule", "Return the month-by-month rows.", default="`false`"),
                Param("decimals", "Rounding of each instalment and interest figure.", default="2"),
                Param("rounding", "`half_up`, `half_even`, `ceil`, `floor`, `truncate`…", default="`half_up`"),
            ),
        ),
        Mode(
            name="compound",
            purpose="Future value under compound interest, optionally with regular contributions.",
            description=(
                "Grows a principal at a rate compounded `annual`, `semiannual`, `quarterly`, `monthly`, "
                "`weekly`, `daily` or `continuous`, over a term in years or months, and reports the "
                "future value, the interest earned and the effective annual rate the compounding "
                "implies. A `contribution` is added every compounding period, at its `end` (ordinary "
                "annuity) or `begin` (annuity due) — the SIP case. When no compounding is given, "
                "annual is used and said so in `assumptions`."
            ),
            params=(
                Param("principal", "Opening balance; may be 0 for a pure contribution plan.", required=True),
                Param("rate", "Interest rate in percent.", required=True),
                Param("rate_period", "`annual` or `monthly` — what the rate is per. Required.", required=True),
                Param("years", "Term in years (or give `months`)."),
                Param("months", "Term in months."),
                Param("compounding", "How often interest is credited.", default="`annual`"),
                Param("contribution", "Amount added each compounding period.", default="0"),
                Param("contribution_timing", "`end` or `begin` of each period.", default="`end`"),
                Param("decimals", "Rounding of the money figures.", default="2"),
                Param("rounding", "Rounding rule.", default="`half_up`"),
            ),
        ),
        Mode(
            name="cagr",
            purpose="Compound annual growth rate between two values.",
            description=(
                "`(end / start)^(1 / years) − 1`, as a percentage to four decimals, alongside the total "
                "growth and the multiple. A decline is a negative rate; a zero or negative start value "
                "has no growth rate and is refused."
            ),
            params=(
                Param("start_value", "Value at the start.", required=True),
                Param("end_value", "Value at the end.", required=True),
                Param("years", "Elapsed years; fractional is fine.", required=True),
            ),
        ),
        Mode(
            name="npv_irr",
            purpose="Net present value at a rate, and the internal rate of return.",
            description=(
                "Takes a cash-flow series with the first entry at time 0 (an outlay is negative) and one "
                "entry per period after it. With `rate`, returns the NPV at that rate per period. The IRR "
                "is always attempted: the rate at which NPV is zero, found by bisection between −99.99% "
                "and 1000% — deterministic, no starting guess, no dependence on a spreadsheet's solver. "
                "Flows that never change sign have no IRR and say so."
            ),
            params=(
                Param("cashflows", "Amounts per period, time 0 first.", required=True),
                Param("rate", "Discount rate in percent per period, for the NPV."),
                Param("decimals", "Rounding of the NPV.", default="2"),
                Param("rounding", "Rounding rule.", default="`half_up`"),
            ),
        ),
        Mode(
            name="gst",
            purpose="Split an amount into base and GST, with CGST/SGST or IGST.",
            description=(
                "Works out the tax-exclusive base and the tax from an amount that is either "
                "`inclusive` or `exclusive` of GST — which one is required, because guessing wrong "
                "changes the invoice. Intra-state supply splits the tax into equal CGST and SGST "
                "halves; `supply: inter` gives a single IGST. Each half is rounded on its own, so when "
                "the halves do not add up to the rounded total the difference is reported in "
                "`rounding_difference` and a warning, instead of being quietly absorbed. The exact "
                "unrounded tax is always included."
            ),
            params=(
                Param("amount", "The amount to split.", required=True),
                Param("rate", "GST rate in percent (5, 12, 18, 28…).", required=True),
                Param("amount_is", "`inclusive` or `exclusive` of GST. Required; never inferred.", required=True),
                Param("supply", "`intra` (CGST + SGST) or `inter` (IGST).", default="`intra`"),
                Param("decimals", "Rounding of the tax figures.", default="2"),
                Param("rounding", "Rounding rule.", default="`half_up`"),
            ),
        ),
        Mode(
            name="percent",
            purpose="Percentage arithmetic that people get wrong in the same four ways.",
            description=(
                "`op` picks the calculation. `change` from `a` to `b` reports the relative percent "
                "change *and* the difference in percentage points, because 10% → 12.5% is both a 25% "
                "change and 2.5 points. `of` is `percent` of `value`. `discount` applies a list of "
                "percentages `stacked` (each on the already-discounted price, as shops do) and "
                "`additive` (percentages summed first, as people expect), with the effective rate of "
                "each. `split` divides a bill plus an optional `tip` percent among `people` using "
                "largest-remainder allocation so the shares add up to the total exactly."
            ),
            params=(
                Param("op", "`change`, `of`, `discount` or `split`.", required=True),
                Param("a", "Starting value, for `change`."),
                Param("b", "Ending value, for `change`."),
                Param("percent", "The percentage, for `of` (or a single discount)."),
                Param("value", "The base value, for `of`."),
                Param("price", "List price, for `discount`."),
                Param("discounts", "Percentages applied in order, for `discount`."),
                Param("total", "The bill, for `split`."),
                Param("tip", "Tip in percent, for `split`.", default="0"),
                Param("people", "How many ways to split."),
                Param("decimals", "Rounding of money figures.", default="2"),
                Param("rounding", "Rounding rule.", default="`half_up`"),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# text
# --------------------------------------------------------------------------- #

TEXT = ToolDoc(
    name="text",
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
    related=(
        "[`collections`](/docs/tools/collections) for the same operations over records rather than "
        "strings · [`validate`](/docs/tools/validate) to check the identifiers `extract` finds · "
        "[`encode`](/docs/tools/encode) for hashing and encoding the text itself."
    ),
    examples=text_mod.EXAMPLES,
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
                Param("text", "The text to measure.", required=True),
                Param("what", "`all`, `occurrences`, or the name of one statistic.", default="`all`"),
                Param("substring", "The needle, for `what: occurrences`."),
                Param("case_sensitive", "Match case when counting occurrences.", default="`true`"),
                Param("overlapping", "Count overlapping matches.", default="`false`"),
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
                Param("text", "The text to search.", required=True),
                Param("pattern", "A Python regular expression.", required=True),
                Param("flags", "Any of `imsxua`."),
                Param("limit", "Maximum matches returned.", default="1000"),
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
                Param("text", "The text to transform.", required=True),
                Param("pattern", "A Python regular expression.", required=True),
                Param("replacement", "Replacement, with `\\1`-style backreferences.", required=True),
                Param("flags", "Any of `imsxua`."),
                Param("count", "Replace at most this many; 0 means all.", default="0"),
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
                Param("a", "The original text.", required=True),
                Param("b", "The changed text.", required=True),
                Param("granularity", "Unit of comparison.", default="`line`"),
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
                Param("items", "The list to sort.", required=True),
                Param("key", "Field to sort on, when the items are objects."),
                Param("order", "Sort direction.", default="`asc`"),
                Param("natural", "Digit runs compare numerically.", default="`true`"),
                Param("case_insensitive", "Fold case before comparing.", default="`true`"),
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
                Param("items", "The list to dedupe.", required=True),
                Param("key", "Field to compare, when the items are objects."),
                Param("case_insensitive", "Fold case before comparing.", default="`false`"),
                Param("normalize_whitespace", "Collapse runs of whitespace before comparing.", default="`true`"),
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
                Param("text", "The text to scan.", required=True),
                Param("what", "One kind, a list of kinds, or `all`.", default="`all`"),
                Param("unique", "Collapse repeated hits.", default="`true`"),
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
                Param("text", "The text to search.", required=True),
                Param("substring", "The string to find.", required=True),
                Param("case_sensitive", "Match case.", default="`false`"),
                Param("context", "Characters of context on each side.", default="40"),
            ),
        ),
        Mode(
            name="similarity",
            purpose="Edit distance, and the best match from a list.",
            description=(
                "Levenshtein distance by codepoint and a 0–1 `ratio` (1 minus distance over the longer "
                "length). Give `a` and `b` for one pair, or `text` and `items` to rank a list of "
                "candidates and return the `best` one with its index — for mapping what a user typed "
                "onto a menu, or spotting near-duplicate names. Case is folded and whitespace "
                "normalised by default; both are stated in `assumptions`. Strings are capped at 5,000 "
                "characters."
            ),
            params=(
                Param("a", "One string of a pair."),
                Param("b", "The other."),
                Param("text", "The input to match against `items`."),
                Param("items", "Candidate strings to rank."),
                Param("case_insensitive", "Fold case before comparing.", default="`true`"),
                Param("normalize_whitespace", "Collapse runs of whitespace first.", default="`true`"),
                Param("limit", "How many ranked candidates to return.", default="5"),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# collections
# --------------------------------------------------------------------------- #

COLLECTIONS = ToolDoc(
    name="collections",
    intro=(
        "Exact list and record logic — the operations a model starts quietly dropping items from "
        "somewhere past twenty entries. Compare two lists and get both differences named, group "
        "records with real aggregates computed as decimals, sort on several keys, find duplicates, "
        "paginate, chunk, and flatten or rebuild nested JSON — plus the table arithmetic a spreadsheet "
        "would do: filter rows, pivot, running totals, IQR outliers, a per-field summary and CSV out. "
        "Records may arrive as JSON objects or as CSV text: the delimiter is sniffed, the header row "
        "detected (the first row when it has no numeric, date or boolean cell; `has_header` overrides), "
        "every field typed as number, ISO date, boolean or text, and blank rows and `N/A`-style cells "
        "skipped and counted — each reading stated in `assumptions`. Numbers loaded from CSV are exact "
        "decimals returned as strings. Tables above 5,000 rows are refused. The list modes address "
        "fields with dotted paths (`rep.name`, `items[0].sku`); the table modes — `filter`, `pivot`, "
        "`running`, `outliers`, `summarize`, `to_csv` — work on flat records, top-level fields only."
    ),
    when=(
        "“What is in list A but not list B?” — and the reverse, in the same answer.",
        "Grouping records by a field with sum/avg/min/max that must be exact.",
        "Multi-key sorting, deduplication and duplicate hunting over records.",
        "A CSV export pasted in: summarise every column, filter rows, pivot, running totals, spot outliers.",
        "Reshaping JSON: flatten for a spreadsheet, unflatten from a form payload, records to CSV.",
        "Pagination and chunking before handing work to another system.",
    ),
    related=(
        "[`text`](/docs/tools/text) `sort`/`dedupe` for plain strings · "
        "[`numbers`](/docs/tools/numbers) for formatting the aggregates · "
        "[`validate`](/docs/tools/validate) `assert` to check the records themselves."
    ),
    examples=collections_.EXAMPLES,
    modes=(
        Mode(
            name="set_ops",
            purpose="Compare two lists: union, intersection, differences.",
            description=(
                "Compares two lists and always returns the full picture — `only_in_a`, `only_in_b`, "
                "`in_both`, counts, and whether the two are equal as sets — regardless of which `op` "
                "you asked for. `op` additionally puts one specific result in `result`. Objects are "
                "compared structurally, or on one field via `key`. Duplicates inside a list are "
                "collapsed, and that is stated in `assumptions`. Either side may be CSV text, read as "
                "records; how each was read is stated with an `a:`/`b:` prefix."
            ),
            params=(
                Param("a", "First list, or CSV text.", required=True),
                Param("b", "Second list, or CSV text.", required=True),
                Param("op", "Which result to highlight.", default="`compare`"),
                Param("key", "Dotted path to compare on, for objects."),
                Param("case_insensitive", "Fold case on string comparisons.", default="`false`"),
                Param("delimiter", "CSV delimiter, when a side is CSV text.", default="sniffed"),
                Param("has_header", "Whether CSV text starts with a header row.", default="detected"),
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
                Param("items", "The records, or CSV text.", required=True),
                Param("key", "Dotted path to group on.", required=True),
                Param("agg_field", "Dotted path the aggregates are computed over."),
                Param("agg", "Which aggregates to compute.", default="`[count]`"),
                Param("include_items", "Include each group's members.", default="`true`"),
                Param("delimiter", "CSV delimiter, when `items` is CSV text.", default="sniffed"),
                Param("has_header", "Whether CSV text starts with a header row.", default="detected"),
            ),
        ),
        Mode(
            name="aggregate",
            purpose="Aggregate one field across every record.",
            description=(
                "The whole-list version of `group_by`'s aggregates: `count`, `count_distinct`, `sum`, "
                "`avg`, `min`, `max`, `first`, `last`, `list`. Numeric aggregates are computed as "
                "decimals and returned as strings; non-numeric values are ignored for them and that is "
                "stated in `assumptions`. Omit `field` to aggregate the items themselves. For every "
                "numeric field at once, see `summarize`."
            ),
            params=(
                Param("items", "The records or values, or CSV text.", required=True),
                Param("field", "Dotted path to aggregate; omit to use the items themselves."),
                Param("ops", "Which aggregates to compute.", default="`[count, sum, avg, min, max]`"),
                Param("delimiter", "CSV delimiter, when `items` is CSV text.", default="sniffed"),
                Param("has_header", "Whether CSV text starts with a header row.", default="detected"),
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
                Param("items", "The records, or CSV text.", required=True),
                Param("fields", "Dotted paths to keep.", required=True),
                Param("rename", "Map of path to output name."),
                Param("short_names", "Use the last path segment as the key.", default="`false`"),
                Param("delimiter", "CSV delimiter, when `items` is CSV text.", default="sniffed"),
                Param("has_header", "Whether CSV text starts with a header row.", default="detected"),
            ),
        ),
        Mode(
            name="flatten",
            purpose="Flatten nested JSON — or nested lists.",
            description=(
                "Given an object, produces a single-level map whose keys are dotted paths "
                "(`rep.name`, `tags[0]`) — the shape a CSV or a form encoder wants. Given a list, "
                "flattens nested lists instead. `depth` limits how far it descends; `separator` changes "
                "the joining character. CSV text is read as a list of records first."
            ),
            params=(
                Param("data", "The structure to flatten, or CSV text.", required=True),
                Param("depth", "Maximum levels to descend.", default="unlimited"),
                Param("separator", "Key separator.", default="`.`"),
                Param("flatten_lists", "Index into lists as well as objects.", default="`true`"),
                Param("delimiter", "CSV delimiter, when `data` is CSV text.", default="sniffed"),
                Param("has_header", "Whether CSV text starts with a header row.", default="detected"),
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
                Param("data", "A flat object with dotted keys.", required=True),
                Param("separator", "Key separator.", default="`.`"),
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
                Param("items", "The full list, or CSV text.", required=True),
                Param("page", "1-based page number.", default="1"),
                Param("per_page", "Items per page.", default="20"),
                Param("delimiter", "CSV delimiter, when `items` is CSV text.", default="sniffed"),
                Param("has_header", "Whether CSV text starts with a header row.", default="detected"),
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
                Param("items", "The list to inspect, or CSV text.", required=True),
                Param("key", "Dotted path to compare, for objects."),
                Param("case_insensitive", "Fold case on string comparisons.", default="`false`"),
                Param("delimiter", "CSV delimiter, when `items` is CSV text.", default="sniffed"),
                Param("has_header", "Whether CSV text starts with a header row.", default="detected"),
            ),
        ),
        Mode(
            name="sort_by",
            purpose="Stable multi-key sort over records.",
            description=(
                "Sorts records by several keys at once, each with its own direction: "
                "`keys: [{field: region}, {field: amount, order: desc}]`. The sort is stable, nulls "
                "sort last, JSON numbers compare as numbers, and strings — numeric-looking ones "
                "included — compare case-insensitively as text. Fields loaded from CSV text are typed "
                "first, so a numeric column sorts numerically. `changed` says whether the order "
                "actually moved."
            ),
            params=(
                Param("items", "The records, or CSV text.", required=True),
                Param("keys", "Sort keys: `{field, order}` or bare field names."),
                Param("key", "A single sort field, as a shorthand for `keys`."),
                Param("order", "Direction for the `key` shorthand.", default="`asc`"),
                Param("delimiter", "CSV delimiter, when `items` is CSV text.", default="sniffed"),
                Param("has_header", "Whether CSV text starts with a header row.", default="detected"),
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
                Param("items", "The list to split, or CSV text.", required=True),
                Param("size", "Maximum items per chunk."),
                Param("n", "Number of chunks; sizes differ by at most 1."),
                Param("delimiter", "CSV delimiter, when `items` is CSV text.", default="sniffed"),
                Param("has_header", "Whether CSV text starts with a header row.", default="detected"),
            ),
        ),
        Mode(
            name="filter",
            purpose="Keep the rows that satisfy every predicate.",
            description=(
                "Keeps the records for which every `where` predicate holds (AND). Each predicate is "
                "`{field, op, value}` with the `validate.assert` vocabulary — `eq`, `ne`, `gt`, `gte`, "
                "`lt`, `lte`, `in`, `not_in`, `contains`, `starts_with`, `ends_with`, `empty`, "
                "`not_empty` — and the comparison is made in the field's inferred type: `500` against a "
                "numeric field is a numeric comparison, `2026-01-07` against a date field a date one. "
                "Text comparisons are case-sensitive. Returns the kept `items`, their `count` and how "
                "many were `removed`; more than 500 rows are echoed in part, with a warning."
            ),
            params=(
                Param("items", "The records, or CSV text.", required=True),
                Param("where", "Predicates `{field, op, value}`, all of which must hold.", required=True),
                Param("columns", "Fields to echo.", default="all"),
                Param("delimiter", "CSV delimiter, when `items` is CSV text.", default="sniffed"),
                Param("has_header", "Whether CSV text starts with a header row.", default="detected"),
            ),
        ),
        Mode(
            name="pivot",
            purpose="Cross-tabulate: row keys × one field's values, one aggregate each.",
            description=(
                "A pivot table: `by` names the row key(s), `pivot_columns` the field whose distinct "
                "values become the columns, and each cell holds one aggregate (`agg`: `sum` by default, "
                "or `avg`, `min`, `max`, `median`, `count`) of `column`. Without `column` the cells count "
                "rows. A combination with no rows is `null`, not zero. Every row carries a `total`, and "
                "`totals` holds the column totals and the grand total — the same aggregate over every "
                "underlying value, so an `avg` total is the true mean, not a mean of means."
            ),
            params=(
                Param("items", "The records, or CSV text.", required=True),
                Param("by", "Field(s) forming the row keys.", required=True),
                Param("pivot_columns", "Field whose values become the columns.", required=True),
                Param("column", "Numeric field aggregated into each cell; omit to count rows."),
                Param("agg", "The single aggregate to use.", default="`sum`, or `count` without `column`"),
                Param("decimals", "Round computed values to this many places, half-up."),
                Param("delimiter", "CSV delimiter, when `items` is CSV text.", default="sniffed"),
                Param("has_header", "Whether CSV text starts with a header row.", default="detected"),
            ),
        ),
        Mode(
            name="running",
            purpose="Cumulative total down the rows.",
            description=(
                "Adds a `running` field to every record: the cumulative total of `column` in the order "
                "given. With `by`, the total restarts for each group and `totals` reports where each "
                "group ended. A blank cell adds nothing. When the table has exactly one numeric field "
                "it is used and that is stated; with several the call is `ambiguous` and lists them."
            ),
            params=(
                Param("items", "The records, or CSV text.", required=True),
                Param("column", "Numeric field to accumulate.", default="the only numeric field"),
                Param("by", "Field(s) whose change restarts the total."),
                Param("columns", "Fields to echo alongside `running`.", default="all"),
                Param("decimals", "Round the running values to this many places, half-up."),
                Param("delimiter", "CSV delimiter, when `items` is CSV text.", default="sniffed"),
                Param("has_header", "Whether CSV text starts with a header row.", default="detected"),
            ),
        ),
        Mode(
            name="outliers",
            purpose="Flag values outside the 1.5×IQR fences.",
            description=(
                "Tukey's rule: Q1 and Q3 are his hinges — the medians of the lower and upper halves of "
                "the sorted values, the middle value included in both when the count is odd — and anything below "
                "`Q1 − 1.5×IQR` or above `Q3 + 1.5×IQR` is an outlier. Reports `q1`, `q3`, `iqr`, both "
                "fences and each flagged row with its 1-based `row`, `value` and `side`. Needs at least "
                "four numeric values. `column` defaults to the only numeric field, if there is one."
            ),
            params=(
                Param("items", "The records, or CSV text.", required=True),
                Param("column", "Numeric field to inspect.", default="the only numeric field"),
                Param("decimals", "Round the quartiles and fences to this many places, half-up."),
                Param("delimiter", "CSV delimiter, when `items` is CSV text.", default="sniffed"),
                Param("has_header", "Whether CSV text starts with a header row.", default="detected"),
            ),
        ),
        Mode(
            name="summarize",
            purpose="Every field at once: counts, totals, ranges.",
            description=(
                "One entry per field with its inferred `type`, the `count` of filled cells and the "
                "`nulls`. Numeric fields add `sum`, `avg`, `min`, `max` and `median` as exact decimals; "
                "date fields their `min` and `max`; boolean fields `true`/`false` counts; text fields "
                "the number of `distinct` values. This is `aggregate` for the whole table in one call."
            ),
            params=(
                Param("items", "The records, or CSV text.", required=True),
                Param("columns", "Fields to summarise.", default="all"),
                Param("decimals", "Round computed values to this many places, half-up."),
                Param("delimiter", "CSV delimiter, when `items` is CSV text.", default="sniffed"),
                Param("has_header", "Whether CSV text starts with a header row.", default="detected"),
            ),
        ),
        Mode(
            name="to_csv",
            purpose="Records out as CSV text.",
            description=(
                "Writes the records as CSV text with a header row: numbers in plain decimal form (no "
                "thousands separators or symbols), booleans as `true`/`false`, blanks as empty cells, "
                "and quoting only where the delimiter or a quote appears in a value. `columns` chooses "
                "and orders the fields. Every row is written — this mode is exempt from the 500-row echo "
                "cap."
            ),
            params=(
                Param("items", "The records, or CSV text to re-shape.", required=True),
                Param("columns", "Fields to write, in order.", default="all"),
                Param("delimiter", "Delimiter to write with — and to read `items` with, when it is CSV text.", default="`,`"),
                Param("has_header", "Whether CSV text starts with a header row.", default="detected"),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# validate
# --------------------------------------------------------------------------- #

VALIDATE = ToolDoc(
    name="validate",
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
    related=(
        "[`text`](/docs/tools/text) `extract` to find identifiers before checking them · "
        "[`collections`](/docs/tools/collections) to reshape a document into the paths your rules "
        "expect · [`encode`](/docs/tools/encode) `jwt_decode` for token claims."
    ),
    examples=validate_mod.EXAMPLES,
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
                Param("schema", "The JSON Schema.", required=True),
                Param("data", "The document to validate."),
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
                Param("data", "The document the rules are evaluated against."),
                Param("rules", "The rules: `{path, op, value, id?, message?, weight?}`.", required=True),
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
                "missing value is an error. Card numbers come back masked, never echoed in full. A valid "
                "ISBN is returned in both forms (`isbn10`, `isbn13`); a 979 book has no ISBN-10."
            ),
            params=(
                Param("kind", "Which scheme to check against.", required=True),
                Param("value", "The identifier.", required=True),
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
                Param("value", "The address to check.", required=True),
            ),
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
                Param("value", "The URL to check.", required=True),
            ),
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
                Param("value", "The number, in any punctuation.", required=True),
                Param("region", "ISO country code, required for national numbers."),
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
                Param("value", "An IP address or CIDR network.", required=True),
            ),
            never_fails=(
                "Nothing makes this mode return `ok: false`. An unparseable value comes back as "
                "`valid: false` with the parser's reason."
            ),
        ),
        Mode(
            name="cidr",
            purpose="Membership and overlap of CIDR blocks.",
            description=(
                "Answers the two questions an allowlist raises: is this address (or smaller block) "
                "inside that network — `contains` — and do these blocks overlap. One `network` returns "
                "its size, usable host count, bounds and masks; add `value` for membership. A list of "
                "two or more networks returns every pair with its relation — `equal`, "
                "`a_contains_b`, `b_contains_a` or `disjoint`; CIDR blocks cannot partially overlap. "
                "A block written with host bits set is read as its network and the reading recorded. "
                "An unparseable network or value is an error; a mixed IPv4/IPv6 comparison is a "
                "`contains: false` with the reason in `assumptions`."
            ),
            params=(
                Param("network", "A CIDR block, or a list of two or more to compare.", required=True),
                Param("value", "An address or block to test for membership in `network`."),
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
                Param("sql", "One or more SQL statements.", required=True),
                Param("dialect", "sqlglot dialect, e.g. `postgres`, `mysql`, `snowflake`.", default="generic"),
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
                Param("pattern", "The regular expression to compile.", required=True),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# random
# --------------------------------------------------------------------------- #

RANDOM = ToolDoc(
    name="random",
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
    related=(
        "[`encode`](/docs/tools/encode) to hash or encode what you generate · "
        "[`collections`](/docs/tools/collections) `chunk` for deterministic batching, when you want "
        "splitting without randomness."
    ),
    examples=random_.EXAMPLES,
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
                Param("n", "How many to generate, 1..10000.", default="1"),
                Param("version", "4 is random; 7 is time-ordered.", default="4"),
                Param("format", "Output form.", default="`canonical`"),
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
                Param("min", "Lower bound, inclusive.", default="1"),
                Param("max", "Upper bound, inclusive.", default="100"),
                Param("n", "How many to draw, 1..10000.", default="1"),
                Param("unique", "Draw without replacement.", default="`false`"),
                Param("seed", "Makes the draw reproducible."),
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
                Param("min", "Lower bound.", default="0.0"),
                Param("max", "Upper bound.", default="1.0"),
                Param("n", "How many to draw, 1..10000.", default="1"),
                Param("decimals", "Round each value to this many places."),
                Param("seed", "Makes the draw reproducible."),
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
                Param("items", "The pool to pick from.", required=True),
                Param("n", "How many to pick.", default="1"),
                Param("unique", "Pick without replacement.", default="`true`"),
                Param("weights", "Relative weights, one per item."),
                Param("seed", "Makes the pick reproducible."),
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
                Param("items", "The list to shuffle.", required=True),
                Param("seed", "Makes the shuffle reproducible."),
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
                Param("kind", "Alphabet or token type.", default="`urlsafe`"),
                Param("length", "Characters (or bytes for `kind: bytes`), 1..4096.", default="32"),
                Param("n", "How many tokens.", default="1"),
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
                Param("p", "Probability of `true`, 0..1.", default="0.5"),
                Param("n", "How many flips, 1..10000.", default="1"),
                Param("seed", "Makes the run reproducible."),
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
                Param("items", "The population.", required=True),
                Param("k", "Sample size, when not splitting into groups.", default="1"),
                Param("groups", "Number of groups, or their names."),
                Param("seed", "Makes the assignment reproducible."),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# geo_offline
# --------------------------------------------------------------------------- #

GEO = ToolDoc(
    name="geo_offline",
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
    related=(
        "[`datetime`](/docs/tools/datetime) `convert_tz` consumes the zone names this returns · "
        "[`convert`](/docs/tools/convert) to restate a distance in other units."
    ),
    examples=geo_offline.EXAMPLES,
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
                Param("place", "City, country or IANA zone name.", required=True),
                Param("all", "Return every matching zone instead of refusing an ambiguous one.", default="`false`"),
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
                Param("lat", "Latitude in decimal degrees.", required=True),
                Param("lon", "Longitude in decimal degrees.", required=True),
                Param("point", "Alternative to `lat`/`lon`: `{lat, lon}`, `[lat, lon]` or `\"lat,lon\"`."),
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
                Param("origin", "Origin: coordinates or a place name.", required=True),
                Param("destination", "Destination: coordinates or a place name.", required=True),
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
                Param("country", "ISO code, country name, or a common alias like `UK` or `USA`.", required=True),
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
                Param("zone", "An IANA zone name, e.g. `Asia/Kolkata`.", required=True),
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
    intro=(
        "Models hallucinate hashes with complete confidence — a plausible-looking 64 hex characters "
        "that is not the SHA-256 of anything. This tool computes them. Hashes, HMACs with constant-"
        "time comparison, CRC/Adler checksums, Base64 (standard and URL-safe), hex, URL and HTML "
        "escaping, JWT claim inspection and JSON parse/format all live here."
    ),
    when=(
        "Any hash, HMAC, checksum, Base64 or URL encoding — never write one from memory.",
        "Verifying a webhook signature or a download: pass `expected` and get a constant-time `matches` instead of eyeballing two hex strings.",
        "Inspecting a JWT's claims without a library (the signature is *not* verified).",
        "Validating, pretty-printing or minifying JSON, with the exact error position when it fails.",
    ),
    related=(
        "[`validate`](/docs/tools/validate) for checksum-verified identifiers and JSON Schema · "
        "[`text`](/docs/tools/text) for counting and transforming the text itself · "
        "[`random`](/docs/tools/random) for the secrets you are about to hash."
    ),
    examples=encode_mod.EXAMPLES,
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
                "hash reproducible. Pass `expected` — hex or Base64, any case, or a whole "
                "`sha256sum` line — and the response adds `matches`, compared in constant time; a "
                "mismatch is still `ok: true`, because it is an answer."
            ),
            params=(
                Param("text", "The input. Non-strings become compact sorted JSON."),
                Param("algo", "Digest algorithm.", default="`sha256`"),
                Param("expected", "A digest to verify against: hex or Base64, or a `<digest>  <file>` line."),
                Param("encoding", "Text encoding before hashing.", default="`utf-8`"),
                Param("bytes_base64", "Raw bytes as Base64, instead of `text`."),
                Param("bytes_hex", "Raw bytes as hex, instead of `text`."),
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
                Param("key", "The secret key.", required=True),
                Param("text", "The message."),
                Param("algo", "Digest algorithm.", default="`sha256`"),
                Param("expected", "A signature to compare against, in hex or Base64."),
                Param("key_base64", "Decode `key` from Base64 first.", default="`false`"),
            ),
        ),
        Mode(
            name="checksum",
            purpose="CRC32 and Adler-32 checksums.",
            description=(
                "Computes a non-cryptographic checksum — `crc32` or `adler32` — returning the value as "
                "an unsigned integer and as eight hex digits. For integrity against corruption, not "
                "against tampering; use `hash` or `hmac` for that. With `expected` the response adds "
                "`matches`."
            ),
            params=(
                Param("text", "The input."),
                Param("algo", "Checksum algorithm.", default="`crc32`"),
                Param("expected", "A value to verify against, as hex or the unsigned integer."),
                Param("bytes_hex", "Raw bytes as hex, instead of `text`."),
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
                Param("action", "Direction.", default="`encode`"),
                Param("text", "The text to encode, or the Base64 to decode.", required=True),
                Param("urlsafe", "Use the `-_` alphabet.", default="`false`"),
                Param("strip_padding", "Drop trailing `=` when encoding.", default="`false`"),
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
                Param("action", "Direction.", default="`encode`"),
                Param("text", "The text to encode, or the hex to decode.", required=True),
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
                Param("action", "Direction.", default="`encode`"),
                Param("text", "The string to encode or decode.", required=True),
                Param("plus", "Form encoding: spaces become `+`.", default="`false`"),
                Param("safe", "Characters left unescaped.", default="`/` for encode"),
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
                Param("action", "Direction.", default="`escape`"),
                Param("text", "The string to escape or unescape.", required=True),
                Param("quote", "Also escape quotes.", default="`true`"),
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
                Param("token", "The JWT.", required=True),
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
                Param("action", "What to do.", default="`parse`"),
                Param("text", "The JSON text, for `parse`."),
                Param("data", "The value to serialise, for `format` and `minify`."),
                Param("indent", "Indent width, for `format`.", default="2"),
                Param("sort_keys", "Sort object keys on output.", default="`false`"),
            ),
        ),
    ),
)


COLOR = ToolDoc(
    name="color",
    intro=(
        "Colour is arithmetic, not opinion, and a model still guesses it — `#F13A1A` becomes "
        "“rgb(240, 60, 30)”, a contrast ratio gets rounded to whatever sounds right, and a blend "
        "is described rather than computed. This tool converts between hex, RGB, HSL, HSV, CMYK "
        "and Lab, names the nearest CSS colour, scores WCAG contrast and proposes the fix, blends, "
        "builds hue harmonies, snaps to a palette, simulates the three dichromacies, greys a "
        "colour by five methods, and returns an actual PNG swatch so a multimodal agent can look "
        "at the result instead of imagining it."
    ),
    when=(
        "Any conversion between colour spaces — never write one from memory, and never guess whether `58, 26, 241` is RGB or HSL: it is refused until the scheme is named.",
        "Naming a colour or describing it in words: the nearest of the 148 CSS names by Lab ΔE, and a description from a fixed wording table.",
        "Checking text on a background for accessibility: the WCAG 2.x ratio, pass/fail per level and text size, and the smallest lightness change that passes.",
        "Design work — blends, complementary or triadic sets, snapping a colour to a brand palette, seeing it as a colour-blind reader would, or getting a swatch to look at.",
    ),
    related=(
        "[`numbers`](/docs/tools/numbers) for formatting the numbers a design token carries · "
        "[`encode`](/docs/tools/encode) when the swatch must travel as bytes · "
        "[`validate`](/docs/tools/validate) for checking the JSON a theme file is made of."
    ),
    examples=color_mod.EXAMPLES,
    modes=(
        Mode(
            name="convert",
            purpose="Every colour space from any one of them, alpha preserved.",
            description=(
                "Reads `value` as hex (`#F3A`, `#F13A1A`, `#F13A1A80`, with or without the `#`), "
                "`rgb()`/`rgba()`, `hsl()`, `hsv()`/`hsb()`, `cmyk()` or a CSS colour name and "
                "returns it as hex, RGB, HSL, HSV, naive CMYK (K = 1 − max(R, G, B), no ICC "
                "profile — the response says so) and CIELAB (D65), each with a CSS-style string. "
                "Alpha survives as `alpha` and in the hex and `rgba()` forms. `spaces` narrows "
                "the output. Three bare numbers with no scheme are refused with `needs.options` "
                "rather than read as RGB."
            ),
            params=(
                Param("value", "The colour: hex, `rgb()`, `hsl()`, `hsv()`/`hsb()`, `cmyk()` or a CSS name.", required=True),
                Param("spaces", "Which spaces to return: `hex`, `rgb`, `hsl`, `hsv` (or `hsb`), `cmyk`, `lab`.", default="all of them"),
                Param("decimals", "Decimals on the HSL/HSV/CMYK numbers.", default="`0`"),
            ),
        ),
        Mode(
            name="describe",
            purpose="The nearest CSS name and a description in fixed words.",
            description=(
                "Finds the closest of the 148 CSS Color Level 4 names by CIE76 ΔE in Lab and reports "
                "the distance, whether it is an exact hit, and the runner-up; duplicate names for the "
                "same colour (`gray`/`grey`, `aqua`/`cyan`) come back as `aliases`. The description "
                "— “vivid red-orange, medium-light” — is read off fixed HSL bands: sixteen hue words "
                "by degree, four saturation words and seven lightness words by percent, with greys, "
                "black and white handled first. No model is involved, so the same colour always gets "
                "the same words."
            ),
            params=(
                Param("value", "The colour to describe.", required=True),
            ),
        ),
        Mode(
            name="swatch",
            purpose="A real PNG of the colour, to look at.",
            description=(
                "Writes a solid square of the colour — or two colours side by side when `other` is "
                "given — as a PNG with the standard library only, and returns it as `png_base64` "
                "with `width`, `height`, `mime` and `bytes`. `size` is the side of each square in "
                "pixels, 16 to 256; anything else is `invalid_input`. A translucent colour keeps its "
                "alpha in the image."
            ),
            params=(
                Param("value", "The colour.", required=True),
                Param("other", "A second colour, drawn to the right of the first."),
                Param("size", "Side of each square in pixels, 16–256.", default="`64`"),
            ),
        ),
        Mode(
            name="contrast",
            purpose="WCAG 2.x contrast, pass/fail, and the fix.",
            description=(
                "Computes relative luminance per WCAG 2.x and the ratio (L1 + 0.05) / (L2 + 0.05) "
                "between `value` (the text) and `other` (the background), then reports pass/fail for "
                "AA and AAA at normal and large text sizes. `passes` is judged against normal text at "
                "`level`. When it fails, the foreground's HSL lightness is stepped 1% at a time in "
                "both directions and the first colour that passes — the smaller change, the higher "
                "ratio on a tie — comes back as `suggestion`; when no lightness reaches the target on "
                "that background, the response warns that the background has to change. Alpha is "
                "ignored, with a warning."
            ),
            params=(
                Param("value", "The foreground (text) colour.", required=True),
                Param("other", "The background colour.", required=True),
                Param("level", "Target level for `passes` and the suggestion: `AA` or `AAA`.", default="`AA`"),
            ),
        ),
        Mode(
            name="mix",
            purpose="Blend two colours by a ratio.",
            description=(
                "Interpolates from `value` to `other`; `ratio` is the share of `other`, so `0` is "
                "the first colour, `1` the second and `0.5` equal parts. `space: srgb` (the default, "
                "stated in `assumptions`) mixes the gamma-encoded channels the way CSS and most "
                "tools do; `space: lab` mixes in CIELAB for a perceptually even blend, clipping back "
                "into the sRGB gamut with a warning when it has to. Alpha is mixed the same way."
            ),
            params=(
                Param("value", "The first colour.", required=True),
                Param("other", "The second colour.", required=True),
                Param("ratio", "Share of `other`, 0 to 1.", default="`0.5`"),
                Param("space", "`srgb` or `lab`.", default="`srgb`"),
                Param("decimals", "Decimals on the HSL/HSV/CMYK numbers of the result.", default="`0`"),
            ),
        ),
        Mode(
            name="harmony",
            purpose="Complementary, analogous, triadic and split-complementary sets.",
            description=(
                "Rotates the HSL hue and keeps saturation and lightness: `complementary` (+180°), "
                "`analogous` (±30°), `triadic` (+120°, +240°) and `split_complementary` (+150°, "
                "+210°), returned as hex with the hues used. Leave `kind` out to get every scheme at "
                "once."
            ),
            params=(
                Param("value", "The base colour.", required=True),
                Param("kind", "`complementary`, `analogous`, `triadic` or `split_complementary`.", default="every scheme"),
            ),
        ),
        Mode(
            name="nearest",
            purpose="Snap a colour to a palette.",
            description=(
                "Ranks every entry of `palette` — hex, any scheme or a CSS name — by CIE76 ΔE in Lab "
                "from `value` and returns the winner with its distance and the runner-up. The same "
                "code path as `describe`, with your brand or design-system colours in place of the "
                "CSS names. Ties keep palette order."
            ),
            params=(
                Param("value", "The colour to snap.", required=True),
                Param("palette", "The candidate colours, as a list.", required=True),
            ),
        ),
        Mode(
            name="simulate",
            purpose="The colour as a dichromat sees it.",
            description=(
                "Projects the colour through the Viénot, Brettel & Mollon (1999) matrices on "
                "linearised sRGB for `deuteranopia`, `protanopia` and `tritanopia`, returning each as "
                "hex and RGB with the ΔE from the original. `kind` picks one; left out, all three "
                "come back and `assumptions` says so. `image: true` adds a strip — original, then "
                "each simulation — as a PNG. Pair it with `contrast` to check that a colour pair "
                "still separates for those readers."
            ),
            params=(
                Param("value", "The colour.", required=True),
                Param("kind", "`deuteranopia`, `protanopia`, `tritanopia` or `all`.", default="`all`"),
                Param("image", "Also return a PNG strip of original and simulated.", default="`false`"),
                Param("size", "Side of each square in the strip, 16–256.", default="`64`"),
            ),
        ),
        Mode(
            name="grayscale",
            purpose="The grey a colour becomes, by a named method.",
            description=(
                "Reduces the colour to grey by `rec709` (luma, Y′ = 0.2126 R′ + 0.7152 G′ + "
                "0.0722 B′ on gamma-encoded channels — what most image tools do, and the default, "
                "stated in `assumptions`), `rec601` (0.299/0.587/0.114), `lab` (the grey with the "
                "same L* lightness, perceptual), `average` (the mean of R, G, B) or `hsl` (HSL "
                "lightness). The grey comes back as hex, 0–255 and a percentage; `method: all` "
                "returns every one side by side. `ramp` adds an evenly spaced sRGB ramp of that many "
                "steps from the colour to its grey, and `image: true` returns a PNG strip of the ramp "
                "(or of colour and grey, or of colour and every grey with `all`)."
            ),
            params=(
                Param("value", "The colour.", required=True),
                Param("method", "`rec709`, `rec601`, `lab`, `average`, `hsl` or `all`.", default="`rec709`"),
                Param("ramp", "Steps from the colour to its grey, 2–64; needs a single method."),
                Param("image", "Also return a PNG strip.", default="`false`"),
                Param("size", "Side of each square in the strip, 16–256.", default="`64`"),
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
    FINANCE,
    TEXT,
    COLLECTIONS,
    VALIDATE,
    RANDOM,
    GEO,
    ENCODE,
    COLOR,
)


# --------------------------------------------------------------------------- #
# Network tools - served from /external/mcp, documented but never executed here
# --------------------------------------------------------------------------- #

WEATHER = ToolDoc(
    name="weather",
    network=True,
    intro=(
        "Live weather from [Open-Meteo](https://open-meteo.com), no key required. A place name is "
        "geocoded first, so `place: \"Kolkata\"` is enough; coordinates skip that step. Every answer "
        "is as-of the moment of the call — there is nothing deterministic about tomorrow's weather."
    ),
    when=(
        "Before stating today's conditions or a forecast: the model's training data has no weather in it.",
        "Historical daily weather back to 1940, for a date or a date range.",
        "Not for time zones — [`geo_offline`](/docs/tools/geo_offline) answers those without a network call.",
    ),
    related=(
        "[`geo`](/docs/tools/geo) to resolve a place first · [`datetime`](/docs/tools/datetime) to "
        "convert the timestamps it returns."
    ),
    modes=(
        Mode(
            name="current",
            purpose="Conditions at this moment, at a place or a pair of coordinates.",
            description=(
                "Conditions right now at the resolved location: temperature, apparent temperature, "
                "humidity, precipitation, wind speed and bearing, cloud cover, pressure, and whether "
                "it is daylight there."
            ),
            params=(
                Param("place", "A place name; geocoded before the lookup. Give this or `lat`/`lon`."),
                Param("lat", "Latitude, when you already have coordinates."),
                Param("lon", "Longitude, when you already have coordinates."),
                Param("units", "`metric` (°C, km/h) or `imperial` (°F, mph).", default="`metric`"),
                Param("tz", "IANA zone for the timestamps; `auto` uses the location's own zone.", default="`auto`"),
            ),
        ),
        Mode(
            name="forecast",
            purpose="Today plus up to sixteen days ahead, day by day.",
            description=(
                "Current conditions plus a daily forecast: highs and lows, precipitation and its "
                "probability, maximum wind, sunrise, sunset and UV index."
            ),
            params=(
                Param("place", "A place name; geocoded before the lookup. Give this or `lat`/`lon`."),
                Param("lat", "Latitude, when you already have coordinates."),
                Param("lon", "Longitude, when you already have coordinates."),
                Param("days", "How many days to return, 1..16.", default="`7`"),
                Param("units", "`metric` or `imperial`.", default="`metric`"),
                Param("tz", "IANA zone for the timestamps.", default="`auto`"),
            ),
        ),
        Mode(
            name="historical",
            purpose="What the weather actually was, on a past date or range.",
            description=(
                "Daily observations from the reanalysis archive, which reaches back to 1940. One day, "
                "or a range when `end_date` is given."
            ),
            params=(
                Param("place", "A place name; geocoded before the lookup. Give this or `lat`/`lon`."),
                Param("lat", "Latitude, when you already have coordinates."),
                Param("lon", "Longitude, when you already have coordinates."),
                Param("date", "First day, `YYYY-MM-DD`.", required=True),
                Param("end_date", "Last day of the range.", default="`date`"),
                Param("units", "`metric` or `imperial`.", default="`metric`"),
                Param("tz", "IANA zone for the timestamps.", default="`auto`"),
            ),
        ),
        Mode(
            name="summary",
            purpose="The forecast plus a ready-to-quote sentence.",
            description=(
                "Everything `forecast` returns, plus a one-sentence English summary of now and the "
                "coming week — for pasting straight into an answer."
            ),
            params=(
                Param("place", "A place name; geocoded before the lookup. Give this or `lat`/`lon`."),
                Param("lat", "Latitude, when you already have coordinates."),
                Param("lon", "Longitude, when you already have coordinates."),
                Param("days", "How many days the sentence covers, 1..16.", default="`7`"),
                Param("units", "`metric` or `imperial`.", default="`metric`"),
                Param("tz", "IANA zone for the timestamps.", default="`auto`"),
            ),
        ),
    ),
)

FX_RATE = ToolDoc(
    name="fx_rate",
    network=True,
    intro=(
        "European Central Bank reference rates, served by [Frankfurter](https://frankfurter.dev). "
        "Mid-market rates published once a business day — right for reporting and estimates, not a "
        "retail quote. The result includes `rates_table_for_convert`, which goes straight into "
        "[`convert`](/docs/tools/convert) as `rates`."
    ),
    when=(
        "Before converting money: [`convert`](/docs/tools/convert) deliberately refuses to invent a rate.",
        "Historical rates for a specific date, for invoices and restatements.",
        "Not for crypto or retail card rates — the ECB publishes neither.",
    ),
    related="[`convert`](/docs/tools/convert) does the arithmetic once you have the rates.",
    params=(
        Param("base", "The currency the rates are quoted against, ISO 4217.", default="`USD`"),
        Param("to", "One code, a list of codes, or a comma-separated string. Omitted returns every published rate."),
        Param("date", "`YYYY-MM-DD` for a historical rate; omitted uses the latest business day."),
        Param("amount", "Converted as well as quoted, when exactly one target currency is given."),
    ),
)

GEO_ONLINE = ToolDoc(
    name="geo",
    network=True,
    intro=(
        "Geocoding, reverse geocoding and road routing, from Open-Meteo's geocoder, OpenStreetMap "
        "Nominatim and the OSRM demo router. Use it when the offline dataset in "
        "[`geo_offline`](/docs/tools/geo_offline) cannot answer — that one covers cities and zones, "
        "not street addresses or driving times."
    ),
    when=(
        "Turning an address or a landmark into coordinates.",
        "Turning coordinates into a postal address.",
        "Driving distance and time, which is not the great-circle distance `geo_offline` returns.",
    ),
    related=(
        "[`geo_offline`](/docs/tools/geo_offline) for time zones and straight-line distance with no "
        "network call · [`weather`](/docs/tools/weather), which geocodes place names itself."
    ),
    modes=(
        Mode(
            name="geocode",
            purpose="A place name becomes coordinates, a country and a time zone.",
            description=(
                "Resolve a place name to coordinates, country, admin area, elevation and IANA time "
                "zone. The best match comes back as `best`, the rest as `results`."
            ),
            params=(
                Param("place", "The name, address or landmark to resolve.", required=True),
                Param("limit", "How many candidates to return.", default="`5`"),
            ),
        ),
        Mode(
            name="reverse",
            purpose="Coordinates become a postal address.",
            description=(
                "Resolve coordinates to a postal address: the formatted `display_name` plus the "
                "structured `address` components."
            ),
            params=(
                Param("lat", "Latitude.", required=True),
                Param("lon", "Longitude.", required=True),
            ),
        ),
        Mode(
            name="route",
            purpose="Driving distance and time by road, not as the crow flies.",
            description=(
                "Road distance and travel time between two points. Each endpoint may be a place name "
                "or a `{lat, lon}` object. The demo router has no live traffic, so read the duration "
                "as free-flow."
            ),
            params=(
                Param("origin", "Where the trip starts: a place name or `{lat, lon}`.", required=True),
                Param("destination", "Where it ends: a place name or `{lat, lon}`.", required=True),
                Param("profile", "`driving`, `walking` or `cycling`.", default="`driving`"),
            ),
        ),
    ),
)

URL_CHECK = ToolDoc(
    name="url_check",
    network=True,
    intro=(
        "Actually fetch a URL and report what happened: final status, the redirect chain, the URL you "
        "ended up at, content type, size and latency. A `HEAD` by default, falling back to `GET` when "
        "a server rejects it."
    ),
    when=(
        "Before telling a user a link works — models are confident about dead URLs.",
        "Checking where a shortened or tracking link actually lands.",
        "Not for reading the page: this reports on the response, not its body.",
    ),
    related="[`validate`](/docs/tools/validate) with `mode: url` checks the *syntax* of a URL without fetching it.",
    params=(
        Param("url", "The URL to fetch. A bare host is assumed to be `https://`.", required=True),
        Param("method", "`HEAD` or `GET`.", default="`HEAD`"),
    ),
)

#: The network-backed tools, served from `/external/mcp`.
EXTERNAL_CATALOGUE: tuple[ToolDoc, ...] = (WEATHER, FX_RATE, GEO_ONLINE, URL_CHECK)
