"""Structured assessments remain distinct from facts and executed checks."""
from __future__ import annotations

import json
import pathlib
import shlex
import sys
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
import duet  # noqa: E402


def item(identity="L1", disposition="supported", **overrides):
    return {"id": identity, "claim": "The empty input path fails.",
            "disposition": disposition, "evidence": ["reader.py:12"], **overrides}


def block(items):
    return "```duet-findings\n" + json.dumps({"findings": items}) + "\n```"


def turn(number, agent, items, **overrides):
    return {"turn": number, "agent": agent,
            "finding_updates": duet.parse_finding_updates(block(items)), **overrides}


def state(history=(), **overrides):
    return {"finding_reports": True, "agents": [{"name": "reviewer"}, {"name": "coder"}],
            "history": list(history), "phase": "finished", "finished_reason": "max_turns",
            **overrides}


class TestFindingParser(unittest.TestCase):
    def test_missing_empty_and_one_structured_block(self):
        self.assertEqual(duet.parse_finding_updates("L1 is supported"),
                         {"status": "missing", "items": []})
        self.assertEqual(duet.parse_finding_updates(block([])), {"status": "ok", "items": []})
        parsed = duet.parse_finding_updates("Narrative\n" + block([item(objection="  ", private="ignored")]))
        self.assertEqual(parsed["status"], "ok")
        self.assertIsNone(parsed["items"][0]["objection"])
        self.assertNotIn("private", parsed["items"][0])
        blank = duet.parse_finding_updates(block([item(evidence=["", "  "])]))
        self.assertEqual(blank["status"], "ok")
        self.assertEqual(blank["items"][0]["evidence"], [])

    def test_duplicate_blocks_unclosed_json_and_duplicate_ids_reject_atomically(self):
        for reply in (block([]) + "\n" + block([]), "```duet-findings\n{}",
                      "```duet-findings\nnot JSON\n```", block([item(), item()]),
                      block([item(), item("bad id")])):
            with self.subTest(reply=reply):
                self.assertEqual(duet.parse_finding_updates(reply), {"status": "malformed", "items": []})

    def test_example_nested_in_other_fence_is_not_an_assessment(self):
        reply = "````text\n" + block([item()]) + "\n````"
        self.assertEqual(duet.parse_finding_updates(reply)["status"], "missing")
        self.assertEqual(duet.parse_finding_updates(reply + "\n" + block([]))["status"], "ok")

    def test_caller_defined_ids_are_accepted_and_bad_ids_rejected(self):
        valid = ["F1", "C1", "P1-1", "a.b_c-d", "9", "L" + "x" * 31]
        parsed = duet.parse_finding_updates(block([item(identity) for identity in valid]))
        self.assertEqual([entry["id"] for entry in parsed["items"]], valid)
        invalid = ["", "F 1", "L" + "x" * 32, "-F1", ".F1", "\u00c9\u00c91", "F1\n",
                   "_F1", "#1", "Finding 3"]
        for identity in invalid:
            with self.subTest(identity=identity):
                self.assertEqual(duet.parse_finding_updates(block([item(identity)]))["status"], "malformed")

    def test_field_limits_and_types(self):
        invalid = [item(claim=""),
                   item(claim="x" * 2001), item(disposition=[]), item(evidence="test"),
                   item(evidence=["x"] * 9), item(evidence=["x" * 2001]),
                   item(objection="x" * 2001), item(claim="\ud800")]
        for value in invalid:
            with self.subTest(value=repr(value)):
                self.assertEqual(duet.parse_finding_updates(block([value]))["status"], "malformed")
        self.assertEqual(duet.parse_finding_updates(block([item(f"L{i}") for i in range(1, 52)]))["status"], "malformed")
        self.assertEqual(duet.parse_finding_updates(block([item("P9999", claim="x" * 2000)]))["status"], "ok")

    def test_byte_limit_recursion_and_integer_limit_do_not_crash(self):
        oversized = block([item(f"L{i}", claim="\u2603" * 2000) for i in range(1, 15)])
        deep = "```duet-findings\n" + "[" * 3000 + "0" + "]" * 3000 + "\n```"
        integer = '```duet-findings\n{"findings":' + "9" * 5000 + '}\n```'
        for reply in (oversized, deep, integer):
            self.assertEqual(duet.parse_finding_updates(reply)["status"], "malformed")


