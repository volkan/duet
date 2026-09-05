"""CLI/config contracts for central metrics controls."""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import duet  # noqa: E402


class TestMetricsCliConfig(unittest.TestCase):
    def _cfg_from_cli(self, *argv: str) -> duet.DuetConfig:
        parser = duet._build_arg_parser()
        args = parser.parse_args(list(argv))
        return duet._build_cfg_from_cli(args, parser, {})

    def test_cli_flags_and_environment_default(self) -> None:
        cfg = self._cfg_from_cli("--no-metrics", "--metrics-kind", "test", "--task", "x")
        self.assertFalse(cfg.metrics_enabled)
        self.assertEqual(cfg.metrics_kind, "test")
        with mock.patch.dict(os.environ, {"DUET_METRICS": "0"}):
            disabled = self._cfg_from_cli("--task", "x")
        self.assertFalse(disabled.metrics_enabled)

    def test_yaml_values_and_cli_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = pathlib.Path(raw) / "duet.json"
            config.write_text(json.dumps({
                "agents": [
                    {"name": "lead", "backend": "claude", "role": "planner"},
                    {"name": "partner", "backend": "codex", "role": "coder"},
                ],
                "task": "x", "metrics_enabled": True, "metrics_kind": "test",
            }), encoding="utf-8")
            parser = duet._build_arg_parser()
            args = parser.parse_args(["--config", str(config), "--no-metrics",
                                      "--metrics-kind", "live"])
            cfg = duet._build_cfg_from_yaml(args, parser, {})
        self.assertFalse(cfg.metrics_enabled)
        self.assertEqual(cfg.metrics_kind, "live")

    def test_continue_restores_saved_metrics_and_cli_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run = pathlib.Path(raw) / "run"
            run.mkdir()
            state = {
                "cwd": raw, "agents": [
                    {"name": "lead", "backend": "claude", "role": "planner"},
                    {"name": "partner", "backend": "codex", "role": "coder"},
                ],
                "history": [], "turns_used": 0, "metrics_enabled": True,
                "metrics_kind": "test", "per_turn_timeout": 10,
            }
            (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
            parser = duet._build_arg_parser()
            args = parser.parse_args(["--continue", str(run), "--no-metrics",
                                      "--metrics-kind", "live"])
            cfg = duet.build_continue_config(str(run), args, parser, {})
        self.assertFalse(cfg.metrics_enabled)
        self.assertEqual(cfg.metrics_kind, "live")

    def test_environment_opt_out_wins_over_config_and_continued_state(self) -> None:
        for mode in ("--config", "--continue"):
            for enabled in ("0", "1"):
                with self.subTest(mode=mode, environment=enabled), \
                        tempfile.TemporaryDirectory() as raw:
                    root = pathlib.Path(raw)
                    source = root / "source"
                    source.mkdir()
                    state_path = source / "state.json"
                    state_path.write_text(json.dumps({
                        "cwd": raw, "task": "test metrics opt-out",
                        "agents": [
                            {"name": "lead", "backend": "claude", "role": "planner"},
                            {"name": "partner", "backend": "codex", "role": "coder"},
                        ],
                        "history": [], "turns_used": 0, "metrics_enabled": True,
                        "metrics_kind": "test", "per_turn_timeout": 10,
                    }), encoding="utf-8")
                    target = state_path if mode == "--config" else source
                    argv = ["duet", mode, str(target), "--dry-run", "--recap",
                            "--runs-dir", str(root / "raw-runs")]
                    with mock.patch.dict(os.environ, {"DUET_METRICS": enabled}), \
                            mock.patch.object(sys, "argv", argv), \
                            mock.patch.object(pathlib.Path, "home", return_value=root / "home"), \
                            mock.patch.object(duet, "_metrics_root", return_value=root / "central"), \
                            contextlib.redirect_stdout(io.StringIO()), \
                            contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(duet.main(), 0)
                    saved = next((root / "raw-runs").glob("*/state.json"))
                    self.assertEqual(json.loads(saved.read_text())["metrics_enabled"], enabled == "1")
                    snapshots = list((root / "central" / "runs").glob("*.json"))
                    self.assertEqual(len(snapshots), int(enabled))

    def test_direct_config_validation_and_old_namespace(self) -> None:
        agents = [duet.Agent("lead", "claude", "planner"),
                  duet.Agent("partner", "codex", "coder")]
        invalid = duet.DuetConfig(cwd=_ROOT, agents=agents, metrics_enabled="yes")
        with self.assertRaises(SystemExit):
            duet.validate_config(invalid)
        cfg = duet.DuetConfig(cwd=_ROOT, agents=agents)
        old_args = type("Old", (), {})()
        duet._apply_metrics_options(cfg, old_args)
        self.assertIsInstance(cfg.metrics_enabled, bool)
        self.assertEqual(cfg.metrics_kind, "live")


class TestMetricsReadOnlyCli(unittest.TestCase):
    def _parse(self, *argv: str):
        parser = duet._build_arg_parser()
        return parser, parser.parse_args(list(argv))

    def test_stats_json_is_read_only_and_refresh_keeps_stdout_json_pure(self) -> None:
        parser, args = self._parse("--stats", "--refresh", "--json")
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(duet, "refresh_metrics", return_value={
                "imported": 2, "unchanged": 1, "skipped": 3}), \
                mock.patch.object(duet, "print_metrics_report", side_effect=lambda **_: print('{"kind":"duet.metrics.report"}') or 0), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = duet._maybe_print_metrics(args, parser)
        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), '{"kind":"duet.metrics.report"}\n')
        self.assertIn("2 imported, 1 unchanged, 3 skipped", stderr.getvalue())

    def test_stats_rejects_control_or_launch_options(self) -> None:
        parser, args = self._parse("--stats", "--task", "x")
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            duet._maybe_print_metrics(args, parser)
        parser, args = self._parse("--refresh")
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            duet._maybe_print_metrics(args, parser)

    def test_json_requires_status_or_stats(self) -> None:
        with mock.patch.object(sys, "argv", ["duet", "--json"]), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                duet.main()


if __name__ == "__main__":
    unittest.main()
