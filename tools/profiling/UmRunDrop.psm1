# UmRunDrop.psm1 -- how tools/profiling/um-run.ps1 places side-files and a job into a file-drop
# agent's inbox. Kept in a module so tests can drive the exact code with an observing or faulty
# copier (sol, PR #135 r1: final-state assertions could not prove share verification or ordering).
#
# Invariants:
#   - a side-file name is a plain basename with an allowlisted extension, no trailing dot/space,
#     not a Windows device name, and never job-shaped; after Windows path normalisation it must
#     still be exactly that name (sol r1 BLOCKER: 'evil.job.ps1.' resolves to 'evil.job.ps1');
#   - every temporary name is unique per submission (GUID), so concurrent submitters never share
#     a .sidepart or .job.tmp;
#   - bytes are verified by re-reading the temporary copy FROM THE SHARE before it is renamed;
#   - renames never overwrite: a destination that appeared concurrently makes the rename fail, and
#     the result is accepted only if that destination already holds identical bytes (side-file) --
#     a job file is never replaced;
#   - every side-file is in place before the job is dropped;
#   - a tracked fixture clip is admitted only when its WORKING-TREE bytes are exactly the committed
#     blob at HEAD, never by name/tracked-status alone (ATTR3-ADMIT-CONTENT-PIN-1, fable key on
#     PR #137) -- see Test-UmRunFixtureContentPin.

Set-StrictMode -Version Latest

$script:AllowedSideFileExtensions = @('.zip', '.json', '.exe', '.dll', '.txt', '.csv')
$script:DeviceNames = @('CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9')
# The clip fixtures, by STEM. sol, PR #137 r2 BLOCKER: "tracked under tests/fixtures/clips" admits
# every tracked file there, including that directory's README -- and a discovery helper that picked
# the smallest tracked file then proved the bypass rather than the feature. The same two stems the
# attribution generator accepts as -ClipId are the admissible set here, and no extension is named.
$script:TrackedFixtureClipStems = @('tiny_dual_iso', 'large_dual_iso')
# tools/profiling/bachelor/AttrCudaArtifacts.psm1's Assert-AttrCudaFixtureCommittedBytes -- the twin
# check ATTR3-FIXTURE-STAGE-1 already wrote for this identical defect class -- is reused by
# Test-UmRunFixtureContentPin below, imported ON DEMAND from this path so a host that never ships
# the bachelor module still runs every other um-run side-file rule unchanged.
$script:AttrCudaArtifactsModulePath = Join-Path (Join-Path $PSScriptRoot 'bachelor') 'AttrCudaArtifacts.psm1'

function Test-UmRunTrackedFixtureSource {
    <#
    .SYNOPSIS
    True when a side-file's SOURCE is a repository fixture clip, i.e. it sits in a
    `tests/fixtures/clips` directory.
    .DESCRIPTION
    ATTR3-FIXTURE-REHEARSAL-1 has to stage a tracked fixture clip onto a measurement host. Its media
    extension is deliberately NOT added to $AllowedSideFileExtensions: the board's NA-4 gate refuses a
    bare media-extension token in a tools file without an authorization, and composing that token from
    pieces to satisfy the gate's text scan would be routing around a guard rather than meeting it.
    The admissible property is not the extension anyway -- it is that the bytes are a TRACKED FIXTURE
    in THIS repository, which is exactly what NA-4 itself admits. So that is what this tests.

    sol, PR #137 r1 BLOCKER: a lexical segment match admitted any lookalike `tests\fixtures\clips`
    tree anywhere on the machine, including a UNC share and a path THROUGH a junction. Admission is
    therefore anchored to the repository this module ships in, and compared on REAL paths: the
    source's directory, with links resolved, must be the resolved `<repoRoot>\tests\fixtures\clips`
    itself, and the file must be tracked there by git.

    ATTR3-ADMIT-CONTENT-PIN-1 (fable key on PR #137): being tracked under that name says nothing
    about whether the WORKING-TREE bytes at that path are still the committed ones -- a working
    copy overwritten with foreign bytes was still admitted and staged onto the measurement host
    under the fixture's name. Test-UmRunFixtureContentPin closes that: admission now requires the
    bytes on disk to equal the committed blob at HEAD, and fails closed (git missing, not a repo,
    untracked/absent from HEAD, or a bytes mismatch) rather than admitting on name alone.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        # The repository this module ships in: tools\profiling\UmRunDrop.psm1 -> two levels up.
        [string]$RepoRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
    )

    $fixturesDir = Join-Path (Join-Path (Join-Path $RepoRoot 'tests') 'fixtures') 'clips'
    if (-not (Test-Path -LiteralPath $fixturesDir -PathType Container)) { return $false }
    if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) { return $false }

    # Real paths, so a junction/symlink cannot present an outside file as a fixture.
    $resolvedDir = Resolve-UmRunRealDirectory -Path $fixturesDir
    $sourceItem = Get-Item -LiteralPath $SourcePath -Force
    $link = $sourceItem.ResolveLinkTarget($true)
    if ($null -ne $link) { $sourceItem = $link }
    $sourceDir = Resolve-UmRunRealDirectory -Path ([IO.Path]::GetDirectoryName($sourceItem.FullName))
    if (-not [string]::Equals($sourceDir, $resolvedDir, [StringComparison]::OrdinalIgnoreCase)) { return $false }

    # A CLIP fixture, not merely a tracked file in that directory (sol r2: README.md is tracked there).
    $name = [IO.Path]::GetFileName($sourceItem.FullName)
    $stem = [IO.Path]::GetFileNameWithoutExtension($name)
    if ($script:TrackedFixtureClipStems -cnotcontains $stem) { return $false }

    # Tracked AND content-pinned: git rev-parse HEAD:<path> (inside Test-UmRunFixtureContentPin)
    # fails the same way for "never committed" and "committed but the working copy has drifted", so
    # a single fail-closed call covers both -- see that function for the distinct thrown reasons.
    try {
        Test-UmRunFixtureContentPin -Path $sourceItem.FullName
    } catch {
        Write-Verbose $_.Exception.Message
        return $false
    }
    return $true
}

