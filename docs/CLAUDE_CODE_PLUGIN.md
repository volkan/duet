# Claude Code Plugin

The Claude Code plugin exposes the shared `duet` skill as `/duet:duet`. It
uses the `duet` CLI on PATH and does not install Duet or backend CLIs. For
Claude itself, follow the [official setup guide](https://code.claude.com/docs/en/quickstart).

## 30-Second Setup

Install the `duet` CLI and confirm Claude Code's shell can find it:

```bash
pipx install duet-cli
command -v duet
```

`pipx` is recommended; `uv tool install duet-cli` or
`python3 -m pip install --user duet-cli` also put `duet` on PATH.

For the shared `duet` skill used by Claude Code, Codex, OpenCode, and ZCode,
see [Shared skill installation](INSTALLATION.md#shared-skill).
See the shared skill's [CLI checks](../plugins/duet/skills/duet/SKILL.md#prerequisites)
before launch. Command examples may differ from installed CLI versions.
See the [model selection guide](USAGE.md#same-backend-peering) for pairs,
aliases, and version IDs.
For OpenCode peers, use `provider/model` without a `#variant` suffix.
OpenCode v2 needs an explicit model for a backend effort argument.
Preserve the requested model and effort when reporting validation errors.
See [OpenCode peer models](OPENCODE_PLUGIN.md#opencode-peer-models) for the fallback and limits.

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
/duet:duet
```

The unified native plugin skill is `/duet:duet`; the standalone shared skill is
`/duet`. Choose one installation route per host to avoid duplicate skills.
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

   Existing plugin users: follow [Upgrade](#upgrade) to refresh the marketplace
   and plugin.

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

## Upgrade

Update the CLI separately, then update the native Claude Code plugin:

```bash
pipx upgrade duet-cli
```

```text
/plugin marketplace update volkan-duet
/plugin update duet@volkan-duet
/reload-plugins
```

Use the package-manager upgrade command matching your CLI installation. The
terminal equivalents are `claude plugin marketplace update volkan-duet` and
`claude plugin update duet@volkan-duet`. If installed at project or local
scope, pass the matching `--scope` to `claude plugin update`. Restart Claude
Code if reloading does not expose the updated skill. For the shared skill,
follow [Upgrade](INSTALLATION.md#upgrade).

The shared native package starts at version `0.2.14`. Claude uses the plugin
version to detect updates, so refreshing only the marketplace does not update
an installed plugin; run both update steps above. See the official
[plugin versioning reference](https://code.claude.com/docs/en/plugins-reference#version-management).

## Run It

The native plugin uses `/duet:duet`. If you installed the standalone shared
skill instead, use `/duet` in these examples.

Default review recipe:

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
Duet runs that kickoff with JSON output, requests `--permission-mode plan` for
a `plan` run and `default` otherwise, and counts its tool denials. Explicit
flags override it.

The review recipe also writes local `review.md` with structured finding
assessments. Use `duet --report RUN` to inspect it or pass
`--no-finding-reports` to disable collection. Claims and unresolved objections
are separate from recorded harness checks; see [FINDINGS.md](FINDINGS.md).

Custom upstream command:

```text
/duet:duet 'npm test 2>&1' --turns 4
```

Review a PR diff:

```text
/duet:duet 'gh pr diff' --turns 6 --reasoning high
```

Use Gemini instead of the default Codex partner:

```text
/duet:duet 'cat failing-log.txt' --partner gemini:coder --turns 2 --permission-mode plan
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

Report `converged` or `converged_after_force` as pair agreement. See
[the CLI convergence rule](USAGE.md#stop-conditions-and-force-prompt) for
the candidate revision and force-prompt rules. Duet returns `revision_changed`
if its final check finds a change or cannot read the candidate. For an independent review,
inspect the final diff in a fresh context and run its checks before acceptance.

Report any positive `tool_denials` count and mark dependent claims as
unverified. The count covers successful loop and forced turns and the
`review` recipe kickoff. It excludes seed extraction. A value of `null`
means the backend did not report denials.

For `agent_error` or `timeout`, status reports only `inspect artifacts`.
Read the last `[duet] AGENT ERROR` or `[duet] TIMEOUT` block in the
`artifacts.transcript` file and the stderr log that it names.
For `setup_error`, read the `[duet] SETUP ERROR` transcript block when present.
Otherwise, report the `[duet] ERROR:` message from the Duet process output.
For `kickoff_error`, read the transcript excerpt and the stderr log it names.

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

Claude, Gemini, and Copilot peers also receive access to a worktree outside
their cwd, including on resume and `--continue`. Added roots can permit writes;
use `--permission-mode plan` when a Claude peer must not edit.
See [worktree access](USAGE.md#access-to-the-worktree-from-the-other-agent).

Review or merge from the host repository using the commands duet prints at the
end of the run.

## Custom launches

A custom launch sets its own agents, models, sandbox, or config instead of a
recipe. Use `duet --help`, [duet.example.yaml](../duet.example.yaml), and the
[usage guide](USAGE.md) as the reference. Do not read `duet.py` for flags. Do
not use a flag that `duet --help` does not list. The CLI rejects it and no run
starts.

- Add `--finding-reports` to a custom review launch, also one that uses
  `--task-from-cmd`. Only the recipes enable it. Without it, the run writes no
  `review.md`.
- Valid roles are `planner`, `coder`, `reviewer`, and `triage-reviewer`. Any
  other role fails with `unknown role` on the first turn of that agent. For a
  custom role, set `role_prompt` for that agent in a `--config` file. The CLI has
  no flag for it. JSON works without extras. YAML needs `duet-cli[yaml]`.
- The partner speaks first. Put the agent that opens the loop in `--partner`.
- Pass the brief as `--task @FILE`. Peers usually have no network, because the
  Codex sandboxes block it. `gh` and `curl` fail inside a turn. Write the diff
  or other needed context into the file. Do not put secrets in it. Every peer
  receives it.
- Label the claims in the brief `L1`, `L2`, and so on. Releases up to 0.2.20
  accept only `L<n>` or `P<n>` as finding IDs. One other ID, such as `F1` or
  `C1`, makes Duet drop the whole findings block of that agent for that turn.

Two read-only Codex reviewers:

```bash
duet --lead codex:reviewer --partner codex:reviewer --sandbox read-only \
  --no-worktree --turns 4 --finding-reports --task @"$DUET_CONTROL_DIR/brief.md" \
  --cwd "$(pwd)" --runs-dir "$(pwd)/.duet/runs" --run-info-file "$DUET_RUN_INFO"
```

Add `--lead-model` and `--partner-model` only for models that you name. The
default for `--turns` is 2. `--no-worktree` lets the reviewers see uncommitted
changes, because a worktree holds only `HEAD`. If a peer can edit files, use a
worktree. Two Codex peers in one directory must each report a session UUID on
the first turn. If one does not, Duet stops the run. See
[Same-backend peering](USAGE.md#same-backend-peering).

## After the run

Do these steps when the run ends, for any `finished_reason`.

1. Read `<run_dir>/review.md` first when finding reports were on. Run
   `duet --report <run_dir>` to rebuild it. Add `--json` for data. Then read
   `recap.md` (only with recap) or `transcript.md`.
2. Relay each finding with its disposition. `supported` and `refuted` mean both
   agents assessed it the same way. `unresolved` means they disagree or
   evidence is missing. These are agent assessments, not proof. Agreement does
   not confirm a finding. The skill reports the cited evidence and the last open
   objection, and says which findings it checked itself. See
   [FINDINGS.md](FINDINGS.md).
3. Name the run directory in the final message.

When the report lists unresolved findings, for example after `max_turns`, the
skill offers this command and runs it only after you agree:

```bash
duet --continue <run_dir> --runs-dir <parent of run_dir> --resolve L1 --resolve P2
```

`--resolve` accepts only IDs that `--report` lists as unresolved. It takes two
turns by default. If you set `--turns`, the value must be 2 or more. The old run
and its stop reason stay unchanged. The findings can end unresolved again, and
Duet does not promise agreement. If the report says that the finding history is
incomplete, Duet refuses `--resolve`. Start a fresh review.

Use `duet --continue` for any follow-up with the same agents, sessions, and
worktree. Always pass `--runs-dir` set to the directory that holds the source
run, which is the parent of its run directory. Without it, Duet can write the new
run to `./runs/<id>` in the current directory. If Duet refuses to replay saved
commands or wider permissions, the skill reports the refusal. It adds
`--trust-state` only when you ask for it after you see the refusal.

`duet --feedback <run_dir> --usefulness V --decision D` records your verdict on
the run. Both flags are required. `V` is `useful`, `mixed`, `not_useful`, or
`unknown`. `D` is `accepted_finding`, `rejected_finding`, `corrected_comment`,
`no_change`, or `not_applied`. The skill runs it only when you state the outcome
and your words map exactly to a label. Otherwise it shows the command and asks.
It never chooses labels from agent agreement, test results, or a merge.

For a worktree run, the skill relays the printed `review:`, `merge:`, and
`drop:` lines as written. It does not build a drop command that Duet did not
print. It does not remove a worktree or branch that holds unmerged commits or
uncommitted changes without your go.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `/plugin install duet@volkan-duet` says the plugin is already installed globally | Nothing else is needed for the plugin. Use `/plugin` to inspect or manage it. |
| `/duet` does not appear after install | Run `/reload-plugins`, then try `/duet:duet` for the native plugin (`/duet` for a standalone skill). Use `/plugin list` to confirm the plugin is installed and enabled. |
| `/duet` says `duet` is not on PATH | Run `make install` from this repo or `pipx install duet-cli`, then make sure Claude Code's shell can resolve `command -v duet`. |
| Plain `/duet` says `claude` is not on PATH | Install/authenticate Claude Code before using the default `/review` recipe. |
| Plain `/duet` says `codex` is not on PATH | Install Codex, or pass a custom partner/config that does not use Codex. |
| No run metadata appears | Check the original duet process. A valid launch writes the requested run-info file before `/review`; do not scrape banners as a fallback. |
| The upstream command exits non-zero or prints no stdout | `duet --task-from-cmd` fails loud. Run that shell command directly in the target repo and fix its output first. |

## Manual Fallback

If native plugin installation is unavailable, use the same shared skill via
[the shared installer](INSTALLATION.md#shared-skill) or
[manual checkout links](INSTALLATION.md#manual-installation-from-a-checkout).
The standalone skill appears as `/duet` in Claude Code. Its source is
[`plugins/duet/skills/duet/SKILL.md`](../plugins/duet/skills/duet/SKILL.md);
there is no separate Claude recipe to copy or keep in sync.

For a ZCode model peer, use the [native ZCode backend](ZCODE.md).
Skill host support alone does not connect its account or select that backend.
