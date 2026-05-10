#!/usr/bin/env bash
# scripts/smoke.sh - exercise duet's CLI surface in dry-run.
set -euo pipefail
DUET=${DUET:-./duet.py}
# Resolve to absolute so cases that `cd $TMPD && duet …` still find the binary.
DUET_ABS=$(cd "$(dirname "$DUET")" && pwd)/$(basename "$DUET")
TMPD=$(mktemp -d -t duet-smoke.XXXX)
TMPD_REAL=$(cd "$TMPD" && pwd -P)
trap 'rm -rf "$TMPD"' EXIT
# Hermeticity: route ~/.duet/runs/ writes (the home-index symlink farm
# `_register_run_in_home_index` creates) into TMPD too, so smoke runs
# don't pollute the user's real $HOME.
export HOME="$TMPD"
PASS=0; FAIL=0
expect() {  # name, want_rc, cmd...
  local name=$1 want=$2; shift 2
  local out err rc
  err=$(mktemp); out=$(mktemp)
  if "$@" >"$out" 2>"$err"; then rc=0; else rc=$?; fi
  if [[ $rc -eq $want ]]; then PASS=$((PASS+1)); echo "ok   $name"
  else FAIL=$((FAIL+1)); echo "FAIL $name (rc=$rc want=$want)"; sed 's/^/    /' "$err"; fi
  rm -f "$out" "$err"
}
expect_stdout() {  # name, want_rc, grep_pattern, cmd...
  local name=$1 want=$2 pattern=$3; shift 3
  local out err rc
  err=$(mktemp); out=$(mktemp)
  if "$@" >"$out" 2>"$err"; then rc=0; else rc=$?; fi
  if [[ $rc -eq $want ]] && grep -q -- "$pattern" "$out"; then
    PASS=$((PASS+1)); echo "ok   $name"
  else
    FAIL=$((FAIL+1)); echo "FAIL $name (rc=$rc want=$want, pattern=$pattern)"
    sed 's/^/    stdout: /' "$out"
    sed 's/^/    stderr: /' "$err"
  fi
  rm -f "$out" "$err"
}

# C1 - task input variants
printf "fix typo\n" > "$TMPD/stdin-task.txt"
expect "task @-"                            0 "$DUET" --task @- --dry-run --cwd "$TMPD" < "$TMPD/stdin-task.txt"
echo "from file" > "$TMPD/t.txt"
expect "task @file"                          0 "$DUET" --task @"$TMPD/t.txt" --dry-run --cwd "$TMPD"
expect "task-from-cmd"                       0 "$DUET" --task-from-cmd 'echo hello' --dry-run --cwd "$TMPD"
expect "task-from-cmd cwd"                   0 "$DUET" --task-from-cmd "test \"\$(pwd -P)\" = \"$TMPD_REAL\" && echo cwd-ok" --dry-run --cwd "$TMPD"
expect "task literal still works"            0 "$DUET" --task "literal" --dry-run --cwd "$TMPD"
RECAP_RUNS="$TMPD/recap-runs"
expect "recap dry-run flag"                  0 "$DUET" --dry-run --recap --task "x" --cwd "$TMPD" --runs-dir "$RECAP_RUNS"
RECAP_RUN=$(ls -1d "$RECAP_RUNS"/2*/ 2>/dev/null | head -1 || true)
if [[ -n "$RECAP_RUN" ]]; then
  expect_stdout "recap status prints path"   0 "recap:" "$DUET" --status "$RECAP_RUN"
  grep -q '"recap_path"' "$RECAP_RUN/state.json" \
      || { echo "FAIL: recap_path missing from recap state.json"; FAIL=$((FAIL+1)); }
else
  echo "FAIL: recap dry-run dir not created"; FAIL=$((FAIL+1))
