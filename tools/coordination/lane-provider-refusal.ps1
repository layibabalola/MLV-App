# Get-ProviderRefusal: classify a lane's raw output as a PROVIDER REFUSAL or not.
#
# WHY THIS EXISTS (incident 2026-09-07T01:28Z, receipt fleet-runs\20260907T012821Z):
# the codex lane `sol` was dispatched for round 3 of the PR #79 review and the provider
# refused it in 7.7 s -- "You've hit your usage limit ... try again at Sep 10th". The
# receipt recorded exitCode=1, failure=null, complete=true, state=complete. Every
# downstream reader (the heartbeat snapshot, a hub reading receipts) saw a FINISHED
# review whose verdict happened to be non-zero, not a review that NEVER RAN. The hub
# then idled for an hour. The same shape was measured 2026-09-05 on the claude engine
# (HTTP 429 "You've hit your session limit", two of twelve dispatches).
#
# A refusal is a third outcome beside "ran" and "threw": the process launched and exited
# cleanly, but the provider did no work. It is NOT a failure of the lane script (failure
# stays null) and it is NOT completion (complete becomes false). The remedy is outside
# the board -- for codex it is an account rotation the owner performs -- so the receipt
# has to say so in a field a machine can branch on.
#
# Dot-sourced by Invoke-Lane.ps1; exercised directly by test_coordination_guardrails.py
# against the real 2026-09-07 stderr, a known-good transcript, and prose that only QUOTES a refusal.
# ASCII-only by project convention.

function Get-ProviderRefusal {
    [CmdletBinding()]
    param(
        [AllowEmptyString()][AllowNull()][string]$Text,
        [string]$Engine = 'unknown',
        # The prompt that was sent. Codex ECHOES the prompt into stderr, so any line that
        # also appears in the prompt is the lane's INPUT, not the provider's answer.
        # sol PR #80 round 1 BLOCKER (2026-09-07): without this, a review that merely
        # quoted "You've hit your usage limit" classified itself as refused.
        [AllowEmptyString()][AllowNull()][string]$Prompt = ''
    )
    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }

    $promptLines = New-Object 'System.Collections.Generic.HashSet[string]'
    if (-not [string]::IsNullOrEmpty($Prompt)) {
        foreach ($pl in ($Prompt -split "`r?`n")) { [void]$promptLines.Add($pl.Trim()) }
    }

    # One vocabulary, one emitter. Each entry: kind, regex over a single line.
    # Kinds are deliberately few; a reader branches on `kind`, and the matched line is
    # carried verbatim so nothing is lost to the classification.
    $patterns = @(
        @{ kind = 'provider-usage-limit'; rx = "you'?ve hit your (usage|session|weekly|monthly) limit" },
        @{ kind = 'provider-usage-limit'; rx = 'purchase more credits' },
        @{ kind = 'provider-usage-limit'; rx = 'usage[ _-]limit[ _-]reached' },
        @{ kind = 'provider-rate-limit';  rx = '(api_error_status|http|status)[^0-9]{0,12}429\b' },
        @{ kind = 'provider-rate-limit';  rx = '\brate[ _-]limit(ed|s)?\b' },
        @{ kind = 'provider-auth';        rx = 'not logged in|invalid api key|authentication failed' }
    )

    # SCOPE. A refusal is something the PROVIDER SAID, and both engines say it in a
    # recognisable frame: codex prints `ERROR: ...` lines; claude --output-format json
    # returns an envelope with "is_error":true / api_error_status. Discussion of a refusal
    # (a prompt, a review, a quoted incident) never carries that frame, so only framed
    # lines are eligible for the vocabulary below. Unframed text is never a refusal.
    $frame = '^\s*ERROR\b|"is_error"\s*:\s*true|api_error_status'

    foreach ($line in ($Text -split "`r?`n")) {
        $t = $line.Trim()
        if ($t.Length -eq 0) { continue }
        if ($promptLines.Contains($t)) { continue }   # echoed input, not an answer
        if ($line -inotmatch $frame) { continue }      # unframed prose is never a refusal
        foreach ($p in $patterns) {
            if ($line -imatch $p.rx) {
                $retry = $null
                # Stop at a sentence end or a JSON quote: the claude engine wraps its message in JSON.
                if ($line -imatch 'try again (at|after|in) ([^."]+)') { $retry = $Matches[2].Trim() }
                $remedy = switch ($p.kind) {
                    'provider-usage-limit' { 'owner rotates the provider account; re-dispatch the same prompt after a probe' }
                    'provider-rate-limit'  { 'wait and re-dispatch the same prompt; no rotation implied' }
                    'provider-auth'        { 'owner re-authenticates the provider CLI; never an agent keystroke' }
                }
                return [ordered]@{
                    kind       = $p.kind
                    engine     = $Engine
                    match      = $line.Trim()
                    retryAfter = $retry
                    # What a reader should do. Stated in the receipt so the remedy travels
                    # with the evidence instead of living in one session's memory.
                    remedy     = $remedy
                }
            }
        }
    }
    return $null
}
