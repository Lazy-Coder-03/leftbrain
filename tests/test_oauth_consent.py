"""The consent screen: the confused-deputy mitigation, and where a connector key is born."""

from urllib.parse import parse_qs, urlencode, urlparse

from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.testclient import TestClient

from leftbrain.keys import MAX_ACTIVE_KEYS_PER_EMAIL, KeyStore
from leftbrain.oauth.store import OAuthStore
from leftbrain.serve import build_app
from leftbrain.web import auth
from leftbrain.web.config import WebConfig

BASE = "https://leftbrain.test"
SECRET = "test-secret-0123456789"
USER = auth.User(login="octo", email="octo@example.com", avatar_url=None)
WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130"
LOOPBACK = "http://localhost:3118/callback"


def make_app(tmp_path):
    cfg = WebConfig(client_id=None, client_secret=None, secret=SECRET,
                    base_url=BASE, open_signup=False)
    return build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"), web_config=cfg)


def signed_in(app, user=USER):
    c = TestClient(app)
    c.cookies.set(auth.SESSION_COOKIE, auth.sign_session(SECRET, user))
    return c


def register(tmp_path, redirect="http://localhost/callback", name="Claude Code"):
    keys = KeyStore(str(tmp_path / "k.sqlite3"), secret=SECRET)
    OAuthStore(keys).save_client(OAuthClientInformationFull(
        client_id="c1", client_secret=None, redirect_uris=[AnyUrl(redirect)],
        token_endpoint_auth_method="none", client_name=name,
    ))
    return keys


def consent_url(redirect=LOOPBACK):
    return "/oauth/consent?" + urlencode({
        "client_id": "c1", "redirect_uri": redirect, "explicit": "1",
        "code_challenge": "chal", "scopes": "mcp", "state": "st", "resource": "",
    })


def approve_form(csrf, redirect=LOOPBACK, tools=("math", "convert"), approve=True):
    form = {
        "csrf": csrf, "client_id": "c1", "redirect_uri": redirect, "explicit": "1",
        "code_challenge": "chal", "scopes": "mcp", "state": "st", "resource": "",
        "scope_form": "1", "scope": list(tools),
    }
    if approve:
        form["approve"] = "1"
    return form


def post_consent(c, **over):
    form = {**approve_form(auth.csrf_token(SECRET, USER)), **over}
    return c.post("/oauth/consent", data=form, headers={"user-agent": WINDOWS}, follow_redirects=False)


# -- who may see it ---------------------------------------------------------


def test_consent_requires_signing_in(tmp_path):
    register(tmp_path)
    with TestClient(make_app(tmp_path)) as c:
        r = c.get(consent_url(), follow_redirects=False)
        assert r.status_code in (302, 303) and "/login" in r.headers["location"]


def test_the_consent_page_names_the_client_and_where_the_code_goes(tmp_path):
    register(tmp_path)
    with signed_in(make_app(tmp_path)) as c:
        r = c.get(consent_url(), headers={"user-agent": WINDOWS})
        assert r.status_code == 200
        assert "Claude Code" in r.text
        assert "localhost" in r.text  # the redirect host, shown plainly
        assert "Claude Code · Windows" in r.text  # the key it would create


def test_a_loopback_redirect_carries_a_warning(tmp_path):
    """A metadata document cannot prove which local process is listening on a port."""
    register(tmp_path)
    with signed_in(make_app(tmp_path)) as c:
        loop = c.get(consent_url(), headers={"user-agent": WINDOWS}).text
        assert "this computer" in loop.lower() or "this machine" in loop.lower()
    register(tmp_path, redirect="https://chatgpt.com/cb", name="ChatGPT")
    with signed_in(make_app(tmp_path)) as c:
        cloud = c.get(consent_url(redirect="https://chatgpt.com/cb"), headers={"user-agent": WINDOWS}).text
        assert "this computer" not in cloud.lower()


def test_a_get_sets_no_cookie_because_that_would_defeat_the_screen(tmp_path):
    """Setting signed state before approval is exactly what makes the screen bypassable."""
    register(tmp_path)
    with signed_in(make_app(tmp_path)) as c:
        r = c.get(consent_url(), headers={"user-agent": WINDOWS})
        assert auth.OAUTH_COOKIE not in r.headers.get("set-cookie", "")


def test_the_page_refuses_to_be_framed(tmp_path):
    register(tmp_path)
    with signed_in(make_app(tmp_path)) as c:
        r = c.get(consent_url(), headers={"user-agent": WINDOWS})
        assert r.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["cache-control"] == "no-store"


# -- approving --------------------------------------------------------------


