"""text - counting, regex, diffs, sorting, dedupe and extraction, done exactly."""

from __future__ import annotations

import difflib
import re
from typing import Any

from ..contract import ToolError, ok, tool

MODES = ("count", "regex_match", "regex_replace", "diff", "sort", "dedupe", "extract", "find")

_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE, "u": re.UNICODE, "a": re.ASCII}
_MAX_TEXT = 2_000_000


def _text(p: dict[str, Any], key: str = "text") -> str:
    t = p.get(key)
    if t is None:
        raise ToolError(f"'{key}' is required")
    if not isinstance(t, str):
        t = str(t)
    if len(t) > _MAX_TEXT:
        raise ToolError(f"{key} too long (> {_MAX_TEXT} chars)")
    return t


def _compile(pattern: Any, flags: Any) -> re.Pattern[str]:
    if not pattern:
        raise ToolError("'pattern' is required")
    f = 0
    for ch in str(flags or ""):
        if ch.lower() not in _FLAGS:
            raise ToolError(f"unknown regex flag {ch!r}")
        f |= _FLAGS[ch.lower()]
    try:
        return re.compile(str(pattern), f)
    except re.error as e:
        raise ToolError(f"invalid regex: {e}") from None


def _count(p: dict[str, Any]) -> dict[str, Any]:
    t = _text(p)
    what = (p.get("what") or "all").lower()
    words = re.findall(r"\b[\w'’-]+\b", t, re.UNICODE)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n{2,}", t.strip()) if s.strip()]
    paragraphs = [x for x in re.split(r"\n\s*\n", t.strip()) if x.strip()]
    stats = {
        "chars": len(t),
        "chars_no_spaces": len(re.sub(r"\s", "", t)),
        "letters": sum(c.isalpha() for c in t),
        "digits": sum(c.isdigit() for c in t),
        "words": len(words),
        "unique_words": len({w.lower() for w in words}),
        "lines": t.count("\n") + 1 if t else 0,
        "non_empty_lines": sum(1 for line in t.splitlines() if line.strip()),
        "sentences": len(sentences),
        "paragraphs": len(paragraphs),
        "bytes_utf8": len(t.encode("utf-8")),
        "tokens_estimate": max(1, round(len(t) / 4)) if t else 0,
    }
    assumptions = ["tokens_estimate ≈ chars/4 (model-specific tokenizers differ)"]
    if what == "all":
        return ok(stats, assumptions=assumptions)
    if what in ("occurrences", "substring", "occurrence"):
        sub = p.get("substring") or p.get("needle")
        if not sub:
            raise ToolError("'substring' is required for occurrences")
        cs = p.get("case_sensitive", True)
        hay, needle = (t, sub) if cs else (t.lower(), sub.lower())
        overlapping = p.get("overlapping", False)
        if overlapping:
            n = len(re.findall(f"(?={re.escape(needle)})", hay))
        else:
            n = hay.count(needle)
        positions = [m.start() for m in re.finditer(f"(?={re.escape(needle)})" if overlapping else re.escape(needle), hay)][:500]
        return ok({"count": n, "substring": sub, "positions": positions}, assumptions=[f"case-{'sensitive' if cs else 'insensitive'}"])
    if what in ("char", "character") and p.get("substring"):
        return ok({"count": t.count(p["substring"])})
    if what in stats:
        return ok({what: stats[what]}, assumptions=assumptions if what == "tokens_estimate" else [])
    if what.rstrip("s") in stats:
        k = what.rstrip("s")
        return ok({k: stats[k]})
    raise ToolError(f"what must be one of all, occurrences, {', '.join(stats)}")


def _regex_match(p: dict[str, Any]) -> dict[str, Any]:
    t = _text(p)
    rx = _compile(p.get("pattern"), p.get("flags"))
    limit = int(p.get("limit", 1000))
    matches = []
    for m in rx.finditer(t):
        matches.append({"match": m.group(0), "start": m.start(), "end": m.end(), "groups": list(m.groups()), "named": m.groupdict()})
        if len(matches) >= limit:
            break
    return ok({"count": len(matches), "matches": matches, "any": bool(matches), "full_match": rx.fullmatch(t) is not None}, warnings=[f"truncated to {limit} matches"] if len(matches) >= limit else [])


def _regex_replace(p: dict[str, Any]) -> dict[str, Any]:
    t = _text(p)
    rx = _compile(p.get("pattern"), p.get("flags"))
    repl = p.get("replacement")
    if repl is None:
        raise ToolError("'replacement' is required")
    count = int(p.get("count", 0))
    try:
        out, n = rx.subn(str(repl), t, count=count)
    except re.error as e:
        raise ToolError(f"bad replacement: {e}") from None
    return ok({"text": out, "replacements": n, "changed": n > 0})


def _diff(p: dict[str, Any]) -> dict[str, Any]:
    a, b = _text(p, "a"), _text(p, "b")
    mode = (p.get("granularity") or p.get("by") or "line").lower()
    if mode == "line":
        al, bl = a.splitlines(), b.splitlines()
    elif mode == "word":
        al, bl = a.split(), b.split()
    elif mode == "char":
        al, bl = list(a), list(b)
    else:
        raise ToolError("granularity must be line, word or char")
    sm = difflib.SequenceMatcher(None, al, bl, autojunk=False)
    ops = []
    added = removed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        joiner = "\n" if mode == "line" else (" " if mode == "word" else "")
        ops.append({"op": tag, "a": joiner.join(al[i1:i2]), "b": joiner.join(bl[j1:j2]), "a_range": [i1, i2], "b_range": [j1, j2]})
        if tag in ("delete", "replace"):
            removed += i2 - i1
        if tag in ("insert", "replace"):
            added += j2 - j1
    unified = "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), "a", "b", lineterm="")) if mode == "line" else None
    return ok({"identical": a == b, "similarity": round(sm.ratio(), 6), "added": added, "removed": removed, "changes": ops[:500], "unified": unified, "granularity": mode}, warnings=["changes truncated to 500"] if len(ops) > 500 else [])


