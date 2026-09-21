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

function Assert-AttrCudaSafeArtifactName {
    <#
    .SYNOPSIS
    Refuse anything that is not a plain file name, and return it.
    .DESCRIPTION
    The staging job used to take artifact names straight out of build.json and feed them to
    Join-Path for cache writes and inbox deletion, while its containment check only covered the
    DIRECTORY roots (sol, PR #133 r2). A name of `..\..\something` therefore produced a path that
    passed the root check and still landed outside it -- on an unattended host, for a
    Remove-Item. A basename cannot do that, so this refuses everything that is not one:
    separators in either direction, any `..` at all, a colon (and so any drive qualifier or
    alternate data stream), the characters Windows forbids in a file name, and surrounding
    whitespace. Assert-AttrCudaDirectChild is the second, independent line.
    Throws with a distinguishable ATTRCUDA_NAME_* token.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Name
    )

    if ([string]::IsNullOrWhiteSpace($Name)) { throw 'ATTRCUDA_NAME_EMPTY an artifact was named with an empty string' }
    if ($Name -ne $Name.Trim()) { throw "ATTRCUDA_NAME_PADDED '$Name' has leading or trailing whitespace" }
    if ($Name.Contains('..')) { throw "ATTRCUDA_NAME_TRAVERSAL '$Name' contains '..'" }
    foreach ($character in @('\', '/', ':')) {
        if ($Name.Contains($character)) { throw "ATTRCUDA_NAME_NOT_A_BASENAME '$Name' contains '$character'" }
    }
    if ($Name -match '[<>"|?*]') { throw "ATTRCUDA_NAME_ILLEGAL_CHARACTER '$Name'" }
    if ($Name -ne [IO.Path]::GetFileName($Name)) { throw "ATTRCUDA_NAME_NOT_A_BASENAME '$Name'" }
    $Name
}

function Assert-AttrCudaDirectChild {
    <#
    .SYNOPSIS
    Require a resolved path to be a DIRECT child of $Root, and return its full path.
    .DESCRIPTION
    Stronger than "starts with the root", and deliberately so: the staging job writes into and
    deletes out of two flat directories, so anything at a deeper level is already wrong even when
    it is technically inside. Resolution is through [IO.Path]::GetFullPath, which collapses `..`
    and relative segments, so the comparison is made on what the filesystem would actually touch
    rather than on the string that was passed in. Called before every Copy-Item, Move-Item and
    Remove-Item, never after.
    Throws ATTRCUDA_PATH_NOT_DIRECT_CHILD.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root,

        [Parameter(Mandatory = $true)]
        [string]$Path,

        [string]$Label = 'path'
    )

    $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $fullPath = [IO.Path]::GetFullPath($Path)
    $parent = [IO.Path]::GetDirectoryName($fullPath)
    if ([string]::IsNullOrEmpty($parent)) {
        throw "ATTRCUDA_PATH_NOT_DIRECT_CHILD $Label '$fullPath' has no parent directory"
    }
    if ($parent.TrimEnd('\') -ne $fullRoot) {
        throw "ATTRCUDA_PATH_NOT_DIRECT_CHILD $Label '$fullPath' is not a direct child of '$fullRoot'"
    }
    $fullPath
}

function Assert-AttrCudaBuildManifest {
    <#
    .SYNOPSIS
    Authenticate a staged build.json before a single one of its fields is trusted, and return it.
    .DESCRIPTION
    The hash chain used to stop at staging (sol, PR #133 r2): the attribution job took whichever
    same-named build.json happened to be in the MUTABLE agent cache and believed its
    pendingSymbolPresence, its artifact sha256s and its sourceCommit. Replacing build.json plus
    the matching artifacts forged all three at once.
    So: the file's own sha256 is checked against a value baked in by the generator BEFORE
    ConvertFrom-Json runs -- parse order matters, because a parsed field is already a trusted
    field. Then three claims the downstream evidence rests on:
      - sourceCommit must equal the commit the job was generated for;
      - dllPairManifestSha256 must be present and well formed, so the exe's DLL pair is itself
        chained back to the Ultra-Magnus manifest rather than merely asserted;
      - pendingSymbolPresence must be a real boolean, because gpu_job_result_provenance.py
        rejects null/omitted and this host cannot re-derive it.
    Throws with a distinguishable ATTRCUDA_BUILD_MANIFEST_* token; returns the parsed manifest.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$ExpectedSha256,

        [Parameter(Mandatory = $true)]
        [string]$ExpectedSourceCommit
    )

    if ($ExpectedSha256 -notmatch '^[0-9a-f]{64}$') {
        throw "ATTRCUDA_BUILD_MANIFEST_SHA_MALFORMED expected sha256 is not 64 lowercase hex: '$ExpectedSha256'"
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "ATTRCUDA_BUILD_MANIFEST_MISSING $Path"
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $ExpectedSha256) {
        throw "ATTRCUDA_BUILD_MANIFEST_SHA_MISMATCH $Path expected=$ExpectedSha256 actual=$actual"
    }

    $manifest = [IO.File]::ReadAllText($Path) | ConvertFrom-Json
    if ($null -eq $manifest) { throw "ATTRCUDA_BUILD_MANIFEST_UNPARSEABLE $Path" }
    $commitProperty = $manifest.PSObject.Properties['sourceCommit']
    $commit = if ($null -eq $commitProperty) { '' } else { [string]$commitProperty.Value }
    if ($commit -ne $ExpectedSourceCommit) {
        throw "ATTRCUDA_BUILD_MANIFEST_COMMIT_MISMATCH $Path declares '$commit', expected '$ExpectedSourceCommit'"
    }
    $dllPairProperty = $manifest.PSObject.Properties['dllPairManifestSha256']
    $dllPairSha = if ($null -eq $dllPairProperty) { '' } else { ([string]$dllPairProperty.Value).ToLowerInvariant() }
    if ($dllPairSha -notmatch '^[0-9a-f]{64}$') {
        throw "ATTRCUDA_BUILD_MANIFEST_DLLPAIR_UNBOUND $Path carries no well-formed dllPairManifestSha256 (got '$dllPairSha')"
    }
    $symbolProperty = $manifest.PSObject.Properties['pendingSymbolPresence']
    if ($null -eq $symbolProperty -or $symbolProperty.Value -isnot [bool]) {
        $observed = if ($null -eq $symbolProperty) { '<absent>' } else { [string]$symbolProperty.Value }
        throw "ATTRCUDA_BUILD_MANIFEST_SYMBOL_PRESENCE_INVALID $Path pendingSymbolPresence is not a boolean (got '$observed')"
    }
    $manifest
}

function Assert-AttrCudaFixtureCommittedBytes {
    <#
    .SYNOPSIS
    Refuse a working-tree fixture whose bytes are not exactly what HEAD has for it.
    .DESCRIPTION
    ATTR3-FIXTURE-STAGE-1. A fixture is admissible because it is a TRACKED repository fixture
    (Test-UmRunTrackedFixtureSource, tools/profiling/UmRunDrop.psm1) -- but "tracked" says
    nothing about whether the WORKING-TREE bytes at that path are still the committed ones. A
    dirty or corrupted working copy would otherwise bake a sha256 into the staging job that
    no reviewed commit ever produced. This compares `git hash-object` of the file on disk
    to `git rev-parse HEAD:<repo-relative path>`, run in whichever repository actually contains
    the file (found from the file's own directory via `git rev-parse --show-toplevel`, never
    assumed to be this module's own checkout), so a throwaway test repository is verified the
    same way the real one is.
    Throws with a distinguishable ATTR3_FIXTURE_* token; returns the verified (matching) hash.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "ATTR3_FIXTURE_MISSING $Path"
    }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "ATTR3_FIXTURE_GIT_UNAVAILABLE git is required to verify $Path against its committed blob"
    }
    $full = [IO.Path]::GetFullPath($Path)
    $dir = [IO.Path]::GetDirectoryName($full)
    $repoRoot = (& git -C $dir rev-parse --show-toplevel 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($repoRoot)) {
        throw "ATTR3_FIXTURE_NOT_IN_A_REPO $full is not inside a git working tree"
    }
    $repoRoot = ($repoRoot.Trim()) -replace '/', '\'
    if (-not $full.StartsWith($repoRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "ATTR3_FIXTURE_NOT_IN_A_REPO $full does not resolve under its own repository root $repoRoot"
    }
    $relative = ($full.Substring($repoRoot.Length + 1)) -replace '\\', '/'
    $workingHash = (& git -C $repoRoot hash-object -- $relative 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($workingHash)) {
        throw "ATTR3_FIXTURE_GIT_UNAVAILABLE could not hash-object $relative in $repoRoot"
    }
    $workingHash = $workingHash.Trim().ToLowerInvariant()
    $committedHash = (& git -C $repoRoot rev-parse "HEAD:$relative" 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($committedHash)) {
        throw "ATTR3_FIXTURE_NOT_COMMITTED HEAD:$relative could not be resolved in $repoRoot"
    }
    $committedHash = $committedHash.Trim().ToLowerInvariant()
    if ($workingHash -ne $committedHash) {
        throw "ATTR3_FIXTURE_WORKING_TREE_DIRTY $relative working-tree blob $workingHash differs from the committed blob $committedHash"
    }
    $workingHash
}

function Resolve-AttrCudaCommittedBlobId {
    <#
    .SYNOPSIS
    Resolve the git blob id of a repo-relative path AS COMMITTED at a given commit.
    .DESCRIPTION
    Generator-only: never embedded into an emitted job, because the measurement host has no
    checkout and no git. This lets two independent generators -- one that pins an expected
    hash into the attribution job, one that stages the matching bytes into the cache -- each
    derive the SAME blob id from the SAME -SourceCommit without either taking the other's word
    for it (ATTR3-SMOKE-RUNNER-PIN-1).
    Throws ATTRCUDA_BLOB_UNRESOLVED.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,

        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[0-9a-f]{40}$')]
        [string]$Commit,

        [Parameter(Mandatory = $true)]
        [string]$RepoRelativePath
    )

    $blobId = (& git -C $RepoRoot rev-parse "${Commit}:${RepoRelativePath}" 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($blobId)) {
        throw "ATTRCUDA_BLOB_UNRESOLVED could not resolve ${Commit}:${RepoRelativePath} in $RepoRoot"
    }
    $blobId = $blobId.Trim()
    if ($blobId -notmatch '^[0-9a-f]{40}$') {
        throw "ATTRCUDA_BLOB_UNRESOLVED ${Commit}:${RepoRelativePath} did not resolve to a blob id (got '$blobId')"
    }
    $blobId
}