class TestFindingProjection(unittest.TestCase):
    def test_one_assessment_and_conflict_remain_unresolved(self):
        history = [turn(1, "reviewer", [item(objection="Missing empty-input test")])]
        first = duet.build_finding_report(state(history))
        self.assertEqual(first["summary"]["unresolved"], 1)
        self.assertEqual(first["findings"][0]["missing_evidence_slots"], ["partner"])
        history.append(turn(2, "coder", [item(disposition="refuted", objection="Guard exists")]))
        finding = duet.build_finding_report(state(history))["findings"][0]
        self.assertEqual(finding["disposition"], "unresolved")
        self.assertEqual(finding["last_open_objection"], "Guard exists")

    def test_later_agreement_refutes_without_verifying_claim(self):
        history = [turn(1, "reviewer", [item(objection="Check guard")]),
                   turn(2, "coder", [item(disposition="refuted", objection="Guard exists")]),
                   turn(3, "reviewer", [item(disposition="refuted", evidence=[])])]
        report = duet.build_finding_report(state(history))
        finding = report["findings"][0]
        self.assertEqual(report["summary"], {"supported": 0, "refuted": 1, "unresolved": 0})
        self.assertEqual(finding["assessment_basis"], "agent_assessment")
        self.assertIsNone(finding["last_open_objection"])
        self.assertEqual(finding["missing_evidence_slots"], ["lead"])
        self.assertEqual(report["executed_checks"], [])

    def test_baseline_preserves_id_original_claim_and_provenance(self):
        original = duet.build_finding_report(state([turn(1, "reviewer", [item()])]))
        report = duet.build_finding_report(state(
            [turn(1, "coder", [item(claim="Changed wording")])],
            finding_baseline=original["events"]))
        self.assertEqual(report["findings"][0]["id"], "L1")
        self.assertEqual(report["findings"][0]["claim"], item()["claim"])
        self.assertEqual([event["inherited"] for event in report["events"]], [True, False])
        self.assertEqual([event["slot"] for event in report["events"]], ["lead", "partner"])
        self.assertEqual(report["findings"][0]["disposition"], "supported")

    def test_failure_data_ignored_and_missing_coverage_visible(self):
        report = duet.build_finding_report(state([
            turn(1, "reviewer", [item()], finished_reason="timeout"),
            {"turn": 2, "agent": "coder"},
            {"turn": 3, "agent": "reviewer", "finding_updates": {"status": "ok", "items": "bad"}},
        ]))
        self.assertEqual(report["events"], [])
        self.assertFalse(report["available"])
        self.assertEqual(report["structured_turns"], {"ok": 0, "missing": 1, "malformed": 1, "failed": 1})
        self.assertIn("unavailable", duet.render_finding_report(report))

    def test_executed_checks_are_separate_from_agent_claims(self):
        report = duet.build_finding_report(state([
            turn(1, "reviewer", [item(evidence=["I ran all tests, they passed"])]),
            turn(2, "coder", [], forced=True, verify={"ok": False, "exit_code": 2,
                 "command": "make test", "log_path": "/private/log", "timed_out": False}),
        ]))
        self.assertEqual(len(report["executed_checks"]), 1)
        check = report["executed_checks"][0]
        self.assertFalse(check["ok"])
        self.assertTrue(check["forced"])
        self.assertEqual(check["scope"], "run_check_not_individual_finding")
        self.assertEqual(report["findings"][0]["disposition"], "unresolved")

    def test_untrusted_state_and_bounded_history(self):
        for raw in (None, [], {"agents": [None, {}], "history": [None, {"agent": []}]},
                    {"finding_baseline": [None, {"id": "L1", "slot": []}]}):
            self.assertFalse(duet.build_finding_report(raw)["available"])
        history = [turn(i, "reviewer", []) for i in range(1, 1002)]
        report = duet.build_finding_report(state(history))
        self.assertTrue(report["truncated"])
        self.assertEqual(report["structured_turns"]["ok"], 1000)

    def test_extreme_untrusted_integers_cannot_break_rendering(self):
        huge = 10 ** 5000
        report = duet.build_finding_report(state([
            turn(huge, "reviewer", [item()], verify={"ok": True, "exit_code": huge}),
        ], finding_baseline=[dict(item(), slot="lead", turn=huge)]))
        self.assertEqual(report["events"], [])
        self.assertIsNone(report["executed_checks"][0]["exit_code"])
        self.assertIn("Structured findings unavailable", duet.render_finding_report(report))

    def test_execution_limits_report_agent_totals_only(self):
        report = duet.build_finding_report(state([
            turn(1, "reviewer", [item()], tool_denials={"Bash": 2}),
            turn(2, "coder", [], tool_denials={}),
            turn(3, "reviewer", [], tool_denials={"Bash": 1}),
            turn(4, "coder", [], tool_denials={"Bash": "secret command"}),
            turn(5, "bad`name", [], tool_denials={"Read": 1}),
        ]))
        self.assertEqual(report["execution_limits"], [
            {"agent": "reviewer", "slot": "lead", "denied_tool_calls": 3},
            {"agent": "unknown", "slot": None, "denied_tool_calls": 1}])
        rendered = duet.render_finding_report(report)
        self.assertIn("Execution limits: `reviewer` (lead) was denied 3 tool call(s). "
                      "Checks that needed them did not run.", rendered)
        self.assertIn("Execution limits: `unknown` was denied 1 tool call(s).", rendered)
        self.assertNotIn("bad`name", rendered)
        self.assertNotIn("secret command", rendered)

    def test_no_execution_limits_without_recorded_denials(self):
        report = duet.build_finding_report(state([turn(1, "reviewer", [item()])]))
        self.assertEqual(report["execution_limits"], [])
        self.assertNotIn("Execution limits", duet.render_finding_report(report))


