"""Unit tests for duet.py pure functions.

These tests cover the correctness-critical helpers that `scripts/smoke.sh`
can't observe through exit codes alone: convergence detection, codex/copilot
session-id parsing, recap header parsing, file-path heuristics, reasoning
mappings, partner-spec parsing, markdown-fence sizing, age formatting, and
bounded agent-error transcript formatting. They are pure-function tests — no
subprocesses, no filesystem writes, no agent CLIs.

Run via:
    python3 -m unittest discover -s tests
or (alongside the smoke suite):
    make test
"""
from __future__ import annotations

import contextlib
import io
import pathlib
import re
import subprocess
import sys
import unittest
from unittest import mock

# Make `import duet` work without installing — duet.py lives in the repo
# root, this file in tests/.
_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import duet  # noqa: E402  (import after sys.path tweak)


SENTINEL = duet.DEFAULT_SENTINEL  # "<<<LGTM>>>"


# ---------- _convergence_markers / convergence_proposed ----------


class TestConvergenceMarkers(unittest.TestCase):
    """`_convergence_markers(text, sentinel) -> (sentinel_seen, rationale_seen)`.

    `convergence_proposed` returns True iff both are True.
    """

    def assertConverged(self, text: str) -> None:
        self.assertTrue(duet.convergence_proposed(text, SENTINEL),
                        msg=f"expected converged for: {text!r}")

    def assertNotConverged(self, text: str) -> None:
        self.assertFalse(duet.convergence_proposed(text, SENTINEL),
                         msg=f"expected NOT converged for: {text!r}")

    def test_happy_path_rationale_then_sentinel(self) -> None:
        text = (
            "LGTM rationale: tests pass and the implementation is solid.\n"
            f"{SENTINEL}\n"
        )
        self.assertConverged(text)

    def test_rationale_alternate_label(self) -> None:
        # "Rationale:" without "LGTM" prefix is also accepted.
        text = (
            "Rationale: code is reviewed, tests cover the new path.\n"
            f"{SENTINEL}\n"
        )
        self.assertConverged(text)

    def test_rationale_with_bold_and_bullet(self) -> None:
        text = (
            "- **LGTM rationale**: behavior preserved, tests pass cleanly.\n"
            f"{SENTINEL}\n"
        )
        self.assertConverged(text)

    def test_bare_sentinel_does_not_converge(self) -> None:
        # No rationale → not converged. Pair-agreement is the contract.
        self.assertNotConverged(f"{SENTINEL}\n")

    def test_rationale_only_does_not_converge(self) -> None:
        # No sentinel → not converged.
        self.assertNotConverged(
            "LGTM rationale: everything looks good and tests pass.\n"
        )

    def test_short_rationale_does_not_converge(self) -> None:
        # Rationale shorter than CONVERGENCE_RATIONALE_MIN_CHARS (20) is
        # treated as missing.
        self.assertLess(len("ok done"), duet.CONVERGENCE_RATIONALE_MIN_CHARS)
        text = f"LGTM rationale: ok done\n{SENTINEL}\n"
        self.assertNotConverged(text)

    def test_sentinel_inside_backtick_fence_ignored(self) -> None:
        text = (
            "Working on it.\n"
            "```python\n"
            f"{SENTINEL}\n"
            "```\n"
            "LGTM rationale: not really, this was just an example block.\n"
        )
        # Sentinel is inside ``` fence, so sentinel_seen=False.
        self.assertNotConverged(text)

    def test_sentinel_inside_tilde_fence_ignored(self) -> None:
        text = (
            "Quoting the sentinel as text:\n"
            "~~~\n"
            f"{SENTINEL}\n"
            "~~~\n"
            "LGTM rationale: example shown above does not auto-converge.\n"
        )
        self.assertNotConverged(text)

    def test_outer_longer_fence_contains_inner_fence(self) -> None:
        # Outer ```` opens, inner ``` is content (length < outer), sentinel
        # in the middle stays inside the outer fence.
        text = (
            "````markdown\n"
            "Example reply:\n"
            "```\n"
            f"{SENTINEL}\n"
            "```\n"
            "````\n"
            "LGTM rationale: showing nested fence handling, not a real ack.\n"
        )
        self.assertNotConverged(text)

    def test_indented_sentinel_still_counts(self) -> None:
        # `^\s*<<<LGTM>>>\s*$` allows leading whitespace.
        text = (
            "LGTM rationale: tests pass, no findings remain unaddressed.\n"
            f"    {SENTINEL}\n"
        )
        self.assertConverged(text)

    def test_inline_quoted_sentinel_does_not_count(self) -> None:
        # Sentinel embedded in a sentence is not on its own line.
        text = (
            f"The sentinel is `{SENTINEL}`; we won't emit it yet.\n"
            "LGTM rationale: nope, more work to do before convergence.\n"
        )
        self.assertNotConverged(text)

    def test_multiline_rationale_reaches_min_chars(self) -> None:
        # First line is short on its own; continuation accumulates.
        text = (
            "LGTM rationale: short.\n"
            "Continuing the rationale across multiple lines now.\n"
            f"{SENTINEL}\n"
        )
        # Combined: "short. Continuing the rationale across multiple lines now."
        # → well above 20 chars.
        self.assertConverged(text)

    def test_paragraph_separated_rationale_accumulates(self) -> None:
        # A blank line between rationale lines must not stop collection: each
        # part is short on its own but accumulates past the min-chars threshold.
        # Agents routinely write rationale as separate paragraphs.
        text = (
            "LGTM rationale: first part.\n"
            "\n"
            "second part adds more.\n"
            f"{SENTINEL}\n"
        )
        self.assertLess(len("first part."), duet.CONVERGENCE_RATIONALE_MIN_CHARS)
        self.assertConverged(text)

    def test_multiple_sentinels_still_converge(self) -> None:
        # An agent that repeats the sentinel (for emphasis, or by accident)
        # after a valid rationale still converges — the first sentinel sets the
        # flag and extra ones are harmless.
        text = (
            "LGTM rationale: tests pass and the change is complete.\n"
            f"{SENTINEL}\n"
            "Thanks — shipping it.\n"
            f"{SENTINEL}\n"
        )
        self.assertConverged(text)

    def test_sentinel_before_rationale_does_not_converge(self) -> None:
        # Lines after the sentinel are ignored, so rationale appearing AFTER
        # the sentinel doesn't get collected. This matches the documented
        # ordering: rationale must be emitted before the sentinel.
        text = (
            f"{SENTINEL}\n"
            "LGTM rationale: tests pass and everything is fine here.\n"
        )
        self.assertNotConverged(text)

    def test_empty_text(self) -> None:
        self.assertNotConverged("")
        sentinel_seen, rationale_seen = duet._convergence_markers("", SENTINEL)
        self.assertFalse(sentinel_seen)
        self.assertFalse(rationale_seen)

    def test_markers_returns_components(self) -> None:
        # Verifies the tuple shape and component independence.
        s, r = duet._convergence_markers(f"{SENTINEL}\n", SENTINEL)
        self.assertEqual((s, r), (True, False))

        s, r = duet._convergence_markers(
            "LGTM rationale: long enough rationale to clear the threshold.\n",
            SENTINEL,
        )
        self.assertEqual((s, r), (False, True))


