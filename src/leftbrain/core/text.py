"""text - counting, regex, diffs, sorting, dedupe and extraction, done exactly."""

from __future__ import annotations

import difflib
import re
from typing import Any

from ..contract import TooLarge, ToolError, Unsupported, check_params, ok, tool

MODES = ("count", "regex_match", "regex_replace", "diff", "sort", "dedupe", "extract", "find", "similarity")

#: What each mode reads. Anything else in a call is a caller's mistake, not a default
#: to fall back on (#28 SS2a). Kept honest by tests/test_mode_params.py, which derives
#: the same map from the code and fails when the two drift.
MODE_PARAMS: dict[str, frozenset[str]] = {
    "count": frozenset({"case_sensitive", "needle", "overlapping", "substring", "text", "what"}),
    "regex_match": frozenset({"flags", "limit", "pattern", "text"}),
    "regex_replace": frozenset({"count", "flags", "pattern", "replacement", "text"}),
    "diff": frozenset({"a", "b", "by", "granularity", "text"}),
    "sort": frozenset({"case_insensitive", "items", "key", "natural", "order"}),
    "dedupe": frozenset({"case_insensitive", "items", "key", "normalize_whitespace"}),
    "extract": frozenset({"kinds", "text", "unique", "what"}),
    "find": frozenset({"case_sensitive", "context", "needle", "query", "substring", "text"}),
    "similarity": frozenset({"a", "b", "case_insensitive", "items", "limit", "normalize_whitespace", "text"}),
}

_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE, "u": re.UNICODE, "a": re.ASCII}
_MAX_TEXT = 2_000_000
#: Characters a replacement may produce. `a`*10000 -> `x`*1000 each is 10 MB (#28 SS2e).
MAX_OUTPUT_CHARS = 200_000
#: Lines, words or characters per side that `diff` will compare. difflib is quadratic:
#: 100 000 lines never returns, and 30 000 words is worse still (#28 SS1).
MAX_DIFF_UNITS = 10_000
#: Matches `regex_match` will scan for before it stops counting.
MAX_MATCH_SCAN = 1_000_000


def _text(p: dict[str, Any], key: str = "text") -> str:
    t = p.get(key)
    if t is None:
        raise ToolError(f"'{key}' is required")
    if not isinstance(t, str):
        t = str(t)
    if len(t) > _MAX_TEXT:
        raise ToolError(f"{key} too long (> {_MAX_TEXT} chars)")
    return t


# --------------------------------------------------------------------------- #
# Catastrophic backtracking (#28 SS1)
#
# `(a+)+$` over `"a"*40 + "b"` makes stdlib `re` try every way of splitting the a's
# between the inner and outer quantifier - 2^40 of them. It cannot be interrupted: `sre`
# is a C loop that never reaches a bytecode boundary, so no signal and no async exception
# is delivered until it finishes, which it does not. The one place to act is before the
# pattern is compiled, so the shapes that cause it are recognised and refused.
#
# RE2 was the plan (issue SS1 step 4) and was measured instead of adopted: it defines `\w`,
# `\d` and `\b` as ASCII, so `\w+` over "hello" returns ['h', 'llo'] where `re` returns
# ['hello']. Swapping the engine underneath callers would silently change answers on
# ordinary patterns over non-ASCII text, which is the one thing this project must not do.
# --------------------------------------------------------------------------- #

_UNBOUNDED = ("*", "+")


def _groups(pattern: str) -> list[tuple[int, str]]:
    """(closing index, body) for every group, skipping escapes and character classes."""
    out: list[tuple[int, str]] = []
    stack: list[int] = []
    i, n, in_class = 0, len(pattern), False
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if in_class:
            if ch == "]":
                in_class = False
            i += 1
            continue
        if ch == "[":
            in_class = True
        elif ch == "(":
            stack.append(i)
        elif ch == ")" and stack:
            start = stack.pop()
            out.append((i, pattern[start + 1 : i]))
        i += 1
    return out


def _open_ended(quantifier: str) -> bool:
    """`{2,}` is unbounded; `{2,4}` is not."""
    inner = quantifier[1:-1]
    return "," in inner and inner.split(",")[1].strip() == ""


