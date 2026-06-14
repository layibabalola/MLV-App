# verify-exports.ps1 (RUN ON ULTRA-MAGNUS) - list the backend artifacts and the
# exported ABI symbols from the built DLL (proves the .def exported them).
param([string]$Dir = 'G:\Temp\mlv-gpu-profile\backend')
$ErrorActionPreference = 'Stop'

Get-ChildItem $Dir -File |
    Where-Object { $_.Name -match 'igpu_recon_cuda\.(dll|lib|exp)$|dll_test\.exe$' } |
    Select-Object Name, Length | Format-Table -AutoSize

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -property installationPath
$dumpbin = Get-ChildItem -Path (Join-Path $vs 'VC\Tools\MSVC') -Recurse -Filter dumpbin.exe -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\x64\\' } | Select-Object -First 1
if ($dumpbin) {
    Write-Host '--- exported symbols (dumpbin /EXPORTS) ---'
    & $dumpbin.FullName /EXPORTS (Join-Path $Dir 'igpu_recon_cuda.dll') |
        Select-String 'igpu_recon_'
} else {
    Write-Host 'dumpbin.exe not found'
}
