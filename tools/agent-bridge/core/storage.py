import json
import os
import shutil
import stat
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional


STATE_SCHEMA_VERSION = 1
INBOX_SCHEMA_VERSION = 1
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


class UnsafeStoragePathError(OSError):
    """Raised when bridge state would traverse a link or Windows reparse point."""


def _is_windows(platform_name: Optional[str] = None) -> bool:
    return (platform_name or os.name) == "nt"


def _path_is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = os.lstat(str(path))
    except (FileNotFoundError, NotADirectoryError):
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


def _absolute_components(path: Path) -> Iterator[Path]:
    absolute = Path(os.path.abspath(str(path)))
    parts = absolute.parts
    if not parts:
        return
    current = Path(parts[0])
    yield current
    for part in parts[1:]:
        current = current / part
        yield current


def _first_link_component(path: Path) -> Optional[Path]:
    for component in _absolute_components(path):
        if _path_is_link_or_reparse(component):
            return component
    return None


def reject_link_components(path: Path) -> None:
    link = _first_link_component(Path(path))
    if link is not None:
        raise UnsafeStoragePathError(
            "agent-bridge state path contains a symlink or reparse point: %s" % link
        )


def _chmod_no_follow(path: Path, mode: int) -> None:
    reject_link_components(path)
    if os.chmod in os.supports_follow_symlinks:
        os.chmod(path, mode, follow_symlinks=False)
    else:
        os.chmod(path, mode)


def ensure_private_directory(path: Path) -> None:
    """Create a bridge-owned directory and enforce owner-only POSIX access.

    Windows ACLs are intentionally not rewritten here.  A correct DACL depends
    on the Desktop/AppContainer identities that need bridge access, and blindly
    replacing inherited ACEs can lock the bridge out or require privileges the
    current process does not have.  Use ``audit_private_path`` to fail closed
    when a deployment requires a verified Windows ACL.
    """
    target = Path(path)
    reject_link_components(target)
    missing: List[Path] = []
    cursor = target
    while not cursor.exists() and cursor.parent != cursor:
        missing.append(cursor)
        cursor = cursor.parent
    target.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)
    reject_link_components(target)
    if not _is_windows():
        for created in reversed(missing):
            _chmod_no_follow(created, PRIVATE_DIRECTORY_MODE)
        if not missing:
            _chmod_no_follow(target, PRIVATE_DIRECTORY_MODE)


def ensure_private_file(path: Path) -> None:
    """Enforce owner-only POSIX access on an existing bridge state file."""
    target = Path(path)
    reject_link_components(target)
    if target.exists() and not _is_windows():
        _chmod_no_follow(target, PRIVATE_FILE_MODE)


def open_private_text(path: Path, mode: str):
    """Open a state file for append or replacement with mode 0600 on POSIX."""
    if mode not in {"a", "w"}:
        raise ValueError("private text files may only be opened for append or write")
    target = Path(path)
    reject_link_components(target)
    ensure_private_directory(target.parent)
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_APPEND if mode == "a" else os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(target), flags, PRIVATE_FILE_MODE)
    try:
        if not _is_windows():
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
        return os.fdopen(descriptor, mode, encoding="utf-8", newline="\n")
    except Exception:
        os.close(descriptor)
        raise


def open_private_read_text(path: Path, *, encoding: str = "utf-8", errors: Optional[str] = None):
    """Open a bridge state file without following its final link component."""
    target = Path(path)
    ensure_private_file(target)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(target), flags)
    try:
        return os.fdopen(descriptor, "r", encoding=encoding, errors=errors, newline="")
    except Exception:
        os.close(descriptor)
        raise


