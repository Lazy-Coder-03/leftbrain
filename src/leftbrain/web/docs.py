"""Markdown docs with an `:::os` block that renders Windows / macOS / Linux tabs."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from markdown_it import MarkdownIt

DOCS_DIR = Path(__file__).parent / "docs"
PAGES: list[tuple[str, str]] = [("quickstart", "Quickstart"), ("clients", "MCP clients")]
OS_LABELS = [("windows", "Windows · PowerShell"), ("macos", "macOS"), ("linux", "Linux")]

_md = MarkdownIt("commonmark", {"html": True, "linkify": False}).enable("table")
_OS_BLOCK = re.compile(r"^:::os\s*\n(.*?)^:::\s*$", re.S | re.M)
_OS_SECTION = re.compile(r"^### (windows|macos|linux)\s*$", re.M)


def _render_os_block(inner: str) -> str:
    parts = _OS_SECTION.split(inner)  # ['', 'windows', body, 'macos', body, ...]
    sections = {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    tabs = "".join(f'<button type="button" data-os="{k}" aria-pressed="{"true" if i == 0 else "false"}">{label}</button>' for i, (k, label) in enumerate(OS_LABELS))
    blocks = "".join(f'<div class="os-block" data-os="{k}"><h4>{label}</h4>{_md.render(sections.get(k, ""))}</div>' for k, label in OS_LABELS)
    return f'<div class="os"><div class="ostabs" role="tablist">{tabs}</div>{blocks}</div>\n'


def render_markdown(text: str) -> str:
    out: list[str] = []
    pos = 0
    for m in _OS_BLOCK.finditer(text):
        out.append(_md.render(text[pos : m.start()]))
        out.append(_render_os_block(m.group(1)))
        pos = m.end()
    out.append(_md.render(text[pos:]))
    return "".join(out)


@lru_cache(maxsize=32)
def load_page(slug: str) -> tuple[str, str] | None:
    title = dict(PAGES).get(slug)
    path = DOCS_DIR / f"{slug}.md"
    if not title or not path.is_file():
        return None
    return title, render_markdown(path.read_text(encoding="utf-8"))
