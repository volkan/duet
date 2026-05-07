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

## Best recipes

```bash
# Fresh task, no prior session.
./duet.py --task "Implement fizzbuzz in Go with tests" --cwd ~/code/scratch

# Pipe any tool's output into duet, on any folder.
claude -p /review | ./duet.py --task @- --cwd ~/workspace/project

# Or let duet run the upstream tool itself in the target project.
./duet.py --task-from-cmd 'npm test 2>&1' --cwd ~/workspace/project --worktree

# YAML config — most knobs live here for repeatable runs.
./duet.py --config duet.example.yaml

# From any other terminal: live status of an in-progress run.
./duet.py --status .duet/runs/<id>/
```

## Install

```bash
make install      # symlinks duet.py to ~/.local/bin/duet
make test         # runs scripts/smoke.sh (20 dry-run cases)
```

## Docs

[**docs/USAGE.md**](docs/USAGE.md) — full reference. Flags, sandbox & network rules, worktree mode, output layout, `--status` exit codes, force prompt, session memory, the `/duet` Claude Code skill.

## Limits / future

- No automatic resume of a prior duet run yet (session ids are saved but you re-pass them manually).
- Same-cwd Codex parallelism is unsafe (`codex exec resume --last` races). `--worktree` isolates duet's Codex cwd from the host repo, but don't start another Codex session inside the same worktree while the run is active.
- Transcript captures full agent text. Sentinel detection requires the sentinel on its own line outside fenced markdown code blocks, but a sufficiently mischievous agent could still trigger early. Acceptable for development use.
