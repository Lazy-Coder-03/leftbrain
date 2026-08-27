"""random - genuine randomness (models cannot produce it) with optional seeding."""

from __future__ import annotations

import os
import random as _random
import secrets
import string
import time
import uuid as _uuid
from typing import Any

from ..contract import ToolError, check_params, ok, tool

MODES = ("uuid", "int", "float", "pick", "shuffle", "token", "bool", "sample")

#: What each mode reads. Anything else in a call is a caller's mistake, not a default
#: to fall back on (#28 SS2a). Kept honest by tests/test_mode_params.py, which derives
#: the same map from the code and fails when the two drift.
MODE_PARAMS: dict[str, frozenset[str]] = {
    "uuid": frozenset({"format", "n", "version"}),
    "int": frozenset({"max", "min", "n", "seed", "unique"}),
    "float": frozenset({"decimals", "max", "min", "n", "seed"}),
    "pick": frozenset({"items", "n", "seed", "unique", "weights"}),
    "shuffle": frozenset({"items", "seed"}),
    "token": frozenset({"kind", "length", "n"}),
    "bool": frozenset({"n", "p", "probability", "seed"}),
    "sample": frozenset({"groups", "items", "k", "n", "seed"}),
}

_MAX_N = 10000


def _rng(seed: Any) -> _random.Random:
    if seed is None:
        return secrets.SystemRandom()
    return _random.Random(str(seed))


def _n(p: dict[str, Any], default: int = 1) -> int:
    n = int(p.get("n", default))
    if n < 1 or n > _MAX_N:
        raise ToolError(f"n must be 1..{_MAX_N}")
    return n


