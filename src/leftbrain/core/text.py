"""text - counting, regex, diffs, sorting, dedupe and extraction, done exactly."""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any

from ..contract import TooLarge, ToolError, Unsupported, check_params, flag, ok, tool, whole

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
#: Characters per side that a `char` diff will compare; difflib is quadratic.
MAX_DIFF_CHARS = 3_000
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
#: Ways a quantified group may match the same text before it is refused outright.
MAX_WAYS = 1_000_000
#: Backtracking steps a pattern may need over the text it is given (about a second in `sre`).
MAX_STEPS = 20_000_000
_DIGITS = frozenset("0123456789")
_SPACES = frozenset(" \t\n\r\f\v")
_WORD = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
ANY = "any"


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


def _strip_verbose(pattern: str) -> str:
    """The pattern with `x`-mode comments and whitespace removed, so they cannot hide a shape."""
    out: list[str] = []
    i, n, in_class = 0, len(pattern), False
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            out.append(pattern[i : i + 2])
            i += 2
            continue
        if in_class:
            if ch == "]":
                in_class = False
            out.append(ch)
        elif ch == "[":
            in_class = True
            out.append(ch)
        elif ch == "#":
            while i < n and pattern[i] != "\n":
                i += 1
        elif not ch.isspace():
            out.append(ch)
        i += 1
    return "".join(out)


def _quantifier_at(pattern: str, i: int) -> tuple[str, int]:
    """The quantifier starting at `i` (with a lazy/possessive suffix), and where it ends."""
    if i >= len(pattern):
        return "", i
    ch = pattern[i]
    if ch in "*+?":
        j = i + 1
    elif ch == "{":
        close = pattern.find("}", i)
        if close < 0 or not re.fullmatch(r"\{\d*(?:,\d*)?\}", pattern[i : close + 1]) or pattern[i : close + 1] == "{}":
            return "", i
        j = close + 1
    else:
        return "", i
    if j < len(pattern) and pattern[j] in "?+":
        j += 1
    return pattern[i:j], j


def _bounds(q: str) -> tuple[int, int | None]:
    """(min, max) repetitions a quantifier allows; max None is unbounded."""
    if not q:
        return 1, 1
    q = q.rstrip("?+") if len(q) > 1 and q[0] != "{" else q
    core = q[:-1] if len(q) > 1 and q[-1] in "?+" and q[0] == "{" and q.count("}") == 1 and not q.endswith("}") else q
    if core == "*":
        return 0, None
    if core == "+":
        return 1, None
    if core == "?":
        return 0, 1
    inner = core[1:-1]
    if "," in inner:
        lo, hi = inner.split(",", 1)
        return int(lo or 0), (int(hi) if hi else None)
    return int(inner), int(inner)


def _open_ended(quantifier: str) -> bool:
    """`{2,}` is unbounded; `{2,4}` is not."""
    return _bounds(quantifier)[1] is None


def _quantifier_after(pattern: str, close: int) -> str | None:
    """The unbounded quantifier applied to the group closing at `close`, if there is one."""
    q, _ = _quantifier_at(pattern, close + 1)
    return q if q and _open_ended(q) else None


def _body(raw: str) -> str:
    """The pattern inside a group, with `?:`, `?P<name>`, lookarounds and inline flags removed."""
    if not raw.startswith("?"):
        return raw
    if raw.startswith("?P<"):
        end = raw.find(">")
        return raw[end + 1 :] if end > 0 else raw
    if raw[:2] in ("?=", "?!"):
        return raw[2:]
    if raw[:3] in ("?<=", "?<!"):
        return raw[3:]
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


def _class_set(cls: str) -> Any:
    """The characters a `[...]` class can match, or ANY when it is negated or too rich to enumerate."""
    inner = cls[1:-1]
    if inner.startswith("^"):
        return ANY
    out: set[str] = set()
    i, n = 0, len(inner)
    while i < n:
        ch = inner[i]
        if ch == "\\" and i + 1 < n:
            esc = inner[i + 1]
            got = _escape_set(esc)
            if got is ANY:
                return ANY
            out |= got
            i += 2
            continue
        if i + 2 < n and inner[i + 1] == "-":
            lo, hi = ord(ch), ord(inner[i + 2])
            if hi - lo > 300:
                return ANY
            out |= {chr(c) for c in range(lo, hi + 1)}
            i += 3
            continue
        out.add(ch)
        i += 1
    return out


