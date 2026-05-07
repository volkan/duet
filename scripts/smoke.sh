#!/usr/bin/env bash
# scripts/smoke.sh - exercise duet's CLI surface in dry-run.
set -euo pipefail
DUET=${DUET:-./duet.py}
TMPD=$(mktemp -d -t duet-smoke.XXXX)
TMPD_REAL=$(cd "$TMPD" && pwd -P)
trap 'rm -rf "$TMPD"' EXIT
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

# Status check on a fresh dry-run dir
RUN=$(ls -1d "$TMPD/.duet/runs"/2*/ 2>/dev/null | head -1 || true)
[[ -n "$RUN" ]] && expect "status on dry-run dir"   0 "$DUET" --status "$RUN"

echo "---"; echo "smoke: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