def uuid7() -> str:
    ts = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    val = (ts << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(_uuid.UUID(int=val))


def _uuid_mode(p: dict[str, Any]) -> dict[str, Any]:
    n = _n(p)
    version = int(p.get("version", 4))
    if version == 4:
        ids = [str(_uuid.uuid4()) for _ in range(n)]
    elif version == 7:
        ids = [uuid7() for _ in range(n)]
    else:
        raise ToolError("version must be 4 (random) or 7 (time-ordered)")
    fmt = (p.get("format") or "canonical").lower()
    if fmt == "hex":
        ids = [i.replace("-", "") for i in ids]
    elif fmt == "upper":
        ids = [i.upper() for i in ids]
    return ok({"uuid": ids[0], "uuids": ids, "version": version} if n == 1 else {"uuids": ids, "version": version, "count": n})


def _int(p: dict[str, Any]) -> dict[str, Any]:
    lo, hi = int(p.get("min", 1)), int(p.get("max", 100))
    if lo > hi:
        raise ToolError("min must be <= max")
    n = _n(p)
    unique = bool(p.get("unique", False))
    rng = _rng(p.get("seed"))
    if unique:
        if hi - lo + 1 < n:
            raise ToolError("range too small for that many unique values")
        vals = rng.sample(range(lo, hi + 1), n)
    else:
        vals = [rng.randint(lo, hi) for _ in range(n)]
    out = {"value": vals[0], "values": vals, "min": lo, "max": hi} if n == 1 else {"values": vals, "min": lo, "max": hi, "count": n, "sum": sum(vals)}
    return ok(out, assumptions=["inclusive range"] + (["seeded: reproducible, not secure"] if p.get("seed") is not None else []))


def _float(p: dict[str, Any]) -> dict[str, Any]:
    lo, hi = float(p.get("min", 0.0)), float(p.get("max", 1.0))
    if lo > hi:
        raise ToolError("min must be <= max")
    n = _n(p)
    dec = p.get("decimals")
    rng = _rng(p.get("seed"))
    vals = [rng.uniform(lo, hi) for _ in range(n)]
    if dec is not None:
        vals = [round(v, int(dec)) for v in vals]
    return ok({"value": vals[0], "values": vals} if n == 1 else {"values": vals, "count": n})


def _pick(p: dict[str, Any]) -> dict[str, Any]:
    items = p.get("items")
    if not isinstance(items, list) or not items:
        raise ToolError("'items' must be a non-empty list")
    n = _n(p)
    unique = bool(p.get("unique", True))
    weights = p.get("weights")
    rng = _rng(p.get("seed"))
    if weights is not None:
        if len(weights) != len(items):
            raise ToolError("weights must match items length")
        if unique and n > 1:
            pool, ws, chosen = list(items), [float(w) for w in weights], []
            for _ in range(min(n, len(pool))):
                c = rng.choices(range(len(pool)), weights=ws, k=1)[0]
                chosen.append(pool.pop(c))
                ws.pop(c)
        else:
            chosen = rng.choices(items, weights=[float(w) for w in weights], k=n)
    elif unique:
        if n > len(items):
            raise ToolError("cannot pick more unique items than available")
        chosen = rng.sample(items, n)
    else:
        chosen = [rng.choice(items) for _ in range(n)]
    return ok({"picked": chosen[0], "items": chosen} if n == 1 else {"picked": chosen, "count": len(chosen)}, assumptions=(["weighted"] if weights else []) + (["without replacement"] if unique and n > 1 else []))


def _shuffle(p: dict[str, Any]) -> dict[str, Any]:
    items = p.get("items")
    if not isinstance(items, list):
        raise ToolError("'items' must be a list")
    rng = _rng(p.get("seed"))
    out = list(items)
    rng.shuffle(out)
    return ok({"shuffled": out, "count": len(out)})


_ALPHABETS = {
    "hex": string.hexdigits.lower()[:16],
    "alnum": string.ascii_letters + string.digits,
    "alpha": string.ascii_letters,
    "digits": string.digits,
    "upper": string.ascii_uppercase + string.digits,
    "lower": string.ascii_lowercase + string.digits,
    "urlsafe": string.ascii_letters + string.digits + "-_",
    "password": string.ascii_letters + string.digits + "!@#$%^&*()-_=+[]{}<>?",
    "readable": "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789",
}


def _token(p: dict[str, Any]) -> dict[str, Any]:
    kind = (p.get("kind") or "urlsafe").lower()
    length = int(p.get("length", 32))
    if length < 1 or length > 4096:
        raise ToolError("length must be 1..4096")
    n = _n(p)
    if kind == "bytes":
        toks = [secrets.token_hex(length) for _ in range(n)]
    elif kind in _ALPHABETS:
        alpha = _ALPHABETS[kind]
        toks = ["".join(secrets.choice(alpha) for _ in range(length)) for _ in range(n)]
        if kind == "password":
            for i, t in enumerate(toks):
                if length >= 4 and not (any(c.islower() for c in t) and any(c.isupper() for c in t) and any(c.isdigit() for c in t) and any(not c.isalnum() for c in t)):
                    t = list(t)
                    t[0], t[1], t[2], t[3] = secrets.choice(string.ascii_lowercase), secrets.choice(string.ascii_uppercase), secrets.choice(string.digits), secrets.choice("!@#$%^&*")
                    secrets.SystemRandom().shuffle(t)
                    toks[i] = "".join(t)
    elif kind == "otp":
        toks = ["".join(secrets.choice(string.digits) for _ in range(length if p.get("length") else 6)) for _ in range(n)]
    else:
        raise ToolError(f"kind must be one of {', '.join(_ALPHABETS)}, bytes, otp")
    return ok({"token": toks[0], "tokens": toks, "kind": kind} if n == 1 else {"tokens": toks, "kind": kind, "count": n}, assumptions=["cryptographically secure (os entropy); seeding is ignored for tokens"])


def _bool(p: dict[str, Any]) -> dict[str, Any]:
    prob = float(p.get("p", p.get("probability", 0.5)))
    if not 0 <= prob <= 1:
        raise ToolError("p must be within 0..1")
    n = _n(p)
    rng = _rng(p.get("seed"))
    vals = [rng.random() < prob for _ in range(n)]
    return ok({"value": vals[0], "values": vals, "p": prob} if n == 1 else {"values": vals, "true_count": sum(vals), "count": n, "p": prob})


def _sample(p: dict[str, Any]) -> dict[str, Any]:
    """Split a list into random groups (e.g. A/B buckets) or a sample subset."""
    items = p.get("items")
    if not isinstance(items, list) or not items:
        raise ToolError("'items' must be a non-empty list")
    rng = _rng(p.get("seed"))
    groups = p.get("groups")
    if groups:
        names = list(groups) if isinstance(groups, list) else [f"group{i + 1}" for i in range(int(groups))]
        pool = list(items)
        rng.shuffle(pool)
        out = {name: [] for name in names}
        for i, x in enumerate(pool):
            out[names[i % len(names)]].append(x)
        return ok({"groups": out, "sizes": {k: len(v) for k, v in out.items()}}, assumptions=["round-robin after shuffle; sizes differ by at most 1"])
    k = int(p.get("k", p.get("n", 1)))
    if k > len(items):
        raise ToolError("k exceeds number of items")
    return ok({"sample": rng.sample(items, k), "k": k})


@tool
def random_tool(mode: str = "uuid", **params: Any) -> dict[str, Any]:
    """Randomness. Modes: uuid, int, float, pick, shuffle, token, bool, sample."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    check_params("random", mode, p, MODE_PARAMS)
    _ = os  # keep import for potential entropy checks
    return {"uuid": _uuid_mode, "int": _int, "float": _float, "pick": _pick, "shuffle": _shuffle, "token": _token, "bool": _bool, "sample": _sample}[mode](p)

#: Worked examples for the reference page, one list per mode. Every one of them is
#: executed when /docs/tools/random is built and sorted by the result into
#: "Examples" (the call succeeded) and "Fails when" (it did not), so a fixture never
#: states an expectation of its own. Mark anything whose output depends on the
#: current instant with "volatile": True.
EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "uuid": [
        {
            "caption": "One v4 UUID.",
            "args": {"mode": "uuid"},
            "volatile": True,
        },
        {
            "caption": "Three time-ordered v7 UUIDs in bare hex — note the shared prefix.",
            "args": {"mode": "uuid", "version": 7, "n": 3, "format": "hex"},
            "volatile": True,
        },
        {
            "caption": "Only versions 4 and 7 are offered.",
            "args": {"mode": "uuid", "version": 5},
        },
        {
            "caption": "`n` must be at least 1.",
            "args": {"mode": "uuid", "n": 0},
        },
    ],
    "int": [
        {
            "caption": "Five dice rolls, seeded — this exact list comes back every time.",
            "args": {"mode": "int", "min": 1, "max": 6, "n": 5, "seed": "demo"},
        },
        {
            "caption": "Six distinct numbers from 1 to 49, seeded.",
            "args": {"mode": "int", "min": 1, "max": 49, "n": 6, "unique": True, "seed": "lotto-2025"},
        },
        {
            "caption": "Unseeded: a fresh draw from system entropy every call.",
            "args": {"mode": "int", "min": 1, "max": 100},
            "volatile": True,
        },
        {
            "caption": "The range must not be inverted.",
            "args": {"mode": "int", "min": 10, "max": 1},
        },
        {
            "caption": "Too few values in the range to draw that many distinct ones.",
            "args": {"mode": "int", "min": 1, "max": 3, "n": 5, "unique": True},
        },
        {
            "caption": "`n` is capped at 10 000.",
            "args": {"mode": "int", "min": 1, "max": 10, "n": 999999},
        },
    ],
    "float": [
        {
            "caption": "Four seeded prices, rounded as they are drawn.",
            "args": {"mode": "float", "min": 10, "max": 20, "n": 4, "decimals": 2, "seed": "demo"},
        },
        {
            "caption": "A single seeded value, unrounded.",
            "args": {"mode": "float", "seed": "demo"},
        },
        {
            "caption": "The range must not be inverted.",
            "args": {"mode": "float", "min": 5, "max": 1},
        },
        {
            "caption": "`n` must be at least 1.",
            "args": {"mode": "float", "n": 0},
        },
    ],
    "pick": [
        {
            "caption": "Two winners from a list, seeded.",
            "args": {"mode": "pick", "items": ["asha", "bo", "chen", "dev", "eve"], "n": 2, "seed": "raffle-1"},
        },
        {
            "caption": "A weighted draw with replacement.",
            "args": {"mode": "pick", "items": ["gold", "silver", "bronze"], "weights": [1, 3, 6], "n": 5, "unique": False, "seed": "loot"},
        },
        {
            "caption": "Weights must line up with items.",
            "args": {"mode": "pick", "items": ["a", "b", "c"], "weights": [1, 2]},
        },
        {
            "caption": "You cannot draw more unique items than exist.",
            "args": {"mode": "pick", "items": ["a", "b"], "n": 5},
        },
    ],
    "shuffle": [
        {
            "caption": "A seeded shuffle.",
            "args": {"mode": "shuffle", "items": ["a", "b", "c", "d", "e"], "seed": "demo"},
        },
        {
            "caption": "A different seed, a different order — from the same input.",
            "args": {"mode": "shuffle", "items": ["a", "b", "c", "d", "e"], "seed": "other"},
        },
        {
            "caption": "`items` must be a list.",
            "args": {"mode": "shuffle", "items": "abcde"},
        },
    ],
    "token": [
        {
            "caption": "A URL-safe API token.",
            "args": {"mode": "token", "kind": "urlsafe", "length": 32},
            "volatile": True,
        },
        {
            "caption": "A password with all four character classes guaranteed.",
            "args": {"mode": "token", "kind": "password", "length": 16},
            "volatile": True,
        },
        {
            "caption": "A six-digit OTP.",
            "args": {"mode": "token", "kind": "otp", "length": 6},
            "volatile": True,
        },
        {
            "caption": "Human-readable codes with no confusable characters.",
            "args": {"mode": "token", "kind": "readable", "length": 8, "n": 3},
            "volatile": True,
        },
        {
            "caption": "An unknown alphabet lists the valid ones.",
            "args": {"mode": "token", "kind": "runes"},
        },
        {
            "caption": "`length` must be between 1 and 4096.",
            "args": {"mode": "token", "kind": "hex", "length": 0},
        },
    ],
    "bool": [
        {
            "caption": "Ten seeded flips at 30%, with the count of trues.",
            "args": {"mode": "bool", "p": 0.3, "n": 10, "seed": "demo"},
        },
        {
            "caption": "A single seeded fair flip.",
            "args": {"mode": "bool", "seed": "demo"},
        },
        {
            "caption": "A probability outside 0..1.",
            "args": {"mode": "bool", "p": 1.5},
        },
        {
            "caption": "`n` must be at least 1.",
            "args": {"mode": "bool", "n": 0},
        },
    ],
    "sample": [
        {
            "caption": "A seeded sample of three.",
            "args": {"mode": "sample", "items": ["u1", "u2", "u3", "u4", "u5", "u6", "u7"], "k": 3, "seed": "audit-2025"},
        },
        {
            "caption": "A named A/B/C split, balanced to within one.",
            "args": {"mode": "sample", "items": ["u1", "u2", "u3", "u4", "u5", "u6", "u7"], "groups": ["control", "variant_a", "variant_b"], "seed": "exp-42"},
        },
        {
            "caption": "`k` cannot exceed the population.",
            "args": {"mode": "sample", "items": ["a", "b"], "k": 5},
        },
    ],
}