def test_approving_mints_a_named_key_and_redirects_with_a_code(tmp_path):
    keys = register(tmp_path)
    with signed_in(make_app(tmp_path)) as c:
        r = post_consent(c)
        assert r.status_code in (302, 303)
        target = urlparse(r.headers["location"])
        assert target.hostname == "localhost" and target.port == 3118
        q = parse_qs(target.query)
        assert q["state"] == ["st"] and q["code"]
    made = keys.list(USER.email)
    assert [k.note for k in made] == ["Claude Code · Windows"]
    assert made[0].scope.allows("math", "eval") and not made[0].scope.allows("text", "slug")


def test_the_minted_key_is_an_ordinary_key_its_owner_can_see(tmp_path):
    keys = register(tmp_path)
    with signed_in(make_app(tmp_path)) as c:
        post_consent(c)
    made = keys.list(USER.email)[0]
    assert made.revealable and keys.reveal(USER.email, made.prefix).startswith("lblz_")
    assert keys.reveal("someone@else.co", made.prefix) is None
    assert made.holds_slot


def test_declining_returns_access_denied_and_mints_nothing(tmp_path):
    keys = register(tmp_path)
    declined = approve_form(auth.csrf_token(SECRET, USER), approve=False)
    with signed_in(make_app(tmp_path)) as c:
        r = c.post("/oauth/consent", data=declined, headers={"user-agent": WINDOWS}, follow_redirects=False)
        assert r.status_code in (302, 303)
        location = r.headers["location"]
        assert "error=access_denied" in location and "state=st" in location
    assert keys.list(USER.email) == []


def test_reconnecting_the_same_client_reuses_its_key(tmp_path):
    keys = register(tmp_path)
    with signed_in(make_app(tmp_path)) as c:
        post_consent(c)
        post_consent(c)
        post_consent(c)
    assert len(keys.list(USER.email)) == 1


# -- what it refuses --------------------------------------------------------


def test_a_redirect_uri_that_is_not_registered_is_refused(tmp_path):
    register(tmp_path)
    with signed_in(make_app(tmp_path)) as c:
        r = c.get(consent_url(redirect="https://evil.example/cb"))
        assert r.status_code == 400
        assert "not registered" in r.text.lower()


def test_approving_towards_an_unregistered_redirect_mints_nothing(tmp_path):
    keys = register(tmp_path)
    with signed_in(make_app(tmp_path)) as c:
        r = post_consent(c, redirect_uri="https://evil.example/cb")
        assert r.status_code == 400
    assert keys.list(USER.email) == []


def test_an_unknown_client_is_refused(tmp_path):
    register(tmp_path)
    with signed_in(make_app(tmp_path)) as c:
        assert c.get(consent_url().replace("client_id=c1", "client_id=nope")).status_code == 400


def test_consent_without_a_valid_csrf_token_is_refused(tmp_path):
    keys = register(tmp_path)
    with signed_in(make_app(tmp_path)) as c:
        assert post_consent(c, csrf="forged").status_code == 403
    assert keys.list(USER.email) == []


def test_approving_with_no_tools_ticked_grants_nothing_not_everything(tmp_path):
    """The failure that fails open: an empty tick-list read as "no restriction"."""
    keys = register(tmp_path)
    with signed_in(make_app(tmp_path)) as c:
        r = post_consent(c, scope=[])
        assert r.status_code == 400
        assert "at least one tool" in r.text
    assert keys.list(USER.email) == []


def test_at_the_cap_consent_refuses_and_says_how_to_fix_it(tmp_path):
    keys = register(tmp_path)
    for i in range(MAX_ACTIVE_KEYS_PER_EMAIL):
        keys.create_for_owner(USER.email, f"k{i}")
    with signed_in(make_app(tmp_path)) as c:
        r = post_consent(c)
        assert r.status_code in (302, 303)
        location = r.headers["location"]
        assert "error=access_denied" in location
        # the agent's user needs the actual fix, not "authorization failed"
        assert "revoke" in location
    assert len(keys.list(USER.email)) == MAX_ACTIVE_KEYS_PER_EMAIL


# -- and afterwards ---------------------------------------------------------


def test_a_connector_key_can_be_narrowed_afterwards_from_the_dashboard(tmp_path):
    """The advice the docs give: connect with everything, then cut it down."""
    keys = register(tmp_path)
    with signed_in(make_app(tmp_path)) as c:
        post_consent(c)
        made = keys.list(USER.email)[0]
        assert made.scope.allows("convert", "units")
        r = c.post(f"/dashboard/keys/{made.prefix}/scope", follow_redirects=False, data={
            "csrf": auth.csrf_token(SECRET, USER), "scope_form": "1", "scope": "math",
        })
        assert r.status_code in (200, 302, 303)
    after = keys.get_by_prefix(made.prefix)
    assert after.scope.allows("math", "eval")
    assert not after.scope.allows("convert", "units")
