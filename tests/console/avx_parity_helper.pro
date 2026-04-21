include(../common/test_defaults.pri)

QT += core

TEMPLATE = app
TARGET = avx_parity_helper

DEFINES += STDOUT_SILENT

include(../common/pipeline_runtime.pri)

SOURCES += \
    $$REPO_ROOT/tests/common/hash_helpers.cpp \
    $$REPO_ROOT/tests/common/repo_paths.cpp \
    $$REPO_ROOT/tests/pipeline/mlv_pipeline_fixture.cpp \
    $$REPO_ROOT/tests/console/avx_parity_helper.cpp

HEADERS += \
    $$REPO_ROOT/tests/common/hash_helpers.h \
    $$REPO_ROOT/tests/common/repo_paths.h \
    $$REPO_ROOT/tests/common/test_runtime.h \
    $$REPO_ROOT/tests/pipeline/mlv_pipeline_fixture.h
