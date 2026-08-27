import httpx
import pytest
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
        assert r.status_code == 200 and "lblz_" in r.text and "Copy it now" in r.text
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
        r = c.post("/demo/convert", json={"mode": "units", "value": 3, "from_unit": "oz", "to_unit": "ml"})
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


def test_docs_changelog_page(tmp_path):
    from leftbrain import __version__

    with TestClient(make_app(tmp_path)) as c:
        r = c.get("/docs/changelog")
        assert r.status_code == 200
        body = article(r.text)
        assert "0.1.0" in body and __version__ in body
        assert "Keep a Changelog" in body
        assert 'href="/docs/changelog" class="cur"' in r.text  # the sidebar lists it last
        assert r.text.index('href="/docs/changelog" class="cur"') > r.text.index('href="/docs/tools"')


def test_footer_version_links_to_the_changelog(tmp_path):
    from leftbrain import __version__

    with TestClient(make_app(tmp_path)) as c:
        for path, headers in (("/docs", {}), ("/", {"Accept": "text/html"})):
            assert f'<a href="/docs/changelog">v{__version__}</a>' in c.get(path, headers=headers).text, path


def test_pages_without_examples_get_no_key_picker(tmp_path):
    """The changelog and the tool index ask for no key, so they must not offer one."""
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        two_keys(c)
        for slug in ("changelog", "tools"):
            r = c.get(f"/docs/{slug}")
            assert r.status_code == 200, slug
            assert 'id="keypick"' not in r.text, slug
            assert 'class="keybar' not in r.text, slug  # neither the picker nor the sign-in note
            assert r.headers.get("cache-control") != "no-store", slug


def test_changelog_page_reads_the_one_copy_that_exists():
    """The wheel ships a copy under web/docs; a dev checkout reads the repo-root original."""
    import pathlib

    from leftbrain.web import HERE
    from leftbrain.web.docs import ROOT_SOURCES, page_source

    root = pathlib.Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    shipped = HERE / "docs" / "changelog.md"
    assert ROOT_SOURCES["changelog"] == root
    assert page_source("changelog") == (shipped if shipped.is_file() else root)


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


# --- request / response containers -------------------------------------------


def test_render_markdown_labels_request_response_and_command():
    from leftbrain.web.docs import render_markdown

    md = (
        ":::request\n```bash\ncurl -s X\n```\n:::\n\n"
        ":::response\n```json\n{\"ok\": true}\n```\n:::\n\n"
        ":::command\n```bash\nexport LB_KEY=x\n```\n:::\n\n"
        ":::request tools/call\n```json\n{\"method\": \"tools/call\"}\n```\n:::\n\nafter\n"
    )
    html = render_markdown(md)
    assert '<div class="io io-req"><span class="io-label">Request · you send this</span>' in html
    assert '<div class="io io-res"><span class="io-label">Response · you get this back</span>' in html
    assert '<div class="io io-cmd"><span class="io-label">Command · run in your terminal</span>' in html
    assert '<span class="io-label">Request · tools/call</span>' in html  # the labelled variant
    assert html.count('class="io ') == 4 and "<p>after</p>" in html
    assert ":::" not in html


def test_render_markdown_io_container_wraps_os_tabs():
    """A request may be per-OS: the container holds the tabs, not the other way round."""
    from leftbrain.web.docs import render_markdown

    md = (
        ":::request\n:::os\n"
        "### windows\n```powershell\ncurl.exe -s X\n```\n"
        "### macos\n```bash\nMACOS_MARKER\n```\n"
        "### linux\n```bash\nLINUX_MARKER\n```\n"
        ":::\n:::\n\nafter\n"
    )
    html = render_markdown(md)
    assert '<div class="io io-req"><span class="io-label">Request · you send this</span><div class="os">' in html
    assert html.count('class="os-block"') == 3 and "MACOS_MARKER" in html and "LINUX_MARKER" in html
    assert html.count('class="io ') == 1 and "<p>after</p>" in html
    assert ":::" not in html  # the inner container's terminator did not close the outer one


