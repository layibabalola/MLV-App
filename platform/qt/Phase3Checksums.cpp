#include "Phase3Checksums.h"

#include "../../src/debug/FrameChecksum.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QTextStream>

namespace Phase3Checksums {

uint64_t xxhash64(const void * data, std::size_t size, uint64_t seed) noexcept
{
    return frame_checksum_compute_seed(data, size, seed);
}

const char * csvHeader() noexcept
{
    return "frame_idx,request_serial,phase3_mode,scale_factor,width,height,checksum_hex";
}

static QString hex64(uint64_t value)
{
    return QStringLiteral("%1").arg(value, 16, 16, QLatin1Char('0'));
}

bool writeCsv(const QString & path,
              const std::vector<Record> & records,
              QString * errorMessage)
{
    const QFileInfo info(path);
    const QDir parent = info.dir();
    if (!parent.exists() && !QDir().mkpath(parent.absolutePath()))
    {
        if (errorMessage)
        {
            *errorMessage = QStringLiteral("Could not create checksum directory: %1")
                .arg(parent.absolutePath());
        }
        return false;
    }

    QFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Text | QIODevice::Truncate))
    {
        if (errorMessage)
        {
            *errorMessage = QStringLiteral("Could not open checksum CSV: %1")
                .arg(path);
        }
        return false;
    }

    QTextStream out(&file);
    out << QString::fromLatin1(csvHeader()) << '\n';
    for (const Record & r : records)
    {
        out << r.frameIndex << ','
            << r.requestSerial << ','
            << QString::fromLatin1(phase3ModeName(r.mode)) << ','
            << r.scaleFactor << ','
            << r.width << ','
            << r.height << ','
            << hex64(r.hash) << '\n';
    }
    return true;
}

} // namespace Phase3Checksums
