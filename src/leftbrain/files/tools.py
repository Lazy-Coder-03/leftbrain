"""files - PDF text extraction, image/base64 handling, file metadata.

Hosted agents (Claude Code, Cursor, claude.ai) read PDFs and images natively;
this module is for custom agent loops that cannot.  Every path is resolved
against an allowlist of roots so the model cannot read arbitrary files.
"""

from __future__ import annotations

import base64
import hashlib
import io
import mimetypes
import os
from pathlib import Path
from typing import Any

from ..contract import ToolError, ok, tool

MODES = ("pdf_text", "pdf_info", "image_info", "image_to_base64", "base64_to_file", "file_info", "read_text", "list_dir")

_MAX_READ = 20 * 1024 * 1024  # 20 MB


def allowed_roots() -> list[Path]:
    raw = os.environ.get("LEFTBRAIN_FILE_ROOTS", "")
    seps = ";" if os.name == "nt" else ":"
    roots = [Path(x).expanduser().resolve() for x in raw.split(seps) if x.strip()] if raw else [Path.cwd().resolve()]
    return roots


def resolve_path(p: Any, *, must_exist: bool = True) -> Path:
    if not p:
        raise ToolError("'path' is required")
    path = Path(str(p)).expanduser()
    if not path.is_absolute():
        path = allowed_roots()[0] / path
    path = path.resolve()
    if not any(path == r or r in path.parents for r in allowed_roots()):
        raise ToolError(f"path is outside the allowed roots ({', '.join(str(r) for r in allowed_roots())}); set LEFTBRAIN_FILE_ROOTS", code="forbidden")
    if must_exist and not path.exists():
        raise ToolError(f"file not found: {path}")
    return path


def _pypdf() -> Any:
    try:
        import pypdf
    except ImportError:  # pragma: no cover
        raise ToolError("PDF support needs pypdf: pip install 'leftbrain[files]'", code="unsupported") from None
    return pypdf


def _pil() -> Any:
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        raise ToolError("image support needs Pillow: pip install 'leftbrain[files]'", code="unsupported") from None
    return Image


