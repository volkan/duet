# duet

Two CLI agents in conversation. One file. Stdlib only.

`duet.py` lets you start a conversation with `claude` interactively, exit, then hand the session id to duet — which loops Claude and a partner agent (Codex by default) back-and-forth until they converge or you stop them. Each agent keeps its own memory across turns via session resume.

## The workflow it's built for

```bash
# 1. Plan with Claude interactively, the normal way.
$ claude
> let's design a CSV→JSON converter in Go with table tests…
…lots of conversation…
> /exit

# Claude prints (or you can find from output) a session id like:
#   106c1c57-ca42-473f-b2f1-1ea764f78c46

# 2. Hand it to duet — Codex now replies to Claude's last message,
#    they ping-pong, each remembering its own side.
$ ./duet.py \
    --resume-claude 106c1c57-ca42-473f-b2f1-1ea764f78c46 \
    --partner codex:coder \
    --cwd ~/code/csv2json \
    --turns 10
```

What duet does on that command:

1. Calls `claude -p --resume <id>` once to extract the latest plan.
2. Sends that to Codex (`codex exec --sandbox workspace-write …`). Codex implements / replies.
3. Sends Codex's reply back to Claude with `--resume <id>` so Claude remembers everything.
4. Repeats until either side emits `<<<LGTM>>>` on its own line, `--turns` is hit, or you press Ctrl-C.
5. Asks `force> ` — Enter to finish, or type feedback to push another round.

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

## CLI flags

| flag | purpose |
|---|---|
| `--resume-claude SESSION_ID` | resume an existing Claude conversation as the lead agent |
| `--resume-codex SESSION_ID` | (advanced) seed Codex with a session id |
| `--task "…"` | task description, used if no resume seed and no kickoff |
| `--kickoff "…"` | explicit first message to send to the partner agent |
| `--lead BACKEND:ROLE` | lead agent spec, default `claude:planner` |
| `--partner BACKEND:ROLE` | partner agent spec, default `codex:coder` |
| `--turns N` | max turns (default 10) |
| `--sentinel STR` | convergence sentinel (default `<<<LGTM>>>`) |
| `--cwd PATH` | working dir for both agents |
| `--sandbox` | codex sandbox: `read-only`, `workspace-write`, `danger-full-access` |
| `--permission-mode` | claude permissions: `default`, `acceptEdits`, `plan`, `bypassPermissions` |
| `--timeout SEC` | per-turn timeout (default 900) |
| `--runs-dir DIR` | where to save transcripts (default `runs/`) |
| `--config PATH` | YAML/JSON config (overrides most flags) |
| `--worktree` | run the partner agent in a throwaway git worktree on a fresh `duet/<run_id>` branch; the worktree is left intact at the end |
| `--worktree-for partner\|lead` | which agent runs in the worktree (default: partner) |
| `--worktree-root PATH` | parent dir for new worktrees; lands at `<PATH>/<run_id>/`. Default: `<runs_dir>/<run_id>/wt/` (durable across reboots & OS temp cleaners). Pass `/tmp` or `$TMPDIR` for OS-temp behavior |
| `--reasoning minimal\|low\|medium\|high\|max` | reasoning effort for both agents. **Codex:** passes `-c model_reasoning_effort=<v>` except for `medium`, Codex's default; `max` maps to `xhigh`. **Claude:** passes `--effort <v>`; `minimal` maps to Claude's lowest documented value, `low`. High/max also add prompt nudges (`think hard` / `ultrathink`) for extra in-context guidance. |
| `--dry-run` | don't call CLIs, fake replies — sanity check the harness |

Roles ship with: `planner`, `coder`, `reviewer`. Override via `role_prompt` in YAML config to define new ones.

## How session memory works

- **Claude**: each call uses `claude -p --resume <session_id> --output-format json`. We capture `session_id` from the JSON wrapper and reuse it. Each turn the prompt sent is just the partner's latest message, so prompts stay small while Claude keeps the full thread in its session.
- **Codex**: first call is `codex exec`, subsequent calls are `codex exec resume --last` in the same `--cd`. Codex doesn't expose a session id we can pin, so it uses "most recent in cwd". **Don't run other codex sessions in that cwd while a duet is running** — they'd compete for `--last`. `--worktree` gives duet's Codex agent its own cwd, but a parallel Codex session launched inside that same worktree can still race.

## Stop conditions

| trigger | result |
|---|---|
| sentinel on its own line | `reason=converged` |
| `--turns` reached | `reason=max_turns` |
| Ctrl-C once | finishes current turn, exits with `reason=force_stop` |
| Ctrl-C twice | hard exit (130) |
| per-turn timeout | turn rc=124, error inserted, loop stops with `reason=force_stop` |

## "force" prompt

After any normal exit, if stdin is a TTY:

```
[duet] loop ended (reason=converged). Press Enter to finish, or type feedback to force another turn:
force>
```

Press Enter to accept; type anything to inject a synthetic human-feedback turn and force the next agent in rotation to respond. Then it asks again. Each forced turn is preserved in the transcript marked `human — force-feedback` and `<agent> — forced`.

## Output

```
runs/
  20260506-194122/
    transcript.md                            # full conversation, human-readable
    state.json                               # task, agents, session_ids, history, finished_reason
    turn-01-codex-coder.stderr.log           # live stderr from each agent invocation
    turn-02-claude-reviewer.stderr.log       # (one file per turn — codex's thinking, tool calls,
    …                                        #  claude's progress; same lines you see scrolling on
                                             #  the terminal during the run, persisted for forensics)
```

The per-turn `*.stderr.log` files capture exactly what duet mirrors live to
your terminal during each agent invocation — codex's reasoning steps and
tool calls, claude's progress markers, etc. Useful when an agent does
something subtle in a 10-minute turn and you want to retrace it later.
`turn-00-extract-*` is the optional seed-extraction call when resuming a
prior claude session; `turn-NN-forced-*` is a human-forced post-loop turn.

`state.json` includes both agents' final session ids so you can re-run later:

```bash
./duet.py --resume-claude $(jq -r '.agents[0].session_id' runs/<ts>/state.json) \
          --kickoff "continue from where we left off"
```

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

## Limits / future

- No automatic resume of a prior duet run yet (session ids are saved but you re-pass them manually).
- Same-cwd Codex parallelism is unsafe (`codex exec resume --last` races). `--worktree` isolates duet's Codex cwd from the host repo, but don't start another Codex session inside the same worktree while the run is active.
- Transcript captures full agent text. Sentinel detection requires the sentinel on its own line outside fenced markdown code blocks, but a sufficiently mischievous agent could still trigger early. Acceptable for development use.
