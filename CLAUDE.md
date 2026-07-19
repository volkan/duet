# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**duet** is a single-file Python harness (`duet.py`, ~3800 lines, stdlib-only, Python 3.9+) that runs two CLI agents — `claude` and `codex` by default, with `gemini`, `copilot`, and `opencode` available as additional backends and same-backend pairings supported — in alternating turns until both agents propose convergence in back-to-back turns with an LGTM rationale plus the sentinel (`<<<LGTM>>>` on its own line), hit `--turns`, time out, or get Ctrl-C'd. Each agent keeps its own conversation memory across turns: Claude via `--resume <session_id>` parsed from its JSON-wrapped output; Codex via `codex exec resume <uuid>` (UUID parsed out of Codex's stderr by `_parse_codex_session_id`) with a `codex exec resume --last` cwd-keyed fallback for builds that don't print one or for legacy continued runs; Gemini via JSON `session_id` from `gemini -p ... --output-format json` and `gemini --resume <session_id>`; Copilot via JSONL `sessionId` from `copilot -p ... --output-format json` and `copilot --resume=<sessionId>`; OpenCode via the top-level `sessionID` in the JSONL event stream from `opencode run --format json` and `opencode run -s <sessionID>` (the id is stable across turns, so resume is always id-keyed, never cwd-keyed).

The single-file shape is a hard constraint. PyYAML is the one optional import, gated behind `--config foo.yaml`; the smoke test uses JSON configs to stay stdlib-only. `README.md` has the user-facing pitch; `docs/USAGE.md` is the full flag reference; this file is for someone modifying `duet.py`.

