param(
    [string]$RepoRoot = ".",
    [string]$WorkBlockId,
    [string]$CandidateId,
    [string]$ActionId,
    [string]$EvidenceHash,
    [string]$PinnedRefsFile,
    [string]$Reviewer = "codex",
    [switch]$Approve,
    [switch]$PrintTuple
)

$argsList = @("review-quorum", "--reviewer", $Reviewer)
if ($WorkBlockId) {
    $argsList += @("--work-block-id", $WorkBlockId)
}
if ($PrintTuple) {
    $argsList += "--print-tuple"
} else {
    $argsList += @(
        "--candidate-id", $CandidateId,
        "--action-id", $ActionId,
        "--evidence-hash", $EvidenceHash,
        "--pinned-refs-file", $PinnedRefsFile
    )
    if ($Approve) {
        $argsList += "--approve"
    }
}
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
