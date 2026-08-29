"""OAuth 2.1 authorization server for MCP clients (#34)."""

import asyncio

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from leftbrain.keys import KeyStore
from leftbrain.oauth.naming import connector_key_name, os_from_user_agent
from leftbrain.oauth.provider import LeftbrainOAuthProvider
from leftbrain.oauth.redirects import is_loopback, redirect_uri_matches
from leftbrain.oauth.store import OAuthStore


def run(coro):
    """Drive one provider coroutine. The suite is otherwise sync; this keeps it that way."""
    return asyncio.run(coro)


def make_store(tmp_path):
    return OAuthStore(KeyStore(str(tmp_path / "k.sqlite3")))


def a_client(client_id="c1", secret="s3cret", uri="https://app.example/cb", name="ChatGPT"):
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret=secret,
        redirect_uris=[AnyUrl(uri)],
        token_endpoint_auth_method="client_secret_post",
        client_name=name,
    )


def test_registered_client_round_trips(tmp_path):
    oauth = make_store(tmp_path)
    oauth.save_client(a_client())
    back = oauth.load_client("c1")
    assert back is not None
    assert back.client_secret == "s3cret"
    assert [str(u) for u in back.redirect_uris] == ["https://app.example/cb"]
    assert back.client_name == "ChatGPT"


def test_unknown_client_is_none(tmp_path):
    assert make_store(tmp_path).load_client("never-registered") is None


def test_public_client_round_trips_without_a_secret(tmp_path):
    oauth = make_store(tmp_path)
    oauth.save_client(a_client(client_id="pub", secret=None))
    back = oauth.load_client("pub")
    assert back is not None and back.client_secret is None


def test_re_registering_a_client_replaces_it(tmp_path):
    oauth = make_store(tmp_path)
    oauth.save_client(a_client(name="Old name"))
    oauth.save_client(a_client(name="New name", uri="https://app.example/other"))
    back = oauth.load_client("c1")
    assert back.client_name == "New name"
    assert [str(u) for u in back.redirect_uris] == ["https://app.example/other"]


def test_consent_is_recorded_per_owner_and_client(tmp_path):
    oauth = make_store(tmp_path)
    oauth.record_consent("a@b.co", "c1", "lblz_AAAAAAAA")
    assert oauth.consent_for("a@b.co", "c1") == "lblz_AAAAAAAA"
    assert oauth.consent_for("a@b.co", "other") is None
    assert oauth.consent_for("someone@else.co", "c1") is None


def test_consent_is_updated_not_duplicated(tmp_path):
    oauth = make_store(tmp_path)
    oauth.record_consent("a@b.co", "c1", "lblz_AAAAAAAA")
    oauth.record_consent("a@b.co", "c1", "lblz_BBBBBBBB")
    assert oauth.consent_for("a@b.co", "c1") == "lblz_BBBBBBBB"


def test_consent_matches_the_owner_however_the_email_is_cased(tmp_path):
    """`keys` lowers and strips every owner it stores; consent must agree or it never matches."""
    oauth = make_store(tmp_path)
    oauth.record_consent("  Octo@Example.COM ", "c1", "lblz_AAAAAAAA")
    assert oauth.consent_for("octo@example.com", "c1") == "lblz_AAAAAAAA"
    assert oauth.consent_for("OCTO@EXAMPLE.COM", "c1") == "lblz_AAAAAAAA"


# -- authorization codes ----------------------------------------------------


def save_a_code(oauth, code="code-1", **over):
    fields = {
        "client_id": "c1", "key_hash": "kh", "owner": "a@b.co", "scopes": ["mcp"],
        "code_challenge": "chal", "redirect_uri": "https://app.example/cb",
        "redirect_uri_provided": True, "resource": None,
    }
    oauth.save_code(code, **{**fields, **over})


def test_a_code_can_be_taken_once(tmp_path):
    oauth = make_store(tmp_path)
    save_a_code(oauth)
    first = oauth.take_code("code-1")
    assert first is not None
    assert first["client_id"] == "c1" and first["code_challenge"] == "chal"
    assert first["scopes"] == ["mcp"]
    assert first["redirect_uri_provided"] is True
    assert oauth.take_code("code-1") is None  # replay refused


def test_an_expired_code_is_not_taken(tmp_path):
    oauth = make_store(tmp_path)
    save_a_code(oauth, code="old", ttl=-1)
    assert oauth.take_code("old") is None


