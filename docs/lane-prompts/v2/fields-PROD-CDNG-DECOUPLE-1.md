# FIELDS for product-card-TEMPLATE.md — composed by the dispatcher; both files are in the ratified manifest
CARD_ID: PROD-CDNG-DECOUPLE-1
PRIORITY: 10
CLIP_OR_NONE: none
ALLOWED_PATHS: src/batch/CdngSequenceExport.h, src/batch/CdngSequenceExport.cpp, src/batch/BatchRunner.h, src/batch/BatchRunner.cpp, platform/qt/MainWindow.cpp, platform/qt/MainWindow.h, platform/qt/MLVApp.pro, tests/pipeline/**, tests/pipeline/pipeline_tests.pro, tools/repo_hygiene/test_batch_no_mainwindow_include.py
DEPENDS_ON: Phase 0.4a (a required job builds platform/qt/MLVApp.pro) — until then a broken BatchRunner.cpp is caught by no required check

DELIVERABLE:
`src/batch/BatchRunner.cpp` includes `platform/qt/MainWindow.h` for the static `MainWindow::exportCdngSequence(...)`
helper, and `MainWindow` derives from `QMainWindow`, so the headless batch path cannot be compiled without the GUI god
object and cannot join the `QT += core` console target. Move `exportCdngSequence` and the stretch/cut/audio static
inlines that `BatchRunner.h` already duplicates into `src/batch/CdngSequenceExport.{h,cpp}` with no Qt-widgets include;
`MainWindow` becomes a caller; `BatchRunner.cpp` no longer includes `MainWindow.h`. Byte-identical output is the rule.

ACCEPTANCE:
`tests/pipeline/pipeline_tests.pro` compiles `CdngSequenceExport.cpp` and `BatchRunner.cpp` (it is `QT += core gui`
capable where console is not; if it still cannot, the card STOPs and says which include blocks it); the existing
`--check-golden` pipeline hashes for CDNG export of the fixture clip are unchanged; `tools/repo_hygiene/test_batch_no_mainwindow_include.py` (created by this card) asserts
`src/batch/` contains no `#include` of `MainWindow.h`.

VERIFY_FIRST:
git -C . grep -n "MainWindow.h" {{BASE_SHA}} -- src/batch/BatchRunner.cpp
git -C . grep -n "class MainWindow" {{BASE_SHA}} -- platform/qt/MainWindow.h
git -C . show {{BASE_SHA}}:tests/pipeline/pipeline_tests.pro | grep -n -E "^QT|BatchRunner|MainWindow"
