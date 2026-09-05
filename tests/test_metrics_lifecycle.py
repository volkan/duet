"""Lifecycle contracts for central metrics snapshots (all agent work is mocked)."""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import duet  # noqa: E402


class TestMetricsLifecycle(unittest.TestCase):
    def _cfg(self, root: pathlib.Path, **overrides: object) -> duet.DuetConfig:
        values: dict[str, object] = {
            "cwd": root,
            "runs_dir": root / "runs",
            "task": "private task",
            "max_turns": 1,
            "metrics_enabled": True,
            "agents": [
                duet.Agent("lead", "claude", "planner"),
                duet.Agent("partner", "codex", "coder"),
            ],
        }
        values.update(overrides)
        return duet.DuetConfig(**values)

    def _snapshot(self, metrics_root: pathlib.Path) -> dict:
        path = next((metrics_root / "runs").glob("*.json"))
        return json.loads(path.read_text(encoding="utf-8"))

    def test_normal_verify_run_collects_only_curated_timing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            metrics = root / "metrics"
            cfg = self._cfg(root, verify_cmd="private verify command")
            verify = duet.VerifyResult(
                True, cfg.verify_cmd, root, 0, "", "", root / "verify.log", elapsed_s=0.25,
            )
            reply = "LGTM rationale: this is enough detail for a valid proposal.\n<<<LGTM>>>"
            with mock.patch.object(duet, "_metrics_root", return_value=metrics), \
                    mock.patch.object(pathlib.Path, "home", return_value=root / "home"), \
                    mock.patch.object(duet, "_metrics_cli_version", return_value="1.2.3"), \
                    mock.patch.object(duet, "call_agent", return_value=reply), \
                    mock.patch.object(duet, "run_verify_command", return_value=verify), \
                    mock.patch.object(duet.sys.stdin, "isatty", return_value=False), \
                    contextlib.redirect_stdout(io.StringIO()):
                state = duet.run_duet(cfg)
            snapshot = self._snapshot(metrics)
        self.assertEqual(state["finished_reason"], duet.FINISHED_MAX_TURNS)
        self.assertEqual(snapshot["verification"], {"attempts": 1, "passed": 1, "failed": 0})
        self.assertEqual(snapshot["turns"][0]["verify_elapsed_s"], 0.25)
        self.assertNotIn("private task", json.dumps(snapshot))
        self.assertNotIn("private verify command", json.dumps(snapshot))

    def test_seed_extraction_is_metrics_only_turn_zero(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            metrics = root / "metrics"
            lead = duet.Agent("lead", "claude", "planner", session_id="seed-session")
            cfg = self._cfg(root, agents=[lead, duet.Agent("partner", "codex", "coder")], task=None)
            with mock.patch.object(duet, "_metrics_root", return_value=metrics), \
                    mock.patch.object(pathlib.Path, "home", return_value=root / "home"), \
                    mock.patch.object(duet, "_metrics_cli_version", return_value="1.2.3"), \
                    mock.patch.object(duet, "call_agent", side_effect=["seed text", "reply"]), \
                    mock.patch.object(duet.sys.stdin, "isatty", return_value=False), \
                    contextlib.redirect_stdout(io.StringIO()):
                state = duet.run_duet(cfg)
            snapshot = self._snapshot(metrics)
        self.assertEqual(len(state["history"]), 1)
        self.assertEqual([turn["kind"] for turn in snapshot["turns"]], ["seed", "loop"])
        self.assertEqual(snapshot["turns"][0]["turn"], 0)
        self.assertIsNotNone(snapshot["seed_elapsed_s"])

    def test_failed_seed_and_kickoff_stay_nonfatal_to_snapshot_storage(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            seed_metrics = root / "seed-metrics"
            lead = duet.Agent("lead", "claude", "planner", session_id="seed-session")
            seed_cfg = self._cfg(root, agents=[lead, duet.Agent("partner", "codex", "coder")], task=None)
            with mock.patch.object(duet, "_metrics_root", return_value=seed_metrics), \
                    mock.patch.object(pathlib.Path, "home", return_value=root / "home"), \
                    mock.patch.object(duet, "_metrics_cli_version", return_value="1.2.3"), \
                    mock.patch.object(duet, "call_agent", side_effect=duet.AgentRunError("agent_error", "private failure")), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                state = duet.run_duet(seed_cfg)
            seed = self._snapshot(seed_metrics)
            self.assertEqual(state["finished_reason"], duet.FINISHED_AGENT_ERROR)
            self.assertEqual(seed["turns"][0]["kind"], "seed")
            self.assertEqual(seed["turns"][0]["outcome"], "agent_error")
            self.assertNotIn("private failure", json.dumps(seed))

            kickoff_metrics = root / "kickoff-metrics"
            kickoff_cfg = self._cfg(root, task=None, task_from_cmd="private kickoff")
            with mock.patch.object(duet, "_metrics_root", return_value=kickoff_metrics), \
                    mock.patch.object(pathlib.Path, "home", return_value=root / "home"), \
                    mock.patch.object(duet, "_metrics_cli_version", return_value="1.2.3"), \
                    mock.patch.object(duet, "run_task_from_cmd", side_effect=duet.AgentRunError("kickoff_error", "private kickoff failure")), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                state = duet.run_duet(kickoff_cfg)
            kickoff = self._snapshot(kickoff_metrics)
        self.assertEqual(state["finished_reason"], duet.FINISHED_KICKOFF_ERROR)
        self.assertEqual(kickoff["turns"], [])
        self.assertIsNotNone(kickoff["kickoff_elapsed_s"])
        self.assertNotIn("private kickoff", json.dumps(kickoff))

    def test_forced_handoff_bytes_exclude_verification_block(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            cfg = self._cfg(root, verify_cmd="verify")
            next_speaker = cfg.agents[1]
            next_speaker.cwd_override = root / "worktree"
            transcript = root / "transcript.md"
            transcript.write_text("", encoding="utf-8")
            verify = duet.VerifyResult(True, "verify", root, 0, "", "", root / "verify.log")
            handoff = "\nHANDOFF-BYTES"
            history: list[dict] = []
            with mock.patch.object(duet, "_metrics_root", return_value=root / "metrics"), \
                    mock.patch.object(duet, "_metrics_cli_version", return_value="1.2.3"), \
                    mock.patch.object(duet, "call_agent", return_value=(
                    "LGTM rationale: this is enough detail for a valid proposal.\n<<<LGTM>>>")), \
                    mock.patch.object(duet, "run_verify_command", return_value=verify), \
                    mock.patch.object(duet, "append_worktree_diff", side_effect=lambda text, *_: text + handoff), \
                    contextlib.redirect_stdout(io.StringIO()):
                duet._begin_run_metrics(cfg)
                duet._run_forced_turn(
                    cfg, forced_turn=1, next_speaker=next_speaker, forced_msg="x",
                    first_turn_for_agent=True, transcript_path=transcript,
                    wt_path=next_speaker.cwd_override, wt_branch="branch", history=history,
                    seen_first_turn={agent.name: False for agent in cfg.agents},
                    last_verify_state=None,
                )
        self.assertEqual(history[0]["metrics"]["handoff_bytes"], len(handoff.encode()))
        self.assertGreater(history[0]["metrics"]["delivered_output_bytes"],
                           history[0]["metrics"]["handoff_bytes"])


if __name__ == "__main__":
    unittest.main()
