"""collections - exact set logic, grouping, sorting, reshaping and table arithmetic over lists/records or CSV text."""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..contract import Ambiguous, TooLarge, ToolError, check_params, flag, ok, tool, whole
from .numbers import _dec_str, parse_number

MODES = ("set_ops", "group_by", "pick_fields", "flatten", "unflatten", "paginate", "find_duplicates", "sort_by", "aggregate", "chunk", "filter", "pivot", "running", "outliers", "summarize", "to_csv")

#: What each mode reads. Anything else in a call is a caller's mistake, not a default
#: to fall back on (#28 SS2a). Kept honest by tests/test_mode_params.py, which derives
#: the same map from the code and fails when the two drift.
MODE_PARAMS: dict[str, frozenset[str]] = {
    "set_ops": frozenset({"a", "b", "case_insensitive", "delimiter", "has_header", "items", "key", "op"}),
    "group_by": frozenset({"agg", "agg_field", "by", "data", "delimiter", "field", "has_header", "include_items", "items", "key"}),
    "pick_fields": frozenset({"data", "delimiter", "fields", "has_header", "items", "rename", "short_names"}),
    "flatten": frozenset({"data", "delimiter", "depth", "flatten_lists", "has_header", "items", "separator"}),
    "unflatten": frozenset({"data", "delimiter", "has_header", "items", "separator"}),
    "paginate": frozenset({"data", "delimiter", "has_header", "items", "page", "per_page"}),
    "find_duplicates": frozenset({"case_insensitive", "data", "delimiter", "has_header", "items", "key"}),
    "sort_by": frozenset({"data", "delimiter", "has_header", "items", "key", "keys", "order"}),
    "aggregate": frozenset({"agg", "data", "delimiter", "field", "has_header", "items", "ops"}),
    "chunk": frozenset({"data", "delimiter", "has_header", "items", "n", "size"}),
    "filter": frozenset({"columns", "delimiter", "has_header", "items", "where"}),
    "pivot": frozenset({"agg", "by", "column", "decimals", "delimiter", "has_header", "items", "pivot_columns"}),
    "running": frozenset({"by", "column", "columns", "decimals", "delimiter", "has_header", "items"}),
    "outliers": frozenset({"column", "decimals", "delimiter", "has_header", "items"}),
    "summarize": frozenset({"columns", "decimals", "delimiter", "has_header", "items"}),
    "to_csv": frozenset({"columns", "delimiter", "escape_formulas", "has_header", "items"}),
}

#: Levels a nested structure may go before flatten refuses it.
MAX_DEPTH = 1000
#: The largest list index unflatten will allocate up to.
MAX_INDEX = 100_000
#: Above this many rows a CSV or record table is refused rather than answered slowly or partially.
MAX_ROWS = 5000
#: Distinct values of 'pivot_columns' that may become columns.
MAX_PIVOT_COLUMNS = 200
#: Row-shaped results of the table modes echo at most this many rows (`to_csv` is exempt: the rows are the answer).
ECHO_ROWS = 500

AGGS = ("sum", "avg", "min", "max", "count", "median")
FILTER_OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "starts_with", "ends_with", "empty", "not_empty")

_EMPTY = {"", "na", "n/a", "-", "\u2014", "null", "none", "nan"}
_TRUE, _FALSE = {"true", "yes"}, {"false", "no"}
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?")
_IQR_MULTIPLIER = Decimal("1.5")

_PATH_TOKEN = re.compile(r"\.?([^.\[\]]+)|\[(\d+)\]")


def get_path(obj: Any, path: str | None) -> Any:
    """Dotted/bracket path lookup: 'a.b[0].c'. Returns None when missing."""
    if path in (None, "", "$", "."):
        return obj
    cur = obj
    p = str(path)
    if p.startswith("$"):
        p = p[1:]
    for m in _PATH_TOKEN.finditer(p):
        key, idx = m.group(1), m.group(2)
        if idx is not None:
            if not isinstance(cur, list) or int(idx) >= len(cur):
                return None
            cur = cur[int(idx)]
        else:
            if isinstance(cur, dict):
                cur = cur.get(key)
            elif isinstance(cur, list) and key.isdigit():
                i = int(key)
                cur = cur[i] if i < len(cur) else None
            else:
                return None
        if cur is None:
            return None
    return cur


def _hashable(v: Any, ci: bool = False) -> Any:
    """A key that distinguishes values Python's own hashing conflates.

    `True == 1` and `1.0 == 1` in Python, so a set of `[1, true]` collapsed to one entry
    and `difference` reported `true` as being in both lists (#28 SS3.11). The type name
    rides along so `1`, `1.0`, `"1"` and `true` stay four different things.
    """
    if isinstance(v, str):
        # `case_insensitive` is asked for when the data is untidy, and untidy data has
        # stray spaces: "a@x.com " and "A@x.com" are the same address (#28 SS3.13).
        return ("str", v.strip().casefold() if ci else v)
    if isinstance(v, (dict, list)):
        return (type(v).__name__, json.dumps(v, sort_keys=True, default=str))
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        # a cell loaded from CSV is a Decimal and the same value from JSON is an int
        return ("num", Decimal(repr(v)) if isinstance(v, float) else Decimal(v))
    return (type(v).__name__, v)


def _items(p: dict[str, Any], key: str = "items") -> list[Any]:
    it = p.get(key)
    if not isinstance(it, list):
        raise ToolError(f"'{key}' must be a list")
    return it


def _input_rows(p: dict[str, Any]) -> list[Any]:
    """The rows to work on, under either of the names this tool uses for them.

    `flatten` and `unflatten` call these `data`; every other mode calls them `items`. An agent
    that learned one mode reasonably tries the same name in the next, and got a refusal and a
    wasted round-trip for it (#78). Both are accepted everywhere records are taken.
    """
    if p.get("items") is None and p.get("data") is not None:
        return _items(p, "data")
    return _items(p)


def _set_ops(p: dict[str, Any]) -> dict[str, Any]:
    a, b = _items(p, "a"), _items(p, "b")
    op = (p.get("op") or "compare").lower()
    key = p.get("key")
    ci = bool(p.get("case_insensitive", False))

    warnings: list[str] = []
    missing = 0

    def k(x: Any) -> Any:
        return _hashable(get_path(x, key) if key else x, ci)

    def keyed(xs: list[Any], name: str) -> dict[Any, Any]:
        nonlocal missing
        out: dict[Any, Any] = {}
        for x in xs:
            if key and get_path(x, key) is None and not (isinstance(x, dict) and key in x):
                missing += 1  # a row without the key matches nothing, least of all another such row
                continue
            out[k(x)] = x
        return out

    ka, kb = keyed(a, "a"), keyed(b, "b")
    if missing:
        warnings.append(f"{missing} row(s) have no '{key}' and were left out of the comparison")
    if a and b and all(isinstance(x, dict) for x in a) != all(isinstance(x, dict) for x in b) and not key:
        warnings.append("one side is a list of records and the other a list of values; nothing can match without a 'key'")
    only_a = [ka[x] for x in ka if x not in kb]
    only_b = [kb[x] for x in kb if x not in ka]
    both = [ka[x] for x in ka if x in kb]
    union = list(ka.values()) + only_b
    sym = only_a + only_b
    res = {"union": union, "intersection": both, "difference": only_a, "symmetric_difference": sym, "compare": None}
    if op not in res:
        raise ToolError("op must be union, intersection, difference, symmetric_difference or compare")
    out: dict[str, Any] = {"only_in_a": only_a, "only_in_b": only_b, "in_both": both, "counts": {"a": len(a), "b": len(b), "a_unique": len(ka), "b_unique": len(kb), "only_in_a": len(only_a), "only_in_b": len(only_b), "in_both": len(both)}, "equal_as_sets": not only_a and not only_b}
    if op != "compare":
        out["result"] = res[op]
        out["count"] = len(res[op])
    assumptions = ["difference = a minus b"] if op == "difference" else []
    if len(ka) + (missing if missing else 0) != len(a) + len(b) - len(kb) and (len(ka) != len(a) - sum(1 for x in a if key and get_path(x, key) is None) or len(kb) != len(b) - sum(1 for x in b if key and get_path(x, key) is None)):
        assumptions.append("duplicates within a list were collapsed")
    return ok(out, assumptions=assumptions, warnings=warnings)


