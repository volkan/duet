"""Check native command arguments without launching agent CLIs."""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import unittest
from unittest import mock

import duet


ROOT = pathlib.Path(__file__).resolve().parents[1]
SESSION = "019e12ad-0b1b-7732-bd7b-6acbbd04ab46"
OTHER_SESSION = "11111111-2222-3333-4444-555555555555"


class TestCodexInvocation(unittest.TestCase):
    def _call(self, agent, *, first_turn=False, sandbox="workspace-write", **options):
        return duet.call_codex(
            agent, "sys", "msg", ROOT, sandbox, 60,
            dry=False, first_turn=first_turn, **options,
        )

    def test_fresh_launch_uses_sandbox_cwd_and_parsed_uuid(self) -> None:
        agent = duet.Agent("coder", "codex")
        stderr = f"starting up\nsession id: {SESSION}\nsession id: {OTHER_SESSION}\n"
        with mock.patch.object(duet, "_run", return_value=(0, "ok", stderr)) as run:
            _, session = self._call(agent, first_turn=True)

        cmd = run.call_args.args[0]
        self.assertEqual(session, SESSION)
        self.assertEqual(cmd[:2], ["codex", "exec"])
        self.assertIn("--sandbox", cmd)
        self.assertIn("--cd", cmd)
        self.assertTrue(cmd[-1].startswith("=== ROLE ==="))

    def test_uuid_resume_keeps_session_pin_and_reasserts_sandbox(self) -> None:
        agent = duet.Agent("coder", "codex", session_id=SESSION)
        for reported_session in (SESSION, OTHER_SESSION):
            with self.subTest(reported_session=reported_session), \
                    mock.patch.object(duet, "_run", return_value=(
                        0, "ok", f"session id: {reported_session}\n",
                    )) as run, contextlib.redirect_stderr(io.StringIO()):
                _, session = self._call(agent, sandbox="read-only")

            cmd = run.call_args.args[0]
            self.assertEqual(session, SESSION)
            self.assertEqual(cmd[:4], ["codex", "exec", "resume", SESSION])
            for flag in ("--last", "--sandbox", "--cd"):
                self.assertNotIn(flag, cmd)
            self.assertEqual(cmd[-3:-1], ["-c", 'sandbox_mode="read-only"'])
            self.assertTrue(cmd[-1].startswith("=== ROLE ==="))

    def test_legacy_resume_reasserts_sandbox_and_uses_last(self) -> None:
        agent = duet.Agent("coder", "codex", session_id="codex-current")
        with mock.patch.object(duet, "_run", return_value=(0, "ok", "")) as run:
            self._call(agent, sandbox="read-only")

        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:4], ["codex", "exec", "resume", "--last"])
        self.assertNotIn("--sandbox", cmd)
        self.assertNotIn("--cd", cmd)
        self.assertEqual(cmd[-3:-1], ["-c", 'sandbox_mode="read-only"'])

    def test_missing_session_starts_fresh_even_after_first_turn(self) -> None:
        agent = duet.Agent("coder", "codex")
        with mock.patch.object(duet, "_run", return_value=(0, "ok", "")) as run:
            _, session = self._call(agent)

        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:2], ["codex", "exec"])
        self.assertIn("--sandbox", cmd)
        self.assertNotIn("resume", cmd)
        self.assertEqual(session, "codex-current")

    def test_fast_options_apply_to_fresh_and_resumed_turns(self) -> None:
        for session in (None, "codex-current"):
            agent = duet.Agent("coder", "codex", role="coder", session_id=session)
            with self.subTest(session=session), \
                    mock.patch.object(duet, "_run", return_value=(0, "ok", "")) as run:
                self._call(agent, first_turn=session is None, reasoning="high", fast=True)

            cmd = run.call_args.args[0]
            self.assertIn("model_reasoning_effort=low", cmd)
            self.assertNotIn("model_reasoning_effort=minimal", cmd)
            self.assertIn("model_reasoning_summary=concise", cmd[:-1])
            if session:
                self.assertEqual(cmd[:4], ["codex", "exec", "resume", "--last"])

    def test_fast_config_only_applies_to_the_coder(self) -> None:
        planner = duet.Agent("planner", "codex", role="planner")
        coder = duet.Agent("coder", "codex", role="coder")
        cfg = duet.DuetConfig(
            cwd=ROOT, agents=[planner, coder], task="x",
            sandbox="workspace-write", permission_mode="acceptEdits",
            reasoning="high", codex_fast=True,
        )
        with mock.patch.object(duet, "_run", return_value=(0, "ok", "")) as run:
            duet.call_agent(planner, "msg", cfg, first_turn_for_agent=True)
            duet.call_agent(coder, "msg", cfg, first_turn_for_agent=True)

        planner_cmd = run.call_args_list[0].args[0]
        coder_cmd = run.call_args_list[1].args[0]
        self.assertIn("model_reasoning_effort=high", planner_cmd)
        self.assertNotIn("model_reasoning_effort=low", planner_cmd)
        self.assertNotIn("model_reasoning_summary=concise", planner_cmd)
        self.assertIn("model_reasoning_effort=low", coder_cmd)
        self.assertNotIn("model_reasoning_effort=high", coder_cmd)
        self.assertIn("model_reasoning_summary=concise", coder_cmd)


