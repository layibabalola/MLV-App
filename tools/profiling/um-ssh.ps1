# um-ssh.ps1
# RUN ON THE VM. Executes a command on Ultra-Magnus over SSH using the dedicated
# key (passwordless). The remote default shell is PowerShell (set by
# enable-openssh-admin.ps1), so -Command runs as PowerShell on the host.
#
# Usage:
#   .\um-ssh.ps1 -Command 'hostname; (Get-CimInstance Win32_VideoController).Name'
#   .\um-ssh.ps1 -Command 'powershell -File G:\Temp\mlv-gpu-profile\run-ultra-magnus-profile.ps1'

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Command,
    [string]$HostName = 'Ultra-Magnus',
    [string]$User = 'obabalola',
    [int]$ConnectTimeout = 10
)
$repo = Split-Path (Split-Path $PSScriptRoot)   # tools\profiling -> repo root
$key  = Join-Path $repo '.claude-state\ssh\ultra-magnus_id'
$khDir = Join-Path ([System.IO.Path]::GetTempPath()) 'mlv-app-ultra-magnus-ssh'
New-Item -ItemType Directory -Force -Path $khDir | Out-Null
$kh   = Join-Path $khDir 'known_hosts'
if (-not (Test-Path $key)) { throw "VM private key not found: $key" }

& ssh.exe -i $key `
    -o StrictHostKeyChecking=accept-new `
    -o "UserKnownHostsFile=$kh" `
    -o ConnectTimeout=$ConnectTimeout `
    -o BatchMode=yes `
    "$User@$HostName" $Command
exit $LASTEXITCODE