def test_an_unknown_code_is_none(tmp_path):
    assert make_store(tmp_path).take_code("never-issued") is None


def test_taking_a_code_does_not_disturb_another(tmp_path):
    oauth = make_store(tmp_path)
    save_a_code(oauth, code="mine")
    save_a_code(oauth, code="yours", client_id="c2")
    assert oauth.take_code("mine")["client_id"] == "c1"
    assert oauth.take_code("yours")["client_id"] == "c2"


# -- tokens -----------------------------------------------------------------


def save_a_token(oauth, token="acc", kind="access", **over):
    fields = {"client_id": "c1", "key_hash": "kh", "scopes": ["mcp"], "resource": None, "ttl": 3600}
    oauth.save_token(token, kind=kind, **{**fields, **over})


def test_access_and_refresh_tokens_are_separate_kinds(tmp_path):
    oauth = make_store(tmp_path)
    save_a_token(oauth, "acc", "access")
    save_a_token(oauth, "ref", "refresh", ttl=86400)
    assert oauth.load_token("acc", "access") is not None
    assert oauth.load_token("acc", "refresh") is None
    assert oauth.load_token("ref", "refresh") is not None


def test_an_expired_token_does_not_load(tmp_path):
    oauth = make_store(tmp_path)
    save_a_token(oauth, "dead", "access", ttl=-1)
    assert oauth.load_token("dead", "access") is None


def test_a_loaded_token_carries_its_binding(tmp_path):
    oauth = make_store(tmp_path)
    save_a_token(oauth, "acc", "access", key_hash="the-key", scopes=["mcp", "offline_access"])
    row = oauth.load_token("acc", "access")
    assert row["key_hash"] == "the-key"
    assert row["scopes"] == ["mcp", "offline_access"]


def test_revoking_one_token_leaves_the_others(tmp_path):
    oauth = make_store(tmp_path)
    save_a_token(oauth, "acc", "access")
    save_a_token(oauth, "ref", "refresh", ttl=86400)
    oauth.revoke_token("acc")
    assert oauth.load_token("acc", "access") is None
    assert oauth.load_token("ref", "refresh") is not None


def test_revoking_clears_every_token_of_that_client_and_key(tmp_path):
    """RFC 7009: revoking one credential takes its sibling with it."""
    oauth = make_store(tmp_path)
    save_a_token(oauth, "acc", "access")
    save_a_token(oauth, "ref", "refresh", ttl=86400)
    save_a_token(oauth, "other-client", "access", client_id="c2")
    save_a_token(oauth, "other-key", "access", key_hash="kh2")
    oauth.revoke_client_tokens("c1", "kh")
    assert oauth.load_token("acc", "access") is None
    assert oauth.load_token("ref", "refresh") is None
    assert oauth.load_token("other-client", "access") is not None
    assert oauth.load_token("other-key", "access") is not None


# -- a token meters exactly as a key does -----------------------------------


def hash_of(keys, prefix):
    return keys.db.one("SELECT key_hash FROM keys WHERE prefix=?", (prefix,))["key_hash"]


def a_key_and_token(tmp_path, token="tok", **create):
    keys = KeyStore(str(tmp_path / "k.sqlite3"))
    oauth = OAuthStore(keys)
    raw, info = keys.create("a@b.co", **create)
    oauth.save_token(token, kind="access", client_id="c1", key_hash=hash_of(keys, info.prefix),
                     scopes=["mcp"], resource=None, ttl=3600)
    return keys, oauth, raw, info


def test_a_token_and_its_key_draw_down_one_quota(tmp_path):
    keys, _, raw, _ = a_key_and_token(tmp_path, daily_quota=3, rpm=100)
    assert keys.verify_oauth_token_and_count("tok").remaining == 2
    assert keys.verify_and_count(raw).remaining == 1
    assert keys.verify_oauth_token_and_count("tok").remaining == 0
    spent = keys.verify_oauth_token_and_count("tok")
    assert not spent.ok and spent.status == 429 and "quota" in spent.reason


def test_a_token_is_rate_limited_like_its_key(tmp_path):
    keys, _, _, _ = a_key_and_token(tmp_path, daily_quota=100, rpm=2)
    assert keys.verify_oauth_token_and_count("tok").ok
    assert keys.verify_oauth_token_and_count("tok").ok
    limited = keys.verify_oauth_token_and_count("tok")
    assert not limited.ok and limited.status == 429 and limited.retry_after


