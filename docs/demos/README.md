# Duet demos

For examples from actual project work, start with the
[anonymized cases](../REAL_CASES.md). The [review workflow demo](#review-workflow-demo)
below shows how to inspect findings and continue an unresolved review using a
public toy fixture. The [earlier GIF overview](#earlier-loop-overview) explains
the two-agent mechanism.

## Review workflow demo

[![Watch the edited 80-second review demo](finding-review.png)](finding-review.mp4)

[Watch or download the MP4](finding-review.mp4) ·
[Download the interactive HTML](finding-review.html) ·
[Captions](finding-review.vtt) · [Source snapshot](finding-review.source.json)

Download `finding-review.html` and open it in a browser. Playback, chapter
navigation, scrubbing, and the embedded source work offline without an agent
CLI, server, CDN, or account. GitHub displays HTML as source; download the raw
file to play it. The silent MP4 is 80 seconds, 1280×720, with readable text in
each scene; the WebVTT file provides accompanying captions.

This is an **edited replay of saved findings**, not a terminal screen recording.
It shows one intentional defect, one refuted comment, and an unresolved
requirement after a focused follow-up. Both agents could read the authored
assessments in `initial-review.md` and `expected.json`. The demo illustrates the
workflow; it does not measure blind discovery, comparative accuracy, human
acceptance, or time saved. The code defect is left intact for reproduction.

## Captured evidence

Two related real OpenCode runs were captured on September 5, 2026:

| Setting | Review | Focused continuation |
|---|---|---|
| Pairing | Two OpenCode reviewer sessions | Same resumed sessions |
| CLI version | 1.18.12 | 1.18.12 |
| Requested model, both slots | `opencode/big-pickle` | `opencode/big-pickle` |
| Reported model revision | Unknown | Unknown |
| Reasoning | Native default; effective value unknown | Native default; effective value unknown |
| Turns used / allowed | 2 / 4 | 2 / 2 (default) |
| Recorded elapsed | 86.49 seconds | 60.39 seconds |
| Focus | L1, L2, L3 | L3 |
| Stop reason | `converged` | `converged` |
| Final assessments | L1 supported; L2 refuted; L3 unresolved | Same |
| Harness checks recorded | None | None |

The playback's 80-second timeline is editorial. Actual elapsed values above
include each run's wall time; neither estimates time saved. Both runs are
classified as `test`. No optional human feedback is fabricated or included.
The deterministic fixture check was rerun separately during demo preparation
on September 6; its exact output is in the source snapshot. Exit zero confirms
the intended demonstration, including reproduction of the defect. Agent-cited
checks remain separate from executed harness checks.

`finding-review.source.json` contains the complete finding-report JSON for both
runs, their per-turn timing and allowlisted metadata, original state SHA-256
digests, the captured public fixture source with checksums, and the separate
check result. It was manually inspected before inclusion. It excludes local
run-directory names, project IDs, session IDs, raw state, and full transcripts.
This is a curated public example, not a general anonymization utility.

The runs used the development working tree later committed in `60d0d86` and
merged as `fff1709`. Runtime state did not save a Git revision, so this commit
is an implementation reference rather than a run-attested revision. Its
`0.2.12` version string does not mean the published 0.2.12 package included the
finding features. Fixture bytes and state digests were captured directly.

## Reproduce the checks or run a new review

Use a checkout containing this demo and the finding-report workflow. From the
repository root:

```sh
python3 examples/review-evidence/check_fixture.py
python3 duet.py --config examples/review-evidence/opencode-review.json
```

Only the second command calls agents and requires OpenCode with access to the
requested model. The portable config preserves the captured public task and
agent settings, replaces the original copied-fixture directory with the public
example, and disables central metrics. Model availability and output may vary.

Use the actual run directory printed by your new run:

```sh
python3 duet.py --report YOUR_RUN_DIRECTORY
python3 duet.py --continue YOUR_RUN_DIRECTORY --resolve L3
```

Continue L3 only if your actual report leaves it unresolved. The continuation
creates a new run; inspect its printed directory for the new report. The video
uses `REVIEW` and `REVIEW_CONFIG` as display placeholders and shortens commands
and outputs for readability. It does not show literal terminal output except
where a saved evidence excerpt is labeled.

## Rebuild the playback

The HTML builder uses only Python's standard library and the already inspected
snapshot. It does not read raw runs or launch agents:

```sh
python3 scripts/build_review_demo.py
python3 scripts/build_review_demo.py --check
```

To regenerate the poster, video, and captions, install Playwright for Node and
its Chromium browser plus `ffmpeg`, then run:

```sh
node scripts/render_review_demo.cjs
```

These are optional documentation tools, not Duet runtime dependencies. The
renderer accepts `DUET_CHROMIUM` for an existing browser executable, `PYTHON`
for the Python executable, and `FFMPEG` for ffmpeg. It checks all scenes at
desktop and mobile widths, playback controls, source identity, and absence of
network requests before exporting. Browser screenshots remain in a temporary
QA directory printed by the renderer.

Preparation checks also verified that the published reports exactly match the
original saved reports, the fixture hashes match, and the portable config
loads with the documented settings. One earlier config-validation attempt
timed out after the then-current CLI ignored `--dry-run` alongside `--config` and
started an agent; that process was stopped and no output from the attempt is
used in this demo. The corrected check passed with `dry_run: true` in a
temporary config. Current source honors `--dry-run` with `--config`, even when
the file sets `dry_run: false`, and skips kickoff commands too. Older releases
need `dry_run: true` in the config to prevent agent turns, but still execute
`task_from_cmd` if present; use current source to preview those configs without
running their kickoff commands. The recorded demo artifacts are unchanged.

The [storyboard](../launch/demo-script.md) tracks the seven scenes. To change
the recorded evidence, manually inspect a new public capture and update its
metadata, captions, and narrative together; never relabel a run to match an
expected outcome.

## Earlier loop overview

The original [39-second GIF overview](../assets/duet-deck.gif) explains
alternating turns, separate session memory, agreement, and the optional
verification gate. It is a slide presentation, not a recorded agent run.

Keep it as optional background after the workflow demo. It predates finding
reports and contains older implementation details, including the line-count
estimate. Use the [usage guide](../USAGE.md) for current behavior. Agreement
alone does not establish correctness.
