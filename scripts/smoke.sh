#!/usr/bin/env bash
# scripts/smoke.sh - exercise duet's CLI surface in dry-run.
set -euo pipefail
# Anchor to THIS repo's duet.py via the script's own location, not cwd or PATH,
# so smoke always tests the repo code regardless of where it is invoked from
# and never silently falls back to a pipx-installed `duet`. Override with
# DUET=… only to deliberately point at a different build.
_SMOKE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DUET=${DUET:-"$_SMOKE_DIR/../duet.py"}
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
expect_stdout "runtime version"                  0 "duet " "$DUET" --version
printf "fix typo\n" > "$TMPD/stdin-task.txt"
expect "task @-"                            0 "$DUET" --task @- --dry-run --cwd "$TMPD" < "$TMPD/stdin-task.txt"
echo "from file" > "$TMPD/t.txt"
expect "task @file"                          0 "$DUET" --task @"$TMPD/t.txt" --dry-run --cwd "$TMPD"
expect_stdout "task-from-cmd preview"        0 "dry-run: task_from_cmd not executed" "$DUET" --task-from-cmd 'echo hello' --dry-run --cwd "$TMPD"
expect "task-from-cmd preview cannot write"  0 bash -c '
  "$1" --task-from-cmd "touch unexpected-kickoff; printf seed" --dry-run --cwd "$2" || exit
  test ! -e "$2/unexpected-kickoff"
' _ "$DUET_ABS" "$TMPD"
expect "task literal still works"            0 "$DUET" --task "literal" --dry-run --cwd "$TMPD"
CONTROL_DIR="$TMPD/control"
mkdir -p "$CONTROL_DIR"
RECIPE_INFO="$CONTROL_DIR/recipe-run.json"
expect "review recipe launch"                 0 "$DUET" --recipe review --task "x" \
  --no-worktree --dry-run --run-info-file "$RECIPE_INFO"
expect "run-info + status JSON contract"      0 python3 - "$DUET_ABS" "$RECIPE_INFO" <<'PY'
import json
import pathlib
import subprocess
import sys

duet, info_path = sys.argv[1:]
info = json.loads(pathlib.Path(info_path).read_text())
assert set(info) == {
    "schema_version", "kind", "duet_version", "run_id",
    "run_dir", "state_path", "pid",
}, info
assert info["schema_version"] == 1 and info["kind"] == "duet.run", info
assert pathlib.Path(info["run_dir"]).is_absolute(), info
state = json.loads(pathlib.Path(info["state_path"]).read_text())
claude_agents = [agent for agent in state["agents"] if agent["backend"] == "claude"]
assert claude_agents and all(agent["model"] == "sonnet" for agent in claude_agents), state
result = subprocess.run(
    [duet, "--status", info["run_dir"], "--json"],
    text=True, capture_output=True,
)
assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
status = json.loads(result.stdout)
assert status["schema_version"] == 1 and status["kind"] == "duet.status", status
assert status["health"] == "terminal" and status["phase"] == "finished", status
assert status["exit_code"] == 0, status
assert status["per_turn_timeout"] == 900, status
assert status["last_timeout"] is None, status
assert "tool_denials" in status and status["tool_denials"] is None, status
if status["active_turn"] is not None:
    assert "budget_seconds" in status["active_turn"], status
    assert "remaining_seconds" in status["active_turn"], status
PY
expect_stdout "reasoning xhigh accepted"     0 "reasoning=xhigh"    "$DUET" --task "x" --dry-run --cwd "$TMPD" --reasoning xhigh
expect_stdout "gemini dry-run accepted"      0 "dry-run gemini/gemini-partner" "$DUET" --task "x" --dry-run --cwd "$TMPD" --partner gemini:coder --turns 1
expect_stdout "gemini reasoning no effort"   0 "reasoning=max"      "$DUET" --task "x" --dry-run --cwd "$TMPD" --partner gemini:coder --turns 1 --reasoning max
expect_stdout "copilot dry-run accepted"     0 "dry-run copilot/copilot-partner" "$DUET" --task "x" --dry-run --cwd "$TMPD" --partner copilot:coder --turns 1
expect_stdout "copilot reasoning accepted"   0 "reasoning=max"      "$DUET" --task "x" --dry-run --cwd "$TMPD" --partner copilot:coder --turns 1 --reasoning max
expect_stdout "opencode dry-run accepted"    0 "dry-run opencode/opencode-partner" "$DUET" --task "x" --dry-run --cwd "$TMPD" --partner opencode:coder --turns 1
expect_stdout "opencode reasoning accepted"  0 "reasoning=max"      "$DUET" --task "x" --dry-run --cwd "$TMPD" --partner opencode:coder --turns 1 --reasoning max
expect "opencode v1 stdin preserves the probe options" 0 python3 - "$DUET_ABS" "$TMPD" <<'PY'
import json
import os
import pathlib
import subprocess
import sys

duet, tmp = sys.argv[1:]
root = pathlib.Path(tmp) / "opencode-stdin"
root.mkdir()
shim = root / "opencode"
shim.write_text(
    '#!/bin/sh\n'
    'case "$1" in\n'
    '  --version) printf "1.18.12\\n";;\n'
    '  debug) test "$2 $3" = "config --pure" || exit 1;\n'
    '         printf \'{"subagent_depth":0,"fixture":"mock-config-output"}\\n\';\n'
    '         printf "mock-config-stderr\\n" >&2;;\n'
    '  run) test "$7" = "--pure" && test "$#" = 7 || exit 1;\n'
    '       grep -q "fixture-message-tail$" || exit 1;\n'
    '       printf \'{"type":"text","sessionID":"ses_smoke","part":{"id":"p","type":"text","text":"opencode-stub-reply"}}\\n\';;\n'
    '  *) exit 1;;\n'
    'esac\n',
    encoding="utf-8",
)
shim.chmod(0o755)
config = root / "config.json"
config.write_text(json.dumps({
    "cwd": str(root), "max_turns": 1,
    "task": "x" * (64 * 1024) + "\nfixture-message-tail",
    "agents": [{"name": name, "backend": "opencode", "role": "reviewer",
                "extra_args": ["--pure"]} for name in ("lead", "partner")],
}), encoding="utf-8")
env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
           OPENCODE_CONFIG_CONTENT="{}")