def test_a_token_carries_the_keys_scope(tmp_path):
    from leftbrain.scopes import parse_scope

    keys, _, _, _ = a_key_and_token(tmp_path, scope=parse_scope(["math"]))
    verdict = keys.verify_oauth_token_and_count("tok")
    assert verdict.ok
    assert verdict.key.scope.allows("math", "eval")
    assert not verdict.key.scope.allows("convert", "units")


def test_disabling_or_revoking_the_key_kills_its_token(tmp_path):
    keys, _, _, info = a_key_and_token(tmp_path)
    assert keys.verify_oauth_token_and_count("tok").ok
    keys.set_disabled(info.prefix, True)
    assert keys.verify_oauth_token_and_count("tok").status == 403
    keys.set_disabled(info.prefix, False)
    assert keys.verify_oauth_token_and_count("tok").ok
    keys.revoke(info.prefix)
    assert keys.verify_oauth_token_and_count("tok").status == 401


def test_an_expired_token_is_refused_even_though_its_key_is_fine(tmp_path):
    keys = KeyStore(str(tmp_path / "k.sqlite3"))
    oauth = OAuthStore(keys)
    raw, info = keys.create("a@b.co")
    oauth.save_token("stale", kind="access", client_id="c1", key_hash=hash_of(keys, info.prefix),
                     scopes=["mcp"], resource=None, ttl=-1)
    assert keys.verify_oauth_token_and_count("stale").status == 401
    assert keys.verify_and_count(raw).ok  # the key itself is untouched


def test_a_refresh_token_is_not_a_credential(tmp_path):
    keys = KeyStore(str(tmp_path / "k.sqlite3"))
    oauth = OAuthStore(keys)
    _, info = keys.create("a@b.co")
    oauth.save_token("refresh-only", kind="refresh", client_id="c1",
                     key_hash=hash_of(keys, info.prefix), scopes=["mcp"], resource=None, ttl=86400)
    assert keys.verify_oauth_token_and_count("refresh-only").status == 401


def test_an_unknown_or_empty_token_is_refused(tmp_path):
    keys = KeyStore(str(tmp_path / "k.sqlite3"))
    assert keys.verify_oauth_token_and_count("nope").status == 401
    assert keys.verify_oauth_token_and_count("").status == 401


def test_the_key_path_is_unchanged(tmp_path):
    """The first acceptance criterion: `lblz_` behaves exactly as it did before OAuth."""
    keys = KeyStore(str(tmp_path / "k.sqlite3"))
    raw, info = keys.create("a@b.co", daily_quota=2, rpm=100)
    first = keys.verify_and_count(raw)
    assert first.ok and first.remaining == 1 and first.key.prefix == info.prefix
    assert keys.verify_and_count(raw).remaining == 0
    assert keys.verify_and_count(raw).status == 429
    assert keys.verify_and_count("lblz_nope").status == 401
    assert keys.verify_and_count("not-even-a-key").status == 401  # wrong prefix, not a token lookup


# -- redirect URIs ----------------------------------------------------------


def test_an_exact_match_is_accepted():
    assert redirect_uri_matches("https://app.example/cb", "https://app.example/cb")


def test_a_different_host_or_path_is_refused():
    assert not redirect_uri_matches("https://app.example/cb", "https://evil.example/cb")
    assert not redirect_uri_matches("https://app.example/cb", "https://app.example/other")


def test_a_remote_host_may_not_vary_its_port():
    assert not redirect_uri_matches("https://app.example/cb", "https://app.example:8443/cb")


def test_loopback_ignores_the_port_because_claude_code_needs_it():
    # Claude Code registers localhost/callback and returns on an ephemeral port
    assert redirect_uri_matches("http://localhost/callback", "http://localhost:3118/callback")
    assert redirect_uri_matches("http://127.0.0.1/callback", "http://127.0.0.1:51234/callback")
    assert redirect_uri_matches("http://localhost:8080/callback", "http://localhost:9999/callback")


def test_loopback_still_matches_host_scheme_and_path_exactly():
    assert not redirect_uri_matches("http://localhost/callback", "http://127.0.0.1:3118/callback")
    assert not redirect_uri_matches("http://localhost/callback", "http://localhost:3118/other")
    assert not redirect_uri_matches("http://localhost/callback", "https://localhost:3118/callback")


