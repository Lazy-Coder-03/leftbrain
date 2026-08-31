"""#53: report a wrong answer from where it happened.

An agent that gets a bad envelope is mid-call with a key and no GitHub login; a person on the
docs is signed in here, not there. Both file onto the same public tracker anyone can also open
by hand, and when filing is off the server says where that is (#102).
"""

import json

import pytest
from starlette.testclient import TestClient

from leftbrain.feedback import (
    KINDS,
    MAX_BODY,
    MAX_TITLE,
    FeedbackConfig,
    compose,
    issue_body,
    redact,
)
from leftbrain.keys import KeyStore
from leftbrain.serve import build_app
from leftbrain.web.config import WebConfig

# --- what goes into the issue -----------------------------------------------------------


def test_a_report_needs_something_to_act_on():
    for missing in ({"body": "x"}, {"title": "x"}, {}):
        with pytest.raises(ValueError, match="required"):
            compose(missing, "key lblz_x")


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_is_accepted(kind):
    assert compose({"kind": kind, "title": "t", "body": "b"}, "r").kind == kind


def test_an_unknown_kind_is_refused_with_the_ones_that_work():
    with pytest.raises(ValueError, match="bug, idea, docs, question"):
        compose({"kind": "rant", "title": "t", "body": "b"}, "r")


def test_oversized_reports_are_refused_rather_than_truncated():
    with pytest.raises(ValueError, match="limit"):
        compose({"title": "t" * (MAX_TITLE + 1), "body": "b"}, "r")
    with pytest.raises(ValueError, match="limit"):
        compose({"title": "t", "body": "b" * (MAX_BODY + 1)}, "r")


@pytest.mark.parametrize("secret", [
    "lblz_abcdef123456",
    "ghp_0123456789abcdefghij",
    "Bearer eyJhbGciOiJIUzI1",
    "-----BEGIN RSA PRIVATE KEY-----",
])
def test_anything_key_shaped_is_blanked(secret):
    """A report is quoted verbatim into a public issue, and the thing a caller is most likely
    to paste while describing a failing call is the key they called with."""
    assert secret not in redact(f"my call was {secret} and it failed")
    assert "[redacted]" in redact(f"my call was {secret} and it failed")


def test_redaction_survives_the_whole_compose_path():
    report = compose({"title": "key lblz_abcdef123456 broke", "body": "used ghp_0123456789abcdefghij"}, "r")
    assert "lblz_abcdef123456" not in report.title and "ghp_" not in report.body


def test_the_issue_says_where_it_came_from_and_that_the_reporter_cannot_be_replied_to():
    body = issue_body(compose({"title": "t", "body": "the tool said 4"}, "key lblz_abc"), "0.4.0")
    assert "the tool said 4" in body
    assert "key lblz_abc" in body and "0.4.0" in body
    # the tracker is public, so the reason a reply cannot go there is where they were, not what they may see
    assert "not on GitHub" in body and "back the way it came" in body


def test_it_is_off_unless_both_halves_are_configured():
    assert not FeedbackConfig(token=None, repo=None).enabled
    assert not FeedbackConfig(token="t", repo=None).enabled
    assert not FeedbackConfig(token=None, repo="a/b").enabled
    assert FeedbackConfig(token="t", repo="a/b").enabled


# --- over HTTP ---------------------------------------------------------------------------


@pytest.fixture
def keyed(tmp_path):
    db = str(tmp_path / "k.sqlite3")
    app = build_app(include_external=False, keys_db=db, web_config=WebConfig(None, None, "s" * 20, None, True))
    raw, info = KeyStore(db).create("a@b.co", daily_quota=100, rpm=100)
    return app, raw, info.prefix, KeyStore(db)


REPORT = {"kind": "bug", "title": "sin(pi) in degrees is wrong", "body": "It returned 0."}


def post(client, key, body=None):
    return client.post("/feedback", json=body or REPORT, headers={"Authorization": f"Bearer {key}"})


