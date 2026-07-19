---
name: duet
description: Run the duet two-agent CLI harness from Codex. Use when asked to run duet, kick off a duet loop, run Claude Code review through duet, hand command output to duet, or have two CLI agents review and implement together.
---

# Duet

Use the installed `duet` CLI from the current project. This skill does not
install `duet`, `claude`, `codex`, or any optional backend.

## Prerequisites

First run:

```bash
command -v duet
```

If it is missing, stop and tell the user to install `duet-cli` with `pipx
install duet-cli`, `uv tool install duet-cli`, or `make install` from a clone.

For the default review recipe, also require:

```bash
command -v claude
command -v codex
```

If either is missing, stop and name the missing binary. Do not substitute a
different harness or backend without the user's direction.

## Launch

Create a private control directory and choose a new run-info path:

```bash
DUET_CONTROL_DIR=$(mktemp -d)
DUET_RUN_INFO="$DUET_CONTROL_DIR/run.json"
```

For the default review loop, launch:

```bash
duet --recipe review --run-info-file "$DUET_RUN_INFO"
```

The `review` recipe means: current cwd, `.duet/runs`, recap mode,
`claude:reviewer` lead, `codex:coder` partner, six turns, strict worktree
isolation, and a `claude -p /review` kickoff. Explicit flags supplied by the
user go after the recipe and override its values.

For a custom upstream command, launch:

```bash
duet --cwd "$(pwd)" --runs-dir "$(pwd)/.duet/runs" \
  --partner codex:coder <conditional worktree defaults> \
  --run-info-file "$DUET_RUN_INFO" \
  --task-from-cmd '<upstream shell command>' <extra duet flags>
```

Replace `<conditional worktree defaults>` before executing; never pass that
placeholder literally. First separate the upstream command from the remaining
duet flags. Examine only the remaining duet flags, not text inside the upstream
command, and synthesize defaults as follows:

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

Thus no topology/strictness override synthesizes
`--worktree --require-worktree`; `--no-worktree` synthesizes nothing;
`--allow-worktree-fallback` synthesizes only `--worktree`; and
`--worktree-path PATH` synthesizes only `--require-worktree`. Keep other
user-supplied flags last so their model, turn, and reasoning choices win. Do
not pre-add `--recap`, because it would conflict with an explicit
`--no-recap`.

Run duet as a long-running command; when the execution tool yields a live
session, retain that session for later output/exit-code collection.

## Discover and monitor

Do not scrape `[duet] run:` or other prose. Poll for `DUET_RUN_INFO`, parse it
as JSON, and accept it only when:

- `schema_version` is `1`;
- `kind` is `duet.run`;
- `run_id`, absolute `run_dir`, absolute `state_path`, and integer `pid` exist.

Compatibility is schema-based. Report `duet_version`, but do not require it to
equal the plugin version.

Once the launch document is valid, monitor with:

```bash
duet --status '<run_dir>' --json
```

Validate `schema_version == 1` and `kind == "duet.status"`, then use the JSON
fields instead of prose. Status exit codes are `0` terminal (for any terminal
reason), `1` running, `2` stuck/crashed, and `3` lookup/schema/status error.
During a live run, report the run dir and phase from JSON. On exit, collect the
original duet process result and the final status snapshot; surface
`finished_reason`, `error`, and artifact paths.

Never copy `state.json` wholesale into chat or automation. The status schema is
the curated interface and excludes prompts, shell commands, credentials, and
backend extra arguments.

## Model selection

Use `--lead-model` for the lead slot and `--partner-model` for the partner slot.
Preserve exact backend IDs supplied by the user. Known friendly mappings:

- Fable 5 → `claude-fable-5`
- Opus 4.8 → `claude-opus-4-8`
- latest Opus → `opus`
- GPT Sol → `gpt-5.6-sol`

With `--recipe review`, a Claude `--lead-model` also pins the standalone
`claude -p /review` kickoff automatically. With a custom explicit
`--task-from-cmd 'claude -p /review …'`, add the same `--model` value inside
that command yourself.

Example:

```bash
duet --recipe review --run-info-file "$DUET_RUN_INFO" \
  --lead-model claude-fable-5 --partner-model gpt-5.6-sol
```
