"""Mounting the SDK's OAuth routes, with the metadata amendments real clients need."""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.routes import (
    TOKEN_PATH,
    build_metadata,
    cors_middleware,
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE, RequestBodyLimitMiddleware
from mcp.shared.auth import OAuthMetadata
from mcp.shared.inbound import MCP_PROTOCOL_VERSION_HEADER
from pydantic import AnyHttpUrl
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route, request_response

from .device import DEVICE_GRANT, DEVICE_PATH, device_routes, device_token_dispatch
from .provider import LeftbrainOAuthProvider
from .store import OAuthStore
from .views import consent_routes

#: One coarse OAuth scope. leftbrain's real per-tool scoping lives on the key and is chosen
#: on the consent screen, so a second vocabulary here could only disagree with it.
MCP_SCOPE = "mcp"
OFFLINE = "offline_access"
#: One list, used for all three of: what the metadata advertises, what a client may ask for
#: at registration, and what a client that asks for nothing is given. They have to agree —
#: Claude reads `scopes_supported` and requests all of it, and `/authorize` then refuses
#: anything the client does not hold, so advertising a scope that is not in the default is
#: advertising one we will reject. That is what `oauth_error=invalid_scope` was.
SCOPES = [MCP_SCOPE, OFFLINE]

#: RFC 8414's well-known path. The SDK names /authorize, /token, /register and /revoke as
#: constants but hardcodes this one inside `create_auth_routes`, so we name it to match on.
METADATA_PATH = "/.well-known/oauth-authorization-server"


class LeftbrainMetadata(OAuthMetadata):
    """RFC 8414 metadata plus the RFC 8628 endpoint the SDK's model does not declare.

    A client checks for both this field and the grant type before offering the device flow,
    so an unadvertised endpoint is an endpoint nobody calls.
    """

    device_authorization_endpoint: AnyHttpUrl | None = None


def oauth_metadata(
    issuer: AnyHttpUrl, registration: ClientRegistrationOptions, revocation: RevocationOptions
) -> LeftbrainMetadata:
    """The SDK's metadata, amended where its defaults turn real clients away.

    `build_metadata` omits CIMD support and omits `none` from the token-endpoint auth methods.
    Anthropic's connector docs are explicit that Claude selects CIMD only when both are
    present, and falls back to registering a fresh client on every connection otherwise.
    `offline_access` has to be advertised too, or Claude never asks for a refresh token.
    """
    base = build_metadata(issuer, None, registration, revocation)
    methods = list(base.token_endpoint_auth_methods_supported or [])
    if "none" not in methods:
        methods.insert(0, "none")
    grants = list(base.grant_types_supported or [])
    if DEVICE_GRANT not in grants:
        grants.append(DEVICE_GRANT)
    return LeftbrainMetadata.model_validate({
        **base.model_dump(exclude_none=True, mode="json"),
        "client_id_metadata_document_supported": True,
        "token_endpoint_auth_methods_supported": methods,
        "scopes_supported": SCOPES,
        "grant_types_supported": grants,
        "device_authorization_endpoint": f"{str(issuer).rstrip('/')}{DEVICE_PATH}",
    })


def _token_route(provider: Any, oauth: Any) -> Route:
    """`/token`, answering the device grant itself and delegating the SDK's own grants.

    Rebuilt rather than wrapped because `create_auth_routes` hands back an assembled ASGI
    app; the CORS and body-size layers here are the same ones it applies.
    """
    dispatch = device_token_dispatch(TokenHandler(provider, ClientAuthenticator(provider)), provider, oauth)
    endpoint = CORSMiddleware(
        app=RequestBodyLimitMiddleware(request_response(dispatch), DEFAULT_MAX_REQUEST_BODY_SIZE),
        allow_origins="*",
        allow_methods=["POST", "OPTIONS"],
        allow_headers=[MCP_PROTOCOL_VERSION_HEADER],
    )
    return Route(TOKEN_PATH, endpoint=endpoint, methods=["POST", "OPTIONS"])


def build_oauth_routes(keys: Any, cfg: Any, mounted: tuple[str, ...] = ("/mcp",)) -> list[Route]:
    """Every OAuth route, or nothing at all when the server is not configured for it.

    ``mounted`` names every MCP endpoint the server serves. Each gets its own RFC 9728
    document at ``/.well-known/oauth-protected-resource<endpoint>`` declaring itself as the
    ``resource``, because a client checks that field against the URL it is connecting to and
    refuses a mismatch (#101).
    """
    if keys is None or not cfg.oauth_enabled:
        return []

    registration = ClientRegistrationOptions(
        enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES
    )
    revocation = RevocationOptions(enabled=True)
    # Built through AuthSettings rather than AnyHttpUrl directly: that model carries
    # `url_preserve_empty_path`, so a path-less issuer keeps its canonical form. Bare
    # AnyHttpUrl appends a slash, and RFC 8414 compares the issuer by exact string, so
    # `https://host/` would silently fail to match the `https://host` a client expects.
    settings = AuthSettings(
        issuer_url=cfg.base_url,
        resource_server_url=f"{cfg.base_url}/mcp",
        client_registration_options=registration,
        revocation_options=revocation,
        required_scopes=[MCP_SCOPE],
    )
    issuer, resource = settings.issuer_url, settings.resource_server_url

    # one store instance, shared by the provider and the views, so they cannot end up
    # looking at the same tables through two different connections
    oauth = OAuthStore(keys)
    # Local development only: lets a client metadata document be fetched over http, or from
    # a loopback or private address. Announced at startup because it removes the SSRF fence.
    allow_insecure = os.environ.get("LEFTBRAIN_CIMD_ALLOW_INSECURE", "0") in ("1", "true", "yes")
    if allow_insecure:
        print(json.dumps({"warning": "LEFTBRAIN_CIMD_ALLOW_INSECURE is on; client metadata may be fetched from private addresses"}), flush=True)
    provider = LeftbrainOAuthProvider(
        oauth, keys, allow_insecure_cimd=allow_insecure, default_scopes=SCOPES
    )

    amended = oauth_metadata(issuer, registration, revocation)
    routes: list[Route] = []
    for route in create_auth_routes(provider, issuer, None, registration, revocation):
        if route.path == METADATA_PATH:
            route = Route(
                METADATA_PATH,
                endpoint=cors_middleware(MetadataHandler(amended).handle, ["GET", "OPTIONS"]),
                methods=["GET", "OPTIONS"],
            )
        elif route.path == TOKEN_PATH:
            route = _token_route(provider, oauth)
        routes.append(route)

    for endpoint in mounted:
        routes += create_protected_resource_routes(
            resource if endpoint == "/mcp" else AnyHttpUrl(f"{cfg.base_url}{endpoint}"),
            [issuer],
            [MCP_SCOPE],
            resource_name="leftbrain",
            # RFC 9728's own field for "where an agent reads how to authenticate here"
            resource_documentation=AnyHttpUrl(f"{cfg.base_url}/docs/agents/auth"),
        )
    routes += consent_routes(keys, cfg, provider, oauth)
    routes += device_routes(keys, cfg, provider, oauth)
    return routes
