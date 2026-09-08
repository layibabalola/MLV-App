import hashlib, json, os, subprocess, sys, time
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CANDIDATE = ROOT / "tools" / "coordination" / "Invoke-Lane.ps1"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object contract")
PWSH = "pwsh.exe"


def wait_json(path, pred=lambda x: True, seconds=12):
    end=time.monotonic()+seconds; last=None
    while time.monotonic()<end:
        try:
            last=json.loads(path.read_text(encoding="utf-8"))
            if pred(last): return last
        except (FileNotFoundError,PermissionError,json.JSONDecodeError): pass
        time.sleep(.05)
    raise AssertionError(f"timeout {path}: {last!r}")


def identity(pid):
    q=f"$p=Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue;if($null-eq $p){{exit 3}};$p.StartTime.ToUniversalTime().ToString('o')"
    r=subprocess.run([PWSH,"-NoProfile","-NonInteractive","-Command",q],text=True,capture_output=True,timeout=5)
    return r.stdout.strip() if r.returncode==0 else None


def wait_absent(item, seconds=10):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if identity(item["pid"]) != item["createdUtc"]: return
        time.sleep(.05)
    raise AssertionError(f"still alive: {item}")


def stop_exact(item):
    if identity(item["pid"]) == item["createdUtc"]:
        subprocess.run([PWSH,"-NoProfile","-NonInteractive","-Command",f"Stop-Process -Id {int(item['pid'])} -Force"],timeout=5,check=False)


@pytest.fixture
def fixture_tree(tmp_path):
    grand=tmp_path/"grand.ps1"; child=tmp_path/"child.ps1"; shim=tmp_path/"fake-claude.cmd"
    grand.write_text("$me=Get-Process -Id $PID;@{pid=$PID;createdUtc=$me.StartTime.ToUniversalTime().ToString('o')}|ConvertTo-Json|Set-Content -Encoding utf8NoBOM $env:MLV_FIXTURE_GRAND;Start-Sleep -Seconds 60\n",encoding="ascii")
    child.write_text(r'''$ErrorActionPreference='Stop'
$me=Get-Process -Id $PID
@{pid=$PID;createdUtc=$me.StartTime.ToUniversalTime().ToString('o')}|ConvertTo-Json|Set-Content -Encoding utf8NoBOM $env:MLV_FIXTURE_CHILD
$args|ConvertTo-Json|Set-Content -Encoding utf8NoBOM $env:MLV_FIXTURE_ARGS
$env:CLAUDE_CODE_EFFORT_LEVEL|Set-Content -Encoding utf8NoBOM $env:MLV_FIXTURE_EFFORT
if($env:MLV_FIXTURE_MODE -ne 'normal'){
  $g=Start-Process pwsh.exe -ArgumentList @('-NoProfile','-NonInteractive','-File',$env:MLV_FIXTURE_GRAND_SCRIPT) -WindowStyle Hidden -PassThru
  while(-not(Test-Path $env:MLV_FIXTURE_GRAND)){Start-Sleep -Milliseconds 20}
}
$text=[Console]::In.ReadToEnd()
$text|Set-Content -Encoding utf8NoBOM $env:MLV_FIXTURE_PROMPT
if($env:MLV_FIXTURE_MODE -ne 'normal'){Start-Sleep -Seconds 60}
[Console]::Out.Write('{"result":"fixture-result","total_cost_usd":0,"num_turns":1}')
[Console]::Error.Write('fixture-err')
exit 0
''',encoding="ascii")
    shim.write_text(f'@echo off\r\n"{PWSH}" -NoProfile -NonInteractive -File "{child}" %*\r\n',encoding="ascii")
    yield {"root":tmp_path,"shim":shim,"child":child,"grand":grand}
    for name in ("child.json","grand.json"):
        p=tmp_path/name
        if p.exists():
            try: stop_exact(json.loads(p.read_text(encoding="utf-8-sig")))
            except Exception: pass


