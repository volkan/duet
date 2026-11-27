"""Exercise the privacy gate without using any real addresses."""
import contextlib
import hashlib
import io
import pathlib
import tempfile
import unittest
from unittest import mock

from scripts import check_distribution_metadata as metadata


class TestDistributionPrivacyGate(unittest.TestCase):
    ADDRESS = "private.contact@company.example"

    def setUp(self):
        local, domain = self.ADDRESS.split("@")
        fingerprint = hashlib.sha256(self.ADDRESS.encode("utf-8")).hexdigest()
        patcher = mock.patch.object(metadata, "FORBIDDEN_EMAIL", (len(local), len(domain), fingerprint))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_exact_address_is_detected_inside_text_and_case_insensitively(self):
        self.assertTrue(metadata._contains_forbidden_email(f"note <{self.ADDRESS}>."))
        self.assertTrue(metadata._contains_forbidden_email(f"xxxxx{self.ADDRESS.upper()}yyyyy"))
        self.assertFalse(metadata._contains_forbidden_email("public.contact@company.example"))
        self.assertFalse(metadata._contains_forbidden_email("unfinished @"))

    def test_failure_names_the_file_and_redacts_the_blocked_address(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            path = root / "test address.txt"
            path.write_text(f"Contact {self.ADDRESS} immediately.", encoding="utf-8")
            sensitive_filename = root / self.ADDRESS
            sensitive_filename.write_text(self.ADDRESS, encoding="utf-8")
            error = io.StringIO()
            with mock.patch.object(metadata, "ROOT", root), \
                    mock.patch.object(metadata, "_tracked_files", return_value=[path, sensitive_filename]), \
                    contextlib.redirect_stderr(error), self.assertRaises(SystemExit):
                metadata._assert_no_forbidden_text()

            self.assertIn("test address.txt", error.getvalue())
            self.assertIn("[redacted path]", error.getvalue())
            self.assertNotIn(self.ADDRESS, error.getvalue())
