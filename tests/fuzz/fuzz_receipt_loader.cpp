#include "../../src/batch/ReceiptLoader.h"
#include "../../platform/qt/ReceiptSettings.h"

#include <QFile>
#include <QTemporaryDir>

extern "C" int LLVMFuzzerTestOneInput(const unsigned char * data, unsigned long long size)
{
    QTemporaryDir temporary_dir;
    const QString path = temporary_dir.filePath(QStringLiteral("input.marxml"));
    QFile file(path);
    if (file.open(QIODevice::WriteOnly)) {
        file.write(reinterpret_cast<const char *>(data), static_cast<qint64>(size));
        file.close();

        ReceiptSettings receipt;
        QString error_message;
        ReceiptLoader::loadFromFile(path, &receipt, &error_message);
    }
    return 0;
}