function Save-AttrCudaCommittedBlobBytes {
    <#
    .SYNOPSIS
    Write a git blob's exact committed bytes to -Destination and return their sha256.
    .DESCRIPTION
    Generator-only, same reason as Resolve-AttrCudaCommittedBlobId. `git cat-file blob <id>` is
    streamed to -Destination through Start-Process -RedirectStandardOutput, which redirects the
    child process's raw stdout HANDLE straight to the file -- never through a PowerShell text
    pipeline, which decodes/re-encodes command output and would silently rewrite CRLF sequences
    on the way through. Byte-exactness is the entire point: hashing anything else (a working-tree
    checkout, a captured `git show` string) would pin bytes that a core.autocrlf setting or a
    dirty tree could quietly disagree with.
    ATTR3-SMOKE-RUNNER-PIN-1 (BLOCKER, round 2): -RepoRoot used to be passed as a `-C $RepoRoot`
    pair inside -ArgumentList, which Start-Process joins into a single command line WITHOUT
    quoting each element. The real repository root contains a space
    (`C:\!Layi Wkspc\MLV-App`), so that command line handed git the bare token `Wkspc\MLV-App` as
    if it were the next argument, and every real-repository call threw ATTRCUDA_BLOB_READ_FAILED.
    -WorkingDirectory sets the child process's start directory directly (never tokenized onto the
    command line), so no argument here can ever contain a space to mis-split.
    Throws ATTRCUDA_BLOB_READ_FAILED.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,

        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[0-9a-f]{40}$')]
        [string]$BlobId,

        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    $proc = Start-Process -FilePath 'git' -WorkingDirectory $RepoRoot -ArgumentList @('cat-file', 'blob', $BlobId) `
        -RedirectStandardOutput $Destination -NoNewWindow -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "ATTRCUDA_BLOB_READ_FAILED git cat-file blob $BlobId exited $($proc.ExitCode)"
    }
    (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
}

# ATTR3-SMOKE-RUNNER-DEPS-1 round 3 (NARROW BY REDESIGN), round 4 (contract stated honestly,
# PR #144). Round 1 and round 2 both discovered this closure by SCANNING committed text -- first
# a regex over one literal shape (Join-Path $PSScriptRoot '<name>'), then a fail-closed AST
# literal scan layered on top of it. A design swarm ruled both undiscoverable-by-patching: a
# scanner built on string literals cannot see an extension-less load
# (`& "$PSScriptRoot\helper"`) or a bareword `Import-Module $PSScriptRoot/modx` -- both load
# code; both scanners returned nothing for either -- and the literal-scan classifier matched a
# resolved dependency by BASENAME alone, so an absolute path like 'C:\evil\provenance-stamp.ps1'
# classified cleanly just by sharing a name with a staged file. Removing the heuristic from the
# trust path removes both gaps permanently: the closure is now this EXPLICIT, pinned list --
# never discovered, never inferred.
#
# WHAT Assert-AttrCudaClosureComplete (below) ACTUALLY PROVES, STATED HONESTLY. It is a
# REGRESSION TRIPWIRE over these four reviewed, byte-pinned files at generator time -- never an
# exhaustive proof that no future edit to them can smuggle in an unstaged load. PowerShell
# resolves some commands dynamically (a computed string, a resolved alias), and no static census
# can enumerate every spelling of "load a file" a determined future edit could use. What it DOES
# guarantee: every load site Get-AttrCudaScriptLoadSites' AST census can see in the manifest's
# own committed text -- including a module-qualified or aliased loader name and any abbreviated
# Add-Type -Path/-LiteralPath parameter (sol round-4 majors 1 and 3) -- classifies as a manifest
# sibling, a scriptblock-only invocation, a pathless Add-Type, or a reviewed exact-pair exclusion,
# or generation refuses outright with ATTRCUDA_UNCLASSIFIED_SCRIPT_REFERENCE. An alias
# definition (Set-Alias/sal/New-Alias/nal) is never resolved at census time -- it is ALWAYS
# unclassified, because what it names is undecidable statically (round-4 sol major 2).
#
# THE SAFETY PROPERTY FOR WHATEVER THIS CENSUS CANNOT SEE IS THE RUNTIME PATH, NOT THIS
# FUNCTION: a staged file that fails to load in the smoke child is reported as SMOKE_RUN_FAILED
# (playback-attr-3-cuda-job.ps1's failure branch, ~:734-780), never silently masked as a
# PresentMon timeout or a clean result. Today's four real files contain none of the forms this
# census cannot see (hub-verified against the real repo by
# test_the_real_current_repo_at_head_classifies_cleanly); this census exists to catch a
# regression the moment one of these four files is edited to add one, not to prove no such form
# could ever exist anywhere PowerShell can run.
$script:AttrCudaSmokeRunnerClosureManifest = @(
    'tools/profiling/run-release-gui-smoke.ps1',
    'tools/profiling/gui-smoke-screenshot-provenance.ps1',
    'tools/profiling/provenance-stamp.ps1',
    'tools/profiling/gui-smoke-process-boundary.psm1'
)

function Get-AttrCudaSmokeRunnerClosureManifest {
    <#
    .SYNOPSIS
    The pinned, explicit smoke-runner closure, as repo-relative paths, root script first.
    #>
    [CmdletBinding()]
    param()
    return @($script:AttrCudaSmokeRunnerClosureManifest)
}

function Get-AttrCudaScriptLoadSites {
    <#
    .SYNOPSIS
    AST census of every code-loading SITE in PowerShell source text. FAILS CLOSED on a parse
    error. Never a text/regex scan.
    .DESCRIPTION
    ATTR3-SMOKE-RUNNER-DEPS-1 round 3. Parses with
    [System.Management.Automation.Language.Parser] and returns every:
      - CommandAst invoked with the dot (.) or call (&) operator (its target may be a literal, a
        variable, or a dynamic expression -- classification is the caller's job);
      - CommandAst named (case-insensitively) Import-Module/ipmo, Start-Process/saps/start,
        pwsh/powershell(.exe), Invoke-Expression/iex, Invoke-Command/icm, Start-Job,
        Start-ThreadJob, Add-Type, Set-Alias/sal or New-Alias/nal -- matched on the segment AFTER
        the last `\`, so a module-qualified invocation
        (`Microsoft.PowerShell.Core\Import-Module ...`) is recognized exactly like the
        unqualified form, never invisible to this census (round 4, sol PR #144 major 1);
      - UsingStatementAst (a `using module|namespace|assembly` statement -- never a C# `using`
        keyword sitting inert inside a string literal handed to Add-Type, which this AST walk
        does not descend into because it is a StringConstantExpressionAst, not PowerShell code);
      - InvokeMemberExpressionAst whose member name is (case-insensitively) Create, AddScript,
        AddCommand, InvokeScript or NewScriptBlock;
      - a STATIC InvokeMemberExpressionAst named (case-insensitively) Start on the type
        expression [System.Diagnostics.Process] / [Diagnostics.Process] -- process launch, per
        fable's round-3 minor (round 4, PR #144).
    This finds every SITE that can execute or generate code regardless of how its target is
    spelled -- an extension-less `& "$PSScriptRoot\helper"` or a bareword
    `Import-Module $PSScriptRoot/modx` are both real AST nodes the parser sees even though
    neither contains a string literal a text scanner could match on. Classifying what each site
    loads is Assert-AttrCudaClosureComplete's job, never this function's.
    Throws ATTRCUDA_SCRIPT_PARSE_ERROR on any parse error: a file this scan cannot understand is
    never silently treated as clean.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$ScriptText,

        [string]$SourceLabel = '<script>'
    )

    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseInput($ScriptText, [ref]$tokens, [ref]$parseErrors)
    if ($null -ne $parseErrors -and $parseErrors.Count -gt 0) {
        $messages = ($parseErrors | ForEach-Object { $_.Message }) -join '; '
        throw "ATTRCUDA_SCRIPT_PARSE_ERROR ${SourceLabel}: $messages"
    }

    $loaderCommandNames = @(
        'Import-Module', 'ipmo',
        'Start-Process', 'saps', 'start',
        'pwsh', 'pwsh.exe', 'powershell', 'powershell.exe',
        'Invoke-Expression', 'iex',
        'Invoke-Command', 'icm',
        'Start-Job', 'Start-ThreadJob',
        'Add-Type',
        'Set-Alias', 'sal', 'New-Alias', 'nal'
    )
    $memberNames = @('Create', 'AddScript', 'AddCommand', 'InvokeScript', 'NewScriptBlock')
    $sites = [System.Collections.Generic.List[object]]::new()

    $commandAsts = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true)
    foreach ($command in $commandAsts) {
        $rawCommandName = $command.GetCommandName()
        # sol PR #144 round 4 major 1: GetCommandName() returns the QUALIFIED string for a
        # module-qualified invocation (`Microsoft.PowerShell.Core\Import-Module ...`), which
        # never matched $loaderCommandNames by exact string. Matching on the segment after the
        # last `\` recognizes the qualified and unqualified spellings identically.
        $commandName = if ($null -ne $rawCommandName) {
            $lastSeparator = $rawCommandName.LastIndexOf('\')
            if ($lastSeparator -ge 0) { $rawCommandName.Substring($lastSeparator + 1) } else { $rawCommandName }
        } else { $null }
        $isOperatorInvocation = ($command.InvocationOperator -eq [System.Management.Automation.Language.TokenKind]::Dot) -or
            ($command.InvocationOperator -eq [System.Management.Automation.Language.TokenKind]::Ampersand)
        $isNamedLoader = ($null -ne $commandName) -and (@($loaderCommandNames) -icontains $commandName)
        if ($isOperatorInvocation -or $isNamedLoader) {
            [void]$sites.Add([pscustomobject]@{
                kind = if ($isOperatorInvocation) { 'InvocationOperator' } else { 'CommandName' }
                operator = [string]$command.InvocationOperator
                commandName = $commandName
                memberName = $null
                text = $command.Extent.Text
                line = $command.Extent.StartLineNumber
                ast = $command
            })
        }
    }

    $usingAsts = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.UsingStatementAst] }, $true)
    foreach ($using in $usingAsts) {
        [void]$sites.Add([pscustomobject]@{
            kind = 'UsingStatement'
            operator = $null
            commandName = $null
            memberName = $null
            text = $using.Extent.Text
            line = $using.Extent.StartLineNumber
            ast = $using
        })
    }

    $memberAsts = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.InvokeMemberExpressionAst] }, $true)
    foreach ($member in $memberAsts) {
        $memberNameValue = $null
        if ($member.Member -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
            $memberNameValue = $member.Member.Value
        }
        $isTrackedMemberCall = ($null -ne $memberNameValue) -and (@($memberNames) -icontains $memberNameValue)
        # fable minor (round 3), fixed round 4: narrowly scoped to the one static member this
        # closure's real files use to launch anything -- [System.Diagnostics.Process]::Start --
        # rather than every member named Start, which would flag unrelated instance calls
        # (e.g. a Stopwatch or a Job) that never load code.
        $isProcessStartCall = $false
        if ($member.Static -and $null -ne $memberNameValue -and $memberNameValue -ieq 'Start' -and
            $member.Expression -is [System.Management.Automation.Language.TypeExpressionAst]) {
            $typeFullName = $member.Expression.TypeName.FullName
            if ($typeFullName -ieq 'System.Diagnostics.Process' -or $typeFullName -ieq 'Diagnostics.Process') {
                $isProcessStartCall = $true
            }
        }
        if ($isTrackedMemberCall -or $isProcessStartCall) {
            [void]$sites.Add([pscustomobject]@{
                kind = if ($isProcessStartCall) { 'ProcessStart' } else { 'MemberCall' }
                operator = $null
                commandName = $null
                memberName = $memberNameValue
                text = $member.Extent.Text
                line = $member.Extent.StartLineNumber
                ast = $member
            })
        }
    }

    return @($sites)
}

function Get-AttrCudaPSScriptRootJoinPathLiteral {
    <#
    .SYNOPSIS
    Private classifier for class (a): if $CommandElement is (a parenthesized)
    `Join-Path $PSScriptRoot '<bare file name>'`, return the bare file name; otherwise $null.
    .DESCRIPTION
    Deliberately narrow and syntactic, never evaluated: the root argument must be the literal
    variable $PSScriptRoot (not $root or any other name -- that is the exact gap sol's basename-
    collision case exploits) and the name argument must be a plain string constant with no path
    separator in it (a bare file name, never a relative or absolute path) -- ValidatePattern-style
    refusal-by-shape rather than a resolve-and-hope.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$CommandElement)

    $expr = $CommandElement
    if ($expr -is [System.Management.Automation.Language.ParenExpressionAst]) {
        $elements = @($expr.Pipeline.PipelineElements)
        if ($elements.Count -ne 1 -or -not ($elements[0] -is [System.Management.Automation.Language.CommandAst])) { return $null }
        $expr = $elements[0]
    }
    if (-not ($expr -is [System.Management.Automation.Language.CommandAst])) { return $null }
    if ($expr.GetCommandName() -ine 'Join-Path') { return $null }
    $args = @($expr.CommandElements | Select-Object -Skip 1)
    if ($args.Count -lt 2) { return $null }
    $rootArg = $args[0]
    $nameArg = $args[1]
    $rootIsPSScriptRoot = ($rootArg -is [System.Management.Automation.Language.VariableExpressionAst]) -and
        ($rootArg.VariablePath.UserPath -ieq 'PSScriptRoot')
    if (-not $rootIsPSScriptRoot) { return $null }
    if (-not ($nameArg -is [System.Management.Automation.Language.StringConstantExpressionAst])) { return $null }
    $name = $nameArg.Value
    if ([string]::IsNullOrWhiteSpace($name) -or $name -match '[\\/]') { return $null }
    return $name
}

function Get-AttrCudaAssignmentExpression {
    <#
    .SYNOPSIS
    Private helper: unwrap an AssignmentStatementAst's Right side to the expression it assigns,
    or $null if it is not a single simple expression.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Assignment)

    $right = $Assignment.Right
    # PowerShell's parser hands back Right as a bare CommandExpressionAst for a simple `$x = ...`
    # assignment, but wraps it in a one-element PipelineAst in other shapes (e.g. inside a nested
    # statement); both are unwrapped the same way here.
    if ($right -is [System.Management.Automation.Language.PipelineAst] -and $right.PipelineElements.Count -eq 1) {
        $right = $right.PipelineElements[0]
    }
    if ($right -is [System.Management.Automation.Language.CommandExpressionAst]) {
        return $right.Expression
    }
    return $null
}

function Test-AttrCudaScriptblockOnlyInvocationTarget {
    <#
    .SYNOPSIS
    Private classifier for class (b): true when $CommandAst's `.`/`&` target is a variable whose
    ONLY assignment anywhere in $Ast is a scriptblock literal, or a [scriptblock]-typed parameter.
    .DESCRIPTION
    A variable assigned a scriptblock literal, or nothing else, can only ever invoke code that
    was already visible to this same census as a literal `{ ... }` -- there is no separate file
    load to miss. A variable with even one non-scriptblock-literal assignment (e.g. a resolved
    executable path) is refused here and falls through to be classified some other way or thrown
    as unclassified: this check proves nothing was smuggled through under a scriptblock's cover.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$CommandAst,
        [Parameter(Mandatory = $true)]$Ast
    )

    if ($CommandAst.CommandElements.Count -lt 1) { return $false }
    $target = $CommandAst.CommandElements[0]
    if (-not ($target -is [System.Management.Automation.Language.VariableExpressionAst])) { return $false }
    $varName = $target.VariablePath.UserPath

    $paramAsts = $Ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.ParameterAst] }, $true)
    foreach ($parameter in $paramAsts) {
        if ($parameter.Name.VariablePath.UserPath -ine $varName) { continue }
        foreach ($attribute in $parameter.Attributes) {
            if ($attribute -is [System.Management.Automation.Language.TypeConstraintAst] -and
                $attribute.TypeName.Name -ieq 'scriptblock') {
                return $true
            }
        }
    }

    $assignments = $Ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        $node.Left -is [System.Management.Automation.Language.VariableExpressionAst] -and
        $node.Left.VariablePath.UserPath -ieq $varName
    }, $true)
    if ($assignments.Count -eq 0) { return $false }
    foreach ($assignment in $assignments) {
        $value = Get-AttrCudaAssignmentExpression -Assignment $assignment
        if (-not ($value -is [System.Management.Automation.Language.ScriptBlockExpressionAst])) { return $false }
    }
    return $true
}

function Test-AttrCudaCommandHasPathParameter {
    <#
    .SYNOPSIS
    Private classifier for class (c): true if $CommandAst names a -Path or -LiteralPath
    parameter, spelled in full, abbreviated, or by its PSPath/LP alias. Used to refuse
    auto-classifying an Add-Type that loads FROM a file.
    .DESCRIPTION
    sol PR #144 round 4 major 3: the exact `-ieq 'Path'`/`-ieq 'LiteralPath'` comparison this
    replaced read `Add-Type -Pat x.cs` or `-Lit x.cs` as pathless, because PowerShell accepts any
    unambiguous parameter-name PREFIX -- it bound those abbreviations to -Path/-LiteralPath at
    runtime even though the AST's parameter name is the shorter text actually written. A
    parameter counts as a path parameter here if 'Path' or 'LiteralPath' STARTS WITH the written
    name (case-insensitive, so any valid prefix abbreviation is caught, including the empty-string
    edge of neither name), or if the written name is the PSPath or LP alias. A parameter that is
    merely AMBIGUOUS between a path name and some other Add-Type parameter (e.g. `-Pa` could bind
    to -Path or -PassThru) still counts as a path parameter here: this classifier's job is to
    refuse auto-classifying, never to resolve the ambiguity itself, so it fails closed.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$CommandAst)

    foreach ($element in $CommandAst.CommandElements) {
        if ($element -is [System.Management.Automation.Language.CommandParameterAst]) {
            $name = $element.ParameterName
            if ([string]::IsNullOrEmpty($name)) { continue }
            if ('Path'.StartsWith($name, [StringComparison]::OrdinalIgnoreCase) -or
                'LiteralPath'.StartsWith($name, [StringComparison]::OrdinalIgnoreCase) -or
                $name -ieq 'PSPath' -or $name -ieq 'LP') {
                return $true
            }
        }
    }
    return $false
}

# ATTR3-SMOKE-RUNNER-DEPS-1 round 3. The explicit, reasoned exclusion list kept NEXT TO the
# classifier it qualifies: every load site Get-AttrCudaScriptLoadSites finds in a manifest file
# must be classified (a)/(b)/(c) above or listed here with a reason, keyed on the EXACT
# (repoRelativePath, site text) pair -- never on a basename or a substring -- so an exclusion can
# never silently widen to cover a different, unreviewed site.
$script:AttrCudaClosureScanExclusions = @(
    [pscustomobject]@{
        repoRelativePath = 'tools/profiling/run-release-gui-smoke.ps1'
        literal = '& $detectorPwsh @detectorArgs 2>&1'
        reason = 'The dormant detect-playback-artifacts.ps1 launch, reached only under ' +
            '-DetectPlaybackArtifacts. The ATTR-3 attribution job (playback-attr-3-cuda-job.ps1) ' +
            'never passes that switch in its emitted smoke invocation -- ' +
            'test_the_emitted_smoke_command_never_passes_detectplaybackartifacts asserts this -- ' +
            'and the runner itself Test-Path-guards the call, falling back to verdict="no-data" ' +
            'if the file is ever missing. Dormant for this route by construction, not by luck.'
    },
    [pscustomobject]@{
        repoRelativePath = 'tools/profiling/run-release-gui-smoke.ps1'
        literal = '[System.Diagnostics.Process]::Start($startInfo)'
        reason = 'Launches the hash-pinned, already-deployed application executable -- ' +
            '$startInfo.FileName is set to $exe, the built app path resolved before this line, ' +
            'never a $PSScriptRoot sibling script. Process.Start executes an OS binary directly; ' +
            'it does not load or run PowerShell/.NET code from a file this census needs to see ' +
            '(round 4, fable round-3 minor, PR #144).'
    }
)

function Test-AttrCudaClosureScanExclusionMatch {
    <#
    .SYNOPSIS
    True if a (repoRelativePath, exact site text) pair is on the explicit exclusion list above.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRelativePath,

        [Parameter(Mandatory = $true)]
        [string]$Literal
    )

    foreach ($exclusion in $script:AttrCudaClosureScanExclusions) {
        if ($exclusion.repoRelativePath -eq $RepoRelativePath -and $exclusion.literal -eq $Literal) {
            return $true
        }
    }
    return $false
}

function Assert-AttrCudaClosureComplete {
    <#
    .SYNOPSIS
    Prove the pinned smoke-runner closure manifest is COMPLETE: every load site in every manifest
    file's own committed text classifies cleanly, and the resolved class-(a) targets are EXACTLY
    the manifest's non-root siblings, in both directions.
    .DESCRIPTION
    ATTR3-SMOKE-RUNNER-DEPS-1 round 3, round 4 (PR #144). Generator-time (and CI-time) only --
    never embedded in an emitted job, same reason as Resolve-AttrCudaCommittedBlobId. This is a
    REGRESSION TRIPWIRE over these four reviewed files, not an exhaustive proof -- see the module
    header above the manifest for the honest statement of what it does and does not guarantee. It
    runs Get-AttrCudaScriptLoadSites over each manifest file's committed text at -Commit and
    requires every site to be exactly one of:
      (a) `Join-Path $PSScriptRoot '<bare file name>'`, whose resolved repo-relative path is a
          manifest entry, by FULL PATH equality -- never by basename alone;
      (b) `&`/`.` on a scriptblock-only variable or a [scriptblock]-typed parameter
          (Test-AttrCudaScriptblockOnlyInvocationTarget);
      (c) `Add-Type` with no -Path/-LiteralPath, matched by prefix and alias so an abbreviated
          parameter cannot pass as pathless (Test-AttrCudaCommandHasPathParameter);
      (d) a pinned exclusion (Test-AttrCudaClosureScanExclusionMatch).
    A Set-Alias/sal/New-Alias/nal site is never a candidate for (a)-(c) and matches (d) only if
    explicitly pinned there -- its target is never resolved statically, so it is always
    unclassified unless excluded by name (round 4, sol major 2). Anything else throws
    ATTRCUDA_UNCLASSIFIED_SCRIPT_REFERENCE. Finally, the set of resolved
    class-(a) targets must equal the manifest minus its root (first) entry, in both directions --
    a manifest entry never reached by a real load, or a real load that resolves outside the
    manifest, is a completeness failure, not silently accepted either way.
    Throws ATTRCUDA_BLOB_UNRESOLVED if a manifest path is not a committed blob at -Commit,
    ATTRCUDA_SCRIPT_PARSE_ERROR (from Get-AttrCudaScriptLoadSites), or
    ATTRCUDA_UNCLASSIFIED_SCRIPT_REFERENCE (above).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,

        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[0-9a-f]{40}$')]
        [string]$Commit,

        [string[]]$RepoRelativePaths = $script:AttrCudaSmokeRunnerClosureManifest
    )

    $normalizedPaths = @($RepoRelativePaths | ForEach-Object { $_ -replace '\\', '/' })
    $manifestBasenames = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($path in $normalizedPaths) {
        [void]$manifestBasenames.Add($path.Substring($path.LastIndexOf('/') + 1))
    }
    $rootPath = $normalizedPaths[0]
    $rootName = $rootPath.Substring($rootPath.LastIndexOf('/') + 1)
    $siblingBasenames = [System.Collections.Generic.HashSet[string]]::new([string[]]$manifestBasenames, [StringComparer]::Ordinal)
    [void]$siblingBasenames.Remove($rootName)

    $resolvedTargets = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)

    foreach ($relativePath in $normalizedPaths) {
        $directory = $relativePath.Substring(0, $relativePath.LastIndexOf('/'))
        $blobId = Resolve-AttrCudaCommittedBlobId -RepoRoot $RepoRoot -Commit $Commit -RepoRelativePath $relativePath
        $tempFile = Join-Path ([IO.Path]::GetTempPath()) "attrcuda-census-$([Guid]::NewGuid().ToString('N')).tmp"
        try {
            [void](Save-AttrCudaCommittedBlobBytes -RepoRoot $RepoRoot -BlobId $blobId -Destination $tempFile)
            $text = [IO.File]::ReadAllText($tempFile)
        } finally {
            if (Test-Path -LiteralPath $tempFile) { Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue }
        }

        # Parsed once here (beyond Get-AttrCudaScriptLoadSites' own internal parse) so the
        # scriptblock-only classifier below can search the WHOLE file for $varName's assignments
        # and parameter declarations -- not just the neighborhood of the one site being classified.
        $fileTokens = $null
        $fileParseErrors = $null
        $fileAst = [System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$fileTokens, [ref]$fileParseErrors)

        foreach ($site in (Get-AttrCudaScriptLoadSites -ScriptText $text -SourceLabel $relativePath)) {
            $classified = $false

            if ($site.kind -eq 'InvocationOperator' -or $site.kind -eq 'CommandName') {
                $argIndex = if ($site.kind -eq 'InvocationOperator') { 0 } else { 1 }
                if ($site.ast.CommandElements.Count -gt $argIndex) {
                    $target = Get-AttrCudaPSScriptRootJoinPathLiteral -CommandElement $site.ast.CommandElements[$argIndex]
                    if ($null -ne $target) {
                        $resolvedPath = "$directory/$target"
                        if ($manifestBasenames.Contains($target) -and ($normalizedPaths -contains $resolvedPath)) {
                            [void]$resolvedTargets.Add($target)
                            $classified = $true
                        }
                    }
                }
            }

            if (-not $classified -and $site.kind -eq 'InvocationOperator') {
                if (Test-AttrCudaScriptblockOnlyInvocationTarget -CommandAst $site.ast -Ast $fileAst) {
                    $classified = $true
                }
            }

            if (-not $classified -and $site.kind -eq 'CommandName' -and $site.commandName -ieq 'Add-Type') {
                if (-not (Test-AttrCudaCommandHasPathParameter -CommandAst $site.ast)) {
                    $classified = $true
                }
            }

            if (-not $classified -and (Test-AttrCudaClosureScanExclusionMatch -RepoRelativePath $relativePath -Literal $site.text)) {
                $classified = $true
            }

            if (-not $classified) {
                throw ("ATTRCUDA_UNCLASSIFIED_SCRIPT_REFERENCE ${relativePath}: site '$($site.text)' " +
                    "(kind=$($site.kind)) is neither a resolved `$PSScriptRoot manifest load (full-path " +
                    "match, never basename), a scriptblock-only invocation, an Add-Type with no -Path, " +
                    "nor on the pinned exclusion list next to Test-AttrCudaClosureScanExclusionMatch in " +
                    "AttrCudaArtifacts.psm1 -- classify it before this closure can be trusted")
            }
        }
    }

    $missing = @($siblingBasenames | Where-Object { -not $resolvedTargets.Contains($_) })
    $extra = @($resolvedTargets | Where-Object { -not $siblingBasenames.Contains($_) })
    if ($missing.Count -gt 0 -or $extra.Count -gt 0) {
        throw ("ATTRCUDA_UNCLASSIFIED_SCRIPT_REFERENCE closure completeness mismatch: " +
            "missing=[$($missing -join ',')] extra=[$($extra -join ',')] -- the pinned manifest and " +
            "the resolved `$PSScriptRoot load sites across its own files must name exactly the same " +
            "siblings, in both directions")
    }
}

