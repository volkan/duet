#!/usr/bin/env python3
"""Real-LLM end-to-end loop tests for duet.

This is intentionally separate from scripts/smoke.sh. Smoke is fast and
stdlib-only dry-run coverage; this script spends real Claude/Codex turns to
prove the product loop:

    issue/spec -> implementation -> review -> rejection if needed
    -> final agreement -> auditable artifacts

Artifacts are durable by default under runs/test-loop/<suite-id>/ so a long
run is not lost to OS temp cleanup.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import importlib.util
import json
import os
import pathlib
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from typing import Callable, Optional


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DUET = REPO_ROOT / "duet.py"
DEFAULT_BASE_ROOT = REPO_ROOT / "runs" / "test-loop"
SENTINEL = "<<<LGTM>>>"


GENERIC_CODER_PROMPT = """\
You are the CODER in a duet end-to-end test. Work only inside the current
repository. Do not modify LOCKED.md. Apply the smallest change that satisfies
the task, run `python3 -m unittest -q`, and summarize the changed files and
checks. If the work is complete, use a real LGTM rationale before the sentinel.
"""


GENERIC_REVIEWER_PROMPT = """\
You are the REVIEWER in a duet end-to-end test. Review the partner's message
and the appended worktree diff. Do not trust prose alone: look at the actual
files if needed. Do not modify files. If the task is satisfied and visible
checks pass, use a real LGTM rationale before the sentinel. If anything
material is missing, omit the sentinel and ask for one concrete follow-up.
"""


REJECT_ONCE_REVIEWER_PROMPT = GENERIC_REVIEWER_PROMPT + """\

Scenario rule: on your first reviewer turn in this run, do not approve even if
the first patch looks close. Ask the coder to explicitly verify both `b == 0`
and `a == 0 and b == 0`, preferably with tests. On later reviewer turns,
approve only if the behavior is correct.
"""


NOOP_CODER_PROMPT = GENERIC_CODER_PROMPT + """\

Scenario rule: this repository may already satisfy the task. If it does, do
not edit any file. Run the tests and report that no code changes were needed.
"""


FENCE_CODER_PROMPT = GENERIC_CODER_PROMPT + """\

