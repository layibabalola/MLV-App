param(
    [string]$RepoRoot = ".",
    [string[]]$Arguments = @()
)

$ErrorActionPreference = "Stop"
$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot).Path
Push-Location -LiteralPath $resolvedRepo
try {
    $runner = @'
import sys
from tools.repo_hygiene.brokered_closeout import bounded_closeout_cli_main
raise SystemExit(bounded_closeout_cli_main(sys.argv[1:]))
'@
    $runnerArgs = @("--repo-root", ".", "--") + $Arguments
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & py -3 -c $runner @runnerArgs
    } else {
        & python -c $runner @runnerArgs
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
