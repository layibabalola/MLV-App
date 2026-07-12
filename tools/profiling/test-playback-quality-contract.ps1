param([string]$RepoRoot = ".")

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $RepoRoot).Path
$smoke = Join-Path $root "tools\profiling\run-release-gui-smoke.ps1"
$pwsh = (Get-Command pwsh.exe -ErrorAction Stop).Source

function Assert-Rejected {
    param(
        [string]$Name,
        [string]$ScriptPath = $smoke,
        [string[]]$Arguments,
        [string]$ExpectedText
    )

    $stdout = Join-Path $env:TEMP ("mlvapp-playback-contract-{0}.out" -f [Guid]::NewGuid())
    $stderr = Join-Path $env:TEMP ("mlvapp-playback-contract-{0}.err" -f [Guid]::NewGuid())
    try {
        $allArgs = @("-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-File", $ScriptPath) + $(if ($ScriptPath -eq $smoke) { @("-RepoRoot", $root) } else { @() }) + $Arguments
        $start = [Diagnostics.ProcessStartInfo]::new()
        $start.FileName = $pwsh
        $start.UseShellExecute = $false
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        foreach ($argument in $allArgs) { [void]$start.ArgumentList.Add($argument) }
        $process = [Diagnostics.Process]::Start($start)
        $outText = $process.StandardOutput.ReadToEndAsync()
        $errText = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        [IO.File]::WriteAllText($stdout, $outText.GetAwaiter().GetResult())
        [IO.File]::WriteAllText($stderr, $errText.GetAwaiter().GetResult())
        $combined = (Get-Content $stdout, $stderr -Raw -ErrorAction SilentlyContinue) -join "`n"
        if ($process.ExitCode -eq 0) {
            throw "$Name unexpectedly succeeded."
        }
        if ($combined -notmatch [Regex]::Escape($ExpectedText)) {
            throw "$Name failed without the expected contract message '$ExpectedText'. Output: $combined"
        }
        Write-Host "[PASS] $Name"
    }
    finally {
        Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    }
}

Assert-Rejected -Name "zero-present requires launch-only declaration" `
    -Arguments @("-AllowZeroPresentedFrames", "-DryRun") `
    -ExpectedText "-AllowZeroPresentedFrames requires -LaunchOnlyProbe"

Assert-Rejected -Name "launch-only declaration requires zero-present exemption" `
    -Arguments @("-LaunchOnlyProbe", "-DryRun") `
    -ExpectedText "-LaunchOnlyProbe requires -AllowZeroPresentedFrames"

Assert-Rejected -Name "launch-only cannot masquerade as frame telemetry" `
    -Arguments @("-LaunchOnlyProbe", "-AllowZeroPresentedFrames", "-FrameTelemetry", "-DryRun") `
    -ExpectedText "-LaunchOnlyProbe cannot be combined with frame telemetry"

Assert-Rejected -Name "launch-only cannot masquerade as lifecycle stress" `
    -Arguments @("-LaunchOnlyProbe", "-AllowZeroPresentedFrames", "-ExerciseClipLifecycleStress", "-DryRun") `
    -ExpectedText "-LaunchOnlyProbe cannot be combined with frame telemetry"

Assert-Rejected -Name "lifecycle discontinuity cannot masquerade as steady cadence" `
    -Arguments @("-ExerciseClipLifecycleStress", "-DetectPlaybackArtifacts", "-DryRun") `
    -ExpectedText "Run lifecycle correctness and uninterrupted cadence as separate legs"

Assert-Rejected -Name "cadence advisory requires artifact detection" `
    -Arguments @("-ArtifactCadenceAdvisory", "-DryRun") `
    -ExpectedText "-ArtifactCadenceAdvisory requires -DetectPlaybackArtifacts"

$invalidSmoke = Join-Path $env:TEMP ("mlvapp-invalid-smoke-{0}.json" -f [Guid]::NewGuid())
try {
    @{
        validation = @{ ok = $false; presentedFrames = 0; launchOnlyProbe = $false }
        log = @{ summary = @{ presented_frames = 0; first_presented_frame = -1; last_presented_frame = -1 } }
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $invalidSmoke -Encoding UTF8
    Assert-Rejected -Name "failed playback leg cannot enter A/B" `
        -ScriptPath (Join-Path $root "tools\profiling\compare-release-gui-smoke-ab.ps1") `
        -Arguments @("-Before", $invalidSmoke, "-After", $invalidSmoke) `
        -ExpectedText "did not independently pass validation"
}
finally {
    Remove-Item -LiteralPath $invalidSmoke -Force -ErrorAction SilentlyContinue
}

Write-Host "[SUMMARY] playback-quality contract tests=7 failed=0"