fi
expect_stdout "recap dry-run prints mode"    0 "mode: recap" "$DUET" --dry-run --recap --task "x" --cwd "$TMPD"
expect "triage-reviewer role"                0 "$DUET" --task "x" --dry-run --cwd "$TMPD" --lead claude:triage-reviewer --partner codex:coder
echo "kickoff from file" > "$TMPD/k.txt"
expect "kickoff @file"                       0 "$DUET" --kickoff @"$TMPD/k.txt" --dry-run --cwd "$TMPD"
printf "kickoff stdin\n" > "$TMPD/stdin-kickoff.txt"
expect "kickoff @-"                          0 "$DUET" --kickoff @- --dry-run --cwd "$TMPD" < "$TMPD/stdin-kickoff.txt"

# C1 conflicts
expect "both @file and from-cmd -> error"    2 "$DUET" --task @"$TMPD/t.txt" --task-from-cmd 'echo x' --dry-run --cwd "$TMPD"
expect "@nonexistent -> error"               2 "$DUET" --task @"$TMPD/missing" --dry-run --cwd "$TMPD"
printf '\xff' > "$TMPD/binary.txt"
expect "@binary -> error"                    2 "$DUET" --task @"$TMPD/binary.txt" --dry-run --cwd "$TMPD"
head -c 524289 /dev/zero > "$TMPD/large.txt"
expect "task too large -> error"             2 "$DUET" --task @"$TMPD/large.txt" --dry-run --cwd "$TMPD"
expect "from-cmd nonzero -> error"           2 "$DUET" --task-from-cmd 'false' --dry-run --cwd "$TMPD"
expect "from-cmd empty stdout -> error"      2 "$DUET" --task-from-cmd 'true' --dry-run --cwd "$TMPD"

# Config key support (JSON keeps the smoke stdlib-only).
cat > "$TMPD/cfg.json" <<JSON
{"cwd":"$TMPD","dry_run":true,"task_from_cmd":"echo config","agents":[{"name":"claude-lead","backend":"claude","role":"planner"},{"name":"codex-partner","backend":"codex","role":"coder"}]}
JSON
expect "config task_from_cmd"                0 "$DUET" --config "$TMPD/cfg.json"
expect "config cli --runs-dir override"      0 "$DUET" --config "$TMPD/cfg.json" --runs-dir "$TMPD/config-runs"
[[ -d "$TMPD/config-runs" ]] || { echo "FAIL: config-runs not created"; FAIL=$((FAIL+1)); }

# `--continue` restores agents/session ids from a prior state.json and
# starts the next agent after the last completed turn.
CONT_RUNS="$TMPD/continue-runs"
expect "continue seed run"                   0 "$DUET" --dry-run --task "x" --cwd "$TMPD" --runs-dir "$CONT_RUNS" --turns 1 --lead codex:planner --partner claude:coder
CONT_BASE=$(ls -1d "$CONT_RUNS"/2*/ 2>/dev/null | head -1 || true)
expect_stdout "continue without task picks next speaker" 0 "Turn 1 :: codex-lead" "$DUET" --continue "$CONT_BASE" --dry-run --runs-dir "$TMPD/continue-runs-2" --turns 1
CONT_NEW=$(ls -1d "$TMPD/continue-runs-2"/2*/ 2>/dev/null | head -1 || true)
[[ -n "$CONT_NEW" ]] && grep -q '"continue_from"' "$CONT_NEW/state.json" \
    || { echo "FAIL: continue_from missing from continued state.json"; FAIL=$((FAIL+1)); }

# Old crashed worktree runs may have a wt/ directory but no worktree field
# in their rolling state.json. Continue should still reuse run/wt.
CONT_SYNTH="$TMPD/continue-synth/20260507-120000"
mkdir -p "$CONT_SYNTH/wt"
cat > "$CONT_SYNTH/state.json" <<JSON
{"task":"x","cwd":"$TMPD","turns_used":2,"agents":[{"name":"codex-lead","backend":"codex","role":"planner","session_id":"codex-current"},{"name":"claude-partner","backend":"claude","role":"coder","session_id":"sid-123"}],"history":[{"turn":1,"agent":"claude-partner"},{"turn":2,"agent":"codex-lead"}],"finished_reason":null,"duet_pid":1}
JSON
expect "continue reuses legacy wt dir"       0 bash -c '
  out=$("$1" --continue "$2" --dry-run --runs-dir "$3" --turns 1)
  echo "$out" | grep -q "reusing worktree:" \
    || { echo "missing reusing worktree output"; echo "$out"; exit 1; }
  echo "$out" | grep -q "Turn 1 :: claude-partner" \
    || { echo "wrong next speaker"; echo "$out"; exit 1; }
  exit 0
