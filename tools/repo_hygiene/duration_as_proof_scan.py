"""PROD-TELEMETRY-DURATION-AS-PROOF-4 round 1: the live scanner behind
test_duration_as_proof_inventory.py.

Finds candidate "duration used as run-proof" sites: places that compare a duration-named
value against zero (or check it via a gtest predicate macro) to decide whether some stage
ran, produced output, or is used as an exclusivity/fallback signal. This is a LOWER BOUND,
not a completeness proof -- see the inventory JSON's own header for the same caveat, and
tests/test_positive_control in the test module for the two seeded snippets that pin the
matcher's actual behaviour (one it must flag, one it must not).

Patterns matched (all case-sensitive, per-physical-line):
  - IDENT_COMPARE: an identifier ending in one of the duration suffixes ``_ms``, ``Ms``
    (this also matches ``SumMs``, which is just ``Ms`` on a longer identifier) or
    ``_seconds``, compared against a zero literal (``0``, ``0.0``, ``0.0f``, ``0L``, ...)
    with one of ``>= <= == != > <``, in either operand order.
  - MILLISECONDS_GETTER: a call of the shape ``FooMilliseconds()`` compared against zero
    the same way (the identifier itself does not end in one of the suffixes above, so this
    needs its own pattern).
  - JSON_MS_KEY: a JSON/QJsonObject-style read of a key ending in ``_ms`` via
    ``.toDouble()`` or ``.toInt()``, compared against zero the same way -- e.g.
    ``sample.value(QStringLiteral("raw_uint16_ms")).toDouble() > 0.0``.
  - ASSERT_MACRO: a gtest ``ASSERT_*``/``EXPECT_*`` predicate macro (``EQ NE GT GE LT LE``)
    whose two top-level arguments are a zero literal and a duration expression (any of the
    three shapes above, or a plain duration identifier with no operator at all, since the
    macro itself encodes the comparison).

A line can trigger more than one pattern; that does not create more than one candidate --
candidates are per PHYSICAL LINE (a "site"), not per pattern.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCAN_GLOBS = (
    "src/*.c", "src/*.cpp", "src/*.h", "src/*.hpp",
    "platform/qt/*.c", "platform/qt/*.cpp", "platform/qt/*.h", "platform/qt/*.hpp",
    "tests/*.c", "tests/*.cpp", "tests/*.h", "tests/*.hpp",
)

_ZERO = r"0(?:\.0+)?[fFlLuU]*"
_OP = r"(?:>=|<=|==|!=|>|<)"
_DURATION_IDENT = r"[A-Za-z_][A-Za-z0-9_]*(?:_ms|_seconds|Ms)"
_MS_GETTER = r"[A-Za-z_][A-Za-z0-9_]*Milliseconds\s*\(\s*\)"
_JSON_MS = r'"[A-Za-z0-9_]*_ms"\s*\)\s*\)\s*\.\s*to(?:Double|Int)\s*\(\s*\)'

_RE_IDENT_COMPARE = re.compile(
    rf"\b{_DURATION_IDENT}\b\s*{_OP}\s*{_ZERO}\b|\b{_ZERO}\s*{_OP}\s*{_DURATION_IDENT}\b"
)
_RE_MS_GETTER = re.compile(
    rf"{_MS_GETTER}\s*{_OP}\s*{_ZERO}\b|\b{_ZERO}\s*{_OP}\s*{_MS_GETTER}"
)
_RE_JSON_MS = re.compile(
    rf"{_JSON_MS}\s*{_OP}\s*{_ZERO}\b|\b{_ZERO}\s*{_OP}\s*{_JSON_MS}"
)
_RE_MACRO_CALL = re.compile(r"\b(?:ASSERT|EXPECT)_(?:EQ|NE|GT|GE|LT|LE)\s*\(([^;]*)\)\s*;")
_RE_ZERO_FULL = re.compile(rf"^{_ZERO}$")
_RE_DURATION_EXPR = re.compile(rf"{_DURATION_IDENT}|{_MS_GETTER}|{_JSON_MS}")


def _split_top_level_args(arg_text: str) -> list[str] | None:
    """Split a macro's argument text on top-level commas (paren/bracket/quote aware).

    Returns None if parens are unbalanced (e.g. the macro call actually spans multiple
    physical lines) so the caller can skip it rather than mis-split it.
    """
    depth = 0
    quote = None
    parts: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(arg_text):
        ch = arg_text[i]
        if quote:
            current.append(ch)
            if ch == "\\" and i + 1 < len(arg_text):
                i += 1
                current.append(arg_text[i])
            elif ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            current.append(ch)
        elif ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    if depth != 0 or quote is not None:
        return None
    parts.append("".join(current))
    return parts


def _macro_is_duration_zero_predicate(line: str) -> bool:
    for m in _RE_MACRO_CALL.finditer(line):
        args = _split_top_level_args(m.group(1))
        if args is None or len(args) != 2:
            continue
        a, b = (x.strip() for x in args)
        a_zero, b_zero = bool(_RE_ZERO_FULL.match(a)), bool(_RE_ZERO_FULL.match(b))
        a_dur, b_dur = bool(_RE_DURATION_EXPR.search(a)), bool(_RE_DURATION_EXPR.search(b))
        if (a_zero and b_dur) or (b_zero and a_dur):
            return True
    return False


def _line_triggers(line: str) -> list[str]:
    triggers = []
    if _RE_IDENT_COMPARE.search(line):
        triggers.append("ident_compare")
    if _RE_MS_GETTER.search(line):
        triggers.append("milliseconds_getter")
    if _RE_JSON_MS.search(line):
        triggers.append("json_ms_key")
    if _macro_is_duration_zero_predicate(line):
        triggers.append("assert_macro")
    return triggers


def normalize_anchor(line: str) -> str:
    """The anchor is the site's identity: normalized text, never a line number.

    Leading/trailing whitespace is stripped and internal whitespace runs are collapsed to
    a single space, so pure reformatting (re-indent, alignment) does not force a spurious
    reclassification -- but any semantic edit to the line changes the anchor and is caught.
    """
    return re.sub(r"\s+", " ", line.strip())


@dataclass(frozen=True)
class Candidate:
    path: str  # POSIX-style, relative to repo root
    anchor: str
    triggers: tuple[str, ...]
    lines: tuple[int, ...]  # 1-indexed line numbers this anchor text was found at


def _tracked_files(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", *SCAN_GLOBS],
        capture_output=True, text=True, check=True,
    )
    return [line for line in proc.stdout.splitlines() if line.strip()]


def scan_text(text: str, source: str = "<text>") -> list[Candidate]:
    """Scan already-loaded text (used directly by the positive-control tests)."""
    by_anchor: dict[str, tuple[set[str], list[int]]] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        triggers = _line_triggers(line)
        if not triggers:
            continue
        anchor = normalize_anchor(line)
        trig_set, lines = by_anchor.setdefault(anchor, (set(), []))
        trig_set.update(triggers)
        lines.append(lineno)
    return [
        Candidate(path=source, anchor=anchor, triggers=tuple(sorted(trig_set)), lines=tuple(lines))
        for anchor, (trig_set, lines) in by_anchor.items()
    ]


def scan_repo(root: Path | None = None) -> list[Candidate]:
    root = root or ROOT
    candidates: list[Candidate] = []
    for rel_path in _tracked_files(root):
        abs_path = root / rel_path
        try:
            text = abs_path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        candidates.extend(scan_text(text, source=rel_path.replace("\\", "/")))
    return candidates


if __name__ == "__main__":
    import json
    import sys

    rows = [
        {"path": c.path, "anchor": c.anchor, "triggers": list(c.triggers), "lines": list(c.lines)}
        for c in sorted(scan_repo(), key=lambda c: (c.path, c.lines[0]))
    ]
    json.dump(rows, sys.stdout, indent=2)
    sys.stdout.write("\n")
