"""Test loop behavior in-process with local fixtures and stubbed agents."""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import shlex
import sys
import tempfile
import unittest
from unittest import mock

import duet


class TestLoopInternals(unittest.TestCase):
    PROPOSAL = (
        "LGTM rationale: task is satisfied, checks were attempted, and no "
        "blocking follow-ups remain.\n<<<LGTM>>>"
    )

    def setUp(self) -> None:
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        self.root = pathlib.Path(fixture.name).resolve()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for patcher in (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
            mock.patch.object(duet, "_register_run_in_home_index"),
            mock.patch.object(duet, "_install_sigint"),
            mock.patch.object(duet, "_install_sigterm"),
            mock.patch.object(duet, "_install_run_stop_signal"),
            mock.patch.object(duet.sys.stdin, "isatty", return_value=False),
        ):
            stack.enter_context(patcher)

    def _config(self, **overrides) -> duet.DuetConfig:
        defaults = {
            "cwd": self.root,
            "runs_dir": self.root / "runs",
            "agents": [duet.Agent("claude-lead", "claude", role="planner"),
                       duet.Agent("codex-partner", "codex", role="coder")],
            "task": "x",
            "max_turns": 2,
            "metrics_enabled": False,
        }
        return duet.DuetConfig(**{**defaults, **overrides})

    def test_task_command_reads_from_its_project_directory(self) -> None:
        seed = duet.run_task_from_cmd("pwd -P", self.root, 10, self.root)
        self.assertEqual(seed.strip(), str(self.root))

    def test_subprocess_replaces_undecodable_bytes_in_both_streams(self) -> None:
        cmd = [
            sys.executable, "-c",
            r"import os; os.write(1, b'out:\xff\n'); os.write(2, b'err:\xff\n')",
        ]
        rc, out, err = duet._run(cmd, cwd=self.root, stdin="", timeout=5)

        self.assertEqual(rc, 0)
        self.assertEqual(out, "out:\ufffd\n")
        self.assertEqual(err, "err:\ufffd\n")

    def test_verifier_failure_reaches_the_next_agent(self) -> None:
        cfg = self._config(verify_cmd="false")

        with mock.patch.object(duet, "call_agent", return_value=self.PROPOSAL) as agent:
            state = duet.run_duet(cfg)

        self.assertEqual(state["finished_reason"], duet.FINISHED_MAX_TURNS)
        self.assertEqual(agent.call_count, 2)
        prompt = agent.call_args.args[1]
        self.assertIn("[duet verify failed]", prompt)
        self.assertIn("command: false", prompt)

    def test_bare_sentinel_does_not_run_the_verifier(self) -> None:
        marker = self.root / "verify-ran"
        cfg = self._config(max_turns=1, verify_cmd=f"touch {shlex.quote(str(marker))}")
        with mock.patch.object(duet, "call_agent", return_value="done\n<<<LGTM>>>"):
            state = duet.run_duet(cfg)

        self.assertEqual(state["finished_reason"], duet.FINISHED_MAX_TURNS)
        self.assertIsNone(state["last_verify"])
        self.assertFalse(marker.exists())

    def test_verify_command_receives_a_pid_file_path(self) -> None:
        cfg = self._config(verify_cmd="true")
        with mock.patch.object(duet, "_run", return_value=(0, "", "")) as run:
            duet.run_verify_command(cfg, self.root, "01", None)
        self.assertEqual(run.call_args.kwargs["pid_file_path"], self.root / "turn-01-verify.pid")

    def test_verify_command_runs_in_the_worktree(self) -> None:
        host = self.root / "host"
        worktree = self.root / "worktree"
        host.mkdir()
        worktree.mkdir()
        cfg = self._config(
            cwd=host, worktree_path=worktree, max_turns=1, verify_cmd="pwd > verify-pwd.txt",
        )
        with mock.patch.object(duet, "call_agent", return_value=self.PROPOSAL):
            duet.run_duet(cfg)

        self.assertEqual((worktree / "verify-pwd.txt").read_text().strip(), str(worktree))
        self.assertFalse((host / "verify-pwd.txt").exists())

    def test_verify_output_in_the_next_prompt_keeps_only_a_bounded_tail(self) -> None:
        command = 'import sys; print("A"*9000); print("TAILMARK"); sys.exit(1)'
        cfg = self._config(verify_cmd=f"{shlex.quote(sys.executable)} -c {shlex.quote(command)}")
        with mock.patch.object(duet, "call_agent", return_value=self.PROPOSAL) as agent:
            duet.run_duet(cfg)

        prompt = agent.call_args.args[1]
        self.assertIn("TAILMARK", prompt)
        self.assertIn("output truncated to last", prompt)
        self.assertNotIn("A" * (duet.VERIFY_OUTPUT_TAIL_CHARS + 1), prompt)

    def _failed_run(self, cfg, *, code: int, stderr: str) -> tuple[dict, pathlib.Path, str]:
        def fail(cmd, **kwargs):
            if kwargs.get("stderr_log_path") is not None:
                duet.write_text_atomic(kwargs["stderr_log_path"], stderr)
            return code, "", stderr

        with mock.patch.object(duet, "_run", side_effect=fail):
            state = duet.run_duet(cfg)
        entry = state["history"][-1]
        return (state, pathlib.Path(entry["stderr_log_path"]),
                pathlib.Path(state["transcript_path"]).read_text())

    def test_turn_timeout_is_saved_with_the_stderr_log(self) -> None:
        stderr = "timeout stderr\n[duet] TIMEOUT after 1s"
        state, log, transcript = self._failed_run(self._config(), code=124, stderr=stderr)

        self.assertEqual(state["finished_reason"], duet.FINISHED_TIMEOUT)
        self.assertEqual(state["turns_used"], 1)
        entry = state["history"][-1]
        self.assertEqual(entry["finished_reason"], duet.FINISHED_TIMEOUT)
        self.assertIn("codex exited 124", entry["error"])
        self.assertIn("timeout stderr", log.read_text())
        self.assertIn("finished_reason: timeout", transcript)
        self.assertIn("TIMEOUT", transcript)
        self.assertIn(str(log), transcript)

    def test_nonzero_exit_keeps_complete_log_and_bounded_persisted_errors(self) -> None:
        length_limit = duet.AGENT_ERROR_TRANSCRIPT_MAX_CHARS
        head = "fatal backend stderr head"
        tail = "fatal backend stderr tail"
        stderr = head + "\n" + "X" * (length_limit * 2) + "\n" + tail
        cfg = self._config()
        state, log, transcript = self._failed_run(cfg, code=7, stderr=stderr)

        self.assertEqual(state["finished_reason"], duet.FINISHED_AGENT_ERROR)
        entry = state["history"][-1]
        self.assertEqual(entry["finished_reason"], duet.FINISHED_AGENT_ERROR)
        self.assertIn("codex exited 7", entry["error"])
        self.assertLessEqual(len(entry["error"]), length_limit)
        self.assertRegex(entry["error"], r"(?:chars|characters) omitted")
        self.assertEqual(log.read_text(), stderr)
        for token in ("finished_reason: agent_error", head, tail, str(log)):
            self.assertIn(token, transcript)
        self.assertNotIn("X" * length_limit, transcript)
        self.assertLess(len(transcript), length_limit + 4000)
        self.assertEqual(duet.print_run_status(str(log.parent)), 0)
        self.assertEqual(duet.print_runs_list(cfg.runs_dir), 0)

    def test_seed_extraction_timeout_is_saved_without_consuming_a_turn(self) -> None:
        cfg = self._config(task=None)
        cfg.agents[0].session_id = "seed-session"
        state, log, transcript = self._failed_run(
            cfg, code=124, stderr="seed timeout stderr\n[duet] TIMEOUT after 1s",
        )

        self.assertEqual(state["finished_reason"], duet.FINISHED_TIMEOUT)
        self.assertEqual(state["turns_used"], 0)
        self.assertEqual(state["history"][-1]["kind"], "seed_extract")
        self.assertEqual(state["history"][-1]["finished_reason"], duet.FINISHED_TIMEOUT)
        self.assertIn("seed timeout stderr", log.read_text())
        self.assertIn(str(log), transcript)

    def test_sigint_before_first_turn_stays_a_force_stop(self) -> None:
        with mock.patch.object(duet, "_install_sigint", side_effect=lambda stop: stop.request("SIGINT")):
            state = duet.run_duet(self._config())
        self.assertEqual(state["finished_reason"], duet.FINISHED_FORCE_STOP)
        self.assertEqual(state["turns_used"], 0)

    def test_same_backend_turns_use_distinct_stderr_logs(self) -> None:
        cfg = self._config(
            start_speaker_idx=0,
            agents=[duet.Agent("codex-lead", "codex", role="planner"),
                    duet.Agent("codex-partner", "codex", role="coder")],
        )
        sessions = iter(["019e12ad-0b1b-7732-bd7b-6acbbd04ab61",
                         "019e12ad-0b1b-7732-bd7b-6acbbd04ab62"])

        def reply(cmd, **kwargs):
            log = kwargs["stderr_log_path"]
            duet.write_text_atomic(log, log.name + "\n")
            return 0, "reply", f"session id: {next(sessions)}\n"

        with mock.patch.object(duet, "_run", side_effect=reply):
            state = duet.run_duet(cfg)
        run_dir = pathlib.Path(state["transcript_path"]).parent
        for name in ("turn-01-codex-lead.stderr.log", "turn-02-codex-partner.stderr.log"):
            self.assertEqual((run_dir / name).read_text(), name + "\n")

    def test_worktree_handoff_identifies_the_review_target_and_git_commands(self) -> None:
        worktree = self.root / "review wt"
        diff = "### git status\n M duet.py\n\n### diff\n..."
        with mock.patch.object(duet, "git_diff_summary", return_value=diff):
            handoff = duet.append_worktree_diff("reply", worktree, "duet/test-run")
        for token in (
            f"#### worktree changes ({worktree.name})",
            f"Worktree path: `{worktree}`",
            "Branch: `duet/test-run`",
            "Any code changes for this turn",
            "Your current cwd may be a clean checkout",
            f"git -C {shlex.quote(str(worktree))} status --short",
            f"git -C {shlex.quote(str(worktree))} diff HEAD",
        ):
            self.assertIn(token, handoff)
        self.assertNotIn("The code changes for this turn", handoff)
        self.assertNotIn("make -C", handoff)


