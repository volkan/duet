# OpenCode Plugin

OpenCode loads the shared `duet` skill natively. The skill uses the `duet` CLI
on PATH; it does not install Duet or backend CLIs.

For the shared `duet` skill used by Claude Code, Codex, OpenCode, and ZCode,
see [Shared skill installation](INSTALLATION.md#shared-skill).
See the shared skill's [CLI checks](../plugins/duet/skills/duet/SKILL.md#prerequisites)
before launch. Command examples may differ from installed CLI versions.
See the [model selection guide](USAGE.md#same-backend-peering) for pairs,
aliases, and version IDs.

An optional `/duet` command loads that same skill and forwards its arguments.
Install the shared skill first, then add the small command wrapper if you want
the slash command. See the official [OpenCode setup](https://opencode.ai/docs/)
and [custom command reference](https://opencode.ai/docs/commands/).

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

   The default recipe runs `claude -p /review --model sonnet` first, then uses
   `codex:coder` in a worktree. Claude loop turns use Sonnet too. If you pass a
   custom partner or config, install whichever backend that recipe needs
   instead. (duet can also drive OpenCode itself as a backend — `--partner
   opencode:coder` — so OpenCode can be one of the two looped agents, not just
   the host.)

4. Install the shared skill using the
   [shared installation guide](INSTALLATION.md#shared-skill). Start a new
   OpenCode session and ask it to use the `duet` skill.

5. Optionally install the `/duet` command wrapper.

   Global (available in every project), from a local checkout:

   ```bash
   mkdir -p ~/.config/opencode/commands
   cp plugins/duet/integrations/opencode/duet.md ~/.config/opencode/commands/duet.md
   ```

   Or symlink it so the command tracks the checkout:

   ```bash
   mkdir -p ~/.config/opencode/commands
   ln -s "$(pwd)/plugins/duet/integrations/opencode/duet.md" ~/.config/opencode/commands/duet.md
   ```

   Project-scoped instead of global (commit it to a repo so the team gets it):

   ```bash
   mkdir -p .opencode/commands
   cp /path/to/duet/plugins/duet/integrations/opencode/duet.md .opencode/commands/duet.md
   ```

   OpenCode discovers commands from the `commands/` directory under
   `~/.config/opencode/` (global) or `.opencode/` (project). The filename
   becomes the command name, so `duet.md` provides `/duet`.

## Run It

Use the shared skill natively with a request such as “Use the duet skill to
review this change.” The optional `/duet` wrapper loads that skill and passes
`$ARGUMENTS` through to it.

In the OpenCode TUI, invoke the command:

```text
/duet
```

Non-interactively from a shell (OpenCode v2 removed `run --command`; invoke
duet directly):

```bash
duet --task-from-cmd 'npm test 2>&1' --turns 4
```

Plain `/duet` runs the default review recipe:

```bash
duet --recipe review
```

The recipe enables local `review.md` finding reports. Read them with
`duet --report RUN`, or pass `--no-finding-reports` to opt out. The report keeps
agent assessments, unresolved objections, and executed checks distinct; see
[FINDINGS.md](FINDINGS.md).

Pass an upstream command (and optional duet flags) as arguments — the first
quoted token is the shell command duet seeds from, anything after is forwarded
to duet:

```text
/duet 'npm test 2>&1' --turns 4
/duet 'gh pr diff' --turns 6 --reasoning high
/duet 'cat failing-log.txt' --partner opencode:coder --turns 2
```

For a custom command, the OpenCode prompt separates the first quoted shell
string from the remaining duet flags and constructs worktree defaults
conditionally. With no topology override it adds
`--worktree --require-worktree`; `--no-worktree` suppresses both defaults;
`--allow-worktree-fallback` suppresses `--require-worktree`; and
`--worktree-path` suppresses fresh-worktree creation. It examines only the
remaining duet flags, so a similarly named option inside the upstream shell
command cannot change duet topology. This is not based on argument order:
argparse rejects mutually exclusive flags even when the override is later.
Custom commands do not pre-add `--recap`, keeping `--no-recap` valid.

The command runs on OpenCode's `build` agent (full tool access) so it can
shell out to `duet`. Make sure your OpenCode permissions allow the `build`
agent to run shell commands, or run with `--auto` for the non-interactive form.
Auto mode still respects explicit permission denials.

## OpenCode peer models

Use a `provider/model` ID for an OpenCode peer. Duet rejects a `#variant`
suffix in the model ID. Set `--reasoning` or the agent's `reasoning_effort`
instead. Report a rejected ID without substituting another model.

OpenCode v1 receives the requested effort through `--variant`. OpenCode v2
requires an explicit model to receive effort through `-m provider/model#<level>`.
Without a model, v2 receives only the prompt prefix and Duet prints a note.
High, xhigh, and max add prompt nudges; other levels have no prompt nudge.
Report this limit without choosing another model or changing the requested effort.

For an explicit v2 peer model, inspect its resolved variants before launch.
Run discovery from the peer's effective cwd with the run's configuration and environment:

```sh
opencode models --standalone
opencode api --standalone model.list --param 'location[directory]=<absolute-peer-cwd>'
```

Use the model list for discovery. Forward an exact requested ID even when discovery omits it.
In the API response's `data` array, match `providerID` and `id`, then read `variants[].id`.
Resolved variants include provider handling, budget-derived levels, and configured overrides.
If the installed CLI lacks this API command, report that discovery is unavailable.
OpenCode v2.3.0 [caches its catalog in the database](https://github.com/anomalyco/opencode/blob/v2.3.0/packages/core/src/models-dev.ts#L531-L575).
Thus, `cache/models.json` can be a stale legacy file.
The [models.dev API](https://models.dev/api.json) supplies supplementary metadata for the exact provider and model.
Its `reasoning_options` array can contain an object with `type: "effort"` and a `values` list.
For example, `zai-coding-plan/glm-5.3` lists `low`, `high`, and `max`.
Missing catalog values do not prove that resolved variants are unavailable.
Report unknown support when discovery is inconclusive. Preserve the requested selection.
Ask the user before launching without the requested level. These discovery commands are for v2 only.

If resolved variants exclude the level, or a turn fails with `Variant unavailable`, report the resolved variant IDs.
For recommendations, keep only variants on Duet's scale (`minimal < low < medium < high < xhigh < max`).
Propose the smallest supported level at or above the request, else the largest below it.
If none remain, report that Duet's effort option cannot select those variants.
Do not map `none`, `thinking`, `default`, or null to a Duet level.
Launch with a changed level only after the user picks it.

For nested checkout paths and clone cleanup limits, see [worktree isolation](USAGE.md#where-the-worktree-lives).

## Upgrade

Update the CLI and shared skill using [the upgrade guide](INSTALLATION.md#upgrade).
If you copied the optional wrapper, pull your Duet checkout and copy it again
from the checkout root:

```bash
git pull --ff-only
mkdir -p ~/.config/opencode/commands
cp plugins/duet/integrations/opencode/duet.md ~/.config/opencode/commands/duet.md
```

A symlinked wrapper follows the checkout automatically. Older installations
copied a standalone recipe to `~/.config/opencode/command/duet.md` or
`.opencode/command/duet.md`. After installing the shared skill, remove only
that old Duet command and replace it with the optional wrapper above if you
still want `/duet`. Restart OpenCode after changing commands or skills.

## Runtime Expectations

The command instructs the supervising assistant to run Duet directly without
extra agents. Current Duet also disables native delegation inside each peer;
see [SUBAGENTS.md](SUBAGENTS.md) for controls, compatible versions, and limits.

The recipe writes a private run-info JSON document before starting the
`/review` kickoff. Read its absolute `run_dir`, then monitor from another
terminal or OpenCode shell:

```bash
duet --status /path/to/project/.duet/runs/<run_id> --json
```

A `converged` or `converged_after_force` result means that the two agents
agreed. It is not an independent review. When `tool_denials` is a number above
zero, report it. Checks that needed the denied tools did not run, so dependent
claims are unverified. The count covers only successful loop and forced turns,
not the kickoff or seed extraction. `null` means that the backend did not
report denials. Tell the user that convergence is not an independent review.
Recommend that they review the final diff in a fresh context and run its
checks before they accept or merge the code.

For `agent_error` or `timeout`, status reports only `inspect artifacts`.
Read the last `[duet] AGENT ERROR` or `[duet] TIMEOUT` block in the
`artifacts.transcript` file and the stderr log that it names.
For `setup_error` or `kickoff_error`, there is no such block. Report the
full `[duet] ERROR:` message from the Duet process output, with all of its lines.

You can also list recent runs:

```bash
duet --list
```

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

## Troubleshooting

| Symptom | Fix |
|---|---|
| OpenCode cannot find the `duet` skill | Install the shared skill, confirm `~/.agents/skills/duet/SKILL.md` exists, and start a new session. The optional command wrapper needs this skill. |
| `/duet` does not appear in OpenCode | Confirm the file is at `~/.config/opencode/commands/duet.md` (or `.opencode/commands/duet.md` in the project). Restart the OpenCode session so it re-scans commands. Remove only an old Duet `command/duet.md` if it creates a duplicate. |
| `/duet` runs but says `duet` is not on PATH | Run `make install` from this repo or `pipx install duet-cli`, then make sure OpenCode's shell can resolve `command -v duet`. |
| The default recipe says `claude` is not on PATH | Install or authenticate Claude Code before using the default `/review` recipe. |
| The default recipe says `codex` is not on PATH | Install Codex, or use a custom partner/config that does not require Codex. |
| The command stalls without running `duet` | OpenCode is likely waiting on a permission prompt for the `build` agent's shell tool. Approve it, or run the loop directly from a shell (OpenCode v2 removed `run --command`): `duet --task-from-cmd "..." --turns 4`. Explicit permission denials still apply. |
| No run directory appears | Check the original duet process. The recipe writes initial state before `/review`; use `duet --list` or a custom `--run-info-file` launch to discover it deterministically. |
| The upstream command exits non-zero or prints no stdout | `duet --task-from-cmd` fails loud. Run that shell command directly in the target repo and fix its output first. |

For a ZCode model peer, use the [native ZCode backend](ZCODE.md).
Skill host support alone does not connect its account or select that backend.
