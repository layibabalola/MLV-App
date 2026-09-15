import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.storage import StorageCapability, UnsafeStoragePathError, atomic_replace, write_json
import core.storage as storage_module


class AtomicReplaceWindowsRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = StorageCapability.bind_trusted(Path(self.temp.name))
        self.target = Path(self.temp.name) / "state.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_write_json_retries_sharing_denial_and_publishes(self):
        real_replace = os.replace
        calls = []

        def replace(src, dst):
            calls.append((src, dst))
            if len(calls) == 1:
                error = PermissionError("sharing violation")
                error.winerror = 32
                raise error
            return real_replace(src, dst)

        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(storage_module, "_secure_posix_available", return_value=False), mock.patch.object(os, "replace", side_effect=replace), mock.patch.object(storage_module.time, "sleep") as sleep:
            write_json(self.target, {"ok": True}, storage=self.storage)
        self.assertEqual({"ok": True}, json.loads(self.target.read_text()))
        self.assertEqual(2, len(calls))
        self.assertEqual([mock.call(0.02)], sleep.call_args_list)
        self.assertFalse(list(self.target.parent.glob("state.json.*.tmp")))

    def test_exhausted_retry_preserves_existing_target_and_cleans_temp(self):
        self.target.write_text('{"old": true}\n')
        error = PermissionError("access denied")
        error.winerror = 5
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(storage_module, "_secure_posix_available", return_value=False), mock.patch.object(os, "replace", side_effect=error) as replace, mock.patch.object(storage_module.time, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                write_json(self.target, {"new": True}, storage=self.storage)
        self.assertEqual({"old": True}, json.loads(self.target.read_text()))
        self.assertFalse(list(self.target.parent.glob("state.json.*.tmp")))
        self.assertEqual(6, replace.call_count)
        self.assertEqual([mock.call(0.02 * i) for i in range(1, 6)], sleep.call_args_list)
        self.assertAlmostEqual(0.3, sum(call.args[0] for call in sleep.call_args_list))

    def test_non_sharing_permission_error_is_not_retried(self):
        error = PermissionError("other permission failure")
        error.winerror = 1234
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(storage_module, "_secure_posix_available", return_value=False), mock.patch.object(os, "replace", side_effect=error) as replace, mock.patch.object(storage_module.time, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                write_json(self.target, {"new": True}, storage=self.storage)
        self.assertEqual(1, replace.call_count)
        sleep.assert_not_called()

    def test_invalid_source_is_rejected_before_replace(self):
        escaped = Path(self.temp.name).parent / (Path(self.temp.name).name + "-outside") / "source.tmp"
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(storage_module, "_secure_posix_available", return_value=False), mock.patch.object(os, "replace") as replace:
            with self.assertRaises(UnsafeStoragePathError):
                atomic_replace(escaped, self.target, storage=self.storage)
        replace.assert_not_called()

    def test_retry_revalidates_source_before_replacement(self):
        source = Path(self.temp.name) / "source.tmp"
        source.write_text("new")
        self.target.write_text("old")
        original_validate = StorageCapability.validate
        source_became_unsafe = False

        def validate(storage, path):
            if source_became_unsafe and Path(path) == source:
                raise UnsafeStoragePathError("source changed during retry")
            return original_validate(storage, path)

        def delay(_seconds):
            nonlocal source_became_unsafe
            source_became_unsafe = True

        error = PermissionError("sharing violation")
        error.winerror = 33
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.object(storage_module, "_secure_posix_available", return_value=False),
            mock.patch.object(StorageCapability, "validate", autospec=True, side_effect=validate),
            mock.patch.object(os, "replace", side_effect=error) as replace,
            mock.patch.object(storage_module.time, "sleep", side_effect=delay),
        ):
            with self.assertRaises(UnsafeStoragePathError):
                atomic_replace(source, self.target, storage=self.storage)
        replace.assert_called_once()
        self.assertEqual("old", self.target.read_text())


if __name__ == "__main__":
    unittest.main()
