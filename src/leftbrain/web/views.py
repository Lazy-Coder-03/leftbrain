"""Route handlers for the web site."""

from __future__ import annotations

import json
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from ..scopes import CATALOGUE, Scope, parse_scope
from . import auth, templates
from .config import WebConfig
from .tools_list import TOOLS


def wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def free_tier() -> dict[str, Any]:
    """The limits every page quotes, read at request time so a configured default is what shows."""
    from .. import keys as keys_mod

    return {"daily_quota": keys_mod.DEFAULT_DAILY, "rpm": keys_mod.DEFAULT_RPM, "max_keys": keys_mod.MAX_ACTIVE_KEYS_PER_EMAIL}


def render(request: Request, name: str, status: int = 200, **ctx: Any) -> Response:
    return templates.TemplateResponse(request, name, {**free_tier(), **ctx}, status_code=status)


def no_store(response: Response) -> Response:
    """Signed-in pages and key mutations must never be cached by a proxy or the browser."""
    response.headers["cache-control"] = "no-store"
    return response


def error_page(request: Request, status: int, title: str, message: str, user: Any = None) -> Response:
    return no_store(render(request, "error.html", status, title=title, message=message, page="error", user=user))


def fail_page_for(request: Request, cfg: WebConfig, status: int, title: str, message: str) -> Response:
    """`fail_page` for callers outside `routes()`: an error page that keeps the nav signed in."""
    return error_page(request, status, title, message, user=auth.current_user(request, cfg))


def parse_grid_scope(form: Any) -> Scope | None:
    """The tool grid as posted, read the same way wherever the grid appears.

    An empty tick-list is **not** "every tool". `parse_scope([])` raises and the caller
    renders that as a form error; substituting `None` here would read "the user chose
    nothing" as "the user chose everything", which is a one-word mistake that fails open.
    """
    values = [str(v) for v in form.getlist("scope")]
    ticked = {v for v in values if ":" not in v}
    values = [v for v in values if ":" not in v or v.partition(":")[0] in ticked]
    return parse_scope(values)


