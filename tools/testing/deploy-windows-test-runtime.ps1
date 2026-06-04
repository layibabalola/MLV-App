param(
    [Parameter(Mandatory = $true)]
    [string]$TargetDir,
    [string]$QtBinDir = "",
    [string]$ExeName = ""
)

$ErrorActionPreference = "Stop"

function Get-ExistingDirectoryList {
    param([string[]]$Candidates)

    $seen = @{}
    $dirs = New-Object System.Collections.Generic.List[string]
    foreach ($candidate in $Candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        try {
            $resolved = (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path
        } catch {
            continue
        }
        $key = $resolved.ToLowerInvariant()
        if (-not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            $dirs.Add($resolved)
        }
    }
    return $dirs.ToArray()
}

function Find-FirstFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string[]]$Directories
    )

    foreach ($dir in $Directories) {
        $candidate = Join-Path $dir $Name
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    return $null
}

$target = New-Item -ItemType Directory -Force -Path $TargetDir
$pathDirs = @()
if (-not [string]::IsNullOrWhiteSpace($env:PATH)) {
    $pathDirs = $env:PATH -split ';'
}

$qtRootBin = ""
if (-not [string]::IsNullOrWhiteSpace($env:QT_ROOT_DIR)) {
    $qtRootBin = Join-Path $env:QT_ROOT_DIR "bin"
}

$mingwRootBin = ""
if (-not [string]::IsNullOrWhiteSpace($env:MINGW_ROOT)) {
    $mingwRootBin = Join-Path $env:MINGW_ROOT "bin"
}

$qtDirs = Get-ExistingDirectoryList @(
    $QtBinDir,
    $qtRootBin,
    "C:\Qt\6.10.2\mingw_64\bin",
    $pathDirs
)

$toolchainDirs = Get-ExistingDirectoryList @(
    $mingwRootBin,
    "C:\Qt\Tools\mingw1310_64\bin",
    $pathDirs,
    $qtDirs
)

$required = @(
    @{ Name = "Qt6Core.dll"; Dirs = $qtDirs },
    @{ Name = "Qt6Gui.dll"; Dirs = $qtDirs },
    @{ Name = "Qt6OpenGL.dll"; Dirs = $qtDirs },
    @{ Name = "libgcc_s_seh-1.dll"; Dirs = $toolchainDirs },
    @{ Name = "libstdc++-6.dll"; Dirs = $toolchainDirs },
    @{ Name = "libwinpthread-1.dll"; Dirs = $toolchainDirs },
    @{ Name = "libgomp-1.dll"; Dirs = $toolchainDirs }
)

$testExeNames = @()
if (-not [string]::IsNullOrWhiteSpace($ExeName)) {
    $testExeNames = @($ExeName)
}
else {
    $testExeNames = @(
        "console_tests.exe",
        "pipeline_tests.exe",
        "gui_tests.exe",
        "perf_tests.exe"
    )
}
$testExes = @(
    foreach ($name in $testExeNames) {
        $candidate = Join-Path $target.FullName $name
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $candidate
        }
    }
)
$windeployqt = Find-FirstFile -Name "windeployqt.exe" -Directories $qtDirs
if ($null -ne $windeployqt -and $testExes.Count -gt 0) {
    foreach ($testExe in $testExes) {
        & $windeployqt $testExe --release --no-translations --no-compiler-runtime
        if ($LASTEXITCODE -ne 0) {
            throw "windeployqt failed for $testExe with exit code $LASTEXITCODE"
        }
    }
}
elseif ($null -eq $windeployqt) {
    Write-Warning "windeployqt.exe was not found; copying only the minimum direct runtime DLL set."
}

$copied = New-Object System.Collections.Generic.List[string]
$missing = New-Object System.Collections.Generic.List[string]

foreach ($entry in $required) {
    $source = Find-FirstFile -Name $entry.Name -Directories $entry.Dirs
    if ($null -eq $source) {
        $missing.Add($entry.Name)
        continue
    }

    Copy-Item -LiteralPath $source -Destination (Join-Path $target.FullName $entry.Name) -Force
    $copied.Add($entry.Name)
}

if ($missing.Count -gt 0) {
    throw ("Could not deploy Windows test runtime DLLs: {0}. Put Qt/MinGW bin dirs on PATH or pass -QtBinDir." -f ($missing -join ", "))
}

Write-Host ("[deploy-windows-test-runtime] {0}: {1}" -f $target.FullName, ($copied -join ", "))
