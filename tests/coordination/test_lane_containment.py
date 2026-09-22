import hashlib, importlib.util, json, os, re, shutil, subprocess, sys, time
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CANDIDATE = ROOT / "tools" / "coordination" / "Invoke-Lane.ps1"
DOC = ROOT / "docs" / "lane-containment.md"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object contract")
PWSH = "pwsh.exe"

# PR #105 round 4: Invoke-Lane.ps1 classifies containment.ownerAbsentReason into a
# CLOSED set of fixed tokens, chosen by WHERE a failure happened rather than by what
# its raw exception message said -- free text is not admissible evidence about a
# safety property. This test file cannot import a .ps1 file, so the tokens are
# pinned here as literals; keep them in sync BY HAND with Invoke-Lane.ps1's own
# $OWNER_ABSENT_* constants (declared beside $hostStarted, ~line 337-346).
NO_HOST_TOKENS = {"launch-budget-exhausted", "start-threw"}
POST_START_UNRECORDED = "post-start-unrecorded"

# PR #105 final: containment.ownerKillOutcome tokens, same hand-duplication problem
# as NO_HOST_TOKENS above -- kept in sync BY HAND with Invoke-Lane.ps1's own
# $OWNER_KILL_OUTCOME_* constants (declared beside $ownerKillAttempted, ~line 365-372).
# The cross-family review that added kill-wait-timeout noted this duplication drifts
# silently unless something pins the full set; test_kill_outcome_token_set_matches_
# producer_constants below asserts this literal against the source directly instead
# of trusting the hand-copy.
KILL_OUTCOME_TOKENS = {"already-exited", "killed", "kill-wait-timeout", "kill-threw"}

# LANE-NO-BACKGROUND-END-TURN-1: the deny list now blocks every tool that hands a
# headless lane a callback it has no later turn to receive, on top of the pre-existing
# nested-agent-fanout denial. Kept as one named constant instead of a literal per
# assertion site so this file has exactly one place to update if the list changes.
# Round 3 (fable minor 1): Workflow (background-orchestrated fan-out) and TaskCreate (the
# same background-promise shape as ScheduleWakeup/CronCreate) joined the list.
DISALLOWED_TOOLS_TOKEN = "Agent,Task,Monitor,ScheduleWakeup,CronCreate,CronDelete,RemoteTrigger,Workflow,TaskCreate"

_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
                 7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve"}


def assert_owner_absence_is_legitimate(containment, context):
    # The one place round-3, round-4, and round-5 tests all funnel through. Since
    # PR #105 round 5, a POST_START_UNRECORDED receipt carries a NON-NULL ownerPid
    # (captured on its own non-throwing line before the construction that failed),
    # so ownerPid presence/absence no longer distinguishes the states -- only
    # ownerAbsentReason does. See Invoke-Lane.ps1's catch block (~line 679-711, and
    # the pre-assignment kill block around ~line 674-696) for the producer side.
    assert containment is not None, f"ambiguous containment receipt: containment itself is None: {context}"
    reason = containment.get("ownerAbsentReason")
    if reason in NO_HOST_TOKENS:
        assert containment.get("ownerPid") is None, (
            f"a no-host token must never carry a pid -- no host ever existed: {context}"
        )
        return  # legitimate: no host was ever created
    pytest.fail(f"ambiguous or unrecognised containment receipt: ownerAbsentReason is {reason!r}, which is "
                f"not one of the closed-set no-host tokens {NO_HOST_TOKENS!r} -- either it is "
                f"{POST_START_UNRECORDED!r} (a host EXISTED; its pid is recorded so the orphan is never "
                f"invisible, but the receipt is still ambiguous and must never be treated as a legitimate "
                f"absence) or it is unrecognised entirely: {context}")


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
    # 1 s -TimeoutSec deadline race (evidence 2026-09-09): the fallback receipt
    # built in Invoke-Lane.ps1's catch block can carry containment.ownerPid=None
    # when the deadline fires before a contained host was ever started. There is
    # no process to look up in that case, so return None instead of int(None).
    if pid is None: return None
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
$env:CLAUDE_CODE_DISABLE_BACKGROUND_TASKS|Set-Content -Encoding utf8NoBOM $env:MLV_FIXTURE_BGTASKS
# LANE-NO-BACKGROUND-END-TURN-1 round 2: simulate a lane that edits a tracked file WITHOUT
# committing (env-gated no-op for every other test).
if($env:MLV_FIXTURE_DIRTY_TRACKED_PATH){
  'lane-edit-uncommitted'|Set-Content -Encoding utf8NoBOM $env:MLV_FIXTURE_DIRTY_TRACKED_PATH
}
# Round 3 (sol minor / fable minor 2): simulate a lane that FURTHER edits a tracked file the
# test already dirtied BEFORE launch, without ever staging or committing either edit -- the
# porcelain status line for this path (' M path') is identical before and after, so only a
# content-identity comparison can see the lane's own edit landed on top of the pre-existing one.
if($env:MLV_FIXTURE_FURTHER_EDIT_TRACKED_PATH){
  'lane-further-edit-uncommitted'|Set-Content -Encoding utf8NoBOM $env:MLV_FIXTURE_FURTHER_EDIT_TRACKED_PATH
}
# Round 5 (sol minor 2, required case): further-edit a BINARY tracked file (contains a NUL byte,
# so git renders `git diff HEAD -- path` as the fixed text "Binary files a/path and b/path
# differ" no matter what the actual bytes are) with DIFFERENT bytes than whatever pre-dirtied it.
# A content-identity check built on a hash of that diff TEXT cannot see this edit at all -- the
# rendered text is identical regardless of which binary bytes are on disk. A check built on
# `git hash-object` of the working-tree bytes themselves can.
if($env:MLV_FIXTURE_FURTHER_EDIT_BINARY_TRACKED_PATH){
  [byte[]]$bytes=@(0x00,0x11,0x22,0x33,0x44,0x55,0xEE,0xFF)
  [IO.File]::WriteAllBytes($env:MLV_FIXTURE_FURTHER_EDIT_BINARY_TRACKED_PATH,$bytes)
}
# Simulate a lane that commits its edit (HEAD moves) and then leaves a further,
# still-uncommitted edit behind on top of that commit.
if($env:MLV_FIXTURE_COMMIT_TRACKED_PATH){
  $repoDir=Split-Path $env:MLV_FIXTURE_COMMIT_TRACKED_PATH -Parent
  'lane-edit-committed'|Set-Content -Encoding utf8NoBOM $env:MLV_FIXTURE_COMMIT_TRACKED_PATH
  git -C $repoDir add -A | Out-Null
  git -C $repoDir commit -q -m 'fixture lane commit' | Out-Null
  'leftover-dirty-after-commit'|Set-Content -Encoding utf8NoBOM $env:MLV_FIXTURE_COMMIT_TRACKED_PATH
}
# Round 8 (sol minor): simulate a lane that reaches out via its own Bash/Write grant and
# rewrites, or deletes, the per-run hook copy AFTER the launch self-test already proved it
# denies -- the exact tamper shape the post-run re-hash check exists to catch. The copy's path
# is not passed to this fixture directly (env-gated, like the other MLV_FIXTURE_* blocks), so
# it is located the same way the test itself locates it: the one *.lane-no-background.py file
# reserved beside the receipt in the run dir.
if($env:MLV_FIXTURE_TAMPER_HOOK_COPY){
  $hookCopy=Get-ChildItem -Path (Join-Path $env:MLV_BOARD_ROOT 'run') -Filter '*.lane-no-background.py' | Select-Object -First 1
  if($env:MLV_FIXTURE_TAMPER_HOOK_COPY -eq 'modify'){
    '# tampered'|Set-Content -Encoding utf8NoBOM $hookCopy.FullName
  } elseif($env:MLV_FIXTURE_TAMPER_HOOK_COPY -eq 'delete'){
    Remove-Item -LiteralPath $hookCopy.FullName -Force
  }
}
if($env:MLV_FIXTURE_MODE -ne 'normal'){
  $g=Start-Process pwsh.exe -ArgumentList @('-NoProfile','-NonInteractive','-File',$env:MLV_FIXTURE_GRAND_SCRIPT) -WindowStyle Hidden -PassThru
  while(-not(Test-Path $env:MLV_FIXTURE_GRAND)){Start-Sleep -Milliseconds 20}
}
$text=[Console]::In.ReadToEnd()
$text|Set-Content -Encoding utf8NoBOM $env:MLV_FIXTURE_PROMPT
if($env:MLV_FIXTURE_MODE -ne 'normal'){Start-Sleep -Seconds 60}
[Console]::Out.Write('{"type":"result","subtype":"success","is_error":false,"terminal_reason":"completed","result":"fixture-result","total_cost_usd":0,"num_turns":1}')
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


def prepare(tree, mode, assignment_failure=False, editing=False, allowed_tools="", lane="sonnet", mutation=None, allow_bulk_reads=False):
    root=tree["root"]; script=root/"Invoke-Lane.ps1"
    text=CANDIDATE.read_text(encoding="utf-8")
    text=text.replace("$CLAUDE_EXE = Join-Path $env:APPDATA 'npm\\claude.cmd'", "$CLAUDE_EXE = '"+str(tree['shim']).replace("'","''")+"'")
    text=text.replace("$CODEX_EXE  = Join-Path $env:APPDATA 'npm\\codex.cmd'", "$CODEX_EXE = '"+str(tree['shim']).replace("'","''")+"'")
    if assignment_failure:
        text=text.replace("[MlvLaneJob]::AssignOrThrow($jobHandle, $proc.Handle)", "throw [ComponentModel.Win32Exception]::new(5, 'fixture-assignment-failure')")
    if mutation: text=mutation(text)
    script.write_text(text,encoding="utf-8")
    (root/"lane-provider-refusal.ps1").write_bytes((ROOT/"tools"/"coordination"/"lane-provider-refusal.ps1").read_bytes())
    (root/"lane-no-background.py").write_bytes((ROOT/"tools"/"coordination"/"lane-no-background.py").read_bytes())
    if editing:
        hook=root/"tools"/"hooks"/"mlv-never-authorized.py"; hook.parent.mkdir(parents=True); hook.write_text("# fixture hook\n",encoding="ascii")
        rec=root/".claude-state"/"coordination"/"dual-lane"/"receipts"/"0.05-hook-enforced.json"; rec.parent.mkdir(parents=True)
        rec.write_text(json.dumps({"hookSha256":hashlib.sha256(hook.read_bytes()).hexdigest()}),encoding="utf-8")
    run=root/"run"; run.mkdir()
    env=os.environ.copy(); env.update({
      "MLV_BOARD_ROOT":str(root),"MLV_FIXTURE_MODE":mode,
      "MLV_FIXTURE_CHILD":str(root/"child.json"),"MLV_FIXTURE_GRAND":str(root/"grand.json"),
      "MLV_FIXTURE_GRAND_SCRIPT":str(tree["grand"]),"MLV_FIXTURE_ARGS":str(root/"args.json"),
      "MLV_FIXTURE_PROMPT":str(root/"prompt.txt"),"MLV_FIXTURE_EFFORT":str(root/"effort.txt"),
      "MLV_FIXTURE_BGTASKS":str(root/"bgtasks.txt"),
      # Round 10 (sol major 1b): pinned here, not left to whatever Python the launcher's own
      # known-locations/PATH search happens to find on the machine running this suite -- this
      # test process's OWN interpreter is by definition present and working, so every fixture
      # test that does not override MLV_LANE_PYTHON_EXE itself is deterministic regardless of
      # whether the host has a "board Python" installed at the dev-machine known-location or on
      # PATH at all. Deliberately NOT pinning MLV_GIT_BASH here: round 10's PowerShell fallback
      # (Invoke-Lane.ps1, shell selection ~line 766) is what makes the Git-Bash-optional half of
      # this requirement host-independent -- a fixture pin would hide a regression in that
      # fallback instead of exercising it.
      "MLV_LANE_PYTHON_EXE":sys.executable})
    cmd=[PWSH,"-NoLogo","-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-File",str(script),"-Lane",lane,"-Prompt","fixture prompt","-WorkDir",str(root),"-RunDir",str(run),"-TimeoutSec","3" if mode=="timeout" else "30","-Card","FIXTURE","-ReasoningEffort","low"]
    if editing: cmd += ["-AllowEdits","-AllowedTools",allowed_tools]
    if allow_bulk_reads: cmd += ["-AllowBulkReads"]
    return cmd,env,run/(lane+"-001.receipt.json")