function Resolve-AttrCudaSmokeRunnerClosure {
    <#
    .SYNOPSIS
    Resolve the PINNED smoke-runner closure's committed bytes, AS COMMITTED at a given commit.
    .DESCRIPTION
    ATTR3-SMOKE-RUNNER-DEPS-1 round 3 (NARROW BY REDESIGN). No scan, no recursion, no traversal:
    the set of paths is Get-AttrCudaSmokeRunnerClosureManifest, proved complete against the real
    script text by Assert-AttrCudaClosureComplete (call that first; this function does not
    re-verify completeness, only resolution). Returns an ordered list of [pscustomobject]@{ name;
    repoRelativePath; blobId; sha256 }, in manifest order (root script first).
    Throws ATTRCUDA_BLOB_UNRESOLVED (from Resolve-AttrCudaCommittedBlobId) if a pinned path is not
    a committed blob at -Commit.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,

        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[0-9a-f]{40}$')]
        [string]$Commit,

        [string[]]$RepoRelativePaths = $script:AttrCudaSmokeRunnerClosureManifest
    )

    $closure = [System.Collections.Generic.List[object]]::new()
    foreach ($relativePath in $RepoRelativePaths) {
        $normalized = $relativePath -replace '\\', '/'
        $name = $normalized.Substring($normalized.LastIndexOf('/') + 1)
        $blobId = Resolve-AttrCudaCommittedBlobId -RepoRoot $RepoRoot -Commit $Commit -RepoRelativePath $normalized
        $tempFile = Join-Path ([IO.Path]::GetTempPath()) "attrcuda-closure-$([Guid]::NewGuid().ToString('N')).tmp"
        try {
            $sha256 = Save-AttrCudaCommittedBlobBytes -RepoRoot $RepoRoot -BlobId $blobId -Destination $tempFile
        } finally {
            if (Test-Path -LiteralPath $tempFile) { Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue }
        }
        [void]$closure.Add([pscustomobject]@{
            name = $name
            repoRelativePath = $normalized
            blobId = $blobId
            sha256 = $sha256
        })
    }
    return @($closure)
}

