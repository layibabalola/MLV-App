"""Fail-closed integrity checks for Git-tracked native payload archives.

This module deliberately separates byte/structure integrity from redistribution
readiness.  A payload with incomplete upstream or licensing evidence may pass
the integrity gate only when the manifest records that debt explicitly; it is
never reported as redistribution-ready.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import struct
import subprocess
import sys
import tarfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable


DEFAULT_MANIFEST = Path("tools/gates/vendored-native-payloads.json")
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
INSPECTION_PREFIX_BYTES = 4 * 1024 * 1024
EXPECTED_BINARY_KIND_POLICY = {
    "pe": "IMAGE_FILE_DLL (0x2000) set means shared-library; clear means executable.",
    "macho": "MH_EXECUTE (2) means executable; MH_DYLIB (6) means shared-library; every fat slice must agree.",
    "elf": (
        "ET_EXEC (2) means executable; ET_DYN (3) with PT_INTERP means dynamic PIE executable; "
        "ET_DYN without PT_INTERP means shared-library. Static PIE without PT_INTERP is intentionally "
        "unsupported and fails an executable kind claim."
    ),
}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class PayloadIntegrityError(RuntimeError):
    """Raised when payload bytes, structure, or manifest policy do not match."""


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    kind: str
    size: int
    mode: int
    compressed_size: int | None
    source: Any


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PayloadIntegrityError(message)


def _sha256_stream(handle: BinaryIO) -> tuple[int, str, bytes]:
    digest = hashlib.sha256()
    total = 0
    prefix = bytearray()
    while True:
        chunk = handle.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        digest.update(chunk)
        if len(prefix) < INSPECTION_PREFIX_BYTES:
            prefix.extend(chunk[: INSPECTION_PREFIX_BYTES - len(prefix)])
    return total, digest.hexdigest(), bytes(prefix)


def _sha256_file(path: Path) -> tuple[int, str]:
    with path.open("rb") as handle:
        size, digest, _ = _sha256_stream(handle)
    return size, digest


def _safe_member_name(raw_name: str, label: str) -> str:
    _require(isinstance(raw_name, str) and raw_name, f"{label} member name must be non-empty text")
    _require("\x00" not in raw_name, f"{label} member contains NUL: {raw_name!r}")
    _require(all(ord(character) >= 32 for character in raw_name),
             f"{label} member contains a control character: {raw_name!r}")
    _require(unicodedata.normalize("NFC", raw_name) == raw_name,
             f"{label} member is not NFC-normalized: {raw_name!r}")
    _require("\\" not in raw_name, f"{label} member uses a backslash path: {raw_name!r}")
    _require(not raw_name.startswith(("/", "//")), f"{label} member is absolute: {raw_name!r}")
    _require(not DRIVE_PREFIX.match(raw_name), f"{label} member is drive-qualified: {raw_name!r}")
    path = PurePosixPath(raw_name)
    _require(".." not in path.parts, f"{label} member traverses a parent: {raw_name!r}")
    _require("." not in path.parts, f"{label} member contains a dot segment: {raw_name!r}")
    normalized = path.as_posix()
    _require(normalized not in {"", "."}, f"{label} member resolves to the archive root")
    _require(normalized == raw_name.rstrip("/"), f"{label} member is not normalized: {raw_name!r}")
    for part in path.parts:
        _require(":" not in part, f"{label} member uses a Windows stream/device separator: {raw_name!r}")
        _require(not part.endswith((" ", ".")),
                 f"{label} member has a Windows-ambiguous trailing character: {raw_name!r}")
        device_name = part.split(".", 1)[0].upper()
        _require(device_name not in WINDOWS_RESERVED_NAMES,
                 f"{label} member uses a Windows reserved device name: {raw_name!r}")
    return normalized


def _manifest_path(raw_path: Any, label: str) -> str:
    _require(isinstance(raw_path, str), f"{label} path must be text")
    return _safe_member_name(raw_path, label)


def _hex_digest(value: Any, label: str) -> str:
    _require(isinstance(value, str) and HEX_SHA256.fullmatch(value) is not None,
             f"{label} sha256 must be a lowercase 64-character digest")
    return value


def _positive_int(value: Any, label: str, *, allow_zero: bool = False) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{label} must be an integer")
    _require(value >= (0 if allow_zero else 1), f"{label} is outside its allowed range")
    return value


def _mach_kind(file_type: int) -> str:
    kinds = {2: "executable", 6: "shared-library"}
    _require(file_type in kinds, f"unsupported Mach-O file type {file_type}")
    return kinds[file_type]


def _thin_mach_identity(prefix: bytes, offset: int = 0) -> dict[str, str]:
    _require(offset >= 0 and offset + 16 <= len(prefix),
             f"Mach-O slice header at offset {offset} is outside the inspected prefix")
    magic = prefix[offset : offset + 4]
    _require(magic in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"},
             f"unsupported Mach-O slice magic {magic.hex()} at offset {offset}")
    order = "<" if magic == b"\xcf\xfa\xed\xfe" else ">"
    cpu_type = struct.unpack_from(f"{order}I", prefix, offset + 4)[0]
    file_type = struct.unpack_from(f"{order}I", prefix, offset + 12)[0]
    machines = {0x01000007: "x86_64", 0x0100000C: "arm64"}
    _require(cpu_type in machines, f"unsupported Mach-O CPU type 0x{cpu_type:08x}")
    return {"format": "macho", "machine": machines[cpu_type], "kind": _mach_kind(file_type)}


def _fat_mach_identity(prefix: bytes) -> dict[str, str]:
    magic = prefix[:4]
    formats = {
        b"\xca\xfe\xba\xbe": (">", False),
        b"\xbe\xba\xfe\xca": ("<", False),
        b"\xca\xfe\xba\xbf": (">", True),
        b"\xbf\xba\xfe\xca": ("<", True),
    }
    _require(magic in formats, f"unsupported fat Mach-O magic {magic.hex()}")
    order, is_64 = formats[magic]
    _require(len(prefix) >= 8, "fat Mach-O header is truncated")
    slice_count = struct.unpack_from(f"{order}I", prefix, 4)[0]
    _require(1 <= slice_count <= 32, f"fat Mach-O slice count is invalid: {slice_count}")
    entry_size = 32 if is_64 else 20
    _require(8 + slice_count * entry_size <= len(prefix), "fat Mach-O architecture table is truncated")
    identities: list[dict[str, str]] = []
    for index in range(slice_count):
        entry = 8 + index * entry_size
        cpu_type = struct.unpack_from(f"{order}I", prefix, entry)[0]
        if is_64:
            slice_offset = struct.unpack_from(f"{order}Q", prefix, entry + 8)[0]
        else:
            slice_offset = struct.unpack_from(f"{order}I", prefix, entry + 8)[0]
        identity = _thin_mach_identity(prefix, slice_offset)
        expected_cpu = {0x01000007: "x86_64", 0x0100000C: "arm64"}.get(cpu_type)
        _require(expected_cpu is not None, f"unsupported fat Mach-O CPU type 0x{cpu_type:08x}")
        _require(identity["machine"] == expected_cpu,
                 f"fat Mach-O slice {index} CPU table/header mismatch")
        identities.append(identity)
    kinds = {identity["kind"] for identity in identities}
    _require(len(kinds) == 1, "fat Mach-O slices disagree on executable/shared-library kind")
    machines = "+".join(sorted({identity["machine"] for identity in identities}))
    return {"format": "macho", "machine": machines, "kind": identities[0]["kind"]}


def _elf_has_interp(prefix: bytes, elf_class: int, order: str) -> bool:
    if elf_class == 1:
        _require(len(prefix) >= 46, "ELF32 header is truncated before its program-header fields")
        program_offset = struct.unpack_from(f"{order}I", prefix, 28)[0]
        entry_size = struct.unpack_from(f"{order}H", prefix, 42)[0]
        entry_count = struct.unpack_from(f"{order}H", prefix, 44)[0]
    else:
        _require(len(prefix) >= 58, "ELF64 header is truncated before its program-header fields")
        program_offset = struct.unpack_from(f"{order}Q", prefix, 32)[0]
        entry_size = struct.unpack_from(f"{order}H", prefix, 54)[0]
        entry_count = struct.unpack_from(f"{order}H", prefix, 56)[0]
    _require(entry_count <= 1024, f"ELF program-header count is invalid: {entry_count}")
    if entry_count == 0:
        return False
    _require(entry_size >= 4, f"ELF program-header entry size is invalid: {entry_size}")
    _require(program_offset + entry_size * entry_count <= len(prefix),
             "ELF program-header table is outside the inspected prefix")
    return any(
        struct.unpack_from(f"{order}I", prefix, program_offset + index * entry_size)[0] == 3
        for index in range(entry_count)
    )


def _binary_identity(prefix: bytes) -> dict[str, str]:
    if prefix.startswith(b"MZ"):
        _require(len(prefix) >= 0x40, "PE payload header is truncated before e_lfanew")
        pe_offset = struct.unpack_from("<I", prefix, 0x3C)[0]
        _require(pe_offset + 24 <= len(prefix), "PE payload header offset is outside the inspected prefix")
        _require(prefix[pe_offset : pe_offset + 4] == b"PE\0\0", "PE payload signature is invalid")
        machine = struct.unpack_from("<H", prefix, pe_offset + 4)[0]
        characteristics = struct.unpack_from("<H", prefix, pe_offset + 22)[0]
        machines = {0x014C: "x86", 0x8664: "x86_64", 0xAA64: "arm64"}
        _require(machine in machines, f"unsupported PE machine 0x{machine:04x}")
        kind = "shared-library" if characteristics & 0x2000 else "executable"
        return {"format": "pe", "machine": machines[machine], "kind": kind}

    if prefix.startswith(b"\x7fELF"):
        _require(len(prefix) >= 20, "ELF payload header is truncated")
        elf_class = prefix[4]
        endian = prefix[5]
        _require(elf_class in {1, 2}, f"unsupported ELF class {elf_class}")
        _require(endian in {1, 2}, f"unsupported ELF endianness {endian}")
        order = "<" if endian == 1 else ">"
        elf_type = struct.unpack_from(f"{order}H", prefix, 16)[0]
        machine = struct.unpack_from(f"{order}H", prefix, 18)[0]
        machines = {3: "x86", 62: "x86_64", 183: "arm64"}
        _require(machine in machines, f"unsupported ELF machine {machine}")
        if elf_type == 2:
            kind = "executable"
        elif elf_type == 3:
            kind = "executable" if _elf_has_interp(prefix, elf_class, order) else "shared-library"
        else:
            raise PayloadIntegrityError(f"unsupported ELF e_type {elf_type}")
        return {"format": "elf", "machine": machines[machine], "kind": kind}

    if prefix[:4] in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"}:
        return _thin_mach_identity(prefix)

    if prefix[:4] in {b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}:
        return _fat_mach_identity(prefix)

    raise PayloadIntegrityError(f"selected payload has an unrecognized executable format: {prefix[:8].hex()}")


class ArchiveView:
    def __init__(self, path: Path, archive_format: str, label: str):
        self.path = path
        self.archive_format = archive_format
        self.label = label
        self._archive: zipfile.ZipFile | tarfile.TarFile | None = None
        self.members: list[ArchiveMember] = []
        self.by_name: dict[str, ArchiveMember] = {}

    def __enter__(self) -> "ArchiveView":
        try:
            if self.archive_format == "zip":
                archive = zipfile.ZipFile(self.path)
                self._archive = archive
                for info in archive.infolist():
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if info.flag_bits & 0x1:
                        kind = "encrypted"
                    elif info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                        kind = "unsupported-compression"
                    elif info.is_dir():
                        kind = "directory"
                    elif mode and stat.S_ISLNK(mode):
                        kind = "symlink"
                    elif mode and not stat.S_ISREG(mode):
                        kind = "special"
                    else:
                        kind = "file"
                    self.members.append(
                        ArchiveMember(info.filename, kind, info.file_size, mode, info.compress_size, info)
                    )
            elif self.archive_format == "tar.xz":
                archive = tarfile.open(self.path, mode="r:xz")
                self._archive = archive
                for info in archive.getmembers():
                    if info.isfile():
                        kind = "file"
                    elif info.isdir():
                        kind = "directory"
                    elif info.issym():
                        kind = "symlink"
                    elif info.islnk():
                        kind = "hardlink"
                    else:
                        kind = "special"
                    self.members.append(ArchiveMember(info.name, kind, info.size, info.mode, None, info))
            else:
                raise PayloadIntegrityError(f"{self.label} has unsupported archive_format {self.archive_format!r}")
        except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
            raise PayloadIntegrityError(f"{self.label} cannot be opened safely: {exc}") from exc

        seen: set[str] = set()
        seen_casefold: dict[str, str] = {}
        for member in self.members:
            normalized = _safe_member_name(member.name, self.label)
            _require(normalized not in seen, f"{self.label} contains duplicate member {normalized!r}")
            folded = normalized.casefold()
            _require(folded not in seen_casefold,
                     f"{self.label} contains case-colliding members {seen_casefold.get(folded)!r} and {normalized!r}")
            _require(member.kind in {"file", "directory"},
                     f"{self.label} contains forbidden {member.kind} member {normalized!r}")
            seen.add(normalized)
            seen_casefold[folded] = normalized
            self.by_name[normalized] = member
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._archive is not None:
            self._archive.close()

    def open_member(self, member: ArchiveMember) -> BinaryIO:
        _require(member.kind == "file", f"{self.label} selected member {member.name!r} is not a regular file")
        _require(self._archive is not None, "archive is not open")
        if isinstance(self._archive, zipfile.ZipFile):
            return self._archive.open(member.source, mode="r")
        handle = self._archive.extractfile(member.source)
        _require(handle is not None, f"{self.label} could not read selected member {member.name!r}")
        return handle


def _summary(members: Iterable[ArchiveMember]) -> dict[str, int]:
    rows = list(members)
    return {
        "entries": len(rows),
        "file_entries": sum(member.kind == "file" for member in rows),
        "directory_entries": sum(member.kind == "directory" for member in rows),
        "total_uncompressed_bytes": sum(member.size for member in rows if member.kind == "file"),
    }


def _validate_policy(raw: Any) -> dict[str, Any]:
    _require(isinstance(raw, dict), "integrity_policy must be an object")
    suffixes = raw.get("tracked_artifact_suffixes")
    _require(isinstance(suffixes, list) and suffixes, "tracked_artifact_suffixes must be a non-empty list")
    _require(all(isinstance(item, str) and item.startswith(".") and item == item.lower() for item in suffixes),
             "tracked_artifact_suffixes must be lowercase suffix strings")
    _require(len(suffixes) == len(set(suffixes)), "tracked_artifact_suffixes contains duplicates")
    for key in (
        "max_archive_bytes",
        "max_members",
        "max_single_member_bytes",
        "max_total_uncompressed_bytes",
        "max_member_expansion_ratio",
        "max_archive_expansion_ratio",
    ):
        _positive_int(raw.get(key), f"integrity_policy.{key}")
    _require(raw.get("binary_kind_policy") == EXPECTED_BINARY_KIND_POLICY,
             "integrity_policy.binary_kind_policy must exactly describe the enforced header rules")
    return raw


def _validate_summary(expected: Any, observed: dict[str, int], label: str) -> None:
    _require(isinstance(expected, dict), f"{label} member_summary must be an object")
    _require(set(expected) == set(observed), f"{label} member_summary fields are incomplete or unexpected")
    for key, value in observed.items():
        _positive_int(expected.get(key), f"{label} member_summary.{key}", allow_zero=True)
        _require(expected[key] == value,
                 f"{label} member_summary.{key} mismatch: expected {expected[key]}, got {value}")


def _unique_marker(text: str, marker: Any, label: str) -> int:
    _require(isinstance(marker, str) and marker, f"{label} marker must be non-empty text")
    count = text.count(marker)
    _require(count == 1, f"{label} marker must occur exactly once; found {count}: {marker!r}")
    return text.index(marker)


def _validate_consumer_claim(
    repo_root: Path,
    payload_path: Any,
    consumer: Any,
    label: str,
) -> None:
    declared_archive = _manifest_path(payload_path, f"{label}.payload")
    archive_basename = PurePosixPath(declared_archive).name
    _require(isinstance(consumer, dict), f"{label} must be an object")
    target = consumer.get("target")
    status = consumer.get("status")
    _require(isinstance(target, str) and target, f"{label} target is missing")
    _require(isinstance(status, str) and status, f"{label} status is missing")
    consumer_path = _manifest_path(consumer.get("path"), label)
    consumer_file = repo_root / consumer_path
    _require(consumer_file.is_file(), f"{label} path is missing: {consumer_path}")
    try:
        consumer_text = consumer_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise PayloadIntegrityError(f"{label} cannot read {consumer_path}: {exc}") from exc
    reference_marker = consumer.get("reference_marker")
    _require(isinstance(reference_marker, str) and reference_marker,
             f"{label}.reference_marker must be non-empty text")
    _require("\\" not in reference_marker,
             f"{label}.reference_marker must use normalized forward slashes")
    _require(
        reference_marker == archive_basename or reference_marker.endswith(f"/{archive_basename}"),
        f"{label}.reference_marker must end at the exact declared archive basename "
        f"{archive_basename!r} from {declared_archive!r}",
    )
    _unique_marker(consumer_text, reference_marker, f"{label}.reference_marker")

    documented_only = status == "documented-manual-packaging-only"
    unvalidated_local = status == "active-local-install-rule-unvalidated"
    active_release = status.startswith("active-release-")
    _require(documented_only or unvalidated_local or active_release,
             f"{label} status is not a recognized consumer contract: {status!r}")
    if documented_only:
        _require("operation_marker" not in consumer and "integrity_gate" not in consumer,
                 f"{label} documented-only consumer must not imply executable extraction coverage")
        return

    operation_marker = consumer.get("operation_marker")
    _unique_marker(consumer_text, operation_marker, f"{label}.operation_marker")
    _require(reference_marker in operation_marker,
             f"{label} operation_marker does not reference the declared archive marker")
    if unvalidated_local:
        _require("integrity_gate" not in consumer,
                 f"{label} unvalidated local rule must not imply integrity-gate coverage")
        return

    gate = consumer.get("integrity_gate")
    _require(isinstance(gate, dict), f"{label} active release consumer is missing integrity_gate")
    gate_path = _manifest_path(gate.get("path"), f"{label}.integrity_gate")
    gate_file = repo_root / gate_path
    _require(gate_file.is_file(), f"{label} integrity gate path is missing: {gate_path}")
    try:
        gate_text = gate_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise PayloadIntegrityError(f"{label} cannot read integrity gate {gate_path}: {exc}") from exc
    gate_index = _unique_marker(gate_text, gate.get("marker"), f"{label}.integrity_gate.marker")
    boundary_marker = gate.get("must_precede")
    boundary_index = _unique_marker(gate_text, boundary_marker, f"{label}.integrity_gate.must_precede")
    _require(gate_index < boundary_index,
             f"{label} integrity gate does not precede its extraction/build boundary")
    if gate_path == consumer_path:
        _require(boundary_marker == operation_marker,
                 f"{label} same-file gate boundary must equal the extraction operation_marker")


def _validate_readiness(readiness: Any, payloads: list[dict[str, Any]]) -> None:
    _require(isinstance(readiness, dict), "redistribution_readiness must be an object")
    _require(readiness.get("status") in {"blocked", "ready"}, "redistribution_readiness status is invalid")
    _require(readiness.get("enforcement") in {"advisory", "required"},
             "redistribution_readiness enforcement is invalid")
    blockers = readiness.get("blockers")
    _require(isinstance(blockers, list), "redistribution_readiness blockers must be a list")
    if readiness["status"] == "blocked":
        _require(readiness["enforcement"] == "advisory",
                 "blocked redistribution readiness must remain advisory for this byte-integrity slice")
        _require(all(isinstance(item, str) and item for item in blockers) and blockers,
                 "blocked redistribution readiness must list explicit blockers")
        _require(all(str(record.get("redistribution_readiness", "")).startswith("blocked-") for record in payloads),
                 "every payload must remain explicitly blocked while top-level readiness is blocked")
        return

    _require(readiness["enforcement"] == "required",
             "ready redistribution status must be enforced, not advisory")
    _require(not blockers, "ready redistribution status cannot retain blockers")
    for record in payloads:
        record_id = str(record.get("id") or "payload")
        provenance = record.get("provenance")
        _require(isinstance(provenance, dict), f"{record_id} provenance must be an object")
        _require(record.get("redistribution_readiness") == "ready",
                 f"{record_id} is not redistribution-ready")
        _require(provenance.get("status") == "verified",
                 f"{record_id} provenance is not verified")
        _require(provenance.get("upstream_checksum_verified") is True,
                 f"{record_id} upstream checksum is not verified")
        for field in ("version", "source", "source_evidence", "build_recipe", "license", "license_evidence"):
            value = provenance.get(field)
            _require(isinstance(value, str) and value and value != "unknown",
                     f"{record_id} provenance {field} is not verified")
        _require(provenance.get("license_notice_in_archive") is True,
                 f"{record_id} does not carry its verified license notice")


def _validate_provenance(record: dict[str, Any]) -> None:
    label = str(record.get("id") or "payload")
    provenance = record.get("provenance")
    _require(isinstance(provenance, dict), f"{label} provenance must be an object")
    _require(provenance.get("status") in {"unknown", "partial-local-evidence", "verified"},
             f"{label} provenance status is invalid")

    authoritative = provenance.get("authoritative_source_archive")
    if authoritative is not None:
        _require(isinstance(authoritative, dict),
                 f"{label} authoritative_source_archive must be an object")
        _require(set(authoritative) == {"url", "bytes", "sha256", "tracked_archive_match", "verified_at"},
                 f"{label} authoritative_source_archive fields are incomplete or unexpected")
        _require(authoritative.get("url") == provenance.get("source"),
                 f"{label} authoritative source URL must equal provenance.source")
        _require(authoritative.get("bytes") == record.get("bytes"),
                 f"{label} authoritative source size must equal the tracked archive")
        _require(authoritative.get("sha256") == record.get("sha256"),
                 f"{label} authoritative source sha256 must equal the tracked archive")
        _require(authoritative.get("tracked_archive_match") == "byte-for-byte",
                 f"{label} authoritative source match must be byte-for-byte")
        _require(re.fullmatch(r"20\d\d-[01]\d-[0-3]\d", str(authoritative.get("verified_at"))) is not None,
                 f"{label} authoritative source verification date is invalid")
        _require(provenance.get("status") == "partial-local-evidence",
                 f"{label} archive equality alone must not promote provenance to verified")
        _require(provenance.get("upstream_checksum_verified") is False,
                 f"{label} archive equality must not claim an independently published upstream checksum")

    candidate = provenance.get("candidate_source_mapping")
    if candidate is not None:
        _require(isinstance(candidate, dict), f"{label} candidate_source_mapping must be an object")
        _require(candidate.get("authoritativeness") == "non-authoritative-unverified",
                 f"{label} candidate source mapping must remain explicitly non-authoritative and unverified")
        _require(provenance.get("status") != "verified",
                 f"{label} candidate source mapping cannot promote provenance to verified")
        _require(str(record.get("redistribution_readiness", "")).startswith("blocked-"),
                 f"{label} candidate source mapping cannot reduce its redistribution blocker")
        for key in ("repository", "evidence", "limitation"):
            _require(isinstance(candidate.get(key), str) and candidate[key],
                     f"{label} candidate source mapping {key} is missing")
        revisions = [value for key, value in candidate.items() if key.endswith("revision")]
        _require(revisions and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)
                                   for value in revisions),
                 f"{label} candidate source revisions must be full 40-character commit ids")


def _validate_archive(repo_root: Path, record: dict[str, Any], policy: dict[str, Any], *, selected: bool) -> None:
    label = str(record.get("id") or record.get("path") or "payload")
    relative = _manifest_path(record.get("path"), label)
    archive_path = repo_root / Path(relative)
    _require(archive_path.is_file(), f"{label} archive is missing: {relative}")
    expected_bytes = _positive_int(record.get("bytes"), f"{label}.bytes")
    expected_hash = _hex_digest(record.get("sha256"), label)
    _require(expected_bytes <= policy["max_archive_bytes"],
             f"{label} declared archive bytes exceed max_archive_bytes")
    actual_bytes, actual_hash = _sha256_file(archive_path)
    _require(actual_bytes == expected_bytes,
             f"{label} archive size mismatch: expected {expected_bytes}, got {actual_bytes}")
    _require(actual_hash == expected_hash,
             f"{label} archive sha256 mismatch: expected {expected_hash}, got {actual_hash}")

    archive_format = record.get("archive_format")
    if archive_format is None:
        archive_format = "tar.xz" if relative.lower().endswith(".tar.xz") else "zip"
    _require(archive_format in {"zip", "tar.xz"}, f"{label} archive_format is invalid")
    _require(relative.lower().endswith(".zip") if archive_format == "zip" else relative.lower().endswith(".tar.xz"),
             f"{label} archive_format does not match its path")

    with ArchiveView(archive_path, archive_format, label) as archive:
        observed = _summary(archive.members)
        _validate_summary(record.get("member_summary"), observed, label)
        _require(observed["entries"] <= policy["max_members"], f"{label} exceeds max_members")
        _require(observed["total_uncompressed_bytes"] <= policy["max_total_uncompressed_bytes"],
                 f"{label} exceeds max_total_uncompressed_bytes")
        _require(observed["total_uncompressed_bytes"] / max(1, actual_bytes) <= policy["max_archive_expansion_ratio"],
                 f"{label} exceeds max_archive_expansion_ratio")
        for member in archive.members:
            if member.kind != "file":
                continue
            _require(member.size <= policy["max_single_member_bytes"],
                     f"{label} member {member.name!r} exceeds max_single_member_bytes")
            if member.compressed_size is not None:
                ratio = member.size / max(1, member.compressed_size)
                _require(ratio <= policy["max_member_expansion_ratio"],
                         f"{label} member {member.name!r} exceeds max_member_expansion_ratio")

        if not selected:
            _require("selected_members" not in record, f"{label} inactive artifact must not select runtime payloads")
            return

        selected_members = record.get("selected_members")
        _require(isinstance(selected_members, list) and selected_members,
                 f"{label} selected_members must be a non-empty list")
        selected_names: set[str] = set()
        output_names: set[str] = set()
        for index, selected_record in enumerate(selected_members):
            item_label = f"{label}.selected_members[{index}]"
            _require(isinstance(selected_record, dict), f"{item_label} must be an object")
            member_name = _manifest_path(selected_record.get("path"), item_label)
            _require(member_name not in selected_names, f"{label} selects member {member_name!r} more than once")
            selected_names.add(member_name)
            output_name = _manifest_path(selected_record.get("output_name"), item_label)
            _require("/" not in output_name, f"{item_label}.output_name must be a basename")
            _require(output_name.casefold() not in output_names, f"{label} output_name collision: {output_name!r}")
            output_names.add(output_name.casefold())
            _require(selected_record.get("kind") in {"executable", "shared-library"},
                     f"{item_label}.kind is invalid")
            _positive_int(selected_record.get("bytes"), f"{item_label}.bytes")
            _hex_digest(selected_record.get("sha256"), item_label)
            member = archive.by_name.get(member_name)
            _require(member is not None, f"{label} selected member is missing: {member_name!r}")
            _require(member.kind == "file", f"{label} selected member is not a regular file: {member_name!r}")
            _require(member.size == selected_record["bytes"],
                     f"{item_label} size mismatch: expected {selected_record['bytes']}, got {member.size}")
            if selected_record.get("executable") is True:
                _require(member.mode & 0o111 != 0, f"{item_label} lost its executable mode")
            with archive.open_member(member) as handle:
                member_bytes, member_hash, prefix = _sha256_stream(handle)
            _require(member_bytes == selected_record["bytes"], f"{item_label} streamed size mismatch")
            _require(member_hash == selected_record["sha256"],
                     f"{item_label} sha256 mismatch: expected {selected_record['sha256']}, got {member_hash}")
            expected_binary = selected_record.get("binary")
            _require(isinstance(expected_binary, dict) and set(expected_binary) == {"format", "machine"},
                     f"{item_label}.binary must contain exactly format and machine")
            actual_binary = _binary_identity(prefix)
            actual_format_machine = {
                "format": actual_binary["format"],
                "machine": actual_binary["machine"],
            }
            _require(actual_format_machine == expected_binary,
                     f"{item_label} binary identity mismatch: expected {expected_binary}, got {actual_format_machine}")
            _require(actual_binary["kind"] == selected_record["kind"],
                     f"{item_label} binary kind mismatch: manifest declares {selected_record['kind']}, "
                     f"header identifies {actual_binary['kind']}")


def _git_tracked_artifacts(repo_root: Path, suffixes: list[str]) -> set[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PayloadIntegrityError(f"cannot enumerate Git-tracked payload candidates: {exc}") from exc
    paths = [path.replace("\\", "/") for path in completed.stdout.decode("utf-8").split("\0") if path]
    artifacts: set[str] = set()
    archive_magics = (
        b"PK\x03\x04",
        b"PK\x05\x06",
        b"PK\x07\x08",
        b"\xfd7zXZ\x00",
        b"7z\xbc\xaf\x27\x1c",
        b"Rar!\x1a\x07",
        b"!<arch>\n",
    )
    native_magics = {
        b"\x7fELF",
        b"\xcf\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    }
    for relative in paths:
        if any(relative.lower().endswith(suffix) for suffix in suffixes):
            artifacts.add(relative)
            continue
        path = repo_root / Path(relative)
        try:
            with path.open("rb") as handle:
                prefix = handle.read(512)
        except OSError as exc:
            raise PayloadIntegrityError(f"cannot inspect Git-tracked file {relative}: {exc}") from exc
        if prefix.startswith(archive_magics) or prefix[:4] in native_magics:
            artifacts.add(relative)
            continue
        if prefix.startswith(b"MZ"):
            try:
                _binary_identity(prefix)
            except PayloadIntegrityError:
                continue
            artifacts.add(relative)
    return artifacts


def _parse_manifest_bytes(raw: bytes, label: str) -> dict[str, Any]:
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PayloadIntegrityError(f"cannot parse payload manifest {label}: {exc}") from exc
    _require(isinstance(manifest, dict), "payload manifest root must be an object")
    return manifest


def validate_manifest(repo_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate an already-parsed immutable manifest snapshot."""
    repo_root = repo_root.resolve()
    _require(manifest.get("schema_version") == 1, "payload manifest schema_version must be 1")
    policy = _validate_policy(manifest.get("integrity_policy"))

    payloads = manifest.get("payloads")
    inactive = manifest.get("inactive_artifacts")
    _require(isinstance(payloads, list) and payloads, "payloads must be a non-empty list")
    _require(isinstance(inactive, list), "inactive_artifacts must be a list")
    records = payloads + inactive
    _require(all(isinstance(record, dict) for record in records), "every payload record must be an object")
    declared_paths = [_manifest_path(record.get("path"), "payload record") for record in records]
    _require(len(declared_paths) == len(set(declared_paths)), "payload manifest declares a path more than once")
    payload_ids = [record.get("id") for record in payloads]
    _require(all(isinstance(value, str) and value for value in payload_ids), "every active payload needs an id")
    _require(len(payload_ids) == len(set(payload_ids)), "payload ids must be unique")

    tracked = _git_tracked_artifacts(repo_root, policy["tracked_artifact_suffixes"])
    declared = set(declared_paths)
    _require(tracked == declared,
             "tracked native/archive inventory mismatch: "
             f"unmanifested={sorted(tracked - declared)}, missing_from_git={sorted(declared - tracked)}")

    for record in payloads:
        _validate_archive(repo_root, record, policy, selected=True)
        consumers = record.get("consumers")
        _require(isinstance(consumers, list) and consumers, f"{record['id']} consumers must be a non-empty list")
        for index, consumer in enumerate(consumers):
            _validate_consumer_claim(
                repo_root,
                record["path"],
                consumer,
                f"{record['id']} consumer[{index}]",
            )
        _validate_provenance(record)
    for record in inactive:
        _validate_archive(repo_root, record, policy, selected=False)

    readiness = manifest.get("redistribution_readiness")
    _validate_readiness(readiness, payloads)
    assert isinstance(readiness, dict)
    blockers = readiness["blockers"]
    return {
        "integrity": "pass",
        "payload_count": len(payloads),
        "inactive_artifact_count": len(inactive),
        "redistribution_readiness": readiness["status"],
        "redistribution_enforcement": readiness["enforcement"],
        "redistribution_blockers": blockers,
    }


def validate(repo_root: Path, manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    manifest_file = manifest_path if manifest_path.is_absolute() else repo_root / manifest_path
    try:
        raw = manifest_file.read_bytes()
    except OSError as exc:
        raise PayloadIntegrityError(f"cannot read payload manifest {manifest_file}: {exc}") from exc
    return validate_manifest(repo_root, _parse_manifest_bytes(raw, str(manifest_file)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--require-redistribution-ready",
        action="store_true",
        help="also fail when the separately tracked redistribution-readiness status is blocked",
    )
    args = parser.parse_args(argv)
    try:
        result = validate(args.repo_root, args.manifest)
    except PayloadIntegrityError as exc:
        print(f"vendored native payload integrity: FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.require_redistribution_ready and result["redistribution_readiness"] != "ready":
        print("vendored native payload redistribution readiness: BLOCKED", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
