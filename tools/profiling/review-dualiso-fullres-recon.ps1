# review-dualiso-fullres-recon.ps1
# Reviewer-side GOLDEN A/B gate for the dual-ISO FULL-RES recon regression (cause #2).
#
# Runs a frame-matched scale=2 (default) vs scale=1 (full-res) live-playback A/B on a
# dual-ISO clip, Look Assist ON, and compares the two grabs of the SAME frame. Because
# the scene content is identical in both legs, content cancels in the ratio and only
# the full-res recon artifact (interlace/mesh + green/magenta speckle) remains. A
# correct fix makes scale=1 look ~as clean as scale=2, so the ratios collapse toward 1
# and the verdict flips CLEAN.
#
# This is CPU-reproducible (no GPU/CUDA needed) -- it runs anywhere, including the Dell
# CPU lane. For the CUDA path specifically, run tools\profiling\run-release-cuda-playback-ab.ps1
# at ScaleFactor=1 on the actual 4090/3060 bench AFTER a verified deploy.
#
# Exit: 0 = CLEAN (no full-res regression signature) ; 1 = ARTIFACT_PRESENT ; 2 = error.
param(
    [string]$RepoRoot = ".",
    [string]$ExePath = "",
    [Alias("Input")]
    [string]$ClipPath = "",
    [int]$StartFrame = 10,
    [int]$Seconds = 3,
    [int]$SettleMs = 3500,
    [string]$OutputRoot = "",
    [double]$HLineRatioThreshold = 3.0,
    [double]$VLineRatioThreshold = 4.0,
    [double]$ChromaRatioThreshold = 3.0,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "provenance-stamp.ps1")

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
if ([string]::IsNullOrWhiteSpace($ExePath)) {
    $ExePath = Join-Path $root "platform\qt\build-release\release\MLVApp.exe"
}
$exe = (Resolve-Path -LiteralPath $ExePath).Path
if ([string]::IsNullOrWhiteSpace($ClipPath)) {
    $ClipPath = Join-Path $root "tests\fixtures\clips\large_dual_iso.mlv"
}
$clip = (Resolve-Path -LiteralPath $ClipPath).Path
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
    $OutputRoot = Join-Path $root ".claude-state\profiling\$stamp-dualiso-fullres-review"
}
$OutputRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputRoot)
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

# Provenance: a quality verdict is worthless if you can't prove which binary ran.
$stampInfo = Get-MlvAppBuildStamp -ExePath $exe
$head = (& git -C $root rev-parse --verify HEAD 2>$null)
if ($head) { $head = $head.Trim() }
if (-not $stampInfo.found) {
    Write-Host "WARNING: exe has no MLVAPP_BUILDSTAMP_v1 provenance field -- verdict cannot cite a commit." -ForegroundColor Yellow
} else {
    Write-Host ("exe embedded SHA: {0} (dirty={1}); HEAD={2}" -f $stampInfo.sha, $stampInfo.dirty, $(if ($head) { $head.Substring(0,12) } else { "?" }))
}

$smoke = Join-Path $PSScriptRoot "run-release-gui-smoke.ps1"
$metricPy = Join-Path $PSScriptRoot "dualiso_fullres_metric.py"
$clipBase = [IO.Path]::GetFileNameWithoutExtension($clip)

if ($DryRun) {
    [pscustomobject]@{
        exe = $exe; embeddedSha = $stampInfo.sha; clip = $clip
        startFrame = $StartFrame; seconds = $Seconds; settleMs = $SettleMs
        scales = @(2, 1); outputRoot = $OutputRoot
        thresholds = [ordered]@{ hlineRatio = $HLineRatioThreshold; vlineRatio = $VLineRatioThreshold; chromaRatio = $ChromaRatioThreshold }
    } | ConvertTo-Json -Depth 5
    exit 0
}

