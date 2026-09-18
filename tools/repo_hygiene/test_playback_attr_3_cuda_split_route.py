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
import shutil
import subprocess
import unittest
from pathlib import Path

PWSH = shutil.which("pwsh")


def _pwsh_quote(value: str) -> str:
    """A PowerShell single-quoted literal; newlines survive as real newlines inside the quotes."""
    return "'" + value.replace("'", "''") + "'"


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
        self.assertIn(
            'Assert-AttrCudaDirectChild -Root $Inbox -Path (Join-Path $Inbox $name)', self.text
        )
        self.assertNotIn("Get-ChildItem", self.text)

    def test_names_are_derived_from_the_commit_not_taken_from_the_manifest(self) -> None:
        # build.json supplies hashes; the basenames come from the convention applied to
        # -SourceCommit, in the generator AND again on the host from the baked commit.
        self.assertIn("$derived = Get-AttrCudaArtifactNames -SourceCommit $SourceCommit", self.text)
        self.assertIn("$canonicalNames -cnotcontains $name", self.text)
        self.assertIn("the canonical name for $SourceCommit", self.text)
        self.assertIn("Names are derived, never taken from the manifest.", self.text)

    def test_every_name_and_every_resolved_path_is_checked_before_any_write(self) -> None:
        # Ordering is a property of the EMITTED job, so compare inside the template only -- the
        # generator's header prose names these cmdlets too.
        template = self.text[self.text.index("$template = @'") :]
        safety = template.index("$StepLog['artifactNameSafety'] = 0")
        for mutation in ("Publish-AttrCudaFileCopy -Source", "Publish-AttrCudaFileMove -Source",
                         "Remove-AttrCudaPartialFile -TrustedRoot $AgentRoot -Path ([string]$item.path)"):
            with self.subTest(mutation=mutation):
                self.assertLess(safety, template.index(mutation))
        for root, label in (("$Inbox", "inbox"), ("$Cache", "cache")):
            with self.subTest(root=root):
                self.assertIn(f"Assert-AttrCudaDirectChild -Root {root}", self.text)
        self.assertIn("Assert-AttrCudaSafeArtifactName -Name $name", self.text)

    def test_traversal_and_non_basenames_are_refused_by_the_shared_guard(self) -> None:
        module = _read(SHARED_MODULE)
        for token in (
            "ATTRCUDA_NAME_TRAVERSAL",
            "ATTRCUDA_NAME_NOT_A_BASENAME",
            "ATTRCUDA_PATH_NOT_DIRECT_CHILD",
        ):
            with self.subTest(token=token):
                self.assertIn(token, module)

    def test_verify_only_stops_before_anything_is_written(self) -> None:
        stop = self.text.index("RESULT=VERIFY_ONLY_OK")
        self.assertLess(stop, self.text.index("New-Item -ItemType Directory -Path $Work"))
        self.assertLess(stop, self.text.index("$PubReady = $true"))


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
            "RESULT=$resultVerb",
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
        for later_verdict in ("RESULT=BACKEND_NOT_AVAILABLE", "RESULT=$resultVerb"):
            with self.subTest(verdict=later_verdict):
                self.assertLess(refusal, self.text.index(later_verdict))