def _num(v: Any) -> Decimal | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, Decimal):  # only ever a cell loaded from CSV text; JSON input never carries one
        return v
    if isinstance(v, float):
        return None if v != v or v in (float("inf"), float("-inf")) else Decimal(repr(v))
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, str):
        try:
            d, _ = parse_number(v)  # the same reading the table loader makes: "12,34" is 12.34 in both
        except Exception:
            return None
        return d if d.is_finite() else None
    return None


def _agg(values: list[Any], ops: list[str]) -> dict[str, Any]:
    nums = [n for n in (_num(v) for v in values) if n is not None]
    out: dict[str, Any] = {}
    for op in ops:
        if op == "count":
            out["count"] = len(values)
        elif op == "count_distinct":
            out["count_distinct"] = len({_hashable(v) for v in values})
        elif op in ("sum", "avg", "mean", "min", "max"):
            if not nums:
                out[op] = None
                continue
            if op == "sum":
                out["sum"] = str(sum(nums))
            elif op in ("avg", "mean"):
                out[op] = str(sum(nums) / len(nums))
            elif op == "min":
                out["min"] = str(min(nums))
            else:
                out["max"] = str(max(nums))
        elif op == "median":
            # `math.stats` has had a median all along; this mode did not (#28 SS3.13).
            if not nums:
                out["median"] = None
                continue
            ordered = sorted(nums)
            mid = len(ordered) // 2
            out["median"] = str(ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2)
        elif op == "first":
            out["first"] = values[0] if values else None
        elif op == "last":
            out["last"] = values[-1] if values else None
        elif op == "list":
            out["list"] = values
        else:
            raise ToolError(f"unknown aggregate op {op!r}")
    return out


def _group_by(p: dict[str, Any]) -> dict[str, Any]:
    items = _input_rows(p)
    # `pivot` and `running` call the grouping key `by`. Accepting both here means an agent that
    # learned one of the three can use the next without a round-trip to find out (#78).
    key = p.get("key") or p.get("by")
    if not key:
        raise ToolError("'key' is required (also accepted as 'by'); 'field' names the column to aggregate, not the one to group on")
    groups: dict[Any, list[Any]] = defaultdict(list)
    shown: dict[Any, Any] = {}  # the key as the caller wrote it, not the type-tagged form
    for x in items:
        value = get_path(x, key)
        k = _hashable(value)
        shown.setdefault(k, value)
        groups[k].append(x)
    agg_field, agg_ops = p.get("agg_field") or p.get("field"), p.get("agg") or ["count"]
    if isinstance(agg_ops, str):
        agg_ops = [agg_ops]
    out_groups = []
    for gk, members in groups.items():
        entry: dict[str, Any] = {"key": shown[gk], "count": len(members)}
        if agg_field:
            entry["agg"] = _agg([get_path(m, agg_field) for m in members], agg_ops)
        if p.get("include_items", True) and len(items) <= 2000:
            entry["items"] = members
        out_groups.append(entry)
    out_groups.sort(key=lambda g: (str(type(g["key"])), g["key"] if g["key"] is not None else ""))
    return ok({"groups": out_groups, "group_count": len(out_groups), "total": len(items)})


def _aggregate(p: dict[str, Any]) -> dict[str, Any]:
    items = _input_rows(p)
    field = p.get("field")
    ops = p.get("ops") or p.get("agg") or ["count", "sum", "avg", "min", "max"]
    if isinstance(ops, str):
        ops = [ops]
    values = [get_path(x, field) for x in items] if field else items
    return ok(_agg(values, ops), assumptions=["non-numeric values ignored for sum/avg/min/max"])


def _pick_fields(p: dict[str, Any]) -> dict[str, Any]:
    items = _input_rows(p)
    fields = p.get("fields")
    if not fields:
        raise ToolError("'fields' is required")
    if isinstance(fields, str):
        fields = [fields]
    rename = p.get("rename") or {}
    targets = [rename.get(f, f.split(".")[-1] if p.get("short_names") else f) for f in fields]
    clashes = sorted({t for t in targets if targets.count(t) > 1})
    if clashes:
        # `{"a": "z", "b": "z"}` kept whichever field came last and dropped the other
        # without a word (#28 SS3.7).
        raise ToolError(
            f"two or more fields would be written to the same name: {', '.join(repr(c) for c in clashes)}",
            details={"colliding": clashes, "targets": targets},
            hint="Give each field a distinct name in 'rename'.",
        )
    out = []
    for x in items:
        row = {}
        for f in fields:
            row[rename.get(f, f.split(".")[-1] if p.get("short_names") else f)] = get_path(x, f)
        out.append(row)
    return ok({"items": out, "count": len(out)})


def _depth_of(obj: Any) -> int:
    """How deep `obj` nests, counted without recursion so it can be measured before the walk."""
    deepest, stack = 0, [(obj, 1)]
    while stack:
        cur, d = stack.pop()
        deepest = max(deepest, d)
        if d > MAX_DEPTH:
            return d
        if isinstance(cur, dict):
            stack.extend((v, d + 1) for v in cur.values())
        elif isinstance(cur, list):
            stack.extend((v, d + 1) for v in cur)
    return deepest


def _too_deep(obj: Any, what: str) -> None:
    depth = _depth_of(obj)
    if depth > MAX_DEPTH:
        raise TooLarge(f"{what} nests more than {MAX_DEPTH:,} levels deep", details={"limit_depth": MAX_DEPTH}, hint=f"Flatten it in stages, or keep nesting under {MAX_DEPTH:,}.")


def _flatten(p: dict[str, Any]) -> dict[str, Any]:
    data = p.get("data") if "data" in p else p.get("items")
    depth = p.get("depth")
    sep = p.get("separator", ".")
    _too_deep(data, "'data'")
    if isinstance(data, list):
        def fl(xs: Any, d: int) -> list[Any]:
            out = []
            for x in xs:
                if isinstance(x, list) and (depth is None or d < int(depth)):
                    out.extend(fl(x, d + 1))
                else:
                    out.append(x)
            return out
        flat = fl(data, 0)
        return ok({"items": flat, "count": len(flat)})
    if isinstance(data, dict):
        out: dict[str, Any] = {}

        def walk(obj: Any, prefix: str, d: int) -> None:
            if isinstance(obj, dict) and obj and (depth is None or d < int(depth)):
                for k, v in obj.items():
                    walk(v, f"{prefix}{sep}{k}" if prefix else str(k), d + 1)
            elif isinstance(obj, list) and obj and p.get("flatten_lists", True) and (depth is None or d < int(depth)):
                for i, v in enumerate(obj):
                    walk(v, f"{prefix}[{i}]", d + 1)
            else:
                if prefix in out:
                    # {"a.b": 1, "a": {"b": 2}} produces "a.b" twice
                    raise ToolError(f"key {prefix!r} is produced twice: once as a literal key and once from the nested path", hint="Rename one of the two, or use a different 'separator'.")
                out[prefix] = obj

        walk(data, "", 0)
        return ok({"flat": out, "count": len(out)})
    raise ToolError("'data' must be a list or object")


