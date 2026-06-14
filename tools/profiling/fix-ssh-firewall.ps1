# fix-ssh-firewall.ps1
# RUN ON ULTRA-MAGNUS (Administrator). Opens inbound TCP 22 for sshd on all
# firewall profiles (the inbox FoD's rule was left disabled/wrong-profile) and
# reports listener + profile state. Writes a report to the SMB bundle.

$ErrorActionPreference = 'Continue'
$out = 'G:\Temp\mlv-gpu-profile\firewall-fix.txt'
$L = @()
$L += "=== ssh firewall fix $(Get-Date -Format o) host=$env:COMPUTERNAME ==="

$L += "`n--- existing inbound rules matching ssh/OpenSSH ---"
Get-NetFirewallRule -Direction Inbound -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -match 'ssh|OpenSSH' -or $_.Name -match 'ssh' } | ForEach-Object {
        $pf = $_ | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue
        $L += ("{0} | Enabled={1} Action={2} Profile={3} Port={4}" -f $_.DisplayName, $_.Enabled, $_.Action, $_.Profile, $pf.LocalPort)
        try { Set-NetFirewallRule -Name $_.Name -Enabled True -Profile Any -ErrorAction Stop; $L += "  -> enabled (all profiles)" } catch { $L += "  -> enable failed: $($_.Exception.Message)" }
    }

$L += "`n--- ensure a permissive inbound TCP 22 rule ---"
if (Get-NetFirewallRule -Name 'sshd-22-any' -ErrorAction SilentlyContinue) {
    Set-NetFirewallRule -Name 'sshd-22-any' -Enabled True -Profile Any
    $L += "updated existing sshd-22-any"
} else {
    New-NetFirewallRule -Name 'sshd-22-any' -DisplayName 'sshd TCP 22 (any)' -Direction Inbound `
        -Protocol TCP -LocalPort 22 -Action Allow -Enabled True -Profile Any | Out-Null
    $L += "created sshd-22-any"
}

$L += "`n--- network connection profiles ---"
Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object {
    $L += ("iface={0} category={1}" -f $_.InterfaceAlias, $_.NetworkCategory)
}

$L += "`n--- is sshd actually listening on 22? ---"
$lis = Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue
if ($lis) { $lis | ForEach-Object { $L += ("listen {0}:{1} pid={2}" -f $_.LocalAddress, $_.LocalPort, $_.OwningProcess) } }
else { $L += "NOT LISTENING on 22 (sshd may not be binding - check service)" }

$L += "`n--- sshd service ---"
$L += ((Get-Service sshd -ErrorAction SilentlyContinue | Format-List Name, Status, StartType | Out-String).Trim())

$L | Set-Content -Encoding ASCII $out
Write-Host "written $out"
Write-Host "`n===== summary ====="
$L | ForEach-Object { Write-Host $_ }
