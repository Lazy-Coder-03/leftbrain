import httpx
from starlette.testclient import TestClient

from leftbrain.serve import build_app
from leftbrain.web import auth
from leftbrain.web.config import WebConfig


def make_app(tmp_path, **cfg):
    defaults = {"client_id": None, "client_secret": None, "secret": "test-secret-0123456789", "base_url": None, "open_signup": False}
    config = WebConfig(**{**defaults, **cfg})
    return build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"), web_config=config)


def test_root_negotiates_html_and_json(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        html = c.get("/", headers={"Accept": "text/html,application/xhtml+xml"})
        assert html.status_code == 200 and html.headers["content-type"].startswith("text/html")
        assert "leftbrain" in html.text
        js = c.get("/", headers={"Accept": "*/*"})
        assert js.headers["content-type"].startswith("application/json") and js.json()["auth"] == "keys"


def test_static_and_healthz(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        assert c.get("/static/site.css").status_code == 200
        assert c.get("/healthz").json()["ok"]


def test_mcp_still_needs_bearer(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        assert c.post("/mcp", json={}).status_code == 401
        assert c.get("/keys/me").status_code == 401


def test_signup_closed_by_default_open_by_flag(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = c.post("/keys/signup", json={"email": "a@b.co"})
        assert r.status_code == 404 and "/login" in r.json()["message"]
    with TestClient(make_app(tmp_path, open_signup=True)) as c:
        assert c.post("/keys/signup", json={"email": "a@b.co"}).status_code == 201


def test_session_roundtrip_and_tamper():
    u = auth.User(login="octo", email="octo@example.com", avatar_url=None)
    tok = auth.sign_session("s3cret", u)
    assert auth.read_session("s3cret", tok) == u
    assert auth.read_session("other", tok) is None
    assert auth.read_session("s3cret", tok + "x") is None
    assert auth.read_session("s3cret", None) is None
    assert auth.read_session("s3cret", tok, max_age=-1) is None  # expired


def test_csrf():
    u = auth.User("octo", "octo@example.com", None)
    t = auth.csrf_token("s3cret", u)
    assert auth.csrf_ok("s3cret", u, t)
    assert not auth.csrf_ok("s3cret", auth.User("x", "x@example.com", None), t)
    assert not auth.csrf_ok("s3cret", u, None) and not auth.csrf_ok("s3cret", u, "nope")


def github_transport(emails=None, token="gho_test"):
    """Fake GitHub: token exchange, /user, /user/emails."""
    emails = (
        emails
        if emails is not None
        else [{"email": "octo@example.com", "primary": True, "verified": True}]
    )

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "github.com" and req.url.path == "/login/oauth/access_token":
            body = dict(x.split("=") for x in req.content.decode().split("&")) if req.content else {}
            assert req.headers["accept"] == "application/json"
            if body.get("code") == "bad":
                return httpx.Response(200, json={"error": "bad_verification_code"})
            return httpx.Response(200, json={"access_token": token, "token_type": "bearer"})
        if req.url.path == "/user":
            assert req.headers["authorization"] == f"Bearer {token}"
            return httpx.Response(200, json={"login": "octo", "avatar_url": "https://a/octo.png"})
        if req.url.path == "/user/emails":
            return httpx.Response(200, json=emails)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def oauth_app(tmp_path, **kw):
    return make_app(tmp_path, client_id="cid", client_secret="csec", github_transport=github_transport(**kw))


def github_transport_malformed_emails(token="gho_test"):
    """Fake GitHub whose /user/emails endpoint returns a non-JSON body."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "github.com" and req.url.path == "/login/oauth/access_token":
            assert req.headers["accept"] == "application/json"
            return httpx.Response(200, json={"access_token": token, "token_type": "bearer"})
        if req.url.path == "/user":
            assert req.headers["authorization"] == f"Bearer {token}"
            return httpx.Response(200, json={"login": "octo", "avatar_url": "https://a/octo.png"})
        if req.url.path == "/user/emails":
            return httpx.Response(
                200, content=b"not json", headers={"content-type": "application/json"}
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_login_not_configured(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = c.get("/login")
        assert r.status_code == 200 and "not configured" in r.text


def test_login_redirects_to_github_with_state(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        r = c.get("/login", follow_redirects=False)
        assert r.status_code == 302
        loc = r.headers["location"]
        assert loc.startswith("https://github.com/login/oauth/authorize?") and "client_id=cid" in loc and "state=" in loc
        assert "redirect_uri=http%3A%2F%2Ftestserver%2Fauth%2Fgithub%2Fcallback" in loc
        assert "lb_oauth" in r.cookies


def test_callback_bad_state_rejected(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        c.get("/login", follow_redirects=False)
        r = c.get("/auth/github/callback?code=ok&state=wrong", follow_redirects=False)
        assert r.status_code == 400 and "sign in again" in r.text


def login_via_github(c: TestClient) -> None:
    r = c.get("/login", follow_redirects=False)
    state = r.headers["location"].split("state=")[1].split("&")[0]
    r = c.get(f"/auth/github/callback?code=ok&state={state}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/dashboard"
    assert "lb_session" in c.cookies


def test_callback_happy_path_sets_session(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        r = c.post("/logout", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/"
        assert "lb_session" not in c.cookies


def test_callback_unverified_email(tmp_path):
    app = oauth_app(tmp_path, emails=[{"email": "octo@example.com", "primary": True, "verified": False}])
    with TestClient(app) as c:
        r = c.get("/login", follow_redirects=False)
        state = r.headers["location"].split("state=")[1].split("&")[0]
        r = c.get(f"/auth/github/callback?code=ok&state={state}")
        assert r.status_code == 403 and "verify your GitHub email" in r.text


def test_callback_github_error(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        r = c.get("/login", follow_redirects=False)
        state = r.headers["location"].split("state=")[1].split("&")[0]
        r = c.get(f"/auth/github/callback?code=bad&state={state}")
        assert r.status_code == 502 and "GitHub" in r.text and "gho_" not in r.text


def test_callback_malformed_github_json(tmp_path):
    app = make_app(
        tmp_path,
        client_id="cid",
        client_secret="csec",
        github_transport=github_transport_malformed_emails(),
    )
    with TestClient(app) as c:
        r = c.get("/login", follow_redirects=False)
        state = r.headers["location"].split("state=")[1].split("&")[0]
        r = c.get(f"/auth/github/callback?code=ok&state={state}")
        assert r.status_code == 502
        assert "GitHub" in r.text
        assert "gho_" not in r.text
        assert "Traceback" not in r.text
