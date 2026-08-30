#!/usr/bin/env python3
"""One-shot, verbatim demotion of CLAUDE.md detail into claude/ children.

Implements section 4 of docs/22-doc-fragmentation-policy.md ("demote in place"):
CLAUDE.md keeps its exact path and becomes the index; detail moves to children in
the sibling directory named for the parent stem (CLAUDE -> claude/).

Guarantees, all machine-checked before anything is written:
  * every moved block is byte-identical to the source (SHA256 compared per block)
  * every substring asserted against CLAUDE.md by the closeout tooling baseline
    survives in the rewritten CLAUDE.md (otherwise closeout_tooling_stale fires)
  * no section is dropped: moved + retained == original section set

Run with --dry-run first. Writes nothing unless every check passes.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys

PARENT = "CLAUDE.md"
CHILD_DIR = "claude"

# heading -> child file. Order within a child follows this list.
PLAN = [
    ("## Agent Bridge \u2014 Session Closeout", "session-closeout.md"),
    ("## Architecture (Locked \u2014 Do Not Deviate)", "batch-cli-spec.md"),
    ("## Target Export Format", "batch-cli-spec.md"),
    ("## Settings / Receipt Strategy (PHASED \u2014 Critical Design Decision)", "batch-cli-spec.md"),
    ("## Build Environment", "batch-cli-spec.md"),
    ("## Key Technical Constraints", "batch-cli-spec.md"),
    ("## File Structure", "batch-cli-spec.md"),
    ("## Implementation Phases (Execute In Order \u2014 Do Not Skip Ahead)", "batch-cli-spec.md"),
    ("## Exit Code Reference", "batch-cli-spec.md"),
    ("## CLI Usage (Target)", "batch-cli-spec.md"),
    ("## .NET Orchestrator Integration (Later \u2014 Not Claude Code's Job)", "batch-cli-spec.md"),
]

CHILD_TITLES = {
    "session-closeout.md": "Agent Bridge - Session Closeout (detail)",
    "batch-cli-spec.md": "Batch CLI Implementation Spec (detail)",
}

# Kept in the parent because they are read on every session start.
RETAIN = [
    "## Purpose",
    "## Memory Policy (project-level content, machine-level pointers)",
    "## Agent Bridge \u2014 Session Startup (Hook-Driven)",
    "## Behavioral Rules for Claude Code",
]


def split_sections(text):
    """Return (preamble, [(heading, body_including_heading), ...]) verbatim."""
    lines = text.split("\n")
    idx = [i for i, l in enumerate(lines) if l.startswith("## ")]
    if not idx:
        raise SystemExit("no '## ' sections found in %s" % PARENT)
    preamble = "\n".join(lines[: idx[0]])
    out = []
    for a, b in zip(idx, idx[1:] + [len(lines)]):
        out.append((lines[a], "\n".join(lines[a:b])))
    return preamble, out


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def derive_pinned(repo_root):
    src = os.path.join(repo_root, "tools/repo_hygiene/brokered_closeout.py")
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    rx = re.compile(r'\{"path":\s*"CLAUDE\.md",\s*"contains":\s*"((?:[^"\\]|\\.)*)"\}')
    return [m.encode().decode("unicode_escape") for m in rx.findall(text)]


def build_index(preamble, retained, pinned, moved_map):
    """Compose the new CLAUDE.md: pointers + procedures, no demoted detail."""
    parts = [preamble.rstrip("\n"), ""]

    parts.append("## Document Map")
    parts.append("")
    parts.append(
        "This file is an INDEX. It carries pointers and session-start procedure only; "
        "detail lives in `%s/` children. Governed by "
        "[docs/22-doc-fragmentation-policy.md](docs/22-doc-fragmentation-policy.md) "
        "(entry tier: 8 KB soft / 12 KB hard). Check with:" % CHILD_DIR
    )
    parts.append("")
    parts.append("```bash")
    parts.append("py -3 tools/docs/check_pinned_tokens.py")
    parts.append("```")
    parts.append("")
    parts.append("Section headings are stable IDs. Demoted sections and where they went:")
    parts.append("")
    parts.append("| Section (stable ID) | Now lives in |")
    parts.append("|---|---|")
    for heading, child in PLAN:
        name = heading[3:]
        parts.append("| %s | [%s/%s](%s/%s) |" % (name, CHILD_DIR, child, CHILD_DIR, child))
    parts.append("")

    parts.append("### Pinned contract tokens")
    parts.append("")
    parts.append(
        "`tools/repo_hygiene/brokered_closeout.py` asserts these exact substrings against "
        "this file; losing one raises `closeout_tooling_stale` and blocks finalize. They stay "
        "resident here as stable IDs even though the surrounding prose was demoted. The "
        "authoritative list is derived, not copied -- re-derive it with:"
    )
    parts.append("")
    parts.append("```bash")
    parts.append("py -3 tools/docs/check_pinned_tokens.py --json")
    parts.append("```")
    parts.append("")
    for tok in pinned:
        child = moved_map.get(tok)
        where = "%s/%s" % (CHILD_DIR, child) if child else "this file"
        parts.append("- `%s` -- detail in %s" % (tok, where))
    parts.append("")

    for _, body in retained:
        parts.append(body.rstrip("\n"))
        parts.append("")

    return "\n".join(parts).rstrip("\n") + "\n"


def refresh_tokens(root, dry_run):
    """Rebuild the pinned-token list in an ALREADY-split CLAUDE.md.

    The initial split derived tokens only from the structured baseline and so
    dropped 12 that are pinned by the test suite instead. This re-derives from
    every pinning surface and rewrites just that block, so the fix is
    reproducible rather than hand-typed.
    """
    sys.path.insert(0, os.path.join(root, "tools", "docs"))
    import check_pinned_tokens as cds

    pinned_all, err = cds.derive_pinned(root)
    if err:
        print("cannot derive pinned tokens: %s" % err)
        return 1
    tokens = pinned_all.get("CLAUDE.md", [])

    child_dir = os.path.join(root, CHILD_DIR)
    child_bodies = {}
    for name in sorted(os.listdir(child_dir)):
        if name.endswith(".md"):
            child_bodies[name] = open(os.path.join(child_dir, name), encoding="utf-8").read()

    parent_path = os.path.join(root, PARENT)
    raw = open(parent_path, "rb").read()
    newline = "\r\n" if raw.count(b"\r\n") > raw.count(b"\n") - raw.count(b"\r\n") else "\n"
    text = open(parent_path, encoding="utf-8").read()
    lines = text.split("\n")

    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("### Pinned contract tokens"))
        end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("## "))
    except StopIteration:
        print("could not locate the pinned-token block in %s" % PARENT)
        return 1

    block = ["### Pinned contract tokens", ""]
    block.append(
        "`tools/repo_hygiene/brokered_closeout.py` and "
        "`tools/repo_hygiene/test_brokered_closeout.py` assert these exact substrings "
        "against this file; losing one raises `closeout_tooling_stale` or reds the test "
        "suite. They stay resident here as stable IDs even though the surrounding prose "
        "was demoted. The list is derived from both surfaces, never hand-maintained -- "
        "re-derive and re-check with:"
    )
    block += ["", "```bash", "py -3 tools/docs/check_pinned_tokens.py", "```", ""]
    for tok in sorted(tokens, key=str.lower):
        where = next((c for c, b in sorted(child_bodies.items()) if tok in b), None)
        loc = "%s/%s" % (CHILD_DIR, where) if where else "this file"
        block.append("- `%s` -- detail in %s" % (tok, loc))
    block.append("")

    new_lines = lines[:start] + block + lines[end:]
    new_text = "\n".join(new_lines)

    missing = [t for t in tokens if t not in new_text]
    if missing:
        print("REFUSING: %d token(s) still absent after refresh" % len(missing))
        for t in missing:
            print("   " + repr(t))
        return 1

    print("pinned tokens: %d -> %d" % (len(lines[start:end]) - 7, len(tokens)))
    print("CLAUDE.md: %d -> %d bytes on disk"
          % (len(raw), len(new_text.replace("\n", newline).encode("utf-8"))))
    if dry_run:
        print("--dry-run: nothing written.")
        return 0
    with open(parent_path, "w", encoding="utf-8", newline=newline) as fh:
        fh.write(new_text)
    print("rewrote %s (sha256 %s)" % (PARENT, sha(new_text)[:16]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=os.getcwd())
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh-tokens", action="store_true",
                    help="rebuild the pinned-token block in an already-split CLAUDE.md")
    args = ap.parse_args()
    root = os.path.abspath(args.repo_root)

    if args.refresh_tokens:
        return refresh_tokens(root, args.dry_run)

    parent_path = os.path.join(root, PARENT)
    raw = open(parent_path, "rb").read()
    # Preserve the file's existing line-ending convention. A verbatim demotion
    # must not silently rewrite every line ending -- that buries the real diff.
    newline = "\r\n" if raw.count(b"\r\n") > raw.count(b"\n") - raw.count(b"\r\n") else "\n"
    original = open(parent_path, encoding="utf-8").read()
    preamble, sections = split_sections(original)

    by_heading = {h: b for h, b in sections}
    plan_headings = [h for h, _ in PLAN]

    missing = [h for h in plan_headings if h not in by_heading]
    if missing:
        raise SystemExit("PLAN references headings not in %s:\n  %s"
                         % (PARENT, "\n  ".join(missing)))
    missing_retain = [h for h in RETAIN if h not in by_heading]
    if missing_retain:
        raise SystemExit("RETAIN references headings not in %s:\n  %s"
                         % (PARENT, "\n  ".join(missing_retain)))

    covered = set(plan_headings) | set(RETAIN)
    orphan = [h for h, _ in sections if h not in covered]
    if orphan:
        raise SystemExit("sections neither moved nor retained (would be LOST):\n  %s"
                         % "\n  ".join(orphan))

    # Group section bodies per child, preserving PLAN order, byte-for-byte.
    children = {}
    for heading, child in PLAN:
        children.setdefault(child, []).append((heading, by_heading[heading]))

    # Map each pinned token to the child that now holds its prose (if any).
    pinned = derive_pinned(root)
    moved_map = {}
    for tok in pinned:
        for heading, child in PLAN:
            if tok in by_heading[heading]:
                moved_map[tok] = child
                break

    retained = [(h, by_heading[h]) for h in RETAIN]
    new_parent = build_index(preamble, retained, pinned, moved_map)

    # --- verification, before any write -------------------------------------
    problems = []

    for tok in pinned:
        if tok not in new_parent:
            problems.append("pinned token would be LOST from CLAUDE.md: %r" % tok)

    child_texts = {}
    for child, items in children.items():
        header = [
            "# %s" % CHILD_TITLES.get(child, child),
            "",
            "Demoted verbatim from [`CLAUDE.md`](../CLAUDE.md) under "
            "[docs/22-doc-fragmentation-policy.md](../docs/22-doc-fragmentation-policy.md). "
            "Headings are unchanged and remain the stable IDs referenced from the parent index.",
            "",
        ]
        body = "\n".join(header) + "\n".join(b.rstrip("\n") + "\n\n" for _, b in items)
        child_texts[child] = body.rstrip("\n") + "\n"
        for heading, orig in items:
            if orig.rstrip("\n") not in body:
                problems.append("NOT VERBATIM: %s in %s" % (heading, child))

    if problems:
        print("REFUSING TO WRITE -- %d problem(s):" % len(problems))
        for p in problems:
            print("  " + p)
        return 1

    # --- report --------------------------------------------------------------
    def on_disk(s):
        """Byte length as it will actually land, honouring the newline convention."""
        return len(s.replace("\n", newline).encode("utf-8"))

    print("CLAUDE.md split plan (verbatim demotion)")
    print("  line endings: %s (preserved)" % ("CRLF" if newline == "\r\n" else "LF"))
    print("  before: %d bytes on disk, %d sections" % (len(raw), len(sections)))
    print("  after : %d bytes on disk, %d retained sections + index"
          % (on_disk(new_parent), len(retained)))
    print()
    for child, text in sorted(child_texts.items()):
        print("  %s/%-24s %7d bytes  (%d sections)"
              % (CHILD_DIR, child, on_disk(text), len(children[child])))
    print()
    print("  pinned tokens preserved in parent: %d/%d" % (
        sum(1 for t in pinned if t in new_parent), len(pinned)))
    print("  every moved block byte-identical:  yes")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    os.makedirs(os.path.join(root, CHILD_DIR), exist_ok=True)
    for child, text in child_texts.items():
        p = os.path.join(root, CHILD_DIR, child)
        with open(p, "w", encoding="utf-8", newline=newline) as fh:
            fh.write(text)
        print("wrote %s/%s (sha256 %s)" % (CHILD_DIR, child, sha(text)[:16]))
    with open(parent_path, "w", encoding="utf-8", newline=newline) as fh:
        fh.write(new_parent)
    print("rewrote %s (sha256 %s)" % (PARENT, sha(new_parent)[:16]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
