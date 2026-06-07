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
     Claude remembers the whole prior conversation). Ping-pong until both
     agents propose convergence with an LGTM rationale and <<<LGTM>>> in
     back-to-back turns, --turns is hit, or you Ctrl-C.

Each agent keeps its own session across turns:
  - Claude: `claude -p --resume <session_id> --output-format json` — we capture
    `session_id` from the JSON wrapper and reuse it.
  - Codex:  first turn `codex exec ...`; subsequent turns `codex exec resume <uuid>`
    when we parsed a session id from Codex's stderr, or `codex exec resume --last`
    in the same cwd as a fallback for builds that don't print one. Pinning the
    UUID makes resume robust to parallel Codex sessions sharing the cwd, but
    `--last` is still keyed on cwd — use `--worktree` to isolate duet's Codex
    cwd from the host repo when no UUID is available.

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
import shlex
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
CONVERGENCE_RATIONALE_MIN_CHARS = 20
VERIFY_OUTPUT_TAIL_CHARS = 4000
VERIFY_LIVE_PREFIX = "  │ [verify] "
SUPPORTED_BACKENDS = {"claude", "codex"}
WORKTREE_FOR_CHOICES = {"lead", "partner"}

RECAP_ADDENDUM = """Format requirement (debug tooling reads these):

Begin every reply with three header lines, then a blank line, then your full reply:

  RECAP: <one short sentence describing what you produced this turn>
  FILES: <comma-separated paths you touched or referenced, or "none">
  STATUS: <one of: planning | implementing | reviewing | requesting-changes | ready-for-review | converged>

The headers DO NOT replace your reply — write your normal answer as usual after the blank line. Use STATUS: converged only when you would also emit the convergence sentinel with an LGTM rationale."""

CONVERGENCE_INSTRUCTION = (
    "Convergence requires pair agreement, not just the sentinel. When you "
    "believe the loop should stop, include a concise `LGTM rationale:` line or "
    "paragraph that explains why the result satisfies the task, what you "
    "checked, and any remaining low-risk follow-ups; then put {SENTINEL} on "
    "its own line. A bare sentinel without that rationale is ignored. If your "
    "partner proposed convergence and you disagree with the rationale, do not "
    "emit the sentinel; explain the gap and ask for another round."
)

