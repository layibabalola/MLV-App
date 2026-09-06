#!/usr/bin/env python3
"""MLV-App PROJECT PreToolUse gate for the never-authorized register (NA-1..NA-9).

Contract
--------
stdin  : exactly ONE JSON object, ``{"tool_name": ..., "tool_input": {...}}``.
exit 0 : ALLOW (nothing on stderr).
exit 2 : DENY, with exactly ONE line on stderr, ``NA-<n>: <reason>``.
exit 2 : fail-CLOSED, with ``hook-error: <detail>`` on stderr, for ANY exception,
         malformed or empty stdin, a non-object payload, or a missing ``tool_name``.

This is the PROJECT hook described by ``never-authorized.json`` (schema v15) and by
``prompts/v2/card-TOOL-HOOK-ENFORCE-1.md``.  It is NOT the global machine hook
``~/.claude/hooks/check-continuity-boundaries.py``, which is shared by every project on
this machine and fails OPEN.  This one fails CLOSED and is tracked on the same ref as the
tree it guards -- a hook is (interpreter x script x REGISTRATION), and only a tracked
script can travel with the worktree it governs.

Environment inputs (all optional; every default keeps the rule fail-closed on the real
machine, and only the test supplies overrides, so the falsifier table is host-independent)
-------------------------------------------------------------------------------------
MLV_LANE_PROMPT             path to the lane's prompt file; NA-4 reads its
                            ``CLIP_OR_NONE:`` line, matched ``^(- )?CLIP_OR_NONE:``
                            (full cards carry the label as a bullet, fields files at
                            column 0).  Unset => any ``*.mlv`` outside the tracked
                            fixtures is DENIED.
MLV_BOARD_ROOT              default ``C:\\!Layi Wkspc\\MLV-App``.
MLV_CLIP_CACHE_ROOT         default ``\\\\bachelor\\mlv-agent\\cache``.
MLV_REQUIRED_CHECKS_SNAPSHOT
                            default ``<board>\\.claude-state\\coordination\\dual-lane\\
                            receipts\\required-checks-live.jsonl``.  NA-9 reads the LAST
                            non-empty row.  Absent, unparseable, or a malformed row
                            anywhere => every protection mutation is DENIED.
MLV_HOOK_DRYRUN=1           print the decision on stdout; the exit code is unchanged.

Stdlib only.  Deterministic: no subprocess, no clock, no network.  The worktree root is
derived from this file's own location (``parents[2]``) rather than from ``git rev-parse``
in the session cwd -- it needs no subprocess, it is the copy that actually governs the
lane, and it is at least as closed as the cwd reading.
"""

import json
import os
import re
import sys

EXIT_ALLOW = 0
EXIT_DENY = 2

DEFAULT_BOARD_ROOT = r"C:\!Layi Wkspc\MLV-App"
DEFAULT_CLIP_CACHE_ROOT = r"\\bachelor\mlv-agent\cache"

SHELL_TOOLS = ("Bash", "PowerShell")
FILE_TOOLS = ("Write", "Edit", "NotebookEdit")
MATCHED_TOOLS = SHELL_TOOLS + FILE_TOOLS

# Relative tails, normalised.  Every containment test below is segment-wise, so the same
# marker matches an absolute path, a worktree-relative path and a board-relative one.
DUAL_TAIL = ".claude-state/coordination/dual-lane"
FLEET_TAIL = ".claude-state/fleet-runs"
NA2_PROTECTED_TAILS = (
    ".claude-state/closeout",
    FLEET_TAIL,
    ".claude-state/coordination",
    ".closeout-evidence",
)
NA2_PROTECTED_FILE_TAIL = ".claude/analysis_log.md"
KILL_SWITCH_LEAF = "workstream-loop-disabled"

CANONICAL_04B_CONTEXTS = frozenset(
    (
        "Repo Hygiene Python (windows-latest)",
        "Repo Hygiene Python (ubuntu-latest)",
        "Windows GUI Pilot",
        "Windows Product Oracles",
        "Batch Compile",
    )
)
CANONICAL_04B_APP_ID = 15368

KILL_SWITCH_RECEIPTS = (
    "0.18-roadmap-parity.json",
    "0.4b-required-checks.json",
    "0.4c-demoted.json",
    "0.6-ratio-guard.json",
    "0.5-factory-frozen.json",
)
# S98: the 0.2 enable is ONE-SHOT.  0.2 writes this receipt in the SAME guarded action as
# the delete, so its PRESENCE is the proof the one authorization was already spent.
KILL_SWITCH_ENABLE_RECEIPT = "0.2-loop-enabled.json"
PROVENANCE_KEYS = ("roadmapParityReceiptSha256", "queueSha256", "productLiveCount")
PROVENANCE_PRODUCT_LIVE_COUNT = 15


class Deny(Exception):
    """A rule fired.  ``rule`` is the register row id, ``reason`` the one-line why."""

    def __init__(self, rule, reason):
        super().__init__("%s: %s" % (rule, reason))
        self.rule = rule
        self.reason = reason


# --------------------------------------------------------------------------- paths