# ---------- verification helpers ----------


class TestVerifyHelpers(unittest.TestCase):
    def test_effective_verify_cwd_prefers_worktree(self) -> None:
        cfg = duet.DuetConfig(
            cwd=pathlib.Path("/host"),
            agents=[
                duet.Agent(name="a", backend="claude"),
                duet.Agent(name="b", backend="codex"),
            ],
        )
        self.assertEqual(
            duet.effective_verify_cwd(cfg, pathlib.Path("/host/runs/1/wt")),
            pathlib.Path("/host/runs/1/wt"),
        )

    def test_effective_verify_cwd_falls_back_to_cfg_cwd(self) -> None:
        cfg = duet.DuetConfig(
            cwd=pathlib.Path("/host"),
            agents=[
                duet.Agent(name="a", backend="claude"),
                duet.Agent(name="b", backend="codex"),
            ],
        )
        self.assertEqual(duet.effective_verify_cwd(cfg, None), pathlib.Path("/host"))

    def test_tail_text_caps_from_the_end(self) -> None:
        text = "a" * (duet.VERIFY_OUTPUT_TAIL_CHARS + 25) + "TAIL"
        out = duet._tail_text(text)
        self.assertIn("output truncated", out)
        self.assertTrue(out.endswith("TAIL"))
        self.assertNotIn("a" * (duet.VERIFY_OUTPUT_TAIL_CHARS + 1), out)

    def test_failure_block_includes_metadata_and_tails(self) -> None:
        result = duet.VerifyResult(
            ok=False,
            cmd="make test",
            cwd=pathlib.Path("/repo/wt"),
            exit_code=2,
            stdout_tail="stdout tail",
            stderr_tail="stderr tail",
            log_path=pathlib.Path("/repo/runs/1/turn-01-verify.log"),
        )
        block = duet.format_verify_failure_block(result)
        self.assertIn("[duet verify failed]", block)
        self.assertIn("command: make test", block)
        self.assertIn("cwd: /repo/wt", block)
        self.assertIn("exit_code: 2", block)
        self.assertIn("stdout tail", block)
        self.assertIn("stderr tail", block)
        self.assertIn("[/duet verify failed]", block)


# ---------- agent finish reasons ----------


class TestAgentFailureTranscript(unittest.TestCase):
    def test_small_error_is_preserved_with_stderr_link(self) -> None:
        log_path = pathlib.Path("/repo/runs/1/turn-01-codex.stderr.log")
        block = duet.format_agent_error_for_transcript(
            RuntimeError("backend failed"), log_path
        )

        self.assertIn("[duet] error: backend failed", block)
        self.assertIn(f"[duet] stderr log: {log_path}", block)
        self.assertNotIn("characters omitted", block)

    def test_large_error_keeps_head_and_tail_with_omitted_count(self) -> None:
        text = "HEAD-" + ("x" * 2000) + "-TAIL"
        excerpt = duet._bounded_agent_error_excerpt(text, max_chars=240)

        self.assertLessEqual(len(excerpt), 240)
        self.assertTrue(excerpt.startswith("HEAD-"))
        self.assertTrue(excerpt.endswith("-TAIL"))
        match = re.search(r"(\d+) characters omitted", excerpt)
        self.assertIsNotNone(match)
        self.assertGreater(int(match.group(1)), 0)
        self.assertNotIn("x" * 500, excerpt)


