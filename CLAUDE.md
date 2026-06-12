# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**duet** is a single-file Python harness (`duet.py`, ~3300 lines, stdlib-only, Python 3.9+) that runs two CLI agents — `claude` and `codex` by default, with same-backend pairings supported — in alternating turns until both agents propose convergence in back-to-back turns with an LGTM rationale plus the sentinel (`<<<LGTM>>>` on its own line), hit `--turns`, time out, or get Ctrl-C'd. Each agent keeps its own conversation memory across turns: Claude via `--resume <session_id>` parsed from its JSON-wrapped output; Codex via `codex exec resume <uuid>` (UUID parsed out of Codex's stderr by `_parse_codex_session_id`) with a `codex exec resume --last` cwd-keyed fallback for builds that don't print one or for legacy continued runs.

The single-file shape is a hard constraint. PyYAML is the one optional import, gated behind `--config foo.yaml`; the smoke test uses JSON configs to stay stdlib-only. `README.md` has the user-facing pitch; `docs/USAGE.md` is the full flag reference; this file is for someone modifying `duet.py`.

Two packaging shims sit beside the file without changing its shape: `pyproject.toml` publishes it to PyPI as `duet-cli` (console script `duet = "duet:main"`, flat module `duet` via `py-modules`, `[yaml]` extra for PyYAML, zero runtime deps — bare `duet` on PyPI is Google's async library), and `.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` (marketplace name `volkan-duet`, required by `/plugin marketplace add volkan/duet`) + `commands/duet.md` make the repo installable as a Claude Code plugin providing `/duet` (the command shells out to the installed `duet` CLI; it does not embed duet.py). Keep `plugin.json`'s `version` in lockstep with `pyproject.toml`'s.

## Common commands

```bash
make install            # symlink duet.py → ~/.local/bin/duet (PREFIX= to override)
make ci                 # everything the CI merge gate runs (unit + reasoning + smoke + complexity)
make test               # unit tests (tests/test_duet.py) + scripts/smoke.sh
make unit-test          # only the stdlib unittest suite under tests/
make smoke-test         # only scripts/smoke.sh dry-run cases
make complexity         # cyclomatic-complexity/length gate (scripts/check_complexity.py)
make reasoning-check    # reasoning-effort translation check (scripts/check_reasoning_levels.py)
make loop-test          # real Claude/Codex E2E loop suite; slow, writes runs/test-loop/
make build              # sdist+wheel into dist/ (needs the 'build' package via uv/pipx)
make uninstall

./duet.py --dry-run --task "x" --cwd /tmp        # quickest end-to-end smoke
./duet.py --config examples/hello.yaml           # 2-turn real run, no edits to disk
./duet.py --status runs/<id>/                    # health-probe a finished or in-flight run
./duet.py --continue runs/<id>/ --task "next"    # fresh run from saved state/session ids
```

Four regression nets, in order of granularity. `.github/workflows/ci.yml` runs all four on every PR (unit + reasoning + smoke on Python 3.9/3.11/3.13, complexity once) — `make ci` runs the same set locally. They are advisory until marked **required** in branch protection; see `.github/BRANCH_PROTECTION.md` (admins can still force-merge).

1. `tests/test_duet.py` (run via `make unit-test` or `python3 -m unittest discover -s tests`) — pure-function unit tests, stdlib `unittest` only. Covers `_convergence_markers` / `convergence_proposed`, `_parse_codex_session_id`, `parse_recap_headers`, `extract_files_heuristic`, the reasoning maps + `validate_reasoning` / `effective_reasoning`, `parse_partner`, `_markdown_fence`, `_humanize_age`, `derive_status_heuristic`, `_next_speaker_idx_from_state`, `_format_byte_size`, `normalize_verify_cmd`, and `_resolve_opt_path`. No subprocesses, no filesystem writes, no agent CLIs — runs in well under a second. Add a test in the same commit as any change to those helpers.
2. `scripts/smoke.sh` (run via `make smoke-test` or as part of `make test`) — the integration net. Each `expect` line is a self-contained `--dry-run` invocation; to run a single case, copy its command out of the script and execute it directly. Smoke compares exit codes, so anything that changes `print_run_status`, the argparse error paths, or `resolve_seed_inputs` will surface there first. The smoke also asserts on side-effects (`<TMPD>/.duet/runs` created, `state.json` contains `duet_pid`, etc.) — read the bottom half of `smoke.sh` before touching foreign-cwd or status logic.
3. `scripts/check_reasoning_levels.py` (run via `make reasoning-check`) — monkey-patches `_run` and asserts each reasoning level emits the right backend cmd; the executable half of invariant 1 below.
4. `scripts/check_complexity.py` (run via `make complexity`) — stdlib AST cyclomatic-complexity/length gate (budget: CC ≤ 25, length ≤ 160). duet is single-file, so the only defense against sprawl is keeping individual functions small; this fails CI if any function exceeds budget. After a refactor, re-run it — the orchestration entrypoints (`run_duet`, `main`) sit a few points under budget, so a careless inline addition can trip it.

## Architecture you'll need to read multiple files to grasp

### Two state objects

- **`Agent`** (dataclass) — `backend` (`claude`|`codex`), `role` (`planner`|`coder`|`reviewer`|custom), `session_id` (mutated across turns), `cwd_override` (set when this agent runs in a worktree), `reasoning_effort` (per-agent override of cfg-level reasoning), `extra_args`, `role_prompt`.
- **`DuetConfig`** (dataclass) — exactly 2 agents plus orchestration knobs (turns, sentinel, sandbox, permission_mode, worktree settings, add_dirs, reasoning).

`run_duet()` is the loop. It's deliberately thin — it delegates to phase helpers: `_allocate_run_dir` (run-dir + gitignore + home-index), `_setup_run_worktree`, `_derive_seed_or_failure` (opening message), `_dry_run_recap_state` (the `--dry-run --recap` early exit), `_execute_turn` (one agent turn: invoke → verify → recap → persist), and `_build_run_state` (every state.json payload). `ask_force` (the post-loop force prompt) delegates its turn body to `_run_forced_turn`, the `-forced` twin of `_execute_turn`. `main()` is a dispatcher over `build_continue_config` / `_build_cfg_from_yaml` / `_build_cfg_from_cli`, with the argparse table in `_build_arg_parser` and the codex-fast scope check in `_warn_codex_fast_scope`. `call_agent()` dispatches to `call_claude()` / `call_codex()`. Everything else (`_run` subprocess wrapper, `print_run_status`, atomic-write helpers) is helpers around those layers. Keep this decomposition when adding to the loop: the complexity gate (net 4) fails if you inline a new branch-heavy block back into `run_duet`/`main` instead of adding a named helper.

### Three invariants spread across multiple call sites

These break easily if you only update one place.

1. **The reasoning translation layer.** Six duet-abstraction levels (`REASONING_LEVELS = ["minimal","low","medium","high","xhigh","max"]`) → two backend-specific maps (`CLAUDE_REASONING_MAP`, `CODEX_REASONING_MAP`) → prompt-prefix table (`CLAUDE_REASONING_PROMPT_PREFIX`) → the actual `--effort`/`-c model_reasoning_effort=` emission inside `call_claude`/`call_codex`. Codex `medium` is intentionally a no-op (Codex's default; don't waste a flag). Codex `xhigh` passes through, and Codex `max` maps to `xhigh` because Codex does not document a separate `max` value. Claude `minimal` maps to `low` (Claude has no `minimal`); Claude `xhigh` and `max` pass through. High/xhigh/max also tack a "think hard"/"think very hard"/"ultrathink" prefix onto the system prompt — those are prompt nudges, not authoritative effort controls. `scripts/check_reasoning_levels.py` monkey-patches `_run` and asserts each level produces the right cmd; rerun it after touching any of these constants.

   **Codex fast mode override.** `cfg.codex_fast` (CLI `--codex-fast`, YAML `codex_fast: true`) is a Codex-only short-circuit that pins this run's codex turns to `model_reasoning_effort=low` and adds `model_reasoning_summary=concise`, regardless of `cfg.reasoning` or `agent.reasoning_effort`. **Fast mode is role-scoped: it applies only to codex agents whose role is `coder`.** `call_agent` recomputes `fast = cfg.codex_fast and agent.role == "coder"` per turn, so a `--lead codex:planner --codex-fast` run no longer silently downgrades the planner. Config validation in `main()` warns on stderr and clears `cfg.codex_fast` when no codex:coder agent exists; it prints a softer note when fast is partial (some codex agents are coders, some aren't). The override happens inside `call_codex` (`effective = "low" if fast else reasoning`) so claude turns are unaffected — `--reasoning high --codex-fast` with the default `claude:planner + codex:coder` is the canonical "deep planner, fast coder" combo. If a user really wants fast on a non-coder codex role, they can set per-agent `reasoning_effort: low` in YAML — that's the documented escape hatch. Do not use Codex `minimal` here unless the default Codex tool set changes; real tool-enabled runs currently reject `minimal` effort. Dry-run output tags codex turns with ` fast` so smoke can grep for it; if you change the tag string, update `scripts/smoke.sh`'s `expect_stdout "codex-fast …"` lines.