Packaging shims sit beside the file without changing its shape: `duet.__version__` is the canonical runtime version and `pyproject.toml` derives the `duet-cli` package version from it (console script `duet = "duet:main"`, flat module `duet` via `py-modules`, `[yaml]` extra for PyYAML, zero runtime deps — bare `duet` on PyPI is Google's async library). The root `.claude-plugin/marketplace.json` points at the narrowed Claude plugin root `plugins/duet-claude/`; `.agents/plugins/marketplace.json` + `plugins/duet/.codex-plugin/plugin.json` + `plugins/duet/skills/duet/SKILL.md` provide the Codex `$duet` skill. Both entry points shell out to the PATH-installed CLI, launch `duet --recipe review --run-info-file ...`, validate schema 1, and poll `duet --status <run_dir> --json` instead of scraping banners. OpenCode ships the drop-in `plugins/duet-opencode/command/duet.md`; it uses `--recipe review`, but structured polling is not yet required there. Keep both manifest-bearing plugin versions in lockstep with `duet.__version__`.

## Common commands

```bash
make install            # symlink duet.py → ~/.local/bin/duet (PREFIX= to override)
make ci                 # fast local gate: unit + reasoning + smoke + complexity + source metadata
make test               # unit tests (tests/test_duet.py) + scripts/smoke.sh
make unit-test          # only the stdlib unittest suite under tests/
make smoke-test         # only scripts/smoke.sh dry-run cases
make complexity         # cyclomatic-complexity/length gate (scripts/check_complexity.py)
make reasoning-check    # reasoning-effort translation check (scripts/check_reasoning_levels.py)
make distribution-check # validate pyproject/plugin manifests and source metadata
make package-check      # build sdist/wheel and validate packaged metadata
make plugin-check       # validate the Claude Code plugin manifest with claude
make loop-test          # real E2E loop suite (default Claude/Codex; any backend via --lead-backend/--partner-backend); slow, writes runs/test-loop/
make build              # sdist+wheel into dist/ (needs: python3 -m pip install build)
make uninstall

./duet.py --dry-run --task "x" --cwd /tmp        # quickest end-to-end smoke
./duet.py --recipe review                         # canonical harness/plugin launch
./duet.py --config examples/hello.yaml           # 2-turn real run, no edits to disk
./duet.py --status runs/<id>/ --json             # stable machine-readable health probe
./duet.py --continue runs/<id>/ --task "next"    # fresh run from saved state/session ids
```

Six merge gates, in order of granularity. `.github/workflows/ci.yml` runs the runtime checks on every PR (unit + reasoning + smoke on Python 3.9/3.11/3.13), plus source/package metadata validation, Claude Code plugin validation, and complexity once. The source metadata validation covers the Claude and Codex plugin manifests and the OpenCode drop-in command file. `make ci` runs the fast local subset that does not install external CLIs or build frontends. All jobs are advisory until marked **required** in branch protection; see `.github/BRANCH_PROTECTION.md` (admins can still force-merge). `.github/workflows/real-loop-canary.yml` runs the bounded S1 scenario weekly and on demand against the latest OpenCode CLI and the currently-free `opencode/big-pickle` model, then retains its run artifacts for 14 days. It is an upstream-health signal, not a PR trigger or required check. `.github/workflows/release.yml` detects when `duet.__version__` changed on `main` and no matching tag exists, then runs gate → build → OIDC PyPI publish → GitHub Release. `.github/workflows/bump-version.yml` bumps `duet.py` plus both plugin manifests and opens the release PR.

1. `tests/` (run via `make unit-test` or `python3 -m unittest discover -s tests`) — stdlib `unittest` only. `test_duet.py` covers pure helpers; `test_control_plane.py` covers the recipe, run-info/status schemas, deferred kickoff, strict worktree failure, and secret exclusion with temporary directories. Add tests in the same commit as contract changes.
2. `scripts/smoke.sh` (run via `make smoke-test` or as part of `make test`) — the integration net. Each `expect` line is a self-contained `--dry-run` invocation; to run a single case, copy its command out of the script and execute it directly. Smoke compares exit codes, so anything that changes `print_run_status`, the argparse error paths, or `resolve_seed_inputs` will surface there first. The smoke also asserts on side-effects (`<TMPD>/.duet/runs` created, `state.json` contains `duet_pid`, etc.) — read the bottom half of `smoke.sh` before touching foreign-cwd or status logic.
3. `scripts/check_reasoning_levels.py` (run via `make reasoning-check`) — monkey-patches `_run` and asserts each reasoning level emits the right backend cmd; the executable half of invariant 1 below.
4. `scripts/check_distribution_metadata.py` validates source/plugin/artifact metadata; `scripts/check_installed_wheel.py` installs the wheel into a temp venv and checks runtime version plus run-info/status JSON. `make package-check` runs both after building.
5. `claude plugin validate .` (run via `make plugin-check`) — validates the Claude Code plugin with the real Claude Code CLI. CI installs the pinned CLI before running this job.
6. `scripts/check_complexity.py` (run via `make complexity`) — stdlib AST cyclomatic-complexity/length gate (budget: CC ≤ 25, length ≤ 160). duet is single-file, so the only defense against sprawl is keeping individual functions small; this fails CI if any function exceeds budget. After a refactor, re-run it — the orchestration entrypoints (`run_duet`, `main`) sit a few points under budget, so a careless inline addition can trip it.

## Required GitHub workflow for agents

The default branch is `main`; do not use `master` in commands, docs, PR bases,
or automation. Do not commit or push feature work directly to `main`. Create a
topic branch, run the relevant local gates, open a PR, wait for all six required
checks, and merge through GitHub. The detailed branch and merge checklist lives
in `docs/AGENT_WORKFLOW.md`.

## Testing discipline (do not ship broken code)

**Broken code must never reach `main` or PyPI. Run tests proportional to what a
change touches — and always against this repo's code, never an installed copy.**

1. **Minimum, every change:** `make ci` must be green (unit + reasoning + smoke
   + complexity + distribution). It is the hard floor, not optional.
2. **Risk-scaled:** anything that affects the live loop — a backend adapter /
   `call_*` helper, session-resume, convergence detection, worktree, or the
   force prompt — also needs a **real `make loop-test` for the affected
   backend(s)**. The loop-test is multi-backend: `--lead-backend` /
   `--partner-backend` / `--lead-model` / `--partner-model` retarget the
   scenarios at any backend, and the currently-free OpenCode model pairing
   (`--lead-backend opencode --partner-backend opencode --lead-model
   opencode/big-pickle …`) gives a real loop with **no auth** while that model is
   available, so there is no excuse to silently skip a real-loop check for a
   backend change. Packaging/plugin
   changes additionally need `make package-check` (+ `make plugin-check`).
3. **Always test the repo code, via the root path.** Every test harness must
   invoke duet through the repo-root `duet.py` resolved from the harness's own
   file location — never a cwd-relative path and never a bare `duet` that could
   resolve to a pipx/PATH install. The Python harnesses use
   `Path(__file__).resolve().parent.parent / "duet.py"`; `scripts/smoke.sh`
   anchors `DUET` to `${BASH_SOURCE[0]}/../duet.py`; `scripts/duet_loop_e2e.py`
   defaults `--duet` to `REPO_ROOT/duet.py`. Do not regress any of these to a
   PATH lookup. (The `/duet` plugin recipes deliberately shell out to the PATH
   `duet` — that is the *product* for end users, not a test path; when
   exercising the plugins locally, `make install` so PATH `duet` is the repo
   symlink.)
4. **No silent skips.** If you genuinely cannot run a relevant real loop (no
   auth for that backend), say so in the PR and fall back to the OpenCode
   free-model loop plus the dry-run matrix — do not call the change verified.

The full how-to and the per-change matrix live in `docs/USAGE.md`
("Which tests to run for a change").

## Architecture you'll need to read multiple files to grasp

### Two state objects

- **`Agent`** (dataclass) — `backend` (`claude`|`codex`|`gemini`|`copilot`|`opencode`), `role` (`planner`|`coder`|`reviewer`|custom), `session_id` (mutated across turns), `cwd_override` (set when this agent runs in a worktree), `reasoning_effort` (per-agent override of cfg-level reasoning), `extra_args`, `role_prompt`.
- **`DuetConfig`** (dataclass) — exactly 2 agents plus orchestration knobs (turns, sentinel, sandbox, permission_mode, deferred `task_from_cmd`, strict worktree settings, run-info path, add_dirs, reasoning).

`run_duet()` is the loop. `_prepare_run` owns allocation, initial phase state, atomic run-info publication, worktree setup, and deferred kickoff before handing control to it. `_execute_turn`, `_derive_seed_or_failure`, `_dry_run_recap_state`, and `ask_force` own later phases; every payload still comes from `_build_run_state`. `build_run_status` builds the secret-minimized status schema and both human/JSON renderers consume it. `main()` delegates validation/config construction to named helpers. Keep this decomposition: the complexity gate fails when branch-heavy setup/status logic is inlined into `run_duet` or `main`.

### Three invariants spread across multiple call sites

These break easily if you only update one place.

1. **The reasoning translation layer.** Six duet-abstraction levels (`REASONING_LEVELS = ["minimal","low","medium","high","xhigh","max"]`) → backend-specific maps (`CLAUDE_REASONING_MAP`, `CODEX_REASONING_MAP`, `GEMINI_REASONING_MAP`, `COPILOT_REASONING_MAP`, `OPENCODE_REASONING_MAP`) and prompt-prefix tables → the actual `--effort`/`-c model_reasoning_effort=`/`--variant` emission inside adapter calls. Codex `medium` is intentionally a no-op (Codex's default; don't waste a flag). Codex `xhigh` passes through, and Codex `max` maps to `xhigh` because Codex does not document a separate `max` value. Claude `minimal` maps to `low` (Claude has no `minimal`); Claude `xhigh` and `max` pass through. Copilot `minimal` maps to `none`; Copilot `xhigh` and `max` pass through. OpenCode uses an identity map (every level emits `run --variant <level>`): variants are provider-specific, but OpenCode silently ignores one a model doesn't define, so passing the level through is forward-compatible and never errors. Gemini has no documented effort flag, so it emits no backend effort argument; high/xhigh/max only tack a "think hard"/"think very hard"/"ultrathink" prefix onto the prompt (Claude/Gemini/Copilot/OpenCode all reuse `CLAUDE_REASONING_PROMPT_PREFIX`). `scripts/check_reasoning_levels.py` monkey-patches `_run` and asserts each level produces the right cmd; rerun it after touching any of these constants.

   **Codex fast mode override.** `cfg.codex_fast` (CLI `--codex-fast`, YAML `codex_fast: true`) is a Codex-only short-circuit that pins this run's codex turns to `model_reasoning_effort=low` and adds `model_reasoning_summary=concise`, regardless of `cfg.reasoning` or `agent.reasoning_effort`; `--no-codex-fast` explicitly disables a value supplied by YAML/JSON or restored by `--continue`. **Fast mode is role-scoped: it applies only to codex agents whose role is `coder`.** `call_agent` recomputes `fast = cfg.codex_fast and agent.role == "coder"` per turn, so a `--lead codex:planner --codex-fast` run no longer silently downgrades the planner. Config validation in `main()` warns on stderr and clears `cfg.codex_fast` when no codex:coder agent exists; it prints a softer note when fast is partial (some codex agents are coders, some aren't). The override happens inside `call_codex` (`effective = "low" if fast else reasoning`) so claude turns are unaffected — `--reasoning high --codex-fast` with the default `claude:planner + codex:coder` is the canonical "deep planner, fast coder" combo. If a user really wants fast on a non-coder codex role, they can set per-agent `reasoning_effort: low` in YAML — that's the documented escape hatch. Do not use Codex `minimal` here unless the default Codex tool set changes; real tool-enabled runs currently reject `minimal` effort. Dry-run output tags codex turns with ` fast` so smoke can grep for it; if you change the tag string, update `scripts/smoke.sh`'s `expect_stdout "codex-fast …"` lines.

