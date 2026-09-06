---
name: duet
description: Run and supervise the Duet two-agent CLI harness from Codex, Claude Code, or OpenCode. Use for duet loops, Claude/Codex review-and-fix runs, or handing an upstream command to Duet.
---

# Duet

Use the installed `duet` CLI from the current project. This skill does not
install `duet`, `claude`, `codex`, or any optional backend.

Arguments may arrive through the host invocation or a wrapper. Treat them as
the user's arguments rather than inserting a literal host placeholder into
commands. Invoke it as `$duet` in Codex, `/duet` in Claude Code (`/duet:duet`
for the native plugin), or by a natural-language Duet request in OpenCode.

Run and supervise the harness directly; do not delegate this workflow to
subagents or launch extra agents alongside its two peers. Current Duet disables
native delegation inside the peers; older releases may not include that policy.

## Prerequisites

First run:

```bash
command -v duet
```

If it is missing, stop and tell the user to install `duet-cli` with `pipx
install duet-cli`, `uv tool install duet-cli`, or `make install` from a clone.

The plain Duet request uses the Claude-plus-Codex `review` recipe. For that
default only, also require:

```bash
command -v claude
command -v codex
```

If either is missing for an otherwise-default request, name the missing binary
and suggest `--recipe codex-review` when Claude is missing. Do not silently
switch recipes or substitute a different harness or backend; an explicit
topology request remains unchanged.

If the user supplies an explicit backend/topology override, custom partner, or
config, check the binaries required by that requested setup instead. Do not
require Claude or Codex merely because they are defaults when the requested
configuration does not use them. Check `git` too when the requested setup uses
a worktree.

The plain request uses this Claude-plus-Codex review recipe. If the user
explicitly asks for a Codex-only review, says they do not have Claude, asks for
two Codex models, or names `--recipe codex-review`, use the Codex-only recipe
below instead. In that case check only `git`, `codex`, and `duet`; do not
require Claude and do not silently change an explicitly chosen topology.

For that Codex-only path, check:

```bash
command -v git
command -v codex
command -v duet
codex login status
```

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
isolation, a `claude -p /review --model sonnet` kickoff, Sonnet-defaulted
Claude loop turns, and continuation past one non-final automatic-turn timeout.
Explicit flags supplied by the user go after the recipe and override its
values.

For a Codex-only review, launch:

```bash
duet --recipe codex-review --run-info-file "$DUET_RUN_INFO"
```

`codex-review` uses `codex:reviewer` as the lead, who speaks first, and
`codex:coder` as the partner, with six turns, recap mode, local finding reports, strict worktree
isolation, and continuation after one non-final automatic-turn timeout. The
default task reviews the latest committed `HEAD`, then asks for focused fixes
that are supported by the review findings. It does not start a separate
Claude or upstream reviewer process. `--lead-model` and `--partner-model` are
optional; when omitted, let the CLI's model defaults apply. Preserve exact
model IDs supplied by the user and never substitute a different model or
force a particular model.

If this recipe is not listed by `duet --help`, the installed PyPI release is
older than the recipe. Use the current checkout with `make install` (or run
the checkout's Python entry point) before passing `--recipe codex-review`;
published versions that predate the recipe do not support that flag.

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
equal the plugin version. If the original process exits before publishing valid
run-info, or the file is missing or invalid, collect and report that process's
result and diagnostics; do not fall back to banners, guess a run directory, or
operate on an unvalidated run.

Once the launch document is valid, monitor with:

```bash
duet --status '<run_dir>' --json
```

Validate `schema_version == 1` and `kind == "duet.status"`, then use the JSON
fields instead of prose. Status exit codes are `0` terminal (for any terminal
reason), `1` running, `2` stuck/crashed, and `3` lookup/schema/status error.
During a live run, report the run dir and phase from JSON. On exit, collect the
original duet process result and the final status snapshot; surface
`finished_reason`, `error`, and artifact paths. If status cannot be read or is
invalid, report that control-plane failure and retain the original process
result; do not inspect `state.json` as a replacement interface.

For an active turn, also surface `budget_seconds` and `remaining_seconds`.
When `last_timeout` is non-null, report its turn and agent; it remains present
when the review recipe continues past one non-final coder timeout so the
partner can inspect its failure block and worktree handoff.

Never copy `state.json` wholesale into chat or automation. The status schema is
the curated interface and excludes prompts, shell commands, credentials, and
backend extra arguments.

## Stop one run safely

If the user asks to stop the discovered run, use:

```bash
duet --stop '<run_dir>'
```

This request is graceful. The active turn can finish before Duet records
`force_stop`. If the user asks to stop the run now, use:

```bash
duet --stop '<run_dir>' --immediate
```

After either request, poll `duet --status '<run_dir>' --json` until the run is
terminal. If status remains `awaiting_force`, ask the user to press Enter in
the original Duet terminal or close its stdin. Never use `pkill`, `killall`,
process-name matching, or a broad process group. Never stop a different Duet
run.

## Model selection

Use `--lead-model` for the lead slot and `--partner-model` for the partner slot.
Preserve exact backend IDs supplied by the user. Known friendly mappings:

- Fable 5 → `claude-fable-5`
- Opus 4.8 → `claude-opus-4-8`
- latest Opus → `opus`
- GPT Sol → `gpt-5.6-sol`

Claude defaults to the stable `sonnet` alias. With `--recipe review`, a Claude
`--lead-model` overrides that default for both the loop agent and standalone
`claude -p /review` kickoff automatically. With a custom explicit
`--task-from-cmd 'claude -p /review …'`, add the same `--model` value inside
that command yourself.

For `codex-review`, model names are passed to the two Codex slots as supplied.
For example, this natural-language request selects two exact model IDs:

```text
Use Codex-only Duet review with --lead-model gpt-5.6-sol and --partner-model gpt-5.6-luna.
```

Model availability depends on the account, rollout, and client. The Codex CLI
can use an existing ChatGPT subscription sign-in; no API key is needed for
that sign-in. Check it with `codex login status`. Both peers use the same
account limits. Two models do not establish correctness, and availability does
not imply free or unlimited usage or independence.

Example:

```bash
duet --recipe review --run-info-file "$DUET_RUN_INFO" \
  --lead-model claude-fable-5 --partner-model gpt-5.6-sol
```
