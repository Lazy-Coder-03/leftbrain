# leftbrain

**The left brain for your AI agent.** Exact, deterministic answers for everything language models are bad at — arithmetic and calculus, dates and timezones, unit conversion, proportional scaling, counting, sorting, validation, randomness, hashes — plus optional live data (weather, FX rates) and file tools (PDF text, image → base64).

Use it as a plain **Python library**, or as an **MCP server** (stdio or HTTPS) with Claude Code, Claude Desktop, Cursor, Windsurf, Zed, or any MCP client.

```
pip install "leftbrain[all]"
claude mcp add leftbrain -- leftbrain          # Claude Code, one line
```

## Why

Models are right-brained: fluent, intuitive, and wrong about `9.11 < 9.9`, the number of r's in *strawberry*, what day it is, how many business days are left before a deadline, and whether 03/04/2025 is March or April. Every one of those has an exact answer. leftbrain is one small, well-described tool set that gives the model that answer instead of letting it guess.

Design rules:

1. **Deterministic only.** Same input, same output. No LLM-in-a-tool.
2. **Refuse ambiguity instead of guessing.** `IST`, `03/04/2025`, `ton`, `oz`, `KB` — each returns the concrete options rather than a silent assumption.
3. **Surface every interpretation.** Each response carries `assumptions[]` ("read as DD/MM per locale IN") and `warnings[]` ("day clamped to month end").
4. **Exact and decimal, together.** `sqrt(2)/2` *and* `0.7071…`, `7/4` *and* `1.75`, so the model never re-rounds.
5. **Few tools, many modes.** 14 core tools, each with a `mode` parameter, so the tool list stays cheap on every turn.
6. **Descriptions say *when*, not *what*.** The usual failure is the model not calling the tool.

## Tools

### Core (`leftbrain`, offline, pure functions)

| Tool | Modes | Replaces the model's guess at… |
|---|---|---|
| `math` | eval, exact, simplify, expand, factor, solve, diff, integrate, limit, series, ode, matrix, stats, convert_form, plot_points | any arithmetic, `15% of 200`, complex numbers `(3+4i)(1-2i)`, trig (`angle` required), calculus, linear algebra, statistics — SymPy, sandboxed |
| `datetime` | now, convert_tz, parse, add, diff, weekday, nth_weekday, business_days, overlap, duration_sum, free_slots, recurrence, cron_next, age, fiscal | the current time, DST-correct conversions, "next Friday 5pm", month-end clamping, working days with public holidays, common free slots across time zones, RRULE expansion, cron |
| `scale` | – | 4 → 7 servings, price per kg → per 250 g, 3 workers × 5 days → 12 workers (`mode=inverse`), with every dependent quantity |
| `convert` | units, temperature, currency, fuel_economy, cooking, sizes | km→mi, sqft→sqm, °C→°F (absolute or delta), GB→GiB, USD→INR (needs a rate), mpg↔L/100 km (US or UK gallon, never guessed), cups↔grams by ingredient density, shoe and clothing size charts |
| `holidays` | list, check, next, countries, subdivisions | public holidays for 150+ countries and their states |
| `numbers` | compare, round, format, allocate, sequence, parse, to_words, semver | `9.11` vs `9.9`, half-up vs banker's rounding, `₹1,23,45,678.50`, splitting ₹100 three ways with no lost paisa, "One lakh twenty-three thousand… only", `1.10` > `1.9` |
| `finance` | emi, compound, cagr, npv_irr, gst, percent | ₹10L at 8.5% for 20 years → ₹8,678.23 with the schedule that reconciles to zero, SIP future value, CAGR, NPV/IRR by bisection, ₹1,180 inclusive → ₹1,000 + ₹90 CGST + ₹90 SGST, 20% then 10% off is 28% not 30% |
| `text` | count, regex_match, regex_replace, diff, sort, dedupe, extract, find, similarity | character/word/occurrence counts, running a regex, exact diffs, natural sort, extracting emails/phones/GSTINs, edit distance and best-match from a list |
| `collections` | set_ops, group_by, aggregate, pick_fields, flatten, unflatten, paginate, find_duplicates, sort_by, chunk, filter, pivot, running, outliers, summarize, to_csv | what's in list A but not B, group-by with sums, multi-key sorts, filters, pivots, running totals, IQR outliers — over JSON records or CSV text, past the ~20-item cliff |
| `validate` | json_schema, assert, id, email, url, phone, ip, sql_parse, regex, cidr | rule checks over JSON (`{path, op, value}` → pass/fail + score), Luhn/IBAN/GSTIN/PAN/Aadhaar/ISBN/EAN/VIN/IFSC/UPI checksums and formats, ISBN-10 ↔ 13, is-this-IP-in-that-block and block overlap, `DELETE` without `WHERE` |
| `random` | uuid, int, float, pick, shuffle, token, bool, sample | real randomness: UUID v4/v7, seeded ints, secure tokens/OTPs, A/B buckets |
| `geo_offline` | tz_for_place, tz_for_coords, distance, country, zone_info | "Mumbai" → `Asia/Kolkata`, haversine distance, a country's zones — no network |
| `encode` | hash, hmac, checksum, base64, hex, url, html, jwt_decode, json | SHA-256, HMAC, CRC32, base64 — models hallucinate all of these; `expected` → `matches` in constant time |
| `color` | convert, describe, swatch, contrast, mix, harmony, nearest, simulate, grayscale | hex ↔ RGB ↔ HSL ↔ HSV ↔ CMYK with alpha, the nearest of the 148 CSS names by Lab ΔE, WCAG contrast with the lightness fix that passes, blends, hue harmonies, snapping to a brand palette, deuteranopia/protanopia/tritanopia, five greyscale methods, and a real PNG swatch to look at |