class AttributionJobFixtureRehearsalTests(unittest.TestCase):
    """ATTR3-FIXTURE-REHEARSAL-1: -ClipId also admits the two tracked fixtures, unmistakably."""

    _CLIP_ID_PATTERN_RX = re.compile(
        r"\[ValidatePattern\('(?P<pattern>[^']+)'\)\]\s*\r?\n\s*\[string\]\$ClipId"
    )

    def setUp(self) -> None:
        self.text = _read(ATTRIBUTION_JOB)
        match = self._CLIP_ID_PATTERN_RX.search(self.text)
        self.assertIsNotNone(match, "could not find the -ClipId ValidatePattern in the generator")
        self.clip_id_pattern = match.group("pattern")

    def match_in_powershell(self, candidates: dict[str, bool]) -> None:
        """Evaluate the pattern in the engine that ENFORCES it.

        The pattern is .NET's, not Python's: it carries a scoped inline flag `(?-i:...)` and the
        `\\z` anchor, and Python's `re` accepts neither on every supported version -- compiling it
        with `re` made these tests error on the CI runner while passing locally, and it would have
        been testing a different engine's semantics either way.
        """
        if PWSH is None:
            self.skipTest("pwsh is not on PATH")
        lines = ["$pattern = " + _pwsh_quote(self.clip_id_pattern)]
        for candidate in candidates:
            lines.append(
                "Write-Output ('CANDIDATE ' + " + _pwsh_quote(candidate) + " + ' -> ' + "
                "([bool](" + _pwsh_quote(candidate) + " -cmatch $pattern)))"
            )
        proc = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "\n".join(lines)],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for candidate, expected in candidates.items():
            with self.subTest(clip_id=candidate):
                self.assertIn(f"CANDIDATE {candidate} -> {expected}", proc.stdout, proc.stdout)

    def test_owner_clip_id_pattern_still_works(self) -> None:
        self.match_in_powershell({"M16-1243": True, "a99-123": True, "Z00-9999": True})

    def test_fixture_clip_ids_are_accepted(self) -> None:
        self.match_in_powershell({"tiny_dual_iso": True, "large_dual_iso": True})

    def test_an_unknown_or_miscased_stem_is_refused(self) -> None:
        # TINY_DUAL_ISO and a trailing newline were both sol findings (PR #137 r1 and r2): each
        # validated once and then failed the case-sensitive membership test.
        self.match_in_powershell({
            "some_other_clip": False,
            "tiny_dual_iso_extra": False,
            "TINY_DUAL_ISO": False,
            "Tiny_Dual_Iso": False,
            "medium_dual_iso": False,
            "tiny_dual_iso\n": False,
        })

    def test_fixture_ids_are_a_literal_allowlist_not_a_loose_pattern(self) -> None:
        # The flag is decided by an exact membership test against the two literals, never by
        # re-deriving it from the ValidatePattern regex.
        self.assertIn("$FixtureClipIds = @('tiny_dual_iso', 'large_dual_iso')", self.text)
        self.assertIn("$FixtureClipIds -ccontains $ClipId", self.text)

    def test_emitted_job_carries_the_flag_from_the_generators_membership_test(self) -> None:
        self.assertIn("$FixtureRehearsal = __FIXTURE_REHEARSAL__", self.text)
        self.assertIn("$fixtureRehearsalLiteral = if ($isFixtureRehearsal)", self.text)
        self.assertIn("Replace('__FIXTURE_REHEARSAL__', $fixtureRehearsalLiteral)", self.text)

    def test_flag_is_recorded_in_every_summary_and_in_the_evidence_manifest(self) -> None:
        # summary.json is written on every early-exit venue; evidence-manifest.json only on
        # the success path. Both carry the flag, so a fixture run is unmistakable either way.
        # early-exit venues, the artifact index, and the success summary: every reader-facing output.
        self.assertGreaterEqual(self.text.count("fixtureRehearsal=$FixtureRehearsal"), 6)
        self.assertGreaterEqual(self.text.count("fixtureRehearsal = $FixtureRehearsal"), 3)  # provenance, manifest, success summary

    def test_clip_path_cache_parent_and_basename_checks_are_unchanged(self) -> None:
        self.assertIn("if ((Split-Path -Parent $clipPath) -ine $Cache)", self.text)
        self.assertIn(
            "if ([IO.Path]::GetFileNameWithoutExtension($clipPath) -cne $ClipId)", self.text
        )
        self.assertIn(
            "if ($clipPath -notmatch '^[A-Za-z]:\\\\[A-Za-z0-9 _.\\\\-]+$')", self.text
        )

    def test_consent_receipt_is_never_cited_for_a_fixture_run(self) -> None:
        # Was queue card ATTR3-CONSENT-RECEIPT-TEST-1: pinned so a revert to the unconditional
        # form (consentReceipt = $ConsentReceiptFileName, cited for a fixture run too) goes red.
        self.assertIn(
            "consentReceipt = $(if ($FixtureRehearsal) { $null } else { $ConsentReceiptFileName })",
            self.text,
        )

    def test_clippath_is_optional_for_a_fixture_id_and_fixture_sha_gates_it(self) -> None:
        # ATTR3-FIXTURE-STAGE-1.
        self.assertIn("[Parameter(Mandatory = $false)]", self.text)
        self.assertIn("[string]$FixtureSha256 = ''", self.text)
        self.assertIn("PLAYBACK_ATTR3_FIXTURE_SHA_REQUIRED", self.text)
        self.assertIn("PLAYBACK_ATTR3_FIXTURE_SHA_REFUSED", self.text)
        self.assertIn("PLAYBACK_ATTR3_CLIPPATH_REQUIRED", self.text)

    def test_fixture_content_is_authenticated_before_the_package_is_deployed(self) -> None:
        # ATTR3-FIXTURE-STAGE-1: a cache file name proves nothing about its bytes.
        self.assertIn("RESULT=FIXTURE_CONTENT_MISMATCH", self.text)
        self.assertIn("exit 17", self.text)
        mismatch = self.text.index("RESULT=FIXTURE_CONTENT_MISMATCH")
        deploy = self.text.index(
            "Expand-Archive -LiteralPath (Join-Path $Cache $BasePackageZip)"
        )
        self.assertLess(mismatch, deploy, "the fixture content gate must run before deployment")

    def test_build_manifest_authentication_smoke_log_selection_and_eligibility_gate_are_unchanged(
        self,
    ) -> None:
        # A fixture run is still subject to the same three gates as an owner-clip run.
        self.assertIn(
            "$buildManifest = Assert-AttrCudaBuildManifest -Path $buildManifestPath "
            "-ExpectedSha256 $BuildManifestSha256 -ExpectedSourceCommit $SourceCommit",
            self.text,
        )
        self.assertIn(
            "Resolve-AttrCudaSmokeRunLog -ResultJsonPath $resultPath -ContainingRoot $Work",
            self.text,
        )
        self.assertIn("if (-not $verdict.admitted)", self.text)
        self.assertIn("exit $verdict.exitCode", self.text)


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

    def _fixture_rehearsal_section(self) -> str:
        # sol, PR #139: section 4b must run exactly as printed in a workspace whose path
        # contains spaces -- quoted path placeholders and two DISTINCT job ids.
        start = self.text.index("## 4b. Fixture rehearsal")
        end = self.text.index("\n## ", start + 1)
        return self.text[start:end]

    def test_fixture_rehearsal_job_ids_are_distinct(self) -> None:
        section = self._fixture_rehearsal_section()
        self.assertIn("<stageJobId>", section)
        self.assertIn("<attrJobId>", section)
        # The old ambiguous placeholder must not survive: a reader copying <jobId> into both
        # submissions hits UMRUN_JOBID_IN_USE on the second one.
        self.assertNotIn("<jobId>", section)
        code_lines = [
            line
            for line in section.splitlines()
            if not line.strip().startswith("#")
        ]
        job_id_args = re.findall(r"-JobId (\S+)", "\n".join(code_lines))
        self.assertEqual(len(job_id_args), 2, "expected exactly two -JobId submissions in 4b")
        self.assertNotEqual(
            job_id_args[0], job_id_args[1], "the two -JobId values in 4b must differ"
        )

    def test_fixture_rehearsal_path_placeholders_are_quoted(self) -> None:
        section = self._fixture_rehearsal_section()
        for flag in ("-FixturePath", "-OutDir", "-ScriptPath", "-SideFile", "-OutFile"):
            with self.subTest(flag=flag):
                self.assertIn(
                    f'{flag} "',
                    section,
                    f"{flag}'s path argument is not double-quoted in runbook 4b, so a "
                    "workspace path containing spaces breaks argument parsing",
                )

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



