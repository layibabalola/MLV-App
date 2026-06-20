# Shipping guard smoke -- the manifest-driven, tiered coverage gate.
#
# Reads the tracked, verified-only clip manifest (tools\profiling\smoke-clip-manifest.json) and runs
# the coverage appropriate to the requested tier. Passes only if every gate that ran passed.
#
#   -Tier fast     (default, per-change): the fast Dual-ISO P3 recon clip + the preferred
#                  non-Dual-ISO pixel guard.
#   -Tier nightly  (release/nightly): the verified releaseNightlyDualIso sample (deterministic) run
#                  through the Dual-ISO P3 validator + the preferred non-Dual-ISO guard.
#   -Tier deep     (occasional sweep): every verified Dual-ISO entry + every non-Dual-ISO guard
#                  entry; records a terminal state per clip.
#
# Coverage truth: Dual-ISO smoke alone only proves the scoped Dual-ISO GPU recon path. A broad
# end-user / shipping / hardening claim requires the non-Dual-ISO pixel guard to be green as well --
# this gate keeps the two verdicts distinct so a claim can cite exactly what was covered.
#
# Gates:
#   Dual-ISO     -> tools\profiling\run-ultramagnus-p3-validation.ps1  (needs UltraMagnus + 4090 + build)
#   Non-Dual-ISO -> tools\profiling\run-non-dual-iso-guard-smoke.ps1   (needs the clip reachable)
#
# EXIT CODES
#   0  every gate that ran passed (full requested coverage is green), or -DryRun
#   2  at least one gate failed, or no gate ran

param(
    [string]$RepoRoot = ".",
    [ValidateSet("fast", "nightly", "deep")]
    [string]$Tier = "fast",
    [string]$ManifestPath = "tools\profiling\smoke-clip-manifest.json",
    [switch]$SkipDualIso,
    [switch]$SkipNonDualIso,
    # nightly only: if > 0 and smaller than the verified pool, take a deterministic rotating sample of
    # that many Dual-ISO clips (the chosen set is recorded in the summary). 0 = use the whole tier set.
    [int]$DualIsoSampleSize = 0,
    # Rotation seed for the nightly sample; -1 = derive from today's day-of-year.
    [int]$RotationSeed = -1,
    # Dual-ISO gate pass-through.
    [switch]$SkipBuild,
    [switch]$AllowNonUltraMagnus,
    [switch]$AllowGpuNameMismatch,
    # Non-Dual-ISO gate pass-through.
    [int]$NonDualIsoSeconds = 30,
    [string]$OutputRoot = ".claude-state\profiling\shipping-guard-smoke",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Resolve-RepoPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return (Join-Path $Root $Path)
}

function ConvertTo-SplatLiteral {
    param($Value)
    if ($Value -is [System.Management.Automation.SwitchParameter]) {
        return ('${0}' -f $Value.IsPresent.ToString().ToLowerInvariant())
    }
    if ($Value -is [bool]) {
        return ('${0}' -f $Value.ToString().ToLowerInvariant())
    }
    if ($Value -is [int] -or $Value -is [long] -or $Value -is [double]) {
        return [string]$Value
    }
    if ($Value -is [array]) {
        if ($Value.Count -eq 0) { return "@()" }
        return "@(" + (($Value | ForEach-Object { "'" + ([string]$_ -replace "'", "''") + "'" }) -join ",") + ")"
    }
    return "'" + ([string]$Value -replace "'", "''") + "'"
}

$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$dualIsoScript = Join-Path $repo "tools\profiling\run-ultramagnus-p3-validation.ps1"
$nonDualIsoScript = Join-Path $repo "tools\profiling\run-non-dual-iso-guard-smoke.ps1"
$manifestFull = Resolve-RepoPath -Root $repo -Path $ManifestPath

$outDir = Resolve-RepoPath -Root $repo -Path $OutputRoot
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$invokeDir = Join-Path $outDir "_invoke"
New-Item -ItemType Directory -Force -Path $invokeDir | Out-Null
$summaryPath = Join-Path $outDir "latest.json"

