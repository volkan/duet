# duet — usage guide

Companion reference to the [README](../README.md). The README has the
human-oriented overview and common recipes; this file has the full reference:
flag details, sandbox/network rules, worktree mode, output layout, `--status`
mode, force prompt, and session memory.

## Contents

- [Other ways to start](#other-ways-to-start)
- [Drive duet from any tool, any folder](#drive-duet-from-any-tool-any-folder)
- [Real loop test](#real-loop-test)
- [CLI flags](#cli-flags)
- [Output layout and status mode](#output-layout-and-status-mode)
- [How session memory works](#how-session-memory-works)
- [Stop conditions and force prompt](#stop-conditions-and-force-prompt)
- [Codex sandbox and network access](#codex-sandbox-and-network-access)
- [Worktree mode](#worktree-mode)
- [After a run finishes](#after-a-run-finishes)

---

## Other ways to start

Hand off an existing Claude conversation by session id — this is duet's founding workflow: plan with claude interactively, hand the plan to codex via duet, claude reviews each codex turn.

```bash
# 1. Start the task in Claude interactively.
claude
> let's design a CSV-to-JSON converter in Go with table tests
> /exit

# 2. Hand Claude's session id to duet. Codex picks up the next step,
#    then duet passes Codex's reply back to Claude for review.
./duet.py --resume-claude 106c1c57-ca42-473f-b2f1-1ea764f78c46 \
          --partner codex:coder \
          --cwd ~/code/csv2json \
          --turns 4
```

What you'll see (the silence + heartbeat is normal — claude `-p` is silent on stderr during the API call):

```text
[duet] run dir: runs/20260507-191155
[duet] claude-lead resumes session 0ce8ad74-972f-4bce-a6cc-1d21a76528dd
[duet] extracting latest message from claude session 0ce8ad74…
[duet]   `claude -p` is silent on stderr during the API call; expect 30–120s.
[duet]   from another terminal: duet --status 20260507-191155
  │ [duet] still working… (20s; subprocess silent — typical for `claude -p`)
  │ [duet] still working… (40s; subprocess silent — typical for `claude -p`)

--- Turn 1 :: codex-partner (codex/coder) [started 19:13:17] ---
  │ … codex's live reasoning + tool calls stream here …
```

The `[duet] still working…` heartbeat fires every 20s when a subprocess goes quiet, so you'll know the run is alive even before the first turn banner prints. Pair with `duet --status <run_id>` from another terminal for a richer health view.

Without a prior Claude session — give it a fresh task and let lead+partner roles drive:

```bash
./duet.py --task "Implement fizzbuzz in Go with tests" \
          --lead claude:planner --partner codex:coder \
          --cwd ~/code/scratch --turns 6
```

With an explicit first message (skips the "extract latest" call):

```bash
./duet.py --resume-claude <id> \
          --kickoff "Now implement step 1 of the plan; run tests." \
          --partner codex:coder
```

With a YAML config:

```bash
./duet.py --config duet.example.yaml
```

Roles ship with: `planner`, `coder`, `reviewer`, `triage-reviewer`. Override via `role_prompt` in YAML config to define new ones.

---

## Drive duet from any tool, any folder

`--task` and `--kickoff` accept literal text, `@file`, or `@-` for stdin. `--task-from-cmd` runs a shell command in the target `--cwd` and uses stdout as the kickoff task, while streaming the command's stderr live.

Canonical recipes:

```bash
# Read a task from a file, but operate on another project.
duet --task @review-notes.md --cwd ~/workspace/project

# Pipe any tool's output into duet.
claude -p /review | duet --task @- --cwd ~/workspace/project

# Let duet run the upstream tool itself from inside the target project.
duet --task-from-cmd 'npm test 2>&1' --cwd ~/workspace/project

# With the user-level Claude Code skill installed:
/duet 'claude -p /review'
```

When `--cwd` points at a different directory and `--runs-dir` is omitted, run artifacts land under that project at `.duet/runs/<run_id>/`. Pass `--runs-dir runs` to keep the legacy invocation-relative `runs/<run_id>/` layout.

### Concrete walkthrough: read a GitHub issue, fix the implementation

**Dry-run first** (verify the recipe builds the right cmd; no agent calls):

```bash
duet --dry-run \
     --task-from-cmd 'gh issue view 304 --repo Fluentra/fluentra-flutter --json number,title,state,body,comments' \
     --cwd /Users/volkan.altan/workspace/fluentra/fluentra-flutter \
     --partner codex:coder --worktree \
     --turns 2
```

If that prints the run dir + faked agent replies cleanly, drop `--dry-run` and bump turns:

```bash
duet --task-from-cmd 'gh issue view 304 --repo Fluentra/fluentra-flutter --json number,title,state,body,comments' \
     --cwd /Users/volkan.altan/workspace/fluentra/fluentra-flutter \
     --partner codex:coder --worktree \
     --reasoning high --turns 8
```

> ⚠ **Use `--json` here, not `--comments`.** `gh issue view --comments` routes its formatted output through `$PAGER` (often `less`), which produces *empty stdout* in a pipe — duet then fails fast with "task-from-cmd produced empty stdout". `gh ... --json field,…` skips the pager entirely. Codex and Claude both read JSON natively.

What this does:

1. Runs the `gh` command with cwd = the target project. The issue body + comments JSON becomes the kickoff text handed to codex.
2. Creates a fresh git worktree at `<cwd>/.duet/runs/<run_id>/wt/` on branch `duet/<run_id>` so the fix is isolated from the working copy.
3. codex reads the issue, explores the codebase, applies a minimal fix in the worktree, runs whatever quick checks make sense.
4. claude (lead, planner role) sees the auto-appended diff each turn and either flags issues or accepts Codex's convergence rationale with its own `LGTM rationale:` plus `<<<LGTM>>>`.
5. Worktree is left intact at end. Merge / drop instructions printed on exit.

To monitor from another terminal: `duet --status <cwd>/.duet/runs/<id>/`.

To adapt:
- Different issue: change the URL/number in `--task-from-cmd`.
- Different project: change `--cwd`.
- Edit main directly (no rollback isolation): drop `--worktree`. Risky; only do this if the repo is fully committed.
- Need network for `gh`/`curl` inside codex's sandbox: codex's `workspace-write` blocks outbound network by default. The default `--partner codex:coder` doesn't pass the override. For configs that need it, prefer YAML (`extra_args: ["-c", "sandbox_workspace_write.network_access=true"]`); see [Codex sandbox and network access](#codex-sandbox-and-network-access).
- Default `--turns` is 2 (one codex pass + one claude review). Bump to 6–10 for multi-step fixes; the `force>` prompt at the end of the loop lets you push more rounds without restarting.

### Edge cases (all fail loud at argparse-time)

| situation | result |
|---|---|
| `--task @file` + `--task-from-cmd` together | `argparse.error()` — mutually exclusive |
| `--task @nonexistent` | `SystemExit("--task: file not found: <path>")` |
| `--task @binary.bin` (non-UTF-8) | `SystemExit("--task: file not UTF-8 text: <path>")` |
| resolved task > 512 KB | `SystemExit("task too large (N > 524288); pipe a shorter summary")` |
| `--task-from-cmd` non-zero rc | `SystemExit(2)` with captured stderr |
| `--task-from-cmd` empty stdout | `SystemExit(2)` (silent-empty is worse than fail-loud) |

Stdin is cached so `--task @-` and `--kickoff @-` can coexist in the same invocation.

### `/duet` Claude Code skill (optional)

Install the `/duet` skill so you can chain any upstream tool from inside Claude Code:

````bash
mkdir -p ~/.claude/skills/duet && cat > ~/.claude/skills/duet/SKILL.md <<'EOF'
---
name: duet
description: Hand off arbitrary command output to the duet two-agent harness in the current folder. Wraps `duet --task-from-cmd <shell>` so any upstream tool (claude -p /review, gh, npm test, cat error.log) can drive a planner↔coder loop. Use when asked to "duet on …", "run duet with …", "kick off duet from …", or "have duet pick up <some output>".
argument-hint: "'<shell command>' [extra duet flags…]"
allowed-tools: Bash(*)
---

# /duet

Run exactly:

```bash
duet --task-from-cmd $ARGUMENTS --cwd "$(pwd)" --partner codex:coder --worktree
```

If `$ARGUMENTS` is empty, print the recipe block above (with the
quoted-shell-cmd convention example `/duet 'claude -p /review'`) and
stop. Otherwise after spawn, print the run dir + the
`duet --status <run_dir>` hint.

Notes:
- The first quoted token of `$ARGUMENTS` is the shell command duet runs
  for the kickoff. Anything after that is forwarded to duet (e.g.
  `--turns 8`, `--reasoning high`, `--lead claude:reviewer`).
- Don't try to chain `/<other-skill>` — Claude Code skills can't
  programmatically read prior assistant turns. Use shell composition:
  `/duet 'claude -p /review'` is the right idiom.
EOF
````

---

## Real loop test

`make test` is the fast dry-run regression suite. To check the actual product
loop with real agents, run:

```bash
make loop-test
```

This invokes `scripts/duet_loop_e2e.py`, which builds disposable git fixtures
under a durable local artifact directory:

```text
runs/test-loop/<suite-id>/
  fixtures/       # generated seed repos and per-scenario configs
  duet-runs/      # duet transcripts, state, recap, stderr logs, worktrees
  failures/       # copied failed run dirs for triage
  results.tsv
```

The suite is intentionally not part of `make test`: it launches real Claude
and Codex turns, can take several minutes, and consumes model calls. Each
scenario uses `--worktree --recap` and validates more than agent agreement:
visible tests, hidden validators, unchanged `LOCKED.md`, clean host repo,
recap/transcript parser consistency, no stale `turn-*.pid` files, and
`duet --status` exit behavior.

Useful variants:

```bash
# Run one scenario while iterating on the harness.
python3 scripts/duet_loop_e2e.py --scenario S1

# Store artifacts somewhere else.
python3 scripts/duet_loop_e2e.py --base-dir ~/duet-loop-runs/$(date +%Y%m%d-%H%M%S)

# Compare lower-cost reasoning behavior with default behavior.
python3 scripts/duet_loop_e2e.py --reasoning low
```

Do not run multiple loop-test sweeps concurrently. Codex resume is cwd-based;
the harness isolates each fixture and worktree, but parallel sweeps add
unnecessary ambiguity.

---

## CLI flags

| flag | purpose |
|---|---|
| `--resume-claude SESSION_ID` | resume an existing Claude conversation as the lead agent |
| `--resume-codex SESSION_ID` | (advanced) seed Codex with a session id |
| `--task "…"`, `--task @file`, `--task @-` | task description, used if no resume seed and no kickoff |
| `--kickoff "…"`, `--kickoff @file`, `--kickoff @-` | explicit first message to send to the partner agent |
| `--task-from-cmd "CMD"` | run `CMD` with `cwd=--cwd` and use stdout as the task |
| `--lead BACKEND:ROLE` | lead agent spec, default `claude:planner` |
| `--partner BACKEND:ROLE` | partner agent spec, default `codex:coder` |
| `--turns N` | max turns (default 2 — codex tries, claude reviews; the `force>` prompt at the end lets you push more rounds. Bump to 6+ for multi-step bugs; YAML configs for self-review / repo-compare set their own higher cap) |
| `--sentinel STR` | convergence sentinel (default `<<<LGTM>>>`). A reply must also include an `LGTM rationale:` / `Rationale:` outside fenced code, and both agents must propose convergence in back-to-back turns before duet stops |
| `--cwd PATH` | working dir for both agents |
| `--sandbox` | codex sandbox: `read-only`, `workspace-write`, `danger-full-access` |
| `--permission-mode` | claude permissions: `default`, `acceptEdits`, `plan`, `bypassPermissions` |
| `--timeout SEC` | per-turn timeout (default 900) |
| `--runs-dir DIR` | where to save transcripts; default is `runs/` from the invocation directory, or `<cwd>/.duet/runs/` for a foreign `--cwd` |
| `--config PATH` | YAML/JSON config (overrides most flags) |
| `--worktree` | run the partner agent in a throwaway git worktree on a fresh `duet/<run_id>` branch; the worktree is left intact at the end |
| `--worktree-for partner\|lead` | which agent runs in the worktree (default: partner) |
| `--worktree-root PATH` | parent dir for new worktrees; lands at `<PATH>/<run_id>/`. Default: `<runs_dir>/<run_id>/wt/` (durable across reboots & OS temp cleaners). Pass `/tmp` or `$TMPDIR` for OS-temp behavior |
| `--reasoning minimal\|low\|medium\|high\|max` | reasoning effort for both agents. **Codex:** passes `-c model_reasoning_effort=<v>` except for `medium`, Codex's default; `max` maps to `xhigh`. **Claude:** passes `--effort <v>`; `minimal` maps to Claude's lowest documented value, `low`. High/max also add prompt nudges (`think hard` / `ultrathink`) for extra in-context guidance. |
| `--codex-fast` | Codex-only fast mode: pin every codex turn to `model_reasoning_effort=minimal` and `model_reasoning_summary=concise`, regardless of `--reasoning` or per-agent `reasoning_effort`. Claude turns are untouched, so `--reasoning high --codex-fast` keeps the planner deep and the coder snappy. YAML key: `codex_fast: true` |
| `--status RUN_DIR_OR_ID` | print a one-shot health summary of an existing run and exit. Accepts a path or a bare run id (`20260507-082801`); see [Output layout and status mode](#output-layout-and-status-mode). Read-only |
| `--list [PATH]` | list all runs found under `PATH` (or under the default search paths if omitted: `./runs/`, `./.duet/runs/`, `~/.duet/runs/*/`). Every run dir registers a symlink at `~/.duet/runs/<cwd-slug>/<run_id>` at creation time, so a foreign-cwd run (`duet --cwd /other/proj …`) shows up in `duet --list` from anywhere. One row per run; runs found via both a cwd-relative path and a home-index symlink are deduped. Read-only — except a self-healing backfill writes the symlink for any pre-existing run dir it discovers (idempotent) |
| `--add-dir PATH` | extra path claude is allowed to read/write outside `--cwd` (repeatable). YAML key: `add_dirs:` |
| `--quiet` | suppress live mirroring of subprocess stderr to your terminal |
| `--recap` | compact per-turn debug view; suppresses the live `│`-mirror and writes `recap.md` next to `transcript.md` |
| `--dry-run` | don't call CLIs, fake replies — sanity check the harness |

---

## Output layout and status mode

Each run produces:

```
runs/                                       # or <cwd>/.duet/runs/ for foreign --cwd
  20260506-194122/
    transcript.md                            # full conversation, human-readable
    recap.md                                 # compact per-turn debug view, if --recap
    state.json                               # task, agents, session_ids, history,
                                             # finished_reason, duet_pid,
                                             # recap_path if --recap
    turn-01-codex-coder.stderr.log           # live stderr from each agent invocation
    turn-01-codex-coder.pid                  # PID file (only present while the turn runs)
    turn-02-claude-reviewer.stderr.log
    …
    wt/                                      # the git worktree (if --worktree)
```

The per-turn `*.stderr.log` files capture exactly what duet mirrors live to your terminal during each agent invocation — codex's reasoning steps and tool calls, claude's progress markers, etc. Useful when an agent does something subtle in a 10-minute turn and you want to retrace it later. `turn-00-extract-*` is the optional seed-extraction call when resuming a prior claude session; `turn-NN-forced-*` is a human-forced post-loop turn.

### Recap view

`--recap` replaces the verbose live `│` stderr mirror with one compact block per completed turn. While a turn is running, duet redraws a single line like:

```text
Turn 01 | codex-partner (coder) · running [00:14]
```

When the turn finishes, that line is replaced with the turn summary:

```text
Turn 01 | codex-partner (coder) · 18s · 2.1KB · 87 lines
RECAP:  Proposed sidecar recap.md with per-turn metadata.
FILES:  duet.py, docs/USAGE.md, README.md, scripts/smoke.sh
STATUS: ready-for-review · convergence: no
```

duet also writes `recap.md` next to `transcript.md`. The sidecar starts with the run dir, mode, and transcript path, then appends one Markdown block per turn. The full agent prose remains in `transcript.md`; `recap.md` is for humans only and is never fed back into agent prompts. Recap runs record `recap_path` in `state.json`, and `duet --status <run>` prints the recap path whenever `recap.md` is present.

In recap mode, duet asks each agent to begin every reply with:

```text
RECAP: <one short sentence describing what you produced this turn>
FILES: <comma-separated paths you touched or referenced, or "none">
STATUS: <one of: planning | implementing | reviewing | requesting-changes | ready-for-review | converged>
```

If an agent omits or mangles a header, duet derives a fallback from the reply. Fallback values are prefixed with `·` in `recap.md`, so `FILES: · duet.py` means duet inferred the file path instead of reading an agent-emitted `FILES:` header. Use `recap.md` to scan what each turn produced, `transcript.md` to read the full conversation, and `turn-*.stderr.log` to inspect the underlying CLI progress stream.

`--dry-run --recap` validates the flag path, prints the recap banner, initializes `transcript.md` / `recap.md`, and exits without synthetic turn blocks.

`state.json` includes both agents' final session ids so you can re-run later:

```bash
./duet.py --resume-claude $(jq -r '.agents[0].session_id' runs/<ts>/state.json) \
          --kickoff "continue from where we left off"
```

### `--status RUN_DIR_OR_ID`

A one-shot health probe of any run. Read-only, suitable for cron / external pollers / Linear bots / tmux status lines. Accepts:

- a **path** (absolute or relative): `duet --status runs/20260506-194122/`
- a **bare run id** like `20260507-082801` — auto-resolved against the same default search paths as `--list` (`./runs/`, `./.duet/runs/`, `~/.duet/runs/*/`). The natural pairing: `duet --list` to scan, copy the id, `duet --status <id>` to drill in.

```bash
$ duet --status 20260506-194122
[duet] /Users/.../runs/20260506-194122
  turns_used:      3
  finished_reason: None
  recap:           /Users/.../runs/20260506-194122/recap.md
  in-flight turn:  turn-04-claude-planner
    pid:           44967  (alive: True)
    started:       2026-05-07T08:40:43  (171s ago)
    last stderr:   2s ago (15909 bytes)
```

Exit codes:

| exit | meaning |
|---|---|
| `0` | run finished (`finished_reason` set, e.g. `converged` / `max_turns` / `force_stop`) |
| `1` | running — either an in-flight turn (`.pid` file present) or between turns / awaiting `force>` (verified via `state["duet_pid"]` matching a live duet process) |
| `2` | stuck/crashed — no `.pid` file, no `finished_reason`, AND duet's recorded PID is gone or the PID has been recycled by an unrelated process |
| `3` | `--status` itself errored (bad path, malformed `state.json`) |

The exit-1 vs exit-2 distinction relies on `state.json["duet_pid"]` plus a cmdline check (`/proc/<pid>/cmdline` on Linux, `ps -o command=` on macOS/BSD). Runs from before the `duet_pid` field shipped fall through to a conservative exit-2 with a one-line note.

### `--list [PATH]`

Multi-row companion to `--status`. With no path, `--list` scans the three places duet writes runs (`./runs/`, `./.duet/runs/`, `~/.duet/runs/*/`) and prints one row per run dir, newest first. With an explicit path, it scans only that directory.

```bash
$ duet --list
      run id           status          turns  activity  dir
      ---------------  --------------  -----  --------  ---
  ✅   20260507-082801  converged       2      3h ago    /Users/.../runs/20260507-082801
  🟢   20260507-180412  in-flight       1      4s ago    /Users/.../.duet/runs/20260507-180412
  ⚠   20260506-234519  stuck (no pid)  1      18h ago   /Users/.../runs/20260506-234519

  3 run(s). Per-run health: duet --status <run-id>
```

Status emoji map: ✅ converged · ⏰ max_turns · 🔴 force_stop · 🟢 running (in-flight or between turns) · ⚠ crashed/stuck · ❓ unknown. Same vocabulary as `--status`, packed for the table column. The "activity" column is the most-recent mtime across `state.json`, `turn-*.pid`, and `turn-*.stderr.log`.

`duet --cwd /other/project …` records its run under `/other/project/.duet/runs/<id>/`, but it also drops a symlink at `~/.duet/runs/<cwd-slug>/<run_id>/` so the run is visible to `duet --list` (and `duet --status <run_id>`) from any cwd. Runs surfaced via both the cwd-relative path and the home-index symlink show as one row (deduped on resolved real path); the cwd-relative path wins for display because that's usually the more informative one. Runs created before this index existed get backfilled the first time `--list` walks their dir — idempotent and silent.

Use `--list` to triage ("which runs are still alive?") and `--status <run-id>` to drill into one specifically.

---

## How session memory works

- **Claude**: each call uses `claude -p --resume <session_id> --output-format json`. We capture `session_id` from the JSON wrapper and reuse it. Each turn the prompt sent is just the partner's latest message, so prompts stay small while Claude keeps the full thread in its session.
- **Codex**: first call is `codex exec`, subsequent calls are `codex exec resume --last` in the same `--cd`. Codex doesn't expose a session id we can pin, so it uses "most recent in cwd". **Don't run other codex sessions in that cwd while a duet is running** — they'd compete for `--last`. `--worktree` gives duet's Codex agent its own cwd, but a parallel Codex session launched inside that same worktree can still race.

---

## Stop conditions and force prompt

| trigger | result |
|---|---|
| both agents propose convergence in back-to-back turns | `reason=converged` |
| `--turns` reached | `reason=max_turns` |
| forced post-loop turn proposes convergence | `reason=converged_after_force` |
| forced post-loop turn runs, then you press Enter | `reason=forced_continuation` |
| Ctrl-C once | finishes current turn, exits with `reason=force_stop` |
| Ctrl-C twice | hard exit (130) |
| per-turn timeout | turn rc=124, error inserted, loop stops with `reason=force_stop` |

After any normal exit, if stdin is a TTY:

```
[duet] loop ended (reason=converged). Press Enter to finish, or type feedback to force another turn:
force>
```

Press Enter to accept; type anything to inject a synthetic human-feedback turn and force the next agent in rotation to respond. The forced prompt includes the previous agent reply plus your feedback, so the next agent can review the existing work and appended worktree diff without you pasting the transcript back in. Then it asks again. Each forced turn is preserved in the transcript marked `human — force-feedback` and `<agent> — forced`.

Convergence is deliberately a pair decision. A single reply with `<<<LGTM>>>`
does not stop the loop unless it also has an `LGTM rationale:` / `Rationale:`
outside fenced code, and the immediately previous agent turn also proposed
convergence with rationale. This lets one agent propose "I think this is done"
while the partner can still reject the rationale and ask for another round.

While duet is at the `force>` prompt, `duet --status RUN_DIR` from another terminal returns exit 1 with `state: between turns / awaiting force> prompt`.

---

## Codex sandbox and network access

duet runs Codex with `--sandbox workspace-write` by default (configurable via `--sandbox` / YAML `sandbox:`). That sandbox **blocks outbound network by default** as a security feature — DNS, HTTPS, anything. So `gh`, `curl`, `npm install`, `pip install`, web APIs, etc. all fail from inside codex turns unless you opt in. Symptom looks like:

```
error connecting to api.github.com
check your internet connection or https://githubstatus.com
```

To enable network for a run, pass codex this config override via the codex agent's `extra_args` in your YAML:

```yaml
agents:
  - name: codex-partner
    backend: codex
    role: coder
    extra_args: ["-c", "sandbox_workspace_write.network_access=true"]
```

`duet.example.yaml` ships with this on. Remove the entry to keep strict network isolation (e.g. for analysis-only or air-gapped runs).

The other two sandbox modes:

- **`read-only`** — read-only filesystem, no network. Good for review/analysis runs.
- **`danger-full-access`** — filesystem + network unrestricted. Rarely the right choice when `workspace-write + network_access=true` covers the same surface but keeps writes scoped.

Source: [`[sandbox_workspace_write] network_access`](https://github.com/openai/codex) in codex-rs.

### Codex fast mode

`--codex-fast` (CLI) or `codex_fast: true` (YAML) pins **every codex turn** in this run to `model_reasoning_effort=minimal` and adds `model_reasoning_summary=concise`. It overrides `--reasoning` and per-agent `reasoning_effort` for codex agents only — Claude turns keep whatever effort you set.

Use it when:

- You want a cheap/snappy implementation pass and let the Claude planner do the heavy thinking. `--reasoning high --codex-fast` is the canonical combo.
- You're iterating on a tight feedback loop and don't need codex to deliberate over every change.

Skip it when the codex side is the one doing the careful reasoning (e.g. `codex:reviewer` reading a long diff). Fast mode is a Codex-only knob; setting it without any codex agent in the run is a no-op.

---

## Worktree mode

`--worktree` creates a git worktree on a fresh `duet/<run_id>` branch and runs the partner there. The lead keeps editing the original repo (or, with `--worktree-for lead`, you flip it). After every partner turn duet appends `git status --short` + `git diff --stat` + truncated `git diff HEAD`, plus fenced previews of untracked text files, to its reply, so the lead sees what the partner actually changed — not just what it claims to have changed.

### Where the worktree lives

By default the worktree lands at `<runs_dir>/<run_id>/wt/` — i.e. right next to that run's `transcript.md` and `state.json`. Two reasons:

1. **Durability.** OS temp-dir cleaners (`periodic` on macOS, `systemd-tmpfiles` on Linux, reboot-time `/tmp` wipes on some distros) can erase a worktree mid-run on long duets. Living under `runs/` survives all of that.
2. **Forensics.** Coming back a week later, the transcript, state, and the actual code state of that run sit in one folder.

To override, use `--worktree-root PATH`. The worktree lands at `<PATH>/<run_id>/`, namespaced so parallel runs don't collide. Pass `/tmp` (or `$TMPDIR`) if you want the old throwaway-temp behavior.

duet auto-creates `<runs_dir>/.gitignore` containing `*` on first use, so nothing it writes (transcripts, state, worktrees) shows up in your host repo's `git status`.

### Cleanup

The worktree is **not deleted** when duet exits — you'll see merge / drop instructions printed at the end:

```
[duet] worktree left intact at runs/20260506-202021/wt (branch duet/20260506-202021).
        merge:  git -C /your/repo merge duet/20260506-202021
        review: git -C runs/20260506-202021/wt diff HEAD
        drop:   git -C /your/repo worktree remove runs/20260506-202021/wt && git -C /your/repo branch -D duet/20260506-202021
```

If `--cwd` isn't a git repo, duet warns and falls back to same-repo mode. No crash.

---

## After a run finishes

When duet exits — converged, max-turns, force-stopped, or you Ctrl-C'd — the run dir is preserved at `<runs_dir>/<run_id>/`. Three things to do, usually in order:

### 1. See what happened

```bash
# Health summary (works on a bare run id from anywhere on PATH)
duet --status 20260507-191155

# Compact turn-by-turn recap, when the run used --recap
less <runs_dir>/20260507-191155/recap.md

# Full transcript: every agent turn, the kickoff seed, the auto-appended diffs
less <runs_dir>/20260507-191155/transcript.md

# Codex's live reasoning trace (often huge — multi-MB on heavy turns)
less <runs_dir>/20260507-191155/turn-01-codex-partner.stderr.log

# Or scan all your runs at once
duet --list
```

### 2. Apply, iterate, or discard the changes

#### Fastest finalize: let claude commit, push, and open the PR

When the loop converged (both agents accepted with rationale-backed
`<<<LGTM>>>` turns), claude already has the full diff and the spec in its
session context — it reviewed every turn. The
quickest way to land the work is to resume claude interactively and just
ask:

```bash
CLAUDE_SID=$(jq -r '.agents[] | select(.backend=="claude") | .session_id' \
                 <runs_dir>/<id>/state.json)
claude --resume "$CLAUDE_SID"

> Commit the changes from our duet with a clear message that summarises
> what we did, push the branch, and open a PR with a description that
> links back to the spec we agreed on.
```

claude already knows the spec, exactly what codex changed (it reviewed the
auto-appended diff each turn), and the convergence verdict. With `gh` on
PATH it'll typically run something like:

```bash
git add -A
git commit -m "<concise summary based on the spec>"
git push -u origin HEAD
gh pr create --fill --base main
```

Use this shortcut when:
- The loop converged with pair-approved, rationale-backed `<<<LGTM>>>` turns.
- You're fine with claude composing the message + PR description.
- You'll skim the resulting commit / PR but don't need to micro-stage.

Skip it and use Case A/B below when:
- You want to read the diff carefully and decide hunk-by-hunk.
- You want to split codex's work across multiple commits.
- You're targeting a non-default base branch, a draft PR, or specific
  labels/reviewers/assignees.

#### Manual review path

There are two manual cases — figure out which one you're in by reading `state.json`'s `worktree` field:

```bash
jq '{worktree, worktree_branch, finished_reason}' <runs_dir>/<id>/state.json
```

#### Case A — `worktree: <some-path>` (you ran with `--worktree`)

Codex edited a fresh `duet/<run_id>` branch in that worktree. Your working copy is untouched. From the host repo:

```bash
cd <project>

# Review
git -C <runs_dir>/<id>/wt diff main

# Pick one:
git merge duet/<id>                           # accept — fast-forward or merge commit
git branch -D duet/<id>                       # reject — branch is gone

# Either way, clean up the worktree:
git worktree remove <runs_dir>/<id>/wt

# duet prints the exact merge/drop commands at end-of-run; copy them.
```

This is non-destructive — your working copy never sees codex's edits unless you `git merge`.

#### Case B — `worktree: null` (you ran without `--worktree`)

Codex edited the working copy directly. Changes are sitting uncommitted in your repo.

```bash
cd <project>

# Review
git status --short
git diff                          # full unified diff
git diff --stat                   # one-line per file

# Pick one:
git add -A && git commit -m "duet: …"   # keep everything
git restore .                            # discard everything
git add -p                               # selective (interactive)
```

> ⚠ Quick tip: case B is a destructive default. If you change your mind after `git restore`, codex's edits are gone (the run dir's transcript still has them, but you'd be re-applying by hand). For any non-trivial run, prefer `--worktree` so "rejection" is just `git branch -D`.

### 3. Continue the conversation (optional)

`state.json` saved both agents' session ids — a follow-up duet run can pick them up so neither agent restarts from scratch:

```bash
RUN=<runs_dir>/20260507-191155
CLAUDE_SID=$(jq -r '.agents[] | select(.backend=="claude") | .session_id' "$RUN/state.json")
CODEX_SID=$( jq -r '.agents[] | select(.backend=="codex")  | .session_id' "$RUN/state.json")

duet --resume-claude "$CLAUDE_SID" \
     --resume-codex  "$CODEX_SID" \
     --kickoff "Now run the tests and fix anything that breaks." \
     --partner codex:coder --worktree --turns 4
```

Notes:
- If `CODEX_SID` is `null` (codex doesn't always expose ids; or the run got Ctrl-C'd before codex's first reply), omit `--resume-codex`. Codex will still pick up via `codex exec resume --last` since the cwd is unchanged. Don't run other codex sessions in that cwd in the meantime — they'd race for `--last`.
- The `--kickoff` is what gets sent to the next-up agent (codex by default). Make it specific so the loop doesn't restart from scratch.
- `--worktree` here gets you a *fresh* worktree on a *new* `duet/<new_run_id>` branch. Old worktree (if any) stays where it was.

You can also drop into a single agent without duet:

```bash
# Talk to claude alone, full context preserved
claude --resume "$CLAUDE_SID"

# Or hand codex a one-off in the same cwd
( cd <project> && codex exec resume --last --sandbox workspace-write \
    "Now also write a unit test for the new error path." )
```