def _escape_set(esc: str) -> Any:
    if esc == "d":
        return set(_DIGITS)
    if esc == "s":
        return set(_SPACES)
    if esc == "w":
        return set(_WORD)
    if esc in "DSW":
        return ANY
    if esc in "bBAZ":
        return set()  # an anchor consumes nothing
    return {esc}


def _atoms(branch: str) -> list[tuple[Any, int, int | None]]:
    """The atoms of one branch as (first-character set, min, max) - groups as one atom each."""
    out: list[tuple[Any, int, int | None]] = []
    i, n = 0, len(branch)
    while i < n:
        ch = branch[i]
        first: Any
        if ch == "\\":
            first = _escape_set(branch[i + 1]) if i + 1 < n else set()
            i += 2
        elif ch == "[":
            depth_end = i + 1
            if depth_end < n and branch[depth_end] == "^":
                depth_end += 1
            if depth_end < n and branch[depth_end] == "]":
                depth_end += 1
            while depth_end < n and branch[depth_end] != "]":
                depth_end += 2 if branch[depth_end] == "\\" else 1
            first = _class_set(branch[i : depth_end + 1])
            i = depth_end + 1
        elif ch == "(":
            depth, j = 1, i + 1
            while j < n and depth:
                if branch[j] == "\\":
                    j += 2
                    continue
                if branch[j] == "[":
                    k = j + 1
                    while k < n and branch[k] != "]":
                        k += 2 if branch[k] == "\\" else 1
                    j = k + 1
                    continue
                depth += 1 if branch[j] == "(" else (-1 if branch[j] == ")" else 0)
                j += 1
            inner = _body(branch[i + 1 : j - 1])
            first = _first_set(inner)
            i = j
        elif ch in "^$":
            first = set()
            i += 1
        elif ch == ".":
            first = ANY
            i += 1
        else:
            first = {ch}
            i += 1
        q, i = _quantifier_at(branch, i)
        lo, hi = _bounds(q)
        out.append((first, lo, hi))
    return out


def _union(a: Any, b: Any) -> Any:
    return ANY if a is ANY or b is ANY else a | b


def _overlap(a: Any, b: Any) -> bool:
    if a is ANY or b is ANY:
        return True
    return bool(a & b)


def _first_set(body: str) -> Any:
    """Every character a match of `body` can start with (ANY when it cannot be pinned down)."""
    total: Any = set()
    for branch in _branches(body):
        for first, lo, _hi in _atoms(branch):
            total = _union(total, first)
            if lo > 0 and first:  # a required atom that consumes something ends the prefix
                break
    return total


def _min_len(body: str) -> int:
    """The fewest characters one match of `body` consumes."""
    best = None
    for branch in _branches(body):
        n = sum(lo * (1 if first is ANY or first else 0) for first, lo, _hi in _atoms(branch))
        best = n if best is None else min(best, n)
    return best or 0


def _variability(body: str) -> float:
    """How many ways one match of `body` can be laid over the same text: the product of each
    quantifier's range, taken over the most variable branch. Infinite when unbounded.
    Alternation itself is not counted; overlapping branches are judged separately."""
    ways = 1.0
    for branch in _branches(body):
        branch_ways = 1.0
        for _first, lo, hi in _atoms(branch):
            if hi is None:
                return float("inf")
            branch_ways *= hi - lo + 1
        ways = max(ways, branch_ways)
    return ways


def _nullable(branch: str) -> bool:
    """True when the branch can match nothing - the classic way a quantifier runs away."""
    return all(lo == 0 or not first for first, lo, _hi in _atoms(branch)) if branch else True


def _growth(lo: int, hi: int | None, ways: float) -> float:
    """Per-character growth of the ways to tile text with runs of `lo`..`hi` characters."""
    if hi is None or ways == float("inf"):
        return 2.0
    lo = max(lo, 1)
    # the largest root of x^hi = x^(hi-lo) + ... + 1, found by bisection
    a, b = 1.0, 2.0
    for _ in range(40):
        m = (a + b) / 2
        if m**hi - sum(m**k for k in range(hi - lo, hi)) > 0:
            b = m
        else:
            a = m
    return b


