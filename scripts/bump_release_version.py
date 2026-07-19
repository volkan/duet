#!/usr/bin/env python3
"""Bump duet's runtime version and the two lockstep plugin manifests.

Used by `.github/workflows/bump-version.yml` (a manual ``workflow_dispatch``
helper) and runnable locally. It only edits the version strings; it never
creates a tag or a GitHub Release. Merging the resulting ``chore: release X.Y.Z``
PR to ``main`` triggers ``.github/workflows/release.yml`` (``on: push: main``),
which detects the bump, publishes to PyPI via OIDC, then auto-creates the
``vX.Y.Z`` tag + GitHub Release. This script still only edits the three version
sources below.

Stdlib-only and Python 3.9-clean: ``duet.__version__`` is the canonical runtime
version and setuptools derives package metadata from it; the two plugin
manifests are JSON. After writing, verification is delegated to
``scripts/check_distribution_metadata.py`` (the one source of truth for
lockstep + metadata), which gates ``tomllib`` itself.

Usage: ``python scripts/bump_release_version.py X.Y.Z``
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DUET_PY = ROOT / "duet.py"
CLAUDE_PLUGIN_JSON = ROOT / "plugins" / "duet-claude" / ".claude-plugin" / "plugin.json"
CODEX_PLUGIN_JSON = ROOT / "plugins" / "duet" / ".codex-plugin" / "plugin.json"

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_VERSION_LINE = re.compile(r'^(\s*__version__\s*=\s*")[^"]*(".*)$')


class BumpError(Exception):
    """Raised on an invalid version or an unexpected manifest shape."""


def parse_strict_semver(value: str) -> tuple:
    """Return (major, minor, patch) or raise. Rejects a leading ``v``,
    prerelease (``-rc1``), build metadata (``+x``), and non-``X.Y.Z`` forms."""
    match = _SEMVER.match(value)
    if not match:
        raise BumpError(
            f"invalid version {value!r}: must be exactly X.Y.Z "
            "(no leading 'v', no prerelease/build-metadata suffix)"
        )
    return tuple(int(part) for part in match.groups())


def _source_version_line_index(lines: list) -> int:
    """Index of the single module-level ``__version__ = "..."`` line."""
    found = [i for i, line in enumerate(lines) if _VERSION_LINE.match(line)]
    if len(found) != 1:
        raise BumpError(
            f"expected exactly one __version__ line in duet.py, found {len(found)}"
        )
    return found[0]


def read_current_version(source_text: str) -> str:
    lines = source_text.splitlines(keepends=True)
    line = lines[_source_version_line_index(lines)]
    return _VERSION_LINE.match(line).group(0).split('"')[1]


def _rewrite_source(source_text: str, new_version: str) -> str:
    lines = source_text.splitlines(keepends=True)
    idx = _source_version_line_index(lines)
    lines[idx] = _VERSION_LINE.sub(r"\g<1>" + new_version + r"\g<2>", lines[idx])
    return "".join(lines)


def _rewrite_json(path: Path, new_version: str) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "version" not in data:
        raise BumpError(f"{path} has no 'version' key")
    data["version"] = new_version
    return json.dumps(data, indent=2) + "\n"


def bump(root: Path, new_version: str) -> str:
    """Bump runtime plus two manifests under ``root``. Validates and computes
    every rewritten file before writing any, so a failure leaves no partial state.
    Returns the previous version."""
    parse_strict_semver(new_version)
    duet_py = root / "duet.py"
    claude = root / "plugins" / "duet-claude" / ".claude-plugin" / "plugin.json"
    codex = root / "plugins" / "duet" / ".codex-plugin" / "plugin.json"

    current = read_current_version(duet_py.read_text(encoding="utf-8"))
    if parse_strict_semver(new_version) <= parse_strict_semver(current):
        raise BumpError(f"{new_version} is not greater than the current version {current}")

    rewritten = {
        duet_py: _rewrite_source(duet_py.read_text(encoding="utf-8"), new_version),
        claude: _rewrite_json(claude, new_version),
        codex: _rewrite_json(codex, new_version),
    }
    for path, text in rewritten.items():
        path.write_text(text, encoding="utf-8")
    return current


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description="Bump duet runtime and plugin versions.")
    parser.add_argument("version", help="new version, exactly X.Y.Z (no leading 'v')")
    parser.add_argument("--skip-verify", action="store_true",
                        help="skip the check_distribution_metadata.py lockstep check (tests use this)")
    args = parser.parse_args(argv)

    try:
        previous = bump(ROOT, args.version)
    except BumpError as exc:
        print(f"[bump_release_version] {exc}", file=sys.stderr)
        return 1

    print(f"[bump_release_version] bumped {previous} -> {args.version}")
    if not args.skip_verify:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_distribution_metadata.py")],
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
