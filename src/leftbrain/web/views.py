"""Route handlers for the web site."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import auth, templates
from .config import WebConfig


def wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def render(request: Request, name: str, status: int = 200, **ctx: Any) -> Response:
    return templates.TemplateResponse(request, name, ctx, status_code=status)


def error_page(request: Request, status: int, title: str, message: str) -> Response:
    return render(request, "error.html", status, title=title, message=message, page="error", user=None)


def routes(store: Any, cfg: WebConfig) -> list[Any]:
    from ..serve import _client_ip  # noqa: E402  (module-level import would be circular)
    from . import demo as demo_mod

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

    async def login(request: Request) -> Response:
        if not cfg.oauth_ready:
            return render(
                request,
                "login.html",
                200,
                page="login",
                user=None,
                notice="Sign-in is not configured on this server. Set GITHUB_CLIENT_ID, "
                "GITHUB_CLIENT_SECRET and LEFTBRAIN_SECRET.",
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
        return resp

    async def callback(request: Request) -> Response:
        if not cfg.oauth_ready:
            return error_page(request, 404, "Sign-in unavailable", "Sign-in is not configured on this server.")
        expected = auth.read_state(cfg.secret or "", request.cookies.get(auth.OAUTH_COOKIE))
        got = request.query_params.get("state")
        code = request.query_params.get("code")
        if not expected or not got or expected != got or not code:
            return render(
                request,
                "login.html",
                400,
                page="login",
                user=None,
                notice="That sign-in link is stale or invalid. Please sign in again.",
            )
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

    return [
        Route("/login", login),
        Route("/auth/github/callback", callback),
        Route("/logout", logout, methods=["POST"]),
        Route("/dashboard", dashboard),
        Route("/dashboard/keys", create_key, methods=["POST"]),
        Route("/dashboard/keys/{prefix}/revoke", revoke_key, methods=["POST"]),
        Route("/demo/{tool}", demo, methods=["POST"]),
    ]


TOOLS = [
    ("math", "Exact arithmetic & symbolic algebra (SymPy)", "eval · exact · simplify · expand · factor · solve …"),
    ("datetime", "Dates, durations, time zones, business days", "now · convert_tz · parse · add · diff · weekday …"),
    ("scale", "Scale numbers and recipes proportionally", "linear · inverse"),
    ("convert", "Units and currencies with pint", "units · temperature · currency · auto"),
    ("holidays", "Public holidays by country/region", "list · check · next · countries · subdivisions"),
    ("numbers", "Compare, round, format, allocate exactly", "compare · round · format · allocate · sequence · parse …"),
    ("text", "Count, slice, case, diff — by codepoint", "count · regex_match · regex_replace · diff · sort · dedupe …"),
    ("collections", "Sort, dedupe, group, set ops", "set_ops · group_by · pick_fields · flatten · unflatten · paginate …"),
    ("validate", "Assert rules over JSON, emails, IBANs, schemas", "json_schema · assert · id · email · url · phone …"),
    ("random", "Seeded, reproducible randomness", "uuid · int · float · pick · shuffle · token …"),
    ("geo_offline", "Distance, bearing, bounding boxes", "tz_for_place · tz_for_coords · distance · country · zone_info"),
    ("encode", "Hashes, base64, URL, hex", "hash · hmac · checksum · base64 · hex · url · html …"),
]


async def landing(request: Request, store: Any, cfg: WebConfig) -> Response:
    return render(request, "landing.html", 200, page="landing", user=auth.current_user(request, cfg), tools=TOOLS)