class TestAgentFinishReasons(unittest.TestCase):
    def test_codex_rc_124_maps_to_timeout(self) -> None:
        def fake_run(cmd, **kwargs):
            return 124, "", "[duet] TIMEOUT after 1s"

        agent = duet.Agent(name="codex-partner", backend="codex", role="coder")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_codex(
                    agent, "sys", "msg", _ROOT, "workspace-write", 1,
                    dry=False, first_turn=True,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_TIMEOUT)
        self.assertIn("codex exited 124", str(ctx.exception))

    def test_subprocess_timeout_exception_maps_to_timeout(self) -> None:
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 1)

        agent = duet.Agent(name="claude-lead", backend="claude", role="planner")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_claude(
                    agent, "sys", "msg", _ROOT, "acceptEdits", 1,
                    dry=False,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_TIMEOUT)
        self.assertIn("claude timed out", str(ctx.exception))

    def test_nonzero_agent_exit_maps_to_agent_error(self) -> None:
        def fake_run(cmd, **kwargs):
            return 2, "", "backend failed"

        agent = duet.Agent(name="claude-lead", backend="claude", role="planner")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_claude(
                    agent, "sys", "msg", _ROOT, "acceptEdits", 60,
                    dry=False,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_AGENT_ERROR)
        self.assertIn("backend failed", str(ctx.exception))

    def test_malformed_claude_json_maps_to_agent_error(self) -> None:
        def fake_run(cmd, **kwargs):
            return 0, "not json", ""

        agent = duet.Agent(name="claude-lead", backend="claude", role="planner")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_claude(
                    agent, "sys", "msg", _ROOT, "acceptEdits", 60,
                    dry=False,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_AGENT_ERROR)
        self.assertIn("malformed JSON", str(ctx.exception))

    def test_gemini_missing_session_id_maps_to_agent_error(self) -> None:
        def fake_run(cmd, **kwargs):
            return 0, '{"response":"ok"}', ""

        agent = duet.Agent(name="gemini-partner", backend="gemini", role="coder")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_gemini(
                    agent, "sys", "msg", _ROOT, "acceptEdits", 60,
                    dry=False, first_turn=True,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_AGENT_ERROR)
        self.assertIn("session_id", str(ctx.exception))

    def test_gemini_error_payload_precedes_missing_session_id(self) -> None:
        def fake_run(cmd, **kwargs):
            return 0, '{"error":"auth failed"}', ""

        agent = duet.Agent(name="gemini-partner", backend="gemini", role="coder")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_gemini(
                    agent, "sys", "msg", _ROOT, "acceptEdits", 60,
                    dry=False, first_turn=True,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_AGENT_ERROR)
        self.assertIn("gemini returned error: auth failed", str(ctx.exception))
        self.assertNotIn("session_id", str(ctx.exception))

    def test_malformed_gemini_json_maps_to_agent_error(self) -> None:
        def fake_run(cmd, **kwargs):
            return 0, "not json", ""

        agent = duet.Agent(name="gemini-partner", backend="gemini", role="coder")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_gemini(
                    agent, "sys", "msg", _ROOT, "acceptEdits", 60,
                    dry=False, first_turn=True,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_AGENT_ERROR)
        self.assertIn("malformed JSON", str(ctx.exception))

    def test_copilot_missing_session_id_maps_to_agent_error(self) -> None:
        def fake_run(cmd, **kwargs):
            return (
                0,
                '{"type":"assistant.message","data":{"content":"ok"}}\n',
                "",
            )

        agent = duet.Agent(name="copilot-partner", backend="copilot", role="coder")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_copilot(
                    agent, "sys", "msg", _ROOT, 60,
                    dry=False, first_turn=True,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_AGENT_ERROR)
        self.assertIn("sessionId", str(ctx.exception))

    def test_malformed_copilot_jsonl_maps_to_agent_error(self) -> None:
        def fake_run(cmd, **kwargs):
            return 0, "not json\n", ""

        agent = duet.Agent(name="copilot-partner", backend="copilot", role="coder")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_copilot(
                    agent, "sys", "msg", _ROOT, 60,
                    dry=False, first_turn=True,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_AGENT_ERROR)
        self.assertIn("malformed JSONL", str(ctx.exception))

    def test_copilot_result_exit_code_maps_to_agent_error(self) -> None:
        def fake_run(cmd, **kwargs):
            return (
                0,
                "\n".join([
                    '{"type":"assistant.message","data":{"content":"failed"}}',
                    '{"type":"result","sessionId":"sid","exitCode":2}',
                ]),
                "",
            )

        agent = duet.Agent(name="copilot-partner", backend="copilot", role="coder")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_copilot(
                    agent, "sys", "msg", _ROOT, 60,
                    dry=False, first_turn=True,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_AGENT_ERROR)
        self.assertIn("exitCode=2", str(ctx.exception))

    def test_copilot_rc_124_maps_to_timeout(self) -> None:
        def fake_run(cmd, **kwargs):
            return 124, "", "[duet] TIMEOUT after 1s"

        agent = duet.Agent(name="copilot-partner", backend="copilot", role="coder")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_copilot(
                    agent, "sys", "msg", _ROOT, 1,
                    dry=False, first_turn=True,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_TIMEOUT)
        self.assertIn("copilot exited 124", str(ctx.exception))

    def test_copilot_nonzero_rc_maps_to_agent_error(self) -> None:
        def fake_run(cmd, **kwargs):
            return 2, "", "backend failed"

        agent = duet.Agent(name="copilot-partner", backend="copilot", role="coder")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_copilot(
                    agent, "sys", "msg", _ROOT, 60,
                    dry=False, first_turn=True,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_AGENT_ERROR)
        self.assertIn("copilot exited 2", str(ctx.exception))

    def test_opencode_error_event_maps_to_agent_error(self) -> None:
        # `opencode run` exits 0 on a model error, so call_opencode must catch
        # the error event in the JSONL stream and not forward a broken reply.
        def fake_run(cmd, **kwargs):
            return (
                0,
                '{"type":"error","sessionID":"ses_x","error":{"data":{"message":"boom"}}}',
                "",
            )

        agent = duet.Agent(name="opencode-partner", backend="opencode", role="coder")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_opencode(
                    agent, "sys", "msg", _ROOT, 60,
                    dry=False, first_turn=True,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_AGENT_ERROR)
        self.assertIn("boom", str(ctx.exception))

    def test_opencode_missing_session_id_maps_to_agent_error(self) -> None:
        def fake_run(cmd, **kwargs):
            return (
                0,
                '{"type":"text","part":{"type":"text","text":"ok","id":"p1"}}',
                "",
            )

        agent = duet.Agent(name="opencode-partner", backend="opencode", role="coder")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_opencode(
                    agent, "sys", "msg", _ROOT, 60,
                    dry=False, first_turn=True,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_AGENT_ERROR)
        self.assertIn("sessionID", str(ctx.exception))

    def test_opencode_rc_124_maps_to_timeout(self) -> None:
        def fake_run(cmd, **kwargs):
            return 124, "", "[duet] TIMEOUT after 1s"

        agent = duet.Agent(name="opencode-partner", backend="opencode", role="coder")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_opencode(
                    agent, "sys", "msg", _ROOT, 1,
                    dry=False, first_turn=True,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_TIMEOUT)
        self.assertIn("opencode exited 124", str(ctx.exception))

    def test_opencode_error_event_precedes_missing_session_id(self) -> None:
        # An error event with no sessionID must surface the error text, not the
        # generic missing-sessionID message — locks the check ordering in
        # call_opencode (mirrors the Gemini precedence test).
        def fake_run(cmd, **kwargs):
            return (
                0,
                '{"type":"error","error":{"data":{"message":"boom"}}}',
                "",
            )

        agent = duet.Agent(name="opencode-partner", backend="opencode", role="coder")
        with mock.patch.object(duet, "_run", fake_run):
            with self.assertRaises(duet.AgentRunError) as ctx:
                duet.call_opencode(
                    agent, "sys", "msg", _ROOT, 60,
                    dry=False, first_turn=True,
                )

        self.assertEqual(ctx.exception.finished_reason, duet.FINISHED_AGENT_ERROR)
        self.assertIn("boom", str(ctx.exception))
        self.assertNotIn("sessionID", str(ctx.exception))

    def test_opencode_command_construction_and_resume(self) -> None:
        # Capture the constructed argv to pin the flags the reasoning-check and
        # dry-run exit codes can't see: the `-s` resume flag, whose silent loss
        # would orphan multi-turn memory while still returning rc=0, and the
        # `-m provider/model` form OpenCode requires for model selection.
        captured: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            captured.append(list(cmd))
            return (
                0,
                '{"type":"text","sessionID":"ses_new","part":{"type":"text","text":"ok","id":"p1"}}',
                "",
            )

        agent = duet.Agent(
            name="opencode-partner",
            backend="opencode",
            role="coder",
            model="anthropic/claude-sonnet-4-6",
        )
        with mock.patch.object(duet, "_run", fake_run):
            # First turn: no session_id yet.
            duet.call_opencode(agent, "sys", "msg", _ROOT, 60,
                               dry=False, first_turn=True)
            first = captured[-1]
            self.assertEqual(first[:2], ["opencode", "run"])
            self.assertIn("--format", first)
            self.assertEqual(first[first.index("--format") + 1], "json")
            self.assertIn("--dir", first)
            self.assertIn("--dangerously-skip-permissions", first)
            self.assertIn("-m", first)
            self.assertEqual(first[first.index("-m") + 1], "anthropic/claude-sonnet-4-6")
            self.assertNotIn("-s", first)  # nothing to resume on turn 1
            # The prompt is the trailing positional arg (options come first).
            self.assertIn("=== MESSAGE FROM PARTNER ===", first[-1])
            self.assertTrue(first[-1].rstrip().endswith("msg"))

            # Second turn: resume by the parsed session id.
            agent.session_id = "ses_prev"
            duet.call_opencode(agent, "sys", "msg2", _ROOT, 60,
                               dry=False, first_turn=False)
            second = captured[-1]
            self.assertIn("-s", second)
            self.assertEqual(second[second.index("-s") + 1], "ses_prev")


# ---------- _parse_codex_session_id ----------


