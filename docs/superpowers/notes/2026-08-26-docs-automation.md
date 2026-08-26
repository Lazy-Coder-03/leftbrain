# The tool reference generates itself (2026-08-26)

`/docs/tools/<name>` used to be a 3,143-line hand-written catalogue: every parameter's JSON type,
required flag and default typed out by hand, every example carrying its own `ok`/not-`ok`
expectation. It drifted. Seventeen parameters the core modules read were missing from the MCP
wrapper signatures, so the docs described arguments an MCP client could not pass, and the tests had
no way to notice.

The reference is now derived. This note says where each fact on a page comes from and what to touch
when you add something.

## Three sources, one page

| What appears on the page | Where it comes from |
| --- | --- |
| Parameter name, JSON type, schema-required, schema default | `tool.parameters` — the input schema the MCP server publishes |
| The list of modes, and a mode's one-liner | the wrapper docstring (`tool.description`) |
| Worked calls and their responses | `EXAMPLES` in the core module, executed at build time |
| "Fails when", for missing/mistyped arguments | the generator, probing the tool itself |
| Prose: intro, "when to use", mode descriptions, parameter meanings | the catalogue in `toolref.py` |

Only the last row is written by hand. Everything above it is read from the running server, so a
rename in `mcp_server.py` is a rename in the docs, and a rename the catalogue does not follow is a
test failure rather than a stale page.

### The schema

`toolref.specs()` builds a `ToolSpec` per tool from `server._tool_manager.list_tools()` — the
registry behind the async `server.list_tools()`, read directly so the docs build stays synchronous.
Each spec carries the input schema, the cleaned-up docstring, the wrapper function, and the pydantic
`arg_model` the server validates against. `tests/test_toolref.py` asserts the registry and the
published `list_tools()` agree, so an SDK change cannot silently desynchronise the page from the
wire.

`rows(tool, params, where)` turns a mode's `Param(...)` list into table rows: the name and the human
meaning come from the catalogue, the type from `_type_name(prop)`, and required from the schema's
`required` list unioned with the mode's own `required=True` marks. The schema is flat across every
mode of a tool, so it cannot know that `expr` is required in `eval`; that stays a hand mark. A
parameter the tool does not accept raises `KeyError` at build time.

`default` prefers the schema's own default and falls back to `Param(default=...)`, which records the
value the *core* function falls back to when the wrapper's default is `None` — `precision` is `15`
inside `mathx`, but `null` in the schema, and the table should say 15.

### The docstring

`docstring_modes(name)` parses the wrapper docstring into an ordered list of modes plus, where the
docstring writes `- mode (args) - prose`, a one-liner. Two rules matter: the `mode:` declaration may
wrap onto the next line (it continues while the accumulated text ends with `|`), and splitting on
`|` is bracket-aware so `json (parse|format|minify)` stays one item. Only the ` - ` form yields
prose; a `- mode: expr, var` bullet is an argument list, not a description, and is ignored.

`purpose_of()` prefers the catalogue's `purpose` and falls back to the docstring one-liner. Modes
where the docstring says more keep no `purpose` at all — `datetime.now` is the example.

### The examples

Each core module ends with `EXAMPLES: dict[str, list[dict]]`, mode → list of
`{"caption", "args", "volatile"?}`. There is no expected outcome in a fixture: the generator runs
every one and files it under "Examples" or "Fails when" by what came back. `volatile: True` marks
anything whose output embeds the current instant and prints the "captured when this page was built"
note.

`call_tool(name, args)` takes the same path the server does — reject unknown argument names,
validate against `arg_model`, then call the wrapper. A validation failure is rendered as the MCP
error result a client actually receives (`isError`, with `Error executing tool X: …` text), not as
the leftbrain contract, because that is what the wire carries.

### The derived failures

For each mode the generator calls the tool with nothing but the mode, then, for each
schema-required parameter, with a wrong-typed value, and then with each required parameter removed
from the first working example. Anything that actually failed and whose message is not already on
the page joins "Fails when", up to `MAX_DERIVED_FAILURES`. That is why 62 hand-written
"`x` is required" fixtures could be deleted while the number of documented failures went *up*.

### The network tools

`weather`, `fx_rate`, `geo` and `url_check` live in `EXTERNAL_CATALOGUE` with `network=True`. Their
parameter tables and mode lists are derived exactly like the others; nothing is executed, and the
page says so. `fx_rate` and `url_check` have no modes at all, so they render one `## Parameters`
table from `ToolDoc.params` instead of a mode index.

## How to add something

**A tool.** Write the wrapper in `mcp_server.py` with a docstring that has a `mode:` line, add
`MODES` to the core module, add an entry to `tools_list.TOOLS`, add a `ToolDoc` to `CATALOGUE`, and
add `EXAMPLES` to the core module. Point `ToolDoc.examples` at it.

**A mode.** Add it to the module's `MODES`, name it on the wrapper docstring's `mode:` line, add a
`Mode(...)` with a description, and add at least two entries to `EXAMPLES` — one that works and one
that fails. If nothing can make the mode fail, set `never_fails="…why…"` and add it to
`NO_FAILURE_MODES` in the test with a reason.

**A parameter.** Add it to the wrapper signature *and* to its `_clean(dict(...))` call, then add
`Param("name", "what it means")` to every mode that accepts it. The type and default look after
themselves.

**An example.** Append `{"caption": …, "args": …}` to the core module's `EXAMPLES`. Say nothing
about whether it succeeds.

## What fails the build

- A mode in `MODES` that the docstring never mentions, or a docstring mode that is not implemented.
- A mode in `MODES` with no `Mode(...)` entry, or the reverse.
- A published parameter no mode documents, or a documented parameter the tool does not accept.
- A mode with no example, with fewer than two, with no example that succeeds, or — outside
  `NO_FAILURE_MODES` — none that fails.
- A mode with no description or no one-liner for the index.
- A "volatile" mode with an unmarked working example, or a stable mode where every example is marked.
- A retired parameter name (`from`, `from_`, bare `to`) in any table or fixture.
