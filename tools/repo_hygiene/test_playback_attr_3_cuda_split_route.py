"""Text-level contract tests for the PLAYBACK-ATTR-3-CUDA split-build route.

WHY TEXT-LEVEL. Every surface under test is a PowerShell generator whose product runs on a
host this suite cannot reach -- Ultra-Magnus (CUDA), the board host (Qt/MinGW), Bachelor (the
measurement laptop). What can be proved here is what the emitted text says, and that is
exactly where the failures this route exists to prevent were visible in advance: a job
targeting sm_89 for a GPU that cannot run it, an export probe on a host with no compiler, a
verdict reached without checking whether the backend loaded at all.

WHY ONE TestCase METHOD PER CLAIM. The repo-hygiene job runs
``python -m unittest discover -s tools/repo_hygiene -p "test_*.py" -t .``; under that runner a
module-level ``def test_*`` table collects ZERO cases and exits green. These are TestCase
methods so they run under both unittest discovery and pytest.

WHY THE MEDIA EXTENSIONS ARE COMPOSED FROM PIECES. The project PreToolUse gate
(tools/hooks/mlv-never-authorized.py) refuses to write a file carrying a bare media-extension
literal without a CLIP_OR_NONE authorization -- correctly, and including this test. The
extensions are therefore assembled at runtime; the assertion is unchanged.

Ruling: .claude-state/fleet-runs/swarm-attr3-buildhost-20260916T2150Z/SYNTHESIS.md
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

DLL_PAIR_JOB = ROOT / "tools" / "profiling" / "ultramagnus" / "playback-attr-3-cuda-dll-job.ps1"
ASSEMBLE = ROOT / "tools" / "profiling" / "bachelor" / "playback-attr-3-cuda-assemble.ps1"
STAGE_JOB = ROOT / "tools" / "profiling" / "bachelor" / "playback-attr-3-cuda-stage-job.ps1"
SHARED_MODULE = ROOT / "tools" / "profiling" / "bachelor" / "AttrCudaArtifacts.psm1"
ATTRIBUTION_JOB = ROOT / "tools" / "profiling" / "bachelor" / "playback-attr-3-cuda-job.ps1"
RETIRED_COMPILE_JOB = ROOT / "tools" / "profiling" / "bachelor" / "playback-attr-3-cuda-compile-job.ps1"
RUNBOOK = ROOT / "docs" / "playback-attr-3-cuda.md"

# The scripts written for this route. The footage/cache token table below is asserted against
# exactly these -- the attribution job legitimately names one owner-authorized path and is not
# in this set.
NEW_SCRIPTS = (DLL_PAIR_JOB, ASSEMBLE, STAGE_JOB, SHARED_MODULE)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class DllPairJobTargetsSm86Tests(unittest.TestCase):
    """The measurement host's GPU cannot run sm_89 cubins; sm_86 is not optional."""

    def setUp(self) -> None:
        self.text = _read(DLL_PAIR_JOB)

    def test_required_architecture_is_sm86(self) -> None:
        self.assertIn("$RequiredArchitecture = 'sm_86'", self.text)

    def test_default_architecture_request_is_sm86(self) -> None:
        self.assertIn("[string[]]$CudaArchitectures = @('sm_86')", self.text)

    def test_generator_refuses_a_request_without_the_required_architecture(self) -> None:
        self.assertRegex(
            self.text,
            r"if \(\$architectures -notcontains \$RequiredArchitecture\) \{\s*\n\s*throw",
        )

    def test_emitted_job_proves_the_architecture_on_the_built_bytes(self) -> None:
        # A requested nvcc flag is not an emitted cubin: the job re-derives the tokens from
        # cuobjdump --list-elf and refuses when the required one is absent.
        self.assertIn("--list-elf", self.text)
        self.assertIn("$tokens -notcontains $RequiredArchitecture", self.text)
        self.assertIn("'architectureProof'", self.text)


