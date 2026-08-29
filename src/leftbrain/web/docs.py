"""Markdown docs with `:::` containers: `:::os` tabs and `:::request`/`:::response` examples."""

from __future__ import annotations

import re
from functools import lru_cache
from html import escape
from pathlib import Path

from markdown_it import MarkdownIt

DOCS_DIR = Path(__file__).parent / "docs"
PAGES: list[tuple[str, str]] = [
    ("quickstart", "Quickstart"),
    ("clients", "MCP clients"),
    ("custom-agents", "Custom agents"),
    # A slug with a slash needs the `{slug:path}` route in views. `load_page` looks the slug
    # up in this list before touching the filesystem, so the allowlist is what keeps a
    # path-shaped slug from reaching anywhere it should not.
    ("agents/auth", "Auth, for agents"),
    ("tools", "Tools"),
    ("changelog", "Changelog"),
]
# Pages whose single source of truth is a file at the repo root. The wheel gets a copy
# under docs/ (see [tool.hatch.build.targets.wheel.force-include] in pyproject.toml); a
# dev checkout has no copy, so read the original instead of shipping a second one.
ROOT_SOURCES: dict[str, Path] = {"changelog": Path(__file__).resolve().parents[3] / "CHANGELOG.md"}
OS_LABELS = [("windows", "Windows · PowerShell"), ("macos", "macOS"), ("linux", "Linux")]
# `:::request` / `:::response` / `:::command` containers: css class, noun, and the note that
# says which way the example runs. `:::request tools/call` replaces the note with its argument.
IO_KINDS: dict[str, tuple[str, str, str]] = {
    "request": ("io-req", "Request", "you send this"),
    "response": ("io-res", "Response", "you get this back"),
    "command": ("io-cmd", "Command", "run in your terminal"),
}
# Every key in every example is written as this literal, so one replace personalises a page.
KEY_PLACEHOLDER = "lblz_YOUR_KEY"
ANON_KEY = "lblz_…"  # what a reader without a key of their own sees instead

_md = MarkdownIt("commonmark", {"html": True, "linkify": False}).enable("table")
# Section headings inside a `:::os` container are split at top level only (this regex
# is not fence-aware). Our own docs content never puts "### windows"/"### macos"/
# "### linux" inside a fenced code block, so this is safe in practice; keep section
# headings outside fences if you add new `:::os` content.
_OS_SECTION = re.compile(r"^### (windows|macos|linux)\s*$", re.M)
# A container opener: `:::os`, or `:::request` / `:::response` / `:::command` with an
# optional label argument (`:::request tools/call`).
_OPEN = re.compile(r"^:::(os|request|response|command)(?:[ \t]+(\S.*?))?[ \t]*$")


def _is_fence_marker(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def _split_containers(text: str) -> list[tuple[str, str, str]]:
    """Split text into ("md", "", chunk) / (kind, arg, inner) segments, fence-aware.

    A `:::os` / `:::request` / `:::response` / `:::command` line (recognized only when
    not inside a fence) opens a container; it closes at the matching `:::` line that is
    also outside a fence — matching, because containers nest, so a `:::request` may wrap
    a `:::os` block. Fences are tracked with a simple "a line starting with ``` or ~~~
    toggles fence state" rule — enough for our own docs content, though not a full
    CommonMark fence-length match.

    An unterminated container (no matching `:::` before EOF) fails open: its opening
    line and everything after it are treated as plain markdown, not a container.
    """
    lines = text.split("\n")
    n = len(lines)
    segments: list[tuple[str, str, str]] = []
    buf: list[str] = []
    in_fence = False
    i = 0
    while i < n:
        line = lines[i]
        opened = None if in_fence else _OPEN.match(line.strip())
        if opened is not None:
            end = None
            inner_fence = False
            depth = 0
            j = i + 1
            while j < n:
                stripped = lines[j].strip()
                if not inner_fence:
                    if stripped == ":::":
                        if depth == 0:
                            end = j
                            break
                        depth -= 1
                    elif _OPEN.match(stripped):
                        depth += 1
                if _is_fence_marker(lines[j]):
                    inner_fence = not inner_fence
                j += 1
            if end is not None:
                if buf:
                    segments.append(("md", "", "\n".join(buf)))
                    buf = []
                segments.append((opened.group(1), opened.group(2) or "", "\n".join(lines[i + 1 : end])))
                i = end + 1
                continue
            # unterminated: fail open — fall through, treat this line as plain text
        if _is_fence_marker(line):
            in_fence = not in_fence
        buf.append(line)
        i += 1
    if buf:
        segments.append(("md", "", "\n".join(buf)))
    return segments


def _render_os_block(inner: str) -> str:
    parts = _OS_SECTION.split(inner)  # ['', 'windows', body, 'macos', body, ...]
    sections = {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    tabs = "".join(f'<button type="button" data-os="{k}" aria-pressed="{"true" if i == 0 else "false"}">{label}</button>' for i, (k, label) in enumerate(OS_LABELS))
    blocks = "".join(f'<div class="os-block" data-os="{k}"><h4>{label}</h4>{render_markdown(sections.get(k, ""))}</div>' for k, label in OS_LABELS)
    return f'<div class="os"><div class="ostabs">{tabs}</div>{blocks}</div>\n'


def _render_io_block(kind: str, arg: str, inner: str) -> str:
    """One request/response/command example: a labelled bar above the code it describes."""
    css, noun, note = IO_KINDS[kind]
    label = f"{noun} · {escape(arg) if arg else note}"
    return f'<div class="io {css}"><span class="io-label">{label}</span>{render_markdown(inner)}</div>\n'


def render_markdown(text: str) -> str:
    out = []
    for kind, arg, chunk in _split_containers(text):
        if kind == "md":
            out.append(_md.render(chunk))
        elif kind == "os":
            out.append(_render_os_block(chunk))
        else:
            out.append(_render_io_block(kind, arg, chunk))
    return "".join(out)


def fill_defaults(html: str) -> str:
    """Quote the free tier's actual limits: ``{{daily_quota}}`` (grouped) and ``{{daily_quota_raw}}``."""
    from ..keys import (  # read at request time, so a configured default shows
        DEFAULT_DAILY,
        DEFAULT_RPM,
    )

    return html.replace("{{daily_quota_raw}}", str(DEFAULT_DAILY)).replace("{{daily_quota}}", f"{DEFAULT_DAILY:,}").replace("{{rpm}}", str(DEFAULT_RPM))


def fill_key(html: str, key: str | None) -> str:
    """Put the reader's own key into every example, or the anonymous placeholder."""
    return html.replace(KEY_PLACEHOLDER, escape(key) if key else ANON_KEY)


def page_source(slug: str) -> Path | None:
    """The markdown file behind a page: the shipped copy, else its repo-root original."""
    path = DOCS_DIR / f"{slug}.md"
    if path.is_file():
        return path
    root = ROOT_SOURCES.get(slug)
    return root if root is not None and root.is_file() else None


@lru_cache(maxsize=32)
def load_page(slug: str) -> tuple[str, str] | None:
    if slug == "tools":
        from ..toolref import index_page  # local import: toolref imports render_markdown from here

        return index_page()
    title = dict(PAGES).get(slug)
    path = page_source(slug) if title else None
    if not title or path is None:
        return None
    return title, render_markdown(path.read_text(encoding="utf-8"))
