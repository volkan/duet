# Finding reports and human feedback

Finding reports keep individual review claims inspectable. They show what each
agent currently thinks, what evidence it cites, and which objections remain
open. They do not turn agreement into proof.

## Start a review

`--recipe review` enables finding reports by default. Other tasks can opt in:

```sh
duet --finding-reports --task 'Check the claims in review.md against the code'
duet --recipe review --no-finding-reports  # use the existing transcript workflow
```

YAML/JSON accepts `finding_reports: true`. Explicit CLI enable/disable flags
override config and continued-run settings. This setting is independent of
central metrics; `--no-metrics` still permits local finding reports.

Duet asks each agent to append one structured assessment block to its normal
reply. A claim that the task, kickoff, or other agent already labels with a
valid ID keeps that ID, such as `F1` or `P1-1`. New claims use `L1` or `P1`.
Later assessments preserve the ID. The ID grammar is in "Structured reply
contract" below. The initial review or kickoff supplies claims to investigate,
not an independent vote supporting them.

Each successful turn stores validated assessments in `state.json` and updates
`review.md` atomically in the run directory. Failed calls cannot supply finding
updates. Missing or malformed structured output is counted visibly; Duet does
not infer findings from ordinary prose. Report-writing failures leave the
authoritative state and agent work intact.

## Inspect the result

```sh
duet --report RUN_DIR_OR_ID
duet --report RUN_DIR_OR_ID --json
```

These commands read local state without invoking agents, executing saved
commands, or modifying artifacts. Markdown goes to stdout; save a regenerated
copy with shell redirection if needed. Invalid or unreadable state exits 3;
a readable run without structured findings exits 0 and reports them unavailable.

The report includes:

- Stable claim IDs and the first recorded wording.
- The latest lead and partner assessments, cited evidence, and inherited/current
  turn provenance. Missing cited evidence remains explicit.
- A disposition of `supported` or `refuted` only when both slots agree on it;
  otherwise `unresolved`, with the last open objection when supplied.
- For each unresolved finding, the reason, and the count per reason near the top
  of the report. The three reasons are:
  - `single_assessment`: only one slot has an accepted assessment of the claim.
    The other agent did not cross-check it, or its reply was rejected as
    malformed.
  - `disputed`: both slots have an accepted assessment, and their latest
    dispositions are `supported` and `refuted`.
  - `open`: both slots have an accepted assessment, and at least one latest
    disposition is `unresolved`.
- The actual run phase and stop reason, even when findings remain unresolved.
- Executed harness checks from recorded `verify` results, separate from
  agent-supplied evidence. A passing run check does not verify every claim.
- Execution limits, when Claude reported denied tool calls. Each line names the
  agent, its slot, and its total denied calls. Checks that needed those tools
  did not run. Successful loop and forced turns count, and so does the
  `review` recipe kickoff (`review-kickoff`, no slot). Seed extraction does not
  count. Execution limits cover only the current run's turns.
  Assessments inherited by `--continue` or `--resolve` keep their limits in the
  source run's `review.md`. The report never shows tool names or tool input.

Dispositions are agent assessments, not correctness labels. An agent can cite
inaccurate evidence or revise a claim's wording; the original wording remains
visible. Human acceptance is recorded separately through feedback.

`--report --json` emits schema 1, `kind: "duet.finding.report"`. Its `findings`
array contains each ID, claim, disposition, latest `assessments`,
`missing_evidence_slots`, `last_open_objection`, and `unresolved_reason`. The
reason is `single_assessment`, `disputed`, or `open` for an unresolved finding
and `null` for a `supported` or `refuted` finding. `unresolved_reasons` counts
the findings per reason; its keys are always present, with a count of `0` when
none applies. The reason follows the same latest assessments that decide the
disposition. `events` preserves the validated update sequence;
`structured_turns`, `executed_checks`, `summary`, `available`, and `truncated`
explain coverage and limits. `execution_limits` lists `agent`, `slot`, and
`denied_tool_calls` for each agent with denials.

These files contain private claim text, code references, paths, and commands.
They stay in the raw run directory and are never copied into central metrics.
Inspect them before sharing. Markdown renders supplied content as inert text.
`review.md` shows each finding ID as inline code in its heading, so that an ID
cannot render as a link or as emphasis. JSON and `--resolve` use the ID
unchanged.

## Continue an unresolved finding

```sh
duet --continue RUN_DIR_OR_ID --resolve L1
duet --continue RUN_DIR_OR_ID --resolve L1 --resolve P2 --turns 4
```

The examples above use the placeholder `RUN_DIR_OR_ID`. When a run ends with
unresolved findings, Duet prints a command that runs as written. It names the
real run directory and passes the parent of that directory as `--runs-dir`, both
shell-quoted. The continued run is stored beside the source run. Without
`--runs-dir`, `--continue` picks its own location, such as `./runs` of the
current directory. The line before the command gives the counts per reason:

