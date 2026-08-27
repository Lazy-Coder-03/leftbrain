"""leftbrain-files MCP server: PDF text, images <-> base64, file info (allowlisted roots)."""

from __future__ import annotations

import argparse
import os
from typing import Any

from .. import __version__
from ..mcp_contract import ContractMCPServer
from . import tools

server = ContractMCPServer(
    "leftbrain-files",
    title="leftbrain files",
    instructions="Read PDFs, inspect images, produce base64 data URIs for vision calls, read local files, and hash a file to verify a download. Access is limited to LEFTBRAIN_FILE_ROOTS.",
    version=__version__,
)


@server.tool(name="files")
def files(
    mode: str = "file_info",
    path: str | None = None,
    pages: str | list[int] | None = None,
    layout: bool | None = None,
    max_chars: int | None = None,
    password: str | None = None,
    base64: str | None = None,
    format: str | None = None,
    max_side: int | None = None,
    max_bytes: int | None = None,
    quality: int | None = None,
    overwrite: bool | None = None,
    encoding: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    glob: str | None = None,
    recursive: bool | None = None,
    limit: int | None = None,
    algo: str | None = None,
    expected: str | None = None,
) -> dict[str, Any]:
    """Use for local files an agent cannot open by itself.
    mode:
    - pdf_text (path or base64, pages="1-3,7", layout, max_chars) - extracts text; warns if scanned
    - pdf_info (path) - page count, metadata, outline, has_text_layer
    - image_info (path or base64) - dimensions, format, EXIF
    - image_to_base64 (path, format=JPEG|PNG|WEBP, max_side, max_bytes, quality) - data URI plus ready-made Anthropic/OpenAI image blocks
    - base64_to_file (base64, path, overwrite) - write decoded bytes
    - file_info (path) | read_text (path, start_line, end_line) | list_dir (path, glob, recursive)
    - file_hash (path, algo=sha256|sha1|md5|blake2b|crc32, expected) - streamed digest of any size; with expected (hex, Base64, or a sha256sum line) adds matches
    """
    params = {k: v for k, v in dict(path=path, pages=pages, layout=layout, max_chars=max_chars, password=password, base64=base64, format=format, max_side=max_side, max_bytes=max_bytes, quality=quality, overwrite=overwrite, encoding=encoding, start_line=start_line, end_line=end_line, glob=glob, recursive=recursive, limit=limit, algo=algo, expected=expected).items() if v is not None}
    return tools.files(mode, **params)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="leftbrain-files")
    ap.add_argument("--roots", help="allowed directories, separated by ; (Windows) or : (POSIX); default: cwd")
    ap.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8767)
    args = ap.parse_args(argv)
    if args.roots:
        os.environ["LEFTBRAIN_FILE_ROOTS"] = args.roots
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.settings.host = args.host
        server.settings.port = args.port
        server.run(transport=args.transport)


if __name__ == "__main__":
    main()
