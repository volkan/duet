"""Unit tests for duet.py pure functions.

These tests cover the correctness-critical helpers that `scripts/smoke.sh`
can't observe through exit codes alone: convergence detection, codex
session-id parsing, recap header parsing, file-path heuristics, reasoning
mappings, partner-spec parsing, markdown-fence sizing, and the age
formatter. They are pure-function tests — no subprocesses, no filesystem
writes, no agent CLIs.

Run via:
    python3 -m unittest discover -s tests
or (alongside the smoke suite):
    make test
"""
from __future__ import annotations

import pathlib
import sys
import unittest

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

    def test_last_match_wins(self) -> None:
        first = "11111111-1111-1111-1111-111111111111"
        second = "22222222-2222-2222-2222-222222222222"
        stderr = (
            f"session id: {first}\n"
            "...\n"
            f"session id: {second}\n"
        )
        self.assertEqual(duet._parse_codex_session_id(stderr), second)

    def test_uuid_re_pattern_round_trip(self) -> None:
        # Sanity: _CODEX_UUID_RE matches a parsed UUID; rejects the
        # "codex-current" sentinel that legacy continue-mode plants.
        self.assertIsNotNone(duet._CODEX_UUID_RE.match(self.UUID))
        self.assertIsNone(duet._CODEX_UUID_RE.match("codex-current"))


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
                self.assertIn(level, duet.CLAUDE_REASONING_PROMPT_PREFIX)

    def test_codex_max_maps_to_xhigh(self) -> None:
        # Documented: duet's `max` maps to Codex's `xhigh` (its actual top).
        self.assertEqual(duet.CODEX_REASONING_MAP["max"], "xhigh")

    def test_claude_minimal_maps_to_low(self) -> None:
        # Claude has no `minimal`; we route it to `low`.
        self.assertEqual(duet.CLAUDE_REASONING_MAP["minimal"], "low")


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


class TestCodexSharedCwdGuards(unittest.TestCase):
    def test_missing_uuid_after_first_turn_fails_for_shared_cwd(self) -> None:
        lead = duet.Agent(name="codex-lead", backend="codex", role="planner")
        partner = duet.Agent(name="codex-partner", backend="codex", role="coder")
        cfg = duet.DuetConfig(cwd=_ROOT, agents=[lead, partner], task="x")
        partner.session_id = "codex-current"

        with self.assertRaises(SystemExit):
            duet.guard_codex_shared_cwd_after_call(
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

        duet.guard_codex_shared_cwd_after_call(
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
            duet.guard_codex_shared_cwd_before_call(
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


if __name__ == "__main__":
    unittest.main()
