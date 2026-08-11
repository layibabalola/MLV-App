# write-verified-json.ps1 - pre-image-pinned, diff-before-install, parse-checked, byte-verified
# writer. Generalizes the guarantees append-ledger.ps1 (EOF byte-verify) and renew-lease.ps1
# (seat gate, stamp-vs-mtime verify) already embody, for any target file a caller wants to
# overwrite safely -- not just ledgers and leases. (QUEUE-DERIVE-1, fable SEQ 1297.)
#
# Guarantees, each one earned by a recorded incident on this board:
# (1) PRE-IMAGE PIN. Refuses to write unless the file's CURRENT sha256 matches
#     -ExpectedCurrentSha256 (or, for a brand-new file, -ExpectedCurrentSha256 is the literal
#     string 'ABSENT'). NOTE what this does and does not prove: a hash pin protects against a
#     STALE BASE -- someone else wrote the file after you last read it -- it does NOT protect
#     against an OVER-BROAD EDIT within a base you correctly pinned. That is what (2) is for; the
#     two are not substitutes for each other.
# (2) STATED DIFF BEFORE INSTALL. Prints a line-level diff (old vs new) to stdout BEFORE any
#     write happens, so an over-broad edit is visible before it lands rather than discovered
#     after a review of the resulting file.
# (3) PARSE CHECK. If the target looks like JSON (.json extension, or -ForceJsonCheck is passed),
#     the new content must parse via ConvertFrom-Json before anything is written; malformed JSON
#     is refused, not written-then-discovered-broken.
# (4) BYTE READ-BACK. After writing, re-reads the file from disk and byte-compares it against the
#     intended new content. A write that did not land exactly as intended is reported as a
#     failure, never silently accepted on the strength of the OS call returning without error.
#
# SEAT-GATING IS DELIBERATELY OUT OF SCOPE. append-ledger.ps1 and renew-lease.ps1 already gate
# their specific targets (ledgers, leases) against the seat registry; this primitive generalizes
# the WRITE-INTEGRITY guarantees those two scripts embody, for targets that have no lane-seat
# concept of their own (e.g. queue.json, which many actors legitimately write). A caller that
# wants seat-gating on top of this composes its own check before invoking it -- this script does
# not invent a second, competing gate.
#
# Usage:
#   pwsh -File write-verified-json.ps1 -Path <file> -ExpectedCurrentSha256 <hex|ABSENT> -NewContentFile <path> [-Note "..."]
#   pwsh -File write-verified-json.ps1 -Path <file> -ExpectedCurrentSha256 <hex|ABSENT> -NewContent <string> [-Note "..."]
#
# Exit codes: 0 ok; 10 pre-image mismatch (or missing/present when the other was expected);
#             11 lock timeout; 12 parse-check failed; 13 bad args/io; 14 post-write byte-verify failed.
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$ExpectedCurrentSha256,
    [string]$NewContent,
    [string]$NewContentFile,
    [string]$Note = "",
    [switch]$ForceJsonCheck,
    [switch]$SkipJsonCheck,
    [int]$LockTimeoutSeconds = 30
)
$ErrorActionPreference = 'Stop'

