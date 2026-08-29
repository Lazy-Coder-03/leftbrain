# leftbrain as an OAuth 2.1 authorization server for MCP clients

Status: design for approval, 2026-08-29. Implements [#34](https://github.com/Lazy-Coder-03/leftbrain/issues/34).
Branch: `feat/34-oauth-mcp`.

## Goal

ChatGPT plugins and Claude's web connectors both refuse a static API key and both perform
MCP's OAuth discovery against the server URL. leftbrain becomes an OAuth 2.1 authorization
server so they can connect — and so can an agent in a terminal with no browser at all.

**Claude Code is a third blocked client, confirmed against the live server on 2026-08-29.**
Adding leftbrain to a Claude Code session fails with `Dynamic client registration rejected:
unsupported — no such endpoint; see / for the endpoint list`. That message is not an auth
error: it is leftbrain's generic 404 handler (`serve.py:443`), reached through the catch-all
`Mount("", app=_McpOnly(root_app))` at `serve.py:445`. Claude Code asks for the discovery
documents, gets 404 for each, falls back to the conventional endpoint names, POSTs `/register`,
and is told the endpoint does not exist. Every one of those 404s becomes a real response in
this design, so the failure resolves as a consequence of the work rather than needing its own
fix. It is worth knowing that the symptom looks like a routing bug rather than a missing
feature — that is a diagnosis trap for whoever meets it next.

Two audiences are equally primary:

- **A person** clicks through a consent screen and sees the result on the dashboard as an
  ordinary key.
- **An agent** either drives the whole flow itself from the 401 it just received, or — when it
  has no browser — obtains a short code and tells its human exactly what to type.

## Non-goals

- leftbrain as a *resource server* trusting an external authorization server (the SDK's
  `token_verifier` half). Different feature; nobody has asked for it.
- ChatGPT's **Mixed** authentication mode.
- Team accounts, per-organisation connectors, or a scope vocabulary richer than one coarse
  OAuth scope. The key already carries leftbrain's fine-grained per-tool scope.
- `auth.md` agent-delegation registration. It is a larger protocol (claim ceremony, JWKS
  verification, `jti` tracking); the agent documentation here is a document, not that protocol.
- Billing. The key cap is made a tunable number so a paid tier is possible later; no payment
  code is written now.

## The three hard constraints

1. **`lblz_…` bearer keys keep working byte for byte.** Every current client uses them. A
   successful call with a valid key returns the same body, the same quota headers and the same
   `tools/list` filtering as it does today. This is the first acceptance criterion and there is
   a regression test at every step.
2. **A connector-minted key counts against the same key cap.** OAuth is another door into the
   same room, not a second room with no lock. See "The key cap" below.
3. **Consent is not optional.** leftbrain meets the MCP spec's confused-deputy preconditions
   exactly (see Security), so its own consent screen is load-bearing, not decoration.

## Clients: what each one actually needs

Researched 2026-08-29 against Anthropic's
[connector authentication docs](https://claude.com/docs/connectors/building/authentication),
OpenAI's plugin docs and client issue trackers. Several of these are requirements the
implementation would otherwise miss.

| Client | Static `lblz_` key | OAuth identity | Redirect URI |
|---|---|---|---|
| Claude Code | **Works** — verified on 2.1.251, header reaches `tools/call` | **CIMD** | `http://localhost:PORT/callback`, **ephemeral port** |
| Claude web / Desktop / mobile | `static_headers`, **beta**, entered by an org admin | CIMD, else DCR | `https://claude.ai/api/mcp/auth_callback` |
| ChatGPT plugins | **No** — no key field, no header editor | CIMD, else DCR | `https://chatgpt.com/connector_platform_oauth_redirect` |
| Codex | Yes | CIMD when advertised, else DCR | loopback |
| Cursor, VS Code, Windsurf, Cline | Yes | DCR (CIMD is an open request on Cursor) | loopback, ephemeral port |

Three corrections to earlier assumptions, each of which would have produced a working-looking
server that real clients reject:

1. **Exact-string `redirect_uri` matching breaks Claude Code.** It registers
   `http://localhost/callback` and `http://127.0.0.1/callback` in its Client ID Metadata
   Document, then redirects to an ephemeral port such as `http://localhost:3118/callback`.
   Anthropic's docs state the authorization server must accept both **with the port component
   ignored**; [RFC 8252 §7.3](https://datatracker.ietf.org/doc/html/rfc8252#section-7.3) requires
   it for `127.0.0.1` and the same treatment is needed for `localhost`. The rule is therefore
   exact string matching **except** for loopback hosts, where the port is ignored and `http` is
   permitted. That is a documented, bounded exception — not a wildcard.
2. **Claude selects CIMD only when the metadata advertises both
   `"client_id_metadata_document_supported": true` and `"none"` in
   `token_endpoint_auth_methods_supported`.** The SDK's `build_metadata()` emits neither: its
   auth methods are `["client_secret_post", "client_secret_basic"]`. Miss the `"none"` and every
   Claude client silently falls back to DCR — which the same docs warn "causes Claude to
   register a new client on every fresh connection".
3. **ChatGPT is the only client that refuses a static key outright.** Claude web gained
   `static_headers` in beta (an org admin enters the credential once). The claim in #34 that
   both clients refuse keys is now half true, and the docs must not repeat it.

Further requirements taken from the same source, all cheap and all load-bearing:

- The protected-resource document's `resource` **must exactly match the MCP URL as the user
  types it**, path included — `https://leftbrain.idlesync.in/mcp`.
- `authorization_servers` — only the **first** entry is used; there is no fallback to later ones.
- **10 s** budget for discovery, registration and token; **30 s** for refresh. A CIMD fetch
  inside the token path must therefore be cached, not repeated.
- Refresh tokens **must rotate** for public clients, and a dead one must return `invalid_grant`,
  not a custom code.
- `offline_access` must appear in `scopes_supported` or Claude never asks for a refresh token.
- `/token` must accept `application/x-www-form-urlencoded`; `/register` takes `application/json`.
  Different parsers, same server.
- DCR clients accumulate. Unused registrations with no consent row are pruned after 30 days.

**Claude Code's header bug: measured, and it does not reproduce.**
[claude-code#28293](https://github.com/anthropics/claude-code/issues/28293) reports custom headers
reaching the initial connection but not tool-call POSTs. Tested 2026-08-29 against a local
leftbrain on **Claude Code 2.1.251**, `--transport http`, with an instrumented middleware logging
every request:

```
POST /mcp  rpc=initialize            auth=YES  -> 200
POST /mcp  rpc=server/discover       auth=YES  -> 200
POST /mcp  rpc=tools/list            auth=YES  -> 200
POST /mcp  rpc=subscriptions/listen  auth=YES  -> 200
POST /mcp  rpc=tools/call:math       auth=YES  -> 200   <- the request the bug is about
```

Every request carried the header, the tool call included, and returned the right answer. The
reported bug is about the `sse` transport's separate `/messages` endpoint; on streamable-http
every message is a POST to `/mcp` and the header rides along. **So a pasted `lblz_` key works in
Claude Code today**, and the docs say so rather than steering people to OAuth for a problem they
do not have.

**What the failure actually is, reproduced exactly.** Registering the same server with *no*
header gives the reported error, and the log shows why:

```
GET  /.well-known/oauth-protected-resource/mcp   -> 404
GET  /.well-known/oauth-protected-resource       -> 404
GET  /.well-known/oauth-authorization-server     -> 404
GET  /.well-known/openid-configuration           -> 404
POST /register                                   -> 404   <- "Dynamic Client Registration rejected"
POST /mcp                                        -> 401
```

Two things worth building to, neither of which was obvious:

1. **Claude Code probes the well-known paths *before* it ever calls `/mcp`.** The 401 and its
   `WWW-Authenticate: resource_metadata=…` pointer arrive last, after discovery has already
   failed. The pointer is still correct and other clients rely on it, but for Claude Code the
   well-known documents must simply *exist* at the origin — a pointer on the 401 alone would be
   too late.
2. **It also probes `/.well-known/openid-configuration`**, which the SDK does not serve. It is
   tried after the RFC 8414 path, so serving that one is enough; no OIDC document is needed.

Both `/.well-known/oauth-protected-resource/mcp` and the bare `/.well-known/oauth-protected-resource`
are probed, so the RFC 9728 route must answer at the path-suffixed form the SDK generates — which
it does — and the bare form is a harmless miss.

**Why the device grant is in, and what it is not.** No shipping MCP client drives RFC 8628 for
MCP today — Claude Code's request for it was closed as a duplicate — so **it will not make Claude
Code connect**, and neither the docs nor the release notes may imply otherwise.

It earns its place on a different argument. Without it, the only route for an agent on a headless
box is a person creating a key on the dashboard and pasting `lblz_…` into the agent — which puts
a live credential into the conversation, the terminal scrollback, and whatever logs sit behind
the model. The device grant moves nothing secret across that boundary: a six-character code,
useless on its own, expiring in ten minutes, granting nothing until a signed-in human approves it
in a browser. This is the same flow `gh auth login`, `docker login`, `aws sso login` and `az
login` use, and it *strengthens* the "a person must be present" property rather than weakening
it — the person is still required, they simply need not be at the same machine.

The forward-looking part is a bonus, not the case for building it.

## Security: why the consent screen shapes the design

The [MCP security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)
describe a confused-deputy attack that becomes possible when **all** of these hold:

| Condition | leftbrain today |
|---|---|
| A static client ID against a third-party authorization server | Yes — GitHub login (`web/auth.py`) |
| MCP clients may register dynamically, each getting its own `client_id` | This feature adds it |
| The third party sets a consent cookie after first authorization | GitHub does |
| The server does not do its own per-client consent first | This feature must prevent it |

The attack: register a client with `redirect_uri: attacker.example`, send a signed-in user a
crafted authorization link, GitHub skips its own consent screen because the cookie is already
set, and leftbrain hands the authorization code to the attacker.

The controls below are therefore requirements, not hardening.

1. **Per-client consent, stored server-side**, checked *before* anything is forwarded to GitHub.
2. **The signed `state` cookie is set only after the user clicks approve.** Setting it earlier
   makes the consent screen bypassable, which is the whole attack.
3. **Exact-string `redirect_uri` matching, with one bounded exception.** No wildcards, no prefix
   matching; a changed `redirect_uri` requires re-registration. The exception is loopback
   (`localhost`, `127.0.0.1`, `[::1]`), where the **port is ignored** and `http` is permitted, per
   RFC 8252 §7.3 — without it Claude Code, Cursor and VS Code cannot complete a flow at all. Host
   and path are still matched exactly, so the exception widens only the port.
4. **PKCE with `S256` required** on every authorization code exchange.
5. **SSRF guard on CIMD fetches.** Accepting Client ID Metadata Documents means leftbrain
   fetches a URL supplied by a stranger. HTTPS only; loopback, private (`10/8`, `172.16/12`,
   `192.168/16`), link-local (`169.254/16`, cloud metadata) and private IPv6 blocked; redirects
   not followed; 5 s timeout; 64 KB response cap.
6. **Consent UI**: names the client, shows the `redirect_uri` **host** prominently, lists the
   tools being granted, carries a CSRF token, and sends `X-Frame-Options: DENY` plus
   `frame-ancestors 'none'` against clickjacking.
7. **A `localhost` redirect URI gets an explicit warning** on the consent screen. Domain control
   cannot prove which local process is listening on a loopback port.
8. **Tokens are audience-bound to leftbrain**, opaque, and stored only as SHA-256 hashes.
   leftbrain never accepts a token it did not issue and never forwards one downstream.
9. **Revoking the key revokes its tokens**, immediately, because tokens join to the key.
10. **Device flow**: high-entropy `user_code`, short expiry, `slow_down` polling per RFC 8628,
    and the same consent screen — a device code grants nothing until a signed-in human approves.

## Architecture

```
src/leftbrain/oauth/
  __init__.py     build_oauth_routes(store, cfg) -> list[Route]; the only import serve.py needs
  store.py        OAuthStore over KeyStore._DB: clients, consents, codes, tokens, device codes
  provider.py     LeftbrainOAuthProvider(OAuthAuthorizationServerProvider) — the SDK's ten methods
  cimd.py         Client ID Metadata Document fetch + the SSRF guard
  device.py       RFC 8628: /oauth/device_authorization, polling, /device verification page
  views.py        consent screen and device-approval screen (reuse web/auth.py session + CSRF)
```

The SDK (`mcp` 2.1.1, already pinned) ships the protocol surface: `create_auth_routes()` gives
`/.well-known/oauth-authorization-server`, `/authorize`, `/token`, `/register` and `/revoke`;
`create_protected_resource_routes()` gives the RFC 9728 document. What we write is the provider,
its persistence, the consent screen, and the device grant the SDK does not cover.

**Four deviations from the SDK's defaults**, each forced by a real client. `build_metadata()` is
called, then the result is amended and substituted for the SDK's own metadata route in the list
`create_auth_routes()` returns. Everything else is the SDK's, CORS and body-size handling
included.

| Field | SDK default | What we set, and why |
|---|---|---|
| `client_id_metadata_document_supported` | absent | `true` — CIMD is how Claude Code identifies itself |
| `token_endpoint_auth_methods_supported` | `["client_secret_post", "client_secret_basic"]` | append `"none"` — without it Claude never selects CIMD and falls back to DCR |
| `scopes_supported` | from registration options | include `offline_access` — without it Claude never requests a refresh token |
| `device_authorization_endpoint` / `grant_types_supported` | absent / no device grant | added, so a client can detect RFC 8628 support |

### Storage

Five tables, added to `keys.py::_SCHEMA` so they are created on both SQLite and Postgres.
`_migrate()` is for new *columns*; whole new tables belong in `_SCHEMA`, which is
`CREATE TABLE IF NOT EXISTS` and already runs on every `KeyStore.__init__`.

| Table | Holds | Notes |
|---|---|---|
| `oauth_clients` | `client_id`, `secret_hash`, `redirect_uris`, `client_name`, metadata JSON, `created_at` | CIMD clients are not stored; they are re-fetched and cached in-process |
| `oauth_consents` | `owner`, `client_id`, `key_prefix`, `granted_at` | The per-client consent registry the security fix requires, and the mechanism that makes re-consent reuse a key |
| `oauth_codes` | `code_hash`, `client_id`, `key_hash`, `code_challenge`, `redirect_uri`, `scopes`, `expires_at` | Single use, 60 s |
| `oauth_tokens` | `token_hash`, `kind` (access/refresh), `client_id`, `key_hash`, `scopes`, `expires_at` | Access 1 h, refresh 30 d, both rotated on refresh |
| `oauth_devices` | `device_code_hash`, `user_code`, `client_id`, `status`, `key_hash`, `expires_at`, `last_polled` | RFC 8628; `status` is pending/approved/denied/expired |
| `tool_usage` | `key_hash`, `tool`, `count`, `last_used` | Lifetime calls per key per tool. Not OAuth's own, but what makes narrowing a decision rather than a guess |

Every token row carries the `key_hash` it is bound to. Resolution joins to `keys`, so a disabled,
expired or revoked key kills its tokens with no extra bookkeeping.

## The key cap

`MAX_ACTIVE_KEYS_PER_EMAIL` (`keys.py:55`) is counted by `_active_count` over active,
non-legacy keys and displayed on the dashboard as `active / max_keys`.

**The default rises from 3 to 5.** It is already environment-tunable
(`LEFTBRAIN_MAX_KEYS_PER_EMAIL`), so this is a default change, not a mechanism change — a
deployment that wants a different number, or a paid tier that wants a larger one, sets the
variable. Five is chosen because a connector now consumes a slot: three was sized for keys
pasted into config files, and a user with Claude, ChatGPT and a terminal agent would otherwise
have nothing left for a script.

Every message and template already interpolates the constant (`{{ max_keys }}` in
`dashboard.html` and `login.html`, f-strings in `keys.py`), so the copy follows the default
automatically. Only the README's stated number and one stale test comment name it literally, and
both are corrected in the same change.

- The consent screen mints its key through **`create_for_owner`**, which already enforces the
  cap — never through `create`, which does not. A connector key is an ordinary key: same
  dashboard row, same revoke button, same quota, same rpm, same scope picker.
- **At the cap, consent refuses** with `you already have 5 active keys; revoke one first`,
  rendered on the consent page with a link to `/dashboard`, and returned to the client as an
  `access_denied` error whose description carries the same sentence — so an agent can relay the
  actual fix to its human rather than "authorization failed".
- **Re-consent reuses the key.** If `oauth_consents` already has a row for this
  `(owner, client_id)` and its key is still usable, the flow issues a fresh token against that
  key instead of minting a second one. Without this, reconnecting a connector twice would
  silently consume two slots.

## How a connector key appears to its owner

A key minted by consent is an ordinary key with a name leftbrain filled in. Nothing about it is
hidden or special-cased: **Show** reveals the raw `lblz_…` for its owner exactly as it does for a
hand-made key (the `secret_enc` column is written by `create`, so this needs no new code), usage
counts, the scope editor, expiry and revoke all behave identically, and it occupies one of the
owner's slots. Revoking it cuts the connector off on its next call.

**Narrowing it afterwards is the point, not an afterthought.** The consent screen ticks every
tool by default, because a person connecting an assistant does not yet know which tools it will
reach for and an empty grid produces a connector that silently fails. The answer to that is the
existing **Edit scope** control on the key's dashboard row: `set_scope` writes the new scope and
the server is stateless, so **the next call is already narrower** — no reconnecting, no second
consent, and the connector observes only that the tools it should not have are gone from
`tools/list`. The documentation must actively recommend this rather than leaving it to be
discovered; a scope editor nobody finds is the same as no scope editor. Widening later is
equally possible and equally the owner's business: it is their key, on their dashboard, behind
their session and CSRF token.

It is named the way WhatsApp names a linked device — **what the app is, and where it runs**:

```
Claude Code · Windows        41 today    last used 2 min ago
Cursor · macOS                8 today    last used yesterday
ChatGPT · web                 5 today    last used 1 hour ago
my deploy script              3 today    last used 4 days ago   <- created by hand
```

- **The app** comes from `client_name` in the client's registration or CIMD document, truncated
  and stripped of control characters. It is attacker-supplied for a DCR client, so it is escaped
  wherever it is rendered and never trusted as markup.
- **Where it runs** comes from the `User-Agent` of the browser that completed consent, reduced to
  an OS word (`Windows`, `macOS`, `Linux`, `iOS`, `Android`).
- **A cloud client gets `· web`, not an OS.** ChatGPT and Claude web run on their vendor's
  servers; the browser that approved says nothing about where the client runs. Stamping the
  approver's OS on such a row would be actively misleading — someone would revoke
  `ChatGPT · Windows` believing it was tied to their PC. A client is treated as cloud when its
  registered redirect URI is not a loopback address, which is exactly the distinction that
  matters.
- The whole string fits the existing 40-character `note` column, which is what the dashboard
  already renders as a key's name. No schema change.

Two identical machines produce two identical names — `Claude Code · Windows` twice. That is the
same limitation WhatsApp has, and the same answer applies: the row already shows last-used, which
is how a person tells them apart. A disambiguating suffix is deliberately **not** added up front;
it would put noise on every row to solve a problem most owners never have. If it proves necessary,
it is added only on collision.

### The editor shows what the connector actually called

Advice to narrow a key is useless without the evidence to narrow it *by*. The scope editor puts
a lifetime call count beside every tool, for that key:

```
Which tools may Claude Code · Windows use?
  [x] math          412 calls
  [x] convert        38 calls
  [x] datetime        6 calls
  [x] holidays        0 calls
  [x] finance         0 calls
```

The zeroes are the answer, and the decision makes itself. leftbrain counts calls per key per
*day* today (`usage`), not per tool, so this needs the `tool_usage` table above and one more
contextvar: `AuthMiddleware` already sets `current_scope` from the resolved key, and now sets a
recorder beside it. `scopes.enforce()` — which wraps every tool and already knows the tool's
name — records the call after the scope check passes, so a call refused by scope is not counted
as usage, because it never ran.

`enforce` runs in the server process, above the dispatch into the compute-isolation worker, so
the write happens where the database connection already lives. It is one UPSERT per tool call,
next to the one `verify_and_count` already performs per request, and it never raises: a lost
count is not worth a caller's answer. If it ever shows up in a profile the fix is to buffer and
flush per request, not to drop the count — but it is not speculatively buffered now.

### An agent may *propose* narrowing its own key. A human approves it.

`POST /keys/me/scope`, authenticated by the credential the caller already holds — a `lblz_` key
or an OAuth token, resolved identically. The body names the tools to keep. **Nothing changes
yet.** The request is recorded as pending, and the response hands the agent something to show
its user:

```json
{"ok": true, "result": {
  "status": "pending_approval",
  "approve_url": "https://leftbrain.idlesync.in/keys/scope-request/7f3a…",
  "tell_your_user": "I have only used math and convert. I would like to give up my access to the other tools. Approve at https://leftbrain.idlesync.in/keys/scope-request/7f3a…",
  "expires_in": 900,
  "check": "GET /keys/me"
}}
```

The owner opens that URL, signed in, and sees the change as a before-and-after with the call
counts beside it — `holidays 0 calls` next to a tool being given up is the whole argument.
Approve applies it; decline discards it. The request is single use and expires in fifteen
minutes. The agent learns the outcome from `GET /keys/me`, which already returns the key's
tools, so no polling protocol is invented for this.

**Two gates, not one.**

1. **Narrow only.** The proposal must be a subset of what the key currently holds. Dropping a
   tool, or dropping modes within a tool, is allowed; adding either is refused immediately with
   `forbidden`, naming what is held and what was asked for. A key with no scope at all may
   propose anything, since everything is a subset of every tool. The subset check runs **again
   at approval**, because the owner may have narrowed the key further in the meantime, which
   would turn a stale proposal into a widening.
2. **Consent.** Even a valid narrowing waits for a human. An agent cannot change what a
   credential may do, in either direction, on its own authority.

The second gate is the one that is easy to argue away and should not be. Dropping privileges
cannot escalate, so it looks safe — but a credential whose permissions change without its owner
touching anything is a surprise, and a prompt-injected agent that cannot escalate can still
strip a key to nothing and silently break every workflow that key serves. "Why did my connector
stop working?" answered by "the agent decided to" is not an answer anyone accepts.

What is left is the good half. The agent does the part it is genuinely better at — it knows
which tools it called and which it never touched — and hands its owner a one-click decision,
instead of the owner reverse-engineering the right scope from a usage table. Proposing is the
agent's job; deciding stays the owner's.

Restoring a tool is unchanged: two clicks on the key's row at `/dashboard`, so the key keeps its
name and its history rather than needing a revoke-and-reconnect.

The agent auth document describes proposing as something an agent *should* do once it knows what
it needs — and states plainly that the answer is a pending request, not a change, so an agent
neither assumes it worked nor retries in a loop.

### A key that allows nothing still answers, and says something true

Three ways a scope can end up granting nothing, and each must return promptly rather than hang,
error obscurely, or — worst — invert into granting everything.

**Nothing ticked is not everything.** `parse_scope([])` raises `a scope needs at least one tool`,
and `_from_map` raises the same when a map reduces to no tools, so an empty selection cannot
become an unrestricted key today. That is worth stating because the obvious implementation of a
checkbox grid does invert it: `parse_scope(values) if values else None` reads an empty tick-list
as `None`, and `None` means *every tool*. The consent screen and the device page must pass the
empty list to `parse_scope` and render the `ValueError` as a form error — never substitute
`None`. There is a test for exactly this on both pages, because it is a one-word mistake that
fails open.

**An empty proposal to `/keys/me/scope`** is a `400` naming the problem, not a grant and not a
no-op. An agent that means "I need nothing" should have its key revoked by its owner, which is a
different act with a different button.

**A scope naming only tools this build does not have** is the case that actually reaches
production: a key scoped to `files` on a server started without the files extra. The store loads
it with `strict=False` on purpose, so the scope survives as `Scope(tools={"files": None})` and
allows nothing. Today that behaves correctly but explains itself badly:

- `tools/list` returns `{"tools": []}` — correct, prompt, and no hang. A client sees a server
  with no tools, which is the truth.
- A tool call returns the `forbidden` contract error — also correct — but worded
  `this key may not call math; allowed: files`, naming a tool nobody on this server can call.

So `denial` gains one case: when **none** of the scope's tools exist in this build's catalogue,
the message becomes `this key is scoped to tools this server does not provide (files); ask its
owner to re-scope it at /dashboard` rather than offering an impossible alternative. When some do,
the existing wording is right and is kept.

The dashboard shows the same thing rather than a blank grid: a key whose scope survives but
matches nothing carries a warning on its row saying it can call nothing until it is re-scoped.
A key that silently does nothing is a support ticket; a key that says why is a fix.

## Flows

### 1. Discovery (what an agent sees first)

`POST /mcp` with no credential returns **401**, and — this is the change that makes discovery
work — the header now carries the pointer the spec requires:

```
WWW-Authenticate: Bearer realm="leftbrain",
                  resource_metadata="https://leftbrain.idlesync.in/.well-known/oauth-protected-resource/mcp"
```

The body keeps its existing three fields (`ok`, `error`, `message`) and **adds** fields written
for an agent to act on and to read aloud:

```json
{
  "ok": false, "error": "missing key", "message": "send Authorization: Bearer <key>",
  "how_to_authorize": {
    "if_you_have_a_browser": "https://leftbrain.idlesync.in/.well-known/oauth-protected-resource/mcp",
    "if_you_have_no_browser": "POST https://leftbrain.idlesync.in/oauth/device_authorization",
    "tell_your_user": "leftbrain needs authorising. I can give you a short code to approve at https://leftbrain.idlesync.in/device",
    "static_key_alternative": "https://leftbrain.idlesync.in/dashboard",
    "documentation": "https://leftbrain.idlesync.in/docs/agents/auth"
  }
}
```

Additive only. A valid key never sees this body, so constraint 1 holds.

### 2. Browser flow (a person, or an MCP client that can open one)

1. Client discovers the protected-resource document, then the authorization-server metadata.
2. Client identifies itself by **CIMD** (an HTTPS URL as `client_id`, fetched through the SSRF
   guard) or by **dynamic registration** at `/register`. Both are supported: DCR is deprecated
   in MCP 2026-07-28 but kept for a twelve-month window, and ChatGPT recommends CIMD.
3. `GET /authorize` with PKCE. The provider checks `oauth_consents`:
   - **already approved, key usable** → issue a code against the existing key immediately.
   - **not approved** → redirect to `/oauth/consent`, which requires a signed-in dashboard user
     (reusing `web/auth.py`'s session), shows the client name, the redirect host, the tool scope
     picker and the remaining key slots, and carries a CSRF token.
4. On approve: mint the key via `create_for_owner`, write `oauth_consents`, **now** set the
   signed state cookie, store a single-use code, redirect to the client's exact registered
   `redirect_uri` with `code` and `state`.
5. `POST /token` with `code_verifier` → access token (1 h) + refresh token (30 d).

### 3. Device flow (an agent with no browser) — RFC 8628

1. Agent `POST /oauth/device_authorization` with its `client_id`. Response:
   `device_code`, `user_code` (e.g. `WXYZ-1234`), `verification_uri`,
   `verification_uri_complete`, `expires_in` (600), `interval` (5).
2. Agent tells its human, in words it was handed rather than words it invented:

   ```
   To connect leftbrain, visit:  https://leftbrain.idlesync.in/device
   Enter code:  WXYZ-1234
   Waiting…
   ```

3. Human opens `/device`, signs in if needed, and gets **the same consent screen** — client
   name, scope picker, slot count, CSRF. Approving mints the key exactly as the browser flow does.
4. Agent polls `POST /token` with `grant_type=urn:ietf:params:oauth:grant-type:device_code`,
   receiving `authorization_pending`, `slow_down`, `access_denied` or `expired_token` until it
   gets a token.

Advertised in the authorization-server metadata as `device_authorization_endpoint` and
`urn:ietf:params:oauth:grant-type:device_code` in `grant_types_supported`, which is exactly what
a client checks for before offering the flow.

### 4. Calling a tool with the token

`AuthMiddleware` grows a **third credential source**, after the static key and the key store:

```
supplied does not start with lblz_
  -> store.verify_oauth_token_and_count(supplied)
       resolve token_hash -> oauth_tokens -> key_hash -> keys row
       then the identical metering path as verify_and_count
```

`verify_and_count`'s body is extracted into a shared `_meter(row)` so both credential kinds run
one implementation. Quota headers, rpm windows, daily counters, `current_scope` and the
`tools/list` trimming are therefore literally the same code — a scoped connector sees only its
tools, and a call outside its scope returns the `forbidden` contract error, not a transport error.

## Documentation

### Two doors, presented as equals

Every page that explains connecting now shows **both** ways, neither buried under the other:

> **Paste a key.** Create one at `/dashboard`, copy it, put it in your client's config. Works
> everywhere, and it is the only way for clients with no OAuth support.
>
> **Or connect with OAuth.** Click connect in your app, approve leftbrain's page, done — you
> never see or handle a key. leftbrain creates one for you, names it after the app, and shows it
> on your dashboard where you can read it, re-scope it or revoke it like any other.

That second paragraph must say the key is created and visible, not just "you're connected".
A credential appearing on someone's dashboard that they did not knowingly create is a surprise;
saying so up front turns it into a feature.

Every place that describes connecting then carries the **tighten it afterwards** advice, in the
imperative rather than as a capability note:

> **Then narrow it.** Connect first with everything allowed, use it for a day, and look at what
> it actually called. Then open the key on your dashboard, choose **Edit scope**, and untick the
> rest. It applies to the connector's very next call — nothing to reconnect and nothing to
> re-approve.

Told in that order — connect, observe, narrow — because it is advice someone will follow. "Pick
your tools now" at consent time asks a question the person cannot yet answer, so they tick
everything and never revisit it, which is how a broad grant becomes permanent.

Covered in `README.md`, `web/docs/quickstart.md` and `web/docs/clients.md`. `clients.md` gains a
**Connect ChatGPT** section replacing the header-injecting-proxy workaround shipped in #49, a
**Connect Claude Code** section, and a **Connect from a terminal** section for the device flow —
the last carrying the plain warning that no shipping client drives it yet, so it is for agents
calling the endpoints directly.

### The agent document

`docs/agents/auth.md`, served at `/docs/agents/auth`, written for a model rather than a person:
every path in order, with the exact requests, the exact fields, and what to say to a human at
each decision point. It is reachable three ways so an agent finds it however it arrives:

- the `documentation` field in the 401 body,
- RFC 9728's `resource_documentation` field in the protected-resource metadata, which is the
  standard place for exactly this and costs one argument to
  `create_protected_resource_routes()`,
- a link from `/docs/clients`.

`web/docs/clients.md` gains a **Connect ChatGPT** section replacing the header-injecting-proxy
workaround shipped in #49, and a **Connect from a terminal** section for the device flow.

## Configuration

| Variable | Meaning |
|---|---|
| `LEFTBRAIN_BASE_URL` | Now **required** when OAuth is enabled. RFC 8414 compares the issuer by exact string; deriving it from request headers cannot be trusted for an issuer. Startup refuses to enable OAuth without it and says so. |
| `LEFTBRAIN_OAUTH` | `1`/`0`, default on when a key store and `LEFTBRAIN_SECRET` are both present. |
| `LEFTBRAIN_MAX_KEYS_PER_EMAIL` | Existing. Default rises 3 → 5. The lever a paid tier would move. |
| `LEFTBRAIN_CIMD_ALLOW_INSECURE` | Local development only: permit `http://` and loopback client-ID documents. Off by default; a warning is printed when on. |

OAuth is inert without a key store and a secret, so a server running with a static key alone is
unchanged.

## Testing

Every flow is driven end to end through `TestClient` against a real SQLite store; the SDK's own
handlers are exercised rather than mocked.

- **Regression guard, run at every step**: an `lblz_…` client gets identical status, body, quota
  headers and scoped `tools/list` before and after the feature.
- Discovery: 401 carries `resource_metadata`; both well-known documents parse; metadata
  advertises PKCE `S256`, CIMD, registration, revocation and the device grant.
- Browser flow end to end: register → consent → code → token → `POST /mcp` succeeds.
- Device flow end to end: device_authorization → poll (`authorization_pending`) → approve →
  poll → token → `POST /mcp` succeeds.
- **Key cap**: at the cap consent refuses and the client receives the actionable message;
  re-consenting an approved client reuses its key and does not consume a slot; the cap honours
  `LEFTBRAIN_MAX_KEYS_PER_EMAIL` rather than a literal.
- Revoking the key on the dashboard makes its tokens 401 on the next call.
- A scoped connector's `tools/list` is trimmed, and an out-of-scope call returns the `forbidden`
  contract error.
- Security: PKCE verifier mismatch refused; `redirect_uri` differing from the registered one
  refused; consent POST without CSRF refused; a code replayed twice refused; a CIMD `client_id`
  resolving to a private or loopback address refused; no state cookie is set on a GET of the
  consent page.
- Postgres: the five tables migrate on both backends (`LEFTBRAIN_TEST_PG_URL`, opt-in).
- Regression for the observed failure: `POST /register` and both well-known paths return real
  documents rather than the catch-all 404, so a Claude Code session no longer reports
  `Dynamic client registration rejected: unsupported`.
- Client compatibility, asserted against the metadata document rather than trusted: `"none"` is
  in `token_endpoint_auth_methods_supported`, `client_id_metadata_document_supported` is `true`,
  `offline_access` is in `scopes_supported`, and `resource` in the protected-resource document
  equals the MCP URL exactly.
- Loopback redirect matching: `http://localhost:3118/callback` is accepted against a registered
  `http://localhost/callback`, and so is the `127.0.0.1` form; a **non**-loopback host with a
  differing port is still refused, and so is a loopback URI with a different path.
- Connector key naming: a loopback client is named `<app> · <OS>` from the consent request's
  `User-Agent`; a non-loopback (cloud) client is named `<app> · web` and never carries the
  approver's OS; a `client_name` containing markup is escaped where rendered.
- The minted key is revealable by its owner and refused to anyone else, exercising the existing
  `reveal` path rather than a new one.
- Per-tool counts: a successful call increments that key's row for that tool and nothing else; a
  call refused by scope increments nothing; two keys calling the same tool count separately; the
  scope editor renders the counts beside the tools, zeroes included.
- An agent proposing a narrowing: the proposal returns `202` and changes **nothing**; approving
  it applies the change and the dropped tool is gone from the next `tools/list`; declining
  discards it and the request is then spent; only the owner can see or approve it, and another
  signed-in user gets `404` rather than a `403` that would confirm it exists; a widening is
  refused `403` at proposal time and never becomes a request; a proposal that has gone stale —
  the owner narrowed further meanwhile — is refused `409` at approval; an OAuth token and a
  `lblz_` key propose identically; an unauthenticated call is `401`.
- A scope that grants nothing: `parse_scope([])` and `parse_scope({"tools": {}})` both raise
  rather than returning `None`, on both the consent screen and the device page, and no key is
  created; a key whose scope matches no tool in this build lists `{"tools": []}` promptly, is
  refused with a message naming the missing tools and `/dashboard` rather than offering
  `allowed: files`, and carries a warning on its dashboard row; the ordinary refusal wording is
  unchanged when some of the scope's tools do exist.

`pytest -q` and `ruff check src tests` green. Skips are checked with `-rs` before the run is
called green — the only legitimate skip is the opt-in Postgres one.

## Build order

0. Reproduce or refute claude-code#28293 against leftbrain with a static key, and record the
   answer here. It costs one command and decides whether the docs tell a Claude Code user to
   paste a key or to use OAuth.
1. Tables in `_SCHEMA` + `OAuthStore` round-trips (SQLite and Postgres).
2. `verify_oauth_token_and_count` + the `_meter` refactor, with the `lblz_` guard green.
3. Provider's ten methods against the store.
4. Mount SDK routes; amend the metadata (`none`, CIMD, `offline_access`, device endpoint);
   discovery documents correct; MCP Inspector reaches the consent step.
5. Consent screen, key cap (including the 3 → 5 default), per-client consent registry,
   loopback-aware redirect matching, and the `<app> · <where>` naming.
6. Device grant.
7. CIMD + SSRF guard, and pruning of unused DCR registrations.
8. Per-tool call counts, and the scope editor that shows them.
9. `POST /keys/me/scope` — proposal only — and the owner's approval page.
10. The refusal and the dashboard warning for a scope that grants nothing.
11. Both-doors documentation, agent document, ChatGPT / Claude Code / terminal client sections,
    README, CHANGELOG.

Steps 8 to 10 are the smaller half of "connect, observe, narrow": without the counts the advice
has no evidence behind it, without the endpoint only a human can start the conversation, and
without step 10 a key that ends up granting nothing fails silently instead of saying why.

MCP Inspector against a local `leftbrain-serve` is the development loop, not ChatGPT. A Claude
Code session pointed at the same local server is the second loop and the cheaper one, since it
is the client whose exact failure is recorded above and it needs no browser configuration.