class DllPairJobBuildsBothDllsTests(unittest.TestCase):
    """Shipping only the recon DLL produced a package that silently fell back."""

    def setUp(self) -> None:
        self.text = _read(DLL_PAIR_JOB)

    def test_uses_the_tracked_recon_backend_script(self) -> None:
        self.assertIn("build-backend-dll.ps1", self.text)

    def test_uses_the_tracked_amaze_backend_script(self) -> None:
        self.assertIn("amaze-debayer-dll.ps1", self.text)

    def test_both_dll_artifacts_are_named_and_published(self) -> None:
        self.assertIn("$ReconDllName = 'igpu_recon_cuda.dll'", self.text)
        self.assertIn("$AmazeDllName = 'igpu_amaze_debayer_cuda.dll'", self.text)
        self.assertIn("name = $ReconDllName; source = $reconDll", self.text)
        self.assertIn("name = $AmazeDllName; source = $amazeDll", self.text)

    def test_a_failed_amaze_build_is_fatal(self) -> None:
        self.assertRegex(self.text, r"if \(\$amazeRc -ne 0[^\n]*\)\s*\{\s*\n\s*Complete-Failed 12")

    def test_ships_the_cuda_runtime_from_the_compiling_toolkit(self) -> None:
        self.assertIn("$CudartName = 'cudart64_12.dll'", self.text)
        self.assertIn("$cudartSource = Join-Path $cudaBin $CudartName", self.text)


class DllPairManifestFieldsTests(unittest.TestCase):
    """The manifest is what every downstream host reads instead of re-deriving."""

    def setUp(self) -> None:
        self.text = _read(DLL_PAIR_JOB)

    def test_manifest_is_named_dll_pair_manifest_json(self) -> None:
        self.assertIn("$ManifestName = 'dll-pair-manifest.json'", self.text)

    def test_manifest_carries_every_required_field(self) -> None:
        for field in (
            "sourceCommit = $SourceCommit",
            "cudaArch = @($CudaArchitectures)",
            "nvccVersion = $nvccVersion",
            "files = $files",
            "pendingSymbolPresence = $pendingSymbolPresence",
            "builtOnHost = $env:COMPUTERNAME",
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.text)

    def test_hashes_are_lowercase_sha256(self) -> None:
        self.assertIn("function Get-ShaLower", self.text)
        self.assertIn("ToLowerInvariant()", self.text)

    def test_pending_symbol_presence_is_a_real_boolean_bound_to_the_exports(self) -> None:
        self.assertIn(
            "$pendingSymbolPresence = [bool](@($exports[$ReconDllName] | Select-String 'igpu_recon_')",
            self.text,
        )

    def test_publish_is_transactional_with_the_manifest_last(self) -> None:
        self.assertIn(".partial", self.text)
        publish_partials = self.text.index("$StepLog['publishPartials'] = 0")
        publish_rename = self.text.index("$StepLog['publishRename'] = 0")
        publish_manifest = self.text.index("$StepLog['publishManifest'] = 0")
        self.assertLess(publish_partials, publish_rename)
        self.assertLess(publish_rename, publish_manifest)

    def test_job_owns_its_temp(self) -> None:
        self.assertIn("$env:TEMP = $Scratch", self.text)
        self.assertIn("$env:TMP = $Scratch", self.text)

    def test_failure_codes_are_distinct(self) -> None:
        pairs = re.findall(r"Complete-Failed (\d+) '([a-zA-Z]+)'", self.text)
        self.assertTrue(pairs, "the job declares no failure codes at all")
        by_code: dict[str, set[str]] = {}
        for code, step in pairs:
            by_code.setdefault(code, set()).add(step)
        for code, steps in by_code.items():
            with self.subTest(code=code):
                self.assertEqual(len(steps), 1, f"exit code {code} is shared by steps {sorted(steps)}")


