#!/usr/bin/env python3
"""Resolve an owner-consented clip id to its verified parts, by id only -- ATTR3-FOOTAGE-BIND-1.

This is PR-A of a three-PR chain.  It answers exactly one question, safely: for a clip id, does
the frozen spec (``tools/gates/output-budget.json`` at a pinned ref) agree with the hook's frozen
consent table (``OWNER_CONSENTED_FOOTAGE`` in ``tools/hooks/mlv-never-authorized.py``) on every
part's length, content sha256 and the sha256 of the hook's own ``norm()`` of the part's path?  It
never opens, hashes or reads footage bytes itself -- that is the presence job's job, on a
different host.

THE SPEC IS READ FROM A GIT REF, NEVER THE WORKING TREE, and never a short ref name: the caller
must always pass (or accept the default) full ref
``refs/remotes/fork/master``, so the bytes resolved are the reviewed, merged bytes, not whatever a
worktree happens to hold.

NO PATH IS EVER PRINTED.  ``resolve()`` returns real paths (parts carry them, and
``--emit-json`` writes them to a file for a generator to consume), but the CLI's own stdout in
``--clip-id`` summary mode reports each part by index, length and a 12-character sha256 prefix
only -- exactly the shape ``verify_consented_footage.py``'s CLI already uses for the same reason.

Exit codes: 0 ok; 1 refused (any typed ``ResolveError``); 2 usage error.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

_GATES_DIR = os.path.dirname(os.path.abspath(__file__))
if _GATES_DIR not in sys.path:
    sys.path.insert(0, _GATES_DIR)

from verify_consented_footage import load_consent_table  # noqa: E402

HOOK_PATH = os.path.join(os.path.dirname(_GATES_DIR), "hooks", "mlv-never-authorized.py")

DEFAULT_REF = "refs/remotes/fork/master"
SPEC_PATH_IN_REPO = "tools/gates/output-budget.json"
SPEC_CAP_BYTES = 4 * 1024 * 1024

PASS = "PASS"
LENGTH_MISMATCH = "LENGTH_MISMATCH"
SHA256_MISMATCH = "SHA256_MISMATCH"
PATH_NORM_MISMATCH = "PATH_NORM_MISMATCH"


class ResolveError(Exception):
    """Base of every typed refusal.  ``code`` is the machine-readable reason."""

    code = "RESOLVE_ERROR"

    def __init__(self, clip_id, detail=""):
        self.clip_id = clip_id
        self.detail = detail
        super().__init__(("%s: clip_id=%r %s" % (self.code, clip_id, detail)).strip())


class UnknownClipError(ResolveError):
    """The id is not present in the spec's ``clips[]`` at all."""

    code = "UNKNOWN_ID"


class NotConsentedError(ResolveError):
    """The id is in the spec but absent from the hook's frozen consent table."""

    code = "NOT_CONSENTED"


class ZeroPartsError(ResolveError):
    """The spec names the clip but lists zero parts for it (e.g. M02-1344)."""

    code = "ZERO_PARTS"


class PartCountMismatchError(ResolveError):
    """The spec's part count for this id does not equal the consent table's."""

    code = "PART_COUNT_MISMATCH"

    def __init__(self, clip_id, spec_count, table_count):
        self.spec_count = spec_count
        self.table_count = table_count
        super().__init__(clip_id, "spec=%d table=%d" % (spec_count, table_count))


class PartMismatchError(ResolveError):
    """At least one part failed length, sha256 or path-norm cross-check against the table."""

    code = "PART_MISMATCH"

    def __init__(self, clip_id, parts):
        self.parts = parts
        super().__init__(clip_id)


class SpecTooLargeError(ResolveError):
    """The spec exceeds the stdout cap and is refused unread."""

    code = "SPEC_TOO_LARGE"


class SpecReadError(ResolveError):
    """``git show`` of the pinned ref failed."""

    code = "SPEC_READ_ERROR"


class SpecParseError(ResolveError):
    """The spec bytes are not valid UTF-8 JSON."""

    code = "SPEC_PARSE_ERROR"


class ResolvedPart(object):
    __slots__ = ("index", "path", "length", "sha256", "status")

    def __init__(self, index, path, length, sha256, status):
        self.index = index
        self.path = path
        self.length = length
        self.sha256 = sha256
        self.status = status


