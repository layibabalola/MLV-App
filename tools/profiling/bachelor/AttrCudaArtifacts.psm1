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

function Get-AttrCudaGitBlobHashFromBytes {
    <#
    .SYNOPSIS
    Git's blob hash of a byte buffer already in memory, exactly as `git hash-object` would compute
    it for a file living at $RelativePath -- content filters (autocrlf, .gitattributes) included.
    .DESCRIPTION
    ATTR3-ADMIT-CONTENT-PIN-1 round 2d. `git hash-object -- <path>` opens and reads $Path itself --
    a SECOND read of the same mutable file, which is exactly the internal check/use gap this whole
    round closes. `git hash-object --stdin --path <RelativePath>` hashes whatever bytes are piped
    to it on stdin while still selecting content filters BY that path (the same ones `--path` would
    apply if it were reading the file itself), so this can feed it the identical buffer
    Assert-AttrCudaFixtureCommittedBytes already read through its own single, write-denying handle
    -- no second read of the file.

    Round-2d SELF-CAUGHT DEFECT: an earlier version of this function computed
    SHA1("blob " + <byte length> + "\0" + <content>) directly over the raw in-memory buffer,
    entirely in .NET, with no git subprocess at all. That is only the correct git blob hash when
    nothing normalises the bytes on the way into the object database. It silently disagreed with
    the committed blob for any autocrlf/gitattributes-normalised text fixture -- caught by
    FixtureCommittedBytesTests.test_an_unmodified_tracked_file_is_accepted (a `.c` source file)
    failing closed on completely unmodified content, before this ever reached review. Filtering
    is a repository-configuration concern only git itself can resolve correctly; reproducing its
    hash FORMAT locally without also reproducing its FILTERS is not a safe shortcut.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )

    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = 'git'
    foreach ($arg in @('-C', $RepoRoot, 'hash-object', '--stdin', '--path', $RelativePath)) {
        [void]$psi.ArgumentList.Add($arg)
    }
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    # round 2e (sol/fable MAJOR): Process.Start (and the stream I/O around it) can throw a raw,
    # untyped .NET exception -- e.g. Win32Exception if the git binary that
    # Assert-AttrCudaFixtureCommittedBytes's earlier Get-Command probe found a moment ago is no
    # longer resolvable when actually launched -- whose message never starts with an ATTR3_FIXTURE_*
    # token. Left uncaught, that escaped Get-UmRunFixtureAdmission's classification entirely: an
    # environmental failure with nothing to say about the fixture's bytes would be graded on
    # whether its first word happened to match, not on what it actually was.
    try {
        $proc = [Diagnostics.Process]::Start($psi)
        try {
            $proc.StandardInput.BaseStream.Write($Bytes, 0, $Bytes.Length)
        } finally {
            $proc.StandardInput.Close()
        }
        $stdout = $proc.StandardOutput.ReadToEnd()
        $stderr = $proc.StandardError.ReadToEnd()
        $proc.WaitForExit()
    } catch {
        throw "ATTR3_FIXTURE_GIT_UNAVAILABLE could not launch git to hash-object (via stdin) '$RelativePath' in '$RepoRoot': $($_.Exception.Message)"
    }
    if ($proc.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($stdout)) {
        throw "ATTR3_FIXTURE_GIT_UNAVAILABLE could not hash-object (via stdin) '$RelativePath' in '$RepoRoot': $stderr"
    }
    $stdout.Trim().ToLowerInvariant()
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
    no reviewed commit ever produced. This compares git's blob hash of the file on disk to
    `git rev-parse HEAD:<repo-relative path>`.
    sol, PR #140 r2 BLOCKER (ATTR3-ADMIT-CONTENT-PIN-1): without -RepoRoot this discovered the
    repository from the FILE'S OWN DIRECTORY via `git rev-parse --show-toplevel` -- so a nested
    repository committed under the fixture's own directory could authorize bytes the OUTER
    repository's HEAD never held. Every fixture-admission caller (Test-UmRunFixtureContentPin in
    tools/profiling/UmRunDrop.psm1, and tools/profiling/bachelor/attr3-stage-fixture-job.ps1's
    own generator-time check) now passes its trusted -RepoRoot; the discovered repository must
    resolve to EXACTLY that root (case-insensitive, full-path normalised) or this throws
    ATTR3_FIXTURE_FOREIGN_REPO, never merely trusting whichever .git happens to be nearest.
    Callers that omit -RepoRoot (this function's generic "is a tracked source file unmodified"
    use, unrelated to the measurement-fixture admission surface) keep the original nearest-repo
    behaviour -- the trusted-root check is opt-in via -RepoRoot, not universal.
    KNOWN LIMITATION (round-2 recon): neither side of the root comparison canonicalises an 8.3
    short name or a junction/symlink component; an equivalent root reached through one of those
    can be falsely rejected as ATTR3_FIXTURE_FOREIGN_REPO rather than accepted. Undefended here --
    callers that might pass such a root should resolve it themselves first -- but no longer
    untested: test_a_root_reached_through_a_junction_is_falsely_refused_known_limitation
    (tools/repo_hygiene/test_playback_attr_3_cuda_behaviour.py) pins the refusal empirically, so a
    future change cannot silently start accepting -- or silently start crashing on -- a
    junction-reached root without that test being touched.
    .PARAMETER Sha256Pin
    Optional [ref]; ATTR3-ADMIT-CONTENT-PIN-1 round 2d (sol BLOCKER / fable MAJOR, PR #140 r2c).
    On a verified match, .Value is set to a SHA256 of the EXACT SAME byte buffer this function
    hashed to produce the git blob comparison below -- one file handle, opened deny-write for its
    whole lifetime and read once, feeds both hashes. The old shape (this function's own `git
    hash-object` subprocess, then a caller's SEPARATE Get-FileHash afterward) read the mutable
    path twice; bytes exchanged in that gap became the trusted pin, and stayed the trusted pin for
    every downstream binding this module added, because nothing downstream ever saw the original
    bytes to compare against. Left unset (the default) when this throws, since a caller must never
    bind to a pin taken from bytes that failed verification.
    Throws with a distinguishable ATTR3_FIXTURE_* token; returns the verified (matching) hash.
    round 2e (sol/fable MAJOR): every throw here now lands in exactly one of DEFINITE REFUSAL
    (ATTR3_FIXTURE_MISSING, ATTR3_FIXTURE_NOT_IN_A_REPO, ATTR3_FIXTURE_FOREIGN_REPO,
    ATTR3_FIXTURE_NOT_COMMITTED, ATTR3_FIXTURE_WORKING_TREE_DIRTY -- a content verdict this
    function stands behind) or COULD NOT DETERMINE (ATTR3_FIXTURE_GIT_UNAVAILABLE,
    ATTR3_FIXTURE_CONTENT_PIN_UNBINDABLE, ATTR3_FIXTURE_HEAD_LOOKUP_UNAVAILABLE -- an
    environmental/operational failure that says nothing about the bytes), never untyped and
    never folded into the wrong bucket; see UmRunDrop.psm1's $UmRunIndeterminateAdmissionTokens,
    which every one of the second group is registered in. The `git rev-parse HEAD:<path>` call
    below used to throw ATTR3_FIXTURE_NOT_COMMITTED for ANY failure there (non-zero exit or empty
    stdout), which classified a corrupted object store, a git I/O error or an unexpected git
    version's message identically to a genuinely untracked path; stderr is now inspected, and only
    git's own distinct messages for an actually-untracked path -- "path does not exist in
    <tree-ish>" (never existed at that path), "exists on disk, but not in '<tree-ish>'"
    (untracked working-tree file), or "invalid object name 'HEAD'" (unborn HEAD, no commits at
    all) -- are treated as the definite refusal; everything else is
    ATTR3_FIXTURE_HEAD_LOOKUP_UNAVAILABLE.
    round 2g (sol/fable MAJOR): the repository-DISCOVERY call (`git rev-parse --show-toplevel`,
    below) had the identical any-failure-becomes-a-verdict defect and is fixed the same way --
    stderr inspected, only git's own "not a git repository" text is the definite
    ATTR3_FIXTURE_NOT_IN_A_REPO refusal, everything else (dubious ownership, a corrupted repo
    config, an I/O error) is ATTR3_FIXTURE_GIT_UNAVAILABLE. Two smaller edges are KNOWN and left
    OPEN, not fixed here: the HEAD:<path> lookup's stderr match for "invalid object name" is
    unanchored and could in principle match a corrupted-ref message that is not actually an unborn
    HEAD; and a fixture between int32.MaxValue and the practical process-memory ceiling can OOM the
    `byte[]` allocation below and surface as a raw, untokened exception rather than
    ATTR3_FIXTURE_CONTENT_PIN_UNBINDABLE.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [string]$RepoRoot = '',
        [ref]$Sha256Pin
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "ATTR3_FIXTURE_MISSING $Path"
    }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "ATTR3_FIXTURE_GIT_UNAVAILABLE git is required to verify $Path against its committed blob"
    }
    $full = [IO.Path]::GetFullPath($Path)
    $dir = [IO.Path]::GetDirectoryName($full)
    $trustedRoot = if ([string]::IsNullOrWhiteSpace($RepoRoot)) { '' } else { ([IO.Path]::GetFullPath($RepoRoot)).TrimEnd('\') }
    if ($trustedRoot -and -not $full.StartsWith($trustedRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "ATTR3_FIXTURE_NOT_IN_A_REPO $full does not resolve under the trusted repository root $trustedRoot"
    }
    # round 2g (sol/fable MAJOR): this used to discard stderr (2>$null) and fold EVERY failure --
    # dubious ownership, a corrupted repo config, a git I/O error -- into the definite refusal
    # ATTR3_FIXTURE_NOT_IN_A_REPO, exactly the any-failure-becomes-a-verdict shape the HEAD:<path>
    # lookup below was already fixed for in round 2e. stderr is now inspected the same way: only
    # git's own distinct "not a git repository" text is a genuine not-in-a-repo verdict; every other
    # failure is ATTR3_FIXTURE_GIT_UNAVAILABLE (already a registered indeterminate token), not a
    # content verdict.
    $rawShowToplevel = & git -C $dir rev-parse --show-toplevel 2>&1
    $discoveredRoot = (($rawShowToplevel | Where-Object { $_ -is [string] }) -join '').Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($discoveredRoot)) {
        $stderrText = (($rawShowToplevel | Where-Object { $_ -is [Management.Automation.ErrorRecord] } |
            ForEach-Object { $_.ToString() }) -join ' ')
        if ($stderrText -match 'not a git repository') {
            throw "ATTR3_FIXTURE_NOT_IN_A_REPO $full is not inside a git working tree: $stderrText"
        }
        throw "ATTR3_FIXTURE_GIT_UNAVAILABLE git rev-parse --show-toplevel for '$dir' failed for a reason other than the path not being inside a git working tree: $stderrText"
    }
    $discoveredRoot = (($discoveredRoot.Trim()) -replace '/', '\').TrimEnd('\')
    if ($trustedRoot) {
        if (-not [string]::Equals($discoveredRoot, $trustedRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "ATTR3_FIXTURE_FOREIGN_REPO $full resolves to git repository '$discoveredRoot', not the trusted root '$trustedRoot' -- a nested repository cannot authorize this fixture's bytes"
        }
        $repoRootForGit = $trustedRoot
    } else {
        if (-not $full.StartsWith($discoveredRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "ATTR3_FIXTURE_NOT_IN_A_REPO $full does not resolve under its own repository root $discoveredRoot"
        }
        $repoRootForGit = $discoveredRoot
    }
    $relative = ($full.Substring($repoRootForGit.Length + 1)) -replace '\\', '/'

    # ATTR3-ADMIT-CONTENT-PIN-1 round 2d: ONE handle, opened deny-write for its whole lifetime, is
    # read ONCE into a buffer. Both the working-tree comparison hash below and -Sha256Pin's value
    # (on success) are derived from that SAME buffer -- there is no internal gap left for a swap
    # to win. FileShare.Read denies any concurrent writer for as long as this handle stays open
    # (and, incidentally, blocks a rename-based swap too, since that needs delete access we never
    # grant).
    try {
        $stream = [IO.File]::Open($full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    } catch {
        throw "ATTR3_FIXTURE_CONTENT_PIN_UNBINDABLE could not open $full for a write-denying read: $($_.Exception.Message)"
    }
    try {
        if ($stream.Length -gt [int32]::MaxValue) {
            throw "ATTR3_FIXTURE_CONTENT_PIN_UNBINDABLE $full is too large ($($stream.Length) bytes) to hash from a single in-memory buffer"
        }
        $bytes = [byte[]]::new([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $read = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($read -le 0) {
                throw "ATTR3_FIXTURE_CONTENT_PIN_UNBINDABLE ${full}: read stopped after $offset of $($bytes.Length) bytes"
            }
            $offset += $read
        }
    } finally {
        $stream.Dispose()
    }
    $workingHash = Get-AttrCudaGitBlobHashFromBytes -Bytes $bytes -RepoRoot $repoRootForGit -RelativePath $relative

    # round 2e (sol/fable, PR #140): stderr is captured (2>&1, not discarded) so a genuine "this
    # path was never committed" refusal -- git's own distinct fatal text for a tree lookup that
    # otherwise ran fine -- can be told apart from ANY OTHER git failure here (a corrupted object
    # store, an I/O error, an unexpected git-version message). Only the former is a verdict this
    # function can stand behind as a definite refusal; everything else fails toward "could not
    # determine" rather than silently becoming the same refusal as a genuinely untracked file.
    $rawHeadLookup = & git -C $repoRootForGit rev-parse "HEAD:$relative" 2>&1
    $committedHash = (($rawHeadLookup | Where-Object { $_ -is [string] }) -join '').Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($committedHash)) {
        $stderrText = (($rawHeadLookup | Where-Object { $_ -is [Management.Automation.ErrorRecord] } |
            ForEach-Object { $_.ToString() }) -join ' ')
        if ($stderrText -match 'does not exist in|exists on disk, but not in|invalid object name') {
            throw "ATTR3_FIXTURE_NOT_COMMITTED HEAD:$relative could not be resolved in $repoRootForGit"
        }
        throw "ATTR3_FIXTURE_HEAD_LOOKUP_UNAVAILABLE HEAD:$relative lookup in $repoRootForGit failed for a reason other than the path never having been committed: $stderrText"
    }
    $committedHash = $committedHash.Trim().ToLowerInvariant()
    if ($workingHash -ne $committedHash) {
        throw "ATTR3_FIXTURE_WORKING_TREE_DIRTY $relative working-tree blob $workingHash differs from the committed blob $committedHash"
    }
    if ($null -ne $Sha256Pin) {
        $sha256 = [Security.Cryptography.SHA256]::Create()
        try {
            $Sha256Pin.Value = (($sha256.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }) -join '')
        } finally {
            $sha256.Dispose()
        }
    }
    $workingHash
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
    Assert-AttrCudaWritableFileSlot, `
    Assert-AttrCudaNonOverwritingFileSlot, `
    Publish-AttrCudaText, `
    Publish-AttrCudaFileCopy, `
    Publish-AttrCudaFileMove, `
    Publish-AttrCudaFileMoveNonOverwriting, `
    New-AttrCudaDirectory, `
    Remove-AttrCudaPartialFile, `
    Assert-AttrCudaNoLinkBelowRoot, `
    Remove-AttrCudaTree, `
    Resolve-AttrCudaSmokeRunLog, `
    Get-AttrCudaLastEligibilityLine, `
    Get-AttrCudaEligibilityVerdict