def prepare(tree, mode, assignment_failure=False, editing=False, allowed_tools="", lane="sonnet", mutation=None):
    root=tree["root"]; script=root/"Invoke-Lane.ps1"
    text=CANDIDATE.read_text(encoding="utf-8")
    text=text.replace("$CLAUDE_EXE = Join-Path $env:APPDATA 'npm\\claude.cmd'", "$CLAUDE_EXE = '"+str(tree['shim']).replace("'","''")+"'")
    text=text.replace("$CODEX_EXE  = Join-Path $env:APPDATA 'npm\\codex.cmd'", "$CODEX_EXE = '"+str(tree['shim']).replace("'","''")+"'")
    if assignment_failure:
        text=text.replace("[MlvLaneJob]::AssignOrThrow($jobHandle, $proc.Handle)", "throw [ComponentModel.Win32Exception]::new(5, 'fixture-assignment-failure')")
    if mutation: text=mutation(text)
    script.write_text(text,encoding="utf-8")
    (root/"lane-provider-refusal.ps1").write_bytes((ROOT/"tools"/"coordination"/"lane-provider-refusal.ps1").read_bytes())
    if editing:
        hook=root/"tools"/"hooks"/"mlv-never-authorized.py"; hook.parent.mkdir(parents=True); hook.write_text("# fixture hook\n",encoding="ascii")
        rec=root/".claude-state"/"coordination"/"dual-lane"/"receipts"/"0.05-hook-enforced.json"; rec.parent.mkdir(parents=True)
        rec.write_text(json.dumps({"hookSha256":hashlib.sha256(hook.read_bytes()).hexdigest()}),encoding="utf-8")
    run=root/"run"; run.mkdir()
    env=os.environ.copy(); env.update({
      "MLV_BOARD_ROOT":str(root),"MLV_FIXTURE_MODE":mode,
      "MLV_FIXTURE_CHILD":str(root/"child.json"),"MLV_FIXTURE_GRAND":str(root/"grand.json"),
      "MLV_FIXTURE_GRAND_SCRIPT":str(tree["grand"]),"MLV_FIXTURE_ARGS":str(root/"args.json"),
      "MLV_FIXTURE_PROMPT":str(root/"prompt.txt"),"MLV_FIXTURE_EFFORT":str(root/"effort.txt")})
    cmd=[PWSH,"-NoLogo","-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-File",str(script),"-Lane",lane,"-Prompt","fixture prompt","-WorkDir",str(root),"-RunDir",str(run),"-TimeoutSec","3" if mode=="timeout" else "30","-Card","FIXTURE","-ReasoningEffort","low"]
    if editing: cmd += ["-AllowEdits","-AllowedTools",allowed_tools]
    return cmd,env,run/(lane+"-001.receipt.json")