class TestFindingRendering(unittest.TestCase):
    def test_fences_make_agent_html_and_markdown_inert(self):
        malicious = "```\n<script>alert(1)</script>\n[click](javascript:alert(1))\n# Heading\n````"
        report = duet.build_finding_report(state([turn(1, "reviewer", [item(
            claim=malicious, evidence=[malicious], objection=malicious)])]))
        rendered = duet.render_finding_report(report)
        self.assertIn("`````text\n" + malicious + "\n`````", rendered)
        self.assertIn("max_turns", rendered)
        self.assertIn("Missing cited evidence: partner", rendered)
        self.assertIn("duet --continue <run> --resolve L1 --turns 2", rendered)
        self.assertIn("private", rendered)
        self.assertIn("not proven facts", rendered)

    def test_heading_shows_id_as_inline_code(self):
        for identity in ["L1", "bug_12", "F1._note_", "www.example.com"]:
            with self.subTest(identity=identity):
                report = duet.build_finding_report(state([turn(1, "reviewer", [item(identity)])]))
                rendered = duet.render_finding_report(report)
                self.assertIn(f"## `{identity}` \u2014 unresolved (agent assessment)\n", rendered)
                self.assertEqual(report["findings"][0]["id"], identity)
                self.assertIn(f"--resolve {identity} --turns 2", rendered)


def assessed(identity, lead=None, partner=None):
    """Return one lead turn and one partner turn, skipping a slot given as None."""
    history = []
    if lead is not None:
        history.append(turn(1, "reviewer", [item(identity, lead)]))
    if partner is not None:
        history.append(turn(2, "coder", [item(identity, partner)]))
    return history