def redos_risk(pattern: str) -> str | None:
    """Why `pattern` can backtrack exponentially on some input, or ``None`` when it cannot.

    A quantified group is dangerous when the engine has more than one way to lay it over the
    same characters: a body that is itself unbounded, a body that can match nothing, an
    alternation whose branches can start with the same character, or a body whose own
    quantifiers allow so many layouts that the outer repetition multiplies them past reason.
    """
    pattern = _strip_verbose(pattern) if "#" in pattern or any(c.isspace() for c in pattern) else pattern
    for close, raw in _groups(pattern):
        q, _ = _quantifier_at(pattern, close + 1)
        if not q:
            continue
        lo, hi = _bounds(q)
        if hi is not None and hi < 2:
            continue
        body = _body(raw)
        shown = f"({body}){q}"
        branches = _branches(body)
        ways = _variability(body)
        if hi is None:
            if ways == float("inf"):
                return f"{shown} applies a quantifier to a group that is already unbounded"
            if any(_nullable(b) for b in branches):
                return f"{shown} applies a quantifier to a group that can match nothing"
            if ways > 1 and _min_len(body) <= 1:
                return f"{shown} repeats without bound a group that can be one character or several, which is {int(ways)} layouts per repetition"
        elif ways != float("inf") and ways > 1 and ways**hi > MAX_WAYS:
            return f"{shown} allows about {int(ways)}^{hi} ways to match the same text"
        elif ways == float("inf") and hi > 5:
            return f"{shown} repeats an unbounded group {hi} times, which backtracks as a polynomial of degree {hi}"
        if len(branches) > 1:
            firsts = [_first_set(b) for b in branches]
            for i, a in enumerate(branches):
                for j in range(i + 1, len(branches)):
                    if _overlap(firsts[i], firsts[j]):
                        return f"{shown} quantifies an alternation whose branches overlap ({a!r} and {branches[j]!r})"
    return None


def _polynomial_degree(pattern: str) -> int:
    """How many consecutive unbounded atoms can start with the same character: `a*a*a*b` is 3.

    No group is involved, so the exponential guard says nothing, yet over long text the
    engine walks n^3 positions before giving up.
    """
    degree = 1
    for branch in _branches(pattern):
        run, prev = 1, None
        for first, _lo, hi in _atoms(branch):
            if hi is None and prev is not None and _overlap(prev, first):
                run += 1
            elif hi is None:
                run = 1
            else:
                run = 1
                prev = None
                continue
            prev = first
            degree = max(degree, run)
    return degree


def _slow_growth(pattern: str, text_len: int) -> str | None:
    """A group whose repetition grows only slowly - `(a{2,4})+` - is fine on a line and not on a page."""
    for close, raw in _groups(pattern):
        q, _ = _quantifier_at(pattern, close + 1)
        if not q or _bounds(q)[1] is not None:
            continue
        body = _body(raw)
        ways = _variability(body)
        if ways == float("inf") or ways <= 1:
            continue
        growth = _growth(_min_len(body), max(hi for _f, _lo, hi in _atoms(_branches(body)[0]) if hi is not None) if any(hi is not None for _f, _lo, hi in _atoms(_branches(body)[0])) else None, ways)
        if growth**text_len > MAX_STEPS:
            return f"({body}){q} can be laid over {text_len:,} characters in about {growth:.2f}^{text_len:,} ways"
    return None


def check_pattern(pattern: str, *, where: str = "pattern", text_len: int | None = None) -> None:
    """Refuse a pattern that would backtrack exponentially. Raises; returns nothing.

    With ``text_len`` the polynomial shapes are budgeted too: `a*a*a*b` is harmless on a line
    and never returns over 100,000 characters.
    """
    risk = redos_risk(pattern)
    if risk is None and text_len is not None:
        degree = _polynomial_degree(_strip_verbose(pattern) if "#" in pattern else pattern)
        if degree >= 2 and text_len**degree > MAX_STEPS:
            risk = f"{degree} consecutive unbounded repeats can start with the same character, which is up to {text_len:,}^{degree} steps over {text_len:,} characters"
        else:
            risk = _slow_growth(pattern, text_len)
    if risk is None:
        return
    raise Unsupported(
        f"{where} {pattern!r} can backtrack exponentially, so it is refused rather than run: {risk}",
        details={"pattern": pattern, "reason": risk},
        hint="Rewrite the inner quantifier away - `(a+)+` is `a+`, `(a|aa)+` is `a+` - or match a bounded number of times over less text.",
    )


