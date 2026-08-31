"""The per-key tool scope renders as a tree: a row per tool, its modes as children.

It was a three-column grid of blocks whose columns ran to wildly different heights, so a
tool's modes wrapped into a shape that had nothing to do with the tool above it.
"""

import pytest
from jinja2 import Environment, FileSystemLoader

from leftbrain.scopes import CATALOGUE, parse_scope

TEMPLATES = "src/leftbrain/web/templates"


@pytest.fixture
def render():
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=True)
    tmpl = env.get_template("_scope_grid.html")
    catalogue = {t: list(m or []) for t, m in CATALOGUE.items()}
    return lambda scope: tmpl.render(catalogue=catalogue, scope_of=scope)


def test_one_node_per_tool(render):
    html = render(None)
    assert html.count("data-node") == len(CATALOGUE)


def test_a_tool_with_modes_gets_a_twisty_and_a_count(render):
    html = render(None)
    with_modes = sum(1 for m in CATALOGUE.values() if m)
    assert html.count("data-twisty") == with_modes
    assert html.count("data-count") == with_modes


def test_a_tool_with_no_modes_gets_a_spacer_so_the_rows_line_up(render):
    html = render(None)
    assert html.count('class="twisty spacer"') == sum(1 for m in CATALOGUE.values() if not m)


def test_the_count_is_rendered_by_the_server_not_only_by_script(render):
    """A key's scope has to be readable with JavaScript off."""
    html = render(parse_scope("math,finance:emi+gst"))
    assert f">2/{len(CATALOGUE['finance'])}<" in html, "finance has two of its modes"
    assert f">{len(CATALOGUE['math'])}/{len(CATALOGUE['math'])}<" in html


def test_a_tool_outside_the_scope_is_unticked_and_its_modes_disabled(render):
    html = render(parse_scope("math"))
    text_row = html.split('value="text" data-tool="text"')[1].split(">")[0]
    assert "checked" not in text_row
    assert html.split('data-of="text"')[1].split(">")[0].strip().endswith("disabled")


def test_everything_is_ticked_when_the_key_has_no_scope(render):
    html = render(None)
    assert " disabled" not in html
    modes = sum(len(m or []) for m in CATALOGUE.values())
    assert html.count("checked") == modes + len(CATALOGUE)


def test_the_header_offers_all_none_and_expand(render):
    html = render(None)
    for control in ("data-all", "data-none", "data-expand", "data-scope-count"):
        assert control in html, control


def test_it_announces_itself_as_a_tree(render):
    html = render(None)
    assert 'role="tree"' in html and html.count('role="treeitem"') == len(CATALOGUE)
    assert html.count('role="group"') == sum(1 for m in CATALOGUE.values() if m)