def test_render_markdown_io_container_is_fence_aware():
    from leftbrain.web.docs import render_markdown

    md = ":::response\n```text\nbefore\n:::\nafter-in-fence\n```\n:::\n\nafter\n"
    html = render_markdown(md)
    assert html.count('class="io io-res"') == 1
    assert "before" in html and "after-in-fence" in html  # the fence was captured whole
    assert "<p>:::</p>" not in html and "<p>after</p>" in html


def test_render_markdown_io_container_unterminated_fails_open():
    from leftbrain.web.docs import render_markdown

    md = "# T\n\n:::request\n```bash\ncurl -s X\n```\n\nno closing marker here\n"
    html = render_markdown(md)  # must not raise
    assert 'class="io' not in html and "<h1>T</h1>" in html


def test_hand_written_pages_label_what_you_send_and_what_comes_back(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        for path in ("/docs", "/docs/clients", "/docs/custom-agents"):
            body = article(c.get(path).text)
            assert 'class="io io-req"' in body, path
            assert 'class="io io-res"' in body, path
            assert "Request · you send this" in body and "Response · you get this back" in body, path
            assert ":::" not in body, path  # no container marker leaks as text
            assert "```" not in body, path
        quickstart = article(c.get("/docs").text)
        assert 'class="io io-cmd"' in quickstart  # storing the key is neither a call nor a reply
        assert "blue blocks" not in quickstart.lower()  # labels speak for themselves; no legend


# --- the set-it-up prompt and the custom-agents page --------------------------


def _quickstart_prompt() -> str:
    """The one fenced block a human is meant to hand to their coding agent."""
    from leftbrain.web import HERE

    md = (HERE / "docs" / "quickstart.md").read_text(encoding="utf-8")
    body = md.split('<h2 id="set-it-up-for-me">')[1]
    return body.split("```text\n")[1].split("\n```")[0]


def test_the_setup_prompt_is_self_contained_and_short():
    prompt = _quickstart_prompt()
    lines = prompt.split("\n")
    assert len(lines) <= 60, f"the prompt block is {len(lines)} lines; keep it pasteable"
    # what the agent needs without asking anyone
    for token in (
        "https://leftbrain.idlesync.in/mcp",
        "https://leftbrain.idlesync.in/external/mcp",
        "MCP Streamable HTTP",
        "Authorization: Bearer",
        "$LB_KEY",
        "ask me for it",
    ):
        assert token in prompt, token
    assert "Never print" in prompt  # the key must not be echoed back
    # it ends by proving the connection
    assert "numbers" in prompt and '"values": ["9.11", "9.9"]' in prompt


@pytest.mark.parametrize(
    ("client", "marker"),
    [
        ("Claude Code", "claude mcp add --transport http"),
        ("Copilot CLI", "copilot mcp add --transport http"),
        ("Gemini CLI", "gemini mcp add --transport http"),
        ("Cursor", ".cursor/mcp.json"),
        ("Windsurf", "~/.codeium/windsurf/mcp_config.json"),
        ("VS Code", ".vscode/mcp.json"),
        ("Cline", "streamableHttp"),
        ("Continue", "streamable-http"),
        ("Codex CLI", "[mcp_servers.leftbrain]"),
        ("Claude Desktop", "mcp-remote"),
    ],
)
def test_the_setup_prompt_covers_each_client(client, marker):
    prompt = _quickstart_prompt()
    assert client in prompt, client
    assert marker in prompt, f"{client}: {marker}"


def test_quickstart_and_clients_point_at_the_prompt_and_custom_agents(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        clients = c.get("/docs/clients").text
        assert "/docs/quickstart#set-it-up-for-me" in clients
        assert 'href="/docs/custom-agents"' in clients
        assert 'id="set-it-up-for-me"' in c.get("/docs/quickstart").text


def test_custom_agents_page_covers_every_language_and_the_no_sdk_path(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = c.get("/docs/custom-agents")
        assert r.status_code == 200 and "<h1>Custom agents</h1>" in r.text
        body = r.text.split('<article class="doc">')[1]
        for language in ("Python", "TypeScript", "Go", "Rust", "Java", "C#", "Swift", "Kotlin"):
            assert language in body, language
        for framework in ("Anthropic Messages API", "OpenAI Agents SDK", "Vercel AI SDK", "LangChain"):
            assert framework in body, framework
        # every snippet says whether it was run
        assert body.count("Executed") >= 2 and body.count("From the SDK docs") >= 6
        # the no-SDK fallback carries the four things that bite
        assert "text/event-stream" in body and "data: " in body
        assert "x-ratelimit-remaining-today" in body
        assert "retry-after" in body and "401" in body and "429" in body
        assert 'href="/docs/custom-agents" class="cur"' in r.text  # sidebar marks it


def test_docs_sidebar_marks_current_page(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        assert 'href="/docs/quickstart" class="cur"' in c.get("/docs").text
        assert 'href="/docs/clients" class="cur"' in c.get("/docs/clients").text


def test_docs_tools_page_lists_every_tool(tmp_path):
    from leftbrain.web.tools_list import TOOLS

    with TestClient(make_app(tmp_path)) as c:
        r = c.get("/docs/tools")
        assert r.status_code == 200
        for name, _desc, _modes in TOOLS:
            assert name in r.text
        assert 'id="geo_offline"' in r.text
        assert 'href="/docs/tools" class="cur"' in r.text
        assert "<h1>Tools</h1>" in r.text


def test_docs_sidebar_drops_readme_link_for_tools_page(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        html = c.get("/docs").text
        assert "github.com/Lazy-Coder-03/leftbrain#tools" not in html
        assert 'href="/docs/tools"' in html


def test_landing_tool_cards_link_to_docs_tools(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        html = c.get("/", headers={"Accept": "text/html"}).text
        assert 'href="/docs/tools/numbers"' in html


# --- demo allow-list, body cap and failure handling (C1, I1) ------------------

# exactly what static/site.js sends for each tab's prefilled values
DEMO_DEFAULTS = {
    "numbers": {"mode": "compare", "values": ["9.11", "9.9", "10"]},
    "convert": {"mode": "units", "value": "3", "from_unit": "oz", "to_unit": "ml"},
    "datetime": {"mode": "diff", "start": "2026-08-26", "end": "2026-12-25"},
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


def test_dashboard_delete_removes_a_revoked_or_expired_key(tmp_path):
    from leftbrain.keys import KeyStore

    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        csrf = csrf_from(c.get("/dashboard").text)
        key = new_key(c, "laptop", csrf)
        prefix = key[:13]
        page = c.get("/dashboard").text
        assert f"/dashboard/keys/{prefix}/delete" not in page  # a live key offers Revoke, not Delete
        r = c.post(f"/dashboard/keys/{prefix}/delete", data={"csrf": csrf})
        assert r.status_code == 409 and "Revoke it first" in r.text
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {key}"}).status_code == 200

        c.post(f"/dashboard/keys/{prefix}/revoke", data={"csrf": csrf}, follow_redirects=False)
        page = c.get("/dashboard").text
        assert f"/dashboard/keys/{prefix}/delete" in page and "data-confirm" in page
        r = c.post(f"/dashboard/keys/{prefix}/delete", data={"csrf": csrf}, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/dashboard"
        assert r.headers["cache-control"] == "no-store"
        page = c.get("/dashboard").text
        assert prefix not in page and "No keys yet" in page
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {key}"}).status_code == 401  # gone, not disabled

        # expired keys can be deleted straight away
        store = KeyStore(str(tmp_path / "k.sqlite3"), secret="test-secret-0123456789")
        _, stale = store.create("octo@example.com", note="stale", lifetime_days=1)
        store.db.run("UPDATE keys SET expires_at=? WHERE prefix=?", ("2000-01-01T00:00:00+00:00", stale.prefix))
        page = c.get("/dashboard").text
        assert f"/dashboard/keys/{stale.prefix}/delete" in page and f"/dashboard/keys/{stale.prefix}/revoke" not in page
        assert c.post(f"/dashboard/keys/{stale.prefix}/delete", data={"csrf": csrf}, follow_redirects=False).status_code == 302
        assert store.get_by_prefix(stale.prefix) is None

        # csrf and ownership guard the route like the others
        assert c.post(f"/dashboard/keys/{prefix}/delete", data={"csrf": "nope"}).status_code == 403
        raw, _ = store.create("other@example.com", note="theirs")
        store.set_disabled(raw[:13], True)
        r = c.post(f"/dashboard/keys/{raw[:13]}/delete", data={"csrf": csrf})
        assert r.status_code == 403 and "different account" in r.text
        assert store.get_by_prefix(raw[:13]) is not None
        assert c.post("/dashboard/keys/lblz_nosuch1/delete", data={"csrf": csrf}).status_code == 403


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
            from leftbrain.web import templates as _t

            assert f"/static/{asset}?v={_t.env.globals['asset_v']}" in html, asset
            assert f"?v={__version__}" not in html


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
    assert f"create up to {keys_mod.MAX_ACTIVE_KEYS_PER_EMAIL} active keys" in readme


def test_landing_cta_reflects_signed_in_user(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        anon = c.get("/", headers={"Accept": "text/html"}).text
        assert "Sign in with GitHub" in anon and 'href="/dashboard">Your API keys' not in anon
        login_via_github(c)
        html = c.get("/", headers={"Accept": "text/html"}).text
        assert "Sign in with GitHub → get a key" not in html
        assert 'href="/dashboard">Your API keys, octo →' in html


# --- the reader's own key, in the dashboard and in every docs example ---------


def article(html: str) -> str:
    """Just the docs body, so nav and layout markup cannot satisfy an assertion."""
    return html.split('<article class="doc">')[1].split("</article>")[0]


def new_key(c: TestClient, name: str, csrf: str) -> str:
    r = c.post("/dashboard/keys", data={"name": name, "csrf": csrf})
    assert r.status_code == 200
    return r.text.split('<code id="new-key">')[1].split("</code>")[0]


def two_keys(c: TestClient) -> tuple[str, str]:
    csrf = csrf_from(c.get("/dashboard").text)
    return new_key(c, "older", csrf), new_key(c, "newer", csrf)


def test_dashboard_show_reveals_a_key_again(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        csrf = csrf_from(c.get("/dashboard").text)
        raw = new_key(c, "laptop", csrf)
        prefix = raw[:13]
        listing = c.get("/dashboard")
        assert raw not in listing.text and f"/dashboard/keys/{prefix}/reveal" in listing.text

        shown = c.post(f"/dashboard/keys/{prefix}/reveal", data={"csrf": csrf})
        assert shown.status_code == 200 and shown.headers["cache-control"] == "no-store"
        assert shown.text.split('<code id="new-key">')[1].split("</code>")[0] == raw
        assert raw not in c.get("/dashboard").text  # only on the page that asked for it

        assert c.post(f"/dashboard/keys/{prefix}/reveal").status_code == 403
        assert c.post(f"/dashboard/keys/{prefix}/reveal", data={"csrf": "bogus"}).status_code == 403
        assert c.post("/dashboard/keys/lblz_notmine1/reveal", data={"csrf": csrf}).status_code == 403

        c.post(f"/dashboard/keys/{prefix}/revoke", data={"csrf": csrf})
        gone = c.post(f"/dashboard/keys/{prefix}/reveal", data={"csrf": csrf})
        assert gone.status_code == 200 and raw not in gone.text and "cannot be shown again" in gone.text


def test_dashboard_marks_keys_that_predate_reveal(tmp_path):
    from leftbrain.keys import KeyStore

    store = KeyStore(str(tmp_path / "k.sqlite3"))
    store.create("octo@example.com", note="ancient")  # hash only
    _, signup = store.create("octo@example.com", note="self-serve signup")
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        page = c.get("/dashboard").text
        assert "created before reveal was enabled" not in page and "/reveal" not in page
        assert page.count('class="pill off">legacy<') == 2 and "active<" not in page
        assert "does not hold one of your 3 slots" in page
        row = page.split(signup.prefix)[1].split("</tr>")[0]
        assert "issued by email signup" in row  # the origin is explained, not just the note echoed
        assert '>0 <span class="sub">/ 3</span>' in page  # legacy rows are not active slots
        body = article(c.get("/docs/quickstart").text)
        assert "lblz_…" in body and 'id="keypick"' not in body
        # the cap is still 3 keys of the user's own: the legacy rows do not eat it
        csrf = csrf_from(page)
        for i in range(3):
            assert "new-key" in c.post("/dashboard/keys", data={"name": f"k{i}", "csrf": csrf}).text
        assert "3 active" in c.post("/dashboard/keys", data={"name": "k3", "csrf": csrf}).text


def test_docs_without_a_key_keep_the_placeholder(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        for slug in ("quickstart", "clients", "custom-agents"):
            r = c.get(f"/docs/{slug}")
            body = article(r.text)
            assert "lblz_YOUR_KEY" not in body, slug  # the literal never reaches a reader
            assert "lblz_…" in body, slug
            assert 'id="keypick"' not in body, slug
            assert "Sign in</a> to fill in your key" in body, slug
            assert r.headers.get("cache-control") != "no-store", slug


def test_docs_fill_in_the_readers_own_key(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        older, newer = two_keys(c)
        r = c.get("/docs/quickstart")
        body = article(r.text)
        assert r.headers["cache-control"] == "no-store"  # never cached with a key in it
        assert "lblz_YOUR_KEY" not in body and "lblz_…" not in body
        assert newer in body and older not in body  # the newest key by default
        assert '<a href="/docs/clients"' in r.text  # the sidebar is left alone
        # the picker offers both, with the one in use selected
        assert f'<option value="{newer[:13]}" selected>newer</option>' in body
        assert f'<option value="{older[:13]}">older</option>' in body
        assert newer[:13] + "…" not in body  # named keys show only their name
        assert "Your key is filled into every example on this page." in body
        # the copy buttons still have code blocks to attach to
        assert "<pre>" in body

        picked = article(c.get(f"/docs/quickstart?key={older[:13]}").text)
        assert older in picked and newer not in picked
        assert f'<option value="{older[:13]}" selected>' in picked
        # a prefix this reader does not own falls back to the default, silently
        fallback = article(c.get("/docs/quickstart?key=lblz_notmine1").text)
        assert newer in fallback and "notmine" not in fallback


def test_the_setup_prompt_and_the_other_pages_carry_the_key(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        _, key = two_keys(c)
        body = article(c.get("/docs/quickstart").text)
        prompt = body.split('<h2 id="set-it-up-for-me">')[1].split("<pre>")[1].split("</pre>")[0]
        assert key in prompt
        assert body.count(key) >= 4  # the prompt plus every $env:LB_KEY / export LB_KEY line
        for slug in ("clients", "custom-agents"):
            page = c.get(f"/docs/{slug}")
            assert key in article(page.text), slug
            assert page.headers["cache-control"] == "no-store", slug


def test_docs_never_show_a_revoked_key(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        older, newer = two_keys(c)
        csrf = csrf_from(c.get("/dashboard").text)
        c.post(f"/dashboard/keys/{newer[:13]}/revoke", data={"csrf": csrf})
        body = article(c.get("/docs/quickstart").text)
        assert newer not in body and f'value="{newer[:13]}"' not in body
        assert older in body
        c.post(f"/dashboard/keys/{older[:13]}/revoke", data={"csrf": csrf})
        body = article(c.get("/docs/quickstart").text)
        assert older not in body and "lblz_…" in body
        assert "Create a key</a> to fill it in" in body


def test_every_docs_page_uses_the_one_placeholder(tmp_path):
    """The whole feature is one string replace, so no page may spell it differently."""
    from leftbrain.web import HERE
    from leftbrain.web.docs import ANON_KEY, KEY_PLACEHOLDER

    for page in sorted((HERE / "docs").glob("*.md")):
        text = page.read_text(encoding="utf-8")
        assert ANON_KEY not in text, f"{page.name}: use {KEY_PLACEHOLDER}, not the ellipsis form"


def test_demo_body_cap_holds_for_chunked_bodies(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        big = b'{"mode":"compare","values":["' + b"9" * 20000 + b'"]}'

        def chunks():
            for i in range(0, len(big), 1024):
                yield big[i : i + 1024]

        r = c.post("/demo/numbers", content=chunks(), headers={"content-type": "application/json"})
        assert r.status_code == 413 and r.json()["error"] == "invalid_input"


def test_demo_rejects_deep_nesting_with_contract_error(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        body = '{"mode":"compare","values":' + "[" * 400 + '"1"' + "]" * 400 + "}"
        r = c.post("/demo/numbers", content=body, headers={"content-type": "application/json"})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_input" and "nested" in r.json()["message"]
        # a sane shape is unaffected
        ok = c.post("/demo/numbers", json={"mode": "compare", "values": ["9.11", "9.9"]})
        assert ok.status_code == 200 and ok.json()["ok"]


# -- key expiry -------------------------------------------------------------


def backdate(tmp_path, prefix: str, iso: str = "2026-01-02T00:00:00.000000+00:00") -> None:
    """Expire a key in the past through a second handle on the same database."""
    from leftbrain.keys import KeyStore

    KeyStore(str(tmp_path / "k.sqlite3")).db.run("UPDATE keys SET expires_at=? WHERE prefix=?", (iso, prefix))


def set_days_left(tmp_path, prefix: str, days: int) -> None:
    from datetime import UTC, datetime, timedelta

    from leftbrain.keys import KeyStore

    # a shade under N days: days_left rounds up, so this reads as exactly N
    iso = (datetime.now(UTC) + timedelta(days=days, hours=-1)).isoformat(timespec="microseconds")
    KeyStore(str(tmp_path / "k.sqlite3")).db.run("UPDATE keys SET expires_at=? WHERE prefix=?", (iso, prefix))


def test_dashboard_create_form_offers_lifetimes_and_warns_about_never(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        page = c.get("/dashboard").text
        assert 'name="lifetime"' in page
        for v in ("30", "90", "365", "never"):
            assert f'value="{v}"' in page, v
        assert 'value="90" selected' in page  # the default
        assert "Keys that never expire are a liability" in page


def test_dashboard_creates_with_the_chosen_lifetime(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        csrf = csrf_from(c.get("/dashboard").text)
        r = c.post("/dashboard/keys", data={"name": "short", "lifetime": "30", "csrf": csrf})
        key = r.text.split('<code id="new-key">')[1].split("</code>")[0]
        me = c.get("/keys/me", headers={"Authorization": f"Bearer {key}"}).json()["result"]
        assert me["expires_at"] and me["expired"] is False
        r = c.post("/dashboard/keys", data={"name": "forever", "lifetime": "never", "csrf": csrf})
        key2 = r.text.split('<code id="new-key">')[1].split("</code>")[0]
        me2 = c.get("/keys/me", headers={"Authorization": f"Bearer {key2}"}).json()["result"]
        assert me2["expires_at"] is None
        page = c.get("/dashboard").text
        assert "expires in 30 days" in page
        assert "never expires" in page
        # a revoked key's expiry is moot: the row goes blank rather than counting down
        c.post(f"/dashboard/keys/{key[:13]}/revoke", data={"csrf": csrf}, follow_redirects=False)
        row = c.get("/dashboard").text.split(key[:13])[1].split("</tr>")[0]
        assert "expires in" not in row and "revoked" in row
        # a missing lifetime (old form, scripted post) falls back to the default rather than never
        r = c.post("/dashboard/keys", data={"name": "default", "csrf": csrf})
        key3 = r.text.split('<code id="new-key">')[1].split("</code>")[0]
        assert c.get("/keys/me", headers={"Authorization": f"Bearer {key3}"}).json()["result"]["expires_at"]


def test_dashboard_rejects_an_unlisted_lifetime(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        csrf = csrf_from(c.get("/dashboard").text)
        r = c.post("/dashboard/keys", data={"name": "x", "lifetime": "7", "csrf": csrf})
        assert r.status_code == 200 and "new-key" not in r.text and "lifetime" in r.text.lower()
        assert "No keys yet" in r.text


def test_expired_key_is_403_with_a_dated_message_and_frees_a_slot(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        csrf = csrf_from(c.get("/dashboard").text)
        keys = [new_key(c, f"k{i}", csrf) for i in range(3)]
        assert "3 active" in c.post("/dashboard/keys", data={"name": "no", "csrf": csrf}).text
        backdate(tmp_path, keys[0][:13])
        r = c.get("/keys/me", headers={"Authorization": f"Bearer {keys[0]}"})
        assert r.status_code == 403
        assert r.json() == {"ok": False, "error": "expired", "message": "key expired on 2026-01-02; create a new one at /dashboard"}
        page = c.get("/dashboard").text
        assert "expired 2026-01-02" in page and "2 <span" in page  # active count drops
        assert f"/dashboard/keys/{keys[0][:13]}/reveal" not in page  # no Show for an expired key
        assert f"/dashboard/keys/{keys[0][:13]}/revoke" not in page  # nothing left to revoke
        assert f"/dashboard/keys/{keys[0][:13]}/delete" in page  # but it can still be cleaned up
        assert "new-key" in c.post("/dashboard/keys", data={"name": "again", "csrf": csrf}).text


def test_dashboard_flags_a_key_expiring_soon(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        raw = new_key(c, "soon", csrf_from(c.get("/dashboard").text))
        set_days_left(tmp_path, raw[:13], 3)
        page = c.get("/dashboard").text
        assert "expires in 3 days" in page and 'class="pill soon"' in page


def test_docs_picker_excludes_expired_keys_and_reminds_near_expiry(tmp_path):
    with TestClient(oauth_app(tmp_path)) as c:
        login_via_github(c)
        older, newer = two_keys(c)
        set_days_left(tmp_path, newer[:13], 5)
        page = c.get("/docs/quickstart").text
        assert newer in page and "expires in 5 days" in page and "/dashboard" in page
        backdate(tmp_path, newer[:13])
        page = c.get("/docs/quickstart").text
        assert newer not in page and older in page and f'value="{newer[:13]}"' not in page
        backdate(tmp_path, older[:13])
        page = c.get("/docs/quickstart").text
        assert older not in page and 'id="keypick"' not in page and "Create a key" in page


def test_every_quota_mention_follows_the_configured_default(tmp_path, monkeypatch):
    """Landing, login, the demo 429 and the docs all quote the same number the store enforces."""
    import leftbrain.keys

    monkeypatch.setattr(leftbrain.keys, "DEFAULT_DAILY", 1234)
    with TestClient(make_app(tmp_path)) as c:  # no OAuth configured, so /login renders the page itself
        assert "1,234 calls/day" in c.get("/", headers={"Accept": "text/html"}).text
        assert "1,234 calls/day each" in c.get("/login", headers={"Accept": "text/html"}).text
        quick = c.get("/docs/quickstart").text
        assert "1,234 calls/day" in quick and '&quot;daily_quota&quot;: 1234' in quick and "5,000" not in quick
        assert "5000" not in c.get("/docs/custom-agents").text.split("daily quota of")[1][:8]
        for _ in range(31):
            r = c.post("/demo/numbers", json={"mode": "compare", "values": ["1", "2"]})
        assert r.status_code == 429 and "1,234 calls/day" in r.json()["message"]
