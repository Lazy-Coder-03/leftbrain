"""encode - hashes, HMAC, checksums and encodings (models hallucinate all of these)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac as _hmac
import html
import json
import re
import zlib
from datetime import UTC
from typing import Any
from urllib.parse import quote, quote_plus, unquote, unquote_plus

from ..contract import ToolError, check_params, ok, tool

MODES = ("hash", "hmac", "checksum", "base64", "hex", "url", "html", "jwt_decode", "json")

#: What each mode reads. Anything else in a call is a caller's mistake, not a default
#: to fall back on (#28 SS2a). Kept honest by tests/test_mode_params.py, which derives
#: the same map from the code and fails when the two drift.
MODE_PARAMS: dict[str, frozenset[str]] = {
    "hash": frozenset({"algo", "algorithm", "bytes_base64", "bytes_hex", "encoding", "expected", "text", "value"}),
    "hmac": frozenset({"algo", "bytes_base64", "bytes_hex", "encoding", "expected", "key", "key_base64", "secret", "text", "value"}),
    "checksum": frozenset({"algo", "bytes_base64", "bytes_hex", "encoding", "expected", "text", "value"}),
    "base64": frozenset({"action", "bytes_base64", "bytes_hex", "encoding", "op", "strip_padding", "text", "urlsafe", "value"}),
    "hex": frozenset({"action", "bytes_base64", "bytes_hex", "encoding", "text", "value"}),
    "url": frozenset({"action", "plus", "safe", "text", "value"}),
    "html": frozenset({"action", "quote", "text", "value"}),
    "jwt_decode": frozenset({"token", "value"}),
    "json": frozenset({"action", "data", "indent", "sort_keys", "text", "value"}),
}

_ALGOS = {"md5", "sha1", "sha224", "sha256", "sha384", "sha512", "sha3_256", "sha3_512", "blake2b", "blake2s"}


def _bytes(p: dict[str, Any]) -> bytes:
    if p.get("bytes_base64") is not None:
        try:
            return base64.b64decode(p["bytes_base64"], validate=True)
        except (binascii.Error, ValueError) as e:
            raise ToolError(f"bytes_base64 is not valid base64: {e}") from None
    if p.get("bytes_hex") is not None:
        try:
            return bytes.fromhex(str(p["bytes_hex"]).replace(" ", ""))
        except ValueError as e:
            raise ToolError(f"bytes_hex is not valid hex: {e}") from None
    t = p.get("text") if "text" in p else p.get("value")
    if t is None:
        raise ToolError("'text' (or bytes_base64 / bytes_hex) is required")
    if not isinstance(t, str):
        t = json.dumps(t, separators=(",", ":"), sort_keys=True)
    return t.encode(p.get("encoding") or "utf-8")


def clean_expected(expected: Any) -> str:
    """An expected digest as the user pasted it: trimmed, and a ``sha256sum``-style ``<digest>  <file>`` line reduced to the digest."""
    s = str(expected).strip()
    return s.split()[0] if s else s


def digest_matches(expected: Any, *forms: str) -> bool:
    """Constant-time comparison of ``expected`` against every textual form of a digest (hex, Base64, decimal)."""
    exp = clean_expected(expected)
    hit = False
    for form in forms:
        hit |= _hmac.compare_digest(form.lower(), exp.lower())  # hex and decimal are case-free; Base64 is compared exactly below
        hit |= _hmac.compare_digest(form, exp)
    return hit


def _hash(p: dict[str, Any]) -> dict[str, Any]:
    algo = (p.get("algo") or p.get("algorithm") or "sha256").lower().replace("-", "_")
    if algo not in _ALGOS:
        raise ToolError(f"algo must be one of {', '.join(sorted(_ALGOS))}")
    data = _bytes(p)
    h = hashlib.new(algo, data)
    out = {"algo": algo, "hex": h.hexdigest(), "base64": base64.b64encode(h.digest()).decode(), "bytes": len(data), "input_encoding": p.get("encoding") or "utf-8"}
    if p.get("expected") is not None:
        out["matches"] = digest_matches(p["expected"], out["hex"], out["base64"])
    return ok(out, assumptions=["input hashed as UTF-8 bytes; JSON inputs serialised compact with sorted keys"] if not isinstance(p.get("text", p.get("value")), str) and not p.get("bytes_base64") and not p.get("bytes_hex") else [])


def _hmac_mode(p: dict[str, Any]) -> dict[str, Any]:
    key = p.get("key") or p.get("secret")
    if key is None:
        raise ToolError("'key' is required")
    algo = (p.get("algo") or "sha256").lower().replace("-", "_")
    if algo not in _ALGOS:
        raise ToolError(f"algo must be one of {', '.join(sorted(_ALGOS))}")
    kb = str(key).encode("utf-8") if not p.get("key_base64") else base64.b64decode(str(key))
    mac = _hmac.new(kb, _bytes(p), algo)
    out = {"algo": algo, "hex": mac.hexdigest(), "base64": base64.b64encode(mac.digest()).decode()}
    if p.get("expected") is not None:
        out["matches"] = digest_matches(p["expected"], out["hex"], out["base64"])
    return ok(out)


def _checksum(p: dict[str, Any]) -> dict[str, Any]:
    algo = (p.get("algo") or "crc32").lower()
    data = _bytes(p)
    if algo == "crc32":
        v = zlib.crc32(data) & 0xFFFFFFFF
    elif algo == "adler32":
        v = zlib.adler32(data) & 0xFFFFFFFF
    else:
        raise ToolError("algo must be crc32 or adler32")
    out: dict[str, Any] = {"algo": algo, "value": v, "hex": f"{v:08x}"}
    if p.get("expected") is not None:
        out["matches"] = digest_matches(p["expected"], out["hex"], str(v))
    return ok(out)


def _base64(p: dict[str, Any]) -> dict[str, Any]:
    action = (p.get("action") or p.get("op") or "encode").lower()
    urlsafe = bool(p.get("urlsafe", False))
    if action == "encode":
        data = _bytes(p)
        enc = base64.urlsafe_b64encode(data) if urlsafe else base64.b64encode(data)
        s = enc.decode()
        if p.get("strip_padding"):
            s = s.rstrip("=")
        return ok({"encoded": s, "bytes": len(data)})
    if action == "decode":
        s = str(p.get("text") or p.get("value") or "").strip()
        s += "=" * (-len(s) % 4)
        if urlsafe and ("+" in s or "/" in s):
            # `urlsafe_b64decode` translates `-_` to `+/` and then accepts `+/` as well, so
            # the flag was accepted and had no effect on standard-alphabet input (#28 SS3.13).
            raise ToolError(
                "urlsafe=true but the text uses the standard alphabet ('+' or '/'); "
                "the URL-safe alphabet uses '-' and '_'",
                details={"urlsafe": True},
                hint="Drop urlsafe, or pass text encoded with the URL-safe alphabet.",
            )
        try:
            raw = base64.urlsafe_b64decode(s) if (urlsafe or "-" in s or "_" in s) else base64.b64decode(s, validate=False)
        except (binascii.Error, ValueError) as e:
            raise ToolError(f"invalid base64: {e}") from None
        out: dict[str, Any] = {"bytes": len(raw), "hex": raw.hex()}
        try:
            out["text"] = raw.decode("utf-8")
        except UnicodeDecodeError:
            out["text"] = None
            out["base64"] = base64.b64encode(raw).decode()
        return ok(out, warnings=[] if out["text"] is not None else ["decoded bytes are not valid UTF-8; returned hex"])
    raise ToolError("action must be encode or decode")


def _hex(p: dict[str, Any]) -> dict[str, Any]:
    action = (p.get("action") or "encode").lower()
    if action == "encode":
        data = _bytes(p)
        return ok({"hex": data.hex(), "bytes": len(data)})
    s = str(p.get("text") or p.get("value") or "").replace(" ", "").replace("0x", "")
    try:
        raw = bytes.fromhex(s)
    except ValueError as e:
        raise ToolError(f"invalid hex: {e}") from None
    try:
        return ok({"text": raw.decode("utf-8"), "bytes": len(raw)})
    except UnicodeDecodeError:
        return ok({"text": None, "base64": base64.b64encode(raw).decode(), "bytes": len(raw)}, warnings=["not valid UTF-8"])


#: A `%` that is not followed by two hex digits: `unquote` passes it through untouched.
_BAD_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})[^\s]{0,2}")


def _url(p: dict[str, Any]) -> dict[str, Any]:
    action = (p.get("action") or "encode").lower()
    s = str(p.get("text") if "text" in p else p.get("value") or "")
    if action == "encode":
        plus = bool(p.get("plus", False))
        return ok({"encoded": quote_plus(s, safe=p.get("safe", "")) if plus else quote(s, safe=p.get("safe", "/"))})
    if action == "decode":
        # `unquote` leaves a malformed escape exactly as it found it, so `a%zz` decoded to
        # `a%zz` and the caller had no way to know an escape was broken (#28 SS3.13).
        bad = _BAD_ESCAPE.findall(s)
        return ok(
            {"decoded": unquote_plus(s) if p.get("plus") else unquote(s)},
            warnings=[f"{', '.join(dict.fromkeys(bad))} is not a valid percent-escape and was left as written"] if bad else [],
        )
    raise ToolError("action must be encode or decode")


def _html(p: dict[str, Any]) -> dict[str, Any]:
    action = (p.get("action") or "escape").lower()
    s = str(p.get("text") if "text" in p else p.get("value") or "")
    if action in ("escape", "encode"):
        return ok({"escaped": html.escape(s, quote=bool(p.get("quote", True)))})
    if action in ("unescape", "decode"):
        return ok({"unescaped": html.unescape(s)})
    raise ToolError("action must be escape or unescape")


def _jwt_decode(p: dict[str, Any]) -> dict[str, Any]:
    tok = str(p.get("token") or p.get("value") or "").strip()
    parts = tok.split(".")
    if len(parts) != 3:
        raise ToolError("a JWT has three dot-separated parts")

    def dec(seg: str) -> Any:
        seg += "=" * (-len(seg) % 4)
        try:
            return json.loads(base64.urlsafe_b64decode(seg))
        except Exception as e:
            raise ToolError(f"segment is not base64url JSON: {e}") from None

    header, payload = dec(parts[0]), dec(parts[1])
    out: dict[str, Any] = {"header": header, "payload": payload, "signature_b64url": parts[2], "algorithm": header.get("alg") if isinstance(header, dict) else None}
    if isinstance(payload, dict):
        from datetime import datetime

        for k in ("exp", "iat", "nbf"):
            if isinstance(payload.get(k), (int, float)):
                out[f"{k}_iso"] = datetime.fromtimestamp(payload[k], tz=UTC).isoformat()
        if isinstance(payload.get("exp"), (int, float)):
            out["expired"] = datetime.now(UTC).timestamp() > payload["exp"]
    return ok(out, warnings=["signature NOT verified; claims are untrusted"])


def _count_nonfinite(value: Any) -> int:
    """How many Infinity/NaN values are in here. JSON has no way to spell either."""
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return 1
    if isinstance(value, dict):
        return sum(_count_nonfinite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_count_nonfinite(v) for v in value)
    return 0


def _json_mode(p: dict[str, Any]) -> dict[str, Any]:
    action = (p.get("action") or "parse").lower()
    if action == "parse":
        s = p.get("text") if "text" in p else p.get("value")
        if isinstance(s, (dict, list)):
            # Through MCP `text` is typed Any, so a JSON-looking string arrives already
            # parsed and `str()` turned it into Python's repr - `{'a': 1}` - which is not
            # JSON, so the tool reported the caller's valid document as invalid (#28 SS3.3).
            s = json.dumps(s)
        try:
            data = json.loads(str(s))
        except json.JSONDecodeError as e:
            return ok({"valid": False, "error": e.msg, "line": e.lineno, "column": e.colno, "position": e.pos})
        return ok({"valid": True, "data": data, "type": type(data).__name__})
    if action in ("stringify", "format", "minify"):
        data = p.get("data") if "data" in p else p.get("value")
        indent = None if action == "minify" else int(p.get("indent", 2))
        # `Infinity` and `NaN` are Python's spelling, not JSON's: json.dumps writes them
        # happily and every strict parser downstream rejects the result (#28 SS3.4).
        nonfinite = _count_nonfinite(data)
        if nonfinite:
            raise ToolError(
                f"{nonfinite} value(s) are Infinity or NaN, which JSON cannot spell; writing them "
                f"produces text no strict parser will read back",
                details={"nonfinite": nonfinite},
                hint="Replace them with null, or with a string, before stringifying.",
            )
        try:
            text = json.dumps(data, indent=indent, sort_keys=bool(p.get("sort_keys", False)), ensure_ascii=False, allow_nan=False, separators=(",", ":") if indent is None else None)
        except (ValueError, TypeError) as e:
            raise ToolError(f"this value cannot be written as JSON: {e}") from None
        return ok({"text": text})
    raise ToolError("action must be parse, format or minify")


@tool
def encode(mode: str = "hash", **params: Any) -> dict[str, Any]:
    """Hashes and encodings. Modes: hash, hmac, checksum, base64, hex, url, html, jwt_decode, json."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    check_params("encode", mode, p, MODE_PARAMS)
    return {"hash": _hash, "hmac": _hmac_mode, "checksum": _checksum, "base64": _base64, "hex": _hex, "url": _url, "html": _html, "jwt_decode": _jwt_decode, "json": _json_mode}[mode](p)

