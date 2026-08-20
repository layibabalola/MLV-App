import json
import os
import shutil
import stat
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple


STATE_SCHEMA_VERSION = 1
INBOX_SCHEMA_VERSION = 1
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
_POSIX_BEFORE_FINAL_OPEN_HOOK: Optional[Callable[[Path], None]] = None


class UnsafeStoragePathError(OSError):
    """Raised when bridge state escapes its root or traverses a link/reparse point."""


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


def _canonical_absolute_path(path: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise UnsafeStoragePathError("agent-bridge storage paths must be absolute: %s" % candidate)
    if any(part == ".." for part in candidate.parts):
        raise UnsafeStoragePathError("agent-bridge storage paths may not contain '..': %s" % candidate)
    return Path(os.path.abspath(os.path.normpath(str(candidate))))


def _path_identity(path: Path) -> Optional[Tuple[int, int]]:
    try:
        metadata = os.lstat(str(path))
    except (FileNotFoundError, NotADirectoryError):
        return None
    return (int(metadata.st_dev), int(metadata.st_ino))


@dataclass(frozen=True, slots=True)
class StorageCapability:
    """Immutable authority for one caller-selected bridge root.

    The bridge is a local trusted-user service.  Root selection therefore
    remains a top-level configuration decision, while every lower-level sink
    must receive this explicit capability.  No process-global authorization is
    retained, so resolving or using one bridge cannot widen another bridge's
    authority.
    """

    root: Path
    root_identity: Optional[Tuple[int, int]] = field(init=False, default=None)

    def __post_init__(self) -> None:
        canonical = _canonical_absolute_path(Path(self.root))
        if canonical.parent == canonical:
            raise UnsafeStoragePathError("the filesystem root cannot be an agent-bridge storage root")
        link = _first_link_component(canonical)
        if link is not None:
            raise UnsafeStoragePathError(
                "agent-bridge state path contains a symlink or reparse point: %s" % link
            )
        if canonical.exists() and not canonical.is_dir():
            raise UnsafeStoragePathError("agent-bridge storage root is not a directory: %s" % canonical)
        object.__setattr__(self, "root", canonical)
        object.__setattr__(self, "root_identity", _path_identity(canonical))

    @classmethod
    def bind_trusted(cls, root: Path) -> "StorageCapability":
        return cls(Path(root))

    def validate(self, path: Path) -> Path:
        canonical = _canonical_absolute_path(Path(path))
        try:
            canonical.relative_to(self.root)
        except ValueError as exc:
            raise UnsafeStoragePathError(
                "agent-bridge storage path is outside its authorized root %s: %s"
                % (self.root, canonical)
            ) from exc
        link = _first_link_component(canonical)
        if link is not None:
            raise UnsafeStoragePathError(
                "agent-bridge state path contains a symlink or reparse point: %s" % link
            )
        if self.root_identity is not None:
            current_identity = _path_identity(self.root)
            if current_identity != self.root_identity:
                raise UnsafeStoragePathError(
                    "agent-bridge storage root identity changed after binding: %s" % self.root
                )
        return canonical

    def reject_link_components(self, path: Path) -> None:
        self.validate(path)

    def ensure_private_directory(self, path: Path) -> None:
        ensure_private_directory(path, storage=self)

    def ensure_private_file(self, path: Path) -> None:
        ensure_private_file(path, storage=self)

    def open_private_text(self, path: Path, mode: str):
        return open_private_text(path, mode, storage=self)

    def open_private_read_text(
        self, path: Path, *, encoding: str = "utf-8", errors: Optional[str] = None
    ):
        return open_private_read_text(path, encoding=encoding, errors=errors, storage=self)

    def open_readonly_text(
        self, path: Path, *, encoding: str = "utf-8", errors: Optional[str] = None
    ):
        return open_readonly_text(path, encoding=encoding, errors=errors, storage=self)

    def iter_jsonl_readonly(self, path: Path) -> Iterator[Dict[str, Any]]:
        return iter_jsonl_readonly(path, storage=self)

    def copy_private_file(self, source: Path, target: Path) -> None:
        copy_private_file(source, target, storage=self)

    def audit_private_path(self, path: Path, *, platform_name: Optional[str] = None) -> Dict[str, Any]:
        return audit_private_path(path, platform_name=platform_name, storage=self)

    def audit_private_tree(self, *, platform_name: Optional[str] = None) -> Dict[str, Any]:
        return audit_private_tree(self.root, platform_name=platform_name, storage=self)

    def file_lock(self, path: Path, timeout_seconds: float = 30.0, stale_seconds: float = 120.0):
        return file_lock(path, timeout_seconds=timeout_seconds, stale_seconds=stale_seconds, storage=self)

    def atomic_replace(self, src: Path, dst: Path) -> None:
        atomic_replace(src, dst, storage=self)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        unlink_storage_path(path, missing_ok=missing_ok, storage=self)

    def read_json(self, path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
        return read_json(path, default, storage=self)

    def write_json(self, path: Path, value: Dict[str, Any]) -> None:
        write_json(path, value, storage=self)

    def update_json(
        self,
        path: Path,
        default: Dict[str, Any],
        updater: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        return update_json(path, default, updater, storage=self)

    def read_jsonl(self, path: Path) -> List[Dict[str, Any]]:
        return read_jsonl(path, storage=self)

    def append_jsonl(self, path: Path, row: Dict[str, Any]) -> None:
        append_jsonl(path, row, storage=self)

    def write_jsonl(self, path: Path, rows: Iterable[Dict[str, Any]]) -> None:
        write_jsonl(path, rows, storage=self)


def authorize_storage_root(root: Path) -> StorageCapability:
    """Compatibility-named factory; it grants no ambient process authority."""
    return StorageCapability.bind_trusted(root)


def validate_storage_path(path: Path, *, storage: StorageCapability) -> Path:
    return storage.validate(path)


def reject_link_components(path: Path, *, storage: StorageCapability) -> None:
    storage.reject_link_components(path)


def _secure_posix_available() -> bool:
    return (
        not _is_windows()
        and os.open in os.supports_dir_fd
        and os.mkdir in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
    )


def _open_posix_directory(storage: StorageCapability, path: Path, *, create: bool) -> int:
    """Open an absolute directory by walking no-follow handles from its anchor."""
    target = storage.validate(path)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(target.anchor, flags)
    current = Path(target.anchor)
    try:
        for part in target.parts[1:]:
            current = current / part
            created = False
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, PRIVATE_DIRECTORY_MODE, dir_fd=descriptor)
                created = True
                child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            if created:
                os.fchmod(descriptor, PRIVATE_DIRECTORY_MODE)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_posix_file(
    storage: StorageCapability,
    path: Path,
    flags: int,
    mode: int = PRIVATE_FILE_MODE,
    *,
    create_parent: bool,
) -> int:
    """Open a file relative to a held no-follow parent directory handle."""
    target = storage.validate(path)
    parent_fd = _open_posix_directory(storage, target.parent, create=create_parent)
    try:
        hook = _POSIX_BEFORE_FINAL_OPEN_HOOK
        if hook is not None:
            hook(target)
        return os.open(
            target.name,
            flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            mode,
            dir_fd=parent_fd,
        )
    finally:
        os.close(parent_fd)


def _normalize_windows_handle_path(path: str) -> Path:
    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    return Path(os.path.normpath(path))


def _open_windows_file_checked(
    storage: StorageCapability,
    path: Path,
    flags: int,
    mode: int = PRIVATE_FILE_MODE,
) -> int:
    """Open, then verify the Windows handle before truncating or writing.

    Python does not expose handle-relative ``NtCreateFile`` traversal.  The
    bridge therefore combines immediate component/reparse validation with a
    final-handle containment check.  This prevents ordinary link/junction
    escapes and avoids destructive truncation before verification, but does
    not claim protection from a hostile same-user parent swap during the
    path-based ``os.open`` call.
    """
    target = storage.validate(path)
    requested_truncate = bool(flags & os.O_TRUNC)
    safe_flags = (flags & ~os.O_TRUNC) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(str(target), safe_flags, mode)
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(descriptor)
        attributes = os.fstat(descriptor)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if bool(getattr(attributes, "st_file_attributes", 0) & reparse_flag):
            raise UnsafeStoragePathError(
                "agent-bridge state handle is a reparse point: %s" % target
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_final_path = kernel32.GetFinalPathNameByHandleW
        get_final_path.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        get_final_path.restype = ctypes.c_uint32
        required = get_final_path(ctypes.c_void_p(handle), None, 0, 0)
        if not required:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(required + 1)
        written = get_final_path(ctypes.c_void_p(handle), buffer, len(buffer), 0)
        if not written or written >= len(buffer):
            raise ctypes.WinError(ctypes.get_last_error())
        final_path = _normalize_windows_handle_path(buffer.value)
        root_path = _normalize_windows_handle_path(os.path.realpath(str(storage.root)))
        final_key = os.path.normcase(str(final_path))
        root_key = os.path.normcase(str(root_path))
        try:
            contained = os.path.commonpath((root_key, final_key)) == root_key
        except ValueError:
            contained = False
        if not contained:
            raise UnsafeStoragePathError(
                "agent-bridge state handle escaped its authorized root %s: %s"
                % (storage.root, final_path)
            )
        if requested_truncate:
            os.ftruncate(descriptor, 0)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_storage_file(
    storage: StorageCapability,
    path: Path,
    flags: int,
    mode: int = PRIVATE_FILE_MODE,
    *,
    create_parent: bool,
) -> int:
    if _secure_posix_available():
        return _open_posix_file(storage, path, flags, mode, create_parent=create_parent)
    if _is_windows():
        return _open_windows_file_checked(storage, path, flags, mode)
    target = storage.validate(path)
    return os.open(str(target), flags | getattr(os, "O_NOFOLLOW", 0), mode)


def _unlink_storage_path(
    storage: StorageCapability, path: Path, *, missing_ok: bool = False
) -> None:
    target = storage.validate(path)
    if _secure_posix_available():
        parent_fd = _open_posix_directory(storage, target.parent, create=False)
        try:
            os.unlink(target.name, dir_fd=parent_fd)
        except FileNotFoundError:
            if not missing_ok:
                raise
        finally:
            os.close(parent_fd)
        return
    target.unlink(missing_ok=missing_ok)


def unlink_storage_path(
    path: Path, *, missing_ok: bool = False, storage: StorageCapability
) -> None:
    """Delete one validated bridge path without following a POSIX parent."""
    _unlink_storage_path(storage, path, missing_ok=missing_ok)


def _chmod_no_follow(storage: StorageCapability, path: Path, mode: int) -> None:
    path = storage.validate(path)
    if os.chmod in os.supports_follow_symlinks:
        os.chmod(path, mode, follow_symlinks=False)
    else:
        os.chmod(path, mode)


def ensure_private_directory(path: Path, *, storage: StorageCapability) -> None:
    """Create a bridge-owned directory and enforce owner-only POSIX access.

    Windows ACLs are intentionally not rewritten here.  A correct DACL depends
    on the Desktop/AppContainer identities that need bridge access, and blindly
    replacing inherited ACEs can lock the bridge out or require privileges the
    current process does not have.  Use ``audit_private_path`` to fail closed
    when a deployment requires a verified Windows ACL.
    """
    target = storage.validate(path)
    if _secure_posix_available():
        descriptor = _open_posix_directory(storage, target, create=True)
        try:
            os.fchmod(descriptor, PRIVATE_DIRECTORY_MODE)
        finally:
            os.close(descriptor)
        return
    missing: List[Path] = []
    cursor = target
    while not cursor.exists() and cursor.parent != cursor:
        missing.append(cursor)
        cursor = cursor.parent
    target.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)
    storage.reject_link_components(target)
    if not _is_windows():
        for created in reversed(missing):
            _chmod_no_follow(storage, created, PRIVATE_DIRECTORY_MODE)
        if not missing:
            _chmod_no_follow(storage, target, PRIVATE_DIRECTORY_MODE)


def ensure_private_file(path: Path, *, storage: StorageCapability) -> None:
    """Enforce owner-only POSIX access on an existing bridge state file."""
    target = storage.validate(path)
    if _secure_posix_available():
        try:
            descriptor = _open_posix_file(storage, target, os.O_RDONLY, create_parent=False)
        except FileNotFoundError:
            return
        try:
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
        finally:
            os.close(descriptor)
        return
    if target.exists() and not _is_windows():
        _chmod_no_follow(storage, target, PRIVATE_FILE_MODE)


def open_private_text(path: Path, mode: str, *, storage: StorageCapability):
    """Open a state file for append or replacement with mode 0600 on POSIX."""
    if mode not in {"a", "w"}:
        raise ValueError("private text files may only be opened for append or write")
    target = storage.validate(path)
    storage.ensure_private_directory(target.parent)
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_APPEND if mode == "a" else os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = _open_storage_file(
        storage, target, flags, PRIVATE_FILE_MODE, create_parent=True
    )
    try:
        if not _is_windows():
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
        return os.fdopen(descriptor, mode, encoding="utf-8", newline="\n")
    except Exception:
        os.close(descriptor)
        raise


def open_private_read_text(
    path: Path,
    *,
    encoding: str = "utf-8",
    errors: Optional[str] = None,
    storage: StorageCapability,
):
    """Open a bridge state file without following its final link component."""
    target = storage.validate(path)
    storage.ensure_private_file(target)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = _open_storage_file(
        storage, target, flags, PRIVATE_FILE_MODE, create_parent=False
    )
    try:
        return os.fdopen(descriptor, "r", encoding=encoding, errors=errors, newline="")
    except Exception:
        os.close(descriptor)
        raise


def open_readonly_text(
    path: Path,
    *,
    encoding: str = "utf-8",
    errors: Optional[str] = None,
    storage: StorageCapability,
):
    """Open state without locks, chmod, quarantine, or other mutation."""
    target = storage.validate(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = _open_storage_file(
        storage, target, flags, PRIVATE_FILE_MODE, create_parent=False
    )
    try:
        return os.fdopen(descriptor, "r", encoding=encoding, errors=errors, newline="")
    except Exception:
        os.close(descriptor)
        raise


def iter_jsonl_readonly(
    path: Path, *, storage: StorageCapability
) -> Iterator[Dict[str, Any]]:
    """Yield valid JSON-object rows without repairing or quarantining input."""
    target = storage.validate(path)
    try:
        with storage.open_readonly_text(target) as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    yield parsed
    except FileNotFoundError:
        return


def copy_private_file(source: Path, target: Path, *, storage: StorageCapability) -> None:
    """Copy one bridge state file without a permissive destination window."""
    source_path = storage.validate(source)
    target_path = storage.validate(target)
    storage.ensure_private_file(source_path)
    storage.reject_link_components(target_path)
    storage.ensure_private_directory(target_path.parent)
    binary = getattr(os, "O_BINARY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    source_flags = os.O_RDONLY | binary | no_follow
    source_fd = _open_storage_file(
        storage, source_path, source_flags, PRIVATE_FILE_MODE, create_parent=False
    )
    target_created = False
    try:
        target_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary | no_follow
        target_fd = _open_storage_file(
            storage, target_path, target_flags, PRIVATE_FILE_MODE, create_parent=True
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
                _unlink_storage_path(storage, target_path, missing_ok=True)
            except OSError:
                pass
        raise
    finally:
        if source_fd >= 0:
            os.close(source_fd)


def audit_private_path(
    path: Path,
    *,
    platform_name: Optional[str] = None,
    storage: StorageCapability,
) -> Dict[str, Any]:
    """Return an executable, fail-closed confidentiality check for one path."""
    try:
        target = storage.validate(path)
    except UnsafeStoragePathError as exc:
        return {
            "ok": False,
            "status": "unsafe_path",
            "path": str(path),
            "platform": platform_name or os.name,
            "message": str(exc),
        }
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


def audit_private_tree(
    root: Path,
    *,
    platform_name: Optional[str] = None,
    storage: StorageCapability,
) -> Dict[str, Any]:
    """Audit the bridge root without mutating permissions or following symlinks."""
    try:
        target = storage.validate(root)
    except UnsafeStoragePathError as exc:
        return {
            "ok": False,
            "status": "unsafe_path",
            "root": str(root),
            "results": [{"ok": False, "status": "unsafe_path", "path": str(root), "message": str(exc)}],
        }
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
    results = [
        audit_private_path(path, platform_name=platform_name, storage=storage)
        for path in paths
    ]
    return {
        "ok": bool(results) and all(result["ok"] for result in results),
        "status": "private" if results and all(result["ok"] for result in results) else "not_verified",
        "root": str(target),
        "results": results,
    }


def _lock_path_for(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


@contextmanager
def file_lock(
    path: Path,
    timeout_seconds: float = 30.0,
    stale_seconds: float = 120.0,
    *,
    storage: StorageCapability,
) -> Iterator[None]:
    """Small cross-process file lock using exclusive directory creation."""
    path = storage.validate(path)
    lock_path = _lock_path_for(path)
    storage.ensure_private_directory(lock_path.parent)
    storage.reject_link_components(lock_path)
    start = time.time()
    parent_fd: Optional[int] = None
    if _secure_posix_available():
        parent_fd = _open_posix_directory(storage, lock_path.parent, create=True)
    while True:
        try:
            if parent_fd is not None:
                os.mkdir(lock_path.name, PRIVATE_DIRECTORY_MODE, dir_fd=parent_fd)
                lock_fd = os.open(
                    lock_path.name,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
                try:
                    os.fchmod(lock_fd, PRIVATE_DIRECTORY_MODE)
                finally:
                    os.close(lock_fd)
            else:
                lock_path.mkdir(mode=PRIVATE_DIRECTORY_MODE)
            if not _is_windows() and parent_fd is None:
                os.chmod(lock_path, PRIVATE_DIRECTORY_MODE)
            break
        except FileExistsError:
            storage.reject_link_components(lock_path)
            try:
                metadata = (
                    os.stat(lock_path.name, dir_fd=parent_fd, follow_symlinks=False)
                    if parent_fd is not None
                    else lock_path.stat()
                )
                age = time.time() - metadata.st_mtime
                if age > stale_seconds:
                    if parent_fd is not None:
                        os.rmdir(lock_path.name, dir_fd=parent_fd)
                    else:
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
            if parent_fd is not None:
                os.rmdir(lock_path.name, dir_fd=parent_fd)
            else:
                lock_path.rmdir()
        except OSError:
            pass
        if parent_fd is not None:
            os.close(parent_fd)


def _temp_path_for(path: Path) -> Path:
    return path.with_name(
        "%s.%s.%s.%s.tmp" % (path.name, os.getpid(), threading.get_ident(), uuid.uuid4().hex)
    )


def atomic_replace(src: Path, dst: Path, *, storage: StorageCapability) -> None:
    """Replace dst with src, using a Windows-safe fallback when needed."""
    src = storage.validate(src)
    dst = storage.validate(dst)
    if _secure_posix_available():
        src_parent_fd = _open_posix_directory(storage, src.parent, create=False)
        dst_parent_fd = _open_posix_directory(storage, dst.parent, create=True)
        try:
            os.replace(src.name, dst.name, src_dir_fd=src_parent_fd, dst_dir_fd=dst_parent_fd)
        finally:
            os.close(src_parent_fd)
            os.close(dst_parent_fd)
    elif sys.platform == "win32":
        # Source and destination are on the same validated bridge root.  Use a
        # single replace operation rather than shutil.move's copy fallback,
        # then revalidate the published destination.  Python does not expose a
        # Windows RootDirectory handle here, so hostile same-user parent swaps
        # remain outside the local trusted-user threat model.
        os.replace(str(src), str(dst))
        storage.validate(dst)
    else:
        src.replace(dst)


def read_json(
    path: Path, default: Dict[str, Any], *, storage: StorageCapability
) -> Dict[str, Any]:
    path = storage.validate(path)
    try:
        storage.ensure_private_file(path)
        if not path.exists():
            return dict(default)
        with storage.file_lock(path):
            with storage.open_private_read_text(path) as handle:
                data = json.load(handle)
    except UnsafeStoragePathError:
        raise
    except (OSError, JSONDecodeError):
        return dict(default)
    if not isinstance(data, dict):
        return dict(default)
    return data


def write_json(
    path: Path, value: Dict[str, Any], *, storage: StorageCapability
) -> None:
    path = storage.validate(path)
    storage.ensure_private_directory(path.parent)
    with storage.file_lock(path):
        tmp = _temp_path_for(path)
        try:
            with storage.open_private_text(tmp, "w") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
            storage.atomic_replace(tmp, path)
            storage.ensure_private_file(path)
        finally:
            try:
                _unlink_storage_path(storage, tmp, missing_ok=True)
            except OSError:
                pass


def update_json(
    path: Path,
    default: Dict[str, Any],
    updater: Callable[[Dict[str, Any]], Dict[str, Any]],
    *,
    storage: StorageCapability,
) -> Dict[str, Any]:
    """Read/modify/write one JSON object while holding the file lock."""
    path = storage.validate(path)
    storage.ensure_private_directory(path.parent)
    with storage.file_lock(path):
        data = dict(default)
        if path.exists():
            try:
                with storage.open_private_read_text(path) as handle:
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
            with storage.open_private_text(tmp, "w") as handle:
                json.dump(updated, handle, indent=2, sort_keys=True)
                handle.write("\n")
            storage.atomic_replace(tmp, path)
            storage.ensure_private_file(path)
        finally:
            try:
                _unlink_storage_path(storage, tmp, missing_ok=True)
            except OSError:
                pass
        return updated


def read_jsonl(path: Path, *, storage: StorageCapability) -> List[Dict[str, Any]]:
    path = storage.validate(path)
    storage.ensure_private_file(path)
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    quarantine: List[str] = []
    with storage.file_lock(path):
        with storage.open_private_read_text(path) as handle:
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
            with storage.open_private_text(qpath, "a") as handle:
                for bad in quarantine:
                    handle.write(bad)
                    handle.write("\n")
    return rows


def append_jsonl(
    path: Path, row: Dict[str, Any], *, storage: StorageCapability
) -> None:
    path = storage.validate(path)
    storage.ensure_private_directory(path.parent)
    with storage.file_lock(path):
        with storage.open_private_text(path, "a") as handle:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")


def write_jsonl(
    path: Path, rows: Iterable[Dict[str, Any]], *, storage: StorageCapability
) -> None:
    path = storage.validate(path)
    storage.ensure_private_directory(path.parent)
    with storage.file_lock(path):
        tmp = _temp_path_for(path)
        try:
            with storage.open_private_text(tmp, "w") as handle:
                for row in rows:
                    handle.write(json.dumps(row, sort_keys=True))
                    handle.write("\n")
            storage.atomic_replace(tmp, path)
            storage.ensure_private_file(path)
        finally:
            try:
                _unlink_storage_path(storage, tmp, missing_ok=True)
            except OSError:
                pass


def with_schema_version(value: Dict[str, Any], version: int = STATE_SCHEMA_VERSION) -> Dict[str, Any]:
    copied = dict(value)
    copied.setdefault("schema_version", version)
    return copied
