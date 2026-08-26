# leftbrain Web Site (landing · GitHub login · key dashboard · docs quickstart) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `leftbrain-serve` a product web site: an HTML landing page with a live demo, GitHub OAuth sign-in, a cookie-session dashboard where a user creates/revokes up to three API keys, and a Markdown-driven `/docs` with Windows/macOS/Linux tabs — while every existing JSON/MCP endpoint keeps working unchanged.

**Architecture:** A new `leftbrain.web` package (Jinja2 templates, one CSS file, small progressive-enhancement JS, Markdown docs) is mounted into the existing Starlette app built by `serve.build_app`. `AuthMiddleware` flips from deny-by-default to protecting only the MCP and `/keys/me` paths; site pages authenticate with an itsdangerous-signed cookie set after the GitHub OAuth web flow. `/` content-negotiates between the landing page and the existing JSON description.

**Tech Stack:** Python 3.11+, Starlette, Jinja2, itsdangerous, markdown-it-py, httpx (GitHub calls + test MockTransport), pytest with `starlette.testclient.TestClient`, SQLite key store in tests.

**Spec:** `docs/superpowers/specs/2026-08-26-web-site-auth-dashboard-design.md`

## Global Constraints

- Python `>=3.11`; ruff `line-length = 100` (E501 ignored), rules `E,F,I,UP,B`. Run `ruff check src tests` before each commit.
- New deps go in the `server`, `all` and `dev` extras only: `jinja2>=3.1`, `itsdangerous>=2.1`, `markdown-it-py>=3`.
- Bearer-protected prefixes (exact): `/mcp`, `/external/mcp`, `/files/mcp`, `/keys/me`. Everything else passes the middleware.
- Cookies: `lb_session` (7 days, HttpOnly, SameSite=Lax, Secure on HTTPS), `lb_oauth` (10 min, same flags). Env: `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `LEFTBRAIN_SECRET`, `LEFTBRAIN_BASE_URL`, `LEFTBRAIN_OPEN_SIGNUP` (default off).
- Key rules: `MAX_ACTIVE_KEYS_PER_EMAIL` (3) enforced; keys shown once; dashboard "name" stored in the existing `note` column; revoke = `set_disabled(prefix, True)`.
- Demo endpoint: only `numbers`, `convert`, `datetime`, `text`; 30 requests / IP / minute.
- Visual direction A · Graph Paper tokens (verbatim): bg `#f6f7f4`, surface `#ffffff`, ink `#14201f`, ink2 `#4b5a58`, line `#c9d2cf`, line2 `#e3e8e5`, accent `#1747c8`, accent-soft `#e2e9fb`, good `#1b7a4a`, warn `#a15c00`, bad `#b3261e`; fonts IBM Plex Mono + IBM Plex Sans (Google Fonts) with system fallbacks; 24 px grid behind the hero.
- Never add a Claude/AI watermark, Co-Authored-By line, or "generated with" note anywhere (repo rule).
- Run the project venv: `.venv/Scripts/python.exe -m pytest -q` (Windows) — all existing tests must stay green after every task.

---

## File map

| Path | Responsibility |
|---|---|
| `pyproject.toml` | add web deps to `server`/`all`/`dev` extras |
| `src/leftbrain/web/__init__.py` | `build_web(store, cfg) -> list` of Starlette routes/mounts; Jinja env |
| `src/leftbrain/web/config.py` | `WebConfig` dataclass + `WebConfig.from_env()` |
| `src/leftbrain/web/auth.py` | session cookie sign/read, CSRF, `current_user`, GitHub OAuth helpers |
| `src/leftbrain/web/views.py` | route handlers: landing, login/callback/logout, dashboard, demo, docs |
| `src/leftbrain/web/demo.py` | demo tool allow-list + per-IP throttle |
| `src/leftbrain/web/docs.py` | Markdown → HTML with `:::os` tab blocks |
| `src/leftbrain/web/templates/*.html` | base, landing, login, dashboard, docs, error |
| `src/leftbrain/web/static/site.css`, `site.js`, `logo.svg` | Direction A styling + progressive enhancement |
| `src/leftbrain/web/docs/quickstart.md`, `clients.md` | docs content |
| `src/leftbrain/keys.py` | `create_for_owner`, `owns` |
| `src/leftbrain/serve.py` | `PROTECTED_PREFIXES`, mount web routes, content-negotiated `/`, signup gating |
| `tests/test_web.py` | all web tests |
| `README.md`, `docs/deploy-northflank.md` | env vars + login docs |

---

### Task 1: Dependencies, web scaffold, middleware flip, content-negotiated `/`

**Files:**
- Modify: `pyproject.toml` (extras lines 39–42)
- Create: `src/leftbrain/web/__init__.py`, `src/leftbrain/web/config.py`, `src/leftbrain/web/templates/base.html`, `src/leftbrain/web/templates/error.html`, `src/leftbrain/web/static/site.css` (minimal for now; full styling in Task 7), `src/leftbrain/web/static/logo.svg`
- Modify: `src/leftbrain/serve.py` (`PUBLIC_PATHS`, `AuthMiddleware.__call__`, `build_app` routes, `index`)
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `leftbrain.web.config.WebConfig(client_id: str | None, client_secret: str | None, secret: str | None, base_url: str | None, open_signup: bool, github_transport: Any | None = None)` with classmethod `from_env()` and property `oauth_ready: bool`.
- Produces: `leftbrain.web.build_web(store, cfg) -> list[Route | Mount]` and `leftbrain.web.templates` (a `starlette.templating.Jinja2Templates`).
- Produces: `serve.build_app(..., web_config: WebConfig | None = None)`; when `None`, `WebConfig.from_env()` is used.
- Produces: `serve.PROTECTED_PREFIXES = ("/mcp", "/external/mcp", "/files/mcp", "/keys/me")`.

- [ ] **Step 1: Add dependencies**

In `pyproject.toml` replace the `server`, `all`, `dev` lines with:

```toml
server = ["mcp>=2.0", "httpx>=0.27", "uvicorn>=0.30", "starlette>=0.40", "jinja2>=3.1", "itsdangerous>=2.1", "markdown-it-py>=3"]
postgres = ["psycopg[binary]>=3.1"]
all = ["mcp>=2.0", "httpx>=0.27", "pypdf>=4.0", "pillow>=10.0", "uvicorn>=0.30", "starlette>=0.40", "jinja2>=3.1", "itsdangerous>=2.1", "markdown-it-py>=3", "psycopg[binary]>=3.1"]
dev = ["mcp>=2.0", "httpx>=0.27", "pypdf>=4.0", "pillow>=10.0", "uvicorn>=0.30", "starlette>=0.40", "jinja2>=3.1", "itsdangerous>=2.1", "markdown-it-py>=3", "psycopg[binary]>=3.1", "pytest>=8", "ruff>=0.6"]
```

Run: `cd "D:\ML projects\leftbrain" && .venv/Scripts/python.exe -m pip install -e ".[dev]" -q`

- [ ] **Step 2: Write the failing tests**

Create `tests/test_web.py`:

```python
from starlette.testclient import TestClient

from leftbrain.serve import build_app
from leftbrain.web.config import WebConfig


def make_app(tmp_path, **cfg):
    config = WebConfig(client_id=None, client_secret=None, secret="test-secret-0123456789", base_url=None, open_signup=False, **cfg)
    return build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"), web_config=config)


def test_root_negotiates_html_and_json(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        html = c.get("/", headers={"Accept": "text/html,application/xhtml+xml"})
        assert html.status_code == 200 and html.headers["content-type"].startswith("text/html")
        assert "leftbrain" in html.text
        js = c.get("/", headers={"Accept": "*/*"})
        assert js.headers["content-type"].startswith("application/json") and js.json()["auth"] == "keys"


def test_static_and_healthz(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        assert c.get("/static/site.css").status_code == 200
        assert c.get("/healthz").json()["ok"]


def test_mcp_still_needs_bearer(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        assert c.post("/mcp", json={}).status_code == 401
        assert c.get("/keys/me").status_code == 401
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leftbrain.web'`

- [ ] **Step 4: Create `config.py`**

```python
"""Web-layer configuration (GitHub OAuth, cookie secret, base URL)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class WebConfig:
    client_id: str | None
    client_secret: str | None
    secret: str | None
    base_url: str | None
    open_signup: bool
    github_transport: Any | None = None  # httpx transport override for tests

    @classmethod
    def from_env(cls) -> WebConfig:
        env = os.environ.get
        return cls(
            client_id=env("GITHUB_CLIENT_ID") or None,
            client_secret=env("GITHUB_CLIENT_SECRET") or None,
            secret=env("LEFTBRAIN_SECRET") or None,
            base_url=(env("LEFTBRAIN_BASE_URL") or "").rstrip("/") or None,
            open_signup=env("LEFTBRAIN_OPEN_SIGNUP", "0") in ("1", "true", "yes"),
        )

    @property
    def oauth_ready(self) -> bool:
        return bool(self.client_id and self.client_secret and self.secret)
```

- [ ] **Step 5: Create `web/__init__.py`, templates and static placeholders**

`src/leftbrain/web/__init__.py`:

```python
"""HTML site for leftbrain-serve: landing, GitHub login, key dashboard, docs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from .. import __version__
from .config import WebConfig

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.globals["version"] = __version__


def build_web(store: Any, cfg: WebConfig) -> list[Any]:
    """Routes for the site. Later tasks append handlers here."""
    from . import views

    return [
        *views.routes(store, cfg),
        Mount("/static", app=StaticFiles(directory=str(HERE / "static")), name="static"),
    ]
```

`src/leftbrain/web/views.py` (Task 1 version — later tasks extend it):

```python
"""Route handlers for the web site."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from . import templates
from .config import WebConfig


def wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def render(request: Request, name: str, status: int = 200, **ctx: Any) -> Response:
    return templates.TemplateResponse(request, name, ctx, status_code=status)


def error_page(request: Request, status: int, title: str, message: str) -> Response:
    return render(request, "error.html", status, title=title, message=message)


def routes(store: Any, cfg: WebConfig) -> list[Any]:
    return []
```

`src/leftbrain/web/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}leftbrain{% endblock %}</title>
<meta name="description" content="Exact, deterministic tools for AI agents: math, dates, units, ordering, validation. MCP server and REST API.">
<link rel="icon" href="/static/logo.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<link rel="stylesheet" href="/static/site.css">
</head>
<body>
<div class="wrap">
  <nav class="nav">
    <a class="brand" href="/"><img src="/static/logo.svg" alt="" width="26" height="26">leftbrain</a>
    <div class="links">
      <a href="/#tools" {% if page == 'landing' %}aria-current="page"{% endif %}>Tools</a>
      <a href="/docs" {% if page == 'docs' %}aria-current="page"{% endif %}>Docs</a>
      <a href="/dashboard" {% if page == 'dashboard' %}aria-current="page"{% endif %}>Keys</a>
    </div>
    {% if user %}<a class="btn ghost" href="/dashboard">{{ user.login }}</a>{% else %}<a class="btn primary" href="/login">Get an API key</a>{% endif %}
  </nav>
  {% block content %}{% endblock %}
  <footer><span>© 2026 leftbrain · MIT · v{{ version }}</span><span><a href="https://github.com/Lazy-Coder-03/leftbrain">GitHub</a> · <a href="https://pypi.org/project/leftbrain/">PyPI</a> · <a href="/healthz">Status</a></span></footer>
</div>
<script src="/static/site.js" defer></script>
</body>
</html>
```

