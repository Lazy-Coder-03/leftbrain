# Changelog

All notable changes to leftbrain are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Docs: connecting Claude on the web, and a ChatGPT section.** Which clients can carry a bearer
  key and which cannot, and why: a config file can set a header, a browser connector dialog
  cannot. ChatGPT gets its own section covering the Plugins rename, the developer-mode
  prerequisite and where the toggle lives per plan, and why no configuration works directly —
  OpenAI's documentation states ChatGPT cannot present custom API keys, and expects OAuth 2.1
  against the MCP authorization spec. Both blocked clients get something to do rather than a
  shrug: the desktop app for Claude, a header-injecting proxy for ChatGPT, with a Cloudflare
  Worker to copy and the trade stated — the proxy's URL becomes the credential.

### Changed

- **The per-key tool scope is a tree.** It was a three-column grid of blocks whose columns ran
  to different heights, so a tool's modes wrapped into a shape unrelated to the tool above it.
  Each tool is now one full-width row with a disclosure triangle, a `picked/total` count, and
  its modes as collapsible children under a guide line. A tool with some but not all of its
  modes shows an indeterminate checkbox; unticking the last mode turns the tool off and
  ticking one turns it back on. The header offers **All**, **None** and **Expand all**. Counts
  are rendered by the server, so the scope is readable with JavaScript off.

### Fixed

- **The loading skeleton now covers every page.** `site.js` looked for `.doc`, then `<main>`,
  then `document.body`, so only the docs pages showed it correctly: the dashboard got the
  markup with no CSS to position or dim it, and the landing, login and error pages prepended
  skeleton lines above the nav. Every template now renders inside one `#page` wrapper, which
  the script targets and the stylesheet positions and dims. The docs pages still dim only the
  article, so the sidebar stays readable.
- The site footer linked to the repository, which is private. It links to the tool reference
  instead.

## [0.3.0] - 2026-08-27

### Added

- **JSON tool reference.** `/docs/tools` and `/docs/tools/<name>` serve JSON to a client and
  HTML to a browser, by content negotiation on `Accept`. The index lists all eighteen tools
  with their modes, the version, and the contract's error codes; a tool gives each mode's
  purpose, description, parameters (type, default, required) and example arguments. Neither
  needs a key. `/` advertises the endpoint as `tools`. `tools/list` over `/mcp` is unchanged
  and remains the authenticated, scope-aware route.

