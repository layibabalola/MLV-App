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

enum class BatchRenderedVideoCodec : int
{
    Unspecified = 0,
    H264 = 1,
    H265 = 2,
    ProRes = 3
};

enum class BatchRenderedVideoContainer : int
{
    Unspecified = 0,
    Mov = 1,
    Mp4 = 2,
    Mkv = 3
};

struct BatchExportFormatRequest
{
    BatchExportFormat format = BatchExportFormat::Cdng;
    BatchRenderedVideoCodec renderedCodec = BatchRenderedVideoCodec::Unspecified;
    BatchRenderedVideoContainer renderedContainer = BatchRenderedVideoContainer::Unspecified;
};

struct BatchRenderedVideoTarget
{
    BatchRenderedVideoCodec codec = BatchRenderedVideoCodec::Unspecified;
    BatchRenderedVideoContainer container = BatchRenderedVideoContainer::Unspecified;
    QString extension;
    bool complete = false;
};

enum class BatchRenderedVideoEncoderProfile : int
{
    Unspecified = 0,
    H264 = 1,
    H265_8 = 2,
    ProRes422HQ = 3
};

enum class BatchRenderedVideoEncoderOption : int
{
    Unspecified = 0,
    H264HighMov = 1,
    H264HighMp4 = 2,
    H264HighMkv = 3,
    H265HighMov = 4,
    H265HighMp4 = 5,
    H265HighMkv = 6,
    ProResFfmpegKostya = 7
};

struct BatchRenderedVideoEncoderPreset
{
    BatchRenderedVideoEncoderProfile profile =
        BatchRenderedVideoEncoderProfile::Unspecified;
    BatchRenderedVideoEncoderOption option =
        BatchRenderedVideoEncoderOption::Unspecified;
    QString extension;
    bool ready = false;
};

inline BatchRenderedVideoCodec batchRenderedVideoCodecFromString(const QString & value, bool * ok = nullptr)
{
    const QString normalized = value.trimmed().toLower();
    if( normalized.isEmpty()
     || normalized == QStringLiteral("unspecified")
     || normalized == QStringLiteral("default")
     || normalized == QStringLiteral("auto") )
    {
        if( ok ) *ok = true;
        return BatchRenderedVideoCodec::Unspecified;
    }
    if( normalized == QStringLiteral("h264")
     || normalized == QStringLiteral("h.264")
     || normalized == QStringLiteral("avc") )
    {
        if( ok ) *ok = true;
        return BatchRenderedVideoCodec::H264;
    }
    if( normalized == QStringLiteral("h265")
     || normalized == QStringLiteral("h.265")
     || normalized == QStringLiteral("hevc") )
    {
        if( ok ) *ok = true;
        return BatchRenderedVideoCodec::H265;
    }
    if( normalized == QStringLiteral("prores")
     || normalized == QStringLiteral("pro-res") )
    {
        if( ok ) *ok = true;
        return BatchRenderedVideoCodec::ProRes;
    }

    if( ok ) *ok = false;
    return BatchRenderedVideoCodec::Unspecified;
}

inline BatchRenderedVideoContainer batchRenderedVideoContainerFromString(const QString & value, bool * ok = nullptr)
{
    const QString normalized = value.trimmed().toLower();
    if( normalized.isEmpty()
     || normalized == QStringLiteral("unspecified")
     || normalized == QStringLiteral("default")
     || normalized == QStringLiteral("auto") )
    {
        if( ok ) *ok = true;
        return BatchRenderedVideoContainer::Unspecified;
    }
    if( normalized == QStringLiteral("mov")
     || normalized == QStringLiteral("quicktime") )
    {
        if( ok ) *ok = true;
        return BatchRenderedVideoContainer::Mov;
    }
    if( normalized == QStringLiteral("mp4") )
    {
        if( ok ) *ok = true;
        return BatchRenderedVideoContainer::Mp4;
    }
    if( normalized == QStringLiteral("mkv")
     || normalized == QStringLiteral("matroska") )
    {
        if( ok ) *ok = true;
        return BatchRenderedVideoContainer::Mkv;
    }

    if( ok ) *ok = false;
    return BatchRenderedVideoContainer::Unspecified;
}

