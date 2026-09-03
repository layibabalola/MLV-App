<#
.SYNOPSIS
    Measure the auto-WB solve as a DISTRIBUTION across builds. Never quotes a single draw.

.DESCRIPTION
    WHY A DISTRIBUTION AND NOT A VALUE.
    MLVAPP_LOOK_ASSIST_WB_TRACE emits one WB_TRACE_WORKER line PER ANALYSIS, and a single run can
    emit several with DIFFERENT values - measured 2026-09-03, one run produced both 7330/-33 and
    7430/-34. So 'the last line' is an arbitrary pick, and a bisect built on single draws is a
    bisect built on noise. Every number this board published from that probe before this script
    existed was a draw.

    THE WIDTH IS THE SIGNAL, NOT AN ERROR BAR TO AVERAGE AWAY.
    Three runs each, same clip, same settings:
        a6bf25f9 (isolate thumbnail + WB analysis) -> -34,-33 / -34,-33 / -34,-33   spread  1
        aa0cab24 (wire isolated WB analysis)       -> -32,7 / 19,13 / -49,-28       spread 68
    The defect introduced at aa0cab24 is VARIANCE, not an offset. Averaging it away destroys the
    finding; reporting min/max/distinct preserves it.

    A clamp can hide this downstream: qBound(-35, tint, 18) at MainWindow.cpp:15567 emits a
    rock-steady -35 for any raw value below the floor, so post-clamp reporting can show a stable
    number over a wildly unstable solve. Always measure PRE-clamp.

.NOTES
    ASCII-only by project convention. Run ON the GPU bench. Uses the app's own capture path;
    never a desktop grab - the bench is a machine its owner uses interactively.
#>
# VALID MEASUREMENT of raw_wb_tint: a DISTRIBUTION, not a draw.
# Established 2026-09-03: the WB probe fires once per analysis and a single run emitted BOTH
# 7330/-33 and 7430/-34. Taking "the last line" is an arbitrary pick, so every earlier bisect
# number was a draw. This collects EVERY WB_TRACE_WORKER line across N runs per build and reports
# n / distinct / median / min / max. Bachelor is also thermally unstable, so aggregation is
# required on this venue regardless (bachelor-cannot-decide-playback-ab-20260830: 3 legs minimum).
$ErrorActionPreference='Continue'
$Root='C:\mlvtmp\mlv-agent'
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$script=Join-Path $Root 'staging\capture-reference-frame.ps1'
$clip=Join-Path $Root 'cache\M16-1243.MLV'
$base=Join-Path $Root ("outbox\wbdist-$stamp")
$env:MLVAPP_LOOK_ASSIST_WB_TRACE='1'
$Runs=3

$points=@(
 @{tag='765ed4a3d066-dirty'; label='Jun-09 GOOD (memo)';           dir='june-765ed4a3d066-dirty'},
 @{tag='d14139e8225e-dirty'; label='Jun-23 keep-LA-bright-diso';   dir='june-d14139e8225e-dirty'},
 @{tag='ba9dec3f3427';       label='Jun-27 tint-floor bandaid';    dir='june-ba9dec3f3427'},
 @{tag='5a30efddd186';       label='Sep-02 modern #25';            dir='mlvapp-5a30efddd186'}
)
function Stat($v){
  if(-not $v -or $v.Count -eq 0){ return $null }
  $s=@($v | Sort-Object); $n=$s.Count
  [ordered]@{ n=$n; distinct=@($v|Select-Object -Unique).Count; median=$s[[int][math]::Floor($n/2)]; min=$s[0]; max=$s[$n-1]
              values=((@($v|Select-Object -Unique|Sort-Object)) -join ',') }
}
$res=[ordered]@{schema='mlv-app/wbtint-distribution/v1'; host=$env:COMPUTERNAME; clip='M16-1243.MLV'; runsPerPoint=$Runs; points=@()}
foreach($p in $points){
  $exe=Join-Path $Root ("staging\" + $p.dir + "\MLVApp-" + $p.tag + ".exe")
  $row=[ordered]@{tag=$p.tag; label=$p.label}
  if(-not (Test-Path -LiteralPath $exe)){ $row.err='exe missing: '+$exe; $res.points+=$row; continue }
  $tints=@(); $temps=@(); $ok=0
  for($i=1;$i -le $Runs;$i++){
    $o=Join-Path $base ("{0}-r{1}" -f $p.tag,$i)
    & pwsh -NoProfile -File $script -Exe $exe -Clip $clip -OutDir $o -Commit $p.tag -Seconds 45 -PresentedFrames 0 2>&1 | Out-Null
    $ef=Join-Path $o 'capture.err.txt'
    if(Test-Path $ef){
      $ok++
      foreach($l in (Get-Content -LiteralPath $ef)){
        if($l -match 'WB_TRACE_WORKER.*raw_wb_temp=(-?[0-9]+) raw_wb_tint=(-?[0-9]+)'){
          $temps+=[int]$Matches[1]; $tints+=[int]$Matches[2] }
      }
    }
  }
  $row.runsWithOutput=$ok
  $row.tint=Stat $tints
  $row.temp=Stat $temps
  $res.points+=$row
}
Write-Output ($res | ConvertTo-Json -Depth 8 -Compress)