def norm(path):
    """Normalise for comparison: backslashes to slashes, lowercased, no trailing slash.

    Links are NOT followed and the filesystem is NOT consulted -- NA-4 requires the
    comparison to be made without following links, and a hook that stats every token is a
    hook that misses its latency budget.
    """
    text = str(path).strip().strip('"').strip("'").replace("\\", "/")
    unc = text.startswith("//")
    text = re.sub(r"/{2,}", "/", text)
    if unc:
        text = "/" + text
    text = text.lower()
    while len(text) > 1 and text.endswith("/"):
        text = text[:-1]
    return text


def after_seg(path_norm, seg_norm):
    """Remainder of ``path_norm`` after a whole-segment run of ``seg_norm``.

    Returns ``None`` when the run is absent, ``""`` when the path IS the run.
    """
    if path_norm == seg_norm:
        return ""
    if path_norm.startswith(seg_norm + "/"):
        return path_norm[len(seg_norm) + 1 :]
    key = "/" + seg_norm
    index = path_norm.find(key)
    while index != -1:
        end = index + len(key)
        if end == len(path_norm):
            return ""
        if path_norm[end] == "/":
            return path_norm[end + 1 :]
        index = path_norm.find(key, index + 1)
    return None


def has_seg(path_norm, seg_norm):
    return after_seg(path_norm, seg_norm) is not None


def under(path_norm, root_norm):
    """True when ``path_norm`` is ``root_norm`` or lives beneath it."""
    if not root_norm:
        return False
    return path_norm == root_norm or path_norm.startswith(root_norm + "/")


def is_absolute(path_norm):
    return bool(re.match(r"^(?:[a-z]:/|//)", path_norm))


# ------------------------------------------------------------------------- tokens

_TOKEN_RX = re.compile(r"\"([^\"]*)\"|'([^']*)'|([^\s'\"=,;|&()\[\]<>]+)")


def tokens(text):
    """Quoted strings and bare runs, in order.  Quotes are stripped, nothing is executed."""
    found = []
    for match in _TOKEN_RX.finditer(text or ""):
        value = match.group(1)
        if value is None:
            value = match.group(2)
        if value is None:
            value = match.group(3)
        if value:
            found.append(value)
    return found


def json_blobs(text):
    """Balanced ``{...}`` runs, string-aware.  Used to recover an inline API body."""
    blobs = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text or ""):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                blobs.append(text[start : index + 1])
                start = -1
    return blobs


# ------------------------------------------------------------------------ context


