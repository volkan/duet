# Review evidence fixture

This small, invented Python example makes three review outcomes inspectable:
one reproduced defect, one refuted comment, and one claim requiring information
outside the code. It contains no project data or agent transcripts.

`initial-review.md` and `expected.json` are authored, illustrative fixtures.
They are not measured agent results, a benchmark, or the finding-report output
schema. Supported/refuted/unresolved are review assessments justified by the
available evidence, not universal truth labels.

The [80-second demo and inspectable source](../../docs/demos/README.md) replay
an actual OpenCode review of this fixture and its focused continuation. The
authored expectations were available to both agents; it is not a blind test.

## Run the deterministic checks

From the repository root, using Python 3.9 or newer:

```sh
python3 examples/review-evidence/check_fixture.py
```

This command needs only the standard library and does not launch agent CLIs.
Exit zero means all expected demonstrations were observed, **including the
intentional defect**: five items at capacity two produce three full batches,
while the stated requirement permits only two. It does not mean `toy.py`
passes correctness checks. If the toy defect is fixed, this reproduction check
should fail until the illustrative expectations are deliberately updated.

The same check confirms that sorting preserves the caller's list. It records
the actual lexical ordering without inventing a production requirement for
natural ordering. Resolving L3 requires that missing requirement.

## Exercise Duet's review workflow

To use the captured demo's OpenCode pairing from the repository root:

```sh
python3 duet.py --config examples/review-evidence/opencode-review.json
```

This requires OpenCode and access to the configured model. The config uses the
recorded public task, reviewer roles, four-turn limit, 180-second timeout, and
native reasoning defaults. It points at this fixture and disables central
metrics for the reproduction. New model responses can differ. The
[demo record](../../docs/demos/README.md) explains the captured settings and
how to inspect or continue your new run.

The following optional commands use the repository's own `duet.py`. They need
the configured agent CLIs and their normal authentication. Actual responses
can differ from the authored fixture; inspect their evidence.

```sh
python3 duet.py --finding-reports --turns 2 --no-metrics --task 'Review examples/review-evidence/toy.py against its documented contract. Evaluate the illustrative claims L1, L2, and L3 in examples/review-evidence/initial-review.md. Run python3 examples/review-evidence/check_fixture.py and inspect its assertions: exit zero demonstrates an intentional defect, not correctness. Preserve these claim IDs in the review. Report supporting or refuting evidence and missing requirements. Do not change files.'
```

Copy the actual run directory printed by that command into `RUN_DIR` below:

```sh
RUN_DIR='runs/REPLACE_WITH_ACTUAL_RUN_DIRECTORY'
python3 duet.py --report "$RUN_DIR"
python3 duet.py --continue "$RUN_DIR" --resolve L3 --turns 2
python3 duet.py --feedback "$RUN_DIR" --usefulness useful --decision corrected_comment
```

The continuation requests another review pass focused on L3, if that ID remains
unresolved in the actual report. Choose an unresolved ID from that report. Use the new run
directory printed by the continuation when inspecting its report. The
feedback command above illustrates a human judgment about the original run;
submit it only if that judgment describes what actually happened. A
`corrected_comment` decision fits a review that corrected or retracted a
comment, such as the unsupported L2 concern. It does not claim a code fix.

For installed-product use, the same options can be passed to `duet` instead
of `python3 duet.py`. Repository checks should keep using the root script.
