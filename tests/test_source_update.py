"""Source updates use disposable local repositories, never the working checkout."""

import shutil
import subprocess
import sys
import time

import pytest

from scripts import update_source


def test_missing_git_does_not_block_startup(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    update_source.pull_latest(tmp_path)
    assert "skipping" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("git") is None, reason="Git is required")
def test_fast_forward_and_divergence(tmp_path, capsys):
    def git(root, *args):
        return subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
            cwd=root, check=True, capture_output=True, text=True,
        ).stdout.strip()

    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init")
    (origin / "word.txt").write_text("first", encoding="utf-8")
    git(origin, "add", ".")
    git(origin, "commit", "-m", "initial")
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", str(origin), str(checkout))
    (origin / "word.txt").write_text("updated", encoding="utf-8")
    git(origin, "commit", "-am", "upstream update")

    update_source.pull_latest(checkout)
    assert (checkout / "word.txt").read_text(encoding="utf-8") == "updated"
    assert git(checkout, "rev-parse", "HEAD") == git(origin, "rev-parse", "HEAD")

    (checkout / "local.txt").write_text("local work", encoding="utf-8")
    git(checkout, "add", ".")
    git(checkout, "commit", "-m", "local change")
    local_head = git(checkout, "rev-parse", "HEAD")
    (origin / "word.txt").write_text("next", encoding="utf-8")
    git(origin, "commit", "-am", "divergent upstream")
    update_source.pull_latest(checkout)
    assert "continuing startup" in capsys.readouterr().out
    assert git(checkout, "rev-parse", "HEAD") == local_head
    assert (checkout / "local.txt").read_text(encoding="utf-8") == "local work"


def test_timeout_stops_update_and_descendants(monkeypatch, tmp_path, capsys):
    marker = tmp_path / "child-survived"
    child_code = (
        "import time; from pathlib import Path; "
        f"time.sleep(2); Path({str(marker)!r}).touch()"
    )
    parent_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(30)"
    )
    popen = subprocess.Popen
    processes = []

    def start_fake_git(args, **kwargs):
        process = popen([sys.executable, "-c", parent_code], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(shutil, "which", lambda _: "fake-git")
    # Keep taskkill's own Popen call real, since subprocess.run uses Popen too.
    def dispatch(args, **kwargs):
        return start_fake_git(args, **kwargs) if args[0] == "fake-git" else popen(args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", dispatch)
    started = time.monotonic()
    update_source.pull_latest(tmp_path, timeout=0.5)
    assert time.monotonic() - started < 2
    assert processes[0].poll() is not None
    assert "timed out" in capsys.readouterr().out
    time.sleep(2.2)
    assert not marker.exists(), "A Git descendant survived timeout cleanup"
