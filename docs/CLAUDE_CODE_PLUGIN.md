# Claude Code Plugin

The Claude Code plugin installs the `/duet` slash command. It does not install
the `duet`, `claude`, `codex`, or `gemini` binaries. The slash command shells
out to the `duet` CLI on your PATH.

## 30-Second Setup

Install the `duet` CLI and confirm Claude Code's shell can find it:

```bash
pipx install duet-cli
command -v duet
```

`pipx` is recommended; `uv tool install duet-cli` or
`python3 -m pip install --user duet-cli` also put `duet` on PATH.

From this repository, `make install` is equivalent if `~/.local/bin` is on
PATH:

```bash
make install
command -v duet
```

Then run this inside Claude Code:

```text
/plugin marketplace add volkan/duet
/plugin install duet@volkan-duet
/reload-plugins
/duet
```

Use `/duet:duet` if your Claude Code install shows the namespaced command.
Use `/plugin list` to inspect installed plugins, or
`/plugin enable duet@volkan-duet` followed by `/reload-plugins` if the plugin
was installed but disabled.

Recording script: see [duet-plugin-demo.md](launch/duet-plugin-demo.md).

<!-- After recording, replace <ID> and uncomment:
[![asciicast](https://asciinema.org/a/<ID>.svg)](https://asciinema.org/a/<ID>)
-->

## Install Checklist

1. Install the `duet` CLI.

   From this repository:

   ```bash
   make install
   ```

   Or from PyPI (`pipx` recommended; `uv tool install` or
   `python3 -m pip install --user` also work):

   ```bash
   pipx install duet-cli
   pipx install 'duet-cli[yaml]'                       # optional PyYAML support for --config
   uv tool install duet-cli                            # alternative
   python3 -m pip install --user duet-cli              # alternative
   python3 -m pip install --user 'duet-cli[yaml]'      # alternative, with --config support
   ```

   Existing plugin users: run `/plugin marketplace update` and reinstall the
   plugin to pick up the narrowed plugin payload. Old cached versions are not
   cleaned up automatically.

2. Confirm `duet` is visible to Claude Code's shell.

   ```bash
   command -v duet
   ```

3. Confirm the default `/duet` recipe dependencies are available.

   ```bash
   command -v claude
   command -v codex
   ```

   Plain `/duet` runs `claude -p /review --model sonnet` first, then uses
   `codex:coder` in a worktree. If you pass a custom partner or config, install
   whichever backend that recipe needs instead.

4. Add and install the marketplace plugin in Claude Code.

   ```text
   /plugin marketplace add volkan/duet
   /plugin install duet@volkan-duet
   /reload-plugins
   ```

   If Claude Code says the plugin is already installed globally, that is fine.
   Use `/plugin` or `/plugin list` to inspect or manage the installed plugin.

## Run It

Depending on Claude Code command disambiguation, the command may appear as
`/duet` or `/duet:duet`.

Default review recipe:

```text
/duet
```

or:

```text
/duet:duet
```

The command creates a private run-info path and runs:

```bash
DUET_CONTROL_DIR=$(mktemp -d)
DUET_RUN_INFO="$DUET_CONTROL_DIR/run.json"
duet --recipe review --run-info-file "$DUET_RUN_INFO"
```

The recipe supplies recap mode, `claude:reviewer`, `codex:coder`, six turns,
strict worktree isolation, `claude -p /review --model sonnet`, Sonnet-defaulted
Claude loop turns, and continuation past one non-final automatic-turn timeout.
Explicit flags override it.

The review recipe also writes local `review.md` with structured finding
assessments. Use `duet --report RUN` to inspect it or pass
`--no-finding-reports` to disable collection. Claims and unresolved objections
are separate from recorded harness checks; see [FINDINGS.md](FINDINGS.md).

Custom upstream command:

```text
/duet 'npm test 2>&1' --turns 4
```

Review a PR diff:

```text
/duet 'gh pr diff' --turns 6 --reasoning high
```

Use Gemini instead of the default Codex partner:

```text
/duet 'cat failing-log.txt' --partner gemini:coder --turns 2 --permission-mode plan
```

The first quoted argument is the shell command used to create the kickoff text.
Flags after that are forwarded to `duet`. For custom commands the plugin
examines only those remaining duet flags (not text inside the shell command)
and inserts `--worktree` / `--require-worktree` only when neither side of the
corresponding override is already present. `--no-worktree` suppresses both
defaults, `--allow-worktree-fallback` suppresses the strictness default, and
`--worktree-path` suppresses fresh-worktree creation. This conditional
construction is required because argparse rejects mutually exclusive flags;
putting an override later is not sufficient. Custom commands also do not
pre-add `--recap`, so `--no-recap` remains valid.

## Autonomous handoff walkthrough

Claude Code can hand a multi-step implementation plan to `/duet`, ask the
agents to review the plan at high reasoning effort, and then continue through
implementation and review. The handoff exposes the generated shell command in
Claude Code's auto-mode details:

![Claude Code handoff using duet](assets/claude-duet-workflow.png?raw=true)

![Claude Code auto mode shell status](assets/claude-duet-auto-mode.png?raw=true)

![Claude Code shell details running duet](assets/claude-duet-shell-details.png?raw=true)

Copy-ready example:

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

## Runtime Expectations

Run and supervise Duet directly. The command must not spawn helper agents, and
current Duet disables native delegation inside its peers. See the
[subagent policy](SUBAGENTS.md) for the controls and their limits.

Duet writes the requested run-info JSON immediately after allocation and the
initial state write, before `/review` starts. The command validates schema 1,
reads its absolute `run_dir`, and monitors with:

```bash
duet --status /path/to/project/.duet/runs/<run_id> --json
```

The curated status document omits prompts, commands, credentials, and backend
extra arguments. Its exit codes are 0 terminal, 1 running, 2 stuck/crashed,
and 3 status error.

During a live turn, `active_turn.budget_seconds` shows the saved budget and
`active_turn.remaining_seconds` shows the zero-clamped time left. When
`last_timeout` is non-null, report its turn and agent; it remains present when
the review recipe lets the partner react to one non-final coder timeout.

You can also list recent runs:

```bash
duet --list
```

Stop one discovered run with `duet --stop <run_dir>`. This is graceful and
lets the active turn finish. Use `duet --stop <run_dir> --immediate` only when
the active child must end now. Poll status until the run is terminal.

If status remains `awaiting_force`, press Enter in the original duet terminal
or close its stdin. A signal does not reliably wake blocking TTY input on all
platforms. Duet records `force_stop` when that input returns.

Do not use `pkill`, `killall`, process-name matching, or a broad process group.
The stop interface validates the saved supervisor identity and signals only
that run's exact supervisor PID.

The default recipe uses `--worktree`, so edits land under:

```text
<run_dir>/wt/
```

Review or merge from the host repository using the commands duet prints at the
end of the run.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `/plugin install duet@volkan-duet` says the plugin is already installed globally | Nothing else is needed for the plugin. Use `/plugin` to inspect or manage it. |
| `/duet` does not appear after install | Run `/reload-plugins`, then try `/duet` or `/duet:duet`. Use `/plugin list` to confirm the plugin is installed and enabled. |
| `/duet` says `duet` is not on PATH | Run `make install` from this repo or `pipx install duet-cli`, then make sure Claude Code's shell can resolve `command -v duet`. |
| Plain `/duet` says `claude` is not on PATH | Install/authenticate Claude Code before using the default `/review` recipe. |
| Plain `/duet` says `codex` is not on PATH | Install Codex, or pass a custom partner/config that does not use Codex. |
| No run metadata appears | Check the original duet process. A valid launch writes the requested run-info file before `/review`; do not scrape banners as a fallback. |
| The upstream command exits non-zero or prints no stdout | `duet --task-from-cmd` fails loud. Run that shell command directly in the target repo and fix its output first. |

## Manual Fallback

If plugin install is unavailable, copy the same command as a user-level Claude
Code skill:

````bash
mkdir -p ~/.claude/skills/duet && cat > ~/.claude/skills/duet/SKILL.md <<'EOF'
---
name: duet
description: Run Claude Code's real /review through the duet two-agent harness, or hand off another command's output to duet. Wraps `duet --task-from-cmd <shell>` so /review, gh, npm test, cat error.log, or another upstream tool can drive a two-agent loop.
argument-hint: "['<shell command>' extra duet flags...]"
allowed-tools: Bash(*)
---

# /duet

First confirm `duet` is on PATH:

```bash
command -v duet
```

For plain `/duet`, also confirm:

```bash
command -v claude
command -v codex
```

Create a private control path:

```bash
DUET_CONTROL_DIR=$(mktemp -d)
DUET_RUN_INFO="$DUET_CONTROL_DIR/run.json"
```

If `$ARGUMENTS` is empty, run:

```bash
duet --recipe review --run-info-file "$DUET_RUN_INFO"
```

Otherwise run:

```bash
duet --cwd "$(pwd)" --runs-dir "$(pwd)/.duet/runs" \
  --partner codex:coder <conditional worktree defaults> \
  --run-info-file "$DUET_RUN_INFO" \
  --task-from-cmd '<upstream shell command>' <remaining duet flags>
```

Replace `<conditional worktree defaults>` before executing; never pass it
literally. Examine only the remaining duet flags. Add `--worktree` only when
none of `--worktree`, `--no-worktree`, or `--worktree-path` is present. Add
`--require-worktree` only when worktree use is not disabled and neither
`--require-worktree` nor `--allow-worktree-fallback` is present. Report
user-supplied conflicting pairs instead of rewriting them. Do not pre-add
`--recap`.

Validate schema 1 in `$DUET_RUN_INFO`, then poll the discovered run with
`duet --status <run_dir> --json`. Do not scrape banners.
Surface `active_turn.remaining_seconds`. When `last_timeout` is non-null,
report its turn and agent; the field remains present when the review recipe
continues past one non-final coder timeout.
EOF
````
