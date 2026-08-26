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


def github_transport_bad_user_shape(token="gho_test"):
    """Fake GitHub whose /user endpoint returns a JSON list instead of an object."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "github.com" and req.url.path == "/login/oauth/access_token":
            assert req.headers["accept"] == "application/json"
            return httpx.Response(200, json={"access_token": token, "token_type": "bearer"})
        if req.url.path == "/user":
            assert req.headers["authorization"] == f"Bearer {token}"
            return httpx.Response(200, json=[])
        if req.url.path == "/user/emails":
            return httpx.Response(
                200, json=[{"email": "octo@example.com", "primary": True, "verified": True}]
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def github_transport_bad_emails_shape(token="gho_test"):
    """Fake GitHub whose /user/emails endpoint returns a dict instead of a list."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "github.com" and req.url.path == "/login/oauth/access_token":
            assert req.headers["accept"] == "application/json"
            return httpx.Response(200, json={"access_token": token, "token_type": "bearer"})
        if req.url.path == "/user":
            assert req.headers["authorization"] == f"Bearer {token}"
            return httpx.Response(200, json={"login": "octo", "avatar_url": "https://a/octo.png"})
        if req.url.path == "/user/emails":
            return httpx.Response(200, json={"email": "octo@example.com", "primary": True})
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


def test_callback_user_wrong_shape(tmp_path):
    app = make_app(
        tmp_path,
        client_id="cid",
        client_secret="csec",
        github_transport=github_transport_bad_user_shape(),
    )
    with TestClient(app) as c:
        r = c.get("/login", follow_redirects=False)
        state = r.headers["location"].split("state=")[1].split("&")[0]
        r = c.get(f"/auth/github/callback?code=ok&state={state}")
        assert r.status_code == 502
        assert "GitHub" in r.text
        assert "gho_" not in r.text
        assert "Traceback" not in r.text


def test_callback_emails_wrong_shape(tmp_path):
    app = make_app(
        tmp_path,
        client_id="cid",
        client_secret="csec",
        github_transport=github_transport_bad_emails_shape(),
    )
    with TestClient(app) as c:
        r = c.get("/login", follow_redirects=False)
        state = r.headers["location"].split("state=")[1].split("&")[0]
        r = c.get(f"/auth/github/callback?code=ok&state={state}")
        assert r.status_code == 502
        assert "GitHub" in r.text
        assert "gho_" not in r.text
        assert "Traceback" not in r.text


