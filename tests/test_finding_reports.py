"""Structured assessments remain distinct from facts and executed checks."""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

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
                      block([item(), item("bad")])):
            with self.subTest(reply=reply):
                self.assertEqual(duet.parse_finding_updates(reply), {"status": "malformed", "items": []})

    def test_example_nested_in_other_fence_is_not_an_assessment(self):
        reply = "````text\n" + block([item()]) + "\n````"
        self.assertEqual(duet.parse_finding_updates(reply)["status"], "missing")
        self.assertEqual(duet.parse_finding_updates(reply + "\n" + block([]))["status"], "ok")

    def test_field_limits_and_types(self):
        invalid = [item("L0"), item("L10000"), item("P01"), item(claim=""),
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


if __name__ == "__main__":
    unittest.main()
