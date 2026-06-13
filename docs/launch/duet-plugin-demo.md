# asciinema demo script - /duet inside Claude Code

Goal: a final cast under 90 seconds showing a Claude Code user invoking the
`/duet` plugin from inside a normal Claude Code session. The story is not the
raw CLI. The story is: install or confirm the plugin, type `/duet`, let
Claude Code's `/review` produce the kickoff, then watch duet run the
Codex/Claude loop with a run directory and worktree.

The live recording should be done by the user. A real two-agent run needs
authenticated `claude`, `codex`, and `duet` binaries, and the useful timings
depend on the local project and model latency.

## Pre-flight (off camera)

```bash
cd ~/workspace/agent2agent/duet
git status --short
command -v duet
command -v claude
command -v codex
claude --version
codex --version
duet --help >/dev/null
export PS1='$ '
clear
```

- Terminal 100x32, large font, dark theme. Resize before recording because
  asciinema records the initial geometry.
- Use a small branch with a visible, committed change so `/review` has
  something concrete to inspect.
- The plugin can be installed off camera. If you want to show setup, keep only
  the marketplace/install/reload commands and cut the waiting time later.
- Confirm `command -v duet` in the same shell environment that starts
  `claude`; the plugin shells out to `duet`.

## Record

```bash
asciinema rec duet-plugin-demo.cast --idle-time-limit 2 --cols 100 --rows 32
```

`--idle-time-limit 2` collapses long model waits. The raw recording may still
run several minutes; trim after capture.

## The script, beat by beat

Target timeline is for the edited cast.

| beat | on screen | target |
|---|---|---|
| 1 | start Claude Code in the repo | 0:00-0:08 |
| 2 | confirm or install `/duet` plugin | 0:08-0:22 |
| 3 | invoke `/duet` | 0:22-0:30 |
| 4 | `/review` kickoff waits, then duet run appears | 0:30-0:42 |
| 5 | turn recaps show Codex/Claude loop | 0:42-1:05 |
| 6 | convergence, `force>`, run/worktree outro | 1:05-1:25 |

### Beat 1 - start Claude Code

```bash
claude
```

Keep the prompt and repository context visible. If Claude Code prints startup
noise, keep only enough to show this is the normal Claude Code TUI.

### Beat 2 - confirm or install the plugin

If already installed, use:

```text
/plugin list
```

Keep the line that shows `duet@volkan-duet` is installed and enabled.

If showing setup from scratch, type:

```text
/plugin marketplace add volkan/duet
/plugin install duet@volkan-duet
/reload-plugins
```

Cut download/cache pauses. Keep the final success line and the reload result.

### Beat 3 - invoke `/duet`

Type:

```text
/duet
```

If the local install exposes the namespaced command, use:

```text
/duet:duet
```

Do not pass custom flags in the main launch cast. The point is that a Claude
Code user can run the default review recipe with one command.

### Beat 4 - `/review` kickoff and run directory

Plain `/duet` runs `claude -p /review` as the upstream kickoff before duet
allocates the run directory. Keep a brief wait, then keep the first line that
looks like:

```text
[duet] run: /path/to/project/.duet/runs/<run_id>
```

If the command prints `[duet] run dir: ...` instead, keep that. The exact line
depends on recap mode.

### Beat 5 - duet turn recaps

Keep one or two recap blocks that show alternating turns, for example:

```text
Turn 01 | codex-partner (coder) ...
Turn 02 | claude-lead (reviewer) ...
```

Keep the `FILES:` or `STATUS:` lines if they appear. Cut long model waits and
verbose output. The viewer should understand that `/duet` is now running the
same two-agent harness they could have launched from a terminal.

### Beat 6 - convergence and outro

Keep:

- the second back-to-back convergence summary, if the run converges,
- the `force>` prompt, then press Enter after about two seconds,
- the final run directory, transcript path, and worktree review/merge/drop
  commands.

Optional closing frame, only if under 90 seconds:

```bash
duet --status .duet/runs/<run_id>
```

End the recording with `exit` or Ctrl-D.

## Post-processing

1. Trim dead air with `asciinema-edit cut` or by editing the cast file while
   keeping timestamps monotonic.
2. Keep the final cast under 90 seconds. The first place to tighten is model
   wait time between `/duet` and the first run-dir line.
3. Upload with `asciinema upload duet-plugin-demo.cast`.
4. Paste the ID into the commented placeholders in `README.md` and
   `docs/CLAUDE_CODE_PLUGIN.md`, then uncomment those embed snippets.
5. Update `docs/launch/showhn.md` if this cast replaces the existing launch
   demo placeholder.

## Failure modes while recording

- `/duet` is not visible after install: run `/reload-plugins`, then try
  `/duet` or `/duet:duet`.
- `/duet` says `duet` is not on PATH: fix the shell environment and verify
  `command -v duet` before starting Claude Code.
- `/review` produces no actionable kickoff: switch to a branch with a small
  committed change or use `/duet 'git show --stat --patch --no-ext-diff HEAD'`.
- The loop hits the turn limit without converging: press Enter at `force>` and
  keep the run summary, or re-record with a smaller change.
- A turn stalls: use `duet --status <run-dir>` from another terminal to
  diagnose, but re-record rather than showing a stall in the launch cast.
