"""Process-level regression tests for run-scoped shutdown."""
from __future__ import annotations

import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import duet


ROOT = pathlib.Path(__file__).resolve().parent.parent
DUET = ROOT / "duet.py"


def _wait_for(predicate, message: str, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(message)


def _read_json_if_ready(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _active_child_pid(run_dir: pathlib.Path):
    for pid_file in run_dir.glob("turn-*.pid"):
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        if duet._pid_alive(pid):
            return pid
    return None


@unittest.skipIf(sys.platform == "win32", "requires POSIX signals")
class TestRunScopedStop(unittest.TestCase):
    def test_immediate_stop_isolated_to_selected_live_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw).resolve()
            bin_dir = root / "bin"
            bin_dir.mkdir()
            fake_backend = bin_dir / "claude"
            fake_backend.write_text(
                "#!/usr/bin/env python3\n"
                "import time\n"
                "while True:\n"
                "    time.sleep(1)\n",
                encoding="utf-8",
            )
            fake_backend.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")

            supervisors: list[subprocess.Popen] = []
            run_dirs: list[pathlib.Path] = []
            child_pids: list[int] = []
            supervisor_starts: list[str] = []
            child_starts: list[str] = []
            try:
                for label in ("a", "b"):
                    work = root / label
                    work.mkdir()
                    info = root / f"{label}.json"
                    proc = subprocess.Popen(
                        [
                            sys.executable, str(DUET),
                            "--cwd", str(work),
                            "--runs-dir", str(work / "runs"),
                            "--run-info-file", str(info),
                            "--lead", "claude:planner",
                            "--partner", "claude:coder",
                            "--task", "wait for shutdown",
                            "--turns", "2",
                            "--timeout", "60",
                            "--no-worktree",
                            "--quiet",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=env,
                        start_new_session=True,
                    )
                    supervisors.append(proc)
                    supervisor_starts.append(_wait_for(
                        lambda p=proc.pid: duet._proc_start_identity(p),
                        f"cannot identify supervisor for run {label}",
                    ))
                    launch = _wait_for(
                        lambda p=info: _read_json_if_ready(p),
                        f"run {label} did not publish run info",
                    )
                    run_dir = pathlib.Path(launch["run_dir"])
                    run_dirs.append(run_dir)
                    child_pid = _wait_for(
                        lambda p=run_dir: _active_child_pid(p),
                        f"run {label} did not start its backend child",
                    )
                    child_pids.append(child_pid)
                    child_starts.append(_wait_for(
                        lambda p=child_pid: duet._proc_start_identity(p),
                        f"cannot identify backend child for run {label}",
                    ))

                for _ in range(2):
                    graceful = subprocess.run(
                        [
                            sys.executable, str(DUET),
                            "--stop", str(run_dirs[0]),
                        ],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=env,
                        timeout=10,
                    )
                    self.assertEqual(
                        graceful.returncode, 0,
                        graceful.stdout + graceful.stderr,
                    )
                    time.sleep(0.1)
                self.assertIsNone(
                    supervisors[0].poll(),
                    "repeated graceful stop hard-exited the supervisor",
                )

                stopped = subprocess.run(
                    [
                        sys.executable, str(DUET),
                        "--stop", str(run_dirs[0]),
                        "--immediate",
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    timeout=10,
                )
                self.assertEqual(
                    stopped.returncode, 0, stopped.stdout + stopped.stderr
                )
                _wait_for(
                    lambda: supervisors[0].poll() is not None,
                    "selected supervisor did not exit",
                )
                state_a = _wait_for(
                    lambda: _read_json_if_ready(run_dirs[0] / "state.json"),
                    "selected run did not retain readable state",
                )
                self.assertEqual(state_a["phase"], "finished")
                self.assertEqual(state_a["finished_reason"], "force_stop")
                self.assertFalse(duet._pid_alive(child_pids[0]))

                self.assertIsNone(supervisors[1].poll())
                self.assertTrue(duet._pid_alive(supervisors[1].pid))
                self.assertTrue(duet._pid_alive(child_pids[1]))
            finally:
                # Cleanup is exact and fail-closed. Never match process names.
                for pid, started in zip(child_pids, child_starts):
                    self._kill_validated(pid, str(fake_backend), started)
                for proc, run_dir, started in zip(
                        supervisors, run_dirs, supervisor_starts):
                    self._kill_validated(proc.pid, str(run_dir.parent), started)
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self._kill_validated(
                            proc.pid, str(run_dir.parent), started, signal.SIGKILL
                        )
                        proc.wait(timeout=3)

    def _kill_validated(self, pid: int, expected: str, saved_start: str,
                        sig: int = signal.SIGTERM) -> None:
        if not duet._pid_alive(pid):
            return
        self.assertEqual(duet._proc_start_identity(pid), saved_start)
        command = duet._proc_cmdline(pid)
        if command is not None:
            self.assertIn(expected, command)
        os.kill(pid, sig)


if __name__ == "__main__":
    unittest.main()