function Get-AttrCudaClosureDigestHex {
    <#
    .SYNOPSIS
    The content-addressed digest of a resolved dependency closure.
    .DESCRIPTION
    ATTR3-SMOKE-RUNNER-DEPS-1. sha256 of the sorted `<sha256>  <name>` lines (two-space
    separator, sha256sum-shaped) of the closure's entries, newline-joined with a trailing
    newline -- so any two generators that resolve the SAME closure at the SAME commit derive
    the SAME digest regardless of discovery order, and the published cache directory name
    (`smoke-runner-<digest16>`) is a pure function of the closure's content.
    Sorted with [StringComparer]::Ordinal (fable minor, PR #144 round 3, carried forward from
    round 2's culture-aware Sort-Object): the digest is a cross-host content address and must
    not depend on the sorting host's culture, even though the 64-hex sha256 prefix makes an
    actual ordinal/culture divergence practically impossible here. The Python test's sorted()
    is ordinal, so this makes the two definitions exact rather than merely agreeing in practice.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Closure
    )

    $lines = [string[]]@($Closure | ForEach-Object { "$($_.sha256)  $($_.name)" })
    [Array]::Sort($lines, [StringComparer]::Ordinal)
    $joined = ($lines -join "`n") + "`n"
    $bytes = [Text.Encoding]::UTF8.GetBytes($joined)
    $stream = [IO.MemoryStream]::new($bytes)
    try {
        (Get-FileHash -InputStream $stream -Algorithm SHA256).Hash.ToLowerInvariant()
    } finally {
        $stream.Dispose()
    }
}

