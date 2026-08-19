#!/usr/bin/env python3
import argparse, json, os, time
from pathlib import Path
p=argparse.ArgumentParser()
for name in ("model","effort","role","subject"): p.add_argument("--"+name,required=True)
p.add_argument("--sleep",type=float,required=True)
a=p.parse_args()
if os.environ.get("MLV_FAKE_STARTED_MARKER"):
    Path(os.environ["MLV_FAKE_STARTED_MARKER"]).write_text("started\n", encoding="utf-8")
time.sleep(a.sleep)
print(json.dumps({"model":a.model,"effort":a.effort,"role":a.role,"subject":a.subject},sort_keys=True))