def _quantifier_after(pattern: str, close: int) -> str | None:
    """The unbounded quantifier applied to the group closing at `close`, if there is one."""
    rest = pattern[close + 1 :]
    if not rest:
        return None
    if rest[0] in _UNBOUNDED:
        return rest[0]
    if rest.startswith("{"):
        end = rest.find("}")
        if end > 0 and _open_ended(rest[: end + 1]):
            return rest[: end + 1]
    return None


def _body(raw: str) -> str:
    """The pattern inside a group, with `?:`, `?P<name>` and inline flags removed."""
    if not raw.startswith("?"):
        return raw
    if raw.startswith("?P<"):
        end = raw.find(">")
        return raw[end + 1 :] if end > 0 else raw
    end = raw.find(":")
    return raw[end + 1 :] if end > 0 else raw


def _branches(body: str) -> list[str]:
    """Top-level alternatives, so `a|(b|c)` is two branches rather than three."""
    out: list[str] = []
    cur: list[str] = []
    depth, i, n, in_class = 0, 0, len(body), False
    while i < n:
        ch = body[i]
        if ch == "\\":
            cur.append(body[i : i + 2])
            i += 2
            continue
        if in_class:
            if ch == "]":
                in_class = False
        elif ch == "[":
            in_class = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "|" and depth == 0:
            out.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def _has_unbounded(body: str) -> bool:
    i, n, in_class = 0, len(body), False
    while i < n:
        ch = body[i]
        if ch == "\\":
            i += 2
            continue
        if in_class:
            if ch == "]":
                in_class = False
            i += 1
            continue
        if ch == "[":
            in_class = True
        elif ch in _UNBOUNDED:
            return True
        elif ch == "{":
            end = body.find("}", i)
            if end > 0 and _open_ended(body[i : end + 1]):
                return True
        i += 1
    return False


def _atoms(branch: str) -> int:
    """Roughly how many things are concatenated, so `a?` is one and `ab?` is two."""
    count, i, n, in_class = 0, 0, len(branch), False
    while i < n:
        ch = branch[i]
        if ch == "\\":
            count += 1
            i += 2
            continue
        if in_class:
            if ch == "]":
                in_class = False
                count += 1
            i += 1
            continue
        if ch == "[":
            in_class = True
        elif ch == "{":
            end = branch.find("}", i)
            i = end if end > 0 else i
        elif ch not in "*+?()":
            count += 1
        i += 1
    return count


def _nullable(branch: str) -> bool:
    """True when the branch can match nothing - the classic way a quantifier runs away."""
    if not branch:
        return True
    return branch[-1] in ("*", "?") and len(_branches(branch)) == 1 and _atoms(branch) == 1


def redos_risk(pattern: str) -> str | None:
    """Why `pattern` can backtrack exponentially, or ``None`` when it cannot.

    Three shapes, each of which gives the engine two ways to consume the same characters:
    a quantified group that is itself unbounded, one that can match nothing, and a
    quantified alternation whose branches overlap.
    """
    for close, raw in _groups(pattern):
        quantifier = _quantifier_after(pattern, close)
        if quantifier is None:
            continue
        body = _body(raw)
        shown = f"({body}){quantifier}"
        branches = _branches(body)
        if len(branches) == 1:
            if _has_unbounded(body):
                return f"{shown} applies a quantifier to a group that is already unbounded"
            if _nullable(body):
                return f"{shown} applies a quantifier to a group that can match nothing"
            continue
        if any(_nullable(b) for b in branches):
            return f"{shown} quantifies an alternation with a branch that can match nothing"
        for i, a in enumerate(branches):
            for b in branches[i + 1 :]:
                if a and b and (a.startswith(b) or b.startswith(a)):
                    return f"{shown} quantifies an alternation whose branches overlap ({a!r} and {b!r})"
    return None


def check_pattern(pattern: str, *, where: str = "pattern") -> None:
    """Refuse a pattern that would backtrack exponentially. Raises; returns nothing."""
    risk = redos_risk(pattern)
    if risk is None:
        return
    raise Unsupported(
        f"{where} {pattern!r} can backtrack exponentially, so it is refused rather than run: {risk}",
        details={"pattern": pattern, "reason": risk},
        hint="Rewrite the inner quantifier away - `(a+)+` is `a+`, `(a|aa)+` is `a+` - or match a bounded number of times.",
    )