def _descend(child: Any, parts: list[Any], i: int, key: str, sep: str) -> Any:
    """Step into `child`, refusing the case where it already holds a value.

    `{"a": 1, "a.b": 2}` asks for `a` to be a number and an object at once. It used to be a
    bare `TypeError: 'int' object does not support item assignment` (#28 SS4).
    """
    if isinstance(child, (dict, list)):
        return child
    held = sep.join(str(x) for x in parts[: i + 1])
    raise ToolError(
        f"key {held!r} is both a value and a prefix of {key!r}; it cannot be a value and an object at once",
        details={"key": held, "conflicts_with": key},
        hint="Rename one of the two keys, or drop the scalar one.",
    )


def _unflatten(p: dict[str, Any]) -> dict[str, Any]:
    data = p.get("data")
    if not isinstance(data, dict):
        raise ToolError("'data' must be a flat object with dotted keys")
    sep = p.get("separator", ".")
    root: dict[str, Any] = {}
    for k, v in data.items():
        parts = []
        for seg in str(k).split(sep):
            for m in re.finditer(r"([^\[\]]+)|\[(\d+)\]", seg):
                parts.append(int(m.group(2)) if m.group(2) is not None else m.group(1))
        cur: Any = root
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            nxt_is_idx = not last and isinstance(parts[i + 1], int)
            if isinstance(part, int):
                if not isinstance(cur, list):
                    raise ToolError(f"key {k!r} indexes into {sep.join(str(x) for x in parts[:i]) or 'the root'!r}, which another key made an object", hint="A path is either a list index or an object key at each level, not both.")
                if part > MAX_INDEX:
                    raise TooLarge(f"key {k!r} asks for list index {part:,}; the most that can be allocated is {MAX_INDEX:,}", details={"index": part, "limit": MAX_INDEX}, hint="Use an object key instead of a sparse list index.")
                while len(cur) <= part:
                    cur.append(None)
                if last:
                    cur[part] = v
                else:
                    if cur[part] is None:
                        cur[part] = [] if nxt_is_idx else {}
                    cur = _descend(cur[part], parts, i, str(k), sep)
            else:
                if not isinstance(cur, dict):
                    raise ToolError(f"key {k!r} names a field of {sep.join(str(x) for x in parts[:i]) or 'the root'!r}, which another key made a list", hint="A path is either a list index or an object key at each level, not both.")
                if last:
                    if isinstance(cur.get(part), (dict, list)):
                        # {"a.b": 2, "a": 1}: the scalar arrives after the prefix
                        raise ToolError(f"key {k!r} is both a value and a prefix of another key; it cannot be a value and an object at once", details={"key": k}, hint="Rename one of the two keys, or drop the scalar one.")
                    cur[part] = v
                else:
                    if part not in cur or cur[part] is None:
                        cur[part] = [] if nxt_is_idx else {}
                    cur = _descend(cur[part], parts, i, str(k), sep)
    return ok({"data": root})


