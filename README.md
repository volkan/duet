# duet

Two CLI agents in conversation. One Python file. Stdlib only.

`duet.py` runs two command-line coding agents, usually Claude and Codex, in
alternating turns. One agent can plan or review while the other implements. The
loop stops when they agree, the turn limit is reached, a timeout happens, or
you stop them.

Use duet when you want:

- A planner/reviewer agent to keep pressure on an implementation agent.
- A second agent to inspect test failures, issue text, or review output.
- A transcript and run directory you can inspect after the agents finish.
- Isolation through an optional git worktree while the partner agent edits.

## Quick Start

First example: hand off a task by conversation id.

```bash
# 1. Start the task in Claude, the normal interactive way.
claude
> let's design a CSV-to-JSON converter in Go with table tests
> /exit

# 2. Hand Claude's session id to duet. Codex picks up the next step,
#    then duet passes Codex's reply back to Claude for review.
./duet.py \
    --resume-claude 106c1c57-ca42-473f-b2f1-1ea764f78c46 \
    --partner codex:coder \
    --cwd ~/code/csv2json \
    --turns 10
```

Use this when you started a conversation with one agent and want to hand the
rest of the process to two agents that keep passing context back and forth.

```bash
# Run a fresh task in a target project.
./duet.py --task "Implement fizzbuzz in Go with tests" --cwd ~/code/scratch

# Check what duet would do without calling either agent.
./duet.py --dry-run --task "Explain this repository" --cwd .
```

Install the `duet` command:

```bash
make install      # symlinks duet.py to ~/.local/bin/duet
make test         # runs scripts/smoke.sh dry-run regression checks
```

## How It Works

Each agent keeps its own conversation memory:

- Claude resumes with `claude -p --resume <session_id>`.
- Codex resumes with `codex exec resume --last` in the working directory.

On each turn, duet sends the latest reply from one agent to the other. It
continues until an agent prints the sentinel `<<<LGTM>>>` on its own line,
`--turns` is reached, a timeout happens, or you press Ctrl-C.

After the loop, duet opens a `force> ` prompt. Press Enter to finish, or type
feedback to force another round.

## Common Recipes

Pipe another tool into duet:

```bash
claude -p /review | ./duet.py --task @- --cwd ~/workspace/project
```

Let duet run the upstream command inside the target project:

```bash
./duet.py --task-from-cmd 'npm test 2>&1' --cwd ~/workspace/project --worktree
```

Use a repeatable config:

```bash
./duet.py --config duet.example.yaml
```

Check an in-progress run from another terminal:

```bash
./duet.py --status .duet/runs/<id>/
```

Gate convergence on P0/P1 review findings:

```bash
./duet.py --task "Fix the issue" --lead claude:triage-reviewer --partner codex:coder --cwd ~/workspace/project
```

## Output

Every run writes a directory containing:

- `transcript.md` - the full conversation.
- `state.json` - run state, agent roles, session ids, and finish reason.
- `turn-*.stderr.log` - live stderr from each agent invocation.
- `turn-*.pid` - present only while a turn is running.
- `wt/` - the git worktree, when `--worktree` is enabled.

When `--cwd` points outside the invocation directory and `--runs-dir` is not
set, artifacts go under the target project at `.duet/runs/<run_id>/`.

## Documentation

Read [docs/USAGE.md](docs/USAGE.md) for the full reference: flags, sandbox and
network rules, worktree mode, output layout, `--status` exit codes, force
prompt behavior, session memory, and the optional `/duet` Claude Code skill.

For contributor guidance, read [CLAUDE.md](CLAUDE.md). Codex-specific entry
notes live in [AGENTS.md](AGENTS.md).

## Limits

- duet does not automatically resume a prior duet run. Session ids are saved,
  but you pass them manually.
- Parallel Codex sessions in the same cwd are unsafe because
  `codex exec resume --last` is cwd-based. `--worktree` isolates duet's Codex
  cwd from the host repo, but do not start another Codex session inside that
  same worktree while the run is active.
- Transcripts capture full agent text. Sentinel detection only counts the
  sentinel on its own line outside fenced markdown code blocks.
