"""Finding-report lifecycle and human-feedback integration contracts."""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import shlex
import shutil
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import duet  # noqa: E402


def _assessment(identity="L1", disposition="unresolved") -> dict:
    return {"id": identity, "claim": "A partial batch is counted as full",
            "disposition": disposition, "evidence": ["toy.py:18; five items produce three batches"],
            "objection": "Need the partner to check the contract" if disposition == "unresolved" else None}


def _reply(disposition="unresolved", *, converged=False) -> str:
    block = "```duet-findings\n" + json.dumps({"findings": [_assessment(disposition=disposition)]}) + "\n```"
    if converged:
        block += "\nLGTM rationale: both sides inspected the bounded change and have no remaining blockers.\n<<<LGTM>>>"
    return block


class TestFindingWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.metrics = self.root / "central"
        for patcher in (
            mock.patch.object(pathlib.Path, "home", return_value=self.root / "home"),
            mock.patch.object(duet, "_metrics_root", return_value=self.metrics),
            mock.patch.object(duet, "_metrics_cli_version", return_value="1.2.3"),
            mock.patch.dict(os.environ, {"DUET_METRICS": "1"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _cfg(self, **overrides) -> duet.DuetConfig:
        values = dict(cwd=self.root, runs_dir=self.root / "runs", task="Review the toy",
                      agents=[duet.Agent("lead", "claude", "reviewer"),
                              duet.Agent("partner", "codex", "coder")],
                      finding_reports=True, metrics_enabled=False, max_turns=2)
        values.update(overrides)
        return duet.DuetConfig(**values)

    def _run(self, replies, **overrides) -> dict:
        cfg = self._cfg(**overrides)
        with mock.patch.object(duet, "call_agent", side_effect=replies), \
                mock.patch.object(duet.sys.stdin, "isatty", return_value=False), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return duet.run_duet(cfg)

    def _cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", [str(_ROOT / "duet.py"), *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                result = duet.main()
            except SystemExit as exc:
                result = exc.code
        return result, out.getvalue(), err.getvalue()

    def _saved_run(self, name="saved", **overrides) -> pathlib.Path:
        run = self.root / name
        run.mkdir()
        state = {
            "cwd": str(self.root), "task": "private-task-marker", "agents": [
                {"name": "lead", "backend": "claude", "role": "reviewer"},
                {"name": "partner", "backend": "codex", "role": "coder"}],
            "history": [], "turns_used": 0, "per_turn_timeout": 10,
            "phase": "finished", "finished_reason": "max_turns",
            "finding_reports": True, "metrics_enabled": True, "dry_run": False,
        }
        state.update(overrides)
        (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
        return run

    def _continue(self, run, *extra) -> duet.DuetConfig:
        parser = duet._build_arg_parser()
        args = parser.parse_args(["--continue", str(run), *extra])
        return duet.build_continue_config(str(run), args, parser, {})

    def _history(self, *, resolved=False) -> list[dict]:
        return [
            {"turn": 1, "agent": "lead", "finding_updates": {
                "status": "ok", "items": [_assessment(disposition="supported")]}},
            {"turn": 2, "agent": "partner", "finding_updates": {
                "status": "ok", "items": [_assessment(disposition="supported" if resolved else "unresolved")]}},
        ]

    def test_review_recipe_enables_reports_and_explicit_opt_out_wins(self) -> None:
        for flags, expected in (([], True), (["--no-finding-reports"], False)):
            with self.subTest(flags=flags):
                parser = duet._build_arg_parser()
                args = parser.parse_args(["--recipe", "review", "--task", "x", *flags])
                duet._apply_recipe_args(args)
                cfg = duet._build_cfg_from_cli(args, parser, {})
                self.assertIs(cfg.finding_reports, expected)

    def test_config_enable_override_and_continue_restore(self) -> None:
        config = self.root / "config.json"
        config.write_text(json.dumps({
            "task": "x", "finding_reports": True,
            "agents": [{"name": "lead", "backend": "claude", "role": "reviewer"},
                       {"name": "partner", "backend": "codex", "role": "coder"}],
        }), encoding="utf-8")
        for flags, expected in (([], True), (["--no-finding-reports"], False)):
            with self.subTest(flags=flags):
                parser = duet._build_arg_parser()
                args = parser.parse_args(["--config", str(config), *flags])
                cfg = duet._build_cfg_from_yaml(args, parser, {})
                self.assertIs(cfg.finding_reports, expected)
        run = self._saved_run()
        self.assertTrue(self._continue(run).finding_reports)
        self.assertFalse(self._continue(run, "--no-finding-reports").finding_reports)

    def test_call_agent_preserves_literal_template_braces_and_appends_protocol(self) -> None:
        cfg = self._cfg()
        cfg.agents[0].role_prompt = 'Keep {literal} and {"example": 1}; sentinel {SENTINEL}'
        adapter = mock.Mock()
        adapter.call.return_value = ("reply", "session")
        with mock.patch.object(duet, "backend_adapter", return_value=adapter):
            self.assertEqual(duet.call_agent(cfg.agents[0], "partner message", cfg, True), "reply")
        prompt = adapter.call.call_args.args[1]
        self.assertIn('{literal} and {"example": 1}', prompt)
        self.assertIn(cfg.sentinel, prompt)
        self.assertNotIn("{SENTINEL}", prompt)
        self.assertIn("duet-findings", prompt)
        self.assertIn('"id":"L1"', prompt)
        self.assertIn("1 to 32 ASCII characters", prompt)
        self.assertIn("letters, digits, '.', '_', or '-'", prompt)
        self.assertIn("starts with a letter or digit", prompt)
        self.assertIn("reuse that exact ID, including its case", prompt)
        self.assertIn("Never copy a label that is not a valid ID", prompt)

    def test_successful_turns_persist_updates_and_rebuild_local_report(self) -> None:
        state = self._run([_reply("supported"), _reply("supported")])
        run = pathlib.Path(state["transcript_path"]).parent
        saved = json.loads((run / "state.json").read_text(encoding="utf-8"))
        self.assertEqual([entry["finding_updates"]["status"] for entry in saved["history"]], ["ok", "ok"])
        self.assertIn("`L1` — supported", (run / "review.md").read_text(encoding="utf-8"))
        (run / "review.md").unlink()
        duet._write_run_state(run / "state.json", saved)
        self.assertEqual((run / "review.md").read_text(encoding="utf-8"),
                         duet.render_finding_report(duet.build_finding_report(saved)))

    def test_caller_defined_ids_differing_by_case_stay_separate_through_a_run(self) -> None:
        def reply(identity: str) -> str:
            return "```duet-findings\n" + json.dumps(
                {"findings": [_assessment(identity=identity)]}) + "\n```"
        state = self._run([reply("F1"), reply("f1")])
        stored = [entry["finding_updates"]["items"][0]["id"] for entry in state["history"]]
        self.assertEqual(stored, ["F1", "f1"])
        run = pathlib.Path(state["transcript_path"]).parent
        report = (run / "review.md").read_text(encoding="utf-8")
        self.assertIn("## `F1` — ", report)
        self.assertIn("## `f1` — ", report)

    def test_failed_reply_cannot_publish_structured_claims_from_error_text(self) -> None:
        state = self._run([duet.AgentRunError("agent_error", _reply("supported"))])
        self.assertNotIn("finding_updates", state["history"][0])
        report = duet.build_finding_report(state)
        self.assertEqual(report["events"], [])
        self.assertEqual(report["structured_turns"]["failed"], 1)

    def test_report_sidecar_failure_preserves_state_and_read_only_recovery(self) -> None:
        original_write = duet.write_text_atomic

        def fail_report_only(path, text):
            if path.name == "review.md":
                raise PermissionError("fixture sidecar is unwritable")
            return original_write(path, text)

        with mock.patch.object(duet, "write_text_atomic", side_effect=fail_report_only):
            state = self._run([_reply("supported"), _reply("supported")])
            run = pathlib.Path(state["transcript_path"]).parent
            saved = json.loads((run / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["finished_reason"], "max_turns")
            self.assertEqual(len(saved["history"]), 2)
            self.assertFalse((run / "review.md").exists())
            code, out, err = self._cli("--report", str(run))
            self.assertEqual(code, 0, err)
            self.assertIn("`L1` — supported", out)
            self.assertFalse((run / "review.md").exists())

    def test_forced_success_and_failure_follow_same_update_contract(self) -> None:
        for failed in (False, True):
            with self.subTest(failed=failed):
                cfg = self._cfg()
                run = self.root / ("forced-failed" if failed else "forced-ok")
                run.mkdir()
                transcript = run / "transcript.md"
                transcript.write_text("", encoding="utf-8")
                history = []
                result = duet.AgentRunError("agent_error", _reply()) if failed else _reply()
                with mock.patch.object(duet, "call_agent", side_effect=[result]), \
                        contextlib.redirect_stdout(io.StringIO()):
                    duet._run_forced_turn(
                        cfg, forced_turn=3, next_speaker=cfg.agents[0], forced_msg="check again",
                        transcript_path=transcript, state_path=run / "state.json",
                        wt_path=None, wt_branch=None, history=history,
                        seen_first_turn={agent.name: False for agent in cfg.agents}, last_verify_state=None)
                self.assertTrue(history[0]["forced"])
                self.assertEqual("finding_updates" in history[0], not failed)
                state = duet._build_run_state(cfg, turns_used=3, history=history,
                    finished_reason="max_turns", transcript_path=transcript, recap_path=run / "recap.md")
                duet._write_run_state(run / "state.json", state)
                report = duet.build_finding_report(json.loads((run / "state.json").read_text()))
                self.assertEqual(len(report["events"]), 0 if failed else 1)

    def test_report_json_is_read_only_and_old_runs_are_unavailable(self) -> None:
        for old in (False, True):
            with self.subTest(old=old):
                run = self._saved_run("old" if old else "new", history=[] if old else self._history(),
                                      finding_reports=not old)
                before = (run / "state.json").read_bytes()
                with mock.patch.object(duet, "call_agent") as agent, \
                        mock.patch.object(duet, "write_text_atomic") as write:
                    code, out, err = self._cli("--report", str(run), "--json")
                self.assertEqual(code, 0, err)
                report = json.loads(out)
                self.assertEqual(report["available"], not old)
                self.assertEqual(report["kind"], "duet.finding.report")
                agent.assert_not_called()
                write.assert_not_called()
                self.assertEqual((run / "state.json").read_bytes(), before)
                self.assertFalse((run / "review.md").exists())

    def _history_for(self, dispositions: dict) -> list[dict]:
        """Build a lead turn and a partner turn; a pair is (lead, partner) or None per ID."""
        history = []
        for turn, (agent, slot) in enumerate((("lead", 0), ("partner", 1)), start=1):
            items = [_assessment(identity=identity, disposition=pair[slot])
                     for identity, pair in dispositions.items() if pair[slot] is not None]
            history.append({"turn": turn, "agent": agent,
                            "finding_updates": {"status": "ok", "items": items}})
        return history

    def _announce(self, state: dict) -> list[str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            duet._announce_finding_report(state)
        return out.getvalue().splitlines()

    def _announce_state(self, run: pathlib.Path, dispositions: dict) -> dict:
        return {"finding_reports": True, "agents": [{"name": "lead"}, {"name": "partner"}],
                "history": self._history_for(dispositions), "transcript_path": str(run / "transcript.md")}

    def test_review_file_and_report_show_the_real_directory_and_it_runs_as_written(self) -> None:
        state = self._run([_reply("supported"), _reply("unresolved")],
                          runs_dir=self.root / "runs with space")
        run = pathlib.Path(state["transcript_path"]).parent
        quoted = shlex.quote(str(run.resolve()))
        self.assertIn(f"duet --continue {quoted} --resolve L1 --turns 2",
                      (run / "review.md").read_text(encoding="utf-8"))
        code, out, err = self._cli("--report", str(run))
        self.assertEqual(code, 0, err)
        line = next(row for row in out.splitlines() if row.startswith("duet --continue"))
        argv = shlex.split(line)
        self.assertEqual(argv[:2], ["duet", "--continue"])
        cfg = self._continue(pathlib.Path(argv[2]), *argv[3:], "--runs-dir", str(self.root / "continued"))
        self.assertEqual(cfg.resolve_findings, ["L1"])

    def test_report_on_a_copied_run_names_the_copy(self) -> None:
        run = self._saved_run("original run", history=self._history(), turns_used=2)
        copied = self.root / "moved here" / "copy"
        shutil.copytree(run, copied)
        code, out, err = self._cli("--report", str(copied))
        self.assertEqual(code, 0, err)
        self.assertIn(shlex.quote(str(copied.resolve())), out)
        self.assertNotIn(str(run.resolve()), out)
        self.assertNotIn("<run>", out)

    def test_end_of_run_lines_show_counts_and_a_runnable_command(self) -> None:
        run = self.root / "finished run"
        state = self._announce_state(run, {
            "L1": ("supported", None), "L2": ("supported", "refuted"),
            "L3": ("unresolved", "unresolved"), "L4": ("supported", "supported")})
        lines = self._announce(state)
        self.assertEqual(len(lines), 2, lines)
        self.assertEqual(lines[0], f"[duet] finding report: {run / 'review.md'} "
                                   "(3 unresolved: 1 single_assessment, 1 disputed, 1 open)")
        prefix = "[duet] continue unresolved findings: "
        self.assertTrue(lines[1].startswith(prefix))
        self.assertEqual(shlex.split(lines[1][len(prefix):]),
                         ["duet", "--continue", str(run.resolve()),
                          "--resolve", "L1", "--resolve", "L2", "--resolve", "L3"])

    def test_end_of_run_command_lists_at_most_five_ids(self) -> None:
        run = self.root / "many"
        ids = {f"L{number}": ("supported", None) for number in range(1, 9)}
        lines = self._announce(self._announce_state(run, ids))
        self.assertEqual(len(lines), 3, lines)
        self.assertIn("(8 unresolved: 8 single_assessment, 0 disputed, 0 open)", lines[0])
        resolved = shlex.split(lines[1].split(": ", 1)[1])[3:]
        self.assertEqual(resolved, [part for number in range(1, 6) for part in ("--resolve", f"L{number}")])
        self.assertIn("3 more unresolved findings are not in this command", lines[2])
        self.assertIn(str(run / "review.md"), lines[2])
        one_more = {f"L{number}": ("supported", None) for number in range(1, 7)}
        lines = self._announce(self._announce_state(run, one_more))
        self.assertEqual(len(lines), 3, lines)
        self.assertIn("1 more unresolved finding is not in this command", lines[2])
        self.assertNotIn("findings are", lines[2])
        exactly = {f"L{number}": ("supported", None) for number in range(1, 6)}
        self.assertEqual(len(self._announce(self._announce_state(run, exactly))), 2)

    def test_end_of_run_has_no_command_without_unresolved_findings(self) -> None:
        run = self.root / "clean"
        with_open = self._announce(self._announce_state(run, {"L1": ("supported", None)}))
        self.assertTrue(any("duet --continue" in line for line in with_open), with_open)
        lines = self._announce(self._announce_state(run, {"L1": ("supported", "supported")}))
        self.assertEqual(lines, [f"[duet] finding report: {run / 'review.md'} (0 unresolved)"])
        self.assertEqual(self._announce({"finding_reports": False}), [])

    def test_end_of_run_has_no_command_when_the_history_is_incomplete(self) -> None:
        state = self._announce_state(self.root / "partial", {"L1": ("supported", None)})
        with mock.patch.object(duet, "_FINDING_EVENT_LIMIT", 1):
            lines = self._announce(state)
        self.assertEqual(len(lines), 1)
        self.assertIn("(1 unresolved: 1 single_assessment, 0 disputed, 0 open)", lines[0])
        self.assertNotIn("duet --continue", lines[0])

    def test_dry_run_continuation_prints_counts_but_no_command(self) -> None:
        run = self._saved_run(history=self._history(), turns_used=2)
        cfg = self._continue(run, "--resolve", "L1", "--dry-run", "--runs-dir", str(self.root / "continued"))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            state = duet.run_duet(cfg)
        self.assertIs(state["dry_run"], True)
        lines = self._announce(state)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("(1 unresolved: 0 single_assessment, 0 disputed, 1 open)", lines[0])
        dry_dir = pathlib.Path(state["transcript_path"]).parent
        review = (dry_dir / "review.md").read_text(encoding="utf-8")
        self.assertIn("duet --continue <run> --resolve L1 --turns 2", review)
        self.assertNotIn(shlex.quote(str(dry_dir.resolve())), review)
        code, out, err = self._cli("--report", str(dry_dir))
        self.assertEqual(code, 0, err)
        self.assertIn("duet --continue <run> --resolve L1 --turns 2", out)
        live_state = self._announce_state(self.root / "live", {"L1": ("supported", None)})
        self.assertEqual(len(self._announce(dict(live_state, dry_run=False))), 2)

    def test_continuation_kickoff_is_free_of_report_only_keys(self) -> None:
        run = self._saved_run(history=self._history(), turns_used=2)
        lead = {"id": "L1", "claim": "A partial batch is counted as full",
                "disposition": "supported",
                "evidence": ["toy.py:18; five items produce three batches"],
                "objection": None, "slot": "lead", "turn": 1, "inherited": False}
        partner = dict(lead, disposition="unresolved", slot="partner", turn=2,
                       objection="Need the partner to check the contract")
        focus = [{"id": "L1", "claim": lead["claim"], "disposition": "unresolved",
                  "assessment_basis": "agent_assessment", "assessments": [lead, partner],
                  "missing_evidence_slots": [],
                  "last_open_objection": "Need the partner to check the contract"}]
        for extra in (["--resolve", "L1"], []):
            with self.subTest(extra=extra):
                kickoff = self._continue(run, *extra).kickoff
                self.assertNotIn("unresolved_reason", kickoff)
                self.assertTrue(kickoff.endswith(json.dumps(focus, ensure_ascii=True)), kickoff)

    def test_resolve_defaults_two_turns_and_preserves_inherited_identity(self) -> None:
        run = self._saved_run(history=self._history(), turns_used=2)
        cfg = self._continue(run, "--resolve", "L1", "--runs-dir", str(self.root / "continued"))
        self.assertEqual(cfg.max_turns, 2)
        self.assertEqual(cfg.resolve_findings, ["L1"])
        self.assertEqual(len(cfg.finding_baseline), 2)
        self.assertIn("L1", cfg.kickoff)
        with mock.patch.object(duet, "call_agent", side_effect=[_reply("refuted"), _reply("refuted")]), \
                mock.patch.object(duet.sys.stdin, "isatty", return_value=False), \
                contextlib.redirect_stdout(io.StringIO()):
            continued = duet.run_duet(cfg)
        report = duet.build_finding_report(continued)
        self.assertEqual([event["inherited"] for event in report["events"]], [True, True, False, False])
        self.assertEqual([finding["id"] for finding in report["findings"]], ["L1"])
        self.assertEqual(report["findings"][0]["disposition"], "refuted")

    def test_resolve_selects_caller_defined_id(self) -> None:
        history = [{"turn": turn, "agent": agent, "finding_updates": {
            "status": "ok", "items": [_assessment(identity="P1-1", disposition=disposition)]}}
            for turn, agent, disposition in ((1, "lead", "supported"), (2, "partner", "unresolved"))]
        run = self._saved_run("caller-id", history=history, turns_used=2)
        cfg = self._continue(run, "--resolve", "P1-1")
        self.assertEqual(cfg.resolve_findings, ["P1-1"])
        self.assertEqual([event["id"] for event in cfg.finding_baseline], ["P1-1", "P1-1"])
        self.assertIn("P1-1", cfg.kickoff)

    def test_resolve_rejects_unknown_resolved_ids_and_short_budget(self) -> None:
        open_run = self._saved_run("open", history=self._history())
        closed_run = self._saved_run("closed", history=self._history(resolved=True))
        for run, args in ((open_run, ["--resolve", "L9"]),
                          (closed_run, ["--resolve", "L1"]),
                          (open_run, ["--resolve", "L1", "--turns", "1"]),
                          (open_run, ["--resolve", "L1", "--no-finding-reports"])):
            with self.subTest(args=args, run=run.name), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    self._continue(run, *args)
                self.assertEqual(caught.exception.code, 2)

    def test_truncated_baseline_blocks_continuation_until_reports_opted_out(self) -> None:
        baseline = [dict(_assessment(identity=f"L{turn}"), slot="lead", turn=turn)
                    for turn in (1, 2, 3)]
        run = self._saved_run(finding_baseline=baseline)
        with mock.patch.object(duet, "_FINDING_EVENT_LIMIT", 2):
            for extra in ([], ["--resolve", "L1"]):
                with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()) as err:
                    with self.assertRaises(SystemExit) as caught:
                        self._continue(run, *extra)
                    self.assertEqual(caught.exception.code, 2)
                    self.assertIn("finding history is incomplete", err.getvalue())
            cfg = self._continue(run, "--no-finding-reports", "--runs-dir", str(self.root / "continued"))
            self.assertFalse(cfg.finding_reports)
            self.assertEqual(cfg.finding_baseline, [])

    def test_finding_dispositions_do_not_replace_existing_convergence_rule(self) -> None:
        cases = (("supported", False, "max_turns"), ("unresolved", True, "converged"))
        for index, (disposition, converged, reason) in enumerate(cases):
            with self.subTest(disposition=disposition):
                state = self._run([_reply(disposition, converged=converged)] * 2,
                                  runs_dir=self.root / f"convergence-{index}")
                self.assertEqual(state["finished_reason"], reason)

    def test_feedback_is_latest_curated_run_outcome_and_updates_count_once(self) -> None:
        run = self._saved_run(history=self._history(), verify_cmd="private-command-marker",
                              metrics={"id": "00000000-0000-4000-8000-000000000123"},
                              raw_code="private-code-marker", raw_prompt="private-prompt-marker")
        for usefulness, decision in (("mixed", "not_applied"), ("useful", "corrected_comment")):
            code, _, err = self._cli("--feedback", str(run), "--usefulness", usefulness, "--decision", decision)
            self.assertEqual(code, 0, err)
        local = json.loads((run / "feedback.json").read_text())
        central_files = list((self.metrics / "feedback").glob("*.json"))
        self.assertEqual(len(central_files), 1)
        central = json.loads(central_files[0].read_text())
        self.assertEqual(central, local)
        self.assertEqual(central["usefulness"], "useful")
        self.assertEqual(central["decision"], "corrected_comment")
        self.assertEqual(set(central), {"schema_version", "kind", "id", "run_kind",
                                       "usefulness", "decision", "recorded_at"})
        text = json.dumps(central)
        for secret in (str(self.root), "private-task-marker", "private-command-marker",
                       "private-code-marker", "private-prompt-marker", "A partial batch"):
            self.assertNotIn(secret, text)
        code, out, err = self._cli("--stats", "--json")
        self.assertEqual(code, 0, err)
        live = json.loads(out)["feedback"]["live"]
        self.assertEqual(live, {"records": 1, "usefulness": {"useful": 1},
                                "decisions": {"corrected_comment": 1}})

    def test_feedback_opt_out_keeps_only_local_record(self) -> None:
        for index, (enabled, environment) in enumerate(((False, "1"), (True, "0"))):
            with self.subTest(enabled=enabled, environment=environment):
                run = self._saved_run(f"disabled-{index}", metrics_enabled=enabled)
                with mock.patch.dict(os.environ, {"DUET_METRICS": environment}):
                    code, _, err = self._cli("--feedback", str(run), "--usefulness", "useful",
                                             "--decision", "corrected_comment")
                self.assertEqual(code, 0, err)
                self.assertTrue((run / "feedback.json").is_file())
                self.assertFalse((self.metrics / "feedback").exists())

    def test_feedback_stats_separate_live_test_and_dry_run_cohorts(self) -> None:
        for kind in ("live", "test", "dry_run"):
            run = self._saved_run(kind, dry_run=kind == "dry_run", metrics_kind=kind if kind == "test" else "live")
            code, _, err = self._cli("--feedback", str(run), "--usefulness", "useful", "--decision", "no_change")
            self.assertEqual(code, 0, err)
        code, out, err = self._cli("--stats", "--json")
        self.assertEqual(code, 0, err)
        feedback = json.loads(out)["feedback"]
        for kind in ("live", "test", "dry_run"):
            self.assertEqual(feedback[kind]["records"], 1)
            self.assertEqual(feedback[kind]["decisions"], {"no_change": 1})
        self.assertEqual(feedback["unknown"]["records"], 0)

    def test_report_and_feedback_reject_conflicting_or_incomplete_operations(self) -> None:
        run = self._saved_run()
        feedback = ["--feedback", str(run), "--usefulness", "useful", "--decision", "corrected_comment"]
        cases = [
            ["--report", str(run), "--task", "x"],
            ["--report", str(run), "--stats"],
            ["--report", str(run), "--feedback", str(run)],
            ["--report", str(run), "--no-finding-reports"],
            [*feedback, "--json"], [*feedback, "--continue", str(run)],
            [*feedback, "--no-metrics"], ["--feedback", str(run)],
            ["--resolve", "L1"], ["--usefulness", "useful"],
            ["--continue", str(run), "--resolve", "L1", "--status", str(run)],
        ]
        with mock.patch.object(duet, "run_duet") as agent:
            for argv in cases:
                with self.subTest(argv=argv):
                    code, _, _ = self._cli(*argv)
                    self.assertEqual(code, 2)
            agent.assert_not_called()
        self.assertFalse((run / "feedback.json").exists())


if __name__ == "__main__":
    unittest.main()
