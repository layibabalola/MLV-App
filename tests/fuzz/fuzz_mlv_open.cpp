#include "../../src/mlv_include.h"

#include <QByteArray>
#include <QFile>
#include <QTemporaryDir>

#include <limits>

extern "C" int LLVMFuzzerTestOneInput(const unsigned char * data, unsigned long long size)
{
    if (!data || size == 0 || size > static_cast<unsigned long long>(std::numeric_limits<qint64>::max())) {
        return 0;
    }

    QTemporaryDir temporary_dir;
    if (!temporary_dir.isValid()) {
        return 0;
    }

    const QString path = temporary_dir.filePath(QStringLiteral("input.MLV"));
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly)) {
        return 0;
    }

    file.write(reinterpret_cast<const char *>(data), static_cast<qint64>(size));
    file.close();

    mlvObject_t * video = initMlvObject();
    if (!video) {
        return 0;
    }

    QByteArray path_bytes = path.toLocal8Bit();
    char open_error[256] = { 0 };
    openMlvClip(video, path_bytes.data(), MLV_OPEN_PREVIEW, open_error);
    freeMlvObject(video);
    return 0;
}
