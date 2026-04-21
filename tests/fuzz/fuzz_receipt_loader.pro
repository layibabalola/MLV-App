include(fuzz_target_defaults.pri)

TARGET = fuzz_receipt_loader

SOURCES += \
    $$REPO_ROOT/platform/qt/ReceiptSettings.cpp \
    $$REPO_ROOT/src/batch/ReceiptLoader.cpp \
    $$REPO_ROOT/src/batch/BatchLogger.cpp \
    $$REPO_ROOT/tests/fuzz/fuzz_receipt_loader.cpp
