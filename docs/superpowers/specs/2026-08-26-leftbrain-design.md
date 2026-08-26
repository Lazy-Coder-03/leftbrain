# leftbrain — design

Date: 2026-08-26 · Status: implemented (v0.1.0)

## Problem

Language models are unreliable at a well-defined class of tasks that have exact answers: arithmetic, calendar and timezone work, unit conversion, proportional scaling, counting, ordering, set logic, checksum validation, randomness, hashing. Existing MCP servers cover these one at a time (a time server, a calculator, a weather server), each a separate process with its own conventions, and none refuses ambiguous input.

## Goal

One small, well-described, deterministic tool set that any agent — MCP client or plain Python loop — can call before stating a number, date, conversion, count, ordering or validation result.

## Non-goals

- LLM-as-judge or any tool whose output depends on a model call. (`validate.assert` covers the "objective scoring" need with rules.)
- Anything requiring API keys in the core.
- Replacing what hosted agents already do natively (reading PDFs/images) — that lives in the opt-in `files` set.

## Architecture

```
leftbrain/
  contract.py        envelope: ok/result/assumptions/warnings, Ambiguous, ToolError, @tool
  core/              pure functions, one module per tool, no I/O, no network
  mcp_server.py      stdio MCP server exposing core (12 tools)
  external/          weather, fx_rate, geo, url_check  (httpx; own stdio server)
  files/             pdf_text, image_to_base64, ...     (pypdf, Pillow; own stdio server; root allowlist)
  serve.py           Starlette app mounting all sets for Streamable HTTP; API-key middleware
```

Library-first: `core/*` has no dependency on `mcp`. The MCP layers are thin adapters that pass named parameters through and provide *when-to-use* descriptions.

## The contract

```
{"ok": true, "result": <never null>, "steps"?: [...], "assumptions": [...], "warnings": [...]}
{"ok": false, "error": "ambiguous", "message": ..., "needs": {"field": ..., "options": [...]}}
{"ok": false, "error": "invalid_input" | "unsupported" | "timeout" | "needs_rates" | "forbidden" | "internal", "message": ...}
```

Rules: refuse ambiguity (return `needs`), never guess silently (record in `assumptions`), fail loudly (no `null`).

## Tool inventory and key decisions

| Tool | Decision |
|---|---|
| `math` | SymPy for exact + symbolic + calculus. Parser sandbox: pre-filter regex (no `__`, attribute access, quotes, keywords), `parse_expr` with a whitelisted global dict and `__builtins__={}`, `AppliedUndef` rejection, thread timeout (20 s). `angle` is required whenever trig appears; `°` in the expression is handled directly. Results carry exact, decimal and LaTeX forms. |
| `datetime` | IANA zones only; abbreviation table returns options when ambiguous (`IST`, `CST`, `EST`), resolves with an assumption when unique (`JST`). Numeric dates with both fields ≤ 12 need a `locale`. Relative phrases resolve against `ref_date`. Elapsed-time arithmetic is done in UTC to respect DST. Business days use the `holidays` dataset. Cron and RRULE expansion implemented deterministically. |
| `scale` | Exact `Fraction` arithmetic; unit-aware via pint; `mode=inverse` for inverse proportion; per-entity `integer` flag rounds up. |
| `convert` | pint with an alias table; ambiguous units (`ton`, `gallon`, `oz`, `cup`, `KB`, `calorie`…) refused unless `assume="common"`. Temperature absolute vs. delta explicit. Currency requires caller-supplied rates (from `external.fx_rate`) — the core stays offline. |
| `numbers` | `Decimal` throughout. Rounding mode defaults to half-up with a note that Python's `round` is banker's. Indian grouping and lakh/crore words. `allocate` uses largest-remainder so shares sum exactly. |
| `validate` | `assert` = rule list over a JSON path → per-rule pass/fail, weighted score. Checksums implemented: Luhn, IBAN mod-97, GSTIN, PAN structure, Aadhaar (Verhoeff), ISBN-10/13, EAN/UPC, VIN. `sql_parse` via sqlglot flags writes and unbounded `DELETE`/`UPDATE`. |
| `geo_offline` | Built on tzdata's `zone1970.tab`/`zone.tab`/`iso3166.tab` plus a curated city alias table. Coordinates are zone reference cities (approximate; stated in assumptions). |
| `random` | `secrets.SystemRandom` unless seeded; tokens are never seeded. UUID v7 implemented locally. |
| `encode` | stdlib hashlib/hmac/zlib/base64. `jwt_decode` is explicitly unverified. |
| dropped | `judge` (non-deterministic), `convert.base`, `random.dice`, `text.slugify/case`, `hash` as a top-level tool (folded into `encode`). |

## HTTP publishing

`leftbrain-serve` mounts core at `/mcp`, external at `/external/mcp`, files at `/files/mcp` (opt-in). Stateless by default. TLS terminated by the host. Optional `LEFTBRAIN_API_KEY` bearer check as pure ASGI middleware. Dockerfile runs as non-root with a healthcheck.

## Testing

- `tests/` — pytest with concrete expected values (e.g. DST edge 2026-03-08 IST→EST, GSTIN checksum, allocate 100/3).
- `scripts/smoke.py` — every tool end to end, prints the envelope.
- `scripts/mcp_client_check.py` — spawns each server over stdio through the official client and calls a tool.
- `scripts/files_check.py` — generated PDF/image fixtures, path-escape rejection.
- CI: ubuntu + windows × Python 3.11–3.13, ruff + pytest + smoke.

## Open items

- TypeScript port sharing the tool names and contract.
- OCR for scanned PDFs (Tesseract optional dependency).
- Boundary-accurate `tz_for_coords` (timezonefinder) as an optional extra.
