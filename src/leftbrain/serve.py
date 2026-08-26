"""leftbrain-serve - one HTTP process exposing every leftbrain tool set.

Endpoints (Streamable HTTP MCP):
    /mcp            core tools (math, datetime, convert, ...)
    /external/mcp   weather, fx_rate, geo, url_check
    /files/mcp      pdf/image/file tools  (opt-in: --files or LEFTBRAIN_SERVE_FILES=1)
    /healthz        liveness JSON
    /               service description JSON
    /keys/signup    POST {"email": ...} -> issues a free API key   (when a key store is enabled)
    /keys/me        GET  -> quota and usage for the calling key

Authentication (either or both may be enabled):
    LEFTBRAIN_API_KEY   a single static bearer token (private deployments)
    LEFTBRAIN_KEYS_DB   path to the SQLite key store: per-user keys, daily quota,
                        per-minute rate limit, self-serve signup

TLS is expected to be terminated by the host (Railway, Render, Fly, Cloudflare,
Caddy, nginx).
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from . import __version__

PROTECTED_PREFIXES = ("/mcp", "/external/mcp", "/files/mcp", "/keys/me")


def _protected(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in PROTECTED_PREFIXES)


def _trusted_proxy_hops() -> int:
    """How many proxies sit in front of this process (``LEFTBRAIN_TRUSTED_PROXY_HOPS``).

    1 = one reverse proxy (Northflank, Render, Fly, a single nginx). 2 = Cloudflare
    in front of that proxy. 0 = the process is reached directly, so no forwarding
    header may be believed at all.
    """
    try:
        return max(0, int(os.environ.get("LEFTBRAIN_TRUSTED_PROXY_HOPS", "1")))
    except ValueError:
        return 1


TRUSTED_PROXY_HOPS = _trusted_proxy_hops()


def _client_ip(scope: Any, hops: int | None = None) -> str:
    """The nearest IP this process is willing to believe.

    ``X-Forwarded-For`` is appended to by each hop, so the *rightmost* entries were
    written by our own infrastructure and the leftmost ones by the caller - which
    means the leftmost entry is attacker-controlled and must never be used as a
    rate-limit key. We count ``hops`` in from the right and fail closed to the last
    entry when the chain is shorter than expected. ``X-Real-IP`` and
    ``CF-Connecting-IP`` are ignored entirely: they are single-valued, so a client
    that can reach the origin directly can forge them with no way to tell.
    """
    n = TRUSTED_PROXY_HOPS if hops is None else hops
    client = scope.get("client")
    direct = client[0] if client else "unknown"
    if n < 1:
        return direct
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    chain = [p.strip() for p in headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if not chain:
        return direct
    return chain[-n] if len(chain) >= n else chain[-1]


SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "x-frame-options": "DENY",
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' https://avatars.githubusercontent.com data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


class SecurityHeadersMiddleware:
    """Add the baseline security headers to every response that does not set them."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.decode().lower() for k, _ in headers}
                headers.extend((k.encode(), v.encode()) for k, v in SECURITY_HEADERS.items() if k not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _bearer(scope: Any) -> str:
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-api-key", "").strip()


class AuthMiddleware:
    """Static key and/or key-store check with quota metering. Public paths pass through."""

    def __init__(self, app: Any, *, static_key: str | None, store: Any | None) -> None:
        self.app = app
        self.static = static_key.encode() if static_key else None
        self.store = store

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not _protected(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        supplied = _bearer(scope)
        if not supplied:
            await self._reject(scope, receive, send, 401, "missing key", "send Authorization: Bearer <key>" + (" (get one at POST /keys/signup)" if self.store else ""))
            return
        if self.static is not None and hmac.compare_digest(supplied.encode(), self.static):
            scope.setdefault("state", {})["auth"] = {"kind": "static"}
            await self.app(scope, receive, send)
            return
        if self.store is not None:
            verdict = self.store.verify_and_count(supplied)
            if verdict.ok:
                scope.setdefault("state", {})["auth"] = {"kind": "key", "key": verdict.key, "remaining": verdict.remaining}
                await self._with_headers(scope, receive, send, {"x-ratelimit-remaining-today": str(verdict.remaining), "x-ratelimit-limit-day": str(verdict.key.daily_quota), "x-ratelimit-limit-minute": str(verdict.key.rpm)})
                return
            extra = {"retry-after": str(verdict.retry_after)} if verdict.retry_after else {}
            await self._reject(scope, receive, send, verdict.status, verdict.reason, "key rejected", extra)
            return
        await self._reject(scope, receive, send, 401, "invalid key", "key not recognised")

    async def _with_headers(self, scope: Any, receive: Any, send: Any, extra: dict[str, str]) -> None:
        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend((k.encode(), v.encode()) for k, v in extra.items())
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)

    @staticmethod
    async def _reject(scope: Any, receive: Any, send: Any, status: int, error: str, message: str, extra: dict[str, str] | None = None) -> None:
        resp = JSONResponse({"ok": False, "error": error, "message": message}, status_code=status, headers={"WWW-Authenticate": "Bearer", **(extra or {})})
        await resp(scope, receive, send)


class _McpOnly:
    """Guard for the core MCP app, which is mounted at ``""`` so it can own ``/mcp``.

    Without this, that mount is the catch-all for the whole site and answers every
    unrouted path with its own bare ``404 Not Found``. Raising ``HTTPException``
    instead lets the site's branded 404 handler produce the response.
    """

    def __init__(self, app: Any, path: str = "/mcp") -> None:
        self.app, self.path = app, path

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        if scope["type"] == "http" and path != self.path and not path.startswith(self.path + "/"):
            raise HTTPException(status_code=404)
        await self.app(scope, receive, send)


def build_app(*, include_external: bool = True, include_files: bool = False, stateless: bool = True, json_response: bool = False, host: str = "0.0.0.0", api_key: str | None = None, keys_db: str | None = None, web_config: Any | None = None) -> Any:
    from .mcp_server import server as core

    servers: list[tuple[str, Any]] = [("", core)]
    if include_external:
        from .external.mcp_server import server as external

        servers.append(("/external", external))
    if include_files:
        from .files.mcp_server import server as files_srv

        servers.append(("/files", files_srv))

    from .web import build_web
    from .web.config import WebConfig

    cfg = web_config or WebConfig.from_env()

    store = None
    if keys_db:
        from .keys import PREFIX_LEN, KeyStore

        # the same secret that signs sessions encrypts the retrievable copy of each key
        store = KeyStore(keys_db, secret=cfg.secret)
    static_key = api_key if api_key is not None else os.environ.get("LEFTBRAIN_API_KEY") or None
    auth_kind = "none" if not (static_key or store) else ("keys" if store else "bearer")
    if cfg.oauth_ready and cfg.base_url is None:
        print(json.dumps({"warning": "LEFTBRAIN_BASE_URL is not set; the OAuth callback URL is derived from request headers"}), flush=True)

    mounts: list[Any] = []
    root_app = None
    for prefix, srv in servers:
        app = srv.streamable_http_app(stateless_http=stateless, json_response=json_response, host=host)
        if prefix:
            mounts.append(Mount(prefix, app=app))
        else:
            root_app = app

    async def index(request: Request) -> Any:
        from .web.views import landing, wants_html

        if wants_html(request):
            return await landing(request, store, cfg)
        return JSONResponse({"name": "leftbrain", "version": __version__, "description": "Exact, deterministic tools for AI agents", "endpoints": {"core": "/mcp", **({"external": "/external/mcp"} if include_external else {}), **({"files": "/files/mcp"} if include_files else {})}, "auth": auth_kind, "signup": "/keys/signup" if (store and cfg.open_signup) else None, "login": "/login", "docs": "/docs", "transport": "streamable-http", "stateless": stateless})

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "version": __version__})

    async def signup(request: Request) -> JSONResponse:
        if store is None or not cfg.open_signup:
            return JSONResponse({"ok": False, "error": "unsupported", "message": "self-serve signup is closed; sign in at /login to create a key"}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            form = await request.form()
            body = dict(form)
        email = str(body.get("email", ""))
        ip = _client_ip(request.scope)
        key, msg = store.signup(email, ip)
        if key is None:
            return JSONResponse({"ok": False, "error": "rejected", "message": msg}, status_code=429 if "limit" in msg else 400)
        info = store.get_by_prefix(key[:PREFIX_LEN])
        return JSONResponse({"ok": True, "key": key, "prefix": key[:PREFIX_LEN], "daily_quota": info.daily_quota if info else None, "rpm": info.rpm if info else None, "usage": "Authorization: Bearer <key> on /mcp and /external/mcp", "note": "store this key now; it cannot be shown again"}, status_code=201)

    async def me(request: Request) -> JSONResponse:
        auth = request.scope.get("state", {}).get("auth") or {}
        if auth.get("kind") == "key" and auth.get("key"):
            return JSONResponse({"ok": True, "result": auth["key"].to_dict()})
        if auth.get("kind") == "static":
            return JSONResponse({"ok": True, "result": {"kind": "static", "quota": "unlimited"}})
        return JSONResponse({"ok": False, "error": "unauthorized", "message": "no key"}, status_code=401)

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            for _prefix, srv in servers:
                await stack.enter_async_context(srv.session_manager.run())
            yield

    async def not_found(request: Request, _exc: Any) -> Any:
        from .web import auth as web_auth
        from .web.views import error_page, wants_html

        if wants_html(request):
            return error_page(request, 404, "Page not found", "That page isn't here. Try the docs, or start from the home page.", user=web_auth.current_user(request, cfg))
        return JSONResponse({"ok": False, "error": "unsupported", "message": "no such endpoint; see / for the endpoint list"}, status_code=404)

    routes: list[Any] = [Route("/", index), Route("/healthz", healthz), Route("/keys/signup", signup, methods=["POST"]), Route("/keys/me", me), *build_web(store, cfg), *mounts, Mount("", app=_McpOnly(root_app))]
    app: Any = Starlette(routes=routes, lifespan=lifespan, exception_handlers={404: not_found})
    if static_key or store:
        app = AuthMiddleware(app, static_key=static_key, store=store)
    return SecurityHeadersMiddleware(app)


def app_from_env() -> Any:
    """uvicorn factory: configuration comes from environment variables."""
    env = os.environ.get
    return build_app(
        include_external=env("LEFTBRAIN_SERVE_EXTERNAL", "1") != "0",
        include_files=env("LEFTBRAIN_SERVE_FILES", "0") in ("1", "true", "yes"),
        stateless=env("LEFTBRAIN_SERVE_STATELESS", "1") != "0",
        json_response=env("LEFTBRAIN_SERVE_JSON", "0") == "1",
        host=env("HOST", "0.0.0.0"),
        keys_db=env("LEFTBRAIN_KEYS_URL") or env("DATABASE_URL") or env("LEFTBRAIN_KEYS_DB") or None,
    )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="leftbrain-serve", description="Serve all leftbrain tool sets over Streamable HTTP")
    ap.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    ap.add_argument("--files", action="store_true", default=os.environ.get("LEFTBRAIN_SERVE_FILES", "") in ("1", "true", "yes"), help="also mount the files tools at /files/mcp")
    ap.add_argument("--no-external", action="store_true", help="do not mount the network tools")
    ap.add_argument("--stateful", action="store_true", help="keep per-client sessions (default: stateless, scales horizontally)")
    ap.add_argument("--json", action="store_true", help="plain JSON responses instead of SSE streams")
    ap.add_argument("--api-key", default=None, help="require this single bearer token (or set LEFTBRAIN_API_KEY)")
    ap.add_argument("--keys-db", default=None, help="enable per-user API keys + signup: SQLite path or postgres:// DSN (or set LEFTBRAIN_KEYS_URL / DATABASE_URL / LEFTBRAIN_KEYS_DB)")
    ap.add_argument("--workers", type=int, default=int(os.environ.get("WEB_CONCURRENCY", "1")))
    ap.add_argument("--version", action="version", version=f"leftbrain {__version__}")
    args = ap.parse_args(argv)

    try:
        import uvicorn
    except ImportError:  # pragma: no cover
        raise SystemExit("leftbrain-serve needs uvicorn: pip install 'leftbrain[server]'") from None

    if args.api_key:
        os.environ["LEFTBRAIN_API_KEY"] = args.api_key
    if args.keys_db:
        os.environ["LEFTBRAIN_KEYS_URL"] = args.keys_db
    os.environ["LEFTBRAIN_SERVE_FILES"] = "1" if args.files else "0"
    os.environ["LEFTBRAIN_SERVE_EXTERNAL"] = "0" if args.no_external else "1"
    os.environ["LEFTBRAIN_SERVE_STATELESS"] = "0" if args.stateful else "1"
    os.environ["LEFTBRAIN_SERVE_JSON"] = "1" if args.json else "0"
    os.environ["HOST"] = args.host
    keys_db = os.environ.get("LEFTBRAIN_KEYS_URL") or os.environ.get("DATABASE_URL") or os.environ.get("LEFTBRAIN_KEYS_DB")
    if keys_db and args.workers > 1:
        print("note: the SQLite key store keeps per-minute rate windows in memory; with several workers the per-minute limit is per worker (daily quotas stay exact)", flush=True)
    print(json.dumps({"leftbrain": __version__, "listen": f"http://{args.host}:{args.port}", "core": "/mcp", "external": None if args.no_external else "/external/mcp", "files": "/files/mcp" if args.files else None, "auth": "keys" if keys_db else ("bearer" if os.environ.get("LEFTBRAIN_API_KEY") else "none"), "signup": "/keys/signup" if (keys_db and os.environ.get("LEFTBRAIN_OPEN_SIGNUP", "0") in ("1", "true", "yes")) else None}), flush=True)
    uvicorn.run("leftbrain.serve:app_from_env", host=args.host, port=args.port, workers=args.workers, factory=True, log_level="info")


if __name__ == "__main__":
    main()
