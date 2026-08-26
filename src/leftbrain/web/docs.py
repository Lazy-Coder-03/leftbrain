"""Markdown docs with an `:::os` block that renders Windows / macOS / Linux tabs."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from markdown_it import MarkdownIt

from .tools_list import TOOLS

DOCS_DIR = Path(__file__).parent / "docs"
PAGES: list[tuple[str, str]] = [("quickstart", "Quickstart"), ("clients", "MCP clients"), ("tools", "Tools")]
OS_LABELS = [("windows", "Windows · PowerShell"), ("macos", "macOS"), ("linux", "Linux")]

_md = MarkdownIt("commonmark", {"html": True, "linkify": False}).enable("table")
# Section headings inside a `:::os` container are split at top level only (this regex
# is not fence-aware). Our own docs content never puts "### windows"/"### macos"/
# "### linux" inside a fenced code block, so this is safe in practice; keep section
# headings outside fences if you add new `:::os` content.
_OS_SECTION = re.compile(r"^### (windows|macos|linux)\s*$", re.M)


def _is_fence_marker(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def _split_os_containers(text: str) -> list[tuple[str, str]]:
    """Split text into ("md", chunk) / ("os", inner) segments, fence-aware.

    A `:::os` line (recognized only when not inside a fence) opens a container; it
    closes at the next `:::` line that is also outside a fence. Fences are tracked
    with a simple "a line starting with ``` or ~~~ toggles fence state" rule — enough
    for our own docs content, though not a full CommonMark fence-length match.

    An unterminated container (no matching `:::` before EOF) fails open: its `:::os`
    line and everything after it are treated as plain markdown, not a container.
    """
    lines = text.split("\n")
    n = len(lines)
    segments: list[tuple[str, str]] = []
    buf: list[str] = []
    in_fence = False
    i = 0
    while i < n:
        line = lines[i]
        if not in_fence and line.strip() == ":::os":
            end = None
            inner_fence = False
            j = i + 1
            while j < n:
                if not inner_fence and lines[j].strip() == ":::":
                    end = j
                    break
                if _is_fence_marker(lines[j]):
                    inner_fence = not inner_fence
                j += 1
            if end is not None:
                if buf:
                    segments.append(("md", "\n".join(buf)))
                    buf = []
                segments.append(("os", "\n".join(lines[i + 1 : end])))
                i = end + 1
                continue
            # unterminated: fail open — fall through, treat this line as plain text
        if _is_fence_marker(line):
            in_fence = not in_fence
        buf.append(line)
        i += 1
    if buf:
        segments.append(("md", "\n".join(buf)))
    return segments


def _render_os_block(inner: str) -> str:
    parts = _OS_SECTION.split(inner)  # ['', 'windows', body, 'macos', body, ...]
    sections = {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    tabs = "".join(f'<button type="button" data-os="{k}" aria-pressed="{"true" if i == 0 else "false"}">{label}</button>' for i, (k, label) in enumerate(OS_LABELS))
    blocks = "".join(f'<div class="os-block" data-os="{k}"><h4>{label}</h4>{_md.render(sections.get(k, ""))}</div>' for k, label in OS_LABELS)
    return f'<div class="os"><div class="ostabs">{tabs}</div>{blocks}</div>\n'


def render_markdown(text: str) -> str:
    return "".join(_render_os_block(chunk) if kind == "os" else _md.render(chunk) for kind, chunk in _split_os_containers(text))


def _first_mode(modes: str) -> str:
    """Pick the first mode out of a "a · b · c …" modes string, dropping the trailing marker."""
    return modes.rstrip(" …").split(" · ")[0].strip()


def _tools_page_markdown() -> str:
    """Build the "Tools" docs page from the TOOLS list — no markdown file backs this page."""
    parts = [
        "Every tool takes a `mode` and returns the same contract — "
        "`{ok, result, assumptions[], warnings[]}` on success, or `{ok:false, error, needs}` when the "
        "input is ambiguous. Full per-tool reference pages are coming; for now, here is the complete "
        "list of tools and their modes.",
        "",
    ]
    for name, desc, modes in TOOLS:
        clean_modes = modes.rstrip(" …")
        first = _first_mode(modes)
        if name == "numbers":
            example = '{"name":"numbers","arguments":{"mode":"compare","values":["9.11","9.9"]}}'
        else:
            example = f'{{"name":"{name}","arguments":{{"mode":"{first}"}}}}'
        parts.append(f'<h2 id="{name}">{name}</h2>')
        parts.append("")
        parts.append(desc)
        parts.append("")
        parts.append(f"**Modes:** {clean_modes}")
        parts.append("")
        parts.append("```json")
        parts.append(example)
        parts.append("```")
        parts.append("")
    return "\n".join(parts)


@lru_cache(maxsize=32)
def load_page(slug: str) -> tuple[str, str] | None:
    if slug == "tools":
        return "Tools", render_markdown(_tools_page_markdown())
    title = dict(PAGES).get(slug)
    path = DOCS_DIR / f"{slug}.md"
    if not title or not path.is_file():
        return None
    return title, render_markdown(path.read_text(encoding="utf-8"))
