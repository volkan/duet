"""Verify expected review evidence, including reproduction of a known defect.

Exit zero means the demonstration behaved as authored. It does not mean the
intentionally buggy implementation is correct. No agent CLI is required.
"""

import json
import pathlib

from toy import plan_batches


def main() -> None:
    item_ids = ["item-2", "item-10", "item-1", "item-3", "item-4"]
    before = list(item_ids)
    plan = plan_batches(item_ids, 2)

    # Five items contain two complete pairs and one leftover item.
    required_full_batches = 2
    if plan["full_batches"] != 3 or plan["full_batches"] == required_full_batches:
        raise AssertionError("L1: expected to reproduce the intentional count defect")
    if item_ids != before:
        raise AssertionError("L2: the caller's list unexpectedly changed")
    if plan["first_batch"] != ["item-1", "item-10"]:
        raise AssertionError("L3: lexical ordering demonstration changed")

    evidence = {
        "fixture_kind": "illustrative_review_evidence",
        "fixture_checks": "passed_expected_demonstrations",
        "toy_correctness": "known_defect_reproduced",
        "findings": [
            {"id": "L1", "assessment": "supported",
             "observed_full_batches": plan["full_batches"],
             "required_full_batches": required_full_batches},
            {"id": "L2", "assessment": "refuted", "caller_list_unchanged": item_ids == before},
            {"id": "L3", "assessment": "unresolved",
             "observed_first_batch": plan["first_batch"],
             "missing_evidence": "production item-ID ordering requirement"},
        ],
    }
    expected_path = pathlib.Path(__file__).resolve().with_name("expected.json")
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    if evidence != expected:
        raise AssertionError("Observed evidence differs from the illustrative expected fixture")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
