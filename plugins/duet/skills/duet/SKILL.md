---
name: duet
description: Run the duet two-agent CLI harness from Codex. Use when asked to run duet, kick off a duet loop, run Claude Code review through duet, hand command output to duet, or have two CLI agents review and implement together.
---

# Duet

Use the installed `duet` CLI to run a two-agent loop from the current project.
This skill does not install `duet`, `claude`, `codex`, or `gemini`.

## Prerequisite Check

First confirm the `duet` CLI is on PATH:

```bash
command -v duet
```

If it is not found, stop and tell the user:

> The `duet` CLI is not on PATH. Install it with `pipx install duet-cli`
> (or `uv tool install duet-cli`, or `python3 -m pip install --user duet-cli`;
> the package is `duet-cli`; the command it installs is `duet`), or clone
> the repo (https://github.com/volkan/duet) and run `make install` (symlinks
> `duet.py` to `~/.local/bin/duet`; make sure `~/.local/bin` is on PATH).
> Then ask Codex to use Duet again.

For the default review recipe, also confirm:

```bash
command -v claude
command -v codex
```

If `claude` is missing, stop and tell the user:

> The default Duet recipe runs `claude -p /review` first, but `claude` is not
> on PATH. Install or authenticate Claude Code, then ask Codex to use Duet
> again.

If `codex` is missing, stop and tell the user:

> The default Duet recipe uses `codex:coder` as the implementation agent, but
> `codex` is not on PATH. Install Codex, or ask Codex to use Duet with a
> different partner/config that does not require Codex.

## Run

If the user did not provide an upstream command or custom duet flags, run
exactly:

```bash
duet --recap --cwd "$(pwd)" --runs-dir "$(pwd)/.duet/runs" --lead claude:reviewer --partner codex:coder --worktree --turns 6 --task-from-cmd 'claude -p /review'
```

If the user provided an upstream command, run:

```bash
duet --cwd "$(pwd)" --runs-dir "$(pwd)/.duet/runs" --partner codex:coder --worktree --task-from-cmd '<upstream shell command>' <extra duet flags>
```

Keep the defaults (`--partner codex:coder --worktree`) before any user-supplied
extra flags so explicit flags such as `--partner gemini:coder`, `--turns 8`, or
`--config foo.yaml` can override them.

## Model Selection

When the user names models, construct the command directly with Duet's
per-agent model flags. Do not inspect Duet's source or help output just to
rediscover this syntax:

- The model for the agent selected by `--lead` uses `--lead-model`.
- The model for the agent selected by `--partner` uses `--partner-model`.
- Preserve an exact backend model ID supplied by the user. Translate an
  unambiguous display name when its CLI ID is known; for example, `Opus 4.8`
  maps to `claude-opus-4-8`, and `GPT Sol` maps to `gpt-5.6-sol`. If the user
  asks only for the latest Opus without a version, use Claude's `opus` alias.
- The default `claude -p /review` kickoff is a separate Claude invocation. If
  the user pins the Claude model, add the same `--model` value inside
  `--task-from-cmd` so both the kickoff and the Duet lead use it.

For example, "use Duet with Opus 4.8 and GPT Sol" means:

```bash
duet --recap --cwd "$(pwd)" --runs-dir "$(pwd)/.duet/runs" --lead claude:reviewer --lead-model claude-opus-4-8 --partner codex:coder --partner-model gpt-5.6-sol --worktree --turns 6 --task-from-cmd 'claude -p /review --model claude-opus-4-8'
```

After spawn, report the run dir and the matching status command once
`[duet] run: ...` or `[duet] run dir: ...` appears:

```bash
duet --status <run_dir>
```

Important: in the default recipe, `claude -p /review` runs as
`--task-from-cmd` before duet allocates a run directory. During that kickoff
phase, `.duet/runs/*` may not exist yet. Do not report that no run was created
until the command exits or prints an error.
