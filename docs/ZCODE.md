# ZCode with Duet

ZCode can load the shared Duet skill as a host. A ZCode peer also needs the
native `zcode` backend. Host skill installation alone does not add a backend
or connect another CLI's account. Native peers require Duet 0.2.17 or later.
Upgrade `duet-cli` through your package manager. For a source install, run
`make install` from the checkout.

## Host shortcuts

Install the shared skill as described in [Installation](INSTALLATION.md#shared-skill).
In ZCode, invoke `/skill duet` followed by the task or Duet arguments.

For a `/duet` shortcut, run these commands from the target project:

```bash
mkdir -p .zcode/commands
ln -s /absolute/path/to/duet/plugins/duet/integrations/zcode/duet.md .zcode/commands/duet.md
zcode commands inspect duet --json
```

Use your Duet checkout's absolute path. The link command fails if the destination exists.
Start a new ZCode session, then invoke `/duet` with your task or Duet arguments.
The wrapper requires the shared skill and forwards arguments through ZCode's native command handling.
It contains no separate launch or monitoring logic.

## Native review peer

The backend supports read-only `reviewer`, `triage-reviewer`, and `planner`
roles through a ZCode account. It does not support coder roles, custom API
providers, or `extra_args`. Pair it with another backend for implementation.

Duet uses the official `zcode` executable on PATH. On macOS, it can also use
the headless runtime in `/Applications/ZCode.app` through an installed Node.js.
It does not install a community CLI or copy account credentials.

Complete [account initialization](#account-initialization) and the native MCP prerequisite below before launching a peer.
Duet finds the PATH CLI's bundled catalog through its real executable path, including symlinks.
It never pairs a PATH CLI with another installation's catalog.

Use the native provider-qualified model ID. OpenCode's
`opencode/glm-5.3` belongs to a different provider and account.

```bash
duet --lead zcode:reviewer \
  --lead-model account:zai-individual-coding-plan/GLM-5.3 \
  --partner codex:reviewer --partner-model gpt-6.1-sol \
  --reasoning max --turns 6 --no-worktree \
  --task 'Review the plan against current source. Do not edit files.'
```

For a review and fix loop with a ZCode reviewer and Sol coder, reuse the existing recipe:

```bash
duet --recipe codex-review --lead zcode:reviewer \
  --lead-model account:zai-individual-coding-plan/GLM-5.3 \
  --partner-model gpt-6.1-sol --reasoning max
```

The explicit lead overrides the recipe's Codex reviewer. The coder uses a strict Git worktree.

For different reasoning per peer, use a JSON config:

```json
{
  "task": "Review the plan against current source. Do not edit files.",
  "max_turns": 6,
  "worktree": false,
  "sandbox": "read-only",
  "agents": [
    {"name": "glm", "backend": "zcode", "role": "reviewer",
     "model": "account:zai-individual-coding-plan/GLM-5.3", "reasoning_effort": "max"},
    {"name": "gpt", "backend": "codex", "role": "reviewer",
     "model": "gpt-6.1-sol", "reasoning_effort": "high"}
  ]
}
```

This example names the Z.ai Individual provider. Use the native provider ID
for the connected account. Model access and supported reasoning levels remain
ZCode's responsibility; an unavailable selection must fail, not change providers.

## Account initialization

The desktop OAuth record alone does not establish the standalone CLI connection.
The official CLI also stores an account identity and auth source. Initialize
them with its normal `login zai` or `login bigmodel` flow. Do not copy tokens,
renew an unrelated OpenCode key, or add identity values by hand.

With the official CLI installed, a separate profile keeps its default model
out of the desktop's personal provider configuration:

```bash
mkdir -p "$HOME/.duet/zcode"
ZCODE_PERSONAL_PROVIDER_CONFIG_FILE="$HOME/.duet/zcode/provider.json" \
  zcode login zai --no-browser
```

For the installed macOS desktop runtime:

```bash
mkdir -p "$HOME/.duet/zcode"
ZCODE_BUILTIN_PROVIDER_CONFIG_FILE="/Applications/ZCode.app/Contents/Resources/config/provider/zcode-builtin.json" \
ZCODE_PERSONAL_PROVIDER_CONFIG_FILE="$HOME/.duet/zcode/provider.json" \
  node /Applications/ZCode.app/Contents/Resources/glm/zcode.cjs \
  login zai --no-browser
```

Complete the first-party OAuth approval. The native login owns credential
storage. The separate profile contains model settings, not credentials.
`Select a model before continuing` can also mean the account is not available;
it does not prove that the requested model name is wrong.
When a ZCode turn fails with `project_token_login_failed`,
`project_token_login_required`, or `Select a model before continuing`, Duet adds
a `[duet] hint:` line to the error. The line points to the login command. Duet
adds no hint for a timeout.

## Desktop CLI in a terminal

The macOS ZCode desktop app includes a CLI at
`/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs`. Run it with
Node.js. Bun cannot load it and fails with `No such built-in module: node:sea`.

The desktop bundle has no terminal UI. With no command, it fails with
`Cannot find package '@zcode/tui'`. Use it for commands such as `-p`, `login`,
`logout`, `doctor`, and `app-server`. A terminal UI needs another ZCode
installation.

For headless use and for Duet, link the desktop entry file as `zcode` on PATH.
This needs Node.js on PATH. The link command fails if the file exists.

```bash
ln -s /Applications/ZCode.app/Contents/Resources/glm/zcode.cjs ~/.local/bin/zcode
```

Duet follows the link to the same bundle and uses the catalog of that bundle.
It needs no variable.

If another installation supplies the terminal UI, do not link the desktop
entry file. Put a wrapper on PATH as `zcode` instead:

```sh
#!/usr/bin/env sh
exec node /Applications/ZCode.app/Contents/Resources/glm/zcode.cjs "$@"
```

Duet uses `zcode` from PATH first. It finds the catalog only through the real
executable path. A wrapper script hides that path, and Duet then fails with
`Cannot locate the PATH ZCode CLI's catalog`. The error text names two fixes:
replace the wrapper with a link, or set this variable on the Duet command only:

```bash
ZCODE_BUILTIN_PROVIDER_CONFIG_FILE=/Applications/ZCode.app/Contents/Resources/config/provider/zcode-builtin.json \
  duet --lead zcode:reviewer ...
```

Use the catalog of the installation that runs headless calls. A wrapper can
send other commands, for example the terminal UI, to another installation. An
exported variable also reaches that installation, which then reads a catalog
that is not its own.

On macOS, if `zcode` is not on PATH, Duet runs the desktop CLI through Node.js
and needs no variable.

The desktop app and the CLI read the same credential store,
`~/.zcode/v2/credentials.json`. The CLI login adds its account identity to that
store. For the login command, see [account initialization](#account-initialization).
The help text of `zcode logout` is "Remove the shared Z.AI login credentials".
Do not copy tokens between installations.

Check the CLI with these commands:

```bash
zcode doctor
zcode -p 'Reply with OK.' --mode plan
```

`-p` uses the `yolo` permission mode by default. Always pass `--mode plan` for
this check.

Before the model request, the CLI sends the stored login token to the account
service to get a project token. Two errors can come from this step:

- `project_token_login_required`: the credential store has no login token or
  no account identity. Run the login command from
  [account initialization](#account-initialization), then repeat the check.
- `project_token_login_failed`: this step did not return a project token. The
  service rejected the stored token, or the CLI could not reach the service.
  Make sure that the network is available. Then log in again and repeat the
  check.

## Launch check

Before the first agent call, Duet checks each ZCode peer. The check repeats
the local checks of each turn:

- the role and the model ID;
- the ZCode runtime;
- the MCP prerequisite;
- the native catalog, with the requested reasoning level.

The check makes no model call. It runs before a configured kickoff command. For
a peer in a new worktree, the MCP prerequisite is checked after the worktree
exists. The kickoff command runs before that, so this failure appears after the
kickoff.

A failed check ends the run with `setup_error` and exit code 1. The error names
the agent and the fix. `--dry-run` skips the check. Each turn repeats it.

## Session and safety contract

Headless ZCode has no model/effort CLI flags. Duet supplies the requested
selection through a private, credential-free provider profile using the
official configuration schema. The shared account store stays in place.
The profile is removed after each turn. Global provider files are unchanged.
The native catalog validates the requested account model and effort before
launch. The peer profile disables every other catalog model, including models
on hidden providers. It snapshots the validated public catalog and disables
remote catalog refresh for the child. Native fallback cannot select another model.
An explicit effort also narrows the selected model to that validated level.
Native resume then rejects an externally changed effort instead of using it.
The macOS runtime supplies its builtin catalog. PATH CLI installations use their own bundled catalog.
A PATH link to the desktop entry file uses the catalog of that desktop bundle.
An explicit `ZCODE_BUILTIN_PROVIDER_CONFIG_FILE` overrides discovery.
A self-contained executable or wrapper without an accessible bundled catalog needs this variable on the Duet command only:

```bash
ZCODE_BUILTIN_PROVIDER_CONFIG_FILE=/absolute/path/to/that/zcode/installation/provider/zcode-builtin.json duet ...
```

Use the public JSON catalog from the installation that runs headless calls. A PATH CLI never borrows the desktop installation's catalog.

Native plan mode allows some MCP tools that do not declare themselves
destructive, including tools with missing annotations. It is not a read-only
MCP boundary. Review execution therefore requires effective native
`features.mcp: false` in the target's ZCode configuration. Duet checks this
prerequisite and does not change the user's settings. Existing MCP-enabled
projects need an explicit native configuration decision before using this backend.
To avoid duplicating the whole native schema, Duet certifies MCP=false only
from this complete minimal document, for example in `.zcode/config.json`:

```json
{"features": {"mcp": false}}
```

Any later features-bearing file must also be this minimal document. A richer
file could be ignored by native validation because of an unrelated invalid
field. Duet refuses that uncertain state instead of assuming MCP is disabled.

Calls use plan mode, disable dynamic workflows, and deny delegation,
shell, write, web, and workflow tools. This limits the native tool surface;
it is not an operating system sandbox. Custom hooks and plugins retain the
same limitations described in [the subagent policy](SUBAGENTS.md).

The adapter preserves all explicit tool denials from native user and discovered
project configurations. It combines them conservatively, even when a later
native file would replace an earlier denial. Command-pattern denials become
whole-tool denials. Unreadable, invalid, or unsupported permission shapes fail
before launch; a nonempty native `allowedTools` list is currently unsupported.
Permission input hashes are checked again immediately before launch. Concurrent
native config changes during the subprocess remain outside this check.
The supported catalog URL forms are canonical ASCII HTTP(S) endpoints. Exotic
URL forms fail before launch rather than risk different native rule matching.

Duet requires a valid JSON response and a `sess_` ID. Later turns use that
exact ID, never the latest session for a directory. A changed ID or malformed
reply ends the turn. ZCode restores model selection from the saved session;
Duet therefore persists the initial requested model/effort pin and refuses
changes on resume. Start a fresh peer to select another model or effort.

`--continue` preserves this pin. External sessions without a Duet selection
pin are not accepted. State records requested settings; native runtime events
are needed to verify actual model and reasoning use.

## Verification

Run `make ci` and the affected real loop from the source checkout:

```bash
make loop-test LOOP_TEST_ARGS='--scenario S2 --lead-backend zcode --lead-model account:zai-individual-coding-plan/GLM-5.3 --partner-backend codex --partner-model gpt-6.1-sol --reasoning max'
```

Inspect the native events as well as the Duet transcript. Require automatic
handoff, a resumed ZCode turn with the same ID, correct model/effort, and no
delegation calls. Dry-run convergence is not model verification.

Sources: [official ZCode source](https://github.com/zai-org/ZCode),
[CLI prompt entry](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/packages/cli/src/prompt-command.ts),
[provider profile schema](https://github.com/zai-org/ZCode/blob/main/packages/provider-node/src/provider-config-file-codec.ts),
[standalone login persistence](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/packages/bootstrap/src/auth-login-persistence.ts).
