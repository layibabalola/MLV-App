# AttrCudaArtifacts.psm1 -- the ONE definition of the PLAYBACK-ATTR-3-CUDA package naming
# convention, of the build-identity header injected into an archived source tree, and of every
# VERIFICATION the emitted jobs perform before they trust an input.
#
# Three surfaces have to agree on these names without exchanging state:
#   - tools/profiling/bachelor/playback-attr-3-cuda-assemble.ps1 (board host: builds them)
#   - tools/profiling/bachelor/playback-attr-3-cuda-stage-job.ps1 (stages them into the cache)
#   - tools/profiling/bachelor/playback-attr-3-cuda-job.ps1 (attribution: verifies them)
# The attribution job derives the same names independently from -SourceCommit, which is why
# this module exists: two copies of a convention drift, and the drift shows up as a cache miss
# on a host nobody is watching.
#
# WHY THE VERIFIERS LIVE HERE TOO (sol, PR #133 r2). Each generator emits a self-contained
# <jobId>.job.ps1 that runs on a host with no checkout and no module path, so a job cannot
# Import-Module this file. Instead the generators EMBED the function text VERBATIM, extracted by
# Get-AttrCudaEmbeddedFunctionSource at generation time. The test suite
# (tools/repo_hygiene/test_playback_attr_3_cuda_split_route.py) executes these same functions
# through pwsh, so the code the tests exercise is byte-identical to the code the jobs run --
# which a re-implementation inside a here-string template could never be.

Set-StrictMode -Version Latest

$script:AttrCudaModulePath = $PSCommandPath

function Get-AttrCudaEmbeddedFunctionSource {
    <#
    .SYNOPSIS
    Return the verbatim source text of named functions in this module, for embedding in an
    emitted job script.
    .DESCRIPTION
    Extraction is textual on purpose: Get-Command .ScriptBlock would return a REBUILT body
    (comments stripped, formatting normalised), so a test asserting "the job embeds what the
    module defines" would be comparing two different strings. Every function in this module
    opens with `function <Name> {` at column 0 and closes with `}` at column 0, and no function
    body contains a line starting with an unindented `}`; that is the contract the regex below
    relies on, and test_embedded_functions_round_trip pins it.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Name,

        [string]$ModulePath
    )

    if ([string]::IsNullOrWhiteSpace($ModulePath)) { $ModulePath = $script:AttrCudaModulePath }
    $text = [IO.File]::ReadAllText($ModulePath)
    $blocks = @()
    foreach ($functionName in $Name) {
        $pattern = '(?ms)^function\s+' + [regex]::Escape($functionName) + '\s*\{.*?^\}'
        $match = [regex]::Match($text, $pattern)
        if (-not $match.Success) {
            throw "AttrCudaArtifacts.psm1 defines no extractable function named '$functionName'"
        }
        $blocks += $match.Value
    }
    ($blocks -join "`r`n`r`n")
}

function Get-AttrCudaZipArchiveComment {
    <#
    .SYNOPSIS
    Read the End-Of-Central-Directory comment of a zip file.
    .DESCRIPTION
    `git archive --format=zip <commit>` stores the 40-hex commit id in this field (git's own
    documented behaviour for a commit-ish, as opposed to a tree). Reading it is how a host with
    no git and no checkout can prove WHICH COMMIT a source zip carries -- a sha256 alone only
    proves the bytes are the ones the generator saw, not what is inside them.
    The EOCD record is the last >=22 bytes of the file: signature 0x06054b50, then 18 bytes of
    fixed fields, then a uint16 comment length at offset 20 and the comment itself at offset 22.
    It is located by scanning backwards, because the comment is variable-length.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 22) { return '' }
    $start = $bytes.Length - 22
    for ($index = $start; $index -ge 0; $index--) {
        if ($bytes[$index] -ne 0x50) { continue }
        if ($bytes[$index + 1] -ne 0x4B -or $bytes[$index + 2] -ne 0x05 -or $bytes[$index + 3] -ne 0x06) { continue }
        $commentLength = [BitConverter]::ToUInt16($bytes, $index + 20)
        if ($commentLength -eq 0) { return '' }
        if (($index + 22 + $commentLength) -gt $bytes.Length) { return '' }
        return [Text.Encoding]::ASCII.GetString($bytes, $index + 22, $commentLength)
    }
    ''
}