class TestContinuationState(unittest.TestCase):
    def setUp(self) -> None:
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        self.root = pathlib.Path(fixture.name).resolve()
        self.run_dir = self.root / "20260507-130000"
        self.run_dir.mkdir()
        self.agents = [
            {"name": "claude-lead", "backend": "claude", "role": "planner", "session_id": "c1"},
            {"name": "codex-partner", "backend": "codex", "role": "coder", "session_id": "codex-current"},
        ]

    def _write_state(self, **overrides) -> None:
        state = {
            "task": "x",
            "cwd": str(self.root),
            "turns_used": 1,
            "agents": self.agents,
            "history": [{"turn": 1, "agent": "claude-lead"}],
            "finished_reason": None,
        }
        (self.run_dir / "state.json").write_text(json.dumps({**state, **overrides}), encoding="utf-8")

    def _continue(self, *flags) -> duet.DuetConfig:
        parser = duet._build_arg_parser()
        args = parser.parse_args(["--continue", str(self.run_dir), *flags])
        with contextlib.redirect_stderr(io.StringIO()):
            return duet.build_continue_config(str(self.run_dir), args, parser, {})

    def test_dry_run_restores_settings_and_allows_disabling_fast_mode(self) -> None:
        extra = self.root / "extra"
        self._write_state(
            sentinel="DONE", sandbox="read-only", permission_mode="plan", per_turn_timeout=7,
            add_dirs=[str(extra)], reasoning="high", codex_fast=True,
        )
        cfg = self._continue("--dry-run")

        self.assertEqual(cfg.sentinel, "DONE")
        self.assertEqual(cfg.sandbox, "read-only")
        self.assertEqual(cfg.permission_mode, "plan")
        self.assertEqual(cfg.per_turn_timeout, 7)
        self.assertEqual(cfg.add_dirs, [extra])
        self.assertEqual(cfg.reasoning, "high")
        self.assertTrue(cfg.codex_fast)
        self.assertFalse(self._continue("--dry-run", "--no-codex-fast").codex_fast)

    def test_state_commands_and_authority_require_trust(self) -> None:
        self.agents[0]["extra_args"] = ["--debug"]
        self._write_state(
            verify_cmd="echo trusted", sandbox="danger-full-access",
            permission_mode="bypassPermissions", add_dirs=["/"],
        )
        with self.assertRaises(SystemExit) as caught:
            self._continue()
        self.assertEqual(caught.exception.code, 2)

        cfg = self._continue("--trust-state")
        self.assertEqual(cfg.verify_cmd, "echo trusted")
        self.assertEqual(cfg.agents[0].extra_args, ["--debug"])
        self.assertEqual(cfg.sandbox, "danger-full-access")
        self.assertEqual(cfg.permission_mode, "bypassPermissions")
        self.assertEqual(cfg.add_dirs, [pathlib.Path("/")])


class TestStatusRace(unittest.TestCase):
    def test_terminal_status_survives_a_disappearing_pid_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = pathlib.Path(raw) / "20260507-140000"
            run_dir.mkdir()
            state = {
                "task": "x", "cwd": raw, "turns_used": 1, "agents": [],
                "history": [], "finished_reason": "max_turns",
            }
            (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (run_dir / "turn-01-codex.pid").write_text("123\n", encoding="utf-8")
            original_stat = pathlib.Path.stat

            def stat(path, *args, **kwargs):
                if path.suffix == ".pid":
                    raise FileNotFoundError(str(path))
                return original_stat(path, *args, **kwargs)

            with mock.patch.object(pathlib.Path, "stat", stat), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(duet.print_run_status(str(run_dir)), 0)