Scenario rule: on your first turn, create CONVERGENCE.md and include in your
reply a fenced markdown example containing the sentinel. Do not place the real
sentinel outside a fence on that first turn. Ask the reviewer to inspect the
doc. On a later turn, if the reviewer accepts it, you may converge normally.
"""


@dataclasses.dataclass
class Scenario:
    sid: str
    name: str
    task: str
    setup: Callable[[pathlib.Path], None]
    hidden_validator: Callable[[pathlib.Path], tuple[bool, str]]
    max_turns: int = 6
    expected_reasons: tuple[str, ...] = ("converged",)
    coder_prompt: str = GENERIC_CODER_PROMPT
    reviewer_prompt: str = GENERIC_REVIEWER_PROMPT
    require_reviewer_rejection: bool = False
    require_empty_diff: bool = False
    require_fenced_nonconvergence: bool = False
    force_feedback: Optional[str] = None


@dataclasses.dataclass
class Turn:
    speaker: str
    role: Optional[str]
    kind: str
    body: str
    scored_body: str
    convergence: bool


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_cmd(cmd: list[str], cwd: pathlib.Path, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def checked(cmd: list[str], cwd: pathlib.Path, timeout: int = 120) -> None:
    p = run_cmd(cmd, cwd, timeout)
    if p.returncode != 0:
        raise RuntimeError(
            f"command failed ({p.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
        )


def init_git_repo(repo: pathlib.Path) -> None:
    checked(["git", "init"], repo)
    checked(["git", "add", "."], repo)
    checked(
        [
            "git",
            "-c",
            "user.name=duet-loop",
            "-c",
            "user.email=duet-loop@example.invalid",
            "commit",
            "-m",
            "seed fixture",
        ],
        repo,
    )


def common_files(repo: pathlib.Path, app_py: str, test_py: str) -> None:
    write_text(repo / "app.py", app_py)
    write_text(repo / "test_app.py", test_py)
    write_text(
        repo / "LOCKED.md",
        """\
        # Locked file

        This file must not change during duet loop tests.
        """,
    )
    write_text(
        repo / "README.md",
        """\
        # Duet loop fixture

        Small disposable project generated by scripts/duet_loop_e2e.py.
        """,
    )


def setup_docstring(repo: pathlib.Path) -> None:
    common_files(
        repo,
        """\
        def greet(name):
            return f"Hello, {name}!"
        """,
        """\
        import unittest
        from app import greet


        class AppTests(unittest.TestCase):
            def test_greet(self):
                self.assertEqual(greet("Ada"), "Hello, Ada!")


        if __name__ == "__main__":
            unittest.main()
        """,
    )
    init_git_repo(repo)


def setup_safe_divide(repo: pathlib.Path) -> None:
    common_files(
        repo,
        """\
        def safe_divide(a, b):
            return a / b
        """,
        """\
        import unittest
        from app import safe_divide


        class AppTests(unittest.TestCase):
            def test_regular_division(self):
                self.assertEqual(safe_divide(6, 3), 2)

            def test_zero_denominator_returns_none(self):
                self.assertIsNone(safe_divide(5, 0))


        if __name__ == "__main__":
            unittest.main()
        """,
    )
    init_git_repo(repo)


def setup_noop(repo: pathlib.Path) -> None:
    common_files(
        repo,
        """\
        def normalize_name(name):
            return " ".join(name.strip().split()).title()
        """,
        """\
        import unittest
        from app import normalize_name


        class AppTests(unittest.TestCase):
            def test_normalize_name(self):
                self.assertEqual(normalize_name("  ada   lovelace "), "Ada Lovelace")


        if __name__ == "__main__":
            unittest.main()
        """,
    )
    init_git_repo(repo)


def setup_sentinel_doc(repo: pathlib.Path) -> None:
    common_files(
        repo,
        """\
        def identity(value):
            return value
        """,
        """\
        import unittest
        from app import identity


        class AppTests(unittest.TestCase):
            def test_identity(self):
                self.assertEqual(identity("x"), "x")


        if __name__ == "__main__":
            unittest.main()
        """,
    )
    init_git_repo(repo)


def setup_recap_fixture(repo: pathlib.Path) -> None:
    common_files(
        repo,
        """\
        def add(a, b):
            return a + b
        """,
        """\
        import unittest
        from app import add


        class AppTests(unittest.TestCase):
            def test_add(self):
                self.assertEqual(add(2, 3), 5)


        if __name__ == "__main__":
            unittest.main()
        """,
    )
    init_git_repo(repo)


def validate_docstring(worktree: pathlib.Path) -> tuple[bool, str]:
    code = (
        "import ast, pathlib\n"
        "m = ast.parse(pathlib.Path('app.py').read_text())\n"
        "fn = next(n for n in m.body if isinstance(n, ast.FunctionDef) and n.name == 'greet')\n"
        "doc = ast.get_docstring(fn) or ''\n"
        "assert 'greeting' in doc.lower() or 'hello' in doc.lower(), doc\n"
    )
    p = run_cmd([sys.executable, "-c", code], worktree)
    return p.returncode == 0, (p.stderr or p.stdout).strip()


def validate_safe_divide(worktree: pathlib.Path) -> tuple[bool, str]:
    code = (
        "from app import safe_divide\n"
        "assert safe_divide(6, 3) == 2\n"
        "assert safe_divide(5, 0) is None\n"
        "assert safe_divide(0, 0) is None\n"
        "assert safe_divide(-4, 2) == -2\n"
    )
    p = run_cmd([sys.executable, "-c", code], worktree)
    return p.returncode == 0, (p.stderr or p.stdout).strip()


def validate_noop(worktree: pathlib.Path) -> tuple[bool, str]:
    code = (
        "from app import normalize_name\n"
        "assert normalize_name('  grace   hopper ') == 'Grace Hopper'\n"
    )
    p = run_cmd([sys.executable, "-c", code], worktree)
    return p.returncode == 0, (p.stderr or p.stdout).strip()


def validate_sentinel_doc(worktree: pathlib.Path) -> tuple[bool, str]:
    doc = worktree / "CONVERGENCE.md"
    if not doc.is_file():
        return False, "CONVERGENCE.md missing"
    text = doc.read_text(encoding="utf-8")
    if SENTINEL not in text:
        return False, "sentinel not documented"
    if "```" not in text and "~~~" not in text:
        return False, "expected fenced example"
    return True, ""


def validate_add_docstring(worktree: pathlib.Path) -> tuple[bool, str]:
    code = (
        "import ast, pathlib\n"
        "m = ast.parse(pathlib.Path('app.py').read_text())\n"
        "fn = next(n for n in m.body if isinstance(n, ast.FunctionDef) and n.name == 'add')\n"
        "assert ast.get_docstring(fn), 'add() docstring missing'\n"
    )
    p = run_cmd([sys.executable, "-c", code], worktree)
    return p.returncode == 0, (p.stderr or p.stdout).strip()


def scenarios() -> list[Scenario]:
    return [
        Scenario(
            sid="S1",
            name="fix-basic",
            setup=setup_docstring,
            hidden_validator=validate_docstring,
            max_turns=4,
            task=(
                "Add a one-line docstring to greet(name) in app.py explaining "
                "that it returns a greeting. Do not change runtime behavior. "
                "Run `python3 -m unittest -q`."
            ),
        ),
        Scenario(
            sid="S2",
            name="review-reject",
            setup=setup_safe_divide,
            hidden_validator=validate_safe_divide,
            max_turns=6,
            reviewer_prompt=REJECT_ONCE_REVIEWER_PROMPT,
            require_reviewer_rejection=True,
            task=(
                "Fix safe_divide(a, b) so any zero denominator returns None, "
                "including a=0 and b=0. Preserve normal division behavior. "
                "Run `python3 -m unittest -q`."
            ),
        ),
        Scenario(
            sid="S3",
            name="no-op",
            setup=setup_noop,
            hidden_validator=validate_noop,
            max_turns=4,
            coder_prompt=NOOP_CODER_PROMPT,
            require_empty_diff=True,
            task=(
                "Inspect normalize_name(name). It should trim surrounding "
                "space, collapse repeated internal whitespace, and title-case "
                "the result. If the implementation already satisfies this, "
                "do not edit any file. Run `python3 -m unittest -q`."
            ),
        ),
        Scenario(
            sid="S4",
            name="sentinel-stress",
            setup=setup_sentinel_doc,
            hidden_validator=validate_sentinel_doc,
            max_turns=5,
            coder_prompt=FENCE_CODER_PROMPT,
            require_fenced_nonconvergence=True,
            task=(
                "Create CONVERGENCE.md documenting how duet's sentinel works. "
                "Include a fenced markdown example reply containing "
                f"{SENTINEL}. Explain that a real convergence proposal needs "
                "a rationale outside code fences. Run `python3 -m unittest -q`."
            ),
        ),
        Scenario(
            sid="S5",
            name="recap-status",
            setup=setup_recap_fixture,
            hidden_validator=validate_add_docstring,
            max_turns=4,
            task=(
                "Add a concise docstring to add(a, b). Do not change behavior. "
                "Run `python3 -m unittest -q`."
            ),
        ),
        Scenario(
            sid="S6",
            name="force-prompt",
            setup=setup_docstring,
            hidden_validator=validate_docstring,
            max_turns=1,
            expected_reasons=("converged_after_force",),
            force_feedback=(
                "Please review the completed worktree diff now. If the change "
                "is correct, confirm with a real LGTM rationale and the sentinel."
            ),
            task=(
                "Add a one-line docstring to greet(name) in app.py explaining "
                "that it returns a greeting. Do not change runtime behavior. "
                "Run `python3 -m unittest -q`."
            ),
        ),
    ]


def load_duet_module(duet_path: pathlib.Path):
    spec = importlib.util.spec_from_file_location("duet_loop_target", duet_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import duet module from {duet_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


HEADING_RE = re.compile(
    r"^## (?:(?P<human>human) - force-feedback \(next: (?P<next>[^)]+)\)"
    r"|(?P<speaker>.+?) \((?P<role>[^)]*)\) - (?P<kind>seed|agent|forced))\s*$",
    re.MULTILINE,
)

# The real transcript headings use an em dash. Keep the script source ASCII by
# constructing the regex at runtime.
HEADING_RE = re.compile(
    HEADING_RE.pattern.replace(" - ", " \u2014 "),
    re.MULTILINE,
)


def strip_worktree_appendix(text: str) -> str:
    for marker in ("\n---\n#### worktree changes (", "\n[duet] git diff failed:"):
        if marker in text:
            return text.split(marker, 1)[0].rstrip()
    return text.rstrip()


def parse_transcript(transcript: pathlib.Path, duet_module) -> list[Turn]:
    text = transcript.read_text(encoding="utf-8")
    matches = list(HEADING_RE.finditer(text))
    turns: list[Turn] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip("\n")
        if match.group("human"):
            turns.append(
                Turn(
                    speaker="human",
                    role=None,
                    kind="force-feedback",
                    body=body,
                    scored_body=body,
                    convergence=False,
                )
            )
            continue
        kind = match.group("kind") or ""
        scored_body = strip_worktree_appendix(body)
        convergence = False
        if kind in {"agent", "forced"}:
            convergence = bool(duet_module.convergence_proposed(scored_body, SENTINEL))
        turns.append(
            Turn(
                speaker=match.group("speaker") or "",
                role=match.group("role"),
                kind=kind,
                body=body,
                scored_body=scored_body,
                convergence=convergence,
            )
        )
    return turns


def recap_convergence_count(recap_path: pathlib.Path) -> tuple[int, int, bool]:
    text = recap_path.read_text(encoding="utf-8")
    blocks = len(re.findall(r"^## Turn \d+ \| ", text, re.MULTILINE))
    yes = len(re.findall(r"^STATUS:\s+.*convergence: yes\s*$", text, re.MULTILINE))
    bad = bool(re.search(r"^STATUS:\s+converged\b.*convergence: no\s*$", text, re.MULTILINE))
    return blocks, yes, bad


def build_config(
    scenario: Scenario,
    repo: pathlib.Path,
    runs_dir: pathlib.Path,
    timeout: int,
    reasoning: Optional[str],
) -> dict:
    cfg = {
        "cwd": str(repo),
        "max_turns": scenario.max_turns,
        "sentinel": SENTINEL,
        "per_turn_timeout": timeout,
        "runs_dir": str(runs_dir),
        "sandbox": "workspace-write",
        "permission_mode": "acceptEdits",
        "worktree": True,
        "worktree_for": "partner",
        "recap": True,
        "task": scenario.task,
        "agents": [
            {
                "name": "claude-reviewer",
                "backend": "claude",
                "role": "reviewer",
                "role_prompt": scenario.reviewer_prompt,
            },
            {
                "name": "codex-coder",
                "backend": "codex",
                "role": "coder",
                "role_prompt": scenario.coder_prompt,
                "extra_args": [],
            },
        ],
    }
    if reasoning:
        cfg["reasoning"] = reasoning
    return cfg


def run_duet_normal(
    duet_path: pathlib.Path,
    config_path: pathlib.Path,
    log_path: pathlib.Path,
    timeout: int,
) -> int:
    cmd = [sys.executable, str(duet_path), "--config", str(config_path)]
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    return proc.returncode


def run_duet_with_force_pty(
    duet_path: pathlib.Path,
    config_path: pathlib.Path,
    log_path: pathlib.Path,
    feedback: str,
    timeout: int,
) -> int:
    cmd = [sys.executable, str(duet_path), "--config", str(config_path)]
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        start_new_session=True,
    )
    os.close(slave_fd)
    deadline = time.time() + timeout
    prompt_count = 0
    sent_inputs = 0
    inputs = [feedback + "\n", "\n"]
    buffer = b""
    try:
        with log_path.open("wb") as log:
            while True:
                if proc.poll() is not None:
                    while True:
                        try:
                            chunk = os.read(master_fd, 4096)
                        except OSError:
                            break
                        if not chunk:
                            break
                        log.write(chunk)
                    return proc.returncode or 0
                if time.time() > deadline:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    log.write(b"\n[test-loop] PTY timeout\n")
                    return proc.returncode or 124

                readable, _, _ = select.select([master_fd], [], [], 1)
                if not readable:
                    continue
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    continue
                if not chunk:
                    continue
                log.write(chunk)
                log.flush()
                buffer += chunk
                new_prompt_count = buffer.count(b"force>")
                if new_prompt_count > prompt_count:
                    prompt_count = new_prompt_count
                    if sent_inputs < len(inputs):
                        os.write(master_fd, inputs[sent_inputs].encode("utf-8"))
                        sent_inputs += 1
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass


RUN_DIR_RE = re.compile(r"^\[duet\] run(?: dir)?:\s+(.+)$", re.MULTILINE)


def extract_run_dir(log_path: pathlib.Path) -> Optional[pathlib.Path]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    matches = RUN_DIR_RE.findall(text)
    if not matches:
        return None
    return pathlib.Path(matches[-1].strip()).expanduser().resolve()


def git_status_clean(repo: pathlib.Path) -> tuple[bool, str]:
    p = run_cmd(["git", "status", "--short"], repo)
    out = (p.stdout or p.stderr).strip()
    return p.returncode == 0 and out == "", out


def git_diff_names(repo: pathlib.Path) -> list[str]:
    p = run_cmd(["git", "diff", "--name-only", "HEAD"], repo)
    if p.returncode != 0:
        return ["<git diff failed>"]
    return [line for line in p.stdout.splitlines() if line.strip()]


def visible_tests_pass(worktree: pathlib.Path) -> tuple[bool, str]:
    p = run_cmd([sys.executable, "-m", "unittest", "-q"], worktree, timeout=120)
    detail = (p.stdout + p.stderr).strip()
    return p.returncode == 0, detail


def score_run(
    scenario: Scenario,
    repo: pathlib.Path,
    run_dir: pathlib.Path,
    locked_hash: str,
    duet_path: pathlib.Path,
) -> tuple[bool, dict]:
    duet_module = load_duet_module(duet_path)
    failures: list[str] = []
    metrics: dict = {
        "scenario": f"{scenario.sid}-{scenario.name}",
        "run_dir": str(run_dir),
    }

    state_path = run_dir / "state.json"
    if not state_path.is_file():
        return False, {**metrics, "failures": ["state.json missing"]}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    reason = state.get("finished_reason")
    turns_used = int(state.get("turns_used") or 0)
    metrics["reason"] = reason
    metrics["turns"] = turns_used
    if reason not in scenario.expected_reasons:
        failures.append(f"finished_reason={reason!r}, expected {scenario.expected_reasons}")

    transcript_path = pathlib.Path(state.get("transcript_path") or run_dir / "transcript.md")
    if not transcript_path.is_file():
        failures.append("transcript missing")
        turns: list[Turn] = []
    else:
        turns = parse_transcript(transcript_path, duet_module)
    agent_turns = [t for t in turns if t.kind in {"agent", "forced"}]
    parser_hits = sum(1 for t in agent_turns if t.convergence)
    metrics["parser_converged_turns"] = parser_hits

    if reason == "converged":
        if len(agent_turns) < 2 or not all(t.convergence for t in agent_turns[-2:]):
            failures.append("final two agent turns did not both pass convergence_proposed()")
    if reason == "converged_after_force":
        forced = [t for t in agent_turns if t.kind == "forced"]
        metrics["forced_turns"] = len(forced)
        if len(forced) != 1:
            failures.append(f"expected exactly one forced agent turn, got {len(forced)}")
        elif not forced[0].convergence:
            failures.append("forced agent turn did not pass convergence_proposed()")

    if scenario.require_reviewer_rejection:
        final_pair_start = max(0, len(agent_turns) - 2)
        rejections = [
            t for t in agent_turns[:final_pair_start]
            if t.role == "reviewer" and not t.convergence
        ]
        metrics["reviewer_rejections"] = len(rejections)
        if not rejections:
            failures.append("expected at least one non-convergent reviewer turn before final pair")

    if scenario.require_fenced_nonconvergence:
        candidates = [
            t for t in agent_turns
            if SENTINEL in t.scored_body and not t.convergence
        ]
        metrics["fenced_or_bare_sentinel_ignored"] = len(candidates)
        if not candidates:
            failures.append("expected at least one sentinel-containing turn ignored by parser")

    worktree_value = state.get("worktree")
    worktree = pathlib.Path(worktree_value).resolve() if worktree_value else None
    if worktree is None or not worktree.is_dir():
        failures.append(f"worktree missing: {worktree_value}")
    else:
        metrics["worktree"] = str(worktree)
        ok, detail = visible_tests_pass(worktree)
        metrics["visible_tests"] = "pass" if ok else "fail"
        if not ok:
            failures.append(f"visible tests failed: {detail}")
        ok, detail = scenario.hidden_validator(worktree)
        metrics["hidden_validator"] = "pass" if ok else "fail"
        if not ok:
            failures.append(f"hidden validator failed: {detail}")
        locked = worktree / "LOCKED.md"
        if not locked.is_file() or sha256_file(locked) != locked_hash:
            failures.append("LOCKED.md changed or missing in worktree")
        if scenario.require_empty_diff:
            diff_names = git_diff_names(worktree)
            metrics["diff_files"] = ",".join(diff_names) if diff_names else "(none)"
            if diff_names:
                failures.append(f"expected empty diff, got {diff_names}")

    if not (repo / "LOCKED.md").is_file() or sha256_file(repo / "LOCKED.md") != locked_hash:
        failures.append("LOCKED.md changed or missing in host repo")
    host_clean, host_status = git_status_clean(repo)
    metrics["host_clean"] = host_clean
    if not host_clean:
        failures.append(f"host repo dirty: {host_status}")

    recap_path_value = state.get("recap_path")
    recap_path = pathlib.Path(recap_path_value).resolve() if recap_path_value else run_dir / "recap.md"
    if not recap_path.is_file():
        failures.append("recap.md missing")
    else:
        blocks, recap_yes, bad_converged_no = recap_convergence_count(recap_path)
        metrics["recap_blocks"] = blocks
        metrics["recap_converged_turns"] = recap_yes
        if blocks != len(agent_turns):
            failures.append(f"recap turn blocks={blocks}, transcript agent turns={len(agent_turns)}")
        if recap_yes != parser_hits:
            failures.append(
                f"recap convergence count={recap_yes}, parser convergence count={parser_hits}"
            )
        if bad_converged_no:
            failures.append("recap has STATUS: converged with convergence: no")

    pid_files = list(run_dir.glob("turn-*.pid"))
    if pid_files:
        failures.append("stale pid files remain: " + ", ".join(p.name for p in pid_files))

    status = subprocess.run(
        [sys.executable, str(duet_path), "--status", str(run_dir)],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        timeout=30,
    )
    metrics["status_rc"] = status.returncode
    if status.returncode != 0:
        failures.append(f"duet --status returned {status.returncode}: {status.stdout}{status.stderr}")

    metrics["failures"] = failures
    return not failures, metrics


def copy_failure(run_dir: pathlib.Path, dest: pathlib.Path) -> None:
    if not run_dir.exists():
        return
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(run_dir, dest, symlinks=True)


def scenario_timeout(per_turn_timeout: int, turns: int, forced: bool) -> int:
    extra_turns = 2 if forced else 1
    return max(120, per_turn_timeout * (turns + extra_turns) + 60)


def parse_csv(value: Optional[str]) -> Optional[set[str]]:
    if not value:
        return None
    return {part.strip() for part in value.split(",") if part.strip()}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run real Claude/Codex end-to-end loop scenarios for duet."
    )
    ap.add_argument("--duet", default=str(DEFAULT_DUET), help="path to duet.py")
    ap.add_argument(
        "--base-dir",
        default=None,
        help="durable artifact base dir (default: runs/test-loop/<suite-id>)",
    )
    ap.add_argument(
        "--scenario",
        default=None,
        help="comma-separated scenario ids/names to run, e.g. S1,S6 or fix-basic",
    )
    ap.add_argument(
        "--reasoning",
        choices=["minimal", "low", "medium", "high", "xhigh", "max"],
        default=None,
        help="optional duet reasoning level for all scenarios",
    )
    ap.add_argument("--timeout", type=int, default=900, help="per-turn timeout seconds")
    args = ap.parse_args()

    duet_path = pathlib.Path(args.duet).expanduser().resolve()
    if not duet_path.is_file():
        print(f"[test-loop] duet not found: {duet_path}", file=sys.stderr)
        return 2
    for required in ("git", "claude", "codex"):
        if shutil.which(required) is None:
            print(f"[test-loop] required command not on PATH: {required}", file=sys.stderr)
            return 2

    suite_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    base_dir = (
        pathlib.Path(args.base_dir).expanduser().resolve()
        if args.base_dir
        else (DEFAULT_BASE_ROOT / suite_id).resolve()
    )
    if base_dir.exists() and any(base_dir.iterdir()):
        print(
            f"[test-loop] base dir already exists and is not empty: {base_dir}\n"
            "            choose a fresh --base-dir so scenarios cannot reuse old repos",
            file=sys.stderr,
        )
        return 2
    fixtures_dir = base_dir / "fixtures"
    duet_runs_dir = base_dir / "duet-runs"
    failures_dir = base_dir / "failures"
    results_path = base_dir / "results.tsv"
    base_dir.mkdir(parents=True, exist_ok=True)
    failures_dir.mkdir(parents=True, exist_ok=True)

    selected = parse_csv(args.scenario)
    all_scenarios = scenarios()
    if selected:
        wanted = [
            s for s in all_scenarios
            if s.sid in selected or s.name in selected or f"{s.sid}-{s.name}" in selected
        ]
        matched = set()
        for s in wanted:
            matched.update({s.sid, s.name, f"{s.sid}-{s.name}"})
        missing = selected - matched
        if missing:
            print(f"[test-loop] unknown scenario(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 2
        wanted_ids = {s.sid for s in wanted}
        all_scenarios = [s for s in all_scenarios if s.sid in wanted_ids]

    print(f"[test-loop] running {len(all_scenarios)} scenario(s)")
    print(f"[test-loop] artifacts: {base_dir}")
    print(f"[test-loop] reasoning: {args.reasoning or 'default'}")

    rows: list[dict] = []
    with results_path.open("w", encoding="utf-8") as results:
        results.write(
            "scenario\tverdict\treason\tturns\tparser_converged_turns\t"
            "recap_converged_turns\tstatus_rc\trun_dir\tfailures\n"
        )
        for scenario in all_scenarios:
            label = f"{scenario.sid}-{scenario.name}"
            print(f"[{label}] running ... ", end="", flush=True)
            scenario_root = fixtures_dir / label
            repo = scenario_root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            scenario.setup(repo)
            locked_hash = sha256_file(repo / "LOCKED.md")

            runs_dir = duet_runs_dir / label
            config_path = scenario_root / "duet-config.json"
            log_path = scenario_root / "duet.log"
            cfg = build_config(scenario, repo, runs_dir, args.timeout, args.reasoning)
            write_text(config_path, json.dumps(cfg, indent=2) + "\n")

            timeout = scenario_timeout(
                args.timeout,
                scenario.max_turns,
                forced=scenario.force_feedback is not None,
            )
            try:
                if scenario.force_feedback is not None:
                    rc = run_duet_with_force_pty(
                        duet_path,
                        config_path,
                        log_path,
                        scenario.force_feedback,
                        timeout,
                    )
                else:
                    rc = run_duet_normal(duet_path, config_path, log_path, timeout)
            except subprocess.TimeoutExpired:
                rc = 124
                with log_path.open("a", encoding="utf-8") as log:
                    log.write("\n[test-loop] duet subprocess timeout\n")

            run_dir = extract_run_dir(log_path)
            if run_dir is None:
                ok = False
                metrics = {
                    "scenario": label,
                    "reason": "<no-run-dir>",
                    "turns": 0,
                    "parser_converged_turns": 0,
                    "recap_converged_turns": 0,
                    "status_rc": "",
                    "run_dir": "",
                    "failures": [f"duet rc={rc}; run dir not found; log={log_path}"],
                }
            elif rc != 0:
                ok, metrics = score_run(scenario, repo, run_dir, locked_hash, duet_path)
                metrics["failures"].insert(0, f"duet process returned {rc}")
                ok = False
            else:
                ok, metrics = score_run(scenario, repo, run_dir, locked_hash, duet_path)

            verdict = "PASS" if ok else "FAIL"
            failures = "; ".join(metrics.get("failures") or [])
            results.write(
                f"{label}\t{verdict}\t{metrics.get('reason')}\t{metrics.get('turns')}\t"
                f"{metrics.get('parser_converged_turns')}\t"
                f"{metrics.get('recap_converged_turns', '')}\t"
                f"{metrics.get('status_rc', '')}\t{metrics.get('run_dir', '')}\t"
                f"{failures}\n"
            )
            results.flush()
            rows.append({"label": label, "ok": ok, "metrics": metrics})

            if ok:
                print(
                    f"PASS ({metrics.get('reason')} in {metrics.get('turns')} turns, "
                    f"{metrics.get('parser_converged_turns')} convergent turn(s))"
                )
            else:
                if run_dir is not None:
                    copy_failure(run_dir, failures_dir / f"{label}-{run_dir.name}")
                print(f"FAIL ({failures})")

    passed = sum(1 for row in rows if row["ok"])
    failed = len(rows) - passed
    print("")
    print("scenario              verdict  reason                  turns  run_dir")
    print("--------------------  -------  ----------------------  -----  -------")
    for row in rows:
        metrics = row["metrics"]
        print(
            f"{row['label']:<20}  {'PASS' if row['ok'] else 'FAIL':<7}  "
            f"{str(metrics.get('reason')):<22}  {str(metrics.get('turns')):<5}  "
            f"{metrics.get('run_dir', '')}"
        )
    print("")
    print(f"{passed}/{len(rows)} PASS, {failed} FAIL")
    print(f"results:   {results_path}")
    print(f"artifacts: {base_dir}")
    if failed:
        print(f"failures:  {failures_dir}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
