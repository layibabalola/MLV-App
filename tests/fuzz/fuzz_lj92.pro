include(fuzz_target_defaults.pri)

TARGET = fuzz_lj92

SOURCES += \
    $$REPO_ROOT/src/mlv/liblj92/lj92.c \
    $$REPO_ROOT/tests/fuzz/fuzz_lj92.cpp

win32 {
    QMAKE_CFLAGS += -std=c99
}

linux-g++* {
    QMAKE_CFLAGS += -std=c99
}

macx {
    QMAKE_CFLAGS += -std=c99
}
