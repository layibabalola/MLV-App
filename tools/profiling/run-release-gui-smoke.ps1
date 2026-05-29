param(
    [string]$RepoRoot = ".",
    [string]$ExePath = "",
    [Alias("Input")]
    [string]$ClipPath = "",
    [string]$Output = "",
    [int]$Seconds = 8,
    [int]$SettleMs = 2500,
    [double]$SettleCpuPercent = 10,
    [int]$SettleCpuStableMs = 1000,
    [int]$SettleCpuMaxMs = 45000,
    [string]$Threads = "6",
    [string]$ScaleFactor = "",
    [switch]$PreferHqMean23,
    [string]$Receipt = "",
    [string]$Scope = "",
    [ValidateSet("", "on", "off")]
    [string]$Zebras = "",
    [bool]$RequireLookAssist = $true,
    [bool]$RequireCpuSettled = $true,
    [string[]]$AdditionalArgs = @(),
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Convert-PlaybackLogLineToObject {
    param([string]$Line)

    $result = [ordered]@{}
    $matches = [regex]::Matches($Line, '(?<key>[A-Za-z0-9_]+)=(?<value>"[^"]*"|\S+)')
    foreach ($match in $matches) {
        $key = $match.Groups["key"].Value
        $rawValue = $match.Groups["value"].Value.Trim('"')

        $intValue = 0L
        $doubleValue = 0.0
        if ([long]::TryParse($rawValue, [ref]$intValue)) {
            $result[$key] = $intValue
        }
        elseif ([double]::TryParse(
            $rawValue,
            [System.Globalization.NumberStyles]::Float,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [ref]$doubleValue)) {
            $result[$key] = $doubleValue
        }
        else {
            $result[$key] = $rawValue
        }
    }
    [pscustomobject]$result
}

function Get-LogTimestampUtc {
    param([string]$Line)

    if ($Line -notmatch '^\[(?<ts>[^\]]+)\]') {
        return $null
    }

    [datetime]::Parse(
        $Matches["ts"],
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
            [System.Globalization.DateTimeStyles]::AdjustToUniversal)
}

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
if ([string]::IsNullOrWhiteSpace($ExePath)) {
    $ExePath = Join-Path $root "platform\qt\build-release\release\MLVApp.exe"
}

$exe = (Resolve-Path -LiteralPath $ExePath).Path
if ([string]::IsNullOrWhiteSpace($ClipPath)) {
    throw "Missing -Input <clip.mlv>."
}
$inputPath = (Resolve-Path -LiteralPath $ClipPath).Path

if ([string]::IsNullOrWhiteSpace($Output)) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $clipBase = [IO.Path]::GetFileNameWithoutExtension($inputPath)
    $Output = Join-Path $root ".claude-state\profiling\$stamp-gui-smoke\$clipBase.json"
}
$outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
$outputDir = Split-Path -Parent $outputPath
if (-not [string]::IsNullOrWhiteSpace($outputDir)) {
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
}

$arguments = @(
    "--gui-smoke-playback",
    "--input", $inputPath,
    "--seconds", [string]$Seconds,
    "--settle-ms", [string]$SettleMs,
    "--settle-cpu-percent", ([string]::Format(
        [System.Globalization.CultureInfo]::InvariantCulture,
        "{0}",
        $SettleCpuPercent)),
    "--settle-cpu-stable-ms", [string]$SettleCpuStableMs,
    "--settle-cpu-max-ms", [string]$SettleCpuMaxMs
)
if (-not [string]::IsNullOrWhiteSpace($Receipt)) {
    $arguments += @("--receipt", (Resolve-Path -LiteralPath $Receipt).Path)
}
if (-not [string]::IsNullOrWhiteSpace($Scope)) {
    $arguments += @("--scope", $Scope)
}
if ($Zebras -eq "on") {
    $arguments += "--zebras"
}
elseif ($Zebras -eq "off") {
    $arguments += "--no-zebras"
}
if ($AdditionalArgs.Count -gt 0) {
    $arguments += $AdditionalArgs
}

$launchEnv = [ordered]@{
    MLVAPP_PLAYBACK_SMOKE_TELEMETRY = "1"
    MLVAPP_PLAYBACK_MAX_THREADS = $Threads
}
if (-not [string]::IsNullOrWhiteSpace($ScaleFactor)) {
    $launchEnv["MLVAPP_PLAYBACK_SCALE_FACTOR"] = $ScaleFactor
}
if ($PreferHqMean23) {
    $launchEnv["MLVAPP_PLAYBACK_PREFER_HQ_MEAN23"] = "1"
}