result = subprocess.run([duet, "--config", str(config), "--no-metrics"], env=env,
                        text=True, capture_output=True, timeout=30)
assert result.returncode == 0, result.stderr
assert "opencode-stub-reply" in result.stdout
assert "mock-config-" not in result.stdout + result.stderr
PY
# --on-turn-timeout is a choices flag: bad values must die in argparse (rc=2).
expect "on-turn-timeout bad value"           2 "$DUET" --task "x" --dry-run --cwd "$TMPD" --on-turn-timeout sometimes
# Reasoning-scaled default per-turn timeout: without --timeout, max reasoning
# derives 1800s into state.json; an explicit --timeout beats derivation.
SCALED_RUNS="$TMPD/scaled-timeout-runs"
expect "reasoning-scaled timeout dry-run"    0 "$DUET" --dry-run --task "x" --cwd "$TMPD" --runs-dir "$SCALED_RUNS" --reasoning max
SCALED_RUN=$(ls -1d "$SCALED_RUNS"/2*/ 2>/dev/null | head -1 || true)
if [[ -n "$SCALED_RUN" ]]; then
  grep -q '"per_turn_timeout": 1800' "$SCALED_RUN/state.json" \
      || { echo "FAIL: reasoning max did not derive per_turn_timeout 1800"; FAIL=$((FAIL+1)); }
  grep -q '"on_turn_timeout": "stop"' "$SCALED_RUN/state.json" \
      || { echo "FAIL: on_turn_timeout default missing from state.json"; FAIL=$((FAIL+1)); }
else
  echo "FAIL: reasoning-scaled dry-run dir not created"; FAIL=$((FAIL+1))
fi
EXPLICIT_TIMEOUT_RUNS="$TMPD/explicit-timeout-runs"
expect "explicit timeout beats scaling"      0 "$DUET" --dry-run --task "x" --cwd "$TMPD" --runs-dir "$EXPLICIT_TIMEOUT_RUNS" --reasoning max --timeout 901
EXPLICIT_TIMEOUT_RUN=$(ls -1d "$EXPLICIT_TIMEOUT_RUNS"/2*/ 2>/dev/null | head -1 || true)
if [[ -n "$EXPLICIT_TIMEOUT_RUN" ]]; then
  grep -q '"per_turn_timeout": 901' "$EXPLICIT_TIMEOUT_RUN/state.json" \
      || { echo "FAIL: explicit --timeout was overridden by reasoning scaling"; FAIL=$((FAIL+1)); }
else
  echo "FAIL: explicit-timeout dry-run dir not created"; FAIL=$((FAIL+1))
fi
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
expect_stdout "resume-codex lead normalized" 0 "Turn 1 :: codex-partner" "$DUET" --resume-codex 019e16c2-635e-7802-83e8-400e93533d2f --lead codex:planner --partner claude:coder --task "x" --turns 1 --dry-run --cwd "$TMPD"
expect_stdout "review resume retains default kickoff" 0 "Turn 1 :: codex-partner" "$DUET" --recipe review --resume-codex 019e16c2-635e-7802-83e8-400e93533d2f --no-recap --no-worktree --turns 1 --dry-run --cwd "$TMPD"
expect_stdout "codex-review resume retains reviewer first" 0 "Turn 1 :: codex-lead (codex/reviewer)" "$DUET" --recipe codex-review --resume-codex 019e16c2-635e-7802-83e8-400e93533d2f --no-recap --no-worktree --turns 1 --dry-run --cwd "$TMPD"
expect_stdout "codex-review explicit resume handoff" 0 "Turn 1 :: codex-partner (codex/coder)" "$DUET" --recipe codex-review --resume-codex 019e16c2-635e-7802-83e8-400e93533d2f --task "x" --no-recap --no-worktree --turns 1 --dry-run --cwd "$TMPD"
expect_stdout "resume-claude partner moved"  0 "Turn 1 :: codex-partner" "$DUET" --resume-claude claude-sid --lead codex:planner --partner claude:coder --task "x" --turns 1 --dry-run --cwd "$TMPD"
expect_stdout "resume-claude keeps claude partner" 0 "Turn 1 :: claude-partner (claude/reviewer)" "$DUET" --resume-claude claude-sid --lead claude:planner --partner claude:reviewer --task "x" --turns 1 --dry-run --cwd "$TMPD"
expect "same-backend codex dry-run slots"   0 bash -c '
  out=$("$1" --task "x" --turns 2 --dry-run --cwd "$2" \
         --lead codex:planner --partner codex:coder)
  echo "$out" | grep -q "Turn 1 :: codex-partner" \
    || { echo "missing codex partner turn"; echo "$out"; exit 1; }
  echo "$out" | grep -q "Turn 2 :: codex-lead" \
    || { echo "missing codex lead turn"; echo "$out"; exit 1; }
  exit 0
' _ "$DUET_ABS" "$TMPD"
expect "codex-review starts reviewer and preserves model slots" 0 bash -c '
  out=$("$1" --recipe codex-review --dry-run --no-recap --no-worktree \
         --turns 2 --cwd "$2" --lead-model reviewer-model --partner-model coder-model)
  echo "$out" | grep -q "Turn 1 :: codex-lead (codex/reviewer)" \
    || { echo "missing initial Codex review"; echo "$out"; exit 1; }
  echo "$out" | grep -q "Turn 2 :: codex-partner (codex/coder)" \
    || { echo "missing Codex implementation turn"; echo "$out"; exit 1; }
  if echo "$out" | grep -q "claude -p"; then
    echo "unexpected Claude kickoff"; exit 1
  fi
  exit 0
