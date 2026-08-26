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
        csrf = csrf_from(c.get("/dashboard").text)
        r = c.post("/logout", data={"csrf": csrf}, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/"
        assert "lb_session" not in c.cookies


def test_logout_requires_csrf(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        assert c.post("/logout").status_code == 403
        assert c.post("/logout", data={"csrf": "bogus"}).status_code == 403
        assert "lb_session" in c.cookies  # still signed in
        csrf = csrf_from(c.get("/dashboard").text)
        assert c.post("/logout", data={"csrf": csrf}, follow_redirects=False).status_code == 302


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


# --- demo allow-list, body cap and failure handling (C1, I1) ------------------

# exactly what static/site.js sends for each tab's prefilled values
DEMO_DEFAULTS = {
    "numbers": {"mode": "compare", "values": ["9.11", "9.9", "10"]},
    "convert": {"mode": "units", "value": "3", "from_unit": "oz", "to": "ml"},
    "datetime": {"mode": "diff", "from": "2026-08-26", "to": "2026-12-25"},
    "text": {"mode": "count", "text": "strawberry \U0001f353 na\u00efve caf\u00e9"},
}


def test_demo_defaults_from_the_landing_page_still_run(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        for tool, body in DEMO_DEFAULTS.items():
            r = c.post(f"/demo/{tool}", json=body)
            assert r.status_code == 200, (tool, r.status_code)
            j = r.json()
            if tool == "convert":  # "oz" is deliberately ambiguous
                assert j["ok"] is False and j["needs"]["field"] == "from_unit"
            else:
                assert j["ok"] is True, (tool, j)


def test_demo_rejects_unlisted_mode_fast(tmp_path):
    """A caller-supplied regex is off the allow-list, so no ReDoS can be triggered."""
    import time

    with TestClient(make_app(tmp_path)) as c:
        t0 = time.monotonic()
        r = c.post("/demo/text", json={"mode": "regex_match", "text": "a" * 40 + "!", "pattern": "(a+)+$"})
        elapsed = time.monotonic() - t0
        assert r.status_code == 400
        assert r.json()["ok"] is False and r.json()["error"] == "invalid_input"
        assert "count" in r.json()["message"]
        assert elapsed < 1.0, f"took {elapsed:.2f}s"


def test_demo_rejects_unlisted_argument_and_oversized_values(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = c.post("/demo/numbers", json={"mode": "compare", "values": ["1"], "precision": 99})
        assert r.status_code == 400 and "precision" in r.json()["message"]
        r = c.post("/demo/text", json={"mode": "count", "text": "x" * 2001})
        assert r.status_code == 400 and "too long" in r.json()["message"]
        r = c.post("/demo/numbers", json={"mode": "compare", "values": ["1"] * 51})
        assert r.status_code == 400 and "too many" in r.json()["message"]
        r = c.post("/demo/numbers", json={"values": ["1", "2"]})  # no mode at all
        assert r.status_code == 400 and r.json()["error"] == "invalid_input"


def test_demo_rejects_oversized_body_before_reading_it(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = c.post("/demo/numbers", content=b"x" * 9000, headers={"content-type": "application/json"})
        assert r.request.headers["content-length"] == "9000"
        assert r.status_code == 413
        assert r.json() == {"ok": False, "error": "invalid_input", "message": "body too large"}


def test_demo_never_returns_a_traceback(tmp_path, monkeypatch):
    from leftbrain.web import demo as demo_mod

    leaky = {"ok": False, "error": "internal", "message": "boom", "trace": "Traceback (most recent call last): ..."}
    monkeypatch.setitem(demo_mod.DEMO_TOOLS, "numbers", lambda *a, **k: dict(leaky))
    with TestClient(make_app(tmp_path)) as c:
        r = c.post("/demo/numbers", json={"mode": "compare", "values": ["1"]})
        assert "trace" not in r.json() and "Traceback" not in r.text


def test_demo_unexpected_exception_is_a_generic_500(tmp_path, monkeypatch):
    from leftbrain.web import demo as demo_mod

    def boom(*a, **k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setitem(demo_mod.DEMO_TOOLS, "numbers", boom)
    with TestClient(make_app(tmp_path)) as c:
        r = c.post("/demo/numbers", json={"mode": "compare", "values": ["1"]})
        assert r.status_code == 500
        assert r.json() == {"ok": False, "error": "internal", "message": "the tool failed; try different input"}
        assert "secret internal detail" not in r.text and "Traceback" not in r.text


# --- client IP / trusted proxy hops (C2) -------------------------------------


def scope_with(xff=None, client="127.0.0.1", **extra_headers):
    headers = [(k.replace("_", "-").encode(), v.encode()) for k, v in extra_headers.items()]
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    return {"type": "http", "client": (client, 1234), "headers": headers}


def test_client_ip_counts_hops_from_the_right_and_ignores_spoofable_headers():
    from leftbrain.serve import _client_ip

    # one proxy in front: the single entry is the one it wrote
    assert _client_ip(scope_with("1.2.3.4"), hops=1) == "1.2.3.4"
    # the attacker-controlled leftmost entry must never be the rate-limit key
    assert _client_ip(scope_with("9.9.9.9, 10.0.0.1"), hops=1) == "10.0.0.1"
    assert _client_ip(scope_with("9.9.9.9, 10.0.0.1"), hops=2) == "9.9.9.9"
    # a chain shorter than expected fails closed to the nearest hop
    assert _client_ip(scope_with("1.2.3.4"), hops=2) == "1.2.3.4"
    # x-real-ip / cf-connecting-ip are single-valued and forgeable: never believed
    assert _client_ip(scope_with(None, x_real_ip="6.6.6.6", cf_connecting_ip="7.7.7.7")) == "127.0.0.1"
    assert _client_ip(scope_with("1.2.3.4", x_real_ip="6.6.6.6"), hops=1) == "1.2.3.4"
    # no proxy at all: the header is worthless
    assert _client_ip(scope_with("1.2.3.4", client="5.5.5.5"), hops=0) == "5.5.5.5"
    assert _client_ip({"type": "http", "client": None, "headers": []}) == "unknown"


def test_trusted_proxy_hops_from_env(monkeypatch):
    from leftbrain import serve

    monkeypatch.setenv("LEFTBRAIN_TRUSTED_PROXY_HOPS", "2")
    assert serve._trusted_proxy_hops() == 2
    monkeypatch.setenv("LEFTBRAIN_TRUSTED_PROXY_HOPS", "nonsense")
    assert serve._trusted_proxy_hops() == 1
    monkeypatch.delenv("LEFTBRAIN_TRUSTED_PROXY_HOPS")
    assert serve._trusted_proxy_hops() == 1


def test_demo_throttle_is_keyed_on_the_trusted_hop_not_the_leftmost(tmp_path):
    """A rotating leftmost X-Forwarded-For must not buy an attacker extra demo calls."""
    with TestClient(make_app(tmp_path)) as c:
        last = None
        for i in range(31):
            last = c.post("/demo/numbers", json={"mode": "compare", "values": ["1", "2"]}, headers={"x-forwarded-for": f"9.9.9.{i}, 10.0.0.1"})
        assert last.status_code == 429 and int(last.headers["retry-after"]) > 0
        assert last.json()["error"] == "rate_limited"


# --- response headers (I2, I3) ------------------------------------------------


def test_security_headers_on_html_and_json(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        for r in (c.get("/", headers={"Accept": "text/html"}), c.get("/healthz")):
            assert r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
            assert r.headers["x-frame-options"] == "DENY"
            csp = r.headers["content-security-policy"]
            assert "default-src 'self'" in csp and "script-src 'self'" in csp
            assert "https://fonts.googleapis.com" in csp and "https://fonts.gstatic.com" in csp
            assert "https://avatars.githubusercontent.com" in csp and "frame-ancestors 'none'" in csp


def test_templates_carry_no_inline_script_so_the_csp_holds():
    from leftbrain.web import HERE

    for tpl in sorted((HERE / "templates").glob("*.html")):
        body = tpl.read_text(encoding="utf-8")
        assert "<script>" not in body and "<script type" not in body, tpl.name
        assert "onclick=" not in body and "onload=" not in body, tpl.name


def test_dashboard_and_key_mutations_are_never_cached(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        page = c.get("/dashboard")
        assert page.headers["cache-control"] == "no-store"
        csrf = csrf_from(page.text)
        created = c.post("/dashboard/keys", data={"name": "x", "csrf": csrf})
        assert created.headers["cache-control"] == "no-store"
        key = created.text.split('<code id="new-key">')[1].split("</code>")[0]
        r = c.post(f"/dashboard/keys/{key[:13]}/revoke", data={"csrf": csrf}, follow_redirects=False)
        assert r.status_code == 302 and r.headers["cache-control"] == "no-store"


# --- 503 guard, cookie flags, cross-owner revoke, protected prefixes (I6, I8) --


def test_key_routes_503_without_a_store(tmp_path):
    config = WebConfig(client_id="cid", client_secret="csec", secret="test-secret-0123456789", base_url=None, open_signup=False, github_transport=github_transport())
    with TestClient(build_app(include_external=False, keys_db=None, web_config=config)) as c:
        login_via_github(c)
        assert c.get("/dashboard").status_code == 503
        csrf = auth.csrf_token("test-secret-0123456789", auth.User("octo", "octo@example.com", None))
        assert c.post("/dashboard/keys", data={"name": "x", "csrf": csrf}).status_code == 503
        assert c.post("/dashboard/keys/lblz_whatever/revoke", data={"csrf": csrf}).status_code == 503


def test_session_cookie_flags_and_oauth_cookie_cleared(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        r = c.get("/login", follow_redirects=False)
        state = r.headers["location"].split("state=")[1].split("&")[0]
        r = c.get(f"/auth/github/callback?code=ok&state={state}", headers={"x-forwarded-proto": "https"}, follow_redirects=False)
        assert r.status_code == 302
        cookies = r.headers.get_list("set-cookie")
        session = next(h for h in cookies if h.startswith("lb_session="))
        assert "httponly" in session.lower() and "samesite=lax" in session.lower() and "secure" in session.lower()
        oauth = next(h for h in cookies if h.startswith("lb_oauth="))
        assert "max-age=0" in oauth.lower() or "01 jan 1970" in oauth.lower()


def test_revoke_of_a_real_other_owner_key_is_refused(tmp_path):
    from leftbrain.keys import KeyStore

    store = KeyStore(str(tmp_path / "k.sqlite3"))
    raw, _ = store.create("other@example.com", note="theirs")
    prefix = raw[:13]
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        page = c.get("/dashboard")
        assert prefix not in page.text  # never listed for the signed-in user
        r = c.post(f"/dashboard/keys/{prefix}/revoke", data={"csrf": csrf_from(page.text)})
        assert r.status_code == 403 and "different account" in r.text
    assert store.get_by_prefix(prefix).disabled is False  # still usable by its owner


def test_callback_404_when_oauth_not_configured(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = c.get("/auth/github/callback?code=ok&state=x")
        assert r.status_code == 404 and "not configured" in r.text


def test_every_protected_prefix_needs_a_bearer(tmp_path):
    from leftbrain.serve import PROTECTED_PREFIXES

    config = WebConfig(client_id=None, client_secret=None, secret="test-secret-0123456789", base_url=None, open_signup=False)
    app = build_app(include_external=True, keys_db=str(tmp_path / "k.sqlite3"), web_config=config)
    with TestClient(app) as c:
        for path in PROTECTED_PREFIXES:
            r = c.get(path) if path == "/keys/me" else c.post(path, json={})
            assert r.status_code != 200, path
            if path != "/files/mcp":  # not mounted here; 401 or 404 are both fine
                assert r.status_code == 401, (path, r.status_code)


def test_key_store_list_normalises_the_owner(tmp_path):
    from leftbrain.keys import KeyStore

    store = KeyStore(str(tmp_path / "k.sqlite3"))
    store.create("Octo@Example.COM", note="mixed case")
    assert len(store.list("octo@example.com")) == 1
    assert len(store.list("  OCTO@Example.com ")) == 1
    assert store.list(None) == store.list()


def test_warns_when_base_url_is_unset(tmp_path, capsys):
    make_app(tmp_path, client_id="cid", client_secret="csec")
    assert "LEFTBRAIN_BASE_URL" in capsys.readouterr().out
    make_app(tmp_path, client_id="cid", client_secret="csec", base_url="https://leftbrain.example")
    assert "LEFTBRAIN_BASE_URL" not in capsys.readouterr().out


# --- static caching, quota, tab markup, 404, throttle pruning (I4, I7, M1-M4) ---


def test_static_assets_are_cached_and_version_stamped(tmp_path):
    from leftbrain import __version__

    with TestClient(make_app(tmp_path)) as c:
        assert c.get("/static/site.css").headers["cache-control"] == "public, max-age=86400"
        assert c.get("/static/site.js").headers["cache-control"] == "public, max-age=86400"
        html = c.get("/", headers={"Accept": "text/html"}).text
        for asset in ("site.css", "site.js", "logo.svg"):
            assert f"/static/{asset}?v={__version__}" in html, asset


def test_dashboard_quota_comes_from_the_configured_default(tmp_path, monkeypatch):
    import leftbrain.keys

    monkeypatch.setattr(leftbrain.keys, "DEFAULT_DAILY", 1234)
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        html = c.get("/dashboard").text
        assert '1,234 <span class="sub">/ key</span>' in html
        assert '5,000 <span class="sub">/ key</span>' not in html


def test_tabs_are_plain_buttons_not_an_aria_tab_widget(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        landing = c.get("/", headers={"Accept": "text/html"}).text
        assert 'role="tablist"' not in landing and 'role="tab"' not in landing
        assert 'aria-pressed="true" data-tool="numbers"' in landing
        docs = c.get("/docs").text
        assert 'role="tablist"' not in docs and 'role="tab"' not in docs
        assert 'class="ostabs"' in docs and 'aria-pressed="true"' in docs


def test_unknown_path_gets_the_branded_404(tmp_path):
    """The MCP app is mounted at "" as a catch-all; /nope must still reach our handler."""
    with TestClient(make_app(tmp_path)) as c:
        r = c.get("/nope", headers={"Accept": "text/html"})
        assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")
        assert 'class="brand"' in r.text and "leftbrain" in r.text and "Page not found" in r.text
        j = c.get("/nope", headers={"Accept": "application/json"})
        assert j.status_code == 404 and j.json()["ok"] is False and j.json()["error"] == "unsupported"
        assert c.post("/mcp", json={}).status_code == 401  # the MCP route itself still works


def test_error_pages_keep_the_signed_in_nav(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        assert "Get an API key" in c.get("/docs/nope").text
        login_via_github(c)
        for path in ("/docs/nope", "/nope"):
            r = c.get(path, headers={"Accept": "text/html"})
            assert r.status_code == 404
            assert '<a class="btn ghost" href="/dashboard">octo</a>' in r.text, path


def test_throttle_prunes_on_a_timer_not_on_dict_size(monkeypatch):
    from leftbrain.web import demo as demo_mod

    class Clock:
        t = 1000.0

        def monotonic(self):
            return self.t

    clock = Clock()
    monkeypatch.setattr(demo_mod, "time", clock)
    t = demo_mod.Throttle(limit=2, window=60)
    for i in range(6000):  # far past the old size-based trigger of 5000
        assert t.allow(f"10.0.{i // 250}.{i % 250}")[0]
    assert len(t._hits) == 6000
    first_prune = t._last_prune
    clock.t += 1
    t.allow("10.0.0.0")
    assert t._last_prune == first_prune and len(t._hits) == 6000  # still inside the window
    clock.t += 61
    t.allow("10.0.0.0")
    assert t._last_prune > first_prune and len(t._hits) == 1  # idle IPs finally forgotten


def test_stylesheet_caps_the_demo_output_and_thins_scrollbars():
    from leftbrain.web import HERE

    css = (HERE / "static" / "site.css").read_text(encoding="utf-8")
    assert "max-height:340px" in css  # a long result scrolls instead of pushing the page down
    assert "align-items:start" in css
    assert ".doc .codewrap pre{padding-right:4.5rem}" in css  # copy button never overlaps code
    assert "scrollbar-width:thin" in css and "pre::-webkit-scrollbar-thumb" in css


def test_docs_json_samples_are_pretty_printed():
    from leftbrain.web import HERE

    md = (HERE / "docs" / "quickstart.md").read_text(encoding="utf-8")
    assert '"remaining_today": 4988\n  }\n}' in md
    assert '"assumptions": [],\n  "warnings": []\n}' in md
    assert '{"ok":true,"result":' not in md  # no one-line JSON blobs left in the samples


# --- the README is the contract people read before deploying (I5) -------------


def test_readme_documents_the_key_store_and_proxy_contract():
    import pathlib

    readme = (pathlib.Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    section = readme.split("### Per-user API keys")[1].split("\n## ")[0]
    for token in (
        "LEFTBRAIN_KEYS_URL",
        "DATABASE_URL",
        "LEFTBRAIN_KEYS_DB",
        "LEFTBRAIN_TRUSTED_PROXY_HOPS",
        "LEFTBRAIN_DEFAULT_DAILY_QUOTA",
        "LEFTBRAIN_DEFAULT_RPM",
        "LEFTBRAIN_SIGNUPS_PER_IP_PER_DAY",
        "LEFTBRAIN_OPEN_SIGNUP",
        "X-RateLimit-Remaining-Today",
        "X-RateLimit-Limit-Day",
        "X-RateLimit-Limit-Minute",
        "Retry-After",
        "GET /keys/me",
        "SHA-256",
        "docs/deploy-northflank.md",
        "leftbrain-keys usage --days 7",
    ):
        assert token in section, token
    # one runnable command per line, not a collapsed pipe-separated list
    assert "| disable" not in section
    for cmd in ("leftbrain-keys create ", "leftbrain-keys list", "leftbrain-keys revoke ", "leftbrain-keys stats"):
        assert f"\n{cmd}" in section, cmd


def test_readme_defaults_match_the_code():
    import pathlib

    from leftbrain import keys as keys_mod

    readme = (pathlib.Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert f"`LEFTBRAIN_DEFAULT_DAILY_QUOTA` ({keys_mod.DEFAULT_DAILY})" in readme
    assert f"`LEFTBRAIN_DEFAULT_RPM` ({keys_mod.DEFAULT_RPM})" in readme
    assert f"`LEFTBRAIN_SIGNUPS_PER_IP_PER_DAY` ({keys_mod.SIGNUPS_PER_IP_PER_DAY})" in readme
    assert f"create up to {keys_mod.MAX_ACTIVE_KEYS_PER_EMAIL} keys" in readme