function Test-UmRunFixtureContentPin {
    <#
    .SYNOPSIS
    Throw unless a fixture's working-tree bytes are exactly its committed blob at HEAD.
    .DESCRIPTION
    ATTR3-ADMIT-CONTENT-PIN-1. Reuses tools/profiling/bachelor/AttrCudaArtifacts.psm1's
    Assert-AttrCudaFixtureCommittedBytes -- the twin check ATTR3-FIXTURE-STAGE-1 already wrote for
    the identical defect class, comparing `git hash-object` of the working-tree file to
    `git rev-parse HEAD:<repo-relative path>` -- rather than re-deriving the same comparison here
    and letting the two drift. The bachelor module is imported ON DEMAND, and only if present, so a
    host that never ships it still runs every OTHER um-run side-file rule unchanged: it simply
    cannot admit a tracked fixture by content, and this fails closed instead of admitting on name
    alone. Deliberately NO -Force: a caller (attr3-stage-fixture-job.ps1) already imports this same
    module itself before calling Test-UmRunTrackedFixtureSource, and Import-Module -Force first
    REMOVES any existing same-named module from the whole session -- unbinding it from that
    caller's own scope, not just this one -- before rebinding it here alone. Plain Import-Module is
    a no-op when the module is already loaded, so the caller's binding survives untouched.
    Throws UMRUN_FIXTURE_CONTENT_PIN_UNAVAILABLE when the bachelor module is not present, or one of
    Assert-AttrCudaFixtureCommittedBytes's own distinct tokens: ATTR3_FIXTURE_GIT_UNAVAILABLE (git
    missing), ATTR3_FIXTURE_NOT_IN_A_REPO (not inside a repo), ATTR3_FIXTURE_NOT_COMMITTED
    (untracked or absent from HEAD), or ATTR3_FIXTURE_WORKING_TREE_DIRTY (bytes differ).
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $script:AttrCudaArtifactsModulePath -PathType Leaf)) {
        throw "UMRUN_FIXTURE_CONTENT_PIN_UNAVAILABLE $($script:AttrCudaArtifactsModulePath) is not present; cannot verify '$Path' against its committed blob"
    }
    Import-Module $script:AttrCudaArtifactsModulePath -ErrorAction Stop
    [void](Assert-AttrCudaFixtureCommittedBytes -Path $Path)
}

