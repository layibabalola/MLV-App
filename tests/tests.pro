TEMPLATE = subdirs
CONFIG += ordered

console.file = $$PWD/console/console_tests.pro
alloc.file = $$PWD/alloc/alloc_tests.pro
pipeline.file = $$PWD/pipeline/pipeline_tests.pro
perf.file = $$PWD/perf/perf_tests.pro
gui.file = $$PWD/gui/gui_tests.pro

SUBDIRS += console alloc pipeline perf
SUBDIRS += gui
