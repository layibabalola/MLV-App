from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mlv_lane_supervisor as supervisor  # noqa: E402
from vendor.universal_provider_control import ControlError  # noqa: E402


FINGERPRINT = "sha256:" + "a" * 64
ZERO = {
    "providerCalls": 0,
    "providerProcesses": 0,
    "inputTokens": 0,
    "cachedInputTokens": 0,
    "reasoningTokens": 0,
    "outputTokens": 0,
}


class PrelaunchBoundaryTests(unittest.TestCase):
    def test_real_production_decision_is_stop_only(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            demand = root / "demand.json"
            demand.write_text(json.dumps({
                "schema": "mlv-provider-demand/v1", "project": "mlv-app",
                "hasWork": True, "lane": "fable", "priority": "PRODUCT_WORK",
                "estimateFraction": 0.1, "availableFraction": 0.9, "turns": 4,
                "contextTokens": 32000, "capsuleSha256": "sha256:" + "a" * 64,
                "checkpointSha256": "sha256:" + "b" * 64,
                "cacheAffinitySha256": "sha256:" + "c" * 64,
            }), encoding="utf-8")
            state = root / "state"
            with mock.patch.object(supervisor, "DEFAULT_STATE_ROOT", state), \
                    mock.patch.object(
                        supervisor.subprocess, "Popen", side_effect=AssertionError("provider invoked")
                    ):
                result = supervisor.prelaunch_boundary(state, demand)
            self.assertEqual(result["status"], "STOPPED")
            self.assertEqual(result["reason"], "ACTIVATION_EVIDENCE_BLOCKED")
            self.assertEqual(result["automaticLaunchGate"], "CLOSED")
            self.assertFalse(state.exists())

    def test_exact_closed_gate_result_is_stopped(self):
        source = {
            "status": "REFUSED", "reason": "AUTOMATIC_LAUNCH_GATE_CLOSED",
            "automaticLaunchGate": "CLOSED", "demandFingerprint": FINGERPRINT, **ZERO,
        }
        result = supervisor.enforce_prelaunch_stop(source)
        self.assertEqual((result["status"], result["reason"]),
                         ("STOPPED", "AUTOMATIC_LAUNCH_GATE_CLOSED"))

    def test_exact_no_work_result_is_stopped(self):
        source = {"status": "IDLE_SKIPPED", "demandFingerprint": FINGERPRINT, **ZERO}
        result = supervisor.enforce_prelaunch_stop(source)
        self.assertEqual((result["status"], result["reason"]), ("STOPPED", "NO_WORK"))

    def test_authorization_or_unknown_shape_is_refused(self):
        for source in (
            {"status": "AUTHORIZED", "demandFingerprint": FINGERPRINT, **ZERO},
            {"status": "REFUSED", "reason": "AUTOMATIC_LAUNCH_GATE_CLOSED",
             "automaticLaunchGate": "CLOSED", "demandFingerprint": FINGERPRINT,
             "authorizationId": "forged", **ZERO},
        ):
            with self.subTest(source=source), self.assertRaisesRegex(
                ControlError, "PRELAUNCH_RESULT_INVALID"
            ):
                supervisor.enforce_prelaunch_stop(source)

    def test_open_gate_result_is_refused(self):
        source = {
            "status": "REFUSED",
            "reason": "AUTOMATIC_LAUNCH_GATE_CLOSED",
            "automaticLaunchGate": "OPEN",
            "demandFingerprint": FINGERPRINT,
            **ZERO,
        }
        with self.assertRaisesRegex(ControlError, "PRELAUNCH_RESULT_INVALID"):
            supervisor.enforce_prelaunch_stop(source)

    def test_native_zero_types_are_required(self):
        for field, value in (("providerCalls", False), ("providerProcesses", "0"),
                             ("inputTokens", 0.0)):
            source = {
                "status": "REFUSED", "reason": "AUTOMATIC_LAUNCH_GATE_CLOSED",
                "automaticLaunchGate": "CLOSED", "demandFingerprint": FINGERPRINT, **ZERO,
            }
            source[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                ControlError, "PRELAUNCH_RESULT_INVALID"
            ):
                supervisor.enforce_prelaunch_stop(source)

    def test_fingerprint_and_blocker_types_are_exact(self):
        blocked = {
            "status": "REFUSED", "reason": "ACTIVATION_EVIDENCE_BLOCKED",
            "automaticLaunchGate": "CLOSED", "disposition": "DISTINGUISH",
            "blockers": ["DIRECT_LAUNCHER_OBSERVED"],
            "demandFingerprint": FINGERPRINT, **ZERO,
        }
        for mutation in (
            {"demandFingerprint": "sha256:" + "A" * 64},
            {"blockers": "DIRECT_LAUNCHER_OBSERVED"},
            {"blockers": []},
        ):
            hostile = dict(blocked)
            hostile.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(
                ControlError, "PRELAUNCH_RESULT_INVALID"
            ):
                supervisor.enforce_prelaunch_stop(hostile)

    def test_tracked_wrapper_exposes_stop_only_prelaunch_command(self):
        wrapper = (ROOT / "invoke-mlv-lane-supervisor.ps1").read_text(encoding="utf-8")
        self.assertIn("'tick','prelaunch','status','observe-signal'", wrapper)
        self.assertIn("@('tick','prelaunch')", wrapper)
        for forbidden in ("Start-Process", "claude.cmd", "Enable-ScheduledTask",
                          "Start-ScheduledTask"):
            self.assertNotIn(forbidden, wrapper)


if __name__ == "__main__":
    unittest.main()
