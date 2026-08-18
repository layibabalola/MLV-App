from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .release_evidence import (
    BUILD_INFO_SCHEMA,
    CONTENTS_SCHEMA,
    EvidenceError,
    _canonical_bytes,
    _inspect_executable_architecture,
    _tool_record,
    _validate_member_names,
    generate_evidence,
    inventory_document,
    inventory_path,
)


ROOT = Path(__file__).resolve().parents[2]


def _pe(machine: int = 0x8664) -> bytes:
    value = bytearray(256)
    value[:2] = b"MZ"
    value[0x3C:0x40] = (128).to_bytes(4, "little")
    value[128:132] = b"PE\0\0"
    value[132:134] = machine.to_bytes(2, "little")
    return bytes(value)


def _elf(machine: int = 62) -> bytes:
    value = bytearray(64)
    value[:6] = b"\x7fELF\x02\x01"
    value[18:20] = machine.to_bytes(2, "little")
    return bytes(value)


def _macho(cpu_type: int = 0x0100000C) -> bytes:
    return b"\xcf\xfa\xed\xfe" + cpu_type.to_bytes(4, "little") + bytes(24)


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        manifest = {
            "schema_version": 1,
            "redistribution_readiness": {
                "status": "blocked",
                "enforcement": "advisory",
                "blockers": ["license evidence incomplete", "source recipe unknown"],
            },
        }
        manifest_path = self.repo / "tools" / "gates" / "vendored-native-payloads.json"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.tool_dir = self.repo / "fixture-tools"
        self.tool_dir.mkdir()
        for name, content in {
            "qmake": b"qmake-tool",
            "compiler": b"compiler-tool",
            "windeployqt": b"windeployqt-tool",
            "linuxdeploy": b"linuxdeploy-tool",
            "linuxdeploy-plugin-qt": b"linuxdeploy-qt-plugin-tool",
            "linuxdeploy-plugin-appimage": b"linuxdeploy-appimage-plugin-tool",
            "macdeployqt": b"macdeployqt-tool",
        }.items():
            (self.tool_dir / name).write_bytes(content)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _directory_product(self) -> Path:
        product = self.repo / "build" / "MLVApp"
        (product / "plugins").mkdir(parents=True)
        (product / "MLVApp.exe").write_bytes(_pe())
        (product / "plugins" / "image.dll").write_bytes(b"plugin")
        return product

    def _generate(self, **overrides: object) -> tuple[Path, Path]:
        product = overrides.pop("product", None)
        if product is None:
            product = self._directory_product()
        values = {
            "repo_root": self.repo,
            "product": product,
            "inventory_root": product,
            "expected_main": "MLVApp.exe",
            "output_dir": self.root / "evidence",
            "label": "windows-x86_64",
            "target_os": "windows",
            "target_arch": "x86_64",
            "repository": "owner/repo",
            "source_ref": "refs/heads/main",
            "commit": "a" * 40,
            "run_id": "123",
            "run_attempt": "2",
            "runner_os": "Windows",
            "runner_arch": "X64",
            "runner_name": "GitHub Actions 1",
            "runner_image_os": "win25",
            "runner_image_version": "20260817.1",
            "tools": [
                f"qmake={self.tool_dir / 'qmake'}",
                f"compiler={self.tool_dir / 'compiler'}",
                f"windeployqt={self.tool_dir / 'windeployqt'}",
            ],
            "tool_versions": [
                "qmake=5.15.2",
                "compiler=mingw-8.1",
                "windeployqt=5.15.2",
            ],
        }
        values.update(overrides)
        return generate_evidence(**values)  # type: ignore[arg-type]

    def test_directory_inventory_is_sorted_and_digest_is_canonical(self) -> None:
        product = self._directory_product()
        inventory = inventory_path(product, logical_name="MLVApp")
        self.assertEqual([row["path"] for row in inventory["files"]], ["MLVApp.exe", "plugins/image.dll"])
        document = inventory_document(inventory)
        self.assertEqual(document["schema"], CONTENTS_SCHEMA)
        self.assertEqual(
            document["inventory_sha256"],
            hashlib.sha256(_canonical_bytes(inventory)).hexdigest(),
        )

    def test_generation_is_deterministic_and_records_only_single_build_observation(self) -> None:
        product = self._directory_product()
        first = self._generate(product=product, output_dir=self.root / "first")
        second = self._generate(product=product, output_dir=self.root / "second")
        self.assertEqual(first[0].read_bytes(), second[0].read_bytes())
        self.assertEqual(first[1].read_bytes(), second[1].read_bytes())
        build_info = json.loads(first[1].read_text(encoding="utf-8"))
        self.assertEqual(build_info["schema"], BUILD_INFO_SCHEMA)
        self.assertEqual(build_info["assurance"]["observation_scope"], "single-build-observation")
        self.assertFalse(build_info["assurance"]["reproducibility_claim"])
        self.assertFalse(build_info["assurance"]["redistribution_ready"])
        self.assertEqual(build_info["assurance"]["blockers"], ["license evidence incomplete", "source recipe unknown"])
        self.assertEqual(build_info["contents"]["count"], 2)
        self.assertEqual(build_info["contents"]["expected_main_executable"], "MLVApp.exe")
        self.assertEqual(
            build_info["contents"]["manifest_sha256"],
            hashlib.sha256(first[0].read_bytes()).hexdigest(),
        )
        self.assertEqual(
            build_info["contents"]["inventory_sha256"],
            json.loads(first[0].read_text(encoding="utf-8"))["inventory_sha256"],
        )
        self.assertEqual(build_info["product"]["kind"], "directory")
        self.assertEqual(build_info["product"]["hash_kind"], "sha256-canonical-file-manifest")
        self.assertEqual(list(build_info["build"]["tools"]), ["compiler", "python", "qmake", "windeployqt"])
        self.assertRegex(build_info["build"]["tools"]["compiler"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(build_info["build"]["tools"]["qmake"]["version"], "5.15.2")
        self.assertEqual(
            build_info["build"]["runner"],
            {
                "arch": "X64",
                "image_os": "win25",
                "image_version": "20260817.1",
                "name": "GitHub Actions 1",
                "os": "Windows",
            },
        )
        self.assertEqual(build_info["contents"]["main_executable"]["architectures"], ["x86_64"])
        self.assertEqual(build_info["contents"]["main_executable"]["format"], "pe")

    def test_file_product_and_separate_staging_inventory_are_bound(self) -> None:
        staging = self.repo / "build" / "image"
        (staging / "usr" / "bin").mkdir(parents=True)
        (staging / "usr" / "bin" / "mlvapp").write_bytes(_elf())
        os.chmod(staging / "usr" / "bin" / "mlvapp", 0o755)
        product = self.repo / "build" / "MLVApp.AppImage"
        product.write_bytes(b"appimage")
        _, build_info_path = self._generate(
            product=product,
            inventory_root=staging,
            expected_main="usr/bin/mlvapp",
            label="linux-x86_64",
            target_os="linux",
            output_dir=self.root / "linux-evidence",
            tools=[
                f"qmake={self.tool_dir / 'qmake'}",
                f"compiler={self.tool_dir / 'compiler'}",
                f"linuxdeploy={self.tool_dir / 'linuxdeploy'}",
                f"linuxdeploy-plugin-qt={self.tool_dir / 'linuxdeploy-plugin-qt'}",
                f"linuxdeploy-plugin-appimage={self.tool_dir / 'linuxdeploy-plugin-appimage'}",
            ],
            tool_versions=[
                "qmake=5.15.2",
                "compiler=12.3",
                "linuxdeploy=1-alpha",
                "linuxdeploy-plugin-qt=1-alpha",
                "linuxdeploy-plugin-appimage=1-alpha",
            ],
        )
        build_info = json.loads(build_info_path.read_text(encoding="utf-8"))
        self.assertEqual(build_info["product"]["kind"], "file")
        self.assertEqual(build_info["product"]["size"], len(b"appimage"))
        self.assertEqual(build_info["product"]["sha256"], hashlib.sha256(b"appimage").hexdigest())
        self.assertEqual(build_info["contents"]["expected_main_executable"], "usr/bin/mlvapp")
        self.assertEqual(build_info["contents"]["main_executable"]["format"], "elf")

    def test_release_target_product_shapes_fail_closed(self) -> None:
        directory = self._directory_product()
        appimage = self.repo / "build" / "MLVApp.AppImage"
        appimage.write_bytes(b"appimage")
        with self.assertRaisesRegex(EvidenceError, "linux evidence requires"):
            self._generate(product=directory, target_os="linux")
        with self.assertRaisesRegex(EvidenceError, "macos evidence requires"):
            self._generate(product=appimage, target_os="macos", target_arch="arm64")
        with self.assertRaisesRegex(EvidenceError, "unsupported release target"):
            self._generate(product=directory, target_os="plan9")

    def test_mislabeled_main_executable_architecture_fails_closed(self) -> None:
        product = self._directory_product()
        (product / "MLVApp.exe").write_bytes(_pe(0xAA64))
        with self.assertRaisesRegex(EvidenceError, "architecture mismatch.*declared x86_64"):
            self._generate(product=product)

    def test_macho_architecture_inspection_covers_arm64_and_x86_64(self) -> None:
        binary = self.repo / "build" / "MLV App"
        binary.parent.mkdir(parents=True, exist_ok=True)
        for cpu_type, expected in ((0x0100000C, "arm64"), (0x01000007, "x86_64")):
            binary.write_bytes(_macho(cpu_type))
            with self.subTest(expected=expected):
                observed = _inspect_executable_architecture(binary)
                self.assertEqual(observed["format"], "macho")
                self.assertEqual(observed["architectures"], [expected])

    def test_same_tool_version_with_different_bytes_changes_identity_hash(self) -> None:
        product = self._directory_product()
        _, first_path = self._generate(product=product, output_dir=self.root / "tool-first")
        first = json.loads(first_path.read_text(encoding="utf-8"))
        (self.tool_dir / "compiler").write_bytes(b"different-compiler-bytes")
        _, second_path = self._generate(product=product, output_dir=self.root / "tool-second")
        second = json.loads(second_path.read_text(encoding="utf-8"))
        self.assertEqual(
            first["build"]["tools"]["compiler"]["version"],
            second["build"]["tools"]["compiler"]["version"],
        )
        self.assertNotEqual(
            first["build"]["tools"]["compiler"]["sha256"],
            second["build"]["tools"]["compiler"]["sha256"],
        )

    def test_missing_tool_or_version_and_empty_tool_fail_closed(self) -> None:
        product = self._directory_product()
        with self.assertRaisesRegex(EvidenceError, "same tools"):
            self._generate(product=product, tool_versions=["qmake=5", "compiler=8"])
        (self.tool_dir / "compiler").write_bytes(b"")
        with self.assertRaisesRegex(EvidenceError, "invalid SHA-256 evidence"):
            self._generate(product=product)
        (self.tool_dir / "compiler").write_bytes(b"compiler")
        with mock.patch(
            "tools.release.release_evidence._read_file_snapshot",
            return_value=(8, "not-a-sha", None),
        ):
            with self.assertRaisesRegex(EvidenceError, "invalid SHA-256 evidence"):
                _tool_record(str(self.tool_dir / "compiler"), "8.1", "compiler")

    def test_empty_directory_and_empty_file_fail_closed(self) -> None:
        empty_directory = self.repo / "empty"
        empty_directory.mkdir()
        with self.assertRaisesRegex(EvidenceError, "contains no files"):
            inventory_path(empty_directory, logical_name="empty")
        empty_file = self.repo / "empty.bin"
        empty_file.touch()
        with self.assertRaisesRegex(EvidenceError, "is empty"):
            inventory_path(empty_file, logical_name="empty.bin")

    def test_missing_expected_main_fails_closed(self) -> None:
        product = self._directory_product()
        with self.assertRaisesRegex(EvidenceError, "expected main executable is missing"):
            self._generate(product=product, expected_main="missing.exe")

    def test_non_windows_main_without_executable_mode_fails_closed(self) -> None:
        staging = self._directory_product()
        product = self.repo / "build" / "MLVApp.AppImage"
        product.write_bytes(b"appimage")
        with mock.patch("tools.release.release_evidence.os.access", return_value=False):
            with self.assertRaisesRegex(EvidenceError, "lacks an executable mode"):
                self._generate(
                    product=product,
                    inventory_root=staging,
                    expected_main="MLVApp.exe",
                    target_os="linux",
                    tools=[
                        f"qmake={self.tool_dir / 'qmake'}",
                        f"compiler={self.tool_dir / 'compiler'}",
                        f"linuxdeploy={self.tool_dir / 'linuxdeploy'}",
                        f"linuxdeploy-plugin-qt={self.tool_dir / 'linuxdeploy-plugin-qt'}",
                        f"linuxdeploy-plugin-appimage={self.tool_dir / 'linuxdeploy-plugin-appimage'}",
                    ],
                    tool_versions=[
                        "qmake=5",
                        "compiler=8",
                        "linuxdeploy=1",
                        "linuxdeploy-plugin-qt=1",
                        "linuxdeploy-plugin-appimage=1",
                    ],
                )

    def test_traversal_and_absolute_expected_main_fail_closed(self) -> None:
        product = self._directory_product()
        for value in ("../MLVApp.exe", "/MLVApp.exe", "C:\\MLVApp.exe"):
            with self.subTest(value=value), self.assertRaisesRegex(
                EvidenceError, "relative|traversal|canonical"
            ):
                self._generate(product=product, expected_main=value)

    def test_casefold_and_unicode_normalization_collisions_fail_closed(self) -> None:
        with self.assertRaisesRegex(EvidenceError, "case-folded"):
            _validate_member_names(["Plugin.dll", "plugin.dll"])
        with self.assertRaisesRegex(EvidenceError, "Unicode-normalized"):
            _validate_member_names(["caf\N{LATIN SMALL LETTER E WITH ACUTE}.txt", "cafe\N{COMBINING ACUTE ACCENT}.txt"])
        with self.assertRaisesRegex(EvidenceError, "canonical '/' separators"):
            _validate_member_names(["a\\b", "a/b"])

    def test_link_or_junction_in_path_ancestor_fails_closed(self) -> None:
        product = self._directory_product()
        redirected_ancestor = self.repo / "tools" / "gates"
        from . import release_evidence

        original = release_evidence._is_link_or_junction

        def pretend_ancestor_link(path: Path) -> bool:
            return path == redirected_ancestor or original(path)

        with mock.patch(
            "tools.release.release_evidence._is_link_or_junction",
            side_effect=pretend_ancestor_link,
        ):
            with self.assertRaisesRegex(EvidenceError, "symbolic-link or junction ancestor"):
                self._generate(product=product)

    def test_symbolic_link_member_fails_closed(self) -> None:
        product = self._directory_product()
        linked = product / "linked.dll"
        linked.write_bytes(b"placeholder")
        original = Path.is_symlink

        def pretend_link(path: Path) -> bool:
            return path == linked or original(path)

        with mock.patch("pathlib.Path.is_symlink", autospec=True, side_effect=pretend_link):
            with self.assertRaisesRegex(EvidenceError, "symbolic link or junction"):
                inventory_path(product, logical_name="MLVApp")

    def test_output_inside_inventory_is_rejected(self) -> None:
        product = self._directory_product()
        with self.assertRaisesRegex(EvidenceError, "must not be inside"):
            self._generate(product=product, output_dir=product / "evidence")

    def test_product_outside_repository_is_rejected(self) -> None:
        outside = self.root / "outside-product"
        outside.mkdir()
        (outside / "MLVApp.exe").write_bytes(b"exe")
        with self.assertRaisesRegex(EvidenceError, "resolves outside the repository root"):
            self._generate(product=outside, inventory_root=outside)

    def test_invalid_commit_and_duplicate_tool_fail_closed(self) -> None:
        product = self._directory_product()
        with self.assertRaisesRegex(EvidenceError, "full 40-character"):
            self._generate(product=product, commit="deadbeef")
        with self.assertRaisesRegex(EvidenceError, "duplicate tool path"):
            self._generate(product=product, tools=["qmake=a", "qmake=b"])

    def test_nonblocked_or_blockerless_vendored_readiness_fails_closed(self) -> None:
        product = self._directory_product()
        manifest_path = self.repo / "tools" / "gates" / "vendored-native-payloads.json"
        for readiness, message in (
            ({"status": "ready", "blockers": []}, "requires vendored redistribution status"),
            ({"status": "blocked", "blockers": []}, "requires non-empty textual blockers"),
        ):
            with self.subTest(readiness=readiness):
                manifest_path.write_text(
                    json.dumps({"schema_version": 1, "redistribution_readiness": readiness}),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(EvidenceError, message):
                    self._generate(product=product)

    def test_contract_remains_explicitly_non_authorizing(self) -> None:
        contract = json.loads(
            (ROOT / "tools" / "release" / "release-evidence-contract.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["scope"], "single-build-observation")
        self.assertFalse(contract["assurance_limits"]["redistribution_ready"])
        self.assertFalse(contract["assurance_limits"]["reproducibility_claim"])
        self.assertEqual(set(contract["workflow_targets"]), {"linux", "macos-arm64", "macos-x86_64", "windows"})


class ReleaseWorkflowEvidenceTests(unittest.TestCase):
    WORKFLOWS = {
        "Windows.yml": ("MLVApp Windows 64bit", "MLVApp Windows 64bit release evidence", "MLVApp.exe"),
        "Linux.yml": ("MLVApp", "MLVApp Linux release evidence", "usr/bin/mlvapp"),
        "macOS-Intel.yml": ("MLVApp macOS Intel", "MLVApp macOS Intel release evidence", "Contents/MacOS/MLV App"),
        "macOS-Arm64.yml": ("MLVApp macOS Arm64", "MLVApp macOS Arm64 release evidence", "Contents/MacOS/MLV App"),
    }

    def test_all_release_workflows_generate_evidence_after_packaging_before_upload(self) -> None:
        for filename, (product_name, evidence_name, expected_main) in self.WORKFLOWS.items():
            with self.subTest(workflow=filename):
                text = (ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8")
                evidence_index = text.index("- name: Generate single-build release evidence")
                evidence_upload_index = text.index("- name: Save release evidence")
                product_upload_index = text.index("- name: Save build artifact")
                self.assertLess(evidence_index, evidence_upload_index)
                self.assertLess(evidence_upload_index, product_upload_index)
                self.assertIn("python -m tools.release.release_evidence", text[evidence_index:evidence_upload_index])
                self.assertIn(f'--expected-main "{expected_main}"', text[evidence_index:evidence_upload_index])
                self.assertIn("${{ github.repository }}", text[evidence_index:evidence_upload_index])
                self.assertIn("${{ github.ref }}", text[evidence_index:evidence_upload_index])
                self.assertIn("${{ github.sha }}", text[evidence_index:evidence_upload_index])
                self.assertIn("${{ github.run_id }}", text[evidence_index:evidence_upload_index])
                self.assertIn("${{ github.run_attempt }}", text[evidence_index:evidence_upload_index])
                self.assertIn("${{ runner.os }}", text[evidence_index:evidence_upload_index])
                self.assertIn("${{ runner.arch }}", text[evidence_index:evidence_upload_index])
                self.assertIn("${{ runner.name }}", text[evidence_index:evidence_upload_index])
                self.assertIn("ImageOS", text[evidence_index:evidence_upload_index])
                self.assertIn("ImageVersion", text[evidence_index:evidence_upload_index])
                expected_tool_count = 5 if filename == "Linux.yml" else 3
                self.assertEqual(
                    text[evidence_index:evidence_upload_index].count("--tool \""),
                    expected_tool_count,
                )
                self.assertEqual(
                    text[evidence_index:evidence_upload_index].count("--tool-version \""),
                    expected_tool_count,
                )
                for tool_name in ("qmake", "compiler"):
                    self.assertIn(f'--tool "{tool_name}=', text[evidence_index:evidence_upload_index])
                    self.assertIn(f'--tool-version "{tool_name}=', text[evidence_index:evidence_upload_index])
                self.assertIn(f"name: {evidence_name}", text[evidence_upload_index:product_upload_index])
                self.assertIn("if-no-files-found: error", text[evidence_upload_index:product_upload_index])
                self.assertIn(f"name: {product_name}", text[product_upload_index:])
                self.assertIn("if-no-files-found: error", text[product_upload_index:])
                self.assertEqual(text.count("permissions:"), 1)
                self.assertIn("permissions:\n  contents: read", text)


if __name__ == "__main__":
    unittest.main()