function Assert-AttrCudaSourceArchive {
    <#
    .SYNOPSIS
    Refuse a source zip that is not, byte for byte, the archive the generator produced from the
    expected commit.
    .DESCRIPTION
    Two independent bindings, because they fail differently:
      - sha256: these are the exact bytes the generator hashed (catches a stale or swapped drop);
      - zip comment: the archive was produced by `git archive` FROM THIS COMMIT (catches an
        archive of some other tree that someone re-hashed into a re-generated job).
    Throws with a distinguishable ATTRCUDA_ARCHIVE_* token; returns the verified sha256.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ArchivePath,

        [Parameter(Mandatory = $true)]
        [string]$ExpectedSha256,

        [string]$ExpectedCommit = ''
    )

    if ($ExpectedSha256 -notmatch '^[0-9a-f]{64}$') {
        throw "ATTRCUDA_ARCHIVE_SHA_MALFORMED expected sha256 is not 64 lowercase hex: '$ExpectedSha256'"
    }
    if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf)) {
        throw "ATTRCUDA_ARCHIVE_MISSING $ArchivePath"
    }
    $actual = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $ExpectedSha256) {
        throw "ATTRCUDA_ARCHIVE_SHA_MISMATCH $ArchivePath expected=$ExpectedSha256 actual=$actual"
    }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedCommit)) {
        $comment = Get-AttrCudaZipArchiveComment -Path $ArchivePath
        if ($comment -ne $ExpectedCommit) {
            throw "ATTRCUDA_ARCHIVE_COMMIT_MISMATCH $ArchivePath declares commit '$comment', expected '$ExpectedCommit'"
        }
    }
    $actual
}

function Get-AttrCudaArtifactNames {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[0-9a-f]{40}$')]
        [string]$SourceCommit
    )

    $shortSha = $SourceCommit.Substring(0, 12)
    [pscustomobject]@{
        sourceCommit = $SourceCommit
        shortSha = $shortSha
        exeName = "MLVApp-playback-attr-3-cuda-$shortSha.exe"
        reconName = "igpu_recon_cuda-playback-attr-3-cuda-$shortSha.dll"
        packageZipName = "MLVApp-playback-attr-3-cuda-$shortSha-pkg.zip"
        buildManifestName = "playback-attr-3-cuda-$shortSha-build.json"
        # The unrenamed build name kept inside the package zip.
        packageExeName = 'MLVApp.exe'
    }
}

function New-AttrCudaBuildInfoHeader {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[0-9a-f]{40}$')]
        [string]$SourceCommit,

        [string]$BuildTimeUtc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    )

    # The source comes from `git archive`, so there is no .git beside it and MLVApp.pro's
    # qmake-time git probe stamps "unknown". This writes the SAME macro set
    # tools/gen-buildinfo.ps1 emits, directly into OUT_PWD before qmake runs, pinned to the
    # commit that was archived (dirty=0, which is what an archive means).
    @"
/* AUTO-GENERATED by tools/gen-buildinfo.ps1 at build time. Do not edit; do not commit. */
#ifndef MLVAPP_BUILD_BUILDINFO_H
#define MLVAPP_BUILD_BUILDINFO_H
/* A qmake-time -D may be stale or "unknown" when qmake cannot execute git.
   This build-time header is generated immediately before compilation and is
   authoritative, so replace any earlier definition rather than preserving it. */
#ifdef MLVAPP_GIT_SHA
#undef MLVAPP_GIT_SHA
#endif
#define MLVAPP_GIT_SHA "$SourceCommit"
#define MLVAPP_BUILD_SHA "$SourceCommit"
#define MLVAPP_GIT_DIRTY 0
#define MLVAPP_GIT_DESCRIBE "$SourceCommit"
#define MLVAPP_BUILD_TIME_UTC "$BuildTimeUtc"
#define MLVAPP_BUILD_STAMP "MLVAPP_BUILDSTAMP_v1|sha=$SourceCommit|dirty=0"
#endif
"@
}

