"""Contract tests for recipe launch metadata and machine-readable status."""
from __future__ import annotations

import contextlib
import io
import json
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
        _, args = self._args("--recipe", "review")
        self.assertEqual(args.lead, "claude:reviewer")
        self.assertEqual(args.partner, "codex:coder")
        self.assertEqual(args.turns, 6)
        self.assertTrue(args.recap)
        self.assertTrue(args.worktree)
        self.assertTrue(args.require_worktree)
        self.assertEqual(args.lead_model, "sonnet")
        self.assertEqual(
            duet.shlex.split(args.task_from_cmd)[:5],
            ["claude", "-p", "/review", "--model", "sonnet"],
        )
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
        _, args = self._args(
            "--recipe", "review",
            "--lead", "gemini:reviewer",
        )

        self.assertIsNone(args.lead_model)
        self.assertEqual(
            duet.shlex.split(args.task_from_cmd)[:5],
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
                "last_timeout", "artifacts", "error",
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
            })
            self.assertEqual(snapshot["last_timeout"], {
                "turn": 1,
                "agent": "codex-coder",
                "elapsed_seconds": 60.2,
                "output_chars": 123,
                "finished_reason": "timeout",
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


if __name__ == "__main__":
    unittest.main()
