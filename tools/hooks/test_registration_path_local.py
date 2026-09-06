"""LOCAL registration-path gate for the MLV-App project PreToolUse hook.

WHY THIS FILE IS NOT UNDER ``tools/repo_hygiene/``.  The repo-hygiene job collects
``tools/repo_hygiene/test_*.py`` by discovery and runs it on ``windows-latest`` and
``ubuntu-latest``.  The command registered in ``.claude/settings.json`` names a PER-USER
ABSOLUTE INTERPRETER that exists on no hosted runner (O81), so a case that executes it
would be red in CI and green locally -- the exact split this repo already paid for.
Hosted CI therefore proves the RULES (``tools/repo_hygiene/test_mlv_never_authorized.py``);
this file proves the REGISTRATION, and the hub runs it on the branch and again after 0.1's
``.claude/settings.json`` conflict resolution.

    python -m unittest tools.hooks.test_registration_path_local -v

A hook is (interpreter x script x REGISTRATION).  Hashing the script proves only the middle
term; this file executes the registered command string VERBATIM -- the same string the
harness runs, with only ``${CLAUDE_PROJECT_DIR}`` expanded, exactly as the harness expands
it -- from a disposable worktree-like directory, and asserts exit 0 on a benign payload and
exit 2 on a harmless one.  ``py -3`` hooks on this machine silently never executed until
2026-08-09; a hook that has never run looks exactly like a hook that always passes.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS = os.path.join(REPO_ROOT, ".claude", "settings.json")

EXPECTED_MATCHER = "Bash|PowerShell|Write|Edit|NotebookEdit"
EXPECTED_COMMAND = (
    '"C:/Users/obabalola/AppData/Local/Python/bin/python.exe" '
    '"${CLAUDE_PROJECT_DIR}/tools/hooks/mlv-never-authorized.py"'
)

ALLOW_PAYLOAD = {"tool_name": "Bash", "tool_input": {"command": "git status --porcelain"}}
# The INERT known-DENY payload named by the plan: it exercises the ANTHROPIC_ prefix rule,
# names no real credential, and the hook only READS it -- nothing is ever executed.
DENY_PAYLOAD = {
    "tool_name": "PowerShell",
    "tool_input": {"command": "if ($false) { setx ANTHROPIC_PROBE_TOKEN x }"},
}


def registered_pretooluse_entries():
    with open(SETTINGS, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    return document.get("hooks", {}).get("PreToolUse", [])


class RegistrationPathTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.entries = registered_pretooluse_entries()
        cls.command = None
        for block in cls.entries:
            if not isinstance(block, dict) or block.get("matcher") != EXPECTED_MATCHER:
                continue
            for hook in block.get("hooks", []):
                if (
                    isinstance(hook, dict)
                    and hook.get("type") == "command"
                    and "mlv-never-authorized.py" in str(hook.get("command", ""))
                ):
                    cls.command = hook["command"]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mlv-reg-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _execute(self, payload):
        """Run the REGISTERED command string verbatim, from a disposable directory."""
        self.assertIsNotNone(self.command, "no PreToolUse registration to execute")
        command = self.command.replace("${CLAUDE_PROJECT_DIR}", REPO_ROOT)
        command = command.replace("$CLAUDE_PROJECT_DIR", REPO_ROOT)
        env = dict(os.environ)
        for key in list(env):
            if key.startswith("MLV_"):
                del env[key]
        env["CLAUDE_PROJECT_DIR"] = REPO_ROOT
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            command,
            shell=True,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=self.tmp,
            env=env,
        )

    def test_pretooluse_entry_is_registered_exactly(self):
        """Matcher, type and command are all EXACT and case-sensitive -- never a substring."""
        self.assertTrue(self.entries, "settings.json declares no PreToolUse hooks")
        matched = [
            hook
            for block in self.entries
            if isinstance(block, dict) and block.get("matcher") == EXPECTED_MATCHER
            for hook in block.get("hooks", [])
            if isinstance(hook, dict)
            and hook.get("type") == "command"
            and hook.get("command") == EXPECTED_COMMAND
        ]
        self.assertEqual(len(matched), 1, "expected exactly one exact PreToolUse registration")

    def test_registered_interpreter_exists(self):
        """A portable-but-wrong command fails OPEN; that is what cost two days here."""
        interpreter = EXPECTED_COMMAND.split('" "')[0].lstrip('"')
        self.assertTrue(os.path.isfile(interpreter), interpreter)

    def test_registered_command_allows_a_benign_payload(self):
        completed = self._execute(ALLOW_PAYLOAD)
        self.assertEqual(
            completed.returncode,
            0,
            "expected ALLOW; stdout=%r stderr=%r" % (completed.stdout, completed.stderr),
        )
        self.assertEqual(completed.stderr.strip(), "")

    def test_registered_command_denies_a_harmless_payload(self):
        completed = self._execute(DENY_PAYLOAD)
        self.assertEqual(
            completed.returncode,
            2,
            "expected DENY; stdout=%r stderr=%r" % (completed.stdout, completed.stderr),
        )
        lines = [line for line in completed.stderr.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, "DENY must print exactly ONE line: %r" % lines)
        self.assertTrue(lines[0].startswith("NA-3:"), lines[0])


if __name__ == "__main__":
    unittest.main()