2. **The launch/status control plane.** `_prepare_run` must write initial state (`kickoff_pending` when applicable) before executing `task_from_cmd`, then atomically publish `--run-info-file` without overwriting. Raw task commands never enter state/run-info. `state.json["duet_pid"]`, transient `turn-*.pid`, stderr logs, and the saved `phase` feed `build_run_status`. JSON status is schema 1 and curated: never add task/kickoff/verify commands, credentials, `extra_args`, or raw errors/history. Exit codes are 0 terminal (any reason), 1 running, 2 stuck/crashed, 3 status error. Every state payload must still come from `_build_run_state`; phase writes cover kickoff pending/running, turn running, between turns, awaiting force, and finished.

3. **Codex's flag-set bifurcation and same-cwd isolation.** `codex exec` accepts `--sandbox` and `--cd`; both `codex exec resume <uuid>` and `codex exec resume --last` reject them with "unexpected argument". `call_codex` sends those flags only on a fresh exec; every resume reasserts the selected sandbox with the supported `-c sandbox_mode="<policy>"` override and gets its cwd from `subprocess.Popen(cwd=...)`. Resume picks `resume <uuid>` when `agent.session_id` matches `_CODEX_UUID_RE`, else falls back to `resume --last`. After every Codex turn, `_parse_codex_session_id(err)` takes the first line-start `session id: <uuid>` match (anchored and case-insensitive), then `_resolve_codex_session_pin` preserves an established UUID if stderr reports a different id, keeps the current pin when no id was parsed, or upgrades the legacy `"codex-current"` marker when a real UUID appears. `call_codex` uses `"codex-current"` only when neither source provides a pin. For `codex`/`codex` peers sharing one effective cwd, `run_duet` must fail immediately if either peer's first turn does not produce a UUID, or before any peer with a non-UUID session marker would resume; otherwise `--last` can hijack the other peer's most recent session. Different effective cwds (for example via `--worktree`) are allowed to use the fallback. Modern Codex's clap parser also requires options *before* the positional prompt — adding any flag after `full_prompt` is a silent regression that surfaces as a confusing rc=2.

