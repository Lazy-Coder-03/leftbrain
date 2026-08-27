# Working notes

How this repo is worked on. Read this first on a fresh machine; the GitHub issues are the
backlog and each one is written to be picked up cold.

## Conventions

- **Deterministic, ambiguity-refusing.** Every tool returns `{ok, result, assumptions[], warnings[]}`
  or `{ok:false, error, message, retryable, needs?, details?, hint?}`. When two readings are
  plausible (IST, 03/04/2025, ton, oz, annual vs monthly rate, GST-inclusive vs exclusive) return
  `needs.options`; never guess silently. Anything interpreted on the caller's behalf goes in
  `assumptions`. The failure codes are `contract.CODES` — that dict is the single list, and it
  also says which ones are worth retrying (`busy`, and any site that knows its own failure
  was transient and passes `retryable=True` at the raise). A traceback is a
  server-side log line, never part of a response unless `LEFTBRAIN_DEBUG` is set.
- **No judge-like tools.** Rules and arithmetic only; nothing that asks a model to grade.
- **One tool, not two that overlap.** A new capability that an existing mode already covers is a
  new mode at most — see how `finance.percent.split` reuses `numbers.allocate`.
- **No Python-keyword warts in the public API.** Ranges are `start`/`end` (datetime),
  `lower`/`upper` (math bounds), `origin`/`destination` (geo). No aliases, no back-compat shims.
- **Library first.** Pure functions in `src/leftbrain/core/*`, thin MCP wrappers in
  `mcp_server.py`, `Decimal`/`Fraction` for anything numeric.
- **No AI-assistant attribution anywhere** — no co-author trailers, no "generated with" lines, no
  mentions in docs, comments or package metadata.
- **No over-engineering.** Small modes, tests with real numbers, prose only where a human needs it.

## Adding a tool or a mode

The reference site generates itself from the MCP schema, the wrapper docstring and each module's
`EXAMPLES`; `tests/test_toolref.py` fails when they drift. The full how-to is
[`superpowers/notes/2026-08-26-docs-automation.md`](superpowers/notes/2026-08-26-docs-automation.md).
Checklist for a **tool**: core module with `MODES` + `EXAMPLES` (≥2 per mode, one that fails) →
wrapper with a `mode:` docstring line → `leftbrain/__init__.py` exports → `web/tools_list.TOOLS` →
`ToolDoc` in `web/toolref.py` + `CATALOGUE` → `scopes.CATALOGUE` (the per-key scope grid; wrap
the wrapper with `@enforce("name")`) → `MODULE_MODES` in `tests/test_toolref.py` → every
"N tools" string (README, landing, quickstart, views 404, tools_list docstring, the toolref count
test) → README table row → `web/docs/custom-agents.md` sample tool lists → CHANGELOG.

## Workflow

- Issue-driven: the design lives in the GitHub issue; build from it, tests first (write the failing
  test, watch it fail, then implement), commit referencing the issue (`closes #N`).
- Before claiming done: `pytest -q`, `ruff check src tests`, and for anything user-facing render it
  locally and look at it:
  `PYTHONPATH=src LEFTBRAIN_SECRET=x LEFTBRAIN_KEYS_DB=<tmp>.sqlite3 python -m leftbrain.serve --port 8791 --no-external --host 127.0.0.1`
- Files are CRLF. When patching with a script, preserve the file's newline style.
- `main` auto-deploys to production via Northflank CD — push only what is verified.

## Production

- https://leftbrain.idlesync.in on Northflank (team "LazyCoder03's Team", project `leftbrain`,
  Europe-West): combined service `leftbrain` from `main`, Postgres addon `keys`, secret group
  `keys-link` (GitHub OAuth + `LEFTBRAIN_SECRET` + base URL). Quota/rpm defaults are env vars on
  the **service itself** (`LEFTBRAIN_DEFAULT_DAILY_QUOTA=1000`, `LEFTBRAIN_DEFAULT_RPM=60`).
  Guide: [`deploy-northflank.md`](deploy-northflank.md).
- Verifying a deploy: static assets are content-stamped (`site.css?v=<asset_v>`); compare prod's
  stamp with `leftbrain.web.templates.env.globals["asset_v"]`. When no asset changed, poll a page
  that the change adds instead. A deploy lands ~90–120 s after the push.
- The Dockerfile `COPY`s only pyproject/README/LICENSE/CHANGELOG/src — a new file referenced from
  `pyproject.toml` must be added there or the build fails quietly.
- The first request to a large tool page after a deploy is slow (examples execute once, then cache).
- Smoke check after deploy: `/healthz`, `/` JSON `auth: keys`, the page you changed, and for key
  work a throwaway key from the dashboard → `/keys/me` → revoke.
- One-off DB operations: Northflank → service → Observe → Shell, `python -c "from leftbrain.keys
  import KeyStore; s=KeyStore(); …"` — the pod has the DSN, the addon stays private.

## Releases

`docs/releasing.md`. Keep a Changelog under `## [Unreleased]`; version in `pyproject.toml` and
`src/leftbrain/__init__.py`; tag `vX.Y.Z` triggers `release.yml` (build, attach dist, pre-release
while 0.x). Small changes ride the current release line rather than bumping.