class TestParseCodexSessionId(unittest.TestCase):
    UUID = "12345678-1234-1234-1234-123456789abc"

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(duet._parse_codex_session_id(""))
        self.assertIsNone(duet._parse_codex_session_id("\n"))

    def test_basic_session_id_label(self) -> None:
        self.assertEqual(
            duet._parse_codex_session_id(f"session id: {self.UUID}\n"),
            self.UUID,
        )

    def test_session_id_underscore_equals(self) -> None:
        self.assertEqual(
            duet._parse_codex_session_id(f"session_id={self.UUID}"),
            self.UUID,
        )

    def test_session_id_hyphen(self) -> None:
        self.assertEqual(
            duet._parse_codex_session_id(f"session-id: {self.UUID}"),
            self.UUID,
        )

    def test_session_id_no_separator(self) -> None:
        # `session[ _-]?id` allows the separator char to be absent.
        self.assertEqual(
            duet._parse_codex_session_id(f"sessionid: {self.UUID}"),
            self.UUID,
        )

    def test_case_insensitive_label_lowercases_uuid(self) -> None:
        upper_uuid = self.UUID.upper()
        result = duet._parse_codex_session_id(f"Session ID: {upper_uuid}")
        self.assertEqual(result, self.UUID)  # lowercased

    def test_stray_uuid_without_label_rejected(self) -> None:
        # Naked UUID in a traceback or path must not be picked up.
        self.assertIsNone(
            duet._parse_codex_session_id(f"see {self.UUID} for details")
        )

    def test_inline_session_id_label_rejected(self) -> None:
        self.assertIsNone(
            duet._parse_codex_session_id(f"trace: session id: {self.UUID}\n")
        )

    def test_first_line_start_match_wins(self) -> None:
        first = "11111111-1111-1111-1111-111111111111"
        second = "22222222-2222-2222-2222-222222222222"
        stderr = (
            f"session id: {first}\n"
            "...\n"
            f"session id: {second}\n"
        )
        self.assertEqual(duet._parse_codex_session_id(stderr), first)

    def test_existing_uuid_pin_not_replaced_by_different_parse(self) -> None:
        existing = "11111111-1111-1111-1111-111111111111"
        parsed = "22222222-2222-2222-2222-222222222222"
        agent = duet.Agent(
            name="codex-partner",
            backend="codex",
            role="coder",
            session_id=existing,
        )
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(duet._resolve_codex_session_pin(agent, parsed), existing)

    def test_uuid_re_pattern_round_trip(self) -> None:
        # Sanity: _CODEX_UUID_RE matches a parsed UUID; rejects the
        # "codex-current" sentinel that legacy continue-mode plants.
        self.assertIsNotNone(duet._CODEX_UUID_RE.match(self.UUID))
        self.assertIsNone(duet._CODEX_UUID_RE.match("codex-current"))


# ---------- _parse_copilot_jsonl ----------


class TestParseCopilotJsonl(unittest.TestCase):
    def test_extracts_last_assistant_message_and_result_session_id(self) -> None:
        out = "\n".join([
            '{"type":"assistant.message","data":{"content":"draft"}}',
            '{"type":"assistant.message","data":{"content":"final"}}',
            '{"type":"result","sessionId":"copilot-session-123","exitCode":0}',
        ])

        text, session_id, exit_code = duet._parse_copilot_jsonl(out)

        self.assertEqual(text, "final")
        self.assertEqual(session_id, "copilot-session-123")
        self.assertEqual(exit_code, 0)

    def test_extracts_text_from_list_content(self) -> None:
        out = "\n".join([
            '{"type":"assistant.message","data":{"content":[{"text":"hello"},{"content":" world"}]}}',
            '{"type":"result","sessionId":"sid","exitCode":0}',
        ])

        text, session_id, exit_code = duet._parse_copilot_jsonl(out)

        self.assertEqual(text, "hello world")
        self.assertEqual(session_id, "sid")
        self.assertEqual(exit_code, 0)

    def test_empty_trailing_content_does_not_overwrite_reply(self) -> None:
        # Real Copilot streams can emit an empty assistant.message before and/or
        # after the substantive one; the `if text:` guard must keep the last
        # NON-EMPTY reply rather than letting a blank event clobber it.
        out = "\n".join([
            '{"type":"assistant.message","data":{"content":""}}',
            '{"type":"assistant.message","data":{"content":"the real answer"}}',
            '{"type":"assistant.message","data":{"content":[]}}',
            '{"type":"result","sessionId":"sid","exitCode":0}',
        ])

        text, session_id, exit_code = duet._parse_copilot_jsonl(out)

        self.assertEqual(text, "the real answer")
        self.assertEqual(session_id, "sid")
        self.assertEqual(exit_code, 0)

    def test_empty_returns_no_session(self) -> None:
        self.assertEqual(duet._parse_copilot_jsonl(""), ("", None, None))

    def test_malformed_line_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            duet._parse_copilot_jsonl("not json")


# ---------- _parse_opencode_jsonl ----------


class TestParseOpencodeJsonl(unittest.TestCase):
    def test_extracts_text_and_session_id(self) -> None:
        out = "\n".join([
            '{"type":"step_start","sessionID":"ses_abc","part":{"type":"step-start"}}',
            '{"type":"text","sessionID":"ses_abc","part":{"type":"text","text":"PONG","id":"p1"}}',
            '{"type":"step_finish","sessionID":"ses_abc","part":{"type":"step-finish"}}',
        ])

        text, session_id, error = duet._parse_opencode_jsonl(out)

        self.assertEqual(text, "PONG")
        self.assertEqual(session_id, "ses_abc")
        self.assertIsNone(error)

    def test_concatenates_multiple_distinct_text_parts(self) -> None:
        # Text split around a tool call arrives as two parts with distinct ids;
        # both are kept, in arrival order.
        out = "\n".join([
            '{"type":"text","sessionID":"ses_x","part":{"type":"text","text":"Let me check.","id":"p1"}}',
            '{"type":"text","sessionID":"ses_x","part":{"type":"text","text":"Done.","id":"p2"}}',
        ])

        text, session_id, error = duet._parse_opencode_jsonl(out)

        self.assertEqual(text, "Let me check.\nDone.")
        self.assertEqual(session_id, "ses_x")
        self.assertIsNone(error)

    def test_streamed_part_last_write_wins(self) -> None:
        # A part re-emitted as it grows (same id) must not duplicate; last wins.
        out = "\n".join([
            '{"type":"text","sessionID":"ses_x","part":{"type":"text","text":"PO","id":"p1"}}',
            '{"type":"text","sessionID":"ses_x","part":{"type":"text","text":"PONG","id":"p1"}}',
        ])

        text, _, _ = duet._parse_opencode_jsonl(out)

        self.assertEqual(text, "PONG")

    def test_error_event_is_surfaced(self) -> None:
        # `opencode run` exits 0 even on model errors; the failure is an error
        # event in the stream, so the parser must report it.
        out = ('{"type":"error","sessionID":"ses_x","error":{"name":"UnknownError",'
               '"data":{"message":"Model not found: foo."}}}')

        text, session_id, error = duet._parse_opencode_jsonl(out)

        self.assertEqual(text, "")
        self.assertEqual(session_id, "ses_x")
        self.assertEqual(error, "Model not found: foo.")

    def test_non_json_banner_lines_are_skipped(self) -> None:
        # A fresh machine can print a one-time DB-migration banner; tolerate it
        # rather than treating the whole turn as malformed.
        out = "\n".join([
            "Performing one time database migration...",
            '{"type":"text","sessionID":"ses_x","part":{"type":"text","text":"ok","id":"p1"}}',
        ])

        text, session_id, error = duet._parse_opencode_jsonl(out)

        self.assertEqual(text, "ok")
        self.assertEqual(session_id, "ses_x")
        self.assertIsNone(error)

    def test_string_error_payload_is_surfaced(self) -> None:
        # OpenCode documents a dict error payload, but a plain-string one must
        # surface verbatim rather than degrading to "unknown error".
        out = '{"type":"error","sessionID":"ses_x","error":"Model not found: foo"}'

        text, session_id, error = duet._parse_opencode_jsonl(out)

        self.assertEqual(text, "")
        self.assertEqual(session_id, "ses_x")
        self.assertEqual(error, "Model not found: foo")

    def test_idless_part_does_not_alias_real_id(self) -> None:
        # An id-less text part must not collide with a real part whose id is the
        # bare integer the old `len(parts)` fallback would have produced ("0").
        # Both texts must survive, in arrival order.
        out = "\n".join([
            '{"type":"text","sessionID":"ses_x","part":{"type":"text","text":"A"}}',
            '{"type":"text","sessionID":"ses_x","part":{"type":"text","text":"B","id":"0"}}',
        ])

        text, _, _ = duet._parse_opencode_jsonl(out)

        self.assertEqual(text, "A\nB")

    def test_empty_returns_no_session(self) -> None:
        self.assertEqual(duet._parse_opencode_jsonl(""), ("", None, None))


