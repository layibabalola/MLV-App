include(fuzz_target_defaults.pri)

TARGET = fuzz_mlv_open

DEFINES += STDOUT_SILENT

include(../common/pipeline_runtime.pri)

SOURCES += \
    $$REPO_ROOT/tests/fuzz/fuzz_mlv_open.cpp
