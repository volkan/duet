# Show HN draft

## Title options

1. `Show HN: Duet – make Claude Code and Codex pair-program until both sign off`
2. `Show HN: I got tired of copy-pasting between Claude Code and Codex, so I wrote a relay`
3. `Show HN: Duet – two CLI coding agents argue until they agree (one Python file)`

Option 1 is the safest. Option 2 leads with the pain and reads most like a
person. Option 3 is the most clickable but slightly undersells the
convergence rules.

## Post draft

For a few months my workflow was: ask Codex to plan, paste the plan into
Claude Code, paste Claude's review back into Codex, lose track of which
terminal had which context, repeat. Both CLIs are good at keeping their own
session memory; the problem was me being the relay between them.

So I wrote duet: a single Python file (stdlib only) that runs two CLI coding
agents in alternating turns. Claude Code and Codex by default; same-backend
pairs (Codex planner + Codex coder, Claude coder + Claude reviewer) also work.
Each agent keeps its own conversation memory — Claude via `--resume
<session_id>`, Codex via `codex exec resume <uuid>` parsed off its stderr —
and duet just carries the latest reply across.

Demo (90s): [PLACEHOLDER — asciinema link: duet reviews its own packaging
branch, Codex as reviewer, Claude as coder, `make ci` as the convergence gate]

Try it without installing anything:

    uvx --from duet-cli duet --task "Fix the failing test" --cwd ~/code/myrepo

(`pip install duet-cli` gives you the `duet` command; the repo is also just
one file you can curl. You need the `claude` and `codex` CLIs on PATH and
logged in — duet shells out to them, it has no API keys of its own.)

How it works:

- The loop stops only when *both* agents, in back-to-back turns, produce an
  `LGTM rationale:` plus a sentinel (`<<<LGTM>>>`) on its own line. A bare
  sentinel is ignored, sentinels inside markdown code fences are ignored, and
  either agent can veto by withholding the sentinel and asking for another
  round.
- `--verify-cmd 'make test'` adds a mechanical gate: a convergence proposal
  only counts if your command exits 0. Non-zero output gets fed back to the
  next agent turn as a failure block. Two LLMs politely agreeing is not
  evidence; a green test suite at least is some.
- `--worktree` puts the editing agent in a fresh git worktree on its own
  branch. After each of its turns, duet appends a diff summary and a handoff
  block so the reviewing agent verifies the edited tree, not the host
  checkout. Your checkout stays clean until you merge.
- Everything lands on disk: full `transcript.md`, `state.json`, per-turn
  stderr logs. `duet --status <run>` health-probes a run from another
  terminal (exit codes distinguish done/running/stuck), and `duet --continue
  <run>` starts a fresh run from the saved session ids. When a loop ends,
  a `force>` prompt lets you type feedback and push one more round.

Why this instead of the first-party versions? Both vendors are building
pairing features — Codex has a Claude Code plugin, Claude Code has Agent
Teams — but each keeps its own product as the host. duet is symmetric:
neither agent owns the loop, the harness doesn't care which vendor is on
which side, and same-backend pairs are first-class. I wanted the referee to
be a dumb, inspectable Python file rather than either vendor's product
surface.

Limits, honestly:

- Convergence detection is text heuristics over agent output. Two models can
  still agree on the wrong thing — `--verify-cmd` mitigates, but reading the
  diff is still your job. duet automates the relay, not the judgment.
- It's exactly two agents, alternating. No swarms, no parallelism. That's
  deliberate (turn-taking is what makes the transcript readable), but if you
  want ten agents on a kanban board this is the wrong tool.
- It shells out to `claude` and `codex`, so their flag changes can break it.
  Codex's resume path already needs a fallback (`codex exec resume --last`)
  for builds that don't print a session id, and that fallback is cwd-keyed —
  don't run a parallel Codex session in the same directory while it's active.
- You're paying for two agents. A 6-turn run at max reasoning effort is not
  cheap, and the default 2 turns exists for a reason.
- ~3,300 lines in one file is both the feature and the smell. Developed on
  macOS, CI on Linux (3.9/3.11/3.13); Windows untested.

Repo: https://github.com/volkan/duet

I'd love feedback on the convergence protocol (rationale + sentinel + pair
agreement + optional verify command) — and on which pairings people actually
want. The most surprising result so far has been same-backend runs: a Claude
reviewer is noticeably harsher on a Claude coder than on a Codex one.
