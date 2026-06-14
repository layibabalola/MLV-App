# diag-libcrypto.ps1
# RUN ON ULTRA-MAGNUS (Administrator). Pins down the sshd.exe / libcrypto DLL
# mismatch (BN_init entry-point error) and writes a report to the SMB bundle.

$ErrorActionPreference = 'Continue'
$out = 'G:\Temp\mlv-gpu-profile\libcrypto-diag.txt'
$osshDir = Join-Path $env:WINDIR 'System32\OpenSSH'
$L = @()
$L += "=== libcrypto diag $(Get-Date -Format o) host=$env:COMPUTERNAME ==="

$L += "`n--- contents of $osshDir ---"
Get-ChildItem $osshDir -ErrorAction SilentlyContinue | ForEach-Object {
    $v = (Get-Item $_.FullName).VersionInfo.FileVersion
    $L += ("{0,-34} {1,12} bytes  ver={2}" -f $_.Name, $_.Length, $v)
}

$L += "`n--- does the OpenSSH dir have its own libcrypto? ---"
$L += (Get-ChildItem "$osshDir\libcrypto*.dll" -ErrorAction SilentlyContinue | ForEach-Object { "$($_.FullName)  ver=$((Get-Item $_.FullName).VersionInfo.FileVersion)" })

$L += "`n--- libcrypto*.dll in System32 top-level (shadowing candidates) ---"
$L += (Get-ChildItem "$env:WINDIR\System32\libcrypto*.dll" -ErrorAction SilentlyContinue | ForEach-Object { "$($_.FullName)  ver=$((Get-Item $_.FullName).VersionInfo.FileVersion)" })

$L += "`n--- where.exe resolves libcrypto names (PATH order) ---"
foreach ($n in @('libcrypto.dll','libcrypto-1_1-x64.dll','libcrypto-3-x64.dll','libcrypto-3.dll')) {
    $L += "[$n]"
    $L += (& where.exe $n 2>&1)
}

$L += "`n--- sshd.exe / ssh.exe versions ---"
$L += ("sshd.exe ver=" + (Get-Item "$osshDir\sshd.exe" -ErrorAction SilentlyContinue).VersionInfo.FileVersion)

$L += "`n--- standalone OpenSSH already present? ---"
$L += ("C:\Program Files\OpenSSH exists: " + (Test-Path 'C:\Program Files\OpenSSH'))

$L += "`n--- machine PATH (look for what dropped a libcrypto) ---"
$L += ([Environment]::GetEnvironmentVariable('Path','Machine'))

$L | Set-Content -Encoding ASCII $out
Write-Host "written $out"
Write-Host "`n===== summary ====="
$L | Where-Object { $_ -match 'libcrypto|sshd.exe ver|OpenSSH exists' } | ForEach-Object { Write-Host $_ }