function Resolve-AttrCudaSmokeRunLog {
    <#
    .SYNOPSIS
    Return the authoritative log of the smoke run that produced $ResultJsonPath, or throw.
    .DESCRIPTION
    tools/profiling/run-release-gui-smoke.ps1 does NOT write into <output dir>\logs. It creates a
    GUID-nonced directory per run and then snapshots the exact lines its own result consumed:

        $runNonce = [Guid]::NewGuid().ToString("N")
        $outputStem = [IO.Path]::GetFileNameWithoutExtension($outputPath)
        $logRoot = Join-Path $outputDir ("logs-{0}-{1}" -f $outputStem, $runNonce)
        ...
        # Preserve the exact lines consumed by this result in a per-run immutable
        # snapshot.  The aggregate rotating app log is allowed to grow later and is
        # therefore diagnostic only; comparison authority comes from this snapshot.
        $runLogSnapshotPath = "$outputPath.run.log"
        [System.IO.File]::WriteAllLines($runLogSnapshotPath, [string[]]$recentLines, $utf8NoBom)
        $runLogSnapshotBinding = Get-EvidenceFileBinding `
            -Path $runLogSnapshotPath -Label "run log snapshot"

    and exposes both through result.json:

        log = [pscustomobject]@{
            path = $runLogSnapshotPath
            aggregateSourcePath = if ($logFile) { $logFile.FullName } else { $null }
        ...
        evidence = [pscustomobject]@{
            runNonce = $runNonce
            inputs = $captureInputBindings
            runLogSnapshot = $runLogSnapshotBinding

    So `logs\mlvapp-*.log` under the job's own output directory NEVER EXISTS, and a glob for it
    cannot reach any gate behind it (sol, PR #133 r2). This resolves `log.path`, the snapshot the
    runner itself calls the comparison authority, and binds it to
    `evidence.runLogSnapshot.sha256` so a log swapped after the run is refused rather than read.
    `log.aggregateSourcePath` is returned for the record but never read: the runner's own comment
    says the aggregate rotating log may grow after the run and is diagnostic only.
    Throws with a distinguishable ATTRCUDA_SMOKE_* token.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResultJsonPath,

        # When set, the resolved log must live under this directory: the smoke runner is handed
        # an -Output inside the job's work tree, so a log.path pointing anywhere else means the
        # result.json being read is not this run's.
        [string]$ContainingRoot = ''
    )

    if (-not (Test-Path -LiteralPath $ResultJsonPath -PathType Leaf)) {
        throw "ATTRCUDA_SMOKE_RESULT_MISSING $ResultJsonPath"
    }
    $result = [IO.File]::ReadAllText($ResultJsonPath) | ConvertFrom-Json
    $logNode = $null
    if ($null -ne $result) { $logNode = $result.PSObject.Properties['log'] }
    if ($null -eq $logNode -or $null -eq $logNode.Value) {
        throw "ATTRCUDA_SMOKE_LOG_NODE_MISSING $ResultJsonPath has no log node"
    }
    $pathProperty = $logNode.Value.PSObject.Properties['path']
    $logPath = if ($null -eq $pathProperty) { '' } else { [string]$pathProperty.Value }
    if ([string]::IsNullOrWhiteSpace($logPath)) {
        throw "ATTRCUDA_SMOKE_LOG_PATH_ABSENT $ResultJsonPath declares no log.path"
    }
    if (-not (Test-Path -LiteralPath $logPath -PathType Leaf)) {
        throw "ATTRCUDA_SMOKE_LOG_MISSING $logPath (named by log.path in $ResultJsonPath)"
    }
    $fullLogPath = [IO.Path]::GetFullPath($logPath)
    if (-not [string]::IsNullOrWhiteSpace($ContainingRoot)) {
        $root = [IO.Path]::GetFullPath($ContainingRoot)
        if (-not $fullLogPath.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "ATTRCUDA_SMOKE_LOG_OUTSIDE_ROOT $fullLogPath is not under $root"
        }
    }
    $actualSha = (Get-FileHash -LiteralPath $fullLogPath -Algorithm SHA256).Hash.ToLowerInvariant()

    $declaredSha = ''
    $runNonce = ''
    $evidenceNode = $result.PSObject.Properties['evidence']
    if ($null -ne $evidenceNode -and $null -ne $evidenceNode.Value) {
        $nonceProperty = $evidenceNode.Value.PSObject.Properties['runNonce']
        if ($null -ne $nonceProperty) { $runNonce = [string]$nonceProperty.Value }
        $snapshotProperty = $evidenceNode.Value.PSObject.Properties['runLogSnapshot']
        if ($null -ne $snapshotProperty -and $null -ne $snapshotProperty.Value) {
            $shaProperty = $snapshotProperty.Value.PSObject.Properties['sha256']
            if ($null -ne $shaProperty) { $declaredSha = ([string]$shaProperty.Value).ToLowerInvariant() }
        }
    }
    if ([string]::IsNullOrWhiteSpace($declaredSha)) {
        throw "ATTRCUDA_SMOKE_LOG_UNBOUND $ResultJsonPath carries no evidence.runLogSnapshot.sha256 to bind $fullLogPath to"
    }
    if ($declaredSha -ne $actualSha) {
        throw "ATTRCUDA_SMOKE_LOG_SHA_MISMATCH $fullLogPath declared=$declaredSha actual=$actualSha"
    }

    $aggregateProperty = $logNode.Value.PSObject.Properties['aggregateSourcePath']
    [pscustomobject]@{
        path = $fullLogPath
        sha256 = $actualSha
        bytes = (Get-Item -LiteralPath $fullLogPath).Length
        runNonce = $runNonce
        source = 'result.json log.path (run log snapshot)'
        aggregateSourcePath = if ($null -eq $aggregateProperty) { $null } else { $aggregateProperty.Value }
    }
}

function Get-AttrCudaLastEligibilityLine {
    <#
    .SYNOPSIS
    Parse the LAST gpu_playback_recon.eligibility line out of a log, as a key/value hashtable.
    .DESCRIPTION
    The line (platform/qt/MainWindow.cpp, emitted under
    MLVAPP_GPU_PLAYBACK_RECON_ELIGIBILITY_DIAG=1) carries both bare (key=1) and quoted
    (key="some text") values, so the value alternation has to handle quotes -- r16_reason is
    quoted and routinely contains spaces. The LAST line wins: the probe re-runs per render
    policy evaluation and the final one describes the state the run ended in.
    Returns $null when the log carries no such line.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$LogText
    )

    $last = $null
    foreach ($line in ($LogText -split "`r?`n")) {
        if ($line -notmatch 'gpu_playback_recon\.eligibility ') { continue }
        $values = @{}
        foreach ($match in [regex]::Matches($line, '(?<key>[A-Za-z0-9_]+)=(?:"(?<quoted>[^"]*)"|(?<bare>[^\s]+))')) {
            $key = $match.Groups['key'].Value
            if ($match.Groups['quoted'].Success) { $values[$key] = $match.Groups['quoted'].Value }
            else { $values[$key] = $match.Groups['bare'].Value }
        }
        $last = $values
    }
    $last
}

function Get-AttrCudaEligibilityVerdict {
    <#
    .SYNOPSIS
    Decide whether a run may be attributed to the CUDA path at all, and with which exit code.
    .DESCRIPTION
    A run where the CUDA backend never loaded, or where the R16 texture path was not admitted,
    cannot produce a CUDA attribution -- the frame counters alone would happily describe some
    other path. A log with NO eligibility line at all is treated exactly like one that says no:
    absence of the diagnostic is not evidence of eligibility. exitCode is 15
    (BACKEND_NOT_AVAILABLE) whenever the run is not admitted, and every field is carried either
    way so a refusal says WHY.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$LogText
    )

    $values = Get-AttrCudaLastEligibilityLine -LogText $LogText
    $read = {
        param([string]$Key)
        if ($null -eq $values -or -not $values.ContainsKey($Key)) { return $null }
        [string]$values[$Key]
    }
    $cudaBackendAvailable = & $read 'cuda_backend_available'
    $r16Available = & $read 'r16_available'
    $admitted = ($cudaBackendAvailable -eq '1' -and $r16Available -eq '1')
    [pscustomobject]@{
        source = 'gpu_playback_recon.eligibility'
        linePresent = [bool]($null -ne $values)
        cudaBackendAvailable = $cudaBackendAvailable
        r16Available = $r16Available
        r16Reason = & $read 'r16_reason'
        cudaBackendAttempted = & $read 'cuda_backend_attempted'
        cudaBackendResolved = & $read 'cuda_backend_resolved'
        r16ProbeRan = & $read 'r16_probe_ran'
        admitted = $admitted
        exitCode = $(if ($admitted) { 0 } else { 15 })
    }
}

Export-ModuleMember -Function `
    Get-AttrCudaArtifactNames, `
    New-AttrCudaBuildInfoHeader, `
    Get-AttrCudaEmbeddedFunctionSource, `
    Get-AttrCudaZipArchiveComment, `
    Assert-AttrCudaSourceArchive, `
    Resolve-AttrCudaSmokeRunLog, `
    Get-AttrCudaLastEligibilityLine, `
    Get-AttrCudaEligibilityVerdict
