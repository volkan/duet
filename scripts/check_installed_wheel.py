#!/usr/bin/env python3
"""Install the current wheel into a temp venv and verify CLI control schemas."""
from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUN_INFO_KEYS = {
    "schema_version", "kind", "duet_version", "run_id", "run_dir",
    "state_path", "pid",
}
STATUS_KEYS = {
    "schema_version", "kind", "duet_version", "run_id", "run_dir",
    "health", "phase", "exit_code", "turns_used", "finished_reason",
    "per_turn_timeout", "active_turn", "last_completed_turn", "last_timeout",
    "tool_denials", "artifacts", "error",
}
ARTIFACT_KEYS = {"state", "transcript", "recap", "worktree"}


def _source_version() -> str:
    tree = ast.parse((ROOT / "duet.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__version__"
                   for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    raise RuntimeError("duet.py has no literal __version__")


def _check_environment(home: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(key, None)
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        APPDATA=str(home / "appdata"),
        LOCALAPPDATA=str(home / "local-appdata"),
        XDG_CACHE_HOME=str(home / ".cache"),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_DATA_HOME=str(home / ".local" / "share"),
        XDG_STATE_HOME=str(home / ".local" / "state"),
        PIP_CONFIG_FILE=os.devnull,
    )
    return env


def _run(
    command: list[str], cwd: pathlib.Path, env: dict[str, str],
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def check(dist_dir: pathlib.Path) -> None:
    version = _source_version()
    wheel = dist_dir.resolve() / f"duet_cli-{version}-py3-none-any.whl"
    if not wheel.is_file():
        raise RuntimeError(f"missing wheel: {wheel}")
    with tempfile.TemporaryDirectory(prefix="duet-wheel-check-") as raw:
        root = pathlib.Path(raw)
        home = root / "home"
        home.mkdir()
        env = _check_environment(home)
        env_dir = root / "venv"
        _run([sys.executable, "-m", "venv", str(env_dir)], root, env)
        bin_dir = env_dir / ("Scripts" if sys.platform == "win32" else "bin")
        python = bin_dir / ("python.exe" if sys.platform == "win32" else "python")
        duet = bin_dir / ("duet.exe" if sys.platform == "win32" else "duet")
        _run([
            str(python), "-m", "pip", "--isolated", "install", "--no-index", "--no-deps",
            str(wheel),
        ], root, env)

        version_result = _run([str(duet), "--version"], root, env)
        if version_result.stdout.strip() != f"duet {version}":
            raise RuntimeError(f"unexpected --version output: {version_result.stdout!r}")

        target_cwd = root / "target-project"
        target_cwd.mkdir()
        info_path = root / "run.json"
        _run([
            str(duet), "--dry-run", "--recap", "--task", "installed wheel smoke",
            "--cwd", str(target_cwd), "--runs-dir", "relative-runs",
            "--run-info-file", str(info_path),
        ], root, env)
        launch = json.loads(info_path.read_text(encoding="utf-8"))
        state = json.loads(
            pathlib.Path(launch["state_path"]).read_text(encoding="utf-8")
        )
        claude_models = [
            agent.get("model")
            for agent in state.get("agents", [])
            if agent.get("backend") == "claude"
        ]
        if not claude_models or any(model != "sonnet" for model in claude_models):
            raise RuntimeError(f"installed Claude model default mismatch: {state}")
        if not isinstance(state.get("duet_process_start"), str):
            raise RuntimeError(f"installed supervisor identity missing: {state}")
        status_result = _run([
            str(duet), "--status", launch["run_dir"], "--json",
        ], root, env)
        status = json.loads(status_result.stdout)
        if set(launch) != RUN_INFO_KEYS:
            raise RuntimeError(f"bad installed run-info keys: {sorted(launch)}")
        if (
            launch.get("schema_version") != 1
            or launch.get("kind") != "duet.run"
            or launch.get("duet_version") != version
        ):
            raise RuntimeError(f"bad installed run-info schema: {launch}")
        if not all(pathlib.Path(launch[key]).is_absolute()
                   for key in ("run_dir", "state_path")):
            raise RuntimeError(f"installed run-info paths are not absolute: {launch}")
        if set(status) != STATUS_KEYS:
            raise RuntimeError(f"bad installed status keys: {sorted(status)}")
        if (
            status.get("schema_version") != 1
            or status.get("kind") != "duet.status"
            or status.get("duet_version") != version
        ):
            raise RuntimeError(f"bad installed status schema: {status}")
        if status.get("run_id") != launch.get("run_id"):
            raise RuntimeError(f"installed status run mismatch: {status}")
        if status.get("run_dir") != launch.get("run_dir"):
            raise RuntimeError(f"installed status path mismatch: {status}")
        if status.get("health") != "terminal" or status.get("exit_code") != 0:
            raise RuntimeError(f"installed status not terminal: {status}")
        artifacts = status.get("artifacts")
        if not isinstance(artifacts, dict) or set(artifacts) != ARTIFACT_KEYS:
            raise RuntimeError(f"bad installed artifact keys: {artifacts}")
        expected_run = pathlib.Path(launch["run_dir"])
        expected_paths = {
            "state": expected_run / "state.json",
            "transcript": expected_run / "transcript.md",
            "recap": expected_run / "recap.md",
        }
        for key, expected in expected_paths.items():
            if artifacts.get(key) != str(expected) or not expected.is_file():
                raise RuntimeError(
                    f"installed status {key} path mismatch: {artifacts.get(key)!r}"
                )


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: check_installed_wheel.py DIST_DIR", file=sys.stderr)
        return 2
    try:
        check(pathlib.Path(argv[0]))
    except Exception as exc:
        print(f"[check_installed_wheel] {exc}", file=sys.stderr)
        return 1
    print(f"[check_installed_wheel] ok: duet-cli {_source_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
