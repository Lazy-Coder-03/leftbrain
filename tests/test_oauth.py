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