$pwshExe = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
if (-not $pwshExe) { $pwshExe = (Get-Command powershell.exe).Source }

function Invoke-ChildScript {
    param(
        [string]$Tag,
        [string]$Script,
        [hashtable]$ScriptArgs
    )
    if (!(Test-Path -LiteralPath $Script)) {
        return [ordered]@{ status = "failed"; exitCode = $null; detail = "Gate script not found: $Script" }
    }
    $safeTag = ($Tag -replace '[^A-Za-z0-9_.-]', '_')
    $invokeScript = Join-Path $invokeDir ("invoke-" + $safeTag + ".ps1")
    $lines = foreach ($k in $ScriptArgs.Keys) { "    $k = " + (ConvertTo-SplatLiteral $ScriptArgs[$k]) }
    $invokeText = "`$ErrorActionPreference = 'Stop'`n`$p = @{`n" + ($lines -join "`n") + "`n}`n& '$Script' @p`nexit `$LASTEXITCODE`n"
    $invokeText | Set-Content -LiteralPath $invokeScript -Encoding UTF8
    $childOutput = (& $pwshExe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $invokeScript 2>&1 | Out-String)
    $code = $LASTEXITCODE
    # Echo child output to STDERR, never stdout: this gate's stdout must stay parseable JSON (callers
    # pipe it to ConvertFrom-Json), and Write-Host on a captured child process leaks into stdout.
    if ($childOutput.Trim()) { [Console]::Error.WriteLine($childOutput.Trim()) }
    return [ordered]@{
        status = if ($code -eq 0) { "success" } else { "failed" }
        exitCode = $code
        detail = $null
    }
}

# --- Read manifest + compute the tier selection (always, even for -DryRun/-Skip*) --------------
if (!(Test-Path -LiteralPath $manifestFull)) {
    Write-Error "Manifest not found: $manifestFull"
    exit 2
}
$manifest = Get-Content -LiteralPath $manifestFull -Raw | ConvertFrom-Json
$fastDual = @($manifest.clipSets.fastDualIsoP3)
$nightlyDual = @($manifest.clipSets.releaseNightlyDualIso)
$nonDualAll = @($manifest.clipSets.nonDualIsoGuard)

$preferredNonDual = @($nonDualAll | Where-Object { $_.preferred })
if ($preferredNonDual.Count -eq 0) { $preferredNonDual = @($nonDualAll | Select-Object -First 1) }

# Dual-ISO selection. The fast tier uses the P3 validator's own default clip (M16-1327); nightly/deep
# pass explicit representative .MLV paths from the verified pool.
$dualUseP3Default = $false
$dualSelected = @()
switch ($Tier) {
    "fast" {
        $dualUseP3Default = $true
        $dualSelected = @($fastDual | ForEach-Object { [ordered]@{ id = $_.id; path = $_.canonicalUncPath } })
    }
    "nightly" {
        $pool = $nightlyDual
        if ($DualIsoSampleSize -gt 0 -and $DualIsoSampleSize -lt $pool.Count) {
            $seed = if ($RotationSeed -ge 0) { $RotationSeed } else { (Get-Date).DayOfYear }
            $start = $seed % $pool.Count
            $idx = 0..($DualIsoSampleSize - 1) | ForEach-Object { ($start + $_) % $pool.Count }
            $pool = @($idx | ForEach-Object { $nightlyDual[$_] })
        }
        $dualSelected = @($pool | ForEach-Object { [ordered]@{ id = $_.id; path = $_.path } })
    }
    "deep" {
        $dualSelected = @($nightlyDual | ForEach-Object { [ordered]@{ id = $_.id; path = $_.path } })
    }
}

# Non-Dual-ISO selection: fast/nightly use the preferred guard clip; deep uses every guard entry.
$nonDualSelected = if ($Tier -eq "deep") {
    @($nonDualAll | ForEach-Object { [ordered]@{ id = $_.id; path = $_.path } })
}
else {
    @($preferredNonDual | ForEach-Object { [ordered]@{ id = $_.id; path = $_.path } })
}

