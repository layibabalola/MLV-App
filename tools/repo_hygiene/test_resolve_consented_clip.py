"""ATTR3-FOOTAGE-BIND-1 PR-A: the id-only, content-bound clip resolver.

Every row uses a SYNTHETIC spec and a SYNTHETIC consent table (``spec_bytes=``/``table=``); no
row reads the real repo's spec or the hook's real table except the two rows that explicitly say
so.  Synthetic paths carry a distinctive token so the path-free-output rows can assert it never
reaches stdout or stderr.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESOLVER = os.path.join(REPO_ROOT, "tools", "gates", "resolve_consented_clip.py")
HOOK = os.path.join(REPO_ROOT, "tools", "hooks", "mlv-never-authorized.py")

TOKEN = "ZZSYNTHETICPATHTOKENZZ"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResolveConsentedClipTests(unittest.TestCase):
    def setUp(self):
        self.rcc = _load(RESOLVER, "_resolve_consented_clip_under_test")
        hook = _load(HOOK, "_mlv_never_authorized_for_resolver_test")
        self.norm = hook.norm
        self.paths = (
            "C:/temp/%s/clip.raw" % TOKEN,
            "C:/temp/%s/clip.raw.part1" % TOKEN,
        )
        self.contents = (b"synthetic part zero " * 97, b"synthetic part one")
        self.spec_parts = [
            {"path": path, "length": len(content), "sha256": hashlib.sha256(content).hexdigest().upper()}
            for path, content in zip(self.paths, self.contents)
        ]
        self.table_parts = tuple(
            (
                len(content),
                hashlib.sha256(content).hexdigest(),
                hashlib.sha256(self.norm(path).encode("utf-8")).hexdigest(),
            )
            for path, content in zip(self.paths, self.contents)
        )
        self.spec = {
            "clips": [
                {"id": "FIX-0001", "parts": self.spec_parts},
                {"id": "FIX-ZERO", "parts": []},
            ]
        }
        self.spec_bytes = json.dumps(self.spec).encode("utf-8")
        self.table = {"clips": {"FIX-0001": self.table_parts}}

    def _resolve(self, clip_id, spec_bytes=None, table=None):
        return self.rcc.resolve(
            clip_id,
            ".",
            spec_bytes=self.spec_bytes if spec_bytes is None else spec_bytes,
            table=self.table if table is None else table,
        )

    # -- pass -----------------------------------------------------------------------------------
    def test_pass_when_every_part_matches(self):
        parts = self._resolve("FIX-0001")
        self.assertEqual([(p.index, p.status) for p in parts], [(0, self.rcc.PASS), (1, self.rcc.PASS)])
        self.assertEqual([p.path for p in parts], list(self.paths))

    def test_case_normalisation_uppercase_spec_vs_lowercase_table_passes(self):
        # self.spec_parts already carries uppercase sha256 (as the real spec does); self.table
        # already carries lowercase (as the hook's real table does). test_pass_when_every_part_
        # matches already proves this, but assert it explicitly so the fold is never accidental.
        for part in self.spec_parts:
            self.assertTrue(part["sha256"].isupper())
        for _length, sha, path_norm_sha in self.table_parts:
            self.assertTrue(sha.islower())
            self.assertTrue(path_norm_sha.islower())
        parts = self._resolve("FIX-0001")
        self.assertTrue(all(p.status == self.rcc.PASS for p in parts))

    # -- unknown id -------------------------------------------------------------------------------
    def test_unknown_id(self):
        with self.assertRaises(self.rcc.UnknownClipError) as ctx:
            self._resolve("NO-SUCH-ID")
        self.assertEqual(ctx.exception.code, "UNKNOWN_ID")

    # -- not in consent table -----------------------------------------------------------------------
    def test_id_absent_from_consent_table(self):
        spec = {"clips": [{"id": "FIX-UNCONSENTED", "parts": self.spec_parts}]}
        with self.assertRaises(self.rcc.NotConsentedError) as ctx:
            self._resolve("FIX-UNCONSENTED", spec_bytes=json.dumps(spec).encode("utf-8"))
        self.assertEqual(ctx.exception.code, "NOT_CONSENTED")

    # -- zero parts -------------------------------------------------------------------------------
    def test_zero_parts(self):
        with self.assertRaises(self.rcc.ZeroPartsError) as ctx:
            self._resolve("FIX-ZERO")
        self.assertEqual(ctx.exception.code, "ZERO_PARTS")

    # -- part-count mismatch ------------------------------------------------------------------------
    def test_part_count_mismatch(self):
        spec = {"clips": [{"id": "FIX-0001", "parts": self.spec_parts[:1]}]}
        with self.assertRaises(self.rcc.PartCountMismatchError) as ctx:
            self._resolve("FIX-0001", spec_bytes=json.dumps(spec).encode("utf-8"))
        self.assertEqual((ctx.exception.spec_count, ctx.exception.table_count), (1, 2))

    # -- length mismatch ----------------------------------------------------------------------------
    def test_length_mismatch(self):
        spec_parts = [dict(part) for part in self.spec_parts]
        spec_parts[1]["length"] += 1
        spec = {"clips": [{"id": "FIX-0001", "parts": spec_parts}]}
        with self.assertRaises(self.rcc.PartMismatchError) as ctx:
            self._resolve("FIX-0001", spec_bytes=json.dumps(spec).encode("utf-8"))
        self.assertEqual(ctx.exception.parts[1].status, self.rcc.LENGTH_MISMATCH)
        self.assertEqual(ctx.exception.parts[0].status, self.rcc.PASS)

    # -- sha mismatch ----------------------------------------------------------------------------
    def test_sha256_mismatch(self):
        spec_parts = [dict(part) for part in self.spec_parts]
        spec_parts[1]["sha256"] = "F" * 64
        spec = {"clips": [{"id": "FIX-0001", "parts": spec_parts}]}
        with self.assertRaises(self.rcc.PartMismatchError) as ctx:
            self._resolve("FIX-0001", spec_bytes=json.dumps(spec).encode("utf-8"))
        self.assertEqual(ctx.exception.parts[1].status, self.rcc.SHA256_MISMATCH)

    # -- path_norm mismatch -------------------------------------------------------------------------
    def test_path_norm_mismatch(self):
        table_parts = list(self.table_parts)
        length, sha, _path_norm_sha = table_parts[0]
        table_parts[0] = (length, sha, "0" * 64)
        table = {"clips": {"FIX-0001": tuple(table_parts)}}
        with self.assertRaises(self.rcc.PartMismatchError) as ctx:
            self._resolve("FIX-0001", table=table)
        self.assertEqual(ctx.exception.parts[0].status, self.rcc.PATH_NORM_MISMATCH)
        self.assertEqual(ctx.exception.parts[1].status, self.rcc.PASS)

    # -- size cap -------------------------------------------------------------------------------
    def test_size_cap_refuses_oversized_spec(self):
        oversized = b" " * (self.rcc.SPEC_CAP_BYTES + 1)
        with self.assertRaises(self.rcc.SpecTooLargeError):
            self._resolve("FIX-0001", spec_bytes=oversized)

    def test_size_cap_boundary_is_inclusive(self):
        # Exactly the cap parses fine (as JSON it will fail to parse -- pad with valid JSON
        # padding instead so only the cap arm is exercised, not the parser).
        padding_len = self.rcc.SPEC_CAP_BYTES - len(self.spec_bytes)
        self.assertGreater(padding_len, 0)
        padded = self.spec_bytes[:-1] + b" " * padding_len + self.spec_bytes[-1:]
        self.assertEqual(len(padded), self.rcc.SPEC_CAP_BYTES)
        parts = self._resolve("FIX-0001", spec_bytes=padded)
        self.assertTrue(all(p.status == self.rcc.PASS for p in parts))

    # -- the full ref is used -----------------------------------------------------------------------
    def test_default_ref_is_full_not_short(self):
        self.assertEqual(self.rcc.DEFAULT_REF, "refs/remotes/fork/master")
        self.assertTrue(self.rcc.DEFAULT_REF.startswith("refs/"))

    def test_git_argv_uses_the_full_ref(self):
        argv = self.rcc._git_show_argv("C:/repo", "refs/remotes/fork/master")
        self.assertEqual(
            argv,
            [
                "git",
                "-C",
                "C:/repo",
                "--no-replace-objects",
                "show",
                "refs/remotes/fork/master:tools/gates/output-budget.json",
            ],
        )

    def test_resolve_invokes_git_with_the_full_ref_when_no_spec_bytes_given(self):
        completed = mock.Mock(returncode=0, stdout=self.spec_bytes, stderr=b"")
        with mock.patch.object(self.rcc.subprocess, "run", return_value=completed) as run:
            # spec_bytes genuinely omitted here (not routed through the _resolve helper, which
            # substitutes self.spec_bytes for a bare None) so the git codepath actually fires.
            self.rcc.resolve("FIX-0001", ".", table=self.table)
        self.assertEqual(run.call_count, 1)
        argv = run.call_args[0][0]
        self.assertIn("refs/remotes/fork/master:tools/gates/output-budget.json", argv[-1])
        self.assertNotIn("fork/master", [a for a in argv if a != argv[-1]])

    # -- CLI never prints a path --------------------------------------------------------------------
    def test_cli_summary_never_prints_a_path_on_pass(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(self.rcc, "resolve", return_value=self._resolve("FIX-0001")):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = self.rcc.main(["--clip-id", "FIX-0001"])
        self.assertEqual(code, 0)
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertNotIn(TOKEN, err.getvalue())
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "PASS")
        self.assertTrue(payload["ok"])
        self.assertEqual([p["status"] for p in payload["parts"]], ["PASS", "PASS"])

    def test_cli_summary_never_prints_a_path_on_mismatch(self):
        spec_parts = [dict(part) for part in self.spec_parts]
        spec_parts[1]["sha256"] = "F" * 64
        spec = {"clips": [{"id": "FIX-0001", "parts": spec_parts}]}

        real_resolve = self.rcc.resolve

        def fake_resolve(clip_id, repo_root, ref=self.rcc.DEFAULT_REF, spec_bytes=None, table=None):
            return real_resolve(
                clip_id, repo_root, ref=ref, spec_bytes=json.dumps(spec).encode("utf-8"), table=self.table
            )

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(self.rcc, "resolve", side_effect=fake_resolve):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = self.rcc.main(["--clip-id", "FIX-0001"])
        self.assertEqual(code, 1)
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertNotIn(TOKEN, err.getvalue())
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "PART_MISMATCH")
        self.assertFalse(payload["ok"])

    def test_cli_usage_error_exits_2(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.rcc.main([]), 2)

    def test_emit_json_writes_full_parts_with_paths(self):
        import tempfile

        with mock.patch.object(self.rcc, "resolve", return_value=self._resolve("FIX-0001")):
            with tempfile.TemporaryDirectory() as tmp:
                out_path = os.path.join(tmp, "emitted.json")
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = self.rcc.main(["--clip-id", "FIX-0001", "--emit-json", out_path])
                self.assertEqual(code, 0)
                self.assertNotIn(TOKEN, out.getvalue())
                with open(out_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
        self.assertEqual([p["path"] for p in payload["parts"]], list(self.paths))


if __name__ == "__main__":
    unittest.main()
