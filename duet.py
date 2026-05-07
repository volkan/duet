#!/usr/bin/env python3
"""
duet.py — two CLI agents in conversation, with per-agent session memory.

Workflow this is built for:

  1. You start `claude` interactively, work out a plan, exit.
     Claude prints (or you grab) a session id like 106c1c57-ca42-473f-b2f1-1ea764f78c46.
  2. You hand it to duet:

       ./duet.py --resume-claude 106c1c57-ca42-473f-b2f1-1ea764f78c46 \
                 --partner codex:coder \
                 --cwd ~/code/myrepo \
                 --turns 10

  3. duet pulls Claude's latest message from that session, feeds it to Codex.
     Codex replies. duet feeds Codex's reply back to Claude (with --resume so
     Claude remembers the whole prior conversation). Ping-pong until either
     the convergence sentinel <<<LGTM>>> appears, --turns is hit, or you Ctrl-C.

Each agent keeps its own session across turns:
  - Claude: `claude -p --resume <session_id> --output-format json` — we capture
    `session_id` from the JSON wrapper and reuse it.
  - Codex:  first turn `codex exec ...`, subsequent turns `codex exec resume --last`
    in the same cwd (caveat: don't run other codex sessions in that cwd in parallel;
    use `--worktree` to isolate duet's Codex cwd from the host repo).

Transcript is always logged to runs/<ts>/transcript.md for humans, but each
prompt sent to an agent is just the latest counterpart message — keeping
prompts small and letting each side rely on its own session memory.

Stdlib only. Python 3.9+.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
from typing import Optional

# ---------- defaults ----------

DEFAULT_SENTINEL = "<<<LGTM>>>"
DEFAULT_TURNS = 2
DEFAULT_TIMEOUT = 60 * 15
TASK_MAX_CHARS = 512 * 1024

ROLE_PROMPTS = {
    "planner": (
        "You are the PLANNER half of a duet. Read the partner agent's latest "
        "message and propose or refine a plan. Be concrete: file names, "
        "functions, edge cases. You may also write or edit non-code "
        "deliverables yourself when the task asks for them — synthesis "
        "documents, reports, comparison matrices, configuration, README "
        "updates, dashboards, etc. What you should NOT do is write production "
        "feature code (that's the coder's job). When you believe the work is "
        "fully done and reviewed, end your reply with {SENTINEL} on its own "
        "line."
    ),
    "coder": (
        "You are the CODER half of a duet. Read the partner agent's latest "
        "message (typically a plan or critique) and produce code. Apply edits "
        "to disk. Run quick checks where reasonable. Summarise what you "
        "changed. When you believe the work is fully done, end your reply "
        "with {SENTINEL} on its own line."
    ),
    "reviewer": (
        "You are the REVIEWER half of a duet. Read the partner agent's "
        "latest message and critically evaluate it: bugs, missing tests, "
        "security, simpler designs. Be specific and brief. If the work meets "
        "the task and you have no material issues, end with {SENTINEL} on "
        "its own line."
    ),
}

# Tiny request used to extract Claude's most recent message when we resume
# from an existing session id, so we have something to hand to the partner.
EXTRACT_LATEST_PROMPT = (
    "[duet harness] I'm about to hand your most recent plan/answer to a "
    "partner coding agent. Please reproduce that plan/answer in full as "
    "your reply now. Reply with the message text only — no preamble, no "
    "framing, no commentary about this request."
)

# User-facing reasoning levels accepted by --reasoning / `reasoning:` in YAML.
# These are the *duet abstraction*; per-backend translation happens below so
# the user doesn't have to remember backend-specific names like Codex's `xhigh`
# or Claude's lack of a `minimal` effort value.
REASONING_LEVELS = ["minimal", "low", "medium", "high", "max"]

# Claude Code exposes thinking control through `--effort`. We still add small
# prompt nudges for high/max because they are useful natural-language guidance,
# and `ultrathink` is a recognized one-turn in-context nudge in current Claude
# Code. The CLI flag below is the authoritative effort control.
CLAUDE_REASONING_PROMPT_PREFIX = {
    "minimal": "",
    "low":     "",
    "medium":  "",
    "high":    "think hard and reason step-by-step before answering. Cover edge cases.\n\n",
    "max":     "ultrathink — reason exhaustively before answering. Enumerate edge "
               "cases, alternatives, and risks. Do not skim.\n\n",
}

# Claude Code `--effort` accepts low, medium, high, xhigh, max. The duet
# abstraction has `minimal` for Codex, so Claude gets its lowest documented
# level for that user-facing value.
CLAUDE_REASONING_MAP = {
    "minimal": "low",
    "low":     "low",
    "medium":  "medium",
    "high":    "high",
    "max":     "max",
}

# Codex CLI takes a config override `-c model_reasoning_effort=<value>`.
# Its accepted values, lowest→highest, are: minimal, low, medium, high, xhigh.
# We map duet's `max` to Codex's `xhigh` (its actual highest).
CODEX_REASONING_MAP = {
    "minimal": "minimal",
    "low":     "low",
    "medium":  "medium",
    "high":    "high",
    "max":     "xhigh",
}


def validate_reasoning(value: Optional[str], context: str) -> None:
    if value is not None and value not in REASONING_LEVELS:
        choices = "|".join(REASONING_LEVELS)
        raise SystemExit(f"bad reasoning value for {context}: {value!r}; expected {choices}")


def effective_reasoning(agent: Agent, cfg_reasoning: Optional[str]) -> Optional[str]:
    return agent.reasoning_effort or cfg_reasoning

# ---------- data classes ----------

@dataclasses.dataclass
class Agent:
    name: str
    backend: str                    # "claude" or "codex"
    role: str = "coder"             # planner | coder | reviewer | custom (with role_prompt)
    role_prompt: Optional[str] = None
    model: Optional[str] = None
    session_id: Optional[str] = None  # tracked across turns
    extra_args: list[str] = dataclasses.field(default_factory=list)
    cwd_override: Optional[pathlib.Path] = None  # set when this agent runs in a git worktree
    reasoning_effort: Optional[str] = None  # one of REASONING_LEVELS; overrides cfg.reasoning

    def system_prompt(self, sentinel: str) -> str:
        tmpl = self.role_prompt or ROLE_PROMPTS.get(self.role)
        if tmpl is None:
            raise SystemExit(f"unknown role '{self.role}' for agent '{self.name}' — "
                             "supply role_prompt to override")
        # str.replace, not str.format — role prompts often contain literal
        # `{...}` (JSON schema, code samples, jq patterns). format() would
        # parse those as format fields and crash with "unexpected '{' in
        # field name". replace handles them as plain text.
        return tmpl.replace("{SENTINEL}", sentinel)


@dataclasses.dataclass
class DuetConfig:
    cwd: pathlib.Path
    agents: list[Agent]                # exactly 2 for now
    task: Optional[str] = None         # used if no resume seed
    kickoff: Optional[str] = None      # explicit first message to partner
    max_turns: int = DEFAULT_TURNS
    sentinel: str = DEFAULT_SENTINEL
    per_turn_timeout: int = DEFAULT_TIMEOUT
    runs_dir: pathlib.Path = pathlib.Path("runs")
    sandbox: str = "workspace-write"          # codex
    permission_mode: str = "acceptEdits"      # claude
    dry_run: bool = False
    worktree: bool = False                    # run partner in a throwaway git worktree
    worktree_for: str = "partner"             # "partner" (idx 1) or "lead" (idx 0)
    worktree_path: Optional[pathlib.Path] = None  # reuse an existing worktree (for resume)
    worktree_root: Optional[pathlib.Path] = None  # parent dir for new worktrees;
                                                  # default = <run_dir>/wt (durable, gitignored)
    add_dirs: list[pathlib.Path] = dataclasses.field(default_factory=list)
                                                  # extra `--add-dir` paths for claude — needed
                                                  # when the task reads/writes outside cwd
                                                  # (e.g. ../DECISION.md). Without these claude
                                                  # silently refuses paths outside cwd.
    reasoning: Optional[str] = None           # default reasoning effort for both agents


# ---------- active child process tracking ----------

_ACTIVE_PROCS: set[subprocess.Popen] = set()
_ACTIVE_PROCS_LOCK = threading.Lock()


def _register_proc(proc: subprocess.Popen) -> None:
    with _ACTIVE_PROCS_LOCK:
        _ACTIVE_PROCS.add(proc)


def _unregister_proc(proc: subprocess.Popen) -> None:
    with _ACTIVE_PROCS_LOCK:
        _ACTIVE_PROCS.discard(proc)


def _signal_proc_tree(proc: subprocess.Popen, sig: int) -> None:
    if proc.poll() is not None:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, sig)
        else:
            proc.send_signal(sig)
    except ProcessLookupError:
        pass
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _terminate_active_processes(sig: int = signal.SIGKILL) -> None:
    with _ACTIVE_PROCS_LOCK:
        procs = list(_ACTIVE_PROCS)
    for proc in procs:
        _signal_proc_tree(proc, sig)


# ---------- git worktree helpers ----------

def is_git_repo(path: pathlib.Path) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def setup_worktree(repo_path: pathlib.Path, branch_name: str,
                   dest: pathlib.Path) -> pathlib.Path:
    """Create a git worktree at `dest` on a fresh branch. Returns the resolved path.

    `dest` must NOT already exist (git worktree add's requirement); its parent
    is created if missing. Caller controls placement — see `cfg.worktree_root`
    or the default `<run_dir>/wt`.
    """
    dest = dest.expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise RuntimeError(f"worktree destination already exists: {dest}")
    cmd = ["git", "-C", str(repo_path), "worktree", "add", "-b", branch_name, str(dest)]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise RuntimeError("git not found on PATH")
    _register_proc(proc)
    try:
        try:
            _, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            _signal_proc_tree(proc, signal.SIGTERM)
            try:
                _, err = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                _signal_proc_tree(proc, signal.SIGKILL)
                _, err = proc.communicate()
            raise RuntimeError(f"git worktree add timed out: {err.strip()}")
    finally:
        _unregister_proc(proc)
    if proc.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {err.strip()}")
    return dest


def git_diff_summary(wt_path: pathlib.Path, max_chars: int = 8000) -> str:
    """Return a short diff summary (status + truncated diff) for the worktree."""
    try:
        status = subprocess.run(
            ["git", "-C", str(wt_path), "status", "--short"],
            capture_output=True, text=True, timeout=10,
        ).stdout.rstrip()
        diff = subprocess.run(
            ["git", "-C", str(wt_path), "diff", "HEAD", "--stat"],
            capture_output=True, text=True, timeout=10,
        ).stdout.rstrip()
        full = subprocess.run(
            ["git", "-C", str(wt_path), "diff", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        if len(full) > max_chars:
            full = full[:max_chars] + f"\n…[truncated, {len(full)-max_chars} more chars]"
        return f"### git status\n{status or '(clean)'}\n\n### diffstat\n{diff or '(none)'}\n\n### diff\n{full or '(none)'}"
    except subprocess.TimeoutExpired:
        return "[duet] git diff timed out"


def write_text_atomic(path: pathlib.Path, text: str) -> None:
    """Write text through a same-directory temp file, then atomically replace."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def append_text_atomic(path: pathlib.Path, text: str) -> None:
    prior = path.read_text(encoding="utf-8") if path.exists() else ""
    write_text_atomic(path, prior + text)


# ---------- subprocess wrappers ----------

# Module-level: when True, _run forwards subprocess stderr to the user's
# terminal in real-time. Codex prints its progress (thinking, tool calls)
# to stderr, so this gives live visibility during long turns.
LIVE_STREAM = True
LIVE_PREFIX = "  │ "  # box-drawing prefix on every streamed line
LIVE_PREFIX_TASK = "  $ "


def _stream_reader(stream, sink: list[str], mirror_to=None, prefix: str = "",
                   tee_to=None):
    """Drain a pipe line-by-line, capture into `sink`, optionally mirror live and/or tee to file.

    `mirror_to` is a writable text stream (typically sys.stderr) that the
    line is echoed to with `prefix`. `tee_to` is an open file handle that
    receives the raw line — used to persist the live stream for post-hoc
    forensics. Either or both may be None.
    """
    try:
        for line in iter(stream.readline, ""):
            sink.append(line)
            if mirror_to is not None:
                try:
                    mirror_to.write(prefix + line if prefix else line)
                    mirror_to.flush()
                except Exception:
                    pass
            if tee_to is not None:
                try:
                    tee_to.write(line)
                    tee_to.flush()
                except Exception:
                    pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _run(cmd: list[str], *, cwd: pathlib.Path, stdin: Optional[str], timeout: int,
         stderr_log_path: Optional[pathlib.Path] = None,
         pid_file_path: Optional[pathlib.Path] = None) -> tuple[int, str, str]:
    """Run a subprocess. Returns (rc, stdout, stderr).

    If LIVE_STREAM is on AND stderr is a TTY, the child's stderr is mirrored
    to our stderr line-by-line as it's produced. stdout is always captured
    silently — duet logs the final answer to the transcript afterwards.

    If `stderr_log_path` is set, the child's stderr is also tee'd line-by-line
    to that file (append mode) — useful for post-hoc forensics on long agent
    turns where the live trace is otherwise lost.

    If `pid_file_path` is set, the child's PID is written there at startup
    and the file is removed when the call returns. External tools can read
    the file + `kill -0 <pid>` to tell apart "duet is alive, agent thinking"
    vs "agent crashed silently". Critical for agents like `claude -p` that
    emit no stderr during their long API call.
    """
    mirror = sys.stderr if (LIVE_STREAM and sys.stderr.isatty()) else None
    out_chunks: list[str] = []
    err_chunks: list[str] = []
    stderr_file = None
    if stderr_log_path is not None:
        try:
            stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
            stderr_file = open(stderr_log_path, "a", encoding="utf-8", buffering=1)
            stderr_file.write(
                f"\n# {dt.datetime.now().isoformat(timespec='seconds')} :: "
                f"{' '.join(cmd[:3])}{' …' if len(cmd) > 3 else ''}\n"
            )
        except OSError as e:
            print(f"[duet] warn: stderr log open failed ({stderr_log_path}): {e}",
                  file=sys.stderr)
            stderr_file = None
    try:
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(cwd),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,  # line-buffered
                start_new_session=True,
            )
        except FileNotFoundError:
            return 127, "", f"[duet] command not found: {cmd[0]}"
        _register_proc(proc)
        if pid_file_path is not None:
            try:
                pid_file_path.parent.mkdir(parents=True, exist_ok=True)
                # Write atomically so a poller never reads a half-written PID.
                tmp = pid_file_path.with_name(pid_file_path.name + ".tmp")
                tmp.write_text(f"{proc.pid}\n")
                os.replace(tmp, pid_file_path)
            except OSError as e:
                print(f"[duet] warn: pid file write failed ({pid_file_path}): {e}",
                      file=sys.stderr)
        t_out = threading.Thread(target=_stream_reader, args=(proc.stdout, out_chunks), daemon=True)
        t_err = threading.Thread(target=_stream_reader,
                                 args=(proc.stderr, err_chunks, mirror, LIVE_PREFIX, stderr_file),
                                 daemon=True)
        t_out.start(); t_err.start()

        try:
            if stdin is not None and proc.stdin is not None:
                try:
                    proc.stdin.write(stdin)
                except BrokenPipeError:
                    pass
            if proc.stdin is not None:
                proc.stdin.close()
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _signal_proc_tree(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _signal_proc_tree(proc, signal.SIGKILL)
                proc.wait()
            t_out.join(timeout=2); t_err.join(timeout=2)
            return 124, "".join(out_chunks), "".join(err_chunks) + f"\n[duet] TIMEOUT after {timeout}s"
        finally:
            _unregister_proc(proc)
        t_out.join(timeout=5); t_err.join(timeout=5)
        return proc.returncode, "".join(out_chunks), "".join(err_chunks)
    finally:
        if stderr_file is not None:
            try:
                stderr_file.close()
            except Exception:
                pass
        if pid_file_path is not None:
            try:
                pid_file_path.unlink(missing_ok=True)
            except OSError:
                pass


def call_claude(agent: Agent, system_prompt: str, message: str,
                cwd: pathlib.Path, perm_mode: str, timeout: int, dry: bool,
                reasoning: Optional[str] = None,
                stderr_log_path: Optional[pathlib.Path] = None,
                pid_file_path: Optional[pathlib.Path] = None,
                add_dirs: Optional[list[pathlib.Path]] = None) -> tuple[str, Optional[str]]:
    """Returns (assistant_text, new_session_id)."""
    eff_cwd = agent.cwd_override or cwd
    if reasoning:
        system_prompt = CLAUDE_REASONING_PROMPT_PREFIX.get(reasoning, "") + system_prompt
    reasoning_args: list[str] = []
    if reasoning:
        claude_value = CLAUDE_REASONING_MAP.get(reasoning, reasoning)
        reasoning_args = ["--effort", claude_value]
    if dry:
        new_sid = agent.session_id or f"dry-claude-{int(time.time())}"
        wt_note = f" wt={eff_cwd}" if agent.cwd_override else ""
        rn = f" reasoning={reasoning}" if reasoning else ""
        return f"[dry-run claude/{agent.name}{wt_note}{rn}] received {len(message)} chars\n{DEFAULT_SENTINEL}", new_sid
    cmd = ["claude", "-p", message,
           "--output-format", "json",
           "--append-system-prompt", system_prompt,
           "--permission-mode", perm_mode,
           *reasoning_args,
           "--add-dir", str(eff_cwd)]
    # Extra read/write roots for tasks that span outside cwd (e.g. writing
    # ../DECISION_v2.md from a cwd-scoped run). Without these, claude refuses
    # paths outside its allowlist with a generic permission error.
    for d in (add_dirs or []):
        cmd += ["--add-dir", str(d)]
    if agent.session_id:
        cmd += ["--resume", agent.session_id]
    if agent.model:
        cmd += ["--model", agent.model]
    cmd += agent.extra_args
    rc, out, err = _run(cmd, cwd=eff_cwd, stdin=None, timeout=timeout,
                        stderr_log_path=stderr_log_path,
                        pid_file_path=pid_file_path)
    if rc != 0:
        raise RuntimeError(f"claude exited {rc}\nstderr:\n{err}")
    try:
        payload = json.loads(out)
        return (payload.get("result") or "").rstrip(), payload.get("session_id") or agent.session_id
    except json.JSONDecodeError:
        snippet = out[:500].strip()
        raise RuntimeError(f"claude returned malformed JSON output: {snippet!r}")


def call_codex(agent: Agent, system_prompt: str, message: str,
               cwd: pathlib.Path, sandbox: str, timeout: int, dry: bool,
               first_turn: bool, reasoning: Optional[str] = None,
               stderr_log_path: Optional[pathlib.Path] = None,
               pid_file_path: Optional[pathlib.Path] = None) -> tuple[str, Optional[str]]:
    """Returns (assistant_text, new_session_id). Codex resume tracking uses --last."""
    eff_cwd = agent.cwd_override or cwd
    if dry:
        new_sid = agent.session_id or f"dry-codex-{int(time.time())}"
        wt_note = f" wt={eff_cwd}" if agent.cwd_override else ""
        rn = f" reasoning={reasoning}" if reasoning else ""
        return f"[dry-run codex/{agent.name}{wt_note}{rn}] received {len(message)} chars\n{DEFAULT_SENTINEL}", new_sid
    full_prompt = f"=== ROLE ===\n{system_prompt}\n\n=== MESSAGE FROM PARTNER ===\n{message}"
    reasoning_args: list[str] = []
    if reasoning:
        codex_value = CODEX_REASONING_MAP.get(reasoning, reasoning)
        # `medium` is Codex's default; only override when we actually want a
        # different effort level.
        if codex_value != "medium":
            reasoning_args = ["-c", f"model_reasoning_effort={codex_value}"]
    # Codex's `exec` parses options BEFORE the positional prompt in modern
    # builds, and some flags (e.g. --ask-for-approval) have come and gone
    # across versions. We keep the default flag set conservative.
    # `extra_args` lets users add their version's approval/auto flag (e.g.
    # `["--full-auto"]` or `["--yolo"]`) and config overrides (`-c …`).
    #
    # IMPORTANT: `codex exec resume` accepts a SUBSET of `codex exec`'s
    # flags. In particular, `--sandbox` and `--cd` are exec-only — they
    # carry over from the resumed session and codex's clap parser rejects
    # them on resume with "unexpected argument '--sandbox' found". So we
    # split: exec_only_opts are passed only on the first call.
    shared_opts = ["--skip-git-repo-check"]
    if agent.model:
        shared_opts += ["--model", agent.model]
    # All options BEFORE the positional prompt — modern codex's clap parser
    # rejects flags after the prompt.
    if first_turn or not agent.session_id:
        exec_only_opts = ["--sandbox", sandbox, "--cd", str(eff_cwd)]
        options = [*exec_only_opts, *shared_opts, *reasoning_args, *agent.extra_args]
        cmd = ["codex", "exec", *options, full_prompt]
    else:
        # Resume the most recent codex session in this cwd. Caveat: don't run
        # parallel codex sessions in the same cwd while a duet is running.
        # cwd is set via subprocess.Popen(cwd=…) so codex inherits the right
        # directory for `--last`'s lookup. sandbox carries over from session.
        options = [*shared_opts, *reasoning_args, *agent.extra_args]
        cmd = ["codex", "exec", "resume", "--last", *options, full_prompt]
    # codex exec hangs on non-TTY stdin without explicit close (issue #20919)
    rc, out, err = _run(cmd, cwd=eff_cwd, stdin="", timeout=timeout,
                        stderr_log_path=stderr_log_path,
                        pid_file_path=pid_file_path)
    if rc != 0:
        raise RuntimeError(f"codex exited {rc}\nstderr:\n{err}\ncmd: {' '.join(cmd[:8])}…")
    # We don't reliably parse codex's session id; treat presence of "last"-resume as our state marker.
    return out.rstrip(), agent.session_id or "codex-current"


def call_agent(agent: Agent, message: str, cfg: DuetConfig, first_turn_for_agent: bool,
               *, run_dir: Optional[pathlib.Path] = None,
               turn_label: Optional[str] = None) -> str:
    sys_prompt = agent.system_prompt(cfg.sentinel)
    reasoning = effective_reasoning(agent, cfg.reasoning)
    # Per-turn stderr log + pid file land in the run dir for forensics +
    # liveness checks, sortable by turn number. The pid file is the only
    # reliable signal for "is the agent still alive?" when stderr goes
    # silent (claude -p emits nothing during its API call).
    log_path: Optional[pathlib.Path] = None
    pid_path: Optional[pathlib.Path] = None
    if run_dir is not None and turn_label is not None:
        log_path = run_dir / f"turn-{turn_label}-{agent.name}.stderr.log"
        pid_path = run_dir / f"turn-{turn_label}-{agent.name}.pid"
    if agent.backend == "claude":
        text, new_sid = call_claude(agent, sys_prompt, message, cfg.cwd,
                                    cfg.permission_mode, cfg.per_turn_timeout, cfg.dry_run,
                                    reasoning=reasoning,
                                    stderr_log_path=log_path,
                                    pid_file_path=pid_path,
                                    add_dirs=cfg.add_dirs)
        agent.session_id = new_sid
        return text
    if agent.backend == "codex":
        text, new_sid = call_codex(agent, sys_prompt, message, cfg.cwd,
                                   cfg.sandbox, cfg.per_turn_timeout, cfg.dry_run,
                                   first_turn=first_turn_for_agent,
                                   reasoning=reasoning,
                                   stderr_log_path=log_path,
                                   pid_file_path=pid_path)
        agent.session_id = new_sid
        return text
    raise SystemExit(f"unknown backend '{agent.backend}'")

# ---------- loop ----------

class StopFlag:
    def __init__(self) -> None:
        self.requested = False
        self.reason = ""
    def request(self, reason: str) -> None:
        self.requested = True
        self.reason = reason


def _install_sigint(stop: StopFlag) -> None:
    def handler(signum, frame):
        if stop.requested:
            print("\n[duet] second SIGINT — exiting hard.", file=sys.stderr)
            _terminate_active_processes(signal.SIGKILL)
            os._exit(130)
        print("\n[duet] SIGINT received — finishing current turn, then stopping. "
              "Press Ctrl-C again to abort immediately.", file=sys.stderr)
        stop.request("SIGINT")
    signal.signal(signal.SIGINT, handler)


def converged(text: str, sentinel: str) -> bool:
    sentinel_re = re.compile(rf"^\s*{re.escape(sentinel)}\s*$")
    in_fence = False
    fence_char = ""
    fence_len = 0
    for line in text.splitlines():
        m = re.match(r"^\s*(`{3,}|~{3,})", line)
        if m:
            marker = m.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len:
                in_fence = False
                fence_char = ""
                fence_len = 0
            continue
        if not in_fence and sentinel_re.match(line):
            return True
    return False


def derive_seed(cfg: DuetConfig, run_dir: Optional[pathlib.Path] = None) -> str:
    """Figure out the first message to send to the partner agent."""
    if cfg.kickoff:
        return cfg.kickoff
    # If agent[0] has a session_id, ask it to dump its latest plan/message.
    a0 = cfg.agents[0]
    if a0.session_id:
        print(f"[duet] extracting latest message from {a0.backend} session {a0.session_id[:8]}…")
        return call_agent(a0, EXTRACT_LATEST_PROMPT, cfg,
                          first_turn_for_agent=False,
                          run_dir=run_dir, turn_label="00-extract")
    if cfg.task:
        return cfg.task
    raise SystemExit("nothing to start the conversation with — supply --task, "
                     "--kickoff, or --resume-claude <session_id>")


def run_duet(cfg: DuetConfig) -> dict:
    if len(cfg.agents) != 2:
        raise SystemExit(f"duet expects exactly 2 agents, got {len(cfg.agents)}")

    try:
        cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(cfg.cwd)).strip("-")[:80]
        fallback = pathlib.Path.home() / ".duet" / "runs" / slug
        print(f"[duet] cannot create runs dir {cfg.runs_dir}: {e}; "
              f"falling back to {fallback}", file=sys.stderr)
        fallback.mkdir(parents=True, exist_ok=True)
        cfg.runs_dir = fallback
    # Auto-ignore everything duet writes (transcripts, state, worktrees) from
    # the host repo's POV. Idempotent — only written once per runs_dir.
    gi = cfg.runs_dir / ".gitignore"
    if not gi.exists():
        write_text_atomic(gi, "# auto-created by duet — ignores all run artifacts\n"
                              "# (transcripts, state.json, worktrees) so they don't\n"
                              "# pollute the host repo. Safe to delete or edit.\n*\n")
    base_run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    for n in range(100):
        run_id = base_run_id if n == 0 else f"{base_run_id}-{n:02d}"
        run_dir = cfg.runs_dir / run_id
        try:
            run_dir.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise SystemExit(f"could not allocate a unique run dir under {cfg.runs_dir}")
    transcript_path = run_dir / "transcript.md"
    state_path = run_dir / "state.json"

    stop = StopFlag()
    _install_sigint(stop)

    seen_first_turn = {a.name: False for a in cfg.agents}
    history: list[dict] = []
    transcript = ""

    def log(speaker: str, role: str, text: str, kind: str = "agent") -> None:
        nonlocal transcript
        head = f"\n## {speaker} ({role}) — {kind}\n\n"
        transcript += head + text + "\n"
        write_text_atomic(transcript_path, transcript)

    print(f"[duet] run dir: {run_dir}")
    if cfg.agents[0].session_id:
        print(f"[duet] {cfg.agents[0].name} resumes session {cfg.agents[0].session_id}")

    # ----- worktree setup (optional) -----
    wt_path: Optional[pathlib.Path] = None
    wt_branch: Optional[str] = None
    wt_idx = {"lead": 0, "partner": 1}.get(cfg.worktree_for, 1)
    if cfg.worktree_path:
        # Reuse an existing worktree (e.g. resuming a cancelled run).
        existing = pathlib.Path(cfg.worktree_path).expanduser().resolve()
        if not existing.is_dir():
            print(f"[duet] WARNING: --worktree-path {existing} doesn't exist. "
                  f"Falling back to same-repo mode.", file=sys.stderr)
        else:
            wt_path = existing
            # Try to recover the branch name (might fail; just for logging)
            try:
                r = subprocess.run(
                    ["git", "-C", str(wt_path), "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True, text=True, timeout=5,
                )
                wt_branch = r.stdout.strip() if r.returncode == 0 else None
            except Exception:
                wt_branch = None
            cfg.agents[wt_idx].cwd_override = wt_path
            print(f"[duet] reusing worktree: {wt_path} (branch {wt_branch}, agent {cfg.agents[wt_idx].name})")
    elif cfg.worktree:
        if not is_git_repo(cfg.cwd):
            print(f"[duet] WARNING: --worktree requested but {cfg.cwd} is not a git repo. "
                  f"Falling back to same-repo mode.", file=sys.stderr)
        else:
            wt_branch = f"duet/{run_id}"
            # Default lives next to the transcript/state in run_dir/wt; users
            # can override to e.g. ~/duet-worktrees, which we then namespace by
            # run_id so parallel runs never collide.
            if cfg.worktree_root:
                wt_dest = cfg.worktree_root / run_id
            else:
                wt_dest = run_dir / "wt"
            try:
                wt_path = setup_worktree(cfg.cwd, wt_branch, wt_dest)
                cfg.agents[wt_idx].cwd_override = wt_path
                print(f"[duet] worktree: {wt_path} (branch {wt_branch}, agent {cfg.agents[wt_idx].name})")
            except Exception as e:
                print(f"[duet] WARNING: worktree setup failed: {e}. Continuing without.", file=sys.stderr)
                wt_path = None

    if stop.requested:
        state = {
            "task": cfg.task,
            "cwd": str(cfg.cwd),
            "turns_used": 0,
            "agents": [{"name": a.name, "backend": a.backend, "role": a.role,
                        "session_id": a.session_id} for a in cfg.agents],
            "history": history,
            "finished_reason": "force_stop",
            "transcript_path": str(transcript_path),
            "worktree": str(wt_path) if wt_path else None,
            "worktree_branch": wt_branch,
            "duet_pid": os.getpid(),
        }
        write_text_atomic(state_path, json.dumps(state, indent=2))
        return state

    seed = derive_seed(cfg, run_dir=run_dir)
    log(cfg.agents[0].name, cfg.agents[0].role, seed, kind="seed")
    seen_first_turn[cfg.agents[0].name] = True
    last_msg = seed

    # Partner (agent[1]) speaks first in the loop, replying to the seed.
    speaker_idx = 1
    finished_reason = "max_turns"

    for turn in range(1, cfg.max_turns + 1):
        if stop.requested:
            finished_reason = "force_stop"
            break
        speaker = cfg.agents[speaker_idx]
        t0 = time.time()
        # Print BEFORE the subprocess starts so the terminal user sees
        # something happen instantly. claude -p emits nothing on stderr
        # during its API call; without this banner the user thinks duet hung.
        print(f"\n--- Turn {turn} :: {speaker.name} ({speaker.backend}/{speaker.role}) "
              f"[started {dt.datetime.now().strftime('%H:%M:%S')}] ---")
        sys.stdout.flush()
        try:
            reply = call_agent(speaker, last_msg, cfg,
                               first_turn_for_agent=not seen_first_turn[speaker.name],
                               run_dir=run_dir, turn_label=f"{turn:02d}")
        except Exception as e:
            reply = f"[duet] AGENT ERROR: {e}"
            stop.request(f"agent_error: {e}")
        seen_first_turn[speaker.name] = True
        elapsed = time.time() - t0

        # If this speaker is the worktree agent, capture the diff and append it to its reply.
        if wt_path is not None and speaker.cwd_override == wt_path:
            try:
                diff_block = git_diff_summary(wt_path)
                reply = f"{reply}\n\n---\n#### worktree changes ({wt_path.name})\n{diff_block}"
            except Exception as e:
                reply = f"{reply}\n\n[duet] git diff failed: {e}"

        log(speaker.name, speaker.role, reply)
        history.append({"turn": turn, "agent": speaker.name, "elapsed_s": elapsed,
                        "len_chars": len(reply), "session_id": speaker.session_id})
        write_text_atomic(state_path, json.dumps({
            "task": cfg.task, "cwd": str(cfg.cwd), "turns_used": turn,
            "agents": [{"name": a.name, "backend": a.backend, "role": a.role,
                        "session_id": a.session_id} for a in cfg.agents],
            "history": history, "finished_reason": None,
            "duet_pid": os.getpid(),
        }, indent=2))
        print(reply)

        if converged(reply, cfg.sentinel):
            finished_reason = "converged"
            break
        if stop.requested:
            finished_reason = "force_stop"
            break

        last_msg = reply
        speaker_idx = 1 - speaker_idx
    else:
        finished_reason = "max_turns"

    finished_reason = ask_force(cfg, history, transcript_path, state_path,
                                last_msg, speaker_idx, seen_first_turn, finished_reason)

    state = {
        "task": cfg.task,
        "cwd": str(cfg.cwd),
        "turns_used": len(history),
        "agents": [{"name": a.name, "backend": a.backend, "role": a.role,
                    "session_id": a.session_id} for a in cfg.agents],
        "history": history,
        "finished_reason": finished_reason,
        "transcript_path": str(transcript_path),
        "duet_pid": os.getpid(),
    }
    state["worktree"] = str(wt_path) if wt_path else None
    state["worktree_branch"] = wt_branch
    write_text_atomic(state_path, json.dumps(state, indent=2))
    print(f"\n[duet] done. reason={finished_reason}. transcript: {transcript_path}")
    print(f"[duet] resumable session ids — "
          + ", ".join(f"{a.name}={a.session_id}" for a in cfg.agents if a.session_id))
    if wt_path:
        print(f"[duet] worktree left intact at {wt_path} (branch {wt_branch}).\n"
              f"        merge:  git -C {cfg.cwd} merge {wt_branch}\n"
              f"        review: git -C {wt_path} diff HEAD\n"
              f"        drop:   git -C {cfg.cwd} worktree remove {wt_path} && "
              f"git -C {cfg.cwd} branch -D {wt_branch}")
    return state


def ask_force(cfg: DuetConfig, history: list, transcript_path: pathlib.Path,
              state_path: pathlib.Path, last_msg: str, speaker_idx: int,
              seen_first_turn: dict, reason: str) -> str:
    """Post-loop interactive prompt: human can push another turn or accept."""
    if not sys.stdin.isatty():
        return reason
    while True:
        print(f"\n[duet] loop ended (reason={reason}). "
              f"Press Enter to finish, or type feedback to force another turn:")
        try:
            line = input("force> ").strip()
        except EOFError:
            return reason
        if not line:
            return reason
        # Inject human feedback as the next "message" to the next-up speaker.
        next_speaker = cfg.agents[speaker_idx]
        # Append a human note to transcript
        head = f"\n## human — force-feedback (next: {next_speaker.name})\n\n"
        append_text_atomic(transcript_path, head + line + "\n")
        last_msg = line
        forced_turn = len(history) + 1
        try:
            reply = call_agent(next_speaker, last_msg, cfg,
                               first_turn_for_agent=not seen_first_turn[next_speaker.name],
                               run_dir=transcript_path.parent,
                               turn_label=f"{forced_turn:02d}-forced")
        except Exception as e:
            reply = f"[duet] AGENT ERROR: {e}"
        seen_first_turn[next_speaker.name] = True
        append_text_atomic(
            transcript_path,
            f"\n## {next_speaker.name} ({next_speaker.role}) — forced\n\n{reply}\n",
        )
        history.append({"turn": len(history) + 1, "agent": next_speaker.name,
                        "forced": True, "len_chars": len(reply),
                        "session_id": next_speaker.session_id})
        print(reply)
        speaker_idx = 1 - speaker_idx
        reason = "forced_continuation"
        if converged(reply, cfg.sentinel):
            return "converged_after_force"

# ---------- config / cli parsing ----------

def parse_partner(spec: str, default_role: str = "coder") -> Agent:
    """'codex:coder' -> Agent(backend=codex, role=coder)."""
    backend, _, role = spec.partition(":")
    if not backend:
        raise SystemExit(f"bad partner spec '{spec}', expected backend or backend:role")
    role = role or default_role
    return Agent(name=f"{backend}-{role}", backend=backend, role=role)


def load_yaml_or_json(path: pathlib.Path) -> dict:
    text = path.read_text()
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError:
            raise SystemExit("PyYAML not installed; convert to JSON or `pip install pyyaml`.")
        return yaml.safe_load(text)
    return json.loads(text)


def _check_task_size(text: str, parser: argparse.ArgumentParser) -> str:
    if len(text) > TASK_MAX_CHARS:
        parser.error(f"task too large ({len(text)} chars > {TASK_MAX_CHARS}); "
                     "pipe a shorter summary")
    return text


def resolve_at_text(value: Optional[str], option_name: str,
                    parser: argparse.ArgumentParser,
                    stdin_cache: dict[str, str]) -> Optional[str]:
    """Resolve literal / @file / @- task text before a run directory exists."""
    if value is None:
        return None
    if not value.startswith("@"):
        return _check_task_size(value, parser)
    if value == "@-":
        if "stdin" not in stdin_cache:
            stdin_cache["stdin"] = sys.stdin.read()
        return _check_task_size(stdin_cache["stdin"], parser)

    raw_path = value[1:]
    if not raw_path:
        parser.error(f"{option_name}: file not found: {raw_path}")
    path = pathlib.Path(raw_path).expanduser()
    if not path.is_file():
        parser.error(f"{option_name}: file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        parser.error(f"{option_name}: file not UTF-8 text: {path}")
    except OSError as e:
        parser.error(f"{option_name}: unable to read file: {path}: {e}")
    return _check_task_size(text, parser)


def resolve_task_from_cmd(cmd_str: str, cwd: pathlib.Path, timeout: int,
                          parser: argparse.ArgumentParser) -> str:
    """Run a shell command and use stdout as the task seed."""
    global LIVE_PREFIX
    old_prefix = LIVE_PREFIX
    LIVE_PREFIX = LIVE_PREFIX_TASK
    try:
        rc, out, err = _run(["sh", "-c", cmd_str], cwd=cwd, stdin=None, timeout=timeout)
    finally:
        LIVE_PREFIX = old_prefix
    if rc != 0:
        parser.error(f"--task-from-cmd exited {rc}\nstderr:\n{err}")
    if out == "":
        parser.error(f"--task-from-cmd produced empty stdout\nstderr:\n{err}")
    return _check_task_size(out, parser)


def resolve_seed_inputs(*, task: Optional[str], kickoff: Optional[str],
                        task_from_cmd: Optional[str], cwd: pathlib.Path,
                        timeout: int, parser: argparse.ArgumentParser,
                        stdin_cache: dict[str, str]) -> tuple[Optional[str], Optional[str]]:
    if task is not None and task_from_cmd is not None:
        parser.error("--task and --task-from-cmd are mutually exclusive")
    resolved_kickoff = resolve_at_text(kickoff, "--kickoff", parser, stdin_cache)
    if task_from_cmd is not None:
        resolved_task = resolve_task_from_cmd(task_from_cmd, cwd, timeout, parser)
    else:
        resolved_task = resolve_at_text(task, "--task", parser, stdin_cache)
    return resolved_task, resolved_kickoff


def choose_runs_dir(raw_runs_dir: Optional[str], cwd_resolved: pathlib.Path) -> pathlib.Path:
    invocation_pwd = pathlib.Path.cwd().resolve()
    if raw_runs_dir is not None:
        return pathlib.Path(raw_runs_dir)
    if cwd_resolved != invocation_pwd:
        runs_dir = cwd_resolved / ".duet" / "runs"
        print("[duet] --cwd points outside the invocation directory; "
              f"defaulting run artifacts to {runs_dir}. "
              "Pass --runs-dir runs to use the legacy invocation-relative path.",
              file=sys.stderr)
        return runs_dir
    return pathlib.Path("runs")


# ---------- run-status (`duet --status <run_dir>`) ----------

def _pid_alive(pid: int) -> bool:
    """True if the OS process still exists. Uses signal 0 (no-op probe)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # PID exists but is owned by someone else — still "alive" for us.
        return True


def _proc_cmdline(pid: int) -> Optional[str]:
    """Best-effort read of a PID's full cmdline. Returns None on any failure.

    Used to validate that a recorded `duet_pid` still belongs to a duet
    process (PIDs get recycled after a reboot; the alive-check alone could
    point at an unrelated app).
    """
    if sys.platform.startswith("linux"):
        try:
            return (pathlib.Path(f"/proc/{pid}/cmdline")
                    .read_bytes().replace(b"\x00", b" ").decode(errors="replace"))
        except OSError:
            return None
    # macOS / BSD: shell out to ps. Cheap, ~5ms.
    try:
        r = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                           capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _is_duet_process(pid: int) -> bool:
    """True if `pid` is alive AND looks like a duet.py process (avoids stale-PID false positives)."""
    if not _pid_alive(pid):
        return False
    cmdline = _proc_cmdline(pid) or ""
    # Match "duet.py" anywhere in the cmdline OR a final path segment
    # equal to "duet" (when installed via `make install`).
    if "duet.py" in cmdline:
        return True
    # Look for ".../duet" or "duet " (the installed-symlink case).
    head = cmdline.split() and cmdline.split()[0]
    if head and pathlib.Path(head).name == "duet":
        return True
    return False


def print_run_status(arg: str) -> int:
    """Print a one-shot health summary for a duet run. Returns shell exit code:
    0 = run finished cleanly, 1 = still running, 2 = stuck/crashed, 3 = error.

    `arg` may be a path (absolute or relative) OR a bare run id like
    `20260507-082801` — bare ids get resolved against the same default
    search paths as `--list` (./runs/, ./.duet/runs/, ~/.duet/runs/*/).
    """
    run_dir = _resolve_run_dir(arg)
    if run_dir is None:
        print(f"[duet] no such run dir: {arg}", file=sys.stderr)
        if "/" not in arg and "\\" not in arg and _RUN_ID_RE.match(arg):
            print(f"[duet]   tried bare-id resolution under default paths "
                  "(./runs/, ./.duet/runs/, ~/.duet/runs/*/). "
                  "Use `duet --list` to see what's available.",
                  file=sys.stderr)
        return 3
    state_path = run_dir / "state.json"
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except json.JSONDecodeError as e:
            print(f"[duet] state.json malformed: {e}", file=sys.stderr)
            return 3
    finished = state.get("finished_reason")
    print(f"[duet] {run_dir}")
    print(f"  turns_used:      {state.get('turns_used', '?')}")
    print(f"  finished_reason: {finished!r}")

    # Find the in-flight turn via .pid file (only present while a turn runs).
    pid_files = sorted(run_dir.glob("turn-*.pid"))
    if pid_files:
        pid_file = pid_files[-1]
        try:
            pid = int(pid_file.read_text().strip())
        except (OSError, ValueError):
            pid = None
        # Filename: turn-<label>-<agent>.pid
        stem = pid_file.stem  # turn-02-claude-planner
        started_at = dt.datetime.fromtimestamp(pid_file.stat().st_mtime)
        elapsed = (dt.datetime.now() - started_at).total_seconds()
        alive = _pid_alive(pid) if pid is not None else False
        print(f"  in-flight turn: {stem}")
        print(f"    pid:          {pid}  (alive: {alive})")
        print(f"    started:      {started_at.isoformat(timespec='seconds')}  "
              f"({int(elapsed)}s ago)")
        # Heartbeat from the matching stderr log
        log = run_dir / f"{stem}.stderr.log"
        if log.exists():
            log_age = (dt.datetime.now()
                       - dt.datetime.fromtimestamp(log.stat().st_mtime)).total_seconds()
            print(f"    last stderr:  {int(log_age)}s ago "
                  f"({log.stat().st_size} bytes)")
        if not alive:
            print("    ⚠ pid file present but process is gone — turn likely "
                  "crashed or was killed without cleanup")
            return 2
        return 1
    # No pid files. Either run hasn't started, has finished, or is between
    # turns (in particular, sitting at the post-loop `force>` prompt).
    if finished:
        print(f"  done. transcript: {state.get('transcript_path', run_dir / 'transcript.md')}")
        return 0

    # Disambiguate "between turns" from "actually crashed" using the
    # duet_pid recorded in state.json.
    duet_pid = state.get("duet_pid")
    if duet_pid is not None:
        if _is_duet_process(int(duet_pid)):
            print(f"  state:           between turns / awaiting force> prompt")
            print(f"  duet pid:        {duet_pid} (alive)")
            history = state.get("history") or []
            if history:
                last = history[-1]
                print(f"  last completed:  turn {last.get('turn')} "
                      f"({last.get('agent')}) in {last.get('elapsed_s', 0):.1f}s, "
                      f"{last.get('len_chars', 0)} chars")
            return 1
        print(f"  ⚠ duet pid {duet_pid} no longer running (or recycled by an "
              "unrelated process); no finished_reason recorded — run died "
              "between turns")
        return 2

    # state.json predates the duet_pid field — keep the old message and the
    # old conservative "looks stuck" exit code so callers don't regress.
    print("  no in-flight turn AND no finished_reason — run may have died "
          "between turns, or hasn't started yet")
    print("  (state.json predates the duet_pid field; can't auto-distinguish "
          "alive-between-turns from crashed)")
    return 2


# ---------- run-list (`duet --list [PATH]`) ----------

# Status glyphs — same vocabulary as print_run_status, packed for table cols.
_LIST_STATUS_FINISHED = {
    "converged":           ("✅", "converged"),
    "converged_after_force": ("✅", "converged"),
    "max_turns":           ("⏰", "max_turns"),
    "force_stop":          ("🔴", "force_stop"),
    "forced_continuation": ("🟡", "forced"),
    "agent_error":         ("⚠", "agent_error"),
}


_RUN_ID_RE = re.compile(r"^\d{8}-\d{6}(?:-\d+)?$")


def _default_list_paths() -> list[pathlib.Path]:
    """Where `duet --list` looks when no PATH is given. Order = display order."""
    paths: list[pathlib.Path] = []
    for p in (pathlib.Path.cwd() / "runs",
              pathlib.Path.cwd() / ".duet" / "runs"):
        if p.is_dir():
            paths.append(p)
    home = pathlib.Path.home() / ".duet" / "runs"
    if home.is_dir():
        # Each subdir under ~/.duet/runs/ is a slug like "Users-volkan-…".
        for slug in sorted(home.iterdir()):
            if slug.is_dir():
                paths.append(slug)
    return paths


def _resolve_run_dir(arg: str) -> Optional[pathlib.Path]:
    """Map a `--status` argument to a real run dir.

    Accepts:
      - an absolute or relative path that exists
      - a bare run id like `20260507-082801`, resolved against the default
        list paths so users don't have to remember `runs/` vs `.duet/runs/`

    Returns the resolved Path, or None when nothing matches.
    """
    p = pathlib.Path(arg).expanduser()
    if p.is_dir():
        return p.resolve()
    # Bare run id (no path separators, matches the timestamp pattern) — search.
    if "/" not in arg and "\\" not in arg and _RUN_ID_RE.match(arg):
        candidates = [root / arg for root in _default_list_paths()
                      if (root / arg).is_dir()]
        if len(candidates) == 1:
            return candidates[0].resolve()
        if len(candidates) > 1:
            # Same id under multiple roots is rare (timestamps are seconds-
            # precise) but possible. Prefer most-recently-modified and warn.
            candidates.sort(key=lambda c: c.stat().st_mtime, reverse=True)
            print(f"[duet] note: run id {arg!r} found under multiple roots; "
                  f"using most recent: {candidates[0]}",
                  file=sys.stderr)
            return candidates[0].resolve()
    return None


def _humanize_age(seconds: int) -> str:
    if seconds < 60:           return f"{seconds}s ago"
    if seconds < 3600:         return f"{seconds // 60}m ago"
    if seconds < 86400:        return f"{seconds // 3600}h ago"
    if seconds < 7 * 86400:    return f"{seconds // 86400}d ago"
    return f"{seconds // 86400}d ago"


def _last_activity_mtime(run_dir: pathlib.Path) -> Optional[float]:
    """Most recent mtime across state.json + per-turn .pid/.stderr.log files."""
    candidates = [run_dir / "state.json", *run_dir.glob("turn-*.pid"),
                  *run_dir.glob("turn-*.stderr.log")]
    mtimes = []
    for c in candidates:
        try:
            mtimes.append(c.stat().st_mtime)
        except OSError:
            pass
    return max(mtimes) if mtimes else None


def _classify_run(run_dir: pathlib.Path) -> tuple[str, str, dict]:
    """Returns (emoji, label, state_dict). Mirrors print_run_status's logic."""
    state_path = run_dir / "state.json"
    if not state_path.is_file():
        return ("❓", "no state.json", {})
    try:
        state = json.loads(state_path.read_text())
    except json.JSONDecodeError:
        return ("❓", "malformed state", {})
    finished = state.get("finished_reason")
    if finished:
        emoji, label = _LIST_STATUS_FINISHED.get(finished, ("✅", finished))
        return (emoji, label, state)
    # No finished_reason — running, between turns, or crashed.
    if list(run_dir.glob("turn-*.pid")):
        return ("🟢", "in-flight", state)
    pid = state.get("duet_pid")
    if pid is not None and _is_duet_process(int(pid)):
        return ("🟢", "between turns", state)
    if pid is not None:
        return ("⚠", "duet died", state)
    return ("⚠", "stuck (no pid)", state)


def print_runs_list(explicit_path: Optional[pathlib.Path]) -> int:
    """`duet --list [PATH]` — print one row per run dir found."""
    if explicit_path is not None:
        roots = [explicit_path.expanduser().resolve()]
    else:
        roots = _default_list_paths()
    if not roots:
        print("[duet] no run dirs found.\n"
              "  Searched ./runs/, ./.duet/runs/, and ~/.duet/runs/*/. "
              "Pass an explicit path: duet --list <DIR>", file=sys.stderr)
        return 0

    rows: list[dict] = []
    now = time.time()
    for root in roots:
        if not root.is_dir():
            print(f"[duet] {root}: not a directory", file=sys.stderr)
            continue
        for child in sorted(root.iterdir(), reverse=True):
            if not child.is_dir() or not _RUN_ID_RE.match(child.name):
                continue
            emoji, label, state = _classify_run(child)
            mtime = _last_activity_mtime(child)
            age = _humanize_age(int(now - mtime)) if mtime else "—"
            history = state.get("history") or []
            turns_used = state.get("turns_used", len(history))
            rows.append({
                "emoji": emoji, "label": label,
                "id": child.name, "turns": turns_used,
                "age": age, "dir": str(child),
            })

    if not rows:
        print(f"[duet] no runs found under: {', '.join(str(r) for r in roots)}",
              file=sys.stderr)
        return 0

    rows.sort(key=lambda r: r["id"], reverse=True)
    # Column widths
    w_id    = max(len("run id"),     max(len(r["id"])    for r in rows))
    w_label = max(len("status"),     max(len(r["label"]) for r in rows))
    w_turns = max(len("turns"),      max(len(str(r["turns"])) for r in rows))
    w_age   = max(len("activity"),   max(len(r["age"])   for r in rows))
    print(f"  {'':2}  {'run id':<{w_id}}  {'status':<{w_label}}  "
          f"{'turns':<{w_turns}}  {'activity':<{w_age}}  dir")
    print(f"  {'':2}  {'-'*w_id}  {'-'*w_label}  {'-'*w_turns}  "
          f"{'-'*w_age}  ---")
    for r in rows:
        print(f"  {r['emoji']:2}  {r['id']:<{w_id}}  {r['label']:<{w_label}}  "
              f"{str(r['turns']):<{w_turns}}  {r['age']:<{w_age}}  {r['dir']}")
    print(f"\n  {len(rows)} run(s). Per-run health: duet --status <dir>")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="duet — two CLI agents in conversation, with per-agent session memory.")
    ap.add_argument("--resume-claude", metavar="SESSION_ID",
                    help="resume an existing Claude session id; harness will pull "
                         "its latest message and feed it to the partner agent.")
    ap.add_argument("--resume-codex", metavar="SESSION_ID",
                    help="(advanced) seed codex with an existing session id.")
    ap.add_argument("--task", help="task description, @file, or @- stdin "
                                   "(used if no --resume-* and no --kickoff)")
    ap.add_argument("--kickoff", help="explicit first message, @file, or @- stdin "
                                      "to send to the partner agent")
    ap.add_argument("--task-from-cmd", metavar="CMD",
                    help="run shell command with cwd=--cwd and use stdout as the task")
    ap.add_argument("--partner", default="codex:coder",
                    help="partner agent spec, e.g. codex:coder, claude:reviewer (default codex:coder)")
    ap.add_argument("--lead", default="claude:planner",
                    help="lead agent spec, e.g. claude:planner (default; ignored if --resume-claude given)")
    ap.add_argument("--cwd", default=".", help="working dir for both agents")
    ap.add_argument("--turns", type=int, default=DEFAULT_TURNS, help=f"max turns (default {DEFAULT_TURNS})")
    ap.add_argument("--sentinel", default=DEFAULT_SENTINEL, help="convergence sentinel")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="per-turn timeout seconds")
    ap.add_argument("--runs-dir", default=None, help="where to save transcripts")
    ap.add_argument("--sandbox", default="workspace-write",
                    help="codex --sandbox: read-only|workspace-write|danger-full-access")
    ap.add_argument("--permission-mode", default="acceptEdits",
                    help="claude --permission-mode: default|acceptEdits|plan|bypassPermissions")
    ap.add_argument("--config", help="optional YAML/JSON config (overrides flags except --resume-claude)")
    ap.add_argument("--worktree", action="store_true",
                    help="run the partner agent in a throwaway git worktree on a fresh branch; "
                         "the worktree is left intact at the end so you can review/merge/drop it.")
    ap.add_argument("--worktree-for", choices=["partner", "lead"], default="partner",
                    help="which agent runs in the worktree (default: partner)")
    ap.add_argument("--worktree-path", metavar="PATH", default=None,
                    help="reuse an EXISTING worktree (e.g. from a previous cancelled run). "
                         "Codex's `exec resume --last` will pick up where it left off in this cwd. "
                         "Skips git worktree creation. Mutually exclusive with --worktree.")
    ap.add_argument("--worktree-root", metavar="PATH", default=None,
                    help="parent directory for newly-created worktrees (used with --worktree). "
                         "Each run lands at <PATH>/<run_id>/. Default: <runs_dir>/<run_id>/wt/, "
                         "which is durable across reboots and OS temp-dir cleaners. "
                         "Pass /tmp or $TMPDIR to mimic the pre-fix throwaway behavior.")
    ap.add_argument("--add-dir", action="append", metavar="PATH", default=[],
                    dest="add_dirs",
                    help="extra path claude is allowed to read/write outside cwd. "
                         "Repeatable. Without this, tasks that touch ../foo or "
                         "absolute paths outside --cwd silently fail with a "
                         "permission error. YAML key: `add_dirs:` (list).")
    ap.add_argument("--reasoning", choices=REASONING_LEVELS, default=None,
                    help="reasoning effort for both agents. Codex: passes "
                         "`-c model_reasoning_effort=<v>` except for medium "
                         "(max → xhigh). Claude: passes `--effort <v>` "
                         "(minimal → low) and adds high/max prompt nudges.")
    ap.add_argument("--status", metavar="RUN_DIR_OR_ID", default=None,
                    help="don't run a duet — instead print a one-shot health "
                         "summary of an existing run and exit. Accepts a path "
                         "(absolute or relative) OR a bare run id like "
                         "`20260507-082801` (resolved against the same "
                         "default paths as `--list`: ./runs/, ./.duet/runs/, "
                         "~/.duet/runs/*/). Exit codes: 0=done, 1=running, "
                         "2=stuck/crashed, 3=error.")
    ap.add_argument("--list", metavar="PATH", nargs="?", const="__defaults__",
                    default=None, dest="list_runs",
                    help="don't run a duet — instead list runs found under "
                         "PATH (or under the default search paths if PATH is "
                         "omitted: ./runs/, ./.duet/runs/, ~/.duet/runs/*/). "
                         "Each row shows status, turns_used, last-activity "
                         "age, and dir. Pair with `--status <dir>` to drill "
                         "into a specific run.")
    ap.add_argument("--quiet", action="store_true",
                    help="don't mirror subprocess stderr to your terminal in real-time. "
                         "By default, duet prints Codex's live progress as it works.")
    ap.add_argument("--dry-run", action="store_true", help="don't actually call CLIs")
    args = ap.parse_args()

    # `--status` is read-only: print run health and exit. Skip everything below.
    if args.status:
        return print_run_status(args.status)

    # `--list` is read-only: print the run-dir table and exit.
    if args.list_runs is not None:
        explicit = (None if args.list_runs == "__defaults__"
                    else pathlib.Path(args.list_runs))
        return print_runs_list(explicit)

    if args.worktree and args.worktree_path:
        ap.error("--worktree and --worktree-path are mutually exclusive")

    # Live-stream subprocess stderr unless --quiet
    global LIVE_STREAM
    LIVE_STREAM = not args.quiet

    stdin_cache: dict[str, str] = {}
    if args.config:
        raw = load_yaml_or_json(pathlib.Path(args.config))
        cfg_cwd = pathlib.Path(raw.get("cwd", ".")).expanduser().resolve()
        cfg_timeout = int(raw.get("per_turn_timeout", DEFAULT_TIMEOUT))
        raw_task = raw.get("task")
        raw_kickoff = raw.get("kickoff")
        raw_task_from_cmd = raw.get("task_from_cmd")
        if raw_task is None and raw_kickoff is None and raw_task_from_cmd is None:
            raw_task = args.task
            raw_kickoff = args.kickoff
            raw_task_from_cmd = args.task_from_cmd
        task, kickoff = resolve_seed_inputs(
            task=raw_task,
            kickoff=raw_kickoff,
            task_from_cmd=raw_task_from_cmd,
            cwd=cfg_cwd,
            timeout=cfg_timeout,
            parser=ap,
            stdin_cache=stdin_cache,
        )
        raw_runs_dir = args.runs_dir if args.runs_dir is not None else raw.get("runs_dir")
        runs_dir = choose_runs_dir(raw_runs_dir, cfg_cwd)
        agents = [Agent(**a) for a in raw.get("agents", [])]
        cfg = DuetConfig(
            cwd=cfg_cwd,
            agents=agents,
            task=task,
            kickoff=kickoff,
            max_turns=int(raw.get("max_turns", DEFAULT_TURNS)),
            sentinel=raw.get("sentinel", DEFAULT_SENTINEL),
            per_turn_timeout=cfg_timeout,
            runs_dir=runs_dir,
            sandbox=raw.get("sandbox", "workspace-write"),
            permission_mode=raw.get("permission_mode", "acceptEdits"),
            dry_run=bool(raw.get("dry_run", False)),
            worktree=bool(raw.get("worktree", False)) or args.worktree,
            worktree_for=raw.get("worktree_for", args.worktree_for),
            worktree_path=(pathlib.Path(args.worktree_path).expanduser().resolve()
                           if args.worktree_path
                           else (pathlib.Path(raw["worktree_path"]).expanduser().resolve()
                                 if raw.get("worktree_path") else None)),
            worktree_root=(pathlib.Path(args.worktree_root).expanduser().resolve()
                           if args.worktree_root
                           else (pathlib.Path(raw["worktree_root"]).expanduser().resolve()
                                 if raw.get("worktree_root") else None)),
            add_dirs=[
                pathlib.Path(d).expanduser().resolve()
                for d in (args.add_dirs or raw.get("add_dirs", []))
            ],
            reasoning=args.reasoning or raw.get("reasoning"),
        )
        # CLI overrides for resume
        if args.resume_claude and cfg.agents and cfg.agents[0].backend == "claude":
            cfg.agents[0].session_id = args.resume_claude
    else:
        cfg_cwd = pathlib.Path(args.cwd).expanduser().resolve()
        task, kickoff = resolve_seed_inputs(
            task=args.task,
            kickoff=args.kickoff,
            task_from_cmd=args.task_from_cmd,
            cwd=cfg_cwd,
            timeout=args.timeout,
            parser=ap,
            stdin_cache=stdin_cache,
        )
        runs_dir = choose_runs_dir(args.runs_dir, cfg_cwd)
        # Build agents from --lead / --partner / --resume-claude
        if args.resume_claude:
            lead = Agent(name="claude-lead", backend="claude",
                         role="planner", session_id=args.resume_claude)
        else:
            lead = parse_partner(args.lead, default_role="planner")
            lead.name = f"{lead.backend}-lead"
        partner = parse_partner(args.partner, default_role="coder")
        partner.name = f"{partner.backend}-partner"
        if args.resume_codex and partner.backend == "codex":
            partner.session_id = args.resume_codex

        cfg = DuetConfig(
            cwd=cfg_cwd,
            agents=[lead, partner],
            task=task,
            kickoff=kickoff,
            max_turns=args.turns,
            sentinel=args.sentinel,
            per_turn_timeout=args.timeout,
            runs_dir=runs_dir,
            sandbox=args.sandbox,
            permission_mode=args.permission_mode,
            dry_run=args.dry_run,
            worktree=args.worktree,
            worktree_for=args.worktree_for,
            worktree_path=(pathlib.Path(args.worktree_path).expanduser().resolve()
                           if args.worktree_path else None),
            worktree_root=(pathlib.Path(args.worktree_root).expanduser().resolve()
                           if args.worktree_root else None),
            add_dirs=[pathlib.Path(d).expanduser().resolve() for d in args.add_dirs],
            reasoning=args.reasoning,
        )

    validate_reasoning(cfg.reasoning, "config reasoning")
    for agent in cfg.agents:
        validate_reasoning(agent.reasoning_effort, f"agent {agent.name} reasoning_effort")
    if cfg.worktree and cfg.worktree_path:
        raise SystemExit("--worktree and --worktree-path/worktree_path are mutually exclusive")

    # Sanity: are CLIs on PATH?
    if not cfg.dry_run:
        for b in {a.backend for a in cfg.agents}:
            if shutil.which(b) is None:
                print(f"[duet] WARNING: '{b}' not on PATH — this run will fail. "
                      f"Install it or use --dry-run.", file=sys.stderr)

    run_duet(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
