# Usage evidence and product direction

Audit date: 5 September 2026.

An audit of one developer's history across two computers supports a focused
product hypothesis: **verify an AI review before sharing its claims**. The work
cohort includes 35 records classified as checking existing claims. This recurring
workflow is a reason to test the product with other developers; the audit does
not establish customer adoption, correctness, time savings, or willingness to pay.

This note publishes aggregate counts and paraphrased cases. Original project
names, folder names, prompts, transcripts, identity mappings, and content
fingerprints remain outside the repository. The underlying audit artifacts are
retained privately, so this note is not a publicly reproducible benchmark.

## Scope and method

Personal-computer artifacts were inspected directly. Work-computer totals were
recomputed from a supplied anonymized export, and its extraction code was read
without being executed. The work source transcripts, command logs, and
repositories were unavailable for independent review. Qualitative work findings
are therefore attributed to the supplied audit.

The common comparison cohort includes personal attempts classified as
substantive and work records carrying a project-task category. It retains early
attempts with task evidence even when no agent completed a reply. Five work
attempts without task evidence are outside that cohort despite the source
export's broader substantive label. Task categories otherwise retain each
source audit's classification.

| Measure | Personal | Work | Total |
|---|---:|---:|---:|
| Retained artifact records | 49 | 137 | 186 |
| Records with project-task evidence | 27 | 128 | 155 |
| Dry-run records | 10 | 4 | 14 |
| Retained harness-test records | 6 | 0 | 6 |
| Unclassified or without task evidence | 6 | 5 | 11 |
| Recorded agreements on project tasks | 20 | 88 | 108 |
| Project-task records with recorded harness checks | 3 | 1 | 4 |
| Passing harness checks on project tasks | 7 | 2 | 9 |

The project-task, dry-run, harness-test, and unclassified rows partition the
186 retained records. The agreement and check rows are subsets, not additional
records. Seven work harness fixtures were excluded before export and have no
individual rows; they are outside these totals.

The 108 agreements are recorded convergence stop conditions, not independently
verified successes. Recorded harness checks are also narrower than all agent
tool use: an absent harness result does not establish that no check was run.

Private comparisons found no matching canonical state fingerprints, nonempty
transcript fingerprints, or original run IDs across computers. Empty transcript
matches were excluded from duplicate detection. No records were merged, but
edited copies can evade exact matching, and repeated tasks remain separate
records. The total is not a proven count of unique invocations or tasks.

Coverage is incomplete: the personal audit found stale registry links and
used a bounded filesystem scan; the work filesystem was unavailable. Project
grouping also differs between sources, so project counts are not added as unique
repositories. Both cohorts belong to the same person.

## What the history suggests

The 128 work records with project tasks were classified as follows:

| Task category | Records |
|---|---:|
| Code or diff review | 61 |
| Verification of existing claims | 35 |
| Design or plan review | 21 |
| Investigation | 5 |
| Duet development | 6 |

Review and claim verification are recurring uses in this person's workflow.
Selected cases in the work audit describe a rebuttal corrected after a
repository-history comparison, a proposed fix that stopped masking values,
a severity reduction, a review left unresolved at the turn limit, and one
exchange with no visible new contribution. These cases illustrate possible
benefits and limits; they do not establish how often either occurs. None confirms
downstream human acceptance, posting, merging, or deployment.

[Read the cases in plain language](REAL_CASES.md) for the initial claim,
challenge, and reported change, including unresolved and low-value exchanges.

The work audit identified 25 candidate transitions from a convergence proposal
to a reply without a proposal. It classified 23 events across 22 records as
substantive objections and two as adapter errors. Proposal detection used a
deterministic marker rule; semantic classification used text heuristics and
selected case reading. The underlying replies were unavailable for independent
event-by-event reassessment. These are objection classifications, not 23 proven
defects. Personal events were not extracted with the same method, so there is
no combined objection rate.

Six objection events occurred at turn five of a six-turn budget. Only one turn
remained, while normal convergence requires two consecutive agreement turns.
An objection at that position therefore prevented normal convergence within the
remaining budget. More turns would create an opportunity for confirmation, not
a guarantee of resolution. Roles, speaking positions, and budgets were not
independently varied, so this result cannot support a vendor-quality ranking.

Older sampled finding/refutation percentages are omitted because the sample
membership, selection rule, and validation rubric could not be reconstructed.
Recorded elapsed time is not interpreted as human time saved or billed cost.

## Product priorities and implementation status

The audit motivated these priorities. The current implementation supplies the
first report workflow; the [finding reference](FINDINGS.md) documents its
contracts and limits. It does not establish adoption or downstream value.

1. **Make the finding the unit of review.** Produce a concise Markdown report
   with a stable ID for each claim, its current disposition (supported, refuted,
   or unresolved), cited evidence, recorded check results, and the last open
   objection. Keep an agent's assessment distinguishable from executed evidence
   and a user's decision. Missing evidence should remain visible.
   **Implemented:** local `review.md`, `--report`, stable IDs, separate agent
   assessments and harness check records, and explicit coverage gaps.
2. **Preserve disagreement at the turn limit.** List unresolved findings when a
   run ends, with a user-selected, bounded continuation for a response and
   confirmation. Keep the existing stop reason visible and retain the two-turn
   agreement rule. Late objections should remain inspectable even if the user
   chooses to stop.
   **Implemented:** unresolved findings stay in the report; `--continue --resolve`
   preserves their IDs and defaults to two additional turns in a new run.
3. **Measure what happens after the report.** In an opt-in pilot, record whether
   a developer changed a review comment, accepted a useful finding, rejected an
   unhelpful one, or returned for another task. Keep acceptance separate from
   technical correctness, and allow participants to report outcomes without
   submitting private code or transcripts.
   **Implemented foundation:** optional `--feedback` records fixed-choice
   usefulness and decisions, with separate stats cohorts. Recruiting a pilot,
   assessing outcomes, and measuring voluntary repeat use remain to be done.

The [public toy fixture](../examples/review-evidence/README.md) now provides
repeatable checks for a defect, a refuted claim, and an unresolved requirement.
The [recorded walkthrough](demos/README.md) demonstrates that fixture's report
workflow. The next validation step is an opt-in pilot. The
[validation and attention plan](launch/validation-plan.md) describes how to test
that workflow with other developers before expanding the product's claims.
