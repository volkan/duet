"""Contract tests for recipe launch metadata and machine-readable status."""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
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
        self.assertEqual(args.task_from_cmd, "claude -p /review")
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
            cfg.task_from_cmd,
            "claude -p /review --model claude-fable-5",
        )

    def test_explicit_seed_suppresses_recipe_kickoff(self) -> None:
        _, args = self._args("--recipe", "review", "--task", "inspect this")
        self.assertIsNone(args.task_from_cmd)
        self.assertEqual(args.task, "inspect this")

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
            command = f"test -s {duet.shlex.quote(str(info))} && printf seed"
            cfg = self._cfg(
                root, info, task_from_cmd=command, dry_run=True, recap=True
            )
            with mock.patch.object(duet, "_register_run_in_home_index"), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                state = duet.run_duet(cfg)

            payload = json.loads(info.read_text(encoding="utf-8"))
            self.assertEqual(set(payload), {
                "schema_version", "kind", "duet_version", "run_id",
                "run_dir", "state_path", "pid",
            })
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["kind"], "duet.run")
            self.assertEqual(payload["duet_version"], duet.__version__)
            self.assertTrue(pathlib.Path(payload["run_dir"]).is_absolute())
            self.assertEqual(state["finished_reason"], "dry_run")
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
                "active_turn", "last_completed_turn", "artifacts", "error",
            })
            self.assertEqual(snapshot["kind"], "duet.status")
            self.assertEqual(snapshot["health"], "terminal")
            self.assertEqual(snapshot["phase"], "finished")
            self.assertNotIn(secret, json.dumps(snapshot))

    def test_relative_artifact_paths_resolve_from_saved_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            cwd = root / "project"
            run = cwd / "runs" / "20260719-120000"
            run.mkdir(parents=True)
            relative_run = pathlib.Path("runs") / run.name
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


if __name__ == "__main__":
    unittest.main()
