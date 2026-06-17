---
description: Run the duet two-agent CLI harness from OpenCode. Wraps `duet --task-from-cmd <shell>` so /review, gh, npm test, cat error.log, or another upstream tool can drive a two-agent loop. With no arguments it seeds from `claude -p /review`. Use when asked to "duet on …", "run duet with …", or "have duet pick up <some output>".
agent: build
---

# /duet

Run the installed `duet` CLI to pair two CLI coding agents in a reviewed loop
from the current project. This command does not install `duet`, `claude`,
`codex`, or `gemini` — it shells out to the `duet` CLI on your PATH.

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
duet --recap --cwd "$(pwd)" --runs-dir "$(pwd)/.duet/runs" --lead claude:reviewer --partner codex:coder --worktree --turns 6 --task-from-cmd 'claude -p /review'
```

Otherwise run exactly:

```bash
duet --cwd "$(pwd)" --runs-dir "$(pwd)/.duet/runs" --partner codex:coder --worktree --task-from-cmd $ARGUMENTS
```

The defaults (`--partner codex:coder --worktree`) appear before `$ARGUMENTS`
on purpose. Any explicit flags the user passes after the first quoted shell
command, such as `--partner opencode:coder`, `--turns 8`, or `--config foo.yaml`,
must win.

After spawn, print the run dir + the `duet --status <run_dir>` hint once the
`[duet] run: ...` or `[duet] run dir: ...` line appears.

Important: with the empty-argument default, `claude -p /review` runs as
`--task-from-cmd` before duet allocates a run directory. During that kickoff
phase, `.duet/runs/*` may not exist yet. Do not report that no run was created
until the command exits or prints an error.

Notes:

- The first quoted token of `$ARGUMENTS` is the shell command duet runs for the
  kickoff. Anything after that is forwarded to duet (e.g. `--turns 8`,
  `--reasoning high`, `--lead claude:reviewer`, `--partner opencode:coder`).
- Plain `/duet` seeds the loop from Claude Code's real `/review` skill.
  `/review` produces the kickoff; duet manages the Codex/Claude turns after
  that.
- duet itself can also drive `opencode` as a backend (`--partner opencode:coder`),
  so you can have OpenCode be one of the two looped agents, not just the host.