def _compile(pattern: Any, flags: Any, text_len: int | None = None) -> re.Pattern[str]:
    if not pattern:
        raise ToolError("'pattern' is required")
    f = 0
    for ch in str(flags or ""):
        if ch.lower() not in _FLAGS:
            raise ToolError(f"unknown regex flag {ch!r}")
        f |= _FLAGS[ch.lower()]
    check_pattern(_strip_verbose(str(pattern)) if f & re.VERBOSE else str(pattern), text_len=text_len)
    try:
        return re.compile(str(pattern), f)
    except re.error as e:
        raise ToolError(f"invalid regex: {e}") from None


#: Characters that join what precedes them into one visible cluster.
_JOINERS = "\u200d\ufe0f\ufe0e"
#: Emoji skin-tone modifiers, which attach to the emoji before them.
_MODIFIERS = range(0x1F3FB, 0x1F400)
#: Controls that reorder or hide text without printing anything themselves.
_BIDI_CONTROLS = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
_ZERO_WIDTH = "\u200b\u200c\u200d\ufeff\u2060"


def _graphemes(t: str) -> int:
    """How many characters a reader sees.

    A short UAX #29: a cluster continues across combining marks, zero-width joiners and
    the code point after one, variation selectors, emoji modifiers, and pairs of regional
    indicators. Enough for counting; not a full segmentation library.
    """
    count, i, n = 0, 0, len(t)
    while i < n:
        count += 1
        i += 1
        while i < n:
            ch = t[i]
            if unicodedata.combining(ch) or ch in _JOINERS or ord(ch) in _MODIFIERS:
                i += 1
                if t[i - 1] == "\u200d" and i < n:  # a ZWJ joins whatever follows it
                    i += 1
                continue
            if 0x1F1E6 <= ord(ch) <= 0x1F1FF and 0x1F1E6 <= ord(t[i - 1]) <= 0x1F1FF:
                i += 1  # a flag is two regional indicators
                continue
            break
    return count


def _hidden_characters(t: str) -> list[str]:
    """Controls that change what text looks like without being visible themselves."""
    out = []
    bidi = sorted({ch for ch in t if ch in _BIDI_CONTROLS})
    if bidi:
        out.append(
            "contains bidi control character(s) "
            + ", ".join(f"U+{ord(c):04X}" for c in bidi)
            + ": the text may not read in the order it is stored (a filename spoofing trick)"
        )
    zero = sorted({ch for ch in t if ch in _ZERO_WIDTH and ch != "\u200d"})
    if zero:
        out.append(
            "contains zero-width character(s) "
            + ", ".join(f"U+{ord(c):04X}" for c in zero)
            + ": they are invisible but count towards length and break exact comparison"
        )
    return out


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
        "lines": len(t.splitlines()) if t else 0,  # the same splitting as non_empty_lines; `\r` alone is a line break too
        "non_empty_lines": sum(1 for line in t.splitlines() if line.strip()),
        "sentences": len(sentences),
        "paragraphs": len(paragraphs),
        "bytes_utf8": len(t.encode("utf-8")),
        "tokens_estimate": max(1, round(len(t) / 4)) if t else 0,
        # What a reader sees. A ZWJ family emoji is five code points and one character
        # to anyone looking at it, and `chars: 5` was the answer to a question nobody
        # asked (#28 SS3.13).
        "graphemes": _graphemes(t),
    }
    assumptions = ["tokens_estimate ≈ chars/4 (model-specific tokenizers differ)"]
    hidden = _hidden_characters(t)
    if what == "all":
        return ok(stats, assumptions=assumptions, warnings=hidden)
    if what in ("occurrences", "substring", "occurrence"):
        sub = p.get("substring") or p.get("needle")
        if not sub:
            raise ToolError("'substring' is required for occurrences")
        sub = str(sub)
        cs = flag(p.get("case_sensitive", True), "case_sensitive")
        overlapping = flag(p.get("overlapping", False), "overlapping")
        # matched on the caller's text with IGNORECASE, not on `t.lower()`: lower-casing
        # changes the length of İ, and positions belong to the caller's string
        rx = re.compile(f"(?={re.escape(sub)})" if overlapping else re.escape(sub), 0 if cs else re.IGNORECASE)
        found = [m.start() for m in rx.finditer(t)]
        return ok({"count": len(found), "substring": sub, "positions": found[:500]}, assumptions=[f"case-{'sensitive' if cs else 'insensitive'}"])
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
    rx = _compile(p.get("pattern"), p.get("flags"), len(t))
    limit = whole(p.get("limit", 1000), "limit", lo=1)
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
        {"count": total, "returned": len(matches), "truncated": truncated, "matches": matches, "any": total > 0, "full_match": rx.fullmatch(t) is not None},
        warnings=[f"{total:,} matches found; the list is truncated to the first {limit:,} (raise 'limit' to see more)"] if truncated else [],
    )