def settings_path_for(receipt):
    # Invoke-Lane.ps1 names every per-run artifact off the same reserved base
    # ("<lane>-NNN"); the settings file sits beside the receipt with the same base.
    assert receipt.name.endswith(".receipt.json")
    return receipt.parent / (receipt.name[: -len(".receipt.json")] + ".settings.json")


def test_read_only_argv_json_stdin_and_tool_denial(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8")); assert q["state"]=="complete" and q["complete"]
    assert q["containment"]["jobAssigned"] and q["containment"]["promptDelivered"]
    assert q["containment"]["childCreatedUtc"].endswith("Z") and "T" in q["containment"]["childCreatedUtc"]
    assert (fixture_tree["root"]/"prompt.txt").read_text(encoding="utf-8-sig").strip()=="fixture prompt"
    argv=json.loads((fixture_tree["root"]/"args.json").read_text(encoding="utf-8-sig"))
    i=argv.index("--disallowedTools"); assert argv[i+1]==DISALLOWED_TOOLS_TOKEN
    j=argv.index("--allowedTools"); assert argv[j+1]=="Read,Grep,Glob"
    assert "--append-system-prompt" in argv
    notice=argv[argv.index("--append-system-prompt")+1]
    assert argv.count("--append-system-prompt")==1
    assert "only Read, Grep, and Glob" in notice and "do not call or retry" in notice
    assert q["authority"]["capabilityNotice"]==notice
    assert q["outputBytes"]>0 and q["spend"]["costUsd"]==0
    assert q["effort"]=="low"
    assert (fixture_tree["root"]/"effort.txt").read_text(encoding="utf-8-sig").strip()=="low"
    # LANE-NO-BACKGROUND-END-TURN-1 round 2 (hub ruling): NA-3 prohibits assigning ANY
    # CLAUDE_CODE_* variable, so the round-1 CLAUDE_CODE_DISABLE_BACKGROUND_TASKS child-env
    # flag was removed rather than narrowed. Assert it never reaches the child and never
    # reappears in the receipt -- a regression here would silently reintroduce NA-3-prohibited
    # behavior.
    assert (fixture_tree["root"]/"bgtasks.txt").read_text(encoding="utf-8-sig").strip()==""
    assert "backgroundTasks" not in q["authority"]


# LANE-NO-BACKGROUND-END-TURN-1 round 6 (swarm ruling): --disallowedTools cannot reach
# `run_in_background` -- it is a parameter of a tool call, not a separate tool name -- so a
# per-lane Claude Code settings file now wires a per-run COPY of
# tools/coordination/lane-no-background.py as a PreToolUse hook for every Claude-engine lane,
# read-only and editing alike. No env-var mechanism, no NA-3 change: see
# docs/lane-containment.md.
# Round 7 (sol major 1 / fable major): the matcher now covers PowerShell too, which carries
# the same run_in_background parameter and is granted to every editing lane by
# docs/Start-EditingLane.ps1.
def test_read_only_settings_json_wires_lane_no_background_hook(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    argv=json.loads((fixture_tree["root"]/"args.json").read_text(encoding="utf-8-sig"))
    i=argv.index("--settings")
    settings=json.loads(settings_path_for(receipt).read_text(encoding="utf-8-sig"))
    assert argv[i+1]==str(settings_path_for(receipt))
    pre=settings["hooks"]["PreToolUse"]
    assert len(pre)==1
    assert pre[0]["matcher"]=="Bash|PowerShell"
    hook=pre[0]["hooks"]
    assert len(hook)==1 and hook[0]["type"]=="command"
    assert "lane-no-background.py" in hook[0]["command"]
    # The Read deny rules stay conditional on -AllowBulkReads exactly as before this round --
    # a read-only lane without -AllowBulkReads still gets them, alongside the new hook.
    assert settings["permissions"]["deny"]
    # Round 7 (sol major 3): the registered command must name a COPY reserved beside this run's
    # receipt, never the source script beside Invoke-Lane.ps1 itself (which sits inside a
    # writable worktree for every editing lane -- docs/Start-EditingLane.ps1 runs Invoke-
    # Lane.ps1 FROM $WorkDir). The copy must actually exist and byte-match the source.
    hook_copy_path = hook[0]["command"].split('"')[3]
    source_bytes = (fixture_tree["root"]/"lane-no-background.py").read_bytes()
    assert Path(hook_copy_path) != fixture_tree["root"]/"lane-no-background.py"
    assert Path(hook_copy_path).read_bytes() == source_bytes
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert re.fullmatch(r"[0-9a-f]{64}", q["authority"]["backgroundHookSha256"])
    assert q["authority"]["backgroundHookSha256"] == hashlib.sha256(source_bytes).hexdigest()


def test_bulk_reads_lane_still_gets_the_hook_without_the_deny_rules(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal",allow_bulk_reads=True)
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    settings=json.loads(settings_path_for(receipt).read_text(encoding="utf-8-sig"))
    assert "permissions" not in settings
    assert settings["hooks"]["PreToolUse"][0]["matcher"]=="Bash|PowerShell"
    assert "lane-no-background.py" in settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def test_editing_settings_json_also_wires_lane_no_background_hook(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    argv=json.loads((fixture_tree["root"]/"args.json").read_text(encoding="utf-8-sig"))
    assert "--settings" in argv
    settings=json.loads(settings_path_for(receipt).read_text(encoding="utf-8-sig"))
    assert settings["hooks"]["PreToolUse"][0]["matcher"]=="Bash|PowerShell"
    assert "lane-no-background.py" in settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["authority"]["backgroundGate"]=="denied-by-settings-hook"
    assert re.fullmatch(r"[0-9a-f]{64}", q["authority"]["backgroundHookSha256"])
    # Round 9: the receipt records the RESOLVED interpreter/shell paths and where each came from
    # (override/PATH/known-location) -- never a hardcoded pin -- so a receipt can be audited
    # against what actually ran the gate on the host that produced it. Round 10 adds
    # backgroundGateShellKind, naming which of the two ('bash' or 'powershell') actually won.
    assert q["authority"]["backgroundGateInterpreterPath"]
    assert q["authority"]["backgroundGateInterpreterSource"]
    assert q["authority"]["backgroundGateShellKind"] in ("bash", "powershell")
    assert q["authority"]["backgroundGateShellPath"]
    assert q["authority"]["backgroundGateShellSource"]
    assert settings["hooks"]["PreToolUse"][0]["hooks"][0]["shell"] == q["authority"]["backgroundGateShellKind"]


def test_codex_lane_gets_no_settings_file_or_flag(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal",lane="sol")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    argv=json.loads((fixture_tree["root"]/"args.json").read_text(encoding="utf-8-sig"))
    assert "--settings" not in argv
    assert not settings_path_for(receipt).exists()
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert "backgroundGate" not in q["authority"]
    assert "backgroundBash" not in q["authority"]


# LANE-NO-BACKGROUND-END-TURN-1 round 7 (sol major 3): a live end-to-end proof that an
# editing lane cannot defeat its OWN gate by rewriting the source Invoke-Lane.ps1 copies
# from -- the launcher must have already reserved and hashed its per-run copy before the
# child (which is what would edit the source) ever starts, and the copy must be the one
# actually registered.
def test_hook_copy_is_reserved_in_run_dir_not_resolved_from_worktree(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    run_dir=fixture_tree["root"]/"run"
    copies=list(run_dir.glob("*.lane-no-background.py"))
    assert len(copies)==1, f"expected exactly one reserved hook copy in {run_dir}, found {copies}"
    assert copies[0].parent==run_dir


LANE_NO_BACKGROUND_SCRIPT = ROOT / "tools" / "coordination" / "lane-no-background.py"


def run_lane_no_background(payload_text):
    return subprocess.run(
        [sys.executable, str(LANE_NO_BACKGROUND_SCRIPT)],
        input=payload_text, text=True, capture_output=True, timeout=10)


def run_lane_no_background_json(payload):
    return run_lane_no_background(json.dumps(payload))


# Round 7 (sol major 1 / fable major, and sol major 2): the deny protocol is now exit 2 with
# one stderr line -- the SAME fail-closed protocol tools/hooks/mlv-never-authorized.py uses --
# instead of round 6's exit-0-plus-stdout-JSON, so a launcher self-test can prove the gate
# with one exit-code check. The deny check itself is now tool-name-agnostic (any tool_name
# with a truthy run_in_background), not limited to Bash.
def test_lane_no_background_script_denies_backgrounded_bash():
    r=run_lane_no_background_json({"tool_name":"Bash","tool_input":{"command":"x","run_in_background":True}})
    assert r.returncode==2,(r.stdout,r.stderr)
    assert r.stdout==""
    assert "headless lane" in r.stderr
    assert "later turn" in r.stderr


def test_lane_no_background_script_denies_backgrounded_powershell():
    r=run_lane_no_background_json({"tool_name":"PowerShell","tool_input":{"command":"x","run_in_background":True}})
    assert r.returncode==2,(r.stdout,r.stderr)
    assert "headless lane" in r.stderr


def test_lane_no_background_script_denies_any_tool_with_the_flag():
    # Round 7 required case: an UNKNOWN tool name carrying the flag must still be denied --
    # the registration matcher narrows which calls reach the script at all, but the script's
    # own check must not assume the matcher is the only thing standing in the way.
    r=run_lane_no_background_json({"tool_name":"SomeFutureTool","tool_input":{"run_in_background":True}})
    assert r.returncode==2,(r.stdout,r.stderr)
    assert "headless lane" in r.stderr


def test_lane_no_background_script_allows_bash_without_the_flag():
    r=run_lane_no_background_json({"tool_name":"Bash","tool_input":{"command":"x"}})
    assert r.returncode==0,(r.stdout,r.stderr)
    assert r.stdout=="" and r.stderr==""


def test_lane_no_background_script_allows_false_flag():
    r=run_lane_no_background_json({"tool_name":"Bash","tool_input":{"command":"x","run_in_background":False}})
    assert r.returncode==0,(r.stdout,r.stderr)
    assert r.stdout=="" and r.stderr==""


def test_lane_no_background_script_allows_other_tools_without_the_flag():
    r=run_lane_no_background_json({"tool_name":"Write","tool_input":{"file_path":"x"}})
    assert r.returncode==0,(r.stdout,r.stderr)
    assert r.stdout=="" and r.stderr==""


# Round 7 (sol major 2): fail CLOSED on malformed/missing/non-JSON stdin, never allow.
def test_lane_no_background_script_denies_empty_stdin():
    r=run_lane_no_background("")
    assert r.returncode==2,(r.stdout,r.stderr)
    assert "hook-error" in r.stderr


def test_lane_no_background_script_denies_non_json_stdin():
    r=run_lane_no_background("not json{{{")
    assert r.returncode==2,(r.stdout,r.stderr)
    assert "hook-error" in r.stderr


def test_lane_no_background_script_denies_non_object_json_stdin():
    r=run_lane_no_background("[1, 2, 3]")
    assert r.returncode==2,(r.stdout,r.stderr)
    assert "hook-error" in r.stderr


def test_lane_no_background_script_denies_non_object_tool_input():
    r=run_lane_no_background_json({"tool_name":"Bash","tool_input":"x"})
    assert r.returncode==2,(r.stdout,r.stderr)
    assert "hook-error" in r.stderr


def _load_lane_no_background_module():
    spec = importlib.util.spec_from_file_location("lane_no_background", LANE_NO_BACKGROUND_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# LANE-NO-BACKGROUND-END-TURN-1 round 12 (fable minor): the deny-reason substring "headless
# lane" is load-bearing in THREE files that must be hand-kept in sync -- lane-no-background.py's
# DENY_REASON (the text ONLY the genuine deny branch ever prints, per its own docstring above),
# Invoke-Lane.ps1's $backgroundGateExpectedDenySubstring (what the launcher's self-test requires
# in the captured output before it will trust ANY shell candidate as a genuine deny), and this
# file's own _bash_candidate_runs_the_hook helper below (used to classify a real Git Bash for
# the fixture tests). A reword of DENY_REASON alone would refuse every Claude lane launch --
# fail-closed, never fail-open -- but silently, with nothing catching the drift before it reached
# a real launch. This reads all three sites directly (never a fourth hand-copied literal) and
# fails if any one disagrees with the others.
def test_deny_reason_substring_matches_launcher_and_test_helper():
    module = _load_lane_no_background_module()
    deny_reason = module.DENY_REASON
    ps1_text = CANDIDATE.read_text(encoding="utf-8")
    m = re.search(r"\$backgroundGateExpectedDenySubstring\s*=\s*'([^']*)'", ps1_text)
    assert m, "Invoke-Lane.ps1's $backgroundGateExpectedDenySubstring literal changed shape; update this test's regex to match"
    launcher_substring = m.group(1)
    assert launcher_substring, "launcher's expected deny substring must not be empty"
    assert launcher_substring in deny_reason, (
        f"Invoke-Lane.ps1 expects {launcher_substring!r} in the hook's deny output, but "
        f"lane-no-background.py's DENY_REASON is {deny_reason!r} -- these two sites must agree "
        f"or every Claude lane launch would refuse its self-test"
    )
    helper_source = Path(__file__).read_text(encoding="utf-8")
    helper_match = re.search(r'return r\.returncode == 2 and "([^"]+)" in', helper_source)
    assert helper_match, "_bash_candidate_runs_the_hook's substring check changed shape; update this test's regex to match"
    assert helper_match.group(1) in deny_reason, (
        f"_bash_candidate_runs_the_hook expects {helper_match.group(1)!r} in the hook's output, "
        f"but DENY_REASON is {deny_reason!r}"
    )
    assert helper_match.group(1) == launcher_substring, (
        "the test helper and the launcher must require the SAME substring, or a real Git Bash "
        "this test file classifies as 'runs the hook' could still fail the launcher's own "
        f"self-test: helper={helper_match.group(1)!r} launcher={launcher_substring!r}"
    )


# LANE-NO-BACKGROUND-END-TURN-1 round 7 (sol major 2): the launcher must PROVE the gate
# before ever starting the provider -- run the exact registered command against a synthetic
# background-Bash payload and require the fail-closed deny (exit 2). A self-test that does
# not pass refuses the launch and records why, rather than trusting an unproven hook.
def test_launch_refuses_when_background_gate_selftest_fails(fixture_tree):
    def install_broken_hook(text):
        return text  # Invoke-Lane.ps1 itself is untouched; the SOURCE hook script is broken below.
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit",
                             mutation=install_broken_hook)
    # Overwrite the SOURCE the launcher copies from (written by prepare() before the launcher
    # runs) with a script that always allows, simulating a gate that would fail open.
    (fixture_tree["root"]/"lane-no-background.py").write_text(
        "import sys\nsys.exit(0)\n", encoding="ascii")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode!=0,(r.stdout,r.stderr)
    assert not (fixture_tree["root"]/"child.json").exists()
    assert not (fixture_tree["root"]/"args.json").exists()
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="failed"
    assert not q["complete"]
    assert q["failure"].startswith("background-gate-selftest-failed")


# LANE-NO-BACKGROUND-END-TURN-1 round 8 (sol major 1): the self-test must run the EXACT
# command STRING that is written into the --settings hook entry, through the SAME shell Claude
# Code itself spawns for a shell-form `command` hook on Windows -- Git Bash (evidence: docs/en/
# hooks-guide.md, quoted in this round's summary.md; corroborated by tools/agent-bridge/core/
# win_process.py's own measured `...Git\bin\..\usr\bin\bash.exe` process path on this host). This
# breaks the registered command by baking a nonexistent path into ONLY the interpreter half of
# $hookCommand -- the SOURCE hook script and $PYTHON_EXE the launcher would use for a direct call
# are both untouched and correct. A self-test built as round 7's `& $PYTHON_EXE $hookCopyPath`
# (two decomposed argv tokens, bypassing the registered string and its shell entirely) cannot see
# this: it would still invoke the real, correct $PYTHON_EXE and pass. Only running the actual
# registered STRING through Git Bash surfaces the break (bash: ...-does-not-exist: No such file or
# directory, exit 127 -- never the hook script's own exit-2 deny protocol).
def test_launch_refuses_when_registered_command_has_a_bad_interpreter_path_even_though_a_direct_interpreter_call_would_succeed(fixture_tree):
    def break_registered_command_only(text):
        needle = "$hookCommand = ('\"{0}\" \"{1}\"' -f $PYTHON_EXE, $hookCopyPath)"
        assert needle in text, "Invoke-Lane.ps1's $hookCommand builder line changed shape; update this fixture's mutation to match"
        broken = "$hookCommand = ('\"{0}-does-not-exist\" \"{1}\"' -f $PYTHON_EXE, $hookCopyPath)"
        return text.replace(needle, broken)
    cmd,env,receipt=prepare(fixture_tree,"normal",mutation=break_registered_command_only)
    # Round 11: shell selection now falls back to PowerShell on a bash self-test failure (the
    # WSL-stub fix), so a broken BASH command string alone would no longer refuse the launch --
    # it would just fall through to a working PowerShell candidate and succeed. Force PowerShell
    # unresolvable too so the bash break is the thing that actually decides the outcome, and the
    # attempts list still names the underlying exit-127 interpreter failure.
    env["MLV_LANE_POWERSHELL_EXE"]=str(fixture_tree["root"]/"nonexistent-powershell.exe")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode!=0,(r.stdout,r.stderr)
    assert not (fixture_tree["root"]/"child.json").exists()
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="failed"
    assert q["failure"].startswith("background-gate-selftest-failed")
    assert "127" in q["failure"]
    # Sanity/documentation half of the requirement -- fable round 8 nit (round 9): this does NOT
    # call the reserved per-run copy the launcher actually registered; run_lane_no_background_json
    # runs the repo SOURCE script (LANE_NO_BACKGROUND_SCRIPT) under `sys.executable` (this test
    # process's own interpreter), not any copy the launcher reserved and not the launcher's
    # resolved $PYTHON_EXE. That is deliberate, not a stand-in for the reserved copy: the two are
    # byte-identical (test_hook_copy_is_reserved_in_run_dir_not_resolved_from_worktree and
    # test_read_only_settings_json_wires_lane_no_background_hook already prove the copy matches
    # the source), so this line is answering a narrower question than "does the registered command
    # work" -- only "does the interpreter-and-script PAIR still deny correctly" -- which is the
    # thing round 7's self-test tested and this mutation left untouched. Only the registered
    # STRING was broken (a bad path baked into $hookCommand); this asserts the underlying pair was
    # never the thing that broke.
    hook_copies=list((fixture_tree["root"]/"run").glob("*.lane-no-background.py"))
    assert len(hook_copies)==1
    direct=run_lane_no_background_json({"tool_name":"Bash","tool_input":{"command":"echo x","run_in_background":True}})
    assert direct.returncode==2,(direct.stdout,direct.stderr)


# LANE-NO-BACKGROUND-END-TURN-1 round 9: root cause of the hosted-CI break that round fixed was
# NOT the pinned Git Bash path (it resolved and ran fine on the runner -- verified against the
# real job log, run 35758428267 job 106850299120) but the pinned $PYTHON_EXE, a per-user absolute
# path that exists only on the dev machine. Both are resolved at runtime, in order: an explicit
# per-variable override env var (authoritative -- never falls through, even to a real hit), then
# known, curated install locations, then PATH as a last resort -- known-locations-before-PATH is
# deliberate (Invoke-Lane.ps1 ~line 154 documents the measured WSL-stub-on-PATH hazard it avoids).
#
# Round 10 (sol major 1a/1b): Git Bash absence is no longer, by itself, a launch refusal --
# PowerShell is tried as a second, registered, self-tested shell (Invoke-Lane.ps1's shell
# selection, ~line 766), matching what Claude Code itself would do rather than refusing more
# strictly than the product. `prepare()` above now also pins MLV_LANE_PYTHON_EXE to this test
# process's own interpreter for EVERY fixture test, not only the ones below that touch it
# explicitly. Scope of what this buys, stated precisely (fable round 9 minor 2): combined, these
# two changes make the FULL fixture suite -- not merely the tests in this section -- deterministic
# regardless of whether the host running it has Git Bash, or a "board Python" at any particular
# known-location or PATH entry. An ordinary Claude-lane launch test now resolves Python from
# prepare()'s pin (never PATH- or install-location-dependent) and resolves a shell from either a
# real Git Bash (if the host has one) or the System32 PowerShell every Windows host ships (if it
# does not) -- both outcomes launch successfully. The one residual: a host missing PowerShell
# itself would still fail closed on the shell axis; no supported Windows configuration lacks it,
# so this is not exercised by a skip-free test the way the four tests below exercise their own
# specific override tiers.
# Round 11 (sol major / fable minor 1): file existence alone is the SAME misclassification
# Resolve-LaneExecutable used to make -- the Windows-shipped WSL bash.exe launcher stub is a
# real file that `shutil.which("bash")` and `Path(...).is_file()` both happily report, but with
# no WSL distribution installed it exits 1 without ever running anything, and even with one
# installed it is not the POSIX shell this hook's command form expects. Invoke-Lane.ps1's own
# shell selection no longer trusts existence either (round 11) -- it classifies a candidate by
# actually self-testing it. This helper does the same: a candidate is "real Git Bash" only if it
# runs lane-no-background.py's own deny protocol correctly, never merely because a file sits at
# that path.
def _bash_candidate_runs_the_hook(bash_exe):
    command = '"{0}" "{1}"'.format(sys.executable, LANE_NO_BACKGROUND_SCRIPT)
    try:
        r = subprocess.run(
            [bash_exe, "-c", command],
            input='{"tool_name":"Bash","tool_input":{"command":"echo x","run_in_background":true}}',
            text=True, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 2 and "headless lane" in (r.stdout + r.stderr)


def _find_real_git_bash():
    candidates = []
    found = shutil.which("bash")
    if found and Path(found).is_file():
        candidates.append(found)
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ):
        if Path(candidate).is_file() and candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        if _bash_candidate_runs_the_hook(candidate):
            return candidate
    return None


def test_launch_proceeds_when_git_bash_and_python_overrides_resolve(fixture_tree):
    real_bash = _find_real_git_bash()
    if not real_bash:
        pytest.skip("no Git Bash found on this test host -- cannot exercise the resolvable-shell path")
    cmd,env,receipt=prepare(fixture_tree,"normal")
    env["MLV_GIT_BASH"]=real_bash
    env["MLV_LANE_PYTHON_EXE"]=sys.executable
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="complete" and q["complete"]
    assert q["authority"]["backgroundGateShellKind"]=="bash"
    assert q["authority"]["backgroundGateShellPath"]==real_bash
    assert q["authority"]["backgroundGateShellSource"]=="override:MLV_GIT_BASH"
    assert q["authority"]["backgroundGateInterpreterPath"]==sys.executable
    assert q["authority"]["backgroundGateInterpreterSource"]=="override:MLV_LANE_PYTHON_EXE"


# Round 10 (sol major 1a): forcing Git Bash unresolvable no longer refuses the launch -- it falls
# back to the registered, self-tested PowerShell path instead. This test replaces round 9's
# test_launch_refuses_when_git_bash_override_is_unresolvable, whose refusal assertion this round's
# behavior change makes false; test_launch_refuses_when_neither_git_bash_nor_powershell_resolve
# below covers the still-fail-closed case where NEITHER shell resolves.
def test_launch_falls_back_to_powershell_when_git_bash_override_is_unresolvable(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal")
    env["MLV_GIT_BASH"]=str(fixture_tree["root"]/"nonexistent-bash.exe")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="complete" and q["complete"]
    assert q["authority"]["backgroundGateShellKind"]=="powershell"
    assert q["authority"]["backgroundGateShellPath"]
    assert q["authority"]["backgroundGateShellSource"]!="override:MLV_GIT_BASH"
    settings=json.loads(settings_path_for(receipt).read_text(encoding="utf-8-sig"))
    hook_entry=settings["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hook_entry["shell"]=="powershell"
    # Round 10: the PowerShell command form needs the leading call operator AND the trailing
    # exit-code forward -- both measured load-bearing (see Invoke-Lane.ps1 ~line 800 and this
    # round's summary.md).
    assert hook_entry["command"].startswith('& "')
    assert hook_entry["command"].endswith('; exit $LASTEXITCODE')


# LANE-NO-BACKGROUND-END-TURN-1 round 11 (sol major / fable minor 1): round 10's fallback only
# fired when Git Bash did not RESOLVE at all -- it never fired when Git Bash resolved to
# something that then FAILED its self-test, which is exactly the WSL-stub host shape (the stub
# exists on PATH, so it resolves; it just cannot run this hook's command form). Simulate that
# shape deterministically, without depending on WSL actually being installed on whatever host
# runs this suite: point MLV_GIT_BASH at a real, launchable binary that is not a POSIX shell (a
# cmd.exe copy -- same decoy binary test_resolve_prefers_known_location_over_path_when_both_
# resolve uses elsewhere in this file). Measured directly: `cmd.exe -c "<hookCommand>"` with the
# self-test payload piped to stdin treats stdin as an interactive command stream and exits 0, never
# 2 -- a real, distinct self-test failure, not a launch exception. Shell selection must fall
# through to PowerShell rather than refusing the launch outright.
def test_launch_falls_back_to_powershell_when_resolved_bash_candidate_fails_its_selftest(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal")
    fake_bash=fixture_tree["root"]/"fake-stub-bash.exe"
    fake_bash.write_bytes(Path(os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")).read_bytes())
    env["MLV_GIT_BASH"]=str(fake_bash)
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="complete" and q["complete"]
    assert q["authority"]["backgroundGateShellKind"]=="powershell"
    assert q["authority"]["backgroundGateShellSource"]!="override:MLV_GIT_BASH"
    settings=json.loads(settings_path_for(receipt).read_text(encoding="utf-8-sig"))
    hook_entry=settings["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hook_entry["shell"]=="powershell"


# LANE-NO-BACKGROUND-END-TURN-1 round 11 (sol minor / fable minor 2): round 10's self-test
# treated any exit 2 as proof of a genuine deny, which a misregistered (missing/misquoted) hook
# copy path can also produce -- Python itself exits 2 for "can't open file". A registration-only
# regression of this shape must now FAIL the self-test rather than pass it. Break the hook COPY
# path baked into $hookCommand (not $PYTHON_EXE, which round 8's sibling test already covers) so
# the interpreter is real and correct but the script argument is not.
def test_launch_refuses_when_registered_hook_copy_path_does_not_exist(fixture_tree):
    def break_hook_copy_path(text):
        needle = "$hookCommand = ('\"{0}\" \"{1}\"' -f $PYTHON_EXE, $hookCopyPath)"
        assert needle in text, "Invoke-Lane.ps1's $hookCommand builder line changed shape; update this fixture's mutation to match"
        broken = "$hookCommand = ('\"{0}\" \"{1}-does-not-exist\"' -f $PYTHON_EXE, $hookCopyPath)"
        return text.replace(needle, broken)
    cmd,env,receipt=prepare(fixture_tree,"normal",mutation=break_hook_copy_path)
    # Same reasoning as the sibling bad-interpreter-path test: force PowerShell unresolvable so
    # the broken bash command string is what decides the outcome, not an available fallback.
    env["MLV_LANE_POWERSHELL_EXE"]=str(fixture_tree["root"]/"nonexistent-powershell.exe")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode!=0,(r.stdout,r.stderr)
    assert not (fixture_tree["root"]/"child.json").exists()
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="failed"
    assert not q["complete"]
    assert q["failure"].startswith("background-gate-selftest-failed")
    # Python's own "can't open file" exit is 2 -- the SAME exit code the hook's own fail-closed
    # empty-stdin path uses. A round-10-shaped self-test (exit-2-only) would have PASSED this
    # mutation despite the hook never running. Proof it never ran: Python's own missing-file
    # message is what's captured, not the hook's DENY_REASON text.
    assert "No such file or directory" in q["failure"]
    assert "a headless lane has no later turn" not in q["failure"]


def test_launch_refuses_when_neither_git_bash_nor_powershell_resolve(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit")
    env["MLV_GIT_BASH"]=str(fixture_tree["root"]/"nonexistent-bash.exe")
    env["MLV_LANE_POWERSHELL_EXE"]=str(fixture_tree["root"]/"nonexistent-powershell.exe")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode!=0,(r.stdout,r.stderr)
    assert not (fixture_tree["root"]/"child.json").exists()
    assert not (fixture_tree["root"]/"args.json").exists()
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="failed"
    assert not q["complete"]
    assert q["failure"].startswith("background-gate-shell-not-found")
    assert "override:MLV_GIT_BASH" in q["failure"]
    assert "override:MLV_LANE_POWERSHELL_EXE" in q["failure"]


# LANE-NO-BACKGROUND-END-TURN-1 round 10 (sol minor / fable minor 1): the implemented, documented
# order is override -> known locations -> PATH -- deliberately, to keep a WSL bash.exe launcher
# stub on PATH from ever hijacking a default launch ahead of a curated, verified Git Bash location
# (Invoke-Lane.ps1 ~line 154). Earlier round-9 test-file prose stated the reverse
# ("override, then PATH, then known install locations"); this test asserts the actual winner
# directly instead of leaving the order to comment-only documentation, which a future reader could
# "fix" by inverting the real precedence and silently reintroducing the stub hazard.
#
# Round 12 (sol major, restated): the launcher no longer stops at the first candidate that
# resolves -- it now self-tests EVERY discoverable candidate for the kind and requires all of
# them to pass (see Resolve-LaneExecutableAllCandidates). A decoy that fails its self-test would
# therefore disqualify 'bash' entirely rather than merely prove it was "never invoked" -- that
# shape is now its own test, test_launch_falls_back_to_powershell_when_a_path_candidate_fails_
# selftest_even_though_known_location_passes below. This test's decoy must therefore be a
# WORKING second bash, not a non-POSIX stand-in (round 12: every candidate must pass, so a
# non-working decoy here would prove the wrong thing). A byte-for-byte COPY of bash.exe alone
# does not work: measured directly, Git's bash.exe is a launcher stub that locates its sibling
# runtime via a path RELATIVE TO ITS OWN DIRECTORY (`..\usr\bin\bash.exe`), so a copy dropped
# into an otherwise-empty directory fails immediately with "'...\usr\bin\bash.exe' not found" --
# a real regression this test would silently paper over by reverting to the OLD never-invoked
# decoy shape instead. A directory JUNCTION to bash's own directory (`_winapi.CreateJunction`,
# no admin privilege required, unlike a symlink) gives a second, PATH-distinct, working
# candidate that resolves its sibling files correctly through the junction -- measured directly
# against the real hook.
def test_resolve_prefers_known_location_over_path_when_both_resolve(fixture_tree):
    real_bash = _find_real_git_bash()
    if not real_bash:
        pytest.skip("no Git Bash found on this test host -- cannot exercise the known-location-vs-PATH precedence path")
    def pin_known_location_to_real_bash(text):
        needle = "-KnownLocations @('C:\\Program Files\\Git\\bin\\bash.exe', 'C:\\Program Files\\Git\\usr\\bin\\bash.exe', 'C:\\Program Files (x86)\\Git\\bin\\bash.exe')"
        assert needle in text, "Invoke-Lane.ps1's Git Bash KnownLocations literal changed shape; update this fixture's mutation to match"
        escaped = real_bash.replace("'", "''")
        return text.replace(needle, "-KnownLocations @('" + escaped + "')")
    cmd,env,receipt=prepare(fixture_tree,"normal",mutation=pin_known_location_to_real_bash)
    # Selection must still prefer the known-location entry over this PATH entry even though
    # both validate (both are the same real Git Bash install, reached two different ways).
    path_dir = fixture_tree["root"]/"path-shadow"
    import _winapi
    _winapi.CreateJunction(str(Path(real_bash).parent), str(path_dir))
    try:
        env["PATH"] = str(path_dir) + os.pathsep + env["PATH"]
        r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
        assert r.returncode==0,(r.stdout,r.stderr)
        q=json.loads(receipt.read_text(encoding="utf-8"))
        assert q["state"]=="complete" and q["complete"]
        assert q["authority"]["backgroundGateShellKind"]=="bash"
        assert q["authority"]["backgroundGateShellPath"]==real_bash
        assert q["authority"]["backgroundGateShellSource"]==f"known-location:{real_bash}"
        # Round 12: both the known-location entry AND the PATH entry were validated (proof the
        # PATH candidate really was tested, not skipped because known-location already won) --
        # the receipt must name both, not just the selected one.
        validated = " ".join(q["authority"]["backgroundGateShellValidatedCandidates"])
        assert f"known-location:{real_bash}" in validated
        assert "PATH:bash.exe" in validated
    finally:
        # A junction, not a copy -- os.rmdir removes the reparse point only, never the target
        # directory's own contents (measured; see the round-12 comment above).
        if path_dir.exists():
            os.rmdir(path_dir)


# LANE-NO-BACKGROUND-END-TURN-1 round 12 (sol major, restated): the mirror-image case of the
# precedence test above -- a known-location candidate that WOULD pass self-test sits alongside a
# PATH candidate that FAILS it (the WSL-stub shape, but reached via PATH rather than via
# MLV_GIT_BASH). Round 11 only ever self-tested the precedence winner, so this host shape would
# have selected 'bash' via the known-location entry without ever finding out the PATH sibling
# denies nothing -- exactly sol's round-11-restated repro ("a different Claude-selected Bash
# executable... can let the self-test pass... while the real hook command fails
# non-blockingly"). The launcher must now refuse to trust 'bash' at all here and fall back to
# PowerShell, because it cannot prove Claude Code would pick the known-location winner over the
# broken PATH sibling.
def test_launch_falls_back_to_powershell_when_a_path_candidate_fails_selftest_even_though_known_location_passes(fixture_tree):
    real_bash = _find_real_git_bash()
    if not real_bash:
        pytest.skip("no Git Bash found on this test host -- cannot exercise the known-location-passes-but-PATH-fails shape")
    def pin_known_location_to_real_bash(text):
        needle = "-KnownLocations @('C:\\Program Files\\Git\\bin\\bash.exe', 'C:\\Program Files\\Git\\usr\\bin\\bash.exe', 'C:\\Program Files (x86)\\Git\\bin\\bash.exe')"
        assert needle in text, "Invoke-Lane.ps1's Git Bash KnownLocations literal changed shape; update this fixture's mutation to match"
        escaped = real_bash.replace("'", "''")
        return text.replace(needle, "-KnownLocations @('" + escaped + "')")
    cmd,env,receipt=prepare(fixture_tree,"normal",mutation=pin_known_location_to_real_bash)
    # A non-POSIX decoy on PATH -- same cmd.exe-copy shape test 621 uses for the override case,
    # here reached through PATH instead, alongside a known-location candidate that DOES pass.
    path_dir = fixture_tree["root"]/"path-shadow"
    path_dir.mkdir()
    decoy = path_dir/"bash.exe"
    decoy.write_bytes((Path(os.environ.get("WINDIR", r"C:\Windows"))/"System32"/"cmd.exe").read_bytes())
    env["PATH"] = str(path_dir) + os.pathsep + env["PATH"]
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="complete" and q["complete"]
    assert q["authority"]["backgroundGateShellKind"]=="powershell"
    assert q["authority"]["backgroundGateShellSource"]!=f"known-location:{real_bash}"
    settings=json.loads(settings_path_for(receipt).read_text(encoding="utf-8-sig"))
    hook_entry=settings["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hook_entry["shell"]=="powershell"


# LANE-NO-BACKGROUND-END-TURN-1 round 12: the producer brief's required verification, made
# permanent -- the SAME two-bash-candidates-only-one-denies shape as the sibling test above,
# but with PowerShell also forced unresolvable so there is no fallback kind left to rescue the
# launch. "If any candidate the product might pick would not deny, that is a refusal to launch,
# not a warning" (producer brief) -- this is that sentence taken to its floor: when bash is the
# ONLY candidate kind and even one of its two discoverable candidates cannot prove the deny, the
# launch must refuse outright, and the failure text must name the specific candidate that could
# not prove it (never just "bash failed").
def test_launch_refuses_when_one_of_two_bash_candidates_cannot_prove_the_deny_and_no_powershell_fallback_exists(fixture_tree):
    real_bash = _find_real_git_bash()
    if not real_bash:
        pytest.skip("no Git Bash found on this test host -- cannot exercise the two-bash-candidates shape")
    def pin_known_location_to_real_bash(text):
        needle = "-KnownLocations @('C:\\Program Files\\Git\\bin\\bash.exe', 'C:\\Program Files\\Git\\usr\\bin\\bash.exe', 'C:\\Program Files (x86)\\Git\\bin\\bash.exe')"
        assert needle in text, "Invoke-Lane.ps1's Git Bash KnownLocations literal changed shape; update this fixture's mutation to match"
        escaped = real_bash.replace("'", "''")
        return text.replace(needle, "-KnownLocations @('" + escaped + "')")
    cmd,env,receipt=prepare(fixture_tree,"normal",mutation=pin_known_location_to_real_bash)
    path_dir = fixture_tree["root"]/"path-shadow"
    path_dir.mkdir()
    decoy = path_dir/"bash.exe"
    decoy.write_bytes((Path(os.environ.get("WINDIR", r"C:\Windows"))/"System32"/"cmd.exe").read_bytes())
    env["PATH"] = str(path_dir) + os.pathsep + env["PATH"]
    env["MLV_LANE_POWERSHELL_EXE"]=str(fixture_tree["root"]/"nonexistent-powershell.exe")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode!=0,(r.stdout,r.stderr)
    assert not (fixture_tree["root"]/"child.json").exists()
    assert not (fixture_tree["root"]/"args.json").exists()
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="failed"
    assert not q["complete"]
    assert q["failure"].startswith("background-gate-selftest-failed")
    # Names the specific failing candidate (the PATH decoy) by its source label, not merely "bash".
    assert "PATH:bash.exe" in q["failure"]
    # And does NOT claim the known-location candidate (which genuinely passed) was the problem.
    assert f"known-location:{real_bash}" not in q["failure"].split("attempts:")[1]


def test_launch_refuses_when_python_override_is_unresolvable(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal")
    env["MLV_LANE_PYTHON_EXE"]=str(fixture_tree["root"]/"nonexistent-python.exe")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode!=0,(r.stdout,r.stderr)
    assert not (fixture_tree["root"]/"child.json").exists()
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="failed"
    assert not q["complete"]
    assert q["failure"].startswith("background-gate-interpreter-not-found")
    assert "override:MLV_LANE_PYTHON_EXE" in q["failure"]


# LANE-NO-BACKGROUND-END-TURN-1 round 8 (sol minor): regression coverage for the post-run
# tamper check (Invoke-Lane.ps1, ~line 1299) that round 7 shipped but never had a dedicated
# test for. A fake provider (the fixture's child.ps1) stands in for a lane that reaches out via
# its own Bash/Write grant mid-run and rewrites, or deletes, the per-run hook copy after the
# launch self-test already proved it denies.
def test_post_run_tamper_check_catches_hook_copy_rewritten_during_the_run(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal")
    env["MLV_FIXTURE_TAMPER_HOOK_COPY"]="modify"
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["authority"]["backgroundGate"]=="background-gate-tampered"
    assert q["authority"]["backgroundGate"]!="denied-by-settings-hook"
    assert q["authority"]["backgroundGateTamperDetail"].startswith("background-gate-tampered: sha256 was")


def test_post_run_tamper_check_catches_hook_copy_deleted_during_the_run(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal")
    env["MLV_FIXTURE_TAMPER_HOOK_COPY"]="delete"
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["authority"]["backgroundGate"]=="background-gate-tampered"
    assert q["authority"]["backgroundGate"]!="denied-by-settings-hook"
    assert q["authority"]["backgroundGateTamperDetail"]=="background-gate-tampered: hook copy missing after run"


def test_timeout_kills_owned_child_and_grandchild(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"timeout")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==124,(r.stdout,r.stderr)
    # A timed-out run ENDED but did not complete its work (2026-09-14: complete means work evidence).
    q=json.loads(receipt.read_text(encoding="utf-8")); assert q["state"]=="ended-incomplete" and q["timedOut"]
    assert q["processEnded"] is True and q["complete"] is False
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
    assert argv[argv.index("--disallowedTools")+1]==DISALLOWED_TOOLS_TOKEN
    assert "--append-system-prompt" not in argv
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["authority"]["disallowedTools"]==DISALLOWED_TOOLS_TOKEN.split(",")
    assert "backgroundTasks" not in q["authority"]
    assert (fixture_tree["root"]/"bgtasks.txt").read_text(encoding="utf-8-sig").strip()==""


# LANE-NO-BACKGROUND-END-TURN-1 round 2 (sol minor 4 / fable minor 1): the pre-reservation
# rejection must cover every entry in DISALLOWED_TOOLS_TOKEN, not only Agent/Task -- round 1
# checked the deny-list argv/receipt but never exercised the guard against the five newer
# tools, so a drift between the guard and the deny list (exactly what sol found) went untested.
@pytest.mark.parametrize("bad",["Agent"," task ","Read, AGENT ,Write","Read,Task",
    "Monitor","Read,ScheduleWakeup","CronCreate,Write","Read,CronDelete,Write"," remotetrigger ",
    "Workflow","Read, WORKFLOW ,Write","TaskCreate","Read,taskcreate,Write"])
def test_editing_explicit_nested_tool_is_rejected_before_reservation(fixture_tree,bad):
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools=bad)
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=10)
    assert r.returncode!=0 and "nested-agent-tool-forbidden" in r.stderr
    assert not receipt.exists() and not (fixture_tree["root"]/"child.json").exists()


@pytest.mark.parametrize("tool",DISALLOWED_TOOLS_TOKEN.split(","))
def test_every_disallowed_tool_is_individually_rejected_from_an_editing_allowlist(fixture_tree,tool):
    # Same guard, exercised one denied tool at a time (as opposed to the mixed-case/whitespace
    # variants above) so a future partial fix -- e.g. one that catches Agent/Task/Monitor but
    # misses a later addition to the deny list -- fails exactly the case it broke.
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools=f"Read,{tool}")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=10)
    assert r.returncode!=0 and "nested-agent-tool-forbidden" in r.stderr
    assert not receipt.exists() and not (fixture_tree["root"]/"child.json").exists()


def _read_denied_tools_display_from_source():
    text = CANDIDATE.read_text(encoding="utf-8")
    m = re.search(r"\$DENIED_TOOLS_DISPLAY\s*=\s*@\(([^)]*)\)", text)
    assert m, "could not find $DENIED_TOOLS_DISPLAY in Invoke-Lane.ps1"
    return [tok.strip().strip("'") for tok in m.group(1).split(",")]


def test_doc_disallowed_tools_token_and_count_word_match_the_constant():
    # Producer brief round 4 (sol + fable minor): docs/lane-containment.md quotes the
    # --disallowedTools token verbatim and names its length in English prose ("the N denied
    # tools"). Round 3 grew the constant from seven entries to nine (Workflow, TaskCreate) and
    # the doc was never updated to match -- it still said seven. Derive BOTH the token and the
    # count word from Invoke-Lane.ps1's own $DENIED_TOOLS_DISPLAY constant directly (not from
    # this file's own DISALLOWED_TOOLS_TOKEN hand-copy, which is itself just as capable of
    # drifting), so a future addition/removal to the deny list fails this test loudly instead of
    # leaving stale doc prose behind.
    tools = _read_denied_tools_display_from_source()
    token = ",".join(tools)
    assert token == DISALLOWED_TOOLS_TOKEN, (
        "this test file's own DISALLOWED_TOOLS_TOKEN hand-copy has drifted from "
        f"Invoke-Lane.ps1's $DENIED_TOOLS_DISPLAY: source={token!r} test-copy={DISALLOWED_TOOLS_TOKEN!r}"
    )
    doc_text = DOC.read_text(encoding="utf-8")
    assert f"`--disallowedTools {token}`" in doc_text, (
        "docs/lane-containment.md's quoted --disallowedTools token does not match "
        f"the Invoke-Lane.ps1 constant {token!r}"
    )
    count_word = _NUMBER_WORDS[len(tools)]
    assert f"the {count_word} denied tools" in doc_text, (
        f"docs/lane-containment.md does not say 'the {count_word} denied tools' "
        f"(the constant currently has {len(tools)} entries)"
    )


def test_codex_editing_lane_with_denied_tool_in_allowlist_is_not_rejected_by_the_claude_only_preflight(fixture_tree):
    # Producer brief round 4, required case (sol major 1): the denied-tool preflight at
    # Invoke-Lane.ps1 (~line 303-309) is now explicitly gated on `$LANES[$Lane].engine -eq
    # 'claude'`, not merely on -AllowEdits. In production a codex+-AllowEdits combination is
    # already refused earlier by the codex-lane-never-edits check (~line 290-292) before this
    # preflight is ever reached, so without neutralising that EARLIER, UNRELATED throw there is
    # no way to exercise this specific gate for a codex invocation at all -- and the whole point
    # of this test is to prove the gate is an explicit engine check, not an accident of check
    # ORDER that a future refactor could silently undo. Neutralise ONLY the codex-never-edits
    # throw (every other check, including the preflight under test, is untouched) and prove a
    # codex allowlist naming a denied tool is NOT rejected here.
    def bypass_codex_never_edits(text):
        old = ("if ($AllowEdits -and $LANES[$Lane].engine -eq 'codex') {\n"
               "    throw \"codex-lane-never-edits: -Lane $Lane with -AllowEdits "
               "(no Claude hook is visible to codex exec)\"\n"
               "}")
        assert text.count(old) == 1
        return text.replace(old, "# fixture: codex-never-edits neutralised for this test only")
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Agent,Write",
                             lane="sol",mutation=bypass_codex_never_edits)
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert "nested-agent-tool-forbidden" not in r.stderr, (r.stdout, r.stderr)
    assert r.returncode==0,(r.stdout,r.stderr)


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
    # The background-tasks deny is a claude-only concept (Monitor/ScheduleWakeup/Cron*/
    # RemoteTrigger are claude CLI tools) -- codex must not receive the env var.
    assert (fixture_tree["root"]/"bgtasks.txt").read_text(encoding="utf-8-sig").strip()==""


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
    # PR #105 round 2 (sol blocker): Invoke-Lane.ps1 now records containedHost.pid
    # the instant Process::Start returns, before any call that can throw -- so a
    # null ownerPid means no host exists (either the launch budget was already
    # gone, or Start itself threw). This fixture's delayed stdin read happens
    # AFTER a successful Start, so it must land in the "owner present" branch;
    # ownerPid==None here would itself be the round-2 regression.
    #
    # PR #105 round 3: a null ownerPid alone still can't tell "budget exhausted
    # before Start was ever called" (Invoke-Lane.ps1:470-472, legitimate) apart
    # from an ambiguous, unexplained absence. containment.ownerAbsentReason
    # (Invoke-Lane.ps1's catch block, ~661-676) now names WHY, so classify on
    # that instead of guessing from ownerPid alone.
    containment=q.get("containment")
    owner_pid=containment.get("ownerPid") if containment else None
    if containment is not None and owner_pid is not None:
        # Unchanged from round 2: a pid was recorded, so a host definitely exists
        # (or existed) and must be reaped.
        if containment["ownerCreatedUtc"] is None:
            # A pid with no createdUtc means Start succeeded but StartTime read threw;
            # there is no createdUtc to compare against, so the only provable check is
            # that the pid is not (or no longer) an alive process.
            assert identity(owner_pid) is None
        else:
            wait_absent({"pid":q["containment"]["ownerPid"],"createdUtc":q["containment"]["ownerCreatedUtc"]})
    else:
        # PR #105 round 4: ownerAbsentReason must be one of the closed-set no-host
        # tokens, never an arbitrary truthy string (round 3's "if reason: pass" let
        # a POST_START_UNRECORDED-shaped failure pose as a legitimate absence).
        assert_owner_absence_is_legitimate(containment, q)


def test_zero_timeout_exhausts_budget_before_spawn_and_names_the_reason(fixture_tree):
    # Reachability proof for the legitimate null-owner branch (PR #105 round 3,
    # task item 4): -TimeoutSec 0 means the budget is already spent by the time
    # execution reaches Invoke-Lane.ps1:470, so that line's throw fires BEFORE
    # Process::Start is ever called -- no mutation/mock needed, this is the real
    # code path. No host exists, so ownerPid must be null with ownerAbsentReason
    # naming why.
    cmd,env,receipt=prepare(fixture_tree,"normal")
    cmd[cmd.index("-TimeoutSec")+1]="0"
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=15)
    assert r.returncode==124,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["timedOut"]
    assert not (fixture_tree["root"]/"child.json").exists()
    containment=q["containment"]
    assert containment["ownerPid"] is None
    assert containment["ownerAbsentReason"]=="launch-budget-exhausted"


def test_start_threw_is_classified_as_no_host(fixture_tree):
    # Reachability proof for the other no-host token (PR #105 round 4): make
    # Process::Start itself throw for the claude engine. $hostStarted is never set
    # (it is assigned on the line immediately AFTER Start returns), so the catch
    # block must land in the "Start never returned" branch and pick the generic
    # start-threw token, not the budget token (this is not a TimeoutException) and
    # not POST_START_UNRECORDED (no host ever existed).
    def break_start(text):
        old = "$proc = [Diagnostics.Process]::Start($psi)"
        assert text.count(old) == 2
        # Replace ONLY the first occurrence -- the claude-engine branch (~line 517),
        # which runs before $hostStarted is set. The second occurrence is the
        # non-claude branch and must stay untouched.
        return text.replace(old, "throw 'fixture-start-threw'", 1)
    cmd,env,receipt=prepare(fixture_tree,"normal",mutation=break_start)
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=15)
    assert r.returncode==127,(r.stdout,r.stderr)
    assert not (fixture_tree["root"]/"child.json").exists()
    q=json.loads(receipt.read_text(encoding="utf-8"))
    containment=q["containment"]
    assert containment["ownerPid"] is None
    assert containment["ownerAbsentReason"]=="start-threw"
    assert containment["ownerAbsentDetail"]=="fixture-start-threw"
    assert_owner_absence_is_legitimate(containment, q)


def test_post_start_unrecorded_is_named_not_hidden(fixture_tree):
    # Reachability proof for the genuinely-ambiguous branch (PR #105 round 4, task
    # item 4): inject a throw between Process::Start returning (a host now EXISTS)
    # and $containedHost being built, using the same fixture-mutation mechanism
    # test_setup_origin_and_expired_budget_are_deterministic already uses to inject
    # text at a specific line. This is exactly the failure the cross-family review
    # found: without $hostStarted, this exception's message would pose as a
    # legitimate no-host reason. With it, the catch block must name it
    # POST_START_UNRECORDED instead -- and the test below must FAIL LOUD on that
    # receipt if a caller naively treated it as legitimate (proven by calling the
    # shared assertion helper and expecting it to raise).
    #
    # PR #105 round 5 (this packet): $hostPid is now captured on its own
    # non-throwing line BEFORE the $containedHost build that this fixture breaks,
    # so a post-start-unrecorded receipt must carry the REAL host pid, not null --
    # a null ownerPid here would itself be the round-5 regression, since the pid
    # is the only channel by which anyone later learns the orphan existed. The
    # mutation also drops a marker file with $hostPid's value (via the same
    # Write-Utf8NoBom helper the production code already uses) so the test can
    # assert the receipt's ownerPid equals the REAL pid, not merely "non-null".
    marker = fixture_tree["root"] / "host-pid.txt"
    def break_containedHost_build(text):
        old = "$containedHost = [ordered]@{ pid=$hostPid; createdUtc=$null }"
        assert text.count(old) == 1
        marker_literal = str(marker).replace("'", "''")
        return text.replace(old, "Write-Utf8NoBom '%s' ([string]$hostPid)\n    throw 'fixture-post-start-unrecorded'" % marker_literal)
    cmd,env,receipt=prepare(fixture_tree,"normal",mutation=break_containedHost_build)
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=15)
    assert r.returncode==127,(r.stdout,r.stderr)
    assert not (fixture_tree["root"]/"child.json").exists()
    q=json.loads(receipt.read_text(encoding="utf-8"))
    containment=q["containment"]
    real_pid = int(marker.read_text(encoding="utf-8-sig").strip())
    assert containment["ownerPid"] == real_pid
    assert containment["ownerAbsentReason"]==POST_START_UNRECORDED
    assert containment["ownerAbsentDetail"]=="fixture-post-start-unrecorded"
    assert containment["ownerAbsentReason"] not in NO_HOST_TOKENS
    # The whole point: this receipt must NOT be accepted as a legitimate absence,
    # even though a pid is now present.
    with pytest.raises(pytest.fail.Exception):
        assert_owner_absence_is_legitimate(containment, q)


def test_pre_assignment_kill_failure_is_recorded_not_swallowed(fixture_tree):
    # Falsifier for the OTHER half of this packet (PR #105 round 5, sol PR #105
    # blocker): at the pre-assignment site (Invoke-Lane.ps1 ~line 674-696), the
    # host is still OUTSIDE the job, so a swallowed Kill failure there leaves a
    # GENUINE orphan -- unlike the post-timeout kill at ~line 602, where the job
    # is kill-on-close and the tree is already terminated. Combine the same
    # post-start-unrecorded trigger (so the pre-assignment kill path is reached
    # at all: $jobAssigned is still false) with a forced Kill failure, and prove
    # the receipt records ownerKillAttempted/ownerKillOutcome instead of the bare
    # `catch { }` this repo used to have there silently discarding it.
    def break_kill(text):
        old_throw = "$containedHost = [ordered]@{ pid=$hostPid; createdUtc=$null }"
        assert text.count(old_throw) == 1
        text = text.replace(old_throw, "throw 'fixture-post-start-unrecorded'")
        # 1b4a82ab split the old single `Kill($true); [void]WaitForExit(5000)`
        # statement into two lines so WaitForExit's bool return could be
        # captured instead of discarded -- the anchor now spans both lines.
        # `$proc.Kill($true)` alone is NOT unique (the post-timeout kill at
        # ~line 626 also calls it), so the second line's exact indentation is
        # part of the anchor, same discipline as every other anchor here.
        old_kill = "$proc.Kill($true)\n                $exitedWithinWait = $proc.WaitForExit(5000)"
        assert text.count(old_kill) == 1
        return text.replace(old_kill, "throw 'fixture-kill-failed'")
    cmd,env,receipt=prepare(fixture_tree,"normal",mutation=break_kill)
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=15)
    assert r.returncode==127,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    containment=q["containment"]
    # The whole point: the failed kill is VISIBLE, never silent.
    assert containment["ownerKillAttempted"] is True
    assert containment["ownerKillOutcome"]=="kill-threw"
    assert containment["ownerKillDetail"]=="fixture-kill-failed"
    # Forcing the kill to throw means the real kill never ran -- this test, not
    # production code, is responsible for reaping the host it just orphaned.
    # ownerPid is guaranteed non-null by this same packet's other remedy.
    owner_pid = containment["ownerPid"]
    assert owner_pid is not None
    subprocess.run([PWSH,"-NoProfile","-NonInteractive","-Command",
                     f"Stop-Process -Id {int(owner_pid)} -Force -ErrorAction SilentlyContinue"],
                    timeout=5, check=False)


def test_pre_assignment_kill_wait_timeout_is_recorded_not_killed(fixture_tree):
    # Falsifier for PR #105 final (cross-family review of 0f8ba40a): WaitForExit(Int32)
    # RETURNS a bool -- true iff the process exited within the timeout -- and round 5
    # discarded that return with [void], recording 'killed' unconditionally. A host
    # that outlives the bounded wait -- exactly the orphan this whole change exists to
    # make visible -- was therefore reported as killed: manufactured evidence, worse
    # than the bare `catch { }` this whole packet replaced.
    #
    # PR #105 round 6 (falsifier hardening, 2026-09-09): the prior construction shrank
    # the real wait to WaitForExit(0), racing a real WaitForExit call against real OS
    # process teardown on the bet that 0ms wouldn't be enough time for the process to
    # actually exit. It was not reliable: reproduced 3 of 3 in isolation on this host
    # (36 processes, 27% CPU -- well below saturation) as an ASSERTION failure, not a
    # timeout, because WaitForExit(0) sometimes observed the process as already gone.
    # A falsifier for "false observations are classified correctly" that can itself
    # observe true is not a proof.
    #
    # Constructing a real process that genuinely SURVIVES Process.Kill(entireProcessTree:
    # true) plus a real bounded wait is not achievable on demand on this platform --
    # TerminateProcess cannot be caught, ignored, or reliably outlasted by the target,
    # so there is no cheap, reliable way to make a real teardown race land on the false
    # branch every time. Per this packet's own instructions, an unreliable variant is
    # worse than no variant (a 3-of-3-failing test teaches a reader to ignore red), so
    # instead of shrinking the wait, this test now mutates the runner's own source to
    # force the OBSERVED boolean itself to $false -- the same fixture-mutation
    # discipline every other test in this file already uses to reach its own branch
    # (see test_start_threw_is_classified_as_no_host,
    # test_post_start_unrecorded_is_named_not_hidden). The real Kill($true) call is
    # left untouched; only the captured WaitForExit result is forced.
    #
    # WHAT THIS PROVES: the CLASSIFICATION LOGIC -- that a false WaitForExit observation
    # is recorded as 'kill-wait-timeout' and is never silently upgraded to 'killed'.
    # WHAT THIS DOES NOT PROVE: that a real process can outlive a real Kill($true) plus
    # a real five-second WaitForExit on this platform. Whether a genuinely slow-to-die
    # host is always caught inside a realistic multi-second window is a timing property
    # of the OS, not a property of this code path, and is not exercised here.
    def force_wait_false(text):
        old = "$exitedWithinWait = $proc.WaitForExit(5000)"
        assert text.count(old) == 1
        return text.replace(old, "$exitedWithinWait = $false")
    cmd,env,receipt=prepare(fixture_tree,"normal",assignment_failure=True,mutation=force_wait_false)
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=15)
    assert r.returncode==127,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    containment=q["containment"]
    # The whole point: an observed-not-exited result must never be reported as killed.
    assert containment["ownerKillAttempted"] is True
    assert containment["ownerKillOutcome"]=="kill-wait-timeout", (
        f"expected the observed-timeout token, got {containment['ownerKillOutcome']!r} -- "
        "this is the exact false-evidence defect this test exists to catch"
    )
    # Kill() itself was real and unmodified (only the captured wait result is forced),
    # so the host is gone or about to be -- reap defensively like the sibling
    # kill-threw test does.
    owner_pid = containment["ownerPid"]
    assert owner_pid is not None
    subprocess.run([PWSH,"-NoProfile","-NonInteractive","-Command",
                     f"Stop-Process -Id {int(owner_pid)} -Force -ErrorAction SilentlyContinue"],
                    timeout=5, check=False)


def test_kill_outcome_token_set_matches_producer_constants():
    # Guard against the exact drift the cross-family review flagged: this file's
    # KILL_OUTCOME_TOKENS is a hand-copy of Invoke-Lane.ps1's $OWNER_KILL_OUTCOME_*
    # constants because this file cannot import a .ps1. Pin the full set against the
    # source directly so an added/renamed/removed token fails this test loudly
    # instead of only failing closed by accident via an exact-string assertion
    # elsewhere.
    text = CANDIDATE.read_text(encoding="utf-8")
    found = set(re.findall(r"\$OWNER_KILL_OUTCOME_\w+\s*=\s*'([^']+)'", text))
    assert found == KILL_OUTCOME_TOKENS, f"producer constants {found!r} != pinned set {KILL_OUTCOME_TOKENS!r}"


def test_owner_absence_helper_fails_loud_on_post_start_or_unrecognised_tokens():
    # Prove the test-side guardrail itself is reachable and fires (not just
    # written): both the named-ambiguous token and a wholly unrecognised string
    # must be rejected, never silently tolerated the way round 3's bare
    # "if reason: pass" tolerated any truthy string.
    for reason in (POST_START_UNRECORDED, "something-unrecognised", None):
        with pytest.raises(pytest.fail.Exception):
            assert_owner_absence_is_legitimate({"ownerPid": None, "ownerAbsentReason": reason}, {"case": reason})
    # And the closed set itself must still pass.
    for reason in NO_HOST_TOKENS:
        assert_owner_absence_is_legitimate({"ownerPid": None, "ownerAbsentReason": reason}, {"case": reason})


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
        # PR #105 round 3: this parametrization mocks $sw.Elapsed to already exceed
        # the budget, so Invoke-Lane.ps1:470-472 throws before $proc = ...Start()
        # (proven by marker.exists() is False above) -- the same code path
        # -TimeoutSec 0 reproduces for real in
        # test_zero_timeout_exhausts_budget_before_spawn_and_names_the_reason.
        assert q["containment"]["ownerAbsentReason"]=="launch-budget-exhausted"
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


def ledger_rows(root):
    ledger=root/".claude-state"/"coordination"/"dual-lane"/"receipts"/"dispatch-reservations.jsonl"
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_direct_launch_writes_a_reserved_versioned_ledger_row_naming_its_receipt(fixture_tree):
    # TOOL-GUARD-COVERAGE-ARM-UNSATISFIABLE-1: a direct Invoke-Lane launch is a dispatch the
    # product-ratio guard must see, so it writes its own 'reserved' row.
    cmd,env,receipt=prepare(fixture_tree,"normal")
    env.pop("MLV_DISPATCH_RESERVATION_ID",None)
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=30)
    assert r.returncode==0,(r.stdout,r.stderr)
    rows=ledger_rows(fixture_tree["root"])
    assert len(rows)==1
    row=rows[0]
    assert row["schemaVersion"]==2 and row["venue"]=="invoke-lane" and row["state"]=="reserved"
    assert Path(row["receiptPath"]).resolve()==receipt.resolve()
    assert row["recordedUtc"].endswith("Z") and row["allowEdits"] is False and row["lane"]=="sonnet"
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["dispatchLedger"]=={"state":"reserved","reservationId":row["reservationId"]}


def test_dispatcher_launch_writes_a_linked_row_not_a_second_reservation(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal")
    env["MLV_DISPATCH_RESERVATION_ID"]="11111111-2222-3333-4444-555555555555"
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=30)
    assert r.returncode==0,(r.stdout,r.stderr)
    rows=ledger_rows(fixture_tree["root"])
    assert [(x["state"],x["reservationId"]) for x in rows]==[("linked","11111111-2222-3333-4444-555555555555")]
    assert Path(rows[0]["receiptPath"]).resolve()==receipt.resolve()


def test_unwritable_ledger_refuses_the_launch_with_a_named_failure(fixture_tree):
    cmd,env,receipt=prepare(fixture_tree,"normal")
    env.pop("MLV_DISPATCH_RESERVATION_ID",None)
    # A DIRECTORY where the ledger file should be makes every append fail.
    (fixture_tree["root"]/".claude-state"/"coordination"/"dual-lane"/"receipts"/"dispatch-reservations.jsonl").mkdir(parents=True)
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=60)
    assert r.returncode!=0
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["failure"].startswith("dispatch-ledger-write-failed") and not q["complete"]
    assert not (fixture_tree["root"]/"child.json").exists(), "no provider may start without a ledger row"


def _git(root, *args):
    r=subprocess.run(["git","-C",str(root)]+list(args),capture_output=True,text=True,timeout=10)
    assert r.returncode==0,(args,r.stdout,r.stderr)
    return r.stdout


def _seed_git_repo(root):
    _git(root,"init","-q")
    _git(root,"config","user.email","fixture@example.com")
    _git(root,"config","user.name","fixture")
    (root/"tracked.txt").write_text("original\n",encoding="ascii")
    _git(root,"add","tracked.txt")
    _git(root,"commit","-q","-m","seed")


# Round 5 (sol minor 1 / fable minor 1), required cases: inject a git-capture failure at exactly
# the PRE-launch snapshot or exactly the POST-exit snapshot, and prove each is recorded as
# dirtyCheck=='unavailable' without ever flipping the receipt's state -- neither a false
# 'ended-incomplete' nor a silent claim of 'clean'. The injection keys on MLV_FIXTURE_CHILD's
# existence (written as literally the fixture child's first action, before any dirty-file
# simulation runs) rather than a call counter, because that marker is already exactly the
# pre-launch/post-exit boundary this script cares about: the PRE snapshot always runs before the
# child process exists, and the POST snapshot always runs after the child has already exited.
def _inject_git_capture_failure(text):
    marker = "function Invoke-GitCaptureUtf8([string]$WorkDir, [string[]]$GitArgs) {\n"
    assert text.count(marker) == 1, "Invoke-GitCaptureUtf8 signature not found or not unique"
    injected = marker + (
        "    if ($env:MLV_FIXTURE_GIT_FAIL_MODE -and $GitArgs.Count -gt 0 -and $GitArgs[0] -eq 'status') {\n"
        "        $childStarted = Test-Path -LiteralPath $env:MLV_FIXTURE_CHILD\n"
        "        if ((($env:MLV_FIXTURE_GIT_FAIL_MODE -eq 'pre') -and -not $childStarted) -or "
        "(($env:MLV_FIXTURE_GIT_FAIL_MODE -eq 'post') -and $childStarted)) {\n"
        "            return [ordered]@{ ok = $false; stdout = $null; exitCode = $null; error = 'fixture-injected-git-failure' }\n"
        "        }\n"
        "    }\n"
    )
    return text.replace(marker, injected)


def test_pre_launch_git_capture_failure_is_unavailable_and_never_flips_state(fixture_tree):
    # Producer brief round 5, required case 1 (fail-closed git capture): if the PRE-launch
    # tracked-dirt snapshot cannot be taken at all, the check must never fall back to treating
    # that as "nothing was dirty" -- it must record dirtyCheck=='unavailable' and leave the
    # receipt's state exactly as the envelope says (never a false ended-incomplete).
    root=fixture_tree["root"]
    _seed_git_repo(root)
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit",
                             mutation=_inject_git_capture_failure)
    env["MLV_FIXTURE_GIT_FAIL_MODE"]="pre"
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="complete" and q["complete"] is True
    assert q["dirtyCheck"]=="unavailable"
    assert "pre-launch snapshot" in q["dirtyCheckReason"]
    assert q["workEvidence"]["reason"]!="dirty-worktree-no-commit"


def test_post_exit_git_capture_failure_is_unavailable_and_never_claims_clean(fixture_tree):
    # Producer brief round 5, required case 1: the lane DOES introduce uncommitted tracked dirt
    # (MLV_FIXTURE_DIRTY_TRACKED_PATH), so a working check would flip this to ended-incomplete --
    # but the POST-exit snapshot is the one that fails here, so there is no positive evidence
    # either way. The receipt must show dirtyCheck=='unavailable', never 'clean' (which would be
    # a false claim the tree was actually verified) and never force ended-incomplete (which would
    # be treating an unreachable git as if it were positive evidence of a dirty tree).
    root=fixture_tree["root"]
    _seed_git_repo(root)
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit",
                             mutation=_inject_git_capture_failure)
    env["MLV_FIXTURE_GIT_FAIL_MODE"]="post"
    env["MLV_FIXTURE_DIRTY_TRACKED_PATH"]=str(root/"tracked.txt")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="complete" and q["complete"] is True
    assert q["dirtyCheck"]=="unavailable"
    assert "post-exit snapshot" in q["dirtyCheckReason"]
    assert q["workEvidence"]["reason"]!="dirty-worktree-no-commit"


# Round 7 (sol minor): a failing pre- or post- `git rev-parse HEAD` must surface as the same
# 'unavailable' state as a status/hash-object capture failure, not as 'not-applicable' (which
# the old uncaptured-failure code path produced by reading a failed call and a legitimately
# inapplicable gate as the same falsy $BaseSha). Same marker-based pre/post injection as the
# status-capture failure tests above, keyed on GitArgs[0] -eq 'rev-parse' instead of 'status'.
def _inject_git_revparse_failure(text):
    marker = "function Invoke-GitCaptureUtf8([string]$WorkDir, [string[]]$GitArgs) {\n"
    assert text.count(marker) == 1, "Invoke-GitCaptureUtf8 signature not found or not unique"
    injected = marker + (
        "    if ($env:MLV_FIXTURE_GIT_FAIL_MODE -and $GitArgs.Count -gt 0 -and $GitArgs[0] -eq 'rev-parse') {\n"
        "        $childStarted = Test-Path -LiteralPath $env:MLV_FIXTURE_CHILD\n"
        "        if ((($env:MLV_FIXTURE_GIT_FAIL_MODE -eq 'pre') -and -not $childStarted) -or "
        "(($env:MLV_FIXTURE_GIT_FAIL_MODE -eq 'post') -and $childStarted)) {\n"
        "            return [ordered]@{ ok = $false; stdout = $null; exitCode = $null; error = 'fixture-injected-git-revparse-failure' }\n"
        "        }\n"
        "    }\n"
    )
    return text.replace(marker, injected)


def test_pre_launch_revparse_head_failure_is_unavailable_not_not_applicable(fixture_tree):
    root=fixture_tree["root"]
    _seed_git_repo(root)
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit",
                             mutation=_inject_git_revparse_failure)
    env["MLV_FIXTURE_GIT_FAIL_MODE"]="pre"
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="complete" and q["complete"] is True
    assert q["dirtyCheck"]=="unavailable"
    assert "pre-launch rev-parse HEAD failed" in q["dirtyCheckReason"]
    assert q["baseSha"] is None


def test_post_exit_revparse_head_failure_is_unavailable_not_head_moved(fixture_tree):
    root=fixture_tree["root"]
    _seed_git_repo(root)
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit",
                             mutation=_inject_git_revparse_failure)
    env["MLV_FIXTURE_GIT_FAIL_MODE"]="post"
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="complete" and q["complete"] is True
    assert q["dirtyCheck"]=="unavailable"
    assert "post-exit rev-parse HEAD failed" in q["dirtyCheckReason"]
    assert q["baseSha"] is not None


def test_further_edit_of_pre_dirty_binary_tracked_file_is_ended_incomplete(fixture_tree):
    # Producer brief round 5, required case 2 (content identity): a binary tracked file that was
    # ALREADY dirty before the lane started, and that the lane edits AGAIN with DIFFERENT binary
    # bytes without staging or committing either edit, must still flip the receipt. `git diff
    # HEAD -- <path>` renders any binary difference as the fixed text "Binary files a/<path> and
    # b/<path> differ" -- identical no matter which bytes are actually on disk -- so a content
    # identity built on hashing that diff TEXT (the pre-round-5 mechanism) cannot distinguish the
    # pre-dirty bytes from the lane's further edit at all. `git hash-object` of the working-tree
    # bytes themselves can.
    root=fixture_tree["root"]
    _seed_git_repo(root)
    binary_name="tracked.bin"
    binary_path=root/binary_name
    binary_path.write_bytes(bytes([0x00,0x01,0x02,0x7F,0x80,0xFF]))
    _git(root,"add",binary_name)
    _git(root,"commit","-q","-m","seed binary tracked file")
    # Pre-dirty it, uncommitted, before the lane ever starts -- different bytes than the seed.
    binary_path.write_bytes(bytes([0x00,0xAA,0xBB,0xCC,0xDD,0xFF]))
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit")
    env["MLV_FIXTURE_FURTHER_EDIT_BINARY_TRACKED_PATH"]=str(binary_path)
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="ended-incomplete"
    assert q["complete"] is False
    assert q["workEvidence"]["reason"]=="dirty-worktree-no-commit"
    assert q["dirtyCheck"]=="dirty"


# LANE-NO-BACKGROUND-END-TURN-1 round 2 (sol major 1 / fable minor 3): the check is now
# gated to -AllowEdits Claude lanes, and it fires only on tracked dirt the LANE ITSELF
# introduces during its run -- compared against a snapshot taken before the child ever
# starts -- never on dirt that merely pre-exists the worktree. The fixture's fake child
# writes to MLV_FIXTURE_DIRTY_TRACKED_PATH mid-run, without committing, simulating exactly
# the "I'll resume when the background job completes" shape this check exists to catch:
# HEAD never moves and the edit is never committed.
def test_dirty_tracked_worktree_introduced_by_editing_lane_is_ended_incomplete(fixture_tree):
    root=fixture_tree["root"]
    _seed_git_repo(root)
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit")
    env["MLV_FIXTURE_DIRTY_TRACKED_PATH"]=str(root/"tracked.txt")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="ended-incomplete"
    assert q["complete"] is False
    assert q["workEvidence"]["reason"]=="dirty-worktree-no-commit"


def test_further_edit_of_already_dirty_tracked_file_is_ended_incomplete(fixture_tree):
    # Producer brief round 3, required case (sol minor / fable minor 2): a path that was
    # ALREADY dirty before the lane started, and that the lane edits AGAIN without staging or
    # committing either edit, must still flip the receipt -- even though the porcelain status
    # line for that path (' M tracked.txt') never changes text across either edit. Only a
    # per-path content-identity comparison (not a status-line comparison) can see this.
    root=fixture_tree["root"]
    _seed_git_repo(root)
    (root/"tracked.txt").write_text("dirty-before-the-lane-ever-ran\n",encoding="ascii")
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit")
    env["MLV_FIXTURE_FURTHER_EDIT_TRACKED_PATH"]=str(root/"tracked.txt")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="ended-incomplete"
    assert q["complete"] is False
    assert q["workEvidence"]["reason"]=="dirty-worktree-no-commit"


def test_further_edit_of_already_dirty_tracked_path_with_space_and_non_ascii_name_is_ended_incomplete(fixture_tree):
    # Producer brief round 4, required case (sol + fable minor): round 3's parser read
    # `git status --porcelain` (no -z) and stripped one leading/trailing '"' per path -- git's
    # default quoting wraps a path in '"' and C-style-octal-escapes non-ASCII bytes whenever it
    # quotes at all, so Trim('"') alone cannot restore a path containing BOTH a space and a
    # non-ASCII character (a space alone needs no quoting; a non-ASCII byte alone gets quoted
    # AND escaped). `-z` disables quoting entirely, so the real path must round-trip exactly.
    # Exercises the exact "pre-dirty, then further edited" shape as the round-3 ASCII test
    # above, on a path this repo's real fleet-runs tree can plausibly contain.
    root=fixture_tree["root"]
    _seed_git_repo(root)
    tracked_name = "trac ked éè.txt"  # embedded space + non-ASCII (accented) characters
    tracked_path = root / tracked_name
    tracked_path.write_text("seed\n", encoding="utf-8")
    _git(root, "add", tracked_name)
    _git(root, "commit", "-q", "-m", "seed space/non-ASCII tracked file")
    # Pre-dirty it, uncommitted, before the lane ever starts.
    tracked_path.write_text("dirty-before-the-lane-ever-ran\n", encoding="utf-8")
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit")
    env["MLV_FIXTURE_FURTHER_EDIT_TRACKED_PATH"]=str(tracked_path)
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="ended-incomplete"
    assert q["complete"] is False
    assert q["workEvidence"]["reason"]=="dirty-worktree-no-commit"


def test_untracked_only_dirt_does_not_trigger_dirty_no_commit(fixture_tree):
    root=fixture_tree["root"]
    _seed_git_repo(root)
    (root/"scratch.log").write_text("untracked scratch output\n",encoding="ascii")
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="complete" and q["complete"] is True
    assert q["workEvidence"]["reason"]!="dirty-worktree-no-commit"


def test_clean_git_worktree_is_still_marked_complete(fixture_tree):
    # The check must not misfire on the common healthy case: a git worktree with nothing
    # dirty at all, exercised through an editing lane so the gated code path actually runs.
    root=fixture_tree["root"]
    _seed_git_repo(root)
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="complete" and q["complete"] is True
    assert q["workEvidence"]["reason"]!="dirty-worktree-no-commit"
    assert q["failure"] is None


def test_pre_existing_tracked_dirt_unchanged_by_editing_lane_stays_complete(fixture_tree):
    # Producer brief round 2, required case: dirt that existed BEFORE the lane ever ran, and
    # that the lane's own run leaves byte-for-byte unchanged, must never flip a receipt -- only
    # dirt the lane itself introduces counts. The fixture's fake child touches nothing here (no
    # MLV_FIXTURE_DIRTY_TRACKED_PATH), so the pre-existing modification survives unchanged.
    root=fixture_tree["root"]
    _seed_git_repo(root)
    (root/"tracked.txt").write_text("dirty-before-the-lane-ever-ran\n",encoding="ascii")
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="complete" and q["complete"] is True
    assert q["workEvidence"]["reason"]!="dirty-worktree-no-commit"


def test_committed_work_with_leftover_tracked_dirt_stays_complete(fixture_tree):
    # Producer brief round 2, required case: pins the HEAD guard. The fixture's fake child
    # commits its edit (moving HEAD away from BaseSha) and then leaves a further,
    # still-uncommitted edit on top -- a regression that dropped the `headAfter -eq $BaseSha`
    # condition would misclassify this as dirty-worktree-no-commit (fable minor 4 / sol minor 3:
    # no prior test pinned this suppression direction).
    root=fixture_tree["root"]
    _seed_git_repo(root)
    cmd,env,receipt=prepare(fixture_tree,"normal",editing=True,allowed_tools="Read,Write,Edit")
    env["MLV_FIXTURE_COMMIT_TRACKED_PATH"]=str(root/"tracked.txt")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="complete" and q["complete"] is True
    assert q["workEvidence"]["reason"]!="dirty-worktree-no-commit"


def test_codex_lane_with_dirty_tracked_worktree_is_never_ended_incomplete_by_this_rule(fixture_tree):
    # Producer brief round 2, required case: the override is a Claude-only, -AllowEdits-only
    # concept ($InitialTrackedDirt is captured only when engine=='claude' and $AllowEdits) -- a
    # codex lane must never be forced into ended-incomplete by it, no matter how dirty the
    # tracked worktree is.
    root=fixture_tree["root"]
    _seed_git_repo(root)
    (root/"tracked.txt").write_text("dirty-tracked-file\n",encoding="ascii")
    cmd,env,receipt=prepare(fixture_tree,"normal",lane="sol")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="complete" and q["complete"] is True
    assert q["workEvidence"]["reason"]!="dirty-worktree-no-commit"


def test_read_only_claude_lane_with_dirty_tracked_worktree_is_never_ended_incomplete_by_this_rule(fixture_tree):
    # Producer brief round 2, required case: a read-only Claude lane can never move HEAD by
    # construction, so without the -AllowEdits gate this degenerated into "was the surrounding
    # checkout dirty" -- a fact outside a review lane's control (sol major 1 / fable minor 3:
    # the round-1 positive test for this check actually used a read-only lane).
    root=fixture_tree["root"]
    _seed_git_repo(root)
    (root/"tracked.txt").write_text("dirty-but-uncommitted\n",encoding="ascii")
    cmd,env,receipt=prepare(fixture_tree,"normal")
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,(r.stdout,r.stderr)
    q=json.loads(receipt.read_text(encoding="utf-8"))
    assert q["state"]=="complete" and q["complete"] is True
    assert q["workEvidence"]["reason"]!="dirty-worktree-no-commit"
