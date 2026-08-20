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
GEN_BUILDINFO = REPO_ROOT / "tools" / "gen-buildinfo.ps1"
ARTIFACT_DETECTOR = REPO_ROOT / "tools" / "profiling" / "detect-playback-artifacts.ps1"
GUI_SMOKE_RUNNER = REPO_ROOT / "tools" / "profiling" / "run-release-gui-smoke.ps1"
GUI_SMOKE_COMPARER = REPO_ROOT / "tools" / "profiling" / "compare-release-gui-smoke-ab.ps1"
BUILD_RELEASE = REPO_ROOT / "tools" / "build-release.ps1"
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


@pytest.mark.skipif(os.name != "nt", reason="MLV-App release provenance is Windows-specific")
def test_buildinfo_header_overrides_qmake_time_sha(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is unavailable")

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    output = tmp_path / "build_buildinfo.h"
    completed = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(GEN_BUILDINFO),
            "-SrcRoot",
            str(REPO_ROOT),
            "-OutHeader",
            str(output),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    header = output.read_text(encoding="ascii")
    assert "#ifdef MLVAPP_GIT_SHA\n#undef MLVAPP_GIT_SHA\n#endif" in header
    assert f'#define MLVAPP_GIT_SHA "{head}"' in header
    assert f'#define MLVAPP_BUILD_SHA "{head}"' in header


@pytest.mark.skipif(os.name != "nt", reason="MLV-App playback detector is PowerShell-based")
def test_playback_detector_cadence_advisory_exits_cleanly(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is unavailable")

    trace = tmp_path / "trace.log"
    trace.write_text(
        "\n".join(
            f"[2026-08-20T00:00:0{i}.000Z] [INFO] [0x1] "
            f"draw_frame_ready.begin display_frame={i} play_checked=1 position={i}"
            for i in range(1, 7)
        )
        + "\n",
        encoding="ascii",
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
            str(ARTIFACT_DETECTOR),
            "-TraceLog",
            str(trace),
            "-CadenceAdvisory",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "ARTIFACT-CHECK verdict=PASS" in completed.stdout
    assert "ARTIFACT-CADENCE cadence_advisory=1" in completed.stdout


@pytest.mark.skipif(os.name != "nt", reason="MLV-App GUI smoke runner is PowerShell-based")
def test_gui_smoke_legacy_option_set_omits_new_cli_flags(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is unavailable")

    receipt = tmp_path / "locked.marxml"
    receipt.write_text(
        '<?xml version="1.0"?><receipt version="4"><lookAssistEnabled>0</lookAssistEnabled></receipt>',
        encoding="ascii",
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
            str(GUI_SMOKE_RUNNER),
            "-RepoRoot",
            str(REPO_ROOT),
            "-ExePath",
            str(GUI_SMOKE_RUNNER),
            "-ClipPath",
            str(GUI_SMOKE_RUNNER),
            "-Output",
            str(tmp_path / "result.json"),
            "-Receipt",
            str(receipt),
            "-NoLoop",
            "-DisableLookAssist",
            "-LegacyGuiSmokeOptions",
            "-DryRun",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    dry_run = json.loads(completed.stdout)
    arguments = dry_run["arguments"]
    assert "--drop-frame-mode" not in arguments
    assert "--loop" not in arguments
    assert "--no-look-assist" not in arguments
    assert arguments[arguments.index("--receipt") + 1] == str(receipt)
    assert dry_run["validationPolicy"]["legacyGuiSmokeOptions"] is True
    assert dry_run["validationPolicy"]["requireLookAssist"] is False


@pytest.mark.skipif(os.name != "nt", reason="MLV-App GUI smoke comparer is PowerShell-based")
def test_gui_smoke_ab_requires_same_last_presented_frame(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is unavailable")

    def write_smoke(path: Path, last_frame: int) -> None:
        path.write_text(
            json.dumps(
                {
                    "validation": {
                        "ok": True,
                        "launchOnlyProbe": False,
                        "presentedFrames": 2,
                        "firstPresentedFrame": last_frame - 1,
                        "lastPresentedFrame": last_frame,
                    }
                }
            ),
            encoding="utf-8",
        )

    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    write_smoke(before, 104)
    write_smoke(after, 105)
    completed = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(GUI_SMOKE_COMPARER),
            "-Before",
            str(before),
            "-After",
            str(after),
            "-Output",
            str(tmp_path / "comparison.json"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 1, completed.stdout + completed.stderr
    result = json.loads((tmp_path / "comparison.json").read_text(encoding="utf-8-sig"))
    assert result["verdict"] == "FAIL"
    assert result["presentedFrameEvidence"]["before"]["lastPresentedFrame"] == 104
    assert result["presentedFrameEvidence"]["after"]["lastPresentedFrame"] == 105
    assert any("not frame-locked" in failure for failure in result["failures"])


def test_release_builder_resolves_source_root_before_changing_directory() -> None:
    source = BUILD_RELEASE.read_text(encoding="utf-8")
    resolve_index = source.index("$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path")
    push_index = source.index("Push-Location $bd")
    assert resolve_index < push_index
