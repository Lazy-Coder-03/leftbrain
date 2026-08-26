# Changelog

All notable changes to leftbrain are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Demo endpoint: the 8 KB body cap now applies to chunked requests too, and deeply nested arguments return a contract `invalid_input` instead of a bare 500.
- Docker image build failed after the changelog was bundled into the wheel (`CHANGELOG.md` was not copied into the image).

### Changed

- Docs key picker shows the key name only (prefix only for unnamed keys).

- Docs examples are labelled Request/Response and colour-coded: blue blocks are what you
  send, green blocks are what comes back, and a neutral block marks a setup command.

## [0.1.0] - 2026-08-26

First public release, and a **pre-release** — the package ships as
`Development Status :: 3 - Alpha`. Tool names, parameters and the response envelope may
still change before 1.0, so pin an exact version if you build on it.

### Added

- **12 core tools**, offline and free of API keys: `math`, `datetime`, `scale`, `convert`,
  `holidays`, `numbers`, `text`, `collections`, `validate`, `random`, `geo_offline`,
  `encode`. Each takes a `mode`, so a client pays for twelve tool descriptions rather than
  a hundred.
- **4 external tools** over key-less public APIs: `weather` (Open-Meteo), `fx_rate` (ECB
  reference rates via Frankfurter), `geo` (geocode, reverse, driving route), `url_check`.
- **Optional file tools** for agents that cannot open files themselves: `pdf_text`,
  `pdf_info`, `image_info`, `image_to_base64`, `base64_to_file`, `file_info`, `read_text`,
  `list_dir`, restricted to the paths in `LEFTBRAIN_FILE_ROOTS`.
- **One response contract** for every tool:
  `{"ok": true, "result": …, "assumptions": [], "warnings": []}`. Failures return
  `ok: false` with an `error` code and a message; an ambiguous input returns `needs` with
  the concrete options instead of guessing (`IST`, `03/04/2025`, `ton`, `KB`). `result` is
  never null, and exact and decimal forms are returned together so nothing is re-rounded.
- **MCP servers over stdio**: `leftbrain`, `leftbrain-external`, `leftbrain-files`.
- **MCP over Streamable HTTP**: `leftbrain-serve` puts every tool set in one stateless
  process — `/mcp`, `/external/mcp`, optionally `/files/mcp`, plus `GET /healthz` and a
  JSON service description at `/`. Dockerfile and compose file included.
- **Use from Python without MCP**: `import leftbrain as lb` gives `lb.math_tool(...)` and
  friends, and `lb.TOOLS` maps every tool name to its function.
- **Hosted service** at <https://leftbrain.idlesync.in>:
  - GitHub sign-in; keys belong to the account's verified primary email.
  - API keys prefixed `lblz_`, up to 3 per account, 5,000 requests/day and 60/minute, with
    quota headers on every response and `429` plus `Retry-After` when a limit is reached.
  - Dashboard: create a named key (shown once), reveal an existing one again, revoke, and
    see today's usage.
  - Docs with Windows PowerShell / macOS / Linux tabs — quickstart, MCP client setup, a
    custom-agents guide, and a copy-paste prompt that configures any coding agent. A
    signed-in reader's own key is filled into every example on the page.
  - A per-tool reference generated from the MCP servers themselves: parameters come from
    the published schemas and every example is executed while the page is built, so the
    docs cannot drift from the code.
  - A key-less live demo on the landing page (`numbers`, `convert`, `datetime`, `text`).
- **Per-user key store** on SQLite or Postgres, resolved from `LEFTBRAIN_KEYS_URL`,
  `DATABASE_URL` or `LEFTBRAIN_KEYS_DB`, with a `leftbrain-keys` admin CLI (create, list,
  enable, disable, revoke, set limits, usage, stats).
- Deployment guide for a free Northflank sandbox in `docs/deploy-northflank.md`.

### Changed

- Range parameters use the name their domain already uses rather than `from`/`to`, which
  collided with the Python keyword and forced a `from_` spelling on the wire:
  `datetime diff`, `business_days` and `cron_next` take `start`/`end`; `math integrate`
  takes `lower`/`upper`; `math limit` takes `point`; `math convert_form` takes `form`;
  `geo_offline distance` and external `geo route` take `origin`/`destination`. The `from`,
  `from_` and bare `to` spellings are gone. `from_unit`/`to_unit`, `from_qty`/`to_qty` and
  `from_tz`/`to_tz` are unchanged.
- The MCP wrappers accept every parameter their core tool supports, so a tool is not less
  capable over MCP than it is from Python.

### Fixed

- The GitHub OAuth callback handles malformed or unexpected responses instead of failing
  with a server error.
- Docs `:::os` tab blocks are parsed fence-aware, so a `:::` inside a code sample no longer
  ends the block early.
- Static assets are stamped with a content hash, so a stylesheet or script change reaches
  browsers without waiting for a version bump.

### Security

- Security headers on every response: `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY` and a
  Content-Security-Policy with `frame-ancestors 'none'`.
- `X-Forwarded-For` is counted from the right using `LEFTBRAIN_TRUSTED_PROXY_HOPS`
  (default 1), and `X-Real-IP` / `CF-Connecting-IP` are ignored — a caller cannot forge the
  address a rate limit is keyed on.
- The demo endpoint runs behind an allow-list of tools, modes and argument names, an 8 KiB
  body cap and per-argument size caps, and never returns a traceback.
- Keys are authenticated by comparing a SHA-256 hash. The revealable copy is
  Fernet-encrypted under a key derived from `LEFTBRAIN_SECRET`; leave that unset and only
  the hash is stored.
- Anonymous signup is closed unless `LEFTBRAIN_OPEN_SIGNUP=1`. Sessions use a signed
  cookie, every mutation is CSRF-checked, and any page carrying a key is sent
  `Cache-Control: no-store`.

[Unreleased]: https://github.com/Lazy-Coder-03/leftbrain/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Lazy-Coder-03/leftbrain/releases/tag/v0.1.0
