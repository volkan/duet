# Awesome-list PR entries

Two target lists, each with its own entry style (copied from their READMEs as
of 2026-06-12). Submit after the PyPI release is live so `uvx --from duet-cli
duet` works for anyone who clicks through.

## 1. awesome-agent-orchestrators

- Repo: https://github.com/andyrewlee/awesome-agent-orchestrators
- Section: **Multi-Agent Swarms** ("Systems for coordinating multiple
  specialized agents working together") — peers like `hcom`, `kodo`, `ORCH`
  live here.
- Style: `- [lowercase-name](url) - One-line description ending in a period.`
  Entries are alphabetical (case-insensitive); `duet` slots between
  `CompanyHelm` and `fusion`.

Entry line:

```markdown
- [duet](https://github.com/volkan/duet) - Two CLI agents (Claude Code + Codex, or same-backend pairs) in alternating turns until both accept convergence with a rationale-backed LGTM; verify-command gate, git worktree isolation with diff handoffs, full transcripts and resumable state on disk. Single stdlib-only Python file.
```

PR title: `Add duet`

PR description:

> Adds duet under Multi-Agent Swarms (alphabetical slot between CompanyHelm
> and fusion). duet pairs exactly two CLI coding agents — Claude Code + Codex
> by default, same-backend pairs supported — in alternating turns until both
> sign off in back-to-back turns with a rationale-backed LGTM sentinel.
> Convergence can be gated on a shell command exiting 0 (`--verify-cmd`), the
> editing agent can be isolated in a git worktree with per-turn diff
> handoffs, and every run leaves a transcript plus `state.json` that
> `--status`/`--continue` operate on. Vendor-neutral and symmetric: neither
> agent's product hosts the loop. MIT, single stdlib-only Python file.

## 2. awesome-cli-coding-agents

- Repo: https://github.com/bradAGI/awesome-cli-coding-agents
- Section: **Harnesses & orchestration → Orchestrators & autonomous loops**
  ("Multi-agent coordination, swarm patterns, and autonomous execution
  loops. Sorted by GitHub stars.")
- Style: `- **[Name](url)** ` followed by a `` `⭐ N` `` star badge, an
  em-dash, a description, and often a trailing license. Sorted by stars —
  insert at the position matching duet's count at PR time (likely near the
  bottom; update `N` before opening the PR).

Entry line:

```markdown
- **[duet](https://github.com/volkan/duet)** `⭐ N` — Single-file Python harness that runs two CLI coding agents (Claude Code + Codex, or same-backend pairs) in alternating turns until both approve with a rationale-backed LGTM; optional verify-command gate before convergence counts, git worktree isolation with diff handoffs, full transcripts and `--status`/`--continue` resumable state on disk. Stdlib-only. MIT.
```

PR title: `Add duet (Orchestrators & autonomous loops)`

PR description:

> Adds duet to Orchestrators & autonomous loops, positioned by current star
> count. duet is a pairing harness rather than a parallel runner: exactly two
> CLI agents (Claude Code + Codex by default; Codex/Codex and Claude/Claude
> pairs work too) alternate turns, each keeping its own session memory
> (`claude --resume`, `codex exec resume <uuid>`), until both produce an LGTM
> rationale plus sentinel in back-to-back turns. A `--verify-cmd` shell gate
> must exit 0 before a convergence proposal counts, `--worktree` isolates the
> editing agent on its own branch with auto-appended diff handoffs, and runs
> persist transcript + state for `--status` health probes and `--continue`.
> Symmetric/vendor-neutral by design — neither vendor's product is the host.
> Stdlib-only single file, MIT.

## Checklist before opening either PR

- [ ] PyPI release published; `uvx --from duet-cli duet --help` works cold.
- [ ] Star badge count (`⭐ N`) updated for list 2.
- [ ] Re-check each list's CONTRIBUTING/README for format drift since
      2026-06-12 (alphabetical vs star ordering, trailing license, em-dash
      vs hyphen).
- [ ] One PR per list, single-line diff each — maintainers merge these fast
      when the diff is minimal and the ordering rule is respected.
