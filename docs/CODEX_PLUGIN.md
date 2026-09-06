# Codex Plugin

The Codex plugin installs the `duet` skill. It does not install the `duet`,
backend binaries. The skill shells out to the `duet` CLI
on your PATH.

For the shared `duet` skill used by Claude Code, Codex, and OpenCode, see
[Shared skill installation](INSTALLATION.md#shared-skill).

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

   The default recipe runs `claude -p /review --model sonnet` first, then uses
   `codex:coder` in a worktree. If you pass a custom partner or config, install
   whichever backend that recipe needs instead.

   For a Codex-only review, Claude is not required. Check the narrower set of
   dependencies instead:

   ```bash
   command -v git
   command -v codex
   command -v duet
   codex login status
   ```

   The Codex CLI can use an existing ChatGPT subscription sign-in, so this path
   does not require an API key. Both sessions use the same account limits; two
   models do not establish correctness.

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

   If `codex plugin add duet@volkan-duet` says the plugin is not found while
   `codex plugin marketplace add` says the marketplace is already added from a
   different source, a cached checkout may remain without its configuration
   entry. Reset the marketplace cache and registration, then retry:

   ```bash
   codex plugin marketplace remove volkan-duet
   codex plugin marketplace add volkan/duet
   codex plugin add duet@volkan-duet
   ```

   For a local install, use `/path/to/duet` in place of `volkan/duet`.

   Restart Codex or start a new thread after installing so the bundled skill is
   available.

## Upgrade

Update the CLI separately, then update the native Codex plugin:

```bash
pipx upgrade duet-cli
codex plugin marketplace upgrade volkan-duet
codex plugin add duet@volkan-duet
```

`marketplace upgrade` refreshes the GitHub checkout; `plugin add` reinstalls
Duet from that refreshed snapshot. For a local marketplace, pull its checkout
first, then run `codex plugin add duet@volkan-duet`. Start a new Codex thread
afterward. If a marketplace source conflict occurs, use the reminder in the
[install checklist](#install-checklist).

Use the package-manager upgrade command matching your CLI installation. For
the shared skill, follow [Upgrade](INSTALLATION.md#upgrade).

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
`claude -p /review --model sonnet` kickoff. Claude loop turns use Sonnet too.
It continues past one non-final automatic-turn timeout. Explicit flags
override recipe values.

Codex-only review:

```text
Use Codex-only Duet review with --recipe codex-review.
```

The Codex-only recipe starts with `codex:reviewer`, then hands its findings to
`codex:coder` in a strict worktree, using up to six turns. It enables recap and
finding reports, continues after
one non-final automatic-turn timeout, reviews the latest committed `HEAD`, and
then asks for focused fixes supported by the findings. It has no separate
Claude or upstream reviewer process. The plain `$duet` request keeps using the
Claude-plus-Codex `review` recipe. A request that says “I don't have Claude,”
“two Codex models,” or “Codex only” selects `codex-review`.

`--lead-model` and `--partner-model` are optional for `codex-review`; if they
are omitted, the CLI defaults apply. Preserve exact model IDs and never
silently substitute or force a model. For example:

```text
Use two Codex models for review: --lead-model gpt-5.6-sol and --partner-model gpt-5.6-luna.
```

Model availability depends on the account, rollout, and client. Both sessions
use the same account limits; two models do not establish correctness. See
OpenAI's [ChatGPT sign-in documentation](https://learn.chatgpt.com/docs/auth)
and [model availability documentation](https://learn.chatgpt.com/docs/models).

The `codex-review` recipe may not yet be present in the current PyPI release.
Run `duet --help` first; if `codex-review` is absent, use this checkout with
`make install` (or the checkout's Python entry point) rather than passing the
unsupported published flag.

The recipe also enables local finding reports in `review.md`.
`duet --report RUN` renders them from state; `--no-finding-reports` opts out.
See [FINDINGS.md](FINDINGS.md) for unresolved-ID continuation and optional human
feedback. Finding assessments do not replace the run's convergence status.

### Select models by name

For the default `claude:reviewer` lead and `codex:coder` partner, named models
map directly to `--lead-model` and `--partner-model`. Claude defaults to the
stable `sonnet` alias, and the recipe automatically pins its separate
`/review` kickoff to the same default or a Claude lead-model override.

For `codex-review`, both slots are Codex and the exact IDs supplied by the user
are preserved. Omitted model flags use the CLI defaults.

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

Run and supervise Duet directly. The skill must not spawn helper agents, and
current Duet disables native delegation inside its peers. See the
[subagent policy](SUBAGENTS.md) for the controls and their limits.

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

For a live turn, `active_turn.budget_seconds` reports the saved budget and
`active_turn.remaining_seconds` reports the zero-clamped time left. When
`last_timeout` is non-null, report its turn and agent; it remains present when
the review recipe continues after one non-final coder timeout so the partner
can inspect its failure block and worktree handoff.

You can also list recent runs:

```bash
duet --list
```

Stop one discovered run with:

```bash
duet --stop /path/to/project/.duet/runs/<run_id>
```

This request lets the active turn finish. Add `--immediate` when the run must
stop now. The skill then polls status until the run is terminal. It never uses
`pkill`, `killall`, process-name matching, or a broad process group. If status
remains `awaiting_force`, press Enter in the original Duet terminal or close
its stdin.

The default recipe uses `--worktree`, so edits land under:

```text
<run_dir>/wt/
```

Review or merge from the host repository using the commands duet prints at the
end of the run.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `codex plugin add duet@volkan-duet` cannot find the plugin | Run `codex plugin marketplace list` and confirm `volkan-duet` is listed. Add the local checkout or GitHub repo with `codex plugin marketplace add` if it is missing. |
| `codex plugin add duet@volkan-duet` cannot find the plugin, while marketplace add says it is already added from a different source | Follow the conditional cache reset reminder in [Install Checklist step 4](#install-checklist). |
| Codex does not invoke the skill after install | Start a new thread or restart Codex so the plugin's bundled skills are loaded. |
| The Duet skill says `duet` is not on PATH | Run `make install` from this repo or `pipx install duet-cli`, then make sure Codex's shell can resolve `command -v duet`. |
| The default recipe says `claude` is not on PATH | The missing binary is `claude`; install or authenticate Claude Code for the default `/review` recipe, or explicitly choose `--recipe codex-review` for the Codex-only path. |
| The default recipe says `codex` is not on PATH | Install Codex, or use a custom partner/config that does not require Codex. |
| No run metadata appears | Check the original duet process. A valid launch atomically creates the requested run-info file before `/review`; never scrape banners as a fallback. |
| The upstream command exits non-zero or prints no stdout | `duet --task-from-cmd` fails loud. Run that shell command directly in the target repo and fix its output first. |