class TestUnresolvedReasons(unittest.TestCase):
    CASES = (
        ("S1", "supported", None, "unresolved", "single_assessment"),
        ("S2", None, "unresolved", "unresolved", "single_assessment"),
        ("S3", None, "refuted", "unresolved", "single_assessment"),
        ("D1", "supported", "refuted", "unresolved", "disputed"),
        ("D2", "refuted", "supported", "unresolved", "disputed"),
        ("O1", "unresolved", "unresolved", "unresolved", "open"),
        ("O2", "supported", "unresolved", "unresolved", "open"),
        ("O3", "unresolved", "refuted", "unresolved", "open"),
        ("R1", "supported", "supported", "supported", None),
        ("R2", "refuted", "refuted", "refuted", None),
    )

    def report(self):
        history = []
        for identity, lead, partner, _, _ in self.CASES:
            history.extend(assessed(identity, lead, partner))
        return duet.build_finding_report(state(history))

    def test_each_reason_follows_the_latest_assessments(self):
        found = {finding["id"]: finding for finding in self.report()["findings"]}
        for identity, _, _, disposition, reason in self.CASES:
            with self.subTest(identity=identity):
                self.assertEqual(found[identity]["disposition"], disposition)
                self.assertEqual(found[identity]["unresolved_reason"], reason)

    def test_reassessment_changes_the_reason_with_the_disposition(self):
        history = assessed("L1", "supported", "refuted")
        self.assertEqual(duet.build_finding_report(state(history))["findings"][0]["unresolved_reason"],
                         "disputed")
        history.append(turn(3, "coder", [item("L1", "supported")]))
        finding = duet.build_finding_report(state(history))["findings"][0]
        self.assertEqual((finding["disposition"], finding["unresolved_reason"]), ("supported", None))
        history = [turn(1, "reviewer", [item("L1", "supported")]),
                   turn(2, "reviewer", [item("L1", "refuted")])]
        finding = duet.build_finding_report(state(history))["findings"][0]
        self.assertEqual(finding["unresolved_reason"], "single_assessment")

    def test_summary_counts_each_reason_and_keeps_the_existing_summary(self):
        report = self.report()
        self.assertEqual(report["unresolved_reasons"],
                         {"single_assessment": 3, "disputed": 2, "open": 3})
        self.assertEqual(report["summary"], {"supported": 1, "refuted": 1, "unresolved": 8})
        empty = duet.build_finding_report(state())
        self.assertEqual(empty["unresolved_reasons"], {"single_assessment": 0, "disputed": 0, "open": 0})

    def test_json_keeps_schema_one_and_adds_only_new_keys(self):
        report = json.loads(json.dumps(self.report(), sort_keys=True))
        self.assertEqual((report["schema_version"], report["kind"]), (1, "duet.finding.report"))
        schema_one = {"schema_version", "kind", "enabled", "available", "phase", "finished_reason",
                      "events", "findings", "summary", "structured_turns", "executed_checks",
                      "execution_limits", "truncated"}
        self.assertEqual(set(report), schema_one | {"unresolved_reasons"})
        finding_schema_one = {"id", "claim", "disposition", "assessment_basis", "assessments",
                              "missing_evidence_slots", "last_open_objection"}
        for finding in report["findings"]:
            self.assertEqual(set(finding), finding_schema_one | {"unresolved_reason"})

    def test_markdown_gives_one_sentence_per_reason_and_the_counts(self):
        rendered = duet.render_finding_report(self.report())
        self.assertIn("Unresolved findings by reason: 3 single_assessment", rendered)
        self.assertIn("2 disputed", rendered)
        self.assertIn("3 open", rendered)
        for sentence in (
                "Only the lead has an accepted assessment of this claim.",
                "Only the partner has an accepted assessment of this claim.",
                "The lead assessed this claim as supported and the partner assessed it as refuted.",
                "The lead assessed this claim as refuted and the partner assessed it as supported.",
                "Both agents have an accepted assessment of this claim, "
                "and at least one still marks it unresolved."):
            with self.subTest(sentence=sentence):
                self.assertIn("\n\n" + sentence + "\n\n", rendered)
        self.assertIn("not proven facts", rendered)

    def test_rejected_reply_leaves_the_earlier_accepted_assessment_in_charge(self):
        rejected = {"turn": 3, "agent": "reviewer",
                    "finding_updates": duet.parse_finding_updates("```duet-findings\nnot JSON\n```")}
        history = assessed("L1", "supported", "refuted") + [rejected]
        report = duet.build_finding_report(state(history))
        self.assertEqual(report["structured_turns"]["malformed"], 1)
        self.assertEqual(report["findings"][0]["unresolved_reason"], "disputed")
        history = [turn(1, "reviewer", [item("L1", "supported")]),
                   {"turn": 2, "agent": "coder",
                    "finding_updates": duet.parse_finding_updates("```duet-findings\nnot JSON\n```")}]
        report = duet.build_finding_report(state(history))
        self.assertEqual(report["findings"][0]["unresolved_reason"], "single_assessment")
        rendered = duet.render_finding_report(report)
        self.assertIn("Only the lead has an accepted assessment of this claim.", rendered)

    def test_reason_covers_inherited_baseline_events(self):
        baseline = duet.build_finding_report(state(assessed("L1", "supported") + assessed("L2", "supported")))
        self.assertEqual(baseline["unresolved_reasons"]["single_assessment"], 2)
        history = [turn(3, "coder", [item("L1", "refuted")])]
        report = duet.build_finding_report(state(history, finding_baseline=baseline["events"]))
        found = {finding["id"]: finding["unresolved_reason"] for finding in report["findings"]}
        self.assertEqual(found, {"L1": "disputed", "L2": "single_assessment"})
        self.assertEqual([event["inherited"] for event in report["events"]], [True, True, False])
        self.assertEqual(report["unresolved_reasons"],
                         {"single_assessment": 1, "disputed": 1, "open": 0})

    def test_markdown_omits_reasons_when_nothing_is_unresolved(self):
        report = duet.build_finding_report(state(assessed("L1", "supported", "supported")))
        rendered = duet.render_finding_report(report)
        self.assertEqual(report["unresolved_reasons"], {"single_assessment": 0, "disputed": 0, "open": 0})
        self.assertIsNone(report["findings"][0]["unresolved_reason"])
        self.assertNotIn("Unresolved findings by reason", rendered)


