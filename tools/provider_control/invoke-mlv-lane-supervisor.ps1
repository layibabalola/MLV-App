param(
    [Parameter(Mandatory)][ValidateSet('tick','prelaunch','status','observe-signal')][string]$Command,
    [string]$Demand = '',
    [ValidateSet('AUTH_SUCCESS','RESET_OBSERVED','CAPACITY_RETURNED','QUOTA_REFUSAL')][string]$Event = '',
    [string]$StateRoot = 'C:\ProgramData\MLV-App\provider-control-v1'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$arguments = @('-3', (Join-Path $root 'mlv_lane_supervisor.py'), '--state-root', $StateRoot, $Command)
if ($Command -in @('tick','prelaunch')) { if (-not $Demand) { throw 'Demand is required' }; $arguments += @('--demand', $Demand) }
if ($Command -eq 'observe-signal') { if (-not $Event) { throw 'Event is required' }; $arguments += @('--event', $Event) }
& (Get-Command py.exe -ErrorAction Stop).Source @arguments
exit $LASTEXITCODE
