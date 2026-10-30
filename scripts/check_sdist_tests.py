#!/usr/bin/env python3
"""Run the complete unit suite from the built source archive, outside the checkout."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

from check_distribution_metadata import _runtime_version


def extract_source(archive_path: Path, destination: Path, prefix: str) -> Path:
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            parts = PurePosixPath(member.name).parts
            if (not parts or parts[0] != prefix or ".." in parts
                    or "\\" in member.name
                    or PurePosixPath(member.name).is_absolute()
                    or not (member.isdir() or member.isfile())):
                raise ValueError("unsupported source archive member: " + member.name)
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    return destination / prefix


def check(dist_dir: Path) -> int:
    version = _runtime_version()
    prefix = "duet_cli-" + version
    archive = dist_dir.resolve() / (prefix + ".tar.gz")
    with tempfile.TemporaryDirectory(prefix="duet-sdist-tests-") as temporary:
        source = extract_source(archive, Path(temporary), prefix)
        # Keep imports inside the extracted archive, even in a configured shell.
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.update(DUET_METRICS="0", PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=source, env=environment, text=True, encoding="utf-8",
            errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        match = re.search(r"^Ran (\d+) tests?\b", result.stderr, re.MULTILINE)
        if result.returncode or not match or int(match.group(1)) == 0:
            raise RuntimeError("source archive unit tests failed or collected no tests")
        return int(match.group(1))


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: check_sdist_tests.py DIST_DIR", file=sys.stderr)
        return 2
    try:
        count = check(Path(argv[0]))
    except (OSError, ValueError, RuntimeError, tarfile.TarError) as exc:
        print("[check_sdist_tests] " + str(exc), file=sys.stderr)
        return 1
    print("[check_sdist_tests] ok: " + str(count) + " tests from the built sdist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