def _git_show_argv(repo_root, ref):
    """Pure argv builder, so a test can assert the exact command without running git."""
    return [
        "git",
        "-C",
        str(repo_root),
        "--no-replace-objects",
        "show",
        "%s:%s" % (ref, SPEC_PATH_IN_REPO),
    ]


def _read_spec_bytes(repo_root, ref):
    argv = _git_show_argv(repo_root, ref)
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise SpecReadError(None, proc.stderr.decode("utf-8", "replace").strip())
    return proc.stdout


def _load_spec(repo_root, ref, spec_bytes):
    raw = _read_spec_bytes(repo_root, ref) if spec_bytes is None else spec_bytes
    if len(raw) > SPEC_CAP_BYTES:
        raise SpecTooLargeError(None, "%d bytes exceeds cap %d" % (len(raw), SPEC_CAP_BYTES))
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpecParseError(None, str(exc)) from exc


def _find_clip(spec, clip_id):
    for clip in spec.get("clips", []):
        if clip.get("id") == clip_id:
            return clip
    raise UnknownClipError(clip_id)


def _load_norm():
    """The hook's own ``norm``, loaded by path -- never a port of it (see module docstring)."""
    spec = importlib.util.spec_from_file_location("_mlv_never_authorized_for_resolver", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.norm


def resolve(clip_id, repo_root, ref=DEFAULT_REF, spec_bytes=None, table=None):
    """-> list of ``ResolvedPart`` (path, length, sha256, in part order) when every part matches.

    Raises a typed ``ResolveError`` subclass on any refusal.  ``spec_bytes`` and ``table`` exist
    for tests only; production callers pass neither and get the pinned ref and the hook's frozen
    table.
    """
    spec = _load_spec(repo_root, ref, spec_bytes)
    clip = _find_clip(spec, clip_id)
    spec_parts = clip.get("parts") or []
    if not spec_parts:
        raise ZeroPartsError(clip_id)

    table = load_consent_table() if table is None else table
    table_parts = table.get("clips", {}).get(clip_id)
    if not table_parts:
        raise NotConsentedError(clip_id)

    if len(spec_parts) != len(table_parts):
        raise PartCountMismatchError(clip_id, len(spec_parts), len(table_parts))

    norm = _load_norm()
    results = []
    any_mismatch = False
    for index, (spec_part, table_part) in enumerate(zip(spec_parts, table_parts)):
        path = spec_part["path"]
        length = spec_part["length"]
        spec_sha = str(spec_part["sha256"]).lower()
        table_length, table_sha, table_path_norm_sha = table_part
        table_sha = table_sha.lower()
        table_path_norm_sha = table_path_norm_sha.lower()

        if length != table_length:
            status = LENGTH_MISMATCH
        elif spec_sha != table_sha:
            status = SHA256_MISMATCH
        elif hashlib.sha256(norm(path).encode("utf-8")).hexdigest() != table_path_norm_sha:
            status = PATH_NORM_MISMATCH
        else:
            status = PASS

        if status != PASS:
            any_mismatch = True
        results.append(ResolvedPart(index=index, path=path, length=length, sha256=spec_sha, status=status))

    if any_mismatch:
        raise PartMismatchError(clip_id, tuple(results))
    return results


def _summary_json(clip_id, status, parts):
    return json.dumps(
        {
            "clipId": clip_id,
            "ok": status == PASS,
            "status": status,
            "partCount": len(parts),
            "parts": [
                {"index": part.index, "length": part.length, "sha256_12": part.sha256[:12], "status": part.status}
                for part in parts
            ],
        },
        sort_keys=True,
    )


def _write_emit_json(path, clip_id, parts):
    payload = {
        "clipId": clip_id,
        "parts": [
            {"index": part.index, "path": part.path, "length": part.length, "sha256": part.sha256}
            for part in parts
        ],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--clip-id", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--ref", default=DEFAULT_REF)
    parser.add_argument("--emit-json", default=None, help="write the full resolved parts, paths included, to FILE")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0

    try:
        parts = resolve(args.clip_id, args.repo_root, ref=args.ref)
    except PartMismatchError as exc:
        sys.stdout.write(_summary_json(args.clip_id, exc.code, exc.parts) + "\n")
        return 1
    except ResolveError as exc:
        sys.stdout.write(_summary_json(args.clip_id, exc.code, ()) + "\n")
        return 1

    sys.stdout.write(_summary_json(args.clip_id, PASS, parts) + "\n")
    if args.emit_json:
        _write_emit_json(args.emit_json, args.clip_id, parts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
