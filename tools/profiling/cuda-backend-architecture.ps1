function Get-CudaBackendArchitectureSidecarPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $directory = Split-Path -Parent $Path
    $name = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    if ([string]::IsNullOrWhiteSpace($directory)) {
        return "$name.arch.json"
    }
    Join-Path $directory "$name.arch.json"
}

function Find-CudaToolkitTool {
    param([Parameter(Mandatory = $true)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $roots = @()
    if (-not [string]::IsNullOrWhiteSpace($env:CUDA_PATH)) {
        $roots += $env:CUDA_PATH
    }
    $cudaRoot = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
    if (Test-Path -LiteralPath $cudaRoot) {
        $roots += @(Get-ChildItem -LiteralPath $cudaRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { $_.FullName })
    }

    foreach ($root in @($roots | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | Select-Object -Unique)) {
        $candidate = Join-Path $root "bin\$Name"
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    $null
}

function Get-CudaArchitectureTokensFromText {
    param([AllowNull()][string[]]$Text)

    @($Text |
        ForEach-Object { [string]$_ } |
        ForEach-Object { [regex]::Matches($_, "(?:sm|compute)_[0-9]{2,3}") } |
        ForEach-Object { $_.Value } |
        Sort-Object -Unique)
}

function New-CudaBackendArchitectureResult {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [bool]$Exists,
        [AllowNull()]$Length,
        [AllowNull()][string]$Sha256,
        [Parameter(Mandatory = $true)][string]$Detector,
        [AllowNull()][string[]]$Tokens,
        [bool]$DetectionReliable,
        [AllowNull()][string]$SidecarPath,
        [AllowNull()][string]$SidecarStatus,
        [AllowNull()][object]$Tool,
        [AllowNull()][object]$Raw,
        [Parameter(Mandatory = $true)][string]$Note
    )

    $normalizedTokens = @($Tokens |
        Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } |
        ForEach-Object { [string]$_ } |
        Sort-Object -Unique)
    [pscustomobject]@{
        schema = "mlvapp.cuda-backend-architecture.v2"
        path = $Path
        exists = $Exists
        length = $Length
        sha256 = $Sha256
        detector = $Detector
        tokens = @($normalizedTokens)
        hasSm86 = [bool]($normalizedTokens -contains "sm_86")
        hasSm89 = [bool]($normalizedTokens -contains "sm_89")
        dellAmpereReady = [bool]($normalizedTokens -contains "sm_86")
        ultraMagnusAdaReady = [bool](($normalizedTokens -contains "sm_89") -or ($normalizedTokens -contains "compute_89"))
        detectionReliable = $DetectionReliable
        sidecarPath = $SidecarPath
        sidecarStatus = $SidecarStatus
        tool = $Tool
        raw = $Raw
        note = $Note
    }
}

function Get-CudaBackendArchitectureFromSidecar {
    param(
        [Parameter(Mandatory = $true)][string]$ResolvedPath,
        [Parameter(Mandatory = $true)][long]$Length,
        [Parameter(Mandatory = $true)][string]$Sha256,
        [Parameter(Mandatory = $true)][string]$SidecarPath
    )

    if (!(Test-Path -LiteralPath $SidecarPath -PathType Leaf)) {
        return $null
    }

    try {
        $sidecar = Get-Content -LiteralPath $SidecarPath -Raw | ConvertFrom-Json
    }
    catch {
        return New-CudaBackendArchitectureResult `
            -Path $ResolvedPath `
            -Exists $true `
            -Length $Length `
            -Sha256 $Sha256 `
            -Detector "sidecar-invalid" `
            -Tokens @() `
            -DetectionReliable $false `
            -SidecarPath $SidecarPath `
            -SidecarStatus "invalid_json" `
            -Tool $null `
            -Raw ([pscustomobject]@{ error = $_.Exception.Message }) `
            -Note "CUDA backend architecture sidecar exists but is not valid JSON."
    }

    $schema = [string]$sidecar.schema
    if ($schema -ne "mlvapp.cuda-backend-architecture-sidecar.v1") {
        return New-CudaBackendArchitectureResult `
            -Path $ResolvedPath `
            -Exists $true `
            -Length $Length `
            -Sha256 $Sha256 `
            -Detector "sidecar-schema-mismatch" `
            -Tokens @() `
            -DetectionReliable $false `
            -SidecarPath $SidecarPath `
            -SidecarStatus "schema_mismatch" `
            -Tool $null `
            -Raw ([pscustomobject]@{ schema = $schema }) `
            -Note "CUDA backend architecture sidecar schema/version is not recognized."
    }

    $sidecarSha = [string]$sidecar.backend.sha256
    if ($sidecarSha -ne $Sha256) {
        return New-CudaBackendArchitectureResult `
            -Path $ResolvedPath `
            -Exists $true `
            -Length $Length `
            -Sha256 $Sha256 `
            -Detector "sidecar-hash-mismatch" `
            -Tokens @() `
            -DetectionReliable $false `
            -SidecarPath $SidecarPath `
            -SidecarStatus "hash_mismatch" `
            -Tool $null `
            -Raw ([pscustomobject]@{ sidecarSha256 = $sidecarSha }) `
            -Note "CUDA backend architecture sidecar does not match the DLL hash."
    }

    $tokens = @($sidecar.tokens | ForEach-Object { [string]$_ })
    New-CudaBackendArchitectureResult `
        -Path $ResolvedPath `
        -Exists $true `
        -Length $Length `
        -Sha256 $Sha256 `
        -Detector "hash-matched-sidecar" `
        -Tokens $tokens `
        -DetectionReliable ([bool]$sidecar.detectionReliable -and $tokens.Count -gt 0) `
        -SidecarPath $SidecarPath `
        -SidecarStatus "accepted" `
        -Tool $sidecar.tool `
        -Raw $sidecar.raw `
        -Note "Architecture tokens came from a hash-matched build-time cuobjdump sidecar."
}

function Get-CudaBackendArchitectureFromCuobjdump {
    param(
        [Parameter(Mandatory = $true)][string]$ResolvedPath,
        [Parameter(Mandatory = $true)][long]$Length,
        [Parameter(Mandatory = $true)][string]$Sha256,
        [AllowNull()][string]$SidecarPath,
        [AllowNull()][string]$SidecarStatus
    )

    $cuobjdump = Find-CudaToolkitTool -Name "cuobjdump.exe"
    if ([string]::IsNullOrWhiteSpace($cuobjdump)) {
        return $null
    }

    $listElf = @(& $cuobjdump --list-elf $ResolvedPath 2>&1 | ForEach-Object { [string]$_ })
    $listExit = $LASTEXITCODE
    $ptx = @(& $cuobjdump --dump-ptx $ResolvedPath 2>&1 | ForEach-Object { [string]$_ })
    $ptxExit = $LASTEXITCODE
    $ptxArchLines = @($ptx |
        Where-Object { $_ -match "\.(version|target)|arch\s*=\s*(?:sm|compute)_[0-9]{2,3}|(?:sm|compute)_[0-9]{2,3}" } |
        Select-Object -First 120)
    $tokens = Get-CudaArchitectureTokensFromText -Text (@($listElf) + @($ptxArchLines))
    $raw = [pscustomobject]@{
        listElfExit = $listExit
        listElf = @($listElf | Select-Object -First 120)
        dumpPtxExit = $ptxExit
        ptxArchLines = @($ptxArchLines)
    }

    New-CudaBackendArchitectureResult `
        -Path $ResolvedPath `
        -Exists $true `
        -Length $Length `
        -Sha256 $Sha256 `
        -Detector "cuobjdump" `
        -Tokens $tokens `
        -DetectionReliable ($listExit -eq 0 -and $tokens.Count -gt 0) `
        -SidecarPath $SidecarPath `
        -SidecarStatus $SidecarStatus `
        -Tool ([pscustomobject]@{ cuobjdump = $cuobjdump }) `
        -Raw $raw `
        -Note "Architecture tokens came from cuobjdump inspection of the DLL."
}

function Get-CudaBackendArchitectureInfo {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) {
        return New-CudaBackendArchitectureResult `
            -Path $Path `
            -Exists $false `
            -Length $null `
            -Sha256 $null `
            -Detector "missing" `
            -Tokens @() `
            -DetectionReliable $false `
            -SidecarPath (Get-CudaBackendArchitectureSidecarPath -Path $Path) `
            -SidecarStatus "backend_missing" `
            -Tool $null `
            -Raw $null `
            -Note "backend DLL missing"
    }

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $item = Get-Item -LiteralPath $resolved
    $sha256 = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash
    $sidecarPath = Get-CudaBackendArchitectureSidecarPath -Path $resolved
    $sidecarStatus = if (Test-Path -LiteralPath $sidecarPath -PathType Leaf) { "present" } else { "missing" }

    $sidecarResult = Get-CudaBackendArchitectureFromSidecar `
        -ResolvedPath $resolved `
        -Length $item.Length `
        -Sha256 $sha256 `
        -SidecarPath $sidecarPath
    if ($sidecarResult -and [bool]$sidecarResult.detectionReliable) {
        return $sidecarResult
    }
    if ($sidecarResult) {
        $sidecarStatus = [string]$sidecarResult.sidecarStatus
    }

    $cuobjdumpResult = Get-CudaBackendArchitectureFromCuobjdump `
        -ResolvedPath $resolved `
        -Length $item.Length `
        -Sha256 $sha256 `
        -SidecarPath $sidecarPath `
        -SidecarStatus $sidecarStatus
    if ($cuobjdumpResult -and [bool]$cuobjdumpResult.detectionReliable) {
        return $cuobjdumpResult
    }

    $bytes = [System.IO.File]::ReadAllBytes($resolved)
    $text = [System.Text.Encoding]::ASCII.GetString($bytes)
    $tokens = Get-CudaArchitectureTokensFromText -Text @($text)
    New-CudaBackendArchitectureResult `
        -Path $resolved `
        -Exists $true `
        -Length $item.Length `
        -Sha256 $sha256 `
        -Detector "ascii-token-scan" `
        -Tokens $tokens `
        -DetectionReliable $false `
        -SidecarPath $sidecarPath `
        -SidecarStatus $sidecarStatus `
        -Tool $null `
        -Raw ([pscustomobject]@{ fallbackTokenCount = $tokens.Count }) `
        -Note "ASCII token scanning is advisory only; embedded CUDA cubins can omit visible sm_* strings. Proof runners require a hash-matched sidecar or cuobjdump."
}
