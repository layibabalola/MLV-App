import hashlib
import json
import struct
import tempfile
import unittest
import zlib
import shutil
import subprocess
from unittest import mock
from pathlib import Path

from tools.gates import output_budget


def png_bytes(width, height, pixels):
    raw = b"".join(b"\x00" + bytes(pixels[y * width * 3:(y + 1) * width * 3]) for y in range(height))
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class OutputBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "tools/gates").mkdir(parents=True)
        self.shipping = self.root / "tools/gates/shipping-defaults.json"
        self.shipping.write_text(json.dumps({"playback": {"qualityMode": {"value": 1}, "scaleFactorOverride": {"value": 0}, "derived": {"initialScaleRequest": {"nonDualIso": 4, "dualIso": 4}}}}), encoding="utf-8")
        self.receipt = self.root / "receipt.bin"
        self.receipt.write_bytes(b"receipt")
        self.clip = self.root / "clip.mlv"
        self.clip.write_bytes(b"clip")
        self.base_png = self.root / "base.png"
        self.cand_png = self.root / "candidate.png"
        pixels = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120]
        self.base_png.write_bytes(png_bytes(2, 2, pixels))
        self.cand_png.write_bytes(png_bytes(2, 2, pixels))
        zero_values = {
            "maxAbsLumaP05Delta": 0.0, "maxAbsLumaP50Delta": 0.0, "maxAbsLumaP95Delta": 0.0,
            "maxAbsLumaMeanDelta": 0.0, "maxAbsVisibleGreenAxisDelta": 0.0, "maxAbsGreenArtifactRatioDelta": 0.0,
        }
        zero_pixels = {
            "maxMeanAbsRgb": 0.0, "maxP95AbsRgb": 0.0, "maxAbsRgb": 0, "maxChangedPixelRatioGt0": 0.0,
            "maxChangedPixelRatioGt2": 0.0, "maxChangedPixelRatioGt8": 0.0, "maxRmseRgb": 0.0,
        }
        self.spec = {
            "schema": output_budget.SCHEMA,
            "shippingDefaults": {"path": "tools/gates/shipping-defaults.json", "sha256": output_budget.sha256_file(self.shipping)},
            "baseline": {"status": "reviewed_instrumented_known_good", "commit": "a" * 40, "executableSha256": "A" * 64, "artifact": "fixture://reviewed-baseline"},
            "receipt": {"path": str(self.receipt), "length": 7, "sha256": output_budget.sha256_file(self.receipt)},
            "profiles": [{"id": "wb_locked", "disableLookAssist": True, "playbackProcessing": "none", "qualityMode": "1", "expectedQualityMode": 1, "scaleFactor": "", "expectedScaleRequest": 4, "selectionAuthority": "shipping-default-controlled"}],
            "clips": [{"id": "clip", "required": True, "path": str(self.clip), "length": 4, "sha256": output_budget.sha256_file(self.clip), "startFrame": 4,
                       "parts": [{"path": str(self.clip), "length": 4, "sha256": output_budget.sha256_file(self.clip)}],
                       "aspect": {"mode": "presented-playback-stretch", "stretchX": 3.0, "stretchY": 1.0, "hStretchIndex": 0, "vStretchIndex": 3}}],
            "budgets": {"values": zero_values, "pixels": zero_pixels, "cadence": {"repeats": 3, "seconds": 30, "maxP99DeltaMs": 15.0, "maxHitchFractionDelta": 0.02, "authority": "advisory_only"}},
            "legacyPolicyAllowlist": [],
        }
        self.spec_path = self.root / "tools/gates/spec.json"
        self.spec_path.write_text(json.dumps(self.spec), encoding="utf-8")
        self.baseline = {"path": str(self.root / "base.exe"), "commit": "a" * 40, "sha256": "A" * 64}
        self.candidate = {"path": str(self.root / "candidate.exe"), "commit": "b" * 40, "sha256": "B" * 64}
        (self.root / "base.exe").write_bytes(b"MLVAPP_BUILDSTAMP_v1|sha=" + b"a" * 40 + b"|dirty=0")
        (self.root / "candidate.exe").write_bytes(b"MLVAPP_BUILDSTAMP_v1|sha=" + b"b" * 40 + b"|dirty=0")
        self.baseline["sha256"] = output_budget.sha256_file(self.root / "base.exe")
        self.candidate["sha256"] = output_budget.sha256_file(self.root / "candidate.exe")
        self.spec["baseline"]["executableSha256"] = self.baseline["sha256"]
        self.spec_path.write_text(json.dumps(self.spec), encoding="utf-8")
        self.base_result = self._write_result("base-result.json", self.baseline["path"], self.base_png)
        self.cand_result = self._write_result("candidate-result.json", self.candidate["path"], self.cand_png)

    def tearDown(self):
        self.temp.cleanup()

    def _stub_exe_validation(self):
        original = output_budget.validate_exe
        output_budget.validate_exe = lambda path, commit, digest, root: {"path": str(path), "commit": commit, "sha256": digest, "dirty": 0}
        self.addCleanup(setattr, output_budget, "validate_exe", original)

    def _write_result(self, name, exe, screenshot):
        provenance = {
            "schema": "mlvapp-gui-smoke-screenshot-provenance.v2", "validAssociation": True, "validFresh": True,
            "pathSource": "render_thread", "processed8CacheHit": 0, "displayFrame": 4, "requestedStartFrame": 4,
            "effectiveStartFrame": 4, "requestedFrame": 4, "presentationIndex": 1, "requestSerial": 2,
            "requestSerialOffset": 1, "screenshotMethod": "presented", "screenshotWidth": 2, "screenshotHeight": 2,
            "screenshotPath": str(screenshot),
            "presentedHistory": [{"index": 1, "displayFrame": 4, "serial": 2, "serialOffset": 1, "requestedFrame": 4, "generation": 8}],
            "effectiveState": {"visualState": {"scale": 4}, "playbackPolicy": {"processing": "none"}, "frame": {"scale": 4}, "renderManifest": {"path": "render_thread"}},
        }
        result = {
            "schema": "mlvapp-gui-smoke-result.v1", "exePath": exe, "clipPath": str(self.clip),
            "inputBindings": {
                "executable": {"path": exe, "length": Path(exe).stat().st_size, "sha256": output_budget.sha256_file(Path(exe)), "embeddedCommit": "a" * 40 if "base.exe" in exe else "b" * 40, "dirty": 0, "stampFound": True},
                "clipParts": [{"path": str(self.clip), "length": 4, "sha256": output_budget.sha256_file(self.clip)}],
                "receipt": None,
            },
            "validation": {"ok": True, "screenshotProvenance": provenance},
            "screenshot": {"path": str(screenshot), "capture": {"image": {"sha256": output_budget.sha256_file(screenshot)}}},
            "visualQuality": {"playbackPolicy": {"playback_processing_selected": "none"}, "visualState": {"look_assist_enabled": 0, "quality_mode": 1, "scale_request": 4}, "aspectEvidence": {"mode": "presented-playback-stretch", "stretchX": 3.0, "stretchY": 1.0, "hStretchIndex": 0, "vStretchIndex": 3}},
        }
        path = self.root / name
        path.write_text(json.dumps(result), encoding="utf-8")
        return path

    def evidence(self):
        return {
            "schema": "mlvapp.output-budget-evidence.v1", "specSha256": output_budget.sha256_file(self.spec_path),
            "shippingDefaultsSha256": output_budget.sha256_file(self.shipping), "baseline": self.baseline, "candidate": self.candidate,
            "pairs": [{"clipId": "clip", "profileId": "wb_locked", "baselineResult": str(self.base_result), "candidateResult": str(self.cand_result)}],
            "cadence": None,
        }

    def test_identical_full_frame_passes_without_cadence_credit(self):
        original_validate_exe = output_budget.validate_exe
        output_budget.validate_exe = lambda path, commit, digest, root: {"path": str(path), "commit": commit, "sha256": digest, "dirty": 0}
        self.addCleanup(setattr, output_budget, "validate_exe", original_validate_exe)
        report, code = output_budget.evaluate(output_budget.validate_spec(self.spec), self.evidence(), self.spec_path)
        self.assertEqual(0, code)
        self.assertEqual("PASS", report["blockingVerdict"])
        self.assertEqual("INDETERMINATE", report["cadenceVerdict"])
        self.assertTrue(report["authorizing"])

    def test_last_pixel_change_is_not_hidden_by_sampling(self):
        original_validate_exe = output_budget.validate_exe
        output_budget.validate_exe = lambda path, commit, digest, root: {"path": str(path), "commit": commit, "sha256": digest, "dirty": 0}
        self.addCleanup(setattr, output_budget, "validate_exe", original_validate_exe)
        changed = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 121]
        self.cand_png.write_bytes(png_bytes(2, 2, changed))
        self.cand_result = self._write_result("candidate-result.json", self.candidate["path"], self.cand_png)
        report, code = output_budget.evaluate(output_budget.validate_spec(self.spec), self.evidence(), self.spec_path)
        self.assertEqual(3, code)
        self.assertEqual("FAIL", report["blockingVerdict"])
        self.assertFalse(report["authorizing"])

    def test_transaction_history_mismatch_blocks(self):
        original_validate_exe = output_budget.validate_exe
        output_budget.validate_exe = lambda path, commit, digest, root: {"path": str(path), "commit": commit, "sha256": digest, "dirty": 0}
        self.addCleanup(setattr, output_budget, "validate_exe", original_validate_exe)
        result = json.loads(self.cand_result.read_text())
        result["validation"]["screenshotProvenance"]["requestSerial"] = 3
        self.cand_result.write_text(json.dumps(result), encoding="utf-8")
        report, code = output_budget.evaluate(output_budget.validate_spec(self.spec), self.evidence(), self.spec_path)
        self.assertEqual(3, code)
        self.assertTrue(any("requestSerial mismatch" in item for item in report["failures"]))

    def test_replacement_baseline_is_rejected_even_when_clean_and_distinct(self):
        evidence = self.evidence()
        evidence["baseline"]["commit"] = "c" * 40
        evidence["baseline"]["sha256"] = "C" * 64
        with self.assertRaises(output_budget.ContractError):
            output_budget.evaluate(output_budget.validate_spec(self.spec), evidence, self.spec_path)

    def test_supplied_evidence_cannot_bypass_pinned_start_frame(self):
        self._stub_exe_validation()
        result = json.loads(self.cand_result.read_text())
        result["validation"]["screenshotProvenance"]["requestedStartFrame"] = 999
        result["validation"]["screenshotProvenance"]["effectiveStartFrame"] = 999
        self.cand_result.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaises(output_budget.ContractError):
            output_budget.evaluate(output_budget.validate_spec(self.spec), self.evidence(), self.spec_path)

    def test_stale_result_cannot_be_rebound_to_replaced_executable_bytes(self):
        self._stub_exe_validation()
        result = json.loads(self.cand_result.read_text())
        result["inputBindings"]["executable"]["sha256"] = "C" * 64
        self.cand_result.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaises(output_budget.ContractError):
            output_budget.evaluate(output_budget.validate_spec(self.spec), self.evidence(), self.spec_path)

    def test_committed_receipt_profile_requires_exact_launch_time_receipt_binding(self):
        self._stub_exe_validation()
        self.spec["profiles"][0]["playbackProcessing"] = "receipt"
        self.spec_path.write_text(json.dumps(self.spec), encoding="utf-8")
        for path in (self.base_result, self.cand_result):
            result = json.loads(path.read_text())
            result["visualQuality"]["playbackPolicy"]["playback_processing_selected"] = "receipt"
            path.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaises(output_budget.ContractError):
            output_budget.evaluate(output_budget.validate_spec(self.spec), self.evidence(), self.spec_path)

    def test_runtime_failure_atomically_replaces_stale_authorizing_report(self):
        evidence_path = self.root / "evidence.json"
        evidence_path.write_text(json.dumps(self.evidence()), encoding="utf-8")
        report_path = self.root / "report.json"
        report_path.write_text('{"authorizing":true}', encoding="utf-8")
        argv = ["output_budget.py", "evaluate", "--spec", str(self.spec_path), "--evidence", str(evidence_path), "--output", str(report_path)]
        with mock.patch("sys.argv", argv), mock.patch.object(output_budget, "evaluate", side_effect=OSError("input disappeared")):
            self.assertEqual(2, output_budget.main())
        replacement = json.loads(report_path.read_text())
        self.assertFalse(replacement["authorizing"])
        self.assertIn("input disappeared", replacement["failures"][0])

    def test_evidence_only_wrapper_is_non_authorizing_and_nonzero(self):
        wrapper = (Path(__file__).parent / "compare-output-budget.ps1").read_text(encoding="utf-8")
        block = wrapper.split("if ($EvidenceOnly", 1)[1]
        self.assertIn("$report.authorizing = $false", block)
        self.assertIn("exit 2", block)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is required for the wrapper integration test")
    def test_failed_wrapper_preflight_replaces_stale_authorizing_report(self):
        repo_root = Path(__file__).resolve().parents[2]
        state_root = repo_root / ".claude-state"
        state_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=state_root) as output:
            output_path = Path(output)
            report_path = output_path / "report.json"
            report_path.write_text('{"authorizing":true,"marker":"stale"}', encoding="utf-8")
            command = [
                shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(repo_root / "tools/gates/compare-output-budget.ps1"),
                "-RepoRoot", str(repo_root), "-SpecPath", "tools/gates/output-budget.json",
                "-BaselineExe", str(repo_root / "missing-baseline.exe"), "-BaselineCommit", "a" * 40, "-BaselineSha256", "A" * 64,
                "-CandidateExe", str(repo_root / "missing-candidate.exe"), "-CandidateCommit", "b" * 40, "-CandidateSha256", "B" * 64,
                "-OutputRoot", str(output_path.relative_to(repo_root)).replace("\\", "/"),
            ]
            completed = subprocess.run(command, cwd=repo_root, capture_output=True, text=True)
            self.assertEqual(4, completed.returncode, completed.stdout + completed.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8-sig"))
            self.assertFalse(report["authorizing"])
            self.assertNotIn("marker", report)

    def test_unbound_cadence_is_rejected_and_cannot_mute_pixel_failure(self):
        original_validate_exe = output_budget.validate_exe
        output_budget.validate_exe = lambda path, commit, digest, root: {"path": str(path), "commit": commit, "sha256": digest, "dirty": 0}
        self.addCleanup(setattr, output_budget, "validate_exe", original_validate_exe)
        self.cand_png.write_bytes(png_bytes(2, 2, [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 121]))
        self.cand_result = self._write_result("candidate-result.json", self.candidate["path"], self.cand_png)
        evidence = self.evidence()
        evidence["cadence"] = {"baselineP99Ms": 10.0, "candidateP99Ms": 30.0, "baselineHitchFraction": 0.0, "candidateHitchFraction": 0.03, "repeats": 3}
        with self.assertRaises(output_budget.ContractError):
            output_budget.evaluate(output_budget.validate_spec(self.spec), evidence, self.spec_path)

    def test_duplicate_key_and_bool_integer_are_rejected(self):
        duplicate = self.root / "duplicate.json"
        duplicate.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
        with self.assertRaises(output_budget.ContractError):
            output_budget.load_json(duplicate)
        broken = json.loads(json.dumps(self.spec))
        broken["clips"][0]["startFrame"] = True
        with self.assertRaises(output_budget.ContractError):
            output_budget.validate_spec(broken)

    def test_required_unacquired_clip_is_a_hard_preflight_block(self):
        broken = json.loads(json.dumps(self.spec))
        broken["clips"][0]["length"] = None
        broken["clips"][0]["sha256"] = None
        broken["clips"][0]["parts"] = []
        broken["clips"][0]["evidenceStatus"] = "required_corpus_not_acquired"
        self.spec_path.write_text(json.dumps(broken), encoding="utf-8")
        report, code = output_budget.preflight(self.spec_path, self.root, None, None)
        self.assertEqual(4, code)
        self.assertFalse(report["authorizing"])
        self.assertTrue(any("no pinned length/hash" in item for item in report["failures"]))


if __name__ == "__main__":
    unittest.main()