def _regex_replace(p: dict[str, Any]) -> dict[str, Any]:
    t = _text(p)
    rx = _compile(p.get("pattern"), p.get("flags"), len(t))
    repl = p.get("replacement")
    if repl is None:
        raise ToolError("'replacement' is required")
    count = whole(p.get("count", 0), "count", lo=0)
    try:
        out, n = rx.subn(str(repl), t, count=count)
    except (re.error, IndexError) as e:  # `\g<nope>` is an IndexError, not a re.error
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
    cap = MAX_DIFF_CHARS if mode == "char" else MAX_DIFF_UNITS
    if biggest > cap:
        raise TooLarge(
            f"{biggest:,} {mode}s to compare; the limit is {cap:,}",
            details={mode + "s": biggest, "limit": cap, "granularity": mode},
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
    warnings = ["changes truncated to 500"] if len(ops) > 500 else []
    if a != b and not ops:
        # `identical: false` with no changes listed needs the reason
        warnings.append("the texts differ only in line endings, trailing newlines or whitespace, which this granularity does not compare")
    return ok({"identical": a == b, "similarity": round(sm.ratio(), 6), "added": added, "removed": removed, "changes": ops[:500], "unified": unified, "granularity": mode}, warnings=warnings)


def _natural_key(s: str) -> list[Any]:
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]


def _sort(p: dict[str, Any]) -> dict[str, Any]:
    items = p.get("items")
    if not isinstance(items, list):
        raise ToolError("'items' must be a list")
    key = p.get("key")
    natural = flag(p.get("natural", True), "natural")
    ci = flag(p.get("case_insensitive", True), "case_insensitive")
    reverse = str(p.get("order", "asc")).lower() in ("desc", "descending", "reverse")
    assumptions = []
    warnings: list[str] = []
    from .collections_ import get_path  # dotted paths, as sort_by reads them

    def val(x: Any) -> Any:
        if key is not None and isinstance(x, dict):
            return get_path(x, key)
        return x

    if key is not None and items and all(val(x) is None for x in items):
        warnings.append(f"no item has a '{key}' key, so the order is unchanged")

    def kf(x: Any) -> Any:
        v = val(x)
        if v is None:
            return (2, "")
        if isinstance(v, bool):
            return (0, int(v))
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
    assumptions.append("ordered by code point, with no locale collation: accented letters sort after z (éclair after Zebra)")
    if ci:
        assumptions.append("case-insensitive")
    if reverse:
        assumptions.append("descending")
    return ok({"sorted": srt, "changed": srt != items, "count": len(srt)}, assumptions=assumptions, warnings=warnings)