function Test-AttrCudaPathIsReparsePoint {
    <#
    .SYNOPSIS
    True if Path exists and is a reparse point (symlink, junction or mount point); false if it
    is a plain file/directory or does not exist at all.
    .DESCRIPTION
    ATTR3-SMOKE-RUNNER-DEPS-1 (sol, PR #144 major 2). Test-Path and Get-FileHash both resolve
    THROUGH a reparse point to whatever it targets, so a hash-pin check that only ever calls
    those two can be satisfied by a link whose TARGET -- not the staged, verified directory --
    happens to carry the pinned bytes. Every closure directory and every member is checked with
    this, before its content is trusted, so a link is refused rather than silently followed.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) { return $false }
    return (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Get-AttrCudaClosureDirectoryMismatch {
    <#
    .SYNOPSIS
    Compare a directory against the expected closure EXACT SET; return $null when it matches
    exactly, or a short reason string naming the first mismatch found. Never throws.
    .DESCRIPTION
    ATTR3-SMOKE-RUNNER-DEPS-1 round 3 (NARROW BY REDESIGN). Round 1/2 defined this exact-set rule
    twice -- once inline in the stager's "already staged" check, once inline in the attribution
    job's ATTRCUDA_SMOKE_RUNNER_STALE gate -- which is exactly how the two could silently drift
    apart. Moved here so both emitted jobs embed the BYTE-IDENTICAL function text
    (Get-AttrCudaEmbeddedFunctionSource), never two copies that only look the same. "Matches
    exactly" means: the directory exists, is not itself a reparse point, has EXACTLY -Entries.Count
    children (no extra entries, no subdirectories among them), none of those children is a reparse
    point, and every -Entries member is present with the pinned sha256.
    -Entries is an array of [pscustomobject]@{ name; sha256 } (sha256 in any case; compared
    case-insensitively). A plain array + -contains, not a HashSet: the template lint
    (attr3_publish_write_scan.ps1 R4) only allowlists specific .NET static/instance members by
    name, and this closure is a handful of files, so an O(n) membership test costs nothing here.
    Returns $null on an exact match; otherwise a reason string. Never throws -- the caller (an
    "already staged?" check vs. a hard pin gate) decides what a mismatch means.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Dir,
        [Parameter(Mandatory = $true)][object[]]$Entries
    )

    if (-not (Test-Path -LiteralPath $Dir -PathType Container)) { return "missing closure directory $Dir" }
    if (Test-AttrCudaPathIsReparsePoint -Path $Dir) { return "closure directory $Dir is a reparse point" }

    # Every EXPECTED entry is checked first, by name, so a refusal names WHICH dependency is
    # stale or missing whenever one is -- before the broader "anything extra?" sweep below, whose
    # own reason (a bare count) would otherwise mask that more useful, specific answer.
    foreach ($entry in $Entries) {
        $path = Join-Path $Dir $entry.name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            return "closure directory $Dir is missing $($entry.name)"
        }
        if (Test-AttrCudaPathIsReparsePoint -Path $path) {
            return "closure directory $Dir entry $($entry.name) is a reparse point"
        }
        $actualSha = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualSha -ne $entry.sha256.ToLowerInvariant()) {
            return "closure directory $Dir entry $($entry.name) sha256 mismatch: expected $($entry.sha256), actual $actualSha"
        }
    }

    # Every expected entry is present and correct -- now prove there is nothing ELSE: no extra
    # file, no extra subdirectory, no reparse point standing in for a plain file.
    $actualEntries = @(Get-ChildItem -LiteralPath $Dir -Force)
    if ($actualEntries.Count -ne $Entries.Count) {
        return "closure directory $Dir has $($actualEntries.Count) entries, expected $($Entries.Count)"
    }
    $expectedNames = @($Entries | ForEach-Object { $_.name })
    foreach ($actual in $actualEntries) {
        if ($expectedNames -notcontains $actual.Name) {
            return "closure directory $Dir has an unexpected entry $($actual.Name)"
        }
        if ($actual.PSIsContainer) {
            return "closure directory $Dir entry $($actual.Name) is a subdirectory"
        }
        if (Test-AttrCudaPathIsReparsePoint -Path $actual.FullName) {
            return "closure directory $Dir entry $($actual.Name) is a reparse point"
        }
    }
    return $null
}