2. **The `--status` liveness protocol.** Three signals must stay consistent: `state.json["duet_pid"]` (set in `run_duet`, used by `_is_duet_process` to detect "duet is alive between turns / at force> prompt"); the per-turn `runs/<id>/turn-NN-<agent>.pid` file (written by `_run` at startup, removed in `finally` — atomic via temp+rename); and the per-turn `runs/<id>/turn-NN-<agent>.stderr.log` (heartbeat, never deleted). `print_run_status` reads all three and maps to exit codes 0 (done) / 1 (running) / 2 (stuck/crashed) / 3 (error). Smoke covers a synthetic stale `duet_pid=1` state.json and an old-format state.json with no `duet_pid`. Every state.json payload is built by the single `_build_run_state` helper — all six write sites (in `run_duet`: dry-run, force_stop, seed-failure, per-turn rolling, end-of-run; plus the per-forced-turn rolling write in `ask_force`) call it — so `duet_pid` and the worktree/continue keys land on every write from one place. `ask_force` persists each forced turn through this helper so a crash at the `force>` prompt keeps `--status`/`--continue` accurate; `run_duet` overwrites with the final `finished_reason` once `ask_force` returns. Don't reintroduce an inline `state = {...}` dict; extend `_build_run_state` instead, or `--status`/`--continue` regress when one site drifts.

