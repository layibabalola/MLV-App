include(../common/test_defaults.pri)

QT += core

TEMPLATE = app
TARGET = alloc_tests

SOURCES += \
    $$REPO_ROOT/tests/common/tracking_alloc.cpp \
    $$REPO_ROOT/tests/alloc/test_alloc_smoke.cpp

HEADERS += \
    $$REPO_ROOT/tests/common/minitest.h \
    $$REPO_ROOT/tests/common/test_runtime.h \
    $$REPO_ROOT/tests/common/tracking_alloc.h
