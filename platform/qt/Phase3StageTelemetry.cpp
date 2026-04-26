#include "Phase3StageTelemetry.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QTextStream>

#include <chrono>

namespace Phase3StageTelemetry {

const char * csvHeader() noexcept
{
    return "frame_idx,request_serial,slot,stage,event,ns,phase3_mode,clip_generation";
}

uint64_t monotonicNs() noexcept
{
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(now).count());
}

static QString csvEscape(const char * value)
{
    QString text = QString::fromUtf8(value ? value : "");
    const bool needsQuotes =
        text.contains(QLatin1Char(','))
        || text.contains(QLatin1Char('"'))
        || text.contains(QLatin1Char('\n'))
        || text.contains(QLatin1Char('\r'));
    if (!needsQuotes) return text;
    text.replace(QLatin1Char('"'), QStringLiteral("\"\""));
    return QStringLiteral("\"") + text + QStringLiteral("\"");
}

bool writeCsv(const QString & path,
              const std::vector<Event> & events,
              QString * errorMessage)
{
    const QFileInfo info(path);
    const QDir parent = info.dir();
    if (!parent.exists() && !QDir().mkpath(parent.absolutePath()))
    {
        if (errorMessage)
        {
            *errorMessage = QStringLiteral("Could not create telemetry directory: %1")
                .arg(parent.absolutePath());
        }
        return false;
    }

    QFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Text | QIODevice::Truncate))
    {
        if (errorMessage)
        {
            *errorMessage = QStringLiteral("Could not open telemetry CSV: %1")
                .arg(path);
        }
        return false;
    }

    QTextStream out(&file);
    out << QString::fromLatin1(csvHeader()) << '\n';
    for (const Event & e : events)
    {
        out << e.frameIndex << ','
            << e.requestSerial << ','
            << e.slot << ','
            << csvEscape(e.stage) << ','
            << csvEscape(e.event) << ','
            << e.ns << ','
            << QString::fromLatin1(phase3ModeName(e.mode)) << ','
            << e.clipGeneration << '\n';
    }
    return true;
}

} // namespace Phase3StageTelemetry
