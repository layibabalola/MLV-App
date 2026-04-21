include(../common/test_defaults.pri)

QT += core gui widgets testlib openglwidgets

TEMPLATE = app
CONFIG += testcase
TARGET = gui_tests

SOURCES += \
    $$REPO_ROOT/platform/qt/ColorToolButton.cpp \
    $$REPO_ROOT/platform/qt/GpuDisplayViewport.cpp \
    $$REPO_ROOT/platform/qt/Histogram.cpp \
    $$REPO_ROOT/platform/qt/VectorScope.cpp \
    $$REPO_ROOT/platform/qt/WaveFormMonitor.cpp \
    $$REPO_ROOT/tests/common/hash_helpers.cpp \
    $$REPO_ROOT/tests/common/image_regression.cpp \
    $$REPO_ROOT/tests/common/repo_paths.cpp \
    $$REPO_ROOT/tests/gui/test_gui_smoke.cpp

HEADERS += \
    $$REPO_ROOT/platform/qt/ColorToolButton.h \
    $$REPO_ROOT/platform/qt/GpuDisplayViewport.h \
    $$REPO_ROOT/platform/qt/Histogram.h \
    $$REPO_ROOT/platform/qt/VectorScope.h \
    $$REPO_ROOT/platform/qt/WaveFormMonitor.h \
    $$REPO_ROOT/tests/common/hash_helpers.h \
    $$REPO_ROOT/tests/common/image_regression.h \
    $$REPO_ROOT/tests/common/repo_paths.h \
    $$REPO_ROOT/tests/common/test_runtime.h
