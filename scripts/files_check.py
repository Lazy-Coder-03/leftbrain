"""Exercise the files tools with generated fixtures. Run: python scripts/files_check.py"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageDraw  # noqa: E402

from leftbrain.files import files  # noqa: E402


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="leftbrain_"))
    os.environ["LEFTBRAIN_FILE_ROOTS"] = str(tmp)
    img_path = tmp / "sample.png"
    im = Image.new("RGB", (1600, 900), (30, 120, 200))
    ImageDraw.Draw(im).text((40, 40), "leftbrain", fill=(255, 255, 255))
    im.save(img_path)
    pdf_path = tmp / "sample.pdf"
    im.convert("RGB").save(pdf_path, "PDF", resolution=100.0)
    (tmp / "notes.txt").write_text("line one\nline two\nline three\n", encoding="utf-8")

    fails = 0
    checks = [
        ("image_info", dict(mode="image_info", path=str(img_path))),
        ("image_to_base64", dict(mode="image_to_base64", path=str(img_path), format="JPEG", max_side=800, max_bytes=60_000)),
        ("pdf_info", dict(mode="pdf_info", path=str(pdf_path))),
        ("pdf_text (scanned)", dict(mode="pdf_text", path=str(pdf_path))),
        ("file_info", dict(mode="file_info", path=str(pdf_path))),
        ("read_text", dict(mode="read_text", path=str(tmp / "notes.txt"), start_line=2, end_line=3)),
        ("list_dir", dict(mode="list_dir", path=str(tmp))),
        ("base64_to_file", dict(mode="base64_to_file", base64="aGVsbG8=", path=str(tmp / "out" / "hello.txt"))),
        ("escape blocked", dict(mode="read_text", path=str(Path.home() / ".bashrc"))),
    ]
    for label, kw in checks:
        r = files(**kw)
        s = json.dumps({k: (v if k != "result" else {kk: (vv if not isinstance(vv, str) or len(vv) < 80 else vv[:60] + "…") for kk, vv in v.items()}) for k, v in r.items()} if r.get("ok") else r, default=str)
        print(f"[{'OK ' if r.get('ok') else 'ERR'}] {label}: {s[:330]}")
        expect_fail = label == "escape blocked"
        if r.get("ok") == expect_fail:
            fails += 1
    print("failures:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
