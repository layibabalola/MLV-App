#!/usr/bin/env python3
import argparse, json, os, sys, time
from pathlib import Path
p=argparse.ArgumentParser()
for name in ("model","effort","role","subject"): p.add_argument("--"+name,required=True)
p.add_argument("--sleep",type=float,required=True)
p.add_argument("--harness-mode",choices=("SHADOW","CONTAINMENT"),required=True)
p.add_argument("--attempt",type=int,required=True)
a=p.parse_args()
if os.environ.get("MLV_FAKE_STARTED_MARKER"):
    Path(os.environ["MLV_FAKE_STARTED_MARKER"]).write_text("started\n", encoding="utf-8")
if os.environ.get("MLV_FAKE_ATTEMPT_LOG"):
    with Path(os.environ["MLV_FAKE_ATTEMPT_LOG"]).open("a", encoding="utf-8") as handle:
        handle.write(f"{a.attempt}\n")
if a.attempt <= int(os.environ.get("MLV_FAKE_FAIL_ATTEMPTS", "0")):
    sys.stderr.write(f"deterministic fake failure {a.attempt}\n")
    raise SystemExit(17)
time.sleep(a.sleep)
print(json.dumps({"model":a.model,"effort":a.effort,"role":a.role,"subject":a.subject,
                  "harnessMode":a.harness_mode,"attempt":a.attempt,"provider":"FAKE_ONLY",
                  "processImagePath":str(Path(sys.executable).resolve()),
                  "scriptPath":str(Path(__file__).resolve()),
                  "requestedAuthority":("OPEN_GATE_AND_ADOPT" if a.harness_mode=="CONTAINMENT"
                                        else "NONE")},sort_keys=True))