`src/leftbrain/web/templates/error.html`:

```html
{% extends "base.html" %}
{% block title %}{{ title }} · leftbrain{% endblock %}
{% block content %}
<section class="login">
  <h2>{{ title }}</h2>
  <p>{{ message }}</p>
  <a class="btn ghost" href="/">Back to leftbrain</a>
</section>
{% endblock %}
```

`src/leftbrain/web/static/logo.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 26 26" width="26" height="26"><circle cx="13" cy="13" r="11.5" fill="none" stroke="#14201f" stroke-width="2"/><path d="M13 1.5A11.5 11.5 0 0 0 13 24.5Z" fill="#1747c8"/><rect x="12" y="1" width="2" height="24" fill="#14201f"/></svg>
```

`src/leftbrain/web/static/site.css` (placeholder; Task 7 replaces it):

```css
:root{--bg:#f6f7f4;--bg2:#ffffff;--ink:#14201f;--ink2:#4b5a58;--line:#c9d2cf;--line2:#e3e8e5;--accent:#1747c8;--accent-soft:#e2e9fb;--good:#1b7a4a;--warn:#a15c00;--bad:#b3261e}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"IBM Plex Sans",system-ui,sans-serif}
.wrap{max-width:1160px;margin:0 auto;padding:0 24px}
```

Create an empty `src/leftbrain/web/static/site.js` containing only `// progressive enhancement; filled in Task 7`.

- [ ] **Step 6: Modify `serve.py`**

Replace `PUBLIC_PATHS = {"/", "/healthz", "/keys/signup"}` with:

```python
PROTECTED_PREFIXES = ("/mcp", "/external/mcp", "/files/mcp", "/keys/me")


def _protected(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in PROTECTED_PREFIXES)
```

In `AuthMiddleware.__call__` change the first guard to:

```python
        if scope["type"] != "http" or not _protected(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
```

In `build_app`, add the parameter `web_config: Any | None = None` to the signature, and after `auth_kind = ...` add:

```python
    from .web import build_web
    from .web.config import WebConfig

    cfg = web_config or WebConfig.from_env()
```

Replace the `index` handler with:

```python
    async def index(request: Request) -> Any:
        if "text/html" in request.headers.get("accept", ""):
            from .web.views import landing

            return await landing(request, store, cfg)
        return JSONResponse({"name": "leftbrain", "version": __version__, "description": "Exact, deterministic tools for AI agents", "endpoints": {"core": "/mcp", **({"external": "/external/mcp"} if include_external else {}), **({"files": "/files/mcp"} if include_files else {})}, "auth": auth_kind, "signup": "/keys/signup" if (store and cfg.open_signup) else None, "login": "/login", "docs": "/docs", "transport": "streamable-http", "stateless": stateless})
```

Replace the `routes` line with:

```python
    routes: list[Any] = [Route("/", index), Route("/healthz", healthz), Route("/keys/signup", signup, methods=["POST"]), Route("/keys/me", me), *build_web(store, cfg), *mounts, Mount("", app=root_app)]
```

Add a temporary `landing` in `views.py` so Task 1 passes (Task 7 replaces its template):

```python
async def landing(request: Request, store: Any, cfg: WebConfig) -> Response:
    return render(request, "error.html", 200, title="leftbrain", message="Landing page coming in Task 7.", page="landing", user=None)
```

- [ ] **Step 7: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web.py tests/test_keys.py -v`
Expected: all PASS (the `/` HTML check passes because "leftbrain" appears in the nav).

- [ ] **Step 8: Lint and commit**

```bash
.venv/Scripts/python.exe -m ruff check src tests
git add pyproject.toml src/leftbrain/web src/leftbrain/serve.py tests/test_web.py
git commit -m "web: scaffold site package, protect only MCP paths, negotiate HTML at /"
```

---

### Task 2: KeyStore owner methods and signup gating

**Files:**
- Modify: `src/leftbrain/keys.py` (after `signup`, ~line 273)
- Modify: `src/leftbrain/serve.py` (`signup` handler)
- Test: `tests/test_keys.py`, `tests/test_web.py`

**Interfaces:**
- Produces: `KeyStore.create_for_owner(email: str, name: str | None) -> tuple[str | None, KeyInfo | str]` — returns `(raw_key, KeyInfo)` or `(None, reason)`.
- Produces: `KeyStore.owns(email: str, prefix: str) -> bool`.
- Consumes: `serve.build_app(web_config=WebConfig(open_signup=...))` from Task 1.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_keys.py`:

```python
def test_create_for_owner_cap_and_owns(tmp_path):
    store = KeyStore(str(tmp_path / "k.sqlite3"))
    made = [store.create_for_owner("Me@Example.com", f"key {i}") for i in range(3)]
    assert all(raw and raw.startswith("lblz_") for raw, _ in made)
    assert made[0][1].owner == "me@example.com" and made[0][1].note == "key 0"
    raw, reason = store.create_for_owner("me@example.com", None)
    assert raw is None and "3 active" in reason
    prefix = made[0][1].prefix
    assert store.owns("me@example.com", prefix) and not store.owns("other@example.com", prefix)
    assert store.set_disabled(prefix, True)
    raw, info = store.create_for_owner("me@example.com", "")  # slot freed, empty name -> None
    assert raw and info.note is None
```

Append to `tests/test_web.py`:

```python
def test_signup_closed_by_default_open_by_flag(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = c.post("/keys/signup", json={"email": "a@b.co"})
        assert r.status_code == 404 and "/login" in r.json()["message"]
    with TestClient(make_app(tmp_path, open_signup=True)) as c:
        assert c.post("/keys/signup", json={"email": "a@b.co"}).status_code == 201
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_keys.py::test_create_for_owner_cap_and_owns tests/test_web.py::test_signup_closed_by_default_open_by_flag -v`
Expected: FAIL — `AttributeError: 'KeyStore' object has no attribute 'create_for_owner'`; signup returns 201 instead of 404.

- [ ] **Step 3: Implement in `keys.py`**

Insert after the `signup` method:

```python
    def create_for_owner(self, email: str, name: str | None, *, daily_quota: int = DEFAULT_DAILY, rpm: int = DEFAULT_RPM) -> tuple[str | None, Any]:
        """Dashboard key creation: verified owner, enforce the active-key cap, no IP throttle."""
        email = (email or "").strip().lower()
        with self._lock:
            active = int(self.db.scalar("SELECT COUNT(*) FROM keys WHERE owner=? AND disabled=0", (email,)) or 0)
            if active >= MAX_ACTIVE_KEYS_PER_EMAIL:
                return None, f"you already have {MAX_ACTIVE_KEYS_PER_EMAIL} active keys; revoke one first"
        note = (name or "").strip()[:40] or None
        return self.create(email, note=note, daily_quota=daily_quota, rpm=rpm)

    def owns(self, email: str, prefix: str) -> bool:
        row = self.db.one("SELECT owner FROM keys WHERE prefix = ?", (prefix,))
        return bool(row) and row["owner"] == (email or "").strip().lower()
```

- [ ] **Step 4: Gate signup in `serve.py`**

In the `signup` handler, change the first guard to:

```python
        if store is None or not cfg.open_signup:
            return JSONResponse({"ok": False, "error": "unsupported", "message": "self-serve signup is closed; sign in at /login to create a key"}, status_code=404)
```

Also update `main()`'s startup print: `"signup": "/keys/signup" if (keys_db and os.environ.get("LEFTBRAIN_OPEN_SIGNUP", "0") in ("1", "true", "yes")) else None`.

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all PASS. Note `test_http_server_with_keys` in `tests/test_keys.py` calls `/keys/signup` — update its `build_app(...)` call to pass `web_config=WebConfig(None, None, "s" * 20, None, True)` (import `from leftbrain.web.config import WebConfig`) and keep the 404 assertion at the end of that file (`assert c.post("/keys/signup", ...).status_code == 404` for the store-less app) unchanged.

- [ ] **Step 6: Commit**

```bash
.venv/Scripts/python.exe -m ruff check src tests
git add src/leftbrain/keys.py src/leftbrain/serve.py tests/test_keys.py tests/test_web.py
git commit -m "keys: owner-scoped create/owns; close anonymous signup unless LEFTBRAIN_OPEN_SIGNUP=1"
```

---

### Task 3: Session cookie, CSRF, `current_user`

**Files:**
- Create: `src/leftbrain/web/auth.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces:
  - `User` dataclass `(login: str, email: str, avatar_url: str | None)`
  - `sign_session(secret: str, user: User) -> str`
  - `read_session(secret: str, value: str | None, max_age: int = 7 * 86400) -> User | None`
  - `current_user(request, cfg) -> User | None`
  - `set_session_cookie(response, request, cfg, user) -> None`, `clear_session_cookie(response, request) -> None`
  - `csrf_token(secret: str, user: User) -> str`, `csrf_ok(secret: str, user: User, token: str | None) -> bool`
  - `is_https(request) -> bool`, `base_url(request, cfg) -> str`
- Cookie names: `SESSION_COOKIE = "lb_session"`, `OAUTH_COOKIE = "lb_oauth"`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_web.py`:

```python
from leftbrain.web import auth


def test_session_roundtrip_and_tamper():
    u = auth.User(login="octo", email="octo@example.com", avatar_url=None)
    tok = auth.sign_session("s3cret", u)
    assert auth.read_session("s3cret", tok) == u
    assert auth.read_session("other", tok) is None
    assert auth.read_session("s3cret", tok + "x") is None
    assert auth.read_session("s3cret", None) is None
    assert auth.read_session("s3cret", tok, max_age=-1) is None  # expired


def test_csrf():
    u = auth.User("octo", "octo@example.com", None)
    t = auth.csrf_token("s3cret", u)
    assert auth.csrf_ok("s3cret", u, t)
    assert not auth.csrf_ok("s3cret", auth.User("x", "x@example.com", None), t)
    assert not auth.csrf_ok("s3cret", u, None) and not auth.csrf_ok("s3cret", u, "nope")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web.py -k "session or csrf" -v`
Expected: FAIL — `ImportError: cannot import name 'auth'`

- [ ] **Step 3: Implement `auth.py` (session + CSRF part)**