' _ "$DUET_ABS" "$CONT_SYNTH" "$TMPD/continue-runs-3"

expect "convergence requires rationale"      0 python3 - "$DUET_ABS" <<'PY'
import importlib.util
import pathlib
import sys

duet_path = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("duet_under_test", duet_path)
m = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = m
spec.loader.exec_module(m)

assert not m.convergence_proposed("done\n<<<LGTM>>>", "<<<LGTM>>>")
assert m.convergence_proposed(
    "LGTM rationale: task is satisfied, checks passed, and no blocking follow-ups remain.\n<<<LGTM>>>",
    "<<<LGTM>>>",
)
assert not m.convergence_proposed(
    "LGTM rationale: task is satisfied, checks passed, and no blocking follow-ups remain.\n"
    "```text\n<<<LGTM>>>\n```",
    "<<<LGTM>>>",
)
assert not m.convergence_proposed(
    "<<<LGTM>>>\nLGTM rationale: task is satisfied, but this came too late.",
    "<<<LGTM>>>",
)
PY
expect_stdout "one proposal not enough"      0 "reason=max_turns" "$DUET" --dry-run --turns 1 --task "x" --cwd "$TMPD"
expect_stdout "two proposals converge"       0 "reason=converged" "$DUET" --dry-run --turns 2 --task "x" --cwd "$TMPD"

expect "worktree handoff names review target" 0 python3 - "$DUET_ABS" "$TMPD" <<'PY'
import importlib.util
import pathlib
import shlex
import sys

duet_path = pathlib.Path(sys.argv[1])
tmpd = pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("duet_under_test", duet_path)
m = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = m
spec.loader.exec_module(m)

wt_path = tmpd / "review wt"
m.git_diff_summary = lambda path: "### git status\n M duet.py\n\n### diff\n..."
out = m.append_worktree_diff("reply", wt_path, "duet/test-run")
quoted = shlex.quote(str(wt_path))
# Heading restores the worktree basename; absolute path lives inside the block.
assert f"#### worktree changes ({wt_path.name})" in out, out
assert f"Worktree path: `{wt_path}`" in out, out
assert "Branch: `duet/test-run`" in out, out
# Wording must accept clean exploration turns: "Any code changes" not "The code changes".
assert "Any code changes for this turn" in out, out
assert "The code changes for this turn" not in out, out
assert "Your current cwd may be a clean checkout" in out, out
assert f"git -C {quoted} status --short" in out, out
assert f"git -C {quoted} diff HEAD" in out, out
# `make -C <wt> test` was project-specific noise; project test commands
# belong in CLAUDE.md / README, not the generic handoff.
assert "make -C" not in out, out
PY

# Codex fast mode: tag must show in dry-run codex output, and reasoning
# must be pinned to `low` even when --reasoning says otherwise.
expect_stdout "codex-fast tags dry-run"      0 "fast"               "$DUET" --dry-run --task "x" --cwd "$TMPD" --codex-fast
expect_stdout "codex-fast pins low"          0 "reasoning=low"      "$DUET" --dry-run --task "x" --cwd "$TMPD" --reasoning high --codex-fast
# YAML key path: codex_fast: true should produce the same tag.
cat > "$TMPD/cfg-fast.json" <<JSON
{"cwd":"$TMPD","dry_run":true,"task":"x","codex_fast":true,"agents":[{"name":"claude-lead","backend":"claude","role":"planner"},{"name":"codex-partner","backend":"codex","role":"coder"}]}
JSON
expect_stdout "codex_fast yaml key"          0 "fast"               "$DUET" --config "$TMPD/cfg-fast.json"