# ---------- _parse_gemini_session_id ----------


class TestParseGeminiSessionId(unittest.TestCase):
    def test_extracts_session_id_from_json_stdout(self) -> None:
        stdout = '{"response":"ok","session_id":"gemini-session-123"}'
        self.assertEqual(
            duet._parse_gemini_session_id(stdout),
            "gemini-session-123",
        )

    def test_missing_or_malformed_returns_none(self) -> None:
        self.assertIsNone(duet._parse_gemini_session_id(""))
        self.assertIsNone(duet._parse_gemini_session_id('{"response":"ok"}'))
        self.assertIsNone(duet._parse_gemini_session_id("not json"))


# ---------- parse_recap_headers ----------


class TestParseRecapHeaders(unittest.TestCase):
    def test_all_three_headers(self) -> None:
        text = (
            "RECAP: did the thing\n"
            "FILES: a.py, b.md\n"
            "STATUS: implementing\n"
            "\n"
            "body...\n"
        )
        self.assertEqual(
            duet.parse_recap_headers(text),
            {"recap": "did the thing", "files": "a.py, b.md",
             "status": "implementing"},
        )

    def test_unknown_status_returns_none(self) -> None:
        text = "RECAP: ok\nSTATUS: bogus_value\n"
        parsed = duet.parse_recap_headers(text)
        self.assertEqual(parsed["recap"], "ok")
        self.assertIsNone(parsed["status"])

    def test_missing_headers_remain_none(self) -> None:
        self.assertEqual(
            duet.parse_recap_headers(""),
            {"recap": None, "files": None, "status": None},
        )

    def test_empty_value_becomes_none(self) -> None:
        # `value or None` collapses empty strings.
        text = "RECAP:   \nFILES: foo.py\n"
        parsed = duet.parse_recap_headers(text)
        self.assertIsNone(parsed["recap"])
        self.assertEqual(parsed["files"], "foo.py")

    def test_only_first_ten_lines_scanned(self) -> None:
        # Pad with 10 non-header lines, then a header on line 11 — must be
        # ignored.
        text = "\n".join([f"line{i}" for i in range(10)]) + "\nRECAP: late\n"
        self.assertIsNone(duet.parse_recap_headers(text)["recap"])

    def test_partial_headers_some_present_some_absent(self) -> None:
        # A real turn often emits RECAP and FILES but omits the optional
        # STATUS line. Present headers parse; the absent one stays None.
        text = "RECAP: did the thing\nFILES: a.py\n\nbody...\n"
        self.assertEqual(
            duet.parse_recap_headers(text),
            {"recap": "did the thing", "files": "a.py", "status": None},
        )

    def test_lowercase_header_does_not_match(self) -> None:
        # Regex is uppercase-only; this is the documented format.
        self.assertIsNone(
            duet.parse_recap_headers("recap: lower\n")["recap"]
        )

    def test_leading_whitespace_blocks_match(self) -> None:
        # `^(RECAP|FILES|STATUS):` requires the header at column 0.
        self.assertIsNone(
            duet.parse_recap_headers("  RECAP: indented\n")["recap"]
        )

    def test_all_status_enum_values_accepted(self) -> None:
        for status in [
            "planning", "implementing", "reviewing",
            "requesting-changes", "ready-for-review", "converged",
        ]:
            with self.subTest(status=status):
                parsed = duet.parse_recap_headers(f"STATUS: {status}\n")
                self.assertEqual(parsed["status"], status)


# ---------- extract_files_heuristic ----------


class TestExtractFilesHeuristic(unittest.TestCase):
    def test_simple_extension_match(self) -> None:
        self.assertEqual(
            duet.extract_files_heuristic("edited foo.py and bar.md."),
            ["foo.py", "bar.md"],
        )

    def test_dedup_repeated_paths(self) -> None:
        self.assertEqual(
            duet.extract_files_heuristic("edited foo.py twice; foo.py again"),
            ["foo.py"],
        )

    def test_backticked_paths_listed_first(self) -> None:
        # The backticked-codes pass runs before the full-text pass, so
        # paths inside `…` appear earlier in the result even when they
        # come later in the original text.
        text = "edited foo.py and `bar.md`"
        self.assertEqual(
            duet.extract_files_heuristic(text),
            ["bar.md", "foo.py"],
        )

    def test_same_file_in_backticks_and_plain_dedups(self) -> None:
        # The same path appearing both in backticks and in plain prose is
        # emitted once — the backtick pass adds it, the plain pass dedups.
        self.assertEqual(
            duet.extract_files_heuristic("see `foo.py` then foo.py again"),
            ["foo.py"],
        )

    def test_path_with_directory_separator(self) -> None:
        self.assertEqual(
            duet.extract_files_heuristic("touched scripts/smoke.sh"),
            ["scripts/smoke.sh"],
        )

    def test_cap_at_eight(self) -> None:
        text = " ".join(f"file{i}.py" for i in range(12))
        result = duet.extract_files_heuristic(text)
        self.assertEqual(len(result), 8)
        self.assertEqual(result, [f"file{i}.py" for i in range(8)])

    def test_unknown_extension_ignored(self) -> None:
        # `.xyz` not in the regex's allowed extensions.
        self.assertEqual(
            duet.extract_files_heuristic("see thing.xyz now"),
            [],
        )

    def test_empty_text(self) -> None:
        self.assertEqual(duet.extract_files_heuristic(""), [])


# ---------- reasoning helpers ----------


