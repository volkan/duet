"""Exercise the data-only native ChatGPT control plane without model calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import duet


ROOT = Path(__file__).resolve().parent.parent
CONTROL_PATH = ROOT / "plugins/duet-chatgpt/skills/duet-chatgpt/scripts/control.py"
control = SimpleNamespace(**runpy.run_path(str(CONTROL_PATH), run_name="duet_chatgpt_control"))
APPROVAL = "LGTM rationale: The inspected result satisfies the task and checks pass.\n<<<LGTM>>>\n"
REVISION = "a" * 64


class TestChatGPTControl(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run = self.root / "run"
        self.task = self.root / "task.txt"
        self.task.write_text("private-task-marker", encoding="utf-8")
        self.reply = self.root / "reply.txt"
        self.reply.write_text(APPROVAL, encoding="utf-8")

    def invoke(self, command, *arguments, expected=0):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            result = control.main([command, "--run-dir", str(self.run), *map(str, arguments)])
        self.assertEqual(result, expected, error.getvalue())
        return output.getvalue()

    def initialize(self, turns=6, bound=True):
        self.invoke("init", "--task-file", self.task, "--turns", turns)
        if bound:
            for role in control.ROLES:
                self.invoke("bind", "--role", role, "--agent-id", role + "-private-handle")

    def record(self, turn, reply=APPROVAL, revision=REVISION, outcome="success", expected=0):
        role = control.ROLES[(turn - 1) % 2]
        self.reply.write_text(reply, encoding="utf-8")
        return self.invoke("record", "--turn", turn, "--role", role,
                           "--agent-id", role + "-private-handle", "--revision", revision,
                           "--reply-file", self.reply, "--outcome", outcome, expected=expected)

    def state(self):
        return control.load(self.run)

    def test_distinct_pinned_handles_and_binding_before_turns(self):
        self.initialize(bound=False)
        self.record(1, expected=2)
        self.invoke("bind", "--role", "reviewer", "--agent-id", "reviewer-private-handle")
        before = (self.run / "state.json").read_bytes()
        self.invoke("bind", "--role", "coder", "--agent-id", "reviewer-private-handle", expected=2)
        self.invoke("bind", "--role", "reviewer", "--agent-id", "replacement", expected=2)
        self.assertEqual((self.run / "state.json").read_bytes(), before)
        self.invoke("bind", "--role", "reviewer", "--agent-id", "reviewer-private-handle")
        self.invoke("bind", "--role", "coder", "--agent-id", "coder-private-handle")
        self.record(1, reply="Please fix the missing input check.")
        self.invoke("bind", "--role", "coder", "--agent-id", "coder-private-handle", expected=2)

    def test_wrong_role_handle_and_stale_turn_leave_state_unchanged(self):
        self.initialize()
        self.record(1, reply="Please fix the missing input check.")
        before = (self.run / "state.json").read_bytes()
        self.record(1, expected=2)
        for role, handle in (("reviewer", "reviewer-private-handle"), ("coder", "unknown")):
            self.invoke("record", "--turn", 2, "--role", role, "--agent-id", handle,
                        "--revision", REVISION, "--reply-file", self.reply,
                        "--outcome", "success", expected=2)
        self.assertEqual((self.run / "state.json").read_bytes(), before)

    def test_two_approvals_on_same_revision_converge(self):
        self.initialize()
        self.record(1)
        self.assertIsNone(self.state()["finished_reason"])
        self.record(2)
        self.assertEqual(self.state()["finished_reason"], "converged")
        self.record(3, expected=2)

    def test_changed_revision_and_nonapproval_break_agreement_sequence(self):
        for breaker in ("revision", "nonapproval"):
            with self.subTest(breaker=breaker):
                self.run = self.root / breaker
                self.initialize()
                self.record(1)
                changed = "b" * 64
                self.record(2, revision=changed,
                            reply=APPROVAL if breaker == "revision" else "Please fix the missing check.")
                self.assertIsNone(self.state()["finished_reason"])
                self.record(3, revision=changed)
                if breaker == "nonapproval":
                    self.assertIsNone(self.state()["finished_reason"])
                    self.record(4, revision=changed)
                self.assertEqual(self.state()["finished_reason"], "converged")

    def test_agreement_parser_matches_existing_duet(self):
        cases = [
            APPROVAL, "<<<LGTM>>>", "LGTM rationale: ok\n<<<LGTM>>>",
            "```text\n" + APPROVAL + "```\n",
            "~~~text\n" + APPROVAL + "~~~\n",
            "````text\n```\n" + APPROVAL + "```\n````\n",
            "LGTM rationale: All checks pass and the result meets the task.\n`<<<LGTM>>>`\n",
            "<<<LGTM>>>\nLGTM rationale: All checks pass and the result meets the task.\n",
            "LGTM rationale: a" + " " * 50 + "b\n<<<LGTM>>>\n",
            "LGTM rationale: " + " " * 50 + "\n<<<LGTM>>>\n",
            "- **Rationale**: All checks pass and the result meets the task.\n <<<LGTM>>> \n",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(control.proposes_agreement(text), duet.convergence_proposed(text, "<<<LGTM>>>"))

    def test_terminal_reasons_reject_later_records(self):
        for reason in ("max_turns", "error", "timeout", "user_stop"):
            with self.subTest(reason=reason):
                self.run = self.root / reason
                self.initialize(turns=2)
                if reason == "user_stop":
                    self.invoke("stop", "--reason", reason)
                elif reason == "max_turns":
                    self.record(1, reply="Changes requested.")
                    self.record(2, reply="Changes made; review needed.")
                else:
                    self.record(1, reply="private-failure-marker", outcome=reason)
                expected = "peer_error" if reason == "error" else reason
                self.assertEqual(self.state()["finished_reason"], expected)
                before = (self.run / "state.json").read_bytes()
                self.record(len(self.state()["history"]) + 1, expected=2)
                self.assertEqual((self.run / "state.json").read_bytes(), before)

    def test_copied_or_nonfinal_approval_stanzas_do_not_approve(self):
        examples = (
            "    " + APPROVAL.replace("\n", "\n    ").rstrip(),
            '\t' + APPROVAL.replace("\n", "\n\t").rstrip(),
            'The prior output was:\n"\n' + APPROVAL + '"\nI am quoting that output.',
            "> Quoted prior reply:\n" + APPROVAL,
            APPROVAL + "\nI do not approve.",
            "Recap without an approval paragraph.\n" + APPROVAL,
            APPROVAL.replace("\n<<<LGTM>>>", "\n\n<<<LGTM>>>"),
            APPROVAL.replace("\n<<<LGTM>>>", "\n> quoted evidence\n\n<<<LGTM>>>"),
            APPROVAL + "\nThe earlier approval is obsolete; tests failed.\n\n<<<LGTM>>>\n",
        )
        for reply in examples:
            with self.subTest(reply=reply):
                self.assertFalse(control.proposes_agreement(reply))
        self.assertTrue(control.proposes_agreement("Checks inspected.\n\n" + APPROVAL))
        self.assertTrue(control.proposes_agreement("<<<LGTM>>>\n\n" + APPROVAL))

    def test_verification_failure_invalidates_terminal_convergence(self):
        self.initialize()
        self.record(1)
        self.record(2)
        self.invoke("stop", "--reason", "verification_failed")
        self.assertEqual(self.state()["finished_reason"], "verification_failed")
        self.assertEqual(len(self.state()["history"]), 2)
        self.record(3, expected=2)
        self.invoke("stop", "--reason", "user_stop")
        self.assertEqual(self.state()["finished_reason"], "verification_failed")

    def test_exclusive_lock_blocks_all_other_mutations(self):
        self.initialize(bound=False)
        before = (self.run / "state.json").read_bytes()
        with control.mutation(self.run):
            self.invoke("bind", "--role", "reviewer", "--agent-id", "handle", expected=2)
            self.record(1, expected=2)
            self.invoke("stop", "--reason", "user_stop", expected=2)
            self.assertTrue((self.run / ".control.lock").exists())
            self.assertEqual((self.run / "state.json").read_bytes(), before)
        self.assertFalse((self.run / ".control.lock").exists())

    def test_atomic_write_failure_preserves_private_state_and_cleans_temporary(self):
        self.initialize()
        path = self.run / "state.json"
        before = path.read_bytes()
        with mock.patch.object(control.os, "replace", side_effect=OSError("injected replace failure")):
            with self.assertRaises(OSError):
                control.save(self.run, {"replacement": True})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.run.iterdir()), [path])
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.run.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_status_excludes_task_reply_and_handles(self):
        self.initialize()
        self.record(1, reply="private-reply-marker")
        output = self.invoke("status")
        for secret in ("private-task-marker", "private-reply-marker", "reviewer-private-handle", "coder-private-handle"):
            self.assertNotIn(secret, output)
        report = json.loads(output)
        self.assertEqual(report["next_role"], "coder")
        self.assertEqual(report["completed_turns"], 1)

    def test_fingerprint_order_content_and_names(self):
        other = self.root / "other.txt"
        other.write_text("other", encoding="utf-8")
        original = control.fingerprint([self.task, other])
        self.assertEqual(original, control.fingerprint([other, self.task, other]))
        other.write_text("changed", encoding="utf-8")
        self.assertNotEqual(original, control.fingerprint([self.task, other]))
        restored = self.root / "renamed.txt"
        restored.write_text("other", encoding="utf-8")
        self.assertNotEqual(original, control.fingerprint([self.task, restored]))

    def test_subprocess_cli_init_bind_record_status_transcript(self):
        def cli(command, *arguments):
            process = subprocess.run([sys.executable, str(CONTROL_PATH), command,
                                      "--run-dir", str(self.run), *map(str, arguments)],
                                     capture_output=True, text=True, check=False)
            self.assertEqual(process.returncode, 0, process.stderr)
            return process.stdout

        cli("init", "--task-file", self.task, "--turns", 2)
        for role in control.ROLES:
            cli("bind", "--role", role, "--agent-id", role + "-handle")
        for turn, role in enumerate(control.ROLES, 1):
            cli("record", "--turn", turn, "--role", role, "--agent-id", role + "-handle",
                "--revision", REVISION, "--reply-file", self.reply, "--outcome", "success")
        self.assertEqual(json.loads(cli("status"))["finished_reason"], "converged")
        rendered = cli("transcript")
        self.assertIn("Turn 1: reviewer", rendered)
        self.assertIn("Turn 2: coder", rendered)
        self.assertIn(APPROVAL.strip(), rendered)
        self.assertIn("Stop reason: converged", rendered)


if __name__ == "__main__":
    unittest.main()
