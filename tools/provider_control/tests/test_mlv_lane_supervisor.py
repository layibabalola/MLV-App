from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import mlv_lane_supervisor as supervisor  # noqa: E402
from vendor.universal_provider_control import (  # noqa: E402
    ControlError, strict_json_file, validate_contract, validate_project_profile,
)


ZERO = "sha256:" + "0" * 64
VENDOR_BLOBS = {
    "vendor/universal_provider_control.py": "0e26b15f249f89972e2fc7807ccd0d98a0bd4954",
    "schemas/provider-native-capacity-evidence-v1.schema.json": "9c80864627b60c4d217eed2a907fe51f37d28a30",
    "schemas/universal-broker-health-v1.schema.json": "98ad95fd6fb26f9b384b5c8c0f1ff205815e5426",
    "schemas/universal-capacity-observation-v1.schema.json": "9039f80de643b521c32d08565d645f244545f740",
    "schemas/universal-control-request-v1.schema.json": "3d9f75ed871c453dd325424e71e43dd0948e8993",
    "schemas/universal-evidence-capsule-request-v1.schema.json": "baa7b26b05744542ecfbe1c3ea60010b90bff91d",
    "schemas/universal-evidence-capsule-v1.schema.json": "c6dd7dab540388f1ca8ebd774b2a188c02789ecb",
    "schemas/universal-gate-transition-v1.schema.json": "64b9261d6fe22d533853cd29a834b14945085f69",
    "schemas/universal-launch-attestation-v1.schema.json": "0f20d156c96c2a9c964c68af6b72e5f920c20c44",
    "schemas/universal-launcher-inventory-v1.schema.json": "a4e123b578bdcd9e747a0ef900776a76d07bed45",
    "schemas/universal-manual-canary-authorization-v1.schema.json": "706cb96fc133624b160bd63a653356baf390d6db",
    "schemas/universal-process-observation-v1.schema.json": "3cdd7412abfb490325d01481bfc5e8d23e03428b",
    "schemas/universal-project-profile-v1.schema.json": "40790b64747786c7c6d506cd92618349d852129e",
}