3. **Codex's flag-set bifurcation and same-cwd isolation.** `codex exec` accepts `--sandbox` and `--cd`; both `codex exec resume <uuid>` and `codex exec resume --last` reject them with "unexpected argument". `call_codex` splits options into `exec_only_opts` (first turn / no session) vs shared opts; resume picks `resume <uuid>` when `agent.session_id` matches `_CODEX_UUID_RE`, else falls back to `resume --last`. After every Codex turn, `_parse_codex_session_id(err)` scans stderr for `session id: <uuid>` (anchored, case-insensitive, last match wins) and `call_codex` returns `parsed_sid or agent.session_id or "codex-current"` — that means a fresh first turn upgrades the agent from `None` to a real UUID, a turn that fails to print a UUID falls back to the `"codex-current"` sentinel that means "use --last," and a later turn that finally prints one upgrades the sentinel in place. For `codex`/`codex` peers sharing one effective cwd, `run_duet` must fail immediately if either peer's first turn does not produce a UUID, or before any peer with a non-UUID session marker would resume; otherwise `--last` can hijack the other peer's most recent session. Different effective cwds (for example via `--worktree`) are allowed to use the fallback. Modern Codex's clap parser also requires options *before* the positional prompt — adding any flag after `full_prompt` is a silent regression that surfaces as a confusing rc=2.

### Convergence: fenced-code-aware, rationale-backed, pair-approved

`convergence_proposed()` does a line-by-line scan tracking whether it's inside a markdown code fence (` ``` ` or `~~~`, length-matched closing). The sentinel only counts on its own line *outside* a fence, and the same reply must include an `LGTM rationale:` / `Rationale:` outside a fence. The loop only stops after two back-to-back agent turns propose convergence, so one agent can reject the other's rationale by omitting the sentinel and asking for another round. An agent quoting "the sentinel is `<<<LGTM>>>`" inline won't false-positive; an agent showing an example code block containing the sentinel won't either. Don't replace this with a regex over the whole text — README's "Limits" section explicitly calls this out as the deliberate trade-off.

### Subprocess plumbing (`_run`)

`subprocess.Popen` with `start_new_session=True` so we own a process group (clean SIGTERM/SIGKILL via `os.killpg`). Two reader threads drain stdout/stderr line-by-line; stderr is mirrored to the user's terminal (with `LIVE_PREFIX = "  │ "`) **and** tee'd to a per-turn `*.stderr.log` file. `pid_file_path` is written atomically (temp+rename) on Popen and removed in `finally`. `_terminate_active_processes(SIGKILL)` walks `_ACTIVE_PROCS` (a thread-locked set) on second-Ctrl-C.

`LIVE_STREAM` and `LIVE_PREFIX` are module-level mutable globals. `resolve_task_from_cmd` swaps `LIVE_PREFIX` to `"  $ "` while running the user's task command and restores it via try/finally — this is the only legitimate write to that global; don't add others.

