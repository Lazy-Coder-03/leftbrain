"""OAuth 2.1 authorization server for MCP clients (#34)."""

from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from leftbrain.keys import KeyStore
from leftbrain.oauth.store import OAuthStore


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
