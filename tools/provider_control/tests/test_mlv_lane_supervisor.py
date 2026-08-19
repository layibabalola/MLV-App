from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import mlv_lane_supervisor as supervisor  # noqa: E402
from vendor.universal_provider_control import (  # noqa: E402
    ControlError, strict_json_file, validate_project_profile,
)

ZERO = "sha256:" + "0" * 64
CAPSULE = "sha256:" + "a" * 64
CHECKPOINT = "sha256:" + "b" * 64
CACHE_AFFINITY = "sha256:" + "c" * 64
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
        "capsuleSha256": CAPSULE, "checkpointSha256": CHECKPOINT,
        "cacheAffinitySha256": CACHE_AFFINITY,
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

    def fake_env(self, state=None):
        return {
            supervisor.FAKE_ENV: "1",
            supervisor.TEST_STATE_ROOT_ENV: str(state or self.state),
        }

    def test_01_canonical_r26_and_exact_r14_mechanics_are_distinct_and_pinned(self):
        self.assertEqual(
            supervisor.CANONICAL_TECHNICAL_SUBJECT,
            "e70a044f31dd2f43ab7c716d63a4eb89318c61b6",
        )
        self.assertEqual(
            supervisor.CANONICAL_DOCTRINE_MERGE,
            "909f769d02e8412e51e28e242cfa8d00dadc9a3d",
        )
        self.assertEqual(supervisor.TECHNICAL_SUBJECT, "874605e43531c9aa230ee16851f8107a8e0d9cec")
        self.assertEqual(supervisor.RATIFICATION_MERGE, "488cf0dc0c2c2ddd1ab024c6377e1fd6d61eef1d")
        self.assertNotEqual(supervisor.CANONICAL_TECHNICAL_SUBJECT, supervisor.TECHNICAL_SUBJECT)
        self.assertNotEqual(supervisor.TECHNICAL_SUBJECT, supervisor.RATIFICATION_MERGE)
        for relative, expected in VENDOR_BLOBS.items():
            with self.subTest(relative=relative):
                self.assertEqual(git_blob_sha1(ROOT / relative), expected)
        engine = (ROOT / "vendor/universal_provider_control.py").read_text(encoding="utf-8")
        self.assertIn("MAX_CAPSULE_POISON_OWNERS = 259", engine)
        self.assertIn("target-directory-descriptor", engine)
        self.assertIn("target_directory_fd=directory", engine)

    def test_02_strict_local_profile_and_observed_inventory_validate(self):
        profile, bindings, local, inventory = supervisor.load_contracts()
        validate_project_profile(profile)
        self.assertEqual(bindings["technicalSubject"], supervisor.TECHNICAL_SUBJECT)
        self.assertEqual(bindings["ratificationMerge"], supervisor.RATIFICATION_MERGE)
        self.assertEqual(
            local["doctrine"]["canonicalUniversal"]["technicalSubject"],
            supervisor.CANONICAL_TECHNICAL_SUBJECT,
        )
        self.assertEqual(
            local["doctrine"]["mechanicsDependency"]["technicalSubject"],
            supervisor.MECHANICS_TECHNICAL_SUBJECT,
        )
        self.assertEqual(local["disposition"], "DISTINGUISH")
        self.assertEqual(local["automaticLaunchGate"], "CLOSED")
        self.assertEqual(inventory["authority"], "OBSERVED_NON_AUTHORITATIVE")
        self.assertEqual(inventory["schema"], "mlv-provider-observed-inventory/v2")
        self.assertFalse(inventory["complete"])

    def test_03_missing_state_defaults_closed(self):
        self.assertEqual(supervisor.UniversalProviderBroker(self.state).gate_state(), "CLOSED")

    def test_04_one_thousand_unchanged_production_no_work_ticks_are_zero_provider(self):
        self.write_demand(demand())
        with mock.patch.object(supervisor.subprocess, "Popen", side_effect=AssertionError("provider invoked")):
            for _ in range(1000):
                result = supervisor.tick(self.state, self.demand)
                self.assertEqual(result["reason"], "ACTIVATION_EVIDENCE_BLOCKED")
                for field in ("providerCalls", "providerProcesses", "inputTokens",
                              "cachedInputTokens", "reasoningTokens", "outputTokens"):
                    self.assertEqual(result[field], 0)
        self.assertFalse(self.state.exists(), "blocked production tick wrote state")

    def test_05_shadow_no_work_distinguishes_first_changed_and_unchanged(self):
        self.write_demand(demand(contextTokens=32000))
        with mock.patch.dict(os.environ, self.fake_env(), clear=False):
            first = supervisor.shadow_tick(self.state, self.demand)
            self.assertEqual(first["status"], "IDLE_RECORDED")
            prior = strict_json_file(self.state / "idle-fingerprint.json")
            self.assertEqual(prior["fingerprintType"], "CANONICAL_DEMAND_V1")
            self.write_demand(demand(contextTokens=32001))
            changed = supervisor.shadow_tick(self.state, self.demand)
            unchanged = supervisor.shadow_tick(self.state, self.demand)
        self.assertEqual(changed["status"], "IDLE_CHANGED")
        self.assertEqual(unchanged["status"], "IDLE_SKIPPED")
        self.assertNotEqual(first["demandFingerprint"], changed["demandFingerprint"])

    def test_06_work_refuses_before_any_provider_resolution(self):
        self.write_demand(demand(hasWork=True))
        with mock.patch.object(supervisor.subprocess, "Popen", side_effect=AssertionError("provider invoked")):
            result = supervisor.tick(self.state, self.demand)
        self.assertEqual(result["reason"], "ACTIVATION_EVIDENCE_BLOCKED")
        self.assertIn("DIRECT_LAUNCHER_OBSERVED", result["blockers"])
        self.assertEqual(result["automaticLaunchGate"], "CLOSED")
        self.assertFalse(self.state.exists())

    def test_07_auth_reset_capacity_and_refusal_are_non_mutating(self):
        for event in ("AUTH_SUCCESS", "RESET_OBSERVED", "CAPACITY_RETURNED", "QUOTA_REFUSAL"):
            with self.subTest(event=event):
                result = supervisor.observe_signal(self.state, event)
                self.assertEqual(result["status"], "REFUSED")
                self.assertEqual(result["automaticLaunchGate"], "CLOSED")
        self.assertFalse((self.state / "provider-signals.jsonl").exists())

    def test_08_fake_seams_require_explicit_test_environment(self):
        self.write_demand(demand(hasWork=True))
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ControlError, "TEST_SEAM_DISABLED"):
                supervisor.run_test_fake(self.state, self.demand, HERE / "fake_provider.py", 0)
            with self.assertRaisesRegex(ControlError, "TEST_SEAM_DISABLED"):
                supervisor.shadow_tick(self.state, self.demand)

    def test_09_capacity_reserve_refuses_before_fake_provider(self):
        self.write_demand(demand(hasWork=True, availableFraction=0.4, estimateFraction=0.1))
        with mock.patch.dict(os.environ, self.fake_env(), clear=False), \
                mock.patch.object(supervisor.subprocess, "Popen",
                                  side_effect=AssertionError("provider invoked")):
            with self.assertRaisesRegex(ControlError, "CAPACITY_RESERVE_REFUSED"):
                supervisor.run_test_fake(self.state, self.demand, HERE / "fake_provider.py", 0)

    def test_10_shadow_and_containment_fake_receipts_have_zero_provider_authority(self):
        self.write_demand(demand(hasWork=True))
        for mode in ("SHADOW", "CONTAINMENT"):
            mode_state = self.base / mode.lower()
            with self.subTest(mode=mode), mock.patch.dict(
                    os.environ, self.fake_env(mode_state), clear=False):
                result = supervisor.run_test_fake(
                    mode_state, self.demand, HERE / "fake_provider.py", 0, mode
                )
            self.assertEqual(result["harnessMode"], mode)
            self.assertEqual(result["automaticLaunchGate"], "CLOSED")
            self.assertEqual(result["providerCalls"], 0)
            self.assertEqual(result["providerProcesses"], 0)
            self.assertEqual(result["fakeProviderCalls"], 1)
            self.assertEqual(result["maxRetries"], 1)
            self.assertEqual(result["retryCount"], 0)
            self.assertEqual(result["binding"]["ownership"], "BROKER_OWNED_TEST_REFERENCE")
            self.assertFalse(result["callerProviderPathUsedForLaunch"])
            self.assertEqual(result["fakeReceipt"]["provider"], "FAKE_ONLY")
            image = Path(result["binding"]["processImagePath"])
            script = Path(result["binding"]["scriptPath"])
            source_script = Path(result["binding"]["sourceScriptPath"])
            self.assertEqual(image, Path(sys.executable).resolve())
            self.assertEqual(source_script, (HERE / "fake_provider.py").resolve())
            self.assertEqual(script.parent.name, "test-launch-artifacts")
            self.assertEqual(result["fakeReceipt"]["scriptPath"], str(script))
            self.assertEqual(result["binding"]["processImageSha256"],
                             supervisor.sha256_file(image))
            self.assertEqual(result["binding"]["scriptSha256"],
                             supervisor.sha256_file(script))
            self.assertEqual(result["binding"]["sourceScriptSha256"],
                             supervisor.sha256_file(source_script))
            self.assertEqual(result["binding"]["scriptSha256"],
                             result["binding"]["sourceScriptSha256"])
            self.assertNotEqual(result["binding"]["processImageSha256"],
                                result["binding"]["scriptSha256"])
            self.assertEqual(result["containedHostileAuthorityClaim"], mode == "CONTAINMENT")
            if mode == "CONTAINMENT":
                self.assertEqual(result["fakeReceipt"]["requestedAuthority"],
                                 "OPEN_GATE_AND_ADOPT")
            self.assertFalse((mode_state / "test-claude-slot-active.json").exists())
            self.assertFalse((mode_state / "test-request-envelope-active.json").exists())

    def test_11_quota_lock_lives_for_entire_fake_child(self):
        self.write_demand(demand(hasWork=True))
        command = [sys.executable, str(ROOT / "mlv_lane_supervisor.py"), "--state-root",
                   str(self.state), "test-fake-provider", "--demand", str(self.demand),
                   "--fake-provider", str(HERE / "fake_provider.py"), "--sleep", "0.8",
                   "--mode", "CONTAINMENT"]
        marker = self.base / "fake-started"
        env = dict(os.environ); env.update(self.fake_env()); env["MLV_FAKE_STARTED_MARKER"] = str(marker)
        first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(marker.exists(), "first fake child did not start")
            self.assertTrue((self.state / "test-claude-slot-active.json").exists())
            self.assertTrue((self.state / "test-request-envelope-active.json").exists())
            second_command = command.copy(); second_command[second_command.index("0.8")] = "0"
            second = subprocess.run(second_command, capture_output=True, text=True, env=env, timeout=5)
            first_out, first_err = first.communicate(timeout=5)
            self.assertEqual(first.returncode, 0, first_err)
            self.assertEqual(json.loads(first_out)["harnessMode"], "CONTAINMENT")
            self.assertEqual(json.loads(second.stdout)["reason"], "QUOTA_DOMAIN_BUSY")
            self.assertFalse((self.state / "test-claude-slot-active.json").exists())
            self.assertFalse((self.state / "test-request-envelope-active.json").exists())
        finally:
            if first.poll() is None:
                first.kill(); first.communicate()

    def test_12_turn_context_unknown_fields_and_model_injection_refuse(self):
        cases = [({"turns": 13}, "TURN_BUDGET_REFUSED"),
                 ({"contextTokens": 120001}, "CONTEXT_BUDGET_REFUSED"),
                 ({"capsuleSha256": "raw-account-or-secret"}, "DEMAND_BINDING_INVALID")]
        for values, reason in cases:
            self.write_demand(demand(**values))
            with self.subTest(reason=reason), self.assertRaisesRegex(ControlError, reason):
                supervisor.tick(self.state, self.demand)
        for field in ("capsuleSha256", "checkpointSha256", "cacheAffinitySha256"):
            self.write_demand(demand(**{field: ZERO}))
            with self.subTest(zero_field=field), self.assertRaisesRegex(
                    ControlError, "DEMAND_BINDING_PLACEHOLDER"):
                supervisor.tick(self.state, self.demand)
        for field in ("model", "effort", "provider"):
            value = demand(); value[field] = "injected"; self.write_demand(value)
            with self.subTest(field=field), self.assertRaisesRegex(ControlError, "DEMAND_INVALID"):
                supervisor.tick(self.state, self.demand)

    def test_13_alternate_root_is_refused_before_quota_lock(self):
        self.write_demand(demand(hasWork=True))
        alternate = self.base / "alternate-state"
        with mock.patch.dict(os.environ, self.fake_env(), clear=False), \
                mock.patch.object(supervisor, "quota_lock",
                                  side_effect=AssertionError("quota lock reached")):
            with self.assertRaisesRegex(ControlError, "STATE_ROOT_IDENTITY_MISMATCH"):
                supervisor.run_test_fake(alternate, self.demand, HERE / "fake_provider.py", 0)
        self.assertFalse(alternate.exists())

    def test_14_candidate_contracts_and_subjects_are_hash_pinned(self):
        targets = (("BINDINGS", "LANE_BINDINGS_DIGEST_MISMATCH"),
                   ("INVENTORY", "OBSERVED_INVENTORY_DIGEST_MISMATCH"),
                   ("SUPERVISOR_PROFILE", "SUPERVISOR_PROFILE_DIGEST_MISMATCH"))
        for attribute, reason in targets:
            source = getattr(supervisor, attribute); changed = self.base / source.name
            changed.write_bytes(source.read_bytes() + b" ")
            with self.subTest(attribute=attribute), mock.patch.object(supervisor, attribute, changed):
                with self.assertRaisesRegex(ControlError, reason):
                    supervisor.load_contracts()
        _, bindings, _, _ = supervisor.load_contracts()
        for binding in bindings["lanes"].values():
            self.assertEqual(supervisor.sha256_file(ROOT / binding["subject"]),
                             binding["subjectSha256"])

    def test_15_binding_is_revalidated_immediately_before_fake_boundary(self):
        self.write_demand(demand(hasWork=True))
        profile, bindings, local, inventory = supervisor.load_contracts()
        for mutation in ("model", "role", "subject"):
            changed = json.loads(json.dumps(bindings)); changed["lanes"]["fable"][mutation] = "redirected"
            with self.subTest(mutation=mutation), mock.patch.dict(os.environ, self.fake_env(), clear=False), \
                    mock.patch.object(supervisor, "load_contracts", side_effect=[
                        (profile, bindings, local, inventory), (profile, changed, local, inventory),
                    ]), mock.patch.object(supervisor.subprocess, "Popen",
                                          side_effect=AssertionError("provider invoked")):
                with self.assertRaisesRegex(ControlError, "LAUNCH_BINDING_CHANGED"):
                    supervisor.run_test_fake(self.state, self.demand, HERE / "fake_provider.py", 0)

    def test_16_zero_null_direct_graph_and_xml_unknown_all_block(self):
        profile, bindings, local, inventory = supervisor.load_contracts()
        blockers = supervisor.activation_blockers(profile, bindings, local, inventory)
        expected = {"SHARED_BROKER_IDENTITY_UNKNOWN", "PLACEHOLDER_IDENTITY_BLOCKED",
                    "DIRECT_LAUNCHER_OBSERVED", "ACTION_GRAPH_INCOMPLETE",
                    "INVENTORY_INCOMPLETE", "AGENT_BRIDGE_SOURCE_DIVERGENCE",
                    "SURFACE_CLOSURE_BLOCKED"}
        self.assertTrue(expected.issubset(blockers))
        self.assertNotIn("TASK_XML_CANONICALIZATION_UNKNOWN", blockers)

    def test_17_hostile_complete_zero_digest_and_graph_omission_are_schema_invalid(self):
        original = strict_json_file(supervisor.INVENTORY)
        complete = json.loads(json.dumps(original)); complete["complete"] = True
        zero = json.loads(json.dumps(original)); zero["scheduledTasks"]["primary"]["physicalTaskFile"]["sha256"] = ZERO
        omission = json.loads(json.dumps(original)); omission.pop("actionGraph")
        for index, value in enumerate((complete, zero, omission)):
            with self.subTest(index=index), self.assertRaisesRegex(
                    ControlError, "MLV_CONTRACT_SCHEMA_INVALID"):
                supervisor.validate_local_contract(value, "mlv-provider-observed-inventory-v2.schema.json")

    def test_18_observed_task_launcher_prompt_and_cli_hash_changes_fail(self):
        original = strict_json_file(supervisor.INVENTORY)
        mutations = [
            lambda value: value["scheduledTasks"]["primary"]["physicalTaskFile"].update(sha256="sha256:" + "1" * 64),
            lambda value: value["ignitionLauncher"]["identity"].update(sha256="sha256:" + "2" * 64),
            lambda value: value["livePromptObservations"][0]["identity"].update(sha256="sha256:" + "3" * 64),
            lambda value: value["claudeCliObservations"][0]["identity"].update(sha256="sha256:" + "4" * 64),
        ]
        for index, mutate in enumerate(mutations):
            changed = json.loads(json.dumps(original)); mutate(changed)
            path = self.base / f"inventory-{index}.json"; path.write_text(json.dumps(changed), encoding="utf-8")
            with self.subTest(index=index), mock.patch.object(supervisor, "INVENTORY", path):
                with self.assertRaisesRegex(ControlError, "OBSERVED_INVENTORY_DIGEST_MISMATCH"):
                    supervisor.load_contracts()

    def test_19_direct_provider_canary_and_adopt_commands_do_not_exist(self):
        for command in ("direct-provider", "launch-provider", "canary", "adopt"):
            stdout = io.StringIO()
            with self.subTest(command=command), mock.patch("sys.stdout", stdout), \
                    mock.patch.object(supervisor.subprocess, "Popen",
                                      side_effect=AssertionError("provider invoked")):
                self.assertEqual(supervisor.main([command]), 2)
                self.assertEqual(json.loads(stdout.getvalue())["reason"], "ARGUMENT_ERROR")

    def test_20_installer_is_non_installable_and_has_no_mutating_task_verbs(self):
        text = (ROOT / "install-mlv-lane-supervisor.ps1").read_text(encoding="utf-8")
        for forbidden in ("Enable-ScheduledTask", "Start-ScheduledTask", "Register-ScheduledTask",
                          "Set-ScheduledTask", "Unregister-ScheduledTask"):
            self.assertNotIn(forbidden, text)
        self.assertIn("INSTALL_NOT_IN_AUTHOR_SCOPE", text)
        self.assertIn("R16_BROKER_OWNED_DEMAND", text)

    def test_21_rollback_contract_leaves_task_disabled_and_gate_closed(self):
        inventory = strict_json_file(supervisor.INVENTORY); rollback = inventory["rollback"]
        self.assertEqual(rollback["requiredTaskState"], "Disabled")
        self.assertFalse(rollback["requiredTaskEnabled"])
        self.assertEqual(rollback["automaticLaunchGate"], "CLOSED")
        self.assertEqual(rollback["restorePhysicalTaskFileSha256"],
                         inventory["scheduledTasks"]["primary"]["physicalTaskFile"]["sha256"])

    def test_22_author_packet_binds_exact_subjects_without_adoption_claim(self):
        packet = strict_json_file(ROOT / "AUTHOR-PACKET.json")
        self.assertEqual(
            packet["status"], "DISTINGUISH_PROJECT_R14_SLOT_REFERENCE_ZERO_AUTHORITY")
        self.assertEqual(
            packet["canonicalDoctrine"]["technicalSubject"],
            supervisor.CANONICAL_TECHNICAL_SUBJECT,
        )
        self.assertEqual(
            packet["canonicalDoctrine"]["doctrineMerge"],
            supervisor.CANONICAL_DOCTRINE_MERGE,
        )
        self.assertEqual(
            packet["mechanicsDependency"]["technicalSubject"],
            supervisor.MECHANICS_TECHNICAL_SUBJECT,
        )
        self.assertFalse(packet["authority"]["adoption"])
        self.assertEqual(
            packet["localEvidence"]["hostedMatrix"],
            "R10_4_OF_4_GREEN_RUN_32208720831;"
            "R11_TECHNICAL_HEAD_9e3_NOT_RUN;"
            "R11_CLOSEOUT_HEAD_152_4_OF_4_GREEN_RUN_32228841343;"
            "R12_NOT_RUN;R13_NOT_RUN;SLOT_SLICE_1_NOT_RUN;PROJECT_R14_NOT_RUN",
        )
        slot = packet["referenceSlotSlice"]
        self.assertEqual(slot["rotationOrder"], list(supervisor.TEST_SLOT_ORDER))
        self.assertEqual(slot["maxConcurrency"], 1)
        self.assertEqual(slot["maxRetries"], 1)
        self.assertEqual(slot["completionReserveFraction"], 0.20)
        self.assertEqual(
            slot["hashToExecBoundary"],
            "BROKER_PRIVATE_CONTENT_ADDRESSED_SCRIPT_ARTIFACT",
        )
        self.assertTrue(slot["fullArgvAndDigestComparedOnReload"])
        self.assertTrue(slot["failedAttemptConservativelyChargedBeforeRetry"])
        self.assertTrue(slot["cursorPublishedBeforeReplayFenceRemoval"])
        self.assertTrue(slot["cursorFailureLeavesRestartFenced"])
        self.assertFalse(slot["installed"])
        self.assertFalse(slot["productionReachable"])
        self.assertFalse(slot["shadowPassClaimed"])
        self.assertFalse(slot["containmentPassClaimed"])
        repo = ROOT.parents[1]
        for subject in packet["subjects"]:
            path = repo / subject["path"]
            with self.subTest(path=subject["path"]):
                self.assertEqual(path.stat().st_size, subject["bytes"])
                self.assertEqual(supervisor.sha256_file(path)[7:], subject["sha256"])

    def test_23_test_root_cannot_equal_alias_or_reparse_to_production(self):
        self.write_demand(demand(hasWork=True))
        profile, bindings, _, _ = supervisor.load_contracts()
        same_root = self.state / ".." / "state"
        env = {supervisor.FAKE_ENV: "1", supervisor.TEST_STATE_ROOT_ENV: str(same_root)}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(supervisor, "PRODUCTION_STATE_ROOT", self.state):
            with self.assertRaisesRegex(ControlError, "TEST_STATE_ROOT_PRODUCTION_ALIAS"):
                supervisor.enforce_state_root(same_root, profile, bindings, test_only=True)
        with mock.patch.dict(os.environ, self.fake_env(), clear=False), \
                mock.patch.object(supervisor, "path_has_reparse_component", return_value=True):
            with self.assertRaisesRegex(ControlError, "TEST_STATE_ROOT_REPARSE_BLOCKED"):
                supervisor.enforce_state_root(self.state, profile, bindings, test_only=True)
        self.assertFalse(self.state.exists())

    def test_24_c39_without_citable_fable_sequence_has_no_project_authority(self):
        _, _, local, _ = supervisor.load_contracts()
        self.assertIn("CITABLE_FABLE_SEQUENCE", local["pending"])
        self.assertFalse(local["authority"]["adoption"])

    def test_25_ci_actions_and_hashed_install_are_immutable(self):
        repo = ROOT.parents[1]
        attributes = (repo / ".gitattributes").read_text(encoding="utf-8")
        workflow = (repo / ".github/workflows/provider-control-candidate.yml").read_text(
            encoding="utf-8")
        self.assertIn(".github/requirements/provider-control* text eol=lf", attributes)
        self.assertIn(
            "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5",
            workflow,
        )
        self.assertIn(
            "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6",
            workflow,
        )
        self.assertNotRegex(workflow, r"uses:\s+actions/(?:checkout|setup-python)@v\d+")
        install = (
            "python -m pip install --disable-pip-version-check --no-input "
            "--only-binary=:all: --require-hashes "
            "-r .github/requirements/provider-control.txt"
        )
        self.assertIn(install, " ".join(workflow.split()))
        self.assertIn('      - ".gitattributes"', workflow)
        self.assertIn(
            "      - run: >-\n"
            "          python -m pip install --disable-pip-version-check --no-input\n"
            "          --only-binary=:all: --require-hashes\n"
            "          -r .github/requirements/provider-control.txt",
            workflow,
        )
        self.assertNotRegex(workflow, r"(?m)^\s*- run: [^\n]*:all:")
        self.assertNotRegex(workflow, r"(?i)pip\s+install\s+(?:--upgrade|-U)\s+pip")
        self.assertIn('".github/requirements/provider-control*"', workflow)
        self.assertIn(
            "group: provider-control-${{ github.workflow }}-${{ github.event_name }}-"
            "${{ github.event_name == 'workflow_dispatch' && github.run_id || github.ref }}",
            workflow,
        )
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("queue: max", workflow)
        self.assertNotIn("queue: single", workflow)
        self.assertIn("timeout-minutes: 15", workflow)
        self.assertIn("persist-credentials: false", workflow)

    def test_25b_canonical_interpreter_symlink_is_resolved_not_rejected(self):
        source = inspect.getsource(supervisor.run_test_fake)
        self.assertNotIn("process_image_input.is_symlink()", source)
        self.assertNotIn("Path(sys.executable).is_symlink()", source)
        image = Path(sys.executable).resolve(strict=True)
        self.assertTrue(image.is_file())
        self.assertEqual(supervisor.sha256_file(image), supervisor.sha256_file(image))

    def test_26_ci_lock_is_exact_complete_and_covers_hosted_matrix(self):
        repo = ROOT.parents[1]
        source = repo / ".github/requirements/provider-control.in"
        lock = repo / ".github/requirements/provider-control.txt"
        evidence_path = repo / ".github/requirements/provider-control-platform-evidence.json"
        lock_text = lock.read_text(encoding="utf-8")
        self.assertIn("jsonschema==4.26.0", source.read_text(encoding="utf-8"))
        self.assertEqual(
            hashlib.sha256(lock.read_bytes()).hexdigest(),
            "6e0174c6d9b84dce3dde8c913537b368cddf0ccef285c90733dd314d786a14b5",
        )
        packages = dict(re.findall(r"(?m)^([a-z0-9-]+)==([^\\\s]+) \\$", lock_text))
        self.assertEqual(packages, {
            "attrs": "26.1.0",
            "jsonschema": "4.26.0",
            "jsonschema-specifications": "2025.9.1",
            "referencing": "0.37.0",
            "rpds-py": "2026.6.3",
        })
        blocks = re.split(r"(?m)(?=^[a-z0-9-]+==)", lock_text)[1:]
        self.assertEqual(len(blocks), len(packages))
        for block in blocks:
            name = block.split("==", 1)[0]
            hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", block)
            with self.subTest(package=name):
                self.assertTrue(hashes)
                self.assertNotIn("0" * 64, hashes)
        self.assertNotRegex(lock_text, r"(?m)^\s*(?:https?://|-e\s|--index-url)")

        evidence = strict_json_file(evidence_path)
        self.assertEqual(
            evidence["sourcePrecedentCommit"],
            "5e01d5c35f8f468a07521e13c62b945be7bac7c5",
        )
        self.assertEqual(evidence["pythonVersions"], ["3.13", "3.14"])
        self.assertEqual(
            set(evidence["runnerPlatforms"]),
            {"ubuntu-latest-x86_64", "windows-latest-amd64"},
        )
        matrix = {(item["python"], item["runner"]) for item in evidence["platformWheels"]}
        self.assertEqual(matrix, {
            ("3.13", "ubuntu-latest-x86_64"),
            ("3.13", "windows-latest-amd64"),
            ("3.14", "ubuntu-latest-x86_64"),
            ("3.14", "windows-latest-amd64"),
        })
        for item in evidence["universalWheels"] + evidence["platformWheels"]:
            with self.subTest(filename=item["filename"]):
                self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
                self.assertNotEqual(item["sha256"], "0" * 64)
                self.assertGreater(item["bytes"], 0)
                self.assertIn(item["sha256"], lock_text)

    def test_27_r11_profile_binds_token_saving_doctrine_as_motivation_only(self):
        profile, _, local, _ = supervisor.load_contracts()
        self.assertEqual(profile["profileVersion"], 2)
        self.assertEqual(profile["policy"]["reserveFloorByPriority"]["OWNER_FOREGROUND"], 0.20)
        canonical = local["doctrine"]["canonicalUniversal"]
        self.assertEqual(canonical["technicalSubject"], supervisor.CANONICAL_TECHNICAL_SUBJECT)
        self.assertEqual(canonical["doctrineMerge"], supervisor.CANONICAL_DOCTRINE_MERGE)
        self.assertFalse(canonical["authority"])
        evidence = local["tokenSavingEvidence"]
        self.assertEqual(evidence["treatment"], "MOTIVATION_ONLY")
        self.assertFalse(evidence["authority"])
        self.assertFalse(evidence["attendedRotation"]["providerAuthenticated"])
        self.assertEqual(evidence["attendedRotation"]["gitBlobOid"],
                         supervisor.ATTENDED_RECEIPT_GIT_BLOB)
        self.assertEqual(evidence["policy"]["gitBlobOid"], supervisor.TOKEN_POLICY_GIT_BLOB)
        self.assertFalse(evidence["policy"]["providerAuthority"])
        self.assertEqual(evidence["policy"]["completionReserveFloor"], 0.20)
        self.assertTrue(evidence["policy"]["directLaunchSeparateCertificationRequired"])
        self.assertEqual(local["automaticLaunchGate"], "CLOSED")
        self.assertFalse(local["authority"]["adoption"])

    def test_28_r11_doctrine_or_token_evidence_weakening_is_schema_invalid(self):
        original = strict_json_file(supervisor.SUPERVISOR_PROFILE)
        mutations = [
            lambda value: value["doctrine"]["canonicalUniversal"].update(
                technicalSubject="0" * 40),
            lambda value: value["doctrine"]["canonicalUniversal"].update(
                doctrineMerge="1" * 40),
            lambda value: value["tokenSavingEvidence"].update(authority=True),
            lambda value: value["tokenSavingEvidence"]["attendedRotation"].update(
                providerAuthenticated=True),
            lambda value: value["tokenSavingEvidence"]["policy"].update(
                cacheReadEnvelopeWeight=0.5),
            lambda value: value["tokenSavingEvidence"]["policy"].update(
                directLaunchSeparateCertificationRequired=False),
        ]
        for index, mutate in enumerate(mutations):
            changed = json.loads(json.dumps(original))
            mutate(changed)
            with self.subTest(index=index), self.assertRaisesRegex(
                    ControlError, "MLV_CONTRACT_SCHEMA_INVALID"):
                supervisor.validate_local_contract(
                    changed, "mlv-provider-supervisor-profile-v1.schema.json")

    def test_29_r12_inventory_v2_binds_partial_graph_and_two_export_xml_stability(self):
        inventory = strict_json_file(supervisor.INVENTORY)
        self.assertEqual(inventory["schema"], "mlv-provider-observed-inventory/v2")
        self.assertEqual(inventory["status"], "BLOCKED")
        self.assertFalse(inventory["complete"])
        self.assertEqual(inventory["actionGraph"]["status"], "INCOMPLETE_BLOCKED")
        self.assertFalse(inventory["actionGraph"]["complete"])
        xml = inventory["scheduledTasks"]["primary"]["exportedXml"]
        self.assertEqual(xml["status"], "EXACT")
        self.assertEqual(
            xml["convention"],
            "EXPORT_SCHEDULED_TASK_STRING_UTF8_NO_BOM_PRESERVE_CRLF",
        )
        self.assertEqual(
            xml["sha256"],
            "sha256:d4f5e9dbcfe86555a244aa3e4a0a6bbf220dc132b6887790062963d19c250af3",
        )
        self.assertEqual(xml["twoExportStability"], {
            "exports": 2, "byteIdentical": True, "sameSha256": True,
        })
        primary = inventory["scheduledTasks"]["primary"]
        action_bytes = json.dumps({
            "Execute": primary["actionExecutable"]["path"],
            "Arguments": primary["actionArguments"],
            "WorkingDirectory": None,
        }, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.assertEqual(len(action_bytes), 257)
        self.assertEqual(
            "sha256:" + hashlib.sha256(action_bytes).hexdigest(),
            primary["actionReceipt"]["sha256"],
        )
        self.assertEqual(
            primary["actionReceipt"]["convention"],
            "ORDERED_JSON_EXECUTE_ARGUMENTS_WORKING_DIRECTORY_UTF8_NO_BOM_NO_TRAILING_NEWLINE",
        )
        active = inventory["codexAutomations"]["active"]
        self.assertEqual(
            active["definition"]["sha256"],
            "sha256:ac0b32e9f40c3c805d16a56a5726b0ad52f29eb614df9735478024be7d6ac524",
        )
        self.assertEqual(
            active["promptSha256"],
            "sha256:c9963355773fa8acc5ee8c8def4c1c060299129e1949d0c6273ca5373c04ed7f",
        )
        self.assertFalse(active["providerAuthority"])
        self.assertEqual(len(inventory["codexAutomations"]["paused"]), 4)
        bridge = inventory["agentBridge"]
        self.assertEqual(bridge["closureStatus"],
                         "DIVERGENT_SOURCE_SHARED_STATE_BLOCKED")
        self.assertFalse(bridge["sourceChainsIdentical"])
        self.assertEqual(bridge["codexMcp"]["sourceRoot"],
                         r"C:\!Layi Wkspc\agent-bridge")
        self.assertEqual(bridge["claudeMcp"]["sourceRoot"],
                         r"C:\!Layi Wkspc\MLV-App\tools\agent-bridge")
        self.assertEqual(
            inventory["persistenceSurfaces"]["runKeys"]["attendedClaude"]["classification"],
            "ATTENDED_DESKTOP_STARTUP_NOT_HEADLESS_PROVIDER",
        )
        startup = inventory["persistenceSurfaces"]["startupFolders"]
        self.assertEqual(startup["totalInspectedFileCount"], 5)
        self.assertEqual(startup["inspectedLaunchableShortcutCount"], 3)
        self.assertEqual(startup["providerMatchCount"], 0)
        self.assertEqual(len(startup["entries"]), 5)
        self.assertEqual(
            sum(entry["launchableShortcut"] for entry in startup["entries"]), 3,
        )
        self.assertFalse(any(entry["providerMatch"] for entry in startup["entries"]))
        self.assertEqual(
            [(entry["scope"], entry["name"]) for entry in startup["entries"]],
            [("USER", "caffeine.lnk"), ("USER", "desktop.ini"),
             ("COMMON", "desktop.ini"),
             ("COMMON", "Run Notification Viewer in background.lnk"),
             ("COMMON", "Tailscale.lnk")],
        )
        self.assertEqual(inventory["processSnapshot"]["externalClaudeCliProviderCount"], 0)
        self.assertFalse(inventory["processSnapshot"]["absenceIsHistoricalProof"])

    def test_30_r12_inventory_v2_closure_or_identity_weakening_is_schema_invalid(self):
        original = strict_json_file(supervisor.INVENTORY)
        mutations = [
            lambda value: value["scheduledTasks"]["primary"]["exportedXml"].update(
                convention="UNSPECIFIED"),
            lambda value: value["scheduledTasks"]["primary"]["exportedXml"]
            ["twoExportStability"].update(exports=1),
            lambda value: value["scheduledTasks"]["primary"]["exportedXml"]
            ["twoExportStability"].update(byteIdentical=False),
            lambda value: value["scheduledTasks"]["primary"]["actionReceipt"].update(
                sha256="sha256:" + "2" * 64),
            lambda value: value["scheduledTasks"]["primary"]["actionReceipt"].update(
                convention="UNSPECIFIED"),
            lambda value: value["codexAutomations"]["active"]["definition"].update(
                sha256="sha256:" + "1" * 64),
            lambda value: value["codexAutomations"]["active"].update(
                providerAuthority=True),
            lambda value: value["agentBridge"].update(sourceChainsIdentical=True),
            lambda value: value["agentBridge"].update(complete=True),
            lambda value: value["processSnapshot"].update(absenceIsHistoricalProof=True),
            lambda value: value["observerExclusions"].pop(),
            lambda value: value["codexAutomations"]["paused"][0].update(
                definitionSha256=ZERO),
            lambda value: value["persistenceSurfaces"]["startupFolders"].update(
                totalInspectedFileCount=3),
            lambda value: value["persistenceSurfaces"]["startupFolders"].update(
                filter="SHORTCUTS_ONLY"),
            lambda value: value["persistenceSurfaces"]["startupFolders"]["entries"][0].update(
                providerMatch=True),
        ]
        for index, mutate in enumerate(mutations):
            changed = json.loads(json.dumps(original))
            mutate(changed)
            with self.subTest(index=index), self.assertRaisesRegex(
                    ControlError, "MLV_CONTRACT_SCHEMA_INVALID"):
                supervisor.validate_local_contract(
                    changed, "mlv-provider-observed-inventory-v2.schema.json")

    def test_31_r13_hosted_evidence_distinguishes_technical_and_closeout_heads(self):
        packet = strict_json_file(ROOT / "AUTHOR-PACKET.json")
        hosted = packet["localEvidence"]["hostedMatrix"]
        self.assertEqual(
            hosted,
            "R10_4_OF_4_GREEN_RUN_32208720831;"
            "R11_TECHNICAL_HEAD_9e3_NOT_RUN;"
            "R11_CLOSEOUT_HEAD_152_4_OF_4_GREEN_RUN_32228841343;"
            "R12_NOT_RUN;R13_NOT_RUN;SLOT_SLICE_1_NOT_RUN;PROJECT_R14_NOT_RUN",
        )
        self.assertNotIn("R11_4_OF_4_GREEN", hosted)

    def test_32_test_no_work_skips_before_provider_identity_slot_or_reservation(self):
        self.write_demand(demand(hasWork=False))
        missing_witness = self.base / "must-not-resolve-provider.py"
        with mock.patch.dict(os.environ, self.fake_env(), clear=False), \
                mock.patch.object(supervisor, "_broker_owned_test_plan",
                                  side_effect=AssertionError("provider identity resolved")), \
                mock.patch.object(supervisor, "quota_lock",
                                  side_effect=AssertionError("quota lock reached")), \
                mock.patch.object(supervisor.subprocess, "Popen",
                                  side_effect=AssertionError("provider invoked")):
            result = supervisor.run_test_fake(self.state, self.demand, missing_witness, 0)
        self.assertEqual(result["status"], "TEST_NO_WORK_SKIPPED")
        self.assertFalse(result["reservationCreated"])
        self.assertFalse(result["slotClaimed"])
        for field in ("providerCalls", "providerProcesses", "fakeProviderCalls",
                      "fakeProviderProcesses", "inputTokens", "cachedInputTokens",
                      "reasoningTokens", "outputTokens"):
            self.assertEqual(result[field], 0)
        self.assertFalse(self.state.exists())

    def test_33_conservative_envelope_counts_fresh_cache_read_create_and_completion(self):
        profile, _, local, _ = supervisor.load_contracts()
        value = demand(hasWork=True, estimateFraction=0.1, availableFraction=0.9)
        envelope = supervisor.conservative_test_envelope(value, profile, local)
        self.assertEqual(envelope["freshInputFraction"], 0.1)
        self.assertEqual(envelope["cacheReadFraction"], 0.1)
        self.assertEqual(envelope["cacheCreateFraction"], 0.1)
        self.assertEqual(envelope["completionReserveFraction"], 0.2)
        self.assertEqual(envelope["envelopeTotalFraction"], 0.5)
        self.assertEqual(envelope["priorityReserveFloorFraction"], 0.35)
        self.assertEqual(envelope["consumptiveFractionPerAttempt"], 0.3)
        self.assertEqual(envelope["protectedReserveFraction"], 0.35)
        self.assertEqual(envelope["requiredAvailableFraction"], 0.65)
        self.assertEqual(envelope["maximumAttemptsRequiredAvailableFraction"], 0.95)
        self.assertEqual(envelope["maxRetries"], 1)

    def test_34_serial_slot_rotates_in_fixed_order_and_refuses_wrong_claimant(self):
        self.write_demand(demand(hasWork=True, lane="fable"))
        with mock.patch.dict(os.environ, self.fake_env(), clear=False):
            first = supervisor.run_test_fake(
                self.state, self.demand, HERE / "fake_provider.py", 0)
        self.assertEqual(first["slot"], {
            "lane": "fable", "generation": 0,
            "rotationOrder": list(supervisor.TEST_SLOT_ORDER),
        })
        cursor = strict_json_file(self.state / "test-claude-slot-cursor.json")
        self.assertEqual((cursor["nextLane"], cursor["generation"]), ("claude-review", 1))
        with mock.patch.dict(os.environ, self.fake_env(), clear=False), \
                mock.patch.object(supervisor.subprocess, "Popen",
                                  side_effect=AssertionError("wrong lane invoked")):
            with self.assertRaisesRegex(ControlError, "ROTATING_SLOT_NOT_CURRENT"):
                supervisor.run_test_fake(
                    self.state, self.demand, HERE / "fake_provider.py", 0)
        self.write_demand(demand(hasWork=True, lane="claude-review"))
        with mock.patch.dict(os.environ, self.fake_env(), clear=False):
            second = supervisor.run_test_fake(
                self.state, self.demand, HERE / "fake_provider.py", 0)
        self.assertEqual(second["slot"]["generation"], 1)
        cursor = strict_json_file(self.state / "test-claude-slot-cursor.json")
        self.assertEqual((cursor["nextLane"], cursor["generation"]), ("opus", 2))

    def test_35_exact_broker_owned_identity_drift_blocks_before_child(self):
        self.write_demand(demand(hasWork=True))
        real_plan = supervisor._broker_owned_test_plan
        calls = 0

        def drifting_plan(*args, **kwargs):
            nonlocal calls
            calls += 1
            value = real_plan(*args, **kwargs)
            if calls == 2:
                value["scriptSha256"] = "sha256:" + "d" * 64
            return value

        with mock.patch.dict(os.environ, self.fake_env(), clear=False), \
                mock.patch.object(supervisor, "_broker_owned_test_plan",
                                  side_effect=drifting_plan), \
                mock.patch.object(supervisor.subprocess, "Popen",
                                  side_effect=AssertionError("provider invoked")):
            with self.assertRaisesRegex(ControlError, "LAUNCH_BINDING_CHANGED"):
                supervisor.run_test_fake(
                    self.state, self.demand, HERE / "fake_provider.py", 0)
        self.assertFalse((self.state / "test-claude-slot-active.json").exists())
        self.assertFalse((self.state / "test-request-envelope-active.json").exists())

    def test_36_one_retry_maximum_and_terminal_cleanup_on_success_and_failure(self):
        self.write_demand(demand(hasWork=True, availableFraction=1.0))
        success_log = self.base / "success-attempts.txt"
        success_env = self.fake_env()
        success_env.update({"MLV_FAKE_FAIL_ATTEMPTS": "1",
                            "MLV_FAKE_ATTEMPT_LOG": str(success_log)})
        with mock.patch.dict(os.environ, success_env, clear=False):
            result = supervisor.run_test_fake(
                self.state, self.demand, HERE / "fake_provider.py", 0)
        self.assertEqual(result["retryCount"], 1)
        self.assertEqual(result["fakeProviderCalls"], 2)
        self.assertEqual(result["conservativeChargedFraction"], 0.6)
        self.assertEqual(result["availableAfterConservativeChargeFraction"], 0.4)
        self.assertEqual(result["attemptReservation"]["attempt"], 2)
        self.assertEqual(result["attemptReservation"]["availableBeforeAttemptFraction"], 0.7)
        self.assertEqual(
            result["attemptReservation"]["availableAfterConservativeChargeFraction"], 0.4)
        self.assertEqual(success_log.read_text(encoding="utf-8").splitlines(), ["1", "2"])
        self.assertFalse((self.state / "test-claude-slot-active.json").exists())
        self.assertFalse((self.state / "test-request-envelope-active.json").exists())

        failed_state = self.base / "failed-state"
        self.write_demand(demand(hasWork=True, availableFraction=1.0))
        failed_log = self.base / "failed-attempts.txt"
        failed_env = self.fake_env(failed_state)
        failed_env.update({"MLV_FAKE_FAIL_ATTEMPTS": "99",
                           "MLV_FAKE_ATTEMPT_LOG": str(failed_log)})
        with mock.patch.dict(os.environ, failed_env, clear=False):
            with self.assertRaisesRegex(ControlError, "TEST_PROVIDER_FAILED"):
                supervisor.run_test_fake(
                    failed_state, self.demand, HERE / "fake_provider.py", 0)
        self.assertEqual(failed_log.read_text(encoding="utf-8").splitlines(), ["1", "2"])
        self.assertFalse((failed_state / "test-claude-slot-active.json").exists())
        self.assertFalse((failed_state / "test-request-envelope-active.json").exists())
        failed_cursor = strict_json_file(failed_state / "test-claude-slot-cursor.json")
        self.assertEqual((failed_cursor["nextLane"], failed_cursor["generation"]),
                         ("claude-review", 1))

    def test_37_reference_slot_slice_is_unreachable_from_production_commands(self):
        tick_source = inspect.getsource(supervisor.tick)
        signal_source = inspect.getsource(supervisor.observe_signal)
        for symbol in ("_begin_test_slot", "_broker_owned_test_plan", "subprocess.Popen"):
            self.assertNotIn(symbol, tick_source)
            self.assertNotIn(symbol, signal_source)
        self.write_demand(demand(hasWork=True))
        with mock.patch.object(supervisor.subprocess, "Popen",
                              side_effect=AssertionError("production child invoked")):
            result = supervisor.tick(self.state, self.demand)
        self.assertEqual(result["reason"], "ACTIVATION_EVIDENCE_BLOCKED")
        self.assertEqual(result["automaticLaunchGate"], "CLOSED")
        self.assertEqual((result["providerCalls"], result["providerProcesses"]), (0, 0))
        self.assertFalse(self.state.exists())

    def test_38_source_path_race_executes_bound_content_addressed_artifact(self):
        self.write_demand(demand(hasWork=True))
        source = (HERE / "fake_provider.py").resolve()
        original = source.read_bytes()
        original_digest = supervisor.sha256_file(source)
        real_popen = supervisor.subprocess.Popen

        def racing_popen(*args, **kwargs):
            source.write_bytes(b"raise SystemExit(91)\n")
            return real_popen(*args, **kwargs)

        try:
            with mock.patch.dict(os.environ, self.fake_env(), clear=False), \
                    mock.patch.object(supervisor.subprocess, "Popen", side_effect=racing_popen):
                result = supervisor.run_test_fake(
                    self.state, self.demand, source, 0)
        finally:
            source.write_bytes(original)
        self.assertEqual(supervisor.sha256_file(source), original_digest)
        self.assertEqual(result["status"], "TEST_FAKE_COMPLETED")
        self.assertEqual(result["binding"]["sourceScriptSha256"], original_digest)
        self.assertEqual(result["binding"]["scriptSha256"], original_digest)
        self.assertNotEqual(Path(result["binding"]["scriptPath"]), source)
        self.assertEqual(result["fakeReceipt"]["scriptPath"],
                         result["binding"]["scriptPath"])

    def test_39_full_argv_and_digest_drift_on_reload_block_before_child(self):
        self.write_demand(demand(hasWork=True))
        for update_digest in (False, True):
            state = self.base / f"argv-drift-{update_digest}"
            real_plan = supervisor._broker_owned_test_plan
            launch_counts = {}

            def drifting_plan(*args, **kwargs):
                value = real_plan(*args, **kwargs)
                if Path(value["scriptPath"]).parent.name == "test-launch-artifacts":
                    attempt = value["attempt"]
                    launch_counts[attempt] = launch_counts.get(attempt, 0) + 1
                    if launch_counts[attempt] == 2:
                        value["argv"] = [*value["argv"], "--unbound-argv"]
                        if update_digest:
                            value["argvSha256"] = "sha256:" + hashlib.sha256(
                                supervisor.canonical_json(value["argv"]).encode()).hexdigest()
                return value

            with self.subTest(update_digest=update_digest), \
                    mock.patch.dict(os.environ, self.fake_env(state), clear=False), \
                    mock.patch.object(supervisor, "_broker_owned_test_plan",
                                      side_effect=drifting_plan), \
                    mock.patch.object(supervisor.subprocess, "Popen",
                                      side_effect=AssertionError("drifted argv reached child")):
                with self.assertRaisesRegex(ControlError, "LAUNCH_BINDING_CHANGED"):
                    supervisor.run_test_fake(
                        state, self.demand, HERE / "fake_provider.py", 0)

    def test_40_cursor_publication_failure_leaves_restart_replay_fenced(self):
        self.write_demand(demand(hasWork=True))
        real_write = supervisor._write_owned_json

        def fail_cursor(path, value):
            if path.name == "test-claude-slot-cursor.json":
                raise ControlError("INJECTED_CURSOR_WRITE_FAILURE")
            return real_write(path, value)

        with mock.patch.dict(os.environ, self.fake_env(), clear=False), \
                mock.patch.object(supervisor, "_write_owned_json", side_effect=fail_cursor):
            with self.assertRaisesRegex(ControlError, "INJECTED_CURSOR_WRITE_FAILURE"):
                supervisor.run_test_fake(
                    self.state, self.demand, HERE / "fake_provider.py", 0)
        self.assertTrue((self.state / "test-claude-slot-active.json").exists())
        self.assertTrue((self.state / "test-request-envelope-active.json").exists())
        self.assertFalse((self.state / "test-claude-slot-cursor.json").exists())
        with mock.patch.dict(os.environ, self.fake_env(), clear=False), \
                mock.patch.object(supervisor.subprocess, "Popen",
                                  side_effect=AssertionError("fenced generation replayed")):
            with self.assertRaisesRegex(ControlError, "TEST_TERMINAL_CLEANUP_INCOMPLETE"):
                supervisor.run_test_fake(
                    self.state, self.demand, HERE / "fake_provider.py", 0)

    def test_41_failed_attempt_is_charged_and_retry_must_preserve_priority_floor(self):
        self.write_demand(demand(hasWork=True, estimateFraction=0.1, availableFraction=0.9))
        attempts = self.base / "reserve-refused-attempts.txt"
        env = self.fake_env()
        env.update({"MLV_FAKE_FAIL_ATTEMPTS": "1",
                    "MLV_FAKE_ATTEMPT_LOG": str(attempts)})
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(ControlError, "CAPACITY_RETRY_RESERVE_REFUSED"):
                supervisor.run_test_fake(
                    self.state, self.demand, HERE / "fake_provider.py", 0)
        self.assertEqual(attempts.read_text(encoding="utf-8").splitlines(), ["1"])
        self.assertFalse((self.state / "test-claude-slot-active.json").exists())
        self.assertFalse((self.state / "test-request-envelope-active.json").exists())
        cursor = strict_json_file(self.state / "test-claude-slot-cursor.json")
        self.assertEqual((cursor["nextLane"], cursor["generation"]), ("claude-review", 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