```python
"""Cookie sessions, CSRF tokens and the GitHub OAuth web flow."""

from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass
from typing import Any

from itsdangerous import BadSignature, URLSafeSerializer, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import Response

from .config import WebConfig

SESSION_COOKIE = "lb_session"
OAUTH_COOKIE = "lb_oauth"
SESSION_MAX_AGE = 7 * 86400
OAUTH_MAX_AGE = 600


@dataclass(frozen=True)
class User:
    login: str
    email: str
    avatar_url: str | None


def sign_session(secret: str, user: User) -> str:
    return URLSafeTimedSerializer(secret, salt="lb-session").dumps(asdict(user))


def read_session(secret: str, value: str | None, max_age: int = SESSION_MAX_AGE) -> User | None:
    if not value or not secret:
        return None
    try:
        data = URLSafeTimedSerializer(secret, salt="lb-session").loads(value, max_age=max_age)
    except BadSignature:
        return None
    try:
        return User(login=str(data["login"]), email=str(data["email"]), avatar_url=data.get("avatar_url"))
    except (KeyError, TypeError, AttributeError):
        return None


def is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip() == "https"


def base_url(request: Request, cfg: WebConfig) -> str:
    if cfg.base_url:
        return cfg.base_url
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{'https' if is_https(request) else 'http'}://{host}"


def current_user(request: Request, cfg: WebConfig) -> User | None:
    return read_session(cfg.secret or "", request.cookies.get(SESSION_COOKIE))


def set_session_cookie(response: Response, request: Request, cfg: WebConfig, user: User) -> None:
    response.set_cookie(SESSION_COOKIE, sign_session(cfg.secret or "", user), max_age=SESSION_MAX_AGE, httponly=True, samesite="lax", secure=is_https(request), path="/")


def clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def csrf_token(secret: str, user: User) -> str:
    return URLSafeSerializer(secret, salt="lb-csrf").dumps(user.email)


def csrf_ok(secret: str, user: User, token: str | None) -> bool:
    if not token:
        return False
    try:
        return URLSafeSerializer(secret, salt="lb-csrf").loads(token) == user.email
    except BadSignature:
        return False


def new_state() -> str:
    return secrets.token_urlsafe(24)


def sign_state(secret: str, state: str) -> str:
    return URLSafeTimedSerializer(secret, salt="lb-oauth").dumps(state)


def read_state(secret: str, value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(URLSafeTimedSerializer(secret, salt="lb-oauth").loads(value, max_age=OAUTH_MAX_AGE))
    except BadSignature:
        return None
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
.venv/Scripts/python.exe -m ruff check src tests
git add src/leftbrain/web/auth.py tests/test_web.py
git commit -m "web: signed session cookie, CSRF token, oauth state helpers"
```

---

### Task 4: GitHub OAuth routes (`/login`, `/auth/github/callback`, `/logout`)

**Files:**
- Modify: `src/leftbrain/web/auth.py` (append GitHub helpers)
- Modify: `src/leftbrain/web/views.py` (add handlers + `routes()`)
- Create: `src/leftbrain/web/templates/login.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces in `auth.py`:
  - `GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"`, `GITHUB_TOKEN = "https://github.com/login/oauth/access_token"`, `GITHUB_API = "https://api.github.com"`
  - `authorize_url(cfg, redirect_uri: str, state: str) -> str`
  - `async fetch_github_user(cfg, code: str, redirect_uri: str) -> User` — raises `OAuthError(message)` on any failure; raises `OAuthError("verify your GitHub email address, then sign in again")` when no primary verified email.
- Produces in `views.py`: `routes(store, cfg)` now returns `Route("/login", login)`, `Route("/auth/github/callback", callback)`, `Route("/logout", logout, methods=["POST"])`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_web.py`:

```python
import json as _json

import httpx


def github_transport(emails=None, token="gho_test"):
    """Fake GitHub: token exchange, /user, /user/emails."""
    emails = emails if emails is not None else [{"email": "octo@example.com", "primary": True, "verified": True}]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "github.com" and req.url.path == "/login/oauth/access_token":
            body = dict(x.split("=") for x in req.content.decode().split("&")) if req.content else {}
            assert req.headers["accept"] == "application/json"
            if body.get("code") == "bad":
                return httpx.Response(200, json={"error": "bad_verification_code"})
            return httpx.Response(200, json={"access_token": token, "token_type": "bearer"})
        if req.url.path == "/user":
            assert req.headers["authorization"] == f"Bearer {token}"
            return httpx.Response(200, json={"login": "octo", "avatar_url": "https://a/octo.png"})
        if req.url.path == "/user/emails":
            return httpx.Response(200, json=emails)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def oauth_app(tmp_path, **kw):
    return make_app(tmp_path, client_id="cid", client_secret="csec", github_transport=github_transport(**kw))


