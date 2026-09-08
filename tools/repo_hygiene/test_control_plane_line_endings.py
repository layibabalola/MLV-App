"""Regression: pinned control-plane files must check out with LF bytes even
when a machine's global/repo core.autocrlf is true.

The fixed-set receipts (and the 0.4c-guardrail actor) hash Git *blob* bytes,
which are always LF-normalized. Other consumers (the coordination wrapper,
the 0.2 gate) hash raw *disk* bytes after checkout. Under core.autocrlf=true
those two only agree for paths the repository's .gitattributes pins to
`text eol=lf` -- everything else gets CRLF on checkout on Windows, silently
diverging from the LF blob hash.

This test never modifies real hook files and never touches global Git
config. It builds a small, disposable Git repository, copies in the ACTUAL
.gitattributes bytes from this repository (not a duplicated test-only
policy), commits inert placeholder payloads at each pinned path (some of
these paths do not exist yet in this repository), forces a fresh checkout,
and compares the checked-out bytes against the Git blob bytes.
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GITATTRIBUTES = (ROOT / '.gitattributes').read_bytes()
GIT = shutil.which('git')

ACTOR_PATH = 'tools/coordination/set-required-checks.ps1'

# Kept in sync by hand with the `text eol=lf` block added for the fixed-set
# receipts / control-plane actors. Do not derive this list from disk listing
# -- some of these paths are intentionally future/nonexistent here.
PINNED_PATHS = [
    'tools/hooks/mlv-never-authorized.py',
    'tools/repo_hygiene/test_mlv_never_authorized.py',
    'tools/hooks/test_registration_path_local.py',
    'tools/coordination/Invoke-Lane.ps1',
    'tools/coordination/Invoke-Workstream.ps1',
    'tools/coordination/Invoke-WorkstreamLoop.ps1',
    'tools/coordination/Compose-LanePrompt.ps1',
    'tools/coordination/demote-factory-bridge.ps1',
    ACTOR_PATH,
    'tools/coordination/Test-ProductRatioGuard.ps1',
    'tools/coordination/freeze-factory-cards.py',
]

INERT_PAYLOAD = b'inert fixture payload\nline two\nline three\n'


def run(cmd, cwd):
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(
            '%s failed (exit=%s): %s' % (cmd, result.returncode, result.stderr.decode('utf-8', 'replace')))
    return result.stdout


def find_rule_line(attrs: bytes, rel: str) -> bytes:
    """Return the exact on-disk line (with its own line ending) that pins
    `rel` to `text eol=lf`, so removal doesn't assume LF vs CRLF."""
    target = rel.encode() + b' text eol=lf'
    for line in attrs.splitlines(keepends=True):
        if line.rstrip(b'\r\n') == target:
            return line
    raise AssertionError('no "%s text eol=lf" rule found in the real .gitattributes' % rel)


@unittest.skipUnless(GIT, 'git is required')
class ControlPlaneLineEndingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mlv-line-endings-test-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        run([GIT, 'init', '-q'], self.repo)
        # Repo-LOCAL config only -- never touches the machine/global config.
        run([GIT, 'config', '--local', 'core.autocrlf', 'true'], self.repo)
        run([GIT, 'config', '--local', 'user.email', 'fixture@example.invalid'], self.repo)
        run([GIT, 'config', '--local', 'user.name', 'Fixture'], self.repo)
        (self.repo / '.gitattributes').write_bytes(GITATTRIBUTES)
        for rel in PINNED_PATHS:
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(INERT_PAYLOAD)
        self._commit('fixture: seed pinned control-plane paths')

    def _commit(self, message):
        run([GIT, 'add', '-A'], self.repo)
        run([GIT, 'commit', '-q', '-m', message], self.repo)

    def _blob_bytes(self, rel):
        return run([GIT, 'cat-file', '-p', 'HEAD:' + rel], self.repo)

    def _fresh_checkout_bytes(self, rel):
        path = self.repo / rel
        path.unlink()
        run([GIT, 'checkout', '--', rel], self.repo)
        return path.read_bytes()

    def test_pinned_paths_checkout_equals_blob_bytes_under_autocrlf_true(self):
        for rel in PINNED_PATHS:
            with self.subTest(path=rel):
                self.assertEqual(self._blob_bytes(rel), self._fresh_checkout_bytes(rel))

    def test_actor_path_checkout_equals_blob_bytes_under_autocrlf_true(self):
        self.assertEqual(self._blob_bytes(ACTOR_PATH), self._fresh_checkout_bytes(ACTOR_PATH))

    def test_removing_a_pinned_rule_breaks_the_checkout_guarantee_then_restores(self):
        rel = PINNED_PATHS[0]
        attrs = (self.repo / '.gitattributes').read_bytes()
        rule_line = find_rule_line(attrs, rel)
        without_rule = attrs.replace(rule_line, b'', 1)
        self.assertNotEqual(attrs, without_rule)

        (self.repo / '.gitattributes').write_bytes(without_rule)
        self._commit('fixture: drop one LF rule')
        blob = self._blob_bytes(rel)
        checkout = self._fresh_checkout_bytes(rel)
        self.assertNotEqual(
            blob, checkout,
            'removing the "%s text eol=lf" rule should let core.autocrlf=true '
            'reintroduce CRLF on checkout, diverging from the LF blob' % rel)
        self.assertEqual(blob, checkout.replace(b'\r\n', b'\n'), 'divergence should be exactly CRLF vs LF')

        (self.repo / '.gitattributes').write_bytes(attrs)
        self._commit('fixture: restore the LF rule')
        self.assertEqual(self._blob_bytes(rel), self._fresh_checkout_bytes(rel))


if __name__ == '__main__':
    unittest.main()
