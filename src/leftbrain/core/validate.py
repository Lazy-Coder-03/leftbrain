"""validate - deterministic checks: JSON schema, rule assertions, IDs, SQL.

``assert`` is the objective-scoring replacement for an LLM judge: a list of
``{path, op, value}`` rules evaluated over a JSON document, each returning
pass/fail with the actual value.
"""

from __future__ import annotations

import ipaddress
import re
import uuid as _uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import jsonschema

from ..contract import TooLarge, ToolError, Unsupported, check_params, ok, tool
from .collections_ import get_path
from .text import check_pattern, redos_risk

MODES = ("json_schema", "assert", "id", "email", "url", "phone", "ip", "sql_parse", "regex", "cidr")

#: What each mode reads. Anything else in a call is a caller's mistake, not a default
#: to fall back on (#28 SS2a). Kept honest by tests/test_mode_params.py, which derives
#: the same map from the code and fails when the two drift.
MODE_PARAMS: dict[str, frozenset[str]] = {
    "json_schema": frozenset({"data", "schema"}),
    "assert": frozenset({"data", "rules"}),
    "id": frozenset({"kind", "value"}),
    "email": frozenset({"value"}),
    "url": frozenset({"value"}),
    "phone": frozenset({"region", "value"}),
    "ip": frozenset({"value"}),
    "sql_parse": frozenset({"dialect", "query", "sql", "value"}),
    "regex": frozenset({"pattern", "value"}),
    "cidr": frozenset({"network", "value"}),
}

# --------------------------------------------------------------------------- #
# JSON Schema
# --------------------------------------------------------------------------- #


#: Nesting a schema may have. jsonschema descends recursively, so 200-deep `allOf`
#: exhausts the C stack and used to surface as `internal` plus a traceback (#28 SS1).
MAX_SCHEMA_DEPTH = 50


