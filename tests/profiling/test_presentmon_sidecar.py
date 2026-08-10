import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools" / "profiling" / "presentmon-sidecar"
SUBMIT = TOOLS / "submit-presentmon-sidecar.ps1"
RUNNER = TOOLS / "invoke-presentmon-sidecar-runner.ps1"
PUBLISH = TOOLS / "publish-presentmon-sidecar-artifacts.ps1"
VERIFY = TOOLS / "verify-presentmon-sidecar-deployment.ps1"
PWSH = shutil.which("pwsh.exe") or shutil.which("pwsh")


def run_ps(script: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    if not PWSH:
        raise unittest.SkipTest("pwsh is required")
    return subprocess.run(
        [
            PWSH,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *map(str, args),
        ],
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "+00:00")


def request_hash(request: dict) -> str:
    keys = (
        "schema",
        "requestId",
        "requestedUtc",
        "expiresUtc",
        "processName",
        "outputFile",
        "timedSeconds",
    )
    canonical = json.dumps({key: request[key] for key in keys}, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest().upper()


class PresentMonSidecarContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.mailbox = self.base / "mailbox"
        self.outbox = self.base / "outbox"
        for name in ("requests", "claims", "receipts", "rejected"):
            (self.mailbox / name).mkdir(parents=True, exist_ok=True)
        self.outbox.mkdir()
        self.fake = self.base / "fake-presentmon.ps1"
        self.fake.write_text(
            "param([int]$FakeExitCode = 0, [string]$process_name, [string]$output_file, [int]$timed, [switch]$terminate_after_timed, [switch]$stop_existing_session, [switch]$no_console_stats)\n"
            "if ($output_file -and $FakeExitCode -eq 0) {\n"
            "  [IO.File]::WriteAllText($output_file, \"MsBetweenDisplayChange,PresentMode`n16.6,Hardware Independent Flip`n\")\n"
            "}\n"
            "exit $FakeExitCode\n",
            encoding="utf-8",
        )
        self.pwsh_hash = hashlib.sha256(Path(PWSH).read_bytes()).hexdigest().upper() if PWSH else ""

    def tearDown(self):
        self.temp.cleanup()

    def make_trigger(self, fake_exit: int = 0) -> Path:
        trigger = self.base / f"trigger-{fake_exit}.ps1"
        quote = lambda value: str(value).replace("'", "''")
        prefix_json = json.dumps(
            ["-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.fake), "-FakeExitCode", str(fake_exit)]
        ).replace("'", "''")
        trigger.write_text(
            "param([string]$RequestId)\n"
            f"& '{quote(RUNNER)}' -MailboxRoot '{quote(self.mailbox)}' "
            f"-AllowedOutputRoot '{quote(self.outbox)}' -PresentMonPath '{quote(PWSH)}' "
            f"-ExpectedPresentMonSha256 '{self.pwsh_hash}' "
            f"-PresentMonArgumentsPrefixJson '{prefix_json}'\n"
            "exit 0\n",
            encoding="utf-8",
        )
        return trigger

    def submit(self, trigger: Path, result: Path | None = None, wait: int = 5):
        result = result or self.base / "sidecar-result.json"
        output = self.outbox / "run" / "presentmon.csv"
        completed = run_ps(
            SUBMIT,
            "-ProcessName",
            "MLVApp-test.exe",
            "-OutputFile",
            output,
            "-ResultPath",
            result,
            "-MailboxRoot",
            self.mailbox,
            "-AllowedOutputRoot",
            self.outbox,
            "-TriggerScript",
            trigger,
            "-TimedSeconds",
            "1",
            "-WaitTimeoutSeconds",
            str(wait),
        )
        return completed, json.loads(result.read_text(encoding="utf-8")), output

    def write_request(self, *, expired=False, output: Path | None = None, tamper=False, request_id="a" * 32) -> dict:
        now = datetime.now(timezone.utc)
        request = {
            "schema": "mlvapp.presentmon-sidecar-request.v2",
            "requestId": request_id,
            "requestedUtc": iso(now - timedelta(minutes=5) if expired else now),
            "expiresUtc": iso(now - timedelta(minutes=1) if expired else now + timedelta(minutes=5)),
            "processName": "MLVApp-test.exe",
            "outputFile": str(output or (self.outbox / "direct.csv")),
            "timedSeconds": 1,
        }
        request["requestHash"] = request_hash(request)
        if tamper:
            request["processName"] = "tampered.exe"
        path = self.mailbox / "requests" / f"{request_id}.request.json"
        path.write_text(json.dumps(request, separators=(",", ":")), encoding="utf-8")
        return request

    def run_runner(self, fake_exit=0):
        return run_ps(
            RUNNER,
            "-MailboxRoot",
            self.mailbox,
            "-AllowedOutputRoot",
            self.outbox,
            "-PresentMonPath",
            PWSH,
            "-ExpectedPresentMonSha256",
            self.pwsh_hash,
            "-PresentMonArgumentsPrefixJson",
            json.dumps(["-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.fake), "-FakeExitCode", str(fake_exit)]),
        )

    def test_happy_path_binds_unique_request_and_receipt(self):
        completed, result, output = self.submit(self.make_trigger())
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertTrue(result["rateClaimsAdmissible"])
        self.assertEqual(result["reason"], "ok")
        self.assertTrue(output.is_file())
        receipts = list((self.mailbox / "receipts").glob("*.done.json"))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt["requestId"], result["requestId"])
        self.assertEqual(receipt["requestHash"], result["requestHash"])

    def test_expired_request_is_rejected_instead_of_replayed(self):
        request = self.write_request(expired=True)
        completed = self.run_runner()
        self.assertEqual(completed.returncode, 2, completed.stderr + completed.stdout)
        receipt = json.loads((self.mailbox / "receipts" / f"{request['requestId']}.done.json").read_text())
        self.assertEqual(receipt["reason"], "request_expired")
        self.assertFalse(list((self.mailbox / "requests").glob("*.json")))

    def test_tampered_request_hash_is_rejected(self):
        request = self.write_request(tamper=True)
        completed = self.run_runner()
        self.assertEqual(completed.returncode, 10)
        receipt = json.loads((self.mailbox / "receipts" / f"{request['requestId']}.done.json").read_text())
        self.assertEqual(receipt["reason"], "request_hash_mismatch")

    def test_output_path_is_confined_to_allowlisted_root(self):
        request = self.write_request(output=self.base / "outside.csv")
        completed = self.run_runner()
        self.assertEqual(completed.returncode, 10)
        receipt = json.loads((self.mailbox / "receipts" / f"{request['requestId']}.done.json").read_text())
        self.assertEqual(receipt["reason"], "output_path_outside_allowed_root")

    def test_existing_request_makes_second_client_fail_mailbox_busy(self):
        self.write_request()
        no_op = self.base / "noop.ps1"
        no_op.write_text("param([string]$RequestId)\nexit 0\n", encoding="utf-8")
        completed, result, _ = self.submit(no_op)
        self.assertEqual(completed.returncode, 22)
        self.assertEqual(result["reason"], "mailbox_busy")
        self.assertFalse(result["rateClaimsAdmissible"])

    def test_expired_queued_request_is_quarantined_before_new_submission(self):
        stale = self.write_request(expired=True)
        completed, result, output = self.submit(self.make_trigger())
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertTrue(result["rateClaimsAdmissible"])
        self.assertNotEqual(result["requestId"], stale["requestId"])
        self.assertTrue(output.is_file())
        quarantined = list((self.mailbox / "rejected").glob("*.client-expired.json"))
        self.assertEqual(len(quarantined), 1)

    def test_partial_atomic_publication_file_is_never_consumed(self):
        partial = self.mailbox / "requests" / ".interrupted.request.json.tmp"
        partial.write_text('{"requestId":"old"', encoding="utf-8")
        completed, result, output = self.submit(self.make_trigger())
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertTrue(result["rateClaimsAdmissible"])
        self.assertTrue(output.is_file())
        self.assertTrue(partial.is_file())

    def test_missing_receipt_fails_closed(self):
        no_op = self.base / "noop.ps1"
        no_op.write_text("param([string]$RequestId)\nexit 0\n", encoding="utf-8")
        completed, result, _ = self.submit(no_op, wait=1)
        self.assertEqual(completed.returncode, 23)
        self.assertEqual(result["reason"], "receipt_timeout")
        self.assertFalse(result["rateClaimsAdmissible"])

    def test_mismatched_receipt_is_not_accepted(self):
        trigger = self.base / "wrong-receipt.ps1"
        quote = lambda value: str(value).replace("'", "''")
        trigger.write_text(
            "param([string]$RequestId)\n"
            f"$root='{quote(self.mailbox)}'\n"
            "$req=Get-Content -Raw -LiteralPath (Join-Path $root \"requests\\$RequestId.request.json\")|ConvertFrom-Json\n"
            "$bad=[ordered]@{requestId='wrong';requestHash=$req.requestHash;status='done';reason='ok';processName=$req.processName;outputFile=$req.outputFile;presentMonExitCode=0}\n"
            "$bad|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $root \"receipts\\$RequestId.done.json\")\n"
            "exit 0\n",
            encoding="utf-8",
        )
        completed, result, _ = self.submit(trigger)
        self.assertEqual(completed.returncode, 24)
        self.assertEqual(result["reason"], "receipt_identity_mismatch")

    def test_presentmon_failure_marks_rate_claims_inadmissible(self):
        completed, result, _ = self.submit(self.make_trigger(fake_exit=7))
        self.assertEqual(completed.returncode, 21)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "presentmon_failed")
        self.assertEqual(result["presentMonExitCode"], 7)
        self.assertFalse(result["rateClaimsAdmissible"])

    def test_app_telemetry_publishes_when_sidecar_failed(self):
        telemetry = self.base / "playback-smoke.json"
        telemetry.write_text('{"presented_frames":42}', encoding="utf-8")
        sidecar = self.base / "failed-sidecar.json"
        sidecar.write_text(
            json.dumps({"status": "client_failure", "reason": "receipt_timeout", "rateClaimsAdmissible": False}),
            encoding="utf-8",
        )
        publication = self.base / "publication"
        completed = run_ps(
            PUBLISH,
            "-AppTelemetryPath",
            telemetry,
            "-SidecarResultPath",
            sidecar,
            "-PublicationRoot",
            publication,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((publication / telemetry.name).is_file())
        manifest = json.loads((publication / "presentmon-sidecar-publication.json").read_text())
        self.assertFalse(manifest["rateClaimsAdmissible"])
        self.assertEqual(manifest["rateClaimsInadmissibleReason"], "receipt_timeout")

    def test_deployment_verifier_is_hash_exact_and_read_only(self):
        deployed_runner = self.base / "runner.ps1"
        deployed_module = self.base / "module.psm1"
        shutil.copy2(RUNNER, deployed_runner)
        shutil.copy2(TOOLS / "PresentMonSidecar.psm1", deployed_module)
        completed = run_ps(
            VERIFY,
            "-ExpectedRunnerPath",
            RUNNER,
            "-DeployedRunnerPath",
            deployed_runner,
            "-ExpectedModulePath",
            TOOLS / "PresentMonSidecar.psm1",
            "-DeployedModulePath",
            deployed_module,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["valid"])
        deployed_runner.write_text("changed", encoding="utf-8")
        mismatch = run_ps(
            VERIFY,
            "-ExpectedRunnerPath",
            RUNNER,
            "-DeployedRunnerPath",
            deployed_runner,
            "-ExpectedModulePath",
            TOOLS / "PresentMonSidecar.psm1",
            "-DeployedModulePath",
            deployed_module,
        )
        self.assertEqual(mismatch.returncode, 1)


if __name__ == "__main__":
    unittest.main()