### External (`leftbrain-external`, network, key-less public APIs)

| Tool | Source |
|---|---|
| `weather` — current, forecast (16 days), historical (back to 1940), summary | Open-Meteo |
| `fx_rate` — latest or dated ECB reference rates, returns a table `convert` accepts | Frankfurter |
| `geo` — geocode, reverse, driving route distance/time | Open-Meteo / Nominatim / OSRM |
| `url_check` — real status code, redirect chain, latency | direct |

### Files (`leftbrain-files`, opt-in)

For custom agent loops that cannot open files themselves (hosted agents like Claude Code already can). `pdf_text`, `pdf_info`, `image_info`, `image_to_base64` (resize/compress to a byte budget; returns ready-made Anthropic and OpenAI image blocks), `base64_to_file`, `file_info`, `read_text`, `list_dir`, `file_hash` (streamed sha256/sha1/md5/blake2b/crc32 of a file of any size; pass `expected` — a digest or a whole `sha256sum` line — to get `matches`, the way to verify a download). Access is limited to `LEFTBRAIN_FILE_ROOTS`.

## Finding out what it can do

Three endpoints need no key, so an agent can look before it signs up:

```bash
curl https://leftbrain.idlesync.in/            # name, version, endpoints, auth mode
curl https://leftbrain.idlesync.in/healthz     # {"ok": true, "version": "0.3.0"}
curl https://leftbrain.idlesync.in/docs/tools  # every tool, its modes, and the contract
```

`/docs/tools` and `/docs/tools/<name>` answer in **JSON to a client and HTML to a browser** —
the same content negotiation `/` uses, so there is one URL per thing rather than two. The JSON
carries what the reference page carries minus the executed responses: each mode's purpose,
every parameter with its type, default and whether it is required, and the example arguments.

```bash
curl https://leftbrain.idlesync.in/docs/tools/holidays | jq '.modes[].name'
```

`tools/list` over `/mcp` is the other machine-readable route; it needs a key, reports no modes,
and honours that key's scope. Use it when you are already connected; use `/docs/tools` when you
are deciding whether to.

### Connecting an app

An app that reads a **config file** can send `Authorization: Bearer lblz_…`, which is all
leftbrain wants. An app configured through a **browser dialog** can only offer what the dialog
offers — and neither Claude's web connector form nor ChatGPT's has a place for a static key.