' _ "$DUET_ABS" "$TMPD"
expect "same-backend claude dry-run slots"   0 bash -c '
  out=$("$1" --task "x" --turns 2 --dry-run --cwd "$2" \
         --lead claude:planner --partner claude:reviewer)
  echo "$out" | grep -q "Turn 1 :: claude-partner" \
    || { echo "missing claude partner turn"; echo "$out"; exit 1; }
  echo "$out" | grep -q "Turn 2 :: claude-lead" \
    || { echo "missing claude lead turn"; echo "$out"; exit 1; }
  exit 0
' _ "$DUET_ABS" "$TMPD"
expect "same-backend copilot dry-run slots" 0 bash -c '
  out=$("$1" --task "x" --turns 2 --dry-run --cwd "$2" \
         --lead copilot:planner --partner copilot:coder)
  echo "$out" | grep -q "Turn 1 :: copilot-partner" \
    || { echo "missing copilot partner turn"; echo "$out"; exit 1; }
  echo "$out" | grep -q "Turn 2 :: copilot-lead" \
    || { echo "missing copilot lead turn"; echo "$out"; exit 1; }
  exit 0
' _ "$DUET_ABS" "$TMPD"
expect "same-backend opencode dry-run slots" 0 bash -c '
  out=$("$1" --task "x" --turns 2 --dry-run --cwd "$2" \
         --lead opencode:planner --partner opencode:coder)
  echo "$out" | grep -q "Turn 1 :: opencode-partner" \
    || { echo "missing opencode partner turn"; echo "$out"; exit 1; }
  echo "$out" | grep -q "Turn 2 :: opencode-lead" \
    || { echo "missing opencode lead turn"; echo "$out"; exit 1; }
  exit 0
' _ "$DUET_ABS" "$TMPD"
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
KICKOFF_INFO="$CONTROL_DIR/kickoff-failure.json"
expect "from-cmd nonzero -> durable error"   1 "$DUET" --task-from-cmd 'false' \
  --no-metrics --cwd "$TMPD" --run-info-file "$KICKOFF_INFO"
expect "from-cmd failure persisted"          0 python3 - "$KICKOFF_INFO" <<'PY'
import json
import pathlib
import sys

info = json.loads(pathlib.Path(sys.argv[1]).read_text())
state = json.loads(pathlib.Path(info["state_path"]).read_text())
assert state["finished_reason"] == "kickoff_error", state
assert state["phase"] == "finished", state
PY
expect "from-cmd empty stdout -> error"      1 "$DUET" --task-from-cmd 'true' --no-metrics --cwd "$TMPD"
echo keep > "$CONTROL_DIR/existing.json"
expect "run-info refuses overwrite"          2 "$DUET" --task "x" --dry-run \
  --cwd "$TMPD" --run-info-file "$CONTROL_DIR/existing.json"
STRICT_ROOT="$TMPD/not-a-repo"
mkdir -p "$STRICT_ROOT"
STRICT_INFO="$CONTROL_DIR/strict-failure.json"
expect "strict worktree fails closed"        1 "$DUET" --task "x" --dry-run \
  --cwd "$STRICT_ROOT" --worktree --require-worktree \
  --run-info-file "$STRICT_INFO"
expect "strict worktree failure persisted"   0 python3 - "$STRICT_INFO" <<'PY'
import json
import pathlib
import sys

info = json.loads(pathlib.Path(sys.argv[1]).read_text())
state = json.loads(pathlib.Path(info["state_path"]).read_text())
assert state["finished_reason"] == "setup_error", state
assert state["phase"] == "finished", state
PY
expect "opencode setup failure CLI matrix"   0 python3 - "$DUET_ABS" "$TMPD" <<'PY'
import json
import os
import pathlib
import subprocess
import sys

duet = pathlib.Path(sys.argv[1])
root = pathlib.Path(sys.argv[2]) / "opencode-setup-failure"
root.mkdir()
repo = root / "repo"
subprocess.run(["git", "init", "-b", "main", str(repo)],
               check=True, capture_output=True, text=True)
subprocess.run([
    "git", "-C", str(repo), "-c", "user.name=Test",
    "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
    "commit", "--allow-empty", "--no-gpg-sign", "-m", "fixture",
], check=True, capture_output=True, text=True)
bin_dir = root / "bin"
bin_dir.mkdir()
shim = bin_dir / "opencode"
shim.write_text(
    '#!/bin/sh\n'
    'test "$1" = --version || exit 99\n'
    'printf "probe\\n" >> "$DUET_TEST_OPENCODE_PROBE"\n'
    'exit 71\n'
)
shim.chmod(0o755)
env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
for reuse in (False, True):
    for strict in (False, True):
        case = f"{'reuse' if reuse else 'create'}-{'strict' if strict else 'ordinary'}"
        info = root / f"{case}.json"
        probe = root / f"{case}.probe"
        env["DUET_TEST_OPENCODE_PROBE"] = str(probe)
        command = [
            sys.executable, str(duet), "--task", "x", "--dry-run", "--turns", "1",
            "--lead", "codex:reviewer", "--partner", "opencode:coder",
            "--cwd", str(repo), "--runs-dir", str(root / case),
            "--run-info-file", str(info), "--no-metrics",
        ]
        command += ["--worktree-path", str(repo)] if reuse else ["--worktree"]
        if strict:
            command += ["--require-worktree"]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == int(strict), (case, result.returncode, result.stdout, result.stderr)
        assert "Traceback" not in result.stderr, (case, result.stderr)
        assert probe.read_text() == "probe\n", case
        failure = "opencode --version failed (exit 71)"
        fallback = "Falling back to same-repo mode."
        if strict:
            assert fallback not in result.stderr, (case, result.stderr)
        else:
            assert "[duet] WARNING:" in result.stderr, (case, result.stderr)
            assert failure in result.stderr and fallback in result.stderr, (case, result.stderr)
        launch = json.loads(info.read_text())
        state = json.loads(pathlib.Path(launch["state_path"]).read_text())
        reason = "setup_error" if strict else "max_turns"
        assert state["phase"] == "finished" and state["finished_reason"] == reason, (case, state)
        assert state["worktree"] is None, (case, state)
        assert state["turns_used"] == (0 if strict else 1), (case, state)
        if strict:
            assert failure in state["error"], (case, state)
        status = subprocess.run(
            [sys.executable, str(duet), "--status", launch["run_dir"], "--json"],
            env=env, capture_output=True, text=True, timeout=30,
        )
        assert status.returncode == 0, (case, status.returncode, status.stdout, status.stderr)
        snapshot = json.loads(status.stdout)
        assert snapshot["health"] == "terminal" and snapshot["exit_code"] == 0, (case, snapshot)
        assert snapshot["phase"] == "finished" and snapshot["finished_reason"] == reason, (case, snapshot)
