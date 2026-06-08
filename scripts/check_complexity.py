#!/usr/bin/env python3
"""Cyclomatic-complexity / function-length gate for duet's single-file harness.

duet is a hard single-file project, so "split into smaller modules" is never an
option — the only lever against sprawl is keeping individual *functions* small
and shallow. This script is duet's equivalent of Go's `gocyclo`: it walks the
AST of the target files, computes McCabe cyclomatic complexity and physical
length for every function/method, and fails (exit 1) if any function exceeds the
budget. Stdlib-only, so it runs in CI with no install step.

Complexity counts one decision point per branch — if/elif, for, while, except,
with, assert, each boolean and/or operand, a ternary, and each for/if clause in
a comprehension — plus a base of 1 for the function's entry path. Nested
functions are measured on their own and excluded from their parent's score, so
an outer function isn't penalized for the bodies of closures it defines. This
mirrors gocyclo closely enough to be a stable budget.

Usage:
  scripts/check_complexity.py [files...] [--max-complexity N] [--max-length N]
                              [--top N] [--quiet]

Defaults to duet.py when no files are given. Exit 0 if every function is within
budget, 1 otherwise (offenders printed first), 2 on a missing file. `--top N`
prints the N worst functions regardless of pass/fail for at-a-glance triage.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import sys
from typing import List

DEFAULT_MAX_COMPLEXITY = 25
DEFAULT_MAX_LENGTH = 160
DEFAULT_TARGETS = ["duet.py"]

_DECISION_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
                   ast.With, ast.AsyncWith, ast.Assert)


class FunctionStat:
    """One function's measured complexity and length."""

    def __init__(self, file: str, name: str, lineno: int,
                 complexity: int, length: int) -> None:
        self.file = file
        self.name = name
        self.lineno = lineno
        self.complexity = complexity
        self.length = length

    def over_budget(self, max_cc: int, max_len: int) -> bool:
        return self.complexity > max_cc or self.length > max_len

    def reasons(self, max_cc: int, max_len: int) -> str:
        parts = []
        if self.complexity > max_cc:
            parts.append(f"complexity {self.complexity} > {max_cc}")
        if self.length > max_len:
            parts.append(f"length {self.length} > {max_len}")
        return ", ".join(parts)


class _ComplexityVisitor(ast.NodeVisitor):
    """McCabe complexity of one function body, not recursing into nested defs."""

    def __init__(self) -> None:
        self.score = 1

    def _skip_nested(self, node: ast.AST) -> None:
        # A nested function is its own measured unit; don't count its body here.
        pass

    visit_FunctionDef = _skip_nested
    visit_AsyncFunctionDef = _skip_nested

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, _DECISION_NODES):
            self.score += 1
        elif isinstance(node, ast.BoolOp):
            self.score += len(node.values) - 1
        elif isinstance(node, ast.IfExp):
            self.score += 1
        elif isinstance(node, ast.comprehension):
            self.score += 1 + len(node.ifs)
        super().generic_visit(node)


def _complexity(fn: ast.AST) -> int:
    visitor = _ComplexityVisitor()
    for stmt in fn.body:
        visitor.visit(stmt)
    return visitor.score


def analyze(path: pathlib.Path) -> List[FunctionStat]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    stats: List[FunctionStat] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            stats.append(FunctionStat(str(path), node.name, node.lineno,
                                      _complexity(node), length))
    return stats


def _format_row(s: FunctionStat) -> str:
    return f"  CC={s.complexity:3d}  len={s.length:4d}  {s.file}:{s.lineno} {s.name}"


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="cyclomatic-complexity gate for duet")
    ap.add_argument("files", nargs="*",
                    help=f"python files to scan (default: {', '.join(DEFAULT_TARGETS)})")
    ap.add_argument("--max-complexity", type=int, default=DEFAULT_MAX_COMPLEXITY)
    ap.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH)
    ap.add_argument("--top", type=int, default=0,
                    help="also print the N worst functions by complexity")
    ap.add_argument("--quiet", action="store_true",
                    help="print only failures (suppress the all-clear summary)")
    args = ap.parse_args(argv)

    root = pathlib.Path(__file__).resolve().parent.parent
    targets = args.files or [str(root / t) for t in DEFAULT_TARGETS]

    all_stats: List[FunctionStat] = []
    for t in targets:
        p = pathlib.Path(t)
        if not p.is_absolute():
            p = root / p
        if not p.exists():
            print(f"[check_complexity] no such file: {p}", file=sys.stderr)
            return 2
        all_stats.extend(analyze(p))

    offenders = [s for s in all_stats
                 if s.over_budget(args.max_complexity, args.max_length)]
    offenders.sort(key=lambda s: (-s.complexity, -s.length))

    if args.top:
        worst = sorted(all_stats, key=lambda s: (-s.complexity, -s.length))[:args.top]
        print(f"top {args.top} functions by complexity:")
        for s in worst:
            print(_format_row(s))
        print()

    if offenders:
        print(f"[check_complexity] {len(offenders)} function(s) over budget "
              f"(max CC={args.max_complexity}, max length={args.max_length}):",
              file=sys.stderr)
        for s in offenders:
            print(_format_row(s) +
                  f"   <- {s.reasons(args.max_complexity, args.max_length)}",
                  file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"[check_complexity] ok: {len(all_stats)} functions, "
              f"all within budget (max CC={args.max_complexity}, "
              f"max length={args.max_length}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
