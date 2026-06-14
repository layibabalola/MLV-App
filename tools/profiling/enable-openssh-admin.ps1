# enable-openssh-admin.ps1
# RUN ONCE ON ULTRA-MAGNUS, IN AN ADMINISTRATOR PowerShell.
# Installs + starts OpenSSH Server, registers the VM's public key for
# passwordless login, sets PowerShell as the default SSH shell, opens the
# firewall, and prints connection + host-key info. After this the VM drives the
# host over SSH with no further host-side action.
#
# Reads the VM public key from vm_authorized_key.pub next to this script.

[CmdletBinding()]
param(
    [string]$PubKeyFile
)
$ErrorActionPreference = 'Stop'

# Resolve the key path robustly. Some host PowerShell contexts leave
# $PSScriptRoot empty during param binding, so fall back through other anchors
# and finally require an explicit -PubKeyFile.
if (-not $PubKeyFile) {
    $scriptDir = $PSScriptRoot
    if (-not $scriptDir -and $PSCommandPath)               { $scriptDir = Split-Path -Parent $PSCommandPath }
    if (-not $scriptDir -and $MyInvocation.MyCommand.Path) { $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
    if (-not $scriptDir) { throw "Could not resolve script directory; re-run with -PubKeyFile <path to vm_authorized_key.pub>." }
    $PubKeyFile = Join-Path $scriptDir 'vm_authorized_key.pub'
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) { Write-Warning 'NOT ELEVATED - re-run this in an Administrator PowerShell, or it will fail.' }

# 1) Install OpenSSH Server (Feature on Demand).
try {
    $cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server*'
    if ($cap.State -ne 'Installed') {
        Write-Host 'Installing OpenSSH.Server ...'
        Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null
    } else { Write-Host 'OpenSSH.Server already installed.' }
} catch {
    throw "OpenSSH.Server install failed ($($_.Exception.Message)). If Windows Update is blocked, install the MSI from https://github.com/PowerShell/Win32-OpenSSH/releases then re-run."
}

# 1b) Ensure sshd_config exists. The FoD only materializes it on first successful
#     start, which never happened here, so create it from the template (or a
#     minimal fallback) and lock down its permissions.
$cfgDir = Join-Path $env:ProgramData 'ssh'
New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
$logDir = Join-Path $cfgDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Windows OpenSSH is sensitive to ProgramData\ssh ACLs. Keep private host keys
# strict below, but make the service/config/log directories match Microsoft's
# expected shape: SYSTEM + Administrators writable, Authenticated Users read-only.
foreach ($dir in @($cfgDir, $logDir)) {
    & icacls $dir /inheritance:r | Out-Null
    & icacls $dir `
        /grant:r "NT AUTHORITY\SYSTEM:(OI)(CI)(F)" `
                 "BUILTIN\Administrators:(OI)(CI)(F)" `
                 "NT AUTHORITY\Authenticated Users:(OI)(CI)(RX)" | Out-Null
}

$cfg = Join-Path $cfgDir 'sshd_config'
if (-not (Test-Path $cfg)) {
    $tmpl = Join-Path $env:WINDIR 'System32\OpenSSH\sshd_config_default'
    if (Test-Path $tmpl) {
        Copy-Item $tmpl $cfg -Force
        Write-Host "Created sshd_config from template."
    } else {
        @(
            'PubkeyAuthentication yes',
            'PasswordAuthentication no',
            'Subsystem sftp sftp-server.exe',
            'Match Group administrators',
            '       AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys'
        ) | Set-Content -Encoding ASCII $cfg
        Write-Host "Wrote minimal sshd_config (template not found)."
    }
}
& icacls $cfg /inheritance:r | Out-Null
& icacls $cfg `
    /grant:r "NT AUTHORITY\SYSTEM:(F)" `
             "BUILTIN\Administrators:(F)" `
             "NT AUTHORITY\Authenticated Users:(R)" | Out-Null
Write-Host "sshd_config ready: $cfg"

$syntax = & "$env:WINDIR\System32\OpenSSH\sshd.exe" -t 2>&1
if ($LASTEXITCODE -ne 0) {
    $syntaxText = ($syntax | Out-String).Trim()
    throw "sshd_config syntax/permission test failed: $syntaxText"
}
Write-Host 'sshd_config syntax test passed.'

# 2) Service auto-start (self-heal the common first-start failure: missing host
#    keys / wrong key permissions).
Set-Service -Name sshd -StartupType Automatic
$started = $false
try {
    Start-Service sshd -ErrorAction Stop
    $started = $true
} catch {
    Write-Warning "sshd did not start on first try: $($_.Exception.Message)"
    try {
        Get-WinEvent -FilterHashtable @{ LogName='Application'; ProviderName='sshd' } -MaxEvents 5 -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Host ("  [evt] {0}: {1}" -f $_.TimeCreated, (($_.Message -split "`r?`n")[0])) }
    } catch {}
    Write-Host 'Generating host keys + repairing permissions, then retrying ...'
    & "$env:WINDIR\System32\OpenSSH\ssh-keygen.exe" -A 2>&1 | Out-Host
    # sshd rejects any host key a non-privileged account can access. An explicit
    # user ACE survives /inheritance:r, so remove it (and common groups) explicitly,
    # leaving only SYSTEM + Administrators.
    Get-ChildItem "$env:ProgramData\ssh\ssh_host_*_key" -ErrorAction SilentlyContinue | ForEach-Object {
        $kf = $_.FullName
        & icacls $kf /inheritance:r | Out-Null
        & icacls $kf /remove:g "$env:USERDOMAIN\$env:USERNAME" "Authenticated Users" "BUILTIN\Users" "Everyone" | Out-Null
        & icacls $kf /grant:r "NT AUTHORITY\SYSTEM:(F)" "BUILTIN\Administrators:(F)" | Out-Null
    }
    Start-Sleep -Seconds 2
    Start-Service sshd -ErrorAction Stop
    $started = $true
}
if ($started) { Write-Host 'sshd started + set to Automatic.' }

# 3) Firewall (the FoD usually adds this; ensure it).
if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
    Write-Host 'Firewall rule added for TCP 22.'
} else { Write-Host 'Firewall rule for TCP 22 already present.' }

