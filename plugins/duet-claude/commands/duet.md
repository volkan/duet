---
description: Run Claude Code's real /review through the duet two-agent harness, or hand off another command's output to duet. Wraps `duet --task-from-cmd <shell>` so /review, gh, npm test, cat error.log, or another upstream tool can drive a two-agent loop. Use when asked to "duet on …", "run duet with …", "kick off duet from …", "run /review through duet", or "have duet pick up <some output>".
---

# /duet

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
command, such as `--partner gemini:coder`, `--turns 8`, or `--config foo.yaml`,
must win.

After spawn, print the run dir + the `duet --status <run_dir>` hint once the
`[duet] run: ...` or `[duet] run dir: ...` line appears.

Important: with the empty-argument default, `claude -p /review` runs as
`--task-from-cmd` before duet allocates a run directory. During that kickoff
phase, `.duet/runs/*` may not exist yet. Do not report that no run was created
until the command exits or prints an error. If Claude Code backgrounds the
command before the run-dir line appears, monitor the background task output for
`[duet] run:` or `[duet] run dir:`.

Notes:

- The first quoted token of `$ARGUMENTS` is the shell command duet runs
  for the kickoff. Anything after that is forwarded to duet (e.g.
  `--turns 8`, `--reasoning high`, `--lead claude:reviewer`).
- Plain `/duet` seeds the loop from Claude Code's real `/review` skill.
  `/review` produces the kickoff; duet manages the Codex/Claude turns after
  that.
