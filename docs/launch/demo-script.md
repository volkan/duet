# asciinema demo script — duet reviews its own packaging branch

Goal: a final cast under 90 seconds showing duet doing a real review of the
branch that adds `pyproject.toml` (the package this demo gets installed
from). Codex is the reviewer (partner, speaks first), Claude is the coder
(lead, edits in a worktree), `make ci` is the mechanical convergence gate.
The verify command runs inside the worktree (`effective_verify_cwd` prefers
the worktree path), so the gate checks the edited tree, not the host
checkout.

## Pre-flight (off camera)

```bash
cd ~/workspace/agent2agent/duet
git switch feat/p0-distribution       # the packaging branch (PR #11)
make ci                               # the gate must be able to pass
uvx --from . duet --help              # pre-warm uv's build cache
claude --version && codex --version   # both CLIs on PATH and logged in
export PS1='$ '                       # plain prompt
clear
```

- Terminal 100x28, large font, dark theme. Resize *before* recording —
  asciinema bakes in the initial geometry.
- The packaging change must be committed at `HEAD`: fresh worktrees start
  from committed `HEAD`, so uncommitted work would be invisible to the coder.
- If the branch has multiple commits, swap the kickoff command below for
  `git diff --stat --patch --no-ext-diff main...HEAD`.

## Record

```bash
asciinema rec duet-packaging-review.cast --idle-time-limit 2 --cols 100 --rows 28
```

`--idle-time-limit 2` collapses think-time automatically; `--recap` already
suppresses the verbose stderr mirror, so most dead air is the redrawn
`running [mm:ss]` line — cheap to cut later.

## The script, beat by beat

Target timeline is for the *edited* cast; the raw recording will run
5–15 minutes.

| beat | on screen | target |
|---|---|---|
| 1 | context: branch + commit | 0:00–0:06 |
| 2 | the one command | 0:06–0:20 |
| 3 | turn 1 — codex reviewer critiques | 0:20–0:35 |
| 4 | turn 2 — claude coder fixes in worktree | 0:35–0:50 |
| 5 | verify gate: `make ci` runs, passes | 0:50–1:05 |
| 6 | pair convergence + force> Enter + outro | 1:05–1:25 |

### Beat 1 — context (0:00–0:06)

```bash
git log --oneline -2
```

Shows the packaging commit at `HEAD`. One command, no narration needed — the
commit message ("feat: package duet-cli for PyPI" or similar) is the setup.

### Beat 2 — the one command (0:06–0:20)

Type it (typing reads better than pasting at 1x; keep it brisk):

```bash
uvx --from . duet --recap \
    --task-from-cmd 'git show --stat --patch --no-ext-diff HEAD' \
    --lead claude:coder \
    --partner codex:reviewer \
    --worktree --worktree-for lead \
    --verify-cmd 'make ci' \
    --turns 6
```

`uvx --from .` is the flourish: the duet reviewing this branch is running
from the package the branch creates. If uv misbehaves on camera, fall back to
`./duet.py` with identical flags — the demo still lands.

KEEP: the run banner (run dir, agents, worktree path, verify cmd echo).
That's the frame for everything that follows.

### Beat 3 — turn 1, codex reviewer (0:20–0:35)

What appears:

```text
Turn 01 | codex-partner (reviewer) · running [00:14]
```

…then the line is replaced by the recap block:

```text
Turn 01 | codex-partner (reviewer) · 96s · 1.8KB · 43 lines
RECAP:  Flagged pyproject gaps: classifier set, script name vs package name.
FILES:  pyproject.toml, README.md
STATUS: requesting-changes · convergence: no
```

CUT: everything between ~3s after the command starts and ~2s before the
recap block lands. Keep a couple of timer redraws (e.g. `[00:14]` →
`[01:02]`) so viewers see liveness, then jump-cut to the block.

### Beat 4 — turn 2, claude coder in the worktree (0:35–0:50)

Same shape: running line, then recap block with `STATUS: ready-for-review`.
KEEP the `FILES:` line — it shows real edits happening in `runs/<id>/wt/`,
not the host checkout. If duet prints the worktree handoff/diff summary
notice here, keep one line of it.

CUT: the wait, same rule as beat 3.

### Beat 5 — the verify gate (0:50–1:05)

This is the money shot. When a reply carries the sentinel + rationale, duet
prints:

```text
[duet] verify turn 03: make ci (cwd=.../runs/<id>/wt)
```

KEEP: that line, a beat of `make ci` output scrolling, and the success
block. The story in one frame: *agents agreeing is not enough; the test
suite has to agree too.* If `make ci` happens to fail on camera, even
better — keep the failure block being handed to the next turn, then cut to
the retry passing. An honest red-then-green beats a staged all-green.

### Beat 6 — pair convergence and outro (1:05–1:25)

KEEP:

- the second back-to-back LGTM turn summary (`convergence: yes`),
- the `force>` prompt — press Enter after ~2s (shows the human stays in the
  loop without making the viewer wait),
- the final summary: run dir, transcript path, and the printed worktree
  merge/review/drop commands.

Optional, only if under budget (~5s):

```bash
duet --status <run-id>
```

`finished_reason: converged` plus the recap path is a clean closing frame.
End the recording (`exit` or Ctrl-D).

## Post-processing

1. Raw cast will be minutes long even with `-i 2`. Trim with
   `asciinema-edit cut --start <t1> --end <t2>` per the CUT notes above
   (the `.cast` file is NDJSON of timestamped events — hand-editing works
   too, just keep timestamps monotonic).
2. Sanity-check total length < 90s; beats 3–4 are the first place to
   tighten (one timer redraw each is enough).
3. `asciinema upload duet-packaging-review.cast`, set the title to
   "duet: Codex reviews, Claude fixes, make ci gates convergence".
4. Paste the link into `docs/launch/showhn.md`'s placeholder line.

## Failure modes while recording

- **Agents converge on turn 2–3 without drama** — fine, shorter demo; skip
  the beat-4 tightening.
- **Loop hits `--turns 6` without converging** — the `force>` prompt still
  appears; type one line of steering feedback on camera (it's a real
  feature) or re-record.
- **Codex sandbox blocks something network-y** — expected; the review task
  is diff-only on purpose. Don't add network flags for the demo.
- **A turn stalls** — `duet --status` from a second terminal is the
  diagnostic, but don't show a stall in the launch cast; re-record.