expect "codex-fast command args"             0 python3 - "$DUET_ABS" "$TMPD" <<'PY'
import importlib.util
import pathlib
import sys

duet_path = pathlib.Path(sys.argv[1])
cwd = pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("duet_under_test", duet_path)
m = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = m
spec.loader.exec_module(m)

calls = []

def fake_run(cmd, **kwargs):
    calls.append(cmd)
    return 0, "ok", ""

m._run = fake_run
agent = m.Agent(name="codex-partner", backend="codex", role="coder")
m.call_codex(
    agent,
    "sys",
    "msg",
    cwd,
    "workspace-write",
    60,
    dry=False,
    first_turn=True,
    reasoning="high",
    fast=True,
)
first = calls[-1]
assert "model_reasoning_effort=low" in first, first
assert "model_reasoning_effort=minimal" not in first, first
assert "model_reasoning_summary=concise" in first, first
assert first.index("model_reasoning_summary=concise") < len(first) - 1, first

agent.session_id = "codex-current"
m.call_codex(
    agent,
    "sys",
    "msg",
    cwd,
    "workspace-write",
    60,
    dry=False,
    first_turn=False,
    reasoning="high",
    fast=True,
)
resume = calls[-1]
assert resume[:4] == ["codex", "exec", "resume", "--last"], resume
assert "model_reasoning_effort=low" in resume, resume
assert "model_reasoning_summary=concise" in resume, resume
PY

# Codex resume by parsed session UUID. call_codex must:
#   1. Parse `session id: <uuid>` out of stderr and return that UUID, so
#      run_duet writes a real id (not the "codex-current" sentinel) to
#      state.json on the first turn.
#   2. On the next turn, pin to that UUID with `codex exec resume <uuid>`
#      (no `--last`, no `--sandbox`, no `--cd`).
#   3. Keep the legacy `codex-current` sentinel routing through `--last`.
#   4. Treat session_id=None as "no resume info" → fresh `codex exec`.
#   5. Keep all options BEFORE the positional prompt.
expect "codex resume by session id"          0 python3 - "$DUET_ABS" "$TMPD" <<'PY'
import importlib.util
import pathlib
import sys

duet_path = pathlib.Path(sys.argv[1])
cwd = pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("duet_under_test", duet_path)
m = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = m
spec.loader.exec_module(m)

UUID = "019e12ad-0b1b-7732-bd7b-6acbbd04ab46"
STDERR_WITH_UUID = (
    "[codex] starting up\n"
    f"session id: {UUID}\n"
    "[codex] tool calls: 0\n"
)

# 1. Stderr parser returns the UUID lowercased; tolerates noise; rejects
#    arbitrary UUIDs that aren't labeled as a session id.
assert m._parse_codex_session_id(STDERR_WITH_UUID) == UUID
assert m._parse_codex_session_id("nothing here") is None
assert m._parse_codex_session_id(f"trace: {UUID} happened") is None
assert m._parse_codex_session_id(f"Session ID: {UUID.upper()}") == UUID

calls = []

def fake_run(cmd, **kwargs):
    calls.append(cmd)
    return 0, "ok", STDERR_WITH_UUID

m._run = fake_run

# 2. Fresh first turn parses the UUID out of stderr and returns it.
agent = m.Agent(name="codex-partner", backend="codex", role="coder")
text, sid = m.call_codex(
    agent, "sys", "msg", cwd, "workspace-write", 60,
    dry=False, first_turn=True,
)
assert sid == UUID, sid
first = calls[-1]
assert "--sandbox" in first, first
assert "--cd" in first, first
assert first[-1].startswith("=== ROLE ==="), first  # prompt is last