inline BatchExportFormatRequest batchExportFormatRequestFromString(const QString & value)
{
    BatchExportFormatRequest request;
    const QString normalized = value.trimmed().toLower();
    if( normalized.isEmpty()
     || normalized == QStringLiteral("cdng")
     || normalized == QStringLiteral("dng")
     || normalized == QStringLiteral("cinemadng")
     || normalized == QStringLiteral("cinema-dng")
     || normalized == QStringLiteral("cinema_dng") )
    {
        request.format = BatchExportFormat::Cdng;
        return request;
    }
    if( normalized == QStringLiteral("rendered")
     || normalized == QStringLiteral("rendered-video")
     || normalized == QStringLiteral("rendered_video")
     || normalized == QStringLiteral("video") )
    {
        request.format = BatchExportFormat::RenderedVideo;
        return request;
    }
    if( normalized == QStringLiteral("mov") )
    {
        request.format = BatchExportFormat::RenderedVideo;
        request.renderedContainer = BatchRenderedVideoContainer::Mov;
        return request;
    }
    if( normalized == QStringLiteral("mp4") )
    {
        request.format = BatchExportFormat::RenderedVideo;
        request.renderedContainer = BatchRenderedVideoContainer::Mp4;
        return request;
    }
    if( normalized == QStringLiteral("mkv") )
    {
        request.format = BatchExportFormat::RenderedVideo;
        request.renderedContainer = BatchRenderedVideoContainer::Mkv;
        return request;
    }
    if( normalized == QStringLiteral("h264")
     || normalized == QStringLiteral("h264-mp4") )
    {
        request.format = BatchExportFormat::RenderedVideo;
        request.renderedCodec = BatchRenderedVideoCodec::H264;
        if( normalized.endsWith(QStringLiteral("-mp4")) )
            request.renderedContainer = BatchRenderedVideoContainer::Mp4;
        return request;
    }
    if( normalized == QStringLiteral("h265")
     || normalized == QStringLiteral("hevc")
     || normalized == QStringLiteral("h265-mp4")
     || normalized == QStringLiteral("hevc-mp4") )
    {
        request.format = BatchExportFormat::RenderedVideo;
        request.renderedCodec = BatchRenderedVideoCodec::H265;
        if( normalized.endsWith(QStringLiteral("-mp4")) )
            request.renderedContainer = BatchRenderedVideoContainer::Mp4;
        return request;
    }
    if( normalized == QStringLiteral("prores")
     || normalized == QStringLiteral("prores-mov") )
    {
        request.format = BatchExportFormat::RenderedVideo;
        request.renderedCodec = BatchRenderedVideoCodec::ProRes;
        request.renderedContainer = BatchRenderedVideoContainer::Mov;
        return request;
    }

    request.format = BatchExportFormat::Unknown;
    return request;
}