def _schema_depth(node: Any) -> int:
    """Deepest nesting in a schema, measured iteratively so the check cannot itself recurse."""
    deepest, stack = 0, [(node, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > deepest:
            deepest = depth
            if deepest > MAX_SCHEMA_DEPTH:
                return deepest
        children = current.values() if isinstance(current, dict) else (current if isinstance(current, list) else ())
        stack.extend((child, depth + 1) for child in children if isinstance(child, (dict, list)))
    return deepest


#: Schema keywords whose value is a regular expression jsonschema hands to stdlib `re`.
_PATTERN_KEYS = ("pattern", "patternProperties")


def _check_schema_patterns(node: Any) -> None:
    """Refuse a schema carrying a runaway pattern; jsonschema runs them on stdlib `re`."""
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key == "pattern" and isinstance(value, str):
                    check_pattern(value, where="schema pattern")
                elif key == "patternProperties" and isinstance(value, dict):
                    for name in value:
                        check_pattern(str(name), where="schema patternProperties key")
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(x for x in current if isinstance(x, (dict, list)))


def _json_schema(p: dict[str, Any]) -> dict[str, Any]:
    schema, data = p.get("schema"), p.get("data")
    if schema is None:
        raise ToolError("'schema' is required")
    _check_schema_patterns(schema)
    depth = _schema_depth(schema)
    if depth > MAX_SCHEMA_DEPTH:
        raise TooLarge(
            f"the schema nests {depth} levels deep; the limit is {MAX_SCHEMA_DEPTH}",
            details={"depth": depth, "limit": MAX_SCHEMA_DEPTH},
            hint="Flatten the schema, or name the repeated part in $defs and $ref it once.",
        )
    try:
        cls = jsonschema.validators.validator_for(schema)
        cls.check_schema(schema)
    except jsonschema.SchemaError as e:
        raise ToolError(f"invalid schema: {e.message}") from None
    v = cls(schema, format_checker=jsonschema.FormatChecker())
    errors = []
    try:
        found = sorted(v.iter_errors(data), key=lambda e: list(e.absolute_path))
    except RecursionError:
        # A $ref cycle ({"$ref": "#"}) is an input the caller can fix, not a crash.
        raise ToolError(
            "the schema refers to itself ($ref cycle), so validating it never terminates",
            hint="Give the recursive branch a base case, or validate against a non-recursive schema.",
        ) from None
    for err in found:
        path = "$" + "".join(f"[{x}]" if isinstance(x, int) else f".{x}" for x in err.absolute_path)
        errors.append({"path": path, "message": err.message, "validator": err.validator, "schema_path": "/".join(str(x) for x in err.absolute_schema_path)})
    return ok({"valid": not errors, "errors": errors, "error_count": len(errors)})


# --------------------------------------------------------------------------- #
# Rules / assert
# --------------------------------------------------------------------------- #


def _dec(v: Any) -> Decimal | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
    if isinstance(v, str):
        try:
            return Decimal(v.strip().replace(",", ""))
        except (InvalidOperation, ValueError):
            return None
    return None


def _dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, str):
        from .datetimex import parse_dt

        try:
            d, _, _ = parse_dt(v, field="value")
            return d.replace(tzinfo=None) if d.tzinfo else d
        except Exception:
            return None
    return None


def _cmp_pair(actual: Any, expected: Any) -> tuple[Any, Any] | None:
    a, b = _dec(actual), _dec(expected)
    if a is not None and b is not None and not (isinstance(actual, str) and isinstance(expected, str) and (_dt(actual) and _dt(expected) and not re.fullmatch(r"[\d.,-]+", str(actual)))):
        return a, b
    da, db = _dt(actual), _dt(expected)
    if da is not None and db is not None:
        return da, db
    if isinstance(actual, str) and isinstance(expected, str):
        return actual, expected
    return None


_TYPES = {"string": str, "number": (int, float, Decimal), "integer": int, "boolean": bool, "array": list, "object": dict, "null": type(None)}


def _eval_rule(data: Any, rule: dict[str, Any]) -> dict[str, Any]:
    path = rule.get("path", "$")
    op = str(rule.get("op", "exists")).lower()
    expected = rule.get("value")
    actual = get_path(data, path)
    exists = actual is not None
    passed: bool
    reason = ""
    try:
        if op in ("exists", "present", "required"):
            passed = exists
        elif op in ("missing", "absent"):
            passed = not exists
        elif op in ("empty",):
            passed = not actual
        elif op in ("not_empty", "nonempty"):
            passed = bool(actual)
        elif op in ("eq", "==", "equals"):
            pair = _cmp_pair(actual, expected)
            passed = (pair[0] == pair[1]) if pair else actual == expected
        elif op in ("ne", "!=", "not_equals"):
            pair = _cmp_pair(actual, expected)
            passed = (pair[0] != pair[1]) if pair else actual != expected
        elif op in ("gt", ">", "gte", ">=", "lt", "<", "lte", "<=", "after", "before", "on_or_after", "on_or_before"):
            pair = _cmp_pair(actual, expected)
            if pair is None:
                passed, reason = False, "values are not comparable"
            else:
                a, b = pair
                passed = {"gt": a > b, ">": a > b, "after": a > b, "gte": a >= b, ">=": a >= b, "on_or_after": a >= b, "lt": a < b, "<": a < b, "before": a < b, "lte": a <= b, "<=": a <= b, "on_or_before": a <= b}[op]
        elif op == "between":
            if not (isinstance(expected, list) and len(expected) == 2):
                raise ToolError("between needs value=[min, max]")
            lo, hi = _cmp_pair(actual, expected[0]), _cmp_pair(actual, expected[1])
            passed = bool(lo and hi and lo[1] <= lo[0] <= hi[1])
        elif op in ("in", "one_of"):
            passed = actual in (expected or [])
        elif op == "not_in":
            passed = actual not in (expected or [])
        elif op == "contains":
            passed = expected in actual if isinstance(actual, (str, list, dict)) else False
        elif op == "not_contains":
            passed = expected not in actual if isinstance(actual, (str, list, dict)) else True
        elif op == "contains_all":
            passed = isinstance(actual, (str, list)) and all(e in actual for e in (expected or []))
        elif op == "contains_any":
            passed = isinstance(actual, (str, list)) and any(e in actual for e in (expected or []))
        elif op == "starts_with":
            passed = isinstance(actual, str) and actual.startswith(str(expected))
        elif op == "ends_with":
            passed = isinstance(actual, str) and actual.endswith(str(expected))
        elif op in ("matches", "regex"):
            passed = isinstance(actual, str) and re.search(str(expected), actual) is not None
        elif op in ("type", "is_type"):
            t = _TYPES.get(str(expected))
            if t is None:
                raise ToolError(f"unknown type {expected!r}")
            passed = isinstance(actual, t) and not (expected in ("number", "integer") and isinstance(actual, bool))
        elif op.startswith("len_") or op in ("length",):
            n = len(actual) if isinstance(actual, (str, list, dict)) else None
            if n is None:
                passed, reason = False, "value has no length"
            else:
                sub = op[4:] if op.startswith("len_") else "eq"
                if sub == "between":
                    passed = expected[0] <= n <= expected[1]
                else:
                    passed = {"eq": n == expected, "gt": n > expected, "gte": n >= expected, "lt": n < expected, "lte": n <= expected}[sub]
                actual = n
        elif op == "is_email":
            passed = _email_ok(actual)
        elif op == "is_url":
            passed = _url_ok(actual)
        elif op == "is_date":
            passed = _dt(actual) is not None
        elif op == "is_uuid":
            passed = _is_uuid(actual)
        elif op == "unique":
            passed = isinstance(actual, list) and len({_key(x) for x in actual}) == len(actual)
        elif op == "sum_eq":
            passed = isinstance(actual, list) and sum(_dec(x) or 0 for x in actual) == _dec(expected)
        elif op == "each":
            sub = expected if isinstance(expected, dict) else {}
            results = [_eval_rule(x, {**sub, "path": sub.get("path", "$")}) for x in (actual if isinstance(actual, list) else [])]
            passed = bool(results) and all(r["passed"] for r in results)
            reason = f"{sum(1 for r in results if not r['passed'])} item(s) failed" if not passed else ""
        else:
            raise ToolError(f"unknown op {op!r}")
    except (TypeError, ValueError) as e:
        passed, reason = False, f"{type(e).__name__}: {e}"
    out = {"path": path, "op": op, "expected": expected, "actual": actual, "passed": bool(passed)}
    if rule.get("id") is not None:
        out["id"] = rule["id"]
    if rule.get("message") and not passed:
        out["message"] = rule["message"]
    if reason:
        out["reason"] = reason
    if rule.get("weight") is not None:
        out["weight"] = rule["weight"]
    return out


def _key(x: Any) -> Any:
    import json

    return json.dumps(x, sort_keys=True, default=str) if isinstance(x, (dict, list)) else x


def _assert(p: dict[str, Any]) -> dict[str, Any]:
    data = p.get("data")
    rules = p.get("rules")
    if isinstance(rules, dict):
        rules = [rules]
    if not isinstance(rules, list) or not rules:
        raise ToolError("'rules' must be a non-empty list of {path, op, value}")
    results = [_eval_rule(data, r) for r in rules]
    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]
    total_w = sum(float(r.get("weight", 1)) for r in results)
    got_w = sum(float(r.get("weight", 1)) for r in passed)
    out = {
        "all_passed": not failed,
        "passed": len(passed),
        "failed": len(failed),
        "total": len(results),
        "score": round(got_w / total_w, 6) if total_w else None,
        "score_percent": round(100 * got_w / total_w, 2) if total_w else None,
        "failures": failed,
        "results": results,
    }
    return ok(out, assumptions=["score = weighted share of rules passed (weight defaults to 1)"])