PY
expect "opencode nested clone and shell hints" 0 python3 - "$DUET_ABS" "$TMPD" <<'PY'
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys

duet_path = pathlib.Path(sys.argv[1])
root = pathlib.Path(sys.argv[2]) / "clone shell '$; fixture"
root.mkdir()
repo = root / "source '$; repo"
subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
(repo / "tracked.txt").write_text("host content\n")
subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
subprocess.run([
    "git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
    "-c", "core.hooksPath=/dev/null", "commit", "--no-gpg-sign", "-m", "fixture",
], check=True, capture_output=True)
nested = repo / "nested cwd"
nested.mkdir()
spec = importlib.util.spec_from_file_location("duet_under_test", duet_path)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
m._OPENCODE_CLI_MAJOR = 2
cfg = m.DuetConfig(
    cwd=nested, task="x", dry_run=True, max_turns=1, metrics_enabled=False,
    agents=[m.Agent("lead", "codex"), m.Agent("partner", "opencode")],
    worktree=True, require_worktree=True, worktree_root=root / "peer '$; clones",
    runs_dir=root / "runs",
)
with contextlib.redirect_stdout(io.StringIO()):
    state = m.run_duet(cfg)
clone = pathlib.Path(state["worktree"])
assert state["finished_reason"] == "max_turns", state
assert cfg.cwd == nested and cfg.agents[1].cwd_override == clone
assert (clone / "tracked.txt").read_text() == "host content\n"
assert state["worktree_kind"] == "clone" and state["owned_worktree_path"] == str(clone)
(clone / "tracked.txt").write_text("peer content\n")
assert (repo / "tracked.txt").read_text() == "host content\n"
status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                        check=True, capture_output=True, text=True)
assert status.stdout == "", status.stdout
bin_dir = root / "bin"
bin_dir.mkdir()
log = root / "argv.jsonl"
shim = (f"#!{sys.executable}\nimport json, os, pathlib, sys\n"
        "with open(os.environ['DUET_SMOKE_ARGV'], 'a') as out:\n"
        "    out.write(json.dumps([pathlib.Path(sys.argv[0]).name, *sys.argv[1:]]) + '\\n')\n")
for name in ("git", "rm"):
    target = bin_dir / name
    target.write_text(shim)
    target.chmod(0o755)
env = dict(os.environ, PATH=str(bin_dir), DUET_SMOKE_ARGV=str(log))
branch = "duet/quote'$;branch"
for kind in ("clone", "linked"):
    cfg._worktree_kind = kind
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        m._print_worktree_notice(cfg, clone, branch)
    commands = [line.split(":", 1)[1].strip() for line in output.getvalue().splitlines()[1:]]
    log.write_text("")
    for command in commands:
        subprocess.run(["/bin/sh", "-c", command], env=env, check=True, capture_output=True)
    actual = [json.loads(line) for line in log.read_text().splitlines()]
    expected = [["git", "-C", str(clone), "diff", "HEAD"]]
    if kind == "clone":
        expected += [["git", "-C", str(nested), "fetch", str(clone), branch],
                     ["git", "-C", str(nested), "merge", "FETCH_HEAD"], ["rm", "-rf", "--", str(clone)]]
    else:
        expected += [["git", "-C", str(nested), "merge", branch],
                     ["git", "-C", str(nested), "worktree", "remove", str(clone)],
                     ["git", "-C", str(nested), "branch", "-D", branch]]
    assert actual == expected, (kind, actual, expected)
assert clone.is_dir()
PY
expect "opencode guide shell command executes" 0 python3 - "$DUET_ABS" "$TMPD" <<'PY'
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

duet_path = pathlib.Path(sys.argv[1])
guide = duet_path.parent / "docs" / "OPENCODE_PLUGIN.md"
example = next(line for line in guide.read_text().splitlines() if line.startswith("duet --task-from-cmd "))
root = pathlib.Path(sys.argv[2]) / "guide-shell"
root.mkdir()
bin_dir = root / "bin"
bin_dir.mkdir()
args_path = root / "args.json"
shim = bin_dir / "duet"
shim.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                "with open(os.environ['DUET_SMOKE_ARGS'], 'w') as out:\n"
                "    json.dump(sys.argv[1:], out)\n")
shim.chmod(0o755)
npm = bin_dir / "npm"
npm.write_text('#!/bin/sh\ntest "$1" = test || exit 7\nprintf "test stdout\\n"\nprintf "test stderr\\n" >&2\n')
npm.chmod(0o755)
env = dict(os.environ, PATH=str(bin_dir), DUET_SMOKE_ARGS=str(args_path))
subprocess.run(["/bin/sh", "-c", example], cwd=root, env=env, check=True, capture_output=True)
args = json.loads(args_path.read_text())
assert args == ["--task-from-cmd", "npm test 2>&1", "--turns", "4"], args
spec = importlib.util.spec_from_file_location("duet_under_test", duet_path)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
assert m.run_task_from_cmd(args[1], root, 10, root) == "test stdout\ntest stderr\n"
PY
ZCODE_FAKE_BIN="$TMPD/zcode-wrapper/a/b/c/d/bin"
mkdir -p "$ZCODE_FAKE_BIN" "$TMPD/zcode-target"
printf '#!/bin/sh\nexit 99\n' > "$ZCODE_FAKE_BIN/zcode"
chmod +x "$ZCODE_FAKE_BIN/zcode"
ZCODE_CHECK_ARGS=(--lead zcode:reviewer --lead-model account:zai-individual-coding-plan/GLM-5.3
  --partner zcode:reviewer --partner-model account:zai-individual-coding-plan/GLM-5.3
  --task "read only" --no-metrics --cwd "$TMPD/zcode-target")