function Assert-AttrCudaWritableFileSlot {
    <#
    .SYNOPSIS
    Prove a publish destination can only ever be written as a plain local FILE, then return it.
    .DESCRIPTION
    sol, PR #133 r3: Copy-Item / Move-Item / Set-Content onto a path that is a junction or a
    directory writes INTO the link target -- possibly outside the job root -- before any hash
    check can fail. Every publish write therefore calls this first. The parent directory must
    exist and must not be a reparse point; the slot itself must be absent or a plain file (which
    is removed so the write creates a fresh file). Anything else throws ATTRCUDA_SLOT_OCCUPIED
    or ATTRCUDA_SLOT_PARENT_IS_LINK, and nothing is written.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $full = [IO.Path]::GetFullPath($Path)
    $parent = Get-Item -LiteralPath ([IO.Path]::GetDirectoryName($full)) -Force -ErrorAction SilentlyContinue
    if ($null -eq $parent -or -not $parent.PSIsContainer) {
        throw "ATTRCUDA_SLOT_PARENT_MISSING $full"
    }
    if (($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "ATTRCUDA_SLOT_PARENT_IS_LINK $($parent.FullName)"
    }
    $existing = Get-Item -LiteralPath $full -Force -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        $isReparse = (($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
        if ($existing.PSIsContainer -or $isReparse) {
            throw "ATTRCUDA_SLOT_OCCUPIED $full is a directory or reparse point; refusing to write through it"
        }
        Remove-Item -LiteralPath $full -Force -Confirm:$false
    }
    return $full
}

function Publish-AttrCudaText {
    <#
    .SYNOPSIS
    The ONLY way an emitted job writes text outside its job-owned work tree: slot check and write
    in one call, so a guard can never be separated from the write it guards (sol PR #133 r5).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][AllowNull()][object]$Value
    )

    $slot = Assert-AttrCudaWritableFileSlot -Path $Path
    $text = if ($null -eq $Value) { '' } else { (@($Value) | ForEach-Object { [string]$_ }) -join [Environment]::NewLine }
    [IO.File]::WriteAllText($slot, $text + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    return $slot
}

function Read-AttrCudaBase64Payload {
    <#
    .SYNOPSIS
    Decode a base64-embedded payload and return both its raw bytes and lowercase sha256.
    .DESCRIPTION
    ATTR3-SMOKE-RUNNER-PIN-1, round 2: the smoke-runner stager embeds the runner's committed
    bytes INLINE in the emitted job (no side file, no inbox -- UmRunDrop.psm1's side-file name
    policy rejects a bare `.ps1`). This exists so [Convert]::FromBase64String never appears as
    literal template text: tools/repo_hygiene/attr3_publish_write_scan.ps1's R4 rule allowlists
    .NET static members in the TEMPLATE by name, and this repository's policy is that a job
    template stays inside that allowlist -- decode-and-hash instead lives here, in the module
    that is the job's actual safety boundary, spliced in and called by NAME like every other
    embedded verifier. Get-FileHash -InputStream is used for the digest (an already-allowlisted
    cmdlet) rather than a raw [Security.Cryptography.SHA256] call, for the same reason.
    Throws ATTRCUDA_BASE64_MALFORMED.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Base64
    )

    try {
        $bytes = [Convert]::FromBase64String($Base64)
    } catch {
        throw "ATTRCUDA_BASE64_MALFORMED payload is not valid base64: $($_.Exception.Message)"
    }
    $stream = [IO.MemoryStream]::new($bytes)
    try {
        $sha256 = (Get-FileHash -InputStream $stream -Algorithm SHA256).Hash.ToLowerInvariant()
    } finally {
        $stream.Dispose()
    }
    [pscustomobject]@{ bytes = $bytes; sha256 = $sha256 }
}

function Publish-AttrCudaBytes {
    <#
    .SYNOPSIS
    Write raw bytes to a destination outside the job-owned work tree, slot-checked in the same
    call -- the byte-array counterpart of Publish-AttrCudaText (sol PR #133 r5's guard-with-the-
    write pattern), for a payload that arrived decoded rather than copied from another file.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][byte[]]$Bytes
    )

    $slot = Assert-AttrCudaWritableFileSlot -Path $Path
    [IO.File]::WriteAllBytes($slot, $Bytes)
    return $slot
}

