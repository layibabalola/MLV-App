#ifndef BATCHTYPES_H
#define BATCHTYPES_H

#include "../../platform/qt/ExportCodecIds.h"

#include <QDir>
#include <QFileInfo>
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
    int guiCodecProfile = -1;
    int guiCodecOption = -1;
    QString extension;
    bool ready = false;
};

struct BatchRenderedVideoFfmpegVideoPlan
{
    QString encoder;
    QString preset;
    QString qualityFlag;
    int qualityValue = -1;
    QString pixelFormat;
    QString videoTag;
    QString videoArguments;
    QString reason;
    bool ready = false;
};

struct BatchRenderedVideoOutputPlan
{
    QString outputPath;
    QString reason;
    bool ready = false;
};

struct BatchRenderedVideoRunnerPrerequisites
{
    bool processingParityReady = false;
    bool headlessRunnerReady = false;
    QString reason = QStringLiteral("rendered processing parity and headless rendered-export runner are not implemented");
    bool ready = false;
};

struct BatchRenderedVideoJobPlan
{
    BatchExportFormatRequest request;
    BatchRenderedVideoTarget target;
    BatchRenderedVideoEncoderPreset encoderPreset;
    BatchRenderedVideoFfmpegVideoPlan ffmpegVideoPlan;
    BatchRenderedVideoOutputPlan outputPlan;
    BatchRenderedVideoRunnerPrerequisites runnerPrerequisites;
    bool requestValid = false;
    bool targetReady = false;
    bool encoderReady = false;
    bool ffmpegVideoReady = false;
    bool outputReady = false;
    bool preflightReady = false;
    bool runnable = false;
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
            preset.guiCodecProfile = CODEC_H264;
            switch( target.container )
            {
                case BatchRenderedVideoContainer::Mov:
                    preset.option = BatchRenderedVideoEncoderOption::H264HighMov;
                    preset.guiCodecOption = CODEC_H264_H_MOV;
                    break;
                case BatchRenderedVideoContainer::Mp4:
                    preset.option = BatchRenderedVideoEncoderOption::H264HighMp4;
                    preset.guiCodecOption = CODEC_H264_H_MP4;
                    break;
                case BatchRenderedVideoContainer::Mkv:
                    preset.option = BatchRenderedVideoEncoderOption::H264HighMkv;
                    preset.guiCodecOption = CODEC_H264_H_MKV;
                    break;
                case BatchRenderedVideoContainer::Unspecified:
                    break;
            }
            break;
        case BatchRenderedVideoCodec::H265:
            preset.profile = BatchRenderedVideoEncoderProfile::H265_8;
            preset.guiCodecProfile = CODEC_H265_8;
            switch( target.container )
            {
                case BatchRenderedVideoContainer::Mov:
                    preset.option = BatchRenderedVideoEncoderOption::H265HighMov;
                    preset.guiCodecOption = CODEC_H265_H_MOV;
                    break;
                case BatchRenderedVideoContainer::Mp4:
                    preset.option = BatchRenderedVideoEncoderOption::H265HighMp4;
                    preset.guiCodecOption = CODEC_H265_H_MP4;
                    break;
                case BatchRenderedVideoContainer::Mkv:
                    preset.option = BatchRenderedVideoEncoderOption::H265HighMkv;
                    preset.guiCodecOption = CODEC_H265_H_MKV;
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
                preset.guiCodecProfile = CODEC_PRORES422HQ;
                preset.guiCodecOption = CODEC_PRORES_OPTION_KS;
            }
            break;
        case BatchRenderedVideoCodec::Unspecified:
            break;
    }

    preset.ready =
        preset.profile != BatchRenderedVideoEncoderProfile::Unspecified
     && preset.option != BatchRenderedVideoEncoderOption::Unspecified
     && preset.guiCodecProfile >= 0
     && preset.guiCodecOption >= 0
     && !preset.extension.isEmpty();
    if( !preset.ready )
    {
        preset.extension.clear();
        preset.guiCodecProfile = -1;
        preset.guiCodecOption = -1;
    }
    return preset;
}

inline BatchRenderedVideoEncoderPreset batchRenderedVideoEncoderPresetFromRequest(
    const BatchExportFormatRequest & request)
{
    return batchRenderedVideoEncoderPresetFromTarget(
        batchRenderedVideoTargetFromRequest(request));
}

