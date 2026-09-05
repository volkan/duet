"""Contract tests for read-only central metrics aggregation."""
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


def _profile(slot: str, backend: str, role: str, model: str, reasoning: str) -> dict:
    return {
        "slot": slot, "backend": backend, "role": role,
        "model_requested": model, "cli_version": "1.2.3",
        "reasoning_requested": reasoning, "reasoning_effective": reasoning,
        "reasoning_backend_value": reasoning, "reasoning_transport": "flag",
        "fast_mode": False, "prompt_transport": "combined_prompt",
    }


def _turn(slot: str, **overrides) -> dict:
    turn = {
        "turn": 1, "kind": "loop", "slot": slot, "outcome": "ok",
        "agent_elapsed_s": 4.0, "verify_elapsed_s": None,
    }
    turn.update(overrides)
    return turn


def _record(number: int, **overrides) -> dict:
    record = {
        "schema_version": 1, "kind": "duet.metrics.run",
        "id": f"00000000-0000-4000-8000-{number:012d}", "source": "recorded",
        "duet_version": "0.2.12", "run_kind": "live", "project_id": "project",
        "started_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:01:00",
        "phase": "finished", "finished_reason": "converged", "max_turns": 4,
        "per_turn_timeout": 900, "wall_elapsed_s": 12.0,
        "agents": [
            _profile("lead", "claude", "planner", "sonnet", "high"),
            _profile("partner", "codex", "coder", "gpt-5", "low"),
        ],
        "turns": [_turn("lead")],
        "verification": {"attempts": 1, "passed": 1, "failed": 0},
    }
    record.update(overrides)
    return record