function Publish-AttrCudaFileCopy {
    <#
    .SYNOPSIS
    Copy a file to a destination outside the job-owned work tree, slot-checked in the same call.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $slot = Assert-AttrCudaWritableFileSlot -Path $Destination
    Copy-Item -LiteralPath $Source -Destination $slot -Force
    return $slot
}

function Publish-AttrCudaFileMove {
    <#
    .SYNOPSIS
    Rename a file into a destination outside the job-owned work tree, slot-checked in the same call.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $slot = Assert-AttrCudaWritableFileSlot -Path $Destination
    Move-Item -LiteralPath $Source -Destination $slot -Force
    return $slot
}

function Assert-AttrCudaNonOverwritingFileSlot {
    <#
    .SYNOPSIS
    Prove a publish destination's PARENT is safe to write into, without touching whatever (if
    anything) already occupies the destination itself.
    .DESCRIPTION
    ATTR3-FIXTURE-STAGE-1 (sol, PR #139 r1 MAJOR). Assert-AttrCudaWritableFileSlot removes a
    plain file already occupying the slot so its caller's subsequent -Force write always
    succeeds -- exactly the race a non-overwriting publish must not have: a different-bytes
    file that a concurrent publisher placed at the destination, deleted out from under it and
    replaced. This checks only what Assert-AttrCudaWritableFileSlot checks about the PARENT
    (must exist, must not itself be a reparse point) and leaves the destination path alone.
    Whether the destination is occupied is never decided here -- Publish-AttrCudaFileMove-
    NonOverwriting's [System.IO.File]::Move(..., $false) is the sole, atomic arbiter of that.
    Throws ATTRCUDA_SLOT_PARENT_MISSING or ATTRCUDA_SLOT_PARENT_IS_LINK; returns the full path
    otherwise.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $full = [IO.Path]::GetFullPath($Path)
    $parent = Get-Item -LiteralPath ([IO.Path]::GetDirectoryName($full)) -Force -ErrorAction SilentlyContinue
    if ($null -eq $parent -or -not $parent.PSIsContainer) {
        throw "ATTRCUDA_SLOT_PARENT_MISSING $full"
    }
    if (($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "ATTRCUDA_SLOT_PARENT_IS_LINK $($parent.FullName)"
    }
    $full
}

function Publish-AttrCudaFileMoveNonOverwriting {
    <#
    .SYNOPSIS
    Atomically rename a file into a destination outside the job-owned work tree WITHOUT ever
    overwriting or deleting a same-named file already there.
    .DESCRIPTION
    ATTR3-FIXTURE-STAGE-1 (sol, PR #139 r1 MAJOR), narrowly scoped to the fixture stager --
    Publish-AttrCudaFileMove's slot check deletes ANY plain file occupying the destination and
    then moves with -Force, so a different-bytes cache file that arrives between the fixture
    job's own initial existence check and this call is silently removed and replaced. That
    race is closed here, not in the shared helper: the package stager (playback-attr-3-cuda-
    stage-job.ps1) still calls Publish-AttrCudaFileMove and still has it, tracked separately.
    The parent is checked exactly like Assert-AttrCudaWritableFileSlot (must exist, must not be
    a link), but the destination slot itself is never inspected or removed first. .NET's
    [System.IO.File]::Move($Source, $Destination, $false) is the sole arbiter of "is it
    occupied" -- overwrite=$false is atomic on NTFS, so there is no check-then-act window for a
    concurrent writer to land in between the check and the rename.
    On IOException the destination already exists; nothing has been moved, deleted or written
    -- the caller re-hashes the destination (identical bytes: a concurrent publisher already
    finished this exact fixture; different bytes: fail closed) instead of this helper silently
    reporting success either way.
    Throws ATTRCUDA_NONOVERWRITE_DESTINATION_EXISTS when the destination is occupied.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $slot = Assert-AttrCudaNonOverwritingFileSlot -Path $Destination
    try {
        [IO.File]::Move($Source, $slot, $false)
    } catch [IO.IOException] {
        throw "ATTRCUDA_NONOVERWRITE_DESTINATION_EXISTS $slot already exists: $($_.Exception.Message)"
    }
    return $slot
}

function Publish-AttrCudaDirectoryMoveNonOverwriting {
    <#
    .SYNOPSIS
    Atomically rename a directory into a destination outside the job-owned work tree WITHOUT
    ever overwriting or deleting a same-named directory already there.
    .DESCRIPTION
    ATTR3-SMOKE-RUNNER-DEPS-1: the smoke-runner closure directory is built under a temp name
    and published in one rename, mirroring Publish-AttrCudaFileMoveNonOverwriting's race-free
    publish for a single file. The parent is checked exactly like
    Assert-AttrCudaNonOverwritingFileSlot (must exist, must not be a link); the destination slot
    itself is never inspected or removed first. [System.IO.Directory]::Move throws IOException
    when the destination already exists on Windows (no overwrite semantics for a directory
    move), so there is no check-then-act window for a concurrent writer to land in between the
    check and the rename.
    On IOException the destination already exists; nothing has been moved, deleted or written --
    the caller re-verifies the destination's content instead of this helper silently reporting
    success either way.
    Throws ATTRCUDA_NONOVERWRITE_DESTINATION_EXISTS when the destination is occupied.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $slot = Assert-AttrCudaNonOverwritingFileSlot -Path $Destination
    try {
        [IO.Directory]::Move($Source, $slot)
    } catch [IO.IOException] {
        throw "ATTRCUDA_NONOVERWRITE_DESTINATION_EXISTS $slot already exists: $($_.Exception.Message)"
    }
    return $slot
}

function New-AttrCudaDirectory {
    <#
    .SYNOPSIS
    Create (or accept) a directory whose parent is not a link and which is not itself a link.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $full = [IO.Path]::GetFullPath($Path)
    $parent = Get-Item -LiteralPath ([IO.Path]::GetDirectoryName($full)) -Force -ErrorAction SilentlyContinue
    if ($null -eq $parent -or -not $parent.PSIsContainer) {
        throw "ATTRCUDA_DIR_PARENT_MISSING $full"
    }
    if (($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "ATTRCUDA_DIR_PARENT_IS_LINK $($parent.FullName)"
    }
    $existing = Get-Item -LiteralPath $full -Force -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        if (-not $existing.PSIsContainer -or (($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
            throw "ATTRCUDA_DIR_OCCUPIED $full is a file or a reparse point"
        }
        return $full
    }
    [void](New-Item -ItemType Directory -Path $full)
    return $full
}

function Remove-AttrCudaPartialFile {
    <#
    .SYNOPSIS
    Delete one .partial path only if it is a plain FILE; never recurse, never follow a link.
    .DESCRIPTION
    sol, PR #133 r3: `Remove-Item -Recurse` on a .partial path that is occupied by a directory can
    traverse an NTFS junction inside it and delete the junction's TARGET, outside the job root
    (PowerShell/PowerShell#26913). A .partial is only ever written as a file, so anything else --
    a directory, a symlink, a junction -- is left exactly where it is and reported. Returns $true
    when the path is absent or was a file that is now gone, $false when it was refused.
    sol, PR #133 r8: the ANCESTOR chain from -TrustedRoot is checked too (a plain file reached
    through a linked inbox/cache/outbox is outside the root). A refusal never throws: this runs in
    cleanup and failure paths, where an exception would mask the job's real exit code.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$TrustedRoot,
        [Parameter(Mandatory = $true)][string]$Path
    )

    try {
        $Path = Assert-AttrCudaNoLinkBelowRoot -TrustedRoot $TrustedRoot -Path $Path
    } catch {
        Write-Warning "ATTRCUDA_PARTIAL_OUTSIDE_TRUSTED_ROOT left in place: $($_.Exception.Message)"
        return $false
    }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) { return $true }
    $isReparse = (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
    if ($item.PSIsContainer -or $isReparse) {
        Write-Warning "ATTRCUDA_PARTIAL_NOT_A_FILE left in place (directory or reparse point): $Path"
        return $false
    }
    Remove-Item -LiteralPath $Path -Force -Confirm:$false -ErrorAction SilentlyContinue
    return (-not (Test-Path -LiteralPath $Path))
}

function Assert-AttrCudaNoLinkBelowRoot {
    <#
    .SYNOPSIS
    Prove $Path lies strictly under $TrustedRoot and that no EXISTING component between them is a
    reparse point; return the full path.
    .DESCRIPTION
    sol, PR #133 r6: a delete of AgentRoot\outbox\<job> followed an outbox JUNCTION outside the agent
    root before any later check could refuse the link. Every component after the trusted root, up to
    and including the path itself, is inspected; the trusted root is the provisioning boundary.
    Throws ATTRCUDA_PATH_NOT_UNDER_ROOT or ATTRCUDA_ANCESTOR_IS_LINK and touches nothing.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$TrustedRoot,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $rootFull = [IO.Path]::GetFullPath($TrustedRoot).TrimEnd('\')
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (-not $full.StartsWith($rootFull + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "ATTRCUDA_PATH_NOT_UNDER_ROOT $full is not strictly under $rootFull"
    }
    $relative = $full.Substring($rootFull.Length + 1)
    $cursor = $rootFull
    foreach ($component in $relative.Split('\')) {
        if ([string]::IsNullOrEmpty($component) -or $component -eq '.' -or $component -eq '..') {
            throw "ATTRCUDA_PATH_NOT_UNDER_ROOT $full has an empty or relative component"
        }
        $cursor = $cursor + '\' + $component
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($null -eq $item) { break }
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "ATTRCUDA_ANCESTOR_IS_LINK $cursor (on the way to $full)"
        }
    }
    return $full
}

function Remove-AttrCudaTree {
    <#
    .SYNOPSIS
    Recursively delete a job-owned directory under a trusted root, refusing if ANY component between
    the root and the directory, or any entry inside it, is a reparse point.
    .DESCRIPTION
    The ancestor chain is checked FIRST (Assert-AttrCudaNoLinkBelowRoot), even when the directory is
    absent, so a linked parent can never steer the delete (sol PR #133 r6). The walk then does not
    descend into reparse points; if one is found the function throws ATTRCUDA_TREE_HAS_REPARSE_POINT
    and deletes nothing. Only a tree proved free of links is handed to Remove-Item -Recurse.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$TrustedRoot,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $Path = Assert-AttrCudaNoLinkBelowRoot -TrustedRoot $TrustedRoot -Path $Path
    $root = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $root) { return }
    $stack = [System.Collections.Generic.Stack[System.IO.FileSystemInfo]]::new()
    $stack.Push($root)
    while ($stack.Count -gt 0) {
        $entry = $stack.Pop()
        if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "ATTRCUDA_TREE_HAS_REPARSE_POINT $($entry.FullName) (refusing to delete $Path)"
        }
        if ($entry -is [System.IO.DirectoryInfo]) {
            foreach ($child in $entry.EnumerateFileSystemInfos()) { $stack.Push($child) }
        }
    }
    Remove-Item -LiteralPath $Path -Recurse -Force -Confirm:$false
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
    $declaredLength = $null
    $runNonce = ''
    $evidenceNode = $result.PSObject.Properties['evidence']
    if ($null -ne $evidenceNode -and $null -ne $evidenceNode.Value) {
        $nonceProperty = $evidenceNode.Value.PSObject.Properties['runNonce']
        if ($null -ne $nonceProperty) { $runNonce = [string]$nonceProperty.Value }
        $snapshotProperty = $evidenceNode.Value.PSObject.Properties['runLogSnapshot']
        if ($null -ne $snapshotProperty -and $null -ne $snapshotProperty.Value) {
            $shaProperty = $snapshotProperty.Value.PSObject.Properties['sha256']
            if ($null -ne $shaProperty) { $declaredSha = ([string]$shaProperty.Value).ToLowerInvariant() }
            $lengthProperty = $snapshotProperty.Value.PSObject.Properties['length']
            if ($null -ne $lengthProperty -and $null -ne $lengthProperty.Value) { $declaredLength = [string]$lengthProperty.Value }
        }
    }
    if ([string]::IsNullOrWhiteSpace($declaredSha)) {
        throw "ATTRCUDA_SMOKE_LOG_UNBOUND $ResultJsonPath carries no evidence.runLogSnapshot.sha256 to bind $fullLogPath to"
    }
    if ($declaredSha -ne $actualSha) {
        throw "ATTRCUDA_SMOKE_LOG_SHA_MISMATCH $fullLogPath declared=$declaredSha actual=$actualSha"
    }
    # sol, PR #133 r3: the smoke runner binds the snapshot by sha256 AND length; both are checked.
    $actualLength = [int64](Get-Item -LiteralPath $fullLogPath).Length
    [int64]$parsedLength = -1
    if ($null -eq $declaredLength -or -not [int64]::TryParse($declaredLength, [ref]$parsedLength)) {
        throw "ATTRCUDA_SMOKE_LOG_LENGTH_UNBOUND $ResultJsonPath carries no integer evidence.runLogSnapshot.length"
    }
    if ($parsedLength -ne $actualLength) {
        throw "ATTRCUDA_SMOKE_LOG_LENGTH_MISMATCH $fullLogPath declared=$parsedLength actual=$actualLength"
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
    Assert-AttrCudaSafeArtifactName, `
    Assert-AttrCudaDirectChild, `
    Assert-AttrCudaBuildManifest, `
    Assert-AttrCudaFixtureCommittedBytes, `
    Resolve-AttrCudaCommittedBlobId, `
    Save-AttrCudaCommittedBlobBytes, `
    Get-AttrCudaSmokeRunnerClosureManifest, `
    Get-AttrCudaScriptLoadSites, `
    Test-AttrCudaClosureScanExclusionMatch, `
    Assert-AttrCudaClosureComplete, `
    Resolve-AttrCudaSmokeRunnerClosure, `
    Get-AttrCudaClosureDigestHex, `
    Assert-AttrCudaWritableFileSlot, `
    Test-AttrCudaPathIsReparsePoint, `
    Get-AttrCudaClosureDirectoryMismatch, `
    Assert-AttrCudaNonOverwritingFileSlot, `
    Read-AttrCudaBase64Payload, `
    Publish-AttrCudaBytes, `
    Publish-AttrCudaText, `
    Publish-AttrCudaFileCopy, `
    Publish-AttrCudaFileMove, `
    Publish-AttrCudaFileMoveNonOverwriting, `
    Publish-AttrCudaDirectoryMoveNonOverwriting, `
    New-AttrCudaDirectory, `
    Remove-AttrCudaPartialFile, `
    Assert-AttrCudaNoLinkBelowRoot, `
    Remove-AttrCudaTree, `
    Resolve-AttrCudaSmokeRunLog, `
    Get-AttrCudaLastEligibilityLine, `
    Get-AttrCudaEligibilityVerdict
