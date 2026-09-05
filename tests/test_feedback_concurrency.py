"""A contending feedback writer must not allocate or publish a second identity."""
from __future__ import annotations

import fcntl
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
import duet  # noqa: E402


class TestFeedbackConcurrency(unittest.TestCase):
    def test_contended_legacy_initialization_and_retry_keep_one_record(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            run = root / "legacy-run"
            run.mkdir()
            central = root / "central"
            state = {"history": [], "dry_run": False, "metrics_enabled": True}
            with mock.patch.object(duet, "_metrics_root", return_value=central), \
                    mock.patch.dict(os.environ, {"DUET_METRICS": "1"}):
                with (run / ".feedback.lock").open("a") as owner:
                    fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with mock.patch.object(duet.uuid, "uuid4") as allocate:
                        with self.assertRaises((OSError, ValueError, RuntimeError)):
                            duet._save_feedback(run, state, "mixed", "not_applied")
                    allocate.assert_not_called()
                    self.assertFalse((run / "feedback.json").exists())
                    self.assertFalse((central / "feedback").exists())

                duet._save_feedback(run, state, "mixed", "not_applied")
                original = json.loads((run / "feedback.json").read_text(encoding="utf-8"))
                central_path = central / "feedback" / (original["id"] + ".json")
                first_local = (run / "feedback.json").read_bytes()
                first_central = central_path.read_bytes()
                with (run / ".feedback.lock").open("a") as owner:
                    fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with self.assertRaises((OSError, ValueError, RuntimeError)):
                        duet._save_feedback(run, state, "useful", "corrected_comment")
                    self.assertEqual((run / "feedback.json").read_bytes(), first_local)
                    self.assertEqual(central_path.read_bytes(), first_central)

                duet._save_feedback(run, state, "useful", "corrected_comment")
                updated = json.loads((run / "feedback.json").read_text(encoding="utf-8"))
                self.assertEqual(updated["id"], original["id"])
                self.assertEqual(json.loads(central_path.read_text(encoding="utf-8")), updated)
                self.assertEqual(len(list((central / "feedback").glob("*.json"))), 1)
                self.assertEqual(duet._load_feedback_summary(central)["live"], {
                    "records": 1, "usefulness": {"useful": 1},
                    "decisions": {"corrected_comment": 1},
                })


if __name__ == "__main__":
    unittest.main()
