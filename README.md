# duet

**A second opinion for your AI coding work.**

Duet runs two coding agents in alternating turns: one reviews or plans, the
other implements or challenges the result. Each keeps its own conversation,
and you get a saved transcript to inspect.

Claude and Codex are the default pair. Gemini, Copilot, OpenCode, and two
agents from the same backend are also supported.

[![Watch the 80-second review workflow demo](https://raw.githubusercontent.com/volkan/duet/main/docs/demos/finding-review.png)](https://github.com/volkan/duet/blob/main/docs/demos/finding-review.mp4)

An edited replay using a public toy fixture. [Playback and source](https://github.com/volkan/duet/blob/main/docs/demos/README.md).

## A real example

A supplied work audit describes a proposed fix that accidentally stopped
hiding sensitive values. The other agent challenged it, and the first revised
both the code and a test that had accepted the incorrect output.

[Read the anonymized cases and their limits](https://github.com/volkan/duet/blob/main/docs/REAL_CASES.md).

## Quick start

You need Python 3.9+ and installed, signed-in Claude Code and Codex CLIs.
Run this from a Git repository:

```bash
pipx install duet-cli
duet --recipe review
```

This starts a review and fix loop with the coder in a separate Git worktree.
Inspect the changes before merging; agent agreement does not prove correctness.

Already working inside an agent? Follow the plugin guide for
[Claude Code](https://github.com/volkan/duet/blob/main/docs/CLAUDE_CODE_PLUGIN.md),
[Codex](https://github.com/volkan/duet/blob/main/docs/CODEX_PLUGIN.md), or
[OpenCode](https://github.com/volkan/duet/blob/main/docs/OPENCODE_PLUGIN.md).

## Go further

- [Installation, recipes, and CLI reference](https://github.com/volkan/duet/blob/main/docs/USAGE.md)
- [Review reports and feedback](https://github.com/volkan/duet/blob/main/docs/FINDINGS.md) · [Recorded workflow demo](https://github.com/volkan/duet/blob/main/docs/demos/README.md)
- [Local metrics and privacy](https://github.com/volkan/duet/blob/main/docs/METRICS.md) · [Usage evidence](https://github.com/volkan/duet/blob/main/docs/USAGE_EVIDENCE.md)

One Python file. Stdlib-only runtime; YAML support is optional. MIT licensed.

Contributing: [project guidance](https://github.com/volkan/duet/blob/main/CLAUDE.md),
[Codex notes](https://github.com/volkan/duet/blob/main/AGENTS.md), and
[merge checks](https://github.com/volkan/duet/blob/main/.github/BRANCH_PROTECTION.md).