def test_login_not_configured(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = c.get("/login")
        assert r.status_code == 200 and "not configured" in r.text


def test_login_redirects_to_github_with_state(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        r = c.get("/login", follow_redirects=False)
        assert r.status_code == 302
        loc = r.headers["location"]
        assert loc.startswith("https://github.com/login/oauth/authorize?") and "client_id=cid" in loc and "state=" in loc
        assert "redirect_uri=http%3A%2F%2Ftestserver%2Fauth%2Fgithub%2Fcallback" in loc
        assert "lb_oauth" in r.cookies


def test_callback_bad_state_rejected(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        c.get("/login", follow_redirects=False)
        r = c.get("/auth/github/callback?code=ok&state=wrong", follow_redirects=False)
        assert r.status_code == 400 and "sign in again" in r.text


def login_via_github(c: TestClient) -> None:
    r = c.get("/login", follow_redirects=False)
    state = r.headers["location"].split("state=")[1].split("&")[0]
    r = c.get(f"/auth/github/callback?code=ok&state={state}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/dashboard"
    assert "lb_session" in c.cookies


def test_callback_happy_path_sets_session(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        r = c.post("/logout", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/"
        assert "lb_session" not in c.cookies


def test_callback_unverified_email(tmp_path):
    app = oauth_app(tmp_path, emails=[{"email": "octo@example.com", "primary": True, "verified": False}])
    with TestClient(app) as c:
        r = c.get("/login", follow_redirects=False)
        state = r.headers["location"].split("state=")[1].split("&")[0]
        r = c.get(f"/auth/github/callback?code=ok&state={state}")
        assert r.status_code == 403 and "verify your GitHub email" in r.text


def test_callback_github_error(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        r = c.get("/login", follow_redirects=False)
        state = r.headers["location"].split("state=")[1].split("&")[0]
        r = c.get(f"/auth/github/callback?code=bad&state={state}")
        assert r.status_code == 502 and "GitHub" in r.text and "gho_" not in r.text
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web.py -k "login or callback" -v`
Expected: FAIL — `/login` returns 404.

- [ ] **Step 3: Append GitHub helpers to `auth.py`**

```python
GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_API = "https://api.github.com"


class OAuthError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


def authorize_url(cfg: WebConfig, redirect_uri: str, state: str) -> str:
    from urllib.parse import urlencode

    q = urlencode({"client_id": cfg.client_id or "", "redirect_uri": redirect_uri, "scope": "read:user user:email", "state": state})
    return f"{GITHUB_AUTHORIZE}?{q}"


async def fetch_github_user(cfg: WebConfig, code: str, redirect_uri: str) -> User:
    import httpx

    async with httpx.AsyncClient(timeout=15, transport=cfg.github_transport) as client:
        try:
            tok = await client.post(GITHUB_TOKEN, data={"client_id": cfg.client_id, "client_secret": cfg.client_secret, "code": code, "redirect_uri": redirect_uri}, headers={"Accept": "application/json"})
            tok.raise_for_status()
            access = tok.json().get("access_token")
            if not access:
                raise OAuthError("GitHub did not accept the sign-in code. Please try again.")
            h = {"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json"}
            user = await client.get(f"{GITHUB_API}/user", headers=h)
            user.raise_for_status()
            emails = await client.get(f"{GITHUB_API}/user/emails", headers=h)
            emails.raise_for_status()
        except OAuthError:
            raise
        except httpx.HTTPError:
            raise OAuthError("GitHub could not be reached. Please try again in a minute.") from None
        except ValueError:
            raise OAuthError("GitHub returned an unexpected response. Please try again.") from None
    primary = next((e for e in emails.json() if e.get("primary") and e.get("verified")), None)
    if not primary:
        raise OAuthError("verify your GitHub email address, then sign in again", status=403)
    u = user.json()
    return User(login=str(u.get("login") or ""), email=str(primary["email"]).lower(), avatar_url=u.get("avatar_url"))
```

- [ ] **Step 4: Add handlers to `views.py`**

Replace the file's `routes` function and add handlers:

```python
from starlette.responses import RedirectResponse
from starlette.routing import Route

from . import auth


def routes(store: Any, cfg: WebConfig) -> list[Any]:
    async def login(request: Request) -> Response:
        if not cfg.oauth_ready:
            return render(request, "login.html", 200, page="login", user=None, notice="Sign-in is not configured on this server. Set GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET and LEFTBRAIN_SECRET.")
        state = auth.new_state()
        resp = RedirectResponse(auth.authorize_url(cfg, auth.base_url(request, cfg) + "/auth/github/callback", state), status_code=302)
        resp.set_cookie(auth.OAUTH_COOKIE, auth.sign_state(cfg.secret or "", state), max_age=auth.OAUTH_MAX_AGE, httponly=True, samesite="lax", secure=auth.is_https(request), path="/")
        return resp

    async def callback(request: Request) -> Response:
        if not cfg.oauth_ready:
            return error_page(request, 404, "Sign-in unavailable", "Sign-in is not configured on this server.")
        expected = auth.read_state(cfg.secret or "", request.cookies.get(auth.OAUTH_COOKIE))
        got = request.query_params.get("state")
        code = request.query_params.get("code")
        if not expected or not got or expected != got or not code:
            return render(request, "login.html", 400, page="login", user=None, notice="That sign-in link is stale or invalid. Please sign in again.")
        try:
            user = await auth.fetch_github_user(cfg, code, auth.base_url(request, cfg) + "/auth/github/callback")
        except auth.OAuthError as e:
            return render(request, "login.html", e.status, page="login", user=None, notice=e.message)
        resp = RedirectResponse("/dashboard", status_code=302)
        resp.delete_cookie(auth.OAUTH_COOKIE, path="/")
        auth.set_session_cookie(resp, request, cfg, user)
        return resp

    async def logout(request: Request) -> Response:
        resp = RedirectResponse("/", status_code=302)
        auth.clear_session_cookie(resp, request)
        return resp

    return [
        Route("/login", login),
        Route("/auth/github/callback", callback),
        Route("/logout", logout, methods=["POST"]),
    ]
```

Update `error_page` to pass `page="error", user=None` in its context (base.html reads them).

- [ ] **Step 5: Create `login.html`**

```html
{% extends "base.html" %}
{% block title %}Sign in · leftbrain{% endblock %}
{% block content %}
<section class="login">
  <h2>Sign in to get a key</h2>
  <p>Keys are tied to your GitHub account's primary email. Free tier: 3 keys, 5,000 calls/day each, 60 requests/minute.</p>
  {% if notice %}<p class="notice" role="alert">{{ notice }}</p>{% endif %}
  <a class="btn" href="/login"><svg class="gh" viewBox="0 0 16 16" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>Continue with GitHub</a>
  <span class="hint">We read your public profile and primary email. Nothing else.</span>
</section>
{% endblock %}
```

- [ ] **Step 6: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web.py -v`
Expected: PASS. (`test_callback_happy_path_sets_session` only checks the redirect; `/dashboard` itself arrives in Task 5.)

- [ ] **Step 7: Commit**

```bash
.venv/Scripts/python.exe -m ruff check src tests
git add src/leftbrain/web tests/test_web.py
git commit -m "web: GitHub OAuth login, callback and logout"
```

---

### Task 5: Dashboard (list, create with one-time reveal, revoke)

**Files:**
- Modify: `src/leftbrain/web/views.py`
- Create: `src/leftbrain/web/templates/dashboard.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `store.list(owner)`, `store.create_for_owner(email, name)`, `store.owns(email, prefix)`, `store.set_disabled(prefix, True)`; `auth.current_user`, `auth.csrf_token`, `auth.csrf_ok`.
- Produces routes: `GET /dashboard`, `POST /dashboard/keys`, `POST /dashboard/keys/{prefix}/revoke`.
- Template context for `dashboard.html`: `user`, `keys: list[KeyInfo]`, `csrf: str`, `new_key: str | None`, `error: str | None`, `today_total: int`, `active: int`, `max_keys: int`, `page="dashboard"`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_web.py`:

```python
def test_dashboard_requires_login(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        r = c.get("/dashboard", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/login"


def csrf_from(html: str) -> str:
    return html.split('name="csrf" value="')[1].split('"')[0]


def test_dashboard_create_list_cap_revoke(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        page = c.get("/dashboard")
        assert page.status_code == 200 and "No keys yet" in page.text and "octo" in page.text
        csrf = csrf_from(page.text)
        r = c.post("/dashboard/keys", data={"name": "laptop", "csrf": csrf})
        assert r.status_code == 200 and "lblz_" in r.text and "won't be shown again" in r.text
        key = r.text.split("<code id=\"new-key\">")[1].split("</code>")[0]
        assert key.startswith("lblz_") and len(key) > 20
        # the key works on the API
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {key}"}).json()["result"]["owner"] == "octo@example.com"
        for i in range(2):
            assert c.post("/dashboard/keys", data={"name": f"k{i}", "csrf": csrf}).status_code == 200
        r = c.post("/dashboard/keys", data={"name": "one-too-many", "csrf": csrf})
        assert r.status_code == 200 and "3 active" in r.text and "new-key" not in r.text
        prefix = key[:13]
        r = c.post(f"/dashboard/keys/{prefix}/revoke", data={"csrf": csrf}, follow_redirects=False)
        assert r.status_code == 302
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {key}"}).status_code == 403
        assert "revoked" in c.get("/dashboard").text


def test_dashboard_csrf_and_ownership(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        assert c.post("/dashboard/keys", data={"name": "x"}).status_code == 403
        assert c.post("/dashboard/keys", data={"name": "x", "csrf": "bogus"}).status_code == 403
        csrf = csrf_from(c.get("/dashboard").text)
        assert c.post("/dashboard/keys/lblz_notmine1/revoke", data={"csrf": csrf}).status_code == 403
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web.py -k dashboard -v`
Expected: FAIL — `/dashboard` 404.

- [ ] **Step 3: Add dashboard handlers in `views.py` (inside `routes`)**

```python
    def require_user(request: Request) -> auth.User | None:
        return auth.current_user(request, cfg)

    def dashboard_ctx(request: Request, user: auth.User, **extra: Any) -> dict[str, Any]:
        from ..keys import MAX_ACTIVE_KEYS_PER_EMAIL

        keys = store.list(user.email) if store else []
        return {"page": "dashboard", "user": user, "keys": keys, "csrf": auth.csrf_token(cfg.secret or "", user), "today_total": sum(k.used_today for k in keys), "active": sum(1 for k in keys if not k.disabled), "max_keys": MAX_ACTIVE_KEYS_PER_EMAIL, "new_key": None, "error": None, **extra}

    async def dashboard(request: Request) -> Response:
        user = require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        if store is None:
            return error_page(request, 503, "Keys unavailable", "This server has no key store configured.")
        return render(request, "dashboard.html", 200, **dashboard_ctx(request, user))

    async def create_key(request: Request) -> Response:
        user = require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        form = await request.form()
        if not auth.csrf_ok(cfg.secret or "", user, str(form.get("csrf") or "")):
            return error_page(request, 403, "Form expired", "Please go back to the dashboard and try again.")
        raw, info = store.create_for_owner(user.email, str(form.get("name") or ""))
        if raw is None:
            return render(request, "dashboard.html", 200, **dashboard_ctx(request, user, error=info))
        return render(request, "dashboard.html", 200, **dashboard_ctx(request, user, new_key=raw))

    async def revoke_key(request: Request) -> Response:
        user = require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        form = await request.form()
        if not auth.csrf_ok(cfg.secret or "", user, str(form.get("csrf") or "")):
            return error_page(request, 403, "Form expired", "Please go back to the dashboard and try again.")
        prefix = request.path_params["prefix"]
        if not store.owns(user.email, prefix):
            return error_page(request, 403, "Not your key", "That key belongs to a different account.")
        store.set_disabled(prefix, True)
        return RedirectResponse("/dashboard", status_code=302)
```

and add to the returned list:

```python
        Route("/dashboard", dashboard),
        Route("/dashboard/keys", create_key, methods=["POST"]),
        Route("/dashboard/keys/{prefix}/revoke", revoke_key, methods=["POST"]),
```

- [ ] **Step 4: Create `dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}API keys · leftbrain{% endblock %}
{% block content %}
<div class="dash">
  <aside class="side">
    <div class="me">{% if user.avatar_url %}<img class="avatar" src="{{ user.avatar_url }}" alt="" width="34" height="34">{% else %}<span class="avatar">{{ user.login[:1]|upper }}</span>{% endif %}<div><div class="who">{{ user.login }}</div><div class="mail">{{ user.email }}</div></div></div>
    <a href="/dashboard" class="cur">API keys</a>
    <a href="/docs">Docs</a>
    <form method="post" action="/logout"><button class="linklike" type="submit">Sign out</button></form>
  </aside>
  <main>
    <div class="stats">
      <div class="stat"><div class="l">Calls today</div><div class="v num">{{ "{:,}".format(today_total) }}</div><div class="meter"><i style="width:{{ [100, (today_total / (5000 * (active or 1)) * 100)|round]|min }}%"></i></div></div>
      <div class="stat"><div class="l">Daily quota</div><div class="v num">5,000 <span class="sub">/ key</span></div></div>
      <div class="stat"><div class="l">Active keys</div><div class="v num">{{ active }} <span class="sub">/ {{ max_keys }}</span></div></div>
    </div>
    <h2 class="sec">Your keys</h2>
    <form class="newkey" method="post" action="/dashboard/keys">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <input name="name" placeholder="Name this key (e.g. laptop, prod-agent)" maxlength="40" autocomplete="off">
      <button class="btn primary" type="submit">Create key</button>
    </form>
    {% if error %}<p class="notice bad" role="alert">{{ error }}</p>{% endif %}
    {% if new_key %}
    <div class="reveal" role="status"><strong>Copy it now — it won't be shown again.</strong><code id="new-key">{{ new_key }}</code><button class="copy" type="button" data-copy="#new-key">copy</button><div class="note">Use it as <code>Authorization: Bearer &lt;key&gt;</code> on <code>/mcp</code> and <code>/external/mcp</code>. <a href="/docs/quickstart">Quickstart →</a></div></div>
    {% endif %}
    <table class="keys">
      <thead><tr><th>Key</th><th>Name</th><th>Created</th><th>Today</th><th>Status</th><th></th></tr></thead>
      <tbody>
      {% for k in keys %}
      <tr>
        <td class="mono">{{ k.prefix }}…</td>
        <td>{{ k.note or "—" }}</td>
        <td class="num">{{ k.created_at[:10] }}</td>
        <td class="num">{{ "{:,}".format(k.used_today) }} / {{ "{:,}".format(k.daily_quota) }}</td>
        <td><span class="pill {{ 'off' if k.disabled else 'ok' }}">{{ "revoked" if k.disabled else "active" }}</span></td>
        <td class="right">{% if not k.disabled %}<form method="post" action="/dashboard/keys/{{ k.prefix }}/revoke"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="linklike" type="submit">Revoke</button></form>{% endif %}</td>
      </tr>
      {% else %}
      <tr><td colspan="6" class="empty">No keys yet. Create one above.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </main>
</div>
{% endblock %}
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
.venv/Scripts/python.exe -m ruff check src tests
git add src/leftbrain/web tests/test_web.py
git commit -m "web: key dashboard with create (one-time reveal), revoke, CSRF and ownership checks"
```

---

### Task 6: Live demo endpoint with per-IP throttle

**Files:**
- Create: `src/leftbrain/web/demo.py`
- Modify: `src/leftbrain/web/views.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `demo.DEMO_TOOLS: dict[str, Callable[..., dict]]` = `{"numbers": numbers, "convert": convert, "datetime": datetime_tool, "text": text}`; `demo.Throttle(limit: int = 30, window: float = 60.0)` with `.allow(ip: str) -> tuple[bool, int]` (allowed, retry_after_seconds); `demo.run(tool: str, args: dict) -> dict` returning the contract dict.
- Route: `POST /demo/{tool}`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_web.py`:

```python
def test_demo_runs_real_tools_and_rejects_unknown(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = c.post("/demo/numbers", json={"mode": "compare", "values": ["9.11", "9.9"]})
        assert r.status_code == 200 and r.json()["ok"] and r.json()["result"]["max"]["input"] == "9.9"
        r = c.post("/demo/convert", json={"mode": "unit", "value": 3, "from_unit": "oz", "to": "ml"})
        assert r.json()["ok"] is False and "needs" in r.json()
        assert c.post("/demo/math", json={"mode": "eval", "expr": "1+1"}).status_code == 404
        assert c.post("/demo/numbers", content=b"not json", headers={"content-type": "application/json"}).status_code == 400


def test_demo_throttle(tmp_path):
    from leftbrain.web.demo import Throttle

    t = Throttle(limit=2, window=60)
    assert t.allow("1.1.1.1") == (True, 0) and t.allow("1.1.1.1")[0]
    ok, retry = t.allow("1.1.1.1")
    assert not ok and 0 < retry <= 60
    assert t.allow("2.2.2.2")[0]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web.py -k demo -v`
Expected: FAIL — 404 on `/demo/numbers`; ImportError for `Throttle`.

- [ ] **Step 3: Create `demo.py`**

```python
"""Key-less demo endpoint used by the landing page: a few tools, throttled per IP."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from ..core.convert import convert
from ..core.datetimex import datetime_tool
from ..core.numbers import numbers
from ..core.text import text

DEMO_TOOLS: dict[str, Callable[..., dict[str, Any]]] = {"numbers": numbers, "convert": convert, "datetime": datetime_tool, "text": text}


class Throttle:
    def __init__(self, limit: int = 30, window: float = 60.0) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, ip: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(ip, []) if now - t < self.window]
            if len(hits) >= self.limit:
                self._hits[ip] = hits
                return False, int(self.window - (now - hits[0])) + 1
            hits.append(now)
            self._hits[ip] = hits
            if len(self._hits) > 5000:  # forget idle IPs
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < self.window}
            return True, 0


def run(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    fn = DEMO_TOOLS[tool]
    mode = str(args.pop("mode", "") or "")
    return fn(mode, **args) if mode else fn(**args)
```

(The `@tool` decorator in `contract.py` already converts exceptions into `{ok: false, error, message}` dicts, so `run` needs no try/except.)

- [ ] **Step 4: Add the route in `views.py` (inside `routes`)**

```python
    from . import demo as demo_mod
    from ..serve import _client_ip  # noqa: E402  (module-level import would be circular)

    throttle = demo_mod.Throttle()

    async def demo(request: Request) -> Response:
        tool = request.path_params["tool"]
        if tool not in demo_mod.DEMO_TOOLS:
            return JSONResponse({"ok": False, "error": "unsupported", "message": f"demo supports {', '.join(demo_mod.DEMO_TOOLS)}"}, status_code=404)
        ok, retry = throttle.allow(_client_ip(request.scope))
        if not ok:
            return JSONResponse({"ok": False, "error": "rate_limited", "message": "demo limit reached; get a free key for 5,000 calls/day"}, status_code=429, headers={"retry-after": str(retry)})
        try:
            args = await request.json()
            if not isinstance(args, dict):
                raise ValueError
        except Exception:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": "invalid_input", "message": "send a JSON object with a mode and the tool's arguments"}, status_code=400)
        return JSONResponse(demo_mod.run(tool, args))
```

Add `Route("/demo/{tool}", demo, methods=["POST"])` to the returned list, and `from starlette.responses import JSONResponse` at the top of `views.py`. To avoid the circular import, move `_client_ip` and `_bearer` from `serve.py` into a new tiny module `src/leftbrain/web/request_utils.py`? — No: keep it simple and import lazily inside `routes()` as shown above (serve imports web at call time, not at module import, so this is safe).

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all PASS. If `numbers.compare` returns `max` in a different shape than `{"input": ...}`, check `scripts/http_check.py` (it asserts `payload["result"]["max"]["input"] == "9.9"`) — that is the authoritative shape.

- [ ] **Step 6: Commit**

```bash
.venv/Scripts/python.exe -m ruff check src tests
git add src/leftbrain/web tests/test_web.py
git commit -m "web: key-less demo endpoint for four tools, 30/min per IP"
```

---

### Task 7: Landing page, Direction A stylesheet, progressive-enhancement JS

**Files:**
- Create: `src/leftbrain/web/templates/landing.html`
- Replace: `src/leftbrain/web/static/site.css`, `src/leftbrain/web/static/site.js`
- Modify: `src/leftbrain/web/views.py` (`landing` handler)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `POST /demo/{tool}` (Task 6). `landing(request, store, cfg)` signature from Task 1 stays.
- Produces: template context `page="landing"`, `user`, `tools: list[tuple[name, description, modes]]`.

- [ ] **Step 1: Write failing test**

Append to `tests/test_web.py`:

```python
def test_landing_content(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        html = c.get("/", headers={"Accept": "text/html"}).text
        assert "left brain" in html and 'id="demo"' in html and "geo_offline" in html
        assert 'href="/login"' in html and 'href="/docs"' in html
        assert "9.11" in html  # proof strip
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web.py::test_landing_content -v`
Expected: FAIL — placeholder page has no `id="demo"`.

- [ ] **Step 3: Replace the `landing` handler in `views.py`**

```python
TOOLS = [
    ("math", "Exact arithmetic & symbolic algebra (SymPy)", "eval · solve · simplify · percent"),
    ("datetime", "Dates, durations, time zones, business days", "diff · add · convert_tz · weekday · business"),
    ("scale", "Scale numbers and recipes proportionally", "factor · to_total · per_unit"),
    ("convert", "Units and currencies with pint", "unit · currency_static · temperature"),
    ("holidays", "Public holidays by country/region", "list · is_holiday · next"),
    ("numbers", "Compare, round, format, allocate exactly", "compare · round · format · allocate · parse · to_words"),
    ("text", "Count, slice, case, diff — by codepoint", "count · slice · case · diff · regex"),
    ("collections", "Sort, dedupe, group, set ops", "sort · unique · group · intersect · rank"),
    ("validate", "Assert rules over JSON, emails, IBANs, schemas", "assert · schema · email · phone · iban"),
    ("random", "Seeded, reproducible randomness", "int · choice · shuffle · uuid"),
    ("geo_offline", "Distance, bearing, bounding boxes", "distance · bearing · bbox"),
    ("encode", "Hashes, base64, URL, hex", "base64 · hash · url · hex"),
]


async def landing(request: Request, store: Any, cfg: WebConfig) -> Response:
    return render(request, "landing.html", 200, page="landing", user=auth.current_user(request, cfg), tools=TOOLS)
```

(Before committing, open each core module's `MODES` tuple and correct the third column so it lists real modes — e.g. `grep -n "^MODES" src/leftbrain/core/*.py`.)

- [ ] **Step 4: Create `landing.html`**

```html
{% extends "base.html" %}
{% block content %}
<section class="hero">
  <div>
    <div class="eyebrow">MCP server · REST · Python library</div>
    <h1 class="hl">The <em>left brain</em> for your AI agent.</h1>
    <p class="lede">Exact, deterministic answers for everything language models get wrong: arithmetic, dates, units, ordering, validation. One free key, one endpoint, twelve tools that refuse to guess.</p>
    <div class="cta">
      <a class="btn primary" href="/login">Sign in with GitHub → get a key</a>
      <a class="btn ghost" href="/docs">Read the docs</a>
      <span class="hint">5,000 calls/day free · no card</span>
    </div>
  </div>
  <div class="demo" id="demo" data-demo>
    <div class="bar" role="tablist">
      <button role="tab" aria-pressed="true" data-tool="numbers">numbers</button>
      <button role="tab" aria-pressed="false" data-tool="convert">convert</button>
      <button role="tab" aria-pressed="false" data-tool="datetime">datetime</button>
      <button role="tab" aria-pressed="false" data-tool="text">text</button>
    </div>
    <div class="body" id="demo-body">
      <noscript><p class="hint">Enable JavaScript to try the tools here, or <a href="/docs/quickstart">use curl</a>.</p></noscript>
    </div>
  </div>
</section>

<div class="proof">
  <div><div class="big num">9.9 &gt; 9.11</div><div class="sm">Decimal comparison, not token comparison.</div></div>
  <div><div class="big num">12 tools</div><div class="sm">Each with a <code>mode</code> parameter. Same contract everywhere.</div></div>
  <div><div class="big num">0 guesses</div><div class="sm">Ambiguous input returns <code>needs.options</code> instead of a wrong answer.</div></div>
</div>

<section class="tools" id="tools">
  <div class="sec-h">
    <h2 class="sec">Twelve tools. One shape.</h2>
    <p>Every call returns <code>{ok, result, assumptions[], warnings[]}</code> — or <code>{ok:false, error, needs}</code> when the input could mean two things.</p>
  </div>
  <div class="tgrid">
    {% for name, desc, modes in tools %}<div class="tool"><div class="n">{{ name }}</div><div class="d">{{ desc }}</div><div class="m">{{ modes }}</div></div>{% endfor %}
  </div>
</section>

<section class="contract">
  <div>
    <h2 class="sec">It would rather ask than guess.</h2>
    <p>"3 oz" is either weight or volume. "03/04/2025" is March 4th or April 3rd. "IST" is India or Israel. leftbrain returns the options and lets the model pick — every assumption it does make is listed in the response.</p>
  </div>
<pre class="out"><span class="k">// convert · mode: unit · "3 oz" → ml</span>
{
  <span class="k">"ok"</span>: <span class="b">false</span>,
  <span class="k">"error"</span>: <span class="s">"ambiguous"</span>,
  <span class="k">"message"</span>: <span class="s">"'oz' can be mass or volume"</span>,
  <span class="k">"needs"</span>: { <span class="k">"field"</span>: <span class="s">"from_unit"</span>, <span class="k">"options"</span>: [<span class="s">"oz"</span>, <span class="s">"fl_oz"</span>, <span class="s">"imperial_fl_oz"</span>] }
}</pre>
</section>
{% endblock %}
```

(Replace the `needs.options` strings with whatever `convert("unit", value=3, from_unit="oz", to="ml")` actually returns — run it in a Python shell and paste the real output.)

- [ ] **Step 5: Write `site.css` (Direction A, complete)**

```css
:root{--bg:#f6f7f4;--bg2:#ffffff;--ink:#14201f;--ink2:#4b5a58;--line:#c9d2cf;--line2:#e3e8e5;--accent:#1747c8;--accent-ink:#ffffff;--accent-soft:#e2e9fb;--good:#1b7a4a;--warn:#a15c00;--bad:#b3261e;--display:"IBM Plex Mono","SFMono-Regular",Consolas,monospace;--body:"IBM Plex Sans",system-ui,"Segoe UI",sans-serif;--mono:"IBM Plex Mono",Consolas,monospace;--radius:4px;--grid:linear-gradient(to right,rgba(23,71,200,.09) 1px,transparent 1px),linear-gradient(to bottom,rgba(23,71,200,.09) 1px,transparent 1px)}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:16px;line-height:1.55;-webkit-font-smoothing:antialiased}
a{color:inherit}button{font:inherit;color:inherit;cursor:pointer}code,pre,kbd,.mono{font-family:var(--mono)}h1,h2,h3{margin:0;text-wrap:balance}.num{font-variant-numeric:tabular-nums}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.wrap{max-width:1160px;margin:0 auto;padding:0 24px}
.nav{display:flex;align-items:center;gap:28px;padding:18px 0;border-bottom:1px solid var(--line2)}
.brand{display:flex;align-items:center;gap:10px;font-family:var(--display);font-weight:600;font-size:1.15rem;letter-spacing:-.01em;text-decoration:none}
.nav .links{display:flex;gap:22px;margin-left:auto;font-size:.95rem}.nav .links a{text-decoration:none;color:var(--ink2)}.nav .links a[aria-current="page"]{color:var(--ink);font-weight:600}
.btn{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--ink);background:var(--ink);color:var(--bg);padding:10px 16px;border-radius:var(--radius);font-weight:600;font-size:.95rem;text-decoration:none}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}.btn.ghost{background:transparent;color:var(--ink)}
.linklike{background:none;border:0;padding:0;color:var(--accent);text-decoration:underline;font-size:.9rem}
.hint{font-family:var(--mono);font-size:.8rem;color:var(--ink2)}
.notice{border-left:3px solid var(--accent);background:var(--accent-soft);padding:10px 14px;border-radius:0 var(--radius) var(--radius) 0;font-size:.92rem;max-width:60ch}.notice.bad{border-color:var(--bad)}
/* hero */
.hero{position:relative;padding:72px 0 56px;display:grid;grid-template-columns:1.05fr .95fr;gap:48px;align-items:center}
.hero::before{content:"";position:absolute;inset:0 -24px;background-image:var(--grid);background-size:24px 24px;z-index:-1;mask-image:linear-gradient(to bottom,#000 60%,transparent)}
.eyebrow{font-family:var(--mono);font-size:.8rem;letter-spacing:.12em;text-transform:uppercase;color:var(--ink2);margin-bottom:18px;display:flex;gap:12px;align-items:center}.eyebrow::before{content:"";width:28px;height:1px;background:var(--accent)}
h1.hl{font-family:var(--display);font-size:clamp(2.4rem,6vw,4.6rem);font-weight:500;letter-spacing:-.03em;line-height:.98}h1.hl em{font-style:normal;color:var(--accent)}
.lede{font-size:1.15rem;color:var(--ink2);max-width:52ch;margin:22px 0 28px}
.cta{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
/* demo */
.demo{background:var(--bg2);border:1px solid var(--line);border-radius:var(--radius);box-shadow:0 1px 0 var(--line),0 12px 32px -20px rgba(20,32,31,.35);overflow:hidden}
.demo .bar{display:flex;gap:2px;border-bottom:1px solid var(--line2);padding:8px;background:#eef1ee}
.demo .bar button{background:none;border:0;padding:8px 12px;border-radius:var(--radius);font-family:var(--mono);font-size:.82rem;color:var(--ink2)}.demo .bar button[aria-pressed="true"]{background:var(--bg2);color:var(--ink);box-shadow:0 0 0 1px var(--line)}
.demo .body{padding:18px;display:grid;gap:14px}
.demo label{display:grid;gap:6px;font-size:.8rem;letter-spacing:.06em;text-transform:uppercase;color:var(--ink2);font-family:var(--mono)}
.demo input{font:inherit;font-family:var(--mono);font-size:1rem;padding:10px 12px;border:1px solid var(--line);border-radius:var(--radius);background:var(--bg);color:var(--ink);width:100%}
.demo .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}.demo .run{justify-self:start}
.out{background:#f0f2ef;border:1px solid var(--line2);border-radius:var(--radius);padding:14px;font-family:var(--mono);font-size:.82rem;line-height:1.5;white-space:pre-wrap;overflow-x:auto;margin:0;min-height:120px}
.out .k{color:var(--ink2)}.out .s{color:var(--accent)}.out .n{color:var(--good)}.out .w{color:var(--warn)}.out .b{color:var(--bad)}
.latency{font-family:var(--mono);font-size:.75rem;color:var(--ink2);display:flex;justify-content:space-between}
/* proof + tools + contract */
.proof{display:grid;grid-template-columns:repeat(3,1fr);border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.proof>div{padding:22px 20px 22px 0;border-right:1px solid var(--line2)}.proof>div:last-child{border-right:0}.proof>div+div{padding-left:20px}
.proof .big{font-family:var(--display);font-size:2rem;font-weight:600;line-height:1}.proof .sm{font-size:.85rem;color:var(--ink2);margin-top:6px}
section.tools{padding:64px 0}.sec-h{display:flex;align-items:baseline;justify-content:space-between;gap:20px;margin-bottom:26px}
h2.sec{font-family:var(--display);font-size:clamp(1.6rem,3vw,2.3rem);font-weight:600;letter-spacing:-.02em}.sec-h p{color:var(--ink2);margin:0;max-width:44ch;text-align:right}
.tgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line2);border:1px solid var(--line2)}
.tool{background:var(--bg2);padding:18px;display:grid;gap:8px;align-content:start;transition:background .15s}.tool:hover{background:var(--accent-soft)}
.tool .n{font-family:var(--mono);font-weight:600}.tool .d{font-size:.9rem;color:var(--ink2)}.tool .m{font-family:var(--mono);font-size:.72rem;color:var(--ink2)}
section.contract{padding:0 0 72px;display:grid;grid-template-columns:1fr 1fr;gap:40px;align-items:start}.contract p{color:var(--ink2);max-width:46ch}
footer{border-top:1px solid var(--line2);padding:28px 0;font-size:.85rem;color:var(--ink2);display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}footer a{color:var(--ink2)}
/* login + error */
.login{padding:96px 0;text-align:center;display:grid;justify-items:center;gap:18px}.login h2{font-family:var(--display);font-size:2.4rem;font-weight:600}.login p{color:var(--ink2);max-width:44ch;margin:0}
/* dashboard */
.dash{padding:48px 0 80px;display:grid;grid-template-columns:260px 1fr;gap:40px}
.side{display:grid;gap:8px;align-content:start;font-size:.95rem}.side .me{display:flex;gap:10px;align-items:center;padding:12px;border:1px solid var(--line2);border-radius:var(--radius);margin-bottom:12px}
.avatar{width:34px;height:34px;border-radius:50%;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;font-weight:700;font-family:var(--display);object-fit:cover}
.side .who{font-weight:600}.side .mail{font-size:.8rem;color:var(--ink2)}
.side a{display:block;padding:8px 10px;border-radius:var(--radius);text-decoration:none;color:var(--ink2)}.side a.cur{background:var(--accent-soft);color:var(--ink);font-weight:600}.side form{padding:8px 10px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line2);border:1px solid var(--line2);margin-bottom:28px}
.stat{background:var(--bg2);padding:16px 18px}.stat .l{font-size:.75rem;letter-spacing:.08em;text-transform:uppercase;color:var(--ink2);font-family:var(--mono)}.stat .v{font-family:var(--display);font-size:1.9rem;font-weight:600;margin-top:4px;line-height:1.1}.stat .sub{font-size:.9rem;color:var(--ink2)}
.meter{height:6px;background:var(--line2);border-radius:3px;margin-top:10px;overflow:hidden}.meter i{display:block;height:100%;background:var(--accent)}
table.keys{width:100%;border-collapse:collapse;font-size:.92rem}table.keys th{text-align:left;font-family:var(--mono);font-weight:500;font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--ink2);padding:10px 8px;border-bottom:1px solid var(--line)}table.keys td{padding:12px 8px;border-bottom:1px solid var(--line2);vertical-align:middle}td.right{text-align:right}td.empty{color:var(--ink2);padding:24px 8px}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:.72rem;font-family:var(--mono);border:1px solid currentColor}.pill.ok{color:var(--good)}.pill.off{color:var(--ink2)}
.newkey{display:flex;gap:10px;margin:18px 0}.newkey input{flex:1;font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:var(--radius);background:var(--bg);color:var(--ink)}
.reveal{margin:0 0 18px;padding:16px;border:1px dashed var(--accent);border-radius:var(--radius);background:var(--accent-soft);position:relative}.reveal code{display:block;font-size:1rem;margin:8px 0;word-break:break-all}.reveal .note{font-size:.85rem;color:var(--ink2)}.reveal .copy{position:absolute;top:12px;right:12px}
/* docs */
.docs{padding:40px 0 80px;display:grid;grid-template-columns:240px minmax(0,1fr);gap:48px}
.toc{position:sticky;top:16px;align-self:start;display:grid;gap:2px;font-size:.92rem}.toc .grp{font-family:var(--mono);font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;color:var(--ink2);margin:14px 0 6px}
.toc a{padding:6px 10px;border-radius:var(--radius);text-decoration:none;color:var(--ink2);border-left:2px solid transparent}.toc a.cur{color:var(--ink);border-left-color:var(--accent);font-weight:600}
.doc h1{font-family:var(--display);font-size:2.4rem;font-weight:600;letter-spacing:-.02em;margin-bottom:8px}.doc h2{font-family:var(--display);font-size:1.35rem;font-weight:600;margin:40px 0 12px;padding-top:12px;border-top:1px solid var(--line2)}.doc h3{font-size:1.05rem;margin:24px 0 8px}
.doc p,.doc li{max-width:66ch;color:var(--ink2)}.doc p strong{color:var(--ink)}.doc a{color:var(--accent)}
.doc pre{background:#f0f2ef;border:1px solid var(--line2);border-radius:var(--radius);padding:16px 18px;overflow-x:auto;font-size:.84rem;line-height:1.55;margin:0 0 14px}.doc :not(pre)>code{background:#eef1ee;padding:1px 5px;border-radius:3px;font-size:.88em}
.doc table{width:100%;border-collapse:collapse;font-size:.9rem;margin:8px 0 16px}.doc th,.doc td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line2);vertical-align:top}.doc th{font-family:var(--mono);font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--ink2)}
.ostabs{display:inline-flex;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;margin:10px 0}.ostabs button{background:none;border:0;padding:8px 14px;font-size:.85rem;font-weight:500;color:var(--ink2)}.ostabs button+button{border-left:1px solid var(--line)}.ostabs button[aria-pressed="true"]{background:var(--ink);color:var(--bg)}
.os-block{position:relative}.os-block h4{font-family:var(--mono);font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--ink2);margin:10px 0 6px}.js .os-block{display:none}.js .os-block.show{display:block}.js .os-block h4{display:none}
.copy{font-family:var(--mono);font-size:.72rem;background:var(--bg2);border:1px solid var(--line);color:var(--ink2);padding:4px 8px;border-radius:var(--radius)}.copy:hover{color:var(--ink)}
.doc .codewrap{position:relative}.doc .codewrap .copy{position:absolute;top:8px;right:8px}
.callout{border-left:3px solid var(--accent);background:var(--accent-soft);padding:12px 16px;border-radius:0 var(--radius) var(--radius) 0;margin:16px 0;font-size:.92rem}
@media (max-width:860px){.hero,.contract,.dash,.docs{grid-template-columns:1fr}.tgrid{grid-template-columns:1fr 1fr}.proof{grid-template-columns:1fr}.proof>div{border-right:0;border-bottom:1px solid var(--line2);padding-left:0}.toc{position:static}.sec-h{flex-direction:column;align-items:flex-start}.sec-h p{text-align:left}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
```

- [ ] **Step 6: Write `site.js`**

```js
(function () {
  document.documentElement.classList.add('js');
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  function toast(msg) {
    var t = $('#toast'); if (!t) { t = document.createElement('div'); t.id = 'toast'; t.className = 'toast'; document.body.appendChild(t); }
    t.textContent = msg; t.classList.add('show'); clearTimeout(t._h); t._h = setTimeout(function () { t.classList.remove('show'); }, 1600);
  }
  function copyText(txt) { try { navigator.clipboard.writeText(txt).then(function () { toast('Copied'); }); } catch (e) { toast('Select and copy manually'); } }

  // copy buttons: data-copy="#selector" or wrap <pre> in .codewrap
  $$('[data-copy]').forEach(function (b) { b.addEventListener('click', function () { var el = $(b.getAttribute('data-copy')); if (el) copyText(el.textContent); }); });
  $$('.doc pre').forEach(function (pre) {
    var w = document.createElement('div'); w.className = 'codewrap'; pre.parentNode.insertBefore(w, pre); w.appendChild(pre);
    var b = document.createElement('button'); b.type = 'button'; b.className = 'copy'; b.textContent = 'copy';
    b.addEventListener('click', function () { copyText(pre.textContent); }); w.appendChild(b);
  });

  // OS tabs in docs
  var os = (navigator.platform || '').match(/win/i) ? 'windows' : (navigator.platform || '').match(/mac/i) ? 'macos' : 'linux';
  try { os = localStorage.getItem('lb-os') || os; } catch (e) {}
  $$('.ostabs').forEach(function (tabs) {
    var group = tabs.parentNode;
    function pick(which) {
      $$('button', tabs).forEach(function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-os') === which); });
      $$('.os-block', group).forEach(function (blk) { blk.classList.toggle('show', blk.getAttribute('data-os') === which); });
    }
    $$('button', tabs).forEach(function (b) { b.addEventListener('click', function () { var w = b.getAttribute('data-os'); try { localStorage.setItem('lb-os', w); } catch (e) {} $$('.ostabs').forEach(function (t) { t._pick && t._pick(w); }); }); });
    tabs._pick = pick; pick(os);
  });

  // live demo
  var demo = $('[data-demo]'); if (!demo) return;
  var FIELDS = {
    numbers: [['values', 'Values (comma separated)', '9.11, 9.9, 10']],
    convert: [['value', 'Value', '3'], ['from_unit', 'From unit', 'oz'], ['to', 'To unit', 'ml']],
    datetime: [['from', 'From (YYYY-MM-DD)', '2026-08-26'], ['to', 'To (YYYY-MM-DD)', '2026-12-25']],
    text: [['text', 'Text', 'strawberry 🍓 naïve café']]
  };
  var MODE = { numbers: 'compare', convert: 'unit', datetime: 'diff', text: 'count' };
  function args(tool, v) {
    if (tool === 'numbers') return { mode: 'compare', values: v.values.split(',').map(function (s) { return s.trim(); }).filter(Boolean) };
    if (tool === 'convert') return { mode: 'unit', value: v.value, from_unit: v.from_unit, to: v.to };
    if (tool === 'datetime') return { mode: 'diff', from: v.from, to: v.to };
    return { mode: 'count', text: v.text };
  }
  function pretty(o) {
    return JSON.stringify(o, null, 2).replace(/[&<>]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]; })
      .replace(/"([^"]+)":/g, '<span class="k">"$1"</span>:').replace(/: "([^"]*)"/g, ': <span class="s">"$1"</span>')
      .replace(/: (-?\d+(\.\d+)?)/g, ': <span class="n">$1</span>').replace(/: true/g, ': <span class="n">true</span>').replace(/: false/g, ': <span class="b">false</span>');
  }
  var cur = 'numbers', body = $('#demo-body');
  function render() {
    var f = FIELDS[cur];
    body.innerHTML = '<div class="' + (f.length > 1 ? 'row' : '') + '">' + f.map(function (x) { return '<label>' + x[1] + '<input data-k="' + x[0] + '" value="' + x[2].replace(/"/g, '&quot;') + '"></label>'; }).join('') + '</div>' +
      '<button class="btn run" type="button" id="run">Run ' + cur + '</button><pre class="out" id="out"></pre><div class="latency"><span>POST /mcp · tools/call · ' + cur + ' · mode ' + MODE[cur] + '</span><span id="lat"></span></div>';
    $('#run').addEventListener('click', run);
    $$('input', body).forEach(function (i) { i.addEventListener('keydown', function (e) { if (e.key === 'Enter') run(); }); });
    run();
  }
  function run() {
    var v = {}; $$('input', body).forEach(function (i) { v[i.getAttribute('data-k')] = i.value; });
    var t0 = performance.now(); $('#out').textContent = '…';
    fetch('/demo/' + cur, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(args(cur, v)) })
      .then(function (r) { return r.json(); })
      .then(function (j) { $('#out').innerHTML = pretty(j); $('#lat').textContent = (performance.now() - t0).toFixed(0) + ' ms'; })
      .catch(function () { $('#out').textContent = 'network error'; });
  }
  $$('.bar button', demo).forEach(function (b) { b.addEventListener('click', function () { $$('.bar button', demo).forEach(function (x) { x.setAttribute('aria-pressed', x === b); }); cur = b.getAttribute('data-tool'); render(); }); });
  render();
})();
```

Add to the end of `site.css`:

```css
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%) translateY(20px);opacity:0;background:var(--ink);color:var(--bg);padding:10px 16px;border-radius:6px;font-size:.9rem;transition:.25s;pointer-events:none}.toast.show{opacity:1;transform:translateX(-50%)}
```

The `datetime` demo assumes `datetime_tool("diff", from=..., to=...)` and `text("count", text=...)` — check the real parameter names with `grep -n "def _diff\|params\[\"" src/leftbrain/core/datetimex.py src/leftbrain/core/text.py` and adjust the `args()` map so the demo actually returns `ok: true`. Verify manually by running the server (`.venv/Scripts/python.exe -m leftbrain.serve --port 8791 --no-external --keys-db <tmp>`) and clicking each tab.

- [ ] **Step 7: Run tests and a visual check**

Run: `.venv/Scripts/python.exe -m pytest -q` → all PASS.
Then start the server as above, open `http://127.0.0.1:8791/` in a browser: hero grid visible, demo returns results for all four tabs, tool grid has 12 cards, nav links work.

- [ ] **Step 8: Commit**

```bash
.venv/Scripts/python.exe -m ruff check src tests
git add src/leftbrain/web tests/test_web.py
git commit -m "web: landing page with live demo, Direction A stylesheet and site.js"
```

---

### Task 8: Docs renderer, quickstart and clients pages

**Files:**
- Create: `src/leftbrain/web/docs.py`, `src/leftbrain/web/docs/quickstart.md`, `src/leftbrain/web/docs/clients.md`, `src/leftbrain/web/templates/docs.html`
- Modify: `src/leftbrain/web/views.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `docs.render_markdown(text: str) -> str` (HTML), `docs.load_page(slug: str) -> tuple[str, str] | None` returning `(title, html)`; `docs.PAGES: list[tuple[slug, title]] = [("quickstart", "Quickstart"), ("clients", "MCP clients")]`.
- Routes: `GET /docs` (renders quickstart), `GET /docs/{slug}`.
- `:::os` block contract: lines `### windows` / `### macos` / `### linux` inside the block each introduce one fenced code block (any language).

- [ ] **Step 1: Write failing tests**

Append to `tests/test_web.py`:

```python
def test_docs_pages_and_os_tabs(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = c.get("/docs")
        assert r.status_code == 200 and "Quickstart" in r.text and 'class="ostabs"' in r.text
        assert 'data-os="windows"' in r.text and "curl.exe" in r.text and "Invoke-RestMethod" in r.text
        assert "leftbrain.idlesync.in" in r.text
        assert c.get("/docs/clients").status_code == 200 and "claude mcp add" in c.get("/docs/clients").text
        assert c.get("/docs/nope").status_code == 404


def test_render_markdown_os_block():
    from leftbrain.web.docs import render_markdown

    md = "# T\n\n:::os\n### windows\n```powershell\ncurl.exe -s X\n```\n### macos\n```bash\ncurl -s X\n```\n### linux\n```bash\ncurl -s X\n```\n:::\n\nafter\n"
    html = render_markdown(md)
    assert html.count('class="os-block"') == 3 and 'data-os="macos"' in html and "<p>after</p>" in html
    assert '<button type="button" data-os="windows"' in html
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web.py -k docs -v`
Expected: FAIL — 404 / ImportError.

- [ ] **Step 3: Create `docs.py`**

```python
"""Markdown docs with an `:::os` block that renders Windows / macOS / Linux tabs."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from markdown_it import MarkdownIt

DOCS_DIR = Path(__file__).parent / "docs"
PAGES: list[tuple[str, str]] = [("quickstart", "Quickstart"), ("clients", "MCP clients")]
OS_LABELS = [("windows", "Windows · PowerShell"), ("macos", "macOS"), ("linux", "Linux")]

_md = MarkdownIt("commonmark", {"html": True, "linkify": False}).enable("table")
_OS_BLOCK = re.compile(r"^:::os\s*\n(.*?)^:::\s*$", re.S | re.M)
_OS_SECTION = re.compile(r"^### (windows|macos|linux)\s*$", re.M)


def _render_os_block(inner: str) -> str:
    parts = _OS_SECTION.split(inner)  # ['', 'windows', body, 'macos', body, ...]
    sections = {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    tabs = "".join(f'<button type="button" data-os="{k}" aria-pressed="{"true" if i == 0 else "false"}">{label}</button>' for i, (k, label) in enumerate(OS_LABELS))
    blocks = "".join(f'<div class="os-block{" show" if i == 0 else ""}" data-os="{k}"><h4>{label}</h4>{_md.render(sections.get(k, ""))}</div>' for i, (k, label) in enumerate(OS_LABELS))
    return f'<div class="os"><div class="ostabs" role="tablist">{tabs}</div>{blocks}</div>\n'


def render_markdown(text: str) -> str:
    out: list[str] = []
    pos = 0
    for m in _OS_BLOCK.finditer(text):
        out.append(_md.render(text[pos:m.start()]))
        out.append(_render_os_block(m.group(1)))
        pos = m.end()
    out.append(_md.render(text[pos:]))
    return "".join(out)


@lru_cache(maxsize=32)
def load_page(slug: str) -> tuple[str, str] | None:
    title = dict(PAGES).get(slug)
    path = DOCS_DIR / f"{slug}.md"
    if not title or not path.is_file():
        return None
    return title, render_markdown(path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Add docs routes in `views.py` (inside `routes`)**

```python
    from . import docs as docs_mod

    async def docs_page(request: Request) -> Response:
        slug = request.path_params.get("slug", "quickstart")
        page = docs_mod.load_page(slug)
        if page is None:
            return error_page(request, 404, "Page not found", "That docs page doesn't exist. Try the quickstart.")
        title, html = page
        return render(request, "docs.html", 200, page="docs", user=auth.current_user(request, cfg), title=title, body=html, slug=slug, pages=docs_mod.PAGES)
```

Add `Route("/docs", docs_page)` and `Route("/docs/{slug}", docs_page)` to the returned list.

- [ ] **Step 5: Create `docs.html`**

```html
{% extends "base.html" %}
{% block title %}{{ title }} · leftbrain docs{% endblock %}
{% block content %}
<div class="docs">
  <nav class="toc" aria-label="Docs">
    <div class="grp">Start</div>
    {% for s, t in pages %}<a href="/docs/{{ s }}" class="{{ 'cur' if s == slug else '' }}">{{ t }}</a>{% endfor %}
    <div class="grp">Reference</div>
    <a href="https://github.com/Lazy-Coder-03/leftbrain#tools">Tool list (README)</a>
  </nav>
  <article class="doc">{{ body|safe }}</article>
</div>
{% endblock %}
```

- [ ] **Step 6: Write `quickstart.md`**

````markdown
# Quickstart

Three steps: get a key, store it in your shell, call a tool. All examples target the hosted server at **https://leftbrain.idlesync.in**; a self-hosted server uses the same routes on your own host.

## 1 · Get a key

[Sign in with GitHub](/login) and create a key on the Keys page. The free tier gives every key 5,000 calls/day and 60 requests/minute. You'll see the key once — copy it immediately.

## 2 · Store it

:::os
### windows
```powershell
$env:LB_KEY = "lblz_…"
# persist across sessions:
[Environment]::SetEnvironmentVariable("LB_KEY", "lblz_…", "User")
```
### macos
```bash
export LB_KEY="lblz_…"
# persist: add the line to ~/.zshrc
```
### linux
```bash
export LB_KEY="lblz_…"
# persist: add the line to ~/.bashrc or ~/.profile
```
:::

## 3 · Check the key

:::os
### windows
```powershell
# PowerShell aliases 'curl' to Invoke-WebRequest — use curl.exe
curl.exe -s https://leftbrain.idlesync.in/keys/me -H "Authorization: Bearer $env:LB_KEY"
```
### macos
```bash
curl -s https://leftbrain.idlesync.in/keys/me -H "Authorization: Bearer $LB_KEY"
```
### linux
```bash
curl -s https://leftbrain.idlesync.in/keys/me -H "Authorization: Bearer $LB_KEY"
```
:::

```json
{"ok":true,"result":{"prefix":"lblz_qm9DsdMO","daily_quota":5000,"rpm":60,"used_today":12,"remaining_today":4988}}
```

## 4 · Call a tool over MCP

The endpoint speaks **Streamable HTTP (JSON-RPC 2.0)** and is stateless, so you can send `tools/call` directly without opening a session.

:::os
### windows
```powershell
curl.exe -s https://leftbrain.idlesync.in/mcp `
  -H "Authorization: Bearer $env:LB_KEY" -H "Content-Type: application/json" `
  -H "Accept: application/json, text/event-stream" `
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"numbers","arguments":{"mode":"compare","values":["9.11","9.9"]}}}'

# or natively:
Invoke-RestMethod -Method Post -Uri https://leftbrain.idlesync.in/mcp `
  -Headers @{ Authorization = "Bearer $env:LB_KEY"; Accept = "application/json, text/event-stream" } `
  -ContentType "application/json" `
  -Body '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"numbers","arguments":{"mode":"compare","values":["9.11","9.9"]}}}'
```
### macos
```bash
curl -s https://leftbrain.idlesync.in/mcp \
  -H "Authorization: Bearer $LB_KEY" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"numbers","arguments":{"mode":"compare","values":["9.11","9.9"]}}}'
```
### linux
```bash
curl -s https://leftbrain.idlesync.in/mcp \
  -H "Authorization: Bearer $LB_KEY" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"numbers","arguments":{"mode":"compare","values":["9.11","9.9"]}}}'
```
:::

The response is an SSE stream by default; the JSON-RPC result is in the `data:` line and its `structuredContent` is the leftbrain contract:

```json
{"ok":true,"result":{"ascending":[{"input":"9.11","value":"9.11"},{"input":"9.9","value":"9.9"}],"max":{"input":"9.9","value":"9.9"}},"assumptions":[],"warnings":[]}
```

Add `-H "Accept: application/json"` only (no `text/event-stream`) if your server runs with `--json` to get a plain JSON body.

<div class="callout">Every response carries <code>x-ratelimit-remaining-today</code> and <code>x-ratelimit-limit-minute</code> headers. A <code>429</code> includes <code>retry-after</code>.</div>

## Next

[Connect an MCP client](/docs/clients) — Claude Code, Claude Desktop, Cursor, VS Code or the Python client.
````

(Confirm the SSE-vs-JSON sentence against the live server before committing: `curl -s https://leftbrain.idlesync.in/mcp ... -d '<tools/call>'` and look at whether the body starts with `event:`/`data:` or `{`.)

- [ ] **Step 7: Write `clients.md`**

````markdown
# MCP clients

leftbrain is a standard MCP server over Streamable HTTP. Point any client at `https://leftbrain.idlesync.in/mcp` with a bearer header. `/external/mcp` adds the network tools (weather, FX rates, geocoding, URL checks).

## Claude Code

:::os
### windows
```powershell
claude mcp add --transport http leftbrain https://leftbrain.idlesync.in/mcp --header "Authorization: Bearer $env:LB_KEY"
```
### macos
```bash
claude mcp add --transport http leftbrain https://leftbrain.idlesync.in/mcp --header "Authorization: Bearer $LB_KEY"
```
### linux
```bash
claude mcp add --transport http leftbrain https://leftbrain.idlesync.in/mcp --header "Authorization: Bearer $LB_KEY"
```
:::

## Claude Desktop, Cursor, VS Code

Add this to the client's MCP config — `claude_desktop_config.json`, `.cursor/mcp.json`, or `.vscode/mcp.json` (VS Code uses `"servers"` instead of `"mcpServers"`):

```json
{
  "mcpServers": {
    "leftbrain": {
      "type": "http",
      "url": "https://leftbrain.idlesync.in/mcp",
      "headers": { "Authorization": "Bearer lblz_…" }
    }
  }
}
```

## Python (official `mcp` client)

```python
import asyncio
import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    http = httpx.AsyncClient(headers={"Authorization": "Bearer lblz_…"})
    async with streamable_http_client("https://leftbrain.idlesync.in/mcp", http_client=http) as streams:
        async with ClientSession(streams[0], streams[1]) as s:
            await s.initialize()
            r = await s.call_tool("numbers", {"mode": "compare", "values": ["9.11", "9.9"]})
            print(r.structured_content)

asyncio.run(main())
```

## No client at all

Install the library and call the same functions locally — no key, no network:

```bash
pip install leftbrain
```

```python
from leftbrain.core.numbers import numbers
numbers("compare", values=["9.11", "9.9"])
```
````

- [ ] **Step 8: Run tests + visual check**

Run: `.venv/Scripts/python.exe -m pytest -q` → all PASS. Start the server and open `/docs`: tabs switch all blocks on the page together, copy buttons appear on every code block, sidebar highlights the current page.

- [ ] **Step 9: Commit**

```bash
.venv/Scripts/python.exe -m ruff check src tests
git add src/leftbrain/web tests/test_web.py
git commit -m "web: markdown docs with OS tabs; quickstart and MCP clients pages"
```

---

### Task 9: README + deploy guide, full verification, deploy

**Files:**
- Modify: `README.md` (section "Per-user API keys (public free tier)", ~line 149), `docs/deploy-northflank.md` (Step 3 env table, Step 4)
- Modify: `scripts/http_check.py` (pass `LEFTBRAIN_OPEN_SIGNUP=1` in `env`)

**Interfaces:** none new.

- [ ] **Step 1: Update `scripts/http_check.py`**

In `main()`, add `"LEFTBRAIN_OPEN_SIGNUP": "1"` to the `env` dict so the smoke script's anonymous signup still works. Run: `.venv/Scripts/python.exe scripts/http_check.py` → prints `HTTP check OK`.

- [ ] **Step 2: README**

Replace the "Per-user API keys (public free tier)" section body with:

```markdown
### Per-user API keys (public free tier)

Point `LEFTBRAIN_KEYS_URL` at SQLite or Postgres and `leftbrain-serve` grows a web site:

- `/` — landing page (browsers) or the JSON service description (`Accept: application/json`)
- `/login` — GitHub OAuth; keys belong to the account's verified primary email
- `/dashboard` — create up to 3 keys (shown once), see today's usage, revoke
- `/docs` — quickstart with Windows PowerShell / macOS / Linux tabs, MCP client setup
- `POST /demo/{numbers|convert|datetime|text}` — key-less demo, 30 req/min per IP

Environment: `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `LEFTBRAIN_SECRET` (cookie signing, 32+ random chars),
`LEFTBRAIN_BASE_URL` (e.g. `https://leftbrain.idlesync.in`, used for the OAuth callback).
Anonymous `POST /keys/signup {"email": …}` is off unless `LEFTBRAIN_OPEN_SIGNUP=1`.

Admin CLI (any DSN):

```bash
leftbrain-keys list | disable lblz_xxxxxxxx | enable … | revoke … | set lblz_xxxxxxxx --daily 20000 | usage --days 7 | stats
```
```

- [ ] **Step 3: Deploy guide**

In `docs/deploy-northflank.md` Step 3 runtime variables add: `GITHUB_CLIENT_ID=…` · `GITHUB_CLIENT_SECRET=…` · `LEFTBRAIN_SECRET=<python -c "import secrets;print(secrets.token_urlsafe(48))">` · `LEFTBRAIN_BASE_URL=https://leftbrain.idlesync.in`. Replace Step 4's curl signup with: "Open `https://<public-url>/`, click *Sign in with GitHub*, create a key on the dashboard, then:" followed by the existing `/keys/me` and `claude mcp add` lines. Add a short "GitHub OAuth App" note before Step 3: homepage `https://leftbrain.idlesync.in`, callback `https://leftbrain.idlesync.in/auth/github/callback`.

- [ ] **Step 4: Full verification**

Run: `.venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe scripts/http_check.py`
Expected: ruff clean, all tests pass, `HTTP check OK`.

Build the Docker image locally if Docker is available: `docker build -t leftbrain .` then `docker run --rm -p 8080:8080 -e LEFTBRAIN_KEYS_DB=/tmp/k.sqlite3 -e LEFTBRAIN_SECRET=x leftbrain` and confirm `curl -H "Accept: text/html" localhost:8080/` returns the landing page (proves templates/static are inside the image).

- [ ] **Step 5: Commit and push**

```bash
git add README.md docs/deploy-northflank.md scripts/http_check.py
git commit -m "docs: web site, GitHub login and env vars in README and deploy guide"
git push origin main
```

- [ ] **Step 6: Configure Northflank and verify live**

Service `leftbrain` → Environment → add `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `LEFTBRAIN_SECRET`, `LEFTBRAIN_BASE_URL=https://leftbrain.idlesync.in` → restart. Then:

```bash
curl -s https://leftbrain.idlesync.in/ | head -c 200            # JSON
curl -s -H "Accept: text/html" https://leftbrain.idlesync.in/ | grep -c "left brain"   # 1+
curl -s https://leftbrain.idlesync.in/docs | grep -c ostabs      # 1+
curl -s -X POST https://leftbrain.idlesync.in/keys/signup -d '{"email":"x@y.z"}' -H "content-type: application/json"  # 404
```

In a browser: `/login` → GitHub → dashboard → create key → `curl /keys/me` with it → revoke → `403`.

---

## Self-review

**Spec coverage:** Architecture/deps (T1), middleware flip + content negotiation (T1), login flow incl. state, verified email, cookie flags, base URL (T3–T4), CSRF (T3, T5), keys/data methods + signup gating (T2), routes table: landing (T7), demo (T6), docs (T8), static (T1), login/callback/logout (T4), dashboard trio (T5); OS tab markdown block (T8); visual tokens (T7 CSS); error handling: OAuth errors → login page (T4), session expiry → redirect (T5), cap/ownership (T5), demo 400/404/429 (T6); testing list mirrored in T1–T8; rollout (T9). Gap check: "Update README and deploy guide" — T9. Sub-project 2 items intentionally excluded.

**Placeholder scan:** none of the forbidden phrases; three steps ask the implementer to verify real tool argument names/outputs against the code (`numbers.compare` shape, `convert` needs options, `datetime`/`text` params, SSE vs JSON) — these are verification steps with exact commands, not placeholders.

**Type consistency:** `WebConfig` fields identical in T1 tests (`make_app`) and T1 config; `auth.User(login, email, avatar_url)` used the same way in T3–T5; `create_for_owner` returns `(str|None, KeyInfo|str)` and T5 treats the second element as the error string only when the first is `None`; `store.list(owner)` (existing) is what the dashboard uses; `demo.run(tool, args)` and `Throttle.allow(ip) -> (bool, int)` match T6 tests; template context names (`page`, `user`, `csrf`, `new_key`, `error`, `keys`, `pages`, `slug`, `body`, `tools`) match between handlers and templates.
