[CmdletBinding()]
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")),
    [string]$Python = "python",
    [switch]$Check,
    [switch]$Upgrade
)

$ErrorActionPreference = "Stop"
if ($Check -and $Upgrade) {
    throw "-Check and -Upgrade are mutually exclusive"
}
$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$expectedPython = (Get-Content -LiteralPath (Join-Path $repo ".python-version") -Raw).Trim()
$actualPython = (& $Python -c "import platform; print(platform.python_version())").Trim()
if ($LASTEXITCODE -ne 0 -or $actualPython -ne $expectedPython) {
    throw "Python lock generation requires $expectedPython; got $actualPython"
}

$pipToolsVersion = (& $Python -c "import importlib.metadata as m; print(m.version('pip-tools'))").Trim()
if ($LASTEXITCODE -ne 0 -or $pipToolsVersion -ne "7.5.3") {
    throw "Python lock generation requires pip-tools 7.5.3; got $pipToolsVersion"
}

$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:PIP_NO_INPUT = "1"
$locks = @(
    @{ Input = ".github/requirements/pip.in"; Output = ".github/requirements/pip.txt"; AllowUnsafe = $true },
    @{ Input = ".github/requirements/lock-tools.in"; Output = ".github/requirements/lock-tools.txt"; AllowUnsafe = $true },
    @{ Input = ".github/requirements/repo-hygiene.in"; Output = ".github/requirements/repo-hygiene.txt"; AllowUnsafe = $false },
    @{ Input = ".github/requirements/aqtinstall.in"; Output = ".github/requirements/aqtinstall.txt"; AllowUnsafe = $false },
    @{ Input = "tools/agent-bridge/requirements.in"; Output = "tools/agent-bridge/requirements.txt"; AllowUnsafe = $false },
    @{ Input = "tools/agent-bridge/requirements-test.in"; Output = "tools/agent-bridge/requirements-test.txt"; AllowUnsafe = $false }
)

$drift = @()
foreach ($lock in $locks) {
    $inputPath = Join-Path $repo $lock.Input
    $outputPath = Join-Path $repo $lock.Output
    $compileOutput = $outputPath
    if ($Check) {
        $compileOutput = Join-Path ([System.IO.Path]::GetTempPath()) ("mlvapp-python-lock-{0}.txt" -f [guid]::NewGuid().ToString("N"))
        # pip-compile reuses pins from its existing output. Seed the temporary
        # output so verification proves the committed solution is internally
        # reproducible instead of failing whenever PyPI publishes a newer
        # version allowed by an input range.
        Copy-Item -LiteralPath $outputPath -Destination $compileOutput
    }

    $arguments = @(
        "-m", "piptools", "compile",
        "--quiet",
        "--generate-hashes",
        "--resolver=backtracking",
        "--strip-extras",
        "--no-header",
        "--no-annotate",
        "--no-emit-index-url",
        "--no-emit-trusted-host",
        "--newline=lf",
        "--output-file", $compileOutput
    )
    if ($lock.AllowUnsafe) {
        $arguments += "--allow-unsafe"
    }
    if ($Upgrade) {
        $arguments += "--upgrade"
    }
    $arguments += $inputPath

    try {
        & $Python @arguments
        if ($LASTEXITCODE -ne 0) {
            throw "pip-compile failed for $($lock.Input) with exit $LASTEXITCODE"
        }
        if ($Check) {
            $expected = [System.IO.File]::ReadAllBytes($outputPath)
            $actual = [System.IO.File]::ReadAllBytes($compileOutput)
            if (-not [System.Linq.Enumerable]::SequenceEqual[byte]($expected, $actual)) {
                $drift += $lock.Output
            }
        }
    }
    finally {
        if ($Check -and (Test-Path -LiteralPath $compileOutput)) {
            Remove-Item -LiteralPath $compileOutput -Force
        }
    }
}

if ($drift.Count -ne 0) {
    throw "Python dependency locks are stale: $($drift -join ', ')"
}

if ($Check) {
    Write-Output "Python dependency locks are current."
} elseif ($Upgrade) {
    Write-Output "Python dependency locks intentionally upgraded."
} else {
    Write-Output "Python dependency locks regenerated without upgrading existing pins."
}
