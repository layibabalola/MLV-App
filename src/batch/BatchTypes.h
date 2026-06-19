#ifndef BATCHTYPES_H
#define BATCHTYPES_H

#include <QString>

/* Shared type header for batch mode.
 * Include this instead of MainWindow.h to avoid circular dependencies.
 * Keep this header lightweight — no Qt widget includes. */

enum class BatchExportFormat : int
{
    Cdng = 0,
    RenderedVideo = 1,
    Unknown = -1
};

inline BatchExportFormat batchExportFormatFromString(const QString & value)
{
    const QString normalized = value.trimmed().toLower();
    if( normalized.isEmpty()
     || normalized == QStringLiteral("cdng")
     || normalized == QStringLiteral("dng")
     || normalized == QStringLiteral("cinemadng")
     || normalized == QStringLiteral("cinema-dng")
     || normalized == QStringLiteral("cinema_dng") )
    {
        return BatchExportFormat::Cdng;
    }
    if( normalized == QStringLiteral("rendered")
     || normalized == QStringLiteral("rendered-video")
     || normalized == QStringLiteral("rendered_video")
     || normalized == QStringLiteral("video")
     || normalized == QStringLiteral("mov")
     || normalized == QStringLiteral("mp4")
     || normalized == QStringLiteral("h264")
     || normalized == QStringLiteral("h265")
     || normalized == QStringLiteral("hevc")
     || normalized == QStringLiteral("prores") )
    {
        return BatchExportFormat::RenderedVideo;
    }
    return BatchExportFormat::Unknown;
}

inline const char * batchExportFormatName(BatchExportFormat format)
{
    switch( format )
    {
        case BatchExportFormat::Cdng:
            return "cdng";
        case BatchExportFormat::RenderedVideo:
            return "rendered-video";
        case BatchExportFormat::Unknown:
            break;
    }
    return "unknown";
}

/* Processing profile for batch export.
 * v1 (Phases 1-5): Uses MLV-App defaults on file open. Only receiptPath
 * and exportFormat are stored here.
 * v1.1 (Phase 6): receiptPath will point to a .marxml file whose parsed
 * settings are applied to the mlvObject_t before export. */
struct ProcessingProfile
{
    QString receiptPath;                 /* Path to .marxml receipt (Phase 6) */
    QString exportFormat = QStringLiteral("cdng"); /* Export format identifier */
};

/* Result of exporting a single MLV clip to CDNG. */
struct ProcessResult
{
    bool success = false;
    QString errorMessage;
    int framesExported = 0;
    int framesSkipped = 0;
    double elapsedSeconds = 0.0;
};

#endif // BATCHTYPES_H
