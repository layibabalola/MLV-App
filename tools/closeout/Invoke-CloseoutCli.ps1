param(
    [string]$RepoRoot = ".",
    [string[]]$Arguments = @()
)

$ErrorActionPreference = "Stop"
$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot).Path
Push-Location -LiteralPath $resolvedRepo
try {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & py -3 -m tools.repo_hygiene.work_block_cli --repo-root . @Arguments
    } else {
        & python -m tools.repo_hygiene.work_block_cli --repo-root . @Arguments
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