$gates = [System.Collections.Generic.List[object]]::new()

# --- Dry run: record the plan, launch nothing -------------------------------------------------
if ($DryRun) {
    $gates.Add([ordered]@{ name = "dual-iso"; tier = $Tier; status = "planned"; clips = @($dualSelected); p3Default = $dualUseP3Default })
    $gates.Add([ordered]@{ name = "non-dual-iso"; tier = $Tier; status = "planned"; clips = @($nonDualSelected) })
    $summary = [ordered]@{
        schema = "mlvapp-shipping-guard-smoke.v2"
        status = "planned"
        tier = $Tier
        repoRoot = $repo
        manifest = $manifestFull
        dryRun = $true
        gates = @($gates)
    }
    $summary | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
    $summary | ConvertTo-Json -Depth 8
    exit 0
}

# --- Dual-ISO gate (one P3 invocation covering all selected clips) ----------------------------
if ($SkipDualIso) {
    $gates.Add([ordered]@{ name = "dual-iso"; tier = $Tier; status = "skipped"; clips = @($dualSelected); detail = "-SkipDualIso" })
}
else {
    $dualArgs = @{
        RepoRoot = $repo
        SkipBuild = $SkipBuild
        AllowNonUltraMagnus = $AllowNonUltraMagnus
        AllowGpuNameMismatch = $AllowGpuNameMismatch
        OutputRoot = (Join-Path $OutputRoot ("$Tier\dual-iso"))
    }
    if (-not $dualUseP3Default) {
        $dualArgs.ClipPaths = @($dualSelected | ForEach-Object { $_.path })
    }
    $res = Invoke-ChildScript -Tag "dual-iso-$Tier" -Script $dualIsoScript -ScriptArgs $dualArgs
    $gates.Add([ordered]@{
        name = "dual-iso"; tier = $Tier; status = $res.status; exitCode = $res.exitCode
        clips = @($dualSelected); p3Default = $dualUseP3Default; detail = $res.detail
    })
}

# --- Non-Dual-ISO gate (one guard invocation per selected clip, distinct verdicts) ------------
if ($SkipNonDualIso) {
    $gates.Add([ordered]@{ name = "non-dual-iso"; tier = $Tier; status = "skipped"; clips = @($nonDualSelected); detail = "-SkipNonDualIso" })
}
else {
    foreach ($nd in $nonDualSelected) {
        $ndArgs = @{
            RepoRoot = $repo
            ClipPath = $nd.path
            Seconds = $NonDualIsoSeconds
            OutputRoot = (Join-Path $OutputRoot ("$Tier\non-dual-iso\" + $nd.id))
        }
        $res = Invoke-ChildScript -Tag "non-dual-$($nd.id)" -Script $nonDualIsoScript -ScriptArgs $ndArgs
        $gates.Add([ordered]@{
            name = "non-dual-iso"; tier = $Tier; clipId = $nd.id; clipPath = $nd.path
            status = $res.status; exitCode = $res.exitCode; detail = $res.detail
        })
    }
}

$ran = @($gates | Where-Object { $_.status -ne "skipped" })
$failed = @($gates | Where-Object { $_.status -eq "failed" })
$status = if ($ran.Count -eq 0) { "failed" } elseif ($failed.Count -eq 0) { "success" } else { "failed" }

$summary = [ordered]@{
    schema = "mlvapp-shipping-guard-smoke.v2"
    status = $status
    tier = $Tier
    repoRoot = $repo
    manifest = $manifestFull
    dryRun = $false
    gates = @($gates)
    note = "Dual-ISO smoke alone covers only the scoped Dual-ISO GPU path; a broad end-user/shipping claim requires the non-dual-iso gate to be green as well."
}

$summary | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
$summary | ConvertTo-Json -Depth 8

if ($status -eq "success") { exit 0 }
exit 2