$grabs = [ordered]@{}
foreach ($sf in @(2, 1)) {
    $scaleDir = Join-Path $OutputRoot "scale$sf"
    New-Item -ItemType Directory -Force -Path $scaleDir | Out-Null
    Write-Host ("[A/B] scale=$sf live autoplay grab ...")
    & pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $smoke `
        -RepoRoot $root -ExePath $exe -Input $clip `
        -ScaleFactor $sf -ExpectedScaleRequest $sf `
        -StartFrame $StartFrame -Seconds $Seconds -SettleMs $SettleMs `
        -CaptureScreenshot -SkipWindowScreenshot `
        -Output (Join-Path $scaleDir "result.json") `
        -ScreenshotOutputDir (Join-Path $scaleDir "shots") *> (Join-Path $scaleDir "smoke.log")
    $png = Join-Path $scaleDir "shots\$clipBase.png"
    if (-not (Test-Path -LiteralPath $png -PathType Leaf)) {
        Write-Host "ERROR: scale=$sf produced no screenshot ($png). See $scaleDir\smoke.log" -ForegroundColor Red
        exit 2
    }
    $grabs["scale$sf"] = $png
}

$metricsJson = & py -3 $metricPy $grabs["scale2"] $grabs["scale1"]
$metrics = $metricsJson | ConvertFrom-Json
$m2 = $metrics[0]; $m1 = $metrics[1]
if ($m2.error -or $m1.error) {
    Write-Host "ERROR computing metrics: $($m2.error) $($m1.error)" -ForegroundColor Red
    exit 2
}

$eps = 1e-6
$ratioH = $m1.hline / [Math]::Max($eps, $m2.hline)
$ratioV = $m1.vline / [Math]::Max($eps, $m2.vline)
$ratioC = $m1.chromaSpeckle / [Math]::Max($eps, $m2.chromaSpeckle)
$artifactPresent = ($ratioV -ge $VLineRatioThreshold) -or ($ratioH -ge $HLineRatioThreshold)
$verdict = if ($artifactPresent) { "ARTIFACT_PRESENT" } else { "CLEAN" }

$report = [ordered]@{
    schema = "mlvapp.dualiso-fullres-review.v1"
    verdict = $verdict
    artifactPresent = $artifactPresent
    capturedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    exe = $exe
    embeddedSha = $stampInfo.sha
    embeddedStampFound = $stampInfo.found
    repoHead = $head
    matchesHead = ($stampInfo.found -and $head -and ($stampInfo.sha -eq $head))
    clip = $clip
    startFrame = $StartFrame
    seconds = $Seconds
    scale2 = [ordered]@{ hline = $m2.hline; vline = $m2.vline; chromaSpeckle = $m2.chromaSpeckle; grab = $grabs["scale2"] }
    scale1 = [ordered]@{ hline = $m1.hline; vline = $m1.vline; chromaSpeckle = $m1.chromaSpeckle; grab = $grabs["scale1"] }
    ratios = [ordered]@{ hline = [Math]::Round($ratioH, 3); vline = [Math]::Round($ratioV, 3); chromaSpeckle = [Math]::Round($ratioC, 3) }
    thresholds = [ordered]@{ hlineRatio = $HLineRatioThreshold; vlineRatio = $VLineRatioThreshold; chromaRatio = $ChromaRatioThreshold }
    interpretation = "Ratios are full-res(scale=1) / default(scale=2) of the same frame. A correct fix collapses them toward 1.0 and flips verdict to CLEAN."
}
$reportPath = Join-Path $OutputRoot "review-verdict.json"
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding UTF8

Write-Host ""
Write-Host ("scale=2 (default) : HLine {0:N2}  VLine {1:N2}  chromaSpeckle {2:N2}" -f $m2.hline, $m2.vline, $m2.chromaSpeckle)
Write-Host ("scale=1 (full-res): HLine {0:N2}  VLine {1:N2}  chromaSpeckle {2:N2}" -f $m1.hline, $m1.vline, $m1.chromaSpeckle)
Write-Host ("ratios full/def   : HLine x{0:N2}  VLine x{1:N2}  chromaSpeckle x{2:N2}" -f $ratioH, $ratioV, $ratioC)
Write-Host ("thresholds        : HLine x{0}  VLine x{1}" -f $HLineRatioThreshold, $VLineRatioThreshold)
if ($artifactPresent) {
    Write-Host ("VERDICT: ARTIFACT_PRESENT -- full-res dual-ISO recon regression still here. Report: {0}" -f $reportPath) -ForegroundColor Red
    exit 1
}
Write-Host ("VERDICT: CLEAN -- full-res ~= default; no recon-regression signature. Report: {0}" -f $reportPath) -ForegroundColor Green
exit 0
