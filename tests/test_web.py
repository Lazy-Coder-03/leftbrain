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