expect "zcode launch check fails closed"     1 env -u ZCODE_BUILTIN_PROVIDER_CONFIG_FILE PATH="$ZCODE_FAKE_BIN:$PATH" \
  "$DUET" "${ZCODE_CHECK_ARGS[@]}" --run-info-file "$CONTROL_DIR/zcode-launch-check.json"
expect "zcode launch check persisted"        0 python3 - "$CONTROL_DIR/zcode-launch-check.json" <<'PY'
import json
import pathlib
import sys

info = json.loads(pathlib.Path(sys.argv[1]).read_text())
state = json.loads(pathlib.Path(info["state_path"]).read_text())
assert state["finished_reason"] == "setup_error", state
assert state["phase"] == "finished", state
assert state["turns_used"] == 0, state
assert state["history"] == [], state
assert state["error"].startswith("ZCode launch check failed for agent "), state
assert "PATH ZCode CLI's catalog" in state["error"], state
PY
expect_stdout "zcode dry run skips launch check" 0 "dry-run zcode/" \
  env -u ZCODE_BUILTIN_PROVIDER_CONFIG_FILE PATH="$ZCODE_FAKE_BIN:$PATH" \
  "$DUET" "${ZCODE_CHECK_ARGS[@]}" --dry-run
cat > "$TMPD/cfg-dupe-agents.json" <<EOF
{"cwd":"$TMPD","dry_run":true,"task":"x","agents":[{"name":"same","backend":"codex","role":"planner"},{"name":"same","backend":"codex","role":"coder"}]}
EOF
expect "duplicate agent names -> error"      2 "$DUET" --config "$TMPD/cfg-dupe-agents.json"
cat > "$TMPD/cfg-bad-worktree-for.json" <<EOF
{"cwd":"$TMPD","dry_run":true,"task":"x","worktree_for":"nonexistent-agent","agents":[{"name":"codex-lead","backend":"codex","role":"planner"},{"name":"codex-partner","backend":"codex","role":"coder"}]}
EOF
expect "bad worktree_for -> error"           2 "$DUET" --config "$TMPD/cfg-bad-worktree-for.json"

# Config key support (JSON keeps the smoke stdlib-only).
cat > "$TMPD/cfg.json" <<JSON
{"cwd":"$TMPD","dry_run":true,"task_from_cmd":"echo config","agents":[{"name":"claude-lead","backend":"claude","role":"planner"},{"name":"codex-partner","backend":"codex","role":"coder"}]}
JSON
expect "config task_from_cmd"                0 "$DUET" --config "$TMPD/cfg.json"
expect "config cli --runs-dir override"      0 "$DUET" --config "$TMPD/cfg.json" --runs-dir "$TMPD/config-runs"
[[ -d "$TMPD/config-runs" ]] || { echo "FAIL: config-runs not created"; FAIL=$((FAIL+1)); }
CFG_FALLBACK_RUNS="$TMPD/config-fallback-runs"
expect "config cli fallback knobs"          0 "$DUET" --config "$TMPD/cfg.json" \
  --runs-dir "$CFG_FALLBACK_RUNS" --sentinel CLI_DONE --timeout 17 \
  --sandbox read-only --permission-mode plan --add-dir "$TMPD/config-extra"
CFG_FALLBACK_RUN=$(ls -1d "$CFG_FALLBACK_RUNS"/2*/ 2>/dev/null | head -1 || true)
if [[ -n "$CFG_FALLBACK_RUN" ]]; then
  grep -q '"sentinel": "CLI_DONE"' "$CFG_FALLBACK_RUN/state.json" \
    || { echo "FAIL: config CLI sentinel fallback missing"; FAIL=$((FAIL+1)); }
  grep -q '"per_turn_timeout": 17' "$CFG_FALLBACK_RUN/state.json" \
    || { echo "FAIL: config CLI timeout fallback missing"; FAIL=$((FAIL+1)); }
  grep -q '"sandbox": "read-only"' "$CFG_FALLBACK_RUN/state.json" \
    || { echo "FAIL: config CLI sandbox fallback missing"; FAIL=$((FAIL+1)); }
  grep -q '"permission_mode": "plan"' "$CFG_FALLBACK_RUN/state.json" \
    || { echo "FAIL: config CLI permission fallback missing"; FAIL=$((FAIL+1)); }
  grep -q 'config-extra' "$CFG_FALLBACK_RUN/state.json" \
    || { echo "FAIL: config CLI add-dir fallback missing"; FAIL=$((FAIL+1)); }
else
  echo "FAIL: config fallback run not created"; FAIL=$((FAIL+1))
fi

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

expect_stdout "one proposal not enough"      0 "reason=max_turns" "$DUET" --dry-run --turns 1 --task "x" --cwd "$TMPD"
expect_stdout "two proposals converge"       0 "reason=converged" "$DUET" --dry-run --turns 2 --task "x" --cwd "$TMPD"

VERIFY_CLI_RUNS="$TMPD/verify-cli-runs"
expect "verify-cmd cli parsed"              0 "$DUET" --dry-run --turns 1 --task "x" --cwd "$TMPD" --runs-dir "$VERIFY_CLI_RUNS" --verify-cmd "true"
VERIFY_CLI_RUN=$(ls -1d "$VERIFY_CLI_RUNS"/2*/ 2>/dev/null | head -1 || true)
[[ -n "$VERIFY_CLI_RUN" ]] && grep -q '"verify_cmd": "true"' "$VERIFY_CLI_RUN/state.json" \
    || { echo "FAIL: verify_cmd missing from CLI state.json"; FAIL=$((FAIL+1)); }