def test_a_loopback_lookalike_host_is_not_loopback():
    assert not is_loopback("https://localhost.evil.example/cb")
    assert not redirect_uri_matches("http://localhost/callback", "https://localhost.evil.example:80/callback")


def test_the_redirect_uris_real_clients_use_are_classified_correctly():
    assert not is_loopback("https://claude.ai/api/mcp/auth_callback")
    assert not is_loopback("https://chatgpt.com/connector_platform_oauth_redirect")
    assert is_loopback("http://localhost:3118/callback")
    assert is_loopback("http://[::1]:7000/callback")


def test_rubbish_is_refused_rather_than_raising():
    assert not is_loopback("")
    assert not is_loopback("not a url")
    assert not redirect_uri_matches("http://localhost/callback", "")


def a_loopback_client(*uris):
    from leftbrain.oauth.redirects import LoopbackTolerantClient

    return LoopbackTolerantClient(
        client_id="c1", client_secret=None, token_endpoint_auth_method="none",
        redirect_uris=[AnyUrl(u) for u in (uris or ("http://localhost/callback",))],
    )


def test_the_client_object_itself_tolerates_a_loopback_port():
    """The SDK's /authorize handler asks the client, not the provider, so the rule lives here."""
    import pytest
    from mcp.shared.auth import InvalidRedirectUriError

    client = a_loopback_client("http://localhost/callback", "http://127.0.0.1/callback")
    for presented in ("http://localhost:3118/callback", "http://127.0.0.1:51234/callback"):
        assert str(client.validate_redirect_uri(AnyUrl(presented))) == presented
    for refused in ("https://evil.example/callback", "http://localhost:3118/other"):
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl(refused))


def test_a_remote_client_object_still_matches_exactly():
    import pytest
    from mcp.shared.auth import InvalidRedirectUriError

    client = a_loopback_client("https://chatgpt.com/cb")
    assert str(client.validate_redirect_uri(AnyUrl("https://chatgpt.com/cb"))) == "https://chatgpt.com/cb"
    with pytest.raises(InvalidRedirectUriError):
        client.validate_redirect_uri(AnyUrl("https://chatgpt.com:8443/cb"))


def test_a_sole_registered_uri_is_still_the_default_when_none_is_presented():
    client = a_loopback_client("http://localhost/callback")
    assert str(client.validate_redirect_uri(None)) == "http://localhost/callback"


# -- what a connector key is called -----------------------------------------

WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1"
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/130 Mobile"
LINUX = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"

LOCAL = ["http://localhost/callback"]
CLOUD = ["https://chatgpt.com/connector_platform_oauth_redirect"]


def test_the_os_is_read_from_the_user_agent():
    assert os_from_user_agent(WINDOWS) == "Windows"
    assert os_from_user_agent(MAC) == "macOS"
    assert os_from_user_agent(LINUX) == "Linux"


def test_a_phone_is_not_read_as_the_desktop_it_resembles():
    # an iPhone's user agent contains "like Mac OS X"; an Android's contains "Linux"
    assert os_from_user_agent(IPHONE) == "iOS"
    assert os_from_user_agent(ANDROID) == "Android"


def test_an_unreadable_user_agent_has_no_os():
    assert os_from_user_agent("curl/8.4.0") is None
    assert os_from_user_agent("") is None
    assert os_from_user_agent(None) is None


def test_a_local_client_is_named_after_the_machine_that_approved_it():
    assert connector_key_name("Claude Code", LOCAL, WINDOWS) == "Claude Code · Windows"
    assert connector_key_name("Cursor", ["http://127.0.0.1:1234/cb"], MAC) == "Cursor · macOS"


def test_a_cloud_client_is_named_web_and_never_the_approvers_os():
    assert connector_key_name("ChatGPT", CLOUD, WINDOWS) == "ChatGPT · web"
    assert connector_key_name("Claude", ["https://claude.ai/api/mcp/auth_callback"], MAC) == "Claude · web"


def test_a_local_client_with_an_unreadable_user_agent_says_local():
    assert connector_key_name("Claude Code", LOCAL, "curl/8.4.0") == "Claude Code · local"
    assert connector_key_name("Claude Code", LOCAL, None) == "Claude Code · local"


def test_a_nameless_client_still_gets_a_name():
    assert connector_key_name(None, CLOUD, WINDOWS) == "app · web"
    assert connector_key_name("   ", CLOUD, WINDOWS) == "app · web"


