# AGENTS.md

This file provides guidance to Codex when working with code in this repository.

## Read first

Before making changes, read `CLAUDE.md`. It contains the authoritative project
overview, commands, architecture notes, backend quirks, and hard constraints for
modifying this repository.

If this file and `CLAUDE.md` ever disagree, follow `CLAUDE.md`.

## Codex workflow

- Follow `docs/AGENT_WORKFLOW.md` for branch creation, local gates, PR creation,
  and merge/release-to-main steps. The default branch is `main`, never
  `master`; do not push directly to `main` for normal work.
- Keep changes scoped to the requested task and consistent with the existing
  single-file Python harness design.
- Preserve the constraints documented in `CLAUDE.md`, especially the stdlib-only
  runtime, atomic writes, prompt-template handling, subprocess process-group
  behavior, and Codex resume flag handling.
- When a change affects agent-visible harness instructions, update
  `plugins/duet/skills/duet/SKILL.md` and the affected host guides in the same
  commit. See `CLAUDE.md` for the detailed scope. Do not update the skill for
  internal-only refactors or an accepted model ID with unchanged behavior.
- Run `make ci` for every change (the minimum gate in `CLAUDE.md`), plus the
  risk-scaled real-loop and package checks listed there.
