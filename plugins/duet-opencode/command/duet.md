---
description: Run the duet two-agent CLI harness from OpenCode. Wraps `duet --task-from-cmd <shell>` so /review, gh, npm test, cat error.log, or another upstream tool can drive a two-agent loop. With no arguments it seeds from `claude -p /review`. Use when asked to "duet on …", "run duet with …", or "have duet pick up <some output>".
agent: build
---

# /duet

Run the installed `duet` CLI to pair two CLI coding agents in a reviewed loop
from the current project. This command does not install `duet`, `claude`, or
`codex` — it shells out to the `duet` CLI on your PATH.

## Prerequisite check

First confirm the `duet` CLI is on PATH:

```bash
command -v duet
```

If it is not found, do NOT improvise an alternative. Stop and tell the user:

> The `duet` CLI is not on PATH. Install it with `pipx install duet-cli`
> (or `uv tool install duet-cli`, or `python3 -m pip install --user duet-cli`;
> the package is `duet-cli`; the command it installs is `duet`), or clone
> the repo (https://github.com/volkan/duet) and run `make install` (symlinks
> `duet.py` to `~/.local/bin/duet`; make sure `~/.local/bin` is on PATH).
> Then re-run `/duet`.

If `$ARGUMENTS` is empty, also confirm the default recipe's agent CLIs are on
PATH:

```bash
command -v claude
command -v codex
```

If `claude` is missing, stop and tell the user:

> The default `/duet` recipe runs `claude -p /review` first, but `claude` is
> not on PATH. Install or authenticate Claude Code, then re-run `/duet`.

If `codex` is missing, stop and tell the user:

> The default `/duet` recipe uses `codex:coder` as the implementation agent,
> but `codex` is not on PATH. Install Codex, or re-run `/duet` with a different
> partner/config that does not require Codex.

## Run

If `$ARGUMENTS` is empty, run exactly:

```bash
duet --recipe review
```

Otherwise run exactly:

```bash
duet --cwd "$(pwd)" --runs-dir "$(pwd)/.duet/runs" \
  --partner codex:coder <conditional worktree defaults> \
  --task-from-cmd '<upstream shell command>' <remaining duet flags>
```

Replace `<conditional worktree defaults>` before executing; never pass that
placeholder literally. Split the first quoted value in `$ARGUMENTS` from the
remaining duet flags and examine only those remaining flags, not text inside
the upstream command:

1. Add `--worktree` only when the remaining flags contain none of
   `--worktree`, `--no-worktree`, `--worktree-path PATH`, or
   `--worktree-path=PATH`.
2. Add `--require-worktree` only when worktree use is effective and the
   remaining flags contain neither
   `--require-worktree` nor `--allow-worktree-fallback`.
3. If the user supplied conflicting flags (`--worktree` with
   `--no-worktree` or `--worktree-path`, or `--require-worktree` with
   `--allow-worktree-fallback`), stop and report the conflict instead of
   reordering or rewriting their flags.

Worktree use is effective when the flags select `--worktree` or
`--worktree-path`, or when step 1 adds `--worktree`. A lone `--no-worktree`
disables it. If `--no-worktree` and `--require-worktree` appear together
without `--worktree-path`, report that invalid combination too.

No topology/strictness override therefore synthesizes
`--worktree --require-worktree`; `--no-worktree` synthesizes nothing;
`--allow-worktree-fallback` synthesizes only `--worktree`; and
`--worktree-path PATH` synthesizes only `--require-worktree`. Do not pre-add
`--recap`, because it would conflict with an explicit `--no-recap`. Keep
`--partner codex:coder` before the remaining flags so an explicit partner can
replace that non-exclusive default.

After spawn, print the run dir + the `duet --status <run_dir>` hint once the
`[duet] run: ...` or `[duet] run dir: ...` line appears.

The review recipe allocates its run directory and writes initial `state.json`
before starting `claude -p /review`, so the run is observable during kickoff.

Notes:

- The first quoted token of `$ARGUMENTS` is the shell command duet runs for the
  kickoff. Anything after that is forwarded to duet (e.g. `--turns 8`,
  `--reasoning high`, `--lead claude:reviewer`, `--partner opencode:coder`).
- Plain `/duet` seeds the loop from Claude Code's real `/review` skill.
  `/review` produces the kickoff; duet manages the Codex/Claude turns after
  that.
- duet itself can also drive `opencode` as a backend (`--partner opencode:coder`),
  so you can have OpenCode be one of the two looped agents, not just the host.
