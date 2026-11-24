"""Contract tests for recipe launch metadata and machine-readable status."""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

import duet


def _agents() -> list[duet.Agent]:
    return [
        duet.Agent(name="claude-reviewer", backend="claude", role="reviewer"),
        duet.Agent(name="codex-coder", backend="codex", role="coder"),
    ]


class TestReviewRecipe(unittest.TestCase):
    def _args(self, *argv: str):
        parser = duet._build_arg_parser()
        args = parser.parse_args(list(argv))
        duet._apply_recipe_args(args)
        return parser, args

    def test_review_defaults_are_canonical(self) -> None:
        parser, args = self._args("--recipe", "review")
        cfg = duet._build_cfg_from_cli(args, parser, {})
        self.assertEqual(args.lead, "claude:reviewer")
        self.assertEqual(args.partner, "codex:coder")
        self.assertEqual(args.turns, 6)
        self.assertTrue(args.recap)
        self.assertTrue(args.worktree)
        self.assertTrue(args.require_worktree)
        self.assertEqual(args.lead_model, "sonnet")
        self.assertIsNone(args.task_from_cmd)
        self.assertEqual(cfg.task_from_cmd[:len("claude -p /review --model sonnet")],
                         "claude -p /review --model sonnet")
        self.assertEqual(cfg._kickoff_kind, duet.REVIEW_KICKOFF_KIND)
        self.assertEqual(
            pathlib.Path(args.runs_dir),
            pathlib.Path.cwd().resolve() / ".duet" / "runs",
        )

    def test_explicit_values_override_recipe_and_pin_kickoff(self) -> None:
        parser, args = self._args(
            "--recipe", "review",
            "--lead-model", "claude-fable-5",
            "--partner", "gemini:coder",
            "--turns", "3",
            "--allow-worktree-fallback",
        )
        cfg = duet._build_cfg_from_cli(args, parser, {})
        self.assertEqual(cfg.agents[0].model, "claude-fable-5")
        self.assertEqual(cfg.agents[1].backend, "gemini")
        self.assertEqual(cfg.max_turns, 3)
        self.assertFalse(cfg.require_worktree)
        self.assertEqual(
            duet.shlex.split(cfg.task_from_cmd)[:5],
            ["claude", "-p", "/review", "--model", "claude-fable-5"],
        )

    def test_non_claude_lead_keeps_sonnet_kickoff_default(self) -> None:
        parser, args = self._args(
            "--recipe", "review",
            "--lead", "gemini:reviewer",
        )
        cfg = duet._build_cfg_from_cli(args, parser, {})

        self.assertIsNone(args.lead_model)
        self.assertEqual(
            duet.shlex.split(cfg.task_from_cmd)[:5],
            ["claude", "-p", "/review", "--model", "sonnet"],
        )

    def test_explicit_seed_suppresses_recipe_kickoff(self) -> None:
        _, args = self._args("--recipe", "review", "--task", "inspect this")
        self.assertIsNone(args.task_from_cmd)
        self.assertEqual(args.task, "inspect this")

    def test_codex_resume_without_seed_keeps_review_kickoff(self) -> None:
        session_id = "019e16c2-635e-7802-83e8-400e93533d2f"
        parser, args = self._args("--recipe", "review", "--resume-codex", session_id)
        cfg = duet._build_cfg_from_cli(args, parser, {})
        self.assertEqual(duet.shlex.split(cfg.task_from_cmd)[:3],
                         ["claude", "-p", "/review"])
        self.assertEqual(cfg.agents[1].session_id, session_id)
        self.assertEqual(cfg.start_speaker_idx, 1)

    def test_review_recipe_defaults_to_timeout_continue(self) -> None:
        parser, args = self._args("--recipe", "review", "--task", "x")
        cfg = duet._build_cfg_from_cli(args, parser, {})
        self.assertEqual(cfg.on_turn_timeout, "continue")

    def test_explicit_on_turn_timeout_stop_overrides_recipe(self) -> None:
        parser, args = self._args(
            "--recipe", "review", "--task", "x", "--on-turn-timeout", "stop"
        )
        cfg = duet._build_cfg_from_cli(args, parser, {})
        self.assertEqual(cfg.on_turn_timeout, "stop")

    def test_explicit_worktree_path_replaces_recipe_creation_but_stays_strict(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            reused = pathlib.Path(raw).resolve()
            parser = duet._build_arg_parser()
            args = parser.parse_args([
                "--recipe", "review",
                "--task", "inspect this",
                "--worktree-path", str(reused),
            ])

            duet._validate_run_arguments(args, parser)
            cfg = duet._build_cfg_from_cli(args, parser, {})

            self.assertFalse(cfg.worktree)
            self.assertEqual(cfg.worktree_path, reused)
            self.assertTrue(cfg.require_worktree)

    def test_explicit_fallback_still_overrides_recipe_with_reused_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            reused = pathlib.Path(raw).resolve()
            parser = duet._build_arg_parser()
            args = parser.parse_args([
                "--recipe", "review",
                "--task", "inspect this",
                "--worktree-path", str(reused),
                "--allow-worktree-fallback",
            ])

            duet._validate_run_arguments(args, parser)
            cfg = duet._build_cfg_from_cli(args, parser, {})

            self.assertFalse(cfg.worktree)
            self.assertEqual(cfg.worktree_path, reused)
            self.assertFalse(cfg.require_worktree)


class TestCodexReviewRecipe(unittest.TestCase):
    def _cfg(self, *argv: str) -> duet.DuetConfig:
        parser = duet._build_arg_parser()
        args = parser.parse_args(["--recipe", "codex-review", *argv])
        duet._validate_run_arguments(args, parser)
        return duet._build_cfg_from_cli(args, parser, {})

    def test_defaults_need_only_two_codex_sessions_and_review_first(self) -> None:
        cfg = self._cfg()
        self.assertEqual(
            [(a.backend, a.role, a.model) for a in cfg.agents],
            [("codex", "reviewer", None), ("codex", "coder", None)],
        )
        self.assertEqual(cfg.start_speaker_idx, 0)
        self.assertIsNone(cfg.task_from_cmd)
        self.assertIn("latest commit (HEAD)", cfg.task)
        self.assertEqual(cfg.max_turns, 6)
        self.assertTrue(cfg.worktree)
        self.assertTrue(cfg.require_worktree)
        self.assertEqual(cfg.worktree_for, "partner")
        self.assertTrue(cfg.recap)
        self.assertTrue(cfg.finding_reports)
        self.assertEqual(cfg.on_turn_timeout, "continue")

    def test_model_and_workflow_overrides_are_preserved(self) -> None:
        cfg = self._cfg(
            "--lead-model", "review-model", "--partner-model", "coding-model",
            "--turns", "4", "--reasoning", "medium", "--no-worktree",
            "--no-recap", "--no-finding-reports", "--on-turn-timeout", "stop",
        )
        self.assertEqual([a.model for a in cfg.agents], ["review-model", "coding-model"])
        self.assertEqual(cfg.max_turns, 4)
        self.assertEqual(cfg.reasoning, "medium")
        self.assertFalse(cfg.worktree)
        self.assertFalse(cfg.require_worktree)
        self.assertFalse(cfg.recap)
        self.assertFalse(cfg.finding_reports)
        self.assertEqual(cfg.on_turn_timeout, "stop")

    def test_explicit_seeds_replace_the_default_task(self) -> None:
        for flag, field in (("--task", "task"), ("--kickoff", "kickoff"),
                            ("--task-from-cmd", "task_from_cmd")):
            with self.subTest(flag=flag):
                cfg = self._cfg(flag, "custom seed")
                self.assertEqual(getattr(cfg, field), "custom seed")
                if field != "task":
                    self.assertIsNone(cfg.task)
                self.assertEqual(cfg.start_speaker_idx, 0)

    def test_reused_worktree_keeps_strict_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cfg = self._cfg("--worktree-path", raw)
            self.assertEqual(cfg.worktree_path, pathlib.Path(raw).resolve())
            self.assertFalse(cfg.worktree)
            self.assertTrue(cfg.require_worktree)

    def test_two_reviewers_can_share_a_read_only_checkout(self) -> None:
        cfg = self._cfg("--partner", "codex:reviewer", "--no-worktree",
                        "--sandbox", "read-only")
        self.assertEqual([a.role for a in cfg.agents], ["reviewer", "reviewer"])
        self.assertEqual(cfg.sandbox, "read-only")
        self.assertFalse(cfg.worktree)
        self.assertEqual(cfg.start_speaker_idx, 0)

    def test_explicit_resume_keeps_the_existing_handoff_order(self) -> None:
        session_id = "019e16c2-635e-7802-83e8-400e93533d2f"
        for flag in ("--task", "--kickoff", "--task-from-cmd"):
            with self.subTest(flag=flag):
                cfg = self._cfg("--resume-codex", session_id, flag, "continue the plan")
                self.assertEqual(cfg.start_speaker_idx, 1)
                self.assertEqual(cfg.agents[1].session_id, session_id)
                self.assertEqual(getattr(cfg, flag[2:].replace("-", "_")),
                                 "continue the plan")

    def test_resume_without_seed_retains_default_task_and_reviewer_first(self) -> None:
        session_id = "019e16c2-635e-7802-83e8-400e93533d2f"
        cfg = self._cfg("--resume-codex", session_id)
        with mock.patch.object(duet, "call_agent", side_effect=AssertionError("unexpected CLI")):
            self.assertEqual(duet.derive_seed(cfg), duet.DEFAULT_CODEX_REVIEW_TASK)
        self.assertEqual(cfg.start_speaker_idx, 0)
        self.assertEqual(cfg.agents[0].role, "reviewer")
        self.assertEqual(cfg.agents[1].role, "coder")
        self.assertEqual(cfg.agents[1].session_id, session_id)
        self.assertIsNone(cfg.task_from_cmd)

    def test_resumable_claude_lead_remains_the_seed_source(self) -> None:
        session_id = "019e16c2-635e-7802-83e8-400e93533d2f"
        cfg = self._cfg("--resume-claude", "claude-session", "--resume-codex", session_id)
        self.assertIsNone(cfg.task)
        self.assertIsNone(cfg.task_from_cmd)
        with mock.patch.object(duet, "call_agent", return_value="previous plan") as call:
            self.assertEqual(duet.derive_seed(cfg), "previous plan")
        self.assertIs(call.call_args.args[0], cfg.agents[0])
        self.assertEqual(cfg.start_speaker_idx, 1)
        self.assertEqual(cfg.agents[1].session_id, session_id)

    def test_dry_run_records_reviewer_then_coder_and_distinct_models(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            cfg = self._cfg(
                "--cwd", str(root), "--runs-dir", str(root / "runs"),
                "--dry-run", "--no-worktree", "--no-recap", "--no-metrics", "--turns", "2",
                "--lead-model", "review-model", "--partner-model", "coding-model",
            )
            with mock.patch.object(duet, "_run", side_effect=AssertionError("unexpected CLI")), \
                    mock.patch.object(duet, "_register_run_in_home_index"), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                state = duet.run_duet(cfg)
            self.assertEqual(
                [turn["agent"] for turn in state["history"]],
                ["codex-lead", "codex-partner"],
            )
            self.assertEqual(
                [agent["model"] for agent in state["agents"]],
                ["review-model", "coding-model"],
            )


class TestConfigDryRun(unittest.TestCase):
    def test_cli_preview_cannot_be_disabled_by_config(self) -> None:
        for configured, recap in ((value, recap) for value in (None, False, True)
                                  for recap in (False, True)):
            with self.subTest(configured=configured, recap=recap), tempfile.TemporaryDirectory() as raw:
                root = pathlib.Path(raw).resolve()
                config = {
                    "cwd": str(root), "runs_dir": str(root / "runs"),
                    "agents": [{"name": "reviewer", "backend": "codex", "role": "reviewer"},
                               {"name": "coder", "backend": "codex", "role": "coder"}],
                    "task_from_cmd": "printf seed",
                    "verify_cmd": "printf checked",
                    "max_turns": 2,
                    "recap": recap,
                }
                if configured is not None:
                    config["dry_run"] = configured
                path = root / "config.json"
                path.write_text(json.dumps(config), encoding="utf-8")
                parser = duet._build_arg_parser()
                args = parser.parse_args(["--config", str(path), "--dry-run", "--no-metrics"])
                cfg = duet._build_cfg_from_yaml(args, parser, {})
                with mock.patch.object(duet, "_run", side_effect=AssertionError("preview executed a command")) as command, \
                        mock.patch.object(duet, "_register_run_in_home_index"), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    state = duet.run_duet(cfg)
                self.assertTrue(cfg.dry_run)
                self.assertTrue(state["dry_run"])
                command.assert_not_called()
                self.assertEqual(state["finished_reason"], "dry_run" if recap else "converged")


class TestRunInfoAndLaunchFailures(unittest.TestCase):
    def _cfg(self, root: pathlib.Path, info: pathlib.Path, **kwargs) -> duet.DuetConfig:
        return duet.DuetConfig(
            cwd=root,
            agents=_agents(),
            runs_dir=root / "runs",
            run_info_file=info,
            **kwargs,
        )

    def test_run_info_exists_before_kickoff_and_has_exact_schema(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            info = root / "control" / "run.json"
            info.parent.mkdir()
            command = "printf seed"
            cfg = self._cfg(
                root, info, task_from_cmd=command, max_turns=2, metrics_enabled=False
            )
            def kickoff(cmd, cwd, timeout, run_dir):
                payload = json.loads(info.read_text(encoding="utf-8"))
                self.assertEqual(pathlib.Path(payload["run_dir"]), run_dir)
                self.assertEqual(cwd, root)
                saved = json.loads(pathlib.Path(payload["state_path"]).read_text())
                self.assertEqual(saved["phase"], "kickoff_running")
                return "seed"

            with mock.patch.object(duet, "run_task_from_cmd", side_effect=kickoff) as kickoff_call, \
                    mock.patch.object(duet, "call_agent", return_value=(
                        "LGTM rationale: the requested review is complete and no issues remain.\n"
                        + duet.DEFAULT_SENTINEL
                    )), \
                    mock.patch.object(duet, "_register_run_in_home_index"), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                state = duet.run_duet(cfg)

            kickoff_call.assert_called_once()
            payload = json.loads(info.read_text(encoding="utf-8"))
            self.assertEqual(set(payload), {
                "schema_version", "kind", "duet_version", "run_id",
                "run_dir", "state_path", "pid",
            })
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["kind"], "duet.run")
            self.assertEqual(payload["duet_version"], duet.__version__)
            self.assertTrue(pathlib.Path(payload["run_dir"]).is_absolute())
            self.assertEqual(state["finished_reason"], "converged")
            self.assertEqual(state["task"], "seed")
            self.assertNotIn(command, json.dumps(state))

    def test_existing_run_info_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            info = root / "run.json"
            info.write_text("keep", encoding="utf-8")
            cfg = self._cfg(root, info, task="x")
            with self.assertRaises(duet.RunSetupError):
                duet.run_duet(cfg)
            self.assertEqual(info.read_text(encoding="utf-8"), "keep")

    def test_recap_dry_run_does_not_create_or_validate_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            info = root / "run.json"
            cfg = self._cfg(
                root, info, task="x", dry_run=True, recap=True,
                worktree=True, require_worktree=True,
            )
            with mock.patch.object(duet, "_register_run_in_home_index"), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                state = duet.run_duet(cfg)
            self.assertEqual(state["finished_reason"], "dry_run")
            self.assertIsNone(state["worktree"])

    def test_strict_worktree_failure_is_durable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            info = root / "run.json"
            cfg = self._cfg(
                root, info, task="x", worktree=True, require_worktree=True
            )
            with mock.patch.object(duet, "_register_run_in_home_index"), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                state = duet.run_duet(cfg)
            self.assertEqual(state["finished_reason"], "setup_error")
            self.assertEqual(state["phase"], "finished")
            run = json.loads(info.read_text(encoding="utf-8"))
            self.assertEqual(
                json.loads(pathlib.Path(run["state_path"]).read_text())["finished_reason"],
                "setup_error",
            )

    def test_strict_reused_path_must_be_a_git_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            reused = root / "ordinary-directory"
            reused.mkdir()
            cfg = duet.DuetConfig(
                cwd=root,
                agents=_agents(),
                task="x",
                worktree_path=reused,
                require_worktree=True,
            )
            with self.assertRaises(duet.RunSetupError), \
                    contextlib.redirect_stderr(io.StringIO()):
                duet._setup_run_worktree(cfg, "run", root)

    def test_opencode_version_failure_obeys_worktree_failure_policy(self) -> None:
        for reuse in (False, True):
            for strict in (False, True):
                for version in ((127, "", "not found"), (0, "", "")):
                    with self.subTest(reuse=reuse, strict=strict, version=version), \
                            tempfile.TemporaryDirectory() as raw:
                        root = pathlib.Path(raw).resolve()
                        info = root / "run.json"
                        cfg = self._cfg(
                            root, info, task="x", dry_run=True,
                            worktree=not reuse, worktree_path=root if reuse else None,
                            require_worktree=strict,
                        )
                        cfg.agents[1].backend = "opencode"
                        git = subprocess.CompletedProcess([], 0, stdout="main\n")
                        with mock.patch.object(duet, "_OPENCODE_CLI_MAJOR", None), \
                                mock.patch.object(duet, "_run", return_value=version), \
                                mock.patch.object(duet, "is_git_repo", return_value=True), \
                                mock.patch.object(duet.subprocess, "run", return_value=git), \
                                mock.patch.object(duet, "_register_run_in_home_index"), \
                                contextlib.redirect_stdout(io.StringIO()), \
                                contextlib.redirect_stderr(io.StringIO()):
                            state = duet.run_duet(cfg)
                        self.assertEqual(state["phase"], "finished")
                        self.assertEqual(state["finished_reason"], "setup_error" if strict else "converged")
                        run = json.loads(info.read_text())["run_dir"]
                        status = duet.build_run_status(pathlib.Path(run))
                        self.assertEqual(status["exit_code"], 0)
                        self.assertIsNone(state["worktree"])

    def test_legacy_non_git_reuse_keeps_isolation_kind_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            reused = root / "ordinary-directory"
            reused.mkdir()
            cfg = self._cfg(root, root / "run.json", task="x", dry_run=True, worktree_path=reused)
            with mock.patch.object(duet, "_register_run_in_home_index"), \
                    contextlib.redirect_stdout(io.StringIO()):
                state = duet.run_duet(cfg)
            self.assertEqual(state["worktree"], str(reused))
            self.assertIsNone(state["worktree_kind"])
            self.assertIsNone(state["owned_worktree_path"])

    def test_immediate_stop_during_worktree_setup_is_force_stop(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            info = root / "run.json"
            cfg = self._cfg(root, info, task="x", worktree=True)
            installed: dict[str, duet.StopFlag] = {}

            def capture_stop(stop: duet.StopFlag) -> None:
                installed["stop"] = stop

            def interrupted_setup(*_args):
                installed["stop"].request("SIGTERM")
                raise duet.RunSetupError("interrupted setup")

            with mock.patch.object(duet, "_install_sigint"), \
                    mock.patch.object(
                        duet, "_install_sigterm", side_effect=capture_stop
                    ), \
                    mock.patch.object(
                        duet, "_setup_run_worktree", side_effect=interrupted_setup
                    ), \
                    mock.patch.object(duet, "_register_run_in_home_index"), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                state = duet.run_duet(cfg)

            self.assertEqual(state["phase"], "finished")
            self.assertEqual(state["finished_reason"], "force_stop")
            saved = json.loads(
                pathlib.Path(json.loads(info.read_text())["state_path"]).read_text()
            )
            self.assertEqual(saved["finished_reason"], "force_stop")

    def test_kickoff_failure_is_terminal_but_status_stays_secret_minimized(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            info = root / "run.json"
            secret = "raw-command-secret"
            command = f"false # {secret}"
            cfg = self._cfg(root, info, task_from_cmd=command)
            with mock.patch.object(duet, "_register_run_in_home_index"), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                state = duet.run_duet(cfg)
            run = json.loads(info.read_text(encoding="utf-8"))
            snapshot = duet.build_run_status(run["run_dir"])
            self.assertEqual(state["finished_reason"], "kickoff_error")
            self.assertEqual(snapshot["health"], "terminal")
            self.assertEqual(snapshot["exit_code"], 0)
            self.assertNotIn(secret, json.dumps(snapshot))
            self.assertNotIn(command, json.dumps(state))
            kickoff_log = pathlib.Path(run["run_dir"]) / "turn-00-kickoff.stderr.log"
            self.assertNotIn(secret, kickoff_log.read_text(encoding="utf-8"))


class TestStatusSchema(unittest.TestCase):
    def _write_state(self, root: pathlib.Path, state: dict) -> pathlib.Path:
        run = root / "20260719-120000"
        run.mkdir()
        (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
        return run

    def test_terminal_snapshot_has_stable_keys_and_excludes_sensitive_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            secret = "do-not-leak-this"
            run = self._write_state(root, {
                "task": secret,
                "verify_cmd": secret,
                "agents": [{"extra_args": [secret]}],
                "history": [{"turn": 1, "agent": "a", "error": secret}],
                "turns_used": 1,
                "finished_reason": "max_turns",
                "error": secret,
                "transcript_path": str(root / "transcript.md"),
            })
            snapshot = duet.build_run_status(str(run))
            self.assertEqual(set(snapshot), {
                "schema_version", "kind", "duet_version", "run_id", "run_dir",
                "health", "phase", "exit_code", "turns_used", "finished_reason",
                "per_turn_timeout", "active_turn", "last_completed_turn",
                "last_timeout", "tool_denials", "artifacts", "error",
            })
            self.assertEqual(snapshot["kind"], "duet.status")
            self.assertEqual(snapshot["health"], "terminal")
            self.assertEqual(snapshot["phase"], "finished")
            self.assertIsNone(snapshot["per_turn_timeout"])
            self.assertNotIn(secret, json.dumps(snapshot))

    def test_live_turn_reports_budget_and_remaining_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            secret = "live-status-secret"
            run = self._write_state(root, {
                "task": secret,
                "phase": "turn_running",
                "turns_used": 0,
                "finished_reason": None,
                "history": [],
                "per_turn_timeout": 60,
            })
            (run / "turn-01-codex-coder.pid").write_text("123", encoding="utf-8")
            started = duet.dt.datetime.fromtimestamp(1_000)
            with mock.patch.object(
                    duet, "_pid_file_snapshot", return_value=(123, started)), \
                    mock.patch.object(duet, "_pid_alive", return_value=True), \
                    mock.patch.object(duet.time, "time", return_value=1_042):
                snapshot = duet.build_run_status(str(run))

            self.assertEqual(snapshot["health"], "running")
            self.assertEqual(snapshot["exit_code"], 1)
            self.assertEqual(snapshot["per_turn_timeout"], 60)
            self.assertEqual(set(snapshot["active_turn"]), {
                "label", "pid", "alive", "started_at", "elapsed_seconds",
                "budget_seconds", "remaining_seconds", "stderr_updated_at",
                "stderr_bytes",
            })
            self.assertEqual(snapshot["active_turn"]["elapsed_seconds"], 42)
            self.assertEqual(snapshot["active_turn"]["budget_seconds"], 60)
            self.assertEqual(snapshot["active_turn"]["remaining_seconds"], 18)
            human = io.StringIO()
            with contextlib.redirect_stdout(human):
                duet._print_human_status(snapshot, str(run))
            self.assertIn("turn_timeout:    60s", human.getvalue())
            self.assertIn("budget:       60s  (18s remaining)", human.getvalue())
            self.assertNotIn(secret, json.dumps(snapshot))

    def test_absorbed_timeout_remains_visible_after_partner_turn(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            secret = "timeout-error-secret"
            run = self._write_state(root, {
                "phase": "finished",
                "turns_used": 2,
                "finished_reason": "max_turns",
                "history": [
                    {
                        "turn": 1,
                        "agent": "codex-coder",
                        "elapsed_s": 60.2,
                        "len_chars": 123,
                        "finished_reason": "timeout",
                        "error": secret,
                    },
                    {
                        "turn": 2,
                        "agent": "claude-reviewer",
                        "elapsed_s": 10.5,
                        "len_chars": 456,
                    },
                ],
            })

            snapshot = duet.build_run_status(str(run))

            self.assertEqual(snapshot["last_completed_turn"], {
                "turn": 2,
                "agent": "claude-reviewer",
                "elapsed_seconds": 10.5,
                "output_chars": 456,
                "finished_reason": None,
                "tool_denials": None,
            })
            self.assertEqual(snapshot["last_timeout"], {
                "turn": 1,
                "agent": "codex-coder",
                "elapsed_seconds": 60.2,
                "output_chars": 123,
                "finished_reason": "timeout",
                "tool_denials": None,
            })
            self.assertNotIn(secret, json.dumps(snapshot))

    def test_invalid_budget_values_are_null_for_a_live_turn(self) -> None:
        for invalid in ("untrusted-budget-secret", True, -3):
            with self.subTest(value=invalid), tempfile.TemporaryDirectory() as raw:
                root = pathlib.Path(raw)
                run = self._write_state(root, {
                    "phase": "turn_running",
                    "turns_used": 0,
                    "finished_reason": None,
                    "history": [],
                    "per_turn_timeout": invalid,
                })
                (run / "turn-01-coder.pid").write_text("123", encoding="utf-8")
                started = duet.dt.datetime.fromtimestamp(1_000)
                with mock.patch.object(
                        duet, "_pid_file_snapshot", return_value=(123, started)), \
                        mock.patch.object(duet, "_pid_alive", return_value=True), \
                        mock.patch.object(duet.time, "time", return_value=1_010):
                    snapshot = duet.build_run_status(str(run))

                self.assertEqual(snapshot["health"], "running")
                self.assertEqual(snapshot["exit_code"], 1)
                self.assertIsNone(snapshot["per_turn_timeout"])
                self.assertIsNone(snapshot["active_turn"]["budget_seconds"])
                self.assertIsNone(snapshot["active_turn"]["remaining_seconds"])
                if isinstance(invalid, str):
                    self.assertNotIn(invalid, json.dumps(snapshot))

    def test_relative_artifact_paths_resolve_from_actual_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            cwd = root / "target-project"
            run = root / "invocation" / "relative-runs" / "20260719-120000"
            run.mkdir(parents=True)
            relative_run = pathlib.Path("relative-runs") / run.name
            state = {
                "cwd": str(cwd),
                "phase": "finished",
                "turns_used": 1,
                "finished_reason": "max_turns",
                "history": [],
                "transcript_path": str(relative_run / "transcript.md"),
                "recap_path": str(relative_run / "recap.md"),
                "worktree": str(relative_run / "wt"),
            }
            (run / "state.json").write_text(json.dumps(state), encoding="utf-8")

            artifacts = duet.build_run_status(str(run))["artifacts"]

            self.assertEqual(artifacts["transcript"], str(run / "transcript.md"))
            self.assertEqual(artifacts["recap"], str(run / "recap.md"))
            self.assertEqual(artifacts["worktree"], str(run / "wt"))

    def test_installed_console_script_is_a_live_duet_process(self) -> None:
        command = "/tmp/venv/bin/python /tmp/venv/bin/duet --status run --json"
        with mock.patch.object(duet, "_pid_alive", return_value=True), \
                mock.patch.object(duet, "_proc_cmdline", return_value=command):
            self.assertTrue(duet._is_duet_process(1234))

    def test_live_pid_is_accepted_when_sandbox_hides_command_line(self) -> None:
        with mock.patch.object(duet, "_pid_alive", return_value=True), \
                mock.patch.object(duet, "_proc_cmdline", return_value=None):
            self.assertTrue(duet._is_duet_process(1234))
            self.assertFalse(duet._is_duet_process(1))

    def test_unrelated_python_process_is_not_a_live_duet_process(self) -> None:
        command = "/tmp/venv/bin/python /tmp/worker.py duet"
        with mock.patch.object(duet, "_pid_alive", return_value=True), \
                mock.patch.object(duet, "_proc_cmdline", return_value=command):
            self.assertFalse(duet._is_duet_process(1234))

    def test_saved_live_phase_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            run = self._write_state(root, {
                "phase": "kickoff_pending",
                "turns_used": 0,
                "finished_reason": None,
                "history": [],
                "duet_pid": 123,
            })
            with mock.patch.object(duet, "_is_duet_process", return_value=True):
                snapshot = duet.build_run_status(str(run))
            self.assertEqual(snapshot["health"], "running")
            self.assertEqual(snapshot["phase"], "kickoff_pending")
            self.assertEqual(snapshot["exit_code"], 1)

    def test_malformed_state_is_status_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run = pathlib.Path(raw) / "20260719-120000"
            run.mkdir()
            (run / "state.json").write_text("{bad", encoding="utf-8")
            snapshot = duet.build_run_status(str(run))
            self.assertEqual(snapshot["health"], "error")
            self.assertEqual(snapshot["exit_code"], 3)
            self.assertIn("JSONDecodeError", snapshot["error"])


class TestRunScopedStopValidation(unittest.TestCase):
    def _run(self, root: pathlib.Path, **overrides) -> pathlib.Path:
        run = root / "20260719-120000"
        run.mkdir()
        state = {
            "phase": "turn_running",
            "finished_reason": None,
            "duet_pid": 123,
            "duet_process_start": "start-123",
        }
        state.update(overrides)
        (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
        return run

    def test_invalid_pid_is_refused_without_signal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run = self._run(pathlib.Path(raw), duet_pid="invalid")
            with mock.patch.object(duet.os, "kill") as kill, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(duet.request_run_stop(str(run)), 2)
            kill.assert_not_called()

    def test_ambiguous_bare_run_id_is_refused_without_signal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            run_id = "20260719-120000"
            roots = [root / "one", root / "two"]
            for candidate_root in roots:
                (candidate_root / run_id).mkdir(parents=True)
            with mock.patch.object(
                    duet, "_default_list_paths", return_value=roots), \
                    mock.patch.object(duet.os, "kill") as kill, \
                    contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(duet.request_run_stop(run_id), 2)
            self.assertIn("ambiguous", stderr.getvalue())
            self.assertIn("explicit run directory path", stderr.getvalue())
            kill.assert_not_called()

    def test_stale_pid_is_refused_without_signal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run = self._run(pathlib.Path(raw))
            with mock.patch.object(duet, "_pid_alive", return_value=False), \
                    mock.patch.object(duet.os, "kill") as kill, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(duet.request_run_stop(str(run)), 2)
            kill.assert_not_called()

    def test_terminal_run_is_refused_without_signal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run = self._run(
                pathlib.Path(raw), phase="finished", finished_reason="max_turns"
            )
            with mock.patch.object(duet.os, "kill") as kill, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(duet.request_run_stop(str(run)), 2)
            kill.assert_not_called()

    def test_pid_one_is_refused_without_signal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run = self._run(pathlib.Path(raw), duet_pid=1)
            with mock.patch.object(duet.os, "kill") as kill, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(duet.request_run_stop(str(run)), 2)
            kill.assert_not_called()

    def test_reused_pid_is_refused_without_signal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run = self._run(pathlib.Path(raw))
            with mock.patch.object(duet, "_pid_alive", return_value=True), \
                    mock.patch.object(
                        duet, "_proc_start_identity", return_value="new-start"
                    ), \
                    mock.patch.object(duet.os, "kill") as kill, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(duet.request_run_stop(str(run)), 2)
            kill.assert_not_called()

    def test_foreign_process_is_refused_without_signal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run = self._run(pathlib.Path(raw))
            with mock.patch.object(duet, "_pid_alive", return_value=True), \
                    mock.patch.object(
                        duet, "_proc_start_identity", return_value="start-123"
                    ), \
                    mock.patch.object(
                        duet, "_proc_cmdline", return_value="python worker.py"
                    ), \
                    mock.patch.object(duet.os, "kill") as kill, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(duet.request_run_stop(str(run)), 2)
            kill.assert_not_called()

    def test_missing_start_identity_is_refused_without_signal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run = self._run(pathlib.Path(raw), duet_process_start=None)
            with mock.patch.object(duet, "_pid_alive", return_value=True), \
                    mock.patch.object(duet.os, "kill") as kill, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(duet.request_run_stop(str(run)), 2)
            kill.assert_not_called()

    def test_graceful_and_immediate_signal_only_exact_supervisor_pid(self) -> None:
        for immediate, expected_signal in (
                (False, duet.STOP_GRACEFUL_SIGNAL),
                (True, duet.signal.SIGTERM)):
            with self.subTest(immediate=immediate), \
                    tempfile.TemporaryDirectory() as raw:
                run = self._run(pathlib.Path(raw))
                with mock.patch.object(duet, "_pid_alive", return_value=True), \
                        mock.patch.object(
                            duet, "_proc_start_identity", return_value="start-123"
                        ), \
                        mock.patch.object(duet, "_proc_cmdline", return_value=None), \
                        mock.patch.object(duet.os, "kill") as kill, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        duet.request_run_stop(str(run), immediate=immediate), 0
                    )
                kill.assert_called_once_with(123, expected_signal)

    def test_repeated_run_stop_signal_does_not_hard_exit(self) -> None:
        if duet.STOP_GRACEFUL_SIGNAL is None:
            self.skipTest("platform has no run-scoped graceful stop signal")
        stop = duet.StopFlag()
        with mock.patch.object(duet.signal, "signal") as install, \
                mock.patch.object(duet.os, "_exit") as hard_exit, \
                contextlib.redirect_stderr(io.StringIO()):
            duet._install_run_stop_signal(stop)
            handler = install.call_args.args[1]
            handler(duet.STOP_GRACEFUL_SIGNAL, None)
            handler(duet.STOP_GRACEFUL_SIGNAL, None)
        self.assertTrue(stop.requested)
        self.assertEqual(stop.reason, "run_stop")
        hard_exit.assert_not_called()

    def test_force_prompt_records_stop_after_tty_input_unblocks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            cfg = duet.DuetConfig(cwd=root, agents=_agents(), task="x")
            stop = duet.StopFlag()

            def unblock_prompt(_prompt: str) -> str:
                stop.request("SIGTERM")
                return ""

            with mock.patch.object(duet.sys.stdin, "isatty", return_value=True), \
                    mock.patch("builtins.input", side_effect=unblock_prompt), \
                    contextlib.redirect_stdout(io.StringIO()):
                reason, _ = duet.ask_force(
                    cfg,
                    [],
                    root / "transcript.md",
                    root / "state.json",
                    "last message",
                    1,
                    {agent.name: False for agent in cfg.agents},
                    "max_turns",
                    stop=stop,
                )

            self.assertEqual(reason, "force_stop")


class TestContinueWorktreeOverrides(unittest.TestCase):
    def _write_prior_run(
        self,
        root: pathlib.Path,
        *,
        require_worktree: object = None,
        worktree: pathlib.Path | None = None,
    ) -> pathlib.Path:
        run = root / "prior" / "20260719-120000"
        run.mkdir(parents=True)
        state = {
            "cwd": str(root),
            "task": "prior task",
            "agents": [duet.agent_state(agent) for agent in _agents()],
            "history": [],
            "turns_used": 0,
            "finished_reason": "max_turns",
            "worktree": str(worktree) if worktree is not None else None,
            "worktree_for": "partner",
        }
        if require_worktree is not None:
            state["require_worktree"] = require_worktree
        (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
        return run

    def _build_cfg(self, run: pathlib.Path, *extra: str) -> duet.DuetConfig:
        parser = duet._build_arg_parser()
        args = parser.parse_args(["--continue", str(run), *extra])
        return duet.build_continue_config(str(run), args, parser, {})

    def test_explicit_strict_override_fails_closed_for_older_non_strict_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            missing = root / "missing-worktree"
            run = self._write_prior_run(root)
            cfg = self._build_cfg(
                run,
                "--require-worktree",
                "--worktree-path", str(missing),
                "--runs-dir", str(root / "new-runs"),
            )

            self.assertTrue(cfg.require_worktree)
            with mock.patch.object(duet, "_register_run_in_home_index"), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                state = duet.run_duet(cfg)
            self.assertEqual(state["finished_reason"], "setup_error")
            self.assertIsNone(state["worktree"])

    def test_explicit_fallback_override_wins_over_saved_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            missing = root / "missing-worktree"
            run = self._write_prior_run(
                root, require_worktree=True, worktree=missing
            )
            cfg = self._build_cfg(run, "--allow-worktree-fallback")

            self.assertFalse(cfg.require_worktree)
            with contextlib.redirect_stderr(io.StringIO()):
                result = duet._setup_run_worktree(cfg, "new-run", root)
            self.assertEqual(result, (None, None))


class TestContinueTimeoutKnobs(unittest.TestCase):
    def _write_prior_run(self, root: pathlib.Path, state_extra: dict) -> pathlib.Path:
        run = root / "prior" / "20260719-120000"
        run.mkdir(parents=True)
        state = {
            "cwd": str(root),
            "task": "prior task",
            "agents": [duet.agent_state(agent) for agent in _agents()],
            "history": [],
            "turns_used": 0,
            "finished_reason": "max_turns",
            **state_extra,
        }
        (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
        return run

    def _build_cfg(self, run: pathlib.Path, *extra: str) -> duet.DuetConfig:
        parser = duet._build_arg_parser()
        args = parser.parse_args(["--continue", str(run), *extra])
        with contextlib.redirect_stderr(io.StringIO()):
            return duet.build_continue_config(str(run), args, parser, {})

    def test_on_turn_timeout_round_trips_through_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            run = self._write_prior_run(root, {"on_turn_timeout": "continue"})
            self.assertEqual(self._build_cfg(run).on_turn_timeout, "continue")

    def test_explicit_stop_overrides_restored_continue(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            run = self._write_prior_run(root, {"on_turn_timeout": "continue"})
            cfg = self._build_cfg(run, "--on-turn-timeout", "stop")
            self.assertEqual(cfg.on_turn_timeout, "stop")

    def test_legacy_state_defaults_to_stop(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            run = self._write_prior_run(root, {})
            self.assertEqual(self._build_cfg(run).on_turn_timeout, "stop")

    def test_legacy_state_derives_timeout_from_restored_reasoning(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            run = self._write_prior_run(root, {"reasoning": "max"})
            self.assertEqual(self._build_cfg(run).per_turn_timeout, 1800)

    def test_saved_timeout_beats_derivation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            run = self._write_prior_run(
                root, {"reasoning": "max", "per_turn_timeout": 900}
            )
            self.assertEqual(self._build_cfg(run).per_turn_timeout, 900)

    def test_explicit_timeout_beats_everything(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            run = self._write_prior_run(
                root, {"reasoning": "max", "per_turn_timeout": 900}
            )
            cfg = self._build_cfg(run, "--timeout", "700")
            self.assertEqual(cfg.per_turn_timeout, 700)


class TestTimeoutContinueLoop(unittest.TestCase):
    """Loop semantics for --on-turn-timeout, with call_agent mocked out."""

    def _cfg(self, root: pathlib.Path, **kwargs) -> duet.DuetConfig:
        defaults = dict(
            cwd=root,
            agents=_agents(),
            task="probe the timeout flow",
            runs_dir=root / "runs",
            max_turns=3,
            on_turn_timeout="continue",
        )
        defaults.update(kwargs)
        return duet.DuetConfig(**defaults)

    def _run(self, cfg: duet.DuetConfig, side_effect):
        calls: list[dict] = []

        def fake_call_agent(agent, message, cfg_, first_turn_for_agent,
                            *, run_dir=None, turn_label=None):
            calls.append({
                "agent": agent.name,
                "message": message,
                "first_turn": first_turn_for_agent,
                "run_dir": run_dir,
            })
            return side_effect(len(calls), agent, calls[-1])

        def skip_force(_cfg, _history, _transcript_path, _state_path,
                       _last_msg, _speaker_idx, _seen_first_turn, reason,
                       _wt_path=None, _wt_branch=None, **_kwargs):
            return reason, None

        err = io.StringIO()
        with mock.patch.object(duet, "call_agent", fake_call_agent), \
                mock.patch.object(duet, "_register_run_in_home_index"), \
                mock.patch.object(duet, "ask_force", skip_force), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            state = duet.run_duet(cfg)
        return state, calls, err.getvalue()

    @staticmethod
    def _ok_reply() -> str:
        return "RECAP: ok\nFILES: none\nSTATUS: reviewing\n\nlooks fine"

    def test_timeout_continue_hands_block_to_partner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            persisted_states: list[dict] = []
            real_write_run_state = duet._write_run_state

            def capture_write(state_path, state):
                persisted_states.append(json.loads(json.dumps(state)))
                real_write_run_state(state_path, state)

            def side_effect(n, agent, call):
                if n == 1:
                    raise duet.AgentRunError(
                        duet.FINISHED_TIMEOUT,
                        "codex exited 124\nslow turn\n" + "x" * 100_000,
                    )
                return self._ok_reply()

            with mock.patch.object(duet, "_write_run_state", capture_write):
                state, calls, err = self._run(self._cfg(root), side_effect)

        self.assertEqual(state["finished_reason"], "max_turns")
        self.assertEqual(len(calls), 3)
        self.assertEqual(state["history"][0]["finished_reason"], "timeout")
        # state.json keeps a bounded excerpt, never the raw 100KB stderr dump.
        self.assertLessEqual(
            len(state["history"][0]["error"]),
            duet.AGENT_ERROR_TRANSCRIPT_MAX_CHARS,
        )
        # Handoff contract: the partner's message is the turn-1 failure block,
        # not a bare "previous turn failed" note.
        self.assertIn("[duet] TIMEOUT: turn 01", calls[1]["message"])
        self.assertIn("codex exited 124", calls[1]["message"])
        # Inspect the actual write after turn 1 and before turn 2 starts. A
        # later turn_running write could otherwise hide a terminal timeout.
        after_timeout = [
            item for item in persisted_states
            if item.get("turns_used") == 1
            and item.get("phase") == "between_turns"
            and len(item.get("history", [])) == 1
            and item["history"][0].get("finished_reason") == "timeout"
        ]
        self.assertEqual(len(after_timeout), 1)
        self.assertIsNone(after_timeout[0]["finished_reason"])
        self.assertNotEqual(after_timeout[0]["phase"], "finished")
        self.assertIn("handing the timeout block", err)

    def test_consecutive_timeouts_stop_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()

            def side_effect(n, agent, call):
                raise duet.AgentRunError(duet.FINISHED_TIMEOUT, "slow")

            state, calls, _ = self._run(self._cfg(root), side_effect)

        self.assertEqual(state["finished_reason"], "timeout")
        self.assertEqual(state["phase"], "finished")
        self.assertEqual(len(calls), 2)

    def test_default_stop_keeps_timeout_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            cfg = self._cfg(root, on_turn_timeout="stop")

            def side_effect(n, agent, call):
                raise duet.AgentRunError(duet.FINISHED_TIMEOUT, "slow")

            state, calls, err = self._run(cfg, side_effect)

        self.assertEqual(duet.DuetConfig(cwd=root, agents=_agents()).on_turn_timeout,
                         "stop")
        self.assertEqual(state["finished_reason"], "timeout")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("handing the timeout block", err)

    def test_final_turn_timeout_is_terminal_even_in_continue_mode(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            cfg = self._cfg(root, max_turns=1)

            def side_effect(n, agent, call):
                raise duet.AgentRunError(duet.FINISHED_TIMEOUT, "slow")

            state, calls, _ = self._run(cfg, side_effect)

        self.assertEqual(state["finished_reason"], "timeout")
        self.assertEqual(len(calls), 1)

    def test_sigint_during_timed_out_call_beats_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            holder: dict = {}
            orig_prepare = duet._prepare_run

            def capture(cfg):
                context, startup = orig_prepare(cfg)
                holder["stop"] = context.stop
                return context, startup

            def side_effect(n, agent, call):
                holder["stop"].requested = True
                raise duet.AgentRunError(duet.FINISHED_TIMEOUT, "slow")

            with mock.patch.object(duet, "_prepare_run", capture):
                state, calls, err = self._run(self._cfg(root), side_effect)

        self.assertEqual(state["finished_reason"], "timeout")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("handing the timeout block", err)

    def test_same_cwd_codex_retry_counts_as_first_turn(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            agents = [
                duet.Agent(name="codex-a", backend="codex", role="planner"),
                duet.Agent(name="codex-b", backend="codex", role="coder"),
            ]
            cfg = self._cfg(root, agents=agents, max_turns=4)

            def side_effect(n, agent, call):
                if n == 1:
                    raise duet.AgentRunError(duet.FINISHED_TIMEOUT, "slow")
                # Both first successful turns pin independent sessions. This
                # proves the timed-out coder retry can pass the after-call
                # same-cwd guard when it produces a real UUID.
                if n == 2:
                    agent.session_id = "11111111-1111-4111-8111-111111111111"
                elif n == 3:
                    agent.session_id = "22222222-2222-4222-8222-222222222222"
                return self._ok_reply()

            state, calls, _ = self._run(cfg, side_effect)

        self.assertEqual([c["agent"] for c in calls],
                         ["codex-b", "codex-a", "codex-b", "codex-a"])
        # The timed-out first turn didn't mark codex-b seen, so its retry runs
        # under first-turn rules and the same-cwd guards keep applying.
        self.assertTrue(calls[2]["first_turn"])
        self.assertFalse(calls[3]["first_turn"])
        self.assertEqual(
            agents[1].session_id, "22222222-2222-4222-8222-222222222222"
        )

    def test_same_cwd_codex_retry_without_uuid_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            agents = [
                duet.Agent(name="codex-a", backend="codex", role="planner"),
                duet.Agent(name="codex-b", backend="codex", role="coder"),
            ]
            cfg = self._cfg(root, agents=agents, max_turns=4)

            def side_effect(n, agent, call):
                if n == 1:
                    raise duet.AgentRunError(duet.FINISHED_TIMEOUT, "slow")
                if n == 3:
                    # The retry "succeeds" but pins only the cwd-keyed legacy
                    # marker — the after-guard must re-validate it because the
                    # retry still counts as a first turn.
                    agent.session_id = "codex-current"
                return self._ok_reply()

            with self.assertRaises(SystemExit):
                self._run(cfg, side_effect)

    def test_worktree_timeout_handoff_includes_diff_block(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]
            subprocess.run([*git[:1], "init", "-q"], cwd=root, check=True)
            (root / "seed.txt").write_text("seed\n", encoding="utf-8")
            subprocess.run([*git[:1], "add", "seed.txt"], cwd=root, check=True)
            subprocess.run([*git, "commit", "-qm", "seed"], cwd=root, check=True)
            cfg = self._cfg(root, max_turns=2, worktree=True,
                            require_worktree=True)

            def side_effect(n, agent, call):
                if n == 1:
                    raise duet.AgentRunError(duet.FINISHED_TIMEOUT, "slow")
                return self._ok_reply()

            state, calls, _ = self._run(cfg, side_effect)

        self.assertEqual(state["finished_reason"], "max_turns")
        # The reviewer received the failure block plus the worktree handoff,
        # so it can review the on-disk work the coder never got to summarize.
        self.assertIn("[duet] TIMEOUT: turn 01", calls[1]["message"])
        self.assertIn("#### worktree changes", calls[1]["message"])


class TestToolDenialStatus(unittest.TestCase):
    def _status(self, root: pathlib.Path, history: list) -> dict:
        run = root / "20260719-120000"
        run.mkdir()
        (run / "state.json").write_text(json.dumps({
            "phase": "finished", "turns_used": len(history),
            "finished_reason": "converged", "history": history,
        }), encoding="utf-8")
        return duet.build_run_status(str(run))

    def test_turn_values_are_coerced_to_a_total_or_null(self) -> None:
        for value, expected in (({"Bash": 2, "Read": 1}, 3), ({"Bash": "2"}, None)):
            with self.subTest(value=value):
                turn = duet._status_history_turn(
                    {"turn": 1, "agent": "a", "tool_denials": value})
                self.assertEqual(turn["tool_denials"], expected)
        self.assertIsNone(duet._status_history_turn({"turn": 1})["tool_denials"])

    def test_top_level_total_counts_only_recorded_turns(self) -> None:
        secret = "tool-input-secret"
        with tempfile.TemporaryDirectory() as raw:
            snapshot = self._status(pathlib.Path(raw), [
                {"turn": 1, "agent": "codex-coder"},
                {"turn": 2, "agent": "claude-reviewer",
                 "tool_denials": {"Bash": 2, secret: "bad"}},
                {"turn": 3, "agent": "claude-reviewer", "tool_denials": {"Bash": 1}},
            ])
            human = io.StringIO()
            with contextlib.redirect_stdout(human):
                duet._print_human_status(snapshot, "run")
        self.assertEqual(snapshot["tool_denials"], 1)
        self.assertEqual(snapshot["last_completed_turn"]["tool_denials"], 1)
        self.assertNotIn(secret, json.dumps(snapshot))
        self.assertIn("tool_denials:    1", human.getvalue())

    def test_counts_above_the_bound_are_unknown_through_the_cli(self) -> None:
        # 4,300 digits is the most json.loads accepts on Python 3.11+; the sum
        # of two such counts exceeds the integer-to-text limit.
        huge = 9 * 10 ** 4299
        with tempfile.TemporaryDirectory() as raw:
            run = pathlib.Path(raw).resolve() / "20260719-120000"
            run.mkdir()
            (run / "state.json").write_text(json.dumps({
                "phase": "finished", "turns_used": 2, "finished_reason": "max_turns",
                "finding_reports": True, "agents": [{"name": "a"}, {"name": "b"}],
                "history": [
                    {"turn": 1, "agent": "a", "tool_denials": {"Bash": huge, "Read": huge}},
                    {"turn": 2, "agent": "b",
                     "tool_denials": {"Bash": duet.TOOL_DENIAL_MAX_COUNT + 1}},
                ],
            }), encoding="utf-8")
            script = str(pathlib.Path(__file__).resolve().parent.parent / "duet.py")
            env = dict(duet.os.environ, DUET_METRICS="0", HOME=raw)
            status = subprocess.run(
                [duet.sys.executable, script, "--status", str(run), "--json"],
                capture_output=True, text=True, env=env)
            report = subprocess.run(
                [duet.sys.executable, script, "--report", str(run)],
                capture_output=True, text=True, env=env)
        self.assertEqual(status.returncode, 0, status.stderr)
        snapshot = json.loads(status.stdout)
        self.assertIsNone(snapshot["tool_denials"])
        self.assertIsNone(snapshot["last_completed_turn"]["tool_denials"])
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertNotIn("Execution limits", report.stdout)

    def test_top_level_total_is_null_when_no_turn_recorded_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            snapshot = self._status(pathlib.Path(raw), [{"turn": 1, "agent": "a"}])
            human = io.StringIO()
            with contextlib.redirect_stdout(human):
                duet._print_human_status(snapshot, "run")
        self.assertIsNone(snapshot["tool_denials"])
        self.assertNotIn("tool_denials", human.getvalue())


class TestToolDenialLoop(unittest.TestCase):
    """Denials reach history, transcript, and the partner without changing convergence."""

    PROPOSAL = "LGTM rationale: checked the diff by reading it.\n<<<LGTM>>>"

    def _run(self, root: pathlib.Path, replies: list, *, dry_run: bool = False):
        calls: list[dict] = []

        def fake_call_agent(agent, message, cfg_, first_turn_for_agent,
                            *, run_dir=None, turn_label=None):
            text, denials = replies[len(calls)]
            calls.append({"agent": agent.name, "message": message})
            agent.last_tool_denials = denials
            return text

        cfg = duet.DuetConfig(cwd=root, agents=_agents(), task="review",
                              runs_dir=root / "runs", max_turns=len(replies),
                              dry_run=dry_run, metrics_enabled=False)
        out = io.StringIO()
        with mock.patch.object(duet, "call_agent", fake_call_agent), \
                mock.patch.object(duet, "_register_run_in_home_index"), \
                mock.patch.object(duet.sys.stdin, "isatty", return_value=False), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            state = duet.run_duet(cfg)
        return state, calls, out.getvalue()

    def test_denials_are_recorded_and_handed_to_partner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            state, calls, out = self._run(root, [
                ("implemented the change", None),
                (self.PROPOSAL, {"Bash": 2}),
                (self.PROPOSAL, None),
            ])
            transcript = pathlib.Path(state["transcript_path"]).read_text()

        self.assertEqual(state["finished_reason"], duet.FINISHED_CONVERGED)
        self.assertNotIn("tool_denials", state["history"][0])
        self.assertEqual(state["history"][1]["tool_denials"], {"Bash": 2})
        note = "[duet] note: claude-reviewer was denied 2 tool call(s) this turn (Bash×2)."
        self.assertIn(note, calls[2]["message"])
        self.assertIn(note, transcript)
        self.assertIn(duet.CONVERGED_RUN_NOTE, out)
        self.assertIn("[duet] note: agents were denied 2 tool call(s) in this run.", out)
        self.assertLess(out.index("[duet] done. reason=converged"),
                        out.index(duet.CONVERGED_RUN_NOTE))

    def test_known_empty_denials_are_recorded_without_a_note(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            state, calls, out = self._run(root, [("done", None), ("checked", {})])
        self.assertEqual(state["history"][1]["tool_denials"], {})
        self.assertNotIn("was denied", out)

    def test_unconverged_finish_prints_only_the_denial_total(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            state, _, out = self._run(root, [("done", None), ("checked", {"Bash": 2})])
        self.assertEqual(state["finished_reason"], duet.FINISHED_MAX_TURNS)
        self.assertIn("[duet] note: agents were denied 2 tool call(s) in this run.", out)
        self.assertNotIn(duet.CONVERGED_RUN_NOTE, out)

    def test_converged_after_force_prints_the_converged_note(self) -> None:
        cfg = duet.DuetConfig(cwd=pathlib.Path("."), agents=_agents(), task="review",
                              metrics_enabled=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            duet._print_run_end_notes(cfg, duet.FINISHED_CONVERGED_AFTER_FORCE, [])
        self.assertEqual(out.getvalue(), duet.CONVERGED_RUN_NOTE + "\n")

    def test_dry_run_prints_no_end_notes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            state, _, out = self._run(
                root, [(self.PROPOSAL, {"Bash": 1}), (self.PROPOSAL, None)],
                dry_run=True)
        self.assertEqual(state["finished_reason"], duet.FINISHED_CONVERGED)
        self.assertNotIn(duet.CONVERGED_RUN_NOTE, out)
        self.assertNotIn("in this run", out)

    def test_forced_turn_records_denials(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            transcript = root / "transcript.md"
            transcript.write_text("", encoding="utf-8")
            agents = _agents()
            cfg = duet.DuetConfig(cwd=root, agents=agents, task="review",
                                  runs_dir=root, metrics_enabled=False)

            def fake_call_agent(agent, *args, **kwargs):
                agent.last_tool_denials = {"Read": 1}
                return "forced reply"

            history: list = []
            with mock.patch.object(duet, "call_agent", fake_call_agent), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = duet._run_forced_turn(
                    cfg, forced_turn=1, next_speaker=agents[0], forced_msg="fix",
                    first_turn_for_agent=False, transcript_path=transcript,
                    wt_path=None, wt_branch=None, history=history,
                    seen_first_turn={a.name: True for a in agents},
                    last_verify_state=None)
            saved = transcript.read_text()
        self.assertEqual(history[0]["tool_denials"], {"Read": 1})
        self.assertIn("was denied 1 tool call(s) this turn (Read×1)", result.reply)
        self.assertIn("was denied 1 tool call(s)", saved)


class TestRevisionBoundConvergence(unittest.TestCase):
    PROPOSAL = "LGTM rationale: checked the candidate by reading it.\n<<<LGTM>>>"

    def setUp(self) -> None:
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        self.root = pathlib.Path(fixture.name).resolve()
        self.candidate = self.root / "candidate.py"
        self.candidate.write_text("first\n", encoding="utf-8")

    def _run(self, replies, *, max_turns, edits=None, inputs=None, verify_cmd=None):
        cfg = duet.DuetConfig(
            cwd=self.root, agents=_agents(), task="review", runs_dir=self.root / "runs",
            max_turns=max_turns, metrics_enabled=False, verify_cmd=verify_cmd,
        )
        scripted_replies = iter(replies)

        def reply_to_turn(_agent, _message, _cfg, first_turn_for_agent, *,
                          run_dir=None, turn_label=None):
            turn = int(turn_label.split("-", 1)[0])
            if edits and turn in edits:
                path, content = edits[turn]
                path.write_text(content, encoding="utf-8")
            return next(scripted_replies)

        with mock.patch.object(duet, "call_agent", reply_to_turn), \
                mock.patch.object(duet, "_register_run_in_home_index"), \
                mock.patch.object(duet.sys.stdin, "isatty", return_value=inputs is not None), \
                mock.patch("builtins.input", side_effect=inputs), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return duet.run_duet(cfg)

    def test_untracked_change_requires_fresh_approvals(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        (self.root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        edits = {2: (self.candidate, "second\n"), 3: (self.root / "ignored.txt", "ignored\n")}

        state = self._run([self.PROPOSAL] * 3, max_turns=3, edits=edits)

        self.assertEqual(state["finished_reason"], duet.FINISHED_CONVERGED)
        self.assertEqual(state["turns_used"], 3)
        before, first, second = state["history"]
        self.assertNotEqual(before["approval_revision"], first["approval_revision"])
        self.assertEqual(first["approval_revision"], second["approval_revision"])
        self.assertNotEqual(first["agent"], second["agent"])

    def test_replacing_tracked_file_with_directory_respects_git_ignore(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        thing = self.root / "thing"
        thing.write_text("tracked file\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("thing/cache.log\n", encoding="utf-8")
        subprocess.run(["git", "add", "thing", ".gitignore"], cwd=self.root, check=True)
        thing.unlink()
        thing.mkdir()
        cache = thing / "cache.log"
        cache.write_text("initial cache\n", encoding="utf-8")
        cfg = duet.DuetConfig(
            cwd=self.root, agents=_agents(), task="review", runs_dir=self.root / "runs",
            metrics_enabled=False,
        )

        def revision():
            digest = duet._approval_revision(cfg, cfg.runs_dir, None).digest
            self.assertRegex(digest or "", r"^[0-9a-f]{64}$")
            return digest

        with self.subTest(selected_child=False):
            before = revision()
            cache.write_text("new cache\n", encoding="utf-8")
            self.assertEqual(revision(), before)

        selected = thing / "keep.py"
        selected.write_text("selected file\n", encoding="utf-8")
        with self.subTest(selected_child=True):
            before = revision()
            cache.write_text("updated cache\n", encoding="utf-8")
            self.assertEqual(revision(), before)
            selected.write_text("edited selected file\n", encoding="utf-8")
            self.assertNotEqual(revision(), before)

    def _revision_digest(self, cwd: pathlib.Path) -> str:
        cfg = duet.DuetConfig(
            cwd=cwd, agents=_agents(), task="review", runs_dir=self.root / "runs",
            metrics_enabled=False,
        )
        digest = duet._approval_revision(cfg, cfg.runs_dir, None).digest
        self.assertRegex(digest or "", r"^[0-9a-f]{64}$")
        return digest

    def test_candidate_inside_ignored_directory_detects_edits(self) -> None:
        def repository(name: str, ignore: str) -> pathlib.Path:
            repo = self.root / name
            (repo / "sub").mkdir(parents=True)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / ".gitignore").write_text(ignore, encoding="utf-8")
            return repo

        with self.subTest(tracked=False):
            candidate = repository("plain", "sub/\n") / "sub"
            (candidate / "a.py").write_text("first\n", encoding="utf-8")
            before = self._revision_digest(candidate)
            (candidate / "a.py").write_text("second\n", encoding="utf-8")
            self.assertNotEqual(self._revision_digest(candidate), before)

        with self.subTest(tracked=True):
            repo = repository("forced", "sub/\n")
            candidate = repo / "sub"
            (candidate / "a.py").write_text("first\n", encoding="utf-8")
            subprocess.run(["git", "add", "-f", "sub/a.py"], cwd=repo, check=True)
            before = self._revision_digest(candidate)
            (candidate / "b.py").write_text("untracked\n", encoding="utf-8")
            added = self._revision_digest(candidate)
            self.assertNotEqual(added, before)
            (candidate / "b.py").write_text("edited\n", encoding="utf-8")
            self.assertNotEqual(self._revision_digest(candidate), added)

        with self.subTest(everything_ignored=True):
            candidate = repository("star", "*\n") / "work" / "proj"
            candidate.mkdir(parents=True)
            (candidate / "a.py").write_text("first\n", encoding="utf-8")
            before = self._revision_digest(candidate)
            (candidate / "a.py").write_text("second\n", encoding="utf-8")
            self.assertNotEqual(self._revision_digest(candidate), before)

    def _transcript(self, state: dict) -> str:
        return pathlib.Path(state["transcript_path"]).read_text(encoding="utf-8")

    def test_unhashable_entry_note_names_path_and_reason(self) -> None:
        os.mkfifo(self.root / "pipe")

        state = self._run([self.PROPOSAL] * 2, max_turns=2)

        self.assertEqual(state["finished_reason"], duet.FINISHED_MAX_TURNS)
        self.assertNotIn("approval_revision", state["history"][0])
        self.assertIn("pipe: unsupported candidate file type", self._transcript(state))

    def test_unreadable_directory_note_names_path_and_remedy(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root can read any directory")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        locked = self.root / "locked"
        locked.mkdir()
        (locked / "secret.txt").write_text("hidden\n", encoding="utf-8")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)

        with self.subTest(ignored=False):
            state = self._run([self.PROPOSAL] * 2, max_turns=2)

            self.assertEqual(state["finished_reason"], duet.FINISHED_MAX_TURNS)
            transcript = self._transcript(state)
            self.assertIn("locked/", transcript)
            self.assertIn("Permission denied", transcript)
            self.assertIn("add it to .gitignore", transcript)

        with self.subTest(ignored=True):
            (self.root / ".gitignore").write_text("locked/\n", encoding="utf-8")
            self._revision_digest(self.root)

    def test_problem_text_survives_non_utf8_paths_and_is_bounded(self) -> None:
        text = duet._revision_problem(OSError("unsupported candidate file type"), b"caf\xe9\xff")
        text.encode("utf-8")
        self.assertIn("unsupported candidate file type", text)
        long_reason = duet._revision_problem(RuntimeError("x" * 5000), b"dir")
        self.assertLessEqual(len(long_reason), duet.ERROR_SUMMARY_MAX_CHARS)

    def test_edit_to_an_already_read_file_invalidates_the_approval(self) -> None:
        (self.root / "second.py").write_text("second file\n", encoding="utf-8")
        edit_pending = False
        real_read = duet._revision_file

        def replies():
            nonlocal edit_pending
            yield self.PROPOSAL
            edit_pending = True
            yield self.PROPOSAL

        def read_file(parent_fd, name, expected):
            nonlocal edit_pending
            if edit_pending and name == b"second.py":
                edit_pending = False
                self.candidate.write_text("changed during the snapshot\n", encoding="utf-8")
            return real_read(parent_fd, name, expected)

        with mock.patch.object(duet, "_revision_file", side_effect=read_file):
            state = self._run(replies(), max_turns=2)

        self.assertEqual(self.candidate.read_text(encoding="utf-8"), "changed during the snapshot\n")
        self.assertEqual(state["finished_reason"], duet.FINISHED_MAX_TURNS)
        self.assertIn("approval_revision", state["history"][0])
        self.assertNotIn("approval_revision", state["history"][1])
        self.assertIn("candidate.py: changed while Duet read the candidate",
                      self._transcript(state))

    def test_submodule_ignores_cache_writes_and_detects_tracked_edits(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            source = pathlib.Path(raw).resolve()
            subprocess.run(["git", "init", "-q"], cwd=source, check=True)
            (source / ".gitignore").write_text("cache.log\n", encoding="utf-8")
            (source / "app.py").write_text("first\n", encoding="utf-8")
            subprocess.run(["git", "add", ".gitignore", "app.py"], cwd=source, check=True)
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                 "commit", "-qm", "seed"], cwd=source, check=True,
            )
            subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
            subprocess.run(
                ["git", "-c", "protocol.file.allow=always",
                 "submodule", "add", "--quiet", "--", str(source), "nested"],
                cwd=self.root, check=True,
            )
            cache = self.root / "nested" / "cache.log"
            cache.write_text("before\n", encoding="utf-8")
            tracked = self.root / "nested" / "app.py"

            state = self._run(
                [self.PROPOSAL] * 3, max_turns=3, edits={2: (tracked, "second\n")},
                verify_cmd="printf 'verify\\n' >> nested/cache.log",
            )

        self.assertEqual(cache.read_text(encoding="utf-8"), "before\n" + "verify\n" * 3)
        self.assertEqual(state["finished_reason"], duet.FINISHED_CONVERGED)
        before, first, second = state["history"]
        self.assertNotEqual(before["approval_revision"], first["approval_revision"])
        self.assertEqual(first["approval_revision"], second["approval_revision"])

    def test_non_repository_git_diagnostics_allow_filesystem_fallback(self) -> None:
        cfg = duet.DuetConfig(
            cwd=self.root, agents=_agents(), task="review", runs_dir=self.root / "runs",
            metrics_enabled=False,
        )
        command = ["git", "rev-parse", "--is-inside-work-tree", "--absolute-git-dir"]
        warning = (
            b"git: warning: confstr() failed with code 5: "
            b"couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead\n"
        )
        no_repo = b"fatal: not a git repository (or any of the parent directories): .git\n"
        mount_boundary = (
            b"fatal: not a git repository (or any parent up to mount point /)\n"
            b"Stopping at filesystem boundary (GIT_DISCOVERY_ACROSS_FILESYSTEM not set).\n"
        )
        with mock.patch.object(duet.subprocess, "run") as probe:
            for stderr in (warning + no_repo, mount_boundary):
                with self.subTest(stderr=stderr):
                    probe.return_value = subprocess.CompletedProcess(command, 128, b"", stderr)
                    revision = duet._approval_revision(cfg, cfg.runs_dir, None)
                    self.assertRegex(revision.digest or "", r"^[0-9a-f]{64}$")

            fatal = b"fatal: detected dubious ownership in repository\n"
            probe.return_value = subprocess.CompletedProcess(command, 128, b"", warning + fatal)
            failed = duet._approval_revision(cfg, cfg.runs_dir, None)
            self.assertIsNone(failed.digest)
            self.assertIn("detected dubious ownership", failed.problem)

    def test_first_forced_approval_is_pending_after_candidate_changes(self) -> None:
        state = self._run(
            [self.PROPOSAL] * 3, max_turns=1, edits={2: (self.candidate, "second\n")},
            inputs=["review the update", "confirm the reviewed update", ""],
        )

        self.assertEqual(state["finished_reason"], duet.FINISHED_CONVERGED_AFTER_FORCE)
        self.assertEqual(state["turns_used"], 3)
        automatic, first, second = state["history"]
        self.assertTrue(first["forced"] and second["forced"])
        self.assertNotEqual(automatic["approval_revision"], first["approval_revision"])
        self.assertEqual(first["approval_revision"], second["approval_revision"])
        self.assertNotEqual(first["agent"], second["agent"])

    def test_forced_approval_can_confirm_last_automatic_turn(self) -> None:
        state = self._run(
            [self.PROPOSAL] * 2, max_turns=1, inputs=["review the candidate", ""],
        )

        self.assertEqual(state["finished_reason"], duet.FINISHED_CONVERGED_AFTER_FORCE)
        automatic, forced = state["history"]
        self.assertTrue(forced["forced"])
        self.assertEqual(automatic["approval_revision"], forced["approval_revision"])
        self.assertNotEqual(automatic["agent"], forced["agent"])

    def test_accepting_after_idle_edit_reports_revision_changed(self) -> None:
        for eof in (False, True):
            with self.subTest(eof=eof):
                self.candidate.write_text("first\n", encoding="utf-8")

                def edit_while_waiting(_prompt):
                    self.candidate.write_text("second\n", encoding="utf-8")
                    if eof:
                        raise EOFError
                    return ""

                state = self._run([self.PROPOSAL] * 2, max_turns=2, inputs=edit_while_waiting)

                self.assertEqual(state["finished_reason"], "revision_changed")

    def test_verifier_can_recreate_directory_for_ignored_output(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        package = self.root / "pkg"
        package.mkdir()
        obsolete = package / "obsolete.py"
        obsolete.write_text("obsolete\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("pkg/verifier.cache\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore", "pkg/obsolete.py"], cwd=self.root, check=True)
        obsolete.unlink()
        package.rmdir()

        state = self._run(
            [self.PROPOSAL] * 2, max_turns=2,
            verify_cmd="mkdir -p pkg && printf 'verify\\n' >> pkg/verifier.cache",
        )

        self.assertEqual((package / "verifier.cache").read_text(encoding="utf-8"), "verify\n" * 2)
        first, second = state["history"]
        self.assertTrue(first["verify"]["ok"] and second["verify"]["ok"])
        self.assertEqual(state["finished_reason"], duet.FINISHED_CONVERGED)
        self.assertEqual(first["approval_revision"], second["approval_revision"])

    def test_verifier_cannot_remove_gitlink_directory(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", "160000", "a" * 40, "sub"],
            cwd=self.root, check=True,
        )
        (self.root / "sub").mkdir()

        state = self._run([self.PROPOSAL] * 2, max_turns=2, verify_cmd="rm -rf sub")

        self.assertEqual(state["finished_reason"], duet.FINISHED_MAX_TURNS)
        first, second = state["history"]
        self.assertNotIn("approval_revision", first)
        self.assertRegex(second["approval_revision"], r"^[0-9a-f]{64}$")

    def test_successful_verifier_cannot_approve_its_own_edit(self) -> None:
        state = self._run(
            [self.PROPOSAL] * 3, max_turns=3,
            verify_cmd="printf 'second\\n' > candidate.py",
        )

        self.assertEqual(state["finished_reason"], duet.FINISHED_CONVERGED)
        self.assertEqual(state["turns_used"], 3)
        changed, first, second = state["history"]
        self.assertTrue(changed["verify"]["ok"])
        self.assertNotIn("approval_revision", changed)
        self.assertEqual(first["approval_revision"], second["approval_revision"])


def _envelope(result: str = "Review findings.", denials=None, **extra) -> str:
    payload = {"type": "result", "subtype": "success", "is_error": False,
               "result": result, "session_id": "kickoff-session-id", "num_turns": 3}
    if denials is not None:
        payload["permission_denials"] = denials
    payload.update(extra)
    return json.dumps(payload)


def _denial(name: str, command: str = "ls") -> dict:
    return {"tool_name": name, "tool_use_id": "toolu_1",
            "tool_input": {"command": command}}


class TestReviewKickoffCommand(unittest.TestCase):
    def _cfg(self, *argv: str) -> duet.DuetConfig:
        parser = duet._build_arg_parser()
        args = parser.parse_args(["--recipe", "review", *argv])
        duet._apply_recipe_args(args)
        return duet._build_cfg_from_cli(args, parser, {})

    def _tokens(self, *argv: str) -> list:
        return duet.shlex.split(self._cfg(*argv).task_from_cmd)

    def test_command_requests_json_denials_blocks_and_evidence(self) -> None:
        cfg = self._cfg()
        cmd = duet.shlex.split(cfg.task_from_cmd)
        self.assertEqual(cfg._kickoff_kind, duet.REVIEW_KICKOFF_KIND)
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "json")
        denied = cmd[cmd.index("--disallowedTools") + 1].split(",")
        self.assertEqual(tuple(denied), duet.CLAUDE_DENIED_DELEGATION_TOOLS)
        prompt = cmd[cmd.index("--append-system-prompt") + 1]
        self.assertEqual(prompt, " ".join((
            duet.NO_SUBAGENTS_INSTRUCTION, duet.REVIEW_EVIDENCE_CRITERIA,
            duet.KICKOFF_MISSING_EVIDENCE_SENTENCE)))
        self.assertIn("inputs that should fail", prompt)
        self.assertIn("name the missing check in your findings", prompt)
        self.assertNotIn("ask your partner", prompt)

    def test_requested_permission_mode_is_never_an_edit_mode(self) -> None:
        for mode, expected in (("plan", "plan"), ("default", "default"),
                               ("acceptEdits", "default"),
                               ("bypassPermissions", "default")):
            with self.subTest(mode=mode):
                cmd = self._tokens("--permission-mode", mode)
                self.assertEqual(cmd[cmd.index("--permission-mode") + 1], expected)
        cmd = self._tokens()
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "default")

    def test_permission_mode_from_a_config_file_follows_the_same_rule(self) -> None:
        for mode, expected in (("plan", "plan"), ("bypassPermissions", "default")):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as raw:
                root = pathlib.Path(raw).resolve()
                path = root / "duet.json"
                path.write_text(json.dumps({
                    "cwd": str(root), "permission_mode": mode,
                    "agents": [{"name": "claude-reviewer", "backend": "claude",
                                "role": "reviewer"},
                               {"name": "codex-coder", "backend": "codex",
                                "role": "coder"}],
                }), encoding="utf-8")
                parser = duet._build_arg_parser()
                args = parser.parse_args(["--recipe", "review", "--config", str(path)])
                duet._apply_recipe_args(args)
                cfg = duet._build_cfg_from_yaml(args, parser, {})
                cmd = duet.shlex.split(cfg.task_from_cmd)
                self.assertEqual(cmd[cmd.index("--permission-mode") + 1], expected)
                self.assertEqual(cfg._kickoff_kind, duet.REVIEW_KICKOFF_KIND)

    def test_a_config_file_seed_clears_the_marker(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            path = root / "duet.json"
            path.write_text(json.dumps({
                "cwd": str(root), "task": "from the file",
                "agents": [{"name": "claude-reviewer", "backend": "claude"},
                           {"name": "codex-coder", "backend": "codex"}],
            }), encoding="utf-8")
            parser = duet._build_arg_parser()
            args = parser.parse_args(["--recipe", "review", "--config", str(path)])
            duet._apply_recipe_args(args)
            cfg = duet._build_cfg_from_yaml(args, parser, {})
        self.assertEqual(cfg.task, "from the file")
        self.assertIsNone(cfg.task_from_cmd)
        self.assertIsNone(cfg._kickoff_kind)

    def test_explicit_seeds_never_get_the_recipe_marker(self) -> None:
        for argv in (("--task", "inspect"), ("--kickoff", "go"),
                     ("--task-from-cmd", "printf seed")):
            with self.subTest(argv=argv):
                cfg = self._cfg(*argv)
                self.assertIsNone(cfg._kickoff_kind)
                if argv[0] == "--task-from-cmd":
                    self.assertEqual(cfg.task_from_cmd, "printf seed")
                else:
                    self.assertIsNone(cfg.task_from_cmd)

    def test_marker_never_reaches_state_or_continue(self) -> None:
        cfg = self._cfg()
        state = duet._build_run_state(
            cfg, turns_used=0, history=[], finished_reason=None,
            transcript_path=pathlib.Path("t.md"),
            recap_path=pathlib.Path("r.md"))
        self.assertNotIn(duet.REVIEW_KICKOFF_KIND, json.dumps(state))
        self.assertNotIn("_kickoff_kind", state)
        self.assertNotIn("task_from_cmd", state)


class TestReviewKickoffParsing(unittest.TestCase):
    def test_valid_envelope_returns_result_and_denial_counts(self) -> None:
        text, denials = duet._parse_review_kickoff_output(_envelope(
            "Findings here", [_denial("Bash"), _denial("Bash"), _denial("Read")]))
        self.assertEqual(text, "Findings here")
        self.assertEqual(denials, {"Bash": 2, "Read": 1})

    def test_missing_denials_are_unknown_and_an_empty_list_is_zero(self) -> None:
        self.assertIsNone(duet._parse_review_kickoff_output(_envelope())[1])
        self.assertEqual(duet._parse_review_kickoff_output(_envelope(denials=[]))[1], {})

    def test_bad_envelopes_are_kickoff_errors(self) -> None:
        bad = {
            "empty output": "",
            "invalid json": "plain review text",
            "list root": "[]",
            "string root": '"text"',
            "empty result": _envelope(""),
            "blank result": _envelope("  \n"),
            "non-string result": json.dumps({"result": 5}),
            "missing result": json.dumps({"is_error": False}),
            "is_error": _envelope("looks fine", is_error=True),
            "deep nesting": "[" * 100000,
        }
        for name, out in bad.items():
            with self.subTest(name=name):
                with self.assertRaises(duet.AgentRunError) as raised:
                    duet._parse_review_kickoff_output(out)
                self.assertEqual(raised.exception.finished_reason,
                                 duet.FINISHED_KICKOFF_ERROR)

    def test_error_text_never_echoes_the_output(self) -> None:
        secret = "private-envelope-secret"
        for out in (secret, json.dumps([secret]),
                    json.dumps({"is_error": False, "result": "", "note": secret})):
            with self.subTest(out=out[:20]):
                with self.assertRaises(duet.AgentRunError) as raised:
                    duet._parse_review_kickoff_output(out)
                self.assertNotIn(secret, str(raised.exception))

    def test_is_error_envelope_keeps_only_the_result_text_as_the_cause(self) -> None:
        out = _envelope(
            "Invalid API key. Please run /login.", [_denial("Bash", "curl SECRET-TOKEN")],
            is_error=True, note="other-field-value")
        with self.assertRaises(duet.AgentRunError) as raised:
            duet._parse_review_kickoff_output(out)
        message = str(raised.exception)
        self.assertEqual(raised.exception.finished_reason, duet.FINISHED_KICKOFF_ERROR)
        self.assertIn("Invalid API key. Please run /login.", message)
        for hidden in ("kickoff-session-id", "SECRET-TOKEN", "other-field-value",
                       '"is_error"', "{"):
            self.assertNotIn(hidden, message)

    def test_is_error_cause_is_bounded(self) -> None:
        with self.assertRaises(duet.AgentRunError) as raised:
            duet._parse_review_kickoff_output(
                _envelope("x" * 100000, is_error=True))
        self.assertLess(len(str(raised.exception)),
                        duet.AGENT_ERROR_STDOUT_TAIL_CHARS + 200)

    def test_nonzero_exit_with_blank_stderr_keeps_the_stdout_cause(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            envelope = _envelope("Invalid API key. Please run /login.",
                                 [_denial("Bash", "curl SECRET-TOKEN")], is_error=True)
            for out, expected in ((envelope, "Invalid API key. Please run /login."),
                                  ("claude: command crashed\n", "claude: command crashed")):
                with self.subTest(out=out[:20]), \
                        mock.patch.object(duet, "_run", return_value=(1, out, "")), \
                        self.assertRaises(duet.AgentRunError) as raised:
                    duet.run_review_kickoff("cmd", root, 10, root)
                message = str(raised.exception)
                self.assertTrue(message.startswith("review kickoff exited 1\nstderr:\n"))
                self.assertIn("stdout (tail):\n" + expected, message)
                for hidden in ("kickoff-session-id", "SECRET-TOKEN", '"is_error"'):
                    self.assertNotIn(hidden, message)

    def test_nonzero_exit_shows_no_stdout_tail_when_stderr_has_text_or_json_has_no_result(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            for out, err in (("stdout text", "boom"), (json.dumps({"a": 1}), ""),
                             (json.dumps([1]), ""), ("", "")):
                with self.subTest(out=out, err=err), \
                        mock.patch.object(duet, "_run", return_value=(1, out, err)), \
                        self.assertRaises(duet.AgentRunError) as raised:
                    duet.run_review_kickoff("cmd", root, 10, root)
                self.assertNotIn("stdout (tail)", str(raised.exception))

    def test_custom_task_from_cmd_failure_text_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            with mock.patch.object(duet, "_run", return_value=(1, "some stdout", "")), \
                    self.assertRaises(duet.AgentRunError) as raised:
                duet.run_task_from_cmd("cmd", root, 10, root)
        self.assertEqual(str(raised.exception),
                         "--task-from-cmd exited 1\nstderr:\n")

    def test_result_limit_applies_to_the_result_not_the_envelope(self) -> None:
        # "é" becomes a six-character é escape, so this envelope is far
        # larger than TASK_MAX_CHARS while the result is exactly at the limit.
        at_limit = _envelope("é" * duet.TASK_MAX_CHARS)
        self.assertGreater(len(at_limit), duet.TASK_MAX_CHARS)
        self.assertLessEqual(len(at_limit), duet.KICKOFF_ENVELOPE_MAX_CHARS)
        text, _ = duet._parse_review_kickoff_output(at_limit)
        self.assertEqual(len(text), duet.TASK_MAX_CHARS)
        with self.assertRaises(duet.AgentRunError):
            duet._parse_review_kickoff_output(
                _envelope("é" * (duet.TASK_MAX_CHARS + 1)))

    def test_shell_stage_accepts_a_large_envelope_but_caps_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            big = _envelope("é" * duet.TASK_MAX_CHARS)
            with mock.patch.object(duet, "_run", return_value=(0, big, "")):
                text, _ = duet.run_review_kickoff("cmd", root, 10, root)
            self.assertEqual(len(text), duet.TASK_MAX_CHARS)
            huge = "x" * (duet.KICKOFF_ENVELOPE_MAX_CHARS + 1)
            with mock.patch.object(duet, "_run", return_value=(0, huge, "")), \
                    self.assertRaises(duet.AgentRunError):
                duet.run_review_kickoff("cmd", root, 10, root)
            with mock.patch.object(duet, "_run", return_value=(0, big, "")), \
                    self.assertRaises(duet.AgentRunError):
                duet.run_task_from_cmd("cmd", root, 10, root)


class TestReviewKickoffRun(unittest.TestCase):
    PROPOSAL = "LGTM rationale: checked the diff by reading it.\n<<<LGTM>>>"
    SECRET = "Bearer SUPER-SECRET-TOKEN"

    def _cfg(self, root: pathlib.Path, *, recipe: bool = True, **kwargs):
        cfg = duet.DuetConfig(
            cwd=root, agents=_agents(), runs_dir=root / "runs",
            task_from_cmd="claude -p /review --private-flag",
            metrics_enabled=False, finding_reports=True, **kwargs)
        if recipe:
            cfg._kickoff_kind = duet.REVIEW_KICKOFF_KIND
        return cfg

    def _run(self, cfg, kickoff_out: str, replies=(), *, inputs=None, tty=False):
        shell_calls: list = []
        agent_calls: list = []
        root = cfg.cwd

        def fake_run(cmd, **kwargs):
            if cmd[0] != "sh":
                raise AssertionError(f"unexpected command {cmd}")
            states = list((root / "runs").glob("*/state.json"))
            self.assertEqual(len(states), 1)
            saved = states[0].read_text(encoding="utf-8")
            self.assertEqual(json.loads(saved)["phase"], "kickoff_running")
            self.assertNotIn("--private-flag", saved)
            shell_calls.append(cmd)
            return 0, kickoff_out, ""

        def fake_call_agent(agent, message, cfg_, first_turn_for_agent, *,
                            run_dir=None, turn_label=None):
            agent_calls.append({"agent": agent.name, "message": message,
                                "label": turn_label})
            agent.last_tool_denials = None
            return replies[len(agent_calls) - 1]

        out = io.StringIO()
        with mock.patch.object(duet, "_run", fake_run), \
                mock.patch.object(duet, "call_agent", fake_call_agent), \
                mock.patch.object(duet, "_register_run_in_home_index"), \
                mock.patch.object(duet.sys.stdin, "isatty", return_value=tty), \
                mock.patch("builtins.input", side_effect=list(inputs or [])), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            state = duet.run_duet(cfg)
        return state, shell_calls, agent_calls, out.getvalue()

    def test_seed_is_the_result_plus_the_denial_note(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            state, shell, calls, _ = self._run(
                self._cfg(root, max_turns=2),
                _envelope("Found a bug.", [_denial("Bash"), _denial("Bash")]),
                [self.PROPOSAL, self.PROPOSAL])
        note = ("[duet] note: review-kickoff was denied 2 tool call(s) this turn "
                "(Bash×2).")
        self.assertEqual(len(shell), 1)
        self.assertTrue(state["task"].startswith("Found a bug.\n\n" + note))
        self.assertIn(note, calls[0]["message"])
        self.assertEqual(state["finished_reason"], duet.FINISHED_CONVERGED)

    def test_clean_envelope_seed_has_no_note(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state, _, _, _ = self._run(
                self._cfg(pathlib.Path(raw).resolve(), max_turns=1),
                _envelope("All clear.", []), ["done"])
        self.assertEqual(state["task"], "All clear.")
        self.assertEqual(state["history"][0]["tool_denials"], {})

    def test_history_entry_records_denials_only_when_the_envelope_has_them(self) -> None:
        cases = (({"Bash": 2}, [_denial("Bash")] * 2), ({}, []), (None, None))
        for expected, denials in cases:
            with self.subTest(expected=expected), \
                    tempfile.TemporaryDirectory() as raw:
                state, _, _, _ = self._run(
                    self._cfg(pathlib.Path(raw).resolve(), max_turns=1),
                    _envelope("seed", denials), ["done"])
                entry = state["history"][0]
                self.assertEqual(
                    {k: entry[k] for k in ("turn", "agent", "kind")},
                    {"turn": 0, "agent": "review-kickoff", "kind": "kickoff"})
                self.assertEqual(entry["len_chars"], len("seed"))
                if expected is None:
                    self.assertNotIn("tool_denials", entry)
                else:
                    self.assertEqual(entry["tool_denials"], expected)

    def test_bad_envelope_ends_the_run_with_kickoff_error(self) -> None:
        for out in ("not json", "[]", _envelope(""), _envelope("x", is_error=True)):
            with self.subTest(out=out[:20]), tempfile.TemporaryDirectory() as raw:
                state, _, calls, _ = self._run(
                    self._cfg(pathlib.Path(raw).resolve()), out)
                self.assertEqual(state["finished_reason"], duet.FINISHED_KICKOFF_ERROR)
                self.assertEqual(calls, [])
                self.assertEqual(state["history"][0]["agent"], "duet")
                self.assertNotIn("tool_denials", state["history"][0])

    def test_tool_input_and_session_id_never_reach_any_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            state, _, calls, out = self._run(
                self._cfg(root, max_turns=1),
                _envelope("seed text", [_denial("Bash", f"curl -H '{self.SECRET}'")]),
                ["done"])
            run_dir = pathlib.Path(state["transcript_path"]).parent
            snapshot = json.dumps(duet.build_run_status(str(run_dir)))
            report = (run_dir / "review.md").read_text(encoding="utf-8")
            files = "".join(f.read_text(encoding="utf-8")
                            for f in run_dir.iterdir() if f.is_file())
        for text in (json.dumps(state), snapshot, report, files, out,
                     calls[0]["message"]):
            self.assertNotIn("SUPER-SECRET", text)
            self.assertNotIn("kickoff-session-id", text)

    def test_custom_task_from_cmd_stays_literal_text(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            envelope = _envelope("looks like claude", [_denial("Bash")])
            state, _, calls, _ = self._run(
                self._cfg(pathlib.Path(raw).resolve(), recipe=False, max_turns=1),
                envelope, ["done"])
        self.assertEqual(state["task"], envelope)
        self.assertNotIn("kickoff", [e.get("kind") for e in state["history"]])
        self.assertNotIn("review-kickoff", json.dumps(state))
        self.assertNotIn("was denied", calls[0]["message"])

    def test_dry_run_never_runs_the_kickoff(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cfg = self._cfg(pathlib.Path(raw).resolve(), dry_run=True)
            with mock.patch.object(duet, "_run",
                                   side_effect=AssertionError("kickoff ran")) as run, \
                    mock.patch.object(duet, "_register_run_in_home_index"), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                state = duet.run_duet(cfg)
            run.assert_not_called()
        self.assertNotIn("review-kickoff", json.dumps(state))

    def test_kickoff_denials_are_counted_exactly_once_everywhere(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            state, _, calls, out = self._run(
                self._cfg(root, max_turns=2),
                _envelope("seed", [_denial("Bash"), _denial("Read")]),
                [self.PROPOSAL, self.PROPOSAL])
            run_dir = pathlib.Path(state["transcript_path"]).parent
            snapshot = duet.build_run_status(str(run_dir))
            report = duet.build_finding_report(state)
            markdown = (run_dir / "review.md").read_text(encoding="utf-8")
        self.assertEqual(snapshot["tool_denials"], 2)
        self.assertEqual(duet.history_tool_denial_total(state["history"]), 2)
        self.assertEqual(report["execution_limits"], [
            {"agent": "review-kickoff", "slot": None, "denied_tool_calls": 2}])
        self.assertEqual(markdown.count("`review-kickoff` was denied 2 tool call(s)"), 1)
        self.assertEqual(out.count("denied 2 tool call(s) in this run"), 1)
        self.assertEqual(sum("review-kickoff was denied" in c["message"]
                             for c in calls), 1)
        self.assertEqual(report["structured_turns"]["missing"], 2)

    def test_turn_numbers_ignore_the_kickoff_entry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state, _, calls, _ = self._run(
                self._cfg(pathlib.Path(raw).resolve(), max_turns=2),
                _envelope("seed"), ["first", "second"])
        self.assertEqual([c["label"] for c in calls], ["01", "02"])
        self.assertEqual([e["turn"] for e in state["history"]], [0, 1, 2])
        self.assertEqual(state["turns_used"], 2)

    def test_forced_turn_follows_the_last_loop_turn(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state, _, calls, _ = self._run(
                self._cfg(pathlib.Path(raw).resolve(), max_turns=2),
                _envelope("seed"), ["first", "second", "forced reply"],
                inputs=["please fix it", ""], tty=True)
        self.assertEqual(calls[2]["label"], "03-forced")
        self.assertEqual([e["turn"] for e in state["history"]], [0, 1, 2, 3])
        self.assertTrue(state["history"][-1]["forced"])
        self.assertEqual(state["turns_used"], 3)

    def test_loop_turn_count_skips_setup_entries_only(self) -> None:
        history = [{"kind": "kickoff"}, {"kind": "seed_extract"},
                   {"kind": "final_review"}, {"turn": 1}, {"turn": 2, "forced": True},
                   "not a dict"]
        self.assertEqual(duet._loop_turn_count(history), 3)
        self.assertEqual(duet._loop_turn_count([]), 0)

    def test_stop_during_setup_reports_loop_turns_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            cfg = self._cfg(root)
            with contextlib.redirect_stdout(io.StringIO()):
                state = duet._requested_stop_state(
                    cfg,
                    history=[duet._review_kickoff_entry(1.0, 4, {"Bash": 1})],
                    transcript_path=root / "transcript.md",
                    recap_path=root / "recap.md",
                    state_path=root / "state.json")
        self.assertEqual(state["finished_reason"], duet.FINISHED_FORCE_STOP)
        self.assertEqual(state["turns_used"], 0)


class TestSeedExtractTurnCount(unittest.TestCase):
    """A seed-extraction entry (--resume-claude) is not a loop or forced turn."""

    def test_forced_turn_number_and_turns_used_skip_the_seed_entry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            transcript = root / "transcript.md"
            transcript.write_text("", encoding="utf-8")
            state_path = root / "state.json"
            agents = _agents()
            cfg = duet.DuetConfig(cwd=root, agents=agents, task="review",
                                  runs_dir=root, metrics_enabled=False)
            history = [
                {"turn": 0, "kind": "seed_extract", "agent": "claude-reviewer"},
                {"turn": 1, "agent": "codex-coder"},
                {"turn": 2, "agent": "claude-reviewer"},
            ]
            labels: list = []

            def fake_call_agent(agent, message, cfg_, first_turn_for_agent, *,
                                run_dir=None, turn_label=None):
                labels.append(turn_label)
                agent.last_tool_denials = None
                return "forced reply"

            with mock.patch.object(duet, "call_agent", fake_call_agent), \
                    mock.patch.object(duet.sys.stdin, "isatty", return_value=True), \
                    mock.patch("builtins.input", side_effect=["more please", ""]), \
                    contextlib.redirect_stdout(io.StringIO()):
                duet.ask_force(
                    cfg, history, transcript, state_path, "last", 1,
                    {a.name: True for a in agents}, duet.FINISHED_MAX_TURNS)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(labels, ["03-forced"])
        self.assertEqual(history[-1]["turn"], 3)
        self.assertEqual(saved["turns_used"], 3)


class TestReviewEvidenceSplit(unittest.TestCase):
    OLD = (
        "Test changed behavior with inputs you choose yourself, not only the "
        "partner's examples. Include inputs that should fail, removed or renamed "
        "cases, and large inputs where cost can grow. If you cannot run a command, "
        "name the missing check and ask your partner to run it and paste the "
        "command and its output."
    )

    def test_loop_reviewer_text_is_byte_identical_to_the_old_constant(self) -> None:
        self.assertEqual(duet.REVIEWER_EVIDENCE_INSTRUCTION, self.OLD)
        for role in ("reviewer", "triage-reviewer"):
            self.assertEqual(duet.ROLE_PROMPTS[role].count(self.OLD), 1)

    def test_contexts_share_the_criteria_and_differ_in_the_missing_check_rule(self) -> None:
        criteria = duet.REVIEW_EVIDENCE_CRITERIA
        self.assertTrue(self.OLD.startswith(criteria))
        self.assertNotIn("If you cannot run", criteria)
        self.assertNotEqual(duet.LOOP_MISSING_EVIDENCE_SENTENCE,
                            duet.KICKOFF_MISSING_EVIDENCE_SENTENCE)
        for role in ("reviewer", "triage-reviewer"):
            self.assertNotIn(duet.KICKOFF_MISSING_EVIDENCE_SENTENCE,
                             duet.ROLE_PROMPTS[role])


if __name__ == "__main__":
    unittest.main()
