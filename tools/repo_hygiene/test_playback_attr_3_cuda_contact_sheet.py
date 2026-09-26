"""CUDA-PLAYBACK-CONTACT-SHEET-1: behaviour tests for the attribution job generator's
opt-in -ContactSheet switch (playback-attr-3-cuda-job.ps1).

Kept as its own small file, separate from the ~4000-line
test_playback_attr_3_cuda_behaviour.py, on purpose: UM-CUDA-BENCH-VENUE-1 is concurrently
adding a venue parameter to the same generator, and a small self-contained test file is far
less likely to collide with that work than an insertion into the shared suite.

WHAT IS EXERCISED. This generates real jobs with the real generator against the same
throwaway fixture repo `_make_fixture_repo` builds for the existing suite (imported from
there rather than duplicated) and asserts on the EMITTED JOB TEXT: the switch is off by
default, an off run's smoke invocation is byte-identical to before this card (the "switch
off -> unchanged job text" requirement), an on run's invocation carries
--contact-sheet-dir/--contact-sheet-frames, and both emitted jobs parse as valid
PowerShell. It does not run either emitted job (no CUDA build, no Bachelor here --
round-1 scope).
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.repo_hygiene.test_playback_attr_3_cuda_behaviour import (  # noqa: E402
    ATTRIBUTION_GENERATOR,
    MODULE,
    OWNER_FOOTAGE_MODULE,
    PWSH,
    _long_path,
    _make_fixture_repo,
    _run_pwsh_file,
    requires_git,
    requires_pwsh,
    tempfile,
)

# The base smoke invocation's own text, exactly as playback-attr-3-cuda-job.ps1 emits it when
# -ContactSheet is not passed. A change to this literal is a real change to what every
# non-contact-sheet leg runs, so this is pinned rather than derived.
_BASE_CMD_TAIL = (
    "-RequireLookAssist:`$false -Scope none -FrameTelemetry -PreserveExperimentalEnvironment "
    "-ExtraEnvironment @($envList)\"\n"
)


@requires_pwsh
@requires_git
class ContactSheetSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        if PWSH is None:  # pragma: no cover - guarded by requires_pwsh too
            self.skipTest("pwsh is not on PATH")
        self._tmp = tempfile.TemporaryDirectory(prefix="attr3-contact-sheet-")
        self.tmp = _long_path(Path(self._tmp.name))
        self.addCleanup(self._tmp.cleanup)
        self.repo = self.tmp / "repo"
        self.shas = _make_fixture_repo(self.repo)

    def _generate(self, out_name: str, *extra_args: str) -> Path:
        out_file = self.tmp / out_name
        script = self.tmp / f"generate-{out_name}.ps1"
        args = " ".join(extra_args)
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"& '{ATTRIBUTION_GENERATOR}' -SourceCommit '{self.shas[1]}' "
            f"-BuildManifestSha256 '{'a' * 64}' -ClipId 'tiny_dual_iso' "
            f"-FixtureSha256 '{'b' * 64}' -OutFile '{out_file}' "
            f"-RepoRoot '{self.repo}' {args}\n",
            encoding="utf-8",
        )
        proc = _run_pwsh_file(script)
        self.assertEqual(proc.returncode, 0, f"generator failed: {proc.stdout}\n{proc.stderr}")
        self.assertTrue(out_file.is_file(), "generator reported success but wrote no job file")
        return out_file

    def _assert_valid_powershell(self, job_file: Path) -> None:
        probe = self.tmp / f"probe-{job_file.name}.ps1"
        probe.write_text(
            "$errors = $null\n"
            f"[void][System.Management.Automation.PSParser]::Tokenize(\n"
            f"    (Get-Content -Raw -LiteralPath '{job_file}'), [ref]$errors)\n"
            "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Output $_.Message } }\n"
            "else { Write-Output 'NO_PARSE_ERRORS' }\n",
            encoding="utf-8",
        )
        proc = _run_pwsh_file(probe)
        self.assertEqual(proc.returncode, 0, f"{proc.stdout}\n{proc.stderr}")
        self.assertIn("NO_PARSE_ERRORS", proc.stdout, proc.stdout)

    def test_switch_off_by_default_bakes_false_and_leaves_smoke_invocation_unchanged(self) -> None:
        job_file = self._generate("off.job.ps1")
        text = job_file.read_text(encoding="utf-8")

        self.assertIn("$ContactSheetEnabled = $false", text)
        self.assertIn("$ContactSheetFrameCount = 6", text)  # generator's own default

        # The base $cmd assignment's own tail must be byte-identical to what every
        # non-contact-sheet leg ran before this card: no AdditionalArgs baked into it.
        self.assertIn(_BASE_CMD_TAIL, text)
        base_cmd_line = next(line for line in text.splitlines() if line.startswith('$cmd = "& '))
        self.assertNotIn("AdditionalArgs", base_cmd_line)

        self._assert_valid_powershell(job_file)

    def test_switch_on_bakes_true_frame_count_and_additional_args(self) -> None:
        job_file = self._generate("on.job.ps1", "-ContactSheet", "-ContactSheetFrames", "8")
        text = job_file.read_text(encoding="utf-8")

        self.assertIn("$ContactSheetEnabled = $true", text)
        self.assertIn("$ContactSheetFrameCount = 8", text)

        # The base $cmd line itself is STILL unchanged (appended to at runtime, not baked) --
        # only the runtime conditional block differs between an off and on generation.
        self.assertIn(_BASE_CMD_TAIL, text)

        # The runtime conditional block that appends -AdditionalArgs must reference both app
        # flags and the frame-count variable, and must run before $cmd is used to launch smoke.
        conditional_match = re.search(
            r"if \(\$ContactSheetEnabled\) \{.*?\$cmd = \"\$cmd -AdditionalArgs \$contactSheetAdditionalArgs\"\n\}",
            text,
            re.DOTALL,
        )
        self.assertIsNotNone(conditional_match, "expected the contact-sheet $cmd append block")
        block = conditional_match.group(0)
        self.assertIn("--contact-sheet-dir", block)
        self.assertIn("--contact-sheet-frames", block)
        self.assertIn("$ContactSheetFrameCount", block)

        self._assert_valid_powershell(job_file)

    def test_publish_block_runs_before_the_artifact_index_is_built(self) -> None:
        job_file = self._generate("on-publish-order.job.ps1", "-ContactSheet")
        text = job_file.read_text(encoding="utf-8")

        publish_pos = text.index("$contactSheetPubDir = Join-Path $Pub 'contact-sheet\\raw'")
        index_pos = text.index(
            "$files = Get-ChildItem -LiteralPath $Pub -Recurse -File"
        )
        self.assertLess(
            publish_pos,
            index_pos,
            "contact-sheet frames must be published before the artifact index is built, "
            "or they will not be indexed",
        )

    def _extract_cmd_construction_snippet(self, text: str) -> str:
        """The real emitted-job lines from the ConvertTo-PsSingleQuoted helper through the end
        of the contact-sheet $cmd-append block -- executed verbatim below with stub inputs, so
        this proves RUNTIME behaviour (what $cmd actually becomes), not just static text shape.
        """
        start = text.index("function ConvertTo-PsSingleQuoted")
        end_marker = '$cmd = "$cmd -AdditionalArgs $contactSheetAdditionalArgs"\n}'
        end = text.index(end_marker) + len(end_marker)
        return text[start:end]

    def _eval_cmd_for(self, text: str, enabled: bool, frame_count: int) -> str:
        snippet = self._extract_cmd_construction_snippet(text)
        work_dir = self.tmp / f"work-eval-{enabled}-{frame_count}"
        probe = self.tmp / f"probe-cmd-{enabled}-{frame_count}.ps1"
        probe.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"$Work = '{work_dir}'\n"
            "$smoke = 'smoke.ps1'\n"
            "$exePath = 'exe.exe'\n"
            "$clipPath = 'clip.mlv'\n"
            "$resultPath = 'result.json'\n"
            "$envList = \"'A=1','B=2'\"\n"
            f"$ContactSheetEnabled = ${'true' if enabled else 'false'}\n"
            f"$ContactSheetFrameCount = {frame_count}\n"
            + snippet
            + "\nWrite-Output \"CMD=$cmd\"\n",
            encoding="utf-8",
        )
        proc = _run_pwsh_file(probe)
        self.assertEqual(proc.returncode, 0, f"{proc.stdout}\n{proc.stderr}")
        line = next(l for l in proc.stdout.splitlines() if l.startswith("CMD="))
        return line[len("CMD="):]

    def test_runtime_cmd_is_unchanged_when_disabled_and_carries_args_when_enabled(self) -> None:
        # One generation covers both: the runtime block is present in the emitted text
        # regardless of the switch (only its EXECUTION is conditional -- see the -ContactSheet
        # param's own comment), so evaluating it twice with different stub inputs exercises the
        # real branch each caller actually takes at job-run time on Bachelor.
        job_file = self._generate("runtime-eval.job.ps1", "-ContactSheet", "-ContactSheetFrames", "8")
        text = job_file.read_text(encoding="utf-8")

        cmd_off = self._eval_cmd_for(text, enabled=False, frame_count=6)
        cmd_on = self._eval_cmd_for(text, enabled=True, frame_count=8)

        self.assertNotIn("AdditionalArgs", cmd_off)
        self.assertNotIn("contact-sheet", cmd_off)
        self.assertIn("-AdditionalArgs", cmd_on)
        self.assertIn("--contact-sheet-dir", cmd_on)
        self.assertIn("--contact-sheet-frames", cmd_on)
        self.assertIn("'8'", cmd_on)
        # The "off" prefix of the "on" command must still match the off command exactly --
        # AdditionalArgs is strictly appended, never interleaved or substituted in.
        self.assertTrue(cmd_on.startswith(cmd_off))

    def _extract_compose_step_snippet(self, text: str) -> str:
        """The real emitted job's contact-sheet compose step, run verbatim below (with the
        module's real functions imported, real embedded composer bytes, but a controlled
        $env:PATH) so this proves RUNTIME behaviour, not just that the text is present."""
        start_marker = (
            "# CUDA-PLAYBACK-CONTACT-SHEET-1 r1b: compose the raw captures "
            "into one labelled sheet +"
        )
        end_marker = (
            "[void](Publish-AttrCudaText -Path (Join-Path $Pub "
            "'contact-sheet\\compose-status.txt') -Value $contactSheetComposeMarker)\n"
            "    }"
        )
        start = text.index(start_marker)
        end = text.index(end_marker) + len(end_marker)
        return text[start:end]

    def test_compose_step_records_a_typed_marker_when_no_python_interpreter_is_on_path(self) -> None:
        # Forces the "unavailable" leg the hub asked for: with no python.exe/py.exe reachable,
        # both probe attempts in the real emitted compose step must fail closed and the step
        # must degrade to a non-fatal, typed compose-status.txt marker -- never throw, never
        # silently produce nothing.
        job_file = self._generate("compose-unavailable.job.ps1", "-ContactSheet")
        text = job_file.read_text(encoding="utf-8")
        snippet = self._extract_compose_step_snippet(text)

        work_dir = self.tmp / "compose-work"
        pub_dir = self.tmp / "compose-pub"
        raw_dir = pub_dir / "contact-sheet" / "raw"
        raw_dir.mkdir(parents=True)
        (raw_dir / "frame-00.png").write_bytes(b"not a real png, never read by this leg")

        probe = self.tmp / "probe-compose-unavailable.ps1"
        probe.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"Import-Module '{MODULE}' -Force\n"
            f"Import-Module '{OWNER_FOOTAGE_MODULE}' -Force\n"
            f"$Work = '{work_dir}'\n"
            f"$Pub = '{pub_dir}'\n"
            f"$contactSheetPubDir = '{raw_dir}'\n"
            "$ClipId = 'tiny_dual_iso'\n"
            "$SourceCommit = '" + self.shas[1] + "'\n"
            "$FixtureRehearsal = $true\n"
            "New-Item -ItemType Directory -Path $Work -Force | Out-Null\n"
            # A PATH with no python.exe/py.exe on it: both probe candidates in the snippet
            # below must fail to launch and the step must still complete without throwing.
            "$env:PATH = $Work\n"
            + snippet
            + "\nWrite-Output ('MARKER=' + $contactSheetComposeMarker)\n",
            encoding="utf-8",
        )
        proc = _run_pwsh_file(probe)
        self.assertEqual(proc.returncode, 0, f"{proc.stdout}\n{proc.stderr}")
        marker_line = next(l for l in proc.stdout.splitlines() if l.startswith("MARKER="))
        self.assertIn("CONTACT_SHEET_COMPOSE_UNAVAILABLE", marker_line)
        self.assertIn(
            "no Python 3 interpreter with Pillow+numpy was found on this venue", marker_line
        )
        status_file = pub_dir / "contact-sheet" / "compose-status.txt"
        self.assertTrue(status_file.is_file(), "expected a compose-status.txt marker file")
        self.assertIn("CONTACT_SHEET_COMPOSE_UNAVAILABLE", status_file.read_text(encoding="utf-8"))
        # Never attempted a compose with no interpreter: no sheet/stats output.
        self.assertFalse((pub_dir / "contact-sheet" / "sheet.png").exists())
        self.assertFalse((pub_dir / "contact-sheet" / "stats.json").exists())

    def test_frame_count_out_of_range_is_refused_at_parameter_binding(self) -> None:
        out_file = self.tmp / "refused.job.ps1"
        script = self.tmp / "generate-refused.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"& '{ATTRIBUTION_GENERATOR}' -SourceCommit '{self.shas[1]}' "
            f"-BuildManifestSha256 '{'a' * 64}' -ClipId 'tiny_dual_iso' "
            f"-FixtureSha256 '{'b' * 64}' -OutFile '{out_file}' "
            f"-RepoRoot '{self.repo}' -ContactSheet -ContactSheetFrames 0\n",
            encoding="utf-8",
        )
        proc = _run_pwsh_file(script)
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(out_file.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
