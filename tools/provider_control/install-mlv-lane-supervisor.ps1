param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName 'MLV-LaneIgnitionWatchdog' -ErrorAction SilentlyContinue
$result = [ordered]@{
    schema='mlv-provider-control-install-plan/v1'; candidateOnly=$true
    taskPresent=[bool]$task; taskEnabled=if($task){[bool]$task.Settings.Enabled}else{$false}
    currentAction=if($task){@($task.Actions|ForEach-Object{"$($_.Execute) $($_.Arguments)"})}else{@()}
    proposedAction=$null
    proposedDemandAuthority='R16_BROKER_OWNED_DEMAND_PENDING_UNRATIFIED'
    proposedEnabled=$false
    rollback='Keep MLV-LaneIgnitionWatchdog Disabled; restore exact physical task-file bytes only through a separately reviewed installer; prove State=Disabled, Settings.Enabled=false, and gate=CLOSED.'
}
if($Apply){$result.status='REFUSED';$result.reason='INSTALL_NOT_IN_AUTHOR_SCOPE';$result|ConvertTo-Json -Depth 5;exit 2}
$result.status='AUDIT_ONLY';$result|ConvertTo-Json -Depth 5
