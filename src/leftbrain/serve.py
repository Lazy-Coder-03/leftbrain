"""leftbrain-serve - one HTTP process exposing every leftbrain tool set.

Endpoints (Streamable HTTP MCP):
    /mcp            core tools (math, datetime, convert, ...)
    /external/mcp   weather, fx_rate, geo, url_check
    /files/mcp      pdf/image/file tools  (opt-in: --files or LEFTBRAIN_SERVE_FILES=1)
    /healthz        liveness JSON
    /               service description JSON
    /keys/signup    POST {"email": ...} -> issues a free API key   (when LEFTBRAIN_OPEN_SIGNUP is on)
    /keys/me        GET  -> quota, usage and tool scope for the calling key

Authentication (either or both may be enabled):
    LEFTBRAIN_API_KEY   a single static bearer token (private deployments)
    LEFTBRAIN_KEYS_DB   path to the SQLite key store: per-user keys, daily quota,
                        per-minute rate limit, self-serve signup, per-key tool scopes

A key with a scope (see ``leftbrain.scopes``) sees only its tools in ``tools/list`` and
gets the contract's ``forbidden`` error from any tool or mode outside it.

TLS is expected to be terminated by the host (Railway, Render, Fly, Cloudflare,
Caddy, nginx).
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from . import __version__, observe
from .scopes import Scope, allowed_tools, current_scope, current_tool_recorder

log = logging.getLogger("leftbrain")

MCP_PREFIXES = ("/mcp", "/external/mcp", "/files/mcp")
PROTECTED_PREFIXES = (*MCP_PREFIXES, "/keys/me", "/feedback")


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def _protected(path: str) -> bool:
    return _under(path, PROTECTED_PREFIXES)


def _mount_for(path: str, mounted: tuple[str, ...]) -> str:
    """The MCP endpoint ``path`` is under, longest match first; ``/mcp`` when it is under none.

    ``/external/mcp/x`` belongs to ``/external/mcp`` and not to ``/mcp``; a non-MCP path such
    as ``/keys/me`` is told about the core endpoint, which is the one every install has.
    """
    for prefix in sorted(mounted, key=len, reverse=True):
        if path == prefix or path.startswith(prefix + "/"):
            return prefix
    return "/mcp"


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


class RequestMetaMiddleware:
    """Stamp every request with an id and report what it cost (#28 SS6).

    The id and the latency go out as headers as well as inside `meta`, so they are visible
    without parsing the body - and so a caller can quote the id when reporting a slow call.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        # An id the caller already assigned is kept, so a trace spans both sides.
        request_id = (incoming.get(observe.REQUEST_ID_HEADER) or "")[:64] or observe.new_request_id()
        token = observe.current_request_id.set(request_id)
        started = time.perf_counter()

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((observe.REQUEST_ID_HEADER.encode(), request_id.encode()))
                headers.append((observe.LATENCY_HEADER.encode(), str(round((time.perf_counter() - started) * 1000)).encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            observe.current_request_id.reset(token)


def _bearer(scope: Any) -> str:
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-api-key", "").strip()


class AuthMiddleware:
    """Static key and/or key-store check with quota metering. Public paths pass through."""

    def __init__(self, app: Any, *, static_key: str | None, store: Any | None, base_url: str | None = None, mounted: tuple[str, ...] = ("/mcp",), signup_open: bool = False) -> None:
        self.app = app
        self.static = static_key.encode() if static_key else None
        self.store = store
        #: set only when this server is an OAuth authorization server, so a 401 can point
        #: at the discovery document and tell an agent what to do next (#34)
        self.base_url = base_url
        #: the MCP endpoints actually mounted, so a 401 on /external/mcp points at that
        #: endpoint's own metadata and not at /mcp's (#101)
        self.mounted = mounted
        #: whether POST /keys/signup answers; the 401 must not send anyone to a closed door (#104)
        self.signup_open = signup_open
        # resolved once here rather than imported at module level, which is how the rest of
        # this file keeps `keys` (and its optional drivers) off the import path
        from .keys import KEY_PREFIX

        self.key_prefix = KEY_PREFIX

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not _protected(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        supplied = _bearer(scope)
        if not supplied:
            await self._reject(scope, receive, send, 401, "missing key", "send Authorization: Bearer <key>" + self._where_to_get_one())
            return
        if self.static is not None and hmac.compare_digest(supplied.encode(), self.static):
            scope.setdefault("state", {})["auth"] = {"kind": "static"}
            await self.app(scope, receive, send)
            return
        if self.store is not None:
            # A credential that is not a leftbrain key is an OAuth access token. Both resolve
            # to a key row and meter through the same code, so quota, rpm and tool scope are
            # enforced in one place whichever door the caller came through (#34).
            verdict = (
                self.store.verify(supplied)
                if supplied.startswith(self.key_prefix)
                else self.store.verify_oauth_token(supplied)
            )
            if verdict.ok:
                scope.setdefault("state", {})["auth"] = {"kind": "key", "key": verdict.key, "remaining": verdict.remaining}
                key_scope = verdict.key.scope
                # The same numbers the headers carry, so `meta.quota` can show them and an
                # agent can back off before it hits a 429 (#28 SS6). Mutable, and read at
                # response time rather than copied now: `remaining_today` is only settled
                # once the tool has run and either spent a unit or not (#62). `_with_headers`
                # holds the response start back so the header reads the same settled figure
                # that `meta.quota` does.
                quota = {"remaining_today": verdict.remaining, "daily_quota": verdict.key.daily_quota, "rpm": verdict.key.rpm}
                quota_token = observe.current_quota.set(quota)
                token = current_scope.set(key_scope)  # what enforce() reads inside every tool
                # enforce() also counts each call against this key, so the scope editor can
                # show its owner which tools it has actually reached for (#34) - and spends
                # the daily unit, which is a tool call rather than an HTTP request (#62).
                prefix = verdict.key.prefix

                def record(tool: str, spends: bool, prefix: str = prefix, quota: dict[str, int] = quota) -> None:
                    self.store.record_tool_call(prefix, tool)
                    if spends:
                        self.store.charge(prefix)
                        quota["remaining_today"] = max(0, quota["remaining_today"] - 1)

                recorder = current_tool_recorder.set(record)
                try:
                    if key_scope is not None and scope.get("method") == "POST" and _under(scope.get("path", ""), MCP_PREFIXES):
                        await self._scoped(scope, receive, send, quota, key_scope)
                    else:
                        await self._with_headers(scope, receive, send, quota)
                finally:
                    current_tool_recorder.reset(recorder)
                    current_scope.reset(token)
                    observe.current_quota.reset(quota_token)
                return
            extra = {"retry-after": str(verdict.retry_after)} if verdict.retry_after else {}
            await self._reject(scope, receive, send, verdict.status, verdict.reason, verdict.message or "key rejected", extra)
            return
        await self._reject(scope, receive, send, 401, "invalid key", "key not recognised")

    @staticmethod
    def _quota_headers(quota: dict[str, int]) -> dict[str, str]:
        return {
            "x-ratelimit-remaining-today": str(quota["remaining_today"]),
            "x-ratelimit-limit-day": str(quota["daily_quota"]),
            "x-ratelimit-limit-minute": str(quota["rpm"]),
        }

    async def _with_headers(self, scope: Any, receive: Any, send: Any, quota: dict[str, int]) -> None:
        """Run the app, adding the quota headers to whatever it answers.

        On an MCP `POST` the response start is held back until there is a body to send with
        it. A streamed reply starts its response *before* the tool runs, so a header written
        then would quote the budget from before the call while `meta.quota` in the body
        quotes it from after - one response, two figures for the same thing (#62). Waiting
        for the first chunk costs nothing here, because that chunk is the result.

        `POST` only: a long-lived `GET` stream may legitimately have nothing to say for
        minutes, and its headers must not wait for that.
        """
        defer = scope.get("method") == "POST" and _under(scope.get("path", ""), MCP_PREFIXES)
        pending: Any = None

        async def start(message: Any) -> None:
            headers = list(message.get("headers", []))
            headers.extend((k.encode(), v.encode()) for k, v in self._quota_headers(quota).items())
            await send({**message, "headers": headers})

        async def send_wrapper(message: Any) -> None:
            nonlocal pending
            if message["type"] == "http.response.start":
                if defer:
                    pending = message
                    return
                await start(message)
                return
            if pending is not None:
                held, pending = pending, None
                await start(held)
            await send(message)

        await self.app(scope, receive, send_wrapper)

    async def _scoped(self, scope: Any, receive: Any, send: Any, quota: dict[str, int], key_scope: Scope) -> None:
        """A scoped key's MCP POST: read the body once, and if it is ``tools/list`` trim the reply to the key's tools.

        The body is replayed to the app unchanged either way; only a ``tools/list`` reply is
        buffered and rewritten, so every other call streams through as it always did.
        """
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return  # the client went away before sending its body
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Any:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        if not _is_tools_list(bytes(body)):
            await self._with_headers(scope, replay, send, quota)
            return

        start: Any = None
        chunks = bytearray()

        async def buffered(message: Any) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
                return
            if message["type"] == "http.response.body":
                chunks.extend(message.get("body", b""))
                if message.get("more_body", False):
                    return
                headers, out = _filter_tools_list(list(start.get("headers", [])), bytes(chunks), key_scope)
                await send({**start, "headers": headers})
                await send({"type": "http.response.body", "body": out, "more_body": False})
                return
            await send(message)

        await self._with_headers(scope, replay, buffered, quota)

    def _where_to_get_one(self) -> str:
        """The clause after "send a key": only ever a door that is open.

        0.4.1 said "get one at POST /keys/signup" whenever a key store existed, and that route
        answers 404 "signup is closed" unless LEFTBRAIN_OPEN_SIGNUP is on — the hosted server
        sent every unauthenticated agent to a closed door (#104).
        """
        if self.signup_open:
            return " (get one at POST /keys/signup)"
        if self.store is not None:
            return " (sign in at /login to create one)"
        return ""

    def _metadata_url(self, path: str) -> str:
        """The protected-resource document for the endpoint ``path`` is under (RFC 9728 §3.1)."""
        return f"{self.base_url}/.well-known/oauth-protected-resource{_mount_for(path, self.mounted)}"

    def _challenge(self, path: str) -> str:
        """RFC 9728: point the client at the document that names our authorization server.

        The pointer has to name the document for the endpoint that was actually asked for.
        RFC 9728 §3.3 has the client check that document's ``resource`` against the URL it is
        connecting to, and Claude Code does: a 401 on /external/mcp that pointed at /mcp's
        document was refused with "does not match expected" before any tool call (#101).
        """
        if not self.base_url:
            return "Bearer"
        return f'Bearer realm="leftbrain", resource_metadata="{self._metadata_url(path)}"'

    def _how_to_authorize(self, path: str) -> dict[str, str]:
        """What an agent should do next, in fields it can act on and a line it can read aloud."""
        return {
            "if_you_have_a_browser": self._metadata_url(path),
            "if_you_have_no_browser": f"POST {self.base_url}/register, then POST {self.base_url}/oauth/device_authorization",
            "tell_your_user": f"leftbrain needs authorising. I can give you a short code to approve at {self.base_url}/device",
            "static_key_alternative": f"{self.base_url}/dashboard",
            "documentation": f"{self.base_url}/docs/agents/auth",
        }

    async def _reject(self, scope: Any, receive: Any, send: Any, status: int, error: str, message: str, extra: dict[str, str] | None = None) -> None:
        # the three existing fields are untouched; a valid key never sees this body at all
        body: dict[str, Any] = {"ok": False, "error": error, "message": message}
        path = scope.get("path", "")
        if self.base_url:
            body["how_to_authorize"] = self._how_to_authorize(path)
        resp = JSONResponse(body, status_code=status, headers={"WWW-Authenticate": self._challenge(path), **(extra or {})})
        await resp(scope, receive, send)


def _is_tools_list(body: bytes) -> bool:
    try:
        msg = json.loads(body)
    except ValueError:
        return False  # the transport answers malformed JSON itself
    return isinstance(msg, dict) and msg.get("method") == "tools/list"


def _trim_tools(raw: bytes, key_scope: Scope) -> bytes | None:
    """``raw`` JSON-RPC message with ``result.tools`` cut down to the scope, or None when it is not one."""
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    result = msg.get("result") if isinstance(msg, dict) else None
    if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
        return None
    keep = set(allowed_tools(key_scope, [t.get("name", "") for t in result["tools"]]))
    result["tools"] = [t for t in result["tools"] if t.get("name") in keep]
    return json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode()


def _filter_tools_list(headers: list[tuple[bytes, bytes]], body: bytes, key_scope: Scope) -> tuple[list[tuple[bytes, bytes]], bytes]:
    """Rewrite a ``tools/list`` reply in either wire form.

    ``json_response=True`` gives a plain JSON body; the default is an SSE body of
    ``event: message`` / ``data: {...}`` lines. The JSON inside each ``data:`` line is
    rewritten and the framing (line endings included) is kept; ``content-length`` is
    corrected when the reply carries one.
    """
    ctype = next((v for k, v in headers if k.lower() == b"content-type"), b"").decode().lower()
    if ctype.startswith("text/event-stream"):
        lines = body.split(b"\n")
        for i, line in enumerate(lines):
            bare = line.rstrip(b"\r")
            if bare.startswith(b"data:"):
                trimmed = _trim_tools(bare[5:].strip(), key_scope)
                if trimmed is not None:
                    lines[i] = b"data: " + trimmed + line[len(bare):]
        body = b"\n".join(lines)
    elif ctype.startswith("application/json"):
        body = _trim_tools(body, key_scope) or body
    else:
        return headers, body
    headers = [(k, str(len(body)).encode() if k.lower() == b"content-length" else v) for k, v in headers]
    return headers, body


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

    # every MCP endpoint this process serves: /mcp, and /external/mcp and /files/mcp when
    # mounted. Each gets its own protected-resource document and its own 401 pointer (#101).
    mounted = tuple(f"{prefix}/mcp" for prefix, _ in servers)

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
        return JSONResponse({"name": "leftbrain", "version": __version__, "description": "Exact, deterministic tools for AI agents", "endpoints": {"core": "/mcp", **({"external": "/external/mcp"} if include_external else {}), **({"files": "/files/mcp"} if include_files else {})}, "auth": auth_kind, "signup": "/keys/signup" if (store and cfg.open_signup) else None, "login": "/login", "docs": "/docs", "tools": "/docs/tools", "transport": "streamable-http", "stateless": stateless})

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
        return JSONResponse({"ok": True, "key": key, "prefix": key[:PREFIX_LEN], "daily_quota": info.daily_quota if info else None, "rpm": info.rpm if info else None, "usage": "Authorization: Bearer <key> on /mcp and /external/mcp", "note": "store this key now" + ("" if store.can_reveal else "; it cannot be shown again")}, status_code=201)

    async def feedback(request: Request) -> JSONResponse:
        """File a report against the repository, for a caller who cannot see it (#53)."""
        from .feedback import MAX_PER_KEY, compose, feedback_config, submit

        fb = feedback_config()
        if not fb.enabled:
            return JSONResponse({"ok": False, "error": "unsupported", "message": "feedback is not configured on this server"}, status_code=404)
        auth = request.scope.get("state", {}).get("auth") or {}
        key = auth.get("key")
        try:
            body = await request.json()
        except Exception:
            body = dict(await request.form())
        prefix = getattr(key, "prefix", None)
        if prefix and store is not None:
            # A lifetime cap rather than a daily one: feedback is rare and spam is cheap.
            if store.tool_counts(prefix).get("__feedback__", 0) >= MAX_PER_KEY:
                return JSONResponse({"ok": False, "error": "rejected", "message": f"this key has filed {MAX_PER_KEY} reports, which is the limit"}, status_code=429)
        try:
            report = compose(body, f"key {prefix}" if prefix else "a static-key caller")
        except ValueError as e:
            return JSONResponse({"ok": False, "error": "invalid_input", "message": str(e)}, status_code=400)
        try:
            created = await run_in_threadpool(submit, report, fb, __version__)
        except Exception:
            log.exception("filing feedback failed")
            return JSONResponse({"ok": False, "error": "internal", "message": "the report could not be filed; nothing was lost on your side", "retryable": True}, status_code=502)
        if prefix and store is not None:
            store.record_tool_call(prefix, "__feedback__")
        return JSONResponse({"ok": True, "result": {**created, "kind": report.kind}, "message": "thank you - the report is on the tracker"}, status_code=201)

    async def me(request: Request) -> JSONResponse:
        auth = request.scope.get("state", {}).get("auth") or {}
        if auth.get("kind") == "key" and auth.get("key"):
            return JSONResponse({"ok": True, "result": auth["key"].to_dict()})
        if auth.get("kind") == "static":
            return JSONResponse({"ok": True, "result": {"kind": "static", "quota": "unlimited"}})
        return JSONResponse({"ok": False, "error": "unauthorized", "message": "no key"}, status_code=401)

    async def me_scope(request: Request) -> JSONResponse:
        """Propose narrowing this credential's own key. A human approves it; this does not (#34).

        Widening is refused outright rather than queued, because a credential that can *ask*
        for more is one whose scope is a suggestion — and anything that hijacks the agent
        inherits the ability to ask.
        """
        from .scopes import narrows, parse_scope, summarize

        info = (request.scope.get("state", {}).get("auth") or {}).get("key")
        if info is None:
            return JSONResponse({"ok": False, "error": "unsupported", "message": "this server's credential has no scope to narrow"}, status_code=400)
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            proposed = parse_scope((body or {}).get("tools"))
        except ValueError as e:
            return JSONResponse({"ok": False, "error": "invalid", "message": str(e)}, status_code=400)
        if proposed is None:
            return JSONResponse({"ok": False, "error": "invalid", "message": 'name the tools to keep, e.g. {"tools": ["math", "convert"]}'}, status_code=400)
        if not narrows(info.scope, proposed):
            return JSONResponse({"ok": False, "error": "forbidden", "message": f"a key may only narrow its own scope; it holds {summarize(info.scope)} and asked for {proposed.summary()}. Widen it at /dashboard."}, status_code=403)
        from .keys import SCOPE_REQUEST_TTL

        request_id = store.open_scope_request(info.prefix, proposed)
        url = f"{cfg.base_url or ''}/keys/scope-request/{request_id}"
        return JSONResponse({"ok": True, "result": {
            "status": "pending_approval",
            "approve_url": url,
            "tell_your_user": f"I would like to give up some of my own access to leftbrain, keeping only {proposed.listing()}. Approve at {url}",
            "expires_in": SCOPE_REQUEST_TTL,
            "check": "GET /keys/me",
        }}, status_code=202)

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        from . import runner

        # Start the workers with the server, so the first real request does not pay for it
        # and a misconfiguration is visible at boot rather than under load (#28 SS1 step 3).
        runner.configure()
        if runner.isolation_active():
            print(json.dumps({"compute_isolation": "on", "timeout_s": runner.settings.timeout, "workers": runner.settings.max_inflight}), flush=True)
        else:
            print(json.dumps({"compute_isolation": "off", "warning": "a runaway call cannot be stopped; install leftbrain[server]"}), flush=True)
        try:
            async with AsyncExitStack() as stack:
                for _prefix, srv in servers:
                    await stack.enter_async_context(srv.session_manager.run())
                yield
        finally:
            runner.shutdown()

    async def not_found(request: Request, _exc: Any) -> Any:
        from .web import auth as web_auth
        from .web.views import error_page, wants_html

        if wants_html(request):
            return error_page(request, 404, "Page not found", "That page isn't here. Try the docs, or start from the home page.", user=web_auth.current_user(request, cfg))
        return JSONResponse({"ok": False, "error": "unsupported", "message": "no such endpoint; see / for the endpoint list"}, status_code=404)

    from .oauth import build_oauth_routes

    # before the catch-all mount, which 404s anything that is not /mcp-shaped: the discovery
    # documents and /register must answer for their own sake (#34)
    oauth_routes = build_oauth_routes(store, cfg, mounted=mounted)

    routes: list[Any] = [Route("/", index), Route("/healthz", healthz), Route("/keys/signup", signup, methods=["POST"]), Route("/keys/me", me), Route("/keys/me/scope", me_scope, methods=["POST"]), Route("/feedback", feedback, methods=["POST"]), *build_web(store, cfg), *oauth_routes, *mounts, Mount("", app=_McpOnly(root_app))]
    app: Any = Starlette(routes=routes, lifespan=lifespan, exception_handlers={404: not_found})
    if static_key or store:
        app = AuthMiddleware(app, static_key=static_key, store=store, base_url=cfg.base_url if oauth_routes else None, mounted=mounted, signup_open=bool(store and cfg.open_signup))
    return RequestMetaMiddleware(SecurityHeadersMiddleware(app))


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
