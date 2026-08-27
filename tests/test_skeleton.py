"""Every page can show a loading skeleton, not just the docs (server-rendered, so the
skeleton is shown by the *outgoing* page the instant a navigation starts).

Before this, `site.js` looked for `.doc`, then `<main>`, then `document.body` — so the
landing, login and error pages prepended skeleton lines above the nav, and the dashboard
got the markup with no CSS to position or dim it.
"""

import re

import pytest

pytest.importorskip("starlette")

from starlette.testclient import TestClient  # noqa: E402

from leftbrain.serve import build_app  # noqa: E402

PAGES = ["/", "/docs", "/docs/quickstart", "/login", "/docs/tools"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEFTBRAIN_SECRET", "x" * 32)
    with TestClient(build_app(include_external=False, keys_db=str(tmp_path / "k.sqlite3"))) as c:
        yield c


@pytest.mark.parametrize("path", PAGES)
def test_every_page_has_the_wrapper_the_skeleton_targets(client, path):
    html = client.get(path, headers={"Accept": "text/html"}).text
    assert 'id="page"' in html, path


def test_a_page_that_is_not_found_has_it_too(client):
    """The error page is a page like any other, and is reachable by navigation."""
    html = client.get("/nope", headers={"Accept": "text/html"}).text
    assert 'id="page"' in html


def test_there_is_exactly_one_wrapper_per_page(client):
    """Two would make the JS pick one and dim the other."""
    for path in PAGES:
        html = client.get(path, headers={"Accept": "text/html"}).text
        assert html.count('id="page"') == 1, path


def test_the_stylesheet_positions_and_dims_the_generic_wrapper(client):
    css = client.get("/static/site.css").text
    assert "#page.is-loading>*:not(.skel-lines)" in css
    assert "#page.is-loading>.skel-lines" in css
    assert re.search(r"#page\{[^}]*position:relative", css), "the overlay needs a positioned parent"


def test_the_script_never_falls_through_to_the_document_body(client):
    js = client.get("/static/site.js").text
    assert "document.body" not in js.split("startLoading")[1].split("}")[0]
    assert "getElementById('page')" in js


def test_the_shimmer_is_dropped_for_reduced_motion(client):
    css = client.get("/static/site.css").text
    assert "@media (prefers-reduced-motion:reduce)" in css and "animation:none" in css


def test_the_footer_does_not_link_to_the_private_repository(client):
    html = client.get("/", headers={"Accept": "text/html"}).text
    assert "github.com/Lazy-Coder-03" not in html
