---
description: Run Claude Code's /review through the duet two-agent harness, or hand another command's output to duet. Uses Duet's schema-v1 run-info and JSON status contracts instead of scraping command output.
---

# /duet

Run the installed `duet` CLI as a long-running process and monitor it through
its machine-readable control plane.

Run and supervise the harness directly. Do not delegate this workflow to
subagents or launch extra agents alongside its two peers. Current Duet disables
native delegation inside the peers; older releases may not include that policy.

## Prerequisites

Require `command -v duet`. If it is missing, stop and tell the user to install
the `duet-cli` package with pipx/uv, or run `make install` from a clone.

For the default review recipe, also require `command -v claude` and `command
-v codex`. If either is missing, stop and name it. Do not improvise another
harness or backend.

## Launch

Create a private control directory and a path that does not exist yet:

```bash
DUET_CONTROL_DIR=$(mktemp -d)
DUET_RUN_INFO="$DUET_CONTROL_DIR/run.json"
```

If `$ARGUMENTS` is empty, or contains only duet flags beginning with `--`, run:

```bash
duet --recipe review --run-info-file "$DUET_RUN_INFO" $ARGUMENTS
```

The recipe owns the canonical defaults: current cwd, `.duet/runs`, recap,
`claude:reviewer`, `codex:coder`, six turns, strict worktree isolation, and a
`claude -p /review --model sonnet` kickoff. Claude loop turns use the same
stable `sonnet` alias by default. It hands one timed-out automatic turn to the
partner with `--on-turn-timeout continue`. Explicit flags after `--recipe
review` win.

If the first quoted value in `$ARGUMENTS` is an upstream shell command, use it
as the kickoff and forward the remaining values as duet flags:

```bash
duet --cwd "$(pwd)" --runs-dir "$(pwd)/.duet/runs" \
  --partner codex:coder <conditional worktree defaults> \
  --run-info-file "$DUET_RUN_INFO" \
  --task-from-cmd '<upstream shell command>' <remaining duet flags>
```

Replace `<conditional worktree defaults>` before executing; never pass that
placeholder literally. Examine only the remaining duet flags, not text inside
the upstream command:

1. Add `--worktree` only when those flags contain none of `--worktree`,
   `--no-worktree`, `--worktree-path PATH`, or `--worktree-path=PATH`.
2. Add `--require-worktree` only when worktree use is effective and those
   flags contain neither `--require-worktree` nor
   `--allow-worktree-fallback`.
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
`--recap`, because it would conflict with an explicit `--no-recap`.

When the shell tool yields a live process/session, retain it so the final duet
exit code and diagnostics can be collected later.

## Discover and monitor

Do not parse `[duet] run:` banners. Poll for `DUET_RUN_INFO`, parse it as JSON,
and require `schema_version == 1`, `kind == "duet.run"`, plus `run_id`, absolute
`run_dir`, absolute `state_path`, and integer `pid`. Report `duet_version`, but
do not require an exact plugin/runtime version match; compatibility follows the
schema version.

Poll the discovered run with:

```bash
duet --status '<run_dir>' --json
```

Require `schema_version == 1` and `kind == "duet.status"`. Use only the JSON
fields. Exit codes are `0` terminal (including timeout/agent/setup failures),
`1` running, `2` stuck/crashed, and `3` lookup/schema/status error. At terminal
state, collect the original duet process result and report `finished_reason`,
`error`, and artifact paths.

When `active_turn` exists, surface its `remaining_seconds`. A null value means
no valid saved budget is available; do not calculate one from `state.json`.
When `last_timeout` is non-null, report its turn and agent; it remains present
when the review recipe absorbs one non-final coder timeout so a later partner
turn can review its timeout block and worktree handoff.

Do not expose `state.json` wholesale. The status document deliberately omits
prompts, shell commands, credentials, and backend extra arguments.

## Stop one run safely

If the user asks to stop the discovered run, use its validated control path:

```bash
duet --stop '<run_dir>'
```

This graceful form lets the active turn finish. If the user asks to stop it
now, use:

```bash
duet --stop '<run_dir>' --immediate
```

After either request, poll `duet --status '<run_dir>' --json` until it is
terminal. If status remains `awaiting_force`, tell the user to press Enter in
the original duet terminal or close its stdin. A signal does not reliably wake
blocking TTY input on all platforms. Never use `pkill`, `killall`, process-name
matching, or a broad process group. Never stop a different duet run.

## Models

Map friendly names in both the Duet slot and its kickoff:

- Fable 5 → `claude-fable-5`
- Opus 4.8 → `claude-opus-4-8`
- latest Opus → `opus`
- GPT Sol → `gpt-5.6-sol`

Use `--lead-model` and `--partner-model`. The `review` recipe automatically
defaults Claude to `sonnet` and passes a Claude lead-model override to its
separate `/review` kickoff. For a custom explicit `claude -p /review` upstream
command, add the same `--model` value inside `--task-from-cmd` too.