# 3. With a real UUID stored, resume pins to it and drops --last/--sandbox/--cd.
agent.session_id = UUID
calls.clear()

def fake_run_resume(cmd, **kwargs):
    calls.append(cmd)
    return 0, "ok", STDERR_WITH_UUID  # codex re-emits same id on resume

m._run = fake_run_resume
m.call_codex(
    agent, "sys", "msg", cwd, "workspace-write", 60,
    dry=False, first_turn=False,
)
resume = calls[-1]
assert resume[:4] == ["codex", "exec", "resume", UUID], resume
assert "--last" not in resume, resume
assert "--sandbox" not in resume, resume
assert "--cd" not in resume, resume
assert resume[-1].startswith("=== ROLE ==="), resume  # options before prompt

# 4. The "codex-current" sentinel still routes through --last (legacy state).
agent.session_id = "codex-current"
calls.clear()
m.call_codex(
    agent, "sys", "msg", cwd, "workspace-write", 60,
    dry=False, first_turn=False,
)
legacy = calls[-1]
assert legacy[:4] == ["codex", "exec", "resume", "--last"], legacy
assert "--sandbox" not in legacy, legacy
assert "--cd" not in legacy, legacy

# 5. session_id=None plus first_turn=False (anomalous, but possible if state
#    was hand-edited) takes the safe path: fresh `codex exec` with --sandbox
#    and --cd, never an unanchored resume.
agent.session_id = None
calls.clear()
m.call_codex(
    agent, "sys", "msg", cwd, "workspace-write", 60,
    dry=False, first_turn=False,
)
fallback = calls[-1]
assert fallback[:2] == ["codex", "exec"], fallback
assert "resume" not in fallback, fallback
assert "--sandbox" in fallback, fallback

# 6. If stderr has no UUID, fresh turn returns the legacy sentinel so the
#    next turn still routes through --last instead of starting another
#    fresh codex exec.
def fake_run_no_uuid(cmd, **kwargs):
    calls.append(cmd)
    return 0, "ok", "no session info here\n"

m._run = fake_run_no_uuid
agent2 = m.Agent(name="codex-partner", backend="codex", role="coder")
_, sid2 = m.call_codex(
    agent2, "sys", "msg", cwd, "workspace-write", 60,
    dry=False, first_turn=True,
)
assert sid2 == "codex-current", sid2
PY

# Role-scoped codex-fast: when the run has a codex:planner alongside a
# codex:coder, fast must apply only to the coder. call_agent recomputes
# `fast = cfg.codex_fast and agent.role == "coder"` so the planner keeps
# its --reasoning effort and the coder gets pinned to low.
expect "codex-fast scoped to coder"          0 python3 - "$DUET_ABS" "$TMPD" <<'PY'
import importlib.util
import pathlib
import sys

duet_path = pathlib.Path(sys.argv[1])
cwd = pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("duet_under_test", duet_path)
m = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = m
spec.loader.exec_module(m)

calls = []
def fake_run(cmd, **kwargs):
    calls.append(cmd)
    return 0, "ok", ""
m._run = fake_run

planner = m.Agent(name="codex-lead", backend="codex", role="planner")
coder = m.Agent(name="codex-partner", backend="codex", role="coder")
cfg = m.DuetConfig(
    cwd=cwd,
    agents=[planner, coder],
    task="x",
    sandbox="workspace-write",
    permission_mode="acceptEdits",
    reasoning="high",
    codex_fast=True,
)
m.call_agent(planner, "msg", cfg, first_turn_for_agent=True)
planner_cmd = calls[-1]
# Planner: high effort, no fast summary, no fast pinning.
assert "model_reasoning_effort=high" in planner_cmd, planner_cmd
assert "model_reasoning_effort=low" not in planner_cmd, planner_cmd
assert "model_reasoning_summary=concise" not in planner_cmd, planner_cmd

