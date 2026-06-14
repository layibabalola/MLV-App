# diag-sshd.ps1
# RUN ON ULTRA-MAGNUS (Administrator). Diagnoses why sshd won't start and writes
# a full report to the SMB bundle so the VM can read it directly. Uses a fixed
# bundle path (no $PSScriptRoot dependency).

$ErrorActionPreference = 'Continue'
$out  = 'G:\Temp\mlv-gpu-profile\sshd-diag.txt'
$ossh = Join-Path $env:WINDIR 'System32\OpenSSH\sshd.exe'
$L = @()
$L += "=== sshd diagnostics $(Get-Date -Format o) host=$env:COMPUTERNAME ==="
$L += "sshd.exe present: $(Test-Path $ossh)"

$L += "`n--- sc qc sshd (config) ---";  $L += (& sc.exe qc sshd 2>&1)
$L += "`n--- sc query sshd (state) ---"; $L += (& sc.exe query sshd 2>&1)
$L += "`n--- sc queryex sshd (state + pid) ---"; $L += (& sc.exe queryex sshd 2>&1)

$L += "`n--- System log events mentioning sshd/OpenSSH ---"
try {
    Get-WinEvent -LogName System -MaxEvents 80 -ErrorAction SilentlyContinue |
        Where-Object { $_.Message -match 'sshd|OpenSSH' } | Select-Object -First 8 |
        ForEach-Object {
            $L += ("[{0}] id={1}" -f $_.TimeCreated, $_.Id)
            $L += (($_.Message -split "`r?`n") | Select-Object -First 6)
        }
} catch { $L += "system log read error: $($_.Exception.Message)" }

$L += "`n--- Application log events from sshd/OpenSSH ---"
try {
    Get-WinEvent -LogName Application -MaxEvents 80 -ErrorAction SilentlyContinue |
        Where-Object { $_.ProviderName -match 'sshd|OpenSSH' } | Select-Object -First 8 |
        ForEach-Object {
            $L += ("[{0}] provider={1} id={2}" -f $_.TimeCreated, $_.ProviderName, $_.Id)
            $L += (($_.Message -split "`r?`n") | Select-Object -First 10)
        }
} catch { $L += "app log read error: $($_.Exception.Message)" }

$L += "`n--- port 22 listeners ---"
try { Get-NetTCPConnection -LocalPort 22 -ErrorAction SilentlyContinue |
        ForEach-Object { $L += ("{0}:{1} state={2} pid={3}" -f $_.LocalAddress,$_.LocalPort,$_.State,$_.OwningProcess) } } catch {}

$L += "`n--- sshd -t (config syntax test) ---"
$L += (& $ossh -t 2>&1)

$L += "`n--- sshd -ddd -E (debug to file, 5s) ---"
try {
    $dlog = "$env:TEMP\sshd-ddd.log"
    Remove-Item $dlog -ErrorAction SilentlyContinue
    # -E writes the debug log straight to a file (reliable; the stderr redirect came back empty before).
    $p = Start-Process $ossh -ArgumentList @('-ddd','-E',$dlog) -NoNewWindow -PassThru
    Start-Sleep -Seconds 5
    if (-not $p.HasExited) { $p.Kill() }
    Start-Sleep -Milliseconds 400
    $L += (Get-Content $dlog -ErrorAction SilentlyContinue)
} catch { $L += "sshd -ddd run error: $($_.Exception.Message)" }

$L += "`n--- ProgramData ssh directory perms ---"
$L += (& icacls "$env:ProgramData\ssh" 2>&1)
$L += "`n--- ProgramData ssh logs directory perms ---"
$L += (& icacls "$env:ProgramData\ssh\logs" 2>&1)

$L += "`n--- sshd_config + its perms ---"
$L += (& icacls "$env:ProgramData\ssh\sshd_config" 2>&1)

$L += "`n--- C:\ProgramData\ssh\logs\sshd.log (tail) ---"
$L += (Get-Content "$env:ProgramData\ssh\logs\sshd.log" -Tail 30 -ErrorAction SilentlyContinue)

$L += "`n--- host key files + ACLs ---"
Get-ChildItem "$env:ProgramData\ssh\ssh_host_*" -ErrorAction SilentlyContinue | ForEach-Object {
    $L += $_.Name
    $L += ((& icacls $_.FullName 2>&1) -join "`n")
}

$L | Set-Content -Encoding ASCII $out
Write-Host "diag written to $out"
Write-Host "`n===== key lines ====="
$L | Where-Object { $_ -match 'fatal|error|denied|permission|bind|address|in use|bad owner|too open|cannot' } |
    Select-Object -First 25 | ForEach-Object { Write-Host $_ }