### Convergence: fenced-code-aware, rationale-backed, pair-approved

`convergence_proposed()` does a line-by-line scan tracking whether it's inside a markdown code fence (` ``` ` or `~~~`, length-matched closing). The sentinel only counts on its own line *outside* a fence, and the same reply must include an `LGTM rationale:` / `Rationale:` outside a fence. The loop only stops after two back-to-back agent turns propose convergence, so one agent can reject the other's rationale by omitting the sentinel and asking for another round. An agent quoting "the sentinel is `<<<LGTM>>>`" inline won't false-positive; an agent showing an example code block containing the sentinel won't either. Don't replace this with a regex over the whole text — README's "Limits" section explicitly calls this out as the deliberate trade-off.

### Subprocess plumbing (`_run`)

`subprocess.Popen` with `start_new_session=True` so we own a process group (clean SIGTERM/SIGKILL via `os.killpg`). Two reader threads drain stdout/stderr line-by-line; stderr is mirrored to the user's terminal (with `LIVE_PREFIX = "  │ "`) **and** tee'd to a per-turn `*.stderr.log` file. `pid_file_path` is written atomically (temp+rename) on Popen and removed in `finally`. `_terminate_active_processes(SIGKILL)` walks `_ACTIVE_PROCS` (a thread-locked set) on second-Ctrl-C.

