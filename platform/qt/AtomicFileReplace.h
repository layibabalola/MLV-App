#ifndef ATOMIC_FILE_REPLACE_H
#define ATOMIC_FILE_REPLACE_H

#include <QByteArray>
#include <QSaveFile>
#include <QString>

namespace atomic_file_replace_detail {

// Keep the device boundary injectable so disk-write failures can be tested
// against the real temporary-file/commit implementation.
inline bool writeAndCommit(QSaveFile &file, const QByteArray &bytes)
{
    file.setDirectWriteFallback(false);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Unbuffered))
        return false;
    if (file.write(bytes) != bytes.size()) {
        file.cancelWriting();
        return false;
    }
    return file.commit();
}

} // namespace atomic_file_replace_detail

inline bool writeAtomically(const QString &finalPath, const QByteArray &bytes)
{
    // QSaveFile uses a same-directory temporary file and atomic replacement.
    // Never remove the previous map before committing its replacement.
    QSaveFile file(finalPath);
    return atomic_file_replace_detail::writeAndCommit(file, bytes);
}

#endif
