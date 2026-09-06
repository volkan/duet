# Install and upgrade Duet

Duet has two parts: the `duet-cli` Python package runs the agents, and the
optional `duet` skill lets an assistant launch and supervise it. Install the
CLI first; the skill uses the `duet` executable on PATH.

## Prerequisites

Use Python 3.9+ and Git. Install and sign in to the CLIs for your chosen pair:

| Tool | Official installation and sign-in guide |
|---|---|
| Claude Code | [Claude Code quickstart](https://code.claude.com/docs/en/quickstart) |
| Codex CLI | [Codex CLI getting started](https://developers.openai.com/codex/cli#getting-started) |
| OpenCode, when used as a host or backend | [OpenCode setup](https://opencode.ai/docs/) |

For the default Claude + Codex review, start `claude` and `codex` once and
complete their sign-in flows. A desktop app alone does not put its CLI on
PATH. Only have Codex? Use the [Codex-only review](USAGE.md#codex-only-review)
recipe, which needs no Claude installation. Custom pairs need their selected
backend CLIs.

## Install the CLI

With [pipx](https://github.com/pypa/pipx#install-pipx):

```bash
pipx install duet-cli
duet --version
```

Other persistent installations:

| Method | Install |
|---|---|
| uv | `uv tool install duet-cli` |
| pip | `python3 -m pip install --user duet-cli` |
| Source checkout | `make install` from a clone of this repository |

Choose one method so multiple `duet` executables do not shadow one another.
For YAML configs use `'duet-cli[yaml]'` as the package specifier; JSON configs
work without extras. The package is named `duet-cli`; the unrelated PyPI
package `duet` is not this project.

From a Git repository, start a review:

```bash
duet --recipe review
```

See [USAGE.md](USAGE.md#installation) for one-shot `uvx` runs and the full CLI
reference. One-shot runners do not install `duet` on the assistant's PATH.

## Shared skill

One skill definition serves Claude Code, Codex, and OpenCode. With Node.js/npm
available, use the [skills installer](https://github.com/vercel-labs/skills):

```bash
npx skills add volkan/duet --skill duet --global \
  --agent claude-code --agent codex --agent opencode
```

Choose **Symlink** when prompted. The installer keeps its canonical copy at
`~/.agents/skills/duet` and links it into agent-specific directories as needed.
Claude Code needs the `~/.claude/skills/duet` compatibility link; Codex and
OpenCode can discover `~/.agents/skills` directly. If symlinks are unavailable,
the installer can use copies. Remove unwanted `--agent` options to target
fewer hosts. The installer manages skills, not the Duet CLI or backend logins.

Start a new agent session after installing:

| Host | Invoke the shared skill |
|---|---|
| Claude Code | `/duet` |
| Codex | `$duet` |
| OpenCode | Ask: `Use the duet skill to review this repository.` |

The [OpenCode guide](OPENCODE_PLUGIN.md) also provides an optional `/duet`
command that loads this same skill.

The source lives at
[`plugins/duet/skills/duet/SKILL.md`](../plugins/duet/skills/duet/SKILL.md).
Both native marketplace manifests point to the same `plugins/duet` package.
The unified native package is version `0.2.14` or later; it remains compatible
with the `0.2.13` CLI through the existing schema 1 control interface.
Host discovery rules are documented in the official
[Codex skills guide](https://learn.chatgpt.com/docs/build-skills),
[Claude Code skills guide](https://code.claude.com/docs/en/skills), and
[OpenCode skills guide](https://opencode.ai/docs/skills/).

### Manual installation from a checkout

On macOS/Linux, from the root of a Duet checkout, link the shared skill:

```bash
mkdir -p ~/.agents/skills ~/.claude/skills
ln -s "$(pwd)/plugins/duet/skills/duet" ~/.agents/skills/duet
ln -s "$HOME/.agents/skills/duet" ~/.claude/skills/duet
```

These commands deliberately fail if a destination already exists. Inspect
an existing Duet skill before replacing it, especially if you customized it.
Keep the checkout at the same path while its skills are linked.

### Existing installations and native plugins

Use one installation route per host to avoid loading duplicate Duet skills.
For native marketplace installation, recovery, and updates, use the
[Claude Code](CLAUDE_CODE_PLUGIN.md) or [Codex](CODEX_PLUGIN.md) guide.

When switching from a native marketplace plugin to the shared installer,
remove that plugin through its host's plugin manager before installing the
shared skill. In Claude Code this changes `/duet:duet` to `/duet`; Codex
continues to use `$duet`. For an older copied OpenCode command, follow the
[OpenCode migration instructions](OPENCODE_PLUGIN.md#upgrade).

If Codex says the plugin was `not found` and marketplace add says
`already added from a different source`, use the reset reminder in the
[Codex installation checklist](CODEX_PLUGIN.md#install-checklist).

## Upgrade

Upgrade the CLI with the same method you used to install it:

| Installed with | Upgrade |
|---|---|
| pipx | `pipx upgrade duet-cli` |
| uv | `uv tool upgrade duet-cli` |
| pip | `python3 -m pip install --upgrade --user duet-cli` |
| Source checkout | `git -C /path/to/duet pull --ff-only`, then `make -C /path/to/duet install` |

If `pipx upgrade duet-cli` reports `already at latest version` despite a newer
release on PyPI, pip may be using a cached package index response. Retry
without the cache:

```bash
pipx upgrade duet-cli --pip-args="--no-cache-dir"
```

For a pip installation with YAML support, upgrade `'duet-cli[yaml]'` instead.
Package-manager installs receive published PyPI releases; a checkout follows
its Git branch. Verify which executable you updated:

```bash
command -v duet
duet --version
```

If you installed the shared skill from GitHub with `npx skills`, update just
Duet's global skill:

```bash
npx skills update duet --global
```

This updates the skill from its recorded source. It does not upgrade
`duet-cli`. Conversely, a Python package upgrade does not refresh a skill or
plugin. If you manually symlinked the skill to a checkout, pulling that
checkout refreshes the linked instructions; for manual copies, copy the
updated skill again. Native marketplace users should follow the
[Claude Code upgrade steps](CLAUDE_CODE_PLUGIN.md#upgrade) or
[Codex upgrade steps](CODEX_PLUGIN.md#upgrade).

Start a new agent session to load the updated skill. If the host CLI itself
needs updating, follow its official installation guide above; its update
method depends on how you installed that CLI.