def git_blob_sha1(path: Path) -> str:
    raw = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def demand(**changes):
    value = {
        "schema": "mlv-provider-demand/v1", "project": "mlv-app", "hasWork": False,
        "lane": "fable", "priority": "PRODUCT_WORK", "estimateFraction": 0.1,
        "availableFraction": 0.9, "turns": 4, "contextTokens": 32000,
        "capsuleSha256": ZERO, "checkpointSha256": ZERO, "cacheAffinitySha256": ZERO,
    }
    value.update(changes)
    return value


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.state = self.base / "state"
        self.demand = self.base / "demand.json"
        self.default_root = mock.patch.object(supervisor, "DEFAULT_STATE_ROOT", self.state)
        self.default_root.start()

    def tearDown(self):
        self.default_root.stop()
        self.temp.cleanup()

    def write_demand(self, value):
        self.demand.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")

    def test_01_vendor_is_exact_canonical_doctrine_bytes(self):
        self.assertEqual(supervisor.DOCTRINE_COMMIT, "488cf0dc0c2c2ddd1ab024c6377e1fd6d61eef1d")
        for relative, expected in VENDOR_BLOBS.items():
            with self.subTest(relative=relative):
                self.assertEqual(git_blob_sha1(ROOT / relative), expected)

    def test_02_profile_and_intended_inventory_validate(self):
        profile = strict_json_file(ROOT / "mlv-project-profile.candidate.json")
        validate_project_profile(profile)
        inventory = strict_json_file(ROOT / "inventory-post-install.candidate.json")
        validate_contract("inventory", inventory)

    def test_03_default_missing_state_is_closed(self):
        self.assertEqual(supervisor.UniversalProviderBroker(self.state).gate_state(), "CLOSED")

    def test_04_one_thousand_no_work_ticks_are_zero_provider(self):
        self.write_demand(demand())
        with mock.patch.object(supervisor.subprocess, "Popen", side_effect=AssertionError("provider invoked")):
            first = supervisor.tick(self.state, self.demand)
            self.assertEqual(first["status"], "IDLE_RECORDED")
            for _ in range(1000):
                result = supervisor.tick(self.state, self.demand)
                self.assertEqual(result["status"], "IDLE_SKIPPED")
                self.assertEqual(result["providerCalls"], 0)
                self.assertEqual(result["providerProcesses"], 0)
                self.assertEqual(result["inputTokens"], 0)
                self.assertEqual(result["outputTokens"], 0)
        self.assertFalse((self.state / "universal-provider-control.sqlite3").exists())
        self.assertFalse((self.state / "claude-shared-account.quota.lock").exists())

    def test_05_work_refuses_closed_before_provider_resolution(self):
        self.write_demand(demand(hasWork=True))
        with mock.patch.object(supervisor.subprocess, "Popen", side_effect=AssertionError("provider invoked")):
            result = supervisor.tick(self.state, self.demand)
        self.assertEqual(result["reason"], "AUTOMATIC_LAUNCH_GATE_CLOSED")
        self.assertEqual(result["providerCalls"], 0)
        self.assertEqual(result["automaticLaunchGate"], "CLOSED")

    def test_06_auth_reset_capacity_and_refusal_cannot_open(self):
        for event in ("AUTH_SUCCESS", "RESET_OBSERVED", "CAPACITY_RETURNED", "QUOTA_REFUSAL"):
            with self.subTest(event=event):
                result = supervisor.observe_signal(self.state, event)
                self.assertEqual(result["automaticLaunchGate"], "CLOSED")
        self.assertEqual(supervisor.UniversalProviderBroker(self.state).gate_state(), "CLOSED")

    def test_07_fake_seam_is_disabled_without_explicit_test_environment(self):
        self.write_demand(demand(hasWork=True))
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ControlError, "TEST_SEAM_DISABLED"):
                supervisor.run_test_fake(self.state, self.demand, HERE / "fake_provider.py", 0)

    def test_08_capacity_reserve_refuses_before_fake_provider(self):
        self.write_demand(demand(hasWork=True, availableFraction=0.4, estimateFraction=0.1))
        env = {supervisor.FAKE_ENV: "1", supervisor.TEST_STATE_ROOT_ENV: str(self.state)}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(supervisor.subprocess, "Popen", side_effect=AssertionError("provider invoked")):
                with self.assertRaisesRegex(ControlError, "CAPACITY_RESERVE_REFUSED"):
                    supervisor.run_test_fake(self.state, self.demand, HERE / "fake_provider.py", 0)

    def test_09_fake_receipt_binds_model_effort_role_subject_and_argv(self):
        self.write_demand(demand(hasWork=True))
        env = {supervisor.FAKE_ENV: "1", supervisor.TEST_STATE_ROOT_ENV: str(self.state)}
        with mock.patch.dict(os.environ, env, clear=False):
            result = supervisor.run_test_fake(self.state, self.demand, HERE / "fake_provider.py", 0)
        self.assertEqual(result["status"], "TEST_FAKE_COMPLETED")
        self.assertEqual(result["providerCalls"], 1)
        self.assertEqual(result["binding"]["model"], "claude-opus-5")
        self.assertEqual(result["binding"]["effort"], "high")
        self.assertEqual(result["binding"]["role"], "HUB")
        for field in ("subjectSha256", "executableSha256", "argvSha256"):
            self.assertRegex(result["binding"][field], r"^sha256:[0-9a-f]{64}$")

    def test_10_quota_lock_lives_for_entire_fake_child(self):
        self.write_demand(demand(hasWork=True))
        command = [sys.executable, str(ROOT / "mlv_lane_supervisor.py"), "--state-root", str(self.state),
                   "test-fake-provider", "--demand", str(self.demand), "--fake-provider",
                   str(HERE / "fake_provider.py"), "--sleep", "0.8"]
        marker = self.base / "fake-started"
        env = dict(os.environ); env[supervisor.FAKE_ENV] = "1"
        env[supervisor.TEST_STATE_ROOT_ENV] = str(self.state)
        env["MLV_FAKE_STARTED_MARKER"] = str(marker)
        first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists(), "first fake child did not start")
        second = subprocess.run(command[:-1] + ["0"], capture_output=True, text=True, env=env, timeout=5)
        first_out, first_err = first.communicate(timeout=5)
        self.assertEqual(first.returncode, 0, first_err)
        self.assertEqual(json.loads(first_out)["status"], "TEST_FAKE_COMPLETED")
        self.assertEqual(second.returncode, 2)
        self.assertEqual(json.loads(second.stdout)["reason"], "QUOTA_DOMAIN_BUSY")

    def test_11_turn_and_context_ceilings_refuse(self):
        for values, reason in (({"turns": 13}, "TURN_BUDGET_REFUSED"),
                               ({"contextTokens": 120001}, "CONTEXT_BUDGET_REFUSED")):
            with self.subTest(reason=reason):
                self.write_demand(demand(**values))
                with self.assertRaisesRegex(ControlError, reason):
                    supervisor.tick(self.state, self.demand)

    def test_12_unknown_fields_and_bad_bindings_refuse(self):
        value = demand(); value["surprise"] = True; self.write_demand(value)
        with self.assertRaisesRegex(ControlError, "DEMAND_INVALID"):
            supervisor.tick(self.state, self.demand)
        self.write_demand(demand(capsuleSha256="raw-account-or-secret"))
        with self.assertRaisesRegex(ControlError, "DEMAND_BINDING_INVALID"):
            supervisor.tick(self.state, self.demand)

    def test_13_author_packet_binds_every_candidate_subject(self):
        packet = strict_json_file(ROOT / "AUTHOR-PACKET.json")
        self.assertEqual(packet["status"], "DISTINGUISH_R2_ZERO_AUTHORITY")
        self.assertEqual(packet["doctrineCommit"], supervisor.DOCTRINE_COMMIT)
        self.assertEqual(packet["r1Commit"], supervisor.R1_COMMIT)
        self.assertEqual(packet["profileSha256"], supervisor.sha256_file(supervisor.PROFILE)[7:])
        self.assertEqual(packet["bindingsSha256"], supervisor.sha256_file(supervisor.BINDINGS)[7:])
        for subject in packet["subjects"]:
            path = ROOT.parents[1] / subject["path"]
            with self.subTest(path=subject["path"]):
                self.assertEqual(path.stat().st_size, subject["bytes"])
                self.assertEqual(supervisor.sha256_file(path)[7:], subject["sha256"])

    def test_14_alternate_root_is_refused_before_quota_lock(self):
        self.write_demand(demand(hasWork=True))
        alternate = self.base / "alternate-state"
        env = {supervisor.FAKE_ENV: "1", supervisor.TEST_STATE_ROOT_ENV: str(self.state)}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(supervisor, "quota_lock",
                                   side_effect=AssertionError("quota lock reached")):
                with self.assertRaisesRegex(ControlError, "STATE_ROOT_IDENTITY_MISMATCH"):
                    supervisor.run_test_fake(alternate, self.demand, HERE / "fake_provider.py", 0)
        self.assertFalse((alternate / "claude-shared-account.quota.lock").exists())

    def test_15_first_and_changed_no_work_are_distinct_and_typed(self):
        self.write_demand(demand(contextTokens=32000))
        first = supervisor.tick(self.state, self.demand)
        self.assertEqual(first["status"], "IDLE_RECORDED")
        prior = strict_json_file(self.state / "idle-fingerprint.json")
        self.assertEqual(prior["fingerprintType"], "CANONICAL_DEMAND_V1")
        self.assertEqual(prior["demandType"], "NO_WORK")
        self.write_demand(demand(contextTokens=32001))
        changed = supervisor.tick(self.state, self.demand)
        self.assertEqual(changed["status"], "IDLE_CHANGED")
        self.assertNotEqual(first["demandFingerprint"], changed["demandFingerprint"])
        unchanged = supervisor.tick(self.state, self.demand)
        self.assertEqual(unchanged["status"], "IDLE_SKIPPED")

    def test_16_binding_bytes_and_subject_digests_are_hash_pinned(self):
        original = strict_json_file(supervisor.BINDINGS)
        for mutation in ("model", "role", "subject"):
            changed = json.loads(json.dumps(original))
            if mutation == "model":
                changed["lanes"]["fable"]["model"] = "redirected-model"
            elif mutation == "role":
                changed["lanes"]["fable"]["role"] = "REDIRECTED_ROLE"
            else:
                changed["lanes"]["fable"]["subject"] = "subjects/seat-opus.md"
            path = self.base / (mutation + ".json")
            path.write_text(json.dumps(changed, sort_keys=True), encoding="utf-8")
            with self.subTest(mutation=mutation), mock.patch.object(supervisor, "BINDINGS", path):
                with self.assertRaisesRegex(ControlError, "LANE_BINDINGS_DIGEST_MISMATCH"):
                    supervisor.load_contracts()
        changed = json.loads(json.dumps(original))
        changed["lanes"]["fable"]["subjectSha256"] = ZERO
        path = self.base / "subject-digest.json"
        path.write_text(json.dumps(changed, sort_keys=True), encoding="utf-8")
        with mock.patch.object(supervisor, "BINDINGS", path), \
                mock.patch.object(supervisor, "EXPECTED_BINDINGS_SHA256",
                                  supervisor.sha256_file(path)):
            with self.assertRaisesRegex(ControlError, "LANE_BINDINGS_INVALID"):
                supervisor.load_contracts()

    def test_17_binding_is_revalidated_immediately_before_child_boundary(self):
        self.write_demand(demand(hasWork=True))
        profile, bindings = supervisor.load_contracts()
        env = {supervisor.FAKE_ENV: "1", supervisor.TEST_STATE_ROOT_ENV: str(self.state)}
        for mutation in ("model", "role", "subject"):
            changed = json.loads(json.dumps(bindings))
            changed["lanes"]["fable"][mutation] = "redirected"
            with self.subTest(mutation=mutation), mock.patch.dict(os.environ, env, clear=False), \
                    mock.patch.object(supervisor, "load_contracts",
                                      side_effect=[(profile, bindings), (profile, changed)]), \
                    mock.patch.object(supervisor.subprocess, "Popen",
                                      side_effect=AssertionError("provider invoked")):
                with self.assertRaisesRegex(ControlError, "LAUNCH_BINDING_CHANGED"):
                    supervisor.run_test_fake(self.state, self.demand, HERE / "fake_provider.py", 0)

    def test_18_exact_r1_red_r2_green_discriminators(self):
        expected_blob = "49289959fd7fa526acbb0123a6a55584ad2ce089"
        actual_blob = subprocess.run(
            ["git", "rev-parse", supervisor.R1_COMMIT + ":tools/provider_control/mlv_lane_supervisor.py"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(actual_blob, expected_blob)
        archive = subprocess.run(
            ["git", "archive", "--format=tar", supervisor.R1_COMMIT, "tools/provider_control"],
            capture_output=True, check=True,
        ).stdout
        extracted = self.base / "r1"
        extracted.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            members = bundle.getmembers()
            self.assertTrue(members)
            self.assertTrue(all(not Path(item.name).is_absolute() and ".." not in Path(item.name).parts
                                for item in members))
            bundle.extractall(extracted, members=members, filter="data")
        script = (
            "import json,sys,tempfile;from pathlib import Path;"
            "root=Path(sys.argv[1]);sys.path.insert(0,str(root/'tools/provider_control'));"
            "import mlv_lane_supervisor as s;"
            "a=root/'state-a';b=root/'state-b';"
            "l1=s.quota_lock(a);l1.__enter__();l2=s.quota_lock(b);l2.__enter__();"
            "l2.__exit__(None,None,None);l1.__exit__(None,None,None);"
            "d=root/'d.json';"
            "base={'schema':'mlv-provider-demand/v1','project':'mlv-app','hasWork':False,"
            "'lane':'fable','priority':'PRODUCT_WORK','estimateFraction':0.1,"
            "'availableFraction':0.9,'turns':4,'contextTokens':32000,"
            "'capsuleSha256':'" + ZERO + "','checkpointSha256':'" + ZERO + "',"
            "'cacheAffinitySha256':'" + ZERO + "'};"
            "d.write_text(json.dumps(base));x=s.tick(a,d);base['contextTokens']=32001;"
            "d.write_text(json.dumps(base));y=s.tick(a,d);"
            "print(json.dumps({'alternateRootsLocked':True,'first':x['status'],'changed':y['status']}))"
        )
        red = subprocess.run([sys.executable, "-c", script, str(extracted)],
                             capture_output=True, text=True, check=True, timeout=20)
        evidence = json.loads(red.stdout)
        self.assertTrue(evidence["alternateRootsLocked"])
        self.assertEqual(evidence["first"], "IDLE_SKIPPED")
        self.assertEqual(evidence["changed"], "IDLE_SKIPPED")
        # R2 greens for the exact red behaviors are asserted by tests 14 and 15.


if __name__ == "__main__":
    unittest.main(verbosity=2)
