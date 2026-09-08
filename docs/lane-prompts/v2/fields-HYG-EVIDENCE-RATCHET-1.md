# FIELDS for product-card-TEMPLATE.md — composed by the dispatcher; both files are in the ratified manifest
CARD_ID: HYG-EVIDENCE-RATCHET-1
PRIORITY: 11
CLIP_OR_NONE: none
ALLOWED_PATHS: tools/repo_hygiene/test_closeout_evidence_ratchet.py, CLOSEOUT-CAPABILITY-LEDGER.json, CLOSEOUT-CAPABILITY-LEDGER.schema.json

DELIVERABLE:
615 `.closeout-evidence/<workBlockId>/` directories are tracked on `fork/master`; the newest carries
`closeoutCleanTruth.status: "pending"`. Untracking them touches the closeout machinery (NA-2 archive-only boundary and
`closeout.config.json`) and is Phase-3-gated. This card only stops the growth and tells the truth about the ledger:
(1) a hygiene test that reads the tracked directory count and FAILS if it exceeds the ratchet value 615 (the value is
a constant in the test, with the derivation command in a comment; lowering it later is allowed, raising it is not);
(2) `CLOSEOUT-CAPABILITY-LEDGER.json` gains a top-level `"status": "STALE-2026-05-08"` beside the existing
`lastVerifiedAt`, and no row is re-scored; (3) `CLOSEOUT-CAPABILITY-LEDGER.schema.json` is updated to allow that one new
top-level property (its root has `additionalProperties: false` at {{BASE_SHA}}, so this edit is REQUIRED, not conditional — S70). Nothing under `.closeout-evidence/` is touched.

ACCEPTANCE:
`python -m unittest discover -s tools/repo_hygiene -p "test_*.py" -t . -v` green; the ratchet test fails when its
constant is set to 614 (prove once); the ledger validates against the updated `CLOSEOUT-CAPABILITY-LEDGER.schema.json`, and a test asserts the schema
rejects any OTHER unknown top-level key (the change is one property, not `additionalProperties: true`).

VERIFY_FIRST:
git -C . ls-tree -r --name-only {{BASE_SHA}} -- .closeout-evidence | cut -d/ -f2 | sort -u | wc -l     # 615 at DIAGNOSIS_BASE — ls-tree reads the TREE; ls-files ignores a tree-ish and reads the moving index (S91)
git -C . show {{BASE_SHA}}:CLOSEOUT-CAPABILITY-LEDGER.json | grep -n -E '"status"|lastVerifiedAt'
