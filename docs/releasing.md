# Cutting a release

A release is a pushed tag. Nothing is built, uploaded or written by hand.

## Steps

1. **Write the changelog.** In `CHANGELOG.md`, turn the accumulated `## [Unreleased]`
   notes into a dated section for the new version and leave `## [Unreleased]` empty above
   it, then update the two link definitions at the bottom of the file:

   ```markdown
   ## [Unreleased]

   ## [0.2.0] - 2026-09-14

   ### Added
   - …

   [Unreleased]: https://github.com/Lazy-Coder-03/leftbrain/compare/v0.2.0...HEAD
   [0.2.0]: https://github.com/Lazy-Coder-03/leftbrain/compare/v0.1.0...v0.2.0
   ```

   Write it for someone using leftbrain, not for someone who read the diff.

2. **Bump the version in both places, in one commit**: `version` in `pyproject.toml` and
   `__version__` in `src/leftbrain/__init__.py`. The release workflow refuses a tag that
   disagrees with either, so they never drift.

3. **Check it locally** before tagging:

   ```bash
   python scripts/check_version.py v0.2.0
   pytest
   ruff check src tests scripts
   python -m build          # optional; the workflow does this too
   ```

4. **Tag and push.** The tag must be `v` + the version, and it must point at the bump
   commit:

   ```bash
   git commit -am "Release 0.2.0"
   git push
   git tag v0.2.0
   git push origin v0.2.0
   ```

Then watch the **Release** run in the Actions tab.

## What the workflow does

`.github/workflows/release.yml` runs on any pushed `v*` tag, with `contents: write` and no
other permission:

1. checks out, sets up Python 3.12, installs `build`;
2. runs `scripts/check_version.py <tag>` — fails if the tag, `pyproject.toml` and
   `__init__.py` do not all say the same version;
3. `python -m build`, producing a wheel and an sdist in `dist/`;
4. extracts that version's section out of `CHANGELOG.md` into the release notes, and fails
   if the file has no section for it;
5. creates the GitHub Release with those notes, attaches `dist/*`, and marks it a
   **pre-release** for any `0.x` version (and for `a`/`b`/`rc` versions).

If a step fails, fix the commit, delete the tag (`git tag -d v0.2.0`,
`git push --delete origin v0.2.0`) and tag again — a tag is cheap, a bad release is not.

### PyPI

leftbrain is not on PyPI yet, so the workflow only publishes a GitHub Release. A
commented-out `pypi` job at the bottom of `release.yml` is ready to go: register the
project on PyPI, add a trusted publisher for this repository (`release.yml`, environment
`pypi`), create that environment here, and uncomment the job. Trusted publishing uses an
OIDC token minted per run, so no API token is ever stored in the repository.

## How the changelog page updates

The site serves `/docs/changelog`, and `CHANGELOG.md` at the repo root is the only copy of
it:

- the wheel gets it as `leftbrain/web/docs/changelog.md`, through
  `[tool.hatch.build.targets.wheel.force-include]` in `pyproject.toml`;
- a dev checkout has no such file, so `leftbrain.web.docs.ROOT_SOURCES` falls back to the
  root `CHANGELOG.md`.

So editing the changelog is all there is to do — the page follows, the footer's `v0.1.0`
links to it, and `tests/test_packaging.py` fails if the wheel ever stops carrying the file.
Rendered pages are cached for the life of the process, so a deployed site picks up a new
changelog when it is redeployed from the tagged commit.

## Version numbers

Semantic versioning, with the 0.x caveat that the tool contract is still allowed to change:
while the version is `0.x`, a breaking change bumps the minor and anything else bumps the
patch. Every 0.x release goes out flagged as a pre-release.
