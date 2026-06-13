# AGENTS.md

This file provides guidance to Codex when working with code in this repository.

## Read first

Before making changes, read `CLAUDE.md`. It contains the authoritative project
overview, commands, architecture notes, backend quirks, and hard constraints for
modifying this repository.

If this file and `CLAUDE.md` ever disagree, follow `CLAUDE.md`.

## Codex workflow

- The default branch is `main`; do not use `master` in commands, PR bases, or
  documentation examples for this repo.
- Do not commit or push feature work directly to `main`. Before committing, make
  sure you are on a topic branch:

  ```bash
  git fetch origin
  git switch main
  git pull --ff-only
  git switch -c <type>/<short-topic>
  ```

  If you already made local edits on `main`, switch them onto a branch before
  committing with `git switch -c <type>/<short-topic>`.
- Use Conventional Commits, run the relevant local gates from `CLAUDE.md`, then
  push the branch and open a PR:

  ```bash
  make ci
  git push -u origin HEAD
  gh pr create --base main --fill
  ```

  For packaging/plugin changes, also run `make package-check` and
  `make plugin-check` before pushing.
- If a direct push to `main` is rejected or GitHub says required checks are
  expected, do not bypass the rule. Move the work to a topic branch and open a
  PR so the six required checks run normally.
- Keep changes scoped to the requested task and consistent with the existing
  single-file Python harness design.
- Preserve the constraints documented in `CLAUDE.md`, especially the stdlib-only
  runtime, atomic writes, prompt-template handling, subprocess process-group
  behavior, and Codex resume flag handling.
- Run `make test` after behavior changes to `duet.py` or CLI/config handling.