def copy_private_file(source: Path, target: Path) -> None:
    """Copy one bridge state file without a permissive destination window."""
    source_path = Path(source)
    target_path = Path(target)
    ensure_private_file(source_path)
    reject_link_components(target_path)
    ensure_private_directory(target_path.parent)
    binary = getattr(os, "O_BINARY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(str(source_path), os.O_RDONLY | binary | no_follow)
    target_created = False
    try:
        target_fd = os.open(
            str(target_path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary | no_follow,
            PRIVATE_FILE_MODE,
        )
        target_created = True
        try:
            if not _is_windows():
                os.fchmod(target_fd, PRIVATE_FILE_MODE)
            with os.fdopen(source_fd, "rb") as source_handle:
                source_fd = -1
                with os.fdopen(target_fd, "wb") as target_handle:
                    target_fd = -1
                    shutil.copyfileobj(source_handle, target_handle)
        finally:
            if target_fd >= 0:
                os.close(target_fd)
    except Exception:
        if target_created:
            try:
                target_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    finally:
        if source_fd >= 0:
            os.close(source_fd)


def audit_private_path(path: Path, *, platform_name: Optional[str] = None) -> Dict[str, Any]:
    """Return an executable, fail-closed confidentiality check for one path."""
    target = Path(path)
    platform = platform_name or os.name
    link = _first_link_component(target)
    if link is not None:
        return {
            "ok": False,
            "status": "link_or_reparse",
            "path": str(target),
            "link_component": str(link),
            "platform": platform,
        }
    if not target.exists():
        return {
            "ok": False,
            "status": "missing",
            "path": str(target),
            "platform": platform,
        }
    if _is_windows(platform):
        return {
            "ok": False,
            "status": "windows_acl_unverified",
            "path": str(target),
            "platform": platform,
            "message": (
                "Windows DACLs are inherited and are not rewritten by agent-bridge; "
                "verify the bridge root's allowed principals before treating it as secret storage."
            ),
        }
    metadata = os.lstat(str(target))
    actual_mode = stat.S_IMODE(metadata.st_mode)
    expected_mode = PRIVATE_DIRECTORY_MODE if target.is_dir() else PRIVATE_FILE_MODE
    return {
        "ok": actual_mode == expected_mode,
        "status": "private" if actual_mode == expected_mode else "mode_mismatch",
        "path": str(target),
        "platform": platform,
        "actual_mode": format(actual_mode, "04o"),
        "expected_mode": format(expected_mode, "04o"),
    }


def audit_private_tree(root: Path, *, platform_name: Optional[str] = None) -> Dict[str, Any]:
    """Audit the bridge root without mutating permissions or following symlinks."""
    target = Path(root)
    paths = [target]
    if target.exists() and target.is_dir() and _first_link_component(target) is None:
        for directory, names, files in os.walk(target, followlinks=False):
            base = Path(directory)
            retained_names: List[str] = []
            for name in names:
                child = base / name
                if _path_is_link_or_reparse(child):
                    paths.append(child)
                else:
                    retained_names.append(name)
                    if not _is_windows(platform_name):
                        paths.append(child)
            names[:] = retained_names
            if not _is_windows(platform_name):
                paths.extend(base / name for name in files)
            else:
                paths.extend(base / name for name in files if _path_is_link_or_reparse(base / name))
    results = [audit_private_path(path, platform_name=platform_name) for path in paths]
    return {
        "ok": bool(results) and all(result["ok"] for result in results),
        "status": "private" if results and all(result["ok"] for result in results) else "not_verified",
        "root": str(target),
        "results": results,
    }


def _lock_path_for(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


@contextmanager
def file_lock(path: Path, timeout_seconds: float = 30.0, stale_seconds: float = 120.0) -> Iterator[None]:
    """Small cross-process file lock using exclusive directory creation."""
    lock_path = _lock_path_for(path)
    ensure_private_directory(lock_path.parent)
    reject_link_components(lock_path)
    start = time.time()
    while True:
        try:
            lock_path.mkdir(mode=PRIVATE_DIRECTORY_MODE)
            if not _is_windows():
                os.chmod(lock_path, PRIVATE_DIRECTORY_MODE)
            break
        except FileExistsError:
            reject_link_components(lock_path)
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > stale_seconds:
                    lock_path.rmdir()
                    continue
            except OSError:
                pass
            if time.time() - start > timeout_seconds:
                raise TimeoutError("timed out waiting for storage lock %s" % lock_path)
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock_path.rmdir()
        except OSError:
            pass


def _temp_path_for(path: Path) -> Path:
    return path.with_name(
        "%s.%s.%s.%s.tmp" % (path.name, os.getpid(), threading.get_ident(), uuid.uuid4().hex)
    )


def atomic_replace(src: Path, dst: Path) -> None:
    """Replace dst with src, using a Windows-safe fallback when needed."""
    reject_link_components(src)
    reject_link_components(dst)
    if sys.platform == "win32":
        shutil.move(str(src), str(dst))
    else:
        src.replace(dst)


def read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        ensure_private_file(path)
        if not path.exists():
            return dict(default)
        with file_lock(path):
            with open_private_read_text(path) as handle:
                data = json.load(handle)
    except (OSError, JSONDecodeError):
        return dict(default)
    if not isinstance(data, dict):
        return dict(default)
    return data


def write_json(path: Path, value: Dict[str, Any]) -> None:
    ensure_private_directory(path.parent)
    with file_lock(path):
        tmp = _temp_path_for(path)
        try:
            with open_private_text(tmp, "w") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
            atomic_replace(tmp, path)
            ensure_private_file(path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def update_json(
    path: Path,
    default: Dict[str, Any],
    updater: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> Dict[str, Any]:
    """Read/modify/write one JSON object while holding the file lock."""
    ensure_private_directory(path.parent)
    with file_lock(path):
        data = dict(default)
        if path.exists():
            try:
                with open_private_read_text(path) as handle:
                    parsed = json.load(handle)
                if isinstance(parsed, dict):
                    data = parsed
            except (OSError, JSONDecodeError):
                data = dict(default)
        updated = updater(data)
        if not isinstance(updated, dict):
            raise ValueError("updater must return a JSON object")
        tmp = _temp_path_for(path)
        try:
            with open_private_text(tmp, "w") as handle:
                json.dump(updated, handle, indent=2, sort_keys=True)
                handle.write("\n")
            atomic_replace(tmp, path)
            ensure_private_file(path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return updated


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    ensure_private_file(path)
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    quarantine: List[str] = []
    with file_lock(path):
        with open_private_read_text(path) as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        rows.append(parsed)
                    else:
                        quarantine.append(line)
                except JSONDecodeError:
                    quarantine.append(line)
        if quarantine:
            qpath = path.with_suffix(".quarantine.jsonl")
            with open_private_text(qpath, "a") as handle:
                for bad in quarantine:
                    handle.write(bad)
                    handle.write("\n")
    return rows


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    ensure_private_directory(path.parent)
    with file_lock(path):
        with open_private_text(path, "a") as handle:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    ensure_private_directory(path.parent)
    with file_lock(path):
        tmp = _temp_path_for(path)
        try:
            with open_private_text(tmp, "w") as handle:
                for row in rows:
                    handle.write(json.dumps(row, sort_keys=True))
                    handle.write("\n")
            atomic_replace(tmp, path)
            ensure_private_file(path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def with_schema_version(value: Dict[str, Any], version: int = STATE_SCHEMA_VERSION) -> Dict[str, Any]:
    copied = dict(value)
    copied.setdefault("schema_version", version)
    return copied
