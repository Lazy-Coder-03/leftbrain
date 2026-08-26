"""encode - hashes, HMAC, checksums and encodings (models hallucinate all of these)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac as _hmac
import html
import json
import zlib
from datetime import UTC
from typing import Any
from urllib.parse import quote, quote_plus, unquote, unquote_plus

from ..contract import ToolError, ok, tool

MODES = ("hash", "hmac", "checksum", "base64", "hex", "url", "html", "jwt_decode", "json")

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


def _hash(p: dict[str, Any]) -> dict[str, Any]:
    algo = (p.get("algo") or p.get("algorithm") or "sha256").lower().replace("-", "_")
    if algo not in _ALGOS:
        raise ToolError(f"algo must be one of {', '.join(sorted(_ALGOS))}")
    data = _bytes(p)
    h = hashlib.new(algo, data)
    return ok({"algo": algo, "hex": h.hexdigest(), "base64": base64.b64encode(h.digest()).decode(), "bytes": len(data), "input_encoding": p.get("encoding") or "utf-8"}, assumptions=["input hashed as UTF-8 bytes; JSON inputs serialised compact with sorted keys"] if not isinstance(p.get("text", p.get("value")), str) and not p.get("bytes_base64") and not p.get("bytes_hex") else [])


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
        exp = str(p["expected"]).strip().lower()
        out["matches"] = _hmac.compare_digest(mac.hexdigest(), exp) or _hmac.compare_digest(out["base64"], str(p["expected"]).strip())
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
    return ok({"algo": algo, "value": v, "hex": f"{v:08x}"})


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


def _url(p: dict[str, Any]) -> dict[str, Any]:
    action = (p.get("action") or "encode").lower()
    s = str(p.get("text") if "text" in p else p.get("value") or "")
    if action == "encode":
        plus = bool(p.get("plus", False))
        return ok({"encoded": quote_plus(s, safe=p.get("safe", "")) if plus else quote(s, safe=p.get("safe", "/"))})
    if action == "decode":
        return ok({"decoded": unquote_plus(s) if p.get("plus") else unquote(s)})
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


def _json_mode(p: dict[str, Any]) -> dict[str, Any]:
    action = (p.get("action") or "parse").lower()
    if action == "parse":
        s = p.get("text") if "text" in p else p.get("value")
        try:
            data = json.loads(str(s))
        except json.JSONDecodeError as e:
            return ok({"valid": False, "error": e.msg, "line": e.lineno, "column": e.colno, "position": e.pos})
        return ok({"valid": True, "data": data, "type": type(data).__name__})
    if action in ("stringify", "format", "minify"):
        data = p.get("data") if "data" in p else p.get("value")
        indent = None if action == "minify" else int(p.get("indent", 2))
        return ok({"text": json.dumps(data, indent=indent, sort_keys=bool(p.get("sort_keys", False)), ensure_ascii=False, separators=(",", ":") if indent is None else None)})
    raise ToolError("action must be parse, format or minify")


@tool
def encode(mode: str = "hash", **params: Any) -> dict[str, Any]:
    """Hashes and encodings. Modes: hash, hmac, checksum, base64, hex, url, html, jwt_decode, json."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    return {"hash": _hash, "hmac": _hmac_mode, "checksum": _checksum, "base64": _base64, "hex": _hex, "url": _url, "html": _html, "jwt_decode": _jwt_decode, "json": _json_mode}[mode](p)
