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
> (the package is `duet-cli`; the command it installs is `duet`), or clone
> the repo (https://github.com/volkan/duet) and run `make install` (symlinks
> `duet.py` to `~/.local/bin/duet`; make sure `~/.local/bin` is on PATH).
> Then re-run `/duet`.

## Run

If `$ARGUMENTS` is empty, run exactly:

```bash
duet --recap --task-from-cmd 'claude -p /review' --cwd "$(pwd)" --lead claude:reviewer --partner codex:coder --worktree --turns 6
```

Otherwise run exactly:

```bash
duet --task-from-cmd $ARGUMENTS --cwd "$(pwd)" --partner codex:coder --worktree
```

After spawn, print the run dir + the `duet --status <run_dir>` hint.

Notes:

- The first quoted token of `$ARGUMENTS` is the shell command duet runs
  for the kickoff. Anything after that is forwarded to duet (e.g.
  `--turns 8`, `--reasoning high`, `--lead claude:reviewer`).
- Plain `/duet` seeds the loop from Claude Code's real `/review` skill.
  `/review` produces the kickoff; duet manages the Codex/Claude turns after
  that.