class TestMetricsReportAggregation(unittest.TestCase):
    def test_profiles_versions_and_partial_provider_coverage(self) -> None:
        lead = _turn("lead", model_reported="claude-sonnet", agent_elapsed_s=2.0,
                     usage={"input_tokens": 0, "output_tokens": 5, "reasoning_tokens": 2}, cost_usd=0.25,
                     usage_scope="invocation")
        partner = _turn("partner", turn=2, model_reported="gpt-5.1", agent_elapsed_s=None,
                        usage={"input_tokens": 7})
        second = _record(2, duet_version="0.3.0", turns=[partner],
                         verification={"attempts": 0, "passed": 0, "failed": 0})
        report = duet.build_metrics_report([_record(1, turns=[lead]), second])

        self.assertEqual(report["records"]["recorded_live"], 2)
        self.assertEqual(len(report["agent_groups"]), 2)
        claude = next(g for g in report["agent_groups"] if g["profile"]["backend"] == "claude")
        self.assertEqual(claude["profile"]["duet_version"], "0.2.12")
        self.assertEqual(claude["profile"]["model_reported"], "claude-sonnet")
        self.assertEqual(claude["provider_tokens"]["input_tokens"]["total"], 0)
        self.assertEqual(claude["provider_tokens"]["reasoning_tokens"]["total"], 2)
        self.assertEqual(claude["provider_tokens"]["input_tokens"]["reported_turns"], 1)
        self.assertEqual(claude["provider_tokens"]["cache_read_input_tokens"]["total"], None)
        codex = next(g for g in report["agent_groups"] if g["profile"]["backend"] == "codex")
        self.assertEqual(codex["timing"]["agent_elapsed_s"]["reported_turns"], 0)
        self.assertEqual(codex["reported_cost_usd"]["total"], None)
        self.assertNotIn("quality", report)
        self.assertNotIn("success", report)

    def test_nonperformance_sources_and_active_wall_time_are_separate(self) -> None:
        active = _record(2, phase="between_turns", finished_reason=None,
                         wall_elapsed_s=99.0, turns=[_turn("partner", turn=2)])
        active["agents"][1]["fast_mode"] = None
        dry = _record(3, run_kind="dry_run")
        legacy = _record(4, source="legacy", run_kind="unknown")
        test = _record(5, run_kind="test")
        report = duet.build_metrics_report([_record(1), active, dry, legacy, test])

        self.assertEqual(report["records"]["recorded_live"], 2)
        self.assertEqual(report["records"]["incomplete"], 1)
        self.assertEqual(report["records"]["dry_run"], 1)
        self.assertEqual(report["records"]["legacy"], 1)
        self.assertEqual(report["records"]["test"], 1)
        self.assertEqual(report["wall_elapsed_s"]["terminal_runs"], 1)
        self.assertEqual(report["wall_elapsed_s"]["median"], 12.0)
        self.assertEqual(sum(g["turn_count"] for g in report["agent_groups"]), 1)
        self.assertEqual(report["records"]["performance_records"], 1)
        self.assertEqual(sum(p["outcomes"].get("converged", 0) for p in report["pair_groups"]), 1)
        self.assertEqual(sum(p["outcomes"].get("incomplete", 0) for p in report["pair_groups"]), 0)

    def test_scope_and_turn_context_keep_usage_and_comparisons_separate(self) -> None:
        invocation = _turn("lead", usage_scope="invocation", call_mode="fresh",
                           usage={"input_tokens": 4}, cost_usd=0.2)
        session = _turn("lead", turn=2, usage_scope="session", call_mode="resume",
                        usage={"input_tokens": 99}, cost_usd=9.9)
        report = duet.build_metrics_report([
            _record(1, turns=[invocation]), _record(2, max_turns=8,
                    per_turn_timeout=1200, turns=[session]),
        ])

        self.assertEqual(len(report["agent_groups"]), 2)
        fresh = next(g for g in report["agent_groups"]
                     if g["profile"]["call_mode"] == "fresh")
        resumed = next(g for g in report["agent_groups"]
                       if g["profile"]["call_mode"] == "resume")
        self.assertEqual(fresh["provider_tokens"]["input_tokens"]["total"], 4)
        self.assertEqual(resumed["provider_tokens"]["input_tokens"]["total"], None)
        self.assertEqual(resumed["reported_cost_usd"]["total"], None)
        self.assertEqual(len(report["pair_groups"]), 2)
        self.assertEqual({p["profile"]["max_turns"] for p in report["pair_groups"]}, {4, 8})

    def test_models_reported_is_bounded_and_safe(self) -> None:
        turn = _turn("lead", model_reported=None,
                     models_reported=["claude-haiku", "claude-sonnet"])
        report = duet.build_metrics_report([_record(1, turns=[turn])])
        self.assertEqual(report["agent_groups"][0]["profile"]["models_reported"], [
            "claude-haiku", "claude-sonnet",
        ])
        bad = _record(2, turns=[_turn("lead", models_reported=["/private/model"])])
        self.assertEqual(duet.build_metrics_report([bad])["records"]["total"], 0)

    def test_rejects_invalid_numeric_telemetry_without_coercing_it(self) -> None:
        bad = _record(1, turns=[_turn("lead", agent_elapsed_s=-1)])
        boolean = _record(2, verification={"attempts": True, "passed": 0, "failed": 0})
        report = duet.build_metrics_report([bad, boolean])

        self.assertEqual(report["records"]["total"], 0)
        self.assertEqual(report["skipped"]["malformed"], 2)

    def test_rejects_unhashable_structural_values_without_crashing(self) -> None:
        records = []
        for path in ("source", "run_kind", "kind", "slot", "outcome", "usage_scope", "call_mode"):
            record = _record(len(records) + 1)
            target = record if path in {"source", "run_kind"} else record["turns"][0]
            target[path] = []
            records.append(record)

        report = duet.build_metrics_report(records)

        self.assertEqual(report["records"]["total"], 0)
        self.assertEqual(report["skipped"]["malformed"], len(records))


class TestMetricsReportStore(unittest.TestCase):
    def test_skips_malformed_schema_and_duplicate_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            runs = root / "runs"
            runs.mkdir()
            (runs / "one.json").write_text(json.dumps(_record(1)), encoding="utf-8")
            (runs / "duplicate.json").write_text(json.dumps(_record(1)), encoding="utf-8")
            (runs / "bad.json").write_text("{", encoding="utf-8")
            (runs / "old.json").write_text(json.dumps({"schema_version": 9}), encoding="utf-8")

            records, skipped = duet._metrics_report_load_records(root)
            report = duet.build_metrics_report(records, skipped)

        self.assertEqual(report["records"]["total"], 1)
        self.assertEqual(report["skipped"]["malformed"], 1)
        self.assertEqual(report["skipped"]["unknown_schema"], 1)
        self.assertEqual(report["skipped"]["duplicate_id"], 1)

    def test_empty_store_human_output_is_helpful_and_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.object(
                duet, "_metrics_root", return_value=pathlib.Path(raw)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                result = duet.print_metrics_report()
        self.assertEqual(result, 0)
        self.assertIn("no metrics snapshots", out.getvalue())

    def test_deeply_nested_snapshot_is_malformed_not_a_report_crash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            runs = root / "runs"
            runs.mkdir()
            (runs / "nested.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(duet.json, "loads", side_effect=RecursionError):
                records, skipped = duet._metrics_report_load_records(root)

        self.assertEqual(records, [])
        self.assertEqual(skipped["malformed"], 1)


if __name__ == "__main__":
    unittest.main()