### `str.replace("{SENTINEL}", ...)`, never `.format(...)`

Role prompts (`ROLE_PROMPTS` and any user-supplied `role_prompt`) frequently contain literal `{...}` (JSON schema examples, jq patterns). `Agent.system_prompt` does `str.replace`, not `.format`, on purpose. If you change this, smoke won't catch it — but any `role_prompt` containing literal `{json: schema}` examples would crash with "unexpected '{' in field name" on the first turn.

### Worktree mode

`--worktree` creates `<runs_dir>/<run_id>/wt/` on a fresh `duet/<run_id>` branch and points the selected worktree agent's `cwd_override` at it (partner by default, lead with `--worktree-for lead`). After every worktree-agent turn, duet appends a handoff block plus `git_diff_summary` (`git status --short` + `--stat` + truncated `diff HEAD`, capped at 8 KB, plus fenced previews of untracked text files) to that turn's reply. The handoff block names the exact worktree path/branch, warns that the receiving agent's cwd may be a clean checkout, and includes `git -C <wt>` review commands so verification targets the edited tree, not the host checkout. The worktree is intentionally **not deleted** at exit — duet prints merge/review/drop commands. Default placement under `runs/<id>/wt/` is durable across reboots (escapes `/tmp` cleaners); `--worktree-root /tmp` opts back into temp-dir behavior. `--worktree-path` is the resume case (point at an existing worktree); `--continue <run>` uses the prior state's `worktree` path or falls back to `<run>/wt/` for older crashed runs. `--worktree` and `--worktree-path` are mutually exclusive and validated twice (argparse + post-config).

### Continue mode

`--continue RUN_DIR_OR_ID` is a fresh-run convenience wrapper around saved `state.json`: `_resolve_run_dir` finds the old run, `build_continue_config` restores both `Agent` objects with their saved `session_id`s, chooses the next speaker from the last completed `history` entry, reuses the saved worktree path (or legacy `<run>/wt/`), and builds a continuation kickoff. It does **not** append to the old transcript. `DuetConfig.start_speaker_idx` is internal plumbing for this; normal runs keep the default partner-first value of `1`. Rolling `state.json` writes must keep `transcript_path`, `worktree`, `worktree_branch`, `worktree_for`, `continue_from`, and `duet_pid` because `--continue` and `--status` both depend on state surviving mid-turn crashes.

### Resume flag normalization

`apply_resume_overrides()` is the single place that applies `--resume-claude` / `--resume-codex` to configured agents. Do not add one-off resume assignment in `main()`. Claude resume is the historical "lead supplies the seed" path, so a resumed Claude agent is normalized into slot 0; `derive_seed()` then extracts its latest message before the partner speaks. Codex resume is the "resume Codex with its prior plan in context" path, so a resumed Codex agent is normalized into slot 1 and speaks first from that session. If the matching backend is absent, the helper creates the conventional slot (`claude-lead` or `codex-partner`). If the user put the backend in the wrong slot, moved agents get slot-default roles (`planner` for lead, `coder` for partner), which prevents `--resume-codex --lead codex:planner --partner claude:coder` from silently dropping the UUID or running the wrong side first.

### Foreign-cwd default

When `--cwd` is outside the invocation directory and `--runs-dir` is omitted, `choose_runs_dir` puts artifacts at `<cwd>/.duet/runs/<id>/` instead of polluting the invocation dir with a `runs/` folder. `<runs_dir>/.gitignore` is auto-created with `*` on first use so transcripts/state/worktrees never show up in the host repo's `git status`. There's a fallback inside `run_duet` for when the chosen `runs_dir` is unwritable: it slugs `cwd` and lands under `~/.duet/runs/<slug>/`.

### Home-index symlinks (`~/.duet/runs/<cwd-slug>/<run_id>`)

Without indexing, `duet --list` from cwd=A can't see runs created with `--cwd B` because they live under `B/.duet/runs/`. To fix that, `run_duet` calls `_register_run_in_home_index(run_dir, cfg.cwd)` right after the run dir is created — it drops a symlink at `~/.duet/runs/<cwd-slug>/<run_id>` pointing at the real run dir. The slug uses the same `re.sub(r"[^a-zA-Z0-9._-]+", "-", str(cwd)).strip("-")[:80]` scheme as the unwritable-cwd fallback, so a fallback dir and a registered symlink for the same cwd land under one slug. Three invariants pin this together:

1. **Skip when `run_dir` is already inside `~/.duet/runs/`.** That's the unwritable-cwd fallback case; registering would create a circular self-link.
2. **Best-effort writes.** The helper catches `(OSError, NotImplementedError)`, prints a one-line stderr notice, and returns. A read-only HOME or a filesystem without symlink support never fails the run.
3. **`print_runs_list` and `_resolve_run_dir` dedupe by `child.resolve()`.** Without dedup, every run would show twice (cwd-relative + home-index symlink), and bare-id `--status` would always print the "found under multiple roots" warning. The dedup also means `print_runs_list`'s `dir` column prefers cwd-relative display because `_default_list_paths()` iterates cwd-relative roots first.

`print_runs_list` also self-heals: every run dir it discovers gets backfill-registered (idempotent — symlink-already-exists is a fast no-op), so runs created before this code shipped become visible after one explicit `duet --list <their-runs-dir>`. The smoke covers this with `HOME=$TMPD` exported up top to keep the symlink farm out of the developer's real home dir.

## Codex-specific quirks (bite often)

- **Sandbox blocks network by default.** `--sandbox workspace-write` blocks outbound network — `gh`, `curl`, `npm install`, anything DNS — until you opt in. The fix lives in YAML as `extra_args: ["-c", "sandbox_workspace_write.network_access=true"]`. Symptoms: every `gh` call fails with "error connecting to api.github.com". `duet.example.yaml`'s commented `codex-partner` `extra_args` is the canonical opt-in to copy from.
- **Resume prefers a parsed UUID; `--last` is the fallback.** Modern Codex builds emit a `session id: <uuid>` line on stderr; duet captures it (`_parse_codex_session_id`) and pins the next turn with `codex exec resume <uuid>`, which is robust to other Codex sessions sharing the cwd. When the UUID isn't visible — old Codex builds, parser regressions, or `--continue`-ing a pre-UUID-parsing run that wrote no `session_id` to `state.json` — we fall back to `codex exec resume --last`, which is keyed on cwd; in that mode, don't run parallel codex sessions in the same cwd while a duet is live. Same-cwd `codex`/`codex` peering is stricter: a missing UUID on either peer's first turn is fatal, and a non-UUID resume marker is fatal before the call. `--worktree` isolates one duet Codex peer's cwd from the other, but a parallel codex *inside* that same worktree still races. `build_continue_config` plants the legacy `"codex-current"` sentinel for any old-state Codex agent that has a history entry but no saved id, so old runs route through `--last` instead of starting a fresh `codex exec` and orphaning the prior session. The sentinel string `"codex-current"` is a deliberately UUID-shaped non-UUID — `_CODEX_UUID_RE` rejects it, which is how `call_codex` decides between UUID-resume and `--last`-resume.
- **`extra_args` flags must be option-form only** and go before the positional prompt (see invariant 3 above).

## Claude-specific quirks

- **Output is JSON-wrapped.** `claude -p ... --output-format json` returns `{"result":"...","session_id":"..."}`. We always re-read `session_id` and write it back to `agent.session_id` so the next turn picks up the rotated id (Claude rotates session ids on every reply). A malformed JSON output raises `RuntimeError` with a 500-char snippet — useful when claude crashes mid-stream.
- **`--add-dir` is required for any path outside cwd.** Without it Claude silently refuses paths outside `cwd` with a generic permission error. YAML key `add_dirs:` (list); CLI flag `--add-dir` is repeatable. Pre-supply `add_dirs: [..]` (or repeat `--add-dir`) whenever the task writes paths above `cwd`.
- **`--effort` is the authoritative reasoning control;** the high/xhigh/max prompt prefixes are belt-and-braces.

## Keep docs in sync with each change

All commits in this repository must use Conventional Commits (`type: summary`, for example `docs: update commit guidance`).

Every change to `duet.py` (or anything else under this repo) must update the related documents in the **same commit**. Drift between code and docs is the dominant failure mode here — there's no CI, no schema validation, just these files. Use this table as a checklist before you commit:

| change in `duet.py` | also update |
|---|---|
| new / renamed / changed CLI flag | `docs/USAGE.md` flag table; `README.md` "Best recipes" if the flag shows up in a canonical recipe; `duet.example.yaml` if there's a corresponding YAML key; `scripts/smoke.sh` if the flag has dry-run-able exit-code semantics |
| new / changed YAML config key | `duet.example.yaml` (commented example with default); `docs/USAGE.md` only if it warrants user-facing prose beyond the flag table |
| new / changed exit code or `--status` output | `docs/USAGE.md` exit-code table; add an `expect` line in `scripts/smoke.sh` |
| new / changed reasoning level or backend mapping | rerun `scripts/check_reasoning_levels.py` and update its `EXPECTED` dict if values shift; extend `tests/test_duet.py::TestReasoningHelpers` for the data side; `docs/USAGE.md` `--reasoning` row |
| new / changed pure helper covered by `tests/test_duet.py` (convergence detector, codex session-id parser, recap header parser, file-path heuristic, partner spec parser, markdown fence sizer, age formatter, status heuristic) | add a case to `tests/test_duet.py` describing the new behavior in the same commit |
| new / changed Codex fast-mode behavior (`cfg.codex_fast`) | `docs/USAGE.md` `--codex-fast` flag-table row + "Codex fast mode" subsection; `duet.example.yaml` `codex_fast:` line; `README.md` "deep planner, fast coder" recipe; `scripts/smoke.sh` `codex-fast` expect lines |
| new / changed output-dir layout (per-turn files, run dirs, worktree placement) | `docs/USAGE.md` "Output layout" block; `scripts/smoke.sh` side-effect assertions (the `[[ -d ... ]]` / `[[ -f ... ]]` / `grep -q` checks at the bottom); `README.md` if user-visible |
| new / changed sandbox / permission-mode / network behavior | `docs/USAGE.md` "Codex sandbox and network access" section; `duet.example.yaml`'s `extra_args` example if users copy that pattern |
| new role or new ROLE_PROMPT entry | `ROLE_PROMPTS` in `duet.py`; the "Roles ship with" line in `docs/USAGE.md` |
| stop-condition / SIGINT / force-prompt change | `docs/USAGE.md` "Stop conditions and force prompt" table; `README.md` "What duet does" numbered list if user-visible |
| change to the `/duet` slash-command recipe | both copies: `commands/duet.md` (the plugin command) and the embedded skill body in `docs/USAGE.md` (the section starting "`/duet` Claude Code command"); `.claude-plugin/plugin.json` description if the behavior summary shifts |
| packaging / plugin metadata change (`pyproject.toml` name, version, console script, extras; `.claude-plugin/plugin.json` / `marketplace.json`) | `README.md` install section (uvx/pipx/plugin snippets); `docs/USAGE.md` `/duet` section if install steps change; the Makefile `build` target if the build tooling changes; keep `plugin.json` version matching `pyproject.toml` |
| function grows past the complexity/length budget | extract a named helper (single-file: never a new module); re-run `make complexity`; if the budget itself moves, update `scripts/check_complexity.py` defaults and the "Four regression nets" net-4 line |
| new CI job / changed check name | `.github/workflows/ci.yml`; the required-check names in `.github/BRANCH_PROTECTION.md`; the "Four regression nets" paragraph |
| breaking semantics or limit change | `README.md` "Limits / future" |

If a change is worth doing, it's worth a smoke case. `scripts/smoke.sh` is the executable part of the docs — drift between it and `duet.py` is the failure mode hardest to spot. Add the `expect` line in the same commit as the code, not later. For the pure helpers listed in the table row above, add the corresponding `tests/test_duet.py` case in the same commit too — unit tests catch contract drift that a `--dry-run` exit code can't see.

When the change is purely internal (helper refactor, no user-visible surface), update the relevant section of **this file** (`CLAUDE.md`) if it falsifies anything in the "Architecture you'll need to read multiple files to grasp" or "Hard constraints" sections.

## Hard constraints when modifying `duet.py`

- **Stdlib-only.** PyYAML is the one allowed exception, gated behind `--config foo.yaml`.
- **Don't `.format()` role-prompt templates** — use `str.replace("{SENTINEL}", ...)` (see above).
- **Don't drop `start_new_session=True`** from `subprocess.Popen` — Ctrl-C handling depends on the child being its own process group leader.
- **Atomic writes only** for `state.json`, `transcript.md`, and `*.pid` files — go through `write_text_atomic` / `append_text_atomic`. A non-atomic write can corrupt the file mid-crash and break `--status` parsing.
- **Don't add a second `<runs_dir>/.gitignore` writer.** `run_duet` already idempotently writes one with `*`.
- **Don't write to `~/.duet/runs/` from anywhere but `_register_run_in_home_index` and the unwritable-cwd fallback.** Both share the same slug scheme on purpose; a third writer with a different scheme would silently fragment the index. If you find yourself wanting one, extend the helper instead.
- **Update related docs in the same commit** (see the table above). A code-only commit is a regression of the docs.