def test_the_name_fits_the_note_column():
    assert len(connector_key_name("x" * 200, CLOUD, WINDOWS)) <= 40
    assert len(connector_key_name("x" * 200, LOCAL, WINDOWS)) <= 40


def test_an_attacker_supplied_name_cannot_forge_a_second_line():
    forged = connector_key_name("Evil\n<script>", CLOUD, WINDOWS)
    assert forged.startswith("Evil <script>")
    assert "\n" not in forged and "\r" not in forged


# -- the provider -----------------------------------------------------------


def a_provider(tmp_path):
    keys = KeyStore(str(tmp_path / "k.sqlite3"))
    return LeftbrainOAuthProvider(OAuthStore(keys), keys), keys


def params(redirect="https://app.example/cb", state="st"):
    return AuthorizationParams(
        state=state, scopes=["mcp"], code_challenge="chal",
        redirect_uri=AnyUrl(redirect), redirect_uri_provided_explicitly=True, resource=None,
    )


def a_bound_code(provider, keys, owner="a@b.co", scopes=("mcp",)):
    _, info = keys.create(owner)
    return provider.issue_code(
        client_id="c1", key_hash=hash_of(keys, info.prefix), owner=owner, scopes=list(scopes),
        code_challenge="chal", redirect_uri="https://app.example/cb",
        redirect_uri_provided=True, resource=None,
    ), info


def test_registering_then_loading_a_client(tmp_path):
    provider, _ = a_provider(tmp_path)
    run(provider.register_client(a_client()))
    assert run(provider.get_client("c1")).client_name == "ChatGPT"
    assert run(provider.get_client("missing")) is None


def test_authorize_sends_the_browser_to_the_consent_page(tmp_path):
    """Nothing is granted and no cookie is set here: consent must come first (#34)."""
    provider, _ = a_provider(tmp_path)
    run(provider.register_client(a_client()))
    url = run(provider.authorize(a_client(), params()))
    assert url.startswith("/oauth/consent?")
    for field in ("client_id=c1", "code_challenge=chal", "state=st", "redirect_uri="):
        assert field in url


def test_a_code_exchanges_once_for_a_token_pair(tmp_path):
    provider, keys = a_provider(tmp_path)
    run(provider.register_client(a_client()))
    code, _ = a_bound_code(provider, keys)

    loaded = run(provider.load_authorization_code(a_client(), code))
    assert loaded is not None and loaded.code_challenge == "chal"
    token = run(provider.exchange_authorization_code(a_client(), loaded))
    assert token.access_token and token.refresh_token
    assert token.expires_in == 3600
    assert keys.verify_oauth_token_and_count(token.access_token).ok
    assert run(provider.load_authorization_code(a_client(), code)) is None  # spent


def test_a_code_belonging_to_another_client_is_not_loaded(tmp_path):
    provider, keys = a_provider(tmp_path)
    run(provider.register_client(a_client()))
    code, _ = a_bound_code(provider, keys)
    assert run(provider.load_authorization_code(a_client(client_id="c2"), code)) is None


def test_a_refresh_rotates_both_tokens(tmp_path):
    provider, keys = a_provider(tmp_path)
    run(provider.register_client(a_client()))
    code, _ = a_bound_code(provider, keys)
    first = run(provider.exchange_authorization_code(
        a_client(), run(provider.load_authorization_code(a_client(), code))))

    loaded = run(provider.load_refresh_token(a_client(), first.refresh_token))
    second = run(provider.exchange_refresh_token(a_client(), loaded, ["mcp"]))
    assert second.access_token != first.access_token
    assert second.refresh_token != first.refresh_token
    # OAuth 2.1 requires rotation for a public client: the presented one is dead
    assert run(provider.load_refresh_token(a_client(), first.refresh_token)) is None
    assert keys.verify_oauth_token_and_count(second.access_token).ok


def test_a_refresh_token_of_another_client_is_not_loaded(tmp_path):
    provider, keys = a_provider(tmp_path)
    run(provider.register_client(a_client()))
    code, _ = a_bound_code(provider, keys)
    pair = run(provider.exchange_authorization_code(
        a_client(), run(provider.load_authorization_code(a_client(), code))))
    assert run(provider.load_refresh_token(a_client(client_id="c2"), pair.refresh_token)) is None