inline BatchRenderedVideoFfmpegVideoPlan
batchRenderedVideoFfmpegVideoPlanFromEncoderPreset(
    const BatchRenderedVideoEncoderPreset & preset)
{
    BatchRenderedVideoFfmpegVideoPlan plan;
    if( !preset.ready )
    {
        plan.reason = QStringLiteral("rendered encoder preset incomplete");
        return plan;
    }

    switch( preset.profile )
    {
        case BatchRenderedVideoEncoderProfile::H264:
            plan.encoder = QStringLiteral("libx264");
            plan.preset = QStringLiteral("medium");
            plan.qualityFlag = QStringLiteral("-crf");
            plan.qualityValue = 14;
            plan.pixelFormat = QStringLiteral("yuv420p");
            break;
        case BatchRenderedVideoEncoderProfile::H265_8:
            plan.encoder = QStringLiteral("libx265");
            plan.preset = QStringLiteral("medium");
            plan.qualityFlag = QStringLiteral("-crf");
            plan.qualityValue = 18;
            plan.pixelFormat = QStringLiteral("yuv420p");
            plan.videoTag = QStringLiteral("hvc1");
            break;
        case BatchRenderedVideoEncoderProfile::ProRes422HQ:
            plan.encoder = QStringLiteral("prores_ks");
            plan.qualityFlag = QStringLiteral("-profile:v");
            plan.qualityValue = CODEC_PRORES422HQ;
            plan.pixelFormat = QStringLiteral("yuv422p10");
            break;
        case BatchRenderedVideoEncoderProfile::Unspecified:
            break;
    }

    plan.ready = !plan.encoder.isEmpty()
              && !plan.qualityFlag.isEmpty()
              && plan.qualityValue >= 0
              && !plan.pixelFormat.isEmpty();
    if( !plan.ready )
    {
        plan.reason = QStringLiteral("rendered ffmpeg video plan unavailable");
        return plan;
    }

    plan.videoArguments = QStringLiteral("-c:v %1 ").arg(plan.encoder);
    if( !plan.preset.isEmpty() )
        plan.videoArguments += QStringLiteral("-preset %1 ").arg(plan.preset);
    plan.videoArguments += QStringLiteral("%1 %2 ").arg(plan.qualityFlag).arg(plan.qualityValue);
    if( !plan.videoTag.isEmpty() )
        plan.videoArguments += QStringLiteral("-tag:v %1 ").arg(plan.videoTag);
    plan.videoArguments += QStringLiteral("-pix_fmt %1").arg(plan.pixelFormat);
    return plan;
}

inline BatchRenderedVideoFfmpegVideoPlan
batchRenderedVideoFfmpegVideoPlanFromRequest(
    const BatchExportFormatRequest & request)
{
    return batchRenderedVideoFfmpegVideoPlanFromEncoderPreset(
        batchRenderedVideoEncoderPresetFromRequest(request));
}

inline BatchRenderedVideoOutputPlan batchRenderedVideoOutputPlanFromPaths(
    const QString & inputPath,
    const QString & outputPath,
    const BatchRenderedVideoTarget & target)
{
    BatchRenderedVideoOutputPlan plan;
    if( !target.complete || target.extension.isEmpty() )
    {
        plan.reason = QStringLiteral("rendered target incomplete");
        return plan;
    }

    const QString inputTrimmed = inputPath.trimmed();
    if( inputTrimmed.isEmpty() )
    {
        plan.reason = QStringLiteral("input path is empty");
        return plan;
    }

    const QString outputTrimmed = outputPath.trimmed();
    if( outputTrimmed.isEmpty() )
    {
        plan.reason = QStringLiteral("output path is empty");
        return plan;
    }

    const QFileInfo inputInfo(inputTrimmed);
    const QString baseName = inputInfo.completeBaseName();
    if( baseName.isEmpty() )
    {
        plan.reason = QStringLiteral("input base name is empty");
        return plan;
    }

    const QString extension = target.extension.startsWith(QLatin1Char('.'))
        ? target.extension
        : QStringLiteral(".") + target.extension;
    const bool explicitDirectory =
        outputTrimmed.endsWith(QLatin1Char('/'))
     || outputTrimmed.endsWith(QLatin1Char('\\'));
    const QFileInfo outputInfo(outputTrimmed);

    if( explicitDirectory || outputInfo.suffix().isEmpty() )
    {
        plan.outputPath = QDir::cleanPath(
            QDir(outputTrimmed).filePath(baseName + extension));
        plan.ready = true;
        return plan;
    }

    if( outputInfo.suffix().toLower() != extension.mid(1).toLower() )
    {
        plan.reason = QStringLiteral("output path extension does not match target extension");
        return plan;
    }

    plan.outputPath = QDir::cleanPath(outputTrimmed);
    plan.ready = true;
    return plan;
}