m.call_agent(coder, "msg", cfg, first_turn_for_agent=True)
coder_cmd = calls[-1]
# Coder: pinned to low, concise summary on.
assert "model_reasoning_effort=low" in coder_cmd, coder_cmd
assert "model_reasoning_effort=high" not in coder_cmd, coder_cmd
assert "model_reasoning_summary=concise" in coder_cmd, coder_cmd
PY

# When --codex-fast is set but no codex:coder is in the run, duet must warn
# on stderr and treat the flag as a no-op (cfg.codex_fast cleared).
# Setup: --lead codex:planner --partner claude:coder --reasoning high.
# Expect: stderr contains the warning; codex planner dry-run output shows
# `reasoning=high`, NOT `reasoning=low`, and is NOT tagged ` fast`.
expect "codex-fast warns when no codex coder" 0 bash -c '
  out=$("$1" --dry-run --task "x" --cwd "$2" --reasoning high \
         --lead codex:planner --partner claude:coder --codex-fast 2>&1)
  echo "$out" | grep -q "WARNING: --codex-fast had no effect" \
    || { echo "missing WARNING in stderr"; echo "$out"; exit 1; }
  echo "$out" | grep -q "\[dry-run codex/codex-lead.*reasoning=high" \
    || { echo "codex-lead not at reasoning=high"; echo "$out"; exit 1; }
  echo "$out" | grep -E "\[dry-run codex/codex-lead.* fast" >/dev/null \
    && { echo "codex-lead got fast tag despite no coder"; echo "$out"; exit 1; }
  exit 0
' _ "$DUET_ABS" "$TMPD"

# When --codex-fast is set and there is a codex:coder plus a non-coder codex
# agent, duet prints a softer "note:" listing the non-coder. Setup:
# --lead codex:planner --partner codex:coder --reasoning high.
expect "codex-fast partial note" 0 bash -c '
  out=$("$1" --dry-run --task "x" --cwd "$2" --reasoning high \
         --lead codex:planner --partner codex:coder --codex-fast 2>&1)
  echo "$out" | grep -q "note: --codex-fast applies only to codex:coder" \
    || { echo "missing partial-scoping note"; echo "$out"; exit 1; }
  exit 0
' _ "$DUET_ABS" "$TMPD"

# C2 - foreign-cwd defaults
expect "foreign cwd creates .duet/runs/"     0 "$DUET" --task "x" --dry-run --cwd "$TMPD"
[[ -d "$TMPD/.duet/runs" ]] || { echo "FAIL: .duet/runs not created"; FAIL=$((FAIL+1)); }
[[ -f "$TMPD/.duet/runs/.gitignore" ]] || { echo "FAIL: .duet/runs/.gitignore missing"; FAIL=$((FAIL+1)); }
grep -qxF "*" "$TMPD/.duet/runs/.gitignore" || { echo "FAIL: .duet/runs/.gitignore missing '*'"; FAIL=$((FAIL+1)); }

expect "explicit --runs-dir overrides"       0 "$DUET" --task "x" --dry-run --cwd "$TMPD" --runs-dir "$TMPD/custom-runs"
[[ -d "$TMPD/custom-runs" ]] || { echo "FAIL: custom-runs not created"; FAIL=$((FAIL+1)); }

# Status check on a fresh dry-run dir (run is finished, so exit 0).
RUN=$(ls -1d "$TMPD/.duet/runs"/2*/ 2>/dev/null | head -1 || true)
[[ -n "$RUN" ]] && expect "status on dry-run dir"   0 "$DUET" --status "$RUN"

# `--list <DIR>` should show the dry-run we just produced.
expect "list runs from explicit path"        0 "$DUET" --list "$TMPD/.duet/runs"