# --------------------------------------------------------------------------- #
# Identifiers
# --------------------------------------------------------------------------- #


def _digits(s: str) -> str:
    return re.sub(r"[\s-]", "", s)


def luhn_ok(s: str) -> bool:
    d = _digits(s)
    if not d.isdigit() or len(d) < 2:
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


_CARD_BRANDS = [
    ("visa", re.compile(r"^4\d{12}(\d{3})?(\d{3})?$")),
    ("mastercard", re.compile(r"^(5[1-5]\d{14}|2(2[2-9]\d|[3-6]\d{2}|7[01]\d|720)\d{12})$")),
    ("amex", re.compile(r"^3[47]\d{13}$")),
    ("discover", re.compile(r"^(6011|65|64[4-9])\d{12,15}$")),
    ("rupay", re.compile(r"^(60|65|81|82|508)\d{13,14}$")),
    ("diners", re.compile(r"^3(0[0-5]|[68]\d)\d{11}$")),
    ("jcb", re.compile(r"^35(2[89]|[3-8]\d)\d{12}$")),
]


def iban_ok(s: str) -> bool:
    s = _digits(s).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False
    moved = s[4:] + s[:4]
    num = "".join(str(int(c, 36)) for c in moved)
    return int(num) % 97 == 1


_IBAN_LEN = {"AL": 28, "AD": 24, "AT": 20, "AZ": 28, "BH": 22, "BE": 16, "BA": 20, "BR": 29, "BG": 22, "CR": 22, "HR": 21, "CY": 28, "CZ": 24, "DK": 18, "DO": 28, "EE": 20, "FO": 18, "FI": 18, "FR": 27, "GE": 22, "DE": 22, "GI": 23, "GR": 27, "GL": 18, "GT": 28, "HU": 28, "IS": 26, "IE": 22, "IL": 23, "IT": 27, "JO": 30, "KZ": 20, "KW": 30, "LV": 21, "LB": 28, "LI": 21, "LT": 20, "LU": 20, "MK": 19, "MT": 31, "MR": 27, "MU": 30, "MC": 27, "MD": 24, "ME": 22, "NL": 18, "NO": 15, "PK": 24, "PS": 29, "PL": 28, "PT": 25, "QA": 29, "RO": 24, "SM": 27, "SA": 24, "RS": 22, "SK": 24, "SI": 19, "ES": 24, "SE": 24, "CH": 21, "TN": 24, "TR": 26, "AE": 23, "GB": 22, "VG": 24}


def gstin_ok(s: str) -> tuple[bool, str]:
    s = s.strip().upper()
    if not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", s):
        return False, "format must be 2 digits + PAN + entity code + 'Z' + check character"
    if not 1 <= int(s[:2]) <= 38 and s[:2] not in ("97", "99"):
        return False, "invalid state code"
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    total = 0
    for i, ch in enumerate(s[:14]):
        v = chars.index(ch)
        factor = 2 if i % 2 else 1
        prod = v * factor
        total += prod // 36 + prod % 36
    check = chars[(36 - total % 36) % 36]
    if check != s[14]:
        return False, f"check character should be {check}"
    return True, "ok"


_PAN_ENTITY = {"P": "individual", "C": "company", "H": "HUF", "F": "firm/LLP", "A": "AOP", "T": "trust", "B": "body of individuals", "L": "local authority", "J": "artificial juridical person", "G": "government", "K": "krish (rare)"}


def pan_ok(s: str) -> tuple[bool, dict[str, Any]]:
    s = s.strip().upper()
    if not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", s):
        return False, {"reason": "format must be 5 letters, 4 digits, 1 letter"}
    entity = _PAN_ENTITY.get(s[3])
    if entity is None:
        return False, {"reason": f"4th character {s[3]!r} is not a valid holder type"}
    return True, {"holder_type": entity, "surname_initial": s[4]}


_VERHOEFF_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1], [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VERHOEFF_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1], [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def verhoeff_ok(s: str) -> bool:
    d = _digits(s)
    if not d.isdigit():
        return False
    c = 0
    for i, ch in enumerate(reversed(d)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def aadhaar_ok(s: str) -> tuple[bool, str]:
    d = _digits(s)
    if not re.fullmatch(r"\d{12}", d):
        return False, "must be 12 digits"
    if d[0] in "01":
        return False, "cannot start with 0 or 1"
    return (True, "ok") if verhoeff_ok(d) else (False, "Verhoeff checksum failed")


def isbn_ok(s: str) -> tuple[bool, str]:
    d = _digits(s).upper()
    if len(d) == 10:
        if not re.fullmatch(r"\d{9}[\dX]", d):
            return False, "ISBN-10 format"
        total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(d))
        return (total % 11 == 0, "isbn10")
    if len(d) == 13:
        if not d.isdigit():
            return False, "ISBN-13 format"
        return (ean_ok(d), "isbn13")
    return False, "must be 10 or 13 characters"


def isbn_forms(d: str) -> tuple[str | None, str]:
    """Both forms of a valid ISBN: (isbn10 or None for a 979 book, isbn13)."""
    d = _digits(d).upper()
    if len(d) == 10:
        core = "978" + d[:9]
        check = (10 - sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(core)) % 10) % 10
        return d, core + str(check)
    if not d.startswith("978"):
        return None, d
    core = d[3:12]
    r = 11 - sum((10 - i) * int(c) for i, c in enumerate(core)) % 11
    return core + ("0" if r == 11 else "X" if r == 10 else str(r)), d


