param(
    [Parameter(Mandatory = $true)]
    [string] $ExePath,
    [string] $QtBin = "C:\Qt\6.10.2\mingw_64\bin",
    [string] $MingwBin = "C:\Qt\Tools\mingw1310_64\bin",
    [switch] $Deploy,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ExePath)) {
    Write-Error "MLVApp executable not found: $ExePath"
    exit 1
}

$exeFullPath = (Resolve-Path -LiteralPath $ExePath).Path
$exeDir = Split-Path -Parent $exeFullPath

if (-not (Test-Path -LiteralPath $QtBin)) {
    Write-Error "Qt binary directory not found: $QtBin"
    exit 1
}

if (-not (Test-Path -LiteralPath $MingwBin)) {
    Write-Error "MinGW binary directory not found: $MingwBin"
    exit 1
}

$requiredQtDlls = @(
    "Qt6Core.dll",
    "Qt6Network.dll"
)

$needsDeploy = $Deploy
if (-not $needsDeploy) {
    foreach ($dll in $requiredQtDlls) {
        if (-not (Test-Path (Join-Path $exeDir $dll))) {
            $needsDeploy = $true
            break
        }
    }
}

if ($needsDeploy) {
    $windeployqt = Join-Path $QtBin "windeployqt.exe"
    if (-not (Test-Path -LiteralPath $windeployqt)) {
        Write-Error "windeployqt not found: $windeployqt"
        exit 1
    }

    Push-Location -LiteralPath $exeDir
    try {
        & $windeployqt $exeFullPath --release --no-translations --no-compiler-runtime
        if ($LASTEXITCODE -ne 0) {
            Write-Error "windeployqt failed with exit code $LASTEXITCODE"
            exit $LASTEXITCODE
        }
    }
    finally {
        Pop-Location
    }
}

$oldQtOpenGl = $env:QT_OPENGL
$oldPath = $env:PATH
try {
    $env:QT_OPENGL = "desktop"
    $env:PATH = "$QtBin;$MingwBin;$exeDir;" + $oldPath
    Set-Location -LiteralPath $exeDir
    Write-Host "Launching $exeFullPath"
    Write-Host "Using Qt bin: $QtBin"
    Write-Host "Using Mingw bin: $MingwBin"
    & $exeFullPath @Arguments
    $exitCode = $LASTEXITCODE
    if ($null -ne $exitCode) { exit $exitCode }
}
finally {
    $env:QT_OPENGL = $oldQtOpenGl
    $env:PATH = $oldPath
}