def _dedupe(p: dict[str, Any]) -> dict[str, Any]:
    items = p.get("items")
    if not isinstance(items, list):
        raise ToolError("'items' must be a list")
    ci = flag(p.get("case_insensitive", False), "case_insensitive")
    ws = flag(p.get("normalize_whitespace", True), "normalize_whitespace")
    key = p.get("key")
    seen: dict[Any, int] = {}
    unique, dupes = [], []
    missing = 0
    from .collections_ import get_path

    for i, x in enumerate(items):
        v = get_path(x, key) if key else x
        if key and v is None:
            # rows without the key are not duplicates of each other
            missing += 1
            unique.append(x)
            continue
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
    warnings = [f"{missing} of {len(items)} items have no '{key}' and were kept as they are"] if missing else []
    return ok({"unique": unique, "removed": len(dupes), "duplicates": dupes[:500], "count": len(unique)}, assumptions=[f"case-{'in' if ci else ''}sensitive", "whitespace normalised" if ws else "exact whitespace"], warnings=warnings)


_EXTRACTORS = {
    "emails": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "urls": re.compile(r"\b(?:https?://|www\.)[^\s<>\"')\]]+"),
    "phones": re.compile(r"(?<![\w.])(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)?\d{3,5}[\s-]?\d{3,5}(?![\w.])"),
    "numbers": re.compile(r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?%?(?!\w|\.\d)"),  # `3.14.` at the end of a sentence is 3.14
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
    if not isinstance(what, (str, list)):
        raise ToolError(f"'what' must be a kind name or a list of them, not {what!r}")
    kinds = list(_EXTRACTORS) if what == "all" else ([what] if isinstance(what, str) else list(what))
    unique = flag(p.get("unique", True), "unique")
    out: dict[str, list[str]] = {}
    for k in kinds:
        if k not in _EXTRACTORS:
            raise ToolError(f"unknown kind {k!r}; options: {', '.join(_EXTRACTORS)}")
        found = [m.group(0).strip() for m in _EXTRACTORS[k].finditer(t)]
        if k == "urls":
            found = [u.rstrip(",.;:!?)") for u in found]  # the comma after a URL is the sentence's, not the URL's
        if unique:
            found = list(dict.fromkeys(found))
        out[k] = found
    return ok(out, assumptions=["regex-based extraction; validate ids with the validate tool"])


def _find(p: dict[str, Any]) -> dict[str, Any]:
    t = _text(p)
    needle = p.get("substring") or p.get("needle") or p.get("query")
    if not needle:
        raise ToolError("'substring' is required")
    cs = flag(p.get("case_sensitive", False), "case_sensitive")
    needle = str(needle)
    hits = []
    ctx = whole(p.get("context", 40), "context", lo=0)
    for m in re.finditer(re.escape(needle), t, 0 if cs else re.IGNORECASE):  # positions in the caller's text
        s, e = m.start(), m.end()
        line = t.count("\n", 0, s) + 1
        hits.append({"start": s, "end": e, "line": line, "context": t[max(0, s - ctx):e + ctx]})
        if len(hits) >= 200:
            break
    return ok({"count": len(hits), "found": bool(hits), "hits": hits}, assumptions=[f"case-{'sensitive' if cs else 'insensitive'}"])


_MAX_SIMILARITY_LEN = 5000
#: Character comparisons one call may make: one pair at the length cap.
_MAX_SIMILARITY_WORK = _MAX_SIMILARITY_LEN * _MAX_SIMILARITY_LEN


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
    ci = flag(p.get("case_insensitive", True), "case_insensitive")
    ws = flag(p.get("normalize_whitespace", True), "normalize_whitespace")

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
        work = len(q) * sum(len(norm(x)) for x in items)
        if work > _MAX_SIMILARITY_WORK:
            # the per-string cap bounds one pair; the work is the sum over all candidates
            raise TooLarge(
                f"comparing a {len(q):,}-character text against {len(items):,} candidates is {work:,} character comparisons; the limit is {_MAX_SIMILARITY_WORK:,}",
                details={"work": work, "limit": _MAX_SIMILARITY_WORK},
                hint="Shorten the text or the candidates, or send fewer candidates per call.",
            )
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
            "caption": "A ZWJ family emoji is five code points and one character to a reader; `graphemes` is the count people mean.",
            "args": {"mode": "count", "text": "\U0001f469\u200d\U0001f469\u200d\U0001f467 family"},
        },
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
