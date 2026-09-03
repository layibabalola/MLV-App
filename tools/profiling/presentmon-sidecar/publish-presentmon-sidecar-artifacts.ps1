[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string[]] $AppTelemetryPath,
    [Parameter(Mandatory)] [string] $SidecarResultPath,
    [Parameter(Mandatory)] [string] $PublicationRoot
)

$ErrorActionPreference = 'Stop'
[IO.Directory]::CreateDirectory($PublicationRoot) | Out-Null
$copied = [Collections.Generic.List[string]]::new()
$missing = [Collections.Generic.List[string]]::new()
foreach ($path in $AppTelemetryPath) {
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        $destination = Join-Path $PublicationRoot ([IO.Path]::GetFileName($path))
        Copy-Item -LiteralPath $path -Destination $destination -Force
        $copied.Add($destination)
    } else {
        $missing.Add($path)
    }
}

$sidecarReason = 'sidecar_result_missing'
$sidecarStatus = 'missing'
$rateClaimsAdmissible = $false
if (Test-Path -LiteralPath $SidecarResultPath -PathType Leaf) {
    $sidecar = Get-Content -LiteralPath $SidecarResultPath -Raw -Encoding utf8 | ConvertFrom-Json
    $sidecarReason = [string]$sidecar.reason
    $sidecarStatus = [string]$sidecar.status
    $rateClaimsAdmissible = [bool]$sidecar.rateClaimsAdmissible
    $destination = Join-Path $PublicationRoot ([IO.Path]::GetFileName($SidecarResultPath))
    Copy-Item -LiteralPath $SidecarResultPath -Destination $destination -Force
}
if ($missing.Count -gt 0) {
    $rateClaimsAdmissible = $false
    $sidecarReason = 'app_telemetry_missing:' + ($missing -join ',')
}

$manifest = [ordered]@{
    schema = 'mlvapp.presentmon-sidecar-publication.v1'
    publishedUtc = [DateTimeOffset]::UtcNow.ToString('o')
    appTelemetryCopied = @($copied)
    appTelemetryMissing = @($missing)
    sidecarStatus = $sidecarStatus
    rateClaimsAdmissible = $rateClaimsAdmissible
    rateClaimsInadmissibleReason = if ($rateClaimsAdmissible) { $null } else { $sidecarReason }
}
$manifestPath = Join-Path $PublicationRoot 'presentmon-sidecar-publication.json'
[IO.File]::WriteAllText($manifestPath, (($manifest | ConvertTo-Json -Depth 10) + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
$manifest | ConvertTo-Json -Depth 10