def test_dashboard_requires_login(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        r = c.get("/dashboard", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/login"


def csrf_from(html: str) -> str:
    return html.split('name="csrf" value="')[1].split('"')[0]


def test_dashboard_create_list_cap_revoke(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        page = c.get("/dashboard")
        assert page.status_code == 200 and "No keys yet" in page.text and "octo" in page.text
        csrf = csrf_from(page.text)
        r = c.post("/dashboard/keys", data={"name": "laptop", "csrf": csrf})
        assert r.status_code == 200 and "lblz_" in r.text and "won't be shown again" in r.text
        key = r.text.split("<code id=\"new-key\">")[1].split("</code>")[0]
        assert key.startswith("lblz_") and len(key) > 20
        # the key works on the API
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {key}"}).json()["result"]["owner"] == "octo@example.com"
        for i in range(2):
            assert c.post("/dashboard/keys", data={"name": f"k{i}", "csrf": csrf}).status_code == 200
        r = c.post("/dashboard/keys", data={"name": "one-too-many", "csrf": csrf})
        assert r.status_code == 200 and "3 active" in r.text and "new-key" not in r.text
        prefix = key[:13]
        r = c.post(f"/dashboard/keys/{prefix}/revoke", data={"csrf": csrf}, follow_redirects=False)
        assert r.status_code == 302
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {key}"}).status_code == 403
        assert "revoked" in c.get("/dashboard").text


def test_dashboard_csrf_and_ownership(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        assert c.post("/dashboard/keys", data={"name": "x"}).status_code == 403
        assert c.post("/dashboard/keys", data={"name": "x", "csrf": "bogus"}).status_code == 403
        csrf = csrf_from(c.get("/dashboard").text)
        assert c.post("/dashboard/keys/lblz_notmine1/revoke", data={"csrf": csrf}).status_code == 403


def test_demo_runs_real_tools_and_rejects_unknown(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = c.post("/demo/numbers", json={"mode": "compare", "values": ["9.11", "9.9"]})
        assert r.status_code == 200 and r.json()["ok"] and r.json()["result"]["max"]["input"] == "9.9"
        r = c.post("/demo/convert", json={"mode": "units", "value": 3, "from_unit": "oz", "to": "ml"})
        assert r.json()["ok"] is False and "needs" in r.json()
        assert c.post("/demo/math", json={"mode": "eval", "expr": "1+1"}).status_code == 404
        assert c.post("/demo/numbers", content=b"not json", headers={"content-type": "application/json"}).status_code == 400


def test_demo_throttle(tmp_path):
    from leftbrain.web.demo import Throttle

    t = Throttle(limit=2, window=60)
    assert t.allow("1.1.1.1") == (True, 0) and t.allow("1.1.1.1")[0]
    ok, retry = t.allow("1.1.1.1")
    assert not ok and 0 < retry <= 60
    assert t.allow("2.2.2.2")[0]


def test_landing_content(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        html = c.get("/", headers={"Accept": "text/html"}).text
        assert "left brain" in html and 'id="demo"' in html and "geo_offline" in html
        assert 'href="/login"' in html and 'href="/docs"' in html
        assert "9.11" in html  # proof strip


def test_docs_pages_and_os_tabs(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = c.get("/docs")
        assert r.status_code == 200 and "Quickstart" in r.text and 'class="ostabs"' in r.text
        assert 'data-os="windows"' in r.text and "curl.exe" in r.text and "Invoke-RestMethod" in r.text
        assert "leftbrain.idlesync.in" in r.text
        assert c.get("/docs/clients").status_code == 200 and "claude mcp add" in c.get("/docs/clients").text
        assert c.get("/docs/nope").status_code == 404


def test_render_markdown_os_block():
    from leftbrain.web.docs import render_markdown

    md = "# T\n\n:::os\n### windows\n```powershell\ncurl.exe -s X\n```\n### macos\n```bash\ncurl -s X\n```\n### linux\n```bash\ncurl -s X\n```\n:::\n\nafter\n"
    html = render_markdown(md)
    assert html.count('class="os-block"') == 3 and 'data-os="macos"' in html and "<p>after</p>" in html
    assert '<button type="button" data-os="windows"' in html


def test_render_markdown_os_block_fence_aware_close_marker():
    from leftbrain.web.docs import render_markdown

    # A literal ":::" line inside the windows fence must not close the container early.
    md = (
        "# T\n\n"
        ":::os\n"
        "### windows\n"
        "```text\n"
        "before\n"
        ":::\n"
        "after-in-fence\n"
        "```\n"
        "### macos\n"
        "```bash\nMACOS_MARKER\n```\n"
        "### linux\n"
        "```bash\nLINUX_MARKER\n```\n"
        ":::\n"
        "\nafter\n"
    )
    html = render_markdown(md)
    assert html.count('class="os-block"') == 3
    assert "MACOS_MARKER" in html and "LINUX_MARKER" in html  # macos/linux fully captured
    assert "<h3>" not in html  # "### macos"/"### linux" must not leak as raw headings
    assert "<p>:::</p>" not in html  # the real closing marker must not leak as text
    assert "<p>after</p>" in html  # true terminator consumed; trailing text parses normally


def test_render_markdown_os_block_unterminated_fails_open():
    from leftbrain.web.docs import render_markdown

    md = "# T\n\n:::os\n### windows\n```powershell\ncurl.exe -s X\n```\n\nno closing marker here\n"
    html = render_markdown(md)  # must not raise
    assert 'class="ostabs"' not in html and 'class="os-block"' not in html
    assert "<h1>T</h1>" in html


def test_docs_sidebar_marks_current_page(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        assert 'href="/docs/quickstart" class="cur"' in c.get("/docs").text
        assert 'href="/docs/clients" class="cur"' in c.get("/docs/clients").text
