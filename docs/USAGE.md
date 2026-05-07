# duet — usage guide

Companion reference to the [README](../README.md). The README has the
elevator pitch and the three canonical recipes; this file has the full
surface — flag reference, sandbox/network rules, worktree mode, output
layout, `--status` mode, force prompt, session memory.

## Contents

- [Other ways to start](#other-ways-to-start)
- [Drive duet from any tool, any folder](#drive-duet-from-any-tool-any-folder)
- [CLI flags](#cli-flags)
- [Output layout and status mode](#output-layout-and-status-mode)
- [How session memory works](#how-session-memory-works)
- [Stop conditions and force prompt](#stop-conditions-and-force-prompt)
- [Codex sandbox and network access](#codex-sandbox-and-network-access)
- [Worktree mode](#worktree-mode)

---

## Other ways to start

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

Roles ship with: `planner`, `coder`, `reviewer`. Override via `role_prompt` in YAML config to define new ones.

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

```bash
duet --task-from-cmd 'gh issue view 304 --repo Fluentra/fluentra-flutter --comments' \
     --cwd /Users/volkan.altan/workspace/fluentra/fluentra-flutter \
     --partner codex:coder --worktree \
     --reasoning high --turns 10
```

What this does:

1. Runs `gh issue view 304 --repo Fluentra/fluentra-flutter --comments` with cwd = the target project. The issue body + comments become the kickoff text handed to codex.
2. Creates a fresh git worktree at `<cwd>/.duet/runs/<run_id>/wt/` on branch `duet/<run_id>` so the fix is isolated from the working copy.
3. codex reads the issue, explores the codebase, applies a minimal fix in the worktree, runs whatever quick checks make sense.
4. claude (lead, planner role) sees the auto-appended diff each turn and either flags issues or emits `<<<LGTM>>>` to converge.
5. Worktree is left intact at end. Merge / drop instructions printed on exit.

To monitor from another terminal: `duet --status <cwd>/.duet/runs/<id>/`.

To adapt:
- Different issue: change the URL/number in `--task-from-cmd`.
- Different project: change `--cwd`.
- Edit main directly (no rollback isolation): drop `--worktree`. Risky; only do this if the repo is fully committed.
- Need network for `gh`/`curl` inside codex's sandbox: codex's `workspace-write` blocks outbound network by default. The default `--partner codex:coder` doesn't pass the override. For configs that need it, prefer YAML (`extra_args: ["-c", "sandbox_workspace_write.network_access=true"]`); see [Codex sandbox and network access](#codex-sandbox-and-network-access).

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
| `--turns N` | max turns (default 10) |
| `--sentinel STR` | convergence sentinel (default `<<<LGTM>>>`) |
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
| `--status RUN_DIR` | print a one-shot health summary of an existing run dir and exit; see [Output layout and status mode](#output-layout-and-status-mode). Read-only |
| `--add-dir PATH` | extra path claude is allowed to read/write outside `--cwd` (repeatable). YAML key: `add_dirs:` |
| `--quiet` | suppress live mirroring of subprocess stderr to your terminal |
| `--dry-run` | don't call CLIs, fake replies — sanity check the harness |

---

## Output layout and status mode

Each run produces:

```
runs/                                       # or <cwd>/.duet/runs/ for foreign --cwd
  20260506-194122/
    transcript.md                            # full conversation, human-readable
    state.json                               # task, agents, session_ids, history,
                                             # finished_reason, duet_pid
    turn-01-codex-coder.stderr.log           # live stderr from each agent invocation
    turn-01-codex-coder.pid                  # PID file (only present while the turn runs)
    turn-02-claude-reviewer.stderr.log
    …
    wt/                                      # the git worktree (if --worktree)
```

The per-turn `*.stderr.log` files capture exactly what duet mirrors live to your terminal during each agent invocation — codex's reasoning steps and tool calls, claude's progress markers, etc. Useful when an agent does something subtle in a 10-minute turn and you want to retrace it later. `turn-00-extract-*` is the optional seed-extraction call when resuming a prior claude session; `turn-NN-forced-*` is a human-forced post-loop turn.

`state.json` includes both agents' final session ids so you can re-run later:

```bash
./duet.py --resume-claude $(jq -r '.agents[0].session_id' runs/<ts>/state.json) \
          --kickoff "continue from where we left off"
```

### `--status RUN_DIR`

A one-shot health probe of any run dir. Read-only, suitable for cron / external pollers / Linear bots / tmux status lines.

```bash
$ duet --status runs/20260506-194122/
[duet] /Users/.../runs/20260506-194122
  turns_used:      3
  finished_reason: None
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

---

## How session memory works

- **Claude**: each call uses `claude -p --resume <session_id> --output-format json`. We capture `session_id` from the JSON wrapper and reuse it. Each turn the prompt sent is just the partner's latest message, so prompts stay small while Claude keeps the full thread in its session.
- **Codex**: first call is `codex exec`, subsequent calls are `codex exec resume --last` in the same `--cd`. Codex doesn't expose a session id we can pin, so it uses "most recent in cwd". **Don't run other codex sessions in that cwd while a duet is running** — they'd compete for `--last`. `--worktree` gives duet's Codex agent its own cwd, but a parallel Codex session launched inside that same worktree can still race.

---

## Stop conditions and force prompt

| trigger | result |
|---|---|
| sentinel on its own line | `reason=converged` |
| `--turns` reached | `reason=max_turns` |
| Ctrl-C once | finishes current turn, exits with `reason=force_stop` |
| Ctrl-C twice | hard exit (130) |
| per-turn timeout | turn rc=124, error inserted, loop stops with `reason=force_stop` |

After any normal exit, if stdin is a TTY:

```
[duet] loop ended (reason=converged). Press Enter to finish, or type feedback to force another turn:
force>
```

Press Enter to accept; type anything to inject a synthetic human-feedback turn and force the next agent in rotation to respond. Then it asks again. Each forced turn is preserved in the transcript marked `human — force-feedback` and `<agent> — forced`.

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

---

## Worktree mode

`--worktree` creates a git worktree on a fresh `duet/<run_id>` branch and runs the partner there. The lead keeps editing the original repo (or, with `--worktree-for lead`, you flip it). After every partner turn duet appends `git status --short` + `git diff --stat` + truncated `git diff HEAD` to its reply, so the lead sees what the partner actually changed — not just what it claims to have changed.

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
