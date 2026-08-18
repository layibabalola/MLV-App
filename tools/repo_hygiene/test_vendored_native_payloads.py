from __future__ import annotations

import copy
import hashlib
import json
import stat
import struct
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from .vendored_native_payloads import (
    DEFAULT_MANIFEST,
    PayloadIntegrityError,
    _binary_identity,
    _git_tracked_artifacts,
    _validate_archive,
    _validate_consumer_claim,
    _validate_policy,
    _validate_readiness,
    main,
    validate,
)


ROOT = Path(__file__).resolve().parents[2]


def _fake_pe(machine: int = 0x8664, fill: bytes = b"payload", *, dll: bool = False) -> bytes:
    payload = bytearray(512)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", payload, 0x84, machine)
    struct.pack_into("<H", payload, 0x80 + 22, 0x2000 if dll else 0)
    payload[0x100 : 0x100 + len(fill)] = fill
    return bytes(payload)


def _fake_elf(*, elf_type: int, interp: bool) -> bytes:
    payload = bytearray(256)
    payload[:4] = b"\x7fELF"
    payload[4:6] = b"\x02\x01"  # ELF64, little-endian
    struct.pack_into("<H", payload, 16, elf_type)
    struct.pack_into("<H", payload, 18, 62)  # x86_64
    struct.pack_into("<I", payload, 20, 1)
    struct.pack_into("<Q", payload, 32, 64 if interp else 0)
    struct.pack_into("<H", payload, 52, 64)
    struct.pack_into("<H", payload, 54, 56)
    struct.pack_into("<H", payload, 56, 1 if interp else 0)
    if interp:
        struct.pack_into("<I", payload, 64, 3)  # PT_INTERP
    return bytes(payload)


def _fake_macho(
    *,
    cpu_type: int = 0x01000007,
    file_type: int = 2,
    byte_order: str = "<",
) -> bytes:
    payload = bytearray(64)
    payload[:4] = b"\xcf\xfa\xed\xfe" if byte_order == "<" else b"\xfe\xed\xfa\xcf"
    struct.pack_into(f"{byte_order}I", payload, 4, cpu_type)
    struct.pack_into(f"{byte_order}I", payload, 8, 3)
    struct.pack_into(f"{byte_order}I", payload, 12, file_type)
    return bytes(payload)


def _fake_fat_macho(
    *,
    file_types: tuple[int, int] = (2, 2),
    is_64: bool = False,
    byte_order: str = ">",
) -> bytes:
    payload = bytearray(768)
    magics = {
        (False, ">"): b"\xca\xfe\xba\xbe",
        (False, "<"): b"\xbe\xba\xfe\xca",
        (True, ">"): b"\xca\xfe\xba\xbf",
        (True, "<"): b"\xbf\xba\xfe\xca",
    }
    payload[:4] = magics[(is_64, byte_order)]
    struct.pack_into(f"{byte_order}I", payload, 4, 2)
    entry_size = 32 if is_64 else 20
    for index, (cpu_type, file_type, offset) in enumerate(
        ((0x01000007, file_types[0], 256), (0x0100000C, file_types[1], 512))
    ):
        entry = 8 + index * entry_size
        struct.pack_into(f"{byte_order}I", payload, entry, cpu_type)
        struct.pack_into(f"{byte_order}I", payload, entry + 4, 0)
        if is_64:
            struct.pack_into(f"{byte_order}Q", payload, entry + 8, offset)
            struct.pack_into(f"{byte_order}Q", payload, entry + 16, 64)
        else:
            struct.pack_into(f"{byte_order}I", payload, entry + 8, offset)
            struct.pack_into(f"{byte_order}I", payload, entry + 12, 64)
        payload[offset : offset + 64] = _fake_macho(cpu_type=cpu_type, file_type=file_type)
    return bytes(payload)


class VendoredNativePayloadTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((ROOT / DEFAULT_MANIFEST).read_text(encoding="utf-8"))

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.policy = copy.deepcopy(self.manifest["integrity_policy"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_zip(
        self,
        entries: list[tuple[str, bytes, int | None]],
        *,
        compression: int = zipfile.ZIP_STORED,
    ) -> Path:
        path = self.root / "fixture.zip"
        with zipfile.ZipFile(path, "w", compression=compression) as archive:
            for name, content, mode in entries:
                info = zipfile.ZipInfo(name)
                info.compress_type = compression
                if mode is not None:
                    info.create_system = 3
                    info.external_attr = mode << 16
                archive.writestr(info, content)
        return path

    def _record(self, path: Path, member_name: str, content: bytes) -> dict:
        archive_bytes = path.read_bytes()
        return {
            "id": "fixture",
            "path": path.name,
            "archive_format": "zip",
            "bytes": len(archive_bytes),
            "sha256": hashlib.sha256(archive_bytes).hexdigest(),
            "member_summary": {
                "entries": 1,
                "file_entries": 1,
                "directory_entries": 0,
                "total_uncompressed_bytes": len(content),
            },
            "selected_members": [
                {
                    "path": member_name,
                    "output_name": "tool.exe",
                    "kind": "executable",
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "binary": {"format": "pe", "machine": "x86_64"},
                }
            ],
        }

    def test_committed_manifest_integrity_passes_but_redistribution_remains_blocked(self) -> None:
        result = validate(ROOT)
        self.assertEqual(result["integrity"], "pass")
        self.assertEqual(result["payload_count"], 9)
        self.assertEqual(result["inactive_artifact_count"], 1)
        self.assertEqual(result["redistribution_readiness"], "blocked")
        self.assertEqual(result["redistribution_enforcement"], "advisory")
        self.assertGreaterEqual(len(result["redistribution_blockers"]), 4)

    def test_manifest_keeps_unknown_raw2mlv_provenance_and_arm64_ffmpeg_debt_explicit(self) -> None:
        raw_records = [record for record in self.manifest["payloads"] if record["id"].startswith("raw2mlv-")]
        self.assertEqual(len(raw_records), 5)
        self.assertTrue(all(record["provenance"]["status"] == "unknown" for record in raw_records))
        self.assertTrue(all(record["provenance"]["license"] == "unknown" for record in raw_records))
        mac_ffmpeg = next(record for record in self.manifest["payloads"] if record["id"] == "ffmpeg-macos-x86_64")
        arm_consumer = next(row for row in mac_ffmpeg["consumers"] if row["target"] == "macos-arm64")
        self.assertEqual(arm_consumer["status"], "active-release-rule-architecture-mismatch")
        self.assertEqual(mac_ffmpeg["selected_members"][0]["binary"]["machine"], "x86_64")

    def test_strict_redistribution_mode_stays_red_without_failing_integrity_mode(self) -> None:
        blocked = {
            "integrity": "pass",
            "payload_count": 9,
            "inactive_artifact_count": 1,
            "redistribution_readiness": "blocked",
            "redistribution_enforcement": "advisory",
            "redistribution_blockers": ["unverified"],
        }
        with mock.patch("tools.repo_hygiene.vendored_native_payloads.validate", return_value=blocked), \
             mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            self.assertEqual(main([]), 0)
            self.assertEqual(main(["--require-redistribution-ready"]), 2)

    def test_redistribution_readiness_has_a_reachable_fully_verified_transition(self) -> None:
        ready_payload = {
            "id": "verified-tool",
            "redistribution_readiness": "ready",
            "provenance": {
                "status": "verified",
                "version": "1.2.3",
                "source": "https://example.invalid/source",
                "source_evidence": "reviewed signed release metadata",
                "build_recipe": "reproducible-build.v1",
                "upstream_checksum_verified": True,
                "license": "GPL-3.0-or-later",
                "license_evidence": "reviewed upstream license at pinned source revision",
                "license_notice_in_archive": True,
            },
        }
        ready = {"status": "ready", "enforcement": "required", "blockers": []}
        _validate_readiness(ready, [ready_payload])

        falsifiers = (
            ("redistribution_readiness", "blocked-unverified", "not redistribution-ready"),
            ("status", "unknown", "provenance is not verified"),
            ("upstream_checksum_verified", False, "upstream checksum is not verified"),
            ("version", "unknown", "provenance version is not verified"),
            ("source", "unknown", "provenance source is not verified"),
            ("source_evidence", "unknown", "provenance source_evidence is not verified"),
            ("build_recipe", "unknown", "provenance build_recipe is not verified"),
            ("license", "unknown", "provenance license is not verified"),
            ("license_evidence", "unknown", "provenance license_evidence is not verified"),
            ("license_notice_in_archive", False, "does not carry its verified license notice"),
        )
        for field, value, expected_error in falsifiers:
            with self.subTest(field=field):
                payload = copy.deepcopy(ready_payload)
                if field == "redistribution_readiness":
                    payload[field] = value
                else:
                    payload["provenance"][field] = value
                with self.assertRaisesRegex(PayloadIntegrityError, expected_error):
                    _validate_readiness(ready, [payload])

    def test_full_validator_can_reach_ready_after_all_integrity_evidence_is_verified(self) -> None:
        ready_manifest = {
            "schema_version": 1,
            "integrity_policy": copy.deepcopy(self.policy),
            "redistribution_readiness": {
                "status": "ready",
                "enforcement": "required",
                "blockers": [],
            },
            "inactive_artifacts": [],
            "payloads": [
                {
                    "id": "verified-tool",
                    "path": "verified-tool.zip",
                    "consumers": [{"fixture": True}],
                    "redistribution_readiness": "ready",
                    "provenance": {
                        "status": "verified",
                        "version": "1.2.3",
                        "source": "https://example.invalid/source",
                        "source_evidence": "reviewed signed release metadata",
                        "build_recipe": "reproducible-build.v1",
                        "upstream_checksum_verified": True,
                        "license": "GPL-3.0-or-later",
                        "license_evidence": "reviewed upstream license at pinned source revision",
                        "license_notice_in_archive": True,
                    },
                }
            ],
        }
        manifest_path = self.root / "ready.json"
        manifest_path.write_text(json.dumps(ready_manifest), encoding="utf-8")
        with mock.patch(
            "tools.repo_hygiene.vendored_native_payloads._git_tracked_artifacts",
            return_value={"verified-tool.zip"},
        ), mock.patch("tools.repo_hygiene.vendored_native_payloads._validate_archive"), mock.patch(
            "tools.repo_hygiene.vendored_native_payloads._validate_consumer_claim"
        ):
            result = validate(self.root, manifest_path)
        self.assertEqual(result["integrity"], "pass")
        self.assertEqual(result["redistribution_readiness"], "ready")
        self.assertEqual(result["redistribution_enforcement"], "required")

    def test_consumer_claims_bind_reference_operation_and_gate_order(self) -> None:
        consumer_path = self.root / "consumer.yml"
        gate_marker = "verify payload integrity"
        operation_marker = "extract bundle.zip"
        consumer_path.write_text(f"{gate_marker}\n{operation_marker}\n", encoding="utf-8")
        consumer = {
            "target": "fixture",
            "path": "consumer.yml",
            "status": "active-release-workflow",
            "reference_marker": "bundle.zip",
            "operation_marker": operation_marker,
            "integrity_gate": {
                "path": "consumer.yml",
                "marker": gate_marker,
                "must_precede": operation_marker,
            },
        }
        _validate_consumer_claim(self.root, "payloads/bundle.zip", consumer, "fixture consumer")

        falsifiers = (
            ("reference_marker", "missing.zip", "exact declared archive basename"),
            ("operation_marker", "missing extract bundle.zip", "operation_marker marker must occur exactly once"),
        )
        for field, value, expected_error in falsifiers:
            with self.subTest(field=field):
                invalid = copy.deepcopy(consumer)
                invalid[field] = value
                with self.assertRaisesRegex(PayloadIntegrityError, expected_error):
                    _validate_consumer_claim(
                        self.root, "payloads/bundle.zip", invalid, "fixture consumer"
                    )

        missing_gate = copy.deepcopy(consumer)
        missing_gate.pop("integrity_gate")
        with self.assertRaisesRegex(PayloadIntegrityError, "missing integrity_gate"):
            _validate_consumer_claim(
                self.root, "payloads/bundle.zip", missing_gate, "fixture consumer"
            )

        consumer_path.write_text(f"{operation_marker}\n{gate_marker}\n", encoding="utf-8")
        with self.assertRaisesRegex(PayloadIntegrityError, "does not precede"):
            _validate_consumer_claim(self.root, "payloads/bundle.zip", consumer, "fixture consumer")

    def test_consumer_claim_rejects_cross_payload_substitution(self) -> None:
        (self.root / "consumer.yml").write_text(
            "verify payload integrity\nextract ffmpegWin64.zip\n", encoding="utf-8"
        )
        substituted = {
            "target": "windows-x86-compat",
            "path": "consumer.yml",
            "status": "active-release-workflow",
            "reference_marker": "ffmpegWin64.zip",
            "operation_marker": "extract ffmpegWin64.zip",
            "integrity_gate": {
                "path": "consumer.yml",
                "marker": "verify payload integrity",
                "must_precede": "extract ffmpegWin64.zip",
            },
        }
        with self.assertRaisesRegex(PayloadIntegrityError, "exact declared archive basename"):
            _validate_consumer_claim(
                self.root,
                "platform/qt/raw2mlv/raw2mlvWin64.zip",
                substituted,
                "raw2mlv consumer",
            )

    def test_two_hop_consumer_gate_is_bound_to_the_build_boundary(self) -> None:
        (self.root / "project.pro").write_text("unzip payload.zip\n", encoding="utf-8")
        (self.root / "release.yml").write_text("verify payload integrity\nmake -j8\n", encoding="utf-8")
        consumer = {
            "target": "fixture",
            "path": "project.pro",
            "status": "active-release-rule",
            "reference_marker": "payload.zip",
            "operation_marker": "unzip payload.zip",
            "integrity_gate": {
                "path": "release.yml",
                "marker": "verify payload integrity",
                "must_precede": "make -j8",
            },
        }
        _validate_consumer_claim(self.root, "payload.zip", consumer, "two-hop consumer")
        (self.root / "release.yml").write_text("make -j8\nverify payload integrity\n", encoding="utf-8")
        with self.assertRaisesRegex(PayloadIntegrityError, "does not precede"):
            _validate_consumer_claim(self.root, "payload.zip", consumer, "two-hop consumer")

    def test_binary_kind_header_boundaries_cover_pe_elf_and_macho(self) -> None:
        self.assertEqual(_binary_identity(_fake_pe())["kind"], "executable")
        self.assertEqual(_binary_identity(_fake_pe(dll=True))["kind"], "shared-library")

        self.assertEqual(_binary_identity(_fake_elf(elf_type=2, interp=False))["kind"], "executable")
        self.assertEqual(_binary_identity(_fake_elf(elf_type=3, interp=True))["kind"], "executable")
        self.assertEqual(_binary_identity(_fake_elf(elf_type=3, interp=False))["kind"], "shared-library")
        with self.assertRaisesRegex(PayloadIntegrityError, "unsupported ELF e_type"):
            _binary_identity(_fake_elf(elf_type=1, interp=False))

        for byte_order in ("<", ">"):
            with self.subTest(macho_byte_order=byte_order):
                self.assertEqual(
                    _binary_identity(_fake_macho(file_type=2, byte_order=byte_order))["kind"],
                    "executable",
                )
                self.assertEqual(
                    _binary_identity(_fake_macho(file_type=6, byte_order=byte_order))["kind"],
                    "shared-library",
                )
        for is_64 in (False, True):
            for byte_order in (">", "<"):
                with self.subTest(fat64=is_64, fat_byte_order=byte_order):
                    fat = {"is_64": is_64, "byte_order": byte_order}
                    identity = _binary_identity(_fake_fat_macho(**fat))
                    self.assertEqual(identity["machine"], "arm64+x86_64")
                    self.assertEqual(identity["kind"], "executable")
                    self.assertEqual(
                        _binary_identity(_fake_fat_macho(file_types=(6, 6), **fat))["kind"],
                        "shared-library",
                    )
                    with self.assertRaisesRegex(PayloadIntegrityError, "slices disagree"):
                        _binary_identity(_fake_fat_macho(file_types=(2, 6), **fat))

    def test_binary_kind_policy_is_exact_and_enforced(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["binary_kind_policy"]["elf"] = "ET_DYN is always a shared library."
        with self.assertRaisesRegex(PayloadIntegrityError, "binary_kind_policy"):
            _validate_policy(policy)

    def test_real_archives_reject_executable_library_kind_swaps(self) -> None:
        mutations = (
            ("raw2mlv-linux-x86_64", "raw2mlv", "shared-library"),
            ("raw2mlv-macos-arm64", "raw2mlv", "shared-library"),
            ("raw2mlv-windows-x86-compat", "raw2mlv.exe", "shared-library"),
            ("raw2mlv-windows-x86-compat", "libraw.dll", "executable"),
        )
        for record_id, output_name, false_kind in mutations:
            with self.subTest(record=record_id, member=output_name, false_kind=false_kind):
                record = copy.deepcopy(
                    next(item for item in self.manifest["payloads"] if item["id"] == record_id)
                )
                selected = next(
                    item for item in record["selected_members"] if item["output_name"] == output_name
                )
                selected["kind"] = false_kind
                with self.assertRaisesRegex(PayloadIntegrityError, "binary kind mismatch"):
                    _validate_archive(ROOT, record, self.policy, selected=True)

    def test_valid_fixture_passes(self) -> None:
        content = _fake_pe()
        path = self._write_zip([("tool.exe", content, stat.S_IFREG | 0o755)])
        _validate_archive(self.root, self._record(path, "tool.exe", content), self.policy, selected=True)

    def test_inventory_discovers_extensionless_native_binaries_and_archives(self) -> None:
        subprocess.run(["git", "init", "--quiet"], cwd=self.root, check=True)
        (self.root / "extensionless-tool").write_bytes(_fake_pe())
        (self.root / "extensionless-fat64").write_bytes(_fake_fat_macho(is_64=True))
        (self.root / "bundle.zip").write_bytes(b"not-yet-a-valid-archive")
        (self.root / "ordinary.txt").write_text("ordinary", encoding="utf-8")
        subprocess.run(
            ["git", "add", "extensionless-tool", "extensionless-fat64", "bundle.zip", "ordinary.txt"],
            cwd=self.root,
            check=True,
        )
        observed = _git_tracked_artifacts(self.root, self.policy["tracked_artifact_suffixes"])
        self.assertEqual(observed, {"extensionless-tool", "extensionless-fat64", "bundle.zip"})

    def test_rejects_archive_size_and_hash_drift(self) -> None:
        content = _fake_pe()
        path = self._write_zip([("tool.exe", content, stat.S_IFREG | 0o755)])
        record = self._record(path, "tool.exe", content)
        wrong_size = copy.deepcopy(record)
        wrong_size["bytes"] += 1
        with self.assertRaisesRegex(PayloadIntegrityError, "archive size mismatch"):
            _validate_archive(self.root, wrong_size, self.policy, selected=True)
        wrong_hash = copy.deepcopy(record)
        wrong_hash["sha256"] = "0" * 64
        with self.assertRaisesRegex(PayloadIntegrityError, "archive sha256 mismatch"):
            _validate_archive(self.root, wrong_hash, self.policy, selected=True)

    def test_rejects_member_summary_size_and_hash_drift(self) -> None:
        content = _fake_pe()
        path = self._write_zip([("tool.exe", content, stat.S_IFREG | 0o755)])
        record = self._record(path, "tool.exe", content)
        wrong_summary = copy.deepcopy(record)
        wrong_summary["member_summary"]["entries"] = 2
        with self.assertRaisesRegex(PayloadIntegrityError, "member_summary.entries mismatch"):
            _validate_archive(self.root, wrong_summary, self.policy, selected=True)
        wrong_size = copy.deepcopy(record)
        wrong_size["selected_members"][0]["bytes"] += 1
        with self.assertRaisesRegex(PayloadIntegrityError, "size mismatch"):
            _validate_archive(self.root, wrong_size, self.policy, selected=True)
        wrong_hash = copy.deepcopy(record)
        wrong_hash["selected_members"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(PayloadIntegrityError, "sha256 mismatch"):
            _validate_archive(self.root, wrong_hash, self.policy, selected=True)

    def test_rejects_path_traversal_and_case_collisions(self) -> None:
        content = _fake_pe()
        traversal = self._write_zip([("../tool.exe", content, stat.S_IFREG | 0o755)])
        record = self._record(traversal, "../tool.exe", content)
        with self.assertRaisesRegex(PayloadIntegrityError, "traverses a parent"):
            _validate_archive(self.root, record, self.policy, selected=True)

        collision = self._write_zip(
            [
                ("Tool.exe", content, stat.S_IFREG | 0o755),
                ("tool.exe", content, stat.S_IFREG | 0o755),
            ]
        )
        record = self._record(collision, "tool.exe", content)
        record["member_summary"] = {
            "entries": 2,
            "file_entries": 2,
            "directory_entries": 0,
            "total_uncompressed_bytes": len(content) * 2,
        }
        with self.assertRaisesRegex(PayloadIntegrityError, "case-colliding"):
            _validate_archive(self.root, record, self.policy, selected=True)

        reserved = self._write_zip([("NUL.exe", content, stat.S_IFREG | 0o755)])
        record = self._record(reserved, "NUL.exe", content)
        with self.assertRaisesRegex(PayloadIntegrityError, "reserved device name"):
            _validate_archive(self.root, record, self.policy, selected=True)

    def test_rejects_symlinks_and_wrong_binary_architecture(self) -> None:
        content = _fake_pe()
        symlink = self._write_zip([("tool.exe", b"target", stat.S_IFLNK | 0o777)])
        record = self._record(symlink, "tool.exe", b"target")
        with self.assertRaisesRegex(PayloadIntegrityError, "forbidden symlink"):
            _validate_archive(self.root, record, self.policy, selected=True)

        path = self._write_zip([("tool.exe", content, stat.S_IFREG | 0o755)])
        record = self._record(path, "tool.exe", content)
        record["selected_members"][0]["binary"]["machine"] = "arm64"
        with self.assertRaisesRegex(PayloadIntegrityError, "binary identity mismatch"):
            _validate_archive(self.root, record, self.policy, selected=True)

    def test_rejects_archive_member_and_expansion_ceilings(self) -> None:
        content = _fake_pe()
        path = self._write_zip([("tool.exe", content, stat.S_IFREG | 0o755)])
        record = self._record(path, "tool.exe", content)

        archive_policy = copy.deepcopy(self.policy)
        archive_policy["max_archive_bytes"] = record["bytes"] - 1
        with self.assertRaisesRegex(PayloadIntegrityError, "declared archive bytes exceed"):
            _validate_archive(self.root, record, archive_policy, selected=True)

        member_policy = copy.deepcopy(self.policy)
        member_policy["max_members"] = 0
        with self.assertRaisesRegex(PayloadIntegrityError, "exceeds max_members"):
            _validate_archive(self.root, record, member_policy, selected=True)

        single_policy = copy.deepcopy(self.policy)
        single_policy["max_single_member_bytes"] = len(content) - 1
        with self.assertRaisesRegex(PayloadIntegrityError, "exceeds max_single_member_bytes"):
            _validate_archive(self.root, record, single_policy, selected=True)

        total_policy = copy.deepcopy(self.policy)
        total_policy["max_total_uncompressed_bytes"] = len(content) - 1
        with self.assertRaisesRegex(PayloadIntegrityError, "exceeds max_total_uncompressed_bytes"):
            _validate_archive(self.root, record, total_policy, selected=True)

        compressed_content = _fake_pe(fill=b"\0" * 200) + b"\0" * 20000
        compressed = self._write_zip(
            [("tool.exe", compressed_content, stat.S_IFREG | 0o755)],
            compression=zipfile.ZIP_DEFLATED,
        )
        compressed_record = self._record(compressed, "tool.exe", compressed_content)
        expansion_policy = copy.deepcopy(self.policy)
        expansion_policy["max_archive_expansion_ratio"] = 1000
        expansion_policy["max_member_expansion_ratio"] = 2
        with self.assertRaisesRegex(PayloadIntegrityError, "exceeds max_member_expansion_ratio"):
            _validate_archive(self.root, compressed_record, expansion_policy, selected=True)

    def test_release_workflows_validate_before_any_payload_extraction(self) -> None:
        command = "python -m tools.repo_hygiene.vendored_native_payloads --repo-root ."
        self.assertEqual((ROOT / ".python-version").read_text(encoding="utf-8").strip(), "3.13.15")
        workflow_expectations = {
            ".github/workflows/Windows.yml": (command, "7z x"),
            ".github/workflows/Linux.yml": (command, "tar -C"),
            ".github/workflows/macOS-Intel.yml": (command, "make -j8"),
            ".github/workflows/macOS-Arm64.yml": (command, "make -j8"),
        }
        for relative, (command, boundary) in workflow_expectations.items():
            with self.subTest(workflow=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertEqual(text.count(command), 1)
                self.assertIn("actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1", text)
                self.assertLess(text.index("actions/setup-python@"), text.index(command))
                self.assertEqual(text.count('python-version-file: ".python-version"'), 1)
                self.assertNotRegex(text, r"(?m)^\s+python-version:\s*")
                for lock_path in (".github/requirements/pip.txt", ".github/requirements/repo-hygiene.txt"):
                    install = (
                        "python -m pip install --disable-pip-version-check --no-input "
                        f"--only-binary=:all: --require-hashes -r {lock_path}"
                    )
                    self.assertEqual(text.count(install), 1)
                    self.assertLess(text.index(install), text.index(command))
                self.assertEqual(text.count("python -m pip check"), 1)
                self.assertLess(text.index(command), text.index(boundary))

        tests_workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        self.assertIn("python -m unittest tools.repo_hygiene.test_vendored_native_payloads -v", tests_workflow)
        self.assertIn("python -m tools.repo_hygiene.vendored_native_payloads --repo-root .", tests_workflow)


if __name__ == "__main__":
    unittest.main()
