"""Focused contract tests for central metrics collection and explicit refresh."""
from __future__ import annotations

import contextlib
import fcntl
import io
import json
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import duet  # noqa: E402


class TestMetricsCollection(unittest.TestCase):
    def _cfg(self, root: pathlib.Path, **overrides: object) -> duet.DuetConfig:
        values: dict[str, object] = {
            "cwd": root / "private-project-name",
            "agents": [
                duet.Agent(name="lead", backend="claude", role="planner",
                            model="sonnet", session_id="private-lead-session"),
                duet.Agent(name="partner", backend="codex", role="coder",
                            model="gpt-5", session_id="private-partner-session"),
            ],
            "task": "private task and secret command text",
            "runs_dir": root / "runs",
            "metrics_enabled": True,
        }
        values.update(overrides)
        return duet.DuetConfig(**values)

    def _state(self, cfg: duet.DuetConfig, state_path: pathlib.Path, *, history=None) -> dict:
        return duet._build_run_state(
            cfg, turns_used=1, history=[] if history is None else history,
            finished_reason="max_turns", transcript_path=state_path.parent / "transcript.md",
            recap_path=state_path.parent / "recap.md",
        )

    def test_snapshot_projects_metadata_and_excludes_private_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            metrics_root = root / "metrics"
            state_path = root / "local" / "state.json"
            state_path.parent.mkdir()
            cfg = self._cfg(root)
            history = [{
                "turn": 1, "agent": "lead", "elapsed_s": 2.5,
                "error": "private raw error", "command": "private command",
                "session_id": "private turn session",
                "metrics": {
                    "model_reported": "claude-sonnet-4-6", "input": "private raw output",
                    "usage": {"input_tokens": 12, "output_tokens": 0},
                    "cost_usd": 0.01,
                },
                "verify": {"ok": True, "elapsed_s": 0.5, "command": "private verify"},
            }]
            with mock.patch.object(duet, "_metrics_root", return_value=metrics_root), \
                    mock.patch.object(duet, "_metrics_cli_version", return_value="9.9.9"):
                duet._begin_run_metrics(cfg)
                duet._probe_run_metrics(cfg)
                state = self._state(cfg, state_path, history=history)
                duet._write_run_state(state_path, state)

            snapshots = list((metrics_root / "runs").glob("*.json"))
            self.assertEqual(len(snapshots), 1)
            snapshot = json.loads(snapshots[0].read_text(encoding="utf-8"))
            self.assertRegex(snapshot["id"], re.compile(
                r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$"))
            self.assertEqual(snapshot["source"], "recorded")
            self.assertEqual(snapshot["agents"][0]["cli_version"], "9.9.9")
            self.assertEqual(snapshot["turns"][0]["agent_elapsed_s"], 2.5)
            self.assertEqual(snapshot["turns"][0]["verify_elapsed_s"], 0.5)
            self.assertEqual(snapshot["turns"][0]["usage"]["output_tokens"], 0)
            dumped = json.dumps(snapshot, sort_keys=True)
            for private in ("private-project-name", "private task", "private command",
                            "private raw error", "private turn session", "private verify",
                            "private-lead-session", "private-partner-session"):
                self.assertNotIn(private, dumped)

            state_path.unlink()
            self.assertEqual(json.loads(snapshots[0].read_text(encoding="utf-8"))["id"],
                             snapshot["id"])

    def test_repeated_writes_keep_one_id_and_separate_runs_get_unique_ids(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            metrics_root = root / "metrics"
            first_path = root / "first" / "state.json"
            second_path = root / "second" / "state.json"
            first_path.parent.mkdir()
            second_path.parent.mkdir()
            first = self._cfg(root)
            second = self._cfg(root)
            with mock.patch.object(duet, "_metrics_root", return_value=metrics_root), \
                    mock.patch.object(duet, "_metrics_now", return_value="2026-01-01T00:00:00+00:00"):
                duet._begin_run_metrics(first)
                duet._begin_run_metrics(second)
                first_state = self._state(first, first_path)
                duet._write_run_state(first_path, first_state)
                duet._write_run_state(first_path, first_state)
                duet._write_run_state(second_path, self._state(second, second_path))

            snapshots = [json.loads(path.read_text(encoding="utf-8"))
                         for path in (metrics_root / "runs").glob("*.json")]
            self.assertEqual(len(snapshots), 2)
            self.assertEqual(len({snapshot["id"] for snapshot in snapshots}), 2)

    def test_disabled_and_unwritable_metrics_do_not_block_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            state_path = root / "local" / "state.json"
            state_path.parent.mkdir()
            disabled = self._cfg(root, metrics_enabled=False)
            with mock.patch.object(duet, "_metrics_root", return_value=root / "metrics"):
                duet._begin_run_metrics(disabled)
                duet._write_run_state(state_path, self._state(disabled, state_path))
            self.assertTrue(state_path.is_file())
            self.assertFalse((root / "metrics" / "runs").exists())

            blocked = root / "blocked-metrics"
            blocked.write_text("not a directory", encoding="utf-8")
            enabled = self._cfg(root)
            broken_state = root / "broken" / "state.json"
            broken_state.parent.mkdir()
            with mock.patch.object(duet, "_metrics_root", return_value=blocked), \
                    contextlib.redirect_stderr(io.StringIO()):
                duet._begin_run_metrics(enabled)
                duet._write_run_state(broken_state, self._state(enabled, broken_state))
            self.assertTrue(broken_state.is_file())

    def test_test_and_dry_run_kinds_are_persisted_separately(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            metrics_root = root / "metrics"
            test_cfg = self._cfg(root, metrics_kind="test")
            dry_cfg = self._cfg(root, dry_run=True)
            paths = [root / "test" / "state.json", root / "dry" / "state.json"]
            for path in paths:
                path.parent.mkdir()
            with mock.patch.object(duet, "_metrics_root", return_value=metrics_root):
                duet._begin_run_metrics(test_cfg)
                duet._begin_run_metrics(dry_cfg)
                duet._write_run_state(paths[0], self._state(test_cfg, paths[0]))
                duet._write_run_state(paths[1], self._state(dry_cfg, paths[1]))
            kinds = {json.loads(path.read_text(encoding="utf-8"))["run_kind"]
                     for path in (metrics_root / "runs").glob("*.json")}
            self.assertEqual(kinds, {"test", "dry_run"})

    def test_stale_writer_cannot_replace_newer_terminal_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            metrics_root = root / "metrics"
            snapshot_id = "00000000-0000-4000-8000-000000000001"
            newer = {
                "schema_version": 1, "kind": "duet.metrics.run", "id": snapshot_id,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "wall_elapsed_s": 20.0,
            }
            stale = {**newer, "wall_elapsed_s": 5.0}
            with mock.patch.object(duet, "_metrics_root", return_value=metrics_root):
                self.assertTrue(duet._write_metric_snapshot(newer))
                self.assertFalse(duet._write_metric_snapshot(stale))
                same_duration_older = {**newer, "updated_at": "2025-12-31T23:59:59+00:00"}
                self.assertFalse(duet._write_metric_snapshot(same_duration_older))
            written = json.loads((metrics_root / "runs" / f"{snapshot_id}.json").read_text())
        self.assertEqual(written["wall_elapsed_s"], 20.0)

    def test_process_held_lock_skips_replace_without_breaking_collection(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            metrics_root = root / "metrics"
            snapshot_id = "00000000-0000-4000-8000-000000000002"
            snapshot = {"id": snapshot_id, "updated_at": None, "wall_elapsed_s": 1.0}
            lock_path = metrics_root / "locks" / f"{snapshot_id}.lock"
            lock_path.parent.mkdir(parents=True)
            state_path = root / "state.json"
            state = {
                "metrics_enabled": True,
                "metrics": {"id": snapshot_id, "project_id": "a" * 64},
                "history": [], "agents": [], "cwd": str(root),
            }
            with lock_path.open("w", encoding="utf-8") as lock, \
                    mock.patch.object(duet, "_metrics_root", return_value=metrics_root), \
                    contextlib.redirect_stderr(io.StringIO()):
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    duet._write_metric_snapshot(snapshot)
                duet._persist_run_metrics(state, state_path)
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            self.assertFalse((metrics_root / "runs" / f"{snapshot_id}.json").exists())


class TestMetricsRefresh(unittest.TestCase):
    def test_explicit_refresh_is_idempotent_and_preserves_legacy_unknowns(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            metrics_root = root / "metrics"
            run = root / "old-run"
            run.mkdir()
            source = run / "state.json"
            legacy = {
                "cwd": str(root / "private-project"), "dry_run": False,
                "metrics_kind": "test", "phase": "finished", "history": [],
                "agents": [{"name": "old-lead", "backend": "claude", "role": "planner"},
                           {"name": "old-partner", "backend": "codex", "role": "coder"}],
            }
            original = json.dumps(legacy, sort_keys=True)
            source.write_text(original, encoding="utf-8")
            corrupt = root / "corrupt-run"
            corrupt.mkdir()
            (corrupt / "state.json").write_text("{", encoding="utf-8")
            with mock.patch.object(duet, "_metrics_root", return_value=metrics_root), \
                    mock.patch.object(duet.subprocess, "run") as run_command:
                first = duet.refresh_metrics(str(root))
                second = duet.refresh_metrics(str(root))

            self.assertEqual(first, {"imported": 1, "unchanged": 0, "skipped": 1})
            self.assertEqual(second, {"imported": 0, "unchanged": 1, "skipped": 1})
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            run_command.assert_not_called()
            snapshot_path = next((metrics_root / "runs").glob("*.json"))
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["source"], "legacy")
            self.assertEqual(snapshot["run_kind"], "test")
            self.assertIsNone(snapshot["duet_version"])
            self.assertIsNone(snapshot["agents"][0]["model_requested"])
            self.assertIsNone(snapshot["agents"][0]["cli_version"])
            self.assertIsNone(snapshot["agents"][0]["reasoning_effective"])

    def test_refresh_repairs_stale_snapshot_then_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            metrics_root = root / "metrics"
            run = root / "completed"
            run.mkdir()
            source = run / "state.json"
            snapshot_id = "00000000-0000-4000-8000-000000000003"
            completed = {
                "cwd": str(root / "private-project"), "dry_run": False,
                "metrics_enabled": True, "metrics_kind": "live",
                "metrics": {"id": snapshot_id, "project_id": "b" * 64,
                            "started_at": "2026-01-01T00:00:00+00:00",
                            "updated_at": "2026-01-01T00:01:00+00:00",
                            "wall_elapsed_s": 20.0, "agents": []},
                "duet_version": "0.2.12", "phase": "finished",
                "finished_reason": "max_turns", "history": [], "agents": [],
            }
            original = json.dumps(completed, sort_keys=True)
            source.write_text(original, encoding="utf-8")
            target = metrics_root / "runs" / f"{snapshot_id}.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({
                "schema_version": 1, "kind": "duet.metrics.run", "id": snapshot_id,
                "updated_at": completed["metrics"]["updated_at"],
                "wall_elapsed_s": 5.0,
            }), encoding="utf-8")
            with mock.patch.object(duet, "_metrics_root", return_value=metrics_root):
                repaired = duet.refresh_metrics(str(root))
                unchanged = duet.refresh_metrics(str(root))
            snapshot = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(repaired, {"imported": 1, "unchanged": 0, "skipped": 0})
            self.assertEqual(unchanged, {"imported": 0, "unchanged": 1, "skipped": 0})
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            self.assertEqual(snapshot["wall_elapsed_s"], 20.0)


if __name__ == "__main__":
    unittest.main()
