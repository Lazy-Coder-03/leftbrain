"""Cookie sessions, CSRF tokens and the GitHub OAuth web flow."""

from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass

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