cat > "$TMPD/cfg-verify.json" <<JSON
{"cwd":"$TMPD","dry_run":true,"task":"x","max_turns":1,"verify_cmd":"true","agents":[{"name":"claude-lead","backend":"claude","role":"planner"},{"name":"codex-partner","backend":"codex","role":"coder"}]}
JSON
VERIFY_CFG_RUNS="$TMPD/verify-cfg-runs"
expect "verify_cmd yaml key"                0 "$DUET" --config "$TMPD/cfg-verify.json" --runs-dir "$VERIFY_CFG_RUNS"
VERIFY_CFG_RUN=$(ls -1d "$VERIFY_CFG_RUNS"/2*/ 2>/dev/null | head -1 || true)
[[ -n "$VERIFY_CFG_RUN" ]] && grep -q '"verify_cmd": "true"' "$VERIFY_CFG_RUN/state.json" \
    || { echo "FAIL: verify_cmd missing from config state.json"; FAIL=$((FAIL+1)); }

cat > "$TMPD/cfg-verify-override.json" <<JSON
{"cwd":"$TMPD","dry_run":true,"task":"x","max_turns":2,"verify_cmd":"false","agents":[{"name":"claude-lead","backend":"claude","role":"planner"},{"name":"codex-partner","backend":"codex","role":"coder"}]}
JSON
expect_stdout "verify_cmd cli overrides yaml" 0 "reason=converged" "$DUET" --config "$TMPD/cfg-verify-override.json" --verify-cmd "true"

expect_stdout "verify success allows convergence" 0 "reason=converged" "$DUET" --dry-run --turns 2 --task "x" --cwd "$TMPD" --verify-cmd "true"
VERIFY_DRY_MARKER="$TMPD/verify-dry-run-marker"
expect_stdout "dry-run skips verify command" 0 "reason=converged" "$DUET" --dry-run --turns 2 --task "x" --cwd "$TMPD" --verify-cmd "touch '$VERIFY_DRY_MARKER'; false"
[[ ! -e "$VERIFY_DRY_MARKER" ]] \
    || { echo "FAIL: dry-run executed verify command"; FAIL=$((FAIL+1)); }

# Codex fast mode: tag must show in dry-run codex output, and reasoning
# must be pinned to `low` even when --reasoning says otherwise.
expect_stdout "codex-fast tags dry-run"      0 "fast"               "$DUET" --dry-run --task "x" --cwd "$TMPD" --codex-fast
expect_stdout "codex-fast pins low"          0 "reasoning=low"      "$DUET" --dry-run --task "x" --cwd "$TMPD" --reasoning high --codex-fast
# YAML key path: codex_fast: true should produce the same tag.
cat > "$TMPD/cfg-fast.json" <<JSON
{"cwd":"$TMPD","dry_run":true,"task":"x","codex_fast":true,"agents":[{"name":"claude-lead","backend":"claude","role":"planner"},{"name":"codex-partner","backend":"codex","role":"coder"}]}
JSON
expect_stdout "codex_fast yaml key"          0 "fast"               "$DUET" --config "$TMPD/cfg-fast.json"
expect "no-codex-fast overrides yaml"       0 bash -c '
  out=$("$1" --config "$2" --no-codex-fast)
  echo "$out" | grep -q "\[dry-run codex/.* fast" \
    && { echo "codex fast remained enabled"; echo "$out"; exit 1; }
  exit 0
' _ "$DUET_ABS" "$TMPD/cfg-fast.json"

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

FINISHED_SYNTH="$TMPD/finished-synth"
mkdir -p "$FINISHED_SYNTH/20260507-100001" "$FINISHED_SYNTH/20260507-100002" "$FINISHED_SYNTH/20260507-100003"
cat > "$FINISHED_SYNTH/20260507-100001/state.json" <<JSON
{"task":"x","cwd":"$TMPD","turns_used":1,"agents":[],"history":[],"finished_reason":"timeout"}
JSON
cat > "$FINISHED_SYNTH/20260507-100002/state.json" <<JSON
{"task":"x","cwd":"$TMPD","turns_used":1,"agents":[],"history":[],"finished_reason":"agent_error"}
JSON
cat > "$FINISHED_SYNTH/20260507-100003/state.json" <<JSON
{"task":"x","cwd":"$TMPD","turns_used":1,"agents":[],"history":[],"finished_reason":"force_stop"}
JSON
expect_stdout "status timeout distinct"      0 "finished_reason: 'timeout'" "$DUET" --status "$FINISHED_SYNTH/20260507-100001"
expect_stdout "status agent_error distinct"  0 "finished_reason: 'agent_error'" "$DUET" --status "$FINISHED_SYNTH/20260507-100002"
expect_stdout "status force_stop preserved"  0 "finished_reason: 'force_stop'" "$DUET" --status "$FINISHED_SYNTH/20260507-100003"
expect "list failure labels distinct"        0 bash -c '
  out=$("$1" --list "$2")
  echo "$out" | grep -q "timeout" \
    || { echo "missing timeout row"; echo "$out"; exit 1; }
  echo "$out" | grep -q "agent_error" \
    || { echo "missing agent_error row"; echo "$out"; exit 1; }
  echo "$out" | grep -q "force_stop" \
    || { echo "missing force_stop row"; echo "$out"; exit 1; }
  exit 0
' _ "$DUET_ABS" "$FINISHED_SYNTH"

# Bogus id → exit 3 with helpful "use --list" error.
expect "status nonexistent id -> exit 3"  3 bash -c "cd '$TMPD' && '$DUET_ABS' --status '99999999-999999'"
expect "stop nonexistent id -> exit 3"    3 bash -c "cd '$TMPD' && '$DUET_ABS' --stop '99999999-999999'"
expect "immediate requires stop"          2 "$DUET" --immediate
expect "stop terminal run -> exit 2"      2 "$DUET" --stop "$FINISHED_SYNTH/20260507-100003"