def test_it_answers_unsupported_when_nobody_configured_it(keyed, monkeypatch):
    """An endpoint that silently swallows reports is worse than one that says it is closed."""
    monkeypatch.delenv("LEFTBRAIN_FEEDBACK_TOKEN", raising=False)
    monkeypatch.delenv("LEFTBRAIN_FEEDBACK_REPO", raising=False)
    app, key, _prefix, _store = keyed
    with TestClient(app) as c:
        r = post(c, key)
        assert r.status_code == 404 and r.json()["error"] == "unsupported"
        # closed is not the same as nowhere: the public tracker is named (#102)
        from leftbrain import __repo__

        assert r.json()["tracker"] == f"{__repo__}/issues"
        assert f"{__repo__}/issues" in r.json()["message"]


def test_the_tracker_is_the_feedback_repository_when_one_is_configured(monkeypatch):
    """A self-hoster filing reports into their fork should be sending people there too."""
    from leftbrain import __repo__
    from leftbrain.feedback import project_links

    monkeypatch.delenv("LEFTBRAIN_FEEDBACK_REPO", raising=False)
    assert project_links() == {"repo": __repo__, "tracker": f"{__repo__}/issues"}
    monkeypatch.setenv("LEFTBRAIN_FEEDBACK_REPO", "someone/leftbrain-fork")
    assert project_links()["tracker"] == "https://github.com/someone/leftbrain-fork/issues"


def test_it_needs_a_key(keyed):
    app, _key, _prefix, _store = keyed
    with TestClient(app) as c:
        assert c.post("/feedback", json=REPORT).status_code == 401


def configure(monkeypatch):
    monkeypatch.setenv("LEFTBRAIN_FEEDBACK_TOKEN", "t0ken")
    monkeypatch.setenv("LEFTBRAIN_FEEDBACK_REPO", "owner/repo")


def stub_github(monkeypatch, status=201, seen=None):
    import leftbrain.feedback as fb

    def fake(report, cfg, version, transport=None):
        if seen is not None:
            seen.append((report, cfg))
        if status >= 300:
            raise RuntimeError(f"GitHub refused the report ({status})")
        return {"number": 123, "url": "https://github.com/owner/repo/issues/123"}

    monkeypatch.setattr(fb, "submit", fake)
    return fake


def test_a_report_becomes_an_issue(keyed, monkeypatch):
    configure(monkeypatch)
    seen = []
    stub_github(monkeypatch, seen=seen)
    app, key, prefix, _store = keyed
    with TestClient(app) as c:
        r = post(c, key)
        assert r.status_code == 201, r.text
        assert r.json()["result"]["number"] == 123
        assert r.json()["result"]["url"].endswith("/123")
    assert seen[0][0].title == REPORT["title"]
    assert prefix in seen[0][0].reporter


