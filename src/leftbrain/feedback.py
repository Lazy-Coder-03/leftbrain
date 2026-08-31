"""Report a wrong answer from where it happened (#53).

The tracker is public, but that is not where a wrong answer is noticed. An agent that gets a
bad envelope is mid-call, holds a leftbrain key and nothing else, and cannot sign in to
GitHub; a person on the docs page is signed in here, not there. This is one code path with
two doors onto it — a form for a signed-in person, and `POST /feedback` for an agent holding
a key — and both file onto the same tracker anyone can also open by hand.

It is **off unless configured**. Without a token and a repository it answers `unsupported`
and names the tracker, because an endpoint that silently swallows reports is worse than one
that says it is closed, and one that says it is closed without saying where else to go is
only a little better (#102).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

__all__ = ["FeedbackConfig", "Submission", "compose", "feedback_config", "submit"]

#: What a report can be about. `kind` becomes a label, so it is a closed set.
KINDS = ("bug", "idea", "docs", "question")

#: Caps, so one report cannot become a denial-of-service on the issue tracker.
MAX_TITLE = 120
MAX_BODY = 8_000
#: Reports one key may file, ever. Feedback is rare and spam is cheap.
MAX_PER_KEY = 20

#: Things that should never be copied into a public issue, however they arrive.
_SECRETS = re.compile(
    r"(lblz_[A-Za-z0-9_-]{4,}|gh[pousr]_[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._-]{12,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
)


@dataclass(frozen=True)
class FeedbackConfig:
    token: str | None
    repo: str | None  # "owner/name"

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.repo)


def project_links(cfg: FeedbackConfig | None = None) -> dict[str, str]:
    """``repo`` and ``tracker``: the source and the issue list.

    When feedback is configured against a repository, that repository is the tracker — a
    self-hoster who files reports into their own fork should be sending people there too.
    Otherwise it is the project's own, from the package, so a server with feedback off still
    has somewhere to point (#102).
    """
    from . import __repo__

    cfg = cfg or feedback_config()
    repo = f"https://github.com/{cfg.repo}" if cfg.repo else __repo__
    return {"repo": repo, "tracker": f"{repo}/issues"}


def feedback_config() -> FeedbackConfig:
    return FeedbackConfig(
        token=os.environ.get("LEFTBRAIN_FEEDBACK_TOKEN") or None,
        repo=os.environ.get("LEFTBRAIN_FEEDBACK_REPO") or None,
    )


@dataclass(frozen=True)
class Submission:
    kind: str
    title: str
    body: str
    reporter: str  # how the report arrived, never an address or a key


def redact(text: str) -> str:
    """Blank anything key-shaped. A report is quoted verbatim into a public issue, and the
    thing a caller is most likely to paste while describing a failing call is their key."""
    return _SECRETS.sub("[redacted]", str(text))


def compose(raw: dict[str, Any], reporter: str) -> Submission:
    """Validate and shape one report, or raise `ValueError` with what is wrong."""
    kind = str(raw.get("kind") or "bug").strip().lower()
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    title = redact(str(raw.get("title") or "").strip())
    body = redact(str(raw.get("body") or raw.get("message") or "").strip())
    if not title:
        raise ValueError("'title' is required: one line saying what went wrong")
    if not body:
        raise ValueError("'body' is required: what you called, what came back, what you expected")
    if len(title) > MAX_TITLE:
        raise ValueError(f"'title' is {len(title)} characters; the limit is {MAX_TITLE}")
    if len(body) > MAX_BODY:
        raise ValueError(f"'body' is {len(body):,} characters; the limit is {MAX_BODY:,}")
    return Submission(kind=kind, title=title, body=body, reporter=reporter)


def issue_body(report: Submission, version: str) -> str:
    """The issue as it will read, with where it came from recorded rather than guessed at."""
    return (
        f"{report.body}\n\n---\n"
        f"Filed through leftbrain's feedback endpoint by **{report.reporter}** "
        f"on version `{version}`. The reporter was not on GitHub when they filed it and may "
        f"not be reachable here, so any question for them has to go back the way it came."
    )


def submit(report: Submission, cfg: FeedbackConfig, version: str, transport: Any = None) -> dict[str, Any]:
    """File the issue. Returns `{number, url}`; raises `RuntimeError` if GitHub refuses."""
    import httpx

    if not cfg.enabled:  # pragma: no cover - callers check `enabled` first
        raise RuntimeError("feedback is not configured")
    payload = {"title": report.title, "body": issue_body(report, version), "labels": [report.kind]}
    with httpx.Client(timeout=15, transport=transport) as client:
        answered = client.post(
            f"https://api.github.com/repos/{cfg.repo}/issues",
            headers={
                "Authorization": f"Bearer {cfg.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            content=json.dumps(payload),
        )
    if answered.status_code >= 300:
        raise RuntimeError(f"GitHub refused the report ({answered.status_code})")
    created = answered.json()
    return {"number": created.get("number"), "url": created.get("html_url")}
