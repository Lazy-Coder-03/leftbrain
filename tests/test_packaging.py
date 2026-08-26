"""The built wheel must carry the changelog the site serves at ``/docs/changelog``.

``CHANGELOG.md`` lives at the repo root and is the only copy. Hatch force-includes it in
the wheel as ``leftbrain/web/docs/changelog.md`` so an installed leftbrain can render the
page without reaching outside the package; this test builds a real wheel and looks.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = "leftbrain/web/docs/changelog.md"


def build_wheel(dest: Path) -> Path | None:
    """Build a wheel into ``dest`` with whatever builder this environment has."""
    for cmd in (
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dest)],
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--wheel-dir", str(dest)],
    ):
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        built = sorted(dest.glob("leftbrain-*.whl"))
        if proc.returncode == 0 and built:
            return built[0]
    return None


def test_wheel_ships_the_changelog_as_a_docs_page(tmp_path):
    wheel = build_wheel(tmp_path)
    if wheel is None:
        pytest.skip("no wheel builder available (pip install build)")
    with zipfile.ZipFile(wheel) as z:
        assert SHIPPED in z.namelist()
        shipped = z.read(SHIPPED).decode("utf-8").replace("\r\n", "\n")
    assert shipped == (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "## [0.1.0]" in shipped