class TestGeminiInvocation(unittest.TestCase):
    def test_fresh_launch_and_resume_use_native_flags(self) -> None:
        agent = duet.Agent("coder", "gemini", role="coder", model="test-model", extra_args=["--debug"])
        reply = json.dumps({"response": "ok", "session_id": "gemini-sid"})
        with mock.patch.object(duet, "_run", return_value=(0, reply, "")) as run:
            _, session = duet.call_gemini(
                agent, "sys", "msg", ROOT, "acceptEdits", 60,
                dry=False, first_turn=True, reasoning="max", add_dirs=[ROOT.parent],
            )
            agent.session_id = session
            duet.call_gemini(agent, "sys", "msg", ROOT, "bypassPermissions", 60,
                             dry=False, first_turn=False)

        self.assertEqual(session, "gemini-sid")
        first = run.call_args_list[0].args[0]
        self.assertEqual(first[0], "gemini")
        for flag, value in (
            ("--output-format", "json"), ("--approval-mode", "auto_edit"),
            ("--model", "test-model"), ("--include-directories", str(ROOT.parent)),
        ):
            self.assertEqual(first[first.index(flag) + 1], value)
        self.assertIn("--debug", first)
        self.assertIn("ultrathink", first[first.index("-p") + 1])
        for flag in ("--sandbox", "--permission-mode", "-c"):
            self.assertNotIn(flag, first)
        resume = run.call_args_list[1].args[0]
        self.assertEqual(resume[resume.index("--resume") + 1], session)
        self.assertEqual(resume[resume.index("--approval-mode") + 1], "yolo")


class TestOpenCodeInvocation(unittest.TestCase):
    def test_legacy_launch_preserves_custom_agent_and_resumes_by_session(self) -> None:
        text = json.dumps({"type": "text", "sessionID": "oc-sid",
                           "part": {"type": "text", "text": "ok", "id": "p1"}})
        agent = duet.Agent("coder", "opencode", role="coder",
                           model="test/model", extra_args=["--agent", "build"])
        with mock.patch.object(duet, "_OPENCODE_CLI_MAJOR", 1), \
                mock.patch.object(duet, "_run", side_effect=[
                    (0, '{"subagent_depth": 0}', ""), (0, text, ""),
                    (0, '{"subagent_depth": 0}', ""), (0, text, ""),
                ]) as run:
            _, session = duet.call_opencode(agent, "sys", "msg", ROOT, 60, dry=False,
                                            first_turn=True, reasoning="max")
            agent.session_id = session
            duet.call_opencode(agent, "sys", "msg", ROOT, 60, dry=False, first_turn=False)

        first = run.call_args_list[1].args[0]
        self.assertEqual(first[:2], ["opencode", "run"])
        self.assertIn("--auto", first)
        self.assertEqual(first[first.index("--format") + 1], "json")
        self.assertEqual(first[first.index("--dir") + 1], str(ROOT))
        self.assertEqual(first[first.index("--agent") + 1], "build")
        self.assertIn("ultrathink", first[-1])
        for flag in ("--dangerously-skip-permissions", "--sandbox", "--permission-mode", "-s"):
            self.assertNotIn(flag, first)
        resume = run.call_args_list[3].args[0]
        self.assertEqual(resume[resume.index("-s") + 1], "oc-sid")
