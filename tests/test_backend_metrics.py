"""Focused tests for curated per-turn backend metrics (no live CLIs)."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import unittest
from unittest import mock


_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import duet  # noqa: E402


class TestBackendMetrics(unittest.TestCase):
    def _cfg(self, agent: duet.Agent, **kwargs: object) -> duet.DuetConfig:
        partner = duet.Agent(name="partner", backend="claude", role="planner")
        kwargs.setdefault("metrics_enabled", True)
        return duet.DuetConfig(cwd=_ROOT, agents=[agent, partner], **kwargs)

    def test_codex_fast_profile_records_requested_and_effective_reasoning(self) -> None:
        agent = duet.Agent(name="coder", backend="codex", role="coder")
        cfg = self._cfg(agent, dry_run=True, reasoning="xhigh", codex_fast=True)

        duet.call_agent(agent, "partner says hello", cfg, first_turn_for_agent=True)

        metrics = agent.last_call_metrics
        self.assertEqual(metrics["reasoning_requested"], "xhigh")
        self.assertEqual(metrics["reasoning_effective"], "low")
        self.assertEqual(metrics["reasoning_backend_value"], "low")
        self.assertEqual(metrics["reasoning_transport"], "config_flag")
        self.assertTrue(metrics["fast_mode"])
        self.assertEqual(metrics["call_mode"], "fresh")
        self.assertEqual(metrics["partner_message_bytes"], len("partner says hello".encode()))
        self.assertGreater(metrics["system_prompt_bytes"], 0)

    def test_native_reasoning_default_is_kept_unknown(self) -> None:
        agent = duet.Agent(name="planner", backend="claude", role="planner")
        cfg = self._cfg(agent, dry_run=True)

        profile = duet.metrics_agent_profile(agent, cfg)

        self.assertIsNone(profile["reasoning_requested"])
        self.assertIsNone(profile["reasoning_effective"])
        self.assertIsNone(profile["reasoning_backend_value"])
        self.assertEqual(profile["reasoning_transport"], "native_default")
        self.assertFalse(profile["fast_mode"])

    def test_gemini_only_reports_prompt_control_for_high_nudges(self) -> None:
        agent = duet.Agent(name="gemini", backend="gemini", role="planner")

        low = duet.metrics_agent_profile(agent, self._cfg(agent, reasoning="low"))
        high = duet.metrics_agent_profile(agent, self._cfg(agent, reasoning="high"))

        self.assertEqual(low["reasoning_requested"], "low")
        self.assertIsNone(low["reasoning_effective"])
        self.assertEqual(low["reasoning_transport"], "native_default")
        self.assertEqual(high["reasoning_effective"], "high")
        self.assertEqual(high["reasoning_transport"], "prompt")

    def test_profile_marks_unpersisted_extra_args_without_exposing_them(self) -> None:
        agent = duet.Agent(name="coder", backend="codex", extra_args=["-c", "x=y"])
        profile = duet.metrics_agent_profile(agent, self._cfg(agent))

        self.assertTrue(profile["extra_args_present"])
        self.assertNotIn("extra_args", profile)

    def test_claude_invocation_model_usage_aggregates_without_model_guess(self) -> None:
        payload = {
            "result": "ok",
            "session_id": "sid",
            "modelUsage": {
                "claude-haiku-4-5": {
                    "inputTokens": 11,
                    "outputTokens": 7,
                    "cacheReadInputTokens": 3,
                    "cacheCreationInputTokens": 0,
                },
                "claude-sonnet-4-6": {
                    "inputTokens": 5,
                    "outputTokens": 2,
                    "cacheReadInputTokens": 1,
                    "cacheCreationInputTokens": 4,
                },
            },
            "total_cost_usd": 0.0125,
        }
        agent = duet.Agent(name="claude", backend="claude")

        with mock.patch.object(duet, "_run", return_value=(0, json.dumps(payload), "")):
            text, sid = duet.call_claude(
                agent, "system", "message", _ROOT, "acceptEdits", 60, dry=False,
            )

        self.assertEqual((text, sid), ("ok", "sid"))
        self.assertIsNone(agent.last_call_metrics["model_reported"])
        self.assertEqual(agent.last_call_metrics["models_reported"], [
            "claude-haiku-4-5", "claude-sonnet-4-6",
        ])
        self.assertEqual(agent.last_call_metrics["usage"], {
            "input_tokens": 16,
            "output_tokens": 9,
            "cache_read_input_tokens": 4,
            "cache_creation_input_tokens": 4,
        })
        self.assertEqual(agent.last_call_metrics["usage_scope"], "invocation")
        self.assertEqual(agent.last_call_metrics["cost_usd"], 0.0125)
        self.assertEqual(agent.last_call_metrics["raw_output_bytes"], len(json.dumps(payload).encode()))

    def test_gemini_stats_split_cache_and_preserve_single_model(self) -> None:
        agent = duet.Agent(name="gemini", backend="gemini")
        duet._metrics_record_gemini_metadata(agent, {
            "stats": {"models": {"gemini-3.5-flash": {"tokens": {
                "prompt": 100, "cached": 70, "candidates": 9, "thoughts": 6,
            }}}},
        })

        self.assertEqual(agent.last_call_metrics["model_reported"], "gemini-3.5-flash")
        self.assertEqual(agent.last_call_metrics["usage"], {
            "input_tokens": 30,
            "cache_read_input_tokens": 70,
            "output_tokens": 9,
            "reasoning_tokens": 6,
        })
        self.assertEqual(agent.last_call_metrics["usage_scope"], "invocation")

    def test_opencode_step_finish_sums_this_invocation(self) -> None:
        agent = duet.Agent(name="opencode", backend="opencode")
        stream = "\n".join([
            json.dumps({"type": "step_finish", "modelID": "opencode/big-pickle", "part": {
                "type": "step-finish", "cost": 0.1,
                "tokens": {"input": 8, "output": 2, "reasoning": 1,
                           "cache": {"read": 3, "write": 1}},
            }}),
            json.dumps({"type": "step_finish", "modelID": "opencode/big-pickle", "part": {
                "type": "step-finish", "cost": 0.2,
                "tokens": {"input": 5, "output": 4, "reasoning": 3,
                           "cache": {"read": 7, "write": 0}},
            }}),
        ])

        duet._metrics_record_opencode_metadata(agent, stream)

        self.assertEqual(agent.last_call_metrics["model_reported"], "opencode/big-pickle")
        self.assertEqual(agent.last_call_metrics["usage"], {
            "input_tokens": 13,
            "output_tokens": 6,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 1,
            "reasoning_tokens": 4,
        })
        self.assertAlmostEqual(agent.last_call_metrics["cost_usd"], 0.3)

    def test_corrupt_or_private_optional_telemetry_is_ignored(self) -> None:
        agent = duet.Agent(name="claude", backend="claude")
        duet._metrics_record_claude_metadata(agent, {
            "modelUsage": {"https://example.test/secret": {
                "inputTokens": True, "outputTokens": -1,
            }},
            "total_cost_usd": float("nan"),
        })

        self.assertNotIn("model_reported", agent.last_call_metrics)
        self.assertNotIn("cost_usd", agent.last_call_metrics)
        self.assertNotIn("usage", agent.last_call_metrics)
        self.assertIsNone(duet._metrics_identifier("person@example.test"))
        self.assertIsNone(duet._metrics_identifier("model\nsecret"))

    def test_incomplete_native_entries_remain_unknown_per_field(self) -> None:
        claude = duet.Agent(name="claude", backend="claude")
        duet._metrics_record_claude_metadata(claude, {"modelUsage": {
            "claude-sonnet": {"inputTokens": 1, "outputTokens": 2,
                              "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0},
            "claude-haiku": {"inputTokens": 3, "cacheReadInputTokens": 0,
                              "cacheCreationInputTokens": 0},
        }})
        self.assertEqual(claude.last_call_metrics["usage"]["input_tokens"], 4)
        self.assertNotIn("output_tokens", claude.last_call_metrics["usage"])

        gemini = duet.Agent(name="gemini", backend="gemini")
        duet._metrics_record_gemini_metadata(gemini, {"stats": {"models": {
            "gemini": {"tokens": {"prompt": 10, "candidates": 2}},
        }}})
        self.assertNotIn("input_tokens", gemini.last_call_metrics["usage"])
        self.assertNotIn("cache_read_input_tokens", gemini.last_call_metrics["usage"])

        opencode = duet.Agent(name="opencode", backend="opencode")
        duet._metrics_record_opencode_metadata(opencode, "\n".join([
            json.dumps({"type": "step_finish", "part": {"type": "step-finish",
                        "cost": 1, "tokens": {"input": 1, "output": 2,
                        "cache": {"read": 0, "write": 0}}}}),
            json.dumps({"type": "step_finish", "part": {"type": "step-finish",
                        "tokens": {"input": 3, "output": 4,
                        "cache": {"read": 0, "write": 0}}}}),
        ]))
        self.assertEqual(opencode.last_call_metrics["usage"]["input_tokens"], 4)
        self.assertNotIn("cost_usd", opencode.last_call_metrics)

    def test_version_probe_is_cached_and_skipped_for_dry_run(self) -> None:
        agent = duet.Agent(name="codex", backend="codex")
        cfg = self._cfg(agent, dry_run=False)
        duet._METRICS_CLI_VERSION_CACHE.clear()

        reader, writer = os.pipe()
        os.write(writer, b"codex-cli 1.2.3\n")
        os.close(writer)

        class FakeProc:
            pid = 999999
            stdout = os.fdopen(reader, "rb")

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        with mock.patch.object(duet.subprocess, "Popen", return_value=FakeProc()) as popen:
            self.assertEqual(duet.metrics_agent_profile(agent, cfg, probe_versions=True)["cli_version"], "1.2.3")
            self.assertEqual(duet.metrics_agent_profile(agent, cfg, probe_versions=True)["cli_version"], "1.2.3")
        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["codex", "--version"])
        self.assertTrue(kwargs["start_new_session"])

        dry_profile = duet.metrics_agent_profile(agent, self._cfg(agent, dry_run=True), probe_versions=True)
        self.assertIsNone(dry_profile["cli_version"])

        duet._METRICS_CLI_VERSION_CACHE.clear()
        disabled = self._cfg(agent, dry_run=False)
        disabled.metrics_enabled = False
        with mock.patch.object(duet.subprocess, "Popen") as popen:
            self.assertIsNone(duet.metrics_agent_profile(
                agent, disabled, probe_versions=True,
            )["cli_version"])
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