def test_load_access_token_returns_the_binding(tmp_path):
    provider, keys = a_provider(tmp_path)
    run(provider.register_client(a_client()))
    code, info = a_bound_code(provider, keys)
    pair = run(provider.exchange_authorization_code(
        a_client(), run(provider.load_authorization_code(a_client(), code))))
    loaded = run(provider.load_access_token(pair.access_token))
    assert loaded is not None and loaded.client_id == "c1"
    assert loaded.key_hash == hash_of(keys, info.prefix)
    assert run(provider.load_access_token("never-issued")) is None


def test_revoking_a_token_takes_its_sibling_with_it(tmp_path):
    provider, keys = a_provider(tmp_path)
    run(provider.register_client(a_client()))
    code, _ = a_bound_code(provider, keys)
    pair = run(provider.exchange_authorization_code(
        a_client(), run(provider.load_authorization_code(a_client(), code))))
    run(provider.revoke_token(run(provider.load_access_token(pair.access_token))))
    assert run(provider.load_access_token(pair.access_token)) is None
    assert run(provider.load_refresh_token(a_client(), pair.refresh_token)) is None


# -- CIMD, as the provider reaches it ---------------------------------------


CIMD_URL = "https://app.example/client.json"


def stub_cimd(monkeypatch, client=None, calls=None):
    async def fetch(client_id, *, allow_insecure, transport=None):
        if calls is not None:
            calls.append(client_id)
        return client

    monkeypatch.setattr("leftbrain.oauth.provider.fetch_client_metadata", fetch)


def test_a_url_client_id_is_fetched_when_it_is_not_registered(tmp_path, monkeypatch):
    provider, _ = a_provider(tmp_path)
    stub_cimd(monkeypatch, a_client(client_id=CIMD_URL, secret=None))
    fetched = run(provider.get_client(CIMD_URL))
    assert fetched is not None and fetched.client_id == CIMD_URL


def test_a_registered_client_is_never_fetched(tmp_path, monkeypatch):
    """A stored registration wins, so a DCR client id is never treated as a URL to visit."""
    provider, _ = a_provider(tmp_path)
    run(provider.register_client(a_client()))
    calls = []
    stub_cimd(monkeypatch, None, calls)
    assert run(provider.get_client("c1")).client_name == "ChatGPT"
    assert calls == []


def test_a_plain_unknown_client_id_is_not_fetched(tmp_path, monkeypatch):
    provider, _ = a_provider(tmp_path)
    calls = []
    stub_cimd(monkeypatch, None, calls)
    assert run(provider.get_client("not-a-url")) is None
    assert calls == []  # only something URL-shaped is worth a request


def test_a_fetched_document_is_cached_for_the_process(tmp_path, monkeypatch):
    """Claude allows 10 s for the whole token call; the token leg must not re-fetch."""
    provider, _ = a_provider(tmp_path)
    calls = []
    stub_cimd(monkeypatch, a_client(client_id=CIMD_URL, secret=None), calls)
    run(provider.get_client(CIMD_URL))
    run(provider.get_client(CIMD_URL))
    run(provider.get_client(CIMD_URL))
    assert calls == [CIMD_URL]


def test_a_document_that_cannot_be_fetched_is_not_cached(tmp_path, monkeypatch):
    provider, _ = a_provider(tmp_path)
    calls = []
    stub_cimd(monkeypatch, None, calls)
    assert run(provider.get_client(CIMD_URL)) is None
    assert run(provider.get_client(CIMD_URL)) is None
    assert calls == [CIMD_URL, CIMD_URL]  # a failure is retried, not remembered


# -- registration housekeeping ----------------------------------------------


def test_pruning_drops_unconsented_registrations_only(tmp_path):
    """Claude registers a fresh client on every connection when it falls back to DCR."""
    oauth = make_store(tmp_path)
    oauth.save_client(a_client(client_id="stale"))
    oauth.save_client(a_client(client_id="consented"))
    oauth.record_consent("a@b.co", "consented", "lblz_AAAAAAAA")
    oauth.db.run("UPDATE oauth_clients SET created_at=?", ("2020-01-01T00:00:00+00:00",))
    assert oauth.prune_clients(older_than_days=30) == 1
    assert oauth.load_client("stale") is None
    assert oauth.load_client("consented") is not None


def test_pruning_keeps_a_recent_registration(tmp_path):
    oauth = make_store(tmp_path)
    oauth.save_client(a_client(client_id="fresh"))
    assert oauth.prune_clients(older_than_days=30) == 0
    assert oauth.load_client("fresh") is not None