Failed-turn transcript blocks must go through `format_agent_error_for_transcript`: it keeps a bounded head/tail excerpt (`AGENT_ERROR_TRANSCRIPT_MAX_CHARS`) and points at the per-turn stderr log, which remains the complete forensic record. Do not inline an unbounded `str(exc)` into `transcript.md`.

`LIVE_STREAM` and `LIVE_PREFIX` are module-level mutable globals. `resolve_task_from_cmd` swaps `LIVE_PREFIX` to `"  $ "` while running the user's task command and restores it via try/finally — this is the only legitimate write to that global; don't add others.

### `str.replace("{SENTINEL}", ...)`, never `.format(...)`

Role prompts (`ROLE_PROMPTS` and any user-supplied `role_prompt`) frequently contain literal `{...}` (JSON schema examples, jq patterns). `Agent.system_prompt` does `str.replace`, not `.format`, on purpose. If you change this, smoke won't catch it — but any `role_prompt` containing literal `{json: schema}` examples would crash with "unexpected '{' in field name" on the first turn.

### Worktree mode

`--worktree` creates `<runs_dir>/<run_id>/wt/` on a fresh `duet/<run_id>` branch and points the selected worktree agent's `cwd_override` at it (partner by default, lead with `--worktree-for lead`). `--require-worktree` converts every missing/non-git/setup failure into durable `setup_error` instead of same-repo fallback; `--recipe review` enables it. After every worktree-agent turn, duet appends a handoff block plus `git_diff_summary` (`git status --short` + `--stat` + truncated `diff HEAD`, capped at 8 KB, plus fenced previews of untracked text files) to that turn's reply. The worktree is intentionally **not deleted** at exit. Default placement under `runs/<id>/wt/` is durable across reboots; `--worktree-root /tmp` opts back into temp behavior. `--worktree-path` is the resume case; `--continue <run>` uses the prior state's worktree. `--worktree` and `--worktree-path` remain mutually exclusive.

### Continue mode

`--continue RUN_DIR_OR_ID` is a fresh-run convenience wrapper around saved `state.json`: `_resolve_run_dir` finds the old run, `build_continue_config` restores both `Agent` objects with their saved `session_id`s, restores saved run knobs (`sentinel`, `sandbox`, `permission_mode`, `add_dirs`, `reasoning`, `codex_fast`) unless the CLI overrides them, chooses the next speaker from the last completed `history` entry, reuses the saved worktree path (or legacy `<run>/wt/`), and builds a continuation kickoff. It does **not** append to the old transcript. Because `state.json` is in the run tree, `build_continue_config` refuses to replay state-sourced `verify_cmd`, agent `extra_args`, extra access roots, or permission/sandbox values outside the safe defaults unless the user passes `--trust-state`; fresh CLI values count as explicit overrides, and `--no-codex-fast` disables restored fast mode. `DuetConfig.start_speaker_idx` is internal plumbing for this; normal runs keep the default partner-first value of `1`. Rolling `state.json` writes must keep `transcript_path`, `worktree`, `worktree_branch`, `worktree_for`, the restored run knobs, `continue_from`, and `duet_pid` because `--continue` and `--status` both depend on state surviving mid-turn crashes.

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

## Gemini-specific quirks

- **JSON `session_id` is required.** `gemini -p ... --output-format json` must include top-level `session_id`; duet stops with `agent_error` if it is absent because multi-turn memory would be unsafe. There is no `--resume-gemini` shortcut yet; use `--continue` from `state.json` or YAML `session_id:` for resumed Gemini agents.
- **No effort flag.** Gemini receives the high/xhigh/max prompt nudges but no backend reasoning argument.
- **Safety flags differ.** `--sandbox` is Codex-only. Gemini maps duet's `permission_mode` to `--approval-mode`: `default`, `auto_edit` for `acceptEdits`, `plan`, or `yolo` for `bypassPermissions`. `add_dirs:` becomes repeated `--include-directories`.
- **Trusted-folder gate.** The Gemini CLI refuses to run in an untrusted directory (e.g. a fresh `/tmp` fixture), exiting 55 with "not running in a trusted directory"; duet surfaces this cleanly as `agent_error`. It is a Gemini policy, not a duet bug. For real Gemini runs in untrusted/temp cwds, export `GEMINI_CLI_TRUST_WORKSPACE=true` (or run in a trusted dir, or pass a per-agent `extra_args` trust opt-in). Real-loop tests that target Gemini in `/tmp` need this.

## Copilot-specific quirks

- **JSONL `sessionId` is required.** `copilot -p ... --output-format json` emits a JSONL event stream; duet takes the last `assistant.message` content as the reply, the final `result.sessionId` as the resume handle, and treats nonzero `result.exitCode` as `agent_error`. Missing or malformed JSONL stops with `agent_error` because multi-turn memory would be unsafe. There is no `--resume-copilot` shortcut yet; use `--continue` from `state.json` or YAML `session_id:` for resumed Copilot agents.
- **Permissions are Copilot-native.** `--sandbox` and `permission_mode` do not apply to Copilot. duet runs Copilot non-interactively with `--allow-all-tools`, sends the effective cwd via `-C`, and maps `add_dirs:` to repeated `--add-dir`. Use per-agent `extra_args` for Copilot's `--deny-tool`, URL/path policy, or broader `--allow-all` / `--yolo` modes.
- **Reasoning names differ at the floor.** Copilot accepts `none`, `low`, `medium`, `high`, `xhigh`, and `max`; duet maps user-facing `minimal` to Copilot's `none`.

## OpenCode-specific quirks

- **`opencode run` exits 0 even on errors.** A model-not-found, auth, or tool failure is emitted as a `{"type":"error","error":{...}}` event in the JSONL stream, *not* a nonzero exit code. `_parse_opencode_jsonl` scans for that event and `call_opencode` raises `agent_error` on it — never rely on the process rc for OpenCode error detection (rc only catches crashes/timeouts/`command not found`). This is the single biggest footgun; the unit test `test_opencode_error_event_maps_to_agent_error` pins it.
- **JSONL `sessionID` is required and stable.** `opencode run --format json` emits a JSONL event stream on stdout; every event carries a top-level `sessionID`. duet collects reply text from `{"type":"text","part":{"type":"text","text":...}}` events (keyed by part id, last-write-wins, concatenated in arrival order so a tool-split reply isn't lost), and resumes with `opencode run -s <sessionID>`. Unlike Claude, OpenCode does **not** rotate the id across turns, so resume is robustly id-keyed and two OpenCode agents can share one cwd safely (`resume_is_cwd_keyed` stays the default `_resume_never_cwd_keyed`; the same-cwd peer guards in `run_duet` are automatic no-ops for OpenCode). A missing `sessionID` stops with `agent_error`.
- **Non-JSON stdout lines are skipped, not fatal.** OpenCode prints a one-time DB-migration banner on a fresh machine. `_parse_opencode_jsonl` tolerates non-JSON lines (it does *not* mirror Copilot's strict "any banner is malformed" rule) — a genuine failure still surfaces via the missing-`sessionID` / error-event checks.
- **Permissions are OpenCode-native.** `--sandbox` and `permission_mode` do not apply. duet runs `opencode run --dangerously-skip-permissions` (like Copilot's `--allow-all-tools`) so tool-using turns don't hang, scopes the project with `--dir <eff_cwd>`, and passes the prompt as the trailing positional (all options first). There is **no `add_dirs:` equivalent** — OpenCode operates on the whole `--dir` project — so `call_opencode` does not take `add_dirs` (it mirrors Codex in that respect). Use per-agent `extra_args` (e.g. `["--agent", "build"]`) for narrower policy.
- **Models use the `provider/model` form** (`-m anthropic/claude-sonnet-4-6`); a bare model name will make OpenCode error. There is no `--resume-opencode` shortcut yet; use `--continue` from `state.json` or YAML `session_id:` for resumed OpenCode agents.

## Keep docs in sync with each change

All commits in this repository must use Conventional Commits (`type: summary`, for example `docs: update commit guidance`).

Every change to `duet.py` (or anything else under this repo) must update the related documents in the **same commit**. Drift between code and docs is the dominant failure mode here — there's no CI, no schema validation, just these files. Use this table as a checklist before you commit:

| change in `duet.py` | also update |
|---|---|
| new / renamed / changed CLI flag | `docs/USAGE.md` flag table; `README.md` "Best recipes" if the flag shows up in a canonical recipe; `duet.example.yaml` if there's a corresponding YAML key; `scripts/smoke.sh` if the flag has dry-run-able exit-code semantics |
| new / changed YAML config key | `duet.example.yaml` (commented example with default); `docs/USAGE.md` only if it warrants user-facing prose beyond the flag table |
| new / changed exit code or `--status` output | `docs/USAGE.md` exit-code table; add an `expect` line in `scripts/smoke.sh` |
| new / changed reasoning level or backend mapping | rerun `scripts/check_reasoning_levels.py` and update its `EXPECTED` dict if values shift; extend `tests/test_duet.py::TestReasoningHelpers` for the data side; `docs/USAGE.md` `--reasoning` row |
| new / changed pure helper covered by `tests/test_duet.py` (convergence detector, codex session-id parser, copilot JSONL parser, opencode JSONL parser, recap header parser, file-path heuristic, partner spec parser, markdown fence sizer, age formatter, status/state coercion or trust helper) | add a case to `tests/test_duet.py` describing the new behavior in the same commit |
| new / changed Codex fast-mode behavior (`cfg.codex_fast`) | `docs/USAGE.md` `--codex-fast` flag-table row + "Codex fast mode" subsection; `duet.example.yaml` `codex_fast:` line; `README.md` "deep planner, fast coder" recipe; `scripts/smoke.sh` `codex-fast` expect lines |
| new / changed output-dir layout (per-turn files, run dirs, worktree placement) | `docs/USAGE.md` "Output layout" block; `scripts/smoke.sh` side-effect assertions (the `[[ -d ... ]]` / `[[ -f ... ]]` / `grep -q` checks at the bottom); `README.md` if user-visible |
| new / changed sandbox / permission-mode / network behavior | `docs/USAGE.md` "Codex sandbox and network access" section; `duet.example.yaml`'s `extra_args` example if users copy that pattern |
| new role or new ROLE_PROMPT entry | `ROLE_PROMPTS` in `duet.py`; the "Roles ship with" line in `docs/USAGE.md` |
| stop-condition / SIGINT / force-prompt change | `docs/USAGE.md` "Stop conditions and force prompt" table; `README.md` "What duet does" numbered list if user-visible |
| change to the `/duet` slash-command recipe | `plugins/duet-claude/commands/duet.md` (the plugin command) and `docs/CLAUDE_CODE_PLUGIN.md` (install/use/troubleshooting); keep `docs/USAGE.md` as the concise reference link/summary; `plugins/duet-claude/.claude-plugin/plugin.json` description if the behavior summary shifts (the root `marketplace.json` deliberately carries only a short listing blurb, so it rarely needs the same edit) |
| change to the Codex `$duet` skill recipe | `plugins/duet/skills/duet/SKILL.md` and `docs/CODEX_PLUGIN.md`; keep `docs/USAGE.md` as the concise reference link/summary; `plugins/duet/.codex-plugin/plugin.json` description if the behavior summary shifts |
| change to the OpenCode `/duet` command recipe | `plugins/duet-opencode/command/duet.md` and `docs/OPENCODE_PLUGIN.md`; keep the `docs/USAGE.md` "Plugin entry points" OpenCode subsection + `README.md` "Inside OpenCode" section in sync; update `scripts/check_distribution_metadata.py`'s `_assert_opencode_command_metadata` required-text list if the recipe's command/flags change |
| packaging / plugin metadata change (`duet.__version__`, `pyproject.toml` dynamic version/console script/extras; plugin manifests/marketplaces) | `README.md` install section; plugin guides and `docs/USAGE.md` if behavior changes; keep both plugin manifest versions matching `duet.__version__` |
| function grows past the complexity/length budget | extract a named helper (single-file: never a new module); re-run `make complexity`; if the budget itself moves, update `scripts/check_complexity.py` defaults and the merge gates paragraph |
| new CI job / changed check name | `.github/workflows/ci.yml`; the required-check names in `.github/BRANCH_PROTECTION.md`; the merge gates paragraph |
| new / changed release or bump workflow (`release.yml`, `bump-version.yml`, `scripts/bump_release_version.py`) | `docs/RELEASING.md` runbook; the release/bump sentences in the merge-gates paragraph; `tests/test_bump_release_version.py` when the bump logic changes |
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
