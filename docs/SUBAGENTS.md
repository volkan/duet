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
| Codex | `agents.enabled=false`, `features.multi_agent=false`, and `--disable multi_agent` on fresh and resumed calls. Approval review uses `user`; `--approve-for-me` is rejected because it invokes a reviewer subagent. | [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) |
| Claude Code | Deny `Agent`, its legacy `Task` alias, `TeamCreate`, `SendMessage`, and `Skill`. Disable agent-team and fork-mode environment toggles for peer calls. `Skill` is denied because forked skills can create subagents. | [Subagents](https://code.claude.com/docs/en/sub-agents), [CLI deny rules](https://code.claude.com/docs/en/cli-reference) |
| Copilot | Exclude `task` and `write_agent` from the model's available tools. | [Tool availability](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference#tool-availability-values) |
| OpenCode | Set `subagent_depth: 0` in the child process's inline config; use `--auto`, which preserves explicit permission denials. | [Subagent depth](https://opencode.ai/docs/config/#subagent-depth), [Auto mode](https://opencode.ai/docs/permissions/#auto-mode) |
| Gemini | Set `experimental.enableAgents: false` through a private temporary system-settings overlay for each child process. | [Configuration and precedence](https://geminicli.com/docs/reference/configuration/) |

User tool exclusions are preserved when Duet adds its own. Codex controls
follow extra config arguments and are reapplied when continuing an old run.
`--trust-state` does not opt out. OpenCode remote `--attach` and the old
`--dangerously-skip-permissions` override are rejected because Duet cannot
enforce its local controls through those paths.

OpenCode's existing `OPENCODE_CONFIG_CONTENT` must be a JSON object; Duet
preserves its other fields. Gemini's existing system-settings JSON is copied
with its other settings intact, the original file is untouched, and the
private copy is removed after the call. Invalid config stops the invocation
instead of silently dropping settings. Neither mechanism moves credentials or
changes the user's global configuration.

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

Codex CLI 0.149.0 completed a reviewer/coder/reviewer fixture with Sol and Luna
at low reasoning. The unchanged project tests passed, and both native session
records contained no delegation calls. The same fixture's earlier unrestricted
reviewer record contained one `spawn_agent` call and two `wait_agent` calls.

OpenCode 1.18.12 completed the four-turn review/rejection scenario. Native
exports from both sessions contained ordinary file and shell tools and no
`task` tool calls. Fresh and resumed launch controls are also covered by the
adapter regression tests.

Claude Code 2.1.221 accepted the launch flags but could not complete a live
turn because its OAuth session had expired. Gemini and Copilot were not
installed in this validation environment; their controls were checked against
the official references and tested through mocked adapter calls. These gaps
are not live verification of those backends.
