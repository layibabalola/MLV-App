param(
    [string]$RepoRoot = ".",
    [string]$WorkBlockId,
    [string]$CandidateId,
    [string]$ActionId,
    [string]$EvidenceHash,
    [string]$PinnedRefsFile,
    [string]$Reviewer = "codex",
    [switch]$Approve,
    [switch]$PrintTuple,
    [string]$Surface,
    [switch]$MarkSurfaceUnavailable,
    [string]$UnavailableReason = "surface could not perform required review",
    [string]$RecoveryCommand
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
    if ($MarkSurfaceUnavailable) {
        $argsList += "--mark-surface-unavailable"
        if ($Surface) {
            $argsList += @("--surface", $Surface)
        }
        if ($UnavailableReason) {
            $argsList += @("--unavailable-reason", $UnavailableReason)
        }
        if ($RecoveryCommand) {
            $argsList += @("--recovery-command", $RecoveryCommand)
        }
    }
}
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
