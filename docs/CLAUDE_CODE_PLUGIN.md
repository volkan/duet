# Claude Code Plugin

The Claude Code plugin installs the `/duet` slash command. It does not install
the `duet`, `claude`, `codex`, or `gemini` binaries. The slash command shells
out to the `duet` CLI on your PATH.

## Install Checklist

1. Install the `duet` CLI.

   From this repository:

   ```bash
   make install
   ```

   Or from PyPI:

   ```bash
   pipx install duet-cli
   pipx install 'duet-cli[yaml]'   # optional PyYAML support for --config
   ```

2. Confirm `duet` is visible to Claude Code's shell.

   ```bash
   command -v duet
   ```

3. Confirm the default `/duet` recipe dependencies are available.

   ```bash
   command -v claude
   command -v codex
   ```

   Plain `/duet` runs `claude -p /review` first, then uses `codex:coder` in a
   worktree. If you pass a custom partner or config, install whichever backend
   that recipe needs instead.

4. Add and install the marketplace plugin in Claude Code.

   ```text
   /plugin marketplace add volkan/duet
   /plugin install duet@volkan-duet
   ```

   If Claude Code says the plugin is already installed globally, that is fine.
   Use `/plugin` to inspect or manage the installed plugin.

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

The default command runs:

```bash
duet --recap \
  --cwd "$(pwd)" \
  --runs-dir "$(pwd)/.duet/runs" \
  --lead claude:reviewer \
  --partner codex:coder \
  --worktree \
  --turns 6 \
  --task-from-cmd 'claude -p /review'
```

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
Flags after that are forwarded to `duet`; explicit flags override the plugin
defaults.

## Runtime Expectations

For plain `/duet`, the run directory is not created immediately. The
`--task-from-cmd 'claude -p /review'` kickoff runs before duet allocates
`<runs-dir>/<run_id>/`, so `.duet/runs/*` may not exist while `/review` is
still producing the opening message.

Wait for this line in the default recap recipe:

```text
[duet] run: /path/to/project/.duet/runs/<run_id>
```

Non-recap runs print `[duet] run dir: ...` instead.

Then monitor from another terminal or Claude Code shell:

```bash
duet --status /path/to/project/.duet/runs/<run_id>
```

You can also list recent runs:

```bash
duet --list
```

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
| `/duet` says `duet` is not on PATH | Run `make install` from this repo or `pipx install duet-cli`, then make sure Claude Code's shell can resolve `command -v duet`. |
| Plain `/duet` says `claude` is not on PATH | Install/authenticate Claude Code before using the default `/review` recipe. |
| Plain `/duet` says `codex` is not on PATH | Install Codex, or pass a custom partner/config that does not use Codex. |
| No run directory appears right away | This is expected while `claude -p /review` is still running. Wait for `[duet] run: ...` or `[duet] run dir: ...` in the command output. |
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

If `$ARGUMENTS` is empty, run:

```bash
duet --recap --cwd "$(pwd)" --runs-dir "$(pwd)/.duet/runs" --lead claude:reviewer --partner codex:coder --worktree --turns 6 --task-from-cmd 'claude -p /review'
```

Otherwise run:

```bash
duet --cwd "$(pwd)" --runs-dir "$(pwd)/.duet/runs" --partner codex:coder --worktree --task-from-cmd $ARGUMENTS
```

After `[duet] run: ...` or `[duet] run dir: ...` appears, print the run dir
and the matching `duet --status <run_dir>` command.
EOF
````