ROLE_PROMPTS = {
    "planner": (
        "You are the PLANNER half of a duet. Read the partner agent's latest "
        "message and propose or refine a plan. Be concrete: file names, "
        "functions, edge cases. You may also write or edit non-code "
        "deliverables yourself when the task asks for them — synthesis "
        "documents, reports, comparison matrices, configuration, README "
        "updates, dashboards, etc. What you should NOT do is write production "
        "feature code (that's the coder's job). When you believe the work is "
        "fully done and reviewed, follow the convergence instructions."
    ),
    "coder": (
        "You are the CODER half of a duet. Read the partner agent's latest "
        "message (typically a plan or critique) and produce code. Apply edits "
        "to disk. Run quick checks where reasonable. Summarise what you "
        "changed. When you believe the work is fully done, follow the "
        "convergence instructions."
    ),
    "reviewer": (
        "You are the REVIEWER half of a duet. Read the partner agent's "
        "latest message and critically evaluate it: bugs, missing tests, "
        "security, simpler designs. When reviewing concrete code changes, "
        "inspect the actual files, diffs, and test output rather than relying "
        "only on the partner's summary. Be specific and brief. If the work "
        "meets the task and you have no material issues, follow the "
        "convergence instructions."
    ),
    "triage-reviewer": (
        "You are the TRIAGE REVIEWER half of a duet. Read the partner agent's "
        "latest message and critically evaluate it: bugs, missing tests, "
        "security, simpler designs. Score every finding with [P0], [P1], "
        "[P2], or [P3]. Default to [P3]; promote only when the impact is "
        "concrete. [P0] means a correctness, security, data-loss, or shipped-"
        "check blocker. [P1] means a real bug, logic gap, or missing edge case "
        "that should block this loop. [P2] means a small bug, polish issue, "
        "or naming/readability fix that is nice to handle. [P3] means a "
        "follow-up, future refactor, or scope creep. When reviewing concrete "
        "code changes, inspect the actual files, diffs, and test output rather "
        "than relying only on the partner's summary. Be specific and brief. "
        "If the coder reasonably argues a finding is over-scored, either "
        "accept the lower score or explain why the higher score still applies. "
        "Emit convergence only when no unfixed [P0] or [P1] findings remain. "
        "When only [P2]/[P3] items remain, move them to a Follow-ups section "
        "and follow the convergence instructions."
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
# users can choose the common `xhigh` level directly while still getting useful
# aliases for backend-specific gaps (`minimal` for Codex, `max` for Claude).
REASONING_LEVELS = ["minimal", "low", "medium", "high", "xhigh", "max"]

# Claude Code exposes thinking control through `--effort`. We still add small
# prompt nudges for high/xhigh/max because they are useful natural-language
# guidance. `ultrathink` is a recognized one-turn in-context nudge in current
# Claude Code; the CLI flag below remains the authoritative effort control.
CLAUDE_REASONING_PROMPT_PREFIX = {
    "minimal": "",
    "low":     "",
    "medium":  "",
    "high":    "think hard and reason step-by-step before answering. Cover edge cases.\n\n",
    "xhigh":   "think very hard and reason carefully before answering. Cover "
               "edge cases, alternatives, and risks.\n\n",
    "max":     "ultrathink — reason exhaustively before answering. Enumerate edge "
               "cases, alternatives, and risks. Do not skim.\n\n",
}

# Claude Code `--effort` accepts low, medium, high, xhigh, max. The duet
# abstraction has `minimal` for Codex, so Claude maps that user-facing value to
# its lowest documented level.
CLAUDE_REASONING_MAP = {
    "minimal": "low",
    "low":     "low",
    "medium":  "medium",
    "high":    "high",
    "xhigh":   "xhigh",
    "max":     "max",
}

# Codex CLI takes a config override `-c model_reasoning_effort=<value>`.
# Its accepted values, lowest→highest, are: minimal, low, medium, high, xhigh.
# We also map duet's `max` alias to Codex's `xhigh` because Codex does not
# document a separate `max` effort value.
CODEX_REASONING_MAP = {
    "minimal": "minimal",
    "low":     "low",
    "medium":  "medium",
    "high":    "high",
    "xhigh":   "xhigh",
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
    role: str = "coder"             # planner | coder | reviewer | triage-reviewer | custom
    role_prompt: Optional[str] = None
    model: Optional[str] = None
    session_id: Optional[str] = None  # tracked across turns
    extra_args: list[str] = dataclasses.field(default_factory=list)
    cwd_override: Optional[pathlib.Path] = None  # set when this agent runs in a git worktree
    reasoning_effort: Optional[str] = None  # one of REASONING_LEVELS; overrides cfg.reasoning

    def system_prompt(self, sentinel: str, recap: bool = False) -> str:
        tmpl = self.role_prompt or ROLE_PROMPTS.get(self.role)
        if tmpl is None:
            raise SystemExit(f"unknown role '{self.role}' for agent '{self.name}' — "
                             "supply role_prompt to override")
        # str.replace, not str.format — role prompts often contain literal
        # `{...}` (JSON schema, code samples, jq patterns). format() would
        # parse those as format fields and crash with "unexpected '{' in
        # field name". replace handles them as plain text.
        prompt = tmpl.replace("{SENTINEL}", sentinel)
        prompt += "\n\n" + CONVERGENCE_INSTRUCTION.replace("{SENTINEL}", sentinel)
        if recap:
            prompt += "\n\n" + RECAP_ADDENDUM
        return prompt


def agent_state(a: Agent) -> dict:
    data = {
        "name": a.name,
        "backend": a.backend,
        "role": a.role,
        "session_id": a.session_id,
    }
    if a.role_prompt is not None:
        data["role_prompt"] = a.role_prompt
    if a.model is not None:
        data["model"] = a.model
    if a.extra_args:
        data["extra_args"] = a.extra_args
    if a.reasoning_effort is not None:
        data["reasoning_effort"] = a.reasoning_effort
    return data


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
    recap: bool = False
    verify_cmd: Optional[str] = None          # shell command that must pass before
                                             # a convergence proposal can count
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
    codex_fast: bool = False                  # Codex-only "fast mode": pin reasoning to
                                              # low and add `model_reasoning_summary=concise`
                                              # for codex coder turns this run, regardless of
                                              # cfg.reasoning / agent.reasoning_effort. Claude's
                                              # effort is untouched, so `--reasoning high
                                              # --codex-fast` keeps the planner deep and the
                                              # coder snappy.
    start_speaker_idx: int = 1                # default loop starts with partner replying
    continue_from: Optional[str] = None       # prior run dir/id when created by --continue


def _config_error(message: str,
                  parser: Optional[argparse.ArgumentParser] = None) -> None:
    if parser is not None:
        parser.error(message)
    raise SystemExit(message)


def validate_config(cfg: DuetConfig,
                    parser: Optional[argparse.ArgumentParser] = None) -> None:
    """Validate final topology after CLI/YAML parsing and resume normalization."""
    if len(cfg.agents) != 2:
        _config_error(f"duet expects exactly 2 agents, got {len(cfg.agents)}", parser)
    if cfg.start_speaker_idx not in (0, 1):
        _config_error(
            f"start_speaker_idx must be 0 or 1, got {cfg.start_speaker_idx}",
            parser,
        )
    if cfg.worktree_for not in WORKTREE_FOR_CHOICES:
        choices = "|".join(sorted(WORKTREE_FOR_CHOICES))
        _config_error(
            f"worktree_for must be one of {choices}, got {cfg.worktree_for!r}",
            parser,
        )

    seen_names: set[str] = set()
    for agent in cfg.agents:
        if agent.backend not in SUPPORTED_BACKENDS:
            choices = "|".join(sorted(SUPPORTED_BACKENDS))
            _config_error(
                f"unknown backend {agent.backend!r} for agent {agent.name!r}; "
                f"expected {choices}",
                parser,
            )
        if agent.name in seen_names:
            _config_error(
                f"duplicate agent name {agent.name!r}; agent names must be unique",
                parser,
            )
        seen_names.add(agent.name)


def effective_agent_cwd(agent: Agent, default_cwd: pathlib.Path) -> pathlib.Path:
    return (agent.cwd_override or default_cwd).resolve()


def shared_cwd_codex_peers(cfg: DuetConfig) -> bool:
    codex_agents = [a for a in cfg.agents if a.backend == "codex"]
    if len(codex_agents) != 2:
        return False
    return (
        effective_agent_cwd(codex_agents[0], cfg.cwd)
        == effective_agent_cwd(codex_agents[1], cfg.cwd)
    )


def codex_session_is_uuid(agent: Agent) -> bool:
    return bool(agent.session_id and _CODEX_UUID_RE.match(agent.session_id))


def codex_shared_cwd_isolation_error(agent: Agent) -> str:
    return (
        "[duet] fatal: cannot safely continue codex/codex peering in one cwd "
        f"because {agent.name} did not produce a Codex session UUID. "
        "`codex exec resume --last` is cwd-based and could resume the other "
        "Codex peer's session. Use --worktree/--worktree-for to isolate one "
        "peer, or use a Codex build that reliably emits `session id: <uuid>`."
    )


def guard_codex_shared_cwd_before_call(cfg: DuetConfig,
                                       agent: Agent,
                                       first_turn_for_agent: bool) -> None:
    if cfg.dry_run or agent.backend != "codex" or not shared_cwd_codex_peers(cfg):
        return
    if (not first_turn_for_agent
            and agent.session_id
            and not codex_session_is_uuid(agent)):
        raise SystemExit(codex_shared_cwd_isolation_error(agent))


def guard_codex_shared_cwd_after_call(cfg: DuetConfig,
                                      agent: Agent,
                                      first_turn_for_agent: bool) -> None:
    if cfg.dry_run or agent.backend != "codex" or not first_turn_for_agent:
        return
    if shared_cwd_codex_peers(cfg) and not codex_session_is_uuid(agent):
        raise SystemExit(codex_shared_cwd_isolation_error(agent))


@dataclasses.dataclass
class VerifyResult:
    ok: bool
    cmd: str
    cwd: pathlib.Path
    exit_code: Optional[int]
    stdout_tail: str
    stderr_tail: str
    log_path: pathlib.Path
    timed_out: bool = False
    error: Optional[str] = None


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
        untracked = _untracked_files_summary(wt_path, max_chars=max_chars)
        untracked_block = (
            f"\n\n### untracked file contents\n{untracked}"
            if untracked else ""
        )
        return (
            f"### git status\n{status or '(clean)'}\n\n"
            f"### diffstat\n{diff or '(none)'}\n\n"
            f"### diff\n{full or '(none)'}"
            f"{untracked_block}"
        )
    except subprocess.TimeoutExpired:
        return "[duet] git diff timed out"


def _untracked_files_summary(wt_path: pathlib.Path, max_chars: int = 8000) -> str:
    proc = subprocess.run(
        ["git", "-C", str(wt_path), "ls-files", "--others",
         "--exclude-standard", "-z"],
        capture_output=True, timeout=10,
    )
    if proc.returncode != 0:
        return ""
    rel_paths = [os.fsdecode(p) for p in proc.stdout.split(b"\0") if p]
    if not rel_paths:
        return ""

    sections: list[str] = []
    remaining = max_chars
    for rel_path in rel_paths:
        if remaining <= 0:
            sections.append("…[truncated]")
            break
        section = _untracked_file_summary(wt_path, rel_path)
        if len(section) > remaining:
            section = section[:remaining] + f"\n…[truncated, {len(section)-remaining} more chars]"
            sections.append(section)
            break
        sections.append(section)
        remaining -= len(section) + 2
    return "\n\n".join(sections)


def _untracked_file_summary(wt_path: pathlib.Path, rel_path: str) -> str:
    display_path = rel_path.replace("\\", "/")
    file_path = wt_path / rel_path
    if file_path.is_symlink():
        return f"#### {display_path}\n(symlink omitted)"
    if not file_path.is_file():
        return f"#### {display_path}\n(non-file omitted)"
    try:
        data = _read_file_preview(file_path)
    except OSError as e:
        return f"#### {display_path}\n(unable to read: {e})"
    if data is None:
        return f"#### {display_path}\n(binary file omitted)"
    fence = _markdown_fence(data)
    return f"#### {display_path}\n{fence}text\n{data}\n{fence}"


def _read_file_preview(path: pathlib.Path, max_bytes: int = 12000) -> Optional[str]:
    with path.open("rb") as f:
        data = f.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    if b"\0" in data:
        return None
    text = data.decode("utf-8", errors="replace")
    if truncated:
        text += f"\n…[truncated, file exceeds {max_bytes} bytes]"
    return text


def _markdown_fence(text: str) -> str:
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _worktree_handoff_block(wt_path: pathlib.Path,
                            wt_branch: Optional[str] = None) -> str:
    """Tell the receiving agent exactly where the edited tree lives.

    Worded for clean turns too — the worktree-agent may have only explored
    this turn, so we say "any code changes" rather than asserting changes
    exist. Suggested commands are intentionally project-agnostic; project
    test commands belong in CLAUDE.md / README, not in this generic block.
    """
    wt_display = str(wt_path)
    wt_arg = shlex.quote(wt_display)
    branch_line = f"- Branch: `{wt_branch}`\n" if wt_branch else ""
    return (
        "### review target\n"
        "Any code changes for this turn are in the git worktree below. "
        "Your current cwd may be a clean checkout, so do not use that cwd's "
        "`git status` as evidence that these edits are absent.\n\n"
        f"- Worktree path: `{wt_display}`\n"
        f"{branch_line}"
        "\n"
        "Use the worktree as the source of truth when reviewing or running "
        "checks:\n\n"
        "```bash\n"
        f"git -C {wt_arg} status --short\n"
        f"git -C {wt_arg} diff HEAD\n"
        "```\n"
    )


def append_worktree_diff(reply: str, wt_path: pathlib.Path,
                         wt_branch: Optional[str] = None) -> str:
    try:
        diff_block = git_diff_summary(wt_path)
        handoff = _worktree_handoff_block(wt_path, wt_branch)
        return (f"{reply}\n\n---\n"
                f"#### worktree changes ({wt_path.name})\n{handoff}\n"
                f"{diff_block}")
    except Exception as e:
        return f"{reply}\n\n[duet] git diff failed: {e}"


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
RECAP_MODE = False


def _stream_reader(stream, sink: list[str], mirror_to=None, prefix: str = "",
                   tee_to=None, activity_event=None):
    """Drain a pipe line-by-line, capture into `sink`, optionally mirror live and/or tee to file.

    `mirror_to` is a writable text stream (typically sys.stderr) that the
    line is echoed to with `prefix`. `tee_to` is an open file handle that
    receives the raw line — used to persist the live stream for post-hoc
    forensics. `activity_event`, if given, is `set()` on every received
    line so a heartbeat thread can detect "subprocess went quiet". All
    parameters are optional.
    """
    try:
        for line in iter(stream.readline, ""):
            sink.append(line)
            if activity_event is not None:
                activity_event.set()
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


def _quiet_heartbeat(proc, mirror_to, start_monotonic: float,
                     activity_event, interval: int = 20,
                     prefix: str = LIVE_PREFIX) -> None:
    """Print "[duet] still working…" when a subprocess goes quiet.

    Most subprocesses emit rich stderr live (codex, gh, npm). Some don't
    — `claude -p` is silent on stderr during the API call, so a long
    seed-extract or claude turn can look like duet has hung. This thread
    waits on `activity_event`; if no activity for `interval` seconds AND
    the subprocess is still alive, it prints elapsed time and resets.
    Mirrors duet's own stderr so it interleaves with live output.
    """
    if mirror_to is None:
        return
    while proc.poll() is None:
        if activity_event.wait(timeout=interval):
            activity_event.clear()
            continue
        if proc.poll() is not None:
            return
        try:
            elapsed = int(time.monotonic() - start_monotonic)
            mirror_to.write(f"{prefix}[duet] still working… ({elapsed}s; "
                            "subprocess silent — typical for `claude -p`)\n")
            mirror_to.flush()
        except Exception:
            return


def _run(cmd: list[str], *, cwd: pathlib.Path, stdin: Optional[str], timeout: int,
         stderr_log_path: Optional[pathlib.Path] = None,
         pid_file_path: Optional[pathlib.Path] = None,
         live_prefix: Optional[str] = None,
         mirror_stdout: bool = False) -> tuple[int, str, str]:
    """Run a subprocess. Returns (rc, stdout, stderr).

    If LIVE_STREAM is on AND stderr is a TTY, the child's stderr is mirrored
    to our stderr line-by-line as it's produced. stdout is captured silently
    unless `mirror_stdout` is set — duet logs agent final answers to the
    transcript afterwards.

    If `stderr_log_path` is set, the child's stderr is also tee'd line-by-line
    to that file (append mode) — useful for post-hoc forensics on long agent
    turns where the live trace is otherwise lost.

    If `pid_file_path` is set, the child's PID is written there at startup
    and the file is removed when the call returns. External tools can read
    the file + `kill -0 <pid>` to tell apart "duet is alive, agent thinking"
    vs "agent crashed silently". Critical for agents like `claude -p` that
    emit no stderr during their long API call.
    """
    mirror = sys.stderr if (LIVE_STREAM and not RECAP_MODE and sys.stderr.isatty()) else None
    prefix = live_prefix if live_prefix is not None else LIVE_PREFIX
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
        activity_event = threading.Event()
        t_out = threading.Thread(target=_stream_reader,
                                 args=(proc.stdout, out_chunks,
                                       mirror if mirror_stdout else None,
                                       prefix if mirror_stdout else "",
                                       None, activity_event),
                                 daemon=True)
        t_err = threading.Thread(target=_stream_reader,
                                 args=(proc.stderr, err_chunks, mirror, prefix,
                                       stderr_file, activity_event),
                                 daemon=True)
        t_out.start(); t_err.start()
        # Heartbeat: print elapsed-time hint when proc goes quiet (>20s no
        # stderr/stdout). Useful for `claude -p`, which stays silent on
        # stderr during the API call. No-op if mirror is None (--quiet).
        t_hb = threading.Thread(target=_quiet_heartbeat,
                                args=(proc, mirror, time.monotonic(), activity_event, 20, prefix),
                                daemon=True)
        t_hb.start()

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


# ---------- verification gate ----------

def effective_verify_cwd(cfg: DuetConfig,
                         worktree_path: Optional[pathlib.Path]) -> pathlib.Path:
    """Return the directory where the convergence verify command should run."""
    return worktree_path or cfg.cwd


def _tail_text(text: str, max_chars: int = VERIFY_OUTPUT_TAIL_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return (
        f"[duet] output truncated to last {max_chars} chars\n"
        + text[-max_chars:]
    )


def _display_output(text: str) -> str:
    return text.rstrip() if text else "(empty)"


def _format_verify_log(turn_label: str, result: VerifyResult,
                       stdout: str, stderr: str) -> str:
    lines = [
        "# duet verify",
        f"turn: {turn_label}",
        f"command: {result.cmd}",
        f"cwd: {result.cwd}",
        f"exit_code: {result.exit_code if result.exit_code is not None else 'n/a'}",
        f"timed_out: {'yes' if result.timed_out else 'no'}",
    ]
    if result.error:
        lines.append(f"error: {result.error}")
    lines += [
        "",
        "## stdout",
        stdout if stdout else "(empty)\n",
        "",
        "## stderr",
        stderr if stderr else "(empty)\n",
    ]
    return "\n".join(lines)


def verify_result_state(result: VerifyResult) -> dict:
    data = {
        "ok": result.ok,
        "command": result.cmd,
        "cwd": str(result.cwd),
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "log_path": str(result.log_path),
        "stdout_tail": result.stdout_tail,
        "stderr_tail": result.stderr_tail,
    }
    if result.error:
        data["error"] = result.error
    return data


def format_verify_success_block(result: VerifyResult) -> str:
    return (
        "[duet verify passed]\n"
        f"command: {result.cmd}\n"
        f"cwd: {result.cwd}\n"
        "exit_code: 0\n"
        f"log: {result.log_path}\n"
        "[/duet verify passed]"
    )


def format_verify_failure_block(result: VerifyResult) -> str:
    exit_code = result.exit_code if result.exit_code is not None else "n/a"
    lines = [
        "[duet verify failed]",
        f"command: {result.cmd}",
        f"cwd: {result.cwd}",
        f"exit_code: {exit_code}",
    ]
    if result.timed_out:
        lines.append("timed_out: yes")
    if result.error:
        lines.append(f"error: {result.error}")
    lines += [
        f"log: {result.log_path}",
        "",
        "stdout tail:",
        _display_output(result.stdout_tail),
        "",
        "stderr tail:",
        _display_output(result.stderr_tail),
        "[/duet verify failed]",
    ]
    return "\n".join(lines)


def run_verify_command(cfg: DuetConfig, run_dir: pathlib.Path, turn_label: str,
                       worktree_path: Optional[pathlib.Path]) -> VerifyResult:
    """Run the configured verification command for a convergence proposal."""
    if not cfg.verify_cmd:
        raise ValueError("run_verify_command called without cfg.verify_cmd")
    cwd = effective_verify_cwd(cfg, worktree_path)
    log_path = run_dir / f"turn-{turn_label}-verify.log"
    pid_path = run_dir / f"turn-{turn_label}-verify.pid"
    started = dt.datetime.now().isoformat(timespec="seconds")
    print(f"[duet] verify turn {turn_label}: {cfg.verify_cmd} (cwd={cwd})")
    try:
        rc, stdout, stderr = _run(
            ["sh", "-c", cfg.verify_cmd],
            cwd=cwd,
            stdin="",
            timeout=cfg.per_turn_timeout,
            live_prefix=VERIFY_LIVE_PREFIX,
            mirror_stdout=True,
            pid_file_path=pid_path,
        )
        timed_out = rc == 124
        result = VerifyResult(
            ok=(rc == 0),
            cmd=cfg.verify_cmd,
            cwd=cwd,
            exit_code=rc,
            stdout_tail=_tail_text(stdout),
            stderr_tail=_tail_text(stderr),
            log_path=log_path,
            timed_out=timed_out,
        )
    except Exception as e:
        stdout = ""
        stderr = ""
        result = VerifyResult(
            ok=False,
            cmd=cfg.verify_cmd,
            cwd=cwd,
            exit_code=None,
            stdout_tail="",
            stderr_tail="",
            log_path=log_path,
            error=str(e),
        )
    finished = dt.datetime.now().isoformat(timespec="seconds")
    log_text = (
        f"started: {started}\n"
        f"finished: {finished}\n\n"
        + _format_verify_log(turn_label, result, stdout, stderr)
    )
    write_text_atomic(log_path, log_text)
    return result


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
        new_sid = agent.session_id or f"dry-claude-{agent.name}-{int(time.time())}"
        wt_note = f" wt={eff_cwd}" if agent.cwd_override else ""
        rn = f" reasoning={reasoning}" if reasoning else ""
        return (
            f"[dry-run claude/{agent.name}{wt_note}{rn}] received {len(message)} chars\n"
            "LGTM rationale: dry-run accepted the harness path and has no real "
            "agent output to review.\n"
            f"{DEFAULT_SENTINEL}"
        ), new_sid
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


# Codex's `codex exec` prints a line like `session id: 019e12ad-0b1b-7732-bd7b-6acbbd04ab46`
# to stderr near startup; modern builds also re-emit it on resume. We pin to that
# UUID for subsequent resumes so duet doesn't depend on `--last`'s cwd-keyed
# lookup. Anchored to "session id" to avoid false-positives on stray UUIDs in
# tracebacks or path strings; case-insensitive because the label has varied.
_CODEX_UUID_PATTERN = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_CODEX_SESSION_ID_RE = re.compile(
    r"session[ _-]?id\s*[:=]\s*(" + _CODEX_UUID_PATTERN + r")",
    re.IGNORECASE,
)
_CODEX_UUID_RE = re.compile(r"\A" + _CODEX_UUID_PATTERN + r"\Z", re.IGNORECASE)


def _parse_codex_session_id(stderr: str) -> Optional[str]:
    """Return the last `session id: <uuid>` UUID found in Codex's stderr.

    The last match wins so a resume that emits both the inherited id and a
    rotated id (if a future Codex build does that) ends up pinned to the
    rotated one. Returns the UUID lowercased; None if no match. We never parse
    stdout — Codex puts the assistant reply there and a UUID inside the reply
    must not be confused for the harness's session pin.
    """
    if not stderr:
        return None
    matches = _CODEX_SESSION_ID_RE.findall(stderr)
    return matches[-1].lower() if matches else None


def call_codex(agent: Agent, system_prompt: str, message: str,
               cwd: pathlib.Path, sandbox: str, timeout: int, dry: bool,
               first_turn: bool, reasoning: Optional[str] = None,
               fast: bool = False,
               stderr_log_path: Optional[pathlib.Path] = None,
               pid_file_path: Optional[pathlib.Path] = None) -> tuple[str, Optional[str]]:
    """Returns (assistant_text, new_session_id).

    Resume strategy: when stderr from a prior turn yielded a UUID we pin to
    that with `codex exec resume <uuid>`; otherwise we fall back to
    `codex exec resume --last`, which keys on cwd. `agent.session_id` carries
    either the parsed UUID, the sentinel ``"codex-current"`` (meaning "use
    --last"), or ``None`` (no prior turn for this agent).
    """
    eff_cwd = agent.cwd_override or cwd
    # Fast mode pins this Codex turn to low reasoning regardless of caller
    # intent. Codex minimal currently rejects the default tool set, while low
    # preserves tool compatibility and still trades depth for latency.
    effective = "low" if fast else reasoning
    if dry:
        new_sid = agent.session_id or f"dry-codex-{agent.name}-{int(time.time())}"
        wt_note = f" wt={eff_cwd}" if agent.cwd_override else ""
        rn = f" reasoning={effective}" if effective else ""
        fast_note = " fast" if fast else ""
        return (
            f"[dry-run codex/{agent.name}{fast_note}{wt_note}{rn}] received {len(message)} chars\n"
            "LGTM rationale: dry-run accepted the harness path and has no real "
            "agent output to review.\n"
            f"{DEFAULT_SENTINEL}"
        ), new_sid
    full_prompt = f"=== ROLE ===\n{system_prompt}\n\n=== MESSAGE FROM PARTNER ===\n{message}"
    reasoning_args: list[str] = []
    if effective:
        codex_value = CODEX_REASONING_MAP.get(effective, effective)
        # `medium` is Codex's default; only override when we actually want a
        # different effort level.
        if codex_value != "medium":
            reasoning_args = ["-c", f"model_reasoning_effort={codex_value}"]
    if fast:
        # Concise reasoning summaries cut output volume and time-to-first-token
        # on Codex turns. Pairs with low effort above; together they're the
        # "trade depth for latency while keeping tools available" knob.
        reasoning_args += ["-c", "model_reasoning_summary=concise"]
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
        # cwd is set via subprocess.Popen(cwd=…) so codex inherits the right
        # directory regardless of how we resume. `--sandbox` and `--cd` are
        # exec-only; sandbox carries over from the resumed session.
        options = [*shared_opts, *reasoning_args, *agent.extra_args]
        if _CODEX_UUID_RE.match(agent.session_id):
            # Pin to the UUID we parsed from a prior turn's stderr. This is
            # robust to parallel codex sessions sharing the cwd because
            # codex looks up the session by id, not by recency.
            cmd = ["codex", "exec", "resume", agent.session_id,
                   *options, full_prompt]
        else:
            # Sentinel value (typically "codex-current") meaning "we know a
            # prior turn happened but never captured a UUID." Fall back to
            # the most recent codex session in this cwd. Caveat: don't run
            # parallel codex sessions in the same cwd while a duet is alive.
            cmd = ["codex", "exec", "resume", "--last",
                   *options, full_prompt]
    # codex exec hangs on non-TTY stdin without explicit close (issue #20919)
    rc, out, err = _run(cmd, cwd=eff_cwd, stdin="", timeout=timeout,
                        stderr_log_path=stderr_log_path,
                        pid_file_path=pid_file_path)
    if rc != 0:
        raise RuntimeError(f"codex exited {rc}\nstderr:\n{err}\ncmd: {' '.join(cmd[:8])}…")
    # Prefer a freshly-parsed UUID from stderr; fall back to whatever id we
    # were already carrying; finally fall back to the "codex-current"
    # sentinel so the next turn at least knows a prior turn happened.
    parsed_sid = _parse_codex_session_id(err)
    return out.rstrip(), parsed_sid or agent.session_id or "codex-current"


def call_agent(agent: Agent, message: str, cfg: DuetConfig, first_turn_for_agent: bool,
               *, run_dir: Optional[pathlib.Path] = None,
               turn_label: Optional[str] = None) -> str:
    sys_prompt = agent.system_prompt(cfg.sentinel, recap=cfg.recap)
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
        # Fast mode is scoped to coder-role codex agents so it can't silently
        # downgrade a planner/reviewer when a user pairs `--reasoning max`
        # with `--codex-fast`. Config validation in main() warns when no
        # codex:coder agent exists at all.
        fast = cfg.codex_fast and agent.role == "coder"
        text, new_sid = call_codex(agent, sys_prompt, message, cfg.cwd,
                                   cfg.sandbox, cfg.per_turn_timeout, cfg.dry_run,
                                   first_turn=first_turn_for_agent,
                                   reasoning=reasoning,
                                   fast=fast,
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


def _convergence_markers(text: str, sentinel: str) -> tuple[bool, bool]:
    """Return (sentinel_seen, rationale_seen), ignoring fenced code blocks."""
    sentinel_re = re.compile(rf"^\s*{re.escape(sentinel)}\s*$")
    rationale_re = re.compile(
        r"^\s*(?:[-*]\s*)?(?:\*\*)?(?:LGTM\s+rationale|Rationale)"
        r"(?:\*\*)?\s*:\s*(.*)$",
        re.IGNORECASE,
    )
    in_fence = False
    fence_char = ""
    fence_len = 0
    sentinel_seen = False
    rationale_parts: list[str] = []
    collecting_rationale = False

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
        if in_fence:
            continue
        if sentinel_re.match(line):
            sentinel_seen = True
            collecting_rationale = False
            continue
        if sentinel_seen:
            continue
        rationale_match = rationale_re.match(line)
        if rationale_match:
            collecting_rationale = True
            rationale_parts.append(rationale_match.group(1).strip())
            continue
        if collecting_rationale:
            stripped = line.strip()
            if stripped:
                rationale_parts.append(stripped)

    rationale_text = " ".join(part for part in rationale_parts if part)
    rationale_text = re.sub(r"\s+", " ", rationale_text).strip()
    rationale_seen = len(rationale_text) >= CONVERGENCE_RATIONALE_MIN_CHARS
    return sentinel_seen, rationale_seen


def convergence_proposed(text: str, sentinel: str) -> bool:
    sentinel_seen, rationale_seen = _convergence_markers(text, sentinel)
    return sentinel_seen and rationale_seen


def converged(text: str, sentinel: str) -> bool:
    """Backward-compatible name for a single reply's convergence proposal."""
    return convergence_proposed(text, sentinel)


def parse_recap_headers(text: str) -> dict[str, Optional[str]]:
    """Parse agent-emitted recap headers from the top of a reply."""
    parsed: dict[str, Optional[str]] = {"recap": None, "files": None, "status": None}
    status_values = {
        "planning", "implementing", "reviewing", "requesting-changes",
        "ready-for-review", "converged",
    }
    for line in text.splitlines()[:10]:
        m = re.match(r"^(RECAP|FILES|STATUS):\s*(.*)$", line)
        if not m:
            continue
        key = m.group(1).lower()
        value = m.group(2).strip()
        if key == "status" and value not in status_values:
            value = ""
        parsed[key] = value or None
    return parsed


_FILE_PATH_RE = re.compile(
    r"\b[\w./-]+\.(?:py|md|sh|ts|tsx|js|jsx|json|yaml|yml|toml|html|css|rs|go|java|sql|txt)\b"
)


def extract_files_heuristic(text: str) -> list[str]:
    """Find plausible file paths in a reply, preserving first-seen order."""
    found: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        if path in seen or len(found) >= 8:
            return
        seen.add(path)
        found.append(path)

    for code in re.findall(r"`([^`\n]+)`", text):
        for m in _FILE_PATH_RE.finditer(code):
            add(m.group(0))
    for m in _FILE_PATH_RE.finditer(text):
        add(m.group(0))
    return found


def derive_status_heuristic(role: str, sentinel_hit: bool) -> str:
    if sentinel_hit:
        return "converged"
    if role == "planner":
        return "planning"
    if role == "coder":
        return "implementing"
    if role in {"reviewer", "triage-reviewer"}:
        return "reviewing"
    return "unknown"


def _derive_recap_heuristic(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if not s or re.match(r"^(RECAP|FILES|STATUS):", s):
            continue
        s = re.sub(r"^\s*[-*#>\d.)]+\s*", "", s).strip()
        if s:
            return textwrap.shorten(s, width=140, placeholder="...")
    return "No concise summary available."


def _format_byte_size(byte_size: int) -> str:
    if byte_size < 1024:
        return f"{byte_size}B"
    return f"{byte_size / 1024:.1f}KB"


def _recap_field(parsed: dict[str, Optional[str]],
                 fallbacks: dict[str, str], key: str) -> str:
    value = parsed.get(key)
    if value:
        return value
    return f"· {fallbacks.get(key, 'unknown')}"


def format_recap_block(turn_no: int, agent_name: str, role: str,
                       elapsed_s: float, byte_size: int, line_count: int,
                       parsed: dict[str, Optional[str]],
                       fallbacks: dict[str, str],
                       sentinel_hit: bool) -> str:
    if not sentinel_hit and parsed.get("status") == "converged":
        parsed = dict(parsed)
        parsed["status"] = None
    recap = _recap_field(parsed, fallbacks, "recap")
    files = _recap_field(parsed, fallbacks, "files")
    status = _recap_field(parsed, fallbacks, "status")
    convergence_label = "yes" if sentinel_hit else "no"
    return (
        f"## Turn {turn_no:02d} | {agent_name} ({role}) · "
        f"{int(round(elapsed_s))}s · {_format_byte_size(byte_size)} · "
        f"{line_count} lines\n\n"
        f"RECAP:  {recap}\n"
        f"FILES:  {files}\n"
        f"STATUS: {status} · convergence: {convergence_label}\n\n"
    )


def _format_live_recap_block(recap_block: str) -> str:
    lines = recap_block.strip("\n").splitlines()
    if lines and lines[0].startswith("## "):
        lines[0] = lines[0][3:]
    if len(lines) > 1 and lines[1] == "":
        del lines[1]
    return "\n".join(lines) + "\n"


def _start_recap_inflight(turn_no: int, agent_name: str, role: str,
                          started_at: float) -> tuple[threading.Event, threading.Thread]:
    stop_event = threading.Event()

    def redraw() -> None:
        while not stop_event.is_set():
            elapsed = int(time.time() - started_at)
            sys.stdout.write(
                f"\rTurn {turn_no:02d} | {agent_name} ({role}) · "
                f"running [{elapsed // 60:02d}:{elapsed % 60:02d}]\033[K"
            )
            sys.stdout.flush()
            stop_event.wait(1)

    t = threading.Thread(target=redraw, daemon=True)
    t.start()
    return stop_event, t


def _stop_recap_inflight(stop_event: threading.Event,
                         thread: threading.Thread) -> None:
    stop_event.set()
    thread.join(timeout=2)
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()


def derive_seed(cfg: DuetConfig, run_dir: Optional[pathlib.Path] = None) -> str:
    """Figure out the first message to send to the partner agent."""
    if cfg.kickoff:
        return cfg.kickoff
    # If agent[0] has a session_id, ask it to dump its latest plan/message.
    a0 = cfg.agents[0]
    if a0.session_id:
        print(f"[duet] extracting latest message from {a0.backend} session "
              f"{a0.session_id[:8]}…")
        if a0.backend == "claude" and run_dir is not None:
            print(f"[duet]   `claude -p` is silent on stderr during the API "
                  f"call; expect 30–120s.")
            print(f"[duet]   from another terminal: "
                  f"duet --status {run_dir.name}")
        return call_agent(a0, EXTRACT_LATEST_PROMPT, cfg,
                          first_turn_for_agent=False,
                          run_dir=run_dir, turn_label="00-extract")
    if cfg.task:
        return cfg.task
    raise SystemExit("nothing to start the conversation with — supply --task, "
                     "--kickoff, or --resume-claude <session_id>")


def run_duet(cfg: DuetConfig) -> dict:
    global RECAP_MODE
    RECAP_MODE = cfg.recap
    validate_config(cfg)

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
    # Register in the home index so `duet --list` / `duet --status <bare-id>`
    # discover this run from any cwd. Best-effort; never fails the run.
    _register_run_in_home_index(run_dir, cfg.cwd)
    transcript_path = run_dir / "transcript.md"
    recap_path = run_dir / "recap.md"
    state_path = run_dir / "state.json"

    if cfg.recap:
        append_text_atomic(
            recap_path,
            f"# duet recap — {run_dir}\n\n"
            f"run dir:    {run_dir}\n"
            f"mode:       recap (live)\n"
            f"transcript: {transcript_path}\n\n",
        )

    stop = StopFlag()
    _install_sigint(stop)

    # Tracks whether an agent has resume context or has actually been invoked.
    # A plain task/kickoff seed logged as agent[0] is not a CLI invocation.
    seen_first_turn = {a.name: bool(a.session_id) for a in cfg.agents}
    history: list[dict] = []
    transcript = ""

    def log(speaker: str, role: str, text: str, kind: str = "agent") -> None:
        nonlocal transcript
        head = f"\n## {speaker} ({role}) — {kind}\n\n"
        transcript += head + text + "\n"
        write_text_atomic(transcript_path, transcript)

    if cfg.recap:
        print(f"[duet] run: {run_dir}")
        print("[duet] mode: recap (live)")
        print(f"[duet] transcript: {transcript_path}")
        print(f"[duet] recap:      {recap_path}")
    else:
        print(f"[duet] run dir: {run_dir}")
    if cfg.verify_cmd:
        print(f"[duet] verify cmd: {cfg.verify_cmd}")
    if cfg.agents[0].session_id:
        print(f"[duet] {cfg.agents[0].name} resumes session {cfg.agents[0].session_id}")

    if cfg.dry_run and cfg.recap:
        if not (cfg.task or cfg.kickoff or cfg.agents[0].session_id):
            raise SystemExit("nothing to start the conversation with — supply --task, "
                             "--kickoff, or --resume-claude <session_id>")
        write_text_atomic(transcript_path, "")
        state = {
            "task": cfg.task,
            "cwd": str(cfg.cwd),
            "turns_used": 0,
            "agents": [agent_state(a) for a in cfg.agents],
            "history": history,
            "finished_reason": "dry_run",
            "transcript_path": str(transcript_path),
            "recap_path": str(recap_path),
            "verify_cmd": cfg.verify_cmd,
            "last_verify": None,
            "worktree": None,
            "worktree_branch": None,
            "worktree_for": cfg.worktree_for,
            "continue_from": cfg.continue_from,
            "duet_pid": os.getpid(),
        }
        write_text_atomic(state_path, json.dumps(state, indent=2))
        print("[duet] dry-run: agents not called; no recap turn blocks written.")
        print(f"[duet] done. reason=dry_run. transcript: {transcript_path}")
        print(f"[duet] recap: {recap_path}")
        return state

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
            "agents": [agent_state(a) for a in cfg.agents],
            "history": history,
            "finished_reason": "force_stop",
            "transcript_path": str(transcript_path),
            "verify_cmd": cfg.verify_cmd,
            "last_verify": None,
            "worktree": str(wt_path) if wt_path else None,
            "worktree_branch": wt_branch,
            "worktree_for": cfg.worktree_for,
            "continue_from": cfg.continue_from,
            "duet_pid": os.getpid(),
        }
        if cfg.recap:
            state["recap_path"] = str(recap_path)
        write_text_atomic(state_path, json.dumps(state, indent=2))
        return state

    if not cfg.kickoff and cfg.agents[0].session_id:
        guard_codex_shared_cwd_before_call(
            cfg, cfg.agents[0], first_turn_for_agent=False
        )
    seed = derive_seed(cfg, run_dir=run_dir)
    log(cfg.agents[0].name, cfg.agents[0].role, seed, kind="seed")
    last_msg = seed

    # Partner (agent[1]) normally speaks first in the loop, replying to the seed.
    # `--continue` may set this to the other agent so the next speaker matches
    # the previous run's last completed turn.
    speaker_idx = cfg.start_speaker_idx
    finished_reason = "max_turns"
    previous_convergence_proposal = False
    last_verify_state: Optional[dict] = None

    for turn in range(1, cfg.max_turns + 1):
        if stop.requested:
            finished_reason = "force_stop"
            break
        speaker = cfg.agents[speaker_idx]
        first_turn_for_agent = not seen_first_turn[speaker.name]
        guard_codex_shared_cwd_before_call(cfg, speaker, first_turn_for_agent)
        t0 = time.time()
        inflight: Optional[tuple[threading.Event, threading.Thread]] = None
        if cfg.recap:
            inflight = _start_recap_inflight(turn, speaker.name, speaker.role, t0)
        else:
            # Print BEFORE the subprocess starts so the terminal user sees
            # something happen instantly. claude -p emits nothing on stderr
            # during its API call; without this banner the user thinks duet hung.
            print(f"\n--- Turn {turn} :: {speaker.name} ({speaker.backend}/{speaker.role}) "
                  f"[started {dt.datetime.now().strftime('%H:%M:%S')}] ---")
            sys.stdout.flush()
        call_succeeded = False
        try:
            reply = call_agent(speaker, last_msg, cfg,
                               first_turn_for_agent=first_turn_for_agent,
                               run_dir=run_dir, turn_label=f"{turn:02d}")
            call_succeeded = True
        except Exception as e:
            if cfg.recap and inflight is not None:
                _stop_recap_inflight(*inflight)
                elapsed = time.time() - t0
                print(f"Turn {turn:02d} | {speaker.name} ({speaker.role}) · "
                      f"ERROR after {int(round(elapsed))}s — "
                      f"see turn-{turn:02d}-{speaker.name}.stderr.log")
                raise
            reply = f"[duet] AGENT ERROR: {e}"
            stop.request(f"agent_error: {e}")
        if cfg.recap and inflight is not None:
            _stop_recap_inflight(*inflight)
        if call_succeeded:
            guard_codex_shared_cwd_after_call(cfg, speaker, first_turn_for_agent)
        seen_first_turn[speaker.name] = True
        elapsed = time.time() - t0
        raw_reply = reply
        convergence_hit = convergence_proposed(raw_reply, cfg.sentinel)
        verify_state: Optional[dict] = None
        if convergence_hit and cfg.verify_cmd and not cfg.dry_run:
            verify_result = run_verify_command(
                cfg, run_dir, f"{turn:02d}", wt_path
            )
            verify_state = verify_result_state(verify_result)
            last_verify_state = verify_state
            if verify_result.ok:
                reply = raw_reply + "\n\n" + format_verify_success_block(verify_result)
            else:
                reply = raw_reply + "\n\n" + format_verify_failure_block(verify_result)
                convergence_hit = False

        if cfg.recap:
            parsed = parse_recap_headers(raw_reply)
            files = extract_files_heuristic(raw_reply)
            fallbacks = {
                "recap": _derive_recap_heuristic(raw_reply),
                "files": ", ".join(files) if files else "none",
                "status": derive_status_heuristic(speaker.role, convergence_hit),
            }
            recap_block = format_recap_block(
                turn, speaker.name, speaker.role, elapsed,
                len(raw_reply.encode("utf-8")),
                raw_reply.count("\n") + 1,
                parsed, fallbacks, convergence_hit,
            )
            append_text_atomic(recap_path, recap_block)

        # If this speaker is the worktree agent, capture the diff and append it to its reply.
        if wt_path is not None and speaker.cwd_override == wt_path:
            reply = append_worktree_diff(reply, wt_path, wt_branch)

        log(speaker.name, speaker.role, reply)
        history_entry = {"turn": turn, "agent": speaker.name, "elapsed_s": elapsed,
                         "len_chars": len(reply), "session_id": speaker.session_id}
        if verify_state is not None:
            history_entry["verify"] = verify_state
        history.append(history_entry)
        turn_state = {
            "task": cfg.task, "cwd": str(cfg.cwd), "turns_used": turn,
            "agents": [agent_state(a) for a in cfg.agents],
            "history": history, "finished_reason": None,
            "transcript_path": str(transcript_path),
            "verify_cmd": cfg.verify_cmd,
            "last_verify": last_verify_state,
            "worktree": str(wt_path) if wt_path else None,
            "worktree_branch": wt_branch,
            "worktree_for": cfg.worktree_for,
            "continue_from": cfg.continue_from,
            "duet_pid": os.getpid(),
        }
        if cfg.recap:
            turn_state["recap_path"] = str(recap_path)
        write_text_atomic(state_path, json.dumps(turn_state, indent=2))
        if cfg.recap:
            print(_format_live_recap_block(recap_block), end="")
        else:
            print(reply)

        if convergence_hit and previous_convergence_proposal:
            finished_reason = "converged"
            break
        if stop.requested:
            finished_reason = "force_stop"
            break

        last_msg = reply
        previous_convergence_proposal = convergence_hit
        speaker_idx = 1 - speaker_idx
    else:
        finished_reason = "max_turns"

    finished_reason, forced_verify_state = ask_force(
        cfg, history, transcript_path, state_path,
        last_msg, speaker_idx, seen_first_turn,
        finished_reason, wt_path, wt_branch
    )
    if forced_verify_state is not None:
        last_verify_state = forced_verify_state

    state = {
        "task": cfg.task,
        "cwd": str(cfg.cwd),
        "turns_used": len(history),
        "agents": [agent_state(a) for a in cfg.agents],
        "history": history,
        "finished_reason": finished_reason,
        "transcript_path": str(transcript_path),
        "verify_cmd": cfg.verify_cmd,
        "last_verify": last_verify_state,
        "continue_from": cfg.continue_from,
        "duet_pid": os.getpid(),
    }
    if cfg.recap:
        state["recap_path"] = str(recap_path)
    state["worktree"] = str(wt_path) if wt_path else None
    state["worktree_branch"] = wt_branch
    state["worktree_for"] = cfg.worktree_for
    write_text_atomic(state_path, json.dumps(state, indent=2))
    print(f"\n[duet] done. reason={finished_reason}. transcript: {transcript_path}")
    if cfg.recap:
        print(f"[duet] recap: {recap_path}")
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
              seen_first_turn: dict, reason: str,
              wt_path: Optional[pathlib.Path] = None,
              wt_branch: Optional[str] = None) -> tuple[str, Optional[dict]]:
    """Post-loop interactive prompt: human can push another turn or accept."""
    if not sys.stdin.isatty():
        return reason, None
    last_verify_state: Optional[dict] = None
    while True:
        print(f"\n[duet] loop ended (reason={reason}). "
              f"Press Enter to finish, or type feedback to force another turn "
              f"(your text is appended as a human-feedback message and sent "
              f"to the next agent):")
        try:
            line = input("force> ").strip()
        except EOFError:
            return reason, last_verify_state
        if not line:
            return reason, last_verify_state
        # Inject human feedback as the next "message" to the next-up speaker.
        next_speaker = cfg.agents[speaker_idx]
        first_turn_for_agent = not seen_first_turn[next_speaker.name]
        guard_codex_shared_cwd_before_call(cfg, next_speaker, first_turn_for_agent)
        # Append a human note to transcript
        head = f"\n## human — force-feedback (next: {next_speaker.name})\n\n"
        append_text_atomic(transcript_path, head + line + "\n")
        forced_msg = (
            f"{last_msg}\n\n---\n"
            "#### human force-feedback\n"
            f"{line}\n"
        )
        forced_turn = len(history) + 1
        t0 = time.time()
        inflight: Optional[tuple[threading.Event, threading.Thread]] = None
        if cfg.recap:
            inflight = _start_recap_inflight(forced_turn, next_speaker.name,
                                             next_speaker.role, t0)
        call_succeeded = False
        try:
            reply = call_agent(next_speaker, forced_msg, cfg,
                               first_turn_for_agent=first_turn_for_agent,
                               run_dir=transcript_path.parent,
                               turn_label=f"{forced_turn:02d}-forced")
            call_succeeded = True
        except Exception as e:
            if cfg.recap and inflight is not None:
                _stop_recap_inflight(*inflight)
                elapsed = time.time() - t0
                print(f"Turn {forced_turn:02d} | {next_speaker.name} "
                      f"({next_speaker.role}) · ERROR after "
                      f"{int(round(elapsed))}s — see "
                      f"turn-{forced_turn:02d}-forced-{next_speaker.name}.stderr.log")
                raise
            reply = f"[duet] AGENT ERROR: {e}"
        if cfg.recap and inflight is not None:
            _stop_recap_inflight(*inflight)
        if call_succeeded:
            guard_codex_shared_cwd_after_call(cfg, next_speaker, first_turn_for_agent)
        elapsed = time.time() - t0
        seen_first_turn[next_speaker.name] = True
        raw_reply = reply
        convergence_hit = convergence_proposed(reply, cfg.sentinel)
        verify_state: Optional[dict] = None
        if convergence_hit and cfg.verify_cmd and not cfg.dry_run:
            verify_result = run_verify_command(
                cfg, transcript_path.parent, f"{forced_turn:02d}-forced", wt_path
            )
            verify_state = verify_result_state(verify_result)
            last_verify_state = verify_state
            if verify_result.ok:
                reply = raw_reply + "\n\n" + format_verify_success_block(verify_result)
            else:
                reply = raw_reply + "\n\n" + format_verify_failure_block(verify_result)
                convergence_hit = False
        if wt_path is not None and next_speaker.cwd_override == wt_path:
            reply = append_worktree_diff(reply, wt_path, wt_branch)
        recap_block = ""
        if cfg.recap:
            parsed = parse_recap_headers(reply)
            files = extract_files_heuristic(reply)
            fallbacks = {
                "recap": _derive_recap_heuristic(reply),
                "files": ", ".join(files) if files else "none",
                "status": derive_status_heuristic(next_speaker.role, convergence_hit),
            }
            recap_block = format_recap_block(
                forced_turn, next_speaker.name, next_speaker.role, elapsed,
                len(reply.encode("utf-8")), reply.count("\n") + 1,
                parsed, fallbacks, convergence_hit,
            )
            append_text_atomic(transcript_path.parent / "recap.md", recap_block)
        append_text_atomic(
            transcript_path,
            f"\n## {next_speaker.name} ({next_speaker.role}) — forced\n\n{reply}\n",
        )
        history.append({"turn": len(history) + 1, "agent": next_speaker.name,
                        "forced": True, "len_chars": len(reply),
                        "session_id": next_speaker.session_id,
                        **({"verify": verify_state} if verify_state is not None else {})})
        if cfg.recap:
            print(_format_live_recap_block(recap_block), end="")
        else:
            print(reply)
        last_msg = reply
        speaker_idx = 1 - speaker_idx
        reason = "forced_continuation"
        if convergence_hit:
            return "converged_after_force", last_verify_state

# ---------- config / cli parsing ----------

def parse_partner(spec: str, default_role: str = "coder") -> Agent:
    """'codex:coder' -> Agent(backend=codex, role=coder)."""
    backend, _, role = spec.partition(":")
    if not backend:
        raise SystemExit(f"bad partner spec '{spec}', expected backend or backend:role")
    role = role or default_role
    return Agent(name=f"{backend}-{role}", backend=backend, role=role)


def normalize_verify_cmd(value, parser: argparse.ArgumentParser) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        parser.error("verify_cmd must be a string")
    cmd = value.strip()
    if not cmd:
        parser.error("verify_cmd must not be empty")
    return cmd


def _slot_name(backend: str, idx: int) -> str:
    slot = "lead" if idx == 0 else "partner"
    return f"{backend}-{slot}"


def _slot_agent(agent: Agent, idx: int, *, rename: bool) -> Agent:
    if not rename:
        return dataclasses.replace(agent)
    return dataclasses.replace(agent, name=_slot_name(agent.backend, idx))


def _default_slot_agent(backend: str, idx: int, *, rename: bool) -> Agent:
    role = "planner" if idx == 0 else "coder"
    name = _slot_name(backend, idx) if rename else f"{backend}-{role}"
    return Agent(name=name, backend=backend, role=role)


def _slot_default_role(idx: int) -> str:
    return "planner" if idx == 0 else "coder"


def _find_backend_idx(agents: list[Agent], backend: str,
                      preferred_idx: int) -> Optional[int]:
    if len(agents) > preferred_idx and agents[preferred_idx].backend == backend:
        return preferred_idx
    for i, agent in enumerate(agents):
        if agent.backend == backend:
            return i
    return None


def _force_resume_slot(
    agents: list[Agent],
    *,
    backend: str,
    slot_idx: int,
    session_id: str,
    rename_slots: bool,
) -> list[Agent]:
    """Move/create a resumed backend into its conventional slot.

    If the user already put the backend in that slot, preserve their role. If
    we have to move it from the other slot, reset moved agents to the slot
    default roles so `--resume-codex --lead codex:planner --partner
    claude:coder` becomes the useful `claude/planner + codex/coder` topology.
    """
    idx = _find_backend_idx(agents, backend, slot_idx)
    other_idx = 1 - slot_idx

    if idx is None:
        target = _default_slot_agent(backend, slot_idx, rename=rename_slots)
    else:
        moved = idx != slot_idx
        target = dataclasses.replace(
            agents[idx],
            role=(_slot_default_role(slot_idx) if moved else agents[idx].role),
        )
    target = dataclasses.replace(
        _slot_agent(target, slot_idx, rename=rename_slots),
        session_id=session_id,
    )

    if idx == other_idx:
        candidate = agents[slot_idx]
        moved_other = True
    else:
        candidate = agents[other_idx]
        moved_other = False

    other = dataclasses.replace(
        candidate,
        role=(
            _slot_default_role(other_idx)
            if moved_other else candidate.role
        ),
    )
    other = _slot_agent(other, other_idx, rename=rename_slots)

    out = [agents[0], agents[1]]
    out[slot_idx] = target
    out[other_idx] = other
    return out


def apply_resume_overrides(
    agents: list[Agent],
    *,
    resume_claude: Optional[str] = None,
    resume_codex: Optional[str] = None,
    rename_slots: bool = False,
) -> list[Agent]:
    """Attach CLI resume ids to the matching backend without silently dropping.

    Claude resume is the historical "lead supplies the seed" path, so a
    resumed Claude agent is normalized into the lead slot. Codex resume is the
    quick-start "Codex implements with its prior plan in context" path, so a
    resumed Codex agent is normalized into the partner slot. Existing roles are
    preserved only when the backend was already in its conventional slot.
    """
    normalized = [_slot_agent(a, i, rename=rename_slots)
                  for i, a in enumerate(agents)]
    if len(normalized) != 2:
        return normalized

    if resume_claude:
        normalized = _force_resume_slot(
            normalized,
            backend="claude",
            slot_idx=0,
            session_id=resume_claude,
            rename_slots=rename_slots,
        )

    if resume_codex:
        normalized = _force_resume_slot(
            normalized,
            backend="codex",
            slot_idx=1,
            session_id=resume_codex,
            rename_slots=rename_slots,
        )

    if rename_slots:
        normalized = [_slot_agent(a, i, rename=True)
                      for i, a in enumerate(normalized)]
    return normalized


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


def _cwd_slug(cwd_resolved: pathlib.Path) -> str:
    """Slugify a cwd into a `~/.duet/runs/` subdir name. Same scheme as the
    unwritable-cwd fallback inside `run_duet`, on purpose: a fallback dir
    and a registered symlink for the same cwd land under the same slug."""
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", str(cwd_resolved)).strip("-")[:80]


def _register_run_in_home_index(run_dir: pathlib.Path,
                                cwd_resolved: pathlib.Path) -> None:
    """Drop a symlink at `~/.duet/runs/<cwd-slug>/<run_id>` -> `run_dir`.

    `_default_list_paths()` already scans `~/.duet/runs/<slug>/<run_id>/`
    (originally for the unwritable-cwd fallback in `run_duet`). Mirroring
    every newly-created run dir into that tree gives `duet --list` and
    `duet --status <bare-id>` a single home-rooted index of every run
    started under this user, regardless of which project's
    `<cwd>/.duet/runs/` it actually lives in. Best-effort: failures
    (filesystem read-only, symlinks not supported, target slug dir
    occupied by something weird) emit a one-line stderr notice but never
    fail the run.
    """
    home_runs = (pathlib.Path.home() / ".duet" / "runs").resolve()
    try:
        run_resolved = run_dir.resolve()
    except OSError:
        return
    # Skip when run_dir already lives under ~/.duet/runs/<slug>/ (the
    # unwritable-cwd fallback already landed there) — registering would
    # be a circular self-reference.
    if home_runs in run_resolved.parents:
        return
    slug = _cwd_slug(cwd_resolved)
    if not slug:
        return  # paranoia: empty slug
    link = home_runs / slug / run_dir.name
    try:
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            try:
                target = pathlib.Path(os.readlink(link))
                if target.is_absolute() and target.resolve() == run_resolved:
                    return  # idempotent: already correct
            except OSError:
                pass
            return  # symlink points elsewhere; leave as-is
        if link.exists():
            return  # not a symlink; refuse to clobber
        link.symlink_to(run_resolved)
    except (OSError, NotImplementedError) as exc:
        print(f"[duet] note: home-index symlink failed "
              f"(~/.duet/runs/{slug}/{run_dir.name}): {exc}",
              file=sys.stderr)


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
    except (subprocess.TimeoutExpired, OSError):
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
    transcript_display = state.get("transcript_path", run_dir / "transcript.md")
    recap_display = state.get("recap_path")
    if recap_display is None and (run_dir / "recap.md").exists():
        recap_display = run_dir / "recap.md"
    print(f"[duet] {run_dir}")
    print(f"  turns_used:      {state.get('turns_used', '?')}")
    print(f"  finished_reason: {finished!r}")
    if recap_display is not None:
        print(f"  recap:           {recap_display}")

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
        print(f"  done. transcript: {transcript_display}")
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
        # Collect candidates and dedupe by resolved real path so a
        # home-index symlink and the cwd-relative real dir collapse into
        # one entry instead of triggering the "multiple roots" warning.
        seen: set[pathlib.Path] = set()
        unique: list[pathlib.Path] = []
        for root in _default_list_paths():
            cand = root / arg
            if not cand.is_dir():
                continue
            try:
                real = cand.resolve()
            except OSError:
                continue
            if real in seen:
                continue
            seen.add(real)
            unique.append(cand)
        if len(unique) == 1:
            return unique[0].resolve()
        if len(unique) > 1:
            # Same id under genuinely distinct dirs is rare (timestamps
            # are seconds-precise) but possible. Prefer most-recent and
            # warn so users notice ambiguity.
            unique.sort(key=lambda c: c.stat().st_mtime, reverse=True)
            print(f"[duet] note: run id {arg!r} found under multiple roots; "
                  f"using most recent: {unique[0]}",
                  file=sys.stderr)
            return unique[0].resolve()
    return None


def _load_run_state(run_dir: pathlib.Path,
                    parser: argparse.ArgumentParser,
                    option_name: str) -> dict:
    state_path = run_dir / "state.json"
    if not state_path.is_file():
        parser.error(f"{option_name}: missing state.json in {run_dir}")
    try:
        return json.loads(state_path.read_text())
    except json.JSONDecodeError as e:
        parser.error(f"{option_name}: state.json malformed: {e}")
    except OSError as e:
        parser.error(f"{option_name}: unable to read state.json: {e}")
    raise AssertionError("parser.error should have exited")


def _agents_from_state(state: dict,
                       parser: argparse.ArgumentParser,
                       option_name: str) -> list[Agent]:
    raw_agents = state.get("agents")
    if not isinstance(raw_agents, list) or len(raw_agents) != 2:
        parser.error(f"{option_name}: state.json must contain exactly two agents")
    agents: list[Agent] = []
    for i, raw in enumerate(raw_agents):
        if not isinstance(raw, dict):
            parser.error(f"{option_name}: agents[{i}] is not an object")
        name = raw.get("name")
        backend = raw.get("backend")
        if not name or not backend:
            parser.error(f"{option_name}: agents[{i}] missing name/backend")
        raw_extra_args = raw.get("extra_args") or []
        if not isinstance(raw_extra_args, list):
            parser.error(f"{option_name}: agents[{i}].extra_args is not a list")
        agents.append(Agent(
            name=str(name),
            backend=str(backend),
            role=str(raw.get("role") or "coder"),
            role_prompt=(str(raw["role_prompt"]) if raw.get("role_prompt") else None),
            model=(str(raw["model"]) if raw.get("model") else None),
            session_id=(str(raw["session_id"]) if raw.get("session_id") else None),
            extra_args=[str(x) for x in raw_extra_args],
            reasoning_effort=(str(raw["reasoning_effort"])
                              if raw.get("reasoning_effort") else None),
        ))
    return agents


def _next_speaker_idx_from_state(agents: list[Agent], state: dict) -> int:
    history = state.get("history") or []
    if isinstance(history, list):
        for item in reversed(history):
            if not isinstance(item, dict):
                continue
            last_agent = item.get("agent")
            for idx, agent in enumerate(agents):
                if agent.name == last_agent:
                    return 1 - idx
    try:
        turns_used = int(state.get("turns_used") or 0)
    except (TypeError, ValueError):
        turns_used = 0
    # Normal runs start with agent[1], so even turns mean agent[1] is next.
    return 1 if turns_used % 2 == 0 else 0


def _continue_note_from_args(args: argparse.Namespace,
                             cwd: pathlib.Path,
                             timeout: int,
                             parser: argparse.ArgumentParser,
                             stdin_cache: dict[str, str]) -> Optional[str]:
    sources = [
        args.task is not None,
        args.kickoff is not None,
        args.task_from_cmd is not None,
    ]
    if sum(1 for x in sources if x) > 1:
        parser.error("--continue accepts only one extra instruction via "
                     "--task, --kickoff, or --task-from-cmd")
    if args.task_from_cmd is not None:
        return resolve_task_from_cmd(args.task_from_cmd, cwd, timeout, parser)
    if args.kickoff is not None:
        return resolve_at_text(args.kickoff, "--kickoff", parser, stdin_cache)
    if args.task is not None:
        return resolve_at_text(args.task, "--task", parser, stdin_cache)
    return None


def _default_continue_kickoff(run_dir: pathlib.Path,
                              state: dict,
                              next_agent: Agent,
                              user_note: Optional[str],
                              worktree_path: Optional[pathlib.Path]) -> str:
    history = state.get("history") or []
    last = history[-1] if isinstance(history, list) and history else {}
    transcript = state.get("transcript_path") or str(run_dir / "transcript.md")
    recap = state.get("recap_path")
    finished = state.get("finished_reason")
    turns_display = state.get(
        "turns_used",
        len(history) if isinstance(history, list) else "?",
    )
    lines = [
        "Continue the previous duet run without restarting from scratch.",
        f"Previous run: {run_dir}",
        f"Previous finished_reason: {finished!r}",
        f"Previous turns_used: {turns_display}",
        f"Next speaker: {next_agent.name} ({next_agent.backend}/{next_agent.role})",
        f"Transcript: {transcript}",
    ]
    if recap:
        lines.append(f"Recap: {recap}")
    if worktree_path is not None:
        lines.append(f"Worktree: {worktree_path}")
    if isinstance(last, dict) and last:
        lines.append(
            f"Last completed turn: {last.get('turn')} by {last.get('agent')}"
        )
    if finished is None:
        lines.append(
            "The previous run appears interrupted or crashed. Inspect the "
            "transcript, stderr logs, and any worktree changes before editing; "
            "keep useful partial work."
        )
    else:
        lines.append(
            "Use the saved session context and artifacts above, then continue "
            "with the next concrete step."
        )
    if user_note:
        lines += ["", "Human continuation instruction:", user_note]
    return "\n".join(lines)


def build_continue_config(run_arg: str,
                          args: argparse.Namespace,
                          parser: argparse.ArgumentParser,
                          stdin_cache: dict[str, str]) -> DuetConfig:
    run_dir = _resolve_run_dir(run_arg)
    if run_dir is None:
        parser.error(f"--continue: no such run dir or id: {run_arg}")
    state = _load_run_state(run_dir, parser, "--continue")
    agents = _agents_from_state(state, parser, "--continue")
    # Older runs (or runs that crashed before the first state.json roll) may
    # have Codex agents that already spoke but have no saved session_id. Without
    # a marker, run_duet would treat the next turn as a fresh `codex exec` and
    # lose the prior session. Plant the legacy "codex-current" sentinel so
    # call_codex resumes via `--last` keyed on cwd.
    history = state.get("history") or []
    if isinstance(history, list):
        codex_speakers = {item.get("agent") for item in history
                          if isinstance(item, dict)}
        for agent in agents:
            if (agent.backend == "codex"
                    and not agent.session_id
                    and agent.name in codex_speakers):
                agent.session_id = "codex-current"
    cwd = pathlib.Path(state.get("cwd") or ".").expanduser().resolve()
    timeout = args.timeout
    user_note = _continue_note_from_args(args, cwd, timeout, parser, stdin_cache)
    next_idx = _next_speaker_idx_from_state(agents, state)

    raw_worktree = args.worktree_path or state.get("worktree")
    if not raw_worktree:
        legacy_wt = run_dir / "wt"
        if legacy_wt.is_dir():
            raw_worktree = str(legacy_wt)
    worktree_path = (pathlib.Path(raw_worktree).expanduser().resolve()
                     if raw_worktree else None)
    worktree_for = str(args.worktree_for or state.get("worktree_for") or "partner")
    kickoff = _default_continue_kickoff(
        run_dir, state, agents[next_idx], user_note, worktree_path
    )
    runs_dir = choose_runs_dir(args.runs_dir, cwd)
    return DuetConfig(
        cwd=cwd,
        agents=agents,
        task=state.get("task"),
        kickoff=kickoff,
        max_turns=args.turns,
        sentinel=args.sentinel,
        per_turn_timeout=timeout,
        runs_dir=runs_dir,
        sandbox=args.sandbox,
        permission_mode=args.permission_mode,
        dry_run=args.dry_run,
        recap=args.recap or bool(state.get("recap_path")),
        verify_cmd=normalize_verify_cmd(
            args.verify_cmd if args.verify_cmd is not None else state.get("verify_cmd"),
            parser,
        ),
        worktree=False,
        worktree_for=worktree_for,
        worktree_path=worktree_path,
        add_dirs=[pathlib.Path(d).expanduser().resolve() for d in args.add_dirs],
        reasoning=args.reasoning,
        codex_fast=bool(args.codex_fast),
        start_speaker_idx=next_idx,
        continue_from=str(run_dir),
    )


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
    # Dedupe by resolved real path so a run discovered via both a
    # cwd-relative root and a home-index symlink only shows once. Iter
    # order in `_default_list_paths()` puts cwd-relative roots first, so
    # the displayed `dir` column prefers the (usually more readable)
    # direct path over the symlink path.
    seen: set[pathlib.Path] = set()
    now = time.time()
    for root in roots:
        if not root.is_dir():
            print(f"[duet] {root}: not a directory", file=sys.stderr)
            continue
        for child in sorted(root.iterdir(), reverse=True):
            if not child.is_dir() or not _RUN_ID_RE.match(child.name):
                continue
            try:
                real = child.resolve()
            except OSError:
                continue
            if real in seen:
                continue
            seen.add(real)
            emoji, label, state = _classify_run(child)
            # Self-heal: backfill the home index for runs created before
            # `_register_run_in_home_index` shipped, or for runs whose
            # `--runs-dir` placed them outside the default tree. The
            # cwd is recorded in state.json (resolved-absolute by
            # main()), so we can compute the same slug used at creation
            # time. Idempotent; the helper swallows its own errors.
            state_cwd = state.get("cwd") if state else None
            if state_cwd:
                _register_run_in_home_index(child, pathlib.Path(state_cwd))
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
    print(f"\n  {len(rows)} run(s). Per-run health: duet --status <run-id>")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="duet — two CLI agents in conversation, with per-agent session memory.")
    ap.add_argument("--resume-claude", metavar="SESSION_ID",
                    help="resume an existing Claude session id; harness will pull "
                         "its latest message and feed it to the partner agent.")
    ap.add_argument("--resume-codex", metavar="SESSION_ID",
                    help="(advanced) seed codex with an existing session id.")
    ap.add_argument("--continue", metavar="RUN_DIR_OR_ID", dest="continue_run",
                    help="start a new run from an existing run's state.json: "
                         "restore agents/session ids, reuse its worktree when "
                         "available, and send the next agent a continuation kickoff. "
                         "--task/--kickoff/--task-from-cmd may add optional guidance.")
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
    ap.add_argument("--sentinel", default=DEFAULT_SENTINEL,
                    help="convergence sentinel; requires an LGTM rationale and "
                         "back-to-back proposals from both agents")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="per-turn timeout seconds")
    ap.add_argument("--verify-cmd", metavar="CMD", default=None,
                    help="shell command that must exit 0 before a convergence "
                         "proposal can count. Runs only for valid LGTM+rationale "
                         "proposals; YAML key: `verify_cmd:`.")
    ap.add_argument("--runs-dir", default=None, help="where to save transcripts")
    ap.add_argument("--sandbox", default="workspace-write",
                    help="codex --sandbox: read-only|workspace-write|danger-full-access")
    ap.add_argument("--permission-mode", default="acceptEdits",
                    help="claude --permission-mode: default|acceptEdits|plan|bypassPermissions")
    ap.add_argument("--config", help="optional YAML/JSON config (overrides flags except --resume-*)")
    ap.add_argument("--worktree", action="store_true",
                    help="run the partner agent in a throwaway git worktree on a fresh branch; "
                         "the worktree is left intact at the end so you can review/merge/drop it.")
    ap.add_argument("--worktree-for", choices=["partner", "lead"], default=None,
                    help="which agent runs in the worktree (default: partner)")
    ap.add_argument("--worktree-path", metavar="PATH", default=None,
                    help="reuse an EXISTING worktree (e.g. from a previous cancelled run). "
                         "Codex resumes via the saved session UUID (or `--last` for "
                         "older runs); cwd is preserved either way. Skips git "
                         "worktree creation. Mutually exclusive with --worktree.")
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
                         "(minimal → low) and adds high/xhigh/max prompt nudges.")
    ap.add_argument("--codex-fast", action="store_true", dest="codex_fast",
                    help="Codex-only fast mode: pin codex coder turns to "
                         "`model_reasoning_effort=low` and "
                         "`model_reasoning_summary=concise`, regardless of "
                         "--reasoning / per-agent reasoning_effort. Trades "
                         "depth for latency on codex coder turns; claude is "
                         "unaffected, so `--reasoning high --codex-fast` is "
                         "a real and useful combo. YAML key: `codex_fast: true`.")
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
                         "age, and dir. Pair with `--status <run-id>` to drill "
                         "into a specific run.")
    ap.add_argument("--quiet", action="store_true",
                    help="don't mirror subprocess stderr to your terminal in real-time. "
                         "By default, duet prints Codex's live progress as it works.")
    ap.add_argument("--recap", action="store_true",
                    help="compact per-turn debug view; suppresses live stderr mirror "
                         "and writes recap.md next to transcript.md")
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
    if args.continue_run and args.config:
        ap.error("--continue and --config are mutually exclusive")
    if args.continue_run and (args.resume_claude or args.resume_codex):
        ap.error("--continue restores session ids from state.json; do not also pass --resume-*")
    if args.continue_run and args.worktree:
        ap.error("--continue reuses the saved worktree; use --worktree-path to override it")

    # Live-stream subprocess stderr unless --quiet
    global LIVE_STREAM
    LIVE_STREAM = not args.quiet

    stdin_cache: dict[str, str] = {}
    if args.continue_run:
        cfg = build_continue_config(args.continue_run, args, ap, stdin_cache)
        print(f"[duet] continuing run {args.continue_run} "
              f"(next: {cfg.agents[cfg.start_speaker_idx].name})")
    elif args.config:
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
        verify_cmd = normalize_verify_cmd(
            args.verify_cmd if args.verify_cmd is not None else raw.get("verify_cmd"),
            ap,
        )
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
            recap=bool(raw.get("recap", False)) or args.recap,
            verify_cmd=verify_cmd,
            worktree=bool(raw.get("worktree", False)) or args.worktree,
            worktree_for=raw.get("worktree_for") or args.worktree_for or "partner",
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
            codex_fast=bool(args.codex_fast or raw.get("codex_fast", False)),
        )
        cfg.agents = apply_resume_overrides(
            cfg.agents,
            resume_claude=args.resume_claude,
            resume_codex=args.resume_codex,
        )
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
        # Build agents from --lead / --partner, then attach any resume ids to
        # the matching backend. This keeps explicit topologies working: if a
        # user puts resumed Codex in the lead slot, duet extracts that session's
        # latest message as the seed instead of silently dropping the UUID.
        lead = parse_partner(args.lead, default_role="planner")
        partner = parse_partner(args.partner, default_role="coder")
        agents = apply_resume_overrides(
            [lead, partner],
            resume_claude=args.resume_claude,
            resume_codex=args.resume_codex,
            rename_slots=True,
        )

        cfg = DuetConfig(
            cwd=cfg_cwd,
            agents=agents,
            task=task,
            kickoff=kickoff,
            max_turns=args.turns,
            sentinel=args.sentinel,
            per_turn_timeout=args.timeout,
            runs_dir=runs_dir,
            sandbox=args.sandbox,
            permission_mode=args.permission_mode,
            dry_run=args.dry_run,
            recap=args.recap,
            verify_cmd=normalize_verify_cmd(args.verify_cmd, ap),
            worktree=args.worktree,
            worktree_for=args.worktree_for or "partner",
            worktree_path=(pathlib.Path(args.worktree_path).expanduser().resolve()
                           if args.worktree_path else None),
            worktree_root=(pathlib.Path(args.worktree_root).expanduser().resolve()
                           if args.worktree_root else None),
            add_dirs=[pathlib.Path(d).expanduser().resolve() for d in args.add_dirs],
            reasoning=args.reasoning,
            codex_fast=bool(args.codex_fast),
        )

    validate_config(cfg, ap)
    validate_reasoning(cfg.reasoning, "config reasoning")
    for agent in cfg.agents:
        validate_reasoning(agent.reasoning_effort, f"agent {agent.name} reasoning_effort")
    if cfg.worktree and cfg.worktree_path:
        raise SystemExit("--worktree and --worktree-path/worktree_path are mutually exclusive")

    # Codex fast mode is scoped to coder-role codex agents (see call_agent).
    # Surface that scoping at config time so a user who pairs `--reasoning max`
    # with `--codex-fast --lead codex:planner` gets a loud signal rather than
    # silently running the planner at low effort.
    if cfg.codex_fast:
        codex_agents = [a for a in cfg.agents if a.backend == "codex"]
        codex_coders = [a for a in codex_agents if a.role == "coder"]
        codex_non_coders = [a for a in codex_agents if a.role != "coder"]
        if not codex_coders:
            print(
                "[duet] WARNING: --codex-fast had no effect — "
                "no codex agent has role=coder in this duet. "
                "Fast mode applies only to codex:coder; set per-agent "
                "`reasoning_effort: low` if you really want fast on a "
                "non-coder role.",
                file=sys.stderr,
            )
            cfg.codex_fast = False
        elif codex_non_coders:
            roles = ", ".join(f"{a.name}({a.role})" for a in codex_non_coders)
            print(
                f"[duet] note: --codex-fast applies only to codex:coder; "
                f"non-coder codex agents [{roles}] keep their normal "
                f"reasoning effort.",
                file=sys.stderr,
            )

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
