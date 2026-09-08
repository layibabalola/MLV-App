"""
Guards PROD-ENVFLAG-1: main.cpp and WorkerThreadCount.h must not carry a
second, divergent definition of empty-value env-flag semantics. Both call
sites now delegate to mlvappEnvFlagEnabled(const QByteArray&) in
src/batch/EnvFlags.h, which treats an empty value as false.

Scope note: this asserts single-definition-of-empty-semantics only for the
two files named in PROD-ENVFLAG-1 (main.cpp, WorkerThreadCount.h), not
repo-wide -- the four other platform/qt env-flag helpers and
src/processing/rbfilter/rbf_wrapper.cpp are deliberately out of scope
(O117/S100) and are not touched or asserted on here.
"""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

GUARDED_FILES = (
    REPO_ROOT / "platform" / "qt" / "main.cpp",
    REPO_ROOT / "src" / "batch" / "WorkerThreadCount.h",
)


class EnvFlagSingleDefinitionTest(unittest.TestCase):
    def test_guarded_files_exist(self):
        for path in GUARDED_FILES:
            self.assertTrue(path.is_file(), f"expected guarded file to exist: {path}")

    def _extract_function_body(self, text, signature_snippet):
        start = text.index(signature_snippet)
        open_brace = text.index("{", start)
        depth = 0
        i = open_brace
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[open_brace:i + 1]
            i += 1
        raise AssertionError(f"unbalanced braces while extracting body for: {signature_snippet!r}")

    def test_no_isEmpty_in_guarded_files(self):
        # isEmpty() inside the flag-helper functions themselves would indicate
        # a locally re-implemented empty-value branch instead of delegating
        # to mlvappEnvFlagEnabled(). Scoped to just those functions -- both
        # guarded files legitimately use isEmpty() elsewhere for unrelated
        # option parsing.
        worker_thread_count = (REPO_ROOT / "src" / "batch" / "WorkerThreadCount.h").read_text(
            encoding="utf-8"
        )
        main_cpp = (REPO_ROOT / "platform" / "qt" / "main.cpp").read_text(encoding="utf-8")

        worker_thread_count_body = self._extract_function_body(
            worker_thread_count, "inline bool mlvappEnvFlagEnabled(const char *name)"
        )
        main_cpp_body = self._extract_function_body(
            main_cpp, "static bool envFlagEnabled(const char *name)"
        )

        for label, body in (
            ("WorkerThreadCount.h mlvappEnvFlagEnabled()", worker_thread_count_body),
            ("main.cpp envFlagEnabled()", main_cpp_body),
        ):
            self.assertNotIn(
                "isEmpty()",
                body,
                f"{label} must not re-implement empty-value semantics locally; "
                "delegate to mlvappEnvFlagEnabled() in src/batch/EnvFlags.h instead",
            )

    def test_both_sites_delegate_to_shared_helper(self):
        worker_thread_count = (REPO_ROOT / "src" / "batch" / "WorkerThreadCount.h").read_text(
            encoding="utf-8"
        )
        main_cpp = (REPO_ROOT / "platform" / "qt" / "main.cpp").read_text(encoding="utf-8")

        self.assertIn('#include "EnvFlags.h"', worker_thread_count)
        self.assertIn("mlvappEnvFlagEnabled(qgetenv(name))", worker_thread_count)

        self.assertIn('#include "../../src/batch/EnvFlags.h"', main_cpp)
        self.assertIn("mlvappEnvFlagEnabled(qgetenv(name))", main_cpp)


if __name__ == "__main__":
    unittest.main()
