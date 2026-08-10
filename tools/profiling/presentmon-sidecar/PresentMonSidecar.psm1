Set-StrictMode -Version Latest

function Get-PresentMonSidecarRequestHash {
    param([Parameter(Mandatory)] $Request)

    $payload = [ordered]@{
        schema = [string]$Request.schema
        requestId = [string]$Request.requestId
        requestedUtc = [string]$Request.requestedUtc
        expiresUtc = [string]$Request.expiresUtc
        processName = [string]$Request.processName
        outputFile = [string]$Request.outputFile
        timedSeconds = [int]$Request.timedSeconds
    }
    $json = $payload | ConvertTo-Json -Compress
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([Convert]::ToHexString($sha.ComputeHash($bytes))).ToUpperInvariant() }
    finally { $sha.Dispose() }
}

function Resolve-PresentMonSidecarOutputPath {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $AllowedRoot
    )

    if (-not [IO.Path]::IsPathFullyQualified($Path) -or $Path.StartsWith('\\')) {
        throw 'output_path_not_fully_qualified'
    }
    $root = [IO.Path]::GetFullPath($AllowedRoot).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $candidate = [IO.Path]::GetFullPath($Path)
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    if (-not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'output_path_outside_allowed_root'
    }
    if ([IO.Path]::GetExtension($candidate) -ine '.csv') {
        throw 'output_path_not_csv'
    }
    return $candidate
}

function Write-PresentMonSidecarJsonAtomic {
    param(
        [Parameter(Mandatory)] $Value,
        [Parameter(Mandatory)] [string] $Path
    )

    $parent = Split-Path -Parent $Path
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $temporary = Join-Path $parent ('.' + [IO.Path]::GetFileName($Path) + '.' + [guid]::NewGuid().ToString('N') + '.tmp')
    $json = $Value | ConvertTo-Json -Depth 20 -Compress
    [IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    try {
        if (Test-Path -LiteralPath $Path) { throw "atomic_target_exists:$Path" }
        [IO.File]::Move($temporary, $Path)
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}

function Read-PresentMonSidecarJson {
    param([Parameter(Mandatory)] [string] $Path)
    # PowerShell otherwise coerces ISO-8601 strings to local DateTime values. That
    # would change the canonical request bytes between publication and validation.
    return (Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json -DateKind String)
}

Export-ModuleMember -Function @(
    'Get-PresentMonSidecarRequestHash',
    'Resolve-PresentMonSidecarOutputPath',
    'Write-PresentMonSidecarJsonAtomic',
    'Read-PresentMonSidecarJson'
)
