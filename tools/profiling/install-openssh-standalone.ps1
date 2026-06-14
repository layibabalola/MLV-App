# install-openssh-standalone.ps1
# RUN ONCE ON ULTRA-MAGNUS, IN AN ADMINISTRATOR PowerShell.
# Replaces the broken inbox OpenSSH (sshd 7.7, missing its libcrypto -> loads the
# System32 LibreSSL 3.x which lacks BN_init) with the self-contained standalone
# OpenSSH 10.0 (bundled matching libcrypto). Then re-applies host keys, config,
# the VM public key, firewall, pwsh shell, and starts the service.

[CmdletBinding()]
param(
    [string]$Zip        = 'G:\Temp\mlv-gpu-profile\OpenSSH-Win64.zip',
    [string]$Dest       = 'C:\Program Files\OpenSSH',
    [string]$PubKeyFile = 'G:\Temp\mlv-gpu-profile\vm_authorized_key.pub'
)
$ErrorActionPreference = 'Stop'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) { throw 'Run this in an Administrator PowerShell.' }
if (-not (Test-Path $Zip)) { throw "Zip not found: $Zip" }

# 1) Remove the broken inbox OpenSSH services.
foreach ($svc in 'sshd','ssh-agent') {
    $s = Get-Service $svc -ErrorAction SilentlyContinue
    if ($s) {
        if ($s.Status -ne 'Stopped') { Stop-Service $svc -Force -ErrorAction SilentlyContinue }
        & sc.exe delete $svc | Out-Null
        Write-Host "removed inbox service: $svc"
    }
}
Start-Sleep -Seconds 2

# 2) Extract the standalone build to $Dest.
$tmp = Join-Path $env:TEMP 'osshx'
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
Expand-Archive -Path $Zip -DestinationPath $tmp -Force
$src = Join-Path $tmp 'OpenSSH-Win64'
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
Copy-Item "$src\*" $Dest -Recurse -Force
Write-Host ("extracted standalone -> {0} (sshd {1}, libcrypto {2})" -f $Dest,
    (Get-Item "$Dest\sshd.exe").VersionInfo.FileVersion,
    (Get-Item "$Dest\libcrypto.dll").VersionInfo.FileVersion)

# 3) Register the service via the bundled installer; verify, fall back to manual.
try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dest 'install-sshd.ps1') 2>&1 | Out-Host
} catch { Write-Warning "install-sshd.ps1: $($_.Exception.Message)" }
$svc = Get-CimInstance Win32_Service -Filter "Name='sshd'" -ErrorAction SilentlyContinue
if (-not $svc -or ($svc.PathName -notlike '*Program Files\OpenSSH*')) {
    Write-Host 'Registering sshd manually (bundled installer did not) ...'
    $bin = '"' + (Join-Path $Dest 'sshd.exe') + '"'
    & sc.exe create sshd binPath= $bin start= auto DisplayName= "OpenSSH SSH Server" obj= LocalSystem | Out-Host
}

# 4) Host keys: generate if missing, then lock to SYSTEM + Administrators only
#    (an explicit user ACE makes sshd reject the key).
& "$Dest\ssh-keygen.exe" -A 2>&1 | Out-Host
Get-ChildItem "$env:ProgramData\ssh\ssh_host_*_key" -ErrorAction SilentlyContinue | ForEach-Object {
    $kf = $_.FullName
    & icacls $kf /inheritance:r | Out-Null
    & icacls $kf /remove:g "$env:USERDOMAIN\$env:USERNAME" "Authenticated Users" "BUILTIN\Users" "Everyone" | Out-Null
    & icacls $kf /grant:r "NT AUTHORITY\SYSTEM:(F)" "BUILTIN\Administrators:(F)" | Out-Null
}

# 5) sshd_config: validate with the NEW sshd; if invalid, use the bundled default.
$cfg = "$env:ProgramData\ssh\sshd_config"
if (-not (Test-Path $cfg)) { Copy-Item (Join-Path $Dest 'sshd_config_default') $cfg -Force }
& "$Dest\sshd.exe" -t 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) {
    Copy-Item $cfg "$cfg.bak" -Force
    Copy-Item (Join-Path $Dest 'sshd_config_default') $cfg -Force
    Write-Host 'sshd_config was invalid for 10.0; replaced with bundled default (old -> .bak).'
    & "$Dest\sshd.exe" -t 2>&1 | Out-Host
}
& icacls $cfg /inheritance:r /grant:r "NT AUTHORITY\SYSTEM:(F)" "BUILTIN\Administrators:(F)" "NT AUTHORITY\Authenticated Users:(R)" | Out-Null

# 6) Default SSH shell = pwsh.
$shell = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
if (-not $shell -and (Test-Path "$env:ProgramFiles\PowerShell\7\pwsh.exe")) { $shell = "$env:ProgramFiles\PowerShell\7\pwsh.exe" }
if (-not $shell) { $shell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" }
New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' -Force | Out-Null
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -Value $shell -PropertyType String -Force | Out-Null
Write-Host "Default SSH shell: $shell"

# 7) Install the VM public key (admins use administrators_authorized_keys).
$pub = (Get-Content $PubKeyFile -Raw).Trim()
$adminKeys = "$env:ProgramData\ssh\administrators_authorized_keys"
if (-not ((Test-Path $adminKeys) -and (Select-String -Path $adminKeys -SimpleMatch $pub -Quiet))) {
    Add-Content -Path $adminKeys -Value $pub -Encoding ASCII
}
& icacls $adminKeys /inheritance:r /grant:r "NT AUTHORITY\SYSTEM:(F)" "BUILTIN\Administrators:(F)" | Out-Null
Write-Host "Key installed -> $adminKeys"

# 8) Firewall.
if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
    Write-Host 'Firewall rule added for TCP 22.'
}

# 9) Start.
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
Start-Sleep -Seconds 1

$ips = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^127\.' }).IPAddress -join ', '
$hk = "$env:ProgramData\ssh\ssh_host_ed25519_key.pub"
Write-Host ''
Write-Host '==================== SSH READY ===================='
Write-Host ("sshd       : {0} (v{1})" -f (Get-Service sshd).Status, (Get-Item "$Dest\sshd.exe").VersionInfo.FileVersion)
Write-Host ("host IPv4  : {0}" -f $ips)
if (Test-Path $hk) { Write-Host 'host key fingerprint:'; & "$Dest\ssh-keygen.exe" -lf $hk }
Write-Host '==================================================='
Write-Host 'VM can now connect passwordless. You are out of the loop.'
