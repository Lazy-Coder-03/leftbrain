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