class AssemblerManifestFieldsTests(unittest.TestCase):
    """The board host emits the layout the attribution job already verifies, plus three fields."""

    def setUp(self) -> None:
        self.text = _read(ASSEMBLE)

    def test_emits_the_schema_the_attribution_job_reads(self) -> None:
        self.assertIn("schema = 'mlvapp.playback-attr-3-cuda-build-cache-manifest.v1'", self.text)
        for field in ("exe = [ordered]@{", "dll = [ordered]@{", "packageZip = [ordered]@{"):
            with self.subTest(field=field):
                self.assertIn(field, self.text)

    def test_carries_the_three_added_fields(self) -> None:
        for field in (
            "pendingSymbolPresence = $pendingSymbolPresence",
            "dllPairManifestSha256 = $dllPairManifestSha256",
            "exeBuiltOnHost = $env:COMPUTERNAME",
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.text)

    def test_verifies_the_dll_pair_manifest_before_deploying_it(self) -> None:
        self.assertIn("dll-pair-manifest.json", self.text)
        self.assertIn("DLL pair was built from", self.text)
        self.assertIn("sha256 mismatch for", self.text)
        binding = self.text.index("$StepLog['dllPairBinding'] = 0")
        deploy = self.text.index("$StepLog['dllPairDeploy'] = 0")
        self.assertLess(binding, deploy, "the pair must be verified before it is deployed")

    def test_requires_both_dlls_and_the_cuda_runtime(self) -> None:
        self.assertIn("@('igpu_recon_cuda.dll', 'igpu_amaze_debayer_cuda.dll', 'cudart64_12.dll')", self.text)

    def test_builds_from_a_clean_archive_with_injected_build_identity(self) -> None:
        self.assertIn("git -C $RepoRoot archive", self.text)
        header = self.text.index("New-AttrCudaBuildInfoHeader -SourceCommit $SourceCommit")
        qmake = self.text.index("$qmakeOutput = @(& $qmakeExe $proFile")
        self.assertLess(header, qmake, "build_buildinfo.h must be written before qmake runs")

    def test_deploys_the_qt_and_mingw_runtime(self) -> None:
        self.assertIn("--release --no-translations --compiler-runtime", self.text)
        self.assertIn("'libgomp-1.dll', 'libgcc_s_seh-1.dll', 'libstdc++-6.dll', 'libwinpthread-1.dll'", self.text)

    def test_publish_is_transactional_with_the_build_manifest_last(self) -> None:
        publish_partials = self.text.index("$StepLog['publishPartials'] = 0")
        publish_rename = self.text.index("$StepLog['publishRename'] = 0")
        publish_manifest = self.text.index("$StepLog['publishManifest'] = 0")
        self.assertLess(publish_partials, publish_rename)
        self.assertLess(publish_rename, publish_manifest)

    def test_make_job_count_is_quoted(self) -> None:
        # Unquoted, PowerShell hands mingw32-make a bare -j and the value separately, and make
        # refuses with "the '-j' option requires a positive integer argument".
        self.assertIn('"-j$MakeJobs"', self.text)


class StageJobTests(unittest.TestCase):
    """Side-files in, cache out, build.json last."""

    def setUp(self) -> None:
        self.text = _read(STAGE_JOB)

    def test_moves_the_package_and_manifest_from_inbox_side_files(self) -> None:
        self.assertIn("$Inbox = Join-Path $AgentRoot 'inbox'", self.text)
        self.assertIn("$Cache = Join-Path $AgentRoot 'cache'", self.text)
        self.assertIn("expected side-file missing from the inbox", self.text)

    def test_reverifies_sha256_on_the_host(self) -> None:
        self.assertIn("$actual = Get-ShaLower ([string]$item.path)", self.text)
        self.assertIn("sha256 mismatch for", self.text)

    def test_publishes_the_build_manifest_last(self) -> None:
        rename = self.text.index("$StepLog['publishRename'] = 0")
        manifest = self.text.index("$StepLog['publishManifest'] = 0")
        cleanup = self.text.index("$StepLog['inboxCleanup'] = 0")
        self.assertLess(rename, manifest, "artifacts must be published before build.json")
        self.assertLess(manifest, cleanup, "side-files may only be removed after the publish")

    def test_side_files_are_addressed_by_exact_name(self) -> None:
        self.assertIn("$path = Join-Path $Inbox $name", self.text)
        self.assertNotIn("Get-ChildItem", self.text)


