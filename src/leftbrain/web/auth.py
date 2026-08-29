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


def safe_next(path: Any) -> str | None:
    """A page on this server to return to after signing in, or None.

    Only a bare path is accepted. `//evil.example` is protocol-relative and a browser
    resolves it off-site; a full URL could point anywhere; a backslash is treated as a
    separator by some browsers; and a newline could split the `Location` header. All are
    refused outright rather than sanitised, because a rewritten redirect target is far
    harder to reason about than a rejected one.
    """
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return None
    if any(c in path for c in ("\\", "\n", "\r")):
        return None
    return path


def sign_state(secret: str, state: str, next_path: str | None = None) -> str:
    """Sign the CSRF state, and where to go once the identity provider has answered.

    `next_path` rides in the signed cookie rather than through the provider, so it cannot
    be swapped between leaving and coming back.
    """
    return URLSafeTimedSerializer(secret, salt="lb-oauth").dumps(
        {"s": state, "n": safe_next(next_path)}
    )


def read_state(secret: str, value: str | None) -> tuple[str | None, str | None]:
    """The signed state and its return path, or ``(None, None)``."""
    if not value:
        return None, None
    try:
        data = URLSafeTimedSerializer(secret, salt="lb-oauth").loads(value, max_age=OAUTH_MAX_AGE)
    except BadSignature:
        return None, None
    if isinstance(data, str):  # a cookie signed before there was anywhere to come back to
        return data, None
    if not isinstance(data, dict):
        return None, None
    state = data.get("s")
    # re-checked on the way out: the cookie is ours, but the path inside it was a query
    # parameter once, and the rule is cheap enough to apply at both ends
    return (str(state) if state else None), safe_next(data.get("n"))


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

    q = urlencode(
        {
            "client_id": cfg.client_id or "",
            "redirect_uri": redirect_uri,
            "scope": "read:user user:email",
            "state": state,
        }
    )
    return f"{GITHUB_AUTHORIZE}?{q}"


async def fetch_github_user(cfg: WebConfig, code: str, redirect_uri: str) -> User:
    import httpx

    async with httpx.AsyncClient(timeout=15, transport=cfg.github_transport) as client:
        try:
            tok = await client.post(
                GITHUB_TOKEN,
                data={
                    "client_id": cfg.client_id,
                    "client_secret": cfg.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            tok.raise_for_status()
            access = tok.json().get("access_token")
            if not access:
                raise OAuthError("GitHub did not accept the sign-in code. Please try again.")
            h = {"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json"}
            user = await client.get(f"{GITHUB_API}/user", headers=h)
            user.raise_for_status()
            emails = await client.get(f"{GITHUB_API}/user/emails", headers=h)
            emails.raise_for_status()
            u = user.json()
            emails_data = emails.json()
            if not isinstance(u, dict) or not isinstance(emails_data, list):
                raise OAuthError("GitHub returned an unexpected response. Please try again.")
            primary = next(
                (
                    e
                    for e in emails_data
                    if isinstance(e, dict)
                    and e.get("primary")
                    and e.get("verified")
                    and e.get("email")
                ),
                None,
            )
            if not primary:
                raise OAuthError(
                    "verify your GitHub email address, then sign in again", status=403
                )
            return User(
                login=str(u.get("login") or ""),
                email=str(primary["email"]).lower(),
                avatar_url=u.get("avatar_url"),
            )
        except OAuthError:
            raise
        except httpx.HTTPError:
            raise OAuthError("GitHub could not be reached. Please try again in a minute.") from None
        except (ValueError, AttributeError, TypeError):
            raise OAuthError("GitHub returned an unexpected response. Please try again.") from None
