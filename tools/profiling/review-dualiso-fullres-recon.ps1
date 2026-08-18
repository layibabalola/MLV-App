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
# GATE-FAIRNESS (2026-06-26): the Look Assist auto-WB is non-deterministic run-to-run
# (.claude-state/analysis/lookassist-wb-nondeterminism.md), so running each leg with its own auto-WB
# makes the A/B conflate the clean recon with auto-WB noise. Two opt-in modes lock the WB so the A/B
# measures recon only (both add --no-look-assist so the auto-WB cannot diverge the legs):
#   -LockWhiteBalance : both legs at one deterministic default WB; chroma_smooth is OFF, so absolute
#                       row energy reads high -- trust the RATIO, which is ~1.0 when recon is scale-fair.
#   -LockReceipt <p>  : both legs --receipt <p>, pinning WB AND chroma identically. Use
#                       tests/fixtures/receipts/large_dual_iso_wblock_chroma.marxml (WB 5000/0,
#                       chromaSmooth=1) for the chroma-ON gold standard -- a CLEAN verdict then means
#                       recon+chroma are good at locked WB. (Chroma-on is heavier; give it -Seconds 20+.)
#
# Exit: 0 = CLEAN (no full-res regression signature) ; 1 = ARTIFACT_PRESENT ; 2 = error.
param(
    [string]$RepoRoot = ".",
    [string]$ExePath = "",
    [Alias("Input")]
    [string]$ClipPath = "",
    [int]$StartFrame = 10,
    [int]$Seconds = 24,      # RULE 2026-06-26 (Layi): generous settled playback window (was 3s) when
                             # playing MLV -- ~the recommended 30s window; chroma-on (-LockReceipt) is
                             # heavy so this also gives the render time to settle before the grab.
    [int]$SettleMs = 3500,
    [string]$OutputRoot = "",
    [double]$HLineRatioThreshold = 3.0,
    [double]$VLineRatioThreshold = 4.0,
    [double]$ChromaRatioThreshold = 3.0,
    # Absolute sanity floor on the DEFAULT (scale=2) leg. The ratio gate is blind to a
    # fix that breaks BOTH legs equally (ratio stays ~1.0). The default playback path is
    # expected to be clean, so if its absolute row energy is implausibly high the gate
    # must NOT auto-pass -- it downgrades to SUSPECT and demands an eyeball. (A scalar
    # cannot reliably tell artifact from busy detail; per the post-mortem, the eyeball is
    # mandatory. This guard caught the 2026-06-24 false-CLEAN where both legs read HLine ~26.)
    [double]$DefaultLegHLineCeiling = 5.0,
    # GATE-FAIRNESS: the Look Assist auto-WB is non-deterministic run-to-run (proven 2026-06-26:
    # .claude-state/analysis/lookassist-wb-nondeterminism.md), so running each leg with its own
    # auto-WB makes the A/B conflate the (clean) recon with auto-WB noise. -LockWhiteBalance runs
    # BOTH legs with --no-look-assist so they share one deterministic default WB, isolating the
    # recon. The frame is already fixed (-StartFrame). Both legs identical WB => the comparison
    # measures recon only. (Caveat: --no-look-assist also disables chroma_smooth; the chroma-on
    # locked control is a follow-up that needs a fixed receipt.)
    [switch]$LockWhiteBalance,
    # GATE-FAIRNESS gold standard: run both legs with this fixed receipt + --no-look-assist, pinning
    # WB AND chroma_smooth identically across legs. Use
    # tests/fixtures/receipts/large_dual_iso_wblock_chroma.marxml (WB 5000/0, chromaSmooth=1) to measure
    # the recon at a representative chroma-ON state -- a CLEAN verdict then means recon+chroma are good
    # at locked WB. Implies locked-WB semantics; takes precedence over -LockWhiteBalance if both are set.
    [string]$LockReceipt = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "provenance-stamp.ps1")
