"""Source archive checks must extract only regular files inside their root."""

import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("check_sdist_tests", ROOT / "scripts/check_sdist_tests.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class TestSourceExtraction(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / "source.tar.gz"
        self.destination = self.root / "unpacked"
        self.destination.mkdir()

    def write_archive(self, name, kind=tarfile.REGTYPE):
        member = tarfile.TarInfo(name)
        member.type = kind
        if kind == tarfile.REGTYPE:
            member.size = 4
        else:
            member.linkname = "../outside"
        with tarfile.open(self.archive, "w:gz") as archive:
            archive.addfile(member, io.BytesIO(b"data") if member.isfile() else None)

    def test_extracts_regular_test_resource(self):
        self.write_archive("duet_cli-0.2.18/scripts/helper.py")
        source = checker.extract_source(self.archive, self.destination, "duet_cli-0.2.18")
        self.assertEqual((source / "scripts/helper.py").read_bytes(), b"data")

    def test_rejects_traversal_and_foreign_roots(self):
        for name in ("duet_cli-0.2.18/../../outside", "/outside", "other/source.py",
                     "duet_cli-0.2.18/..\\outside"):
            with self.subTest(name=name):
                self.write_archive(name)
                with self.assertRaisesRegex(ValueError, "unsupported"):
                    checker.extract_source(self.archive, self.destination, "duet_cli-0.2.18")
                self.assertEqual(list(self.destination.iterdir()), [])

    def test_rejects_symbolic_and_hard_links(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            with self.subTest(kind=kind):
                self.write_archive("duet_cli-0.2.18/link", kind)
                with self.assertRaisesRegex(ValueError, "unsupported"):
                    checker.extract_source(self.archive, self.destination, "duet_cli-0.2.18")
                self.assertEqual(list(self.destination.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
