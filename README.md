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

Recommended split: Codex plans and reviews, Claude implements.

```bash
cd ~/code/myrepo

./duet.py \
    --recap \
    --task-from-cmd 'gh issue view 1234 --json number,title,state,body,comments' \
    --lead claude:coder \
    --partner codex:planner \
    --worktree --worktree-for lead \
    --turns 4
```

Use this when you want a planner-led implementation pass on something concrete
— a bug report, a feature request, a chore. Codex reads the issue, shapes the
plan, and reviews each turn; Claude writes the patch in an isolated worktree
until both agents converge or you stop them.

For fresh `--task` input, duet sends the first real turn to the partner agent.
That is why this recipe makes Codex the partner planner and uses
`--worktree-for lead` to isolate Claude's implementation turns.

For a task you already have in words, pass it directly:

```bash
./duet.py \
    --recap \
    --task "Add Codex fast mode for duet-managed Codex runs, don't miss any doc files" \
    --lead claude:coder \
    --partner codex:planner \
    --worktree --worktree-for lead \
    --turns 4
```

`--recap` keeps the live output compact and the worktree keeps the host
checkout clean until you merge.

```bash
# Run a fresh task in a target project.
./duet.py --task "Implement fizzbuzz in Go with tests" \
    --lead claude:coder --partner codex:planner \
    --cwd ~/code/scratch

# Check what duet would do without calling either agent.
./duet.py --dry-run --task "Explain this repository" \
    --lead claude:coder --partner codex:planner \
    --cwd .
```

Install the `duet` command:

```bash
make install      # symlinks duet.py to ~/.local/bin/duet
make test         # runs scripts/smoke.sh dry-run regression checks
make loop-test    # slow real Claude/Codex loop checks; writes runs/test-loop/
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
feedback to force another round; duet sends the next agent the previous reply
plus your feedback, including any appended worktree handoff block and diff.

## Common Recipes

Pipe another tool into duet:

```bash
claude -p /review | ./duet.py --task @- \
    --lead claude:coder --partner codex:planner \
    --cwd ~/workspace/project
```

Let duet run the upstream command inside the target project:

```bash
./duet.py --task-from-cmd 'npm test 2>&1' \
    --lead claude:coder --partner codex:planner \
    --cwd ~/workspace/project \
    --worktree --worktree-for lead
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
./duet.py --task "Fix the issue" \
    --lead claude:coder --partner codex:triage-reviewer \
    --cwd ~/workspace/project
```

Review a recent implementation - Codex reviews at max effort, Claude applies
only requested fixes:

```bash
./duet.py --recap \
    --task "Review the current main branch changes. Codex should act as reviewer: identify any blocking issues in the latest commit. Claude should act as coder: implement only the fixes Codex explicitly requests. Preserve project constraints and run make test before convergence." \
    --lead codex:reviewer \
    --partner claude:coder \
    --reasoning max \
    --worktree \
    --turns 6
```

Keep `--codex-fast` off in that recipe: Codex is the reviewer, so max effort is
the point.

Review the latest commit plus an untracked notes file by seeding both into the
task:

```bash
./duet.py --recap \
    --task-from-cmd 'git show --stat --patch --no-ext-diff HEAD && printf "\n\n--- TODO.md ---\n" && cat TODO.md' \
    --lead codex:reviewer \
    --partner claude:coder \
    --reasoning max \
    --worktree \
    --turns 6
```

Fresh worktrees start from committed `HEAD`; commit the notes first if the coder
must edit them as a normal tracked file.

Deep planner, fast coder — Claude plans at high effort, Codex coder turns drop to low for latency (uses the default `claude:planner + codex:coder` pairing):

```bash
./duet.py --reasoning high --codex-fast \
    --task "Fix the issue" \
    --cwd ~/workspace/project
```

Compact live debug view — see only what each turn produced, in real time:

```bash
./duet.py --recap --task "Fix the issue" \
    --lead claude:coder --partner codex:planner \
    --cwd ~/workspace/project
```

## Output

Every run writes a directory containing:

- `transcript.md` - the full conversation.
- `recap.md` - compact per-turn debug view when `--recap` is enabled; `--status` shows this path when present.
- `state.json` - run state, agent roles, session ids, finish reason, and `recap_path` for recap runs.
- `turn-*.stderr.log` - live stderr from each agent invocation.
- `turn-*.pid` - present only while a turn is running.
- `wt/` - the git worktree, when `--worktree` is enabled.

When a worktree agent replies, duet appends a handoff block to that reply before
the diff. The block names the exact worktree path and branch, warns that the
receiving agent's cwd may be a clean checkout, and includes `git -C <wt>` review
commands so verification happens against the edited tree.

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
