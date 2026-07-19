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

The skill creates a private run-info path, then launches the canonical recipe:

```bash
DUET_CONTROL_DIR=$(mktemp -d)
DUET_RUN_INFO="$DUET_CONTROL_DIR/run.json"
duet --recipe review --run-info-file "$DUET_RUN_INFO"
```

`--recipe review` expands to the current project, `.duet/runs`, recap mode,
`claude:reviewer`, `codex:coder`, six turns, strict worktree isolation, and a
`claude -p /review` kickoff. Explicit flags override recipe values.

### Select models by name

For the default `claude:reviewer` lead and `codex:coder` partner, named models
map directly to `--lead-model` and `--partner-model`. The recipe automatically
pins its separate `/review` kickoff to a Claude lead model too.

For example:

```text
Use Duet with Opus 4.8 and GPT Sol.
```

The skill translates that request to:

```bash
duet --recipe review \
  --run-info-file "$DUET_RUN_INFO" \
  --lead-model claude-opus-4-8 \
  --partner-model gpt-5.6-sol
```

If the user supplies exact backend model IDs, the skill preserves them. A
request for the latest Opus without a version uses Claude's stable `opus`
alias; `Fable 5` maps to `claude-fable-5`. With custom agents, the model follows
the slot: the `--lead` agent uses
`--lead-model`, and the `--partner` agent uses `--partner-model`.

Custom upstream command:

```text
Use Duet to run `npm test 2>&1` with --turns 4.
```

For custom commands the skill separates the upstream shell string from the
remaining duet flags. It adds strict worktree defaults only when those flags do
not already select `--worktree`, `--no-worktree`, `--worktree-path`,
`--require-worktree`, or `--allow-worktree-fallback`. This is conditional
command construction, not argument-order overriding: argparse rejects both
members of a mutually exclusive pair even when one appears later. For example,
`--no-worktree` suppresses both defaults, while
`--allow-worktree-fallback` keeps `--worktree` but suppresses
`--require-worktree`. The skill also leaves recap unset for custom commands so
an explicit `--no-recap` cannot conflict with a pre-added `--recap`.

Review a PR diff:

```text
Use Duet to run `gh pr diff` with --turns 6 --reasoning high.
```

Use Gemini instead of the default Codex partner:

```text
Use Duet to run `cat failing-log.txt` with --partner gemini:coder --turns 2 --permission-mode plan.
```

## Runtime Expectations

Duet atomically writes `DUET_RUN_INFO` immediately after allocating the run and
writing initial `state.json`, before `/review` starts. The skill validates
`schema_version == 1` and `kind == "duet.run"`, then monitors the absolute
`run_dir` from that document:

```bash
duet --status /path/to/project/.duet/runs/<run_id> --json
```

The status schema reports `health`, `phase`, `finished_reason`, active/last
turns, and artifact paths without exposing prompts, commands, credentials, or
backend extra arguments. Status exit codes are 0 terminal, 1 running, 2
stuck/crashed, and 3 status error.

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
| No run metadata appears | Check the original duet process. A valid launch atomically creates the requested run-info file before `/review`; never scrape banners as a fallback. |
| The upstream command exits non-zero or prints no stdout | `duet --task-from-cmd` fails loud. Run that shell command directly in the target repo and fix its output first. |
