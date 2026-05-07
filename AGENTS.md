# AGENTS.md

This file provides guidance to Codex when working with code in this repository.

## Read first

Before making changes, read `CLAUDE.md`. It contains the authoritative project
overview, commands, architecture notes, backend quirks, and hard constraints for
modifying this repository.

If this file and `CLAUDE.md` ever disagree, follow `CLAUDE.md`.

## Codex workflow

- Keep changes scoped to the requested task and consistent with the existing
  single-file Python harness design.
- Preserve the constraints documented in `CLAUDE.md`, especially the stdlib-only
  runtime, atomic writes, prompt-template handling, subprocess process-group
  behavior, and Codex resume flag handling.
- Run `make test` after behavior changes to `duet.py` or CLI/config handling.