def ean_ok(s: str) -> bool:
    d = _digits(s)
    if not d.isdigit() or len(d) not in (8, 12, 13, 14):
        return False
    total = 0
    for i, ch in enumerate(reversed(d[:-1])):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10 == int(d[-1])


_VIN_TRANS = {**{str(i): i for i in range(10)}, "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8, "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9, "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9}
_VIN_W = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def vin_ok(s: str) -> bool:
    s = s.strip().upper()
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", s):
        return False
    total = sum(_VIN_TRANS[c] * w for c, w in zip(s, _VIN_W, strict=True))
    check = total % 11
    return s[8] == ("X" if check == 10 else str(check))


def _is_uuid(s: Any) -> bool:
    try:
        _uuid.UUID(str(s))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _id(p: dict[str, Any]) -> dict[str, Any]:
    kind = (p.get("kind") or "").lower()
    value = p.get("value")
    if value is None:
        raise ToolError("'value' is required")
    s = str(value)
    out: dict[str, Any] = {"kind": kind, "value": s}
    if kind in ("luhn", "card", "credit_card"):
        d = _digits(s)
        out["valid"] = luhn_ok(d)
        if kind != "luhn":
            out["brand"] = next((b for b, rx in _CARD_BRANDS if rx.match(d)), None)
            out["length"] = len(d)
            out["masked"] = f"{'*' * max(0, len(d) - 4)}{d[-4:]}" if d else None
            out["valid"] = out["valid"] and 12 <= len(d) <= 19
    elif kind == "iban":
        d = _digits(s).upper()
        valid = iban_ok(d)
        cc = d[:2]
        exp = _IBAN_LEN.get(cc)
        out.update({"valid": valid and (exp is None or len(d) == exp), "country": cc, "expected_length": exp, "length": len(d), "formatted": " ".join(d[i:i + 4] for i in range(0, len(d), 4))})
    elif kind == "gstin":
        v, reason = gstin_ok(s)
        out.update({"valid": v, "reason": reason, "state_code": s[:2] if v else None, "pan": s[2:12].upper() if v else None})
    elif kind == "pan":
        v, extra = pan_ok(s)
        out.update({"valid": v, **extra})
    elif kind == "aadhaar":
        v, reason = aadhaar_ok(s)
        out.update({"valid": v, "reason": reason, "masked": "XXXX-XXXX-" + _digits(s)[-4:] if v else None})
    elif kind == "isbn":
        v, which = isbn_ok(s)
        out.update({"valid": v, "format": which if v else None, "reason": None if v else which})
        if v:
            out["isbn10"], out["isbn13"] = isbn_forms(s)
    elif kind in ("ean", "upc", "gtin", "ean13", "ean8", "upca"):
        d = _digits(s)
        out.update({"valid": ean_ok(d), "length": len(d), "format": {8: "EAN-8", 12: "UPC-A", 13: "EAN-13", 14: "GTIN-14"}.get(len(d))})
    elif kind == "ifsc":
        u = s.strip().upper()
        out.update({"valid": re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", u) is not None, "bank_code": u[:4], "branch_code": u[5:]})
    elif kind == "vin":
        out["valid"] = vin_ok(s)
    elif kind == "uuid":
        v = _is_uuid(s)
        out["valid"] = v
        if v:
            u = _uuid.UUID(s)
            out.update({"version": u.version, "variant": str(u.variant)})
    elif kind == "upi":
        out["valid"] = re.fullmatch(r"[\w.\-]{2,256}@[a-zA-Z]{2,64}", s.strip()) is not None
    elif kind == "pincode":
        d = _digits(s)
        out["valid"] = re.fullmatch(r"[1-9]\d{5}", d) is not None
    elif kind == "ssn":
        d = _digits(s)
        out["valid"] = re.fullmatch(r"(?!000|666|9\d{2})\d{3}(?!00)\d{2}(?!0000)\d{4}", d) is not None
    elif kind == "verhoeff":
        out["valid"] = verhoeff_ok(s)
    elif kind == "mod97":
        d = _digits(s)
        out["valid"] = d.isdigit() and int(d) % 97 == 1
    else:
        raise ToolError("kind must be one of luhn, card, iban, gstin, pan, aadhaar, isbn, ean, upc, ifsc, vin, uuid, upi, pincode, ssn, verhoeff, mod97")
    return ok(out)


# --------------------------------------------------------------------------- #
# Email / URL / phone / IP
# --------------------------------------------------------------------------- #

_EMAIL_RE = re.compile(r"^(?P<local>[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*)@(?P<domain>(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63})$")
_DISPOSABLE = {"mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "yopmail.com", "trashmail.com", "sharklasers.com", "getnada.com", "dispostable.com"}


def _email_ok(s: Any) -> bool:
    return isinstance(s, str) and len(s) <= 254 and _EMAIL_RE.match(s.strip()) is not None and ".." not in s


def _email(p: dict[str, Any]) -> dict[str, Any]:
    s = str(p.get("value") or "").strip()
    m = _EMAIL_RE.match(s)
    valid = _email_ok(s)
    out: dict[str, Any] = {"valid": valid, "value": s}
    if m:
        out.update({"local": m.group("local"), "domain": m.group("domain").lower(), "normalized": f"{m.group('local')}@{m.group('domain').lower()}", "disposable": m.group("domain").lower() in _DISPOSABLE, "local_length_ok": len(m.group("local")) <= 64})
        out["valid"] = valid and out["local_length_ok"]
    elif s:
        out["reason"] = "does not match address syntax"
    return ok(out, assumptions=["syntax check only; deliverability not verified"])


def _url_ok(s: Any) -> bool:
    if not isinstance(s, str):
        return False
    try:
        u = urlparse(s.strip())
    except ValueError:
        return False
    return u.scheme in ("http", "https", "ftp", "ftps", "mailto", "tel", "file") and (bool(u.netloc) or u.scheme in ("mailto", "tel", "file"))


def _url(p: dict[str, Any]) -> dict[str, Any]:
    s = str(p.get("value") or "").strip()
    try:
        u = urlparse(s)
    except ValueError as e:
        return ok({"valid": False, "value": s, "reason": str(e)})
    valid = _url_ok(s)
    host = u.hostname
    out = {"valid": valid, "value": s, "scheme": u.scheme or None, "host": host, "port": u.port, "path": u.path or "/", "query": u.query or None, "fragment": u.fragment or None, "is_ip": False, "tld": None, "secure": u.scheme == "https"}
    if host:
        try:
            ipaddress.ip_address(host)
            out["is_ip"] = True
        except ValueError:
            if "." in host:
                out["tld"] = host.rsplit(".", 1)[-1]
            elif host != "localhost":
                out["valid"] = False
                out["reason"] = "host has no TLD"
    if not valid and "reason" not in out:
        out["reason"] = "missing scheme or host (e.g. https://example.com)"
    return ok(out, assumptions=["syntax check only; reachability not verified"])


_PHONE_RULES: dict[str, tuple[str, int, str]] = {
    "IN": ("91", 10, r"[6-9]\d{9}"),
    "US": ("1", 10, r"[2-9]\d{2}[2-9]\d{6}"),
    "CA": ("1", 10, r"[2-9]\d{2}[2-9]\d{6}"),
    "GB": ("44", 10, r"[1-9]\d{8,9}"),
    "UK": ("44", 10, r"[1-9]\d{8,9}"),
    "AE": ("971", 9, r"[2-9]\d{7,8}"),
    "SG": ("65", 8, r"[3689]\d{7}"),
    "AU": ("61", 9, r"[2-9]\d{8}"),
    "DE": ("49", 10, r"[1-9]\d{6,11}"),
    "FR": ("33", 9, r"[1-9]\d{8}"),
    "BD": ("880", 10, r"1\d{9}"),
    "PK": ("92", 10, r"3\d{9}"),
    "LK": ("94", 9, r"[1-9]\d{8}"),
    "NP": ("977", 10, r"9\d{9}"),
    "SA": ("966", 9, r"5\d{8}"),
    "MY": ("60", 9, r"1\d{8,9}"),
    "ID": ("62", 10, r"8\d{8,10}"),
    "PH": ("63", 10, r"9\d{9}"),
    "NG": ("234", 10, r"[789]\d{9}"),
    "ZA": ("27", 9, r"[1-9]\d{8}"),
    "BR": ("55", 11, r"[1-9]\d{9,10}"),
    "JP": ("81", 10, r"[1-9]\d{8,9}"),
    "CN": ("86", 11, r"1\d{10}"),
}


def _phone(p: dict[str, Any]) -> dict[str, Any]:
    raw = str(p.get("value") or "")
    region = (p.get("region") or "").upper()
    s = re.sub(r"[\s().\-]", "", raw)
    if s.startswith("00"):
        s = "+" + s[2:]
    out: dict[str, Any] = {"value": raw, "valid": False}
    digits = s[1:] if s.startswith("+") else s
    if not digits.isdigit():
        out["reason"] = "contains non-digit characters"
        return ok(out)
    if s.startswith("+"):
        if not 8 <= len(digits) <= 15:
            out["reason"] = "E.164 numbers have 8-15 digits"
            return ok(out)
        cc = next((c for c in sorted({r[0] for r in _PHONE_RULES.values()}, key=len, reverse=True) if digits.startswith(c)), None)
        national = digits[len(cc):] if cc else None
        matched = [k for k, (c, _n, rx) in _PHONE_RULES.items() if c == cc and national and re.fullmatch(rx, national)]
        out.update({"e164": "+" + digits, "country_code": cc, "national": national, "region_guess": matched[0] if matched else None, "valid": bool(matched) if cc else True})
        if cc and not matched:
            out["reason"] = f"national part does not match known pattern for +{cc}"
        return ok(out, assumptions=["E.164 syntax check; number existence not verified"])
    if not region:
        raise ToolError("number has no '+country code'; pass region (e.g. 'IN', 'US')")
    if region not in _PHONE_RULES:
        raise ToolError(f"unsupported region {region!r}")
    cc, n, rx = _PHONE_RULES[region]
    if digits.startswith("0") and region not in ("US", "CA"):
        digits = digits.lstrip("0")
        out["trunk_prefix_stripped"] = True
    if digits.startswith(cc) and len(digits) > n:
        digits = digits[len(cc):]
    valid = re.fullmatch(rx, digits) is not None
    out.update({"valid": valid, "national": digits, "e164": f"+{cc}{digits}" if valid else None, "region": region, "country_code": cc})
    if not valid:
        out["reason"] = f"does not match {region} national format"
    if region == "IN" and valid:
        out["type"] = "mobile"
    return ok(out, assumptions=["syntax check only; number existence not verified"])


def _ip(p: dict[str, Any]) -> dict[str, Any]:
    s = str(p.get("value") or "").strip()
    try:
        if "/" in s:
            net = ipaddress.ip_network(s, strict=False)
            return ok({"valid": True, "kind": "network", "version": net.version, "network": str(net), "num_addresses": net.num_addresses, "first": str(net[0]), "last": str(net[-1]), "private": net.is_private})
        ip = ipaddress.ip_address(s)
        return ok({"valid": True, "kind": "address", "version": ip.version, "private": ip.is_private, "loopback": ip.is_loopback, "multicast": ip.is_multicast, "global": ip.is_global, "reserved": ip.is_reserved, "compressed": ip.compressed, "exploded": ip.exploded})
    except ValueError as e:
        return ok({"valid": False, "value": s, "reason": str(e)})


def _network(s: Any, field: str, assumptions: list[str]) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    text = str(s).strip()
    try:
        net = ipaddress.ip_network(text, strict=False)
    except ValueError as e:
        raise ToolError(f"{field}: {e}") from None
    if str(net) != text and "/" in text:
        assumptions.append(f"{text} has host bits set; read as {net}")
    return net


def _cidr(p: dict[str, Any]) -> dict[str, Any]:
    spec = p.get("network")
    if spec is None:
        raise ToolError("'network' is required: a CIDR block, or a list of two or more to test for overlap")
    assumptions: list[str] = []
    if isinstance(spec, list):
        if len(spec) < 2:
            raise ToolError("'network' as a list needs two or more blocks to compare")
        nets = [_network(x, "network", assumptions) for x in spec]
        pairs = []
        for i in range(len(nets)):
            for j in range(i + 1, len(nets)):
                a, b = nets[i], nets[j]
                if a.version != b.version:
                    relation = "disjoint"
                elif a == b:
                    relation = "equal"
                elif b.subnet_of(a):
                    relation = "a_contains_b"
                elif a.subnet_of(b):
                    relation = "b_contains_a"
                else:
                    relation = "disjoint"
                pairs.append({"a": str(a), "b": str(b), "overlap": relation != "disjoint", "relation": relation})
        return ok({"networks": [str(n) for n in nets], "overlaps": any(x["overlap"] for x in pairs), "pairs": pairs}, assumptions=assumptions + ["CIDR blocks either nest or are disjoint; a partial overlap cannot occur"])
    net = _network(spec, "network", assumptions)
    hosts = net.num_addresses - 2 if net.version == 4 and net.prefixlen < 31 else net.num_addresses
    out: dict[str, Any] = {"network": str(net), "version": net.version, "prefixlen": net.prefixlen, "num_addresses": net.num_addresses, "usable_hosts": hosts, "first": str(net[0]), "last": str(net[-1]), "netmask": str(net.netmask), "hostmask": str(net.hostmask), "private": net.is_private}
    if p.get("value") is not None:
        raw = str(p["value"]).strip()
        try:
            member: Any = ipaddress.ip_network(raw, strict=False) if "/" in raw else ipaddress.ip_address(raw)
        except ValueError as e:
            raise ToolError(f"value: {e}") from None
        if member.version != net.version:
            out["contains"] = False
            assumptions.append(f"{raw} is IPv{member.version} and {net} is IPv{net.version}; they cannot overlap")
        elif isinstance(member, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
            out["contains"] = member.subnet_of(net)
        else:
            out["contains"] = member in net
        out["value"] = str(member)
    return ok(out, assumptions=assumptions)


# --------------------------------------------------------------------------- #
# SQL
# --------------------------------------------------------------------------- #

_WRITE_TYPES = {"Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable", "Truncate", "Grant", "Revoke", "Set", "Command"}


def _sql_parse(p: dict[str, Any]) -> dict[str, Any]:
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:  # pragma: no cover
        raise Unsupported("install sqlglot for sql_parse") from None
    sql = p.get("sql") or p.get("value") or p.get("query")
    if not sql:
        raise ToolError("'sql' is required")
    dialect = p.get("dialect")
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.ParseError as e:
        return ok({"valid": False, "errors": [str(err.get("description", e)) for err in getattr(e, "errors", [])] or [str(e)], "statement_count": 0, "read_only": None})
    out_stmts = []
    read_only = True
    warnings: list[str] = []
    for st in statements:
        if st is None:
            continue
        kind = type(st).__name__
        tables_read, tables_write = set(), set()
        for t in st.find_all(exp.Table):
            name = ".".join(x for x in (t.catalog, t.db, t.name) if x)
            tables_read.add(name)
        target = None
        if isinstance(st, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
            tgt = st.this
            tbl = tgt.find(exp.Table) if tgt is not None else None
            target = ".".join(x for x in (tbl.catalog, tbl.db, tbl.name) if x) if tbl is not None else None
            if target:
                tables_write.add(target)
                tables_read.discard(target)
        if isinstance(st, (exp.Create, exp.Drop, exp.Alter, exp.TruncateTable)):
            tbl = st.find(exp.Table)
            if tbl is not None:
                tables_write.add(tbl.name)
        is_write = kind in _WRITE_TYPES
        if is_write:
            read_only = False
        entry: dict[str, Any] = {"type": kind.upper(), "is_write": is_write, "tables_read": sorted(tables_read), "tables_write": sorted(tables_write), "columns": sorted({c.name for c in st.find_all(exp.Column) if c.name})[:200], "has_where": st.find(exp.Where) is not None, "has_limit": st.find(exp.Limit) is not None}
        if isinstance(st, (exp.Update, exp.Delete)) and not entry["has_where"]:
            warnings.append(f"{kind.upper()} without WHERE affects every row in {target or 'the table'}")
            entry["unbounded"] = True
        if isinstance(st, (exp.Drop, exp.TruncateTable)):
            warnings.append(f"{kind.upper()} is destructive")
        try:
            entry["normalized"] = st.sql(dialect=dialect, pretty=False)
        except Exception:  # pragma: no cover
            pass
        out_stmts.append(entry)
    return ok({"valid": True, "statement_count": len(out_stmts), "read_only": read_only, "statements": out_stmts, "dialect": dialect}, warnings=warnings, assumptions=[f"dialect: {dialect or 'generic'}"])


def _regex(p: dict[str, Any]) -> dict[str, Any]:
    pat = p.get("pattern") or p.get("value")
    if pat is None:
        raise ToolError("'pattern' is required")
    try:
        rx = re.compile(str(pat))
    except re.error as e:
        return ok({"valid": False, "reason": str(e), "position": e.pos})
    # This mode exists to judge a pattern, so a runaway one is reported rather than refused -
    # unlike `text.regex_match`, nothing here runs it against a subject (#28 SS1).
    risk = redos_risk(str(pat))
    return ok(
        {"valid": True, "groups": rx.groups, "named_groups": list(rx.groupindex), "backtracking_risk": risk},
        warnings=[f"this pattern can backtrack exponentially: {risk}"] if risk else [],
    )


@tool
def validate(mode: str = "assert", **params: Any) -> dict[str, Any]:
    """Deterministic checks. Modes: json_schema, assert, id, email, url, phone, ip, sql_parse, regex, cidr."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    check_params("validate", mode, p, MODE_PARAMS)
    return {"json_schema": _json_schema, "assert": _assert, "id": _id, "email": _email, "url": _url, "phone": _phone, "ip": _ip, "sql_parse": _sql_parse, "regex": _regex, "cidr": _cidr}[mode](p)

#: Shared fixture for the documented examples below.
_EX_LEAVE_DOC = {"employee": {"id": "E-19", "email": "asha@example.com"}, "leave": {"type": "casual", "days": 3, "start": "2025-09-01"}, "approvals": ["manager"]}

#: Shared fixture for the documented examples below.
_EX_LEAVE_SCHEMA = {"type": "object", "required": ["employee", "leave"], "properties": {"employee": {"type": "object", "required": ["id", "email"], "properties": {"id": {"type": "string"}, "email": {"type": "string", "format": "email"}}}, "leave": {"type": "object", "required": ["type", "days"], "properties": {"type": {"enum": ["casual", "sick", "earned"]}, "days": {"type": "integer", "minimum": 1, "maximum": 2}}}}}

#: Worked examples for the reference page, one list per mode. Every one of them is
#: executed when /docs/tools/validate is built and sorted by the result into
#: "Examples" (the call succeeded) and "Fails when" (it did not), so a fixture never
#: states an expectation of its own. Mark anything whose output depends on the
#: current instant with "volatile": True.
EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "json_schema": [
        {
            "caption": "A schema that refers to itself: validating it never terminates, so it is refused as input rather than crashing.",
            "args": {"mode": "json_schema", "schema": {"$ref": "#"}, "data": 1},
        },
        {
            "caption": "A document that fails two constraints, each located by path.",
            "args": {"mode": "json_schema", "schema": _EX_LEAVE_SCHEMA, "data": _EX_LEAVE_DOC},
        },
        {
            "caption": "The same schema against a document that passes.",
            "args": {"mode": "json_schema", "schema": _EX_LEAVE_SCHEMA, "data": {"employee": {"id": "E-19", "email": "asha@example.com"}, "leave": {"type": "sick", "days": 2}}},
        },
        {
            "caption": "A schema that is not a valid schema.",
            "args": {"mode": "json_schema", "schema": {"type": "nonsense"}, "data": {}},
        },
    ],
    "assert": [
        {
            "caption": "A policy check that fails one rule, with a score and a human message.",
            "args": {"mode": "assert", "data": _EX_LEAVE_DOC, "rules": [{"id": "days", "path": "leave.days", "op": "lte", "value": 2, "message": "casual leave is capped at 2 days", "weight": 3}, {"id": "type", "path": "leave.type", "op": "in", "value": ["casual", "sick", "earned"]}, {"id": "email", "path": "employee.email", "op": "is_email"}, {"id": "start", "path": "leave.start", "op": "after", "value": "2025-08-31"}]},
        },
        {
            "caption": "String numbers compared as numbers, and a list checked for uniqueness.",
            "args": {"mode": "assert", "data": {"total": "1200.50", "skus": ["A1", "B2", "A1"]}, "rules": [{"path": "total", "op": "gt", "value": 1000}, {"path": "skus", "op": "unique"}, {"path": "skus", "op": "len_eq", "value": 3}]},
        },
        {
            "caption": "`each` applies a sub-rule to every element of a list.",
            "args": {"mode": "assert", "data": {"lines": [{"qty": 2}, {"qty": 0}]}, "rules": [{"path": "lines", "op": "each", "value": {"path": "qty", "op": "gt", "value": 0}}]},
        },
        {
            "caption": "An unknown operator.",
            "args": {"mode": "assert", "data": {"a": 1}, "rules": [{"path": "a", "op": "frobnicate", "value": 1}]},
        },
        {
            "caption": "`between` needs a two-element range.",
            "args": {"mode": "assert", "data": {"a": 1}, "rules": [{"path": "a", "op": "between", "value": 5}]},
        },
        {
            "caption": "An unknown type name.",
            "args": {"mode": "assert", "data": {"a": 1}, "rules": [{"path": "a", "op": "type", "value": "decimal"}]},
        },
    ],
    "id": [
        {
            "caption": "A card number that passes Luhn, with its brand detected and the value masked.",
            "args": {"mode": "id", "kind": "card", "value": "4111 1111 1111 1111"},
        },
        {
            "caption": "One digit changed: the same call, `valid: false`.",
            "args": {"mode": "id", "kind": "card", "value": "4111 1111 1111 1112"},
        },
        {
            "caption": "An IBAN, checked by mod-97 and by its country's expected length.",
            "args": {"mode": "id", "kind": "iban", "value": "GB82 WEST 1234 5698 7654 32"},
        },
        {
            "caption": "A GSTIN, whose check character is recomputed and whose embedded PAN is returned.",
            "args": {"mode": "id", "kind": "gstin", "value": "19ABCDE1234F1ZX"},
        },
        {
            "caption": "A PAN, with the holder type decoded from its fourth character.",
            "args": {"mode": "id", "kind": "pan", "value": "ABCDE1234F"},
        },
        {
            "caption": "An Aadhaar number verified by the Verhoeff algorithm and returned masked.",
            "args": {"mode": "id", "kind": "aadhaar", "value": "2345 6789 0124"},
        },
        {
            "caption": "An ISBN in either form is verified and returned in both — the ISBN-13 a shop wants and the ISBN-10 on the old jacket.",
            "args": {"mode": "id", "kind": "isbn", "value": "0-306-40615-2"},
        },
        {
            "caption": "`value` is required.",
            "args": {"mode": "id", "kind": "card"},
        },
    ],
    "email": [
        {
            "caption": "A normal address, normalised.",
            "args": {"mode": "email", "value": "Asha.Roy@Example.COM"},
        },
        {
            "caption": "A disposable domain, flagged but still syntactically valid.",
            "args": {"mode": "email", "value": "throwaway@mailinator.com"},
        },
        {
            "caption": "A malformed address: a successful call with `valid: false` and a reason.",
            "args": {"mode": "email", "value": "asha@@example..com"},
        },
    ],
    "url": [
        {
            "caption": "A full URL, decomposed.",
            "args": {"mode": "url", "value": "https://leftbrain.dev:8443/docs/tools?q=math#eval"},
        },
        {
            "caption": "A missing scheme is reported, not repaired.",
            "args": {"mode": "url", "value": "leftbrain.dev/docs"},
        },
        {
            "caption": "An IP literal host is detected as one.",
            "args": {"mode": "url", "value": "http://192.168.1.10:8080/health"},
        },
    ],
    "phone": [
        {
            "caption": "An E.164 number: the region is inferred, not asked for.",
            "args": {"mode": "phone", "value": "+91 98765 43210"},
        },
        {
            "caption": "A national number with an explicit region and a trunk prefix.",
            "args": {"mode": "phone", "value": "098765 43210", "region": "IN"},
        },
        {
            "caption": "A number that does not match its region's pattern.",
            "args": {"mode": "phone", "value": "12345 67890", "region": "IN"},
        },
        {
            "caption": "A national number with no region: the tool asks rather than assuming a country.",
            "args": {"mode": "phone", "value": "9876543210"},
        },
        {
            "caption": "An unsupported region.",
            "args": {"mode": "phone", "value": "9876543210", "region": "ZZ"},
        },
    ],
    "ip": [
        {
            "caption": "A private IPv4 address.",
            "args": {"mode": "ip", "value": "192.168.1.10"},
        },
        {
            "caption": "A CIDR network, with its size and bounds.",
            "args": {"mode": "ip", "value": "10.0.0.0/24"},
        },
        {
            "caption": "IPv6, compressed and exploded.",
            "args": {"mode": "ip", "value": "2001:db8::1"},
        },
        {
            "caption": "Something that is not an address at all.",
            "args": {"mode": "ip", "value": "300.1.1.1"},
        },
    ],
    "cidr": [
        {
            "caption": "Is this address inside the allowlisted block? A boolean, plus the block's size and bounds.",
            "args": {"mode": "cidr", "network": "10.0.0.0/24", "value": "10.0.0.200"},
        },
        {
            "caption": "Do these blocks overlap? Every pair is named with its relation — CIDR blocks either nest or are disjoint.",
            "args": {"mode": "cidr", "network": ["10.0.0.0/16", "10.0.5.0/24", "192.168.0.0/24"]},
        },
        {
            "caption": "A block with host bits set is read as its network, and the reading is recorded.",
            "args": {"mode": "cidr", "network": "192.168.1.77/24"},
        },
        {
            "caption": "A prefix that does not fit the address family.",
            "args": {"mode": "cidr", "network": "10.0.0.0/33"},
        },
    ],
    "sql_parse": [
        {
            "caption": "A read-only query: tables, columns and `read_only: true`.",
            "args": {"mode": "sql_parse", "sql": "SELECT o.id, c.name FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.total > 100 LIMIT 50"},
        },
        {
            "caption": "An unbounded DELETE, flagged in `warnings` before it runs.",
            "args": {"mode": "sql_parse", "sql": "DELETE FROM sessions"},
        },
        {
            "caption": "Invalid SQL: a successful call that says the SQL is invalid.",
            "args": {"mode": "sql_parse", "sql": "SELCT * FROM t WHERE"},
        },
        {
            "caption": "An unknown dialect.",
            "args": {"mode": "sql_parse", "sql": "SELECT 1", "dialect": "klingon"},
        },
    ],
    "regex": [
        {
            "caption": "The same pattern `text.regex_match` refuses: here it is judged, not run, so it is valid with a warning.",
            "args": {"mode": "regex", "pattern": "(a+)+$"},
        },
        {
            "caption": "A valid pattern with named groups.",
            "args": {"mode": "regex", "pattern": "(?P<year>\\d{4})-(?P<month>\\d{2})"},
        },
        {
            "caption": "An invalid pattern, with the failure position.",
            "args": {"mode": "regex", "pattern": "([a-z"},
        },
        {
            "caption": "`pattern` is required.",
            "args": {"mode": "regex"},
        },
    ],
}
