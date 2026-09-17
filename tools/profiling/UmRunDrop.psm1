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
#   - every side-file is in place before the job is dropped.

Set-StrictMode -Version Latest

$script:AllowedSideFileExtensions = @('.zip', '.json', '.exe', '.dll', '.txt', '.csv')
$script:DeviceNames = @('CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9')

function Assert-UmRunSideFileName {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Name, [Parameter(Mandatory = $true)][string]$Inbox)

    if ($Name -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)+$') {
        throw "UMRUN_SIDEFILE_NAME_INVALID '$Name' is not a plain basename (letters, digits, '_', '-', single dots between parts)"
    }
    if ($Name -match '\.job\.' -or $Name -match '\.(sidepart|tmp|ps1|psm1|psd1|bat|cmd|vbs|js)$') {
        throw "UMRUN_SIDEFILE_NAME_INVALID '$Name' is job-shaped or executable-script-shaped"
    }
    $extension = [IO.Path]::GetExtension($Name).ToLowerInvariant()
    if ($script:AllowedSideFileExtensions -notcontains $extension) {
        throw "UMRUN_SIDEFILE_NAME_INVALID '$Name' extension '$extension' is not in the allowlist"
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
        $destination = Assert-UmRunSideFileName -Name $name -Inbox $Inbox
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

Export-ModuleMember -Function Assert-UmRunSideFileName, Invoke-UmRunDrop