def test_read_only_argv_json_stdin_and_tool_denial(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8")); assert q["state"]=="complete" and q["complete"]
    assert q["containment"]["jobAssigned"] and q["containment"]["promptDelivered"]
    assert q["containment"]["childCreatedUtc"].endswith("Z") and "T" in q["containment"]["childCreatedUtc"]
    assert (fixture_tree["root"]/"prompt.txt").read_text(encoding="utf-8-sig").strip()=="fixture prompt"
    argv=json.loads((fixture_tree["root"]/"args.json").read_text(encoding="utf-8-sig"))
    i=argv.index("--disallowedTools"); assert argv[i+1]=="Agent,Task"
    j=argv.index("--allowedTools"); assert argv[j+1]=="Read,Grep,Glob"
    assert "--append-system-prompt" in argv
    notice=argv[argv.index("--append-system-prompt")+1]
    assert argv.count("--append-system-prompt")==1
    assert "only Read, Grep, and Glob" in notice and "do not call or retry" in notice
    assert q["authority"]["capabilityNotice"]==notice
    assert q["outputBytes"]>0 and q["spend"]["costUsd"]==0
    assert q["effort"]=="low"
    assert (fixture_tree["root"]/"effort.txt").read_text(encoding="utf-8-sig").strip()=="low"


def test_timeout_kills_owned_child_and_grandchild(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"timeout")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==124,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8")); assert q["state"]=="complete" and q["timedOut"]
    wait_absent(json.loads((fixture_tree["root"]/"child.json").read_text(encoding="utf-8-sig")))
    wait_absent(json.loads((fixture_tree["root"]/"grand.json").read_text(encoding="utf-8-sig")))


def test_owner_loss_closes_job(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"ownerloss")
    outer=subprocess.Popen(cmd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    q=wait_json(receipt,lambda x:x.get("state")=="running" and x.get("containment",{}).get("promptDelivered"))
    assert q["containment"]["childCreatedUtc"]==identity(q["containment"]["childPid"])
    child=wait_json(fixture_tree["root"]/"child.json"); grand=wait_json(fixture_tree["root"]/"grand.json")
    outer_identity={"pid":outer.pid,"createdUtc":identity(outer.pid)}
    stop_exact(outer_identity); outer.wait(timeout=8)
    wait_absent(child); wait_absent(grand)
    q=json.loads(receipt.read_text(encoding="utf-8")); assert q["state"]=="running" and not q["complete"]


def test_assignment_failure_starts_no_provider(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal",True)
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=15)
    assert r.returncode==127,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8")); assert q["state"]=="failed" and not q["complete"]
    assert not q["containment"]["jobAssigned"] and q["containment"]["assignmentErrorCode"]==5
    assert not (fixture_tree["root"]/"child.json").exists()
    assert not (fixture_tree["root"]/"prompt.txt").exists()
    wait_absent({"pid":q["containment"]["ownerPid"],"createdUtc":q["containment"]["ownerCreatedUtc"]})


def test_editing_argv_preserves_allowlist_and_denies_nested_tools(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    argv=json.loads((fixture_tree["root"]/"args.json").read_text(encoding="utf-8-sig"))
    assert argv[argv.index("--permission-mode")+1]=="acceptEdits"
    assert argv[argv.index("--allowedTools")+1]=="Read,Write,Edit"
    assert argv[argv.index("--disallowedTools")+1]=="Agent,Task"
    assert "--append-system-prompt" not in argv
    q=json.loads(receipt.read_text(encoding="utf-8")); assert q["authority"]["disallowedTools"]==["Agent","Task"]


@pytest.mark.parametrize("bad",["Agent"," task ","Read, AGENT ,Write","Read,Task"])
def test_editing_explicit_nested_tool_is_rejected_before_reservation(fixture_tree,bad):
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools=bad)
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=10)
    assert r.returncode!=0 and "nested-agent-tool-forbidden" in r.stderr
    assert not receipt.exists() and not (fixture_tree["root"]/"child.json").exists()


def test_codex_launch_stays_direct_without_claude_flags(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal",lane="sol")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    argv=json.loads((fixture_tree["root"]/"args.json").read_text(encoding="utf-8-sig"))
    assert argv[0]=="exec" and "--disallowedTools" not in argv and "--allowedTools" not in argv
    assert "--append-system-prompt" not in argv
    assert argv[argv.index("-s")+1]=="read-only"
    assert 'model_reasoning_effort=low' in argv or 'model_reasoning_effort="low"' in argv
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["containment"] is None and q["effort"]=="low" and q["complete"]


def test_startup_consumes_same_deadline_without_starting_provider(fixture_tree):
    def delay(text):
        return text.replace("$line=[Console]::In.ReadLine(); if([string]::IsNullOrWhiteSpace($line)){throw 'launch-frame-missing'}", "Start-Sleep -Seconds 8\n$line=[Console]::In.ReadLine(); if([string]::IsNullOrWhiteSpace($line)){throw 'launch-frame-missing'}")
    cmd,env,receipt=prepare(fixture_tree,"normal",mutation=delay)
    cmd[cmd.index("-TimeoutSec")+1]="1"
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=15)
    assert r.returncode==124,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["timedOut"]
    assert not (fixture_tree["root"]/"child.json").exists()
    wait_absent({"pid":q["containment"]["ownerPid"],"createdUtc":q["containment"]["ownerCreatedUtc"]})


@pytest.mark.parametrize("elapsed_ms,expected_exit", [(0, 0), (4000, 124)])
def test_setup_origin_and_expired_budget_are_deterministic(fixture_tree, elapsed_ms, expected_exit):
    marker = fixture_tree["root"] / "start-attempt.txt"
    def clocks(text):
        text = text.replace("$startedUtc = (Get-Date).ToUniversalTime()",
            "$startedUtc = [datetime]::Parse('2000-01-01T00:00:00Z').ToUniversalTime()", 1)
        old = "$sw         = [System.Diagnostics.Stopwatch]::StartNew()"
        assert text.count(old) == 1
        text = text.replace(old, "$sw = [pscustomobject]@{Elapsed=[timespan]::FromMilliseconds(%d)}\n$sw | Add-Member ScriptMethod Stop {}" % elapsed_ms)
        old = "$proc = [Diagnostics.Process]::Start($psi)"
        assert text.count(old) == 2
        return text.replace(old, "Write-Utf8NoBom '%s' 'start'\n    %s" % (str(marker).replace("'", "''"), old))
    cmd, env, receipt = prepare(fixture_tree, "normal", mutation=clocks)
    cmd[cmd.index("-TimeoutSec") + 1] = "3"
    result = subprocess.run(cmd, env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode == expected_exit, (result.stdout, result.stderr)
    assert marker.exists() == (expected_exit == 0)
    q = json.loads(receipt.read_text(encoding="utf-8"))
    assert datetime.fromisoformat(q["startedUtc"]) == datetime.fromisoformat("2000-01-01T00:00:00+00:00")
    assert datetime.fromisoformat(q["containment"]["deadlineUtc"]) == datetime.fromisoformat("2000-01-01T00:00:03+00:00")
    assert q["timedOut"] == (expected_exit == 124)
    if expected_exit == 124:
        assert not q["containment"]["jobAssigned"]
        assert q["containment"]["ownerPid"] is None
        assert not (fixture_tree["root"] / "child.json").exists()


def test_final_receipt_io_failure_cannot_keep_descendants_alive(fixture_tree):
    def fail_final_write(text):
        old='Write-Utf8NoBomAtomic $rcptPath (($receipt | ConvertTo-Json -Depth 6))'
        assert text.count(old)==1
        return text.replace(old,"throw 'fixture-final-receipt-write-failed'")
    cmd,env,receipt=prepare(fixture_tree,"timeout",mutation=fail_final_write)
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode!=0 and 'fixture-final-receipt-write-failed' in r.stderr
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q['state']=='running' and not q['complete']
    wait_absent(json.loads((fixture_tree["root"]/"child.json").read_text(encoding="utf-8-sig")))
    wait_absent(json.loads((fixture_tree["root"]/"grand.json").read_text(encoding="utf-8-sig")))
