"""collections - exact set logic, grouping, sorting and reshaping of lists/records."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any

from ..contract import ToolError, ok, tool

MODES = ("set_ops", "group_by", "pick_fields", "flatten", "unflatten", "paginate", "find_duplicates", "sort_by", "aggregate", "chunk")

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
    if isinstance(v, str):
        return v.casefold() if ci else v
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, default=str)
    return v


def _items(p: dict[str, Any], key: str = "items") -> list[Any]:
    it = p.get(key)
    if not isinstance(it, list):
        raise ToolError(f"'{key}' must be a list")
    return it


def _set_ops(p: dict[str, Any]) -> dict[str, Any]:
    a, b = _items(p, "a"), _items(p, "b")
    op = (p.get("op") or "compare").lower()
    key = p.get("key")
    ci = bool(p.get("case_insensitive", False))

    def k(x: Any) -> Any:
        return _hashable(get_path(x, key) if key else x, ci)

    ka = {k(x): x for x in a}
    kb = {k(x): x for x in b}
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
    if len(ka) != len(a) or len(kb) != len(b):
        assumptions.append("duplicates within a list were collapsed")
    return ok(out, assumptions=assumptions)


def _num(v: Any) -> Decimal | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
    if isinstance(v, str):
        try:
            return Decimal(v.replace(",", ""))
        except Exception:
            return None
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
    items = _items(p)
    key = p.get("key")
    if not key:
        raise ToolError("'key' is required")
    groups: dict[Any, list[Any]] = defaultdict(list)
    for x in items:
        groups[_hashable(get_path(x, key))].append(x)
    agg_field, agg_ops = p.get("agg_field") or p.get("field"), p.get("agg") or ["count"]
    if isinstance(agg_ops, str):
        agg_ops = [agg_ops]
    out_groups = []
    for gk, members in groups.items():
        entry: dict[str, Any] = {"key": gk, "count": len(members)}
        if agg_field:
            entry["agg"] = _agg([get_path(m, agg_field) for m in members], agg_ops)
        if p.get("include_items", True) and len(items) <= 2000:
            entry["items"] = members
        out_groups.append(entry)
    out_groups.sort(key=lambda g: (str(type(g["key"])), g["key"] if g["key"] is not None else ""))
    return ok({"groups": out_groups, "group_count": len(out_groups), "total": len(items)})


def _aggregate(p: dict[str, Any]) -> dict[str, Any]:
    items = _items(p)
    field = p.get("field")
    ops = p.get("ops") or p.get("agg") or ["count", "sum", "avg", "min", "max"]
    if isinstance(ops, str):
        ops = [ops]
    values = [get_path(x, field) for x in items] if field else items
    return ok(_agg(values, ops), assumptions=["non-numeric values ignored for sum/avg/min/max"])


def _pick_fields(p: dict[str, Any]) -> dict[str, Any]:
    items = _items(p)
    fields = p.get("fields")
    if not fields:
        raise ToolError("'fields' is required")
    if isinstance(fields, str):
        fields = [fields]
    rename = p.get("rename") or {}
    out = []
    for x in items:
        row = {}
        for f in fields:
            row[rename.get(f, f.split(".")[-1] if p.get("short_names") else f)] = get_path(x, f)
        out.append(row)
    return ok({"items": out, "count": len(out)})


def _flatten(p: dict[str, Any]) -> dict[str, Any]:
    data = p.get("data") if "data" in p else p.get("items")
    depth = p.get("depth")
    sep = p.get("separator", ".")
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
                out[prefix] = obj

        walk(data, "", 0)
        return ok({"flat": out, "count": len(out)})
    raise ToolError("'data' must be a list or object")


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
                while len(cur) <= part:
                    cur.append(None)
                if last:
                    cur[part] = v
                else:
                    if cur[part] is None:
                        cur[part] = [] if nxt_is_idx else {}
                    cur = cur[part]
            else:
                if last:
                    cur[part] = v
                else:
                    if part not in cur or cur[part] is None:
                        cur[part] = [] if nxt_is_idx else {}
                    cur = cur[part]
    return ok({"data": root})


def _paginate(p: dict[str, Any]) -> dict[str, Any]:
    items = _items(p)
    per = int(p.get("per_page", 20))
    page = int(p.get("page", 1))
    if per < 1 or page < 1:
        raise ToolError("page and per_page must be >= 1")
    total_pages = max(1, -(-len(items) // per))
    start = (page - 1) * per
    return ok({"items": items[start:start + per], "page": page, "per_page": per, "total": len(items), "total_pages": total_pages, "has_next": page < total_pages, "has_prev": page > 1, "range": [start + 1, min(start + per, len(items))] if start < len(items) else None})


def _find_duplicates(p: dict[str, Any]) -> dict[str, Any]:
    items = _items(p)
    key = p.get("key")
    ci = bool(p.get("case_insensitive", False))
    where: dict[Any, list[int]] = defaultdict(list)
    for i, x in enumerate(items):
        where[_hashable(get_path(x, key) if key else x, ci)].append(i)
    dupes = [{"value": items[idx[0]] if not key else get_path(items[idx[0]], key), "indices": idx, "count": len(idx)} for idx in where.values() if len(idx) > 1]
    return ok({"duplicates": dupes, "duplicate_groups": len(dupes), "has_duplicates": bool(dupes), "counts": {str(k): len(v) for k, v in Counter({k: v for k, v in where.items()}).items()} if len(where) <= 200 else None})


def _sort_by(p: dict[str, Any]) -> dict[str, Any]:
    items = _items(p)
    keys = p.get("keys") or ([{"field": p["key"], "order": p.get("order", "asc")}] if p.get("key") else None)
    if not keys:
        raise ToolError("'keys' (list of {field, order}) is required")
    if isinstance(keys, str):
        keys = [{"field": keys}]
    keys = [{"field": k} if isinstance(k, str) else k for k in keys]
    srt = list(items)
    for spec in reversed(keys):
        field = spec.get("field")
        desc = str(spec.get("order", "asc")).lower() in ("desc", "descending")

        def kf(x: Any, field: Any = field) -> Any:
            v = get_path(x, field)
            n = _num(v)
            if v is None:
                return (2, 0, "")
            if n is not None and not isinstance(v, str):
                return (0, n, "")
            return (1, 0, str(v).casefold())

        srt.sort(key=kf, reverse=desc)
    return ok({"items": srt, "count": len(srt), "changed": srt != items}, assumptions=["stable multi-key sort; None sorts last; strings case-insensitive"])


def _chunk(p: dict[str, Any]) -> dict[str, Any]:
    items = _items(p)
    size = p.get("size")
    n = p.get("n")
    if size:
        size = int(size)
        chunks = [items[i:i + size] for i in range(0, len(items), size)]
    elif n:
        n = int(n)
        q, r = divmod(len(items), n)
        chunks, i = [], 0
        for c in range(n):
            ln = q + (1 if c < r else 0)
            chunks.append(items[i:i + ln])
            i += ln
    else:
        raise ToolError("chunk needs 'size' or 'n'")
    return ok({"chunks": chunks, "count": len(chunks), "sizes": [len(c) for c in chunks]})


@tool
def collections(mode: str = "set_ops", **params: Any) -> dict[str, Any]:
    """List/record utilities. Modes: set_ops, group_by, aggregate, pick_fields, flatten, unflatten, paginate, find_duplicates, sort_by, chunk."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    return {"set_ops": _set_ops, "group_by": _group_by, "aggregate": _aggregate, "pick_fields": _pick_fields, "flatten": _flatten, "unflatten": _unflatten, "paginate": _paginate, "find_duplicates": _find_duplicates, "sort_by": _sort_by, "chunk": _chunk}[mode](p)