```text
[duet] finding report: /path/to/runs/run/review.md (3 unresolved: 2 single_assessment, 1 disputed, 0 open)
[duet] continue unresolved findings: duet --continue /path/to/runs/run --runs-dir /path/to/runs --resolve L1 --resolve L2 --resolve L3
```

The command lists at most five IDs, in report order. When more are unresolved,
a third line gives the number of IDs left out and points to the report. Duet
prints no command when no finding is unresolved, when the finding history is
incomplete (a continuation cannot start from an incomplete ledger), or when the
run is a dry-run (its session IDs are not real). The per-finding example in
`review.md` uses the same real directory. A dry-run's `review.md` and
`duet --report` for a dry-run keep the `<run>` placeholder.
`duet --report` fills in the directory that it reads, so it stays correct after
you move or copy a run. A `review.md` file written during a run keeps the
directory from that time; regenerate it with `duet --report` after a move.
Without a known directory, the example keeps the `<run>` placeholder. For an
incomplete ledger, `review.md` and `duet --report` print "Continuation is
unavailable: the finding history is incomplete." in place of the example.

The printed command does not add `--trust-state`. If the saved run holds a
verify command, agent `extra_args`, or settings that widen authority,
`--continue` refuses to replay them and names the option. Inspect `state.json`
before you add it.

Only IDs currently marked unresolved can be selected. A focused continuation
defaults to two turns for a response and confirmation; an explicit `--turns`
must be at least two. It starts a new run, preserves prior finding IDs and
assessments as inherited events, and tells the agents which claims to revisit.
The original artifacts and stop reason remain intact. Normal `--continue`
also inherits the ledger when reports are enabled.

A focused continuation follows the [normal convergence and stop rules](USAGE.md#stop-conditions-and-force-prompt).
A focused continuation can still finish unresolved or fail. It does not promise
agreement, automatically grant extra turns, or declare an unresolved finding
correct. An incomplete ledger at the reporting limit cannot be continued with
finding reports enabled; inspect it and start a fresh review instead.

## Record an optional human outcome

```sh
duet --feedback RUN_DIR_OR_ID --usefulness useful --decision corrected_comment
duet --stats
duet --stats --json
```

`--usefulness` accepts `useful`, `mixed`, `not_useful`, or `unknown`.
`--decision` accepts `accepted_finding`, `rejected_finding`, `corrected_comment`,
`no_change`, or `not_applied`. Both are required. Choose them only when they
describe your own experience; neither field establishes technical correctness.

Feedback is opt in and records the latest outcome for a run. Repeating the
command updates that run's record instead of increasing its count. A nonblocking
per-run lock asks simultaneous submissions to retry. The command writes
`feedback.json` locally and, when central collection is enabled, a curated copy
under `~/.duet/metrics/feedback/<UUID>.json`. Each copy contains only an opaque
ID, timestamp, run kind, and those two choice labels. It contains no free text,
project names, paths, code, prompts, or session IDs. Nothing is uploaded.

`DUET_METRICS=0` or a saved `metrics_enabled: false` keeps new feedback local.
Neither deletes earlier central feedback. A central write failure preserves the
local outcome and reports that limitation. Run the feedback command again to
retry publication when the store becomes available.

Stats report feedback separately from timing and provider usage. JSON separates
live, test, dry-run, and unknown cohorts and counts skipped feedback files; the
human summary shows live usefulness counts. These are optional responses, not
an acceptance rate over all users or proof that the tool saves time.

## Structured reply contract

````text
```duet-findings
{"findings":[{"id":"L1","claim":"The empty input path fails.","disposition":"unresolved","evidence":["reader.py:12"],"objection":"An empty-input check is still missing."}]}
```
````

Use exactly one top-level triple-backtick block labelled `duet-findings`. Keep
LGTM rationale and the sentinel outside it. An empty `findings` array means no
updates. The block is bounded at 64 KiB and 50 unique IDs. Claims, objections,
and each of up to eight evidence strings are limited to 2,000 characters. An ID
has 1 to 32 ASCII characters: it starts with a letter or digit, then uses
letters, digits, `.`, `_`, or `-`. Agents use `L<n>` or `P<n>` for claims that
they introduce. IDs are case-sensitive. An agent reuses a valid ID that the
task, the kickoff, or the other agent gives to a claim. When a label is not a
valid ID, such as `#1`, the agent allocates `L<n>` or `P<n>` and names the label
in the claim. Malformed blocks are rejected as a whole. Reports cap
history/events at 1,000 and expose truncation explicitly.

The [review evidence fixture](../examples/review-evidence/README.md) supplies
fresh toy code and repeatable checks for a defect, a refuted comment, and an
unresolved requirement. Its authored expectations are an illustration, not
measured agent performance.
