# TODO

## P0 - Reasoning flags must not surprise users

- [x] Fix `--reasoning max` + `--codex-fast` precedence.

  Implemented: `--codex-fast` is now role-scoped to `codex:coder` agents.
  `call_agent` recomputes `fast = cfg.codex_fast and agent.role == "coder"`
  per turn so a `--lead codex:planner --codex-fast` run no longer silently
  downgrades the planner. Config validation in `main()` prints a stderr
  WARNING and clears `cfg.codex_fast` when no codex:coder is present, and
  prints a softer `note:` listing non-coder codex agents when scoping is
  partial. Escape hatch (per-agent `reasoning_effort: low`) is documented
  for users who really want fast on a non-coder role.

  Smoke coverage: `codex-fast scoped to coder`,
  `codex-fast warns when no codex coder`, `codex-fast partial note`.

  Docs synced: `CLAUDE.md` (Codex fast mode override), `docs/USAGE.md`
  (flag-table row + Codex fast mode subsection), `duet.example.yaml`.

## P1 - Tighten worktree handoff wording

- [x] Rename `worktree_handoff_block` to `_worktree_handoff_block`.
- [x] Soften the handoff claim for clean exploration turns ("Any code changes
      for this turn …").
- [x] Remove the suggested `make -C <worktree> test` command from the handoff.
- [x] Restore the worktree basename in the heading
      (`#### worktree changes ({wt_path.name})`), full absolute path stays in
      the block.

  Smoke coverage strengthened in `worktree handoff names review target` to
  assert the new basename heading, the softened wording, and the absence of
  the `make -C` line.

## P1 - Make worktree verification more structural

- [ ] Decide whether prompt guidance is enough.

  The current handoff is a prompt nudge. It tells the receiving agent where to
  verify, but a model can still ignore it.

  Possible structural improvements:

  - Put an explicit `review_command` field in the transcript/handoff.
  - Add a known read-only path for the non-worktree agent to inspect.
  - Add a mode where both agents can read the worktree, while only the selected
    worktree agent writes there.

  Acceptance criteria:

  - A reviewer should not be able to conclude "no changes" from the host repo's
    clean `git status` when the worktree has edits.
  - The transcript should preserve enough information for a human to reproduce
    the review with `git -C <worktree> ...`.

## P2 - Expand regression coverage around worktree handoff

- [ ] Add coverage for branch/path plumbing beyond direct helper calls.

  Current smoke coverage checks `append_worktree_diff(..., wt_branch)` directly.
  That is useful but does not catch a future call site that forgets to pass the
  branch.

  Candidate tests:

  - A lightweight real-git worktree smoke case that verifies the transcript
    contains `Branch: duet/<run_id>`.
  - A unit-style smoke that drives the run loop with fake agents and `--worktree`
    enabled.

- [ ] Keep an eye on prompt size.

  The handoff adds roughly a dozen lines per worktree turn. This is acceptable
  now, but should be considered if the diff summary grows or token budgets get
  tighter.

## P2 - Clarify force prompt behavior

- [x] Improve the `force>` prompt copy.

  Implemented: prompt now reads "Press Enter to finish, or type feedback to
  force another turn (your text is appended as a human-feedback message and
  sent to the next agent)". `docs/USAGE.md` Stop conditions example updated
  to the same wording.

- [ ] Review stale forced-turn pid handling.

  A crashed or killed forced turn can leave `turn-NN-forced-*.pid`, causing
  `duet --status` to report the run as stuck. That is correct for crash
  detection, but the force path should be checked for cleanup parity with normal
  turns.