# state.json should record duet_pid for liveness checks during the run.
[[ -n "$RUN" ]] && grep -q '"duet_pid"' "$RUN/state.json" \
    || { echo "FAIL: duet_pid missing from dry-run state.json"; FAIL=$((FAIL+1)); }
[[ -n "$RUN" ]] && grep -q '"duet_process_start"' "$RUN/state.json" \
    || { echo "FAIL: duet_process_start missing from dry-run state.json"; FAIL=$((FAIL+1)); }

REVISION_CHANGED_RUN="$TMPD/revision-changed"
mkdir -p "$REVISION_CHANGED_RUN"
cat > "$REVISION_CHANGED_RUN/state.json" <<'JSON'
{"phase":"finished","turns_used":2,"agents":[],"history":[],"finished_reason":"revision_changed"}
JSON
expect_stdout "revision changed is a terminal status" 0 '"finished_reason": "revision_changed"' \
  "$DUET" --status "$REVISION_CHANGED_RUN" --json

# Synthetic mid-run state.json with a stale (or unrelated) duet_pid → exit 2.
# We use PID 1 (init) which is alive but whose cmdline does not contain
# 'duet.py', so _is_duet_process() should reject it.
SYNTH="$TMPD/synth-stale"
mkdir -p "$SYNTH"
cat > "$SYNTH/state.json" <<JSON
{"task":"x","cwd":".","turns_used":1,"agents":[],"history":[],"finished_reason":null,"duet_pid":1}
JSON
expect "stale duet_pid -> exit 2"             2 "$DUET" --status "$SYNTH"

BAD_PID_ROOT="$TMPD/bad-pid-runs"
SYNTH_BAD_PID="$BAD_PID_ROOT/20260507-140001"
mkdir -p "$SYNTH_BAD_PID"
cat > "$SYNTH_BAD_PID/state.json" <<JSON
{"task":"x","cwd":".","turns_used":1,"agents":[],"history":[],"finished_reason":null,"duet_pid":"not-a-pid"}
JSON
expect "invalid duet_pid -> exit 2"           2 "$DUET" --status "$SYNTH_BAD_PID"
expect_stdout "list invalid duet_pid"         0 "invalid pid" "$DUET" --list "$BAD_PID_ROOT"

# Synthetic mid-run state.json predating duet_pid → exit 2 with legacy
# fallback message.
SYNTH_OLD="$TMPD/synth-old"
mkdir -p "$SYNTH_OLD"
cat > "$SYNTH_OLD/state.json" <<JSON
{"task":"x","cwd":".","turns_used":1,"agents":[],"history":[],"finished_reason":null}
JSON
expect "no duet_pid (old run) -> exit 2"      2 "$DUET" --status "$SYNTH_OLD"

# Central stats exercise the actual CLI and survive deletion of raw artifacts.
expect "central metrics CLI durability" 0 python3 - "$DUET_ABS" "$TMPD" <<'PY'
import json
import os
import pathlib
import shutil
import subprocess
import sys

duet, raw = sys.argv[1:]
root = pathlib.Path(raw)
env = dict(os.environ, DUET_METRICS="1")
def command(*args):
    result = subprocess.run([duet, *args], env=env, text=True, capture_output=True)
    assert result.returncode == 0, (result.returncode, result.stderr)
    return result.stdout
def stats(*args):
    return json.loads(command("--stats", "--json", *args))
before = stats()
run_root = root / "metrics-smoke"
command("--task", "private-metrics-task", "--dry-run", "--recap", "--cwd", raw,
        "--runs-dir", str(run_root))
source_run = next(run_root.glob("*/state.json")).parent
after = stats()
assert after["records"]["dry_run"] == before["records"]["dry_run"] + 1
assert after["records"]["performance_records"] == before["records"]["performance_records"]
assert "private-metrics-task" not in json.dumps(after)
refreshed = stats("--refresh", str(run_root))
assert refreshed == after
command("--task", "disabled", "--dry-run", "--recap", "--cwd", raw,
        "--runs-dir", str(run_root), "--no-metrics")
assert stats() == after
continued_info = root / "metrics-continued.json"
continued = subprocess.run(
    [duet, "--continue", str(source_run), "--dry-run", "--recap",
     "--runs-dir", str(run_root), "--run-info-file", str(continued_info)],
    env=dict(env, DUET_METRICS="0"), text=True, capture_output=True,
)
assert continued.returncode == 0, continued.stderr
continued_state = pathlib.Path(json.loads(continued_info.read_text())["state_path"])
assert json.loads(continued_state.read_text())["metrics_enabled"] is False
assert stats() == after
shutil.rmtree(run_root)
assert stats() == after
PY

# Malformed dates and extreme finite numbers must still produce valid CLI JSON.
expect "central metrics numeric boundary recovery" 0 python3 - "$DUET_ABS" "$TMPD" <<'PY'
import json
import os
import pathlib
import subprocess
import sys

duet, raw = sys.argv[1:]
root = pathlib.Path(raw) / "metrics-boundaries"
env = dict(os.environ, HOME=str(root / "home"))
originals = {}
for number, timestamp in enumerate(("0001-01-01T00:00:00+23:00",
                                    "9999-12-31T23:59:59-23:00"), start=1):
    source = root / "source" / str(number) / "state.json"
    source.parent.mkdir(parents=True)
    profiles = [{"backend": "claude", "role": "planner"},
                {"backend": "codex", "role": "coder"}]
    state = {
        "agents": [dict(name=slot, **profile)
                   for slot, profile in zip(("lead", "partner"), profiles)],
        "history": [{"turn": 1, "agent": "lead", "elapsed_s": 1e308,
                     "metrics": {"usage_scope": "invocation", "cost_usd": 1e308}}],
        "dry_run": False, "phase": "finished", "finished_reason": "max_turns",
        "metrics": {"id": f"00000000-0000-4000-8000-{number:012d}",
                    "project_id": "a" * 64, "agents": profiles,
                    "started_at": timestamp, "updated_at": timestamp,
                    "wall_elapsed_s": 1e308},
    }
    originals[source] = json.dumps(state)
    source.write_text(originals[source], encoding="utf-8")

