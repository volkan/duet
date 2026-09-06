# duet

**A second opinion for your AI coding work.**

Duet runs two coding agents in alternating turns: one reviews or plans, the
other implements or challenges the result. Each keeps its own conversation,
and you get a saved transcript to inspect.

Pair Claude and Codex, or use **two models through one CLI**. Gemini,
Copilot, and OpenCode are also supported.

[![Watch the 80-second review workflow demo](https://raw.githubusercontent.com/volkan/duet/main/docs/demos/finding-review.png)](https://github.com/volkan/duet/blob/main/docs/demos/finding-review.mp4)

An edited replay using a public toy fixture. [Playback and source](https://github.com/volkan/duet/blob/main/docs/demos/README.md).

## A real example

A supplied work audit describes a proposed fix that accidentally stopped
hiding sensitive values. The other agent challenged it, and the first revised
both the code and a test that had accepted the incorrect output.

[Read the anonymized cases and their limits](https://github.com/volkan/duet/blob/main/docs/REAL_CASES.md).

## Quick start

You need Python 3.9+ and the CLIs for your chosen pair, installed and signed in:
[install Claude Code](https://code.claude.com/docs/en/quickstart) and
[install Codex CLI](https://developers.openai.com/codex/cli#getting-started).
**Only have Codex?** [Run two Codex models](https://github.com/volkan/duet/blob/main/docs/USAGE.md#codex-only-review).
For the Claude + Codex pair, run this from a Git repository:

```bash
pipx install duet-cli
duet --recipe review
```

This starts a review and fix loop with the coder in a separate Git worktree.
Inspect the changes before merging; agent agreement does not prove correctness.

Already working inside an agent? Install the **same Duet skill** for Claude
Code, Codex, and OpenCode (requires Node.js/npm; choose **Symlink**):

```bash
npx skills add volkan/duet --skill duet --global \
  --agent claude-code --agent codex --agent opencode
```

Start a new session, then use `/duet` in Claude Code, `$duet` in Codex, or ask
OpenCode to use the `duet` skill.
[Installation details](https://github.com/volkan/duet/blob/main/docs/INSTALLATION.md)
cover `~/.agents/skills`, prerequisites, and alternative installers. Native
integration guides:
[Claude Code](https://github.com/volkan/duet/blob/main/docs/CLAUDE_CODE_PLUGIN.md),
[Codex](https://github.com/volkan/duet/blob/main/docs/CODEX_PLUGIN.md), or
[OpenCode](https://github.com/volkan/duet/blob/main/docs/OPENCODE_PLUGIN.md).

## Upgrade

Update the CLI and, if installed, the shared skill:

```bash
pipx upgrade duet-cli
npx skills update duet --global
```

Start a new agent session afterward. For `uv`, `pip`, source checkouts, or
native marketplace plugins, follow the
[upgrade guide](https://github.com/volkan/duet/blob/main/docs/INSTALLATION.md#upgrade).

## Go further

- [Installation, recipes, and CLI reference](https://github.com/volkan/duet/blob/main/docs/USAGE.md)
- [Review reports and feedback](https://github.com/volkan/duet/blob/main/docs/FINDINGS.md) · [Recorded workflow demo](https://github.com/volkan/duet/blob/main/docs/demos/README.md)
- [Local metrics and privacy](https://github.com/volkan/duet/blob/main/docs/METRICS.md) · [Usage evidence](https://github.com/volkan/duet/blob/main/docs/USAGE_EVIDENCE.md)

One Python file. Stdlib-only runtime; YAML support is optional. MIT licensed.

Contributing: [project guidance](https://github.com/volkan/duet/blob/main/CLAUDE.md),
[Codex notes](https://github.com/volkan/duet/blob/main/AGENTS.md), and
[merge checks](https://github.com/volkan/duet/blob/main/.github/BRANCH_PROTECTION.md).
