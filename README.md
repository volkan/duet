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

Pair-programming pattern: plan with codex in its own session first, then hand
the session id to duet — codex implements with the plan in context while
claude reviews each turn.

```bash
cd ~/code/myrepo

# Find the codex session you just planned in:
#   ls -lt ~/.codex/sessions/ | head
# or look for `session id: <uuid>` on `codex exec`'s stderr.

./duet.py \
    --resume-codex <codex-session-id> \
    --worktree \
    --reasoning max \
    --task "Implement the plan from your codex planning session."
```

Four flags carry their weight; everything else is a default. Codex (resumed,
with the plan in context) speaks first as the coder. Claude reviews each
turn as the planner. The worktree keeps the host checkout clean until you
merge. Sentinel + rationale convergence rules are baked into both role
prompts — you do not need to restate them in `--task`.

Resume flags attach to the matching backend even when you override
`--lead`/`--partner`. A resumed Claude agent is normalized to lead so duet can
extract its latest message as the seed; a resumed Codex agent is normalized to
partner/coder so it speaks first with its existing plan in context.

The symmetric `--resume-claude <session-id>` does the inverse — plan in
claude, hand off to codex — and is duet's founding workflow, documented in
[docs/USAGE.md](docs/USAGE.md).

Have a task in words but no prior planning session? Let codex plan inside
the loop while claude implements:

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

# Seed duet from Claude Code's real /review output.
./duet.py --recap --task-from-cmd 'claude -p /review' \
    --lead claude:reviewer --partner codex:coder \
    --worktree \
    --cwd .
```

In the review recipe, Claude's `/review` runs once to produce the kickoff
critique. Duet then hands that critique to Codex, preserves both agent
sessions, and manages the back-and-forth until convergence or the turn limit.

Install the `duet` command:

```bash
make install      # symlinks duet.py to ~/.local/bin/duet
make test         # unit tests (tests/test_duet.py) + scripts/smoke.sh dry-run checks
make unit-test    # only the stdlib unittest suite under tests/
make smoke-test   # only scripts/smoke.sh dry-run regression checks
make loop-test    # slow real Claude/Codex loop checks; writes runs/test-loop/
```

## How It Works

Each agent keeps its own conversation memory:

- Claude resumes with `claude -p --resume <session_id>`.
- Codex resumes with `codex exec resume <session_id>` when duet captured one
  from Codex's stderr, or `codex exec resume --last` in the working directory
  as a fallback for older builds that don't print a session id.

On each turn, duet sends the latest reply from one agent to the other. It
continues until both agents accept convergence in back-to-back turns, `--turns`
is reached, a timeout happens, or you press Ctrl-C. A convergence proposal must
include an `LGTM rationale:` explaining why the work is done, followed by the
sentinel `<<<LGTM>>>` on its own line; a bare sentinel is ignored.

If you pass `--verify-cmd`, duet runs that shell command before counting a
valid convergence proposal. Exit code 0 allows the proposal to count; any
non-zero exit, timeout, or execution error feeds a capped failure block to the
next agent turn.

After the loop, duet opens a `force> ` prompt. Press Enter to finish, or type
feedback to force another round; duet sends the next agent the previous reply
plus your feedback, including any appended worktree handoff block and diff.

## Common Recipes

Call Claude Code's real `/review` skill through duet:

```bash
./duet.py --recap --task-from-cmd 'claude -p /review' \
    --lead claude:reviewer --partner codex:coder \
    --worktree \
    --cwd ~/workspace/project \
    --turns 6
```

The `/review` skill supplies the initial findings; duet handles the subsequent
Codex fix turn, Claude verification turn, worktree diff handoff, and any extra
rounds.

With the optional Claude Code skill from [docs/USAGE.md](docs/USAGE.md),
plain `/duet` runs that same `/review` kickoff recipe.

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

Require a mechanical check before convergence:

```bash
./duet.py \
  --task "Fix the issue" \
  --lead claude:coder \
  --partner codex:reviewer \
  --worktree --worktree-for lead \
  --verify-cmd 'make test'
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
    --lead claude:coder \
    --partner codex:reviewer \
    --reasoning max \
    --worktree --worktree-for lead \
    --turns 6
```

The partner speaks first, so Codex (reviewer) opens turn 1 with its critique
and Claude (coder) responds in turn 2 with the fixes. `--worktree-for lead`
keeps the editable checkout under the coder. Keep `--codex-fast` off in this
recipe: Codex is the reviewer, so max effort is the point.

That same recipe is also packaged as a YAML config you can drop into any
repo — `examples/pr-review.yaml` reviews `HEAD`'s diff with the same
agent/effort/worktree pairing, with comments calling out which keys to swap
for variants (review uncommitted changes, review a specific PR by number,
faster iteration once review is mostly done).

Review the latest commit plus an untracked notes file by seeding both into the
task:

```bash
./duet.py --recap \
    --task-from-cmd 'git show --stat --patch --no-ext-diff HEAD && printf "\n\n--- TODO.md ---\n" && cat TODO.md' \
    --lead claude:coder \
    --partner codex:reviewer \
    --reasoning max \
    --worktree --worktree-for lead \
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
- `state.json` - run state, agent roles, session ids, finish reason, worktree metadata, and `recap_path` for recap runs.
- `turn-*.stderr.log` - live stderr from each agent invocation.
- `turn-*-verify.log` - verify command metadata, stdout, and stderr when `--verify-cmd` runs.
- `turn-*.pid` - present only while an agent or verify command is running.
- `wt/` - the git worktree, when `--worktree` is enabled.

When a worktree agent replies, duet appends a handoff block to that reply before
the diff. The block names the exact worktree path and branch, warns that the
receiving agent's cwd may be a clean checkout, and includes `git -C <wt>` review
commands so verification happens against the edited tree.

When `--cwd` points outside the invocation directory and `--runs-dir` is not
set, artifacts go under the target project at `.duet/runs/<run_id>/`.

## Documentation

Read [docs/USAGE.md](docs/USAGE.md) for the full reference: flags, sandbox and
network rules, worktree mode, output layout, `--status` / `--continue`, force
prompt behavior, session memory, the post-run "apply / iterate / discard"
checklist, and the optional `/duet` Claude Code skill.

For contributor guidance, read [CLAUDE.md](CLAUDE.md). Codex-specific entry
notes live in [AGENTS.md](AGENTS.md).

## Limits

- `duet --continue <run>` starts a fresh run from a prior `state.json`, restores
  saved session ids, and reuses the previous worktree when available. It does
  not append to the old transcript.
- Parallel Codex sessions in the same cwd are safe when duet captured a
  session id from Codex's stderr — that turn pins to the UUID, not to recency.
  When the UUID was not captured (old Codex builds, or continuing a pre-UUID
  run), duet falls back to `codex exec resume --last`, which is cwd-based and
  unsafe to share. `--worktree` isolates duet's Codex cwd from the host repo;
  in `--last` fallback mode, do not start another Codex session inside that
  same worktree while the run is active.
- Transcripts capture full agent text. Convergence detection only counts
  rationale-backed sentinels outside fenced markdown code blocks.