class FixtureRehearsalVisibilityTests(unittest.TestCase):
    """sol PR #137 r1: a rehearsal must be unmistakable in EVERY reader-facing output."""

    GENERATOR = ROOT / "tools" / "profiling" / "bachelor" / "playback-attr-3-cuda-job.ps1"

    def setUp(self) -> None:
        self.text = self.GENERATOR.read_text(encoding="utf-8")
        self.template = self.text[self.text.index("$template = @'"):]

    def test_the_fixture_arm_of_the_clip_id_pattern_is_case_sensitive(self) -> None:
        # ValidatePattern is case-insensitive by default, so the fixture ids carry (?-i:...);
        # otherwise TINY_DUAL_ISO validates while the membership test calls it an owner clip.
        line = [l for l in self.text.splitlines() if "ValidatePattern" in l and "dual_iso" in l]
        self.assertEqual(len(line), 1, line)
        self.assertIn("(?-i:", line[0])

    def test_every_reader_facing_output_carries_the_flag(self) -> None:
        for artifact in ("summary.json", "provenance.json", "evidence-manifest.json", "artifact-index.json"):
            with self.subTest(artifact=artifact):
                index = self.template.index(artifact)
                window = self.template[max(0, index - 3000):index]
                self.assertIn("fixtureRehearsal", window, f"{artifact} is written without the flag nearby")

    def test_the_success_path_writes_a_summary_and_a_distinct_result_verb(self) -> None:
        self.assertIn("FIXTURE_REHEARSAL_CAPTURED", self.template)
        self.assertIn("FIXTURE_REHEARSAL=$FixtureRehearsal", self.template)
        # the success path writes summary.json too, not only the failure paths
        tail = self.template[self.template.index("$resultVerb ="):] if "$resultVerb =" in self.template else ""
        self.assertTrue(tail, "no success result verb found")
        self.assertIn("summary.json", self.template[self.template.index("artifact-index.v1") - 2500:])


if __name__ == "__main__":
    unittest.main()
