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

First example: plan a GitHub issue with Claude, hand the implementation to duet.

```bash
# 1. In Claude Code, plan how you'd resolve a GitHub issue. Claude
#    can fetch the issue itself via its bash tool / `gh` CLI.
cd ~/code/myrepo
claude
> Read GitHub issue 1234 in this repo (`gh issue view 1234` if you
>   haven't already). Make me an implementation plan: which files to
>   touch, edge cases, the test strategy. Don't write code yet.
> /exit
# Note the session id from "Resume this session with: claude --resume <uuid>"

# 2. Hand the planning session to duet. Codex implements the plan in a
#    fresh worktree on `duet/<run_id>`, Claude reviews each turn.
./duet.py \
    --resume-claude 106c1c57-ca42-473f-b2f1-1ea764f78c46 \
    --partner codex:coder \
    --worktree --turns 4
```

Use this when you want a planner-led implementation pass on something concrete
— a bug report, a feature request, a chore. Claude reads the issue and produces
a plan; duet drives Codex to execute and Claude to review until they converge
or you stop them.

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
continues until both agents accept convergence in back-to-back turns, `--turns`
is reached, a timeout happens, or you press Ctrl-C. A convergence proposal must
include an `LGTM rationale:` explaining why the work is done, followed by the
sentinel `<<<LGTM>>>` on its own line; a bare sentinel is ignored.

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

Compact live debug view — see only what each turn produced, in real time:

```bash
./duet.py --recap --task "Fix the issue" --cwd ~/workspace/project
```

## Output

Every run writes a directory containing:

- `transcript.md` - the full conversation.
- `recap.md` - compact per-turn debug view when `--recap` is enabled; `--status` shows this path when present.
- `state.json` - run state, agent roles, session ids, finish reason, and `recap_path` for recap runs.
- `turn-*.stderr.log` - live stderr from each agent invocation.
- `turn-*.pid` - present only while a turn is running.
- `wt/` - the git worktree, when `--worktree` is enabled.

When `--cwd` points outside the invocation directory and `--runs-dir` is not
set, artifacts go under the target project at `.duet/runs/<run_id>/`.

## Documentation

Read [docs/USAGE.md](docs/USAGE.md) for the full reference: flags, sandbox and
network rules, worktree mode, output layout, `--status` exit codes, force
prompt behavior, session memory, the post-run "apply / iterate / discard"
checklist, and the optional `/duet` Claude Code skill.

For contributor guidance, read [CLAUDE.md](CLAUDE.md). Codex-specific entry
notes live in [AGENTS.md](AGENTS.md).

## Limits

- duet does not automatically resume a prior duet run. Session ids are saved,
  but you pass them manually.
- Parallel Codex sessions in the same cwd are unsafe because
  `codex exec resume --last` is cwd-based. `--worktree` isolates duet's Codex
  cwd from the host repo, but do not start another Codex session inside that
  same worktree while the run is active.
- Transcripts capture full agent text. Convergence detection only counts
  rationale-backed sentinels outside fenced markdown code blocks.
