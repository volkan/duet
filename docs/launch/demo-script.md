# Review evidence demo storyboard (80 seconds)

This edited recording uses the public `examples/review-evidence/` fixture and a
real OpenCode toy review followed by a focused continuation. It demonstrates
how Duet keeps review claims, cited evidence, any executed checks, and
unresolved requirements visible. The fixture is a teaching example with one
intentional defect; it is not a performance benchmark or a claim about model
quality.

The playback artifacts are `docs/demos/finding-review.html`,
`docs/demos/finding-review.mp4`, `docs/demos/finding-review.png`, and
`docs/demos/finding-review.vtt`. The capture
metadata should record the source snapshot, review/continuation labels, state
file SHA256 values, backend/model, commands, and date. Do not publish raw run
directories or session paths. This is an edited replay from saved reports, not
a whole-screen recording or automatic exact source attestation.

## Pre-flight (off camera)

From the repository root, verify the public fixture and have the OpenCode CLI
available. Preserve the complete raw run artifacts. The source snapshot and
real run metadata belong in the capture record, not in this storyboard.
The fixture’s `initial-review.md` and `expected.json` intentionally disclose
the expected assessments, so introduce the recording as a workflow and
evidence-boundary demonstration rather than blind finding discovery.

```sh
python3 examples/review-evidence/check_fixture.py
```

## Storyboard and narration

| time | screen | narration |
|---|---|---|
| 0:00–0:09 | Scene 1: Fixture README and `initial-review.md`; show claim IDs L1, L2, L3. | “This public fixture gives us three review questions: an intentional batching defect, a plausible concern the code refutes, and a requirement the code cannot answer.” |
| 0:09–0:20 | Scene 2: Saved review metadata; show two OpenCode agents, 2 turns used of 4 allowed, and the requested model. | “Two agents inspect the same claims, preserve their IDs, and cite what the repository can show.” |
| 0:20–0:32 | Scene 3: Separately rerun `check_fixture.py`; highlight L1. | “Five items at capacity two are counted as three full batches, but only two are full. Exit zero confirms the authored demonstration, including the intentional defect.” |
| 0:32–0:44 | Scene 4: Saved L2 assessment; show `--report` editorial replay. | “The code uses `sorted()`, so the caller’s list stays unchanged. Refuted is the recorded assessment; the code and separate check remain inspectable.” |
| 0:44–0:56 | Scene 5: Saved report; show L1 supported, L2 refuted, L3 unresolved, and `Executed harness checks: none recorded`. | “No harness check was recorded in these runs because no verify command was configured. Agent agreement is evidence to inspect, not proof.” |
| 0:56–1:10 | Scene 6: Saved continuation replay; show `duet --continue REVIEW --resolve L3`, two turns used of two allowed. | “The continuation focuses on L3. It preserves the missing production requirement instead of turning an unknown into a verdict.” |
| 1:10–1:20 | Scene 7: Show the reproduction command and public fixture location; the HTML source panel below includes metadata and hashes. | “This is one authored fixture and two saved real runs, replayed and edited for pace. It demonstrates the workflow, not blind discovery, accuracy, human usefulness, or time saved.” |

## Commands shown on camera

Use the repository script and the portable config to repeat the captured
public task and agent settings. Directory and collection-setting differences
are documented in the [demo record](../demos/README.md):

```sh
python3 duet.py --config examples/review-evidence/opencode-review.json
```

The saved run commands, shown as editorial labels rather than raw paths, are:

```sh
python3 duet.py --report REVIEW
python3 duet.py --continue REVIEW --resolve L3
```

If the first report does not mark L3 unresolved, show the actual report and do
not force the continuation. The final frame must reflect the real disposition.

## Editing and evidence boundaries

Keep an edited replay of the saved review and continuation reports, the claim
IDs, the literal “No executed harness checks recorded” line, the separately
rerun deterministic check, and the focused continuation. The published replay
is assembled from saved reports rather than a terminal screen capture. Cut
waiting and repeated redraws, but label review and continuation outputs clearly
and do not splice them into a synthetic run. Publish only review/continuation
labels, state SHA256 values, and relevant backend/model metadata; omit raw run
directories and session paths. The fixture’s `initial-review.md` and
`expected.json` disclose the expected assessments, so say that this
demonstrates the workflow and evidence boundaries, not blind finding discovery.
The source snapshot records fixture bytes and state digests directly. The runs
used a development working tree later committed as `60d0d86`, but runtime did
not save a git revision; that commit is an implementation reference, not a
run-attested source revision. Do not present it as exact source attestation.
Do not show or claim a human feedback outcome; `--feedback` is optional and
only records a person’s own experience after the run. Do not describe the
fixture as a benchmark.