def _page_range(spec: Any, n: int) -> list[int]:
    if spec is None:
        return list(range(n))
    if isinstance(spec, int):
        return [spec - 1]
    if isinstance(spec, list):
        return [int(x) - 1 for x in spec]
    pages: list[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            a_i = int(a) if a else 1
            b_i = int(b) if b else n
            pages.extend(range(a_i - 1, min(b_i, n)))
        elif part:
            pages.append(int(part) - 1)
    bad = [p + 1 for p in pages if p < 0 or p >= n]
    if bad:
        raise ToolError(f"page(s) out of range: {bad} (document has {n} pages)")
    return pages


def _open_pdf(p: dict[str, Any]) -> tuple[Any, str]:
    pypdf = _pypdf()
    if p.get("base64"):
        raw = base64.b64decode(p["base64"])
        src = "<base64>"
        reader = pypdf.PdfReader(io.BytesIO(raw))
    else:
        path = resolve_path(p.get("path"))
        if path.stat().st_size > _MAX_READ:
            raise ToolError("PDF larger than 20 MB")
        reader = pypdf.PdfReader(str(path))
        src = str(path)
    if reader.is_encrypted:
        pw = p.get("password") or ""
        if not reader.decrypt(pw):
            raise ToolError("PDF is encrypted; pass 'password'")
    return reader, src


def _pdf_text(p: dict[str, Any]) -> dict[str, Any]:
    reader, src = _open_pdf(p)
    n = len(reader.pages)
    pages = _page_range(p.get("pages"), n)
    max_chars = int(p.get("max_chars", 200_000))
    out_pages = []
    total = 0
    truncated = False
    layout = bool(p.get("layout", False))
    for i in pages:
        page = reader.pages[i]
        try:
            txt = page.extract_text(extraction_mode="layout" if layout else "plain") or ""
        except TypeError:
            txt = page.extract_text() or ""
        if total + len(txt) > max_chars:
            txt = txt[: max(0, max_chars - total)]
            truncated = True
        total += len(txt)
        out_pages.append({"page": i + 1, "chars": len(txt), "text": txt})
        if truncated:
            break
    empty = sum(1 for x in out_pages if not x["text"].strip())
    warnings = []
    if truncated:
        warnings.append(f"output truncated at {max_chars} chars; request fewer pages")
    if empty and empty == len(out_pages):
        warnings.append("no extractable text - the PDF is probably scanned images; OCR is needed")
    elif empty:
        warnings.append(f"{empty} page(s) had no extractable text (images or scans)")
    return ok({"source": src, "page_count": n, "pages_returned": [x["page"] for x in out_pages], "pages": out_pages, "text": "\n\n".join(x["text"] for x in out_pages), "total_chars": total}, warnings=warnings)


def _pdf_info(p: dict[str, Any]) -> dict[str, Any]:
    reader, src = _open_pdf(p)
    meta = reader.metadata or {}
    info = {k.lstrip("/"): str(v) for k, v in dict(meta).items()} if meta else {}
    first = reader.pages[0] if len(reader.pages) else None
    size = None
    if first is not None:
        box = first.mediabox
        size = {"width_pt": float(box.width), "height_pt": float(box.height), "width_mm": round(float(box.width) * 25.4 / 72, 1), "height_mm": round(float(box.height) * 25.4 / 72, 1)}
    outline = []
    try:
        for item in reader.outline[:50]:
            if isinstance(item, list):
                continue
            outline.append({"title": item.title, "page": reader.get_destination_page_number(item) + 1})
    except Exception:  # noqa: BLE001
        pass
    has_text = False
    try:
        has_text = bool((reader.pages[0].extract_text() or "").strip()) if reader.pages else False
    except Exception:  # noqa: BLE001
        pass
    return ok({"source": src, "page_count": len(reader.pages), "metadata": info, "page_size": size, "encrypted": reader.is_encrypted, "outline": outline, "has_text_layer": has_text, "form_fields": list((reader.get_fields() or {}).keys())[:100] if hasattr(reader, "get_fields") else []})


def _load_image(p: dict[str, Any]) -> tuple[Any, str, int]:
    Image = _pil()
    if p.get("base64"):
        data = p["base64"]
        if "," in data and data.strip().startswith("data:"):
            data = data.split(",", 1)[1]
        raw = base64.b64decode(data)
        img = Image.open(io.BytesIO(raw))
        return img, "<base64>", len(raw)
    path = resolve_path(p.get("path"))
    size = path.stat().st_size
    if size > _MAX_READ:
        raise ToolError("image larger than 20 MB")
    return Image.open(str(path)), str(path), size


def _image_info(p: dict[str, Any]) -> dict[str, Any]:
    img, src, size = _load_image(p)
    exif: dict[str, Any] = {}
    try:
        from PIL import ExifTags

        raw = img.getexif()
        for k, v in raw.items():
            name = ExifTags.TAGS.get(k, str(k))
            if isinstance(v, (int, float, str)) and len(str(v)) < 200:
                exif[name] = v
    except Exception:  # noqa: BLE001
        pass
    return ok({"source": src, "format": img.format, "mode": img.mode, "width": img.width, "height": img.height, "megapixels": round(img.width * img.height / 1e6, 2), "aspect_ratio": round(img.width / img.height, 4) if img.height else None, "orientation": "landscape" if img.width > img.height else ("portrait" if img.height > img.width else "square"), "bytes": size, "has_alpha": img.mode in ("RGBA", "LA", "P") and "transparency" in img.info or img.mode in ("RGBA", "LA"), "animated": bool(getattr(img, "is_animated", False)), "frames": getattr(img, "n_frames", 1), "exif": exif, "dpi": img.info.get("dpi")})


def _image_to_base64(p: dict[str, Any]) -> dict[str, Any]:
    Image = _pil()
    img, src, size = _load_image(p)
    fmt = (p.get("format") or (img.format or "PNG")).upper()
    if fmt == "JPG":
        fmt = "JPEG"
    max_side = p.get("max_side") or p.get("max_size")
    max_bytes = p.get("max_bytes")
    quality = int(p.get("quality", 85))
    assumptions: list[str] = []
    w0, h0 = img.width, img.height
    if max_side and max(img.width, img.height) > int(max_side):
        scale = int(max_side) / max(img.width, img.height)
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
        assumptions.append(f"resized {w0}x{h0} -> {img.width}x{img.height} (max_side={max_side})")
    if fmt == "JPEG" and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
        assumptions.append("converted to RGB for JPEG")

    def encode(im: Any, q: int) -> bytes:
        buf = io.BytesIO()
        kw: dict[str, Any] = {"quality": q, "optimize": True} if fmt in ("JPEG", "WEBP") else {"optimize": True} if fmt == "PNG" else {}
        im.save(buf, format=fmt, **kw)
        return buf.getvalue()

    data = encode(img, quality)
    if max_bytes:
        mb = int(max_bytes)
        q = quality
        while len(data) > mb and (q > 30 or max(img.width, img.height) > 256):
            if fmt in ("JPEG", "WEBP") and q > 30:
                q -= 10
            else:
                img = img.resize((max(1, img.width * 3 // 4), max(1, img.height * 3 // 4)), Image.LANCZOS)
            data = encode(img, q)
        assumptions.append(f"compressed to fit max_bytes={mb}: {img.width}x{img.height}, quality={q}" if fmt in ("JPEG", "WEBP") else f"downscaled to {img.width}x{img.height} to fit max_bytes={mb}")
        if len(data) > mb:
            raise ToolError(f"could not fit under {mb} bytes (got {len(data)})")
    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif", "BMP": "image/bmp", "TIFF": "image/tiff"}.get(fmt, f"image/{fmt.lower()}")
    b64 = base64.b64encode(data).decode()
    out = {"source": src, "media_type": mime, "width": img.width, "height": img.height, "bytes": len(data), "base64": b64, "data_uri": f"data:{mime};base64,{b64}", "anthropic_block": {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}, "openai_block": {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}, "approx_tokens_claude": round(img.width * img.height / 750)}
    return ok(out, assumptions=assumptions + ["approx_tokens_claude = width*height/750"])


def _base64_to_file(p: dict[str, Any]) -> dict[str, Any]:
    data = p.get("base64") or p.get("data")
    if not data:
        raise ToolError("'base64' is required")
    if data.strip().startswith("data:") and "," in data:
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"invalid base64: {e}") from None
    path = resolve_path(p.get("path"), must_exist=False)
    if path.exists() and not p.get("overwrite"):
        raise ToolError(f"{path} exists; pass overwrite=true")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return ok({"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})


def _file_info(p: dict[str, Any]) -> dict[str, Any]:
    path = resolve_path(p.get("path"))
    st = path.stat()
    mime, _ = mimetypes.guess_type(str(path))
    out: dict[str, Any] = {"path": str(path), "name": path.name, "extension": path.suffix.lower(), "is_dir": path.is_dir(), "bytes": st.st_size, "human_size": _human(st.st_size), "modified": __import__("datetime").datetime.fromtimestamp(st.st_mtime).isoformat(), "mime_guess": mime}
    if path.is_file() and st.st_size <= _MAX_READ and p.get("hash", True):
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        out["sha256"] = h.hexdigest()
        with path.open("rb") as f:
            head = f.read(16)
        out["magic"] = _magic(head)
    return ok(out)


def _magic(head: bytes) -> str | None:
    sigs = [(b"%PDF", "pdf"), (b"\x89PNG", "png"), (b"\xff\xd8\xff", "jpeg"), (b"GIF8", "gif"), (b"PK\x03\x04", "zip/office"), (b"RIFF", "riff (webp/wav/avi)"), (b"\x1f\x8b", "gzip"), (b"BM", "bmp"), (b"II*\x00", "tiff"), (b"MM\x00*", "tiff"), (b"{", "json?"), (b"<", "xml/html?")]
    for sig, name in sigs:
        if head.startswith(sig):
            return name
    return None


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"  # pragma: no cover


def _read_text(p: dict[str, Any]) -> dict[str, Any]:
    path = resolve_path(p.get("path"))
    if path.stat().st_size > _MAX_READ:
        raise ToolError("file larger than 20 MB")
    raw = path.read_bytes()
    enc = p.get("encoding")
    warnings: list[str] = []
    if not enc:
        for cand in ("utf-8-sig", "utf-16", "latin-1"):
            try:
                txt = raw.decode(cand)
                enc = cand
                break
            except UnicodeDecodeError:
                continue
        else:  # pragma: no cover
            raise ToolError("could not decode file")
        if enc != "utf-8-sig":
            warnings.append(f"decoded as {enc} (not UTF-8)")
    else:
        txt = raw.decode(enc)
    max_chars = int(p.get("max_chars", 200_000))
    truncated = len(txt) > max_chars
    lines = txt.splitlines()
    start, end = p.get("start_line"), p.get("end_line")
    if start or end:
        sel = lines[(int(start) - 1 if start else 0):(int(end) if end else None)]
        txt = "\n".join(sel)
        truncated = False
    return ok({"path": str(path), "encoding": enc, "text": txt[:max_chars], "chars": len(txt), "lines": len(lines), "truncated": truncated}, warnings=warnings + ([f"truncated to {max_chars} chars"] if truncated else []))


def _list_dir(p: dict[str, Any]) -> dict[str, Any]:
    path = resolve_path(p.get("path") or ".")
    if not path.is_dir():
        raise ToolError(f"{path} is not a directory")
    pattern = p.get("glob") or "*"
    recursive = bool(p.get("recursive", False))
    it = path.rglob(pattern) if recursive else path.glob(pattern)
    entries = []
    for i, e in enumerate(sorted(it)):
        if i >= int(p.get("limit", 500)):
            break
        try:
            st = e.stat()
        except OSError:
            continue
        entries.append({"path": str(e.relative_to(path)), "is_dir": e.is_dir(), "bytes": st.st_size if e.is_file() else None, "modified": __import__("datetime").datetime.fromtimestamp(st.st_mtime).isoformat()})
    return ok({"root": str(path), "count": len(entries), "entries": entries})


@tool
def files(mode: str = "file_info", **params: Any) -> dict[str, Any]:
    """File tools. Modes: pdf_text, pdf_info, image_info, image_to_base64, base64_to_file, file_info, read_text, list_dir."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    return {"pdf_text": _pdf_text, "pdf_info": _pdf_info, "image_info": _image_info, "image_to_base64": _image_to_base64, "base64_to_file": _base64_to_file, "file_info": _file_info, "read_text": _read_text, "list_dir": _list_dir}[mode](p)
