"""Native delegation controls on fresh, resumed, and upstream agent calls."""
from __future__ import annotations

import json
import os
import pathlib
import shlex
import sys
import tempfile
import unittest
from unittest import mock

import duet


SID = "019e16c2-635e-7802-83e8-400e93533d2f"


class TestNoSubagents(unittest.TestCase):
    def test_custom_role_cannot_omit_the_no_delegation_instruction(self):
        agent = duet.Agent("peer", "codex", role_prompt="Inspect {data} and {SENTINEL}")
        prompt = agent.system_prompt("DONE")
        self.assertIn("Inspect {data} and DONE", prompt)
        self.assertIn("Do not spawn or delegate to subagents", prompt)
        self.assertIn("through shell commands", prompt)

    def test_codex_controls_follow_extra_args_on_every_resume_path(self):
        for sid in (None, SID, "codex-current"):
            with self.subTest(sid=sid):
                agent = duet.Agent("peer", "codex", session_id=sid, extra_args=[
                    "-c", "agents.enabled=true", "--enable", "multi_agent",
                    "-c", "features.multi_agent=true", "-c", 'approvals_reviewer="auto_review"',
                ])
                with mock.patch.object(duet, "_run", return_value=(0, "ok", "")) as run:
                    duet.call_codex(agent, "system", "message", pathlib.Path.cwd(),
                                    "read-only", 10, False, first_turn=sid is None)
                cmd = run.call_args.args[0]
                config = dict(arg.split("=", 1) for i, arg in enumerate(cmd)
                              if i > 0 and cmd[i - 1] == "-c")
                self.assertEqual(config["agents.enabled"], "false")
                self.assertEqual(config["features.multi_agent"], "false")
                self.assertEqual(config["approvals_reviewer"], '"user"')
                self.assertEqual(cmd[cmd.index("--disable") + 1], "multi_agent")
                self.assertTrue(cmd[-1].startswith("=== ROLE ==="))
                if sid:
                    self.assertNotIn("--sandbox", cmd)
                    self.assertNotIn("--cd", cmd)
                    self.assertEqual(config["sandbox_mode"], '"read-only"')

    def test_claude_preserves_existing_denials_and_blocks_forking_tools(self):
        for sid in (None, SID):
            with self.subTest(sid=sid):
                agent = duet.Agent("peer", "claude", session_id=sid, extra_args=[
                    "--disallowed-tools", "Bash(git push *)", "WebFetch",
                    "--disallowedTools=WebSearch", "--effort", "low",
                ])
                output = json.dumps({"result": "ok", "session_id": SID})
                with mock.patch.object(duet, "_run", return_value=(0, output, "")) as run:
                    duet.call_claude(agent, "system", "message", pathlib.Path.cwd(),
                                     "acceptEdits", 10, False)
                cmd = run.call_args.args[0]
                denied = cmd[cmd.index("--disallowedTools") + 1].split(",")
                self.assertEqual(set(denied), {"Agent", "Task", "TeamCreate", "SendMessage",
                                               "Skill", "Bash(git push *)", "WebFetch", "WebSearch"})
                self.assertIn("low", cmd)
                self.assertEqual("--resume" in cmd, sid is not None)
                self.assertEqual(run.call_args.kwargs["env_overrides"]["CLAUDE_CODE_FORK_SUBAGENT"], "0")

    def test_copilot_excludes_delegation_without_losing_user_exclusions(self):
        agent = duet.Agent("peer", "copilot", extra_args=[
            "--excluded-tools=web_fetch", "--excluded-tools", "bash", "--model", "chosen",
        ])
        result = duet._delegation_safe_extra_args(agent)
        excluded = result[result.index("--excluded-tools") + 1].split(",")
        self.assertEqual(set(excluded), {"task", "write_agent", "web_fetch", "bash"})
        self.assertEqual(result[:2], ["--model", "chosen"])

    def test_other_adapters_apply_controls_on_fresh_and_resumed_calls(self):
        outputs = {
            "gemini": json.dumps({"response": "ok", "session_id": SID}),
            "copilot": "\n".join([
                json.dumps({"type": "assistant.message", "data": {"content": "ok"}}),
                json.dumps({"type": "result", "sessionId": SID, "exitCode": 0}),
            ]),
            "opencode": json.dumps({"type": "text", "sessionID": SID,
                                    "part": {"id": "text", "type": "text", "text": "ok"}}),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            env = {"GEMINI_CLI_SYSTEM_SETTINGS_PATH": str(root / "system.json"),
                   "OPENCODE_CONFIG_CONTENT": "{}"}
            for backend in outputs:
                for resuming in (False, True):
                    with self.subTest(backend=backend, resuming=resuming):
                        agent = duet.Agent("peer", backend, session_id=SID if resuming else None)
                        cfg = duet.DuetConfig(cwd=root, agents=[agent, duet.Agent("other", "codex")],
                                              metrics_enabled=False)

                        def run(cmd, **kwargs):
                            overrides = kwargs["env_overrides"]
                            if backend == "gemini":
                                path = pathlib.Path(overrides["GEMINI_CLI_SYSTEM_SETTINGS_PATH"])
                                self.assertFalse(json.loads(path.read_text())["experimental"]["enableAgents"])
                                self.assertEqual("--resume" in cmd, resuming)
                            elif backend == "opencode":
                                self.assertEqual(json.loads(overrides["OPENCODE_CONFIG_CONTENT"])["subagent_depth"], 0)
                                self.assertIn("--auto", cmd)
                                self.assertEqual("-s" in cmd, resuming)
                            else:
                                denied = cmd[cmd.index("--excluded-tools") + 1].split(",")
                                self.assertTrue({"task", "write_agent"}.issubset(denied))
                                self.assertEqual(f"--resume={SID}" in cmd, resuming)
                            return 0, outputs[backend], ""

                        with mock.patch.dict(os.environ, env), mock.patch.object(duet, "_run", side_effect=run):
                            self.assertEqual(duet.call_agent(agent, "task", cfg, not resuming), "ok")
                        self.assertEqual(agent.session_id, SID)

    def test_extra_args_cannot_hide_policy_or_attach_to_an_uncontrolled_server(self):
        cases = [(backend, ["--"]) for backend in duet.SUPPORTED_BACKENDS]
        cases += [("opencode", ["--attach", "http://localhost:1234"]),
                  ("opencode", ["--attach=http://localhost:1234"]),
                  ("opencode", ["--dangerously-skip-permissions"]),
                  ("codex", ["--approve-for-me"])]
        for backend, extra in cases:
            with self.subTest(backend=backend, extra=extra), self.assertRaises(ValueError):
                duet._delegation_safe_extra_args(duet.Agent("peer", backend, extra_args=extra))

    def test_opencode_depth_override_preserves_inline_settings_and_parent_environment(self):
        original = json.dumps({"subagent_depth": 4, "model": "example/model",
                               "permission": {"bash": "deny"}})
        with mock.patch.dict(os.environ, {"OPENCODE_CONFIG_CONTENT": original}):
            with duet._agent_environment("opencode") as env:
                config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
                self.assertEqual(config["subagent_depth"], 0)
                self.assertEqual(config["permission"], {"bash": "deny"})
                self.assertEqual(config["model"], "example/model")
            self.assertEqual(os.environ["OPENCODE_CONFIG_CONTENT"], original)

    def test_invalid_inline_config_stops_before_spawning_a_process(self):
        for raw in ("not-json-private-value", "[]", "null"):
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {"OPENCODE_CONFIG_CONTENT": raw}), \
                    mock.patch.object(duet, "_run") as run:
                with self.assertRaises(duet.AgentRunError) as error:
                    duet._agent_run(["opencode", "run"], backend="opencode", cwd=pathlib.Path.cwd(),
                                    stdin=None, timeout=10, stderr_log_path=None, pid_file_path=None)
                run.assert_not_called()
                self.assertNotIn(raw, str(error.exception))

    def test_gemini_overlay_preserves_settings_and_is_cleaned_up_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / "system.json"
            original = json.dumps({"experimental": {"enableAgents": True, "worktrees": False},
                                   "security": {"auth": {"selectedType": "existing"}}})
            source.write_text(original)
            with mock.patch.dict(os.environ, {"GEMINI_CLI_SYSTEM_SETTINGS_PATH": str(source)}):
                with self.assertRaisesRegex(RuntimeError, "test failure"):
                    with duet._agent_environment("gemini") as env:
                        overlay = pathlib.Path(env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"])
                        config = json.loads(overlay.read_text())
                        self.assertFalse(config["experimental"]["enableAgents"])
                        self.assertFalse(config["experimental"]["worktrees"])
                        self.assertEqual(config["security"], json.loads(original)["security"])
                        self.assertNotEqual(overlay, source)
                        self.assertEqual(overlay.parent.stat().st_mode & 0o777, 0o700)
                        raise RuntimeError("test failure")
                self.assertFalse(overlay.exists())
                self.assertEqual(source.read_text(), original)
                self.assertEqual(os.environ["GEMINI_CLI_SYSTEM_SETTINGS_PATH"], str(source))

    def test_environment_override_reaches_child_without_mutating_parent(self):
        with mock.patch.dict(os.environ, {"DUET_TEST_CHILD_POLICY": "parent"}):
            rc, out, _ = duet._run(
                [sys.executable, "-c", "import os; print(os.environ['DUET_TEST_CHILD_POLICY'])"],
                cwd=pathlib.Path.cwd(), stdin=None, timeout=10,
                env_overrides={"DUET_TEST_CHILD_POLICY": "child"},
            )
            self.assertEqual((rc, out.strip()), (0, "child"))
            self.assertEqual(os.environ["DUET_TEST_CHILD_POLICY"], "parent")

    def test_review_recipe_kickoff_denies_native_delegation(self):
        parser = duet._build_arg_parser()
        args = parser.parse_args(["--recipe", "review"])
        duet._apply_recipe_args(args)
        cmd = shlex.split(args.task_from_cmd)
        self.assertEqual(cmd[:5], ["claude", "-p", "/review", "--model", "sonnet"])
        denied = cmd[cmd.index("--disallowedTools") + 1].split(",")
        self.assertTrue({"Agent", "Task", "TeamCreate", "SendMessage", "Skill"}.issubset(denied))
        self.assertIn("Do not spawn or delegate", cmd[cmd.index("--append-system-prompt") + 1])

    def test_recorded_policy_does_not_claim_historical_runs_disabled_subagents(self):
        agent = duet.Agent("peer", "codex")
        cfg = duet.DuetConfig(cwd=pathlib.Path.cwd(), agents=[agent, duet.Agent("second", "codex")])
        profile = duet.metrics_agent_profile(agent, cfg)
        self.assertEqual(profile["subagent_policy"], "disabled")
        self.assertNotIn("subagent_count", profile)
        self.assertIsNone(duet._metrics_snapshot_profile({}, "lead")["subagent_policy"])


if __name__ == "__main__":
    unittest.main()