function Resolve-UmRunRealDirectory {
    <# Full path of a directory with every link in the chain resolved; '' when it does not exist. #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) { return '' }
    $link = $item.ResolveLinkTarget($true)
    if ($null -ne $link) { $item = $link }
    return $item.FullName.TrimEnd('\')
}

function Assert-UmRunSideFileName {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Inbox,
        [string]$SourcePath = ''
    )

    if ($Name -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)+$') {
        throw "UMRUN_SIDEFILE_NAME_INVALID '$Name' is not a plain basename (letters, digits, '_', '-', single dots between parts)"
    }
    if ($Name -match '\.job\.' -or $Name -match '\.(sidepart|tmp|ps1|psm1|psd1|bat|cmd|vbs|js)$') {
        throw "UMRUN_SIDEFILE_NAME_INVALID '$Name' is job-shaped or executable-script-shaped"
    }
    $extension = [IO.Path]::GetExtension($Name).ToLowerInvariant()
    $trackedFixture = $SourcePath -and (Test-UmRunTrackedFixtureSource -SourcePath $SourcePath)
    if (-not $trackedFixture -and $script:AllowedSideFileExtensions -notcontains $extension) {
        throw "UMRUN_SIDEFILE_NAME_INVALID '$Name' extension '$extension' is not in the allowlist and its source is not a tracked fixture clip"
    }
    $stem = $Name.Split('.')[0].ToUpperInvariant()
    if ($script:DeviceNames -contains $stem) {
        throw "UMRUN_SIDEFILE_NAME_INVALID '$Name' is a Windows device name"
    }
    $full = [IO.Path]::GetFullPath((Join-Path $Inbox $Name))
    if ([IO.Path]::GetFileName($full) -cne $Name -or
        -not [string]::Equals([IO.Path]::GetDirectoryName($full).TrimEnd('\'), [IO.Path]::GetFullPath($Inbox).TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
        throw "UMRUN_SIDEFILE_NAME_INVALID '$Name' does not normalise to itself directly inside the inbox"
    }
    return $full
}

function Invoke-UmRunDrop {
    <#
    .SYNOPSIS
    Place verified side-files, then the job, into an agent inbox. Returns the job id.
    .PARAMETER Copier
    Performs one copy: & $Copier <source> <destination>. Defaults to Copy-Item. Tests pass an
    observing or faulty copier; production never does.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Inbox,
        [Parameter(Mandatory = $true)][string]$Outbox,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [string]$JobId = '',
        [string[]]$SideFile = @(),
        [scriptblock]$Copier = { param($Source, $Destination) Copy-Item -LiteralPath $Source -Destination $Destination }
    )

    if ($JobId -and $JobId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { throw "UMRUN_JOBID_INVALID '$JobId'" }
    if ($JobId -and $JobId.EndsWith('.')) { throw "UMRUN_JOBID_INVALID '$JobId' ends with a dot" }
    $id = if ($JobId) { $JobId } else { "job_{0}_{1}" -f (Get-Date -Format 'yyyyMMdd_HHmmss'), ([guid]::NewGuid().ToString('N').Substring(0, 8)) }
    if (Test-Path -LiteralPath (Join-Path $Outbox "$id.result.json")) {
        throw "UMRUN_JOBID_IN_USE outbox already holds $id.result.json"
    }
    $final = Join-Path $Inbox "$id.job.ps1"
    if (Test-Path -LiteralPath $final) { throw "UMRUN_JOBID_IN_USE inbox already holds $id.job.ps1" }
    if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) { throw "UMRUN_SCRIPT_MISSING $ScriptPath" }

    $nonce = [guid]::NewGuid().ToString('N')
    foreach ($path in @($SideFile | ForEach-Object { $_ -split ';' } | Where-Object { $_ })) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "UMRUN_SIDEFILE_MISSING $path" }
        $sourceItem = Get-Item -LiteralPath $path -Force
        $name = $sourceItem.Name
        # The name must be the file's own name AND survive validation; a trailing-dot alias passed
        # as a path reports its canonical Name here, and the literal argument is compared too.
        if ([IO.Path]::GetFileName($path) -cne $name) {
            throw "UMRUN_SIDEFILE_NAME_INVALID '$([IO.Path]::GetFileName($path))' is an alias of '$name'"
        }
        $destination = Assert-UmRunSideFileName -Name $name -Inbox $Inbox -SourcePath $sourceItem.FullName
        $localSha = (Get-FileHash -LiteralPath $sourceItem.FullName -Algorithm SHA256).Hash

        if (Test-Path -LiteralPath $destination) {
            $existing = Get-Item -LiteralPath $destination -Force
            if (-not $existing.PSIsContainer -and (($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) -and
                (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -eq $localSha) {
                Write-Output "side-file already present with matching sha256: $name"
                continue
            }
            throw "UMRUN_SIDEFILE_CONFLICT inbox\$name already exists with DIFFERENT content or is not a plain file"
        }

        $part = Join-Path $Inbox "$name.$nonce.sidepart"
        try {
            & $Copier $sourceItem.FullName $part
            $remoteSha = (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash
            if ($remoteSha -ne $localSha) {
                throw "UMRUN_SIDEFILE_VERIFY_FAILED $name did not round-trip to the share (local $localSha, share $remoteSha)"
            }
            try {
                Move-Item -LiteralPath $part -Destination $destination -ErrorAction Stop   # no -Force: never overwrite
            } catch {
                if ((Test-Path -LiteralPath $destination) -and (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -eq $localSha) {
                    Write-Output "side-file placed concurrently with matching sha256: $name"
                } else {
                    throw "UMRUN_SIDEFILE_CONFLICT inbox\$name appeared concurrently with different content"
                }
            }
        } finally {
            if (Test-Path -LiteralPath $part) { Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue }
        }
        Write-Output ("side-file placed: {0} sha256={1}" -f $name, $localSha.ToLowerInvariant())
    }

    $tmp = Join-Path $Inbox "$id.$nonce.job.tmp"
    try {
        & $Copier $ScriptPath $tmp
        try {
            Move-Item -LiteralPath $tmp -Destination $final -ErrorAction Stop   # no -Force: a job is never replaced
        } catch {
            throw "UMRUN_JOBID_IN_USE inbox\$id.job.ps1 appeared concurrently; refusing to replace it"
        }
    } finally {
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    }
    Write-Output "submitted $id -> $final"
    Write-Output "UMRUN_JOBID=$id"
}

Export-ModuleMember -Function Assert-UmRunSideFileName, Test-UmRunTrackedFixtureSource, Resolve-UmRunRealDirectory, Invoke-UmRunDrop
