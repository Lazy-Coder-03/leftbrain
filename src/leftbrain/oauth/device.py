"""RFC 8628, the device authorization grant.

An agent on a machine with no browser cannot receive a loopback redirect. Instead it asks for
a code, tells its human where to type it, and polls. Nothing secret crosses the conversation:
the user code grants nothing until a signed-in human approves it here, which is the same
property the browser flow has and the reason this is better than pasting a key into a chat.
"""

from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from ..scopes import CATALOGUE
from ..web import auth
from ..web.views import armoured, fail_page_for, parse_grid_scope, render
from .naming import connector_key_name

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
DEVICE_PATH = "/oauth/device_authorization"
DEVICE_TTL = 600  # ten minutes: long enough to walk to another device
POLL_INTERVAL = 5
#: No vowels (so no code spells anything) and none of 0/1/I/O, which are misread aloud
#: and mistyped from a screen. 28 symbols over 8 places is ~38 bits.
_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ23456789"


def new_user_code() -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"{body[:4]}-{body[4:]}"


def device_routes(keys: Any, cfg: Any, provider: Any, oauth: Any) -> list[Route]:
    from ..keys import MAX_ACTIVE_KEYS_PER_EMAIL

    async def start(request: Request) -> Response:
        """The agent's first call: a code it can show a human, and one it keeps."""
        form = await request.form()
        client_id = str(form.get("client_id") or "")
        if not client_id or await provider.get_client(client_id) is None:
            # RFC 6749 §5.2 allows a description, and an agent that followed the 401 straight
            # here has not been told about /register yet - a bare code is a dead end (#104)
            return JSONResponse({
                "error": "invalid_client",
                "error_description": f"unknown client_id: register first with POST {cfg.base_url}/register, then retry; see {cfg.base_url}/docs/agents/auth",
            }, status_code=400)
        device_code, user_code = secrets.token_urlsafe(32), new_user_code()
        oauth.save_device(device_code, user_code=user_code, client_id=client_id,
                          scopes=str(form.get("scope") or "mcp").split(), ttl=DEVICE_TTL)
        return JSONResponse({
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": f"{cfg.base_url}/device",
            "verification_uri_complete": f"{cfg.base_url}/device?code={user_code}",
            "expires_in": DEVICE_TTL,
            "interval": POLL_INTERVAL,
        })

    def page(request: Request, user: Any, code: str = "", error: str | None = None,
             status: int = 200, approved: bool = False) -> Response:
        return armoured(render(
            request, "device.html", status, page="device", user=user, user_code=code,
            approved=approved, error=error, catalogue=CATALOGUE, scope_of=None,
            max_keys=MAX_ACTIVE_KEYS_PER_EMAIL,
            csrf=auth.csrf_token(cfg.secret or "", user),
        ))

    async def verification_page(request: Request) -> Response:
        user = auth.current_user(request, cfg)
        if user is None:
            # keep the code across the sign-in detour, or they have to fetch it again
            here = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return armoured(RedirectResponse(f"/login?next={quote(here)}", status_code=302))
        return page(request, user, code=request.query_params.get("code", ""))

    async def settle(request: Request) -> Response:
        user = auth.current_user(request, cfg)
        if user is None:
            return armoured(RedirectResponse("/login", status_code=302))
        form = await request.form()
        if not auth.csrf_ok(cfg.secret or "", user, str(form.get("csrf") or "")):
            return armoured(fail_page_for(request, cfg, 403, "Form expired",
                                          "Please enter the code again."))
        code = str(form.get("user_code") or "").strip().upper()
        record = oauth.device_by_user_code(code)
        if record is None:
            return page(request, user, code=code, status=400,
                        error="That code is not one leftbrain issued, or it has expired. "
                              "Ask the app for a new one.")
        if not form.get("approve"):
            oauth.settle_device(code, status="denied")
            return page(request, user, error="Declined. Nothing was connected.")

        client = await provider.get_client(record["client_id"])
        uris = [str(u) for u in (client.redirect_uris or [])] if client else []
        existing = oauth.consent_for(user.email, record["client_id"])
        info = keys.get_by_prefix(existing) if existing else None
        if info is not None and info.usable:
            prefix = info.prefix  # reconnecting: reuse rather than eat another slot
        else:
            try:
                scope = parse_grid_scope(form)
            except ValueError as e:
                return page(request, user, code=code, status=400,
                            error=f"{e}. Nothing was connected.")
            name = connector_key_name(client.client_name if client else None, uris,
                                      request.headers.get("user-agent"), grant="device")
            raw, made = keys.create_for_owner(user.email, name, scope=scope)
            if raw is None:
                return page(request, user, code=code, status=409, error=str(made))
            prefix = made.prefix
        oauth.record_consent(user.email, record["client_id"], prefix)
        row = keys.db.one("SELECT key_hash FROM keys WHERE prefix=?", (prefix,))
        oauth.settle_device(code, status="approved", owner=user.email, key_hash=row["key_hash"])
        return page(request, user, code=code, approved=True)

    return [
        Route(DEVICE_PATH, start, methods=["POST"]),
        Route("/device", verification_page, methods=["GET"]),
        Route("/device", settle, methods=["POST"]),
    ]


def device_token_dispatch(sdk_handler: Any, provider: Any, oauth: Any) -> Any:
    """Answer the device grant; hand every other grant to the SDK's token handler.

    Starlette caches the parsed form on the request, so reading it here to decide costs the
    SDK's handler nothing when the request turns out to be one of its own grants.
    """

    async def handle(request: Request) -> Response:
        form = await request.form()
        if str(form.get("grant_type") or "") != DEVICE_GRANT:
            return await sdk_handler.handle(request)
        client_id = str(form.get("client_id") or "")
        record = oauth.take_device(str(form.get("device_code") or ""), client_id)
        if record is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if record["status"] == "expired":
            return JSONResponse({"error": "expired_token"}, status_code=400)
        if record["status"] == "denied":
            return JSONResponse({"error": "access_denied"}, status_code=400)
        if record["status"] == "pending":
            return JSONResponse({"error": "authorization_pending"}, status_code=400)
        pair = provider._issue_pair(client_id, record["key_hash"], record["scopes"], None)
        return JSONResponse(pair.model_dump(exclude_none=True))

    return handle