def _natural_key(s: str) -> list[Any]:
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]


def _sort(p: dict[str, Any]) -> dict[str, Any]:
    items = p.get("items")
    if not isinstance(items, list):
        raise ToolError("'items' must be a list")
    key = p.get("key")
    natural = p.get("natural", True)
    ci = p.get("case_insensitive", True)
    reverse = str(p.get("order", "asc")).lower() in ("desc", "descending", "reverse")
    assumptions = []

    def val(x: Any) -> Any:
        if key is not None and isinstance(x, dict):
            return x.get(key)
        return x

    def kf(x: Any) -> Any:
        v = val(x)
        if v is None:
            return (2, "")
        if isinstance(v, bool):
            return (1, int(v))
        if isinstance(v, (int, float)):
            return (0, v)
        s = str(v)
        s = s.casefold() if ci else s
        return (1, _natural_key(s) if natural else [s])

    try:
        srt = sorted(items, key=kf, reverse=reverse)
    except TypeError as e:
        raise ToolError(f"items are not comparable: {e}") from None
    if natural:
        assumptions.append("natural sort (file2 < file10)")
    if ci:
        assumptions.append("case-insensitive")
    if reverse:
        assumptions.append("descending")
    return ok({"sorted": srt, "changed": srt != items, "count": len(srt)}, assumptions=assumptions)


def _dedupe(p: dict[str, Any]) -> dict[str, Any]:
    items = p.get("items")
    if not isinstance(items, list):
        raise ToolError("'items' must be a list")
    ci = p.get("case_insensitive", False)
    ws = p.get("normalize_whitespace", True)
    key = p.get("key")
    seen: dict[Any, int] = {}
    unique, dupes = [], []
    for i, x in enumerate(items):
        v = x.get(key) if (key and isinstance(x, dict)) else x
        k: Any
        if isinstance(v, str):
            k = " ".join(v.split()) if ws else v
            k = k.casefold() if ci else k
        elif isinstance(v, (dict, list)):
            import json

            k = json.dumps(v, sort_keys=True)
        else:
            k = v
        if k in seen:
            dupes.append({"index": i, "value": x, "first_index": seen[k]})
        else:
            seen[k] = i
            unique.append(x)
    return ok({"unique": unique, "removed": len(dupes), "duplicates": dupes[:500], "count": len(unique)}, assumptions=[f"case-{'in' if ci else ''}sensitive", "whitespace normalised" if ws else "exact whitespace"])


_EXTRACTORS = {
    "emails": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "urls": re.compile(r"\b(?:https?://|www\.)[^\s<>\"')\]]+"),
    "phones": re.compile(r"(?<![\w.])(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)?\d{3,5}[\s-]?\d{3,5}(?![\w.])"),
    "numbers": re.compile(r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?%?(?![\w.])"),
    "dates": re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|(?:\d{1,2}\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b", re.I),
    "times": re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm|AM|PM)?\b|\b\d{1,2}\s*(?:am|pm|AM|PM)\b"),
    "hashtags": re.compile(r"(?<!\w)#\w+"),
    "mentions": re.compile(r"(?<!\w)@\w+"),
    "ips": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "money": re.compile(r"(?:₹|\$|€|£|¥|Rs\.?|INR|USD|EUR|GBP)\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|K|L|lakh|lakhs|Cr|crore|crores|M|mn|bn|B))?\b"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "gstin": re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b"),
    "uuids": re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
}


def _extract(p: dict[str, Any]) -> dict[str, Any]:
    t = _text(p)
    what = p.get("what") or p.get("kinds") or "all"
    kinds = list(_EXTRACTORS) if what == "all" else ([what] if isinstance(what, str) else list(what))
    out: dict[str, list[str]] = {}
    for k in kinds:
        if k not in _EXTRACTORS:
            raise ToolError(f"unknown kind {k!r}; options: {', '.join(_EXTRACTORS)}")
        found = [m.group(0) for m in _EXTRACTORS[k].finditer(t)]
        if p.get("unique", True):
            found = list(dict.fromkeys(found))
        out[k] = found
    return ok(out, assumptions=["regex-based extraction; validate ids with the validate tool"])


def _find(p: dict[str, Any]) -> dict[str, Any]:
    t = _text(p)
    needle = p.get("substring") or p.get("needle") or p.get("query")
    if not needle:
        raise ToolError("'substring' is required")
    cs = p.get("case_sensitive", False)
    hay, nd = (t, needle) if cs else (t.lower(), needle.lower())
    hits = []
    ctx = int(p.get("context", 40))
    for m in re.finditer(re.escape(nd), hay):
        s, e = m.start(), m.end()
        line = t.count("\n", 0, s) + 1
        hits.append({"start": s, "end": e, "line": line, "context": t[max(0, s - ctx):e + ctx]})
        if len(hits) >= 200:
            break
    return ok({"count": len(hits), "found": bool(hits), "hits": hits}, assumptions=[f"case-{'sensitive' if cs else 'insensitive'}"])


@tool
def text(mode: str = "count", **params: Any) -> dict[str, Any]:
    """Text utilities. Modes: count, regex_match, regex_replace, diff, sort, dedupe, extract, find."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    return {"count": _count, "regex_match": _regex_match, "regex_replace": _regex_replace, "diff": _diff, "sort": _sort, "dedupe": _dedupe, "extract": _extract, "find": _find}[mode](p)