class Ctx(object):
    """Everything a rule needs, resolved once."""

    def __init__(self, tool, tool_input):
        self.tool = tool
        self.tool_input = tool_input
        env = os.environ
        self.board_root = norm(env.get("MLV_BOARD_ROOT") or DEFAULT_BOARD_ROOT)
        self.board_root_raw = env.get("MLV_BOARD_ROOT") or DEFAULT_BOARD_ROOT
        self.clip_cache_root = norm(
            env.get("MLV_CLIP_CACHE_ROOT") or DEFAULT_CLIP_CACHE_ROOT
        )
        self.worktree_root = norm(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        self.worktree_root_raw = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        self.dual_dir_raw = os.path.join(
            self.board_root_raw, ".claude-state", "coordination", "dual-lane"
        )
        self.receipts_dir_raw = os.path.join(self.dual_dir_raw, "receipts")
        self.snapshot_raw = env.get("MLV_REQUIRED_CHECKS_SNAPSHOT") or os.path.join(
            self.receipts_dir_raw, "required-checks-live.jsonl"
        )
        self.lane_prompt = env.get("MLV_LANE_PROMPT") or ""

        self.command = ""
        self.path = ""
        self.path_norm = ""
        self.old_text = None
        self.new_text = None

        if tool in SHELL_TOOLS:
            command = tool_input.get("command")
            if command is not None and not isinstance(command, str):
                raise Deny("hook-error", "non-string command for %s" % tool)
            self.command = command or ""
        elif tool == "Write":
            self.path = _first_str(tool_input, ("file_path", "path"))
            self.new_text = _opt_str(tool_input, "content")
        elif tool == "Edit":
            self.path = _first_str(tool_input, ("file_path", "path"))
            self.old_text = _opt_str(tool_input, "old_string")
            self.new_text = _opt_str(tool_input, "new_string")
        elif tool == "NotebookEdit":
            self.path = _first_str(tool_input, ("notebook_path", "file_path", "path"))
            self.new_text = _opt_str(tool_input, "new_source")
        self.path_norm = norm(self.path) if self.path else ""

    @property
    def subject(self):
        """The whole text a pattern rule may look at, for this tool."""
        if self.tool in SHELL_TOOLS:
            return self.command
        parts = [self.path or ""]
        if self.old_text:
            parts.append(self.old_text)
        if self.new_text:
            parts.append(self.new_text)
        return "\n".join(parts)

    def receipt(self, name):
        return os.path.join(self.receipts_dir_raw, name)


def _first_str(tool_input, keys):
    for key in keys:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _opt_str(tool_input, key):
    value = tool_input.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise Deny("hook-error", "non-string %s" % key)
    return value


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


# ------------------------------------------------------------------------ verbs

_DELETE_RX = re.compile(
    r"(?:^|[\s;&|`(])(?:rm|del|rd|rmdir)\b"
    r"|\bremove-item\b"
    r"|\bgit\s+rm\b"
    r"|\bos\.(?:remove|unlink)\s*\("
    r"|\bshutil\.rmtree\s*\("
    r"|\.unlink\s*\(",
    re.I,
)
_MOVE_RX = re.compile(
    r"(?:^|[\s;&|`(])(?:mv|move)\b"
    r"|\bmove-item\b"
    r"|\brename-item\b"
    r"|\bgit\s+mv\b"
    r"|\bos\.rename\s*\("
    r"|\bshutil\.move\s*\(",
    re.I,
)
# A single '>' that is followed by something path-like.  '>>' (append), '2>&1', '->',
# '>=' and '<>' are all excluded, so an append never reads as a truncation.
_TRUNC_REDIRECT_RX = re.compile(r"(?<![>\-=!<])>(?!>)\s*['\"]?[\w.$~/\\{]")
_TRUNC_CMDLET_RX = re.compile(
    r"\bclear-content\b"
    r"|\bset-content\b"
    r"|\bout-file\b"
    r"|(?:^|[\s;&|`(])tee\b"
    r"|\bos\.truncate\s*\(",
    re.I,
)
_APPEND_FLAG_RX = re.compile(r"-append\b|(?:^|\s)tee\s+(?:[^|;]*\s)?-a\b", re.I)


_GIT_RM_CACHED_RX = re.compile(r"\bgit\s+rm\b(?=[^\n;|&]*--cached\b)", re.I)


def shell_acts(command):
    """Which destructive act classes the command text exhibits.

    `git rm --cached` unstages and deletes nothing from the tree, so it is neutralised
    BEFORE the delete scan -- otherwise the bare `rm` alternative inside `git rm` matches
    first and the register's own carve-out never gets a chance to apply.
    """
    scan = _GIT_RM_CACHED_RX.sub(" gitrmcached ", command or "")
    acts = set()
    if _DELETE_RX.search(scan):
        acts.add("delete")
    if _MOVE_RX.search(scan):
        acts.add("move")
    if _TRUNC_REDIRECT_RX.search(scan):
        acts.add("truncate")
    if _TRUNC_CMDLET_RX.search(scan) and not _APPEND_FLAG_RX.search(scan):
        acts.add("truncate")
    return acts


# ------------------------------------------------------------------------- NA-1

_PUSH_RX = re.compile(r"\bgit\b[^\n;|&]*\bpush\b", re.I)
_FORCE_RX = re.compile(r"--force-with-lease(?:=\S*)?|--force\b|(?:^|\s)-[a-z]*f[a-z]*\b", re.I)
_FILTER_RX = re.compile(r"\bfilter-branch\b|\bfilter-repo\b", re.I)
_RESET_HARD_RX = re.compile(r"\bgit\b[^\n;|&]*\breset\b[^\n;|&]*--hard\b", re.I)
_PROTECTED_URL_RX = re.compile(r"layibabalola/mlv-app", re.I)


def rule_na1(ctx):
    text = ctx.subject
    if ctx.tool not in SHELL_TOOLS:
        return
    if _FILTER_RX.search(text):
        raise Deny("NA-1", "history rewrite (filter-branch/filter-repo) is never authorized")
    pushes = list(_PUSH_RX.finditer(text))
    if not pushes:
        return
    if _RESET_HARD_RX.search(text):
        raise Deny("NA-1", "`git reset --hard` followed by a push in one command rewrites history")
    for match in pushes:
        segment = text[match.start() :]
        segment = re.split(r"[\n;|&]", segment)[0]
        forced = bool(_FORCE_RX.search(segment))
        plus_refspec = any(
            token.startswith("+") and ":" in token for token in tokens(segment)
        )
        if not forced and not plus_refspec:
            continue
        targets = [norm(token) for token in tokens(segment)]
        protected = _PROTECTED_URL_RX.search(segment) or "fork" in targets
        # Fail closed: a forced push naming no remote resolves to a default this hook
        # cannot read, and the default on this board is the protected one.
        named_remote = any(
            token in ("origin", "upstream") for token in targets
        ) and not protected
        if protected or not named_remote:
            raise Deny(
                "NA-1",
                "force push or +refspec to the protected fork is never authorized",
            )


# ------------------------------------------------------------------------- NA-2


def _carve_tag(ctx, path_norm):
    """Which NA-2 carve-out (O47) a protected path sits on, if any."""
    rest = after_seg(path_norm, DUAL_TAIL)
    if rest is not None:
        if rest == KILL_SWITCH_LEAF:
            return "killswitch"
        if rest.startswith("receipts/"):
            return "receipts"
        if rest in ("queue.json", "lane-gh-capability.json"):
            return "carve-file"
        if rest.startswith("digest/"):
            return "digest"
        if "/" not in rest and rest.endswith("-resume-current.md"):
            return "resume"
        if rest.startswith("ignition/seat-") and rest.endswith(".md"):
            return "seat"
    if after_seg(path_norm, FLEET_TAIL) is not None:
        return "fleet"
    return None


def _is_na2_protected(path_norm):
    if any(has_seg(path_norm, tail) for tail in NA2_PROTECTED_TAILS):
        return True
    return has_seg(path_norm, NA2_PROTECTED_FILE_TAIL)


def _kill_switch_receipts_ok(ctx):
    """Exception (i): every named 0.2 receipt exists and validates, AND the enable is unspent.

    The execution-control arm selects the NEWEST ``execution-control-*.json`` by its
    ``recordedUtc``.  A receipt LACKING ``recordedUtc`` is INVALID (O105) and its mere
    presence makes the newest undecidable, so the whole exception fails closed.

    S98 -- THE ENABLE IS ONE-SHOT, and this is the first arm because it is the decisive
    one.  Exception (i) opens only while ``receipts/0.2-loop-enabled.json`` is ABSENT.  0.2
    creates that receipt in the same guarded action as the delete, so once it exists the
    single ratified authorization has been spent: a RE-ARMED marker (creating the kill
    switch is always allowed, exception (iii)) cannot then be deleted again without a NEWLY
    ratified authorization.  Without this arm the six receipts would be a standing key --
    valid forever, re-usable on every re-arm -- which is a gate that opens once and never
    closes.  Fail-closed: PRESENCE of the path is enough; it is not parsed, so an
    unreadable or truncated enable receipt still refuses.
    """
    enable_receipt = ctx.receipt(KILL_SWITCH_ENABLE_RECEIPT)
    if os.path.exists(enable_receipt):
        return (
            False,
            "the 0.2 enable is ONE-SHOT and receipts/%s is PRESENT, so this authorization "
            "is spent -- a re-armed marker needs a newly ratified authorization (S98)"
            % KILL_SWITCH_ENABLE_RECEIPT,
        )
    for name in KILL_SWITCH_RECEIPTS:
        if read_json(ctx.receipt(name)) is None:
            return False, "receipt %s is absent or does not validate" % name
    try:
        names = sorted(os.listdir(ctx.receipts_dir_raw))
    except Exception:
        return False, "receipts directory is unreadable"
    controls = [
        name
        for name in names
        if name.startswith("execution-control-") and name.endswith(".json")
    ]
    if not controls:
        return False, "no execution-control receipt is present"
    newest = None
    for name in controls:
        document = read_json(ctx.receipt(name))
        if not isinstance(document, dict):
            return False, "execution-control receipt %s does not validate" % name
        stamp = document.get("recordedUtc")
        if not isinstance(stamp, str) or not stamp:
            return False, "execution-control receipt %s lacks recordedUtc" % name
        if newest is None or stamp > newest[0]:
            newest = (stamp, name, document)
    document = newest[2]
    for key in PROVENANCE_KEYS:
        if key not in document:
            return False, "newest execution-control receipt lacks %s" % key
    if document.get("productLiveCount") != PROVENANCE_PRODUCT_LIVE_COUNT:
        return False, "newest execution-control receipt carries the wrong productLiveCount"
    return True, ""


def _archive_release_ok(ctx, path_norm, new_text):
    """Exception (ii): a byte-identical archive copy exists and the stub names its sha256."""
    if new_text is None:
        return False
    import hashlib

    target = None
    for candidate in (ctx.path,):
        if candidate and os.path.isfile(candidate):
            target = candidate
    if target is None:
        return False
    try:
        with open(target, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
    except Exception:
        return False
    archive = os.path.join(ctx.board_root_raw, ".claude-state", "continuity", "archive")
    matched = False
    for root, _dirs, files in os.walk(archive):
        for name in files:
            try:
                with open(os.path.join(root, name), "rb") as handle:
                    if hashlib.sha256(handle.read()).hexdigest() == digest:
                        matched = True
                        break
            except Exception:
                continue
        if matched:
            break
    if not matched:
        return False
    return digest.lower() in new_text.lower()


def _file_shrinks(ctx):
    """Does this Write/Edit/NotebookEdit make an EXISTING target shorter?"""
    if ctx.tool == "Edit":
        if ctx.old_text is None or ctx.new_text is None:
            return True  # fail closed: an unreadable edit cannot be proven non-shrinking
        return len(ctx.new_text) < len(ctx.old_text)
    if ctx.new_text is None:
        return True
    if not ctx.path or not os.path.isfile(ctx.path):
        return False  # a create is create-or-extend, which the carve-out allows
    try:
        existing = os.path.getsize(ctx.path)
    except Exception:
        return True
    return len(ctx.new_text.encode("utf-8")) < existing


def _na2_decide(ctx, path_norm, acts, source):
    """One protected path, one act set.  Raises Deny, or returns for ALLOW."""
    tag = _carve_tag(ctx, path_norm)

    if tag == "killswitch":
        if "delete" in acts or "move" in acts:
            ok, why = _kill_switch_receipts_ok(ctx)
            if ok:
                return
            raise Deny(
                "NA-2",
                "deleting WORKSTREAM-LOOP-DISABLED is refused: %s" % why,
            )
        # Exception (iii): CREATING the kill switch is ALWAYS allowed, any tool, any verb.
        return

    if tag is not None:
        if "delete" in acts:
            raise Deny("NA-2", "deleting %s is denied on the carve-out paths" % path_norm)
        if "move" in acts:
            raise Deny("NA-2", "moving %s is denied on the carve-out paths" % path_norm)
        if source == "shell":
            if "truncate" in acts:
                raise Deny(
                    "NA-2",
                    "a truncating write to %s cannot be proven non-shrinking" % path_norm,
                )
            return
        if _file_shrinks(ctx):
            if tag in ("resume", "seat") and _archive_release_ok(ctx, path_norm, ctx.new_text):
                return
            raise Deny(
                "NA-2",
                "shrinking overwrite of %s (archive with a stub instead)" % path_norm,
            )
        return

    if source == "shell":
        if acts:
            raise Deny(
                "NA-2",
                "%s of ledger/pen/receipt/evidence content at %s"
                % ("/".join(sorted(acts)), path_norm),
            )
        return
    raise Deny("NA-2", "overwriting ledger/pen/receipt/evidence content at %s" % path_norm)


def rule_na2(ctx):
    if ctx.tool in SHELL_TOOLS:
        acts = shell_acts(ctx.command)
        if not acts:
            return
        for token in tokens(ctx.command):
            path_norm = norm(token)
            if _is_na2_protected(path_norm):
                _na2_decide(ctx, path_norm, acts, "shell")
        return
    if not ctx.path_norm or not _is_na2_protected(ctx.path_norm):
        return
    _na2_decide(ctx, ctx.path_norm, {"write"}, "file")


# ------------------------------------------------------------------------- NA-3

_TOKEN_PREFIXES = r"(?:ANTHROPIC_|OPENAI_|CLAUDE_CODE_)"
_NA3_RULES = (
    (
        re.compile(r"(?:^|[\s;&|`(])" + _TOKEN_PREFIXES + r"\w*\s*=", re.I),
        "assignment of a name starting ANTHROPIC_/OPENAI_/CLAUDE_CODE_",
    ),
    (
        re.compile(r"\bexport\s+" + _TOKEN_PREFIXES, re.I),
        "export of a name starting ANTHROPIC_/OPENAI_/CLAUDE_CODE_",
    ),
    (
        re.compile(r"\$env:" + _TOKEN_PREFIXES + r"\w*\s*=", re.I),
        "$env: assignment of a name starting ANTHROPIC_/OPENAI_/CLAUDE_CODE_",
    ),
    (
        re.compile(r"\bset\s+" + _TOKEN_PREFIXES + r"\w*\s*=", re.I),
        "`set` assignment of a name starting ANTHROPIC_/OPENAI_/CLAUDE_CODE_",
    ),
    (
        re.compile(r"\bsetx\s+" + _TOKEN_PREFIXES, re.I),
        "`setx` of a name starting ANTHROPIC_/OPENAI_/CLAUDE_CODE_",
    ),
    (
        re.compile(
            r"setenvironmentvariable\s*\(\s*['\"]" + _TOKEN_PREFIXES, re.I
        ),
        "SetEnvironmentVariable of a name starting ANTHROPIC_/OPENAI_/CLAUDE_CODE_",
    ),
    (re.compile(r"\bcodex\b[^\n;|&]*\blogin\b", re.I), "`codex login` is the owner's own keystrokes"),
)

# ---- the `claude auth ...` arm, bounded by the REGISTER and not by the card's phrase ----
#
# ``never-authorized.json`` (schema v15) row NA-3 reads, VERBATIM:
#
#     claude auth login|logout, codex login, or assignment (X=, export, $env:, set, setx)
#     of any variable starting ANTHROPIC_, OPENAI_, CLAUDE_CODE_
#
# so the acts the register never authorizes are `login` and `logout`.  The card's prose
# says only "claude auth", and this hook shipped a `\bclaude\b...\bauth\b` catch-all that
# also denied `claude auth status` -- a READ-ONLY identity check that MUST keep running.
# The board's account-rotation procedure settles CLI-versus-desktop identity with exactly
# `claude auth status --json`, and from plan step 0.1 onward the hub session that runs it
# is itself under this hook; denying it would break the one procedure that detects an
# account drift, while stopping no never-authorized act.
#
# FAIL-CLOSED READING OF THE GAP (recorded, 0.05 review).  The register names two verbs;
# this arm admits exactly ONE -- `status`, alone or with any flags.  EVERY other
# `claude ... auth ...` form is DENIED: `auth` with no subcommand, and any verb this hook
# does not recognise.  The alternative -- a bare `login|logout` denylist that matches the
# register literally -- fails OPEN on the next auth verb the CLI grows, and NA-3 is a
# fail-closed rule.  The cost of this reading is a false DENY on a future read-only verb,
# which is visible and one line to fix; the cost of the other is a silent ALLOW.
_NA3_CLAUDE_AUTH_RX = re.compile(r"\bclaude\b(?P<rest>[^\n;|&<>]*\bauth\b[^\n;|&<>]*)", re.I)
_NA3_AUTH_SPLIT_RX = re.compile(r"\bauth\b", re.I)
_NA3_LOGIN_RX = re.compile(r"\b(?:login|logout)\b", re.I)
NA3_CLAUDE_AUTH_READONLY_VERB = "status"


def _na3_claude_auth(text):
    """The `claude auth ...` arm.  Returns a one-line DENY reason, or None to ALLOW."""
    for match in _NA3_CLAUDE_AUTH_RX.finditer(text):
        segment = match.group("rest")
        if _NA3_LOGIN_RX.search(segment):
            return "`claude auth login|logout` is the owner's own keystrokes (register NA-3)"
        verbs = []
        for word in _NA3_AUTH_SPLIT_RX.split(segment, 1)[-1].split():
            word = word.strip("\"'")
            if not word or word.startswith("-"):
                continue  # a flag, in any order
            verbs.append(word.lower())
        if verbs == [NA3_CLAUDE_AUTH_READONLY_VERB]:
            continue  # read-only `claude auth status [--json]`: the rotation check
        return (
            "`claude auth` without the one read-only subcommand `status` cannot be proven "
            "read-only (register NA-3 names login|logout; fail-closed on any other verb)"
        )
    return None


def rule_na3(ctx):
    text = ctx.subject
    if ctx.tool not in SHELL_TOOLS:
        return
    for pattern, why in _NA3_RULES:
        if pattern.search(text):
            raise Deny("NA-3", why)
    why = _na3_claude_auth(text)
    if why:
        raise Deny("NA-3", why)


# ------------------------------------------------------------------------- NA-4

_CLIP_LINE_RX = re.compile(r"^(?:- )?CLIP_OR_NONE:\s*(.*?)\s*$", re.M)
FIXTURE_TAIL = "tests/fixtures/clips"


def authorized_clip(ctx):
    """The ONE canonical absolute path from the lane prompt, or None."""
    if not ctx.lane_prompt:
        return None
    try:
        with open(ctx.lane_prompt, "r", encoding="utf-8", errors="replace") as handle:
            body = handle.read()
    except Exception:
        return None
    match = _CLIP_LINE_RX.search(body)
    if not match:
        return None
    value = match.group(1).strip().strip("`").strip('"').strip("'")
    if not value or value.lower() == "none":
        return None
    return norm(value)


def rule_na4(ctx):
    allowed = authorized_clip(ctx)
    fixture_root = norm(os.path.join(ctx.worktree_root_raw, FIXTURE_TAIL))
    for token in tokens(ctx.subject):
        path_norm = norm(token)
        is_clip = path_norm.endswith(".mlv")
        in_cache = under(path_norm, ctx.clip_cache_root) or has_seg(
            path_norm, ctx.clip_cache_root.lstrip("/")
        )
        if not is_clip and not in_cache:
            continue
        if under(path_norm, fixture_root) or has_seg(path_norm, FIXTURE_TAIL):
            continue  # tracked fixtures are always allowed
        if allowed is not None and path_norm == allowed:
            continue
        if allowed is None:
            raise Deny(
                "NA-4",
                "no CLIP_OR_NONE authorization for %s (fixtures only)" % path_norm,
            )
        raise Deny(
            "NA-4",
            "clip %s is not the authorized path %s (a same-basename clip is a different clip)"
            % (path_norm, allowed),
        )


# ------------------------------------------------------------------------- NA-6

_ASSERT_RX = re.compile(r"\bQVERIFY\b|\bQCOMPARE\b|\bassert\b")
_QSKIP_RX = re.compile(r"\bQSKIP\b")
_RUN_HEAD_RX = re.compile(r"^([ \t]*)run:[ \t]*(.*)$")
_BLOCK_SCALAR_MARKERS = ("|", "|-", "|+", ">", ">-", ">+", "")
_TEST_CPP_RX = re.compile(r"\btest_[\w./\\-]*\.cpp\b", re.I)
WORKFLOW_TAIL = ".github/workflows"


def _indent_of(line):
    return len(re.match(r"^[ \t]*", line).group(0).expandtabs(4))


def _run_bodies(text):
    """Every ``run:`` body in a workflow fragment, whitespace-normalised.

    A block scalar ends at the first non-blank line indented no more than its ``run:``
    key -- getting that boundary wrong would swallow the following job and make a MOVE
    look like a removal (or the reverse), which is precisely the distinction NA-6's
    exception turns on.
    """
    bodies = []
    lines = (text or "").splitlines()
    index = 0
    while index < len(lines):
        head = _RUN_HEAD_RX.match(lines[index])
        if not head:
            index += 1
            continue
        indent = _indent_of(head.group(1))
        rest = head.group(2).strip()
        index += 1
        if rest not in _BLOCK_SCALAR_MARKERS:
            bodies.append(re.sub(r"\s+", " ", rest).strip())
            continue
        block = []
        while index < len(lines):
            line = lines[index]
            if line.strip() and _indent_of(line) <= indent:
                break
            block.append(line.strip())
            index += 1
        body = re.sub(r"\s+", " ", " ".join(block)).strip()
        if body:
            bodies.append(body)
    return bodies


def rule_na6(ctx):
    if ctx.tool in SHELL_TOOLS:
        acts = shell_acts(ctx.command)
        if "delete" not in acts:
            return
        for token in tokens(ctx.command):
            path_norm = norm(token)
            if has_seg(path_norm, "tests") or path_norm.startswith("tests/"):
                raise Deny("NA-6", "deleting %s removes a test" % path_norm)
        return

    path_norm = ctx.path_norm
    if not path_norm:
        return
    new_text = ctx.new_text or ""
    old_text = ctx.old_text

    if has_seg(path_norm, WORKFLOW_TAIL):
        if "continue-on-error" in new_text.lower():
            raise Deny("NA-6", "continue-on-error in a workflow makes a red test green")
        if old_text is not None:
            # The predicate is NET removal across .github/workflows/*.yml, never per-edit:
            # a step MOVED with a byte-identical `run:` body removes nothing (O108).
            after = _run_bodies(new_text)
            removed = [body for body in _run_bodies(old_text) if body not in after]
            if removed:
                raise Deny("NA-6", "net removal of a workflow test step: %r" % removed[0][:80])
        return

    if path_norm.endswith(".pro") and old_text is not None:
        after = set(_TEST_CPP_RX.findall(new_text))
        removed = [name for name in _TEST_CPP_RX.findall(old_text) if name not in after]
        if removed:
            raise Deny("NA-6", "removal of %s from a .pro manifest" % removed[0])
        return

    if has_seg(path_norm, "tests") or path_norm.startswith("tests/"):
        if old_text is None:
            if ctx.tool == "Write" and ctx.path and os.path.isfile(ctx.path):
                try:
                    with open(ctx.path, "r", encoding="utf-8", errors="replace") as handle:
                        old_text = handle.read()
                except Exception:
                    raise Deny("NA-6", "the existing test at %s is unreadable" % path_norm)
            else:
                return  # a NEW test file weakens nothing
        if _QSKIP_RX.search(new_text) and not _QSKIP_RX.search(old_text):
            raise Deny("NA-6", "QSKIP added to %s" % path_norm)
        before = len(_ASSERT_RX.findall(old_text))
        after = len(_ASSERT_RX.findall(new_text))
        if after < before:
            raise Deny(
                "NA-6",
                "assertion count in %s falls from %d to %d" % (path_norm, before, after),
            )


# ------------------------------------------------------------------------- NA-7

FACTORY_TAIL = ".factory"
_SED_I_RX = re.compile(r"\bsed\b[^\n;|&]*?\s-i\b[^\n;|&]*", re.I)
_COPY_RX = re.compile(r"(?:^|[\s;&|`(])(?:cp)\b[^\n;|&]*|\bcopy-item\b[^\n;|&]*", re.I)
_SET_CONTENT_RX = re.compile(
    r"\b(?:set-content|out-file|add-content|clear-content)\b[^\n;|&]*", re.I
)
_REDIRECT_DEST_RX = re.compile(r"(?<![\-=!<])>>?\s*(['\"][^'\"]+['\"]|[^\s;|&()<>]+)")
_TEE_RX = re.compile(r"(?:^|[\s;&|`(])tee\b[^\n;|&]*", re.I)


def _write_destinations(command):
    """Every path this command text could WRITE to."""
    dests = []
    for match in _REDIRECT_DEST_RX.finditer(command):
        dests.append(match.group(1))
    for pattern in (_SED_I_RX, _COPY_RX, _SET_CONTENT_RX, _TEE_RX):
        for match in pattern.finditer(command):
            segment = match.group(0)
            candidates = [
                token
                for token in tokens(segment)[1:]
                if not token.startswith("-") and ("/" in token or "\\" in token)
            ]
            if pattern is _COPY_RX and len(candidates) > 1:
                candidates = candidates[1:]  # cp SRC DST -- only DST is written
            dests.extend(candidates)
    return dests


def _na7_check_path(ctx, path_norm, how):
    if has_seg(path_norm, FACTORY_TAIL):
        raise Deny("NA-7", "%s into .factory/ is never authorized (%s)" % (how, path_norm))
    if not is_absolute(path_norm):
        return  # a relative destination resolves inside the worktree by construction
    if under(path_norm, ctx.worktree_root) or under(path_norm, ctx.board_root):
        return
    raise Deny(
        "NA-7",
        "%s outside both the worktree and the board root (%s)" % (how, path_norm),
    )


def rule_na7(ctx):
    if ctx.tool in SHELL_TOOLS:
        dests = _write_destinations(ctx.command)
        if dests or shell_acts(ctx.command):
            for token in tokens(ctx.command):
                path_norm = norm(token)
                if has_seg(path_norm, FACTORY_TAIL):
                    raise Deny(
                        "NA-7", "writing into .factory/ is never authorized (%s)" % path_norm
                    )
        for dest in dests:
            _na7_check_path(ctx, norm(dest), "write")
        return
    if ctx.path_norm:
        _na7_check_path(ctx, ctx.path_norm, "%s" % ctx.tool)


# ------------------------------------------------------------------------- NA-8

_NA8_RX = re.compile(
    r"(?:\bset-itemproperty\b|\breg\b\s+add\b)[^\n;|&]*usergpupreferences"
    r"|\busergpupreferences\b[^\n;|&]*(?:\bset-itemproperty\b|\breg\b\s+add\b)"
    r"|\bpowercfg\b",
    re.I,
)


def rule_na8(ctx):
    if ctx.tool not in SHELL_TOOLS:
        return
    if _NA8_RX.search(ctx.command):
        raise Deny("NA-8", "the owner's graphics/power machine state is never an agent's to mutate")


# ------------------------------------------------------------------------- NA-9

_PROTECTION_RX = re.compile(
    r"\bgh\b[^\n]*\bapi\b[^\n]*(?:-X|--method)\s*(DELETE|PATCH|PUT)\b", re.I
)


def _rsc(document):
    if not isinstance(document, dict):
        return None
    inner = document.get("required_status_checks")
    if isinstance(inner, dict):
        return inner
    if "checks" in document or "contexts" in document:
        return document
    return None


def check_set(document):
    """-> {'contexts': {name: app_id_or_None}, 'strict': bool_or_None} or None."""
    section = _rsc(document)
    if section is None:
        return None
    contexts = {}
    checks = section.get("checks")
    legacy = section.get("contexts")
    if isinstance(checks, list):
        for item in checks:
            if isinstance(item, dict) and isinstance(item.get("context"), str):
                contexts[item["context"]] = item.get("app_id")
            elif isinstance(item, str):
                contexts[item] = None
            else:
                return None
    elif isinstance(legacy, list):
        for item in legacy:
            if not isinstance(item, str):
                return None
            contexts[item] = None
    else:
        return None
    if not contexts:
        return None
    return {"contexts": contexts, "strict": section.get("strict")}


def load_snapshot(ctx):
    """LAST non-empty row of the append-only JSONL snapshot, or None (fail closed)."""
    try:
        with open(ctx.snapshot_raw, "r", encoding="utf-8") as handle:
            rows = handle.read().splitlines()
    except Exception:
        return None
    payloads = []
    for row in rows:
        if not row.strip():
            continue
        try:
            payloads.append(json.loads(row))
        except Exception:
            return None  # a malformed row ANYWHERE fails closed
    if not payloads:
        return None
    return check_set(payloads[-1])


def _canonical_04b(body):
    if body is None:
        return False
    if set(body["contexts"]) != set(CANONICAL_04B_CONTEXTS):
        return False
    if body.get("strict") is not True:
        return False
    return all(app_id == CANONICAL_04B_APP_ID for app_id in body["contexts"].values())


def _04b_receipts_ok(ctx):
    falsifier = read_json(ctx.receipt("0.4a-batch-compile-falsifier.json"))
    if not isinstance(falsifier, dict) or falsifier.get("failingContext") != "Batch Compile":
        return False
    guardrail = read_json(ctx.receipt("0.4c-guardrail-move.json"))
    if not isinstance(guardrail, dict) or guardrail.get("conclusion") != "success":
        return False
    return not os.path.exists(ctx.receipt("0.4b-required-checks.json"))


def rule_na9(ctx):
    if ctx.tool not in SHELL_TOOLS:
        return
    match = _PROTECTION_RX.search(ctx.command)
    if not match:
        return
    lowered = ctx.command.lower()
    if "branches/" not in lowered or "protection" not in lowered:
        return
    method = match.group(1).upper()

    snapshot = load_snapshot(ctx)
    if snapshot is None:
        raise Deny(
            "NA-9",
            "the required-checks snapshot is absent or unparseable; every protection "
            "mutation is denied while it is",
        )
    if method == "DELETE":
        raise Deny("NA-9", "deleting branch protection removes every required context")

    body = None
    for blob in json_blobs(ctx.command):
        try:
            candidate = check_set(json.loads(blob))
        except Exception:
            continue
        if candidate is not None:
            body = candidate
            break
    if body is None:
        raise Deny("NA-9", "no readable required-check body; add-only cannot be proven")

    removed = sorted(set(snapshot["contexts"]) - set(body["contexts"]))
    weakened = snapshot.get("strict") is True and body.get("strict") is not True
    for name, app_id in snapshot["contexts"].items():
        if app_id is not None and name in body["contexts"] and body["contexts"][name] != app_id:
            weakened = True
    if not removed and not weakened:
        return  # add-only, judged against the last recorded set
    if _canonical_04b(body) and _04b_receipts_ok(ctx):
        return  # the single recorded 0.4b transition
    if removed:
        raise Deny("NA-9", "removes required context(s) %s" % ", ".join(removed))
    raise Deny("NA-9", "weakens the required-check binding (strict or app_id)")


RULES = (rule_na1, rule_na2, rule_na3, rule_na4, rule_na6, rule_na7, rule_na8, rule_na9)


# -------------------------------------------------------------------------- main


def decide(payload):
    """-> (exit_code, stderr_line).  Never raises for a rule; raises only on a bug."""
    if not isinstance(payload, dict):
        return EXIT_DENY, "hook-error: payload was not a JSON object"
    tool = payload.get("tool_name")
    if not isinstance(tool, str) or not tool:
        return EXIT_DENY, "hook-error: missing tool_name"
    tool_input = payload.get("tool_input")
    if tool_input is None:
        tool_input = {}
    if not isinstance(tool_input, dict):
        return EXIT_DENY, "hook-error: tool_input was not an object"
    if tool not in MATCHED_TOOLS:
        return EXIT_ALLOW, ""
    try:
        ctx = Ctx(tool, tool_input)
        for rule in RULES:
            rule(ctx)
    except Deny as deny:
        if deny.rule == "hook-error":
            return EXIT_DENY, "hook-error: %s" % deny.reason
        return EXIT_DENY, "%s: %s" % (deny.rule, deny.reason)
    return EXIT_ALLOW, ""


def main():
    try:
        raw = sys.stdin.read()
    except Exception as error:
        sys.stderr.write("hook-error: stdin unreadable (%s)\n" % type(error).__name__)
        return EXIT_DENY
    if not raw or not raw.strip():
        sys.stderr.write("hook-error: empty stdin\n")
        return EXIT_DENY
    try:
        payload = json.loads(raw)
    except Exception as error:
        sys.stderr.write("hook-error: stdin was not one JSON object (%s)\n" % type(error).__name__)
        return EXIT_DENY
    try:
        code, line = decide(payload)
    except Exception as error:  # any bug in a rule denies; this hook never fails open
        sys.stderr.write("hook-error: %s: %s\n" % (type(error).__name__, error))
        return EXIT_DENY
    if line:
        sys.stderr.write(line + "\n")
    if os.environ.get("MLV_HOOK_DRYRUN") == "1":
        sys.stdout.write("DRYRUN %s %s\n" % ("ALLOW" if code == EXIT_ALLOW else "DENY", line))
    return code


if __name__ == "__main__":
    sys.exit(main())
