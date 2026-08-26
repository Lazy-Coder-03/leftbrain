# leftbrain web site: landing, GitHub login, key dashboard, docs quickstart

Status: approved design, 2026-08-26. Sub-project 1 of 2 (sub-project 2 = full docs reference).
Visual direction: **A · Graph Paper** (see mockup artifact "leftbrain Directions").

## Goal

Turn `https://leftbrain.idlesync.in` from a JSON-only endpoint into a product site:
people land, understand the tool set in ten seconds, sign in with GitHub, create up to
three API keys, see usage, and follow a quickstart that works on Windows PowerShell,
macOS and Linux. API clients and the MCP endpoints keep working unchanged.

## Non-goals (this sub-project)

- Full per-tool reference pages, Python-library docs, self-hosting docs (sub-project 2).
- Usage history charts, key renaming, team accounts, email magic links, billing.
- A JavaScript framework or a separate frontend service.

## Architecture

```
src/leftbrain/web/
  __init__.py      build_web(store, cfg) -> list[Route|Mount]   (wired by serve.build_app)
  config.py        WebConfig from env: GITHUB_CLIENT_ID/SECRET, LEFTBRAIN_SECRET,
                   LEFTBRAIN_BASE_URL (optional), LEFTBRAIN_OPEN_SIGNUP (default off)
  auth.py          GitHub OAuth (authorize URL, code exchange, user+emails fetch),
                   session cookie sign/verify (itsdangerous), CSRF token
  views.py         landing, docs, login/callback/logout, dashboard, demo endpoint
  demo.py          per-IP throttle + allow-list of demo tools/modes
  docs.py          Markdown -> HTML (markdown-it-py) with an :::os tab block extension
  templates/       base.html, landing.html, login.html, dashboard.html, docs.html, error.html
  static/          site.css, logo.svg, site.js (demo fetch + OS tabs + copy; the page works
                   without it except the live demo)
  docs/            quickstart.md, clients.md
```

- Dependencies added to the `server` extra: `jinja2>=3.1`, `itsdangerous>=2.1`,
  `markdown-it-py>=3`. `httpx` (already present) performs the GitHub calls.
- Templates, static files and markdown ship inside the wheel (hatch `include`), so the
  Dockerfile is unchanged.
- `serve.build_app` mounts the web routes before the MCP mounts. `AuthMiddleware` changes
  from "everything except PUBLIC_PATHS needs a bearer" to **"only PROTECTED_PREFIXES need a
  bearer"**: `/mcp`, `/external/mcp`, `/files/mcp`, `/keys/me`. Everything else passes through
  and is protected (or not) by the cookie session inside the view.
- `GET /` content-negotiates: if the `Accept` header prefers `text/html`, render the landing
  page; otherwise return the existing JSON service description (curl, SDKs and monitors keep
  working). `/healthz` unchanged.

## Login (GitHub OAuth, web flow)

1. `GET /login` — if OAuth is not configured, render `login.html` with a "sign-in is not
   configured on this server" notice (200). Otherwise create a random `state`, store it in a
   signed, 10-minute `lb_oauth` cookie, and redirect to
   `https://github.com/login/oauth/authorize?client_id=…&redirect_uri=<base>/auth/github/callback&scope=read:user user:email&state=…`.
2. `GET /auth/github/callback?code&state` — verify `state` matches the cookie (else 400
   error page), POST `https://github.com/login/oauth/access_token` (JSON accept), then
   GET `/user` and `/user/emails`. Choose the email with `primary && verified`; if none is
   verified, render `error.html` "verify your GitHub email address, then sign in again".
3. Set `lb_session` = signed payload `{login, email, avatar_url, iat}`; max-age 7 days;
   `HttpOnly; SameSite=Lax; Secure` when the request is HTTPS (derived from
   `x-forwarded-proto` or the scheme). Redirect to `/dashboard`.
4. `POST /logout` clears the cookie and redirects to `/`.
5. Base URL for the callback: `LEFTBRAIN_BASE_URL` if set, else `<x-forwarded-proto or
   scheme>://<host>` from the request.
6. CSRF: every dashboard form includes a hidden token = signed(session email) valid for
   the session's lifetime; POST handlers verify it (403 on mismatch).

Session payload is read by a helper `current_user(request) -> User | None`; expired or
tampered cookies behave as signed-out.

## Keys and data

- No schema change. `keys.owner` = the verified GitHub email; the dashboard "name" is the
  existing `note` column.
- `KeyStore` gains:
  - `list_by_owner(email) -> list[KeyInfo]` (all keys incl. disabled, newest first)
  - `create_for_owner(email, name) -> tuple[str, KeyInfo] | tuple[None, str]` — enforces
    `MAX_ACTIVE_KEYS_PER_EMAIL` (3), default quota/rpm, no IP throttle
  - `owns(email, prefix) -> bool`