inline BatchRenderedVideoRunnerPrerequisites
batchRenderedVideoRunnerPrerequisitesForCurrentBuild()
{
    BatchRenderedVideoRunnerPrerequisites prerequisites;
    prerequisites.processingParityReady = false;
    prerequisites.headlessRunnerReady = false;
    prerequisites.reason = QStringLiteral("rendered processing parity and headless rendered-export runner are not implemented");
    prerequisites.ready = false;
    return prerequisites;
}

inline BatchRenderedVideoJobPlan batchRenderedVideoJobPlanFromRequest(
    const QString & inputPath,
    const QString & outputPath,
    const BatchExportFormatRequest & request)
{
    BatchRenderedVideoJobPlan plan;
    plan.request = request;
    plan.requestValid = request.format == BatchExportFormat::RenderedVideo
                     && batchRenderedVideoRequestShapeValid(request);

    if( request.format == BatchExportFormat::RenderedVideo )
    {
        plan.target = batchRenderedVideoTargetFromRequest(request);
        plan.encoderPreset =
            batchRenderedVideoEncoderPresetFromTarget(plan.target);
        plan.ffmpegVideoPlan =
            batchRenderedVideoFfmpegVideoPlanFromEncoderPreset(plan.encoderPreset);
        plan.outputPlan =
            batchRenderedVideoOutputPlanFromPaths(inputPath, outputPath, plan.target);
    }
    else
    {
        plan.outputPlan.reason = QStringLiteral("not a rendered-video request");
    }

    plan.runnerPrerequisites =
        batchRenderedVideoRunnerPrerequisitesForCurrentBuild();
    plan.targetReady = plan.target.complete;
    plan.encoderReady = plan.encoderPreset.ready;
    plan.ffmpegVideoReady = plan.ffmpegVideoPlan.ready;
    plan.outputReady = plan.outputPlan.ready;
    plan.preflightReady = plan.requestValid
                       && plan.targetReady
                       && plan.encoderReady
                       && plan.ffmpegVideoReady
                       && plan.outputReady;
    plan.runnable = plan.preflightReady && plan.runnerPrerequisites.ready;
    return plan;
}

inline QString batchRenderedVideoJobPlanFirstBlocker(
    const BatchRenderedVideoJobPlan & plan)
{
    if( plan.request.format != BatchExportFormat::RenderedVideo )
        return QStringLiteral("not a rendered-video request");
    if( !plan.requestValid )
        return batchRenderedVideoRequestShapeError(plan.request);
    if( !plan.targetReady )
        return QStringLiteral("rendered target incomplete");
    if( !plan.encoderReady )
        return QStringLiteral("rendered encoder preset unavailable");
    if( !plan.ffmpegVideoReady )
    {
        return plan.ffmpegVideoPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg video plan unavailable")
            : plan.ffmpegVideoPlan.reason;
    }
    if( !plan.outputReady )
    {
        return plan.outputPlan.reason.isEmpty()
            ? QStringLiteral("rendered output path invalid")
            : plan.outputPlan.reason;
    }
    if( !plan.runnerPrerequisites.ready )
        return plan.runnerPrerequisites.reason;
    return QString();
}

inline QString batchRenderedVideoTargetSummary(const BatchRenderedVideoTarget & target)
{
    return QStringLiteral("target-codec=%1 target-container=%2 target-extension=%3 target-complete=%4")
        .arg(batchRenderedVideoCodecName(target.codec))
        .arg(batchRenderedVideoContainerName(target.container))
        .arg(target.extension.isEmpty() ? QStringLiteral("unspecified") : target.extension)
        .arg(target.complete ? QStringLiteral("true") : QStringLiteral("false"));
}

inline QString batchRenderedVideoTargetSummary(const BatchExportFormatRequest & request)
{
    return batchRenderedVideoTargetSummary(
        batchRenderedVideoTargetFromRequest(request));
}