def test_a_bad_report_is_refused_before_github_is_touched(keyed, monkeypatch):
    configure(monkeypatch)
    seen = []
    stub_github(monkeypatch, seen=seen)
    app, key, _prefix, _store = keyed
    with TestClient(app) as c:
        r = c.post("/feedback", json={"title": "no body"}, headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 400 and "required" in r.json()["message"]
    assert seen == []


def test_github_refusing_is_reported_as_retryable_and_not_as_success(keyed, monkeypatch):
    configure(monkeypatch)
    stub_github(monkeypatch, status=503)
    app, key, _prefix, _store = keyed
    with TestClient(app) as c:
        r = post(c, key)
        assert r.status_code == 502 and r.json()["retryable"] is True
        assert "t0ken" not in r.text and "owner/repo" not in r.text


def test_one_key_cannot_flood_the_tracker(keyed, monkeypatch):
    configure(monkeypatch)
    stub_github(monkeypatch)
    from leftbrain.feedback import MAX_PER_KEY

    app, key, prefix, store = keyed
    for _ in range(MAX_PER_KEY):
        store.record_tool_call(prefix, "__feedback__")
    with TestClient(app) as c:
        r = post(c, key)
        assert r.status_code == 429 and str(MAX_PER_KEY) in r.json()["message"]


def test_filing_feedback_costs_no_daily_quota(keyed, monkeypatch):
    """It is not a tool call, and #62's rule is that the quota counts work done."""
    configure(monkeypatch)
    stub_github(monkeypatch)
    app, key, prefix, store = keyed
    with TestClient(app) as c:
        assert post(c, key).status_code == 201
    assert store.get_by_prefix(prefix).used_today == 0


def test_the_reporter_is_recorded_without_the_key_itself(keyed, monkeypatch):
    configure(monkeypatch)
    seen = []
    stub_github(monkeypatch, seen=seen)
    app, key, prefix, _store = keyed
    with TestClient(app) as c:
        post(c, key)
    assert key not in json.dumps(seen[0][0].__dict__)
    assert prefix in seen[0][0].reporter  # the prefix is not the secret


# --- the human door onto the same path ---------------------------------------------------


def test_the_form_needs_a_signed_in_person(tmp_path):
    from test_web import oauth_app

    with TestClient(oauth_app(tmp_path)) as c:
        r = c.get("/report", follow_redirects=False)
        assert r.status_code == 303 and "/login" in r.headers["location"]


def test_the_form_renders_for_a_signed_in_person(tmp_path, monkeypatch):
    configure(monkeypatch)
    from test_web import login_via_github, oauth_app

    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        r = c.get("/report")
        assert r.status_code == 200
        assert "Report a problem" in r.text and 'name="csrf"' in r.text


def test_the_form_says_so_when_reporting_is_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("LEFTBRAIN_FEEDBACK_TOKEN", raising=False)
    monkeypatch.delenv("LEFTBRAIN_FEEDBACK_REPO", raising=False)
    from test_web import login_via_github, oauth_app

    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        page = c.get("/report").text
        assert "not configured" in page
        # and the page still gives the person a door (#102)
        from leftbrain import __repo__

        assert f'href="{__repo__}/issues"' in page


def test_a_form_post_files_the_issue(tmp_path, monkeypatch):
    configure(monkeypatch)
    seen = []
    stub_github(monkeypatch, seen=seen)
    from test_web import csrf_from, login_via_github, oauth_app

    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        token = csrf_from(c.get("/report").text)
        r = c.post("/report", data={"csrf": token, **REPORT})
        assert r.status_code == 200 and "#123" in r.text
    assert seen[0][0].title == REPORT["title"]
    assert "github user" in seen[0][0].reporter


def test_the_reporters_email_is_never_attached(tmp_path, monkeypatch):
    configure(monkeypatch)
    seen = []
    stub_github(monkeypatch, seen=seen)
    from test_web import csrf_from, login_via_github, oauth_app

    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        c.post("/report", data={"csrf": csrf_from(c.get("/report").text), **REPORT})
    assert "@" not in seen[0][0].reporter


def test_a_form_without_a_csrf_token_is_refused(tmp_path, monkeypatch):
    configure(monkeypatch)
    seen = []
    stub_github(monkeypatch, seen=seen)
    from test_web import login_via_github, oauth_app

    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        r = c.post("/report", data={"csrf": "forged", **REPORT})
        assert r.status_code == 400
    assert seen == []


def test_the_site_says_so_when_the_tracker_cannot_be_reached(tmp_path, monkeypatch):
    """The failure path from the form was not exercised, which is how an undefined logger
    survived the tests and was caught by the linter instead."""
    configure(monkeypatch)
    stub_github(monkeypatch, status=503)
    from test_web import csrf_from, login_via_github, oauth_app

    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        token = csrf_from(c.get("/report").text)
        r = c.post("/report", data={"csrf": token, **REPORT})
        assert r.status_code == 502 and "Nothing was lost" in r.text
        assert "t0ken" not in r.text