- **Every response says what it cost** (#28 §6): a `meta` block with `tool`, `mode`,
  `latency_ms` (wall time in the server), `compute_ms` (time in the engine), `version`,
  `truncated` — lifted out of the result so a caller reading a list knows it is not the whole
  list — plus `request_id` and `quota` when the server has them. `X-Request-Id` and
  `X-Leftbrain-Latency-Ms` carry the first two as headers; a caller-supplied `X-Request-Id` is
  kept so one id spans both sides of a trace. `meta` never affects `ok`.
- **A 15-second compute ceiling** (#28 §1 step 3). `math`, `text`, `validate`, `collections`
  and `numbers` calls run in a worker process that is terminated when the deadline passes. A
  call that reaches it returns `timeout` with `stopped: "worker_terminated"`, the limit and
  the elapsed time; a caller-supplied `timeout` is clamped to the ceiling; a call that cannot
  get a worker within the queue deadline returns the retryable `busy`. Workers carry
  `RLIMIT_CPU` and `RLIMIT_AS`, use `forkserver` on POSIX, and are recycled every 200 calls.
  Configured with `LEFTBRAIN_COMPUTE_TIMEOUT`, `LEFTBRAIN_QUEUE_TIMEOUT`,
  `LEFTBRAIN_MAX_INFLIGHT` and friends — see the README for the full table and the ingress
  relationship. Needs `pebble`, added to the `server`, `all` and `dev` extras; without it the
  server logs that isolation is off and runs in-process. The library always runs in-process.
- **`retryable` on every failure, and four codes that say what was hit** (#28 §1, §4): the
  envelope's failure half is now `{ok: false, error, message, details?, retryable, hint?}`.
  `retryable` is `false` for everything except `busy` and `internal` — a client that reads only
  `ok: false` retries, and an identical retry of a call that hit a limit multiplies the load that
  caused it. New codes alongside `invalid_input` / `ambiguous` / `unsupported` / `timeout` /
  `forbidden` / `internal`: `too_large` (a pre-check refused it before any work started),
  `resource_exhausted` (a memory or CPU limit rather than the clock) and `busy` (saturated;
  nothing was computed). `contract.CODES` is the one list, and `TooLarge`, `ResourceExhausted`
  and `Busy` are raisable from a tool. Only `busy` is retryable by default: `internal` is the
  catch-all for an exception no mode anticipated, and most of those are a deterministic
  consequence of the input, so a site that knows its own failure was transient passes
  `retryable=True` at the raise instead.

- **Size caps: an input whose answer would be enormous is refused before it is computed**
  (#28 §2e/§2f/§2g). Each check costs microseconds and returns `too_large`, naming the limit
  and the parameter to change:
  - `math`: the expression is walked as an AST and the answer's size estimated in log10 space
    before anything is evaluated — SymPy computes `Integer ** Integer` while *parsing*, so
    `9^9^9^9`, `2^(2^100)`, `2^1000000`, `factorial(factorial(20))`, `gamma(10**10)` and
    `exp(10^20)` never start. The bound is `sys.get_int_max_str_digits()` (4 300 by default),
    which is the point beyond which CPython cannot render the integer at all. `precision` is
    capped at 5 000 digits and `series` `order` at 50 terms.
  - `numbers`: `sequence` caps the largest term at 1 000 digits (`geometric` ratio 2, n 10 000
    ended at 2^10000 and returned 15 MB); `allocate` caps `parts` at 10 000 (1 000 000 returned
    116 MB).
  - `text`: `regex_replace` refuses an output over 200 000 characters; `diff` caps each side at
    10 000 lines, words or characters (difflib is quadratic — 100 000 lines never returned).
  - `collections`: `pivot` refuses more than 200 distinct pivot columns.
  - `datetime`: `business_days` caps the range at 3 660 days; `now` and `convert_tz` cap the zone
    list at 50.
  - `holidays`: `next` caps `n` at 100.
  - A global 256 KB ceiling on any successful response backs all of them up, configurable with
    `LEFTBRAIN_MAX_RESPONSE_BYTES`. A mode with a knob should refuse earlier and say which knob;
    this only catches what slipped past one.

### Fixed

- **Extreme magnitudes are rendered rather than lost or refused.** Four behaviours for one
  class of input: `numbers.parse "1e400"` was `invalid_input` (`Decimal` holds it exactly —
  the parser's suffix regex simply never let it try); the same value as a JSON *number*
  arrived as `inf` and came back as a successful `"Infinity"` with nothing said;
  `convert "1e-400"` leaked `OverflowError: int too large to convert to float`; and
  `finance.compound principal=1e300` was `internal` plus a bare `InvalidOperation`.
  Now: scientific notation parses however large, a value that arrived as infinity says its
  magnitude was already lost before the tool saw it, and `convert` computes exactly at any
  size — only `value`, which is a JSON number, saturates to `Infinity`/`0`, with the exact
  answer in the new `value_exact` and the loss named in `warnings`. An ordinary conversion
  gains neither field. An amount too big to be money is `too_large`, not a crash.

- **The small list** (#28 §3.13): sixteen findings that were each wrong or silent rather than
  dangerous. `random.sample` with more groups than items left some empty and said nothing;
  `random.float decimals=100` ignored the parameter (a binary float has ~17 significant digits,
  so it is now 0..15 and anything larger is refused); `encode.base64 urlsafe=true` decoded
  standard-alphabet text anyway; `encode.url` left a malformed `%zz` escape untouched with no
  note; `validate.url` accepted embedded credentials and punycode/Cyrillic look-alike hosts
  silently, and now warns about both; `validate.ip` rejected the scoped IPv6 form
  `fe80::1%eth0` (RFC 4007), which is now valid with the `zone` reported;
  `validate.sql_parse` counted a trailing comment as a third statement;
  `numbers.parse` rejected the Unicode minus `−5`, which is what copy-paste from a document
  produces; `collections.find_duplicates` with `case_insensitive` did not also trim, so
  `"a@x.com "` and `"A@x.com"` were different; `collections.aggregate` had no `median`
  although `math.stats` did; `datetime.recurrence` refused `every other tuesday`, the short
  form people actually write; `holidays.list` for a year the calendar has no data for looked
  identical to a year with no holidays, and years past 2075 are now marked as estimated;
  `text.sort` sorted `éclair` after `Zebra` without saying it does no locale collation; and
  `text.count` now reports `graphemes` — what a reader sees, so a ZWJ family emoji is one
  character rather than five — and warns when text carries bidi controls or zero-width
  characters, which are invisible, count towards length, and are how a filename is made to
  read backwards.
- **Unknown parameters are refused instead of silently dropped** (#28 §2a). Every tool kept the
  keys it recognised and discarded the rest, so `datetime.age dob=… ref_date=…` returned the age
  as of *today* (the parameter is `on`) and `math.plot_points start=… end=…` plotted the default
  range — an answer computed from defaults after the caller's arguments were thrown away, with
  nothing in the response to tell it from a right one. Each of the fourteen core tools now
  declares `MODE_PARAMS`, what every one of its modes reads, and a call carrying anything else is
  `invalid_input` naming the mode, listing what it accepts and suggesting the closest match.
  `tests/test_mode_params.py` derives the same map from the source and fails if the two drift.
  Retired names (`from`/`to`) now say what replaced them instead of being ignored. `overlap` also
  honours a per-interval `tz`, which it used to drop — two windows 12½ hours apart were compared
  as wall clocks and reported as overlapping by 8 hours.
- **Mutually exclusive parameters say which one won** (#28 §2b): `numbers.round significant=2
  decimals=5` returned 120 with no word about `decimals`, and `finance.emi months=12 years=5`
  gave a one-year loan to a caller who described a five-year one. Both now record the choice in
  `assumptions`.
- **Eleven confidently wrong answers** (#28 §3.3–3.13). None of these failed; each returned
  `ok: true` and a value a caller would act on. `encode.json parse` reported a valid document
  as invalid when it arrived through MCP already decoded (`text` is typed `Any`, so
  `{"a": 1}` became Python's `{'a': 1}` repr); `encode.json stringify` emitted literal
  `Infinity` and `NaN`, which no strict JSON parser accepts, and now refuses;
  `datetime.parse` turned `01/02/50` into 2050 with no note — there is now a stated pivot
  (49 and below is 20xx) recorded in `assumptions`, because for a date of birth a silent
  century is a hundred years wrong; `datetime.recurrence` with `FREQ=MINUTELY` returned four
  copies of the same date, and a sub-daily frequency now keeps its times;
  `collections.pick_fields` silently destroyed a field when two renames collided;
  `collections.find_duplicates` with a key no record has reported every row as a duplicate of
  every other; `datetime.free_slots` refused two participants in the same timezone, which is
  two colleagues in one city, and now numbers them apart; `math.eval √2` produced a symbol
  called `sqrt2` because the sign only bound inside brackets; `collections.set_ops` conflated
  `1` and `true` (Python hashes them the same), and now compares by type as well as value;
  `finance.npv_irr` reported one IRR for a cashflow with three sign changes and now reports
  `sign_changes` and warns that Descartes' rule allows several.
- **Ranges that were never checked, and approximations that should have been refusals**
  (#28 §2c/§2d). Every one of these returned `ok: true` with a confident number:
  `geo_offline` accepted `lat=91` (returning `Arctic/Longyearbyen`, and a 10 118 km distance);
  `datetime.business_days` silently swapped a reversed range and now reports `sign` and
  `direction` like `diff` does; `convert.temperature` converted −500 °C to −226.85 K, and a
  *reading* below absolute zero is now refused while a `delta` difference still may be any
  sign; `convert.currency` accepted `rate=-83`; `holidays.list` accepted `month=13`, and the
  month filter now applies to `long_weekends` as well as `holidays`; `datetime.convert_tz`
  answered a wall time the clocks skipped or repeated with no note, and now warns naming which
  it is; `geo_offline.distance "Delhi" → "Mumbai"` returned **0 km** because both were
  approximated by Asia/Kolkata's reference city, and an unknown place is now `needs:
  coordinates` — a name whose reference city *is* the place (`Kolkata`, `London`) still
  resolves; `math.eval 1/0` and `tan(pi/2)` returned SymPy's complex infinity rendered as
  `nan + nani` and are now `invalid_input` pointing at `mode: limit`; `math.solve` on a
  degree-40 polynomial reported `no solutions found` where 40 roots exist, and now falls back
  to numeric roots saying so in `assumptions`.
- **Predictable inputs are phrased instead of raising Python exceptions** (#28 §4): five modes
  reported what broke inside leftbrain rather than what the caller did wrong.
  `collections.unflatten {"a": 1, "a.b": 2}` was `TypeError: 'int' object does not support item
  assignment` and now names the key that is both a value and a prefix; `scale` with `to_qty: 0`
  was `ZeroDivisionError: Fraction(1, 0)`; `datetime.add amount=1e9 unit=years` was
  `ValueError: year must be in 1..9999` and is now a stated 4 000 000 limit; `finance.compound
  years=1000000` was `internal` plus a bare `InvalidOperation` and is now `too_large` at 10 000
  years; `convert.units value=1e400` was `OverflowError: int too large to convert to float` and
  is now a stated 1e300 magnitude limit. Each carries `details` and a `hint`.
- **SSRF in `url_check`** (#28 §5): `http://169.254.169.254/latest/meta-data/` — the cloud
  metadata service, which answers with credentials — was actually fetched, as were `localhost`
  and RFC 1918 addresses. The host is now resolved and judged before the connection is opened and
  again after every redirect (a public host can answer `302` to a private address), and only
  public `http`/`https` addresses are fetched. `file:///etc/passwd` was being rewritten to
  `https://file:///etc/passwd` and attempted; any non-http(s) scheme is now refused instead. The
  connect budget drops from 15 s to 5 s.
- **CSV formula injection in `collections.to_csv`** (#28 §5): cells such as
  `=cmd|' /C calc'!A0`, `+1-2` and `@SUM(1)` were written verbatim, so opening the file in a
  spreadsheet ran them. Cells beginning `=`, `+`, `-`, `@`, tab or CR are now prefixed with an
  apostrophe, with the count in `assumptions` and `escaped_cells`; `escape_formulas: false`
  restores the old behaviour and warns. A negative number is left alone — `-12.5` is data.
- **A regular expression can no longer freeze the process** (#28 §1): `text.regex_match` with
  `(a+)+$` over `"a"*40 + "b"` never returned, and neither did `(a|aa)+` in `regex_replace` or a
  `pattern` inside a `validate.json_schema` schema. Once stdlib `re` starts backtracking nothing
  can stop it — `sre` is a C loop that never reaches a bytecode boundary, so no timeout, signal
  or thread kill is delivered until it finishes. The three shapes that cause it (a quantified
  group that is itself unbounded, one that can match nothing, and a quantified alternation whose
  branches overlap) are now recognised and refused with `unsupported` before the pattern is
  compiled, naming the shape and how to rewrite it. `validate.regex` judges rather than runs, so
  it keeps answering `valid: true` and reports the new `backtracking_risk` field with a warning.
- **Silent truncation now says so** (#28 §2f): `text.regex_match`'s `count` is the total number
  of matches rather than the number returned — an agent reading `count` off a truncated response
  got the limit and believed it was the answer. `returned` and `truncated` are new fields.
  `datetime.recurrence` with `count=1000000` returned 100 occurrences with nothing said about it,
  because a `count` larger than `limit` silenced the warning; it now sets `truncated` and warns.
  `holidays.next` warns when the two-year search window holds fewer holidays than were asked for.
- **A recursive JSON Schema is input, not a crash** (#28 §1): `validate.json_schema` with
  `{"$ref": "#"}` or 200-deep `allOf` raised `RecursionError`, which surfaced as `internal` with
  a traceback. Schemas nesting deeper than 50 levels are refused with `too_large`, and a `$ref`
  cycle is `invalid_input` explaining that validation cannot terminate.
- **`internal` errors no longer ship a stack trace to the caller** (#28 §4): the traceback named
  server file paths in every response. It is logged server-side now; set `LEFTBRAIN_DEBUG=1` to
  get the `trace` field back in the response as well.

### Changed

- **A call that fails the input schema now answers in the contract** (#28 §4): `convert` with no
  arguments, or `collections` with `where` as a string, used to come back as an MCP transport
  error carrying a pydantic dump and an `errors.pydantic.dev` link. Both now return
  `{"ok": false, "error": "invalid_input", …}` with the offending parameters under
  `details.parameters` and `needs.missing` naming anything required that was left out — the
  rejected values themselves are the caller's data and are not echoed back.

- **Per-key tool scopes** (#27): a key can be limited to chosen tools and, per tool, chosen
  modes — on the dashboard (a **Tools** disclosure on the create form, **Edit scope** on every
  key's row) or with `leftbrain-keys create|set … --tools "math,datetime,holidays:list+check"`
  (`--all-tools` lifts it). A scoped key's `tools/list` shows only the tools it may call, and a
  `tools/call` outside the scope returns the contract error
  `{"ok": false, "error": "forbidden", "message": "this key may not call holidays mode 'next'; allowed: list, check"}`
  as a result, not a transport error. `GET /keys/me` and `leftbrain-keys list` report `tools`
  (`null` for every tool). Existing keys have no scope and behave exactly as before; the
  `keys` table gains a nullable `scope` column on first start.

- `math`: `round(...)` over an expression with `vars` failed at parse time ("Cannot convert
  expression to float") because it evaluated its argument before the variables were
  substituted. It is now a deferred function that fires once the argument is numeric, and it
  rounds half-up on the decimal value, returning an exact rational (`round(2.675, 2)` →
  `67/25` = 2.68; `round(basic * 12 / 365 * unpaid_days, 2)` with vars → `5128.77`).
- `datetime`: a unix timestamp given as a digit string (`"1787232546"`, `"1787232546000"`) was
  read as a year and refused. Any 9–13 digit value, string or number, is now an epoch in every
  mode that takes a `value` (`parse`, `convert_tz`, `add`, `diff`, …), with the existing
  "unix timestamp read as UTC" / "read as milliseconds" assumptions; the MCP `value` parameter
  accepts numbers as well as strings, so `convert_tz` can take an epoch straight from another tool.

## [0.2.0] - 2026-08-27

Two new core tools — fourteen in all — and new modes on seven of the existing ones, plus a
dashboard that lets you delete old keys and a hosted free tier of 1,000 calls per key per day.
Everything is additive: every call that worked on 0.1.0 returns the same shape on 0.2.0.

### Added

#### New tools

- **`finance` tool** (13th core tool): `emi` (instalment, totals and amortisation schedule that
  reconciles to zero), `compound` (future value with optional per-period contributions and the
  effective annual rate), `cagr`, `npv_irr` (IRR by bisection), `gst` (inclusive/exclusive split
  into CGST/SGST or IGST, rounding difference reported) and `percent` (change vs percentage
  points, stacked vs additive discounts, exact bill splits). The rate's period and whether an
  amount is GST-inclusive are required, not guessed.
- **`color` tool** (14th core tool): `convert` between hex (3/4/6/8-digit), RGB, HSL, HSV/HSB,
  naive CMYK and Lab with alpha preserved; `describe` names the nearest of the 148 CSS colours
  by CIE76 ΔE and words the colour from fixed HSL bands; `swatch` returns a real PNG (stdlib
  zlib, 16–256 px, one colour or two side by side); `contrast` gives the WCAG 2.x ratio,
  AA/AAA for normal and large text and the smallest lightness change that passes; `mix`
  blends in sRGB or Lab; `harmony` rotates hue for complementary, analogous, triadic and
  split-complementary sets; `nearest` snaps to a caller's palette with the runner-up;
  `simulate` shows deuteranopia, protanopia and tritanopia (Viénot–Brettel–Mollon 1999);
  `grayscale` greys by rec709, rec601, lab, average or hsl with an optional ramp and strip.
  Bare triples such as `58, 26, 241` are refused with `needs.options` (rgb, hsl, hsv).

#### New modes and parameters

- `datetime` `now` takes a list as `tz` — zone names or `{tz, label}` objects — and answers with
  the shared instant as `utc` plus one full per-zone entry under `zones`, each carrying its
  `label` back. `convert_tz` accepts the same `{tz, label}` entries in `to_tz`. One round-trip
  for "what time is it in each of our offices right now"; the single-`tz` form is unchanged.
- `datetime` `free_slots`: common free slots for two or more participants in different time
  zones, from weekly windows (`09:00`–`17:00` on `mon`…`fri`) or one-off local ranges, intersected
  in UTC through `zoneinfo`. Each slot is shown in every participant's local time and in UTC,
  `per_day` totals the overlap per date, a window spanning a DST change is expanded to its real
  length with a note in `assumptions`, and no common time is `ok: true` with `slots: []` and a
  warning naming who never overlaps.
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
- `numbers` `semver`: compare or sort version strings as versions (`1.10` > `1.9`), with
  SemVer 2.0 pre-release precedence, build metadata carried but ignored for ordering, and a
  leading `v` / missing minor or patch tolerated and recorded in `assumptions`.
- `text` `similarity`: Levenshtein distance and a 0–1 ratio for a pair, or `text` + `items` to
  rank candidates and return the best match with its index — mapping typed input onto a menu,
  spotting near-duplicate names. Case-folded and whitespace-normalised by default, both stated.
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

#### Keys and dashboard

- `leftbrain-keys set --all --daily N [--rpm N] [--from-daily N]` changes limits on every key —
  or, with `--from-daily`, only on keys still at an old default — so a moved default can be
  migrated without a database shell or touching keys set by hand. Prints how many keys changed.
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

