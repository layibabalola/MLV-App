#include "hash_helpers.h"

#include <QByteArray>
#include <QCryptographicHash>
#include <QFile>
#include <QString>

static std::string to_hex_string(const QByteArray & value)
{
    return value.toHex().toStdString();
}

std::string sha256_bytes(const void * data, std::size_t size)
{
    const QByteArray bytes(static_cast<const char *>(data), static_cast<int>(size));
    return to_hex_string(QCryptographicHash::hash(bytes, QCryptographicHash::Sha256));
}

std::string sha256_string(const std::string & value)
{
    return sha256_bytes(value.data(), value.size());
}

std::string sha256_qstring(const QString & value)
{
    const QByteArray utf8 = value.toUtf8();
    return sha256_bytes(utf8.constData(), static_cast<std::size_t>(utf8.size()));
}

std::string sha256_file(const QString & path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return std::string();
    }

    QCryptographicHash hash(QCryptographicHash::Sha256);
    while (!file.atEnd()) {
        hash.addData(file.read(1 << 16));
    }
    return to_hex_string(hash.result());
}
