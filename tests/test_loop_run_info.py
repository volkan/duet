"""The live loop driver must not discover runs from human-readable output."""
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest


_SPEC = importlib.util.spec_from_file_location(
    "duet_loop_driver_test", pathlib.Path(__file__).parents[1] / "scripts/duet_loop_e2e.py",
)
DRIVER = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = DRIVER
_SPEC.loader.exec_module(DRIVER)


class TestLoopRunInfo(unittest.TestCase):
    def test_accepts_schema_one_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "run-info.json"
            payload = self.payload(path.parent)
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(DRIVER.extract_run_dir(path), pathlib.Path(payload["run_dir"]))

    def test_banner_cannot_replace_launch_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "run-info.json"
            self.assertIsNone(DRIVER.extract_run_dir(path))
            path.write_text("[duet] run: /tmp/spoofed\n", encoding="utf-8")
            self.assertIsNone(DRIVER.extract_run_dir(path))

    def test_rejects_incompatible_or_incomplete_handoffs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "run-info.json"
            overrides = [
                {"schema_version": 2}, {"schema_version": True}, {"kind": "duet.status"},
                {"run_id": ""}, {"pid": True}, {"pid": "42"},
                {"run_dir": "relative"}, {"state_path": "relative"}, {"run_dir": None},
            ]
            for override in overrides:
                with self.subTest(override=override):
                    path.write_text(json.dumps({**self.payload(path.parent), **override}), encoding="utf-8")
                    self.assertIsNone(DRIVER.extract_run_dir(path))
            path.write_text("[]", encoding="utf-8")
            self.assertIsNone(DRIVER.extract_run_dir(path))

    @staticmethod
    def payload(root):
        return {"schema_version": 1, "kind": "duet.run", "run_id": "test-run", "pid": 42,
                "run_dir": str(root / "run"), "state_path": str(root / "run/state.json")}
