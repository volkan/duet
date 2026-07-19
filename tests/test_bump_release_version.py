"""Unit tests for scripts/bump_release_version.py (stdlib unittest, no subprocess)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import bump_release_version as brv  # noqa: E402

DUET_SOURCE = """\
#!/usr/bin/env python3
from __future__ import annotations

__version__ = "0.2.1"

def main():
    return 0
"""

CLAUDE_PLUGIN = {"name": "duet", "version": "0.2.1", "author": {"name": "Volkan Altan"}}
CODEX_PLUGIN = {"name": "duet", "version": "0.2.1", "skills": "./skills/"}


def _make_repo(tmp: Path) -> None:
    (tmp / "duet.py").write_text(DUET_SOURCE, encoding="utf-8")
    claude = tmp / "plugins" / "duet-claude" / ".claude-plugin"
    codex = tmp / "plugins" / "duet" / ".codex-plugin"
    claude.mkdir(parents=True)
    codex.mkdir(parents=True)
    (claude / "plugin.json").write_text(json.dumps(CLAUDE_PLUGIN, indent=2) + "\n", encoding="utf-8")
    (codex / "plugin.json").write_text(json.dumps(CODEX_PLUGIN, indent=2) + "\n", encoding="utf-8")


class TestParseStrictSemver(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(brv.parse_strict_semver("1.2.3"), (1, 2, 3))
        self.assertEqual(brv.parse_strict_semver("0.2.10"), (0, 2, 10))

    def test_rejects(self):
        for bad in ("v1.2.3", "0.3.0-rc1", "0.3.0+build", "1.2", "1.2.3.4", "1.2.x", ""):
            with self.assertRaises(brv.BumpError, msg=bad):
                brv.parse_strict_semver(bad)


class TestBump(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_repo(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _versions(self):
        runtime = brv.read_current_version((self.root / "duet.py").read_text())
        cl = json.loads((self.root / "plugins/duet-claude/.claude-plugin/plugin.json").read_text())
        cx = json.loads((self.root / "plugins/duet/.codex-plugin/plugin.json").read_text())
        return runtime, cl["version"], cx["version"]

    def test_read_current_version(self):
        self.assertEqual(brv.read_current_version(DUET_SOURCE), "0.2.1")

    def test_bump_updates_all_three(self):
        prev = brv.bump(self.root, "0.3.0")
        self.assertEqual(prev, "0.2.1")
        self.assertEqual(self._versions(), ("0.3.0", "0.3.0", "0.3.0"))

    def test_bump_preserves_source_and_json_structure(self):
        brv.bump(self.root, "0.2.2")
        cl = json.loads((self.root / "plugins/duet-claude/.claude-plugin/plugin.json").read_text())
        self.assertEqual(cl["name"], "duet")
        self.assertEqual(cl["author"], {"name": "Volkan Altan"})
        source = (self.root / "duet.py").read_text()
        self.assertIn('from __future__ import annotations', source)
        self.assertIn('__version__ = "0.2.2"', source)

    def test_reject_downgrade(self):
        with self.assertRaises(brv.BumpError):
            brv.bump(self.root, "0.2.0")
        self.assertEqual(self._versions(), ("0.2.1", "0.2.1", "0.2.1"))  # unchanged

    def test_reject_same_version(self):
        with self.assertRaises(brv.BumpError):
            brv.bump(self.root, "0.2.1")

    def test_reject_invalid_before_write(self):
        with self.assertRaises(brv.BumpError):
            brv.bump(self.root, "v0.3.0")
        self.assertEqual(self._versions(), ("0.2.1", "0.2.1", "0.2.1"))  # no partial write

    def test_missing_runtime_version_raises(self):
        (self.root / "duet.py").write_text("def main():\n    return 0\n", encoding="utf-8")
        with self.assertRaises(brv.BumpError):
            brv.bump(self.root, "0.3.0")


if __name__ == "__main__":
    unittest.main()