try {
    $haveInline = -not [string]::IsNullOrEmpty($NewContent)
    $haveFile   = -not [string]::IsNullOrEmpty($NewContentFile)
    if ($haveInline -eq $haveFile) {
        [Console]::Error.WriteLine("write-verified-json: supply exactly one of -NewContent / -NewContentFile")
        exit 13
    }
    if ($haveFile -and -not (Test-Path $NewContentFile)) {
        [Console]::Error.WriteLine("write-verified-json: missing -NewContentFile: $NewContentFile")
        exit 13
    }

    $newText = if ($haveFile) { [System.IO.File]::ReadAllText($NewContentFile) } else { $NewContent }
    if ([string]::IsNullOrEmpty($newText)) {
        [Console]::Error.WriteLine("write-verified-json: new content is empty")
        exit 13
    }

    $exists = Test-Path $Path
    $expectedNorm = $ExpectedCurrentSha256.ToUpperInvariant()
    if (-not $exists) {
        if ($expectedNorm -ne 'ABSENT') {
            [Console]::Error.WriteLine("PRE-IMAGE MISMATCH: target does not exist but -ExpectedCurrentSha256 was not 'ABSENT'. Refused -- pass -ExpectedCurrentSha256 ABSENT to create a new file.")
            exit 10
        }
    } else {
        if ($expectedNorm -eq 'ABSENT') {
            [Console]::Error.WriteLine("PRE-IMAGE MISMATCH: -ExpectedCurrentSha256 was 'ABSENT' but the target already exists. Refused -- read the current file and pass its real sha256.")
            exit 10
        }
        $actualHash = (Get-FileHash -Path $Path -Algorithm SHA256).Hash
        if ($actualHash -ne $expectedNorm) {
            [Console]::Error.WriteLine("PRE-IMAGE MISMATCH: expected=$expectedNorm actual=$actualHash. Refused -- your base is stale, re-read the file before writing.")
            exit 10
        }
    }

    # ---- exclusive lock, same convention as append-ledger.ps1 (create-new semantics, stale
    # locks older than 5 minutes are broken with a warning rather than left to jam forever) ----
    $lockPath = "$Path.write-lock"
    $deadline = (Get-Date).AddSeconds($LockTimeoutSeconds)
    $locked = $false
    while (-not $locked) {
        try {
            $fs = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
            $lockBytes = [System.Text.Encoding]::UTF8.GetBytes("$env:USERNAME $((Get-Date).ToUniversalTime().ToString('o'))")
            $fs.Write($lockBytes, 0, $lockBytes.Length); $fs.Flush(); $fs.Close()
            $locked = $true
        } catch {
            if ((Test-Path $lockPath) -and ((Get-Date) - (Get-Item $lockPath).LastWriteTime).TotalMinutes -gt 5) {
                Write-Warning "breaking stale write-lock (>5 min old): $lockPath"
                Remove-Item $lockPath -Force -Confirm:$false -ErrorAction SilentlyContinue
                continue
            }
            if ((Get-Date) -gt $deadline) {
                [Console]::Error.WriteLine("write-verified-json: lock timeout on $lockPath")
                exit 11
            }
            Start-Sleep -Milliseconds 250
        }
    }
    try {
        # ---- re-verify the pre-image INSIDE the lock: closes the TOCTOU gap between the check
        # above and taking the lock, which is exactly the gap a competing writer could land in ----
        if ($exists) {
            $actualHash2 = (Get-FileHash -Path $Path -Algorithm SHA256).Hash
            if ($actualHash2 -ne $expectedNorm) {
                [Console]::Error.WriteLine("PRE-IMAGE MISMATCH (post-lock re-check): the file changed between your read and this write. Refused.")
                exit 10
            }
        }

        # ---- parse check ----
        $looksJson = ($Path.ToLowerInvariant().EndsWith('.json')) -or $ForceJsonCheck
        if ($looksJson -and -not $SkipJsonCheck) {
            try {
                $null = $newText | ConvertFrom-Json -ErrorAction Stop
            } catch {
                [Console]::Error.WriteLine("PARSE CHECK FAILED: new content is not valid JSON ($($_.Exception.Message)). Refused, nothing written.")
                exit 12
            }
        }

        # ---- stated diff before install ----
        # NOTE (QUEUE-DERIVE-1 review, LANE-4 b6e24c75, BLOCKING [1]): PowerShell unrolls a
        # one-element array returned from an `if` expression into a scalar String, so a
        # single-line target (e.g. this board's one-line lease files) silently turned
        # $oldLines into a String and $oldLines[0] into its first CHARACTER, not its first
        # line -- printing a plausible-looking one-character diff with no error. [string[]]
        # forces the array type regardless of element count.
        [string[]]$oldLines = if ($exists) { [System.IO.File]::ReadAllLines($Path) } else { @() }
        $newLines = $newText -split "`r?`n"
        Write-Output "=== DIFF (old -> new): $Path ==="
        if ($Note) { Write-Output "note: $Note" }
        $max = [Math]::Max($oldLines.Count, $newLines.Count)
        $shown = 0
        for ($i = 0; $i -lt $max; $i++) {
            $o = if ($i -lt $oldLines.Count) { $oldLines[$i] } else { $null }
            $n = if ($i -lt $newLines.Count) { $newLines[$i] } else { $null }
            if ($o -ne $n) {
                if ($null -ne $o) { Write-Output ("- {0}: {1}" -f ($i + 1), $o) }
                if ($null -ne $n) { Write-Output ("+ {0}: {1}" -f ($i + 1), $n) }
                $shown++
                if ($shown -ge 200) { Write-Output "... (diff truncated at 200 changed lines)"; break }
            }
        }
        if ($shown -eq 0) { Write-Output "(no line-level differences)" }
        Write-Output ("=== END DIFF ({0} old lines, {1} new lines) ===" -f $oldLines.Count, $newLines.Count)

        # ---- write via temp file in the same directory + same-volume rename (atomic-ish: no
        # window where the target is truncated-but-not-yet-fully-written) ----
        $newBytes = [System.Text.Encoding]::UTF8.GetBytes($newText)
        $tmpPath = "$Path.tmp-$PID"
        [System.IO.File]::WriteAllBytes($tmpPath, $newBytes)
        Move-Item -Path $tmpPath -Destination $Path -Force

        # ---- byte read-back verify ----
        $postBytes = [System.IO.File]::ReadAllBytes($Path)
        if ($postBytes.Length -ne $newBytes.Length) {
            [Console]::Error.WriteLine("write-verified-json: POST-WRITE VERIFY FAILED (length mismatch, expected=$($newBytes.Length) actual=$($postBytes.Length))")
            exit 14
        }
        for ($i = 0; $i -lt $newBytes.Length; $i++) {
            if ($postBytes[$i] -ne $newBytes[$i]) {
                [Console]::Error.WriteLine("write-verified-json: POST-WRITE VERIFY FAILED (byte mismatch at offset $i)")
                exit 14
            }
        }
        $finalHash = (Get-FileHash -Path $Path -Algorithm SHA256).Hash
        Write-Output ("WRITE-OK path={0} bytes={1} sha256={2} at={3}" -f $Path, $postBytes.Length, $finalHash, (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))
        exit 0
    } finally {
        Remove-Item $lockPath -Force -Confirm:$false -ErrorAction SilentlyContinue
    }
} catch {
    [Console]::Error.WriteLine("write-verified-json failure: $($_.Exception.Message)")
    exit 13
}