def routes(store: Any, cfg: WebConfig) -> list[Any]:
    from ..serve import _client_ip  # a module-level import would be circular
    from . import demo as demo_mod
    from . import docs as docs_mod
    from . import toolref as toolref_mod

    throttle = demo_mod.Throttle()

    def fail_page(request: Request, status: int, title: str, message: str) -> Response:
        """An error page that still shows who is signed in, so the nav does not flip to Sign in."""
        return error_page(request, status, title, message, user=auth.current_user(request, cfg))

    def docs_shell(request: Request, title: str, html: str, slug: str, tool: str | None = None, *, user: auth.User | None = None, keys: Any = (), selected: Any = None, note: str = "") -> Response:
        return render(request, "docs.html", 200, page="docs", user=user, title=title, body=html, slug=slug, pages=docs_mod.PAGES, tools=toolref_mod.tool_names(), tool=tool, keybar=keys, key_selected=selected, key_note=note)

    def docs_key(request: Request, user: auth.User | None) -> tuple[list[Any], Any, str | None]:
        """The reader's own usable, revealable keys, which one the page uses, and its plaintext."""
        if user is None or store is None:
            return [], None, None
        keys = [k for k in store.list(user.email) if k.usable and k.revealable]
        if not keys:
            return [], None, None
        wanted = request.query_params.get("key")
        # store.list() is newest first; a prefix they do not own simply falls back to that
        chosen = next((k for k in keys if k.prefix == wanted), keys[0])
        return keys, chosen, store.reveal(user.email, chosen.prefix)

    def docs_note(user: auth.User | None) -> str:
        if store is None:
            return ""
        if user is None:
            return '<a href="/login">Sign in</a> to fill in your key wherever these examples ask for one.'
        return '<a href="/dashboard">Create a key</a> to fill it in wherever these examples ask for one.'

    async def docs_page(request: Request) -> Response:
        slug = request.path_params.get("slug", "quickstart")
        # `/docs/tools` is the one docs page an agent has a reason to read, so it answers
        # in JSON to a client that did not ask for HTML - the same negotiation `/` does.
        if slug == "tools" and not wants_html(request):
            return JSONResponse(toolref_mod.catalogue_json())
        page = docs_mod.load_page(slug)
        if page is None:
            return fail_page(request, 404, "Page not found", "That docs page doesn't exist. Try the quickstart.")
        title, html = page
        html = docs_mod.fill_defaults(html)
        user = auth.current_user(request, cfg)
        # Only a page that actually asks for a key gets the picker; the tool index and the
        # changelog would otherwise offer to fill in examples they do not have.
        wants_key = docs_mod.KEY_PLACEHOLDER in html
        keys, selected, raw = docs_key(request, user) if wants_key else ([], None, None)
        note = "" if raw or not wants_key else docs_note(user)
        resp = docs_shell(request, title, docs_mod.fill_key(html, raw), slug, user=user, keys=keys, selected=selected, note=note)
        return no_store(resp) if raw else resp  # a page carrying a real key is never cached

    async def tool_page(request: Request) -> Response:
        name = request.path_params["name"]
        if not wants_html(request):
            doc = toolref_mod.tool_json(name)
            if doc is None:
                return JSONResponse({"ok": False, "error": "unsupported", "message": f"no such tool: {name}"}, status_code=404)
            return JSONResponse(doc)
        page = await run_in_threadpool(toolref_mod.tool_page, name)  # first build runs every example
        if page is None:
            return fail_page(request, 404, "No such tool", "leftbrain has fourteen tools; that isn't one of them.")
        title, html = page
        return docs_shell(request, title, html, "tools", tool=name, user=auth.current_user(request, cfg))

    async def demo(request: Request) -> Response:
        tool = request.path_params["tool"]
        if tool not in demo_mod.DEMO_TOOLS:
            return JSONResponse({"ok": False, "error": "unsupported", "message": f"demo supports {', '.join(demo_mod.DEMO_TOOLS)}"}, status_code=404)
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > demo_mod.MAX_BODY:
            return JSONResponse({"ok": False, "error": "invalid_input", "message": "body too large"}, status_code=413)
        ok, retry = throttle.allow(_client_ip(request.scope))
        if not ok:
            return JSONResponse({"ok": False, "error": "rate_limited", "message": f"demo limit reached; get a free key for {free_tier()['daily_quota']:,} calls/day"}, status_code=429, headers={"retry-after": str(retry)})
        # Read the body ourselves so the cap holds for chunked requests too (no content-length).
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > demo_mod.MAX_BODY:
                return JSONResponse({"ok": False, "error": "invalid_input", "message": "body too large"}, status_code=413)
        try:
            args = json.loads(bytes(body) or b"null")
            if not isinstance(args, dict):
                raise ValueError
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid_input", "message": "send a JSON object with a mode and the tool's arguments"}, status_code=400)
        try:
            rejected = demo_mod.validate(tool, args)
            if rejected is not None:
                return JSONResponse(rejected, status_code=400)
            # core functions are sync and can be slow: keep them off the event loop
            result = await run_in_threadpool(demo_mod.run, tool, args)
        except Exception:
            return JSONResponse({"ok": False, "error": "internal", "message": "the tool failed; try different input"}, status_code=500)
        if isinstance(result, dict):
            result.pop("trace", None)  # never leak a traceback from the public demo
        return JSONResponse(result)

    async def login(request: Request) -> Response:
        if not cfg.oauth_ready:
            return no_store(
                render(
                    request,
                    "login.html",
                    200,
                    page="login",
                    user=None,
                    notice="Sign-in is not configured on this server. Set GITHUB_CLIENT_ID, "
                    "GITHUB_CLIENT_SECRET and LEFTBRAIN_SECRET.",
                )
            )
        state = auth.new_state()
        resp = RedirectResponse(
            auth.authorize_url(cfg, auth.base_url(request, cfg) + "/auth/github/callback", state),
            status_code=302,
        )
        resp.set_cookie(
            auth.OAUTH_COOKIE,
            auth.sign_state(cfg.secret or "", state),
            max_age=auth.OAUTH_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=auth.is_https(request),
            path="/",
        )
        return no_store(resp)

    async def callback(request: Request) -> Response:
        if not cfg.oauth_ready:
            return fail_page(request, 404, "Sign-in unavailable", "Sign-in is not configured on this server.")
        expected = auth.read_state(cfg.secret or "", request.cookies.get(auth.OAUTH_COOKIE))
        got = request.query_params.get("state")
        code = request.query_params.get("code")
        if not expected or not got or expected != got or not code:
            return no_store(
                render(
                    request,
                    "login.html",
                    400,
                    page="login",
                    user=None,
                    notice="That sign-in link is stale or invalid. Please sign in again.",
                )
            )
        try:
            user = await auth.fetch_github_user(cfg, code, auth.base_url(request, cfg) + "/auth/github/callback")
        except auth.OAuthError as e:
            return no_store(render(request, "login.html", e.status, page="login", user=None, notice=e.message))
        resp = RedirectResponse("/dashboard", status_code=302)
        resp.delete_cookie(auth.OAUTH_COOKIE, path="/")
        auth.set_session_cookie(resp, request, cfg, user)
        return no_store(resp)

    async def logout(request: Request) -> Response:
        user = auth.current_user(request, cfg)
        if user is not None:  # a session-less logout changes nothing; nothing to forge
            form = await request.form()
            if not auth.csrf_ok(cfg.secret or "", user, str(form.get("csrf") or "")):
                return fail_page(request, 403, "Form expired", "Please go back to the dashboard and try again.")
        resp = RedirectResponse("/", status_code=302)
        auth.clear_session_cookie(resp, request)
        return no_store(resp)

    def require_user(request: Request) -> auth.User | None:
        return auth.current_user(request, cfg)

    def dashboard_ctx(request: Request, user: auth.User, **extra: Any) -> dict[str, Any]:
        from ..keys import (
            DEFAULT_DAILY,
            DEFAULT_LIFETIME_DAYS,
            LIFETIME_CHOICES,
            MAX_ACTIVE_KEYS_PER_EMAIL,
            NEVER_EXPIRES_WARNING,
        )

        keys = store.list(user.email) if store else []
        return {"page": "dashboard", "user": user, "keys": keys, "csrf": auth.csrf_token(cfg.secret or "", user), "today_total": sum(k.used_today for k in keys), "active": sum(1 for k in keys if k.holds_slot), "max_keys": MAX_ACTIVE_KEYS_PER_EMAIL, "daily_quota": DEFAULT_DAILY, "lifetimes": LIFETIME_CHOICES, "default_lifetime": DEFAULT_LIFETIME_DAYS, "never_warning": NEVER_EXPIRES_WARNING, "new_key": None, "revealed": False, "can_reveal": bool(store and store.can_reveal), "error": None, "catalogue": CATALOGUE, "scope_of": None, **extra}

    def parse_form_scope(form: Any, *, required: bool) -> tuple[bool, Scope | None | str]:
        """The tool grid's checkbox values: ``tool`` ticks a tool, ``tool:mode`` narrows it to those modes.

        A mode box only counts when its tool is ticked (without script support an unticked
        tool still posts its mode boxes). No grid at all (``scope_form`` absent: an older form
        or a scripted post) means every tool unless ``required``. Returns ``(ok, scope)`` or
        ``(False, message)``.
        """
        if "scope_form" not in form and not required:
            return True, None
        values = [str(v) for v in form.getlist("scope")]
        ticked = {v for v in values if ":" not in v}
        values = [v for v in values if ":" not in v or v.partition(":")[0] in ticked]
        try:
            return True, parse_scope(values)
        except ValueError as e:
            return False, f"Tools: {e}."

    def parse_form_lifetime(value: str) -> tuple[bool, int | None]:
        """The create form's lifetime: one of the offered day counts, ``never``, or the default when absent."""
        from ..keys import DEFAULT_LIFETIME_DAYS, LIFETIME_CHOICES

        value = (value or "").strip().lower()
        if not value:
            return True, DEFAULT_LIFETIME_DAYS
        if value == "never":
            return True, None
        if value.isdigit() and int(value) in LIFETIME_CHOICES:
            return True, int(value)
        return False, None

    def keys_unavailable(request: Request) -> Response:
        return fail_page(request, 503, "Keys unavailable", "This server has no key store configured.")

    async def dashboard(request: Request) -> Response:
        user = require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        if store is None:
            return keys_unavailable(request)
        return no_store(render(request, "dashboard.html", 200, **dashboard_ctx(request, user)))

    async def create_key(request: Request) -> Response:
        user = require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        form = await request.form()
        if not auth.csrf_ok(cfg.secret or "", user, str(form.get("csrf") or "")):
            return fail_page(request, 403, "Form expired", "Please go back to the dashboard and try again.")
        if store is None:
            return keys_unavailable(request)
        ok, lifetime = parse_form_lifetime(str(form.get("lifetime") or ""))
        if not ok:
            return no_store(render(request, "dashboard.html", 200, **dashboard_ctx(request, user, error="Pick a lifetime from the list: 30, 90 or 365 days, or never.")))
        ok, scope = parse_form_scope(form, required=False)
        if not ok:
            return no_store(render(request, "dashboard.html", 200, **dashboard_ctx(request, user, error=scope)))
        raw, info = store.create_for_owner(user.email, str(form.get("name") or ""), lifetime_days=lifetime, scope=scope)
        if raw is None:
            return no_store(render(request, "dashboard.html", 200, **dashboard_ctx(request, user, error=info)))
        return no_store(render(request, "dashboard.html", 200, **dashboard_ctx(request, user, new_key=raw)))

    async def reveal_key(request: Request) -> Response:
        user = require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        form = await request.form()
        if not auth.csrf_ok(cfg.secret or "", user, str(form.get("csrf") or "")):
            return fail_page(request, 403, "Form expired", "Please go back to the dashboard and try again.")
        if store is None:
            return keys_unavailable(request)
        prefix = request.path_params["prefix"]
        if not store.owns(user.email, prefix):
            return fail_page(request, 403, "Not your key", "That key belongs to a different account.")
        raw = store.reveal(user.email, prefix)
        if raw is None:
            return no_store(render(request, "dashboard.html", 200, **dashboard_ctx(request, user, error=f"{prefix} cannot be shown again — it was created before reveal was enabled, or it has since been revoked. Create a new key instead.")))
        return no_store(render(request, "dashboard.html", 200, **dashboard_ctx(request, user, new_key=raw, revealed=True)))

    async def revoke_key(request: Request) -> Response:
        user = require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        form = await request.form()
        if not auth.csrf_ok(cfg.secret or "", user, str(form.get("csrf") or "")):
            return fail_page(request, 403, "Form expired", "Please go back to the dashboard and try again.")
        if store is None:
            return keys_unavailable(request)
        prefix = request.path_params["prefix"]
        if not store.owns(user.email, prefix):
            return fail_page(request, 403, "Not your key", "That key belongs to a different account.")
        store.set_disabled(prefix, True)
        return no_store(RedirectResponse("/dashboard", status_code=302))

    async def delete_key(request: Request) -> Response:
        """Drop a revoked or expired key and its usage rows for good; a key that still works must be revoked first."""
        user = require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        form = await request.form()
        if not auth.csrf_ok(cfg.secret or "", user, str(form.get("csrf") or "")):
            return fail_page(request, 403, "Form expired", "Please go back to the dashboard and try again.")
        if store is None:
            return keys_unavailable(request)
        prefix = request.path_params["prefix"]
        if not store.owns(user.email, prefix):
            return fail_page(request, 403, "Not your key", "That key belongs to a different account.")
        info = store.get_by_prefix(prefix)
        if info is not None and info.usable:
            return fail_page(request, 409, "Revoke it first", f"{prefix} still works. Revoke it, then delete it.")
        store.revoke(prefix)  # the store's revoke is the hard delete; the dashboard's Revoke only disables
        return no_store(RedirectResponse("/dashboard", status_code=302))

    def scope_ctx(user: auth.User, info: Any, error: str | None = None) -> dict[str, Any]:
        return {"page": "dashboard", "user": user, "key": info, "csrf": auth.csrf_token(cfg.secret or "", user), "catalogue": CATALOGUE, "scope_of": info.scope, "error": error}

    async def scope_page(request: Request) -> Response:
        """The tool grid for one key, pre-filled with what it may call today."""
        user = require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        if store is None:
            return keys_unavailable(request)
        prefix = request.path_params["prefix"]
        if not store.owns(user.email, prefix):
            return fail_page(request, 403, "Not your key", "That key belongs to a different account.")
        return no_store(render(request, "key_scope.html", 200, **scope_ctx(user, store.get_by_prefix(prefix))))

    async def set_scope(request: Request) -> Response:
        """Replace the key's scope with the grid as posted; the server is stateless, so the next call sees it."""
        user = require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        form = await request.form()
        if not auth.csrf_ok(cfg.secret or "", user, str(form.get("csrf") or "")):
            return fail_page(request, 403, "Form expired", "Please go back to the dashboard and try again.")
        if store is None:
            return keys_unavailable(request)
        prefix = request.path_params["prefix"]
        if not store.owns(user.email, prefix):
            return fail_page(request, 403, "Not your key", "That key belongs to a different account.")
        ok, scope = parse_form_scope(form, required=True)
        if not ok:
            return no_store(render(request, "key_scope.html", 200, **scope_ctx(user, store.get_by_prefix(prefix), error=scope)))
        store.set_scope(prefix, scope)
        return no_store(RedirectResponse("/dashboard", status_code=302))

    return [
        Route("/login", login),
        Route("/auth/github/callback", callback),
        Route("/logout", logout, methods=["POST"]),
        Route("/dashboard", dashboard),
        Route("/dashboard/keys", create_key, methods=["POST"]),
        Route("/dashboard/keys/{prefix}/reveal", reveal_key, methods=["POST"]),
        Route("/dashboard/keys/{prefix}/revoke", revoke_key, methods=["POST"]),
        Route("/dashboard/keys/{prefix}/delete", delete_key, methods=["POST"]),
        Route("/dashboard/keys/{prefix}/scope", scope_page, methods=["GET"]),
        Route("/dashboard/keys/{prefix}/scope", set_scope, methods=["POST"]),
        Route("/demo/{tool}", demo, methods=["POST"]),
        Route("/docs", docs_page),
        Route("/docs/tools/{name}", tool_page),
        Route("/docs/{slug}", docs_page),
    ]


async def landing(request: Request, store: Any, cfg: WebConfig) -> Response:
    return render(request, "landing.html", 200, page="landing", user=auth.current_user(request, cfg), tools=TOOLS)