class TestReasoningHelpers(unittest.TestCase):
    def test_validate_accepts_known_levels(self) -> None:
        for level in duet.REASONING_LEVELS:
            with self.subTest(level=level):
                duet.validate_reasoning(level, "test")  # no raise

    def test_validate_accepts_none(self) -> None:
        duet.validate_reasoning(None, "test")  # no raise

    def test_validate_rejects_unknown(self) -> None:
        with self.assertRaises(SystemExit):
            duet.validate_reasoning("ultra", "test")

    def test_effective_uses_agent_override(self) -> None:
        agent = duet.Agent(name="x", backend="claude", reasoning_effort="high")
        self.assertEqual(duet.effective_reasoning(agent, "low"), "high")

    def test_effective_falls_back_to_cfg(self) -> None:
        agent = duet.Agent(name="x", backend="claude")
        self.assertEqual(duet.effective_reasoning(agent, "low"), "low")

    def test_effective_returns_none_when_neither_set(self) -> None:
        agent = duet.Agent(name="x", backend="claude")
        self.assertIsNone(duet.effective_reasoning(agent, None))

    def test_reasoning_maps_cover_all_levels(self) -> None:
        # Guards against a duet-level addition that forgets the per-backend
        # mapping. `scripts/check_reasoning_levels.py` covers the cmd-emission
        # side; this is the data-coverage half.
        for level in duet.REASONING_LEVELS:
            with self.subTest(level=level):
                self.assertIn(level, duet.CLAUDE_REASONING_MAP)
                self.assertIn(level, duet.CODEX_REASONING_MAP)
                self.assertIn(level, duet.GEMINI_REASONING_MAP)
                self.assertIn(level, duet.COPILOT_REASONING_MAP)
                self.assertIn(level, duet.OPENCODE_REASONING_MAP)
                self.assertIn(level, duet.CLAUDE_REASONING_PROMPT_PREFIX)
                self.assertIn(level, duet.GEMINI_REASONING_PROMPT_PREFIX)
                self.assertIn(level, duet.COPILOT_REASONING_PROMPT_PREFIX)
                self.assertIn(level, duet.OPENCODE_REASONING_PROMPT_PREFIX)

    def test_codex_max_maps_to_xhigh(self) -> None:
        # Codex documents `xhigh` but not `max`; duet keeps `max` as a
        # backend-normalized alias for the highest Codex effort.
        self.assertEqual(duet.CODEX_REASONING_MAP["max"], "xhigh")

    def test_xhigh_maps_to_xhigh_for_both_backends(self) -> None:
        self.assertEqual(duet.CLAUDE_REASONING_MAP["xhigh"], "xhigh")
        self.assertEqual(duet.CODEX_REASONING_MAP["xhigh"], "xhigh")

    def test_claude_minimal_maps_to_low(self) -> None:
        # Claude has no `minimal`; we route it to `low`.
        self.assertEqual(duet.CLAUDE_REASONING_MAP["minimal"], "low")

    def test_copilot_minimal_maps_to_none(self) -> None:
        # Copilot names its lowest documented effort level `none`.
        self.assertEqual(duet.COPILOT_REASONING_MAP["minimal"], "none")

    def test_gemini_reasoning_map_emits_no_effort_value(self) -> None:
        for level in duet.REASONING_LEVELS:
            with self.subTest(level=level):
                self.assertEqual(duet.GEMINI_REASONING_MAP[level], "")

    def test_opencode_reasoning_map_is_identity(self) -> None:
        # OpenCode tolerates/ignores unknown `--variant` values, so duet passes
        # each level through unchanged for forward-compatibility.
        for level in duet.REASONING_LEVELS:
            with self.subTest(level=level):
                self.assertEqual(duet.OPENCODE_REASONING_MAP[level], level)


# ---------- parse_partner ----------


class TestParsePartner(unittest.TestCase):
    def test_backend_with_role(self) -> None:
        agent = duet.parse_partner("codex:planner")
        self.assertEqual(agent.backend, "codex")
        self.assertEqual(agent.role, "planner")
        self.assertEqual(agent.name, "codex-planner")

    def test_backend_only_uses_default_role(self) -> None:
        agent = duet.parse_partner("codex")
        self.assertEqual(agent.backend, "codex")
        self.assertEqual(agent.role, "coder")  # default
        self.assertEqual(agent.name, "codex-coder")

    def test_explicit_default_role(self) -> None:
        agent = duet.parse_partner("claude", default_role="planner")
        self.assertEqual(agent.role, "planner")
        self.assertEqual(agent.name, "claude-planner")

    def test_empty_backend_raises(self) -> None:
        with self.assertRaises(SystemExit):
            duet.parse_partner(":coder")
        with self.assertRaises(SystemExit):
            duet.parse_partner("")

    def test_gemini_backend_with_role(self) -> None:
        agent = duet.parse_partner("gemini:coder")
        self.assertEqual(agent.backend, "gemini")
        self.assertEqual(agent.role, "coder")
        self.assertEqual(agent.name, "gemini-coder")

    def test_copilot_backend_with_role(self) -> None:
        agent = duet.parse_partner("copilot:coder")
        self.assertEqual(agent.backend, "copilot")
        self.assertEqual(agent.role, "coder")
        self.assertEqual(agent.name, "copilot-coder")


# ---------- _build_cfg_from_cli ----------


class TestBuildCfgFromCli(unittest.TestCase):
    def _parse(self, *argv: str):
        parser = duet._build_arg_parser()
        return parser, parser.parse_args(list(argv))

    def test_lead_and_partner_models_attach_to_cli_agents(self) -> None:
        parser, args = self._parse(
            "--task", "x",
            "--lead", "claude:reviewer",
            "--partner", "codex:coder",
            "--lead-model", "claude-opus-4-5",
            "--partner-model", "gpt-5",
        )

        cfg = duet._build_cfg_from_cli(args, parser, {})

        self.assertEqual(cfg.agents[0].backend, "claude")
        self.assertEqual(cfg.agents[0].role, "reviewer")
        self.assertEqual(cfg.agents[0].model, "claude-opus-4-5")
        self.assertEqual(cfg.agents[1].backend, "codex")
        self.assertEqual(cfg.agents[1].role, "coder")
        self.assertEqual(cfg.agents[1].model, "gpt-5")

    def test_empty_cli_model_flags_normalize_to_none(self) -> None:
        parser, args = self._parse(
            "--task", "x",
            "--lead-model", "",
            "--partner-model", "",
        )

        cfg = duet._build_cfg_from_cli(args, parser, {})

        self.assertIsNone(cfg.agents[0].model)
        self.assertIsNone(cfg.agents[1].model)


# ---------- apply_resume_overrides ----------


