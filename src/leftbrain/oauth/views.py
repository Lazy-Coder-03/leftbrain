"""The consent screen: leftbrain's own per-client approval, before anything is granted.

The MCP security guidance is explicit that a server which uses a static client id against a
third party (leftbrain's GitHub login), allows dynamic client registration, and relies on that
third party's consent cookie is open to a confused-deputy attack. The mitigation is this page:
consent recorded per client, checked before anything is forwarded, and no signed state set
until a human has actually approved.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode, urlparse

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from ..scopes import CATALOGUE
from ..web import auth
from ..web.views import fail_page_for, no_store, parse_grid_scope, render
from .naming import connector_key_name
from .redirects import is_loopback, redirect_uri_matches

#: Everything the authorization request carries across the consent page and back again.
FIELDS = ("client_id", "redirect_uri", "explicit", "code_challenge", "scopes", "state", "resource")


def armoured(response: Response) -> Response:
    """No framing, no caching. A consent screen inside an iframe is a clickjacking target."""
    response.headers["x-frame-options"] = "DENY"
    response.headers["content-security-policy"] = "frame-ancestors 'none'"
    return no_store(response)


def back_to_client(redirect_uri: str, **params: str) -> RedirectResponse:
    joiner = "&" if urlparse(redirect_uri).query else "?"
    return RedirectResponse(f"{redirect_uri}{joiner}{urlencode(params)}", status_code=302)


def consent_routes(keys: Any, cfg: Any, provider: Any, oauth: Any) -> list[Route]:
    from ..keys import MAX_ACTIVE_KEYS_PER_EMAIL

    def fields_from(source: Any) -> dict[str, str]:
        return {name: str(source.get(name) or "") for name in FIELDS}

    async def resolve(request: Request, fields: dict[str, str]) -> tuple[Any, Response | None]:
        """The client, or the page explaining why we will not go any further."""
        client = await provider.get_client(fields["client_id"])
        if client is None:
            return None, armoured(fail_page_for(
                request, cfg, 400, "Unknown app",
                "That application is not registered with leftbrain, so nothing was granted."))
        registered = [str(u) for u in (client.redirect_uris or [])]
        if not any(redirect_uri_matches(r, fields["redirect_uri"]) for r in registered):
            return None, armoured(fail_page_for(
                request, cfg, 400, "Return address not registered",
                "This app asked leftbrain to send the result somewhere it did not register. "
                "Nothing was granted."))
        return client, None

    def page(request: Request, user: Any, client: Any, fields: dict[str, str], error: str | None = None, status: int = 200) -> Response:
        uris = [str(u) for u in (client.redirect_uris or [])]
        owned = keys.list(user.email)
        return armoured(render(
            request, "consent.html", status, page="consent", user=user,
            client_name=client.client_name or "This app",
            redirect_host=urlparse(fields["redirect_uri"]).hostname or "",
            loopback=is_loopback(fields["redirect_uri"]),
            proposed_name=connector_key_name(client.client_name, uris, request.headers.get("user-agent")),
            catalogue=CATALOGUE, scope_of=None,  # everything ticked; narrowing comes later
            slots_used=sum(1 for k in owned if k.holds_slot),
            max_keys=MAX_ACTIVE_KEYS_PER_EMAIL,
            csrf=auth.csrf_token(cfg.secret or "", user), passthrough=fields, error=error,
        ))

    async def consent_page(request: Request) -> Response:
        user = auth.current_user(request, cfg)
        fields = fields_from(request.query_params)
        if user is None:
            return armoured(RedirectResponse(f"/login?next={quote(str(request.url))}", status_code=302))
        client, problem = await resolve(request, fields)
        if problem is not None:
            return problem
        return page(request, user, client, fields)

    async def consent_submit(request: Request) -> Response:
        user = auth.current_user(request, cfg)
        if user is None:
            return armoured(RedirectResponse("/login", status_code=302))
        form = await request.form()
        if not auth.csrf_ok(cfg.secret or "", user, str(form.get("csrf") or "")):
            return armoured(fail_page_for(request, cfg, 403, "Form expired",
                                          "Please start the connection again from the app."))
        fields = fields_from(form)
        client, problem = await resolve(request, fields)
        if problem is not None:
            return problem
        if not form.get("approve"):
            return armoured(back_to_client(fields["redirect_uri"], error="access_denied",
                                           error_description="You declined the connection.",
                                           state=fields["state"]))

        existing = oauth.consent_for(user.email, client.client_id)
        info = keys.get_by_prefix(existing) if existing else None
        if info is not None and info.usable:
            prefix = info.prefix  # reconnecting: reuse the key rather than eat another slot
        else:
            try:
                scope = parse_grid_scope(form)
            except ValueError as e:
                return page(request, user, client, fields, error=f"{e}. Nothing was connected.", status=400)
            uris = [str(u) for u in (client.redirect_uris or [])]
            name = connector_key_name(client.client_name, uris, request.headers.get("user-agent"))
            raw, made = keys.create_for_owner(user.email, name, scope=scope)
            if raw is None:
                # `made` carries the cap message; the agent's user needs it verbatim, not
                # "authorization failed", because the fix is a specific thing they can do
                return armoured(back_to_client(fields["redirect_uri"], error="access_denied",
                                               error_description=str(made), state=fields["state"]))
            prefix = made.prefix
        oauth.record_consent(user.email, client.client_id, prefix)

        row = keys.db.one("SELECT key_hash FROM keys WHERE prefix=?", (prefix,))
        code = provider.issue_code(
            client_id=client.client_id, key_hash=row["key_hash"], owner=user.email,
            scopes=(fields["scopes"] or "mcp").split(), code_challenge=fields["code_challenge"],
            redirect_uri=fields["redirect_uri"], redirect_uri_provided=fields["explicit"] == "1",
            resource=fields["resource"] or None,
        )
        return armoured(back_to_client(fields["redirect_uri"], code=code, state=fields["state"]))

    return [
        Route("/oauth/consent", consent_page, methods=["GET"]),
        Route("/oauth/consent", consent_submit, methods=["POST"]),
    ]