# Home-index symlink: every run dir gets a sibling pointer at
# ~/.duet/runs/<cwd-slug>/<run_id> so `--list` and bare-id `--status`
# discover runs across every project. We use a fresh sub-cwd ($TMPD/proj)
# so the slug differs from $TMPD's own slug — exercises the foreign-cwd
# happy path, not the degenerate HOME==cwd case.
mkdir -p "$TMPD/proj"
expect "foreign cwd run registered"          0 "$DUET" --task "x" --dry-run --cwd "$TMPD/proj"
PROJ_RUN=$(ls -1d "$TMPD/proj/.duet/runs"/2*/ 2>/dev/null | head -1 || true)
PROJ_ID=$(basename "$PROJ_RUN")
HOME_LINK=$(find "$TMPD/.duet/runs" -mindepth 2 -maxdepth 2 -name "$PROJ_ID" -type l 2>/dev/null | head -1 || true)
[[ -n "$HOME_LINK" && -L "$HOME_LINK" ]] \
    || { echo "FAIL: home-index symlink not created for $PROJ_ID"; FAIL=$((FAIL+1)); }
[[ -d "$HOME_LINK" ]] \
    || { echo "FAIL: home-index symlink dangling: $HOME_LINK"; FAIL=$((FAIL+1)); }
# Bare-id `--status` should resolve via the home index from any cwd.
expect "status by bare id via home-index"    0 bash -c "cd '$TMPD' && '$DUET_ABS' --status '$PROJ_ID'"
# `--list` (no path) from a third cwd should still find the run via the
# home index — and the dedup logic in print_runs_list should not double-
# list it (cwd-relative path is empty here, only the home-index sees it).
expect "list finds run via home-index"       0 bash -c "cd '$TMPD' && '$DUET_ABS' --list"

# `--list /nonexistent` exits 0 with a stderr notice (no rows ≠ failure).
expect "list nonexistent path -> exit 0"     0 "$DUET" --list "$TMPD/no-such-runs-dir"

# `--status <bare-id>` resolves against default paths (./runs/, ./.duet/runs/).
# Stage a fake run under TMPD/runs/<id>/ and call --status with just the id.
FAKE_ID="20260507-100000"
mkdir -p "$TMPD/runs/$FAKE_ID"
cat > "$TMPD/runs/$FAKE_ID/state.json" <<JSON
{"task":"x","cwd":".","turns_used":1,"agents":[],"history":[],"finished_reason":"converged"}
JSON
# Use `bash -c` to put the `cd` inside the SUT (a child shell) instead of
# wrapping `expect` in a subshell — that would lose the PASS/FAIL counters.
expect "status by bare run id"            0 bash -c "cd '$TMPD' && '$DUET_ABS' --status '$FAKE_ID'"

# Bogus id → exit 3 with helpful "use --list" error.
expect "status nonexistent id -> exit 3"  3 bash -c "cd '$TMPD' && '$DUET_ABS' --status '99999999-999999'"

# state.json should record duet_pid for liveness checks during the run.
[[ -n "$RUN" ]] && grep -q '"duet_pid"' "$RUN/state.json" \
    || { echo "FAIL: duet_pid missing from dry-run state.json"; FAIL=$((FAIL+1)); }

# Synthetic mid-run state.json with a stale (or unrelated) duet_pid → exit 2.
# We use PID 1 (init) which is alive but whose cmdline does not contain
# 'duet.py', so _is_duet_process() should reject it.
SYNTH="$TMPD/synth-stale"
mkdir -p "$SYNTH"
cat > "$SYNTH/state.json" <<JSON
{"task":"x","cwd":".","turns_used":1,"agents":[],"history":[],"finished_reason":null,"duet_pid":1}
JSON
expect "stale duet_pid -> exit 2"             2 "$DUET" --status "$SYNTH"

# Synthetic mid-run state.json predating duet_pid → exit 2 with legacy
# fallback message.
SYNTH_OLD="$TMPD/synth-old"
mkdir -p "$SYNTH_OLD"
cat > "$SYNTH_OLD/state.json" <<JSON
{"task":"x","cwd":".","turns_used":1,"agents":[],"history":[],"finished_reason":null}
JSON
expect "no duet_pid (old run) -> exit 2"      2 "$DUET" --status "$SYNTH_OLD"

echo "---"; echo "smoke: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