class TestContinuationCommand(unittest.TestCase):
    def unresolved(self, **overrides):
        return duet.build_finding_report(state(assessed("L1", "supported", "refuted"), **overrides))

    def test_placeholder_remains_when_the_run_directory_is_unknown(self):
        self.assertEqual(duet._finding_continue_command(None, ["L1"], turns=2),
                         "duet --continue <run> --resolve L1 --turns 2")
        self.assertIn("duet --continue <run> --resolve L1 --turns 2",
                      duet.render_finding_report(self.unresolved()))

    def test_real_directory_replaces_placeholder_and_is_shell_quoted(self):
        for name in ("plain", "with space", "it's here"):
            with self.subTest(name=name):
                run_dir = pathlib.Path("/tmp/duet runs") / name
                rendered = duet.render_finding_report(self.unresolved(), run_dir)
                self.assertNotIn("<run>", rendered)
                line = next(row for row in rendered.splitlines() if row.startswith("duet --continue"))
                self.assertEqual(shlex.split(line),
                                 ["duet", "--continue", str(run_dir), "--resolve", "L1", "--turns", "2"])

    def test_incomplete_history_offers_no_command(self):
        with mock.patch.object(duet, "_FINDING_EVENT_LIMIT", 1):
            report = duet.build_finding_report(state(assessed("L1", "supported", "refuted")))
        self.assertTrue(report["truncated"])
        rendered = duet.render_finding_report(report, pathlib.Path("/tmp/run"))
        self.assertNotIn("duet --continue", rendered)
        self.assertIn("Continuation is unavailable", rendered)


if __name__ == "__main__":
    unittest.main()
