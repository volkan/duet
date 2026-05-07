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

# C1 - task input variants
printf "fix typo\n" > "$TMPD/stdin-task.txt"
expect "task @-"                            0 "$DUET" --task @- --dry-run --cwd "$TMPD" < "$TMPD/stdin-task.txt"
echo "from file" > "$TMPD/t.txt"
expect "task @file"                          0 "$DUET" --task @"$TMPD/t.txt" --dry-run --cwd "$TMPD"
expect "task-from-cmd"                       0 "$DUET" --task-from-cmd 'echo hello' --dry-run --cwd "$TMPD"
expect "task-from-cmd cwd"                   0 "$DUET" --task-from-cmd "test \"\$(pwd -P)\" = \"$TMPD_REAL\" && echo cwd-ok" --dry-run --cwd "$TMPD"
expect "task literal still works"            0 "$DUET" --task "literal" --dry-run --cwd "$TMPD"
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