inline BatchExportFormat batchExportFormatFromString(const QString & value)
{
    return batchExportFormatRequestFromString(value).format;
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

inline const char * batchRenderedVideoCodecName(BatchRenderedVideoCodec codec)
{
    switch( codec )
    {
        case BatchRenderedVideoCodec::Unspecified:
            return "unspecified";
        case BatchRenderedVideoCodec::H264:
            return "h264";
        case BatchRenderedVideoCodec::H265:
            return "h265";
        case BatchRenderedVideoCodec::ProRes:
            return "prores";
    }
    return "unspecified";
}

inline const char * batchRenderedVideoContainerName(BatchRenderedVideoContainer container)
{
    switch( container )
    {
        case BatchRenderedVideoContainer::Unspecified:
            return "unspecified";
        case BatchRenderedVideoContainer::Mov:
            return "mov";
        case BatchRenderedVideoContainer::Mp4:
            return "mp4";
        case BatchRenderedVideoContainer::Mkv:
            return "mkv";
    }
    return "unspecified";
}

inline const char * batchRenderedVideoContainerExtension(BatchRenderedVideoContainer container)
{
    switch( container )
    {
        case BatchRenderedVideoContainer::Mov:
            return ".mov";
        case BatchRenderedVideoContainer::Mp4:
            return ".mp4";
        case BatchRenderedVideoContainer::Mkv:
            return ".mkv";
        case BatchRenderedVideoContainer::Unspecified:
            break;
    }
    return "";
}

inline const char * batchRenderedVideoEncoderProfileName(
    BatchRenderedVideoEncoderProfile profile)
{
    switch( profile )
    {
        case BatchRenderedVideoEncoderProfile::Unspecified:
            return "unspecified";
        case BatchRenderedVideoEncoderProfile::H264:
            return "h264";
        case BatchRenderedVideoEncoderProfile::H265_8:
            return "h265-8";
        case BatchRenderedVideoEncoderProfile::ProRes422HQ:
            return "prores422hq";
    }
    return "unspecified";
}

inline const char * batchRenderedVideoEncoderOptionName(
    BatchRenderedVideoEncoderOption option)
{
    switch( option )
    {
        case BatchRenderedVideoEncoderOption::Unspecified:
            return "unspecified";
        case BatchRenderedVideoEncoderOption::H264HighMov:
            return "ffmpeg-mov-high";
        case BatchRenderedVideoEncoderOption::H264HighMp4:
            return "ffmpeg-mp4-high";
        case BatchRenderedVideoEncoderOption::H264HighMkv:
            return "ffmpeg-mkv-high";
        case BatchRenderedVideoEncoderOption::H265HighMov:
            return "ffmpeg-mov-high";
        case BatchRenderedVideoEncoderOption::H265HighMp4:
            return "ffmpeg-mp4-high";
        case BatchRenderedVideoEncoderOption::H265HighMkv:
            return "ffmpeg-mkv-high";
        case BatchRenderedVideoEncoderOption::ProResFfmpegKostya:
            return "ffmpeg-kostya";
    }
    return "unspecified";
}

inline QString batchExportFormatRequestSummary(const BatchExportFormatRequest & request)
{
    return QStringLiteral("request=%1 codec=%2 container=%3")
        .arg(batchExportFormatName(request.format))
        .arg(batchRenderedVideoCodecName(request.renderedCodec))
        .arg(batchRenderedVideoContainerName(request.renderedContainer));
}

inline bool batchRenderedVideoRequestShapeValid(const BatchExportFormatRequest & request)
{
    if( request.format != BatchExportFormat::RenderedVideo )
        return true;

    if( request.renderedCodec == BatchRenderedVideoCodec::ProRes
     && request.renderedContainer != BatchRenderedVideoContainer::Unspecified
     && request.renderedContainer != BatchRenderedVideoContainer::Mov )
    {
        return false;
    }

    return true;
}

inline QString batchRenderedVideoRequestShapeError(const BatchExportFormatRequest & request)
{
    if( batchRenderedVideoRequestShapeValid(request) )
        return QString();

    if( request.renderedCodec == BatchRenderedVideoCodec::ProRes )
        return QStringLiteral("codec=prores requires container=mov or unspecified");

    return QStringLiteral("unsupported rendered-video codec/container combination");
}

inline BatchRenderedVideoTarget batchRenderedVideoTargetFromRequest(
    const BatchExportFormatRequest & request)
{
    BatchRenderedVideoTarget target;
    if( request.format != BatchExportFormat::RenderedVideo
     || !batchRenderedVideoRequestShapeValid(request) )
    {
        return target;
    }

    target.codec = request.renderedCodec;
    target.container = request.renderedContainer;

    if( target.codec == BatchRenderedVideoCodec::Unspecified )
    {
        switch( target.container )
        {
            case BatchRenderedVideoContainer::Mov:
                target.codec = BatchRenderedVideoCodec::ProRes;
                break;
            case BatchRenderedVideoContainer::Mp4:
            case BatchRenderedVideoContainer::Mkv:
                target.codec = BatchRenderedVideoCodec::H264;
                break;
            case BatchRenderedVideoContainer::Unspecified:
                break;
        }
    }

    if( target.container == BatchRenderedVideoContainer::Unspecified )
    {
        switch( target.codec )
        {
            case BatchRenderedVideoCodec::H264:
            case BatchRenderedVideoCodec::H265:
                target.container = BatchRenderedVideoContainer::Mp4;
                break;
            case BatchRenderedVideoCodec::ProRes:
                target.container = BatchRenderedVideoContainer::Mov;
                break;
            case BatchRenderedVideoCodec::Unspecified:
                break;
        }
    }

    target.extension =
        QString::fromLatin1(batchRenderedVideoContainerExtension(target.container));
    target.complete = target.codec != BatchRenderedVideoCodec::Unspecified
                   && target.container != BatchRenderedVideoContainer::Unspecified
                   && !target.extension.isEmpty();
    return target;
}

inline BatchRenderedVideoEncoderPreset batchRenderedVideoEncoderPresetFromTarget(
    const BatchRenderedVideoTarget & target)
{
    BatchRenderedVideoEncoderPreset preset;
    if( !target.complete )
        return preset;

    preset.extension = target.extension;
    switch( target.codec )
    {
        case BatchRenderedVideoCodec::H264:
            preset.profile = BatchRenderedVideoEncoderProfile::H264;
            switch( target.container )
            {
                case BatchRenderedVideoContainer::Mov:
                    preset.option = BatchRenderedVideoEncoderOption::H264HighMov;
                    break;
                case BatchRenderedVideoContainer::Mp4:
                    preset.option = BatchRenderedVideoEncoderOption::H264HighMp4;
                    break;
                case BatchRenderedVideoContainer::Mkv:
                    preset.option = BatchRenderedVideoEncoderOption::H264HighMkv;
                    break;
                case BatchRenderedVideoContainer::Unspecified:
                    break;
            }
            break;
        case BatchRenderedVideoCodec::H265:
            preset.profile = BatchRenderedVideoEncoderProfile::H265_8;
            switch( target.container )
            {
                case BatchRenderedVideoContainer::Mov:
                    preset.option = BatchRenderedVideoEncoderOption::H265HighMov;
                    break;
                case BatchRenderedVideoContainer::Mp4:
                    preset.option = BatchRenderedVideoEncoderOption::H265HighMp4;
                    break;
                case BatchRenderedVideoContainer::Mkv:
                    preset.option = BatchRenderedVideoEncoderOption::H265HighMkv;
                    break;
                case BatchRenderedVideoContainer::Unspecified:
                    break;
            }
            break;
        case BatchRenderedVideoCodec::ProRes:
            if( target.container == BatchRenderedVideoContainer::Mov )
            {
                preset.profile = BatchRenderedVideoEncoderProfile::ProRes422HQ;
                preset.option =
                    BatchRenderedVideoEncoderOption::ProResFfmpegKostya;
            }
            break;
        case BatchRenderedVideoCodec::Unspecified:
            break;
    }

    preset.ready =
        preset.profile != BatchRenderedVideoEncoderProfile::Unspecified
     && preset.option != BatchRenderedVideoEncoderOption::Unspecified
     && !preset.extension.isEmpty();
    if( !preset.ready )
        preset.extension.clear();
    return preset;
}

inline BatchRenderedVideoEncoderPreset batchRenderedVideoEncoderPresetFromRequest(
    const BatchExportFormatRequest & request)
{
    return batchRenderedVideoEncoderPresetFromTarget(
        batchRenderedVideoTargetFromRequest(request));
}

inline QString batchRenderedVideoTargetSummary(const BatchExportFormatRequest & request)
{
    const BatchRenderedVideoTarget target =
        batchRenderedVideoTargetFromRequest(request);
    return QStringLiteral("target-codec=%1 target-container=%2 target-extension=%3 target-complete=%4")
        .arg(batchRenderedVideoCodecName(target.codec))
        .arg(batchRenderedVideoContainerName(target.container))
        .arg(target.extension.isEmpty() ? QStringLiteral("unspecified") : target.extension)
        .arg(target.complete ? QStringLiteral("true") : QStringLiteral("false"));
}

inline QString batchRenderedVideoEncoderPresetSummary(
    const BatchExportFormatRequest & request)
{
    const BatchRenderedVideoEncoderPreset preset =
        batchRenderedVideoEncoderPresetFromRequest(request);
    return QStringLiteral("encoder-profile=%1 encoder-option=%2 encoder-extension=%3 encoder-ready=%4")
        .arg(batchRenderedVideoEncoderProfileName(preset.profile))
        .arg(batchRenderedVideoEncoderOptionName(preset.option))
        .arg(preset.extension.isEmpty() ? QStringLiteral("unspecified") : preset.extension)
        .arg(preset.ready ? QStringLiteral("true") : QStringLiteral("false"));
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