- Revoke = existing `set_disabled(prefix, True)` (keeps usage rows). Only after `owns()`.
- Anonymous `POST /keys/signup` returns 404 `{"error":"unsupported","message":"sign in at
  /login to create a key"}` unless `LEFTBRAIN_OPEN_SIGNUP=1`.

## Pages and routes

| Route | Auth | Behaviour |
|---|---|---|
| `GET /` | – | Landing (HTML) or JSON by `Accept`. Hero, live demo, proof strip, 12-tool grid, contract example, footer. |
| `POST /demo/{tool}` | – | Runs the real tool for `numbers`, `convert`, `datetime`, `text` only. Body `{"mode":…, …}` forwarded to the core function. Per-IP limit 30/min (in-memory), 429 with `retry-after`. 404 for other tools. Response = the tool contract JSON. |
| `GET /docs`, `GET /docs/{page}` | – | Markdown pages (`quickstart`, `clients`) rendered in `docs.html` with sidebar. Unknown page → 404 page. |
| `GET /static/{path}` | – | StaticFiles, cache 1 day. |
| `GET /login` | – | GitHub redirect or "not configured" page. |
| `GET /auth/github/callback` | – | As above. |
| `POST /logout` | cookie | Clear session → `/`. |
| `GET /dashboard` | cookie else 302 `/login` | Stats (calls today across keys, quota per key, active keys/3), key table (prefix, name, created, used today / quota, status, revoke), create form. |
| `POST /dashboard/keys` | cookie + CSRF | Create; on success re-render dashboard with the full key shown once in a highlighted box; on cap reached show inline error. |
| `POST /dashboard/keys/{prefix}/revoke` | cookie + CSRF | 403 if not owner; else disable and redirect to `/dashboard`. |

Docs OS tabs: markdown uses a container block

```
:::os
### windows
(powershell fence)
### macos
(bash fence)
### linux
(bash fence)
:::
```

rendered as the tab control from the mockup; PowerShell examples use `curl.exe` and an
`Invoke-RestMethod` variant. Copy buttons via `site.js`; without JS all three blocks show.

## Visual design (Direction A · Graph Paper)

Tokens from the approved mockup: ground `#f6f7f4`, surface `#fff`, ink `#14201f`,
secondary `#4b5a58`, lines `#c9d2cf` / `#e3e8e5`, accent ink-blue `#1747c8`, faint 24 px
grid behind the hero. Type: IBM Plex Mono (display, labels, code) + IBM Plex Sans (body),
Google Fonts with system fallbacks. One stylesheet, no framework. Layout, copy and component
structure follow the mockup's Direction A screens (landing / login → keys / docs).

## Error handling

- OAuth errors (denied, bad state, GitHub 4xx/5xx, network) → `login.html` with a plain
  reason and a retry button. Never leak tokens or raw GitHub bodies.
- Missing/expired/tampered session → treated as signed-out (302 to `/login` on dashboard).
- Key cap, empty name (allowed, stored as NULL), revoke of unknown/foreign prefix → 403/404
  error page or inline message.
- Demo endpoint: invalid JSON → 400 contract error; tool exception → 500 contract error
  with a generic message; throttle → 429.

## Testing

`tests/test_web.py` with Starlette `TestClient`, a temp SQLite key store, and
`httpx.MockTransport` injected for GitHub:

- `/` returns HTML for `Accept: text/html`, JSON otherwise; `/healthz` unchanged.
- `/login` → 302 to github with `state` cookie; callback with wrong state → 400; happy path
  sets `lb_session` and redirects; unverified email → error page.
- `/dashboard` without cookie → 302 `/login`; with cookie → 200 listing keys.
- create → key shown once, listed as active; 4th active key → cap error; revoke own key →
  disabled; revoke other's key → 403; missing CSRF → 403.
- `/keys/signup` → 404 by default, 201 with `LEFTBRAIN_OPEN_SIGNUP=1`.
- `/demo/numbers` compare → exact result; unknown tool → 404; 31st call in a minute → 429.
- `/docs`, `/docs/quickstart`, `/docs/clients` → 200 containing the OS tab markup;
  `/static/site.css` → 200.
- MCP endpoints still require a bearer (existing `test_keys.py` stays green).

## Rollout

1. User creates a GitHub OAuth App: homepage `https://leftbrain.idlesync.in`, callback
   `https://leftbrain.idlesync.in/auth/github/callback`.
2. Northflank service env: `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `LEFTBRAIN_SECRET`
   (32+ random bytes), `LEFTBRAIN_BASE_URL=https://leftbrain.idlesync.in`.
3. Push to `main`; CD deploys. Verify: landing renders, login round-trip, key create → MCP call
   with that key, revoke → 401, `curl /` still JSON.
4. Update README "Per-user API keys" section and `docs/deploy-northflank.md` (env vars).
