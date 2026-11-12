"""Best-effort source update shared by the Windows and macOS launchers."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess


def pull_latest(root: Path, timeout: float = 5) -> None:
    git = shutil.which("git")
    if git is None:
        print("Git is not installed; skipping source update.", flush=True)
        return

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    env["GIT_ASKPASS"] = ""
    env["SSH_ASKPASS"] = ""
    env["SSH_ASKPASS_REQUIRE"] = "never"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    print("Checking for source updates (up to 5 seconds)...", flush=True)
    try:
        process = subprocess.Popen(
            [git, "-c", "credential.interactive=false", "-c", "core.askPass=", "pull", "--ff-only"],
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            **options,
        )
    except OSError as error:
        print(f"Cannot check source updates; continuing startup: {error}", flush=True)
        return

    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Git may have spawned SSH or a credential helper; stop the whole tree.
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()
        process.wait()
        print("Source update timed out; continuing startup.", flush=True)
        return

    if returncode:
        print("Source update unavailable; continuing startup.", flush=True)


if __name__ == "__main__":
    pull_latest(Path(__file__).resolve().parent.parent)