inline QString batchRenderedVideoEncoderPresetSummary(
    const BatchRenderedVideoEncoderPreset & preset)
{
    return QStringLiteral("encoder-profile=%1 encoder-option=%2 gui-codec-profile=%3 gui-codec-option=%4 encoder-extension=%5 encoder-ready=%6")
        .arg(batchRenderedVideoEncoderProfileName(preset.profile))
        .arg(batchRenderedVideoEncoderOptionName(preset.option))
        .arg(preset.guiCodecProfile)
        .arg(preset.guiCodecOption)
        .arg(preset.extension.isEmpty() ? QStringLiteral("unspecified") : preset.extension)
        .arg(preset.ready ? QStringLiteral("true") : QStringLiteral("false"));
}

inline QString batchRenderedVideoEncoderPresetSummary(
    const BatchExportFormatRequest & request)
{
    return batchRenderedVideoEncoderPresetSummary(
        batchRenderedVideoEncoderPresetFromRequest(request));
}

inline QString batchRenderedVideoFfmpegVideoPlanSummary(
    const BatchRenderedVideoFfmpegVideoPlan & plan)
{
    return QStringLiteral("ffmpeg-video-encoder=%1 ffmpeg-video-preset=%2 ffmpeg-video-quality=%3:%4 ffmpeg-video-pix-fmt=%5 ffmpeg-video-tag=%6 ffmpeg-video-args=%7 ffmpeg-video-ready=%8 ffmpeg-video-reason=%9")
        .arg(plan.encoder.isEmpty() ? QStringLiteral("unspecified") : plan.encoder)
        .arg(plan.preset.isEmpty() ? QStringLiteral("none") : plan.preset)
        .arg(plan.qualityFlag.isEmpty() ? QStringLiteral("unspecified") : plan.qualityFlag)
        .arg(plan.qualityValue)
        .arg(plan.pixelFormat.isEmpty() ? QStringLiteral("unspecified") : plan.pixelFormat)
        .arg(plan.videoTag.isEmpty() ? QStringLiteral("none") : plan.videoTag)
        .arg(plan.videoArguments.isEmpty() ? QStringLiteral("unspecified") : plan.videoArguments)
        .arg(plan.ready ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoFfmpegVideoPlanSummary(
    const BatchExportFormatRequest & request)
{
    return batchRenderedVideoFfmpegVideoPlanSummary(
        batchRenderedVideoFfmpegVideoPlanFromRequest(request));
}

inline QString batchRenderedVideoOutputPlanSummary(
    const BatchRenderedVideoOutputPlan & plan)
{
    return QStringLiteral("rendered-output=%1 rendered-output-ready=%2 rendered-output-reason=%3")
        .arg(plan.outputPath.isEmpty() ? QStringLiteral("unspecified") : plan.outputPath)
        .arg(plan.ready ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoOutputPlanSummary(
    const QString & inputPath,
    const QString & outputPath,
    const BatchExportFormatRequest & request)
{
    return batchRenderedVideoOutputPlanSummary(
        batchRenderedVideoOutputPlanFromPaths(
            inputPath,
            outputPath,
            batchRenderedVideoTargetFromRequest(request)));
}

inline QString batchRenderedVideoRunnerPrerequisitesSummary(
    const BatchRenderedVideoRunnerPrerequisites & prerequisites)
{
    return QStringLiteral("runner-processing-parity-ready=%1 runner-headless-export-ready=%2 runner-ready=%3 runner-reason=%4")
        .arg(prerequisites.processingParityReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(prerequisites.headlessRunnerReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(prerequisites.ready ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(prerequisites.reason.isEmpty() ? QStringLiteral("none") : prerequisites.reason);
}

inline QString batchRenderedVideoJobPlanSummary(
    const BatchRenderedVideoJobPlan & plan)
{
    const QString blocker = batchRenderedVideoJobPlanFirstBlocker(plan);
    return QStringLiteral("%1 %2 %3 %4 %5 %6 preflight-ready=%7 runnable=%8 first-blocker=%9")
        .arg(batchExportFormatRequestSummary(plan.request))
        .arg(batchRenderedVideoTargetSummary(plan.target))
        .arg(batchRenderedVideoEncoderPresetSummary(plan.encoderPreset))
        .arg(batchRenderedVideoFfmpegVideoPlanSummary(plan.ffmpegVideoPlan))
        .arg(batchRenderedVideoOutputPlanSummary(plan.outputPlan))
        .arg(batchRenderedVideoRunnerPrerequisitesSummary(plan.runnerPrerequisites))
        .arg(plan.preflightReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.runnable ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(blocker.isEmpty()
            ? QStringLiteral("none")
            : blocker);
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
