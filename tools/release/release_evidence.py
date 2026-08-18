"""Generate fail-closed evidence for one release-workflow build.

The output describes one observed build and its staged contents.  It is not a
reproducible-build result, a redistribution approval, an attestation, or a
signature.  Only Python's standard library is used so packaging jobs do not
gain an additional runtime dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import struct
import sys
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


CONTENTS_SCHEMA = "mlvapp.release-contents.v1"
BUILD_INFO_SCHEMA = "mlvapp.release-build-info.v1"
OBSERVATION_SCOPE = "single-build-observation"
DEFAULT_VENDORED_MANIFEST = "tools/gates/vendored-native-payloads.json"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{40}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class EvidenceError(ValueError):
    """Raised when release evidence cannot be generated safely."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_file_snapshot(path: Path, *, capture: bool = False) -> tuple[int, str, bytes | None]:
    """Read one regular file while rejecting link swaps and in-read mutation."""
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise EvidenceError(f"path is not a regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    captured = bytearray() if capture else None
    try:
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise EvidenceError(f"path changed to a non-regular file: {path}")
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
                if captured is not None:
                    captured.extend(block)
            after_open = os.fstat(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    after_path = path.stat(follow_symlinks=False)
    # Windows synthesizes executable permission bits from the filename for a
    # path stat but not an fd stat, so compare file type separately from stable
    # identity/content fields.
    identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if any(getattr(before, field) != getattr(opened, field) for field in identity_fields) or any(
        getattr(opened, field) != getattr(after_open, field) for field in identity_fields
    ) or any(getattr(after_open, field) != getattr(after_path, field) for field in identity_fields) or any(
        stat.S_IFMT(item.st_mode) != stat.S_IFREG
        for item in (before, opened, after_open, after_path)
    ):
        raise EvidenceError(f"file changed while release evidence was generated: {path}")
    return after_open.st_size, digest.hexdigest(), bytes(captured) if captured is not None else None


def _sha256_file(path: Path) -> str:
    return _read_file_snapshot(path)[1]


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def _reject_link_ancestors(path: Path, label: str) -> None:
    absolute = Path(os.path.abspath(path))
    for candidate in reversed((absolute, *absolute.parents)):
        if _is_link_or_junction(candidate):
            raise EvidenceError(
                f"{label} has a symbolic-link or junction ancestor: {candidate}"
            )


def _require_plain_root(path: Path, label: str) -> None:
    _reject_link_ancestors(path, label)
    if not path.exists():
        raise EvidenceError(f"{label} does not exist: {path}")


def _require_contained(repo_root: Path, path: Path, label: str) -> None:
    try:
        path.resolve(strict=True).relative_to(repo_root.resolve(strict=True))
    except ValueError as exc:
        raise EvidenceError(f"{label} resolves outside the repository root: {path}") from exc


def _safe_relative_path(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceError(f"{label} must be a non-empty relative path")
    if "\\" in value:
        raise EvidenceError(f"{label} must use canonical '/' separators: {value}")
    pure = PurePosixPath(value)
    if pure.is_absolute() or re.match(r"^[A-Za-z]:", value):
        raise EvidenceError(f"{label} must be relative: {value}")
    parts = pure.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise EvidenceError(f"{label} contains traversal or an invalid segment: {value}")
    return "/".join(parts)


def _validate_member_names(paths: Iterable[str]) -> None:
    casefolded: dict[str, str] = {}
    normalized: dict[str, str] = {}
    for path in paths:
        _safe_relative_path(path, "inventory member")
        folded = path.casefold()
        if folded in casefolded and casefolded[folded] != path:
            raise EvidenceError(
                f"case-folded inventory path collision: {casefolded[folded]!r} and {path!r}"
            )
        casefolded[folded] = path
        unicode_key = unicodedata.normalize("NFC", path).casefold()
        if unicode_key in normalized and normalized[unicode_key] != path:
            raise EvidenceError(
                f"Unicode-normalized inventory path collision: {normalized[unicode_key]!r} and {path!r}"
            )
        normalized[unicode_key] = path


def _file_record(path: Path, logical_path: str) -> dict[str, Any]:
    size, digest, _ = _read_file_snapshot(path)
    if size <= 0:
        raise EvidenceError(f"inventory member is empty: {logical_path}")
    return {
        "path": logical_path,
        "sha256": digest,
        "size": size,
    }


def inventory_path(path: Path, *, logical_name: str) -> dict[str, Any]:
    """Return a canonical inventory object for a regular file or directory."""
    path = Path(path)
    _require_plain_root(path, "inventory root")
    logical_name = _safe_relative_path(logical_name, "logical name")
    records: list[dict[str, Any]] = []
    if path.is_file():
        records.append(_file_record(path, logical_name))
        kind = "file"
    elif path.is_dir():
        kind = "directory"
        for current, directory_names, file_names in os.walk(path, followlinks=False):
            current_path = Path(current)
            for name in [*directory_names, *file_names]:
                candidate = current_path / name
                if _is_link_or_junction(candidate):
                    relative = candidate.relative_to(path).as_posix()
                    raise EvidenceError(
                        f"inventory contains a symbolic link or junction: {relative}"
                    )
            for name in file_names:
                candidate = current_path / name
                relative = candidate.relative_to(path).as_posix()
                records.append(_file_record(candidate, relative))
    else:
        raise EvidenceError(f"inventory root is neither a file nor a directory: {path}")
    if not records:
        raise EvidenceError(f"inventory root contains no files: {path}")
    _validate_member_names(record["path"] for record in records)
    records.sort(key=lambda record: record["path"].encode("utf-8"))
    return {
        "file_count": len(records),
        "files": records,
        "kind": kind,
        "logical_name": logical_name,
        "total_size": sum(record["size"] for record in records),
    }


def inventory_document(inventory: dict[str, Any]) -> dict[str, Any]:
    return {
        "inventory": inventory,
        "inventory_sha256": _sha256_bytes(_canonical_bytes(inventory)),
        "schema": CONTENTS_SCHEMA,
    }


def _parse_assignments(values: Sequence[str], label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, assigned = value.partition("=")
        key = key.strip()
        assigned = assigned.strip()
        if not separator or not key or not assigned or not LABEL_RE.fullmatch(key):
            raise EvidenceError(f"{label} must use non-empty NAME=VALUE syntax: {value!r}")
        if key in result:
            raise EvidenceError(f"duplicate {label} name: {key}")
        result[key] = assigned
    return result


def _tool_record(path_value: str, version: str, name: str) -> dict[str, Any]:
    try:
        resolved = Path(path_value).resolve(strict=True)
    except OSError as exc:
        raise EvidenceError(f"tool {name} executable cannot be resolved: {path_value}") from exc
    _require_plain_root(resolved, f"tool {name} executable")
    if not resolved.is_file():
        raise EvidenceError(f"tool {name} executable is not a regular file: {resolved}")
    size, digest, _ = _read_file_snapshot(resolved)
    if size <= 0 or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise EvidenceError(f"tool {name} executable has invalid SHA-256 evidence")
    if not version.strip():
        raise EvidenceError(f"tool {name} version must be non-empty")
    return {
        "resolved_path": str(resolved),
        "sha256": digest,
        "size": size,
        "version": version.strip(),
    }


def _parse_tools(
    path_values: Sequence[str],
    version_values: Sequence[str],
    target_os: str,
) -> dict[str, dict[str, Any]]:
    paths = _parse_assignments(path_values, "tool path")
    versions = _parse_assignments(version_values, "tool version")
    if set(paths) != set(versions):
        raise EvidenceError(
            "tool paths and versions must name the same tools: "
            f"paths={sorted(paths)}, versions={sorted(versions)}"
        )
    required = {
        "windows": {"compiler", "qmake", "windeployqt"},
        "linux": {
            "compiler",
            "linuxdeploy",
            "linuxdeploy-plugin-appimage",
            "linuxdeploy-plugin-qt",
            "qmake",
        },
        "macos": {"compiler", "macdeployqt", "qmake"},
    }[target_os]
    if set(paths) != required:
        raise EvidenceError(
            f"{target_os} release evidence requires exact tool set {sorted(required)}; "
            f"got {sorted(paths)}"
        )
    python_path = str(Path(sys.executable).resolve(strict=True))
    records = {
        name: _tool_record(paths[name], versions[name], name)
        for name in sorted(paths)
    }
    records["python"] = _tool_record(python_path, platform.python_version(), "python")
    return dict(sorted(records.items()))


def _inspect_executable_architecture(path: Path) -> dict[str, Any]:
    _, digest, data = _read_file_snapshot(path, capture=True)
    assert data is not None
    architectures: list[str]
    binary_format: str
    if data.startswith(b"MZ") and len(data) >= 64:
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if pe_offset + 6 > len(data) or data[pe_offset : pe_offset + 4] != b"PE\0\0":
            raise EvidenceError("expected main executable has a malformed PE header")
        machine = struct.unpack_from("<H", data, pe_offset + 4)[0]
        architectures = [{0x8664: "x86_64", 0xAA64: "arm64", 0x14C: "x86"}.get(machine, f"unknown-0x{machine:04x}")]
        binary_format = "pe"
    elif data.startswith(b"\x7fELF") and len(data) >= 20:
        byte_order = {1: "<", 2: ">"}.get(data[5])
        if byte_order is None:
            raise EvidenceError("expected main executable has an invalid ELF byte order")
        machine = struct.unpack_from(f"{byte_order}H", data, 18)[0]
        architectures = [{62: "x86_64", 183: "arm64", 3: "x86"}.get(machine, f"unknown-{machine}")]
        binary_format = "elf"
    elif len(data) >= 8 and data[:4] in {
        b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"
    }:
        byte_order = ">" if data[:4] in {b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf"} else "<"
        cpu_type = struct.unpack_from(f"{byte_order}I", data, 4)[0]
        architectures = [{0x01000007: "x86_64", 0x0100000C: "arm64", 7: "x86"}.get(cpu_type, f"unknown-0x{cpu_type:08x}")]
        binary_format = "macho"
    elif len(data) >= 8 and data[:4] in {
        b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"
    }:
        byte_order = ">" if data[:4] in {b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"} else "<"
        entry_size = 32 if data[:4] in {b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"} else 20
        count = struct.unpack_from(f"{byte_order}I", data, 4)[0]
        if count == 0 or count > 32 or 8 + count * entry_size > len(data):
            raise EvidenceError("expected main executable has a malformed Mach-O fat header")
        architectures = []
        for index in range(count):
            cpu_type = struct.unpack_from(f"{byte_order}I", data, 8 + index * entry_size)[0]
            architectures.append({0x01000007: "x86_64", 0x0100000C: "arm64", 7: "x86"}.get(cpu_type, f"unknown-0x{cpu_type:08x}"))
        architectures = sorted(set(architectures))
        binary_format = "macho-fat"
    else:
        raise EvidenceError("expected main executable format is not PE, ELF, or Mach-O")
    return {"architectures": architectures, "format": binary_format, "sha256": digest}


def _load_vendored_readiness(repo_root: Path, relative_path: str) -> dict[str, Any]:
    relative_path = _safe_relative_path(relative_path, "vendored manifest path")
    path = repo_root / Path(relative_path)
    _require_plain_root(path, "vendored manifest")
    _require_contained(repo_root, path, "vendored manifest")
    if not path.is_file():
        raise EvidenceError("vendored manifest must be a regular file")
    _, manifest_sha256, manifest_bytes = _read_file_snapshot(path, capture=True)
    assert manifest_bytes is not None
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"vendored manifest is unreadable: {exc}") from exc
    readiness = manifest.get("redistribution_readiness")
    if not isinstance(readiness, dict):
        raise EvidenceError("vendored manifest lacks redistribution_readiness")
    status_value = readiness.get("status")
    blockers = readiness.get("blockers")
    if status_value != "blocked":
        raise EvidenceError(
            "release evidence groundwork requires vendored redistribution status to remain blocked"
        )
    if not isinstance(blockers, list) or not blockers or not all(
        isinstance(item, str) and item.strip() for item in blockers
    ):
        raise EvidenceError("blocked vendored readiness requires non-empty textual blockers")
    return {
        "blockers": blockers,
        "enforcement": readiness.get("enforcement"),
        "path": relative_path,
        "schema_version": manifest.get("schema_version"),
        "sha256": manifest_sha256,
        "status": status_value,
    }


def _assert_expected_main(
    inventory_root: Path,
    inventory: dict[str, Any],
    relative: str,
    target_os: str,
    target_arch: str,
) -> tuple[str, dict[str, Any]]:
    relative = _safe_relative_path(relative, "expected main executable")
    if inventory["kind"] != "directory":
        raise EvidenceError("expected main executable requires a directory inventory root")
    members = {record["path"] for record in inventory["files"]}
    if relative not in members:
        raise EvidenceError(f"expected main executable is missing from inventory: {relative}")
    candidate = inventory_root.joinpath(*PurePosixPath(relative).parts)
    if _is_link_or_junction(candidate) or not candidate.is_file():
        raise EvidenceError(f"expected main executable is not a regular file: {relative}")
    if target_os.casefold() != "windows" and not os.access(candidate, os.X_OK):
        raise EvidenceError(f"expected main executable lacks an executable mode: {relative}")
    architecture = _inspect_executable_architecture(candidate)
    inventory_sha = next(
        record["sha256"] for record in inventory["files"] if record["path"] == relative
    )
    if architecture["sha256"] != inventory_sha:
        raise EvidenceError("expected main executable changed after inventory generation")
    expected_format = {"windows": "pe", "linux": "elf", "macos": "macho"}[target_os]
    if not architecture["format"].startswith(expected_format):
        raise EvidenceError(
            f"expected main executable format mismatch: target {target_os} requires "
            f"{expected_format}, observed {architecture['format']}"
        )
    if target_arch not in architecture["architectures"]:
        raise EvidenceError(
            f"expected main executable architecture mismatch: declared {target_arch}, "
            f"observed {architecture['architectures']}"
        )
    return relative, architecture


def generate_evidence(
    *,
    repo_root: Path,
    product: Path,
    inventory_root: Path,
    expected_main: str,
    output_dir: Path,
    label: str,
    target_os: str,
    target_arch: str,
    repository: str,
    source_ref: str,
    commit: str,
    run_id: str,
    run_attempt: str,
    runner_os: str,
    runner_arch: str,
    runner_name: str,
    runner_image_os: str,
    runner_image_version: str,
    tools: Sequence[str] = (),
    tool_versions: Sequence[str] = (),
    vendored_manifest: str = DEFAULT_VENDORED_MANIFEST,
) -> tuple[Path, Path]:
    """Generate contents and build-info JSON, returning their output paths."""
    repo_root = Path(repo_root)
    product = Path(product)
    inventory_root = Path(inventory_root)
    output_dir = Path(output_dir)
    _require_plain_root(repo_root, "repository root")
    _require_plain_root(product, "product")
    _require_plain_root(inventory_root, "inventory root")
    _require_contained(repo_root, product, "product")
    _require_contained(repo_root, inventory_root, "inventory root")
    if not LABEL_RE.fullmatch(label):
        raise EvidenceError("label must contain only letters, digits, dot, underscore, or hyphen")
    if not SHA256_RE.fullmatch(commit):
        raise EvidenceError("commit must be a full 40-character hexadecimal Git object name")
    required_values = {
        "target OS": target_os,
        "target architecture": target_arch,
        "repository": repository,
        "source ref": source_ref,
        "run id": run_id,
        "run attempt": run_attempt,
        "runner OS": runner_os,
        "runner architecture": runner_arch,
        "runner name": runner_name,
        "runner image OS": runner_image_os,
        "runner image version": runner_image_version,
    }
    for name, value in required_values.items():
        if not isinstance(value, str) or not value.strip():
            raise EvidenceError(f"{name} must be non-empty")
    target_os = target_os.casefold()
    target_arch = target_arch.casefold()
    allowed_arches = {
        "linux": {"x86_64"},
        "macos": {"arm64", "x86_64"},
        "windows": {"x86_64"},
    }
    if target_os not in allowed_arches or target_arch not in allowed_arches[target_os]:
        raise EvidenceError(f"unsupported release target: {target_os}/{target_arch}")
    if target_os == "windows":
        if not product.is_dir() or not inventory_root.is_dir():
            raise EvidenceError("Windows evidence requires a product directory and directory inventory")
    else:
        expected_suffix = ".appimage" if target_os == "linux" else ".dmg"
        if not product.is_file() or product.suffix.casefold() != expected_suffix:
            raise EvidenceError(f"{target_os} evidence requires a non-empty {expected_suffix} product file")
        if not inventory_root.is_dir():
            raise EvidenceError(f"{target_os} evidence requires a directory staging inventory")
    expected_main = _safe_relative_path(expected_main, "expected main executable")
    tool_records = _parse_tools(tools, tool_versions, target_os)

    # Evidence inside the staged tree would mutate the inventory it describes.
    inventory_resolved = inventory_root.resolve()
    output_resolved = output_dir.resolve()
    _reject_link_ancestors(output_dir, "output directory")
    try:
        output_resolved.relative_to(inventory_resolved)
    except ValueError:
        pass
    else:
        raise EvidenceError("output directory must not be inside the inventory root")

    contents_inventory = inventory_path(inventory_root, logical_name=inventory_root.name)
    expected_main, main_executable = _assert_expected_main(
        inventory_root,
        contents_inventory,
        expected_main,
        target_os,
        target_arch,
    )
    contents = inventory_document(contents_inventory)

    if product.is_dir():
        if product.resolve() == inventory_root.resolve():
            product_inventory = contents_inventory
        else:
            product_inventory = inventory_path(product, logical_name=product.name)
        product_record = {
            "hash_kind": "sha256-canonical-file-manifest",
            "kind": "directory",
            "logical_name": product.name,
            "sha256": _sha256_bytes(_canonical_bytes(product_inventory)),
            "size": product_inventory["total_size"],
        }
    elif product.is_file():
        product_record = _file_record(product, product.name)
        product_record.update({"hash_kind": "sha256-file", "kind": "file", "logical_name": product.name})
        product_record.pop("path")
    else:
        raise EvidenceError("product must be a regular file or directory")

    vendored = _load_vendored_readiness(repo_root, vendored_manifest)
    build_info = {
        "assurance": {
            "blockers": vendored["blockers"],
            "observation_scope": OBSERVATION_SCOPE,
            "redistribution_ready": False,
            "reproducibility_claim": False,
        },
        "build": {
            "commit": commit.lower(),
            "repository": repository,
            "run_attempt": run_attempt,
            "run_id": run_id,
            "runner": {
                "arch": runner_arch,
                "image_os": runner_image_os,
                "image_version": runner_image_version,
                "name": runner_name,
                "os": runner_os,
            },
            "source_ref": source_ref,
            "target_arch": target_arch,
            "target_os": target_os,
            "tools": tool_records,
        },
        "contents": {
            "count": contents_inventory["file_count"],
            "expected_main_executable": expected_main,
            "main_executable": main_executable,
            "inventory_sha256": contents["inventory_sha256"],
            "manifest": f"{label}.contents.json",
            "manifest_sha256": _sha256_bytes(_canonical_bytes(contents) + b"\n"),
            "size": contents_inventory["total_size"],
        },
        "product": product_record,
        "schema": BUILD_INFO_SCHEMA,
        "vendored_payloads": vendored,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    contents_path = output_dir / f"{label}.contents.json"
    build_info_path = output_dir / f"{label}.build-info.json"
    _atomic_write_json(contents_path, contents)
    _atomic_write_json(build_info_path, build_info)
    return contents_path, build_info_path


def _atomic_write_json(path: Path, value: Any) -> None:
    payload = _canonical_bytes(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--product", required=True, type=Path)
    parser.add_argument("--inventory-root", required=True, type=Path)
    parser.add_argument("--expected-main", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--target-os", required=True)
    parser.add_argument("--target-arch", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--runner-os", required=True)
    parser.add_argument("--runner-arch", required=True)
    parser.add_argument("--runner-name", required=True)
    parser.add_argument("--runner-image-os", required=True)
    parser.add_argument("--runner-image-version", required=True)
    parser.add_argument("--tool", action="append", default=[])
    parser.add_argument("--tool-version", action="append", default=[])
    parser.add_argument("--vendored-manifest", default=DEFAULT_VENDORED_MANIFEST)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        contents, build_info = generate_evidence(
            repo_root=args.repo_root,
            product=args.product,
            inventory_root=args.inventory_root,
            expected_main=args.expected_main,
            output_dir=args.output_dir,
            label=args.label,
            target_os=args.target_os,
            target_arch=args.target_arch,
            repository=args.repository,
            source_ref=args.source_ref,
            commit=args.commit,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            runner_os=args.runner_os,
            runner_arch=args.runner_arch,
            runner_name=args.runner_name,
            runner_image_os=args.runner_image_os,
            runner_image_version=args.runner_image_version,
            tools=args.tool,
            tool_versions=args.tool_version,
            vendored_manifest=args.vendored_manifest,
        )
    except (EvidenceError, OSError) as exc:
        print(f"release evidence generation failed: {exc}", file=sys.stderr)
        return 2
    print(contents)
    print(build_info)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
