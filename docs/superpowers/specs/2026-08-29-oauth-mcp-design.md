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
3. **Exact-string `redirect_uri` matching.** No wildcards, no prefix matching. A changed
   `redirect_uri` requires re-registration.
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

**One deviation from the SDK's defaults.** `build_metadata()` does not set
`client_id_metadata_document_supported`, and does not know about the device endpoint. We build
the metadata ourselves from `build_metadata()`, set both fields, and substitute our own metadata
route into the list `create_auth_routes()` returns. Everything else is the SDK's, CORS and
body-size handling included.

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

## Agent documentation

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

`pytest -q` and `ruff check src tests` green. Skips are checked with `-rs` before the run is
called green — the only legitimate skip is the opt-in Postgres one.

## Build order

1. Tables in `_SCHEMA` + `OAuthStore` round-trips (SQLite and Postgres).
2. `verify_oauth_token_and_count` + the `_meter` refactor, with the `lblz_` guard green.
3. Provider's ten methods against the store.
4. Mount SDK routes; discovery documents correct; MCP Inspector reaches the consent step.
5. Consent screen, key cap (including the 3 → 5 default), per-client consent registry.
6. Device grant.
7. CIMD + SSRF guard.
8. Agent documentation, ChatGPT and terminal client docs, README, CHANGELOG.

MCP Inspector against a local `leftbrain-serve` is the development loop, not ChatGPT. A Claude
Code session pointed at the same local server is the second loop and the cheaper one, since it
is the client whose exact failure is recorded above and it needs no browser configuration.