# 4) Default SSH shell = pwsh (fallback Windows PowerShell) so VM commands run in PS.
$shell = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
# Right after an MSI install PATH isn't refreshed in this session, so also check the well-known location.
if (-not $shell -and (Test-Path "$env:ProgramFiles\PowerShell\7\pwsh.exe")) { $shell = "$env:ProgramFiles\PowerShell\7\pwsh.exe" }
if (-not $shell) { $shell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" }
New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' -Force | Out-Null
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -Value $shell -PropertyType String -Force | Out-Null
Write-Host "Default SSH shell: $shell"

# 5) Install the VM public key.
if (-not (Test-Path $PubKeyFile)) { throw "Public key not found: $PubKeyFile" }
$pub = (Get-Content $PubKeyFile -Raw).Trim()

# administrators_authorized_keys is what sshd uses for admin users (per-user file is ignored for admins).
$adminKeys = Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
if (-not ((Test-Path $adminKeys) -and (Select-String -Path $adminKeys -SimpleMatch $pub -Quiet))) {
    Add-Content -Path $adminKeys -Value $pub -Encoding ASCII
}
icacls $adminKeys /inheritance:r /grant 'SYSTEM:F' /grant 'BUILTIN\Administrators:F' | Out-Null
Write-Host "Key installed -> $adminKeys"

# Per-user authorized_keys too (covers the non-admin case).
$userSsh = Join-Path $env:USERPROFILE '.ssh'
New-Item -ItemType Directory -Force -Path $userSsh | Out-Null
$userKeys = Join-Path $userSsh 'authorized_keys'
if (-not ((Test-Path $userKeys) -and (Select-String -Path $userKeys -SimpleMatch $pub -Quiet))) {
    Add-Content -Path $userKeys -Value $pub -Encoding ASCII
}
icacls $userKeys /inheritance:r /grant "$($env:USERNAME):F" /grant 'SYSTEM:F' | Out-Null
Write-Host "Key installed -> $userKeys"

# 6) Restart sshd to pick up shell + key changes.
Restart-Service sshd
Start-Sleep -Seconds 1

# 7) Report.
$ips = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^127\.' }).IPAddress -join ', '
$hostKey = Join-Path $env:ProgramData 'ssh\ssh_host_ed25519_key.pub'
Write-Host ''
Write-Host '==================== SSH READY ===================='
Write-Host ("host user : {0}" -f $env:USERNAME)
Write-Host ("host IPv4 : {0}" -f $ips)
Write-Host ("sshd      : {0}" -f (Get-Service sshd).Status)
if (Test-Path $hostKey) { Write-Host 'host key fingerprint:'; ssh-keygen -lf $hostKey }
Write-Host '==================================================='
Write-Host 'VM can now connect passwordless. You are out of the loop.'
