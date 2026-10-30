#!/usr/bin/env python3
"""Record a native ChatGPT Duet loop. Never invoke agents, models, or networks."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


KIND = "duet.chatgpt.run"
ROLES = ("reviewer", "coder")
STOP_REASONS = ("user_stop", "capability_unavailable", "verification_failed")


def proposes_agreement(text):
    """Require a final top-level approval stanza, outside quoted examples."""
    lines = text.rstrip().splitlines()
    if not lines or lines[-1].strip() != "<<<LGTM>>>":
        return False
    fence = ""
    rationale = []
    collecting = False
    previous_blank = True
    quoted_paragraph = False
    for index, line in enumerate(lines):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            value = marker.group(1)
            if not fence:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = ""
            previous_blank = False
            collecting = False
            rationale = []
            continue
        if fence:
            continue
        if not line.strip():
            previous_blank = True
            quoted_paragraph = False
            collecting = False
            rationale = []
            continue
        # CommonMark permits unprefixed lazy continuations of a blockquote.
        if line.lstrip().startswith(">"):
            quoted_paragraph = True
        if quoted_paragraph or re.match(r"^(?: {4}| {0,3}\t)", line):
            previous_blank = False
            collecting = False
            rationale = []
            continue
        if line.strip() == "<<<LGTM>>>":
            if index != len(lines) - 1:
                collecting = False
                rationale = []
                previous_blank = False
                continue
            normalized = re.sub(r"\s+", " ", " ".join(rationale)).strip()
            return collecting and len(normalized) >= 20
        match = re.match(
            r"^\s*(?:[-*]\s*)?(?:\*\*)?(?:LGTM\s+rationale|Rationale)"
            r"(?:\*\*)?\s*:\s*(.*)$", line, re.IGNORECASE,
        )
        if match and previous_blank:
            collecting = True
            rationale = [match.group(1).strip()]
        elif collecting and line.strip():
            rationale.append(line.strip())
        previous_blank = False
    return False


def fingerprint(paths):
    digest = hashlib.sha256()
    names = sorted(set(str(Path(path)) for path in paths))
    for name in names:
        path = Path(name)
        if path.is_symlink() or not path.is_file():
            raise ValueError("fingerprint inputs must be regular, non-symlink files")
        content = path.read_bytes()
        label = name.encode("utf-8")
        for value in (label, content):
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
    return digest.hexdigest()


def write_atomic(path, text):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def save(run_dir, state):
    write_atomic(run_dir / "state.json", json.dumps(state, indent=2) + "\n")


def load(run_dir):
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or state.get("kind") != KIND
            or state.get("schema_version") != 1):
        raise ValueError("not a native ChatGPT Duet state document")
    if (type(state.get("turn_limit")) is not int or state["turn_limit"] < 2
            or not isinstance(state.get("history"), list)
            or not isinstance(state.get("peers"), dict)
            or set(state["peers"]) != set(ROLES)):
        raise ValueError("invalid native ChatGPT Duet state")
    return state


@contextmanager
def mutation(run_dir):
    # A stale lock needs inspection; never steal another coordinator's lock.
    lock = run_dir / ".control.lock"
    descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.close(descriptor)
        yield load(run_dir)
    finally:
        lock.unlink()


def status(state):
    finished = state["finished_reason"]
    count = len(state["history"])
    return {
        "schema_version": 1, "kind": "duet.chatgpt.status",
        "completed_turns": count, "turn_limit": state["turn_limit"],
        "next_turn": None if finished else count + 1,
        "next_role": None if finished else ROLES[count % 2],
        "peers_bound": {role: bool(state["peers"][role]) for role in ROLES},
        "finished_reason": finished,
    }


def initialize(args):
    task = args.task_file.read_text(encoding="utf-8")
    if not task.strip() or args.turns < 2:
        raise ValueError("a nonempty task and at least two turns are required")
    args.run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    state = {
        "schema_version": 1, "kind": KIND, "task": task,
        "turn_limit": args.turns, "peers": dict.fromkeys(ROLES),
        "history": [], "finished_reason": None,
    }
    save(args.run_dir, state)
    return state


def bind(state, args):
    if state["finished_reason"] or state["history"]:
        raise ValueError("cannot bind peers after turns have started or the run has ended")
    if not args.agent_id.strip():
        raise ValueError("native peer handle must be nonempty")
    if state["peers"][args.role] == args.agent_id:
        return
    if state["peers"][args.role] is not None:
        raise ValueError("cannot replace an existing native peer handle")
    if args.agent_id in state["peers"].values():
        raise ValueError("reviewer and coder must have distinct native handles")
    state["peers"][args.role] = args.agent_id


def record(state, args):
    if state["finished_reason"]:
        raise ValueError("run is terminal; start a new run for an authorized continuation")
    expected = status(state)
    if args.turn != expected["next_turn"] or args.role != expected["next_role"]:
        raise ValueError("stale turn number or wrong speaker")
    if not all(state["peers"].values()) or state["peers"][args.role] != args.agent_id:
        raise ValueError("turn must come from the bound native peer; bind both roles first")
    if not re.fullmatch(r"[a-f0-9]{64}", args.revision):
        raise ValueError("revision must be the SHA-256 fingerprint of the inspected inputs")
    reply = args.reply_file.read_text(encoding="utf-8")
    if not reply.strip():
        raise ValueError("a complete reply or failure diagnostic is required")
    approval = args.outcome == "success" and proposes_agreement(reply)
    previous = state["history"][-1] if state["history"] else None
    entry = {
        "turn": args.turn, "role": args.role, "agent_id": args.agent_id,
        "outcome": args.outcome, "revision": args.revision,
        "reply": reply, "approval_proposed": approval,
    }
    state["history"].append(entry)
    if args.outcome != "success":
        state["finished_reason"] = "timeout" if args.outcome == "timeout" else "peer_error"
    elif (approval and previous and previous["approval_proposed"]
          and previous["revision"] == args.revision):
        state["finished_reason"] = "converged"
    elif args.turn >= state["turn_limit"]:
        state["finished_reason"] = "max_turns"


def transcript(state):
    lines = ["# Duet for ChatGPT", "", "Stop reason: " + str(state["finished_reason"]), ""]
    for entry in state["history"]:
        reply = entry["reply"]
        # Contain peer output as data even when a reply contains Markdown fences.
        longest = max((len(x) for x in re.findall(r"`+", reply)), default=0)
        fence = "`" * max(3, longest + 1)
        lines.extend([
            "## Turn {}: {} ({})".format(entry["turn"], entry["role"], entry["outcome"]),
            "", "Revision: " + entry["revision"], "", fence + "text", reply, fence, "",
        ])
    return "\n".join(lines)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("init", "bind", "record", "status", "stop", "transcript"):
        command = commands.add_parser(name)
        command.add_argument("--run-dir", type=Path, required=True)
        if name == "init":
            command.add_argument("--task-file", type=Path, required=True)
            command.add_argument("--turns", type=int, default=6)
        if name in ("bind", "record"):
            command.add_argument("--role", choices=ROLES, required=True)
            command.add_argument("--agent-id", required=True)
        if name == "record":
            command.add_argument("--turn", type=int, required=True)
            command.add_argument("--reply-file", type=Path, required=True)
            command.add_argument("--revision", required=True)
            command.add_argument("--outcome", choices=("success", "error", "timeout"), required=True)
        if name == "stop":
            command.add_argument("--reason", choices=STOP_REASONS, required=True)
    hashing = commands.add_parser("fingerprint")
    hashing.add_argument("files", nargs="+", type=Path)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "fingerprint":
            print(fingerprint(args.files))
            return 0
        if args.command == "init":
            state = initialize(args)
        elif args.command in ("bind", "record", "stop"):
            with mutation(args.run_dir) as state:
                if args.command == "bind":
                    bind(state, args)
                elif args.command == "record":
                    record(state, args)
                elif (not state["finished_reason"]
                      or (args.reason == "verification_failed"
                          and state["finished_reason"] == "converged")):
                    state["finished_reason"] = args.reason
                save(args.run_dir, state)
        else:
            state = load(args.run_dir)
        if args.command == "transcript":
            print(transcript(state))
        else:
            print(json.dumps(status(state)))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("[duet-chatgpt] " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
