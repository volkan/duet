# OpenCode Plugin

The OpenCode integration installs a `/duet` custom command. It does not install
the `duet`, `claude`, `codex`, or `gemini` binaries. The command shells out to
the `duet` CLI on your PATH.

Unlike the Claude Code plugin (a marketplace plugin) and the Codex plugin (a
marketplace skill), OpenCode custom commands are **drop-in markdown files** —
there is no marketplace step. You copy (or symlink) one file into OpenCode's
command directory and `/duet` is available.

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

2. Confirm `duet` is visible to OpenCode's shell.

   ```bash
   command -v duet
   ```

3. Confirm the default `/duet` recipe dependencies are available.

   ```bash
   command -v claude
   command -v codex
   ```

   The default recipe runs `claude -p /review` first, then uses `codex:coder`
   in a worktree. If you pass a custom partner or config, install whichever
   backend that recipe needs instead. (duet can also drive OpenCode itself as a
   backend — `--partner opencode:coder` — so OpenCode can be one of the two
   looped agents, not just the host.)

4. Install the `/duet` command.

   Global (available in every project), from a local checkout:

   ```bash
   mkdir -p ~/.config/opencode/command
   cp plugins/duet-opencode/command/duet.md ~/.config/opencode/command/duet.md
   ```

   Or symlink it so the command tracks the checkout:

   ```bash
   mkdir -p ~/.config/opencode/command
   ln -s "$(pwd)/plugins/duet-opencode/command/duet.md" ~/.config/opencode/command/duet.md
   ```

   Project-scoped instead of global (commit it to a repo so the team gets it):

   ```bash
   mkdir -p .opencode/command
   cp /path/to/duet/plugins/duet-opencode/command/duet.md .opencode/command/duet.md
   ```

   OpenCode discovers commands from the `command/` directory (singular) under
   `~/.config/opencode/` (global) or `.opencode/` (project). The filename
   becomes the command name, so `duet.md` provides `/duet`.

## Run It

In the OpenCode TUI, invoke the command:

```text
/duet
```

Non-interactively from a shell:

```bash
opencode run --command duet "'npm test 2>&1' --turns 4"
```

Plain `/duet` runs the default review recipe:

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

Pass an upstream command (and optional duet flags) as arguments — the first
quoted token is the shell command duet seeds from, anything after is forwarded
to duet:

```text
/duet 'npm test 2>&1' --turns 4
/duet 'gh pr diff' --turns 6 --reasoning high
/duet 'cat failing-log.txt' --partner opencode:coder --turns 2
```

The command runs on OpenCode's `build` agent (full tool access) so it can
shell out to `duet`. Make sure your OpenCode permissions allow the `build`
agent to run shell commands, or run with `--dangerously-skip-permissions` for
the non-interactive form.

## Runtime Expectations

For the default recipe, the run directory is not created immediately. The
`--task-from-cmd 'claude -p /review'` kickoff runs before duet allocates
`<runs-dir>/<run_id>/`, so `.duet/runs/*` may not exist while `/review` is
still producing the opening message.

Wait for this line in the default recap recipe:

```text
[duet] run: /path/to/project/.duet/runs/<run_id>
```

Non-recap runs print `[duet] run dir: ...` instead.

Then monitor from another terminal or OpenCode shell:

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
| `/duet` does not appear in OpenCode | Confirm the file is at `~/.config/opencode/command/duet.md` (or `.opencode/command/duet.md` in the project). The directory is `command/`, singular. Restart the OpenCode session so it re-scans commands. |
| `/duet` runs but says `duet` is not on PATH | Run `make install` from this repo or `pipx install duet-cli`, then make sure OpenCode's shell can resolve `command -v duet`. |
| The default recipe says `claude` is not on PATH | Install or authenticate Claude Code before using the default `/review` recipe. |
| The default recipe says `codex` is not on PATH | Install Codex, or use a custom partner/config that does not require Codex. |
| The command stalls without running `duet` | OpenCode is likely waiting on a permission prompt for the `build` agent's shell tool. Approve it, or invoke non-interactively with `opencode run --command duet --dangerously-skip-permissions "..."`. |
| No run directory appears right away | This is expected while `claude -p /review` is still running. Wait for `[duet] run: ...` or `[duet] run dir: ...` in the command output. |
| The upstream command exits non-zero or prints no stdout | `duet --task-from-cmd` fails loud. Run that shell command directly in the target repo and fix its output first. |
