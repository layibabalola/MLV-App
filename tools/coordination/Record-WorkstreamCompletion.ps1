[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][string]$CardId,
 [Parameter(Mandatory=$true)][string]$LaneReceipt,
 [Parameter(Mandatory=$true)][string]$ReviewVerdictPath,
 [Parameter(Mandatory=$true)][string]$Worktree,
 [Parameter(Mandatory=$true)][string[]]$AllowedPath,
 [Parameter(Mandatory=$true)][string[]]$TestReceiptPath,
 [string[]]$ArtifactPath=@(),
 [Parameter(Mandatory=$true)][string]$OutputReceipt,
 [string]$PythonExecutable='python'
)
$ErrorActionPreference='Stop'
$scriptPath=Join-Path $PSScriptRoot 'record_workstream_completion.py'
$pythonArgv=@($scriptPath,'--card-id',$CardId,'--lane-receipt',$LaneReceipt,
 '--review-verdict',$ReviewVerdictPath,'--worktree',$Worktree,
 '--output-receipt',$OutputReceipt)
foreach($value in $AllowedPath){$pythonArgv+=@('--allowed-path',$value)}
foreach($value in $TestReceiptPath){$pythonArgv+=@('--test-receipt',$value)}
foreach($value in $ArtifactPath){$pythonArgv+=@('--artifact',$value)}
& $PythonExecutable @pythonArgv
$code=$LASTEXITCODE
if($null -eq $code){$code=1}
exit $code
