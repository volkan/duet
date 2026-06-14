# Codex Plugin

The Codex plugin installs the `duet` skill. It does not install the `duet`,
`claude`, `codex`, or `gemini` binaries. The skill shells out to the `duet` CLI
on your PATH.

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

2. Confirm `duet` is visible to Codex's shell.

   ```bash
   command -v duet
   ```

3. Confirm the default Duet skill recipe dependencies are available.

   ```bash
   command -v claude
   command -v codex
   ```

   The default recipe runs `claude -p /review` first, then uses `codex:coder`
   in a worktree. If you pass a custom partner or config, install whichever
   backend that recipe needs instead.

4. Add and install the plugin marketplace in Codex.

   From a local checkout:

   ```bash
   codex plugin marketplace add /path/to/duet
   codex plugin add duet@volkan-duet
   ```

   From GitHub:

   ```bash
   codex plugin marketplace add volkan/duet
   codex plugin add duet@volkan-duet
   ```

   Restart Codex or start a new thread after installing so the bundled skill is
   available.

## Run It

Invoke the skill explicitly with `$duet`, or ask Codex to use Duet in natural
language.

Default review recipe:

```text
$duet
```

The default skill recipe runs:

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
Use Duet to run `npm test 2>&1` with --turns 4.
```

Review a PR diff:

```text
Use Duet to run `gh pr diff` with --turns 6 --reasoning high.
```

Use Gemini instead of the default Codex partner:

```text
Use Duet to run `cat failing-log.txt` with --partner gemini:coder --turns 2 --permission-mode plan.
```

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

Then monitor from another terminal or Codex shell:

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
| `codex plugin add duet@volkan-duet` cannot find the marketplace | Run `codex plugin marketplace list` and confirm `volkan-duet` is listed. Add the local checkout or GitHub repo with `codex plugin marketplace add` if it is missing. |
| Codex does not invoke the skill after install | Start a new thread or restart Codex so the plugin's bundled skills are loaded. |
| The Duet skill says `duet` is not on PATH | Run `make install` from this repo or `pipx install duet-cli`, then make sure Codex's shell can resolve `command -v duet`. |
| The default recipe says `claude` is not on PATH | Install or authenticate Claude Code before using the default `/review` recipe. |
| The default recipe says `codex` is not on PATH | Install Codex, or use a custom partner/config that does not require Codex. |
| No run directory appears right away | This is expected while `claude -p /review` is still running. Wait for `[duet] run: ...` or `[duet] run dir: ...` in the command output. |
| The upstream command exits non-zero or prints no stdout | `duet --task-from-cmd` fails loud. Run that shell command directly in the target repo and fix its output first. |