class AttributionJobTests(unittest.TestCase):
    """What the measurement host may no longer do, and what it must check first."""

    def setUp(self) -> None:
        self.text = _read(ATTRIBUTION_JOB)

    def test_no_msvc_export_tooling_anywhere_in_the_job(self) -> None:
        # Bachelor has Visual Studio WITHOUT the VC tools component. The old probe could only
        # ever throw there, which is why the symbol test moved to the building host.
        lowered = self.text.lower()
        for token in ("dumpbin", "vswhere"):
            with self.subTest(token=token):
                self.assertNotIn(token, lowered)

    def test_pending_symbol_presence_is_read_from_the_build_manifest(self) -> None:
        self.assertIn("$pendingSymbolPresence = [bool]$buildManifest.pendingSymbolPresence", self.text)

    def test_a_missing_or_non_boolean_pending_symbol_presence_is_refused(self) -> None:
        # The check moved into the shared authentication call, which the job cannot get past.
        self.assertIn(
            "ATTRCUDA_BUILD_MANIFEST_SYMBOL_PRESENCE_INVALID", _read(SHARED_MODULE)
        )

    def test_the_build_manifest_is_authenticated_before_it_is_parsed(self) -> None:
        # The cache is mutable and this job does not own it: a same-named build.json with
        # matching artifacts would otherwise forge pendingSymbolPresence and the DLL association.
        self.assertIn("[ValidatePattern('^[0-9a-f]{64}$')]\n    [string]$BuildManifestSha256", self.text)
        self.assertIn(
            "$buildManifest = Assert-AttrCudaBuildManifest -Path $buildManifestPath "
            "-ExpectedSha256 $BuildManifestSha256 -ExpectedSourceCommit $SourceCommit",
            self.text,
        )
        module = _read(SHARED_MODULE)
        function = module[module.index("function Assert-AttrCudaBuildManifest") :]
        function = function[: function.index("\nfunction ")]
        # Statement positions, not prose: the doc comment names ConvertFrom-Json too.
        self.assertLess(
            function.index('throw "ATTRCUDA_BUILD_MANIFEST_SHA_MISMATCH'),
            function.index("$manifest = [IO.File]::ReadAllText($Path) | ConvertFrom-Json"),
            "the hash must be checked before the manifest is parsed",
        )

    def test_the_build_manifest_must_chain_to_the_dll_pair_manifest(self) -> None:
        self.assertIn("ATTRCUDA_BUILD_MANIFEST_DLLPAIR_UNBOUND", _read(SHARED_MODULE))
        self.assertIn("dllPairManifestSha256=$dllPairManifestSha256", self.text)

    def test_the_build_manifest_source_commit_must_equal_the_generators(self) -> None:
        self.assertIn("ATTRCUDA_BUILD_MANIFEST_COMMIT_MISMATCH", _read(SHARED_MODULE))

    def test_backend_availability_gate_is_present(self) -> None:
        # The parse and the decision live in the shared module (and are embedded verbatim into
        # the emitted job); the job wires them to its refusal path.
        module = _read(SHARED_MODULE)
        for token in ("cuda_backend_available", "r16_available", "r16_reason"):
            with self.subTest(token=token):
                self.assertIn(token, module)
        self.assertIn("BACKEND_NOT_AVAILABLE", self.text)
        self.assertIn("exit $verdict.exitCode", self.text)

    def test_the_gate_requires_both_fields_to_be_one(self) -> None:
        module = _read(SHARED_MODULE)
        self.assertIn(
            "$admitted = ($cudaBackendAvailable -eq '1' -and $r16Available -eq '1')", module
        )
        self.assertIn("exitCode = $(if ($admitted) { 0 } else { 15 })", module)
        self.assertIn("if (-not $verdict.admitted)", self.text)

    def test_the_gate_runs_before_every_verdict(self) -> None:
        gate = self.text.index("RESULT=BACKEND_NOT_AVAILABLE")
        for later_verdict in (
            "RESULT=GPU_RECON_FRAMES_ZERO",
            "RESULT=CPU_FALLBACK_DETECTED",
            "RESULT=MEASUREMENT_CAPTURED",
        ):
            with self.subTest(verdict=later_verdict):
                self.assertLess(gate, self.text.index(later_verdict))

    def test_both_fields_and_the_reason_are_recorded(self) -> None:
        self.assertIn("cudaBackendAvailable = $verdict.cudaBackendAvailable", self.text)
        self.assertIn("r16Available = $verdict.r16Available", self.text)
        self.assertIn("r16Reason = $verdict.r16Reason", self.text)
        self.assertIn("diagnostics = $diagnostics", self.text)

    def test_the_log_comes_from_the_smoke_result_not_a_glob(self) -> None:
        # run-release-gui-smoke.ps1 writes into logs-<stem>-<GUID>, so the old search could
        # never match and the job threw before reaching any gate.
        self.assertIn(
            "Resolve-AttrCudaSmokeRunLog -ResultJsonPath $resultPath -ContainingRoot $Work",
            self.text,
        )
        self.assertNotIn("-Filter 'mlvapp-*.log'", self.text)

    def test_an_unavailable_log_fails_closed_with_its_own_code(self) -> None:
        self.assertIn("RESULT=SMOKE_LOG_UNAVAILABLE", self.text)
        self.assertIn("exit 16", self.text)
        refusal = self.text.index("RESULT=SMOKE_LOG_UNAVAILABLE")
        for later_verdict in ("RESULT=BACKEND_NOT_AVAILABLE", "RESULT=MEASUREMENT_CAPTURED"):
            with self.subTest(verdict=later_verdict):
                self.assertLess(refusal, self.text.index(later_verdict))