. (Join-Path $PSScriptRoot "gui-smoke-screenshot-provenance.ps1")

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
$legEvidence = [ordered]@{}
# GATE-FAIRNESS: with -LockWhiteBalance, run both legs with --no-look-assist so they share one
# deterministic default WB (the auto-WB is non-deterministic run-to-run). The gate computes its
# metrics from the captured PNG, which --no-look-assist still produces, so the recon comparison is
# isolated from auto-WB noise. (-DisableLookAssist is a [switch]; splatting an empty array is a no-op.)
# GATE-FAIRNESS: lock the WB across both legs so the A/B measures recon, not the non-deterministic
# Look Assist auto-WB. -LockReceipt pins a fixed receipt (WB + chroma); -LockWhiteBalance alone uses
# --no-look-assist (default WB, chroma off). Both add --no-look-assist so auto-WB cannot diverge legs.
$lockArgs = @()
if ($LockReceipt) {
    $lockReceiptPath = (Resolve-Path -LiteralPath $LockReceipt).Path
    $lockArgs = @('-Receipt', $lockReceiptPath, '-DisableLookAssist')
    Write-Host "[A/B] -LockReceipt: both legs run --receipt '$lockReceiptPath' --no-look-assist (locked WB + chroma per receipt) so the gate measures recon at a fixed processing state." -ForegroundColor Cyan
}
elseif ($LockWhiteBalance) {
    $lockArgs = @('-DisableLookAssist')
    Write-Host "[A/B] -LockWhiteBalance: both legs run --no-look-assist (locked default WB, chroma off) so the gate measures recon, not the non-deterministic Look Assist auto-WB." -ForegroundColor Cyan
}
foreach ($sf in @(2, 1)) {
    $scaleDir = Join-Path $OutputRoot "scale$sf"
    New-Item -ItemType Directory -Force -Path $scaleDir | Out-Null
    Write-Host ("[A/B] scale=$sf live autoplay grab ...")
    # Build the smoke args as an array (canonical splat) so conditional flags compose cleanly with
    # the *> redirect -- inline-splatting an array next to *> misparses (binds a stray '-').
    $smokeArgs = @(
        '-RepoRoot', $root, '-ExePath', $exe, '-Input', $clip,
        '-ScaleFactor', $sf, '-ExpectedScaleRequest', $sf,
        '-StartFrame', $StartFrame, '-Seconds', $Seconds, '-SettleMs', $SettleMs,
        '-CaptureScreenshot', '-RequireFreshScreenshotRender', '-SkipWindowScreenshot',
        # Frame-matched A/B: BOTH legs must stop on the SAME end frame for the ratio to be valid, so
        # this gate opts OUT of the new default-on playback loop (the general smoke loops; this does not).
        '-NoLoop',
        '-Output', (Join-Path $scaleDir 'result.json'),
        '-ScreenshotOutputDir', (Join-Path $scaleDir 'shots')
    )
    $smokeArgs += $lockArgs
    $smokeLogPath = Join-Path $scaleDir 'smoke.log'
    $resultPath = Join-Path $scaleDir 'result.json'
    & pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $smoke @smokeArgs *> $smokeLogPath
    $smokeExitCode = $LASTEXITCODE
    $png = Join-Path $scaleDir "shots\$clipBase.png"
    try {
        $smokeChildGate = Assert-GuiSmokeChildEvidenceReady `
            -ExitCode $smokeExitCode `
            -ResultExists (Test-Path -LiteralPath $resultPath -PathType Leaf) `
            -ScreenshotExists (Test-Path -LiteralPath $png -PathType Leaf)
    }
    catch {
        Write-Host "ERROR: scale=$sf smoke child failed before evidence consumption: $($_.Exception.Message) See $smokeLogPath" -ForegroundColor Red
        exit 2
    }
    try {
        $smokeResult = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
    }
    catch {
        Write-Host "ERROR: scale=$sf result JSON is unreadable ($resultPath): $($_.Exception.Message)" -ForegroundColor Red
        exit 2
    }
    $smokeValidation = $smokeResult.validation
    $smokeProvenance = if ($smokeValidation) { $smokeValidation.screenshotProvenance } else { $null }
    if ($null -eq $smokeValidation -or
        -not $smokeValidation.ok -or
        -not $smokeValidation.freshScreenshotRenderRequired -or
        -not $smokeValidation.freshScreenshotRenderValidated -or
        $null -eq $smokeProvenance -or
        -not $smokeProvenance.validFresh) {
        Write-Host "ERROR: scale=$sf result JSON lacks a passing fresh-render validation/provenance record ($resultPath)." -ForegroundColor Red
        exit 2
    }
    $legEvidence["scale$sf"] = [ordered]@{
        smokeExitCode = $smokeExitCode
        smokeLog = $smokeLogPath
        resultJson = $resultPath
        childGate = $smokeChildGate
        validation = $smokeValidation
        screenshotProvenance = $smokeProvenance
    }
    $grabs["scale$sf"] = $png
}

# SCALE -> ASPECT-RATIO CHECK (2026-06-26): the playback scale must PRESERVE the aspect ratio -- a
# UNIFORM width/height downscale, identical between scale=1 and scale=2, and matching the clip's native
# RAWI aspect. A one-dimensional scale (e.g. half-width/full-height) would distort the image and confound
# the A/B. The recon ratio already catches BETWEEN-leg distortion; this also catches a both-legs-equal
# distortion vs the native aspect. Non-fatal: warns + records in the verdict. (large_dual_iso.mlv is
# natively PORTRAIT 1808x2268 ~0.797 -- the grabs are a uniform fit-to-display downscale to ~0.796.)
function Get-PngAspectInfo([string]$p) {
    $b = [System.IO.File]::ReadAllBytes($p)
    $w = ([int]$b[16] -shl 24) -bor ([int]$b[17] -shl 16) -bor ([int]$b[18] -shl 8) -bor [int]$b[19]
    $h = ([int]$b[20] -shl 24) -bor ([int]$b[21] -shl 16) -bor ([int]$b[22] -shl 8) -bor [int]$b[23]
    return [ordered]@{ w = $w; h = $h; aspect = [Math]::Round($w / [Math]::Max(1, $h), 4) }
}
function Get-MlvNativeAspectInfo([string]$p) {
    $fs = [System.IO.File]::OpenRead($p)
    try {
        $buf = New-Object byte[] 262144
        $n = $fs.Read($buf, 0, $buf.Length)
        for ($i = 0; $i -lt $n - 20; $i++) {
            if ($buf[$i] -eq 0x52 -and $buf[$i+1] -eq 0x41 -and $buf[$i+2] -eq 0x57 -and $buf[$i+3] -eq 0x49) {
                $xRes = [int]$buf[$i+16] -bor ([int]$buf[$i+17] -shl 8)
                $yRes = [int]$buf[$i+18] -bor ([int]$buf[$i+19] -shl 8)
                if ($xRes -gt 0 -and $yRes -gt 0) { return [ordered]@{ w = $xRes; h = $yRes; aspect = [Math]::Round($xRes / $yRes, 4) } }
            }
        }
    } finally { $fs.Close() }
    return $null
}
$asp2 = Get-PngAspectInfo $grabs["scale2"]
$asp1 = Get-PngAspectInfo $grabs["scale1"]
$aspNative = Get-MlvNativeAspectInfo $clip
$aspectTol = 0.02
$aspectLegMatch = ([Math]::Abs($asp2.aspect - $asp1.aspect) -le $aspectTol)
$aspectVsNative = ($null -ne $aspNative) -and ([Math]::Abs($asp2.aspect - $aspNative.aspect) -le $aspectTol)
$aspectOk = $aspectLegMatch -and $aspectVsNative
$aspectCheck = [ordered]@{ scale2 = $asp2; scale1 = $asp1; native = $aspNative; toleranceAspect = $aspectTol; legMatch = $aspectLegMatch; matchesNative = $aspectVsNative; ok = $aspectOk }
if ($aspectOk) {
    Write-Host ("[A/B] aspect OK: scale2 {0}x{1} / scale1 {2}x{3} ~{4} == native {5}x{6} ~{7} (scale preserves aspect)." -f $asp2.w,$asp2.h,$asp1.w,$asp1.h,$asp2.aspect,$aspNative.w,$aspNative.h,$aspNative.aspect) -ForegroundColor DarkGray
} else {
    $nstr = if ($aspNative) { "$($aspNative.w)x$($aspNative.h) ~$($aspNative.aspect)" } else { "(native unread)" }
    Write-Host ("ASPECT WARNING: scale2 {0}x{1} ~{2} / scale1 {3}x{4} ~{5} / native {6}  legMatch={7} matchesNative={8} -- scale must be a UNIFORM W/H downscale; a mismatch means the playback scale DISTORTED the aspect ratio." -f $asp2.w,$asp2.h,$asp2.aspect,$asp1.w,$asp1.h,$asp1.aspect,$nstr,$aspectLegMatch,$aspectVsNative) -ForegroundColor Yellow
}

# DE-SQUEEZED EYEBALL COMPANION (2026-06-27, Claude tooling lane): the recon MEASUREMENT must stay on the
# native-aspect grab -- de-squeezing vertically compresses the grab ~2.3x (1820 -> ~774 rows) and would
# UNDERSAMPLE the dual-ISO row mesh this gate exists to catch, and it would re-anchor the established HLine
# baseline. But a vertically-stretched portrait grab is hard to eyeball for the color cast (the gate's
# known blind spot). So, WITHOUT touching the measurement, write a de-squeezed COMPANION per leg purely for
# the human/agent eyeball: horizontally stretch the native grab by the SAME dual-ISO display de-squeeze
# MLVApp itself applies for comboBoxVStretch index 3 (MainWindow.cpp:25529 getHorizontalStretchFactor:
# factor *= 3.0). Not a clip-fit constant -- it is the app's own dual-ISO de-squeeze factor.
$deSqueezeX = 3.0
$desqueezed = @{}
try {
    Add-Type -AssemblyName System.Drawing
    foreach ($sf in @(2, 1)) {
        $src = $grabs["scale$sf"]
        $dst = Join-Path (Join-Path $OutputRoot "scale$sf") "shots\$clipBase-desqueezed.png"
        $img = [System.Drawing.Image]::FromFile($src)
        try {
            $nw = [int]($img.Width * $deSqueezeX); $nh = $img.Height
            $bmp = New-Object System.Drawing.Bitmap $nw, $nh
            try {
                $g = [System.Drawing.Graphics]::FromImage($bmp)
                try {
                    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                    $g.DrawImage($img, 0, 0, $nw, $nh)
                } finally { $g.Dispose() }
                $bmp.Save($dst, [System.Drawing.Imaging.ImageFormat]::Png)
            } finally { $bmp.Dispose() }
            $desqueezed["scale$sf"] = $dst
        } finally { $img.Dispose() }
    }
    Write-Host ("[A/B] de-squeezed EYEBALL companions written (correct ~{0:N2} display aspect; the MEASUREMENT below stays on the native-resolution grab): scale2 + scale1 shots\$clipBase-desqueezed.png" -f ($asp2.aspect * $deSqueezeX)) -ForegroundColor Cyan
} catch {
    Write-Host "WARN: could not write de-squeezed eyeball companions: $($_.Exception.Message)" -ForegroundColor Yellow
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
$defaultLegSuspect = ($m2.hline -gt $DefaultLegHLineCeiling)
$verdict = if ($artifactPresent) { "ARTIFACT_PRESENT" }
           elseif ($defaultLegSuspect) { "SUSPECT_EYEBALL_REQUIRED" }
           else { "CLEAN" }

$report = [ordered]@{
    schema = "mlvapp.dualiso-fullres-review.v1"
    verdict = $verdict
    artifactPresent = $artifactPresent
    whiteBalanceLocked = [bool]($LockWhiteBalance -or $LockReceipt)
    lockReceipt = $LockReceipt
    aspectCheck = $aspectCheck
    deSqueezedEyeball = [ordered]@{ note = "Correct-aspect (~x3 dual-ISO de-squeeze) companions for the EYEBALL only; the HLine/VLine/chromaSpeckle measurement uses the native-resolution grab to preserve row-mesh sensitivity."; scale2 = $desqueezed["scale2"]; scale1 = $desqueezed["scale1"] }
    capturedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    exe = $exe
    embeddedSha = $stampInfo.sha
    embeddedStampFound = $stampInfo.found
    repoHead = $head
    matchesHead = ($stampInfo.found -and $head -and ($stampInfo.sha -eq $head))
    clip = $clip
    startFrame = $StartFrame
    seconds = $Seconds
    legEvidence = $legEvidence
    scale2 = [ordered]@{ hline = $m2.hline; vline = $m2.vline; chromaSpeckle = $m2.chromaSpeckle; grab = $grabs["scale2"] }
    scale1 = [ordered]@{ hline = $m1.hline; vline = $m1.vline; chromaSpeckle = $m1.chromaSpeckle; grab = $grabs["scale1"] }
    ratios = [ordered]@{ hline = [Math]::Round($ratioH, 3); vline = [Math]::Round($ratioV, 3); chromaSpeckle = [Math]::Round($ratioC, 3) }
    defaultLegSuspect = $defaultLegSuspect
    thresholds = [ordered]@{ hlineRatio = $HLineRatioThreshold; vlineRatio = $VLineRatioThreshold; chromaRatio = $ChromaRatioThreshold; defaultLegHlineCeiling = $DefaultLegHLineCeiling }
    interpretation = "Ratios are full-res(scale=1)/default(scale=2) of the SAME frame; a correct fix collapses them toward 1.0. The ratio gate is blind to a fix that breaks BOTH legs equally, so an absolute ceiling on the default leg downgrades CLEAN->SUSPECT_EYEBALL_REQUIRED. A scalar cannot finally certify quality -- always eyeball the grabs."
}
$reportPath = Join-Path $OutputRoot "review-verdict.json"
$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding UTF8

Write-Host ""
Write-Host ("scale=2 (default) : HLine {0:N2}  VLine {1:N2}  chromaSpeckle {2:N2}" -f $m2.hline, $m2.vline, $m2.chromaSpeckle)
Write-Host ("scale=1 (full-res): HLine {0:N2}  VLine {1:N2}  chromaSpeckle {2:N2}" -f $m1.hline, $m1.vline, $m1.chromaSpeckle)
Write-Host ("ratios full/def   : HLine x{0:N2}  VLine x{1:N2}  chromaSpeckle x{2:N2}" -f $ratioH, $ratioV, $ratioC)
Write-Host ("thresholds        : HLine x{0}  VLine x{1}" -f $HLineRatioThreshold, $VLineRatioThreshold)
if ($artifactPresent) {
    Write-Host ("VERDICT: ARTIFACT_PRESENT -- full-res worse than default (ratio gate). Report: {0}" -f $reportPath) -ForegroundColor Red
    exit 1
}
if ($defaultLegSuspect) {
    Write-Host ("VERDICT: SUSPECT_EYEBALL_REQUIRED -- ratios ~1 but default-leg HLine {0:N2} > ceiling {1}; both legs may be equally broken. EYEBALL the grabs before any CLEAN claim. Report: {2}" -f $m2.hline, $DefaultLegHLineCeiling, $reportPath) -ForegroundColor Yellow
    exit 3
}
Write-Host ("VERDICT: CLEAN -- full-res ~= default AND default leg within clean ceiling. Still eyeball the grabs. Report: {0}" -f $reportPath) -ForegroundColor Green
exit 0
