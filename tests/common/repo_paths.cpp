#include "repo_paths.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>

QString find_repo_root()
{
    QDir probe(QCoreApplication::applicationDirPath());
    for (int depth = 0; depth < 8; ++depth) {
        if (QFileInfo::exists(probe.filePath(QStringLiteral("README.md"))) &&
            QFileInfo::exists(probe.filePath(QStringLiteral("receipts"))) &&
            QFileInfo::exists(probe.filePath(QStringLiteral("src"))) &&
            QFileInfo::exists(probe.filePath(QStringLiteral("tests")))) {
            return probe.absolutePath();
        }
        if (!probe.cdUp()) {
            break;
        }
    }
    return QString();
}

QString repo_file_path(const QString & relative_path)
{
    const QString root = find_repo_root();
    if (root.isEmpty()) {
        return QString();
    }
    return QDir(root).filePath(relative_path);
}