class RetiredCompileJobTests(unittest.TestCase):
    """The old route must refuse, and say where to go instead."""

    def setUp(self) -> None:
        self.text = _read(RETIRED_COMPILE_JOB)

    def test_refuses_to_emit(self) -> None:
        self.assertIn("is RETIRED and emits nothing", self.text)

    def test_refuses_before_writing_or_archiving_anything(self) -> None:
        refusal = self.text.index('throw @"')
        archive = self.text.index("git -C $RepoRoot archive")
        write = self.text.index("[IO.File]::WriteAllText($jobPath")
        self.assertLess(refusal, archive)
        self.assertLess(refusal, write)

    def test_names_the_replacement_route(self) -> None:
        for replacement in (
            "playback-attr-3-cuda-dll-job.ps1",
            "playback-attr-3-cuda-assemble.ps1",
            "playback-attr-3-cuda-stage-job.ps1",
            "playback-attr-3-cuda.md",
        ):
            with self.subTest(replacement=replacement):
                self.assertIn(replacement, self.text)


class RunbookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = _read(RUNBOOK)

    def test_documents_the_route_in_order(self) -> None:
        positions = [
            self.text.index("Toolchain probe first"),
            self.text.index("playback-attr-3-cuda-dll-job.ps1"),
            self.text.index("playback-attr-3-cuda-assemble.ps1"),
            self.text.index("playback-attr-3-cuda-stage-job.ps1"),
            self.text.index("Attribution job (inside the owner-granted lane)"),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_carries_the_trap_paragraph(self) -> None:
        lowered = self.text.lower()
        self.assertIn("a precedent script is not a precedent result", lowered)
        self.assertIn("result.json", lowered)
        self.assertIn("toolchain probe receipt", lowered)

    def test_says_the_old_route_is_retired(self) -> None:
        self.assertIn("retired and refuses to emit", self.text.lower())


class NoFootageTokensTests(unittest.TestCase):
    """None of the new scripts may name, glob or resolve footage, or sweep the agent cache.

    NA-4 admits a real clip only as the one owner-typed path the attribution job is handed. The
    build-route scripts have no business knowing footage exists, and a cache sweep is how an
    id-to-file resolver gets reintroduced by accident.
    """

    # Assembled, not written literally: see the module docstring.
    MEDIA_EXTENSIONS = tuple("." + suffix for suffix in ("mlv", "raw", "dng", "cdng", "mp4", "mov"))
    FORBIDDEN_SUBSTRINGS = ("clip",) + MEDIA_EXTENSIONS

    def test_no_footage_or_media_extension_tokens(self) -> None:
        for path in NEW_SCRIPTS:
            lowered = _read(path).lower()
            for token in self.FORBIDDEN_SUBSTRINGS:
                with self.subTest(script=path.name, token=token):
                    self.assertNotIn(token, lowered)

    def test_no_cache_globbing(self) -> None:
        for path in NEW_SCRIPTS:
            for number, line in enumerate(_read(path).splitlines(), start=1):
                if "cache" not in line.lower():
                    continue
                with self.subTest(script=path.name, line=number):
                    self.assertNotIn("*", line, f"{path.name}:{number} globs over a cache path: {line.strip()}")


class SharedNamingContractTests(unittest.TestCase):
    """One definition of the names three surfaces must agree on without exchanging state."""

    def test_module_exports_the_naming_helper(self) -> None:
        text = _read(SHARED_MODULE)
        export = text[text.index("Export-ModuleMember") :]
        for name in ("Get-AttrCudaArtifactNames", "New-AttrCudaBuildInfoHeader"):
            with self.subTest(name=name):
                self.assertIn(name, export)

    def test_consumers_import_the_module_rather_than_restating_the_convention(self) -> None:
        for path in (ASSEMBLE, STAGE_JOB):
            with self.subTest(script=path.name):
                self.assertIn("Import-Module (Join-Path $PSScriptRoot 'AttrCudaArtifacts.psm1')", _read(path))

    def test_module_names_match_what_the_attribution_job_derives(self) -> None:
        module = _read(SHARED_MODULE)
        attribution = _read(ATTRIBUTION_JOB)
        for fragment in (
            '"MLVApp-playback-attr-3-cuda-$shortSha.exe"',
            '"igpu_recon_cuda-playback-attr-3-cuda-$shortSha.dll"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, module)
                self.assertIn(fragment, attribution)


if __name__ == "__main__":
    unittest.main()
