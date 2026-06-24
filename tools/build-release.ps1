# P0-2: the ONE authoritative clean-build path. Produces a trustworthy artifact:
#  - refuses (or -AllowDirty marks) a dirty tree
#  - FRESH out-of-tree build (no cross-commit object mixing)
#  - fresh qmake (so the embedded SHA is current even without the P0-1 gen-target)
#  - names the exe MLVApp-<sha>[-dirty].exe + writes build-manifest.json (sha, checksum, env)
#  - VERIFIES the produced exe's embedded SHA == HEAD (fail-closed)
# Usage: pwsh -NoProfile -File tools\build-release.ps1 [-AllowDirty] [-OutRoot <dir>]
param(
    [string]$SourceRoot = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$QtBin      = "C:\Qt\6.10.2\mingw_64\bin",
    [string]$MingwBin   = "C:\Qt\Tools\mingw1310_64\bin",
    [string]$OutRoot    = "$env:USERPROFILE\mlvapp-artifacts",
    [switch]$AllowDirty
)
$ErrorActionPreference = 'Stop'
$env:Path = "$QtBin;$MingwBin;$env:Path"
$head  = (& git -C $SourceRoot rev-parse HEAD).Trim()
$porcelain = (& git -C $SourceRoot status --porcelain)
$dirty = [bool]($porcelain -and ($porcelain | Where-Object { $_ }).Count -gt 0)
if ($dirty -and -not $AllowDirty) {
    Write-Host "ABORT: working tree is dirty (use -AllowDirty to build a -dirty artifact):" -ForegroundColor Red
    $porcelain | ForEach-Object { Write-Host "  $_" }
    exit 5
}
$tag = $head.Substring(0,12) + $(if ($dirty) { '-dirty' } else { '' })
$bd  = Join-Path ([System.IO.Path]::GetTempPath()) ("mlvbuild-" + $tag)
if (Test-Path -LiteralPath $bd) { Remove-Item -LiteralPath $bd -Recurse -Force }   # fresh, no object reuse
New-Item -ItemType Directory -Force -Path $bd | Out-Null
Write-Host "Clean build: HEAD=$head dirty=$dirty -> $bd"
Push-Location $bd
try {
    # P0-1: stamp the build-time provenance header INTO the build dir before qmake/compile,
    # so the artifact self-identifies its real commit + dirty state (not a qmake-pinned label).
    & pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "$SourceRoot\tools\gen-buildinfo.ps1" `
        -SrcRoot "$SourceRoot" -OutHeader (Join-Path $bd "build_buildinfo.h") 2>&1 | Out-Host
    & "$QtBin\qmake.exe" "$SourceRoot\platform\qt\MLVApp.pro" 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "qmake failed ($LASTEXITCODE)" }
    & "$MingwBin\mingw32-make.exe" -f Makefile.Release -j8 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "make failed ($LASTEXITCODE)" }
} finally { Pop-Location }
$builtExe = Join-Path $bd "release\MLVApp.exe"
if (-not (Test-Path $builtExe)) { throw "no exe produced" }

# fail-closed: verify the EXACT uniquely-tagged provenance stamp compiled into the binary == HEAD.
# (Strict: not "some 40-hex string in the exe matches" -- match MLVAPP_BUILDSTAMP_v1|sha=...|dirty=...)
$exeText  = [System.Text.Encoding]::ASCII.GetString([System.IO.File]::ReadAllBytes($builtExe))
$stamp    = [regex]::Match($exeText, 'MLVAPP_BUILDSTAMP_v1\|sha=([0-9a-f]{40})\|dirty=([01])')
$embedded = [regex]::Matches($exeText, '[0-9a-f]{40}') | ForEach-Object { $_.Value } | Select-Object -Unique  # diagnostic only
$stampSha = if ($stamp.Success) { $stamp.Groups[1].Value } else { '' }
$stampDirty = if ($stamp.Success) { [int]$stamp.Groups[2].Value } else { -1 }
$shaOk = ($stamp.Success -and $stampSha -eq $head)
if (-not $stamp.Success) { Write-Host "FAIL: no MLVAPP_BUILDSTAMP_v1 provenance field in the exe" -ForegroundColor Red }
elseif (-not $shaOk)     { Write-Host "FAIL: provenance stamp sha=$stampSha != HEAD $head" -ForegroundColor Red }

$dest = Join-Path $OutRoot $tag
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$destExe = Join-Path $dest ("MLVApp-" + $tag + ".exe")
Copy-Item -LiteralPath $builtExe -Destination $destExe -Force
$manifest = [ordered]@{
    head = $head; dirty = $dirty; tag = $tag
    describe = (& git -C $SourceRoot describe --always --dirty --abbrev=40).Trim()
    provenanceStampShaMatchesHead = $shaOk; provenanceStampSha = $stampSha; provenanceStampDirty = $stampDirty
    embeddedShas = @($embedded)
    exe = (Split-Path $destExe -Leaf); sha256 = (Get-FileHash -LiteralPath $destExe -Algorithm SHA256).Hash
    qt = "6.10.2"; toolchain = "mingw1310"; builtUtc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    host = $env:COMPUTERNAME
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $dest "build-manifest.json") -Encoding UTF8
Write-Host "OK -> $destExe" -ForegroundColor Green
Write-Host "    embedded-SHA==HEAD: $shaOk ; manifest written"
if (-not $shaOk) { exit 6 }
