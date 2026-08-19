param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName 'MLV-LaneIgnitionWatchdog' -ErrorAction SilentlyContinue
$result = [ordered]@{
    schema='mlv-provider-control-install-plan/v1'; candidateOnly=$true
    taskPresent=[bool]$task; taskEnabled=if($task){[bool]$task.Settings.Enabled}else{$false}
    currentAction=if($task){@($task.Actions|ForEach-Object{"$($_.Execute) $($_.Arguments)"})}else{@()}
    proposedAction='pwsh.exe -File C:\!Layi Wkspc\MLV-App\tools\provider_control\invoke-mlv-lane-supervisor.ps1 -Command tick -Demand <signed-demand>'
    proposedEnabled=$false
    rollback='Disable MLV-LaneIgnitionWatchdog; restore pre-install task XML; prove State=Disabled and Settings.Enabled=false.'
}
if($Apply){$result.status='REFUSED';$result.reason='INSTALL_NOT_IN_AUTHOR_SCOPE';$result|ConvertTo-Json -Depth 5;exit 2}
$result.status='AUDIT_ONLY';$result|ConvertTo-Json -Depth 5
