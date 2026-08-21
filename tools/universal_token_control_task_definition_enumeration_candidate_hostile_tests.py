#!/usr/bin/env python3
"""Provider-free hostile controls for the exact R37 task-definition candidate."""

from __future__ import annotations

import copy
import json
import pathlib
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "docs" / "universal-token-control-task-definition-enumeration-candidate-2026-08-21.json"
VERIFIER = ROOT / "tools" / "verify_universal_token_control_task_definition_enumeration_candidate.py"


def run(path: pathlib.Path, *, allow_fixture: bool = True) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-B", str(VERIFIER), "--artifact", str(path)]
    if allow_fixture:
        command.append("--allow-mutated-fixture")
    return subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def require_refused(name: str, path: pathlib.Path) -> None:
    result = run(path)
    if result.returncode == 0:
        raise AssertionError(f"{name}: hostile mutation was accepted: {result.stdout}")


def write_json(path: pathlib.Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    pristine = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    passed = 0
    with tempfile.TemporaryDirectory(prefix="mlv-r37-hostile-") as directory:
        root = pathlib.Path(directory)

        clean = root / "clean.json"
        write_json(clean, pristine)
        result = run(clean)
        if result.returncode != 0:
            raise AssertionError(f"clean semantic fixture refused: {result.stderr}")
        passed += 1

        mutations: list[tuple[str, callable]] = [
            ("extra-root-key", lambda d: d.__setitem__("unexpected", False)),
            ("schema-drift", lambda d: d.__setitem__("schema", "wrong")),
            ("status-drift", lambda d: d.__setitem__("status", "ADOPTED")),
            ("project-drift", lambda d: d.__setitem__("projectId", "cloudvore")),
            ("base-commit", lambda d: d["baseCandidate"].__setitem__("commit", "0" * 40)),
            ("base-tree", lambda d: d["baseCandidate"].__setitem__("tree", "0" * 40)),
            ("base-parent", lambda d: d["baseCandidate"].__setitem__("parent", "0" * 40)),
            ("r35-bytes-type", lambda d: d["baseCandidate"]["r35Artifact"].__setitem__("bytes", "21007")),
            ("r36-sha", lambda d: d["baseCandidate"]["r36Artifact"].__setitem__("sha256", "0" * 64)),
            ("machine-input-omit", lambda d: d["machineInputs"].pop()),
            ("machine-input-bytes", lambda d: d["machineInputs"][0].__setitem__("bytes", 1)),
            ("machine-input-hash", lambda d: d["machineInputs"][1].__setitem__("sha256", "0" * 64)),
            ("task-count-type", lambda d: d["observationBoundary"].__setitem__("nonMicrosoftTaskCount", "41")),
            ("readable-count", lambda d: d["observationBoundary"].__setitem__("readableDefinitionCount", 40)),
            ("unreadable-count-bool", lambda d: d["observationBoundary"].__setitem__("unreadableDefinitionCount", False)),
            ("digest", lambda d: d["observationBoundary"].__setitem__("definitionTupleDigest", "0" * 64)),
            ("query-complete-false", lambda d: d["observationBoundary"].__setitem__("taskDefinitionInventoryCompleteForQuery", False)),
            ("inventory-overclaim", lambda d: d["observationBoundary"].__setitem__("launcherInventoryComplete", True)),
            ("graph-overclaim", lambda d: d["observationBoundary"].__setitem__("actionGraphComplete", True)),
            ("semantic-overclaim", lambda d: d["observationBoundary"].__setitem__("semanticCompletenessClaimed", True)),
            ("point-in-time-alias", lambda d: d["observationBoundary"].__setitem__("pointInTimeOnly", 1)),
            ("outside-window-overclaim", lambda d: d["operationalLogBoundary"].__setitem__("outsideWindowAbsenceProven", True)),
            ("definition-row-omit", lambda d: d["relevantDefinitions"].pop()),
            ("definition-task-id", lambda d: d["relevantDefinitions"][0].__setitem__("taskId", "\\Other")),
            ("definition-bytes", lambda d: d["relevantDefinitions"][0]["definition"].__setitem__("bytes", 1304)),
            ("definition-sha", lambda d: d["relevantDefinitions"][1]["definition"].__setitem__("sha256", "0" * 64)),
            ("enabled-type", lambda d: d["relevantDefinitions"][2].__setitem__("enabled", 0)),
            ("result-type", lambda d: d["relevantDefinitions"][3].__setitem__("lastTaskResult", False)),
            ("history-type", lambda d: d["relevantDefinitions"][5]["history"].__setitem__("eventCount", "4200")),
            ("absence-credit", lambda d: d["r35EdgeReconciliation"][0].__setitem__("globalAbsenceCredit", True)),
            ("installed-overclaim", lambda d: d["closedFinding"].__setitem__("mlvAppUniversalControlInstalled", True)),
            ("gap-not-closed", lambda d: d["closedFinding"].__setitem__("currentWindowsTaskDefinitionGapClosed", False)),
            ("uncredited-omit", lambda d: d["uncreditedProofs"].pop()),
            ("authority-true", lambda d: d["authority"].__setitem__("installation", True)),
            ("authority-type", lambda d: d["authority"].__setitem__("projectAdoption", 0)),
            ("forbidden-action-omit", lambda d: d["nextLawfulStep"]["forbiddenActions"].pop()),
        ]
        for index, (name, mutate) in enumerate(mutations, start=1):
            value = copy.deepcopy(pristine)
            mutate(value)
            path = root / f"{index:02d}-{name}.json"
            write_json(path, value)
            require_refused(name, path)
            passed += 1

        duplicate = root / "duplicate.json"
        raw = ARTIFACT.read_text(encoding="utf-8")
        duplicate.write_text(raw.replace('"schema":', '"Schema": "wrong",\n  "schema":', 1), encoding="utf-8", newline="\n")
        require_refused("case-colliding-key", duplicate)
        passed += 1

        malformed = root / "malformed.json"
        malformed.write_text("{", encoding="utf-8")
        require_refused("malformed-json", malformed)
        passed += 1

        pinned = run(ARTIFACT, allow_fixture=False)
        if pinned.returncode != 0:
            raise AssertionError(f"frozen artifact refused: {pinned.stderr}")
        passed += 1

    print(f"RESULT: PASS ({passed} assertions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
