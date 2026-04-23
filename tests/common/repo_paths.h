#ifndef TESTS_COMMON_REPO_PATHS_H
#define TESTS_COMMON_REPO_PATHS_H

#include <QString>

QString find_repo_root();
QString repo_file_path(const QString & relative_path);

#endif // TESTS_COMMON_REPO_PATHS_H