| client | works today |
| --- | --- |
| Claude Code, Claude Desktop, Cursor, VS Code | yes — a `headers` block, see [MCP clients](https://leftbrain.idlesync.in/docs/clients) |
| Claude on the web, ChatGPT | not yet — the dialogs expect OAuth |

For Claude on the web, use the desktop app: same account, and the connector lives in
`claude_desktop_config.json`. For ChatGPT, put a header-injecting proxy in front and point the
connector at that with **No Auth** — the docs carry a Cloudflare Worker that does it in a dozen
lines, and the trade it makes. OAuth on `/mcp` is the real fix and is tracked as its own issue.

## The contract

Every tool returns the same envelope:

```json
{"ok": true,  "result": {...}, "assumptions": ["read as DD/MM per locale IN"], "warnings": []}
{"ok": false, "error": "ambiguous", "message": "...", "needs": {"field": "locale", "options": [...]}, "retryable": false}
{"ok": false, "error": "too_large", "message": "...", "details": {...}, "retryable": false, "hint": "..."}
```

`result` is never `null`. When `needs.options` is present, pick one and call again; when
`needs.missing` is present, those parameters were left out.

Every failure carries **`retryable`** — whether an *identical* retry could ever succeed. It is
there because a client that reads only `ok: false` retries, and retrying a call that hit a limit
multiplies the load that caused it. `details` (the numbers behind the message) and `hint` (what to
change) are present when the tool has something concrete to say.

| `error` | when | `retryable` |
| --- | --- | --- |
| `invalid_input` | the call is wrong — a bad value, a missing or mistyped parameter | `false` |
| `ambiguous` | two readings are both plausible; `needs.options` lists them | `false` |
| `unsupported` | the mode cannot do this at all | `false` |
| `too_large` | a pre-check refused it before any work started | `false` |
| `timeout` | it ran to its deadline and was stopped | `false` |
| `resource_exhausted` | a memory or CPU limit was hit rather than the clock | `false` |
| `forbidden` | the key's scope does not include this tool or mode | `false` |
| `busy` | the server was saturated; nothing was computed | `true` |
| `internal` | something broke unexpectedly | `false` |

`internal` is `false` because it is the catch-all for an exception no mode anticipated, and most
of those are a deterministic consequence of the input — the same call fails the same way every
time. A site that genuinely knows its failure was transient says so at the raise.

A call whose arguments fail the tool's input schema never reaches the tool, and still answers in
this shape: `invalid_input`, the offending parameters under `details.parameters`, and
`needs.missing` naming anything required that was left out.

An `internal` error never ships a stack trace to the caller — it is logged server-side. Set
`LEFTBRAIN_DEBUG=1` to get a `trace` field back in the response as well.

### What a call cost

Every response carries a `meta` block. It never affects `ok`.

```json
{"ok": true, "result": {...}, "assumptions": [], "warnings": [],
 "meta": {"tool": "math", "mode": "eval", "latency_ms": 12, "compute_ms": 9,
          "version": "0.2.0", "truncated": false, "request_id": "9f2c1a4b7e0d3c88",
          "quota": {"remaining_today": 987, "daily_quota": 1000, "rpm": 60}}}
```

`latency_ms` is the wall time in the server, `compute_ms` the time in the engine, and
`truncated` is lifted out of the result so a caller reading a list knows it is not the whole
list. `quota` lets an agent back off before it hits a 429. `request_id` is echoed as
`X-Request-Id` — send your own and it is kept, so one id spans both sides of a trace — and
`X-Leftbrain-Latency-Ms` carries the latency, so both are visible without parsing the body.

`compute_ms` is also the regression alarm for the ceiling above: **a response whose
`compute_ms` exceeds its own deadline is a timeout that did not fire.** That was measurably the
case before the worker landed, when a call with `timeout=5` was still computing at 9.53 s.

### Parameters

Every mode declares what it reads, and a parameter it does not read is **refused**, not dropped.
`datetime.age` takes `on`, so passing `ref_date` used to return the age as of *today* — an answer
computed from a default after the caller's argument was discarded, with nothing to distinguish it
from a right answer. The refusal names the mode, lists what it does accept, and suggests the
closest match. Where two parameters are mutually exclusive (`significant` and `decimals`,
`months` and `years`) the winner is stated in `assumptions` rather than chosen silently.

### The 15-second ceiling

Every `math`, `text`, `validate`, `collections` and `numbers` call runs in a **worker process**
that can be killed, because a Python thread cannot. `int.__pow__`, `sre`'s matching loop and
`difflib`'s inner loops are C code that never returns to the eval loop, so an async exception is
never delivered, `SIGALRM` never fires, and — since the runaway holds the GIL — the whole
process starves, `/healthz` included. That is how one `math.eval 9^9^9^9` took the hosted
instance offline for 35 minutes. The layer-0 caps above refuse the obvious cases in
microseconds; this is the backstop for what they cannot estimate.

A call that reaches the deadline comes back as `timeout` with `stopped: "worker_terminated"`,
the limit and the real elapsed time, and `retryable: false` — an identical retry costs the same
15 seconds. A caller-supplied `timeout` is clamped to the ceiling. Under load, a call that waits
too long for a free worker returns `busy`, which *is* retryable, rather than queueing behind a
15-second wait.

| variable | default | what it does |
| --- | --- | --- |
| `LEFTBRAIN_COMPUTE_TIMEOUT` | `15` | wall-clock seconds before the worker is terminated |
| `LEFTBRAIN_QUEUE_TIMEOUT` | `5` | how long a call waits for a free worker before `busy` |
| `LEFTBRAIN_MAX_INFLIGHT` | CPU count | concurrent workers |
| `LEFTBRAIN_WORKER_MAX_TASKS` | `200` | calls before a worker is recycled (bounds SymPy's caches) |
| `LEFTBRAIN_WORKER_MEMORY_BYTES` | `1.5e9` | `RLIMIT_AS` in the child |
| `LEFTBRAIN_WORKER_CPU_SECONDS` | timeout + 2 | `RLIMIT_CPU` in the child, the kernel's backstop |
| `LEFTBRAIN_WORKER_TERM_GRACE` | `0.5` | SIGTERM→SIGKILL grace; a C loop never runs a handler |
| `LEFTBRAIN_COMPUTE_ISOLATION` | `1` | set `0` to run in-process (the library default) |

The worst case a client sees is the timeout plus the termination grace — about **15.5 s** — so
the reverse proxy in front of the server needs an idle timeout comfortably above that, or the
caller gets a platform 502 instead of the envelope. Workers use `forkserver` on POSIX
(`fork` is unsafe in a threaded server, and Python 3.12 still defaults to it) and `spawn` on
Windows. Without `pebble` installed the server logs that isolation is off and runs in-process;
the library always runs in-process.

### Limits

Every mode that can be asked for something enormous refuses it *before* computing, in
microseconds, with `too_large` naming the limit and the knob to turn: `math` estimates the
answer's digit count from the expression (so `9^9^9^9` never starts), sequences cap their largest
term, `diff` caps the units per side, `pivot` caps its column count, and so on — each one is
documented on that mode's [reference page](https://leftbrain.idlesync.in/docs/tools). Behind them
sits a 256 KB ceiling on any successful response (`LEFTBRAIN_MAX_RESPONSE_BYTES`). When a result
*is* trimmed rather than refused, the response says so: `truncated: true` and a line in
`warnings`, with the untrimmed total still reported.

Caller-supplied regular expressions get the same treatment. A pattern that can backtrack
exponentially — `(a+)+$`, `(a|aa)+`, `(a*)*` — is refused with `unsupported` before it is
compiled, in `text.regex_match`, `text.regex_replace` and any `pattern` inside a
`validate.json_schema` schema. Once stdlib `re` starts on one, nothing can stop it: it is a C
loop that never reaches a bytecode boundary, so no timeout, signal or thread kill is delivered
until it finishes, which it does not. `validate.regex` is the exception — it judges a pattern
rather than running it, so it reports `backtracking_risk` and stays `valid: true`.

`url_check` fetches only public `http`/`https` addresses. The host is resolved and checked before
the connection and again after each redirect, so loopback, link-local, RFC 1918, unique-local,
reserved and cloud-metadata addresses are refused — a server that fetches what a caller names is
otherwise a way to read the instance metadata service. `collections.to_csv` prefixes cells
beginning `= + - @` tab or CR with an apostrophe so a spreadsheet reads them as text rather than
running them; `escape_formulas: false` turns that off with a warning.

## Install

```bash
pip install leftbrain                 # library only
pip install "leftbrain[mcp]"          # + MCP servers (stdio)
pip install "leftbrain[all]"          # + external (httpx), files (pypdf, Pillow), HTTP server (uvicorn)
```

Python 3.11+. Zero API keys.

## Use from Python

```python
import leftbrain as lb

lb.math_tool("eval", expr="(3+4i)*(1-2i)")["result"]["decimal"]      # '11 - 2i'
lb.math_tool("solve", equations=["x^2+1=0"])                          # ±i
lb.datetime_tool("convert_tz", value="2026-03-08 09:30", from_tz="Asia/Kolkata", to_tz="America/New_York")
lb.datetime_tool("parse", value="03/04/2025")                         # ok: False, needs.options = DD/MM or MM/DD
lb.datetime_tool("business_days", start="2026-10-01", end="2026-10-31", region="IN")
lb.scale_tool(from_qty=4, to_qty=7, entities=[{"name": "flour", "qty": "2.5", "unit": "cup"}])
lb.convert_tool(value=2, from_unit="ton", to_unit="kg")               # ambiguous: metric / short / long
lb.numbers_tool("allocate", total=100, parts=3)                       # 33.34 / 33.33 / 33.33
lb.numbers_tool("to_words", value=123456.5, system="indian", currency="INR")
lb.validate_tool("id", kind="gstin", value="27AAPFU0939F1ZV")
lb.validate_tool("assert", data=doc, rules=[{"path": "leave.days", "op": "lte", "value": 2}])
```

`lb.TOOLS` maps tool names to functions if you want to wire them into OpenAI function-calling, LangChain, or your own loop — no MCP required.

## Use as an MCP server (stdio)

**Claude Code**

```bash
claude mcp add leftbrain -- leftbrain
claude mcp add leftbrain-external -- leftbrain-external
claude mcp add leftbrain-files -e LEFTBRAIN_FILE_ROOTS=/path/to/docs -- leftbrain-files
```

**Claude Desktop / Cursor / Windsurf** (`claude_desktop_config.json`, `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "leftbrain":          { "command": "leftbrain" },
    "leftbrain-external": { "command": "leftbrain-external" }
  }
}
```

No install at all: `"command": "uvx", "args": ["--from", "leftbrain[all]", "leftbrain"]`.

## Use over HTTPS (hosted)

One process serves every tool set with Streamable HTTP:

```bash
pip install "leftbrain[server]"
LEFTBRAIN_API_KEY=your-secret leftbrain-serve --port 8080
# core:      http://localhost:8080/mcp
# external:  http://localhost:8080/external/mcp
# files:     add --files (and set LEFTBRAIN_FILE_ROOTS)
```

It runs stateless by default, so it scales horizontally behind any load balancer. TLS is terminated by the platform in front of it:

- **Docker**: `docker build -t leftbrain . && docker run -p 8080:8080 -e LEFTBRAIN_API_KEY=… leftbrain`
- **Railway / Render / Fly.io**: point at the repo; the `Dockerfile` and `$PORT` are picked up automatically, HTTPS is provided.
- **Self-hosted**: put Caddy or nginx in front (`reverse_proxy localhost:8080`), or a Cloudflare Tunnel.

Then connect a client to the public URL:

```bash
claude mcp add --transport http leftbrain https://leftbrain.example.com/mcp \
  --header "Authorization: Bearer your-secret"
```

Health: `GET /healthz`. Service description: `GET /`.

### Per-user API keys (public free tier)

To let other people use your deployment with their own keys, quotas and rate limits, enable the key store instead of (or alongside) the static key:

```bash
LEFTBRAIN_KEYS_DB=/data/keys.sqlite3 leftbrain-serve     # or --keys-db
```

The store speaks **SQLite** (a path, for one instance with a volume) or **Postgres** (`LEFTBRAIN_KEYS_URL=postgres://…`, `pip install "leftbrain[postgres]"`) for platforms without persistent disk. The DSN is read from `LEFTBRAIN_KEYS_URL`, then `DATABASE_URL`, then `LEFTBRAIN_KEYS_DB` — so Northflank/Render/Railway's injected `DATABASE_URL` is picked up automatically.

With a store configured, `leftbrain-serve` also grows a web site:

- `/` — landing page (browsers) or the JSON service description (`Accept: application/json`)
- `/login` — GitHub OAuth; keys belong to the account's verified primary email
- `/dashboard` — create up to 5 active keys with a lifetime of 30 / 90 / 365 days (or never, with a warning), choose which tools — and which modes of each — a key may call (on creation, or later with **Edit scope** on its row), see today's usage and when each key expires, show a key again, revoke, and delete a revoked or expired key for good. Keys issued before the server could show keys again are marked **legacy**: they still work if you saved them, but do not hold one of the 5 slots
- `/docs` — quickstart with Windows PowerShell / macOS / Linux tabs, MCP client setup, and `/docs/agents/auth`, an authentication guide written for a model rather than a person
- `POST /demo/{numbers|convert|datetime|text}` — key-less demo, 30 req/min per IP

**There are two ways to connect.** Paste a key from `/dashboard` into your client's config, or —
with `LEFTBRAIN_BASE_URL` set — connect with **OAuth 2.1** and let leftbrain create the key for
you. ChatGPT has no field for a key at all and OAuth is its only route; Claude Code, Cursor and
VS Code can use either.

- **OAuth 2.1 for MCP clients**: discovery (RFC 8414 and RFC 9728), Client ID Metadata Documents,
  dynamic client registration (RFC 7591), PKCE `S256`, refresh-token rotation, and the device
  grant (RFC 8628) for an agent with no browser. Requires `LEFTBRAIN_SECRET` and
  `LEFTBRAIN_BASE_URL`; without both, none of it is mounted.
- **A connector's key is an ordinary key.** Approving on the consent screen creates one named
  after the app and where it runs — `Claude Code · Windows`, `ChatGPT · web` — visible on the
  dashboard, revealable, re-scopable, revocable, and counting against the same active-key cap.
  Revoking it stops that connector on its next call.
- **`POST /keys/me/scope`** lets a caller propose *narrowing* its own key. It returns `202` with a
  URL for its owner to approve; nothing changes until they do, and widening is refused outright.
- The scope editor shows how many times the key has called each tool, so the ones sitting at zero
  are the ones to untick.

and the key API behaves like this:

- **Self-serve signup**: `POST /keys/signup {"email": "dev@example.com"}` → `{"key": "lblz_…", "daily_quota": 1000, "rpm": 60}`. Throttled to 3 signups per IP per day and 5 active keys per email. Anonymous signup is **off** unless `LEFTBRAIN_OPEN_SIGNUP=1`; with the web site, people sign in at `/login` instead.
- **Every request** is metered: `X-RateLimit-Remaining-Today`, `X-RateLimit-Limit-Day`, `X-RateLimit-Limit-Minute` headers; `429` with `Retry-After` when a limit is hit; `403` for a disabled key, and `403 {"error": "expired", "message": "key expired on 2026-11-25; create a new one at /dashboard"}` once a key's lifetime is up. Expired keys stop counting towards the active-key cap.
- **Caller self-check**: `GET /keys/me` with the key → owner, quota, used today, `expires_at`, and `tools` (the key's scope, or `null` for every tool).
- **Scoped keys**: a key can be limited to specific tools, and to specific modes of a tool — from the dashboard (the **Tools** disclosure on the create form, or **Edit scope** on a key's row) or with `leftbrain-keys … --tools "math,datetime,holidays:list+check"`. A scoped key's `tools/list` shows only the tools it may call, and a `tools/call` outside the scope returns the contract error `{"ok": false, "error": "forbidden", "message": "this key may not call holidays mode 'next'; allowed: list, check"}` — a result, not an HTTP error, so an agent reads it and stops. Keys without a scope may call everything; a changed scope applies on the key's next call.

Environment: `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `LEFTBRAIN_SECRET` (cookie signing, 32+ random chars), `LEFTBRAIN_BASE_URL` (e.g. `https://leftbrain.idlesync.in`, used for the OAuth callback), and `LEFTBRAIN_TRUSTED_PROXY_HOPS` (default `1`) — how many proxies append to `X-Forwarded-For` in front of the process, so per-IP limits are keyed on the entry *your* proxy wrote rather than the caller-supplied leftmost one. One reverse proxy (Northflank, Render, Fly, nginx) is `1`; add Cloudflare in front and it becomes `2`; `0` means nothing proxies it and no forwarding header is believed.

Defaults come from `LEFTBRAIN_DEFAULT_DAILY_QUOTA` (1000), `LEFTBRAIN_DEFAULT_RPM` (60), `LEFTBRAIN_SIGNUPS_PER_IP_PER_DAY` (3). Authentication only ever compares a SHA-256 of the key. When `LEFTBRAIN_SECRET` is set the store also keeps a Fernet-encrypted copy of each key, under a key derived from that secret, so the signed-in owner can be shown their own key again on the dashboard and have it filled into the docs examples. Rotating `LEFTBRAIN_SECRET` leaves existing keys working but no longer revealable; leave the secret unset and nothing but the hash is stored.

Admin CLI (any DSN):

```bash
leftbrain-keys create --owner you@example.com --daily 50000 --rpm 300 --expires 90d --note "partner"   # default 365d; --expires never warns
leftbrain-keys create --owner bot@example.com --tools "math,datetime,holidays:list+check"              # only these tools; tool:mode+mode narrows a tool
leftbrain-keys list                                     # one JSON line per key, with expires_at / expired / tools
leftbrain-keys disable lblz_xxxxxxxx
leftbrain-keys enable lblz_xxxxxxxx
leftbrain-keys revoke lblz_xxxxxxxx
leftbrain-keys set lblz_xxxxxxxx --daily 20000 --rpm 120 --expires 30d   # --expires counts from now; also revives an expired key
leftbrain-keys set lblz_xxxxxxxx --tools "numbers,convert"                # replace the key's scope; --all-tools lifts it
leftbrain-keys set --all --daily 1000 --from-daily 5000                  # migrate every key still on an old default; drop --from-daily to hit every key
leftbrain-keys usage --days 7
leftbrain-keys stats
```

**Free hosting that fits**: Northflank's sandbox (always-on service + free Postgres + custom domain) — see [`docs/deploy-northflank.md`](docs/deploy-northflank.md) for a step-by-step including DNS for a subdomain.

## Examples of what changes

| Ask | Without | With leftbrain |
|---|---|---|
| "Which is bigger, 9.11 or 9.9?" | often 9.11 | `numbers.compare` → 9.9 |
| "Convert 9:30 IST on 8 March to New York" | ±1 h around DST | `datetime.convert_tz` → 23:00 on 7 March, EST, day_shift −1 |
| "Split ₹100 among 3 people" | 33.33 × 3 = 99.99 | `numbers.allocate` → 33.34 / 33.33 / 33.33 |
| "Is 27AAPFU0939F1ZV a valid GSTIN?" | "looks valid" | checksum verified |
| "How many working days in October 2026 in India?" | guesses 21–23 | 20, listing Gandhi Jayanti and Dussehra |
| "sin(30)" | 0.5 or −0.988 depending on mood | refuses until `angle` is given |

## Development

```bash
git clone <this repo> && cd leftbrain
python -m venv .venv && . .venv/bin/activate     # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest                          # unit tests
python scripts/smoke.py         # every tool, end to end
python scripts/mcp_client_check.py   # spawn each MCP server over stdio and call it
ruff check src tests
```

Layout: `src/leftbrain/core/` holds the pure functions (one file per tool), `contract.py` the envelope, `mcp_server.py` the stdio server, `serve.py` the HTTP server, `external/` and `files/` the optional sets.

Releases are cut by pushing a `vX.Y.Z` tag — see [`docs/releasing.md`](docs/releasing.md); what changed in each one is in [`CHANGELOG.md`](CHANGELOG.md).

## Roadmap

- TypeScript port (same tool names and contract) for Node-based agents
- OCR fallback for scanned PDFs
- `csv`/`xlsx` parsing in `files`

## License

MIT
