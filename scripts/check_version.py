"""Fail unless pyproject.toml, leftbrain.__version__ and the release tag all agree.

Run: python scripts/check_version.py v0.1.0

Used by .github/workflows/release.yml so a tag can never publish a wheel that calls
itself something else. Reads the version out of the source rather than importing the
package, so it works before the dependencies are installed.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_VERSION_LINE = re.compile(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", re.M)


def pyproject_version(root: Path = ROOT) -> str | None:
    path = root / "pyproject.toml"
    if not path.is_file():
        return None
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    return version if isinstance(version, str) else None


def package_version(root: Path = ROOT) -> str | None:
    path = root / "src" / "leftbrain" / "__init__.py"
    if not path.is_file():
        return None
    match = _VERSION_LINE.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def check(tag: str, root: Path = ROOT) -> list[str]:
    """Problems found, or an empty list when everything matches."""
    wanted = tag[1:] if tag.startswith("v") else tag
    problems = []
    if not wanted:
        return ["empty tag"]
    for label, found in (("pyproject.toml", pyproject_version(root)), ("__init__.py", package_version(root))):
        if found is None:
            problems.append(f"no version found in {label}")
        elif found != wanted:
            problems.append(f"{label} says {found}, tag says {wanted}")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python scripts/check_version.py vX.Y.Z", file=sys.stderr)
        return 2
    problems = check(args[0])
    for problem in problems:
        print(f"version mismatch: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"version {args[0]} matches pyproject.toml and __init__.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