if ($DryRun) {
    [pscustomobject]@{
        exePath = $exe
        workingDirectory = $root
        arguments = $arguments
        environment = $launchEnv
        clearsEnvironment = @(
            "MLVAPP_PLAYBACK_SCALE_FACTOR",
            "MLVAPP_PLAYBACK_PREFER_HQ_MEAN23"
        )
        output = $outputPath
    } | ConvertTo-Json -Depth 5
    return
}

$smokeMutex = [System.Threading.Mutex]::new($false, "Global\MLVAppGuiSmokeLogParser")
$smokeMutexAcquired = $smokeMutex.WaitOne([TimeSpan]::FromMinutes(10))
if (-not $smokeMutexAcquired) {
    throw "Timed out waiting for another GUI smoke run to release the shared MLVApp log parser."
}

$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $exe
$startInfo.WorkingDirectory = $root
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $false
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
foreach ($argument in $arguments) {
    [void]$startInfo.ArgumentList.Add($argument)
}

$envBlock = $startInfo.EnvironmentVariables
$envBlock["MLVAPP_PLAYBACK_SMOKE_TELEMETRY"] = "1"
$envBlock["MLVAPP_PLAYBACK_MAX_THREADS"] = $Threads
if (-not [string]::IsNullOrWhiteSpace($ScaleFactor)) {
    $envBlock["MLVAPP_PLAYBACK_SCALE_FACTOR"] = $ScaleFactor
}
else {
    [void]$envBlock.Remove("MLVAPP_PLAYBACK_SCALE_FACTOR")
}
if ($PreferHqMean23) {
    $envBlock["MLVAPP_PLAYBACK_PREFER_HQ_MEAN23"] = "1"
}
else {
    [void]$envBlock.Remove("MLVAPP_PLAYBACK_PREFER_HQ_MEAN23")
}

$startUtc = [datetime]::UtcNow
$process = [System.Diagnostics.Process]::Start($startInfo)
$stdoutTask = $process.StandardOutput.ReadToEndAsync()
$stderrTask = $process.StandardError.ReadToEndAsync()
$process.WaitForExit()
$stdout = $stdoutTask.GetAwaiter().GetResult()
$stderr = $stderrTask.GetAwaiter().GetResult()
$endUtc = [datetime]::UtcNow

$logRoot = Join-Path $env:APPDATA "magiclantern\MLVApp\logs"
$logFile = Get-ChildItem -LiteralPath $logRoot -Filter "mlvapp-*.log" |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

$recentLines = @()
if ($logFile) {
    $since = $startUtc.AddSeconds(-2)
    foreach ($line in Get-Content -LiteralPath $logFile.FullName) {
        $timestamp = Get-LogTimestampUtc -Line $line
        if ($timestamp -and $timestamp -ge $since) {
            $recentLines += $line
        }
    }
}

$runMetadataLine = $recentLines |
    Where-Object { $_ -like "*run_metadata=*" -and $_ -like "*--gui-smoke-playback*" } |
    Select-Object -Last 1
$playbackStartLine = $recentLines |
    Where-Object { $_ -like "*playback_smoke.start*" } |
    Select-Object -Last 1
$summaryLine = $recentLines |
    Where-Object { $_ -like "*playback_smoke.summary*" } |
    Select-Object -Last 1
$cpuSummaryLine = $recentLines |
    Where-Object { $_ -like "*playback_smoke.cpu_summary*" } |
    Select-Object -Last 1
$dualIsoSummaryLine = $recentLines |
    Where-Object { $_ -like "*playback_smoke.dual_iso_full20_summary*" } |
    Select-Object -Last 1
$lookAssistSettleLine = $recentLines |
    Where-Object { $_ -like "*gui_smoke.look_assist_settle*" } |
    Select-Object -Last 1
$lookAssistApplyLine = $recentLines |
    Where-Object { $_ -like "*look_assist.apply.result*" } |
    Select-Object -Last 1
$cpuSettleLine = $recentLines |
    Where-Object { $_ -like "*gui_smoke.cpu_settle*" } |
    Select-Object -Last 1

$runMetadata = $null
if ($runMetadataLine -and $runMetadataLine -match 'run_metadata=(?<json>\{.*\})') {
    $runMetadata = $Matches["json"] | ConvertFrom-Json
}