#: Worked examples for the reference page, one list per mode. Every one of them is
#: executed when /docs/tools/encode is built and sorted by the result into
#: "Examples" (the call succeeded) and "Fails when" (it did not), so a fixture never
#: states an expectation of its own. Mark anything whose output depends on the
#: current instant with "volatile": True.
EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "hash": [
        {
            "caption": "SHA-256 of a string, in hex and Base64.",
            "args": {"mode": "hash", "text": "hello world"},
        },
        {
            "caption": "A different algorithm on the same input.",
            "args": {"mode": "hash", "text": "hello world", "algo": "md5"},
        },
        {
            "caption": "Hashing an object — serialised deterministically, and it says so.",
            "args": {"mode": "hash", "text": {"b": 2, "a": 1}},
        },
        {
            "caption": "Hashing raw bytes given as hex.",
            "args": {"mode": "hash", "bytes_hex": "deadbeef", "algo": "sha1"},
        },
        {
            "caption": "Verifying a download or a message against a published digest: `matches` is compared in constant time. A `sha256sum`-style `<digest>  <file>` line works as-is.",
            "args": {"mode": "hash", "text": "abc", "expected": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad  abc.txt"},
        },
        {
            "caption": "A mismatch is an answer, not an error: `ok` stays true and `matches` is false.",
            "args": {"mode": "hash", "text": "abd", "expected": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"},
        },
        {
            "caption": "An unknown algorithm lists the supported ones.",
            "args": {"mode": "hash", "text": "hello", "algo": "sha999"},
        },
        {
            "caption": "`bytes_hex` must actually be hex.",
            "args": {"mode": "hash", "bytes_hex": "zzzz"},
        },
    ],
    "hmac": [
        {
            "caption": "An HMAC-SHA256 signature.",
            "args": {"mode": "hmac", "key": "s3cret", "text": "payload-1"},
        },
        {
            "caption": "Verifying a signature: `matches` is computed in constant time.",
            "args": {"mode": "hmac", "key": "s3cret", "text": "payload-1", "expected": "874582d507bf2715cab202a7b899745887fba3a1935da6699029a96c6a82e770"},
        },
        {
            "caption": "A different digest algorithm.",
            "args": {"mode": "hmac", "key": "s3cret", "text": "payload-1", "algo": "sha512"},
        },
        {
            "caption": "An unknown algorithm.",
            "args": {"mode": "hmac", "key": "s3cret", "text": "x", "algo": "sha999"},
        },
        {
            "caption": "Some message is required.",
            "args": {"mode": "hmac", "key": "s3cret"},
        },
    ],
    "checksum": [
        {
            "caption": "CRC32 of a string.",
            "args": {"mode": "checksum", "text": "hello world"},
        },
        {
            "caption": "Adler-32 of the same input.",
            "args": {"mode": "checksum", "text": "hello world", "algo": "adler32"},
        },
        {
            "caption": "Checking against an expected value, given as hex or as the unsigned integer.",
            "args": {"mode": "checksum", "text": "hello world", "expected": "0d4a1185"},
        },
        {
            "caption": "Only CRC32 and Adler-32 are checksums here.",
            "args": {"mode": "checksum", "text": "hello", "algo": "md5"},
        },
    ],
    "base64": [
        {
            "caption": "Encoding.",
            "args": {"mode": "base64", "action": "encode", "text": "leftbrain ✓"},
        },
        {
            "caption": "Decoding the same string back.",
            "args": {"mode": "base64", "action": "decode", "text": "bGVmdGJyYWluIOKckw=="},
        },
        {
            "caption": "URL-safe and unpadded, for a query string.",
            "args": {"mode": "base64", "action": "encode", "text": "sub?a=1&b=2", "urlsafe": True, "strip_padding": True},
        },
        {
            "caption": "Decoding bytes that are not UTF-8: hex, with a warning, instead of mojibake.",
            "args": {"mode": "base64", "action": "decode", "text": "3q2+7w=="},
        },
        {
            "caption": "An unknown action.",
            "args": {"mode": "base64", "action": "flip", "text": "abc"},
        },
        {
            "caption": "Input that is not valid Base64.",
            "args": {"mode": "base64", "action": "decode", "text": "a"},
        },
    ],
    "hex": [
        {
            "caption": "Encoding.",
            "args": {"mode": "hex", "action": "encode", "text": "leftbrain"},
        },
        {
            "caption": "Decoding, with separators tolerated.",
            "args": {"mode": "hex", "action": "decode", "text": "6c 65 66 74 62 72 61 69 6e"},
        },
        {
            "caption": "Input that is not hex.",
            "args": {"mode": "hex", "action": "decode", "text": "zzzz"},
        },
        {
            "caption": "An odd number of hex digits.",
            "args": {"mode": "hex", "action": "decode", "text": "abc"},
        },
    ],
    "url": [
        {
            "caption": "Path-style encoding: the slash survives.",
            "args": {"mode": "url", "action": "encode", "text": "reports/Q3 2025/summary&final.pdf"},
        },
        {
            "caption": "Form-style encoding of the same string.",
            "args": {"mode": "url", "action": "encode", "text": "reports/Q3 2025/summary&final.pdf", "plus": True},
        },
        {
            "caption": "Decoding.",
            "args": {"mode": "url", "action": "decode", "text": "q%3Dleft%20brain%26page%3D2"},
        },
        {
            "caption": "An unknown action.",
            "args": {"mode": "url", "action": "flip", "text": "abc"},
        },
    ],
    "html": [
        {
            "caption": "Escaping markup and quotes.",
            "args": {"mode": "html", "action": "escape", "text": "<b class=\"x\">Tom & Jerry</b>"},
        },
        {
            "caption": "Unescaping entities, named and numeric.",
            "args": {"mode": "html", "action": "unescape", "text": "caf&eacute; &amp; cr&#232;me"},
        },
        {
            "caption": "An unknown action.",
            "args": {"mode": "html", "action": "flip", "text": "abc"},
        },
    ],
    "jwt_decode": [
        {
            "caption": "An expired token: claims decoded, timestamps rendered, signature untouched.",
            "args": {"mode": "jwt_decode", "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyLTQyIiwibmFtZSI6IkFkYSIsImlhdCI6MTY5OTk5NjQwMCwiZXhwIjoxNzAwMDAwMDAwfQ.c2lnbmF0dXJlLW5vdC12ZXJpZmllZA"},
        },
        {
            "caption": "Three parts, but not Base64url JSON.",
            "args": {"mode": "jwt_decode", "token": "a.b.c"},
        },
    ],
    "json": [
        {
            "caption": "Infinity and NaN are Python's spelling, not JSON's; writing them produces text no strict parser will read back.",
            "args": {"mode": "json", "action": "stringify", "data": {"ratio": 1e999}},
        },
        {
            "caption": "Valid JSON, parsed.",
            "args": {"mode": "json", "action": "parse", "text": "{\"a\": 1, \"b\": [2, 3]}"},
        },
        {
            "caption": "Invalid JSON: the error is located to line and column.",
            "args": {"mode": "json", "action": "parse", "text": "{\"a\": 1,}"},
        },
        {
            "caption": "Pretty-printing with sorted keys.",
            "args": {"mode": "json", "action": "format", "data": {"b": 2, "a": 1}, "sort_keys": True},
        },
        {
            "caption": "Minifying.",
            "args": {"mode": "json", "action": "minify", "data": {"a": 1, "b": [2, 3]}},
        },
        {
            "caption": "An unknown action.",
            "args": {"mode": "json", "action": "lint", "text": "{}"},
        },
    ],
}
