# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**duet** is a single-file Python harness (`duet.py`, ~1350 lines, stdlib-only, Python 3.9+) that runs two CLI agents — `claude` and `codex` by default — in alternating turns until both agents propose convergence in back-to-back turns with an LGTM rationale plus the sentinel (`<<<LGTM>>>` on its own line), hit `--turns`, time out, or get Ctrl-C'd. Each agent keeps its own conversation memory across turns: Claude via `--resume <session_id>` parsed from its JSON-wrapped output; Codex via `codex exec resume --last` keyed on cwd.

The single-file shape is a hard constraint. PyYAML is the one optional import, gated behind `--config foo.yaml`; the smoke test uses JSON configs to stay stdlib-only. `README.md` has the user-facing pitch; `docs/USAGE.md` is the full flag reference; this file is for someone modifying `duet.py`.

## Common commands

```bash
make install            # symlink duet.py → ~/.local/bin/duet (PREFIX= to override)
make test               # scripts/smoke.sh — ~20 --dry-run cases, all stdlib
make loop-test          # real Claude/Codex E2E loop suite; slow, writes runs/test-loop/
make uninstall

./duet.py --dry-run --task "x" --cwd /tmp        # quickest end-to-end smoke
./duet.py --config examples/hello.yaml           # 2-turn real run, no edits to disk
./duet.py --status runs/<id>/                    # health-probe a finished or in-flight run
python3 scripts/decision_from_transcript.py \
    --transcript runs/<id>/transcript.md --out-dir ../   # rebuild deliverables when agents deadlocked
```

There's no unit-test suite — `scripts/smoke.sh` is the regression net. Each `expect` line is a self-contained `--dry-run` invocation; to run a single case, copy its command out of the script and execute it directly. Smoke compares exit codes, so anything that changes `print_run_status`, the argparse error paths, or `resolve_seed_inputs` will surface there first. The smoke also asserts on side-effects (`<TMPD>/.duet/runs` created, `state.json` contains `duet_pid`, etc.) — read the bottom half of `smoke.sh` before touching foreign-cwd or status logic.

## Architecture you'll need to read multiple files to grasp

### Two state objects

- **`Agent`** (dataclass) — `backend` (`claude`|`codex`), `role` (`planner`|`coder`|`reviewer`|custom), `session_id` (mutated across turns), `cwd_override` (set when this agent runs in a worktree), `reasoning_effort` (per-agent override of cfg-level reasoning), `extra_args`, `role_prompt`.
- **`DuetConfig`** (dataclass) — exactly 2 agents plus orchestration knobs (turns, sentinel, sandbox, permission_mode, worktree settings, add_dirs, reasoning).

`run_duet()` is the loop. `call_agent()` dispatches to `call_claude()` / `call_codex()`. Everything else (`_run` subprocess wrapper, `print_run_status`, argparse / YAML parsing, worktree setup, atomic-write helpers) is helpers around those two layers.

### Three invariants spread across multiple call sites

These break easily if you only update one place.

