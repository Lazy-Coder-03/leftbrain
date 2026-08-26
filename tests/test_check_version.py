"""scripts/check_version.py guards the release workflow, so it gets its own test."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location("check_version", ROOT / "scripts" / "check_version.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_repo(root: Path, pyproject: str | None, package: str | None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if pyproject is not None:
        (root / "pyproject.toml").write_text(f'[project]\nname = "leftbrain"\nversion = "{pyproject}"\n', encoding="utf-8")
    if package is not None:
        pkg = root / "src" / "leftbrain"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(f'"""doc."""\n\n__version__ = "{package}"\n', encoding="utf-8")
    return root


def test_this_repo_agrees_with_itself():
    cv = load_script()
    from leftbrain import __version__

    assert cv.pyproject_version() == __version__
    assert cv.package_version() == __version__
    assert cv.check(f"v{__version__}") == []
    assert cv.check(__version__) == []  # a bare version works too
    assert cv.main([f"v{__version__}"]) == 0


def test_a_tag_that_disagrees_fails():
    cv = load_script()
    from leftbrain import __version__

    assert cv.check("v9.9.9") == [
        f"pyproject.toml says {__version__}, tag says 9.9.9",
        f"__init__.py says {__version__}, tag says 9.9.9",
    ]
    assert cv.main(["v9.9.9"]) == 1


def test_half_bumped_repo_fails(tmp_path):
    cv = load_script()
    root = fake_repo(tmp_path, pyproject="0.2.0", package="0.1.0")
    assert cv.check("v0.2.0", root) == ["__init__.py says 0.1.0, tag says 0.2.0"]
    assert cv.check("v0.1.0", root) == ["pyproject.toml says 0.2.0, tag says 0.1.0"]
    assert cv.check("v0.2.0", fake_repo(tmp_path / "matched", "0.2.0", "0.2.0")) == []


def test_missing_versions_are_reported(tmp_path):
    cv = load_script()
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cv.check("v1.0.0", empty) == [
        "no version found in pyproject.toml",
        "no version found in __init__.py",
    ]


def test_bad_usage_exits_two():
    cv = load_script()

    assert cv.main([]) == 2
    assert cv.main(["v1.0.0", "extra"]) == 2
    assert cv.main([""]) == 1
