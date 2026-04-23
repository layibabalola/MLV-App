include(../common/test_defaults.pri)

QT += core

TEMPLATE = app
TARGET = console_tests

SOURCES += \
    $$REPO_ROOT/tests/common/test_artifacts.cpp \
    $$REPO_ROOT/tests/common/frame_compare.cpp \
    $$REPO_ROOT/tests/common/hash_helpers.cpp \
    $$REPO_ROOT/tests/common/repo_paths.cpp \
    $$REPO_ROOT/platform/qt/ReceiptSettings.cpp \
    $$REPO_ROOT/src/mlv/frame_caching.c \
    $$REPO_ROOT/src/batch/BatchLogger.cpp \
    $$REPO_ROOT/src/batch/ReceiptLoader.cpp \
    $$REPO_ROOT/src/batch/ReceiptApplier.cpp \
    $$REPO_ROOT/tests/console/stubs/pipeline_stubs.cpp \
    $$REPO_ROOT/tests/console/test_main.cpp \
    $$REPO_ROOT/tests/console/test_clip_golden.cpp \
    $$REPO_ROOT/tests/console/test_cache_behavior.cpp \
    $$REPO_ROOT/tests/console/test_avx_golden.cpp \
    $$REPO_ROOT/tests/console/test_worker_thread_count.cpp \
    $$REPO_ROOT/tests/console/test_dual_iso_playback_policy.cpp \
    $$REPO_ROOT/tests/console/test_frame_compare.cpp \
    $$REPO_ROOT/tests/console/test_receipt_loader.cpp \
    $$REPO_ROOT/tests/console/test_receipt_applier.cpp

HEADERS += \
    $$REPO_ROOT/tests/common/minitest.h \
    $$REPO_ROOT/tests/common/test_artifacts.h \
    $$REPO_ROOT/tests/common/test_runtime.h \
    $$REPO_ROOT/tests/common/frame_compare.h \
    $$REPO_ROOT/tests/common/hash_helpers.h \
    $$REPO_ROOT/tests/common/repo_paths.h
