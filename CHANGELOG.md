# Changelog

All notable changes to leftbrain are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `collections`: every record input (`items`, `a`/`b`, `data`) also accepts CSV text — the
  delimiter is sniffed, the header row detected (first row with no numeric, date or boolean cell;
  `has_header` overrides), each field typed as number (exact `Decimal`, via `numbers.parse`),
  ISO date, boolean or text, and blank rows and `N/A`/`-`/`null` cells skipped and counted, all
  stated in `assumptions`; above 5,000 rows the call is refused. New table modes over the same
  input: `filter` (`where` predicates with the `validate.assert` vocabulary, compared in the
  field's type), `pivot` (`by` × `pivot_columns`, one aggregate of `column`, row and grand
  totals, empty cells `null`), `running` (cumulative total, restarting per `by`), `outliers`
  (Tukey hinges, 1.5×IQR, fences reported, at least 4 values), `summarize` (count/nulls/sum/avg/
  min/max/median for every numeric field, distinct counts for text, range for dates) and `to_csv`
  (`delimiter`, chosen `columns`). A lone numeric field is assumed for `running`/`outliers` and
  said so; several are `ambiguous`. `decimals` rounds computed values only; row-shaped results
  echo at most 500 rows with a warning, except `to_csv`.
- `convert` `fuel_economy`: `mpg_us` / `mpg_uk` / `km_per_l` / `l_per_100km` with exact
  constants; a bare `mpg` is `ambiguous` (US or imperial gallon), and any conversion that crosses
  L/100 km states the inverse relation in `assumptions`.
- `convert` `cooking`: cups, tbsp, tsp, ml, fl oz ↔ g, kg, oz, lb. Mass ↔ volume needs
  `ingredient`, looked up in a built-in density table (18 staples; the grams-per-cup used are
  stated); a missing or unknown ingredient returns the table as `needs.options`. `cup` selects the
  US (240 ml, default and declared), metric, UK or Australian (20 ml tablespoon) system.
- `convert` `sizes`: `category=shoe` converts US men / US women / UK / EU / cm on a generic
  adult chart, snapping to the nearest half size with a warning; `category=clothing` maps XS–XXL
  to chest and waist cm bands for a chart chosen by `region` (`us` inch-based, `eu` EN 13402-3)
  and `gender`, both required. Every result warns that sizes are approximate and names the chart.
- `datetime` `free_slots`: common free slots for two or more participants in different time
  zones, from weekly windows (`09:00`–`17:00` on `mon`…`fri`) or one-off local ranges, intersected
  in UTC through `zoneinfo`. Each slot is shown in every participant's local time and in UTC,
  `per_day` totals the overlap per date, a window spanning a DST change is expanded to its real
  length with a note in `assumptions`, and no common time is `ok: true` with `slots: []` and a
  warning naming who never overlaps.
- `validate` `cidr`: membership (`network` + `value` → `contains`, for an address or a smaller
  block) and overlap (a list of networks → every pair with `equal` / `a_contains_b` /
  `b_contains_a` / `disjoint`), plus a block's size, usable hosts, bounds and masks. Host bits
  set are read as the network and recorded in `assumptions`.
- `validate` `id` with `kind=isbn` returns both `isbn10` and `isbn13` for a valid number (a 979
  book has no ISBN-10 form).
- `encode` `hash` and `checksum` take `expected` and answer `matches` — compared in constant
  time against the hex, Base64 (or, for checksums, decimal) form, case- and whitespace-
  insensitive, with a `sha256sum`-style `<digest>  <file>` line accepted as-is. A mismatch is
  `ok: true, matches: false`: an answer, not an error. `hmac` shares the same comparison.
- `files` `file_hash`: sha256 (default) / sha1 / md5 / blake2b / crc32 and the other hashlib
  algorithms, streamed in 1 MiB chunks so a file's size is bounded only by `LEFTBRAIN_FILE_ROOTS`,
  with `bytes`, the algorithm used, and `expected` → `matches` for verifying a download.
- `numbers` `semver`: compare or sort version strings as versions (`1.10` > `1.9`), with
  SemVer 2.0 pre-release precedence, build metadata carried but ignored for ordering, and a
  leading `v` / missing minor or patch tolerated and recorded in `assumptions`.
- `text` `similarity`: Levenshtein distance and a 0–1 ratio for a pair, or `text` + `items` to
  rank candidates and return the best match with its index — mapping typed input onto a menu,
  spotting near-duplicate names. Case-folded and whitespace-normalised by default, both stated.
- `datetime` `now` takes a list as `tz` — zone names or `{tz, label}` objects — and answers with
  the shared instant as `utc` plus one full per-zone entry under `zones`, each carrying its
  `label` back. `convert_tz` accepts the same `{tz, label}` entries in `to_tz`. One round-trip
  for "what time is it in each of our offices right now"; the single-`tz` form is unchanged.
- `leftbrain-keys set --all --daily N [--rpm N] [--from-daily N]` changes limits on every key —
  or, with `--from-daily`, only on keys still at an old default — so a moved default can be
  migrated without a database shell or touching keys set by hand. Prints how many keys changed.
- **`finance` tool** (13th core tool): `emi` (instalment, totals and amortisation schedule that
  reconciles to zero), `compound` (future value with optional per-period contributions and the
  effective annual rate), `cagr`, `npv_irr` (IRR by bisection), `gst` (inclusive/exclusive split
  into CGST/SGST or IGST, rounding difference reported) and `percent` (change vs percentage
  points, stacked vs additive discounts, exact bill splits). The rate's period and whether an
  amount is GST-inclusive are required, not guessed.

- Dashboard **Delete** for a revoked or expired key: removes the row and its usage history for
  good (with a confirm). A key that still works must be revoked first; deletion is owner- and
  CSRF-checked like the other key routes.

### Fixed

- Dashboard: keys issued before the server could show keys again (including the old
  `POST /keys/signup` rows, still named `self-serve signup`) are marked **legacy** and explained
  on the row instead of the bare "created before reveal was enabled". They no longer hold one of
  the owner's active-key slots, so a first sign-in that inherits three of them can still create a
  key. `/keys/me` output is unchanged.
- Demo endpoint: the 8 KB body cap now applies to chunked requests too, and deeply nested arguments return a contract `invalid_input` instead of a bare 500.
- Docker image build failed after the changelog was bundled into the wheel (`CHANGELOG.md` was not copied into the image).

### Changed

- Free tier is **1,000 calls per key per day** (was 5,000); `LEFTBRAIN_DEFAULT_DAILY_QUOTA` still overrides it. Every place the site quotes the limit — landing, sign-in, quickstart, the demo's 429 — now reads the configured value instead of a hard-coded number.
- Site shows loading skeletons on navigation, form submits and demo runs; the colour legend on docs pages was dropped.
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
- **Key expiry.** Keys are created with a lifetime — 30, 90 (default) or 365 days, or never
  (the dashboard and CLI warn that a key which never expires is a liability if leaked).
  An expired key is refused with `403 {"error": "expired", "message": "key expired on <date>; create a new one at /dashboard"}`,
  drops out of the 3-active cap, and can no longer be shown again. The dashboard shows
  when each key expires and flags one within 7 days; the docs key picker skips expired
  keys and reminds you when the selected one is about to expire. `GET /keys/me` reports
  `expires_at` and `expired`; `leftbrain-keys create --expires 90d|never` and
  `set <prefix> --expires …` manage it from the CLI. Existing keys are migrated with no
  expiry.
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
