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
MAIN_WINDOW = REPO_ROOT / "platform" / "qt" / "MainWindow.cpp"
NEUTRAL_RECEIPT = REPO_ROOT / "tests" / "fixtures" / "receipts" / "neutral_look_assist_off_v4.marxml"
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

    clip = tmp_path / "clip.mlv"
    receipt = tmp_path / "locked.marxml"
    clip.write_bytes(b"same clip bytes")
    receipt.write_bytes(b"<receipt version=\"4\"/>")
    commits = subprocess.run(
        ["git", "rev-parse", "HEAD", "HEAD^"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout.splitlines()

    def write_smoke(path: Path, last_frame: int, build_sha: str) -> None:
        exe = tmp_path / f"{path.stem}.exe"
        log = tmp_path / f"{path.stem}.log"
        exe.write_bytes(f"exe:{build_sha}".encode("ascii"))
        log.write_bytes(f"log:{build_sha}".encode("ascii"))
        path.write_text(
            json.dumps(
                {
                    "repoRoot": str(REPO_ROOT),
                    "exePath": str(exe),
                    "clipPath": str(clip),
                    "launch": {
                        "arguments": [
                            "--input",
                            str(clip),
                            "--receipt",
                            str(receipt),
                        ]
                    },
                    "log": {
                        "path": str(log),
                        "runMetadata": {
                            "build_sha": build_sha,
                            "command_line": [
                                str(exe),
                                "--input",
                                str(clip),
                                "--receipt",
                                str(receipt),
                            ],
                        },
                    },
                    "visualQuality": {
                        "visualState": {
                            "look_assist_enabled": 0,
                            "temperature": 6000,
                            "tint": 0,
                            "raw_black": 20470,
                            "raw_white": 6000,
                            "chroma_smooth": 0,
                            "stretch_x": 3.0,
                            "stretch_y": 1.0,
                            "h_stretch_index": 0,
                            "v_stretch_index": 3,
                            "dual_iso_mode": 1,
                            "dual_iso_interp": 0,
                            "dual_iso_alias_map": 0,
                            "dual_iso_fullres": 1,
                            "drop_frame": 0,
                            "scale_request": 4,
                            "quality_mode": 2,
                            "receipt_supplied": 1,
                        },
                        "colorArtifactScan": {"verdict": "clear-heuristic"},
                    },
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
    write_smoke(before, 104, commits[1])
    write_smoke(after, 105, commits[0])
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


@pytest.mark.skipif(os.name != "nt", reason="MLV-App GUI smoke comparer is PowerShell-based")
def test_gui_smoke_ab_rejects_same_build_identity(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is unavailable")

    build_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout.strip()
    clip = tmp_path / "clip.mlv"
    receipt = tmp_path / "locked.marxml"
    clip.write_bytes(b"same clip bytes")
    receipt.write_bytes(b"<receipt version=\"4\"/>")

    def write_smoke(path: Path, marker: str) -> None:
        exe = tmp_path / f"{marker}.exe"
        log = tmp_path / f"{marker}.log"
        exe.write_bytes(f"different executable {marker}".encode("ascii"))
        log.write_bytes(f"log {marker}".encode("ascii"))
        state = {
            "look_assist_enabled": 0,
            "temperature": 6000,
            "tint": 0,
            "raw_black": 20470,
            "raw_white": 6000,
            "chroma_smooth": 0,
            "stretch_x": 3.0,
            "stretch_y": 1.0,
            "h_stretch_index": 0,
            "v_stretch_index": 3,
            "dual_iso_mode": 1,
            "dual_iso_interp": 0,
            "dual_iso_alias_map": 0,
            "dual_iso_fullres": 1,
            "drop_frame": 0,
            "scale_request": 4,
            "quality_mode": 2,
            "receipt_supplied": 1,
        }
        path.write_text(
            json.dumps(
                {
                    "repoRoot": str(REPO_ROOT),
                    "exePath": str(exe),
                    "clipPath": str(clip),
                    "launch": {
                        "arguments": [
                            "--input",
                            str(clip),
                            "--receipt",
                            str(receipt),
                        ]
                    },
                    "log": {
                        "path": str(log),
                        "runMetadata": {
                            "build_sha": build_sha,
                            "command_line": [
                                str(exe),
                                "--input",
                                str(clip),
                                "--receipt",
                                str(receipt),
                            ],
                        },
                    },
                    "visualQuality": {
                        "visualState": state,
                        "colorArtifactScan": {"verdict": "clear-heuristic"},
                    },
                    "validation": {
                        "ok": True,
                        "launchOnlyProbe": False,
                        "presentedFrames": 2,
                        "firstPresentedFrame": 89,
                        "lastPresentedFrame": 90,
                    },
                }
            ),
            encoding="utf-8",
        )

    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    write_smoke(before, "before")
    write_smoke(after, "after")
    output = tmp_path / "comparison.json"
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
            str(output),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 1, completed.stdout + completed.stderr
    result = json.loads(output.read_text(encoding="utf-8-sig"))
    assert result["verdict"] == "FAIL"
    assert any("same-arm A/B is forbidden" in failure for failure in result["failures"])


def test_release_builder_resolves_source_root_before_changing_directory() -> None:
    source = BUILD_RELEASE.read_text(encoding="utf-8")
    resolve_index = source.index("$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path")
    push_index = source.index("Push-Location $bd")
    assert resolve_index < push_index


def test_gui_smoke_ab_v3_requires_clean_capture_time_evidence() -> None:
    comparer = GUI_SMOKE_COMPARER.read_text(encoding="utf-8")
    runner = GUI_SMOKE_RUNNER.read_text(encoding="utf-8")
    main_window = MAIN_WINDOW.read_text(encoding="utf-8")

    assert 'schema = "gui-smoke-ab-compare.v3"' in comparer
    assert 'schema = "mlvapp-gui-smoke-result.v2"' in runner
    assert "Assert-RecordedFileBinding" in comparer
    assert "Get-EmbeddedBuildStamp" in comparer
    assert "missing MZ header" in comparer
    assert "same-tree A/B is forbidden" in comparer
    assert "build manifest is dirty, unbound, or not independently runnable" in comparer
    assert "per-run log snapshot" in comparer
    assert 'Label "$Name screenshot event"' in comparer
    assert "screenshot bytes/frame/serial are not atomically bound" in comparer
    assert "MLVAPP_GUI_SMOKE_RUN_NONCE" in runner
    assert "logs-{0}-{1}" in runner
    assert "eventSha256" in runner
    assert "frame=%7 serial=%8 generation=%9" in main_window
    assert "QCryptographicHash::Sha256" in main_window
    assert NEUTRAL_RECEIPT.read_text(encoding="utf-8") == (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<receipt version="4">\n'
        '  <lookAssistEnabled>0</lookAssistEnabled>\n'
        '</receipt>\n'
    )