1. **The reasoning translation layer.** Five duet-abstraction levels (`REASONING_LEVELS = ["minimal","low","medium","high","max"]`) → two backend-specific maps (`CLAUDE_REASONING_MAP`, `CODEX_REASONING_MAP`) → prompt-prefix table (`CLAUDE_REASONING_PROMPT_PREFIX`) → the actual `--effort`/`-c model_reasoning_effort=` emission inside `call_claude`/`call_codex`. Codex `medium` is intentionally a no-op (Codex's default; don't waste a flag). Codex `max` maps to `xhigh` (Codex's actual highest documented value). Claude `minimal` maps to `low` (Claude has no `minimal`). High/max also tack a "think hard"/"ultrathink" prefix onto the system prompt — those are prompt nudges, not authoritative effort controls. `examples/self-review.yaml` ships an inline Python verification one-liner that monkey-patches `_run` and asserts each level produces the right cmd; rerun it after touching any of these constants.

   **Codex fast mode override.** `cfg.codex_fast` (CLI `--codex-fast`, YAML `codex_fast: true`) is a Codex-only short-circuit that pins this run's codex turns to `model_reasoning_effort=minimal` and adds `model_reasoning_summary=concise`, regardless of `cfg.reasoning` or `agent.reasoning_effort`. The override happens inside `call_codex` (`effective = "minimal" if fast else reasoning`) so claude turns are unaffected — `--reasoning high --codex-fast` is the canonical "deep planner, fast coder" combo. Dry-run output tags codex turns with ` fast` so smoke can grep for it; if you change the tag string, update `scripts/smoke.sh`'s `expect_stdout "codex-fast …"` lines.

2. **The `--status` liveness protocol.** Three signals must stay consistent: `state.json["duet_pid"]` (set in `run_duet`, used by `_is_duet_process` to detect "duet is alive between turns / at force> prompt"); the per-turn `runs/<id>/turn-NN-<agent>.pid` file (written by `_run` at startup, removed in `finally` — atomic via temp+rename); and the per-turn `runs/<id>/turn-NN-<agent>.stderr.log` (heartbeat, never deleted). `print_run_status` reads all three and maps to exit codes 0 (done) / 1 (running) / 2 (stuck/crashed) / 3 (error). Smoke covers a synthetic stale `duet_pid=1` state.json and an old-format state.json with no `duet_pid`. Don't drop `duet_pid` from `state` dicts — there are **three** write sites in `run_duet` (the early `force_stop` exit, the per-turn rolling write, and the end-of-run write); all three must include it or `--status` regresses to exit 2.

3. **Codex's flag-set bifurcation.** `codex exec` accepts `--sandbox` and `--cd`; `codex exec resume --last` rejects them with "unexpected argument". `call_codex` splits options into `exec_only_opts` (first turn / no session) vs shared opts. Modern Codex's clap parser also requires options *before* the positional prompt — adding any flag after `full_prompt` is a silent regression that surfaces as a confusing rc=2.

### Convergence: fenced-code-aware, rationale-backed, pair-approved

`convergence_proposed()` does a line-by-line scan tracking whether it's inside a markdown code fence (` ``` ` or `~~~`, length-matched closing). The sentinel only counts on its own line *outside* a fence, and the same reply must include an `LGTM rationale:` / `Rationale:` outside a fence. The loop only stops after two back-to-back agent turns propose convergence, so one agent can reject the other's rationale by omitting the sentinel and asking for another round. An agent quoting "the sentinel is `<<<LGTM>>>`" inline won't false-positive; an agent showing an example code block containing the sentinel won't either. Don't replace this with a regex over the whole text — README's "Limits" section explicitly calls this out as the deliberate trade-off.

### Subprocess plumbing (`_run`)

`subprocess.Popen` with `start_new_session=True` so we own a process group (clean SIGTERM/SIGKILL via `os.killpg`). Two reader threads drain stdout/stderr line-by-line; stderr is mirrored to the user's terminal (with `LIVE_PREFIX = "  │ "`) **and** tee'd to a per-turn `*.stderr.log` file. `pid_file_path` is written atomically (temp+rename) on Popen and removed in `finally`. `_terminate_active_processes(SIGKILL)` walks `_ACTIVE_PROCS` (a thread-locked set) on second-Ctrl-C.

`LIVE_STREAM` and `LIVE_PREFIX` are module-level mutable globals. `resolve_task_from_cmd` swaps `LIVE_PREFIX` to `"  $ "` while running the user's task command and restores it via try/finally — this is the only legitimate write to that global; don't add others.

### `str.replace("{SENTINEL}", ...)`, never `.format(...)`

Role prompts (`ROLE_PROMPTS` and any user-supplied `role_prompt`) frequently contain literal `{...}` (JSON schema examples, jq patterns). `Agent.system_prompt` does `str.replace`, not `.format`, on purpose. If you change this, smoke won't catch it — but `examples/repo-compare.yaml`'s role_prompt would crash with "unexpected '{' in field name" on the first turn.

### Worktree mode

`--worktree` creates `<runs_dir>/<run_id>/wt/` on a fresh `duet/<run_id>` branch and points the partner agent's `cwd_override` at it. After every worktree-agent turn, `git_diff_summary` (`git status --short` + `--stat` + truncated `diff HEAD`, capped at 8 KB, plus fenced previews of untracked text files) is appended to that turn's reply so the lead sees what was actually changed, not what the partner claims. The worktree is intentionally **not deleted** at exit — duet prints merge/review/drop commands. Default placement under `runs/<id>/wt/` is durable across reboots (escapes `/tmp` cleaners); `--worktree-root /tmp` opts back into temp-dir behavior. `--worktree-path` is the resume case (point at an existing worktree); `--worktree` and `--worktree-path` are mutually exclusive and validated twice (argparse + post-config).

### Foreign-cwd default

When `--cwd` is outside the invocation directory and `--runs-dir` is omitted, `choose_runs_dir` puts artifacts at `<cwd>/.duet/runs/<id>/` instead of polluting the invocation dir with a `runs/` folder. `<runs_dir>/.gitignore` is auto-created with `*` on first use so transcripts/state/worktrees never show up in the host repo's `git status`. There's a fallback inside `run_duet` for when the chosen `runs_dir` is unwritable: it slugs `cwd` and lands under `~/.duet/runs/<slug>/`.

### Home-index symlinks (`~/.duet/runs/<cwd-slug>/<run_id>`)

Without indexing, `duet --list` from cwd=A can't see runs created with `--cwd B` because they live under `B/.duet/runs/`. To fix that, `run_duet` calls `_register_run_in_home_index(run_dir, cfg.cwd)` right after the run dir is created — it drops a symlink at `~/.duet/runs/<cwd-slug>/<run_id>` pointing at the real run dir. The slug uses the same `re.sub(r"[^a-zA-Z0-9._-]+", "-", str(cwd)).strip("-")[:80]` scheme as the unwritable-cwd fallback, so a fallback dir and a registered symlink for the same cwd land under one slug. Three invariants pin this together:

1. **Skip when `run_dir` is already inside `~/.duet/runs/`.** That's the unwritable-cwd fallback case; registering would create a circular self-link.
2. **Best-effort writes.** The helper catches `(OSError, NotImplementedError)`, prints a one-line stderr notice, and returns. A read-only HOME or a filesystem without symlink support never fails the run.
3. **`print_runs_list` and `_resolve_run_dir` dedupe by `child.resolve()`.** Without dedup, every run would show twice (cwd-relative + home-index symlink), and bare-id `--status` would always print the "found under multiple roots" warning. The dedup also means `print_runs_list`'s `dir` column prefers cwd-relative display because `_default_list_paths()` iterates cwd-relative roots first.

`print_runs_list` also self-heals: every run dir it discovers gets backfill-registered (idempotent — symlink-already-exists is a fast no-op), so runs created before this code shipped become visible after one explicit `duet --list <their-runs-dir>`. The smoke covers this with `HOME=$TMPD` exported up top to keep the symlink farm out of the developer's real home dir.

## Codex-specific quirks (bite often)

- **Sandbox blocks network by default.** `--sandbox workspace-write` blocks outbound network — `gh`, `curl`, `npm install`, anything DNS — until you opt in. The fix lives in YAML as `extra_args: ["-c", "sandbox_workspace_write.network_access=true"]`. Symptoms: every `gh` call fails with "error connecting to api.github.com". `examples/repo-compare.yaml` has the canonical opt-in.
- **`--last` resume keys on cwd.** Codex doesn't expose a session id duet can pin; resume = "most recent codex session in this cwd". Don't run parallel codex sessions in the same cwd while a duet is live. `--worktree` isolates duet's Codex cwd from the host repo, but a parallel codex *inside* that same worktree still races.
- **`extra_args` flags must be option-form only** and go before the positional prompt (see invariant 3 above).

## Claude-specific quirks

- **Output is JSON-wrapped.** `claude -p ... --output-format json` returns `{"result":"...","session_id":"..."}`. We always re-read `session_id` and write it back to `agent.session_id` so the next turn picks up the rotated id (Claude rotates session ids on every reply). A malformed JSON output raises `RuntimeError` with a 500-char snippet — useful when claude crashes mid-stream.
- **`--add-dir` is required for any path outside cwd.** Without it Claude silently refuses paths outside `cwd` with a generic permission error. YAML key `add_dirs:` (list); CLI flag `--add-dir` is repeatable. `examples/repo-compare.yaml` writes `../DECISION_v2.{md,html}` and pre-supplies `add_dirs: [..]` for that reason.
- **`--effort` is the authoritative reasoning control;** the high/max prompt prefixes are belt-and-braces.

## When agents deadlock or fail to write deliverables

`scripts/decision_from_transcript.py` is a fallback that parses the codex turn-1 fenced JSON blocks out of `transcript.md` and rebuilds `DECISION_v2.{md,html}` directly. It exists because of one specific run (`runs/20260506-235839/`) where the agents produced all the per-repo JSON rubrics but the planner failed to write the deliverables (sandbox + add-dirs interaction). If you find yourself in a similar "transcript has the data but the deliverable file is missing" situation, that script is the template for a new salvager — copy and adapt rather than re-running the duet.

## Keep docs in sync with each change

Every change to `duet.py` (or anything else under this repo) must update the related documents in the **same commit**. Drift between code and docs is the dominant failure mode here — there's no CI, no schema validation, just these files. Use this table as a checklist before you commit:

| change in `duet.py` | also update |
|---|---|
| new / renamed / changed CLI flag | `docs/USAGE.md` flag table; `README.md` "Best recipes" if the flag shows up in a canonical recipe; `duet.example.yaml` if there's a corresponding YAML key; `scripts/smoke.sh` if the flag has dry-run-able exit-code semantics |
| new / changed YAML config key | `duet.example.yaml` (commented example with default); `docs/USAGE.md` only if it warrants user-facing prose beyond the flag table |
| new / changed exit code or `--status` output | `docs/USAGE.md` exit-code table; add an `expect` line in `scripts/smoke.sh` |
| new / changed reasoning level or backend mapping | rerun the verification one-liner in `examples/self-review.yaml` and update its **expected output block** if values shift; `docs/USAGE.md` `--reasoning` row |
| new / changed Codex fast-mode behavior (`cfg.codex_fast`) | `docs/USAGE.md` `--codex-fast` flag-table row + "Codex fast mode" subsection; `duet.example.yaml` `codex_fast:` line; `README.md` "deep planner, fast coder" recipe; `scripts/smoke.sh` `codex-fast` expect lines |
| new / changed output-dir layout (per-turn files, run dirs, worktree placement) | `docs/USAGE.md` "Output layout" block; `scripts/smoke.sh` side-effect assertions (the `[[ -d ... ]]` / `[[ -f ... ]]` / `grep -q` checks at the bottom); `README.md` if user-visible |
| new / changed sandbox / permission-mode / network behavior | `docs/USAGE.md` "Codex sandbox and network access" section; `examples/repo-compare.yaml` if its `extra_args` pattern is what users copy |
| new role or new ROLE_PROMPT entry | `ROLE_PROMPTS` in `duet.py`; the "Roles ship with" line in `docs/USAGE.md` |
| stop-condition / SIGINT / force-prompt change | `docs/USAGE.md` "Stop conditions and force prompt" table; `README.md` "What duet does" numbered list if user-visible |
| change to the `/duet` slash-command recipe | the embedded skill body in `docs/USAGE.md` (the section starting "`/duet` Claude Code skill (optional)") |
| breaking semantics or limit change | `README.md` "Limits / future" |

If a change is worth doing, it's worth a smoke case. `scripts/smoke.sh` is the executable part of the docs — drift between it and `duet.py` is the failure mode hardest to spot. Add the `expect` line in the same commit as the code, not later.

When the change is purely internal (helper refactor, no user-visible surface), update the relevant section of **this file** (`CLAUDE.md`) if it falsifies anything in the "Architecture you'll need to read multiple files to grasp" or "Hard constraints" sections.

## Hard constraints when modifying `duet.py`

- **Stdlib-only.** PyYAML is the one allowed exception, gated behind `--config foo.yaml`.
- **Don't `.format()` role-prompt templates** — use `str.replace("{SENTINEL}", ...)` (see above).
- **Don't drop `start_new_session=True`** from `subprocess.Popen` — Ctrl-C handling depends on the child being its own process group leader.
- **Atomic writes only** for `state.json`, `transcript.md`, and `*.pid` files — go through `write_text_atomic` / `append_text_atomic`. A non-atomic write can corrupt the file mid-crash and break `--status` parsing.
- **Don't add a second `<runs_dir>/.gitignore` writer.** `run_duet` already idempotently writes one with `*`.
- **Don't write to `~/.duet/runs/` from anywhere but `_register_run_in_home_index` and the unwritable-cwd fallback.** Both share the same slug scheme on purpose; a third writer with a different scheme would silently fragment the index. If you find yourself wanting one, extend the helper instead.
- **Update related docs in the same commit** (see the table above). A code-only commit is a regression of the docs.