class TestApplyResumeOverrides(unittest.TestCase):
    def test_resume_codex_partner_gets_session(self) -> None:
        agents = duet.apply_resume_overrides(
            [
                duet.Agent(name="claude-lead", backend="claude", role="planner"),
                duet.Agent(name="codex-partner", backend="codex", role="coder"),
            ],
            resume_codex="codex-sid",
            rename_slots=True,
        )

        self.assertEqual(agents[0].name, "claude-lead")
        self.assertIsNone(agents[0].session_id)
        self.assertEqual(agents[1].name, "codex-partner")
        self.assertEqual(agents[1].session_id, "codex-sid")

    def test_resume_codex_preserves_explicit_codex_lead(self) -> None:
        agents = duet.apply_resume_overrides(
            [
                duet.Agent(name="codex-planner", backend="codex", role="planner"),
                duet.Agent(name="codex-reviewer", backend="codex", role="reviewer"),
            ],
            resume_codex="codex-sid",
            rename_slots=True,
        )

        self.assertEqual(agents[0].name, "codex-lead")
        self.assertEqual(agents[0].role, "planner")
        self.assertIsNone(agents[0].session_id)
        self.assertEqual(agents[1].name, "codex-partner")
        self.assertEqual(agents[1].role, "reviewer")
        self.assertEqual(agents[1].session_id, "codex-sid")

    def test_resume_codex_lead_moves_to_partner(self) -> None:
        agents = duet.apply_resume_overrides(
            [
                duet.Agent(name="codex-planner", backend="codex", role="planner"),
                duet.Agent(name="claude-coder", backend="claude", role="coder"),
            ],
            resume_codex="codex-sid",
            rename_slots=True,
        )

        self.assertEqual(agents[0].name, "claude-lead")
        self.assertEqual(agents[0].role, "planner")
        self.assertIsNone(agents[0].session_id)
        self.assertEqual(agents[1].name, "codex-partner")
        self.assertEqual(agents[1].role, "coder")
        self.assertEqual(agents[1].session_id, "codex-sid")

    def test_resume_codex_move_preserves_model(self) -> None:
        agents = duet.apply_resume_overrides(
            [
                duet.Agent(
                    name="codex-planner",
                    backend="codex",
                    role="planner",
                    model="gpt-5",
                ),
                duet.Agent(
                    name="claude-coder",
                    backend="claude",
                    role="coder",
                    model="claude-sonnet-4-6",
                ),
            ],
            resume_codex="codex-sid",
            rename_slots=True,
        )

        self.assertEqual(agents[0].backend, "claude")
        self.assertEqual(agents[0].model, "claude-sonnet-4-6")
        self.assertEqual(agents[1].backend, "codex")
        self.assertEqual(agents[1].session_id, "codex-sid")
        self.assertEqual(agents[1].model, "gpt-5")

    def test_resume_claude_partner_moves_to_lead(self) -> None:
        agents = duet.apply_resume_overrides(
            [
                duet.Agent(name="codex-planner", backend="codex", role="planner"),
                duet.Agent(name="claude-coder", backend="claude", role="coder"),
            ],
            resume_claude="claude-sid",
            rename_slots=True,
        )

        self.assertEqual(agents[0].name, "claude-lead")
        self.assertEqual(agents[0].backend, "claude")
        self.assertEqual(agents[0].role, "planner")
        self.assertEqual(agents[0].session_id, "claude-sid")
        self.assertEqual(agents[1].name, "codex-partner")
        self.assertEqual(agents[1].role, "coder")

    def test_resume_claude_preserves_explicit_claude_partner(self) -> None:
        agents = duet.apply_resume_overrides(
            [
                duet.Agent(name="claude-planner", backend="claude", role="planner"),
                duet.Agent(name="claude-reviewer", backend="claude",
                           role="reviewer"),
            ],
            resume_claude="claude-sid",
            rename_slots=True,
        )

        self.assertEqual(agents[0].name, "claude-lead")
        self.assertEqual(agents[0].role, "planner")
        self.assertEqual(agents[0].session_id, "claude-sid")
        self.assertEqual(agents[1].name, "claude-partner")
        self.assertEqual(agents[1].role, "reviewer")
        self.assertIsNone(agents[1].session_id)

    def test_resume_both_yields_claude_lead_and_codex_partner(self) -> None:
        agents = duet.apply_resume_overrides(
            [
                duet.Agent(name="codex-planner", backend="codex", role="planner"),
                duet.Agent(name="claude-coder", backend="claude", role="coder"),
            ],
            resume_claude="claude-sid",
            resume_codex="codex-sid",
            rename_slots=True,
        )

        self.assertEqual([(a.backend, a.session_id) for a in agents],
                         [("claude", "claude-sid"), ("codex", "codex-sid")])


# ---------- validate_config / Codex isolation guards ----------


class TestConfigValidation(unittest.TestCase):
    def _cfg(self, agents: list[duet.Agent]) -> duet.DuetConfig:
        return duet.DuetConfig(cwd=_ROOT, agents=agents, task="x")

    def test_same_backend_unique_names_are_valid(self) -> None:
        cfg = self._cfg([
            duet.Agent(name="codex-lead", backend="codex", role="planner"),
            duet.Agent(name="codex-partner", backend="codex", role="coder"),
        ])
        duet.validate_config(cfg)

    def test_duplicate_agent_names_fail(self) -> None:
        cfg = self._cfg([
            duet.Agent(name="codex-peer", backend="codex", role="planner"),
            duet.Agent(name="codex-peer", backend="codex", role="coder"),
        ])
        with self.assertRaises(SystemExit):
            duet.validate_config(cfg)

    def test_unknown_backend_fails(self) -> None:
        cfg = self._cfg([
            duet.Agent(name="claude-lead", backend="claude", role="planner"),
            duet.Agent(name="other-partner", backend="other", role="coder"),
        ])
        with self.assertRaises(SystemExit):
            duet.validate_config(cfg)

    def test_invalid_worktree_for_fails(self) -> None:
        cfg = self._cfg([
            duet.Agent(name="claude-lead", backend="claude", role="planner"),
            duet.Agent(name="codex-partner", backend="codex", role="coder"),
        ])
        cfg.worktree_for = "nonexistent-agent"
        with self.assertRaises(SystemExit):
            duet.validate_config(cfg)


class TestCwdKeyedResumeGuards(unittest.TestCase):
    def test_missing_uuid_after_first_turn_fails_for_shared_cwd(self) -> None:
        lead = duet.Agent(name="codex-lead", backend="codex", role="planner")
        partner = duet.Agent(name="codex-partner", backend="codex", role="coder")
        cfg = duet.DuetConfig(cwd=_ROOT, agents=[lead, partner], task="x")
        partner.session_id = "codex-current"

        with self.assertRaises(SystemExit):
            duet.guard_cwd_keyed_resume_after_call(
                cfg, partner, first_turn_for_agent=True
            )

    def test_missing_uuid_allowed_when_codex_peers_have_different_cwds(self) -> None:
        lead = duet.Agent(name="codex-lead", backend="codex", role="planner")
        partner = duet.Agent(
            name="codex-partner",
            backend="codex",
            role="coder",
            cwd_override=_ROOT.parent,
        )
        cfg = duet.DuetConfig(cwd=_ROOT, agents=[lead, partner], task="x")
        partner.session_id = "codex-current"

        duet.guard_cwd_keyed_resume_after_call(
            cfg, partner, first_turn_for_agent=True
        )

    def test_legacy_resume_marker_fails_before_call_for_shared_cwd(self) -> None:
        lead = duet.Agent(name="codex-lead", backend="codex", role="planner")
        partner = duet.Agent(
            name="codex-partner",
            backend="codex",
            role="coder",
            session_id="codex-current",
        )
        cfg = duet.DuetConfig(cwd=_ROOT, agents=[lead, partner], task="x")

        with self.assertRaises(SystemExit):
            duet.guard_cwd_keyed_resume_before_call(
                cfg, partner, first_turn_for_agent=False
            )


# ---------- _markdown_fence ----------


