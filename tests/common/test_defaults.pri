REPO_ROOT = $$clean_path($$PWD/../..)

CONFIG += c++17 console warn_on
CONFIG -= app_bundle

INCLUDEPATH += $$REPO_ROOT \
               $$REPO_ROOT/src \
               $$REPO_ROOT/src/librtprocess/src/include \
               $$REPO_ROOT/platform/qt \
               $$REPO_ROOT/tests/common

DEPENDPATH += $$INCLUDEPATH