$result = [pscustomobject]@{
    schema = "mlvapp-gui-smoke-result.v1"
    capturedAtUtc = $endUtc.ToString("o")
    repoRoot = $root
    exePath = $exe
    clipPath = $inputPath
    outputPath = $outputPath
    launch = [pscustomobject]@{
        workingDirectory = $root
        arguments = $arguments
        environment = $launchEnv
        validationPolicy = [pscustomobject]@{
            requireLookAssist = $RequireLookAssist
            requireCpuSettled = $RequireCpuSettled
        }
        matchedUserShellDefaults = [pscustomobject]@{
            telemetry = $true
            playbackMaxThreads = $Threads
            scaleFactorUnsetUnlessRequested = [string]::IsNullOrWhiteSpace($ScaleFactor)
            preferHqMean23UnsetUnlessRequested = -not $PreferHqMean23
            qtPlatformNotForced = $true
        }
    }
    process = [pscustomobject]@{
        id = $process.Id
        exitCode = $process.ExitCode
        startedAtUtc = $startUtc.ToString("o")
        endedAtUtc = $endUtc.ToString("o")
        stdout = $stdout.Trim()
        stderr = $stderr.Trim()
    }
    log = [pscustomobject]@{
        path = if ($logFile) { $logFile.FullName } else { $null }
        runMetadata = $runMetadata
        playbackStart = if ($playbackStartLine) { Convert-PlaybackLogLineToObject $playbackStartLine } else { $null }
        summary = if ($summaryLine) { Convert-PlaybackLogLineToObject $summaryLine } else { $null }
        cpuSummary = if ($cpuSummaryLine) { Convert-PlaybackLogLineToObject $cpuSummaryLine } else { $null }
        dualIsoFull20Summary = if ($dualIsoSummaryLine) { Convert-PlaybackLogLineToObject $dualIsoSummaryLine } else { $null }
        lookAssistSettle = if ($lookAssistSettleLine) { Convert-PlaybackLogLineToObject $lookAssistSettleLine } else { $null }
        lookAssistApply = if ($lookAssistApplyLine) { Convert-PlaybackLogLineToObject $lookAssistApplyLine } else { $null }
        cpuSettle = if ($cpuSettleLine) { Convert-PlaybackLogLineToObject $cpuSettleLine } else { $null }
        raw = [pscustomobject]@{
            runMetadata = $runMetadataLine
            playbackStart = $playbackStartLine
            summary = $summaryLine
            cpuSummary = $cpuSummaryLine
            dualIsoFull20Summary = $dualIsoSummaryLine
            lookAssistSettle = $lookAssistSettleLine
            lookAssistApply = $lookAssistApplyLine
            cpuSettle = $cpuSettleLine
        }
    }
}

$validationFailures = @()
$lookAssistApplied = $lookAssistApplyLine -and $lookAssistSettleLine
if ($lookAssistApplied) {
    $lookAssistSettle = Convert-PlaybackLogLineToObject $lookAssistSettleLine
    $lookAssistApply = Convert-PlaybackLogLineToObject $lookAssistApplyLine
    $lookAssistApplied =
        ($lookAssistSettle.enabled -eq 1) -and
        ($lookAssistSettle.diagnostics_valid -eq 1) -and
        (-not [string]::IsNullOrWhiteSpace([string]$lookAssistApply.scene))
}
if ($RequireLookAssist -and -not $lookAssistApplied) {
    $validationFailures += "Look Assist did not settle/apply before playback."
}

$cpuSettled = $true
if ($SettleCpuMaxMs -gt 0 -or $SettleCpuStableMs -gt 0) {
    $cpuSettled = $false
    if ($cpuSettleLine) {
        $cpuSettle = Convert-PlaybackLogLineToObject $cpuSettleLine
        $cpuSettled = ($cpuSettle.settled -eq 1)
    }
}
if ($RequireCpuSettled -and -not $cpuSettled) {
    $validationFailures += "CPU did not settle before playback."
}

$result | Add-Member -NotePropertyName validation -NotePropertyValue ([pscustomobject]@{
    ok = ($validationFailures.Count -eq 0)
    lookAssistApplied = [bool]$lookAssistApplied
    cpuSettled = [bool]$cpuSettled
    failures = $validationFailures
})

$result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $outputPath -Encoding UTF8
$result | ConvertTo-Json -Depth 8
if ($smokeMutexAcquired) {
    $smokeMutex.ReleaseMutex()
    $smokeMutex.Dispose()
}
if ($process.ExitCode -ne 0) {
    exit $process.ExitCode
}
if ($validationFailures.Count -gt 0) {
    foreach ($failure in $validationFailures) {
        Write-Error $failure
    }
    exit 2
}
exit 0
