# Subagents in Duet

Duet's peers must do their work directly. Running a CLI in batch mode does
not, by itself, prevent that CLI from delegating to additional agents. An
earlier Codex test had two top-level Duet sessions **and** a native
`spawn_agent` call, so counting peer sessions was insufficient.

Current source disables native delegation for every adapter call, including
fresh turns, resumes, seed extraction, and forced turns. The built-in Claude
review kickoff also denies delegation tools. Role prompts tell both peers to
work directly even when a project's instructions request subagents.

## Native controls

Official documentation checked on 2026-09-06:

| Backend | Control applied by Duet | Official reference |
| --- | --- | --- |
| Codex | `agents.enabled=false`, `features.multi_agent=false`, and `--disable multi_agent` on fresh and resumed calls. Top-level and default app approval review use `user`; `features.guardian_approval=false` and `--disable guardian_approval` turn off the Guardian feature. `--approve-for-me` is rejected. See the validation limits below for named app overrides. | [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) |
| Claude Code | Deny `Agent`, its legacy `Task` alias, `TeamCreate`, `SendMessage`, and `Skill`. Disable agent-team and fork-mode environment toggles for peer calls. `Skill` is denied because forked skills can create subagents. | [Subagents](https://code.claude.com/docs/en/sub-agents), [CLI deny rules](https://code.claude.com/docs/en/cli-reference) |
| Copilot | Exclude `task` and `write_agent` from the model's available tools. | [Tool availability](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference#tool-availability-values) |
| OpenCode | Set `subagent_depth: 0` in the child process's inline config and check `opencode debug config` before every turn. Stop if the resolved depth is not integer zero. Use `--auto`, which preserves explicit permission denials. | [Subagent depth](https://opencode.ai/docs/config/#subagent-depth), [Config precedence](https://opencode.ai/docs/config/#precedence-order), [Auto mode](https://opencode.ai/docs/permissions/#auto-mode) |
| Gemini | Set `experimental.enableAgents: false` through a private temporary system-settings overlay. Preserve the original system-defaults file location for each child process. | [Configuration and precedence](https://geminicli.com/docs/reference/configuration/), [Settings loader](https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/config/settings.ts) |

User tool exclusions are preserved when Duet adds its own. Codex controls
follow extra config arguments and are reapplied when continuing an old run.
`--trust-state` does not opt out. OpenCode remote `--attach` and the old
`--dangerously-skip-permissions` override are rejected because Duet cannot
enforce its local controls through those paths.

OpenCode's existing `OPENCODE_CONFIG_CONTENT` accepts JSONC comments and
trailing commas, matching the native parser. Gemini system settings accept
comments. Quoted strings and other settings are preserved. Invalid syntax or
a non-object config stops the invocation instead of silently dropping settings.
Gemini's original settings file is untouched, its system-defaults path remains
the original sibling file unless explicitly overridden, and the private copy
is removed after the call. Relative system-settings paths resolve from the
peer's working directory. Symlinked settings retain the defaults file beside
the configured path, not beside the symlink target. Neither mechanism moves
credentials or changes the user's global configuration.

OpenCode managed configuration can override inline settings, so the native
configuration check must succeed before the peer starts. It uses the same
child environment and effective working directory, runs no model request,
and has a 30-second cap within the turn's time budget. Duet does not print or
persist the configuration output. A failed check ends the turn; it never
falls back to running the peer without the check.

These controls require CLI versions that support the documented settings.
An unsupported flag or configuration error ends the turn; Duet does not retry
with delegation enabled. The published 0.2.12 package predates this policy.

## What the policy establishes

It restricts the CLIs' native delegation facilities. It is **not an operating
system barrier against every possible additional model call**. Custom startup
hooks, plugins, MCP servers, explicit kickoff/verification commands, or an
agent's shell command can invoke another process or service. The role prompt
prohibits workarounds, but a prompt alone cannot enforce that boundary.
Already-running children from a session created outside Duet are not canceled
by this policy; use fresh sessions when that history is unknown.

For tool comparisons, inspect the native session events as well as Duet's
transcript. Check executed delegation calls and child-session records, not
just an agent's claim that it worked alone. Compaction, retries, verification,
and the review kickoff can still produce additional work; two peers does not
mean exactly two model requests.

New state and metrics profiles record `subagent_policy: "disabled"`. This is
the launch policy, **not a measured zero-subagent count**. Historical
records without it remain unknown. Metrics groups distinguish that policy so
older unrestricted runs are not silently pooled with restricted runs.

## Validation on this change

Codex CLI 0.149.0 resolved `multi_agent=false` and `guardian_approval=false`
with Duet's complete flags, including after conflicting feature flags and a
named app's `approvals_reviewer="auto_review"` setting. This establishes the
resolved feature state; an app-specific automatic-review event has not been
exercised. The native event audits below cover the tested workflows, not every
app approval path.

Duet's CLI reviewed this PR with `gpt-5.6-sol` and `gpt-5.6-terra`, both at
high reasoning, through the same Codex sign-in. Their four-turn review found
a missing default seed and incorrect speaking order for resumed review
recipes. After corrections and focused follow-ups, both reviewers converged
with an LGTM rationale. Native records confirmed the selected models and
reasoning, with no delegation calls or matching child-session records.

The Codex/Codex `make loop-test` S2 fixture used Sol and Terra at low reasoning
and converged in four turns; rejection, correction, verification, and the
hidden validator passed. Both native session records contained no delegation
calls. An earlier unrestricted fixture's reviewer record contained one
`spawn_agent` call and two `wait_agent` calls. These observations cover those
runs and do not establish comparative model quality.

OpenCode 1.18.12's native config probes accepted JSONC and depth zero and
refused a managed depth of two before any model invocation. An earlier build,
before the managed-config preflight was added, completed the four-turn S2
fixture without native `task` calls. The new preflight has not completed a
live OpenCode model loop: this environment has no configured OpenAI provider
for OpenCode, and the final review was restricted to OpenAI models. Fresh and
resumed paths, probe privacy, and timeout handling have adapter-test coverage.

Claude Code 2.1.221 accepted the launch flags but could not complete a live
turn because its OAuth session had expired. Gemini and Copilot were not
installed in this validation environment; their controls were checked against
the official references and tested through mocked adapter calls. These gaps
are not live verification of those backends.
