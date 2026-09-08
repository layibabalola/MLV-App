# FIELDS for product-card-TEMPLATE.md — composed by the dispatcher; both files are in the ratified manifest
CARD_ID: PROD-BATCHTYPES-SPLIT-1
PRIORITY: 12
CLIP_OR_NONE: none
ALLOWED_PATHS: src/batch/BatchTypes.h, src/batch/BatchRenderedVideoPlan.h, src/batch/BatchRenderedVideoPlan.cpp, src/batch/BatchContext.h, src/batch/BatchRunner.h (it exposes the moved BatchRenderedVideo* types and includes only BatchTypes.h — S96), src/batch/BatchRunner.cpp, platform/qt/MLVApp.pro, tests/console/console_tests.pro, tests/pipeline/pipeline_tests.pro, tests/console/test_receipt_applier.cpp, tools/repo_hygiene/test_batch_header_hygiene.py, tests/console/test_rendered_video_runner.cpp
NOTE: tests/console/test_rendered_video_runner.cpp uses the moved types 17 times and reaches them through BatchRunner.h's re-export (S96); it is expected to stay unchanged and is in ALLOWED_PATHS so the lane can add one include if the re-export is dropped, rather than halting on a path it may not touch (O162).

DELIVERABLE:
`src/batch/BatchTypes.h` is 6,698 lines / ~355 KB, 41 top-level `struct` declarations and 144 `inline` functions (both counts DERIVED at DIAGNOSIS_BASE by the VERIFY_FIRST commands below — S97), includes `QDir`,
`QFileInfo`, `QRegularExpression`, and ends in dozens of `*PlanSummary` serialisers — while its own header comment says
"keep this header lightweight". Every batch translation unit pays for it. Split with NO behaviour change:
(a) `BatchTypes.h` keeps enums, `ProcessResult`, render settings, and nothing that needs `QRegularExpression`/`QDir`;
(b) `BatchRenderedVideoPlan.{h,cpp}` takes the plan structs and the summary serialisers; (c) callers include what
they use. Golden strings emitted by the summaries must be byte-identical (tests compare them).

ACCEPTANCE:
`tests/fixtures/golden/` pipeline hashes unchanged; `tests/console/test_receipt_applier.cpp` unchanged and green;
new `tools/repo_hygiene/test_batch_header_hygiene.py` asserts `BatchTypes.h` contains no `#include <QRegularExpression>`
and no `#include <QDir>` and is under 1,500 lines (deterministic; no compile-time measurement is asserted).

VERIFY_FIRST:
git -C . show {{BASE_SHA}}:src/batch/BatchTypes.h | wc -l                    # 6698
git -C . show {{BASE_SHA}}:src/batch/BatchTypes.h | grep -c '^inline '        # 144 (S97)
git -C . show {{BASE_SHA}}:src/batch/BatchTypes.h | grep -cE '^struct [A-Za-z_]'   # 41
git -C . show {{BASE_SHA}}:src/batch/BatchRunner.h | grep -n 'BatchTypes.h\|BatchRenderedVideo'   # includes only BatchTypes.h; uses the moved types (S96)
git -C . grep -n -E "#include <Q(Dir|FileInfo|RegularExpression)>" {{BASE_SHA}} -- src/batch/BatchTypes.h
