"""Every page can show a loading skeleton, not just the docs (server-rendered, so the
skeleton is shown by the *outgoing* page the instant a navigation starts).

Before this, `site.js` looked for `.doc`, then `<main>`, then `document.body` — so the
landing, login and error pages prepended skeleton lines above the nav, and the dashboard
got the markup with no CSS to position or dim it.

The skeleton then *covered* the page rather than standing in for it (#50): the content was
faded to a quarter and the lines laid over the top, so both showed at once.
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


def _blocks(css, selector):
    """Every declaration block whose selector list mentions `selector`."""
    out = []
    for rule in css.split("}"):
        head, brace, body = rule.partition("{")
        if brace and selector in head:
            out.append(body)
    return out


def test_the_stylesheet_hides_and_stands_in_for_the_generic_wrapper(client):
    css = client.get("/static/site.css").text
    assert _blocks(css, "#page.is-loading>*:not(.skel-lines)")
    assert _blocks(css, "#page.is-loading>.skel-lines")
    assert re.search(r"#page\{[^}]*min-height", css), "the footer must not ride up while loading"


@pytest.mark.parametrize("wrapper", ["#page", ".doc"])
def test_the_skeleton_replaces_the_page_instead_of_covering_it(client, wrapper):
    """#50: the page was dimmed to a quarter and the skeleton laid over it, so both showed."""
    css = client.get("/static/site.css").text
    hidden = _blocks(css, wrapper + ".is-loading>*:not(.skel-lines)")
    assert hidden, wrapper
    for block in hidden:
        assert "display:none" in block, block
        assert "opacity" not in block, "dimmed content still shows through: " + block
    for block in _blocks(css, wrapper + ".is-loading>.skel-lines"):
        assert "position:absolute" not in block, "an overlay, not a stand-in: " + block


def test_a_restored_page_is_not_left_with_the_skeleton_stacked_above_it(client):
    """`pageshow` cleaned up `.doc` only, so going back left `#page`'s skeleton lines in place."""
    js = client.get("/static/site.js").text
    swept = re.search(r"querySelectorAll\('([^']*skel-lines[^']*)'\)", js)
    assert swept, "nothing removes a leftover skeleton"
    assert "#page" in swept.group(1), swept.group(1)


def test_the_script_never_falls_through_to_the_document_body(client):
    js = client.get("/static/site.js").text
    assert "document.body" not in js.split("startLoading")[1].split("}")[0]
    assert "getElementById('page')" in js


def test_the_shimmer_is_dropped_for_reduced_motion(client):
    css = client.get("/static/site.css").text
    assert "@media (prefers-reduced-motion:reduce)" in css and "animation:none" in css


def test_the_footer_links_to_the_public_repository(client):
    """The inverse of what this test asserted while the repository was private (#102)."""
    from leftbrain import __repo__

    html = client.get("/", headers={"Accept": "text/html"}).text
    assert f'href="{__repo__}"' in html and f'href="{__repo__}/issues"' in html
