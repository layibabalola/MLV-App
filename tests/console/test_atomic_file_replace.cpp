#include "../common/minitest.h"
#include "../../platform/qt/AtomicFileReplace.h"

#include <QDir>
#include <QFile>
#include <QTemporaryDir>

namespace {
QByteArray readFile(const QString &path)
{
    QFile file(path);
    ASSERT_TRUE(file.open(QIODevice::ReadOnly));
    return file.readAll();
}

class FailingSaveFile : public QSaveFile
{
public:
    explicit FailingSaveFile(const QString &path) : QSaveFile(path) {}
protected:
    qint64 writeData(const char *data, qint64 length) override
    {
        // Real partial bytes reach the temporary file, then the write fails.
        QSaveFile::writeData(data, qMin(qint64(3), length));
        return -1;
    }
};
}

TEST(AtomicFileReplace, CreatesAndReplacesWithoutTemporaryResidue)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    const QString path = dir.filePath("80000331_1808x1190.fpm");
    ASSERT_TRUE(writeAtomically(path, "old map"));
    const QByteArray replacement("new\0map\n", 8);
    ASSERT_TRUE(writeAtomically(path, replacement));
    ASSERT_TRUE(readFile(path) == replacement);
    ASSERT_TRUE(QDir(dir.path()).entryList(QDir::AllEntries | QDir::Hidden | QDir::NoDotAndDotDot)
                == QStringList{"80000331_1808x1190.fpm"});
    ASSERT_TRUE(writeAtomically(path, QByteArray()));
    ASSERT_TRUE(readFile(path).isEmpty());
}

TEST(AtomicFileReplace, PartialWriteFailurePreservesExistingMap)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    const QString path = dir.filePath("a_1x2.fpm");
    ASSERT_TRUE(writeAtomically(path, "known good map"));
    {
        FailingSaveFile file(path);
        ASSERT_FALSE(atomic_file_replace_detail::writeAndCommit(file, "replacement map"));
    }
    ASSERT_TRUE(readFile(path) == QByteArray("known good map"));
    ASSERT_TRUE(QDir(dir.path()).entryList(QDir::AllEntries | QDir::Hidden | QDir::NoDotAndDotDot)
                == QStringList{"a_1x2.fpm"});
}

TEST(AtomicFileReplace, InvalidDestinationFailsWithoutCreatingPartialFile)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    const QString missingParent = dir.filePath("absent/a_1x2.fpm");
    ASSERT_FALSE(writeAtomically(missingParent, "map"));
    ASSERT_FALSE(QFile::exists(missingParent));
    ASSERT_FALSE(writeAtomically(dir.path(), "map"));
    ASSERT_TRUE(QDir(dir.path()).entryList(QDir::AllEntries | QDir::Hidden | QDir::NoDotAndDotDot).isEmpty());
}
