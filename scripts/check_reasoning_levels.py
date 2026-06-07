#!/usr/bin/env python3
"""Verify duet's reasoning-effort translation layer end-to-end.

Six duet-abstraction levels (`minimal`, `low`, `medium`, `high`, `xhigh`,
`max`) map to backend-specific cmd-line flags / values via four constants in
`duet.py`:
`REASONING_LEVELS`, `CLAUDE_REASONING_MAP`, `CODEX_REASONING_MAP`,
`CLAUDE_REASONING_PROMPT_PREFIX`. Drift between any of them is a P0 because
the user-facing flag values silently lie about which effort is in use.

This script monkey-patches `_run` so no agent is actually invoked, walks every
level, and asserts:
  - claude is invoked with `--effort <mapped>` for that level
  - codex is invoked with `-c model_reasoning_effort=<mapped>` (or no `-c`
    flag at all for `medium`, which is Codex's default)
  - the system-prompt prefix contains the right reasoning-nudge marker for
    `high` (`think hard`), `xhigh` (`think very hard`), and `max`
    (`ultrathink`); minimal/low/medium have no nudge

Exits 0 on full match, 1 on any row mismatch (with the offending rows
flagged in the printed table). CLAUDE.md's reasoning-translation invariant
points here as the canonical regression check; rerun after touching any of
the four constants.

Was previously an inline Python one-liner inside `examples/self-review.yaml`;
promoted to a standalone script when that example was deleted.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys


# Per-level expectations. `prefix_marker` is a substring that MUST appear
# inside the first 30 chars of the claude system-prompt prefix; for levels
# without a reasoning nudge it's the literal sentinel "sys" we pass in below.
EXPECTED = {
    "minimal": {"prefix_marker": "sys",        "effort": "low",    "codex_arg": "model_reasoning_effort=minimal"},
    "low":     {"prefix_marker": "sys",        "effort": "low",    "codex_arg": "model_reasoning_effort=low"},
    "medium":  {"prefix_marker": "sys",        "effort": "medium", "codex_arg": "(none)"},
    "high":    {"prefix_marker": "think hard", "effort": "high",   "codex_arg": "model_reasoning_effort=high"},
    "xhigh":   {"prefix_marker": "think very", "effort": "xhigh",  "codex_arg": "model_reasoning_effort=xhigh"},
    "max":     {"prefix_marker": "ultrathink", "effort": "max",    "codex_arg": "model_reasoning_effort=xhigh"},
}


def _load_duet() -> object:
    duet_path = pathlib.Path(__file__).resolve().parent.parent / "duet.py"
    if not duet_path.is_file():
        sys.exit(f"could not find duet.py next to scripts/ (looked at {duet_path})")
    spec = importlib.util.spec_from_file_location("duet_under_test", duet_path)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def main() -> int:
    m = _load_duet()

    captured: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        captured.append(list(cmd))
        if cmd[0] == "claude":
            return (0, json.dumps({"result": "ok", "session_id": "s"}), "")
        return (0, "ok", "")

    m._run = fake_run  # type: ignore[attr-defined]

    a_cl = m.Agent(name="c", backend="claude", role="planner")
    a_cx = m.Agent(name="x", backend="codex", role="coder")

    rows: list[tuple[str, str, str, str, bool, list[str]]] = []
    bad = 0

    for lvl in m.REASONING_LEVELS:
        captured.clear()
        m.call_claude(
            a_cl, "sys", "msg", pathlib.Path("."),
            "acceptEdits", 60, dry=False, reasoning=lvl,
        )
        claude_cmd = captured[-1]
        prefix = claude_cmd[claude_cmd.index("--append-system-prompt") + 1]
        effort = (
            claude_cmd[claude_cmd.index("--effort") + 1]
            if "--effort" in claude_cmd else "(none)"
        )

        captured.clear()
        m.call_codex(
            a_cx, "sys", "msg", pathlib.Path("."),
            "workspace-write", 60, dry=False, first_turn=True, reasoning=lvl,
        )
        codex_cmd = captured[-1]
        codex_arg = (
            codex_cmd[codex_cmd.index("-c") + 1]
            if "-c" in codex_cmd else "(none)"
        )

        want = EXPECTED[lvl]
        problems: list[str] = []
        if want["prefix_marker"] not in prefix[:30]:
            problems.append(f"claude prefix missing {want['prefix_marker']!r}")
        if effort != want["effort"]:
            problems.append(f"claude --effort {effort} (want {want['effort']})")
        if codex_arg != want["codex_arg"]:
            problems.append(f"codex -c {codex_arg} (want {want['codex_arg']})")

        rows.append((lvl, repr(prefix[:30]), effort, codex_arg, not problems, problems))
        if problems:
            bad += 1

    # Pretty-print the table even on success — same shape as the original
    # one-liner so the smoke check is easy to eyeball.
    print(f"{'level':>8}  {'claude_prefix':35}  {'effort':6}  codex_arg")
    print(f"{'-' * 8:>8}  {'-' * 35}  {'-' * 6}  {'-' * 30}")
    for lvl, prefix_repr, effort, codex_arg, ok, _ in rows:
        mark = "" if ok else "  ← mismatch"
        print(f"{lvl:>8}  {prefix_repr:35}  {effort:6}  {codex_arg}{mark}")

    if bad:
        print(f"\nFAIL: {bad} row(s) mismatch.", file=sys.stderr)
        for lvl, _, _, _, ok, problems in rows:
            if not ok:
                print(f"  {lvl}: {'; '.join(problems)}", file=sys.stderr)
        return 1
    print(f"\nOK: all {len(m.REASONING_LEVELS)} reasoning levels map to the expected backend args.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
