import json
import os
import shutil
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List


STATE_SCHEMA_VERSION = 1
INBOX_SCHEMA_VERSION = 1


def _lock_path_for(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


@contextmanager
def file_lock(path: Path, timeout_seconds: float = 30.0, stale_seconds: float = 120.0) -> Iterator[None]:
    """Small cross-process file lock using exclusive directory creation."""
    lock_path = _lock_path_for(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    while True:
        try:
            lock_path.mkdir()
            break
        except FileExistsError:
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
    if sys.platform == "win32":
        shutil.move(str(src), str(dst))
    else:
        src.replace(dst)


def read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if not path.exists():
            return dict(default)
        with file_lock(path):
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
    except (OSError, JSONDecodeError):
        return dict(default)
    if not isinstance(data, dict):
        return dict(default)
    return data


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        tmp = _temp_path_for(path)
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
            atomic_replace(tmp, path)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        data = dict(default)
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as handle:
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
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(updated, handle, indent=2, sort_keys=True)
                handle.write("\n")
            atomic_replace(tmp, path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return updated


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    quarantine: List[str] = []
    with file_lock(path):
        with path.open("r", encoding="utf-8") as handle:
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
            with qpath.open("a", encoding="utf-8", newline="\n") as handle:
                for bad in quarantine:
                    handle.write(bad)
                    handle.write("\n")
    return rows


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        tmp = _temp_path_for(path)
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                for row in rows:
                    handle.write(json.dumps(row, sort_keys=True))
                    handle.write("\n")
            atomic_replace(tmp, path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def with_schema_version(value: Dict[str, Any], version: int = STATE_SCHEMA_VERSION) -> Dict[str, Any]:
    copied = dict(value)
    copied.setdefault("schema_version", version)
    return copied