def _compile(pattern: Any, flags: Any) -> re.Pattern[str]:
    if not pattern:
        raise ToolError("'pattern' is required")
    f = 0
    for ch in str(flags or ""):
        if ch.lower() not in _FLAGS:
            raise ToolError(f"unknown regex flag {ch!r}")
        f |= _FLAGS[ch.lower()]
    check_pattern(str(pattern))
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
    total = 0
    for m in rx.finditer(t):
        total += 1
        if len(matches) < limit:
            matches.append({"match": m.group(0), "start": m.start(), "end": m.end(), "groups": list(m.groups()), "named": m.groupdict()})
        if total >= MAX_MATCH_SCAN:
            break
    # `count` is the total, not the number returned: an agent reading `count` off a
    # truncated response used to get the limit and believe it was the answer (#28 SS2f).
    truncated = total > len(matches)
    return ok(
        {"count": total, "returned": len(matches), "truncated": truncated, "matches": matches, "any": bool(matches), "full_match": rx.fullmatch(t) is not None},
        warnings=[f"{total:,} matches found; the list is truncated to the first {limit:,} (raise 'limit' to see more)"] if truncated else [],
    )


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
    if len(out) > MAX_OUTPUT_CHARS:
        raise TooLarge(
            f"the replacement produces {len(out):,} characters; the limit is {MAX_OUTPUT_CHARS:,}",
            details={"output_chars": len(out), "limit_chars": MAX_OUTPUT_CHARS, "replacements": n},
            hint="Shorten 'replacement', or replace over a smaller piece of text.",
        )
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
    biggest = max(len(al), len(bl))
    if biggest > MAX_DIFF_UNITS:
        raise TooLarge(
            f"{biggest:,} {mode}s to compare; the limit is {MAX_DIFF_UNITS:,}",
            details={mode + "s": biggest, "limit": MAX_DIFF_UNITS, "granularity": mode},
            hint="Diff a smaller section, or use granularity='line' on shorter input.",
        )
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


_MAX_SIMILARITY_LEN = 5000


def levenshtein(a: str, b: str) -> int:
    """Edit distance by codepoint, two-row dynamic programming."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _sim_pair(a: str, b: str) -> dict[str, Any]:
    d = levenshtein(a, b)
    longest = max(len(a), len(b))
    return {"levenshtein": d, "ratio": round(1 - d / longest, 4) if longest else 1.0, "equal": d == 0, "max_len": longest}


def _similarity(p: dict[str, Any]) -> dict[str, Any]:
    ci = p.get("case_insensitive", True)
    ws = p.get("normalize_whitespace", True)

    def norm(s: Any) -> str:
        s = str(s)
        s = " ".join(s.split()) if ws else s
        return s.casefold() if ci else s

    assumptions = [f"case-{'in' if ci else ''}sensitive", "whitespace normalised" if ws else "exact whitespace"]
    items = p.get("items")
    if items is not None:
        if not isinstance(items, list) or not items:
            raise ToolError("'items' must be a non-empty list of candidates")
        query = p.get("text") if "text" in p else p.get("a")
        if query is None:
            raise ToolError("'text' (the input to match) is required with 'items'")
        q = norm(query)
        if len(q) > _MAX_SIMILARITY_LEN or any(len(norm(x)) > _MAX_SIMILARITY_LEN for x in items):
            raise ToolError(f"strings longer than {_MAX_SIMILARITY_LEN} characters are not compared")
        scored = [{"index": i, "value": x, **_sim_pair(q, norm(x))} for i, x in enumerate(items)]
        ranked = sorted(scored, key=lambda r: (-r["ratio"], r["levenshtein"], r["index"]))
        limit = int(p.get("limit", 5))
        return ok({"best": ranked[0], "ranked": ranked[:limit], "candidates": len(items)}, assumptions=assumptions)
    a, b = p.get("a"), p.get("b")
    if a is None or b is None:
        raise ToolError("similarity needs 'a' and 'b', or 'text' and 'items'")
    a, b = norm(a), norm(b)
    if len(a) > _MAX_SIMILARITY_LEN or len(b) > _MAX_SIMILARITY_LEN:
        raise ToolError(f"strings longer than {_MAX_SIMILARITY_LEN} characters are not compared")
    return ok(_sim_pair(a, b), assumptions=assumptions)


@tool
def text(mode: str = "count", **params: Any) -> dict[str, Any]:
    """Text utilities. Modes: count, regex_match, regex_replace, diff, sort, dedupe, extract, find, similarity."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    check_params("text", mode, p, MODE_PARAMS)
    return {"count": _count, "regex_match": _regex_match, "regex_replace": _regex_replace, "diff": _diff, "sort": _sort, "dedupe": _dedupe, "extract": _extract, "find": _find, "similarity": _similarity}[mode](p)

