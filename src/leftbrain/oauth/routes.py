"""Mounting the SDK's OAuth routes, with the metadata amendments real clients need."""

from __future__ import annotations

from typing import Any

from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.routes import (
    build_metadata,
    cors_middleware,
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthMetadata
from pydantic import AnyHttpUrl
from starlette.routing import Route

from .provider import LeftbrainOAuthProvider
from .store import OAuthStore
from .views import consent_routes

#: One coarse OAuth scope. leftbrain's real per-tool scoping lives on the key and is chosen
#: on the consent screen, so a second vocabulary here could only disagree with it.
MCP_SCOPE = "mcp"
OFFLINE = "offline_access"

METADATA_PATH = "/.well-known/oauth-authorization-server"


def oauth_metadata(
    issuer: AnyHttpUrl, registration: ClientRegistrationOptions, revocation: RevocationOptions
) -> OAuthMetadata:
    """The SDK's metadata, amended where its defaults turn real clients away.

    `build_metadata` omits CIMD support and omits `none` from the token-endpoint auth methods.
    Anthropic's connector docs are explicit that Claude selects CIMD only when both are
    present, and falls back to registering a fresh client on every connection otherwise.
    `offline_access` has to be advertised too, or Claude never asks for a refresh token.
    """
    meta = build_metadata(issuer, None, registration, revocation)
    meta.client_id_metadata_document_supported = True
    methods = list(meta.token_endpoint_auth_methods_supported or [])
    if "none" not in methods:
        methods.insert(0, "none")
    meta.token_endpoint_auth_methods_supported = methods
    meta.scopes_supported = [MCP_SCOPE, OFFLINE]
    return meta


def build_oauth_routes(keys: Any, cfg: Any) -> list[Route]:
    """Every OAuth route, or nothing at all when the server is not configured for it."""
    if keys is None or not cfg.oauth_enabled:
        return []

    registration = ClientRegistrationOptions(
        enabled=True, valid_scopes=[MCP_SCOPE, OFFLINE], default_scopes=[MCP_SCOPE]
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
    # one store instance, shared by the provider and the consent views, so they cannot
    # end up looking at the tables through two different connections
    oauth = OAuthStore(keys)
    provider = LeftbrainOAuthProvider(oauth, keys)

    amended = oauth_metadata(issuer, registration, revocation)
    routes: list[Route] = []
    for route in create_auth_routes(provider, issuer, None, registration, revocation):
        if route.path == METADATA_PATH:
            # the SDK builds its own metadata inside create_auth_routes; swap in the amended
            # document rather than serving one that no Claude client will read as CIMD-capable
            route = Route(
                METADATA_PATH,
                endpoint=cors_middleware(MetadataHandler(amended).handle, ["GET", "OPTIONS"]),
                methods=["GET", "OPTIONS"],
            )
        routes.append(route)

    routes += create_protected_resource_routes(
        resource,
        [issuer],
        [MCP_SCOPE],
        resource_name="leftbrain",
        # RFC 9728's own field for "where an agent reads how to authenticate here"
        resource_documentation=AnyHttpUrl(f"{cfg.base_url}/docs/agents/auth"),
    )
    routes += consent_routes(keys, cfg, provider, oauth)
    return routes