def _paginate(p: dict[str, Any]) -> dict[str, Any]:
    items = _input_rows(p)
    per = whole(p.get("per_page", 20), "per_page", lo=1)
    page = whole(p.get("page", 1), "page", lo=1)
    total_pages = max(1, -(-len(items) // per))
    start = (page - 1) * per
    return ok({"items": items[start:start + per], "page": page, "per_page": per, "total": len(items), "total_pages": total_pages, "has_next": page < total_pages, "has_prev": page > 1, "range": [start + 1, min(start + per, len(items))] if start < len(items) else None})


def _find_duplicates(p: dict[str, Any]) -> dict[str, Any]:
    items = _input_rows(p)
    key = p.get("key")
    ci = bool(p.get("case_insensitive", False))
    where: dict[Any, list[int]] = defaultdict(list)
    missing = 0
    for i, x in enumerate(items):
        if key:
            value = get_path(x, key)
            if value is None and not (isinstance(x, dict) and key in x):
                # A row that has no such key is not equal to every other row that lacks it.
                # Grouping them made `key="nope"` report every row as a duplicate (#28 SS3.8).
                missing += 1
                continue
        else:
            value = x
        where[_hashable(value, ci)].append(i)
    dupes = [{"value": items[idx[0]] if not key else get_path(items[idx[0]], key), "indices": idx, "count": len(idx)} for idx in where.values() if len(idx) > 1]
    warnings = [f"{missing} of {len(items)} rows have no '{key}' and were left out of the comparison"] if missing else []
    assumptions: list[str] = []
    if not ci and not dupes:
        loose = Counter(_hashable(get_path(x, key) if key else x, True) for x in items if not key or get_path(x, key) is not None)
        near = sum(1 for c in loose.values() if c > 1)
        if near:
            assumptions.append(f"{near} group(s) differ only by case or surrounding spaces; pass case_insensitive=true to treat them as duplicates")
    return ok(
        {"duplicates": dupes, "duplicate_groups": len(dupes), "has_duplicates": bool(dupes), "compared": len(items) - missing, "skipped_missing_key": missing,
         "counts": {str(k[1]): len(v) for k, v in Counter({k: v for k, v in where.items()}).items()} if len(where) <= 200 else None},
        warnings=warnings,
        assumptions=assumptions,
    )


#: Accepted spellings of a sort direction, and whether each one reverses.
_ORDERS = {"asc": False, "ascending": False, "desc": True, "descending": True}


def _sort_spec(spec: Any, default: str, items: list[Any], notes: list[str]) -> dict[str, Any]:
    """One sort key: a field name, `-name` for descending, or `{field, order}`."""
    if isinstance(spec, dict):
        return {"field": spec.get("field"), "order": str(spec.get("order", default)).lower()}
    name = str(spec)
    # `-field` is the shape agents reach for, and it used to be taken as a literal field name
    # that nothing had (#84). A column genuinely called `-v` still wins, because it is checked
    # against the data first.
    if name.startswith("-") and len(name) > 1:
        present = any(get_path(x, name) is not None for x in items)
        bare = any(get_path(x, name[1:]) is not None for x in items)
        if not present and bare:
            notes.append(f"'{name}' read as '{name[1:]}' descending; a field actually named '{name}' would win")
            return {"field": name[1:], "order": "desc"}
    return {"field": name, "order": default}


def _sort_by(p: dict[str, Any]) -> dict[str, Any]:
    items = _input_rows(p)
    # `order` used to be folded into the single-`key` form only, so a caller who passed `keys`
    # - the only way to sort on more than one field - silently got ascending every time (#84).
    default = str(p.get("order") or "asc").lower()
    if default not in _ORDERS:
        raise ToolError(f"order must be one of {', '.join(_ORDERS)}")
    raw = p.get("keys") if p.get("keys") is not None else p.get("key")
    if not raw:
        raise ToolError("'keys' (list of {field, order}) is required")
    if isinstance(raw, (str, dict)):
        raw = [raw]
    notes: list[str] = []
    keys = [_sort_spec(k, default, items, notes) for k in raw]
    for spec in keys:
        if spec["order"] not in _ORDERS:
            raise ToolError(f"order must be one of {', '.join(_ORDERS)}; '{spec['order']}' was given for '{spec['field']}'")
    srt = list(items)
    for spec in reversed(keys):
        field = spec.get("field")
        desc = _ORDERS[spec["order"]]

        def kf(x: Any, field: Any = field) -> Any:
            v = get_path(x, field)
            n = _num(v)
            if v is None:
                return (2, 0, "")
            if n is not None and not isinstance(v, str):
                return (0, n, "")
            return (1, 0, str(v).casefold())

        srt.sort(key=kf, reverse=desc)
    assumptions = ["stable multi-key sort; None sorts last; strings case-insensitive", *notes]
    warnings: list[str] = []
    for spec in keys:
        fld = spec.get("field")
        values = [get_path(x, fld) for x in items]
        if fld is None:
            raise ToolError("each sort key needs a 'field'")
        if items and all(v is None for v in values):
            warnings.append(f"no item has a '{fld}' key, so it changed nothing")
        elif any(isinstance(v, str) and _num(v) is not None for v in values):
            assumptions.append(f"'{fld}' holds numbers written as text, which sort as text (\"10\" < \"9\"); convert them to numbers to sort numerically")
    return ok({"items": srt, "count": len(srt), "changed": srt != items}, assumptions=assumptions, warnings=warnings)


def _chunk(p: dict[str, Any]) -> dict[str, Any]:
    items = _input_rows(p)
    size = p.get("size")
    n = p.get("n")
    if size is not None:
        size = whole(size, "size", lo=1)
        chunks = [items[i:i + size] for i in range(0, len(items), size)]
    elif n is not None:
        n = whole(n, "n", lo=1)
        if n > len(items):
            raise TooLarge(f"n is {n:,} but there are only {len(items):,} items to split", details={"n": n, "items": len(items)}, hint="Use n of at most the number of items.")
        q, r = divmod(len(items), n)
        chunks, i = [], 0
        for c in range(n):
            ln = q + (1 if c < r else 0)
            chunks.append(items[i:i + ln])
            i += ln
    else:
        raise ToolError("chunk needs 'size' or 'n'")
    return ok({"chunks": chunks, "count": len(chunks), "sizes": [len(c) for c in chunks]})


# --------------------------------------------------------------------------- #
# Tables: CSV text or a list of records, loaded into typed cells
# --------------------------------------------------------------------------- #


@dataclass
class Table:
    """A loaded table: typed cells (`Decimal`, `bool`, ISO date string, text or `None`)."""

    columns: list[str]
    rows: list[dict[str, Any]]
    types: dict[str, str]
    assumptions: list[str] = field(default_factory=list)


def _kind(v: Any) -> str:
    """What one raw cell looks like: empty, bool, number, date or text."""
    if v is None:
        return "empty"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return "empty"  # NaN is the absence of a number
    if isinstance(v, (int, float, Decimal)):
        return "number"
    if not isinstance(v, str):
        return "text"
    s = v.strip()
    if not s or s.casefold() in _EMPTY:
        return "empty"
    if re.fullmatch(r"0\d+", s):
        return "text"  # 02134 is a postcode, not the number 2134
    if s.casefold() in _TRUE or s.casefold() in _FALSE:
        return "bool"
    if _ISO_DATE.fullmatch(s):
        return "date"
    try:
        parse_number(s)
    except Exception:
        return "text"
    return "number"


def _coerce(v: Any, kind: str, notes: set[str]) -> Any:
    if kind == "number":
        if isinstance(v, bool):
            return Decimal(int(v))
        if isinstance(v, (int, Decimal)):
            return Decimal(v)
        if isinstance(v, float):
            return Decimal(repr(v))
        d, assumptions = parse_number(str(v).strip())
        notes.update(assumptions)
        return d
    if kind == "bool":
        return v if isinstance(v, bool) else str(v).strip().casefold() in _TRUE
    if kind == "date":
        return str(v).strip()
    if isinstance(v, Decimal):
        return _dec_str(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    return v if isinstance(v, (str, dict, list)) else str(v)


def _csv_rows(text: str, key: str, delimiter: str | None, assumptions: list[str]) -> list[list[Any]]:
    text = text.lstrip("﻿")
    if not text.strip():
        raise ToolError(f"'{key}' is empty")
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
            assumptions.append(f"delimiter {delimiter!r} detected")
        except csv.Error:
            delimiter = ","
            assumptions.append("delimiter ',' assumed (could not be detected)")
    elif len(delimiter) != 1:
        raise ToolError("'delimiter' must be a single character")
    return [list(r) for r in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _split_header(rows: list[list[Any]], has_header: bool | None, assumptions: list[str]) -> tuple[list[str], list[list[Any]]]:
    first = rows[0]
    if has_header is None:
        typed = [c for c in first if _kind(c) in ("number", "date", "bool")]
        has_header = not typed and any(_kind(c) != "empty" for c in first)
        assumptions.append("first row read as the header (no numeric, date or boolean cells in it)" if has_header else "no header row: the first row holds data, so columns are named col1..colN")
    if has_header:
        names = [str(c).strip() or f"col{i + 1}" for i, c in enumerate(first)]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ToolError(f"duplicate column name(s) in the header: {', '.join(dupes)}")
        return names, rows[1:]
    width = max(len(r) for r in rows)
    return [f"col{i + 1}" for i in range(width)], rows


def _load(p: dict[str, Any], key: str = "items") -> Table:
    """Load `p[key]` - CSV text or a list of records - into a typed `Table`, stating every reading made."""
    data = p.get(key)
    if data is None:
        raise ToolError(f"'{key}' is required: CSV text or a list of records")
    assumptions: list[str] = []
    if isinstance(data, str):
        header = flag(p["has_header"], "has_header") if p.get("has_header") is not None else None
        columns, raw = _split_header(_csv_rows(data, key, p.get("delimiter"), assumptions), header, assumptions)
    elif isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
        columns = [str(k) for k in dict.fromkeys(k for r in data for k in r)]
        raw = [[r.get(c) for c in columns] for r in data]
    elif isinstance(data, list) and not data:
        raise ToolError(f"'{key}' has no rows")
    else:
        raise ToolError(f"'{key}' must be CSV text or a list of records")
    if len(raw) > MAX_ROWS:
        raise ToolError(f"{len(raw):,} rows exceeds the {MAX_ROWS:,}-row cap; split the table or pre-aggregate it")

    cells: list[list[Any]] = []
    blank = 0
    sentinels: dict[str, int] = {}
    for i, r in enumerate(raw, 1):
        if len(r) > len(columns):
            raise ToolError(f"row {i} has {len(r)} cells but the header has {len(columns)}")
        r = list(r) + [None] * (len(columns) - len(r))
        if all(_kind(c) == "empty" for c in r):
            blank += 1
            continue
        row = []
        for c in r:
            if isinstance(c, str) and c.strip() and c.strip().casefold() in _EMPTY:
                sentinels[c.strip()] = sentinels.get(c.strip(), 0) + 1
                c = None
            elif isinstance(c, str) and not c.strip():
                c = None
            elif not isinstance(c, str) and _kind(c) == "empty":
                c = None  # NaN and infinities are absences, not numbers
            row.append(c)
        cells.append(row)
    if blank:
        assumptions.append(f"{blank} blank row{'s' if blank > 1 else ''} skipped")
    if sentinels:
        assumptions.append("cells reading " + ", ".join(f"{k!r}" for k in sentinels) + f" treated as empty ({sum(sentinels.values())})")

    types: dict[str, str] = {}
    notes: dict[str, set[str]] = {}
    for j, name in enumerate(columns):
        kinds = {_kind(r[j]) for r in cells} - {"empty"}
        types[name] = kinds.pop() if len(kinds) == 1 else "text"
        notes[name] = set()
    rows = [{name: (None if r[j] is None else _coerce(r[j], types[name], notes[name])) for j, name in enumerate(columns)} for r in cells]
    assumptions.append("inferred types: " + ", ".join(f"{c}={t}" for c, t in types.items()))
    for j, name in enumerate(columns):
        if types[name] == "text" and any(isinstance(r[j], str) and re.fullmatch(r"0\d+", r[j].strip()) for r in cells):
            assumptions.append(f"'{name}' has values with leading zeros, so it is read as text (an identifier), not as numbers")
    for name in columns:
        for note in sorted(notes[name]):
            assumptions.append(f"field '{name}': {note}")
    return Table(columns, rows, types, assumptions)


def _plain(v: Any) -> Any:
    return _dec_str(v) if isinstance(v, Decimal) else v


def _records(t: Table) -> list[dict[str, Any]]:
    """The table as records for the list modes: numbers stay `Decimal` (normalised) so they sort and sum as numbers."""
    return [{c: (Decimal(_dec_str(r[c])) if isinstance(r[c], Decimal) else r[c]) for c in t.columns} for r in t.rows]


def _jsonify(v: Any) -> Any:
    """Back to the module's convention for the wire: every `Decimal` becomes a plain decimal string."""
    if isinstance(v, Decimal):
        return _dec_str(v)
    if isinstance(v, dict):
        return {k: _jsonify(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonify(x) for x in v]
    return v


#: Which parameters of the list modes may carry CSV text instead of a list.
_CSV_INPUTS = {"set_ops": ("a", "b"), "flatten": ("data",)}


def _inline_csv(mode: str, p: dict[str, Any]) -> list[str]:
    """Replace CSV text in the list modes' inputs with records; returns how each one was read."""
    notes: list[str] = []
    # `data` joins `items` here because it is now accepted wherever records are (#78); a mode
    # that does not take it simply has nothing under that name to convert.
    keys = _CSV_INPUTS.get(mode, ("items", "data"))
    converted = [key for key in keys if isinstance(p.get(key), str)]
    for key in converted:
        t = _load(p, key)
        p[key] = _records(t)
        notes.extend(t.assumptions if len(converted) == 1 else [f"{key}: {a}" for a in t.assumptions])
    return notes


# --------------------------------------------------------------------------- #
# Table helpers
# --------------------------------------------------------------------------- #


def _out(d: Decimal, decimals: int | None) -> str:
    if decimals is None:
        return _dec_str(d)
    return format(d.quantize(Decimal(1).scaleb(-int(decimals)), rounding=ROUND_HALF_UP), "f")


def _label(v: Any) -> str:
    if v is None:
        return "(blank)"
    if isinstance(v, bool):
        return "true" if v else "false"
    return _dec_str(v) if isinstance(v, Decimal) else str(v)


def _echo(rows: list[dict[str, Any]], columns: list[str], warnings: list[str], limit: int | None = ECHO_ROWS) -> list[dict[str, Any]]:
    shown = rows if limit is None else rows[:limit]
    if limit is not None and len(rows) > limit:
        warnings.append(f"showing the first {limit} of {len(rows)} rows")
    return [{c: _plain(r.get(c)) for c in columns} for r in shown]


def _check(t: Table, names: list[str]) -> None:
    missing = [n for n in names if n not in t.types]
    if missing:
        raise ToolError(f"unknown field(s) {', '.join(repr(m) for m in missing)}; the table has {', '.join(t.columns)}")


def _cols(t: Table, p: dict[str, Any]) -> list[str]:
    names = p.get("columns")
    if names is None:
        return list(t.columns)
    names = [names] if isinstance(names, str) else [str(n) for n in names]
    _check(t, names)
    return names


def _by(t: Table, p: dict[str, Any], required: bool = True) -> list[str]:
    by = p.get("by")
    if by is None:
        if required:
            raise ToolError("'by' names the field(s) to group on")
        return []
    names = [by] if isinstance(by, str) else [str(b) for b in by]
    _check(t, names)
    return names


def _aggs(p: dict[str, Any]) -> list[str] | None:
    agg = p.get("agg")
    if agg is None:
        return None
    aggs = [agg] if isinstance(agg, str) else list(agg)
    aggs = ["avg" if str(a).lower() == "mean" else str(a).lower() for a in aggs]
    bad = [a for a in aggs if a not in AGGS]
    if bad:
        raise ToolError(f"unknown aggregate {', '.join(repr(b) for b in bad)}; use {', '.join(AGGS)}")
    return aggs


def _metric(t: Table, p: dict[str, Any], assumptions: list[str]) -> str:
    """The numeric field to work on: `column`, or the only numeric field there is - never a guess between two."""
    column = p.get("column")
    if column is None:
        numeric = [c for c in t.columns if t.types[c] == "number"]
        if not numeric:
            raise ToolError("no numeric field to use; pass 'column'")
        if len(numeric) > 1:
            raise Ambiguous("more than one numeric field; say which with 'column'", "column", numeric)
        column = numeric[0]
        assumptions.append(f"'{column}' used: it is the only numeric field")
    _check(t, [column])
    if t.types[column] != "number":
        raise ToolError(f"field '{column}' is {t.types[column]}, not numeric")
    return column


def _dec_note(p: dict[str, Any], assumptions: list[str]) -> int | None:
    decimals = whole(p["decimals"], "decimals", lo=0, hi=20) if p.get("decimals") is not None else None
    if decimals is not None:
        assumptions.append(f"computed values rounded to {decimals} decimals, half-up")
    return decimals


def _median(nums: list[Decimal]) -> Decimal:
    s = sorted(nums)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _agg_value(values: list[Any], agg: str, decimals: int | None) -> Any:
    """One aggregate over a list of typed cells; `count` counts rows, the rest skip blanks."""
    if agg == "count":
        return len(values)
    nums = [v for v in values if isinstance(v, Decimal)]
    if not nums:
        return None
    if agg == "sum":
        d = sum(nums, Decimal(0))
    elif agg == "avg":
        d = sum(nums, Decimal(0)) / len(nums)
    elif agg == "min":
        d = min(nums)
    elif agg == "max":
        d = max(nums)
    else:
        d = _median(nums)
    return _out(d, decimals)


def _sort_key(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, str):
        return v.casefold()
    return v


def _group_order(key: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple((v is None, _sort_key(v) if v is not None else 0) for v in key)


# --------------------------------------------------------------------------- #
# Table modes
# --------------------------------------------------------------------------- #


def _summarize(t: Table, p: dict[str, Any]) -> dict[str, Any]:
    a = list(t.assumptions)
    cols = _cols(t, p)
    decimals = _dec_note(p, a)
    out: dict[str, Any] = {}
    for c in cols:
        values = [r[c] for r in t.rows]
        present = [v for v in values if v is not None]
        entry: dict[str, Any] = {"type": t.types[c], "count": len(present), "nulls": len(values) - len(present)}
        if t.types[c] == "number":
            entry.update({agg: _agg_value(present, agg, decimals) for agg in ("sum", "avg", "min", "max", "median")})
        elif t.types[c] == "date":
            entry.update({"min": min(present) if present else None, "max": max(present) if present else None})
        elif t.types[c] == "bool":
            entry.update({"true": sum(1 for v in present if v), "false": sum(1 for v in present if not v)})
        else:
            entry["distinct"] = len({_hashable(v) for v in present})
        out[c] = entry
    return ok({"count": len(t.rows), "fields": out, "types": {c: t.types[c] for c in cols}}, assumptions=a)


def _expected(kind: str, op: str, value: Any) -> Any:
    if op in ("empty", "not_empty"):
        return None
    if op in ("contains", "starts_with", "ends_with"):
        return _label(value)
    if op in ("in", "not_in"):
        if not isinstance(value, list):
            raise ToolError(f"'{op}' needs a list as value")
        return [_expected(kind, "eq", v) for v in value]
    if kind == "number":
        if isinstance(value, bool) or value is None:
            raise ToolError(f"cannot compare a number with {value!r}")
        return _coerce(value, "number", set())
    if kind == "bool":
        if op in ("eq", "ne"):
            return flag(value, "value")
        return _coerce(value, "bool", set())
    if op in ("gt", "gte", "lt", "lte") and _num(value) is not None and not isinstance(value, str):
        raise ToolError(f"'{op}' compares numbers, but this column holds text (or a mix of text and numbers), so it would compare as text", hint="Clean the column so every value is a number, or use contains/eq.")
    return _label(value)


def _holds(actual: Any, op: str, expected: Any) -> bool:
    if op == "empty":
        return actual is None
    if op == "not_empty":
        return actual is not None
    if actual is None:
        return op in ("ne", "not_in")
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return actual in expected
    if op == "not_in":
        return actual not in expected
    if op in ("contains", "starts_with", "ends_with"):
        s = _label(actual)
        return {"contains": expected in s, "starts_with": s.startswith(expected), "ends_with": s.endswith(expected)}[op]
    if isinstance(actual, bool) or isinstance(expected, bool):
        raise ToolError(f"'{op}' does not apply to a boolean field")
    return {"gt": actual > expected, "gte": actual >= expected, "lt": actual < expected, "lte": actual <= expected}[op]


def _filter(t: Table, p: dict[str, Any]) -> dict[str, Any]:
    a, w = list(t.assumptions), []
    where = p.get("where")
    if not isinstance(where, list) or not where:
        raise ToolError("'where' is a non-empty list of {field, op, value} predicates")
    preds = []
    for pred in where:
        if not isinstance(pred, dict) or not pred.get("field"):
            raise ToolError("each predicate is {field, op, value}")
        fld, op = str(pred["field"]), str(pred.get("op", "eq")).lower()
        _check(t, [fld])
        if op not in FILTER_OPS:
            raise ToolError(f"unknown op {op!r}; use {', '.join(FILTER_OPS)}")
        preds.append((fld, op, _expected(t.types[fld], op, pred.get("value"))))
    kept = [r for r in t.rows if all(_holds(r[f], op, v) for f, op, v in preds)]
    a.append("every predicate must hold (AND); text comparisons are case-sensitive")
    return ok({"items": _echo(kept, _cols(t, p), w), "count": len(kept), "removed": len(t.rows) - len(kept)}, assumptions=a, warnings=w)


def _pivot(t: Table, p: dict[str, Any]) -> dict[str, Any]:
    a = list(t.assumptions)
    by = _by(t, p)
    across = p.get("pivot_columns")
    if across is None:
        raise ToolError("'pivot_columns' names the field whose values become the pivot columns")
    across = str(across)
    _check(t, [across])
    column = p.get("column")
    aggs = _aggs(p)
    if aggs is not None and len(aggs) != 1:
        raise ToolError("pivot takes a single aggregate")
    agg = aggs[0] if aggs else ("sum" if column is not None else "count")
    if column is None:
        if agg != "count":
            raise ToolError("'column' is required for sum, avg, min, max or median")
        a.append("no 'column' given, so each cell counts rows")
    else:
        _check(t, [column])
        if t.types[column] != "number" and agg != "count":
            raise ToolError(f"field '{column}' is {t.types[column]}, not numeric; only count applies")
    decimals = _dec_note(p, a)
    distinct = len({_label(r[across]) for r in t.rows})
    if distinct > MAX_PIVOT_COLUMNS:
        raise TooLarge(
            f"'{across}' has {distinct:,} distinct values, so the table would have that many columns; "
            f"the limit is {MAX_PIVOT_COLUMNS:,}",
            details={"pivot_columns": across, "distinct": distinct, "limit": MAX_PIVOT_COLUMNS},
            hint="Group the field into fewer buckets first, or pivot on a lower-cardinality field.",
        )
    cells: dict[tuple[Any, ...], dict[str, list[Any]]] = {}
    labels: dict[str, Any] = {}
    for r in t.rows:
        label = _label(r[across])
        labels.setdefault(label, r[across])
        cells.setdefault(tuple(r[b] for b in by), {}).setdefault(label, []).append(r[column] if column is not None else r)
    ordered = sorted(labels, key=lambda lb: (labels[lb] is None, _sort_key(labels[lb]) if labels[lb] is not None else 0))
    clash = [lb for lb in ordered if lb in by or lb == "total"]
    if clash:
        raise ToolError(f"pivot value(s) {', '.join(repr(c) for c in clash)} collide with the row key or 'total' column names")
    rows = []
    col_values: dict[str, list[Any]] = {lb: [] for lb in ordered}
    grand: list[Any] = []
    for key in sorted(cells, key=_group_order):
        entry: dict[str, Any] = {b: _plain(v) for b, v in zip(by, key, strict=True)}
        row_values: list[Any] = []
        for lb in ordered:
            vals = cells[key].get(lb)
            entry[lb] = _agg_value(vals, agg, decimals) if vals else None
            if vals:
                row_values.extend(vals)
                col_values[lb].extend(vals)
        entry["total"] = _agg_value(row_values, agg, decimals)
        grand.extend(row_values)
        rows.append(entry)
    totals = {lb: (_agg_value(v, agg, decimals) if v else None) for lb, v in col_values.items()}
    totals["total"] = _agg_value(grand, agg, decimals)
    a.append(f"cells are {agg} of '{column}'" if column is not None else "cells are row counts")
    a.append("an empty cell (no rows for that combination) is null; totals use the same aggregate over every underlying value")
    return ok({"columns": by + ordered + ["total"], "rows": rows, "totals": totals, "row_count": len(rows)}, assumptions=a)


def _running(t: Table, p: dict[str, Any]) -> dict[str, Any]:
    a, w = list(t.assumptions), []
    column = _metric(t, p, a)
    by = _by(t, p, required=False)
    decimals = _dec_note(p, a)
    acc: dict[tuple[Any, ...], Decimal] = {}
    rows = []
    for r in t.rows:
        key = tuple(r[b] for b in by)
        acc[key] = acc.get(key, Decimal(0)) + (r[column] if r[column] is not None else Decimal(0))
        rows.append({**r, "running": Decimal(_out(acc[key], decimals))})
    a.append("rows are accumulated in the order given; a blank cell adds nothing")
    result: dict[str, Any] = {"items": _echo(rows, _cols(t, p) + ["running"], w), "count": len(rows), "column": column, "total": _out(sum(acc.values(), Decimal(0)), decimals)}
    if by:
        result["by"] = by
        result["totals"] = [{**{b: _plain(v) for b, v in zip(by, key, strict=True)}, "total": _out(v, decimals)} for key, v in acc.items()]
    return ok(result, assumptions=a, warnings=w)


def _outliers(t: Table, p: dict[str, Any]) -> dict[str, Any]:
    a, w = list(t.assumptions), []
    column = _metric(t, p, a)
    decimals = _dec_note(p, a)
    present = [(i, r) for i, r in enumerate(t.rows, 1) if r[column] is not None]
    if len(present) < 4:
        raise ToolError(f"the IQR rule needs at least 4 numeric values; '{column}' has {len(present)}")
    xs = sorted(r[column] for _i, r in present)
    n = len(xs)
    q1, q3 = _median(xs[: (n + 1) // 2]), _median(xs[n // 2 :])  # Tukey's hinges: an odd count keeps the middle value in both halves
    iqr = q3 - q1
    lo, hi = q1 - _IQR_MULTIPLIER * iqr, q3 + _IQR_MULTIPLIER * iqr
    flagged = [{"row": i, "value": _dec_str(r[column]), "side": "low" if r[column] < lo else "high", **r} for i, r in present if r[column] < lo or r[column] > hi]
    a.append("Tukey's hinges: Q1 and Q3 are the medians of the lower and upper halves (the middle value included in both for an odd count); fences are Q1 - 1.5*IQR and Q3 + 1.5*IQR")
    result = {"column": column, "count": n, "q1": _out(q1, decimals), "q3": _out(q3, decimals), "iqr": _out(iqr, decimals), "lower_fence": _out(lo, decimals), "upper_fence": _out(hi, decimals), "multiplier": _dec_str(_IQR_MULTIPLIER), "outliers": _echo(flagged, ["row", "value", "side"] + t.columns, w), "outlier_count": len(flagged)}
    return ok(result, assumptions=a, warnings=w)


#: Characters that make Excel, LibreOffice and Sheets treat a cell as a formula rather than
#: as text. A cell starting with one of these is data to us and code to them (#28 SS5).
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _neutralise(cell: str) -> tuple[str, bool]:
    """Prefix a formula-shaped cell with `'`, which every spreadsheet reads as "text follows"."""
    if not cell or not cell.startswith(_FORMULA_LEAD):
        return cell, False
    if cell[0] == "-":
        # A negative number is data, not a lead-in to `-1+cmd()`. Escaping it would
        # corrupt every column of deltas in the file.
        try:
            float(cell)
            return cell, False
        except ValueError:
            pass
    return "'" + cell, True


def _to_csv(t: Table, p: dict[str, Any]) -> dict[str, Any]:
    a = list(t.assumptions)
    cols = _cols(t, p)
    delimiter = p.get("delimiter") or ","
    if len(delimiter) != 1:
        raise ToolError("'delimiter' must be a single character")
    escape = p.get("escape_formulas", True)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    escaped = 0

    def cell(value: Any) -> str:
        nonlocal escaped
        text = "" if value is None else _label(value)
        if not escape:
            return text
        text, changed = _neutralise(text)
        escaped += changed
        return text

    writer.writerow([cell(c) for c in cols])
    for r in t.rows:
        writer.writerow([cell(r[c]) for c in cols])
    a.append("numbers are written in plain decimal form (no thousands separators or symbols); blanks are empty cells")
    if escaped:
        a.append(f"{escaped} cell(s) starting with = + - @ tab or CR were prefixed with an apostrophe so a spreadsheet reads them as text, not as a formula")
    warnings = [] if escape else ["escape_formulas=false: cells starting with = + - @ are not escaped, so a spreadsheet will execute them"]
    return ok({"csv": buf.getvalue(), "count": len(t.rows), "columns": cols, "escaped_cells": escaped if escape else None}, assumptions=a, warnings=warnings)


_LIST_MODES = {"set_ops": _set_ops, "group_by": _group_by, "aggregate": _aggregate, "pick_fields": _pick_fields, "flatten": _flatten, "unflatten": _unflatten, "paginate": _paginate, "find_duplicates": _find_duplicates, "sort_by": _sort_by, "chunk": _chunk}
_TABLE_MODES = {"filter": _filter, "pivot": _pivot, "running": _running, "outliers": _outliers, "summarize": _summarize, "to_csv": _to_csv}


@tool
def collections(mode: str = "set_ops", **params: Any) -> dict[str, Any]:
    """List/record utilities. Modes: set_ops, group_by, aggregate, pick_fields, flatten, unflatten, paginate, find_duplicates, sort_by, chunk, filter, pivot, running, outliers, summarize, to_csv."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    check_params("collections", mode, p, MODE_PARAMS)
    if mode in _TABLE_MODES:
        return _TABLE_MODES[mode](_load(p), p)
    notes = _inline_csv(mode, p)
    out = _LIST_MODES[mode](p)
    if notes:
        out = _jsonify(out)
        out["assumptions"] = notes + out["assumptions"]
    return out

#: Shared fixtures for the documented examples below.
_EX_CSV = """region,rep,amount,date
north,Asha,"1,200.50",2026-01-05
south,Bo,890.00,2026-01-06
north,Asha,430.25,2026-01-07
east,Chen,2100.00,2026-01-08
south,Bo,75.75,2026-01-09
"""
_EX_SALES = "day,sales\n" + "\n".join(f"d{i},{v}" for i, v in enumerate([12, 15, 11, 14, 13, 16, 12, 15, 14, 95], 1)) + "\n"
_EX_ORDERS = [{"id": "A-1", "region": "north", "amount": "1200.50", "rep": {"name": "Asha"}}, {"id": "A-2", "region": "south", "amount": "890.00", "rep": {"name": "Bo"}}, {"id": "A-3", "region": "north", "amount": "430.25", "rep": {"name": "Asha"}}, {"id": "A-4", "region": "east", "amount": "2100.00", "rep": {"name": "Chen"}}, {"id": "A-5", "region": "south", "amount": "75.75", "rep": {"name": "Bo"}}]

#: Worked examples for the reference page, one list per mode. Every one of them is
#: executed when /docs/tools/collections is built and sorted by the result into
#: "Examples" (the call succeeded) and "Fails when" (it did not), so a fixture never
#: states an expectation of its own. Mark anything whose output depends on the
#: current instant with "volatile": True.
EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "set_ops": [
        {
            "caption": "Two lists of SKUs: both directions of difference at once.",
            "args": {"mode": "set_ops", "a": ["A1", "B2", "C3", "D4"], "b": ["B2", "D4", "E5"]},
        },
        {
            "caption": "A union, with the duplicate collapse reported.",
            "args": {"mode": "set_ops", "a": ["x", "y", "y"], "b": ["y", "z"], "op": "union"},
        },
        {
            "caption": "Comparing records on one field rather than whole objects.",
            "args": {"mode": "set_ops", "a": [{"sku": "A1", "qty": 2}, {"sku": "B2", "qty": 1}], "b": [{"sku": "B2", "qty": 9}], "op": "difference", "key": "sku"},
        },
        {
            "caption": "Both sides must be lists or CSV text.",
            "args": {"mode": "set_ops", "a": ["x"], "b": {"y": 1}},
        },
        {
            "caption": "An unknown operation.",
            "args": {"mode": "set_ops", "a": ["x"], "b": ["y"], "op": "xor"},
        },
    ],
    "group_by": [
        {
            "caption": "Sales by region, with an exact decimal sum and average.",
            "args": {"mode": "group_by", "items": _EX_ORDERS, "key": "region", "agg_field": "amount", "agg": ["sum", "avg", "count"], "include_items": False},
        },
        {
            "caption": "Grouping on a nested path, keeping the members.",
            "args": {"mode": "group_by", "items": _EX_ORDERS, "key": "rep.name", "include_items": True},
        },
        {
            "caption": "The same grouping over CSV text: delimiter, header and field types are detected and stated.",
            "args": {"mode": "group_by", "items": _EX_CSV, "key": "region", "agg_field": "amount", "agg": ["sum", "count"], "include_items": False},
        },
        {
            "caption": "`items` must be a list.",
            "args": {"mode": "group_by", "items": {"a": 1}, "key": "a"},
        },
        {
            "caption": "An unknown aggregate.",
            "args": {"mode": "group_by", "items": _EX_ORDERS, "key": "region", "agg_field": "amount", "agg": ["median"]},
        },
    ],
    "aggregate": [
        {
            "caption": "Totals across a field.",
            "args": {"mode": "aggregate", "items": _EX_ORDERS, "field": "amount"},
        },
        {
            "caption": "Distinct values of a field.",
            "args": {"mode": "aggregate", "items": _EX_ORDERS, "field": "region", "ops": ["count", "count_distinct", "list"]},
        },
        {
            "caption": "`items` must be a list or CSV text.",
            "args": {"mode": "aggregate", "items": {"a": 1}},
        },
        {
            "caption": "An unknown aggregate.",
            "args": {"mode": "aggregate", "items": _EX_ORDERS, "field": "amount", "ops": ["stdev"]},
        },
    ],
    "pick_fields": [
        {
            "caption": "Two fields renamed onto the same key would destroy one of them, so it is refused.",
            "args": {"mode": "pick_fields", "items": [{"a": 1, "b": 2}], "fields": ["a", "b"], "rename": {"a": "z", "b": "z"}},
        },
        {
            "caption": "Flattening a nested field into a table-shaped row.",
            "args": {"mode": "pick_fields", "items": _EX_ORDERS, "fields": ["id", "rep.name", "amount"], "short_names": True},
        },
        {
            "caption": "Renaming as you project; a missing path becomes null.",
            "args": {"mode": "pick_fields", "items": _EX_ORDERS, "fields": ["id", "rep.email"], "rename": {"rep.email": "contact"}},
        },
        {
            "caption": "`items` must be a list.",
            "args": {"mode": "pick_fields", "items": {"id": 1}, "fields": ["id"]},
        },
    ],
    "flatten": [
        {
            "caption": "A nested object flattened to dotted keys.",
            "args": {"mode": "flatten", "data": {"order": {"id": "A-1", "rep": {"name": "Asha"}}, "tags": ["rush", "gift"]}},
        },
        {
            "caption": "Limiting the depth leaves deeper structures intact.",
            "args": {"mode": "flatten", "data": {"order": {"id": "A-1", "rep": {"name": "Asha"}}}, "depth": 2},
        },
        {
            "caption": "A list of lists, flattened.",
            "args": {"mode": "flatten", "data": [1, [2, [3, 4]], 5]},
        },
        {
            "caption": "`data` is required.",
            "args": {"mode": "flatten"},
        },
    ],
    "unflatten": [
        {
            "caption": "Dotted keys back into a nested object.",
            "args": {"mode": "unflatten", "data": {"order.id": "A-1", "order.rep.name": "Asha", "order.total": 1200.5}},
        },
        {
            "caption": "Bracketed indices rebuild arrays.",
            "args": {"mode": "unflatten", "data": {"items[0].sku": "A1", "items[1].sku": "B2", "items[1].qty": 3}},
        },
        {
            "caption": "`data` is required.",
            "args": {"mode": "unflatten"},
        },
    ],
    "paginate": [
        {
            "caption": "The middle page of five items, three to a page.",
            "args": {"mode": "paginate", "items": _EX_ORDERS, "page": 2, "per_page": 3},
        },
        {
            "caption": "A page past the end: empty, and the flags explain it.",
            "args": {"mode": "paginate", "items": _EX_ORDERS, "page": 9, "per_page": 3},
        },
        {
            "caption": "Page numbers start at 1.",
            "args": {"mode": "paginate", "items": _EX_ORDERS, "page": 0},
        },
        {
            "caption": "`per_page` must be at least 1.",
            "args": {"mode": "paginate", "items": _EX_ORDERS, "per_page": 0},
        },
        {
            "caption": "`items` must be a list or CSV text.",
            "args": {"mode": "paginate", "items": {"a": 1}},
        },
    ],
    "find_duplicates": [
        {
            "caption": "Repeated addresses, matched without regard to case.",
            "args": {"mode": "find_duplicates", "items": ["a@x.com", "b@x.com", "A@X.com", "c@x.com", "b@x.com"], "case_insensitive": True},
        },
        {
            "caption": "Duplicate records on one field.",
            "args": {"mode": "find_duplicates", "items": _EX_ORDERS, "key": "rep.name"},
        },
        {
            "caption": "`items` must be a list or CSV text.",
            "args": {"mode": "find_duplicates", "items": {"a": 1}},
        },
    ],
    "sort_by": [
        {
            "caption": "Region ascending, then amount descending within each region.",
            "args": {"mode": "sort_by", "items": _EX_ORDERS, "keys": [{"field": "region"}, {"field": "amount", "order": "desc"}]},
        },
        {
            "caption": "The single-key shorthand.",
            "args": {"mode": "sort_by", "items": _EX_ORDERS, "key": "amount", "order": "desc"},
        },
        {
            "caption": "Sort keys are required.",
            "args": {"mode": "sort_by", "items": _EX_ORDERS},
        },
        {
            "caption": "`items` must be a list or CSV text.",
            "args": {"mode": "sort_by", "items": {"a": 1}, "key": "id"},
        },
    ],
    "chunk": [
        {
            "caption": "Fixed-size batches.",
            "args": {"mode": "chunk", "items": [1, 2, 3, 4, 5, 6, 7], "size": 3},
        },
        {
            "caption": "A fixed number of near-equal chunks.",
            "args": {"mode": "chunk", "items": [1, 2, 3, 4, 5, 6, 7], "n": 3},
        },
        {
            "caption": "One of `size` or `n` is required.",
            "args": {"mode": "chunk", "items": [1, 2, 3]},
        },
        {
            "caption": "`items` must be a list or CSV text.",
            "args": {"mode": "chunk", "items": {"a": 1}, "size": 2},
        },
    ],
    "filter": [
        {
            "caption": "Rows where the amount is at least 500 — compared as numbers, not strings.",
            "args": {"mode": "filter", "items": _EX_CSV, "where": [{"field": "amount", "op": "gte", "value": 500}]},
        },
        {
            "caption": "Two predicates, both required, over records; only some fields echoed.",
            "args": {"mode": "filter", "items": _EX_ORDERS, "where": [{"field": "region", "op": "in", "value": ["north", "south"]}, {"field": "amount", "op": "lt", "value": "1000"}], "columns": ["id", "amount"]},
        },
        {
            "caption": "An op the tool does not know.",
            "args": {"mode": "filter", "items": _EX_CSV, "where": [{"field": "rep", "op": "like", "value": "A%"}]},
        },
    ],
    "pivot": [
        {
            "caption": "Regions down, reps across, amounts summed, with row and column totals.",
            "args": {"mode": "pivot", "items": _EX_CSV, "by": "region", "pivot_columns": "rep", "column": "amount"},
        },
        {
            "caption": "Counting rows instead of summing: reps down, regions across.",
            "args": {"mode": "pivot", "items": _EX_CSV, "by": "rep", "pivot_columns": "region", "agg": "count"},
        },
        {
            "caption": "`pivot_columns` is required.",
            "args": {"mode": "pivot", "items": _EX_CSV, "by": "region", "column": "amount"},
        },
    ],
    "running": [
        {
            "caption": "A cumulative total down the table.",
            "args": {"mode": "running", "items": _EX_CSV, "column": "amount", "columns": ["date", "amount"]},
        },
        {
            "caption": "Restarting the running total per region.",
            "args": {"mode": "running", "items": _EX_CSV, "column": "amount", "by": "region", "columns": ["region", "amount"]},
        },
        {
            "caption": "A text field cannot accumulate.",
            "args": {"mode": "running", "items": _EX_CSV, "column": "rep"},
        },
    ],
    "outliers": [
        {
            "caption": "Ten days of sales, one of them wildly off: the IQR fences and the row that breaks them.",
            "args": {"mode": "outliers", "items": _EX_SALES, "column": "sales"},
        },
        {
            "caption": "Fences rounded to two decimals; the only numeric field is assumed.",
            "args": {"mode": "outliers", "items": "v\n1.5\n2.25\n2.5\n3.75\n4\n40\n", "decimals": 2},
        },
        {
            "caption": "Too few values for quartiles.",
            "args": {"mode": "outliers", "items": "v\n1\n2\n3\n", "column": "v"},
        },
    ],
    "summarize": [
        {
            "caption": "A CSV with thousands separators: every field typed, every numeric field totalled exactly.",
            "args": {"mode": "summarize", "items": _EX_CSV},
        },
        {
            "caption": "Blank rows and `N/A` cells are skipped and counted, not silently zeroed.",
            "args": {"mode": "summarize", "items": "name,score\nAnn,10\n,\nBob,N/A\n\nCid,30\n"},
        },
        {
            "caption": "A field the table does not have.",
            "args": {"mode": "summarize", "items": _EX_CSV, "columns": ["amount", "total"]},
        },
    ],
    "to_csv": [
        {
            "caption": "A cell that a spreadsheet would run as a formula is prefixed with an apostrophe, and the count is reported.",
            "args": {"mode": "to_csv", "items": [{"name": "Asha", "note": "=cmd|' /C calc'!A0"}, {"name": "Ravi", "note": "fine"}]},
        },
        {
            "caption": "Records out as CSV text, numbers in plain decimal form.",
            "args": {"mode": "to_csv", "items": _EX_ORDERS, "columns": ["id", "region", "amount"]},
        },
        {
            "caption": "Only some fields, semicolon-separated.",
            "args": {"mode": "to_csv", "items": _EX_ORDERS, "columns": ["id", "amount"], "delimiter": ";"},
        },
        {
            "caption": "A field the records do not have.",
            "args": {"mode": "to_csv", "items": _EX_ORDERS, "columns": ["id", "total"]},
        },
    ],
}