#: Shared fixture for the documented examples below.
_EX_LOG_A = "2025-08-26 09:00 INFO started\n2025-08-26 09:05 WARN retrying\n2025-08-26 09:06 INFO ready"

#: Shared fixture for the documented examples below.
_EX_LOG_B = "2025-08-26 09:00 INFO started\n2025-08-26 09:05 ERROR timed out\n2025-08-26 09:06 INFO ready\n2025-08-26 09:07 INFO done"

#: Shared fixture for the documented examples below.
_EX_CONTACT = "Ping ops@example.com or billing@mailinator.com, docs at https://leftbrain.dev/docs, invoice ₹1,25,000 due 2025-09-15, GST 19ABCDE1234F1ZX, call +91 98765 43210. #urgent @sayantan"

#: Worked examples for the reference page, one list per mode. Every one of them is
#: executed when /docs/tools/text is built and sorted by the result into
#: "Examples" (the call succeeded) and "Fails when" (it did not), so a fixture never
#: states an expectation of its own. Mark anything whose output depends on the
#: current instant with "volatile": True.
EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "count": [
        {
            "caption": "How many `r` in strawberry — counted, with positions.",
            "args": {"mode": "count", "text": "strawberry", "what": "occurrences", "substring": "r"},
        },
        {
            "caption": "Codepoints versus bytes: a family emoji is one glyph, seven codepoints and 25 bytes.",
            "args": {"mode": "count", "text": "Café 👨‍👩‍👧‍👦"},
        },
        {
            "caption": "Overlapping matches, which a plain `count()` misses.",
            "args": {"mode": "count", "text": "aaaa", "what": "occurrences", "substring": "aa", "overlapping": True},
        },
        {
            "caption": "Just one statistic.",
            "args": {"mode": "count", "text": _EX_LOG_A, "what": "lines"},
        },
        {
            "caption": "An unknown statistic lists the valid ones.",
            "args": {"mode": "count", "text": "abc", "what": "vowels"},
        },
        {
            "caption": "Counting occurrences needs a `substring`.",
            "args": {"mode": "count", "text": "abc", "what": "occurrences"},
        },
    ],
    "regex_match": [
        {
            "caption": "A pattern that backtracks exponentially is refused before it runs - this one would never return.",
            "args": {"mode": "regex_match", "text": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaab", "pattern": "(a+)+$"},
        },
        {
            "caption": "Every four-digit run in a line.",
            "args": {"mode": "regex_match", "text": "Order 1234 shipped 2025-08-26 to PIN 560001", "pattern": "\\d{4}"},
        },
        {
            "caption": "Named groups come back separately.",
            "args": {"mode": "regex_match", "text": "2025-08-26", "pattern": "(?P<year>\\d{4})-(?P<month>\\d{2})-(?P<day>\\d{2})"},
        },
        {
            "caption": "Case-insensitive matching with a flag.",
            "args": {"mode": "regex_match", "text": "Error: ERROR while erroring", "pattern": "error", "flags": "i"},
        },
        {
            "caption": "A pattern that does not compile, with the position of the problem.",
            "args": {"mode": "regex_match", "text": "abc", "pattern": "([a-z"},
        },
        {
            "caption": "An unknown flag letter.",
            "args": {"mode": "regex_match", "text": "abc", "pattern": "a", "flags": "z"},
        },
    ],
    "regex_replace": [
        {
            "caption": "Masking digits.",
            "args": {"mode": "regex_replace", "text": "call 98765 43210 now", "pattern": "\\d", "replacement": "#"},
        },
        {
            "caption": "Reordering with backreferences.",
            "args": {"mode": "regex_replace", "text": "2025-08-26", "pattern": "(\\d{4})-(\\d{2})-(\\d{2})", "replacement": "\\3/\\2/\\1"},
        },
        {
            "caption": "Only the first two, and the count proves it.",
            "args": {"mode": "regex_replace", "text": "a a a a", "pattern": "a", "replacement": "b", "count": 2},
        },
        {
            "caption": "`replacement` is required — an empty string is fine, but it must be given.",
            "args": {"mode": "regex_replace", "text": "abc", "pattern": "a"},
        },
        {
            "caption": "A backreference to a group that does not exist.",
            "args": {"mode": "regex_replace", "text": "abc", "pattern": "a", "replacement": "\\9"},
        },
        {
            "caption": "A pattern that does not compile.",
            "args": {"mode": "regex_replace", "text": "abc", "pattern": "a(", "replacement": "x"},
        },
    ],
    "diff": [
        {
            "caption": "A line diff, with the unified patch included.",
            "args": {"mode": "diff", "a": _EX_LOG_A, "b": _EX_LOG_B},
        },
        {
            "caption": "A word-level diff of a single sentence.",
            "args": {"mode": "diff", "a": "the quick brown fox", "b": "the quiet brown dog", "granularity": "word"},
        },
        {
            "caption": "An unknown granularity.",
            "args": {"mode": "diff", "a": "x", "b": "y", "granularity": "sentence"},
        },
    ],
    "sort": [
        {
            "caption": "Natural ordering: `file2` before `file10`.",
            "args": {"mode": "sort", "items": ["file10.txt", "file2.txt", "File1.txt", "file20.txt"]},
        },
        {
            "caption": "Sorting objects by a field, descending.",
            "args": {"mode": "sort", "items": [{"n": "b", "v": 2}, {"n": "a", "v": 10}, {"n": "c", "v": 7}], "key": "v", "order": "desc"},
        },
        {
            "caption": "Turning natural ordering off gives plain lexicographic order.",
            "args": {"mode": "sort", "items": ["file10.txt", "file2.txt"], "natural": False},
        },
        {
            "caption": "`items` must be a list.",
            "args": {"mode": "sort", "items": "a,b,c"},
        },
    ],
    "dedupe": [
        {
            "caption": "Case and whitespace variations collapsed, with each duplicate traced back.",
            "args": {"mode": "dedupe", "items": ["Apple", "  apple ", "APPLE", "banana", "banana"], "case_insensitive": True},
        },
        {
            "caption": "Deduping records on one field.",
            "args": {"mode": "dedupe", "items": [{"id": 1, "n": "a"}, {"id": 2, "n": "b"}, {"id": 1, "n": "c"}], "key": "id"},
        },
        {
            "caption": "`items` must be a list.",
            "args": {"mode": "dedupe", "items": {"a": 1}},
        },
    ],
    "extract": [
        {
            "caption": "A few specific kinds.",
            "args": {"mode": "extract", "text": _EX_CONTACT, "what": ["emails", "urls", "money"]},
        },
        {
            "caption": "Everything the library knows about, in one pass.",
            "args": {"mode": "extract", "text": _EX_CONTACT},
        },
        {
            "caption": "An unknown kind lists the valid ones.",
            "args": {"mode": "extract", "text": "abc", "what": "vehicles"},
        },
    ],
    "find": [
        {
            "caption": "Case-insensitive search with line numbers.",
            "args": {"mode": "find", "text": _EX_LOG_B, "substring": "info", "context": 12},
        },
        {
            "caption": "The same search, case-sensitive, finds fewer.",
            "args": {"mode": "find", "text": _EX_LOG_B, "substring": "info", "case_sensitive": True},
        },
        {
            "caption": "`text` is required.",
            "args": {"mode": "find", "substring": "abc"},
        },
    ],
    "similarity": [
        {
            "caption": "Edit distance and a 0–1 ratio between two strings.",
            "args": {"mode": "similarity", "a": "kitten", "b": "sitting"},
        },
        {
            "caption": "Mapping what a user typed onto a menu: the best match, and the runners-up.",
            "args": {"mode": "similarity", "text": "bengaluru", "items": ["Mumbai", "Bangalore", "Bengaluru", "Bengal", "Chennai"], "limit": 3},
        },
        {
            "caption": "Case is folded by default; switch it off to count the case change.",
            "args": {"mode": "similarity", "a": "Delhi", "b": "delhi", "case_insensitive": False},
        },
        {
            "caption": "One side only.",
            "args": {"mode": "similarity", "a": "kitten"},
        },
    ],
}
