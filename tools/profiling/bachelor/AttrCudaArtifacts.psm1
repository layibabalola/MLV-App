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

Export-ModuleMember -Function `
    Get-AttrCudaArtifactNames, `
    New-AttrCudaBuildInfoHeader, `
    Get-AttrCudaEmbeddedFunctionSource, `
    Get-AttrCudaZipArchiveComment, `
    Assert-AttrCudaSourceArchive