def command(*args):
    result = subprocess.run([duet, "--stats", *args], env=env,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return result.stdout

def reject_constant(token):
    raise ValueError(f"non-JSON numeric constant: {token}")

report = json.loads(command("--refresh", str(root / "source"), "--json"),
                    parse_constant=reject_constant)
assert report["records"]["performance_records"] == 2
assert report["wall_elapsed_s"]["median"] == 1e308
assert report["agent_groups"][0]["reported_cost_usd"]["total"] is None
assert "provider-reported cost: unknown (overflow)" in command()
for snapshot in (root / "home" / ".duet" / "metrics" / "runs").glob("*.json"):
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    assert data["started_at"] is None and data["updated_at"] is None
assert all(path.read_text(encoding="utf-8") == text for path, text in originals.items())
PY

expect "report needs readable state" 3 "$DUET" --report "$TMPD/no-finding-run"
expect "resolve requires continuation" 2 "$DUET" --resolve L1
expect "finding report continuation and feedback CLI" 0 python3 - "$DUET_ABS" "$TMPD" <<'PY'
import json
import os
import pathlib
import shlex
import subprocess
import sys

duet, raw = sys.argv[1:]
root = pathlib.Path(raw) / "finding-workflow"
binary = root / "bin" / "opencode"
binary.parent.mkdir(parents=True)
binary.write_text('''#!/usr/bin/env python3
import json, sys, uuid
if "--version" in sys.argv:
    print("opencode v2.2.5")
elif sys.argv[1:3] == ["debug", "config"]:
    print(json.dumps([{"info": {"experimental": {"subagent_depth": 0}, "permissions": [{"action": "subagent", "resource": "*", "effect": "deny"}]}}]))
else:
    items = [{"id":"L1","claim":"private-claim-marker","disposition":"unresolved",
              "evidence":[],"objection":"Missing requirement"}]
    reply = "```duet-findings\\n" + json.dumps({"findings":items}) + "\\n```"
    reply += "\\nLGTM rationale: this bounded review records the missing requirement explicitly.\\n<<<LGTM>>>"
    sid = sys.argv[sys.argv.index("-s")+1] if "-s" in sys.argv else str(uuid.uuid4())
    print(json.dumps({"type":"text","sessionID":sid,"part":{"id":"reply","type":"text","text":reply}}))
''', encoding="utf-8")
binary.chmod(0o755)
env = dict(os.environ, HOME=str(root / "home"), DUET_METRICS="1",
           PATH=str(binary.parent) + os.pathsep + os.environ["PATH"])
def command(*args):
    result = subprocess.run([duet, *args], env=env, text=True, capture_output=True)
    assert result.returncode == 0, (result.returncode, result.stderr)
    return result.stdout
info = root / "info.json"
first_output = command("--finding-reports", "--lead", "opencode:reviewer", "--partner", "opencode:reviewer",
        "--task", "private-task-marker", "--cwd", str(root), "--turns", "2", "--recap",
        "--metrics-kind", "test", "--runs-dir", str(root / "runs"), "--run-info-file", str(info))
run = pathlib.Path(json.loads(info.read_text())["run_dir"])
report = json.loads(command("--report", str(run), "--json"))
assert report["summary"]["unresolved"] == 1
assert report["unresolved_reasons"] == {"single_assessment": 0, "disputed": 0, "open": 1}
assert report["findings"][0]["unresolved_reason"] == "open"
assert report["structured_turns"]["ok"] == 2
assert "L1" in (run / "review.md").read_text()
assert "Both agents have an accepted assessment of this claim, and at least one still marks it unresolved." in (run / "review.md").read_text()
assert "(1 unresolved: 0 single_assessment, 0 disputed, 1 open)" in first_output
printed = [line for line in first_output.splitlines() if line.startswith("[duet] continue unresolved findings: ")]
assert len(printed) == 1
printed_argv = shlex.split(printed[0].split(": ", 1)[1])
assert printed_argv[:3] == ["duet", "--continue", str(run.resolve())] and printed_argv[3:] == ["--resolve", "L1"]
before = (run / "state.json").read_bytes()
continued_info = root / "continued.json"
dry_output = command(*printed_argv[1:], "--dry-run", "--recap",
        "--run-info-file", str(continued_info), "--runs-dir", str(root / "continued"))
continued = pathlib.Path(json.loads(continued_info.read_text())["run_dir"])
assert "[duet] finding report:" in dry_output and "continue unresolved findings" not in dry_output
assert "duet --continue <run> --resolve L1 --turns 2" in (continued / "review.md").read_text()
state = json.loads((continued / "state.json").read_text())
assert state["max_turns"] == 2 and state["finding_focus"] == ["L1"]
assert all(event["inherited"] for event in json.loads(command("--report", str(continued), "--json"))["events"])
assert (run / "state.json").read_bytes() == before
for usefulness in ("mixed", "useful"):
    command("--feedback", str(run), "--usefulness", usefulness, "--decision", "corrected_comment")
stats = json.loads(command("--stats", "--json"))
assert stats["feedback"]["test"]["records"] == 1
assert stats["feedback"]["test"]["usefulness"] == {"useful":1}
assert stats["feedback"]["live"]["records"] == 0
for path in (root / "home" / ".duet" / "metrics").rglob("*.json"):
    text = path.read_text()
    assert "private-claim-marker" not in text and "private-task-marker" not in text
PY

expect_stdout "zcode native review dry run" 0 "dry-run zcode/" "$DUET" --dry-run --no-worktree --lead zcode:reviewer --lead-model account:zai-individual-coding-plan/GLM-5.3 --partner codex:reviewer --reasoning max --task "read only" --cwd "$TMPD"

echo "---"; echo "smoke: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
