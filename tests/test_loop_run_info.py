"""The live loop driver must not discover runs from human-readable output."""
import importlib.util
import json
import pathlib
import sys
import tempfile
import textwrap
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


class TestForcePromptDriver(unittest.TestCase):
    NOTICE = (
        "[duet] loop ended (reason=max_turns). "
        "Press Enter to finish, or type feedback to force another turn "
        "(your text is appended as a human-feedback message and sent to the next agent):"
    )

    def test_prompt_spans_reads_without_recounting_or_retaining_the_stream(self):
        prompt = f"\r\n{self.NOTICE}\r\nforce> ".encode()
        quoted = b"agent explains 'force> ' as a string\r\nforce> in echoed feedback\r\n"
        for split in range(1, len(prompt)):
            with self.subTest(split=split):
                tail, found = DRIVER._scan_force_prompt(b"", b"x" * 4096 + quoted + prompt[:split])
                self.assertFalse(found)
                self.assertLessEqual(len(tail), len(prompt) + 100)
                tail, found = DRIVER._scan_force_prompt(tail, prompt[split:])
                self.assertTrue(found)
                _, found = DRIVER._scan_force_prompt(tail, quoted)
                self.assertFalse(found)

    def test_pty_waits_for_each_prompt_and_ignores_quoted_output_and_echo(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            config = root / "config.json"
            log = root / "console.log"
            peer = root / "fake-duet.py"
            peer.write_text(textwrap.dedent(f"""\
                import json
                import os
                import pathlib
                import select
                import sys

                info = pathlib.Path(sys.argv[sys.argv.index("--run-info-file") + 1])
                run = info.parent / "run"
                run.mkdir()
                state = run / "state.json"
                info.write_text(json.dumps({{
                    "schema_version": 1, "kind": "duet.run", "run_id": "test-run",
                    "run_dir": str(run), "state_path": str(state), "pid": os.getpid(),
                }}))

                def publish(phase, forced):
                    pending = state.with_suffix(".pending")
                    pending.write_text(json.dumps({{
                        "phase": phase, "finished_reason": None,
                        "history": [{{"forced": True}}] * forced,
                    }}))
                    pending.replace(state)

                def emit_misleading_text():
                    print("Quoted in agent output: 'force> '", flush=True)
                    print("\\n" + {self.NOTICE!r})
                    print("force> ", end="", flush=True)
                    assert not select.select([sys.stdin], [], [], 0.3)[0], "premature input"

                publish("turn_running", 0)
                emit_misleading_text()
                for index, expected in enumerate(("first force> sample", "second", "")):
                    publish("awaiting_force", index)
                    print("\\n" + {self.NOTICE!r})
                    assert input("force> ") == expected
                    if expected:
                        emit_misleading_text()
                print("Finished both forced turns")
                """), encoding="utf-8")
            code = DRIVER.run_duet_with_force_pty(
                peer, config, log, ("first force> sample", "second"), timeout=5,
            )
            output = log.read_text(encoding="utf-8")
            self.assertEqual(code, 0, output)
            self.assertIn("Finished both forced turns", output)
