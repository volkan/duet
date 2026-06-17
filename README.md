# duet

**Two CLI agents in conversation. One Python file. Stdlib only.**

`duet` runs two command-line coding agents in alternating turns until they
agree. By default that is Claude and Codex; Gemini, Copilot, and OpenCode are
also supported, and you can pair two agents from the same backend. One agent
plans or reviews while the other implements; each keeps its own session memory
across turns, and every run leaves a transcript you can inspect.

## Use it four ways

### 1. Inside Claude Code — `/duet`

The fastest path if you already live in Claude Code.

```text
/plugin marketplace add volkan/duet
/plugin install duet@volkan-duet
/duet
```

Plain `/duet` runs Claude Code's real `/review`, then loops Codex and Claude in
a worktree until they converge. Pass any upstream command as the kickoff:
`/duet 'npm test 2>&1' --turns 4`. The plugin shells out to the `duet` CLI, so
install that first (see below) and make sure `command -v duet` passes in Claude
Code's shell. Full guide:
[docs/CLAUDE_CODE_PLUGIN.md](https://github.com/volkan/duet/blob/main/docs/CLAUDE_CODE_PLUGIN.md).
If Claude Code disambiguates plugin commands with namespaces, `/duet:duet` is
the same command.

Autonomous handoff example:

![Claude Code handoff using duet](./docs/assets/claude-duet-workflow.png?raw=true)

While the handoff runs, Claude Code shows the shell in auto mode and exposes
the exact `duet` command under shell details:

![Claude Code auto mode shell status](./docs/assets/claude-duet-auto-mode.png?raw=true)

![Claude Code shell details running duet](./docs/assets/claude-duet-shell-details.png?raw=true)

Copy-ready version:

```text
/loop /goal Create a temporary todo.md from the plan above and the remaining
tasks in todo_codex.md.

1. Use /duet:duet with max reasoning to confirm the plan.
2. After the plan is confirmed, implement it.
3. Once the first implementation is done, use /duet:duet with max reasoning
   for code review.
4. Use /duet:duet with max reasoning to review the second plan, then implement
   it.
5. When the process is complete and all checks are green, merge the approved
   changes.

P.S. I will not be around, so handle decisions without me. If you need another
opinion, use /duet:duet to discuss it with Codex.
```

### 2. Inside Codex — `$duet`

```text
codex plugin marketplace add volkan/duet
codex plugin add duet@volkan-duet
```

Start a new Codex thread and invoke `$duet`, or just ask Codex to use duet in
plain language. Like the Claude Code plugin, the skill shells out to the `duet`
CLI, so install that first (see below) and make sure `command -v duet` passes in
Codex's shell. Full guide:
[docs/CODEX_PLUGIN.md](https://github.com/volkan/duet/blob/main/docs/CODEX_PLUGIN.md).

### 3. Inside OpenCode — `/duet`

OpenCode custom commands are drop-in files — no marketplace step:

```bash
mkdir -p ~/.config/opencode/command
cp plugins/duet-opencode/command/duet.md ~/.config/opencode/command/duet.md
```

Then invoke `/duet` in the OpenCode TUI (or `opencode run --command duet "..."`
non-interactively). Like the other plugins it shells out to the `duet` CLI, so
install that first and make sure `command -v duet` passes in OpenCode's shell.
The command runs on OpenCode's `build` agent; plain `/duet` runs the same
`claude -p /review` kickoff, and `/duet 'npm test 2>&1' --turns 4` seeds from
any command. Full guide:
[docs/OPENCODE_PLUGIN.md](https://github.com/volkan/duet/blob/main/docs/OPENCODE_PLUGIN.md).
(duet can also drive OpenCode as a backend — `--partner opencode:coder` — so
OpenCode can be one of the two looped agents too.)

### 4. From the terminal — `duet`

```bash
pipx install duet-cli        # recommended; the command it installs is `duet`
duet --task "Fix the failing test" --cwd ~/code/myrepo
```

`pipx` is the recommended install. Two other persistent options put `duet` on
PATH the same way:

```bash
uv tool install duet-cli
python3 -m pip install --user duet-cli
```

The PyPI package is `duet-cli` (bare `duet` on PyPI is Google's async library).
Add the `[yaml]` extra for `--config foo.yaml` support — `pipx install
'duet-cli[yaml]'`, `uv tool install 'duet-cli[yaml]'`, or `python3 -m pip
install --user 'duet-cli[yaml]'`. One-shot, no install:
`uvx --from duet-cli duet --task "..."` — note this is ephemeral and does not put
`duet` on PATH, so the `/duet` and `$duet` plugins need a persistent install
(`pipx install duet-cli`, `uv tool install duet-cli`,
`python3 -m pip install --user duet-cli`, or `make install`) instead.

## Examples

Each command teaches one capability. The partner agent speaks first.

**Review loop** — Codex reviews at max effort, Claude applies only the fixes
Codex asks for, in an isolated worktree:

```bash
duet --task "Review the latest commit; fix only what the reviewer requests." \
    --lead claude:coder --partner codex:reviewer \
    --reasoning max --worktree --worktree-for lead --turns 6
```

**Seed from another tool's output** — drive the loop from Claude Code's real
`/review`, a test run, or any command:

```bash
duet --task-from-cmd 'claude -p /review' \
    --lead claude:reviewer --partner codex:coder \
    --worktree --recap --cwd ~/workspace/project --turns 6
```

**Deep planner, fast coder** — Claude plans at high effort while Codex coder
turns drop to low for latency:

```bash
duet --reasoning high --codex-fast \
    --task "Fix the issue" --cwd ~/workspace/project
```

**Verify gate** — a convergence proposal only counts if `make test` exits 0;
any failure feeds back into the next turn:

```bash
duet --task "Fix the issue" \
    --lead claude:coder --partner codex:reviewer \
    --verify-cmd 'make test' --worktree --worktree-for lead
```

**Resume a plan** — plan with Codex in its own session, then hand the session
id to duet; Codex implements with the plan in context while Claude reviews
(`--resume-claude <id>` does the inverse):

```bash
duet --resume-codex <codex-session-id> --worktree --reasoning max \
    --task "Implement the plan from your Codex planning session."
```

Reusable configs ship under `examples/` — `pr-review.yaml` (deep review of
`HEAD`) and `codex-test-fix.yaml` (Codex planner diagnoses failing checks, Codex
coder fixes them). Run one with `duet --config examples/pr-review.yaml`.

## How it works

Each agent keeps its own conversation memory across turns (Claude via
`--resume`, Codex via `codex exec resume`, Gemini, Copilot, and OpenCode via
their JSON session ids). On each turn duet sends one agent's latest reply to the
other.

To converge, an agent must include an `LGTM rationale:` explaining why the work
is done, followed by the sentinel `<<<LGTM>>>` on its own line — a bare
sentinel is ignored, and **both** agents must agree in back-to-back turns. The
loop also stops on `--turns`, a per-turn timeout, or Ctrl-C. After a normal
stop, duet opens a `force>` prompt so you can push another round.

Every run writes a directory with `transcript.md`, `state.json`, per-turn
stderr logs, and the `wt/` worktree when `--worktree` is on. Inspect a run with
`duet --status <run-id>`, list runs with `duet --list`, and start a fresh run
from saved state with `duet --continue <run> --task "next thing"`.

- **Backends:** `claude`, `codex`, `gemini`, `copilot`, `opencode`
- **Roles:** `planner`, `coder`, `reviewer`, `triage-reviewer`, or a custom one
- **Reasoning:** `--reasoning minimal|low|medium|high|xhigh|max`

## Documentation

[docs/USAGE.md](https://github.com/volkan/duet/blob/main/docs/USAGE.md) is the
full reference: every flag, reasoning levels, session memory, output layout,
`--status` / `--continue`, the force prompt, Codex sandbox and network rules,
and worktree mode.

## Contributing

Contributor guidance is in
[CLAUDE.md](https://github.com/volkan/duet/blob/main/CLAUDE.md); Codex entry
notes are in [AGENTS.md](https://github.com/volkan/duet/blob/main/AGENTS.md).
CI runs on every PR and is advisory until marked required — see
[.github/BRANCH_PROTECTION.md](https://github.com/volkan/duet/blob/main/.github/BRANCH_PROTECTION.md).