class TestMarkdownFence(unittest.TestCase):
    def test_minimum_three_backticks(self) -> None:
        self.assertEqual(duet._markdown_fence(""), "```")
        self.assertEqual(duet._markdown_fence("no backticks here"), "```")

    def test_one_or_two_backticks_still_three(self) -> None:
        self.assertEqual(duet._markdown_fence("a `b` c"), "```")
        self.assertEqual(duet._markdown_fence("a ``b`` c"), "```")

    def test_three_backticks_grow_fence_to_four(self) -> None:
        self.assertEqual(duet._markdown_fence("``` python ```"), "````")

    def test_four_backticks_grow_fence_to_five(self) -> None:
        self.assertEqual(duet._markdown_fence("````"), "`````")

    def test_picks_longest_run(self) -> None:
        # Mixed runs: 1, 2, 3 backticks → fence is 4.
        text = "` and `` and ```"
        self.assertEqual(duet._markdown_fence(text), "````")

    def test_tildes_do_not_grow_fence(self) -> None:
        # `_markdown_fence` only counts backticks; tildes in content are
        # safe to wrap with `````.
        self.assertEqual(duet._markdown_fence("~~~~~~~~~~"), "```")


# ---------- _humanize_age ----------


class TestHumanizeAge(unittest.TestCase):
    def test_seconds_bucket(self) -> None:
        self.assertEqual(duet._humanize_age(0), "0s ago")
        self.assertEqual(duet._humanize_age(59), "59s ago")

    def test_minutes_bucket(self) -> None:
        self.assertEqual(duet._humanize_age(60), "1m ago")
        self.assertEqual(duet._humanize_age(3599), "59m ago")

    def test_hours_bucket(self) -> None:
        self.assertEqual(duet._humanize_age(3600), "1h ago")
        self.assertEqual(duet._humanize_age(86399), "23h ago")

    def test_days_bucket_within_week(self) -> None:
        self.assertEqual(duet._humanize_age(86400), "1d ago")
        self.assertEqual(duet._humanize_age(6 * 86400), "6d ago")

    def test_days_bucket_beyond_week(self) -> None:
        # The fall-through branch is functionally identical to the
        # within-week branch; both report days. Test both paths.
        self.assertEqual(duet._humanize_age(7 * 86400), "7d ago")
        self.assertEqual(duet._humanize_age(30 * 86400), "30d ago")


# ---------- derive_status_heuristic ----------


class TestDeriveStatusHeuristic(unittest.TestCase):
    def test_sentinel_overrides_role(self) -> None:
        self.assertEqual(
            duet.derive_status_heuristic("planner", True), "converged"
        )
        self.assertEqual(
            duet.derive_status_heuristic("coder", True), "converged"
        )

    def test_role_to_status(self) -> None:
        self.assertEqual(
            duet.derive_status_heuristic("planner", False), "planning"
        )
        self.assertEqual(
            duet.derive_status_heuristic("coder", False), "implementing"
        )
        self.assertEqual(
            duet.derive_status_heuristic("reviewer", False), "reviewing"
        )
        self.assertEqual(
            duet.derive_status_heuristic("triage-reviewer", False), "reviewing"
        )

    def test_unknown_role_falls_through(self) -> None:
        self.assertEqual(
            duet.derive_status_heuristic("custom", False), "unknown"
        )

    def test_sentinel_overrides_custom_role(self) -> None:
        # The sentinel short-circuits before any role check, so even a role
        # with no mapping reports converged when the sentinel fired.
        self.assertEqual(
            duet.derive_status_heuristic("custom", True), "converged"
        )


# ---------- _next_speaker_idx_from_state (continue mode) ----------


class TestNextSpeakerIdx(unittest.TestCase):
    def _agents(self) -> list:
        return [
            duet.Agent(name="claude-lead", backend="claude", role="planner"),
            duet.Agent(name="codex-partner", backend="codex", role="coder"),
        ]

    def test_next_is_the_other_agent_from_last_history_turn(self) -> None:
        # Last speaker was the partner → the lead speaks next, and vice versa.
        agents = self._agents()
        self.assertEqual(
            duet._next_speaker_idx_from_state(
                agents, {"history": [{"agent": "codex-partner"}]}), 0)
        self.assertEqual(
            duet._next_speaker_idx_from_state(
                agents, {"history": [{"agent": "claude-lead"}]}), 1)

    def test_falls_back_to_turn_parity_without_history(self) -> None:
        # No history: partner (idx 1) leads, so an even turn count means the
        # partner is up next, an odd count means the lead.
        agents = self._agents()
        self.assertEqual(
            duet._next_speaker_idx_from_state(agents, {"turns_used": 0}), 1)
        self.assertEqual(
            duet._next_speaker_idx_from_state(agents, {"turns_used": 3}), 0)

    def test_malformed_turns_used_defaults_to_partner(self) -> None:
        agents = self._agents()
        self.assertEqual(
            duet._next_speaker_idx_from_state(agents, {"turns_used": "x"}), 1)


# ---------- _resolve_opt_path ----------


class TestResolveOptPath(unittest.TestCase):
    def test_none_when_all_empty(self) -> None:
        self.assertIsNone(duet._resolve_opt_path())
        self.assertIsNone(duet._resolve_opt_path(None, "", None))

    def test_first_truthy_wins(self) -> None:
        # CLI flag (first arg) takes precedence over a config-file value.
        result = duet._resolve_opt_path("/tmp/cli", "/tmp/cfg")
        self.assertEqual(result, pathlib.Path("/tmp/cli").expanduser().resolve())

    def test_falls_back_to_later_candidate(self) -> None:
        result = duet._resolve_opt_path(None, "/tmp/cfg")
        self.assertEqual(result, pathlib.Path("/tmp/cfg").expanduser().resolve())

    def test_returns_absolute_resolved_path(self) -> None:
        result = duet._resolve_opt_path("relative/dir")
        self.assertTrue(result.is_absolute())


# ---------- _format_byte_size ----------


class TestFormatByteSize(unittest.TestCase):
    def test_bytes_below_one_kib(self) -> None:
        self.assertEqual(duet._format_byte_size(0), "0B")
        self.assertEqual(duet._format_byte_size(1023), "1023B")

    def test_kib_threshold_and_rounding(self) -> None:
        self.assertEqual(duet._format_byte_size(1024), "1.0KB")
        self.assertEqual(duet._format_byte_size(1536), "1.5KB")


# ---------- normalize_verify_cmd ----------


class TestNormalizeVerifyCmd(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = duet.argparse.ArgumentParser()

    def test_none_passes_through(self) -> None:
        self.assertIsNone(duet.normalize_verify_cmd(None, self.parser))

    def test_strips_surrounding_whitespace(self) -> None:
        self.assertEqual(
            duet.normalize_verify_cmd("  make test  ", self.parser), "make test")

    def test_empty_string_errors(self) -> None:
        # parser.error prints usage to stderr before exiting; swallow it so the
        # test log stays clean.
        with self.assertRaises(SystemExit), \
                contextlib.redirect_stderr(io.StringIO()):
            duet.normalize_verify_cmd("   ", self.parser)

    def test_non_string_errors(self) -> None:
        with self.assertRaises(SystemExit), \
                contextlib.redirect_stderr(io.StringIO()):
            duet.normalize_verify_cmd(123, self.parser)


if __name__ == "__main__":
    unittest.main()
