import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from .closeout import (
    approve_transaction,
    evaluate_closeout_triggers,
    open_transaction,
    record_agent_review,
    record_codex_recommendation,
    sign_closeout_payload,
    transaction_status,
    tx_hash,
    validate_transaction_apply,
)
from .core import (
    IMPLEMENTED_CANDIDATE_KINDS,
    IMPLEMENTED_CLOSEOUT_ACTION_IDS,
    IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS,
    IMPLEMENTED_CLOSEOUT_PUBLISH_MODES,
    IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS,
    IMPLEMENTED_DASHBOARD_ACTION_IDS,
    IMPLEMENTED_RISK_TIERS,
    HygieneError,
    dirty_recommendation,
    is_reparse_point,
    load_config,
    run_apply,
    run_scan,
    stable_id,
    verify_policy,
)


ROOT = Path(__file__).resolve().parents[2]
CODEQL_STORAGE_MODEL_DIR = (
    ROOT / ".github" / "codeql" / "extensions" / "agent-bridge-storage-python"
)
EXPECTED_CODEQL_STORAGE_PACK = {
    "name": "layibabalola/agent-bridge-storage-python-models",
    "version": "0.0.0",
    "library": True,
    "extensionTargets": {"codeql/python-all": "*"},
    "dataExtensions": ["models/**/*.yml"],
}
EXPECTED_CODEQL_STORAGE_MODEL = {
    "extensions": [
        {
            "addsTo": {
                "pack": "codeql/python-all",
                "extensible": "barrierModel",
            },
            "data": [
                [
                    "core.storage.StorageCapability",
                    "Member[validate].ReturnValue",
                    "path-injection",
                ]
            ],
        }
    ]
}

PYTHON_LOCK_ROOTS = {
    ".github/requirements/pip.txt": {"pip"},
    ".github/requirements/lock-tools.txt": {"pip", "pip-tools"},
    ".github/requirements/repo-hygiene.txt": {"jsonschema"},
    ".github/requirements/aqtinstall.txt": {"aqtinstall"},
    "tools/agent-bridge/requirements.txt": {
        "hypothesis",
        "mcp",
        "psutil",
        "websockets",
    },
    "tools/agent-bridge/requirements-test.txt": {
        "hypothesis",
        "mcp",
        "psutil",
        "pytest",
        "websockets",
    },
}


def parse_hashed_requirement_lock(path: Path) -> dict[str, str]:
    """Return normalized package pins after enforcing the lock-file grammar."""
    logical_lines: list[str] = []
    current = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        continued = stripped.endswith("\\")
        fragment = stripped[:-1].strip() if continued else stripped
        current = f"{current} {fragment}".strip()
        if not continued:
            logical_lines.append(current)
            current = ""
    if current:
        raise AssertionError(f"unterminated continuation in {path}")

    pins: dict[str, str] = {}
    for entry in logical_lines:
        if re.search(r"(?:^|\s)(?:-e|-[rci]|--index-url|--extra-index-url|--trusted-host)(?:\s|=)", entry):
            raise AssertionError(f"forbidden requirement option in {path}: {entry}")
        if " @ " in entry or "://" in entry:
            raise AssertionError(f"direct URL or VCS requirement in {path}: {entry}")
        pin = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;]+)(?:\s*;[^\s]+(?:\s+[^\s]+)*)?\s+", entry)
        if pin is None:
            raise AssertionError(f"non-exact requirement in {path}: {entry}")
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})(?:\s|$)", entry)
        if not hashes:
            raise AssertionError(f"unhashed requirement in {path}: {entry}")
        without_hashes = re.sub(r"\s*--hash=sha256:[0-9a-f]{64}", "", entry).strip()
        if without_hashes != entry[: pin.end()].strip():
            raise AssertionError(f"unexpected lock token in {path}: {entry}")
        normalized_name = re.sub(r"[-_.]+", "-", pin.group(1)).lower()
        if normalized_name in pins:
            raise AssertionError(f"duplicate package pin in {path}: {normalized_name}")
        pins[normalized_name] = pin.group(2)
    if not pins:
        raise AssertionError(f"empty Python dependency lock: {path}")
    return pins


def assert_exact_codeql_storage_model(test_case: unittest.TestCase, model: dict) -> None:
    """Keep the CodeQL exception at the reviewed capability return boundary."""
    test_case.assertEqual(model, EXPECTED_CODEQL_STORAGE_MODEL)


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result


class RepoHygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = Path(tempfile.mkdtemp(prefix="repo-hygiene-test-"))
        self.repo_counter = 0

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def init_repo(self) -> Path:
        self.repo_counter += 1
        repo = self.tempdir / ("repo" if self.repo_counter == 1 else f"repo-{self.repo_counter}")
        repo.mkdir()
        git(repo, "init", "-b", "master")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "Test User")
        (repo / "README.md").write_text("hello\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "\n".join(
                [
                    ".claude-state/",
                    ".claude/worktrees/",
                    ".hypothesis/",
                    "**/__pycache__/",
                    "*.pyc",
                    "monitor-probe.runtime.json",
                    "platform/qt/FFmpeg/ffmpeg.exe",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        git(repo, "add", "README.md", ".gitignore")
        git(repo, "commit", "-m", "initial")
        policy_dir = repo / "tools" / "repo-hygiene"
        policy_dir.mkdir(parents=True)
        shutil.copy(ROOT / "tools" / "repo-hygiene" / "hygiene.config.json", policy_dir / "hygiene.config.json")
        shutil.copy(ROOT / "tools" / "repo-hygiene" / "POLICY.md", policy_dir / "POLICY.md")
        shutil.copy(ROOT / "tools" / "repo-hygiene" / "closeout.contract.json", policy_dir / "closeout.contract.json")
        return repo

    def signed(self, tx: dict, artifact_type: str, payload: dict, actor_id: str = "codex-test") -> dict:
        payload = json.loads(json.dumps(payload))
        payload["provenance"] = {
            "artifact_type": artifact_type,
            "actor_id": actor_id,
            "session_id": "test-session",
            "adapter_id": "unit-test",
            "key_hash": tx["state"]["provenance_key_hashes"][artifact_type],
            "signature": "",
        }
        payload["provenance"]["signature"] = sign_closeout_payload(
            tx["tx_id"],
            artifact_type,
            payload,
            tx["trusted_provenance_keys"][artifact_type],
        )
        return payload

    def record_recommendation(self, repo: Path, tx: dict, payload: dict) -> dict:
        return record_codex_recommendation(
            repo,
            tx["tx_id"],
            self.signed(tx, "codex_recommendation", payload),
            provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
        )

    def record_review(self, repo: Path, tx: dict, payload: dict, actor_id: str = "reviewer-test") -> dict:
        return record_agent_review(
            repo,
            tx["tx_id"],
            self.signed(tx, "agent_review", payload, actor_id=actor_id),
            provenance_key=tx["trusted_provenance_keys"]["agent_review"],
        )

    def approve_closeout(self, repo: Path, tx: dict, payload: dict) -> dict:
        return approve_transaction(
            repo,
            tx["tx_id"],
            self.signed(tx, "approval", payload, actor_id="approver-test"),
            tx["trusted_approval_nonce"],
            provenance_key=tx["trusted_provenance_keys"]["approval"],
            recommendation_provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
            review_provenance_key=tx["trusted_provenance_keys"]["agent_review"],
        )

    def validate_apply(self, repo: Path, tx: dict) -> dict:
        return validate_transaction_apply(
            repo,
            tx["tx_id"],
            approval_provenance_key=tx["trusted_provenance_keys"]["approval"],
        )

    def test_policy_verifier_passes_for_repo_config_docs_and_tests(self) -> None:
        result = verify_policy(ROOT)
        self.assertTrue(result["ok"], result["failures"])
        self.assertIn("policy_hash", result)
        for tier in IMPLEMENTED_RISK_TIERS:
            self.assertIn(tier, load_config(ROOT)["portability"]["risk_tiers"])
        for kind in IMPLEMENTED_CANDIDATE_KINDS:
            self.assertIn(kind, load_config(ROOT)["portability"]["candidate_kinds"])
        for kind in IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS:
            self.assertIn(kind, load_config(ROOT)["portability"]["closeout_candidate_kinds"])
        for action in IMPLEMENTED_CLOSEOUT_ACTION_IDS:
            self.assertIn(action, load_config(ROOT)["portability"]["closeout_action_ids"])
        for mode in IMPLEMENTED_CLOSEOUT_PUBLISH_MODES:
            self.assertIn(mode, load_config(ROOT)["portability"]["closeout_publish_modes"])
            self.assertIn(mode, load_config(ROOT)["closeout"]["publish_modes"])
        self.assertIn("local_merge_only", IMPLEMENTED_CLOSEOUT_PUBLISH_MODES)
        self.assertIn("no_publish", IMPLEMENTED_CLOSEOUT_PUBLISH_MODES)
        for signal in IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS:
            self.assertIn(signal, load_config(ROOT)["portability"]["closeout_trigger_signal_ids"])
        self.assertIn("repo-sweep-retained-blocker", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("detached-dirty-worktree", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("protected-worktree-cleanup", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("remote-feature-branch", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("foreign_dirty_integrated_branch_prune", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("detached_dirty_preserve", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("explicit_protected_worktree_cleanup", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("resolve_conflicts_with_agent", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("agent_remediation_surface_unavailable", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("protected-target-noop-closeout", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("evidence_preserving_prune_recovery", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("delete_remote_branch", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("remote_feature_clean_integrate", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("remote_feature_prune", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("retained_blocker_auto_remediation", IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS)
        self.assertIn("agent_remediation_queue_consumer", IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS)
        self.assertIn("protected_target_noop_closeout", IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS)
        for action in IMPLEMENTED_DASHBOARD_ACTION_IDS:
            self.assertIn(action, load_config(ROOT)["portability"]["dashboard_action_ids"])

        self.assertTrue(load_config(ROOT)["closeout"]["auto_trigger"]["enabled"])
        self.assertFalse(load_config(ROOT)["closeout"]["allow_review_waiver"])
        self.assertIn("codex_desktop", load_config(ROOT)["closeout"]["trusted_approval_sources"])
        self.assertIn("codex_background_agent", load_config(ROOT)["closeout"]["allowed_review_sources"])
        contract = json.loads((ROOT / "tools" / "repo-hygiene" / "closeout.contract.json").read_text(encoding="utf-8"))
        self.assertIn("executor-handoff.json", contract["artifact_names"])
        self.assertIn("closeout-compare-result.json", contract["artifact_names"])
        self.assertIn("closeout-compare-result.schema.json", contract["artifact_names"])
        self.assertIn("agent-remediation-queue/*.json", contract["artifact_names"])
        self.assertIn("agent-remediation-results/*.json", contract["artifact_names"])
        self.assertIn("manual-prune/*.json", contract["artifact_names"])
        self.assertIn("manual-prune/*.bundle", contract["artifact_names"])
        self.assertTrue(contract["requires_signed_provenance"])
        self.assertTrue(contract["role_specific_provenance_keys"])
        self.assertEqual(contract["cli_secret_transport"], "environment")
        registry = load_config(ROOT)["root_registry"]
        for required in [".claude", ".claude-state", ".claude/worktrees", "tools/repo_hygiene", "tools/repo-hygiene"]:
            self.assertIn(required, registry)
        self.assertIn("osx_installer/BuildInstaller.sh", load_config(ROOT)["tracked_ignored_allowlist"])
        self.assertIn("tools/gpu/build-cuda.ps1", load_config(ROOT)["tracked_ignored_allowlist"])
        self.assertIn(".claude-state/probe.tmp", load_config(ROOT)["required_ignore_samples"]["must_be_ignored"])

    def test_codeql_storage_model_pack_schema_and_barrier_are_exact(self) -> None:
        """Default setup may trust only StorageCapability.validate's return value."""
        manifest_path = CODEQL_STORAGE_MODEL_DIR / "codeql-pack.yml"
        model_path = CODEQL_STORAGE_MODEL_DIR / "models" / "storage.model.yml"
        self.assertTrue(manifest_path.is_file())
        self.assertTrue(model_path.is_file())

        # JSON is a strict subset of YAML, so CodeQL accepts these model-pack
        # files while this cross-platform CI guard needs no YAML dependency.
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        model = json.loads(model_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest, EXPECTED_CODEQL_STORAGE_PACK)
        assert_exact_codeql_storage_model(self, model)

        discovered_models = [
            path.relative_to(CODEQL_STORAGE_MODEL_DIR).as_posix()
            for path in CODEQL_STORAGE_MODEL_DIR.glob("models/**/*.yml")
        ]
        self.assertEqual(discovered_models, ["models/storage.model.yml"])

    def test_codeql_storage_model_rejects_broader_path_barriers(self) -> None:
        """Path/root constructors and generic canonicalizers are not sanitizers."""
        broader_barriers = [
            ["pathlib.Path", "ReturnValue", "path-injection"],
            [
                "core.storage.StorageCapability",
                "Member[bind_trusted].ReturnValue",
                "path-injection",
            ],
            [
                "core.storage",
                "Member[authorize_storage_root].ReturnValue",
                "path-injection",
            ],
            [
                "core.storage",
                "Member[_canonical_absolute_path].ReturnValue",
                "path-injection",
            ],
            ["core.storage.StorageCapability", "AnyMember.ReturnValue", "path-injection"],
            [
                "core.storage.StorageCapability",
                "Member[validate].ReturnValue",
                "command-injection",
            ],
        ]
        for barrier in broader_barriers:
            with self.subTest(barrier=barrier):
                candidate = json.loads(json.dumps(EXPECTED_CODEQL_STORAGE_MODEL))
                candidate["extensions"][0]["data"] = [barrier]
                with self.assertRaises(AssertionError):
                    assert_exact_codeql_storage_model(self, candidate)

    def test_c_variadic_format_contracts_remain_type_safe_and_portable(self) -> None:
        """Guard the format contracts whose mismatches are undefined behavior."""
        blender = (ROOT / "platform" / "mlv_blender" / "MLVBlender.c").read_text(
            encoding="utf-8"
        )
        basic = (ROOT / "src" / "debayer" / "basic.c").read_text(encoding="utf-8")
        llrawproc = (
            ROOT / "src" / "mlv" / "llrawproc" / "llrawproc.c"
        ).read_text(encoding="utf-8")
        mcraw = (ROOT / "src" / "mlv" / "mcraw" / "mcraw.c").read_text(
            encoding="utf-8"
        )
        cube_lut = (ROOT / "src" / "processing" / "cube_lut.c").read_text(
            encoding="utf-8"
        )

        self.assertIn('#include <inttypes.h>', blender)
        self.assertIn('"Exporting frame %" PRIu64 "/%" PRIu64', blender)
        self.assertNotIn('"Exporting frame %i/%i', blender)

        self.assertEqual(basic.count('allocation of %zu bytes failed.'), 2)
        self.assertNotIn('allocation of %u bytes failed.', basic)

        self.assertIn('raw_image_size / sizeof(*raw_image_buff) > 1000', llrawproc)
        self.assertIn('(unsigned int)raw_image_buff[1000]', llrawproc)
        self.assertIn('Proc_Black = %f', llrawproc)
        self.assertNotIn('Proc_Black = %d', llrawproc)

        self.assertIn('#include <inttypes.h>', mcraw)
        self.assertNotIn('%ld', mcraw)
        self.assertIn('size: %8" PRIu32', mcraw)
        self.assertIn('"File header is missing, invalid file"', mcraw)
        self.assertNotIn('invalid file:  %s", ctx->fd', mcraw)

        self.assertIn(
            'printf("LUT_1D_INPUT_RANGE %f %f\\n", inMin, inMax);',
            cube_lut,
        )
        self.assertIn(
            'printf("LUT_3D_INPUT_RANGE %f %f\\n", inMin, inMax);',
            cube_lut,
        )
        self.assertIn('Data line #%u values:', cube_lut)
        self.assertNotIn('Data line #%d values:', cube_lut)
        self.assertNotRegex(
            cube_lut,
            r'printf\("LUT_[13]D_INPUT_RANGE[^;]+&inMin',
        )

    def test_ci_product_oracles_are_isolated_from_factory_bridge_failures(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        bridge_start = workflow.index("\n  factory-bridge-regressions:")
        product_start = workflow.index("\n  windows-product-oracles:")
        gui_start = workflow.index("\n  windows-gui-pilot:")
        bridge_job = workflow[bridge_start:product_start]
        product_job = workflow[product_start:gui_start]

        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("Run agent bridge PowerShell launcher regressions", bridge_job)
        self.assertIn("Verify compatible MCP runtime", bridge_job)
        self.assertNotIn("Run agent bridge PowerShell launcher regressions", product_job)
        self.assertNotRegex(product_job, r"(?m)^    needs\s*:")
        self.assertIn("Run console_tests --check-golden", product_job)
        self.assertIn("Run pipeline_tests --check-golden (bounded shards)", product_job)

        requirements = (ROOT / "tools" / "agent-bridge" / "requirements.in").read_text(encoding="utf-8")
        self.assertIn("mcp>=1.27.0,<2.0.0", requirements.splitlines())

    def test_contributor_governance_routes_stay_synchronized(self) -> None:
        documents = {
            name: (ROOT / name).read_text(encoding="utf-8")
            for name in ("CONTRIBUTING.md", "SUPPORT.md", "CHANGELOG.md")
        }
        contributing = documents["CONTRIBUTING.md"]
        support = documents["SUPPORT.md"]
        changelog = documents["CHANGELOG.md"]
        normalized_contributing = " ".join(contributing.split())
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        payload_manifest = json.loads(
            (ROOT / "tools" / "gates" / "vendored-native-payloads.json").read_text(
                encoding="utf-8"
            )
        )

        required_checks = (
            "Repo Hygiene Python (windows-latest)",
            "Repo Hygiene Python (ubuntu-latest)",
            "Factory Bridge Regressions",
            "Windows GUI Pilot",
            "Windows Product Oracles",
        )
        protected_section = contributing.split(
            "The protected branch currently requires exactly these hosted checks:", 1
        )[1].lstrip().split("\n\n", 1)[0]
        self.assertEqual(
            tuple(re.findall(r"(?m)^- `([^`]+)`$", protected_section)),
            required_checks,
        )
        self.assertNotEqual(required_checks[0], required_checks[1])
        self.assertIn("os: [windows-latest, ubuntu-latest]", workflow)
        for required_check in required_checks:
            self.assertIn(required_check, contributing)
            workflow_name = required_check.replace(" (windows-latest)", " (${{ matrix.os }})")
            workflow_name = workflow_name.replace(" (ubuntu-latest)", " (${{ matrix.os }})")
            self.assertIn(f"name: {workflow_name}", workflow)

        self.assertIn("`Windows Product Oracles` runs independently", normalized_contributing)
        self.assertIn("It is a branch-protection required check", normalized_contributing)
        self.assertNotIn("not yet a branch-protection required check", normalized_contributing)
        self.assertIn("Bachelor", normalized_contributing)
        self.assertIn(
            "CPU or factory diagnostics from this VM do not prove CUDA behavior",
            normalized_contributing,
        )
        self.assertIn("pinned known-good build", normalized_contributing)
        self.assertIn("real 8-bit present path", normalized_contributing)

        for wrapper_command in (
            "pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "
            "tools\\testing\\run-windows-test.ps1 -Suite console",
            "pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "
            "tools\\testing\\run-windows-test.ps1 -Suite pipeline",
        ):
            self.assertIn(wrapper_command, contributing)

        bootstrap_fragments = (
            "$resolvedPython = py -3.13",
            "Python 3.13.15 is required",
            "-m venv $lockToolsVenv",
            "--only-binary=:all: --require-hashes -r .github\\requirements\\pip.txt",
            "--only-binary=:all: --require-hashes -r .github\\requirements\\lock-tools.txt",
            "& $lockPython -m pip check",
            "update-python-locks.ps1 -RepoRoot . -Python $lockPython -Check",
            "update-python-locks.ps1 -RepoRoot . -Python $lockPython -Upgrade",
        )
        for bootstrap_fragment in bootstrap_fragments:
            self.assertIn(bootstrap_fragment, contributing)
        self.assertLess(
            contributing.index(".github\\requirements\\pip.txt"),
            contributing.index("update-python-locks.ps1 -RepoRoot . -Python $lockPython -Check"),
        )
        self.assertLess(
            contributing.index(".github\\requirements\\lock-tools.txt"),
            contributing.index("update-python-locks.ps1 -RepoRoot . -Python $lockPython -Check"),
        )

        self.assertEqual(payload_manifest["redistribution_readiness"]["status"], "blocked")
        self.assertIn("--require-redistribution-ready", contributing)
        self.assertIn("py -3.13 -m tools.repo_hygiene.vendored_native_payloads", contributing)
        self.assertIn("PYTHON_31315=/absolute/path/to/python3.13", contributing)
        self.assertNotRegex(
            contributing,
            r"(?m)^(?:python|python3) -m tools\.repo_hygiene\.vendored_native_payloads",
        )
        self.assertIn(
            "currently marks redistribution readiness as `blocked`",
            normalized_contributing,
        )
        self.assertIn("[SECURITY.md](SECURITY.md)", contributing)
        self.assertIn("[SECURITY.md](SECURITY.md)", support)
        self.assertIn("https://github.com/ilia3101/MLV-App/issues", support)
        self.assertIn("Issues are disabled on this `layibabalola/MLV-App` fork", support)
        self.assertNotIn("https://github.com/layibabalola/MLV-App/issues", support)
        self.assertIn("does not promise a", support)
        self.assertIn("service level agreement", support)
        self.assertIn("## [Unreleased]", changelog)
        self.assertNotRegex(changelog, r"(?m)^## \[[0-9]+\.[0-9]+")

    def test_python_ci_dependencies_are_exact_hash_locked_and_reproducible(self) -> None:
        expected_python = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
        self.assertEqual(expected_python, "3.13.15")

        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
        for lf_contract in (
            ".python-version text eol=lf",
            ".github/requirements/*.in text eol=lf",
            ".github/requirements/*.txt text eol=lf",
            "tools/agent-bridge/requirements*.in text eol=lf",
            "tools/agent-bridge/requirements*.txt text eol=lf",
            "tools/dependencies/update-python-locks.ps1 text eol=lf",
        ):
            self.assertIn(lf_contract, attributes)

        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count('python-version-file: ".python-version"'), 4)
        self.assertNotRegex(workflow, r"(?m)^\s+python-version:\s*")
        self.assertNotIn("pip install --upgrade", workflow)
        self.assertNotRegex(
            workflow,
            r"python -m pip install\s+(?:jsonschema|pytest|aqtinstall|mcp)(?:\s|[<>=\"'])",
        )

        allowed_locks = {path.replace("/", "\\") for path in PYTHON_LOCK_ROOTS}
        allowed_locks.update(PYTHON_LOCK_ROOTS)
        install_lines = [
            line.strip()
            for line in workflow.splitlines()
            if "-m pip install " in line
        ]
        self.assertEqual(len(install_lines), 9)
        for line in install_lines:
            for required_flag in (
                "--disable-pip-version-check",
                "--no-input",
                "--only-binary=:all:",
                "--require-hashes",
                " -r ",
            ):
                self.assertIn(required_flag, line, f"unsafe Python install command: {line}")
            lock_path = line.rsplit(" -r ", 1)[1].strip()
            self.assertIn(lock_path, allowed_locks, f"unapproved Python lock: {lock_path}")
        self.assertEqual(workflow.count("python -m pip check"), 4)

        observed_locks: dict[str, dict[str, str]] = {}
        for relative_path, roots in PYTHON_LOCK_ROOTS.items():
            lock_path = ROOT / relative_path
            self.assertTrue(lock_path.is_file(), f"missing Python lock: {relative_path}")
            pins = parse_hashed_requirement_lock(lock_path)
            self.assertTrue(roots.issubset(pins), f"{relative_path} lost direct roots")
            observed_locks[relative_path] = pins

        self.assertEqual(observed_locks[".github/requirements/pip.txt"]["pip"], "26.2.1")
        self.assertEqual(
            observed_locks[".github/requirements/lock-tools.txt"]["pip-tools"],
            "7.5.3",
        )
        self.assertTrue(
            observed_locks["tools/agent-bridge/requirements.txt"]["mcp"].startswith("1."),
            "the reviewed bridge API requires MCP 1.x",
        )
        self.assertTrue(
            observed_locks["tools/agent-bridge/requirements-test.txt"]["pytest"].startswith("8."),
            "pytest major updates require explicit review",
        )

        updater = (ROOT / "tools" / "dependencies" / "update-python-locks.ps1").read_text(
            encoding="utf-8"
        )
        for required_symbol in (
            'pip-tools 7.5.3',
            "[string[]]$PythonArguments = @()",
            'throw "-PythonArguments requires -Python"',
            '[System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT',
            '$pySelector = "-{0}.{1}"',
            'throw "Python lock generation requires exact Python $expectedPython; attempts:',
            "Install .github/requirements/lock-tools.txt into that interpreter.",
            "[switch]$Upgrade",
            'throw "-Check and -Upgrade are mutually exclusive"',
            "Copy-Item -LiteralPath $outputPath -Destination $compileOutput",
            '"--generate-hashes"',
            '"--resolver=backtracking"',
            '"--no-emit-index-url"',
            '"--no-emit-trusted-host"',
            '"--newline=lf"',
            '$arguments += "--upgrade"',
            "SequenceEqual[byte]",
        ):
            self.assertIn(required_symbol, updater)
        self.assertLess(
            updater.index("Copy-Item -LiteralPath $outputPath -Destination $compileOutput"),
            updater.index("& $pythonCommand @pythonPrefixArguments @arguments"),
            "check mode must seed the temporary output before pip-compile resolves",
        )
        self.assertLess(
            updater.index('Command = "python"'),
            updater.index('Command = "py"'),
            "hosted setup-python should remain the first/default interpreter candidate",
        )

        lock_policy = (ROOT / ".github" / "requirements" / "README.md").read_text(
            encoding="utf-8"
        )
        for required_policy in (
            "A newly\npublished package therefore cannot make an unchanged commit fail.",
            "`-Upgrade`\nis the only mode that passes `--upgrade` to pip-tools.",
            "Dependabot intentionally ignores `pip-tools`.",
            "synchronized policy tuple",
            "Every candidate must report\nthe complete version in `.python-version`",
            "An explicit executable or launcher is authoritative",
        ):
            self.assertIn(required_policy, lock_policy)

    def test_ci_workflow_hardening_is_fail_closed_and_coordination_aware(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        trigger_section = workflow[: workflow.index("\npermissions:")]

        self.assertIn(
            "concurrency:\n"
            "  group: tests-${{ github.workflow }}-${{ github.event_name }}-"
            "${{ github.event_name == 'workflow_dispatch' && github.run_id || github.ref }}\n"
            "  cancel-in-progress: ${{ github.event_name != 'workflow_dispatch' }}",
            workflow,
        )
        self.assertIn("  pull_request:\n", trigger_section)
        self.assertIn("  push:\n    branches:\n      - master\n", trigger_section)
        self.assertNotRegex(
            trigger_section,
            r"(?m)^\s+paths(?:-ignore)?:",
            "required PR and master CI must not be suppressible by path filtering",
        )

        expected_timeouts = {
            "repo-hygiene-python": 45,
            "factory-bridge-regressions": 45,
            "windows-product-oracles": 120,
            "windows-gui-pilot": 60,
        }
        jobs_text = workflow[workflow.index("\njobs:") :]
        job_matches = list(re.finditer(r"(?m)^  ([a-z0-9-]+):\r?$", jobs_text))
        self.assertEqual({match.group(1) for match in job_matches}, set(expected_timeouts))
        for index, match in enumerate(job_matches):
            end = job_matches[index + 1].start() if index + 1 < len(job_matches) else len(jobs_text)
            job = jobs_text[match.start() : end]
            self.assertIn(
                f"    timeout-minutes: {expected_timeouts[match.group(1)]}",
                job,
                f"{match.group(1)} must have an explicit bounded deadline",
            )

        checkout_blocks = re.findall(
            r"(?m)^\s*- uses: actions/checkout@([0-9a-f]{40}) # v5\r?\n"
            r"\s+with:\r?\n"
            r"\s+persist-credentials: false\s*$",
            workflow,
        )
        self.assertEqual(
            len(checkout_blocks),
            4,
            "every checkout v5 site in tests.yml must disable persisted GitHub credentials",
        )
        self.assertEqual(
            len(set(checkout_blocks)),
            1,
            "tests.yml must use one consistent immutable checkout v5 revision",
        )

        bridge_start = workflow.index("\n  factory-bridge-regressions:")
        product_start = workflow.index("\n  windows-product-oracles:")
        bridge_job = workflow[bridge_start:product_start]
        self.assertIn("Run coordination and self-healing guardrails", bridge_job)
        self.assertIn("tools\\coordination\\test_coordination_guardrails.py", bridge_job)
        self.assertIn("tests\\coordination", bridge_job)
        self.assertIn("tools\\agent-bridge\\requirements-test.txt", bridge_job)
        self.assertNotIn('pip install "pytest', bridge_job)

        product_job = workflow[product_start : workflow.index("\n  windows-gui-pilot:")]
        self.assertNotRegex(product_job, r"(?m)^    needs\s*:")
        artifact_step_start = product_job.index("\n      - name: Upload pipeline crash forensics")
        next_step = product_job.find("\n      - ", artifact_step_start + 1)
        artifact_step = product_job[artifact_step_start : next_step if next_step != -1 else len(product_job)]
        self.assertIn("\n        if: always()", artifact_step)
        self.assertIn("\n        uses: actions/upload-artifact@", artifact_step)
        self.assertIn("\n          path: |", artifact_step)
        self.assertIn("${{ runner.temp }}\\pipeline-golden-actual.json", artifact_step)

    def test_release_workflows_are_bounded_serialized_and_non_attesting_while_blocked(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        release_workflows = (
            workflow_dir / "Linux.yml",
            workflow_dir / "Windows.yml",
            workflow_dir / "macOS-Arm64.yml",
            workflow_dir / "macOS-Intel.yml",
        )
        for workflow_path in release_workflows:
            workflow = workflow_path.read_text(encoding="utf-8")
            trigger_section = workflow[: workflow.index("\npermissions:")]
            self.assertEqual(trigger_section.count("workflow_dispatch:"), 1)
            self.assertNotIn("branches:", trigger_section)
            self.assertIn(
                "concurrency:\n"
                "  group: release-${{ github.workflow }}-${{ github.ref }}\n"
                "  cancel-in-progress: false",
                workflow,
            )
            self.assertNotIn("if: github.ref == 'refs/heads/master'", workflow)
            self.assertIn("    timeout-minutes: 120", workflow)
            self.assertNotIn("attestations: write", workflow)
            self.assertNotIn("id-token: write", workflow)
            self.assertNotIn("actions/attest", workflow)
            self.assertNotIn("Attest build provenance", workflow)

    def test_all_tracked_workflow_actions_are_immutably_pinned_and_inventoried(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        discovered_workflows = sorted(
            [*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")],
            key=lambda path: path.as_posix().lower(),
        )
        tracked_result = git(
            ROOT,
            "ls-files",
            "--",
            ".github/workflows/*.yml",
            ".github/workflows/*.yaml",
        )
        tracked_workflows = {
            (ROOT / line.strip()).resolve()
            for line in tracked_result.stdout.splitlines()
            if line.strip()
        }
        self.assertEqual(
            {path.resolve() for path in discovered_workflows},
            tracked_workflows,
            "the guard must scan every and only tracked top-level GitHub workflow",
        )

        expected_remote_inventory = Counter(
            {
                ("actions/checkout", "v5"): 8,
                ("actions/setup-python", "v6"): 8,
                ("actions/upload-artifact", "v7"): 10,
                ("ConorMacBride/install-package", "v1"): 2,
            }
        )
        observed_remote_inventory: Counter[tuple[str, str]] = Counter()
        revisions_by_action_major: dict[tuple[str, str], set[str]] = {}
        credential_isolated_checkout_sites = 0
        local_action_pattern = re.compile(
            r"^\./(?!\.\.(?:/|$))(?!.*(?:/)\.\.(?:/|$))[A-Za-z0-9_.\/-]+$"
        )
        remote_action_pattern = re.compile(
            r"^(?P<action>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)"
            r"@(?P<revision>[0-9a-f]{40})$"
        )

        for workflow_path in discovered_workflows:
            workflow_text = workflow_path.read_text(encoding="utf-8")
            permissions_declarations = re.findall(
                r"(?m)^(?P<indent>[ \t]*)permissions:\s*",
                workflow_text,
            )
            self.assertEqual(
                permissions_declarations,
                [""],
                f"{workflow_path.relative_to(ROOT)} may not add job-level token permissions",
            )
            permissions_blocks = re.findall(
                r"(?m)^permissions:\s*\r?\n((?:[ \t]+[^\r\n]*(?:\r?\n|$))+)",
                workflow_text,
            )
            self.assertEqual(
                len(permissions_blocks),
                1,
                f"{workflow_path.relative_to(ROOT)} must declare one top-level permissions block",
            )
            observed_permissions = [
                line.strip() for line in permissions_blocks[0].splitlines() if line.strip()
            ]
            expected_permissions = ["contents: read"]
            self.assertEqual(
                observed_permissions,
                expected_permissions,
                f"{workflow_path.relative_to(ROOT)} must grant only its reviewed top-level permissions",
            )
            credential_isolated_checkout_sites += len(
                re.findall(
                    r"(?m)^\s*- uses: actions/checkout@[0-9a-f]{40} # v5\r?\n"
                    r"\s+with:\r?\n"
                    r"\s+persist-credentials: false\s*$",
                    workflow_text,
                )
            )
            for line_number, line in enumerate(workflow_text.splitlines(), start=1):
                match = re.match(r"^\s*(?:-\s+)?uses\s*:\s*(.*?)\s*$", line)
                if not match:
                    continue
                raw_target = match.group(1)
                target, comment_separator, version_comment = raw_target.partition(" #")
                target = target.strip().strip("\"'")
                location = f"{workflow_path.relative_to(ROOT)}:{line_number}"
                self.assertFalse(
                    target.startswith("docker://"),
                    f"{location}: Docker actions are forbidden absent a reviewed digest policy",
                )
                if target.startswith("./"):
                    self.assertRegex(
                        target,
                        local_action_pattern,
                        f"{location}: local actions must be explicit safe ./ paths",
                    )
                    continue

                remote_match = remote_action_pattern.fullmatch(target)
                self.assertIsNotNone(
                    remote_match,
                    f"{location}: remote actions must use owner/repo[/path]@<full-40-hex-SHA>",
                )
                assert remote_match is not None
                version = version_comment.strip() if comment_separator else ""
                version_match = re.fullmatch(r"(?P<major>v[0-9]+)(?:\.[0-9]+){0,2}", version)
                self.assertIsNotNone(
                    version_match,
                    f"{location}: immutable pins require an explicit major/version comment",
                )
                assert version_match is not None
                action_major = (remote_match.group("action"), version_match.group("major"))
                observed_remote_inventory[action_major] += 1
                revisions_by_action_major.setdefault(action_major, set()).add(
                    remote_match.group("revision")
                )

        self.assertEqual(
            observed_remote_inventory,
            expected_remote_inventory,
            "remote action additions or major changes require an explicit reviewed inventory update",
        )
        inconsistent_revisions = {
            f"{action}@{major}": sorted(revisions)
            for (action, major), revisions in revisions_by_action_major.items()
            if len(revisions) != 1
        }
        self.assertFalse(
            inconsistent_revisions,
            "each action+major must use one consistent immutable SHA across all workflows: "
            f"{inconsistent_revisions}",
        )
        self.assertEqual(
            credential_isolated_checkout_sites,
            expected_remote_inventory[("actions/checkout", "v5")],
            "every checkout v5 site must set persist-credentials: false",
        )
        upload_artifact_majors = {
            int(major.removeprefix("v"))
            for action, major in observed_remote_inventory
            if action == "actions/upload-artifact"
        }
        self.assertEqual(
            upload_artifact_majors,
            {7},
            "all artifact uploads must use the official Node 24-capable v7 action line",
        )

    def test_linuxdeploy_downloads_are_fixed_and_checksum_verified(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        all_workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted([*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")])
        )
        self.assertNotIn(
            "miurahr/install-linuxdeploy-action",
            all_workflows,
            "the retired Node 16 LinuxDeploy installer action must not return",
        )

        linux_workflow = (workflow_dir / "Linux.yml").read_text(encoding="utf-8")
        step_headings = list(re.finditer(r"(?m)^    - name:\s*([^\r\n]+)\r?$", linux_workflow))

        def named_step(name: str) -> tuple[int, str]:
            matches = [
                (index, heading)
                for index, heading in enumerate(step_headings)
                if heading.group(1).strip() == name
            ]
            self.assertEqual(len(matches), 1, f"Linux.yml must contain exactly one {name!r} step")
            index, heading = matches[0]
            end = step_headings[index + 1].start() if index + 1 < len(step_headings) else len(linux_workflow)
            return heading.start(), linux_workflow[heading.start() : end]

        install_step_offset, install_step = named_step("Install pinned LinuxDeploy tools")
        create_step_offset, create_step = named_step("Create Appimage")
        self.assertLess(
            install_step_offset,
            create_step_offset,
            "the verified LinuxDeploy install/PATH step must run before Create Appimage",
        )
        self.assertIn('printf \'%s\\n\' "${tools_dir}" >> "${GITHUB_PATH}"', install_step)

        def bare_linuxdeploy_invocations(step: str) -> list[str]:
            return re.findall(
                r"(?m)^\s*(linuxdeploy-x86_64\.AppImage)(?=\s|$)",
                step,
            )

        self.assertEqual(
            bare_linuxdeploy_invocations(create_step),
            ["linuxdeploy-x86_64.AppImage"],
            "Create Appimage must invoke the PATH-resolved LinuxDeploy binary exactly once",
        )
        self.assertEqual(
            create_step.count("linuxdeploy-x86_64.AppImage"),
            1,
            "Create Appimage may not contain a second prefixed or absolute LinuxDeploy invocation",
        )
        for prefix in ("./", "/tmp/linuxdeploy-tools/"):
            falsified_step = create_step.replace(
                "linuxdeploy-x86_64.AppImage",
                f"{prefix}linuxdeploy-x86_64.AppImage",
                1,
            )
            self.assertEqual(
                bare_linuxdeploy_invocations(falsified_step),
                [],
                f"the bare-command guard must reject the {prefix!r} prefix",
            )

        self.assertNotRegex(
            linux_workflow,
            r"(?i)(?:releases/download/)?continuous",
            "LinuxDeploy downloads must never use the mutable continuous channel",
        )
        expected_assets = {
            "linuxdeploy-x86_64.AppImage": (
                "https://github.com/linuxdeploy/linuxdeploy/releases/download/"
                "1-alpha-20251107-1/linuxdeploy-x86_64.AppImage",
                "c20cd71e3a4e3b80c3483cef793cda3f4e990aca14014d23c544ca3ce1270b4d",
            ),
            "linuxdeploy-plugin-qt-x86_64.AppImage": (
                "https://github.com/linuxdeploy/linuxdeploy-plugin-qt/releases/download/"
                "1-alpha-20250213-1/linuxdeploy-plugin-qt-x86_64.AppImage",
                "15106be885c1c48a021198e7e1e9a48ce9d02a86dd0a1848f00bdbf3c1c92724",
            ),
            "linuxdeploy-plugin-appimage-x86_64.AppImage": (
                "https://github.com/linuxdeploy/linuxdeploy-plugin-appimage/releases/download/"
                "1-alpha-20250213-1/linuxdeploy-plugin-appimage-x86_64.AppImage",
                "992d502a248e14ab185448ddf6f6e7d25558cb84d4623c354c3af350c25fccb3",
            ),
        }
        observed_urls = re.findall(
            r"https://github\.com/linuxdeploy/[A-Za-z0-9_.-]+/releases/download/"
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
            linux_workflow,
        )
        self.assertEqual(observed_urls, [asset[0] for asset in expected_assets.values()])
        observed_checksums = {
            filename: checksum
            for checksum, filename in re.findall(
                r"(?m)^\s+([0-9a-f]{64})  "
                r"(linuxdeploy(?:-plugin-(?:qt|appimage))?-x86_64\.AppImage)\s*$",
                linux_workflow,
            )
        }
        self.assertEqual(
            observed_checksums,
            {filename: checksum for filename, (_, checksum) in expected_assets.items()},
        )
        self.assertEqual(linux_workflow.count("curl --fail --location --retry 5 --retry-all-errors"), 3)
        self.assertIn("sha256sum -c SHA256SUMS", linux_workflow)
        self.assertIn('printf \'%s\\n\' "${tools_dir}" >> "${GITHUB_PATH}"', linux_workflow)
        self.assertLess(linux_workflow.index("sha256sum -c"), linux_workflow.index("chmod +x"))
        self.assertLess(linux_workflow.index("chmod +x"), linux_workflow.index('"${GITHUB_PATH}"'))

    def test_windows_and_linux_release_toolchains_are_fail_closed(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        permissive_shell = re.compile(
            r"(?im)(?:\|\|\s*(?:true|:)(?:\s|$)|(?:;|&&)\s*true(?:\s|$)|"
            r"^\s*set\s+\+(?:e|o\s+errexit)\b)"
        )

        def named_step_blocks(text: str) -> list[tuple[str, str]]:
            matches = list(re.finditer(r"(?m)^    - name:\s*([^\r\n]+)\s*$", text))
            return [
                (
                    match.group(1).strip(),
                    text[
                        match.start() : matches[index + 1].start()
                        if index + 1 < len(matches)
                        else len(text)
                    ],
                )
                for index, match in enumerate(matches)
            ]

        def assert_unique_order(text: str, expected_order: tuple[str, ...]) -> dict[str, str]:
            steps = named_step_blocks(text)
            names = [name for name, _ in steps]
            for name in expected_order:
                self.assertEqual(names.count(name), 1, f"expected one step named {name!r}")
            positions = [names.index(name) for name in expected_order]
            self.assertEqual(positions, sorted(positions))
            return dict(steps)

        def replace_in_named_step(text: str, name: str, old: str, new: str) -> str:
            pattern = re.compile(
                rf"(?ms)^    - name:\s*{re.escape(name)}\s*$.*?(?=^    - name:|\Z)"
            )
            matches = list(pattern.finditer(text))
            self.assertEqual(len(matches), 1, f"expected one step named {name!r}")
            match = matches[0]
            block = match.group(0)
            self.assertEqual(block.count(old), 1, f"expected one {old!r} in {name!r}")
            replacement = block.replace(old, new, 1)
            return text[: match.start()] + replacement + text[match.end() :]

        def assert_common_fail_closed(text: str) -> None:
            self.assertNotRegex(text, r"(?im)^\s*continue-on-error\s*:")
            self.assertNotRegex(text, permissive_shell)

        def assert_windows_policy(text: str) -> None:
            assert_common_fail_closed(text)
            blocks = assert_unique_order(
                text,
                (
                    "Install OpenSSL",
                    "Install MinGW 8.1",
                    "Install Qt",
                    "Verify Windows build toolchain",
                    "Build",
                    "Generate single-build release evidence",
                    "Save build artifact",
                ),
            )
            self.assertIn("timeout-minutes: 15", blocks["Install OpenSSL"])
            self.assertIn("timeout-minutes: 15", blocks["Install MinGW 8.1"])
            self.assertIn("timeout-minutes: 20", blocks["Install Qt"])
            for install_step, command, timeout in (
                ("Install OpenSSL", "choco install openssl", "600"),
                (
                    "Install MinGW 8.1",
                    "choco install mingw --version=8.1.0 --exact --allow-downgrade",
                    "600",
                ),
                (
                    "Install Qt",
                    "choco install qt5-default --version=5.15.2.20240623 --exact",
                    "900",
                ),
            ):
                block = blocks[install_step]
                self.assertIn(
                    f"{command} --yes --no-progress --limit-output --execution-timeout={timeout}",
                    block,
                )
                self.assertIn("if ($LASTEXITCODE -ne 0) { throw", block)

            verify_block = blocks["Verify Windows build toolchain"]
            required_executables_match = re.search(
                r"(?ms)^\s*\$requiredExecutables\s*=\s*@\((.*?)^\s*\)",
                verify_block,
            )
            self.assertIsNotNone(required_executables_match)
            required_executables = required_executables_match.group(1)
            for executable_token in (
                '(Join-Path $env:OPENSSL_BIN "openssl.exe")',
                "$env:QMAKE_EXE",
                "$env:WINDEPLOYQT_EXE",
                "$env:MAKE_EXE",
                "$env:GXX_EXE",
            ):
                self.assertEqual(required_executables.count(executable_token), 1)
            for required_token in (
                '$mingwRoot = Join-Path $env:ChocolateyInstall "lib\\mingw\\tools\\install"',
                'Get-ChildItem -LiteralPath $mingwRoot -Filter "mingw32-make.exe" -File -Recurse',
                "if ($makeCandidates.Count -ne 1)",
                "$env:MINGW_BIN = [IO.Path]::GetFullPath($makeCandidates[0].DirectoryName)",
                "$env:MAKE_EXE = [IO.Path]::GetFullPath($makeCandidates[0].FullName)",
                '$env:GXX_EXE = [IO.Path]::GetFullPath((Join-Path $env:MINGW_BIN "g++.exe"))',
                '$env:LIBGOMP_DLL = [IO.Path]::GetFullPath((Join-Path $env:MINGW_BIN "libgomp-1.dll"))',
                "Test-Path -LiteralPath $executable -PathType Leaf",
                "Test-Path -LiteralPath $env:LIBGOMP_DLL -PathType Leaf",
                'Get-ChildItem -LiteralPath $env:OPENSSL_BIN -Filter "libcrypto*.dll" -File',
                'Get-ChildItem -LiteralPath $env:OPENSSL_BIN -Filter "libssl*.dll" -File',
                "if ($cryptoDlls.Count -eq 0) { throw",
                "if ($sslDlls.Count -eq 0) { throw",
                '"OPENSSL_CRYPTO_DLLS=$($cryptoDlls.FullName -join \';\')" >> $env:GITHUB_ENV',
                '"OPENSSL_SSL_DLLS=$($sslDlls.FullName -join \';\')" >> $env:GITHUB_ENV',
                "$qmakeBin = [IO.Path]::GetFullPath((Split-Path -Parent $env:QMAKE_EXE))",
                "$deployBin = [IO.Path]::GetFullPath((Split-Path -Parent $env:WINDEPLOYQT_EXE))",
                "if ($deployBin -ne $qmakeBin)",
                "& $env:GXX_EXE --version",
                'if ($LASTEXITCODE -ne 0) { throw "exact MinGW g++ executable probe failed" }',
                "$compilerVersion = (& $env:GXX_EXE -dumpfullversion -dumpversion).Trim()",
                '$compilerVersion -ne "8.1.0"',
                "$compilerTarget = (& $env:GXX_EXE -dumpmachine).Trim()",
                '$compilerTarget -ne "x86_64-w64-mingw32"',
                "$qtVersion = (& $env:QMAKE_EXE -query QT_VERSION).Trim()",
                '$qtVersion -ne "5.15.2"',
                "$makeBin = [IO.Path]::GetFullPath((Split-Path -Parent $env:MAKE_EXE))",
                "$configuredMingwBin = [IO.Path]::GetFullPath($env:MINGW_BIN)",
                "if ($makeBin -ne $configuredMingwBin)",
                '$expectedCompiler = [IO.Path]::GetFullPath((Join-Path $makeBin "g++.exe"))',
                "$configuredCompiler = [IO.Path]::GetFullPath($env:GXX_EXE)",
                "if ($configuredCompiler -ne $expectedCompiler)",
                '$env:PATH = "$makeBin;$env:PATH"',
                "$resolvedCompiler = Get-Command g++.exe -CommandType Application -ErrorAction Stop",
                "$actualCompiler = [IO.Path]::GetFullPath($resolvedCompiler.Source)",
                "if ($actualCompiler -ne $expectedCompiler)",
                "& g++.exe --version",
                'if ($LASTEXITCODE -ne 0) { throw "PATH-resolved MinGW g++ executable probe failed" }',
                '"MAKE_EXE=$env:MAKE_EXE" >> $env:GITHUB_ENV',
                '"GXX_EXE=$env:GXX_EXE" >> $env:GITHUB_ENV',
                '"LIBGOMP_DLL=$env:LIBGOMP_DLL" >> $env:GITHUB_ENV',
                "$makeBin >> $env:GITHUB_PATH",
            ):
                self.assertIn(required_token, verify_block)

            build_block = blocks["Build"]
            for required_token in (
                "& $env:QMAKE_EXE",
                "& $env:MAKE_EXE",
                "& $env:WINDEPLOYQT_EXE",
                "Copy-Item -LiteralPath $env:LIBGOMP_DLL",
                "$env:OPENSSL_CRYPTO_DLLS -split ';'",
                "$env:OPENSSL_SSL_DLLS -split ';'",
                "Copy-Item -LiteralPath $dll -Destination .",
            ):
                self.assertIn(required_token, build_block)
            self.assertNotRegex(build_block, r"(?i)Copy-Item.*lib(?:crypto|ssl)\*")
            self.assertNotIn("WINDEPLOYQT_EXE --version", verify_block)
            evidence_block = blocks["Generate single-build release evidence"]
            for required_token in (
                "$CompilerPath = (Get-Command $env:GXX_EXE -CommandType Application -ErrorAction Stop).Source",
                "$CompilerTarget = & $CompilerPath -dumpmachine",
                '--tool-version "compiler=$CompilerVersion|$CompilerTarget"',
            ):
                self.assertIn(required_token, evidence_block)
            self.assertNotIn("ProgramData\\chocolatey\\lib\\mingw", evidence_block)
            self.assertEqual(blocks["Save build artifact"].count("if-no-files-found: error"), 1)

        def assert_linux_policy(text: str) -> None:
            assert_common_fail_closed(text)
            blocks = assert_unique_order(
                text,
                (
                    "Install compiler & Qt",
                    "Verify required Qt multimedia plugins",
                    "Build",
                    "Copy Qt multimedia plugins",
                    "Create Appimage",
                    "Save build artifact",
                ),
            )
            required_directories = "required_directories=(audio mediaservice playlistformats)"
            required_plugins = (
                "audio/libqtaudio_alsa.so",
                "audio/libqtmedia_pulse.so",
                "mediaservice/libgstmediaplayer.so",
                "playlistformats/libqtmultimedia_m3u.so",
            )
            verify_block = blocks["Verify required Qt multimedia plugins"]
            copy_block = blocks["Copy Qt multimedia plugins"]
            expected_tool_probes = (
                "command -v qmake",
                "qmake -v",
                "command -v make",
                "make --version",
                "command -v g++",
                "g++ --version",
            )
            for probe in expected_tool_probes:
                self.assertEqual(verify_block.count(probe), 1)
            for block in (verify_block, copy_block):
                self.assertIn("set -euo pipefail", block)
                self.assertIn(required_directories, block)
                for plugin in required_plugins:
                    self.assertIn(plugin, block)
            self.assertIn('test -d "${plugin_root}/${directory}"', verify_block)
            self.assertIn('test -f "${plugin_root}/${plugin}"', verify_block)
            self.assertIn('test -d "${source_directory}"', copy_block)
            self.assertIn('cp -a "${source_directory}/." "${destination_directory}/"', copy_block)
            self.assertIn('test -f "image/usr/plugins/${plugin}"', copy_block)
            self.assertEqual(blocks["Save build artifact"].count("if-no-files-found: error"), 1)

        windows = (workflow_dir / "Windows.yml").read_text(encoding="utf-8")
        linux = (workflow_dir / "Linux.yml").read_text(encoding="utf-8")
        assert_windows_policy(windows)
        assert_linux_policy(linux)

        falsifiers = (
            (
                "Windows continue-on-error",
                assert_windows_policy,
                windows.replace(
                    "    - name: Install OpenSSL\n",
                    "    - name: Install OpenSSL\n      continue-on-error: true\n",
                    1,
                ),
            ),
            (
                "Windows masks install failure",
                assert_windows_policy,
                windows.replace("choco install openssl", "choco install openssl || true", 1),
            ),
            (
                "Windows drops executable assertion",
                assert_windows_policy,
                windows.replace("             $env:QMAKE_EXE,\n", "", 1),
            ),
            (
                "Windows does not bind deploy tool to Qt kit",
                assert_windows_policy,
                windows.replace("if ($deployBin -ne $qmakeBin)", "if ($false)", 1),
            ),
            (
                "Windows floats MinGW toolchain",
                assert_windows_policy,
                windows.replace(" --version=8.1.0 --exact --allow-downgrade", "", 1),
            ),
            (
                "Windows hard-codes stale make name",
                assert_windows_policy,
                windows.replace(' -Filter "mingw32-make.exe"', ' -Filter "make.exe"', 1),
            ),
            (
                "Windows skips compiler version binding",
                assert_windows_policy,
                windows.replace('$compilerVersion -ne "8.1.0"', '$compilerVersion -ne ""', 1),
            ),
            (
                "Windows skips compiler target binding",
                assert_windows_policy,
                windows.replace(
                    '$compilerTarget -ne "x86_64-w64-mingw32"',
                    '$compilerTarget -ne ""',
                    1,
                ),
            ),
            (
                "Windows evidence hashes stale compiler path",
                assert_windows_policy,
                windows.replace(
                    "Get-Command $env:GXX_EXE -CommandType Application",
                    "Get-Command C:\\stale\\g++.exe -CommandType Application",
                    1,
                ),
            ),
            (
                "Windows drops exact compiler assertion",
                assert_windows_policy,
                windows.replace("             $env:GXX_EXE\n", "", 1),
            ),
            (
                "Windows does not bind bare compiler",
                assert_windows_policy,
                windows.replace(
                    "$resolvedCompiler = Get-Command g++.exe -CommandType Application -ErrorAction Stop\n",
                    "",
                    1,
                ),
            ),
            (
                "Windows permits missing artifact",
                assert_windows_policy,
                replace_in_named_step(
                    windows,
                    "Save build artifact",
                    "        if-no-files-found: error\n",
                    "",
                ),
            ),
            (
                "Linux disables errexit",
                assert_linux_policy,
                linux.replace("           set -euo pipefail", "           set +e", 1),
            ),
            (
                "Linux masks plugin copy failure",
                assert_linux_policy,
                linux.replace(
                    'cp -a "${source_directory}/." "${destination_directory}/"',
                    'cp -a "${source_directory}/." "${destination_directory}/" || true',
                    1,
                ),
            ),
            (
                "Linux drops packaged plugin assertion",
                assert_linux_policy,
                linux.replace('             test -f "image/usr/plugins/${plugin}"\n', "", 1),
            ),
            (
                "Linux drops compiler version probe",
                assert_linux_policy,
                linux.replace("           g++ --version\n", "", 1),
            ),
            (
                "Linux drops qmake resolution probe",
                assert_linux_policy,
                linux.replace("           command -v qmake\n", "", 1),
            ),
            (
                "Linux permits missing artifact",
                assert_linux_policy,
                replace_in_named_step(
                    linux,
                    "Save build artifact",
                    "        if-no-files-found: error\n",
                    "",
                ),
            ),
        )
        for label, assertion, falsified in falsifiers:
            with self.subTest(falsifier=label):
                with self.assertRaises(AssertionError):
                    assertion(falsified)

    def test_macos_release_runners_are_supported_and_architecture_explicit(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        intel_workflow = (workflow_dir / "macOS-Intel.yml").read_text(encoding="utf-8")
        arm_workflow = (workflow_dir / "macOS-Arm64.yml").read_text(encoding="utf-8")

        self.assertIn("runs-on: macos-15-intel", intel_workflow)
        self.assertIn("runs-on: macos-15", arm_workflow)
        self.assertNotRegex(
            intel_workflow + arm_workflow,
            r"runs-on:\s*macos-(?:13|14)(?:\s|$)",
            "retired or deprecated macOS labels must not silently queue or encode the next outage",
        )

    def test_qt_opengl_headers_and_cpu_feature_probe_are_architecture_portable(self) -> None:
        qt_dir = ROOT / "platform" / "qt"
        for source_path in sorted(qt_dir.rglob("*")):
            if source_path.suffix.lower() not in {".h", ".hpp", ".cpp", ".cc"}:
                continue
            source = source_path.read_text(encoding="utf-8")
            self.assertNotRegex(
                source,
                r"(?m)^\s*#\s*include\s*<QtOpenGL(?:Widgets)?/",
                f"{source_path.relative_to(ROOT)} must use Qt5/Qt6-compatible generic OpenGL headers",
            )

        portable_headers = {
            "GpuDisplayViewport.h": (
                "#include <QOpenGLShaderProgram>",
                "#include <QOpenGLTexture>",
                "#include <QOpenGLWidget>",
            ),
            "GpuDisplayWindow.h": (
                "#include <QOpenGLShaderProgram>",
                "#include <QOpenGLTexture>",
                "#include <QOpenGLWindow>",
            ),
        }
        for relative_path, required_includes in portable_headers.items():
            source = (qt_dir / relative_path).read_text(encoding="utf-8")
            for required_include in required_includes:
                self.assertIn(required_include, source)

        crash_forensics = (qt_dir / "CrashForensics.cpp").read_text(encoding="utf-8")
        cpu_guard = re.search(
            r"(?ms)^#if defined\(__GNUC__\) && \(defined\(__i386__\) \|\| defined\(__x86_64__\)\)\s*$"
            r"(?P<body>.*?)"
            r"^#endif\s*$",
            crash_forensics,
        )
        self.assertIsNotNone(
            cpu_guard,
            "GCC/Clang x86 CPU-feature builtins must stay compile-time guarded off ARM",
        )
        assert cpu_guard is not None
        guarded_builtins = re.findall(r"\b__builtin_cpu_(?:init|supports)\b", cpu_guard.group("body"))
        self.assertEqual(
            len(guarded_builtins),
            5,
            "the x86 guard must contain cpu init plus all four feature probes",
        )
        outside_guard = crash_forensics[: cpu_guard.start()] + crash_forensics[cpu_guard.end() :]
        self.assertNotRegex(
            outside_guard,
            r"\b__builtin_cpu_(?:init|supports)\b",
            "no x86 CPU builtin may remain reachable on ARM outside the architecture guard",
        )

    def test_release_sources_remain_cxx14_and_c_memory_portable(self) -> None:
        for source_root in (ROOT / "src", ROOT / "platform" / "qt"):
            for source_path in sorted(source_root.rglob("*")):
                if source_path.suffix.lower() not in {".h", ".hh", ".hpp", ".c", ".cc", ".cpp", ".cxx"}:
                    continue
                source = source_path.read_text(encoding="utf-8", errors="replace")
                self.assertNotRegex(
                    source,
                    r"\bstd::(?:gcd|lcm)\b",
                    f"{source_path.relative_to(ROOT)} must remain compatible with the C++14 release target",
                )

        batch_types = (ROOT / "src" / "batch" / "BatchTypes.h").read_text(encoding="utf-8")
        self.assertRegex(
            batch_types,
            r"(?s)while\( remainder != 0 \).*?divisor % remainder.*?divisor = remainder.*?remainder = next",
            "rendered-video aspect reduction must retain a C++14-compatible Euclidean divisor",
        )

        frame_caching = (ROOT / "src" / "mlv" / "frame_caching.c").read_text(encoding="utf-8")
        self.assertRegex(
            frame_caching,
            r"(?m)^#include <string\.h>$",
            "the C frame cache must declare memcpy/memset on Linux and macOS",
        )
        self.assertIn("memcpy(", frame_caching)
        self.assertIn("memset(", frame_caching)

    def test_console_frame_cache_linkage_has_processed8_invalidation_provider(self) -> None:
        console_project = (ROOT / "tests" / "console" / "console_tests.pro").read_text(
            encoding="utf-8"
        )
        links_frame_cache = "src/mlv/frame_caching.c" in console_project
        links_video_mlv = "src/mlv/video_mlv.c" in console_project

        if links_frame_cache and not links_video_mlv:
            self.assertIn(
                "tests/console/stubs/pipeline_stubs.cpp",
                console_project,
                "console_tests must link its test-only providers when video_mlv.c is absent",
            )
            pipeline_stubs = (
                ROOT / "tests" / "console" / "stubs" / "pipeline_stubs.cpp"
            ).read_text(encoding="utf-8")
            provider = re.search(
                r"(?s)void\s+mlvInvalidateProcessed8PrefetchCache\s*\(\s*mlvObject_t\s*\*\s*video\s*\)"
                r"\s*\{(?P<body>.*?)^\}",
                pipeline_stubs,
                flags=re.MULTILINE,
            )
            self.assertIsNotNone(
                provider,
                "frame_caching.c requires a processed8 invalidation provider in console_tests",
            )
            assert provider is not None
            provider_body = provider.group("body")

            expected_scalars = {
                "current_processed_frame_8bit_active": "0",
                "current_processed_frame_8bit_signature": "0",
                "current_processed_frame_8bit": "0",
                "current_processed_frame_8bit_threads": "0",
                "processed_8bit_cache_next_slot": "0",
                "processed_8bit_cache_unit_size": "0",
                "processed8_prefetch_snapshot_dirty": "1",
            }
            for field, value in expected_scalars.items():
                self.assertRegex(
                    provider_body,
                    rf"\bvideo->{field}\s*=\s*{value}\s*;",
                    f"console processed8 provider must set {field} to {value}",
                )

            expected_arrays = {
                "processed_8bit_cache_active",
                "processed_8bit_cache_frame",
                "processed_8bit_cache_threads",
                "processed_8bit_cache_signature",
                "processed_8bit_cache_scale",
                "processed_8bit_cache_phase4b_path",
                "processed_8bit_cache_phase4b_y_crop_rows",
                "processed_8bit_cache_state",
                "processed_8bit_cache_prefetched",
                "processed_8bit_cache_generation",
            }
            observed_arrays = set(
                re.findall(
                    r"std::memset\(video->(processed_8bit_cache_[a-z0-9_]+),\s*0,\s*"
                    r"sizeof\(video->\1\)\s*\)\s*;",
                    provider_body,
                )
            )
            self.assertEqual(
                observed_arrays,
                expected_arrays,
                "console processed8 provider must mirror exactly the synchronous slot arrays",
            )
            self.assertNotRegex(
                provider_body,
                r"pthread_|video->processed8_prefetch_(?:generation|request|thread|worker|stop)",
                "async prefetch synchronization and generation behavior belongs to pipeline tests",
            )

    def test_release_processing_stores_and_gpu_stubs_are_platform_portable(self) -> None:
        raw_processing = (ROOT / "src" / "processing" / "raw_processing.c").read_text(
            encoding="utf-8"
        )
        self.assertRegex(
            raw_processing,
            r"(?s)static inline void agx_store_float_triplet_fast\(.*?float \* const pix0,"
            r".*?\*pix0 = \(float\)\(uint16_t\)LIMIT16\(out_r\);"
            r".*?\*pix1 = \(float\)\(uint16_t\)LIMIT16\(out_g\);"
            r".*?\*pix2 = \(float\)\(uint16_t\)LIMIT16\(out_b\);",
            "the AgX gradient path must clamp and retain 16-bit quantization through float pointers",
        )
        self.assertNotRegex(
            raw_processing,
            r"(?s)agx_store_float_triplet_fast\(.*?agx_store_u16_fast\(",
            "the float writer must clamp before conversion instead of inheriting the unsafe fast cast",
        )
        self.assertIn(
            "agx_store_float_triplet_fast(agx_out_r, agx_out_g, agx_out_b, "
            "&pixg[0], &pixg[1], &pixg[2]);",
            raw_processing,
        )
        self.assertNotIn(
            "agx_store_u16_triplet_fast(agx_out_r, agx_out_g, agx_out_b, "
            "&pixg[0], &pixg[1], &pixg[2]);",
            raw_processing,
            "float gradient storage must never be passed to a uint16_t writer",
        )

        llrawproc = (ROOT / "src" / "mlv" / "llrawproc" / "llrawproc.c").read_text(
            encoding="utf-8"
        )
        non_windows = llrawproc.split(
            "#else\nstatic int llrawproc_gpu_export_backend_available", 1
        )[1].split("#endif\n\nstatic int llrawproc_worker_copy_pixel_map", 1)[0]
        for symbol in (
            "llrpGpuPlaybackReconPreuploadFrame",
            "llrpGpuPlaybackReconGetLastPreuploadStatus",
        ):
            self.assertRegex(
                non_windows,
                rf"(?s)int {symbol}\([^;]*?\)\s*\{{.*?return 0;\s*\}}",
                f"{symbol} must fail closed at the non-Windows llrawproc API boundary",
            )
        self.assertRegex(
            non_windows,
            r"(?s)int llrpGpuPlaybackReconGetLastPreuploadStatus\([^;]*?\)\s*\{"
            r".*?if\(status\) memset\(status, 0, sizeof\(\*status\)\);.*?return 0;\s*\}",
            "unavailable preupload telemetry must be fully initialized before returning",
        )

    def test_dependency_updates_and_private_security_reporting_are_bounded(self) -> None:
        dependabot = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        update_blocks = re.split(r"(?m)^  - package-ecosystem:\s*", dependabot)[1:]
        self.assertEqual(len(update_blocks), 2, "Dependabot must expose exactly two bounded ecosystems")

        observed_updates: dict[str, dict[str, str]] = {}
        for block in update_blocks:
            ecosystem_match = re.match(r'["\']?([^"\'\r\n]+)["\']?\r?\n', block)
            self.assertIsNotNone(ecosystem_match)
            assert ecosystem_match is not None
            ecosystem = ecosystem_match.group(1)

            def required_field(pattern: str, label: str) -> str:
                match = re.search(pattern, block, flags=re.MULTILINE)
                self.assertIsNotNone(match, f"{ecosystem} is missing {label}")
                assert match is not None
                return match.group(1)

            directory_match = re.search(
                r'^    directory:\s*["\']?([^"\'\r\n]+)', block, flags=re.MULTILINE
            )
            directories_match = re.search(
                r"(?ms)^    directories:\s*\r?\n(?P<body>(?:      - [^\r\n]+\r?\n)+)",
                block,
            )
            self.assertNotEqual(
                directory_match is not None,
                directories_match is not None,
                f"{ecosystem} must use exactly one directory form",
            )
            if directory_match is not None:
                directories = (directory_match.group(1),)
            else:
                assert directories_match is not None
                directories = tuple(
                    value.strip().strip('"\'')
                    for value in re.findall(r"(?m)^      - ([^\r\n]+)$", directories_match.group("body"))
                )

            observed_updates[ecosystem] = {
                "directories": directories,
                "interval": required_field(r'^      interval:\s*["\']?([^"\'\r\n]+)', "interval"),
                "day": required_field(r'^      day:\s*["\']?([^"\'\r\n]+)', "schedule day"),
                "time": required_field(r'^      time:\s*["\']?([^"\'\r\n]+)', "schedule time"),
                "limit": required_field(r"^    open-pull-requests-limit:\s*([0-9]+)", "PR limit"),
                "group": required_field(r"^    groups:\r?\n      ([A-Za-z0-9_-]+):", "update group"),
                "compiler_ignore": bool(
                    re.search(r'^      - dependency-name:\s*"pip-tools"\s*$', block, flags=re.MULTILINE)
                ),
            }
            self.assertIn(
                '        patterns:\n          - "*"',
                block.replace("\r\n", "\n"),
                f"{ecosystem} must group every dependency",
            )
            self.assertIn(
                '        update-types:\n          - "minor"\n          - "patch"',
                block.replace("\r\n", "\n"),
                f"{ecosystem} must bound grouped updates to minor and patch versions",
            )

        self.assertEqual(
            observed_updates,
            {
                "github-actions": {
                    "directories": ("/",),
                    "interval": "weekly",
                    "day": "monday",
                    "time": "06:00",
                    "limit": "2",
                    "group": "routine-actions",
                    "compiler_ignore": False,
                },
                "pip": {
                    "directories": ("/.github/requirements", "/tools/agent-bridge"),
                    "interval": "weekly",
                    "day": "tuesday",
                    "time": "06:00",
                    "limit": "2",
                    "group": "routine-python",
                    "compiler_ignore": True,
                },
            },
        )
        self.assertNotIn(
            "/",
            observed_updates["pip"]["directories"],
            "the repository root is not a supported pip dependency surface",
        )

        security_policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn(
            "https://github.com/layibabalola/MLV-App/security/advisories/new",
            security_policy,
        )
        self.assertRegex(
            security_policy,
            r"(?i)do not open a public issue",
            "vulnerability reports must use the private advisory channel",
        )

    def test_release_playback_profile_wrapper_pins_windows_qpa(self) -> None:
        script = ROOT / "tools" / "profiling" / "run-release-playback-profile.ps1"
        text = script.read_text(encoding="utf-8")
        self.assertIn('$env:QT_QPA_PLATFORM = "windows"', text)
        self.assertIn("QT_QPA_PLATFORM_PLUGIN_PATH", text)
        self.assertIn("qwindows.dll", text)
        self.assertIn("platform\\qt\\build-release\\release\\MLVApp.exe", text)
        self.assertIn('[Alias("Input")]', text)
        self.assertIn("[string]$ClipPath", text)
        self.assertNotIn("[string]$Input", text)
        self.assertNotIn('$env:QT_QPA_PLATFORM = "offscreen"', text)

    def test_policy_verifier_catches_runtime_auto_trigger_signal_drift(self) -> None:
        repo = self.init_repo()
        package_dir = repo / "tools" / "repo_hygiene"
        package_dir.mkdir(parents=True)
        (package_dir / "core.py").write_text("# verifier sample\n", encoding="utf-8")
        shutil.copy(ROOT / "tools" / "repo_hygiene" / "test_repo_hygiene.py", package_dir / "test_repo_hygiene.py")
        config_path = repo / "tools" / "repo-hygiene" / "hygiene.config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["closeout"]["auto_trigger"]["signals"] = ["dirty_current_work"]
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        result = verify_policy(repo)
        self.assertFalse(result["ok"])
        self.assertTrue(any("closeout.auto_trigger.signals" in failure for failure in result["failures"]))
        config["closeout"]["auto_trigger"]["signals"] = load_config(ROOT)["closeout"]["auto_trigger"]["signals"]
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        contract_path = repo / "tools" / "repo-hygiene" / "closeout.contract.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["states"] = ["approved"]
        contract_path.write_text(json.dumps(contract, indent=2), encoding="utf-8")
        result = verify_policy(repo)
        self.assertFalse(result["ok"])
        self.assertTrue(any("contract.states" in failure for failure in result["failures"]))

    def test_candidate_ids_are_stable_and_do_not_require_raw_paths(self) -> None:
        first = stable_id("orphan-dir", {"path": "C:/repo/.claude/worktrees/tmp"})
        second = stable_id("orphan-dir", {"path": "C:/repo/.claude/worktrees/tmp"})
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("orphan-dir:"))
        self.assertNotIn("C:/repo", first)

    def test_reparse_helper_returns_boolean_on_platform_branch(self) -> None:
        self.assertIsInstance(is_reparse_point(ROOT), bool)

    def test_dirty_file_triage_recommends_generated_and_commit_groups(self) -> None:
        config = load_config(ROOT)
        generated = dirty_recommendation(".claude-state/profiling/run.json", config, [], "codex/hygiene")
        source = dirty_recommendation("tools/repo_hygiene/core.py", config, ["tools/repo_hygiene/core.py"], "codex/hygiene")
        self.assertEqual(generated[0], "ignore/generated")
        self.assertGreaterEqual(generated[1], 0.8)
        self.assertIn(source[0], {"commit", "split"})
        self.assertGreaterEqual(source[1], 0.65)

    def test_scan_emits_structured_artifacts_and_dirty_group_candidate(self) -> None:
        repo = self.init_repo()
        (repo / "tools" / "repo_hygiene").mkdir(parents=True)
        (repo / "tools" / "repo_hygiene" / "core.py").write_text("print('dirty')\n", encoding="utf-8")
        result = run_scan(repo)
        run_dir = Path(result["run_dir"])
        self.assertTrue((run_dir / "facts.json").exists())
        self.assertTrue((run_dir / "plan.json").exists())
        self.assertTrue((run_dir / "result.json").exists())
        self.assertTrue((run_dir / "summary.md").exists())
        plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["schema_version"], "1.0")
        self.assertIn("policy_hash", plan)
        dirty = [c for c in plan["candidates"] if c["kind"] == "dirty-group"]
        self.assertTrue(dirty)
        self.assertIn("evidence_hash", dirty[0])
        self.assertEqual(dirty[0]["decision"], "retain")

    def test_cli_scan_works_from_path_with_spaces_and_bang(self) -> None:
        repo = self.init_repo()
        spaced = self.tempdir / "path with space !"
        repo.rename(spaced)
        cli = ROOT / "tools" / "repo-hygiene" / "hygiene.py"
        result = subprocess.run(
            [
                sys.executable,
                str(cli),
                "--repo-root",
                str(spaced),
                "scan",
                "--no-write-artifacts",
                "--trust-local-base",
                "--json",
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertIn(result.returncode, {0, 1}, result.stderr)
        self.assertIn('"schema_version"', result.stdout)
        self.assertFalse((spaced / ".claude-state" / "repo-hygiene").exists())

    def test_orphan_source_like_directory_is_manual_only(self) -> None:
        repo = self.init_repo()
        orphan = repo / ".claude" / "worktrees" / "orphan-source"
        orphan.mkdir(parents=True)
        (orphan / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
        scan = run_scan(repo)
        candidate = next(
            c for c in scan["plan"]["candidates"] if c["kind"] == "orphan-dir" and c.get("path") == str(orphan.resolve())
        )
        self.assertEqual(candidate["risk_tier"], "R4")
        self.assertNotIn("orphan_quarantine", candidate["allowed_actions"])
        self.assertIn("source-like", candidate["never_allowed_reason"])

    def test_generated_report_apply_uses_candidate_id_and_revalidates_evidence(self) -> None:
        repo = self.init_repo()
        config = load_config(repo)
        config_path = repo / "tools" / "repo-hygiene" / "hygiene.config.json"
        config.pop("policy_hash", None)
        config["thresholds"]["generated_run_retention_days"] = 0
        config["thresholds"]["generated_run_keep_latest"] = 0
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        old_run = repo / ".claude-state" / "repo-hygiene" / "runs" / "old-run"
        old_run.mkdir(parents=True)
        (old_run / "summary.md").write_text("old\n", encoding="utf-8")
        os.utime(old_run, (time.time() - 3600, time.time() - 3600))
        scan = run_scan(repo)
        candidate = next(
            c for c in scan["plan"]["candidates"] if c["kind"] == "generated-report" and c.get("path") == str(old_run.resolve())
        )
        with self.assertRaises(HygieneError):
            run_apply(
                repo,
                candidate_id=candidate["id"],
                action_id="repo_hygiene_prune_old_runs",
            )
        (old_run / "extra.txt").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(HygieneError):
            run_apply(
                repo,
                candidate_id=candidate["id"],
                action_id="repo_hygiene_prune_old_runs",
                expected_evidence_hash=candidate["evidence_hash"],
            )
        scan = run_scan(repo)
        candidate = next(
            c for c in scan["plan"]["candidates"] if c["kind"] == "generated-report" and c.get("path") == str(old_run.resolve())
        )
        result = run_apply(
            repo,
            candidate_id=candidate["id"],
            action_id="repo_hygiene_prune_old_runs",
            expected_evidence_hash=candidate["evidence_hash"],
        )
        self.assertEqual(result["result"]["status"], "applied")
        self.assertFalse(old_run.exists())

    def test_apply_mutex_blocks_concurrent_mutation(self) -> None:
        repo = self.init_repo()
        lock = repo / ".claude-state" / "repo-hygiene" / "apply.lock"
        lock.parent.mkdir(parents=True)
        lock.write_text("busy", encoding="utf-8")
        with self.assertRaises(HygieneError):
            run_apply(repo, candidate_id="generated-report:none", action_id="repo_hygiene_prune_old_runs")

    def test_branch_delete_archives_before_deleting(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "topic")
        (repo / "topic.txt").write_text("topic\n", encoding="utf-8")
        git(repo, "add", "topic.txt")
        git(repo, "commit", "-m", "topic")
        git(repo, "checkout", "master")
        git(repo, "merge", "--no-ff", "topic", "-m", "merge topic")
        scan = run_scan(repo, trust_local_base=True)
        candidate = next(c for c in scan["plan"]["candidates"] if c["kind"] == "branch" and c["evidence"]["branch"]["name"] == "topic")
        result = run_apply(
            repo,
            candidate_id=candidate["id"],
            action_id="branch_archive_delete",
            expected_evidence_hash=candidate["evidence_hash"],
            manual_override=True,
            trust_local_base=True,
        )
        self.assertEqual(result["result"]["status"], "applied")
        self.assertTrue(result["result"]["commands_invoked"])
        self.assertNotEqual(git(repo, "rev-parse", "--verify", "topic", check=False).returncode, 0)
        archive_ref = result["result"]["outcomes"][0]["archive_ref"]
        self.assertEqual(git(repo, "rev-parse", "--verify", archive_ref).returncode, 0)

    def test_stash_promote_creates_recovery_branch_without_drop(self) -> None:
        repo = self.init_repo()
        (repo / "code.py").write_text("print('stash')\n", encoding="utf-8")
        git(repo, "add", "code.py")
        git(repo, "stash", "push", "-m", "codex-temp code")
        scan = run_scan(repo, trust_local_base=True)
        candidate = next(c for c in scan["plan"]["candidates"] if c["kind"] == "stash")
        result = run_apply(
            repo,
            candidate_id=candidate["id"],
            action_id="stash_promote",
            expected_evidence_hash=candidate["evidence_hash"],
            manual_override=True,
            trust_local_base=True,
        )
        self.assertEqual(result["result"]["status"], "applied")
        self.assertIn("hygiene/stash/", result["result"]["outcomes"][0]["branch"])
        self.assertIn("stash@{0}", git(repo, "stash", "list").stdout)

    def approved_closeout(self, repo: Path) -> tuple[dict, str]:
        (repo / "work.py").write_text("print('work')\n", encoding="utf-8")
        tx = open_transaction(repo)
        packet = json.loads((Path(tx["tx_dir"]) / "decision-packet.json").read_text(encoding="utf-8"))
        unit_id = packet["dirty_commit_units"][0]["id"]
        recommendation = {
            "tx_id": tx["tx_id"],
            "decision_packet_hash": tx["decision_packet_hash"],
            "summary": "Commit the selected commit unit, keep cleanup recommendations symbolic, then publish manually.",
            "actions": [{"action_id": "commit_unit_commit", "commit_unit_id": unit_id}],
            "cleanup_actions": [],
            "residual_risks": [],
        }
        recorded = self.record_recommendation(repo, tx, recommendation)
        for reviewer_id in ["stranger-a", "stranger-b"]:
            self.record_review(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": reviewer_id,
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": 0,
                    "score": 10,
                    "approve": True,
                    "rationale": "Recommendation is data-only and references symbolic action IDs.",
                },
        )
        self.assertFalse((Path(tx["tx_dir"]) / "trusted-approval-nonce.json").exists())
        self.assertTrue((Path(tx["tx_dir"]) / "trusted-approval-nonce.public.json").exists())
        self.assertTrue((Path(tx["tx_dir"]) / "trusted-provenance-key.public.json").exists())
        persisted_state = json.loads((Path(tx["tx_dir"]) / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn("trusted_provenance_key", persisted_state)
        self.assertNotIn("trusted_provenance_keys", persisted_state)
        self.approve_closeout(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "recommendation_hash": recorded["recommendation_hash"],
                "approval_source": "local_interactive_cli",
                "approved_action_ids": ["commit_unit_commit"],
                "approved_commit_unit_ids": [unit_id],
                "approved_candidate_ids": [],
                "risk_acceptance": "Local test approves the data-only closeout recommendation.",
            },
        )
        return tx, unit_id

    def test_closeout_transaction_requires_data_only_codex_review_and_readonly_strangers(self) -> None:
        repo = self.init_repo()
        (repo / "work.py").write_text("print('work')\n", encoding="utf-8")
        tx = open_transaction(repo)
        packet = json.loads((Path(tx["tx_dir"]) / "decision-packet.json").read_text(encoding="utf-8"))
        self.assertEqual(tx["state"]["state"], "awaiting_codex_review")
        self.assertTrue(packet["dirty_commit_units"])
        self.assertIn("closeout-transaction", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("commit-unit", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("merge-readiness", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("publish-target", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("prune-after-publish", IMPLEMENTED_CLOSEOUT_CANDIDATE_KINDS)
        self.assertIn("commit_unit_commit", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("publish_pr", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("publish_direct_branch", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("local_merge", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        self.assertIn("prune_after_publish", IMPLEMENTED_CLOSEOUT_ACTION_IDS)
        with self.assertRaises(HygieneError):
            record_codex_recommendation(
                repo,
                tx["tx_id"],
                self.signed(tx, "codex_recommendation", {
                    "tx_id": tx["tx_id"],
                    "decision_packet_hash": tx["decision_packet_hash"],
                    "actions": [{"action_id": "commit_unit_commit", "command": "git commit -am bad"}],
                }),
                provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
            )
        with self.assertRaises(HygieneError):
            record_codex_recommendation(
                repo,
                tx["tx_id"],
                self.signed(tx, "codex_recommendation", {
                    "tx_id": tx["tx_id"],
                    "decision_packet_hash": tx["decision_packet_hash"],
                    "actions": [{"action_id": "ask", "note": "unknown nested key"}],
                }),
                provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
            )
        with self.assertRaises(HygieneError):
            record_codex_recommendation(
                repo,
                tx["tx_id"],
                self.signed(tx, "codex_recommendation", {
                    "tx_id": tx["tx_id"],
                    "decision_packet_hash": tx["decision_packet_hash"],
                    "actions": [{"action_id": "commit_unit_commit", "commit_unit_id": "commit-unit:missing"}],
                }),
                provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
            )
        with self.assertRaises(HygieneError):
            record_codex_recommendation(
                repo,
                tx["tx_id"],
                self.signed(tx, "codex_recommendation", {
                    "tx_id": tx["tx_id"],
                    "decision_packet_hash": tx["decision_packet_hash"],
                    "actions": [],
                    "cleanup_actions": [{"action_id": "worktree_remove", "candidate_id": "worktree:missing"}],
                }),
                provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
            )
        with self.assertRaises(HygieneError):
            record_codex_recommendation(
                repo,
                tx["tx_id"],
                self.signed(tx, "codex_recommendation", {
                    "tx_id": tx["tx_id"],
                    "decision_packet_hash": tx["decision_packet_hash"],
                    "actions": [{"action_id": "publish_pr"}],
                }),
                provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
            )

    def test_closeout_approval_and_apply_validation_are_revalidated(self) -> None:
        repo = self.init_repo()
        tx, _unit_id = self.approved_closeout(repo)
        status = transaction_status(repo, tx["tx_id"])
        self.assertEqual(status["state"]["state"], "approved")
        self.assertEqual(status["review_count"], 2)
        validation = self.validate_apply(repo, tx)
        self.assertEqual(validation["status"], "validated")
        self.assertEqual(validation["preflight_results"]["candidate_ids_revalidated"], [])
        handoff = json.loads((Path(tx["tx_dir"]) / "executor-handoff.json").read_text(encoding="utf-8"))
        self.assertEqual(handoff["boundary"], "validation_only_not_completion")
        self.assertIn("raw shell", handoff["forbidden_inputs"])
        explained = transaction_status(repo, tx["tx_id"], explain=True)
        self.assertIn("validation_boundary", explained["explain"])
        events = [
            json.loads(line)
            for line in (Path(tx["tx_dir"]) / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(all("event_hash" in event for event in events))
        for previous, current in zip(events, events[1:]):
            self.assertEqual(current["previous_event_hash"], previous["event_hash"])

    def test_closeout_apply_validation_blocks_when_dirty_file_changes(self) -> None:
        repo = self.init_repo()
        tx, _unit_id = self.approved_closeout(repo)
        (repo / "work.py").write_text("print('changed after approval')\n", encoding="utf-8")
        with self.assertRaises(HygieneError):
            self.validate_apply(repo, tx)

    def test_closeout_apply_validation_blocks_when_event_chain_is_tampered(self) -> None:
        repo = self.init_repo()
        tx, _unit_id = self.approved_closeout(repo)
        events_path = Path(tx["tx_dir"]) / "events.jsonl"
        lines = events_path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["event"] = "tampered"
        lines[0] = json.dumps(first, sort_keys=True)
        events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(HygieneError):
            self.validate_apply(repo, tx)

    def test_closeout_apply_validation_blocks_when_accepted_review_changes_or_disappears(self) -> None:
        repo = self.init_repo()
        tx, _unit_id = self.approved_closeout(repo)
        review_file = sorted(Path(tx["tx_dir"]).glob("agent-review-*.json"))[0]
        review = json.loads(review_file.read_text(encoding="utf-8"))
        review["score"] = 1
        review_file.write_text(json.dumps(review, indent=2), encoding="utf-8")
        with self.assertRaises(HygieneError):
            self.validate_apply(repo, tx)

        shutil.rmtree(repo, ignore_errors=True)
        repo = self.init_repo()
        tx, _unit_id = self.approved_closeout(repo)
        review_file = sorted(Path(tx["tx_dir"]).glob("agent-review-*.json"))[0]
        review_file.unlink()
        with self.assertRaises(HygieneError):
            self.validate_apply(repo, tx)

    def test_closeout_apply_validation_blocks_when_approval_manifest_is_rewritten(self) -> None:
        repo = self.init_repo()
        tx, _unit_id = self.approved_closeout(repo)
        tx_dir = Path(tx["tx_dir"])
        for path in tx_dir.glob("agent-review-*.json"):
            path.unlink()
        approval_path = tx_dir / "approval.json"
        state_path = tx_dir / "state.json"
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
        approval["accepted_review_hashes"] = []
        approval.pop("approval_hash", None)
        approval["approval_hash"] = tx_hash(approval)
        state["accepted_review_hashes"] = []
        state["approval_hash"] = approval["approval_hash"]
        approval_path.write_text(json.dumps(approval, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaises(HygieneError):
            self.validate_apply(repo, tx)

    def test_closeout_readonly_review_and_trusted_approval_are_enforced(self) -> None:
        repo = self.init_repo()
        (repo / "work.py").write_text("print('work')\n", encoding="utf-8")
        tx = open_transaction(repo)
        recorded = self.record_recommendation(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "decision_packet_hash": tx["decision_packet_hash"],
                "actions": [{"action_id": "ask"}],
            },
        )
        with self.assertRaises(HygieneError):
            record_agent_review(
                repo,
                tx["tx_id"],
                {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": "unsigned",
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": 0,
                    "score": 10,
                    "approve": True,
                },
                provenance_key=tx["trusted_provenance_keys"]["agent_review"],
            )
        with self.assertRaises(HygieneError):
            record_agent_review(
                repo,
                tx["tx_id"],
                self.signed(tx, "agent_review", {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": "write-capable",
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": True},
                    "write_attempts": 0,
                    "score": 10,
                    "approve": True,
                }),
                provenance_key=tx["trusted_provenance_keys"]["agent_review"],
            )
        with self.assertRaises(HygieneError):
            record_agent_review(
                repo,
                tx["tx_id"],
                self.signed(tx, "agent_review", {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": "malformed",
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": "not-an-int",
                    "score": 10,
                    "approve": True,
                }),
                provenance_key=tx["trusted_provenance_keys"]["agent_review"],
            )
        with self.assertRaises(HygieneError):
            record_agent_review(
                repo,
                tx["tx_id"],
                self.signed(tx, "agent_review", {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": "malformed-approve",
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": 0,
                    "score": 10,
                    "approve": "not-bool",
                }),
                provenance_key=tx["trusted_provenance_keys"]["agent_review"],
            )
        recorded_review = self.record_review(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "reviewer_id": "../escape",
                "recommendation_hash_reviewed": recorded["recommendation_hash"],
                "review_source": "codex_background_agent",
                "reviewer_mode": "read_only",
                "tool_capabilities": {"write_tools_enabled": False},
                "write_attempts": 0,
                "score": 10,
                "approve": True,
            },
        )
        self.assertFalse((Path(tx["tx_dir"]).parent / "escape.json").exists())
        self.assertTrue(any(path.name.startswith("agent-review-") for path in Path(tx["tx_dir"]).glob("agent-review-*.json")))
        self.assertEqual(recorded_review["reviewer_id"], "../escape")
        nonce = tx["trusted_approval_nonce"]
        with self.assertRaises(HygieneError):
            approve_transaction(
                repo,
                tx["tx_id"],
                self.signed(tx, "approval", {
                    "tx_id": tx["tx_id"],
                    "recommendation_hash": recorded["recommendation_hash"],
                    "approval_source": "untrusted_dashboard",
                    "approved_action_ids": ["ask"],
                    "approved_commit_unit_ids": [],
                    "approved_candidate_ids": [],
                    "review_waiver": {"risk_acceptance": "test waiver"},
                }),
                nonce,
                provenance_key=tx["trusted_provenance_keys"]["approval"],
                recommendation_provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
                review_provenance_key=tx["trusted_provenance_keys"]["agent_review"],
            )
        with self.assertRaises(HygieneError):
            approve_transaction(
                repo,
                tx["tx_id"],
                self.signed(tx, "approval", {
                    "tx_id": tx["tx_id"],
                    "recommendation_hash": recorded["recommendation_hash"],
                    "approval_source": "local_interactive_cli",
                    "approved_action_ids": ["ask"],
                    "approved_commit_unit_ids": [],
                    "approved_candidate_ids": [],
                }),
                nonce,
                provenance_key=tx["trusted_provenance_keys"]["agent_review"],
                recommendation_provenance_key=tx["trusted_provenance_keys"]["codex_recommendation"],
                review_provenance_key=tx["trusted_provenance_keys"]["agent_review"],
            )

    def test_closeout_disabled_waiver_cannot_override_blocking_review(self) -> None:
        repo = self.init_repo()
        (repo / "work.py").write_text("print('work')\n", encoding="utf-8")
        tx = open_transaction(repo)
        recorded = self.record_recommendation(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "decision_packet_hash": tx["decision_packet_hash"],
                "actions": [{"action_id": "ask"}],
            },
        )
        for reviewer_id, score, approve in [("stranger-a", 10, True), ("stranger-b", 2, False)]:
            self.record_review(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": reviewer_id,
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": 0,
                    "score": score,
                    "approve": approve,
                },
            )
        with self.assertRaises(HygieneError):
            self.approve_closeout(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "recommendation_hash": recorded["recommendation_hash"],
                    "approval_source": "local_interactive_cli",
                    "approved_action_ids": ["ask"],
                    "approved_commit_unit_ids": [],
                    "approved_candidate_ids": [],
                    "review_waiver": {"risk_acceptance": "try to override blocker"},
                },
            )

    def test_closeout_required_review_count_is_config_driven(self) -> None:
        repo = self.init_repo()
        config_path = repo / "tools" / "repo-hygiene" / "hygiene.config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["closeout"]["required_read_only_reviewers"] = 3
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        (repo / "work.py").write_text("print('work')\n", encoding="utf-8")
        tx = open_transaction(repo)
        recorded = self.record_recommendation(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "decision_packet_hash": tx["decision_packet_hash"],
                "actions": [{"action_id": "ask"}],
            },
        )
        for reviewer_id in ["stranger-a", "stranger-b"]:
            self.record_review(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": reviewer_id,
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": 0,
                    "score": 10,
                    "approve": True,
                },
            )
        with self.assertRaises(HygieneError):
            self.approve_closeout(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "recommendation_hash": recorded["recommendation_hash"],
                    "approval_source": "local_interactive_cli",
                    "approved_action_ids": ["ask"],
                    "approved_commit_unit_ids": [],
                    "approved_candidate_ids": [],
                },
            )

    def test_closeout_approval_counts_only_event_recorded_review_artifacts(self) -> None:
        repo = self.init_repo()
        (repo / "work.py").write_text("print('work')\n", encoding="utf-8")
        tx = open_transaction(repo)
        recorded = self.record_recommendation(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "decision_packet_hash": tx["decision_packet_hash"],
                "actions": [{"action_id": "ask"}],
            },
        )
        self.record_review(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "reviewer_id": "stranger-a",
                "recommendation_hash_reviewed": recorded["recommendation_hash"],
                "review_source": "codex_background_agent",
                "reviewer_mode": "read_only",
                "tool_capabilities": {"write_tools_enabled": False},
                "write_attempts": 0,
                "score": 10,
                "approve": True,
            },
        )
        fake = {
            "schema_version": "1.0",
            "created_at": "2026-05-04T00:00:00+00:00",
            **self.signed(
                tx,
                "agent_review",
                {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": "stranger-b",
                    "recommendation_hash_reviewed": recorded["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": 0,
                    "score": 10,
                    "approve": True,
                },
            ),
        }
        fake["review_hash"] = tx_hash(fake)
        (Path(tx["tx_dir"]) / "agent-review-fake.json").write_text(json.dumps(fake, indent=2), encoding="utf-8")
        with self.assertRaises(HygieneError):
            self.approve_closeout(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "recommendation_hash": recorded["recommendation_hash"],
                    "approval_source": "local_interactive_cli",
                    "approved_action_ids": ["ask"],
                    "approved_commit_unit_ids": [],
                    "approved_candidate_ids": [],
                },
            )

    def test_closeout_reviews_must_match_current_recommendation_hash(self) -> None:
        repo = self.init_repo()
        (repo / "work.py").write_text("print('work')\n", encoding="utf-8")
        tx = open_transaction(repo)
        first = self.record_recommendation(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "decision_packet_hash": tx["decision_packet_hash"],
                "summary": "First recommendation.",
                "actions": [{"action_id": "ask"}],
            },
        )
        for reviewer_id in ["stranger-a", "stranger-b"]:
            self.record_review(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "reviewer_id": reviewer_id,
                    "recommendation_hash_reviewed": first["recommendation_hash"],
                    "review_source": "codex_background_agent",
                    "reviewer_mode": "read_only",
                    "tool_capabilities": {"write_tools_enabled": False},
                    "write_attempts": 0,
                    "score": 10,
                    "approve": True,
                },
            )
        second = self.record_recommendation(
            repo,
            tx,
            {
                "tx_id": tx["tx_id"],
                "decision_packet_hash": tx["decision_packet_hash"],
                "summary": "Replacement recommendation.",
                "actions": [{"action_id": "ask"}],
            },
        )
        with self.assertRaises(HygieneError):
            self.approve_closeout(
                repo,
                tx,
                {
                    "tx_id": tx["tx_id"],
                    "recommendation_hash": second["recommendation_hash"],
                    "approval_source": "local_interactive_cli",
                    "approved_action_ids": ["ask"],
                    "approved_commit_unit_ids": [],
                    "approved_candidate_ids": [],
                },
            )

    def test_closeout_publish_policy_is_enforced_from_config(self) -> None:
        repo = self.init_repo()
        git(repo, "checkout", "-b", "topic")
        (repo / "feature.py").write_text("print('feature')\n", encoding="utf-8")
        git(repo, "add", "feature.py")
        git(repo, "commit", "-m", "feature")
        with self.assertRaises(HygieneError):
            open_transaction(repo, publish_mode="pr_only", publish_remote="origin")
        with self.assertRaises(HygieneError):
            open_transaction(repo, publish_mode="direct_push_branch", publish_remote="fork")
        git(repo, "checkout", "-b", "codex/publish-ready")
        ok = open_transaction(repo, publish_mode="pr_only", publish_remote="fork")
        self.assertEqual(ok["state"]["publish_mode"], "pr_only")

    def test_closeout_auto_trigger_opens_transaction_for_clean_feature_branch(self) -> None:
        repo = self.init_repo()
        git(
            repo,
            "add",
            "tools/repo-hygiene/hygiene.config.json",
            "tools/repo-hygiene/POLICY.md",
            "tools/repo-hygiene/closeout.contract.json",
        )
        git(repo, "commit", "-m", "add hygiene policy")
        git(repo, "checkout", "-b", "codex/ready")
        (repo / "feature.py").write_text("print('ready')\n", encoding="utf-8")
        git(repo, "add", "feature.py")
        git(repo, "commit", "-m", "ready feature")
        result = evaluate_closeout_triggers(repo, open_if_triggered=True)
        self.assertTrue(result["triggered"])
        self.assertEqual(result["opened_transaction"]["state"]["state"], "awaiting_codex_review")
        latest = json.loads((repo / ".claude-state" / "repo-hygiene" / "triggers" / "latest.json").read_text(encoding="utf-8"))
        self.assertNotIn("trusted_approval_nonce", latest["opened_transaction"])
        self.assertNotIn("trusted_provenance_keys", latest["opened_transaction"])
        self.assertIn("clean_feature_branch_ready_to_publish", [signal["id"] for signal in result["signals"]])
        self.assertIn("dirty_current_work", IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS)
        self.assertIn("dirty_generated_only", IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS)
        self.assertIn("clean_feature_branch_ready_to_publish", IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS)
        self.assertIn("hygiene_cleanup_recommendations", IMPLEMENTED_CLOSEOUT_TRIGGER_SIGNAL_IDS)
        second = evaluate_closeout_triggers(repo, open_if_triggered=True)
        self.assertFalse(second["triggered"])
        self.assertEqual(len(second["active_transactions"]), 1)

    def test_config_paths_must_stay_repo_relative_and_state_under_claude_state(self) -> None:
        repo = self.init_repo()
        config_path = repo / "tools" / "repo-hygiene" / "hygiene.config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["state_root"] = "../outside"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        with self.assertRaises(HygieneError):
            load_config(repo)

    def test_load_bearing_registered_worktree_is_not_removal_candidate(self) -> None:
        repo = self.init_repo()
        wt = repo / ".claude" / "worktrees" / "registered"
        git(repo, "worktree", "add", str(wt), "-b", "registered-topic")
        scan = run_scan(repo, trust_local_base=True)
        candidate = next(c for c in scan["plan"]["candidates"] if c["kind"] == "worktree" and c.get("path") == str(wt.resolve()))
        self.assertEqual(candidate["risk_tier"], "R4")
        self.assertNotIn("worktree_remove", candidate["allowed_actions"])
        self.assertEqual(candidate["never_allowed_reason"], "load-bearing worktree root")

    def test_reparse_or_symlink_orphan_is_refused_before_inventory(self) -> None:
        repo = self.init_repo()
        link = repo / ".claude" / "worktrees" / "linked"
        link.mkdir(parents=True)
        (link / "would-be-skipped.cpp").write_text("int outside;\n", encoding="utf-8")

        def fake_reparse(path: Path) -> bool:
            return Path(path).name == "linked"

        with patch("tools.repo_hygiene.core.is_reparse_point", side_effect=fake_reparse):
            scan = run_scan(repo)
        candidate = next(c for c in scan["plan"]["candidates"] if c["kind"] == "orphan-dir" and c["title"].endswith(": linked"))
        self.assertEqual(candidate["risk_tier"], "R4")
        self.assertNotIn("orphan_quarantine", candidate["allowed_actions"])
        self.assertTrue(candidate["evidence"]["is_reparse_point"])
        self.assertTrue(candidate["evidence"]["inventory"]["refused_reparse_point"])


if __name__ == "__main__":
    unittest.main()
