"""Web-layer configuration (GitHub OAuth, cookie secret, base URL)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class WebConfig:
    client_id: str | None
    client_secret: str | None
    secret: str | None
    base_url: str | None
    open_signup: bool
    github_transport: Any | None = None  # httpx transport override for tests

    @classmethod
    def from_env(cls) -> WebConfig:
        env = os.environ.get
        return cls(
            client_id=env("GITHUB_CLIENT_ID") or None,
            client_secret=env("GITHUB_CLIENT_SECRET") or None,
            secret=env("LEFTBRAIN_SECRET") or None,
            base_url=(env("LEFTBRAIN_BASE_URL") or "").rstrip("/") or None,
            open_signup=env("LEFTBRAIN_OPEN_SIGNUP", "0") in ("1", "true", "yes"),
        )

    @property
    def oauth_ready(self) -> bool:
        return bool(self.client_id and self.client_secret and self.secret)
