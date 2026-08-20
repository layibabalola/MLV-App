import json
import os
import base64
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "tools" / "coordination" / "invoke-mlv-exclusive.ps1"
STDOUT_SENTINEL = "EXCLUSIVE_STDOUT_SENTINEL"
STDERR_SENTINEL = "EXCLUSIVE_STDERR_SENTINEL"


@pytest.mark.skipif(os.name != "nt", reason="MLV-App process ownership is Windows-specific")
def test_exclusive_runner_preserves_child_stdout(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is unavailable")

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    completed = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(RUNNER),
            "-RepoRoot",
            str(repo_root),
            "-Owner",
            "pytest-stdout",
            "-FilePath",
            os.environ.get("COMSPEC", "cmd.exe"),
            "-ArgumentListBase64",
            base64.b64encode(json.dumps(["/d", "/c", "echo", STDOUT_SENTINEL]).encode()).decode(),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert STDOUT_SENTINEL in completed.stdout

    records_path = repo_root / ".claude-state" / "coordination" / "build-ownership.jsonl"
    records = [json.loads(line) for line in records_path.read_text(encoding="utf-8-sig").splitlines()]
    assert records[-1]["owner"] == "pytest-stdout"
    assert records[-1]["outcome"] == "succeeded"
    assert records[-1]["exitCode"] == 0


@pytest.mark.skipif(os.name != "nt", reason="MLV-App process ownership is Windows-specific")
def test_exclusive_runner_preserves_argument_boundaries(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is unavailable")

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    probe = (
        "import json, sys; print(json.dumps(sys.argv[1:])); "
        f"print({STDERR_SENTINEL!r}, file=sys.stderr)"
    )
    expected = ["my clip.mlv", "second"]
    completed = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(RUNNER),
            "-RepoRoot",
            str(repo_root),
            "-Owner",
            "pytest-arguments",
            "-FilePath",
            sys.executable,
            "-ArgumentListBase64",
            base64.b64encode(json.dumps(["-c", probe, *expected]).encode()).decode(),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip()) == expected
    assert STDERR_SENTINEL in completed.stderr


@pytest.mark.skipif(os.name != "nt", reason="MLV-App process ownership is Windows-specific")
def test_exclusive_runner_preserves_child_environment_and_path(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is unavailable")

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    path_marker = tmp_path / "nested-tool-bin"
    path_marker.mkdir()
    sentinel = "exclusive-environment-preserved"
    child_environment = os.environ.copy()
    child_environment["MLVAPP_EXCLUSIVE_ENV_SENTINEL"] = sentinel
    child_environment["PATH"] = os.pathsep.join(
        [str(path_marker), child_environment.get("PATH", "")]
    )
    probe = (
        "import json, os; "
        "print(json.dumps({"
        "'sentinel': os.environ.get('MLVAPP_EXCLUSIVE_ENV_SENTINEL'), "
        "'path': os.environ.get('PATH', '')"
        "}))"
    )
    completed = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(RUNNER),
            "-RepoRoot",
            str(repo_root),
            "-Owner",
            "pytest-environment",
            "-FilePath",
            sys.executable,
            "-ArgumentListBase64",
            base64.b64encode(json.dumps(["-c", probe]).encode()).decode(),
        ],
        cwd=REPO_ROOT,
        env=child_environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout.strip())
    assert observed["sentinel"] == sentinel
    assert str(path_marker).casefold() in observed["path"].casefold()
