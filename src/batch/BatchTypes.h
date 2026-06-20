#ifndef BATCHTYPES_H
#define BATCHTYPES_H

#include "../../platform/qt/ExportCodecIds.h"
#include "../../platform/qt/StretchFactors.h"

#include <QDir>
#include <QFileInfo>
#include <QLocale>
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

struct BatchRenderedVideoFfmpegFilterPlan
{
    QString source = QStringLiteral("gui-base-color-scale");
    QString colorScaleFilter;
    QString filterArguments;
    QString reason;
    bool baseColorScaleReady = false;
    bool optionalFiltersOwned = false;
    bool moireeFilterOwned = false;
    bool hdrBlendOwned = false;
    bool stabilizationOwned = false;
    bool ready = false;
};

struct BatchRenderedVideoFfmpegFramePlan
{
    int sourceWidth = 0;
    int sourceHeight = 0;
    int outputWidth = 0;
    int outputHeight = 0;
    double frameRate = 0.0;
    QString frameRateArgument;
    QString frameSizeArgument;
    QString reason;
    bool resizeEnabled = false;
    bool resizeHeightLocked = false;
    bool stretchApplied = false;
    bool codecDimensionAdjusted = false;
    bool scaled = false;
    bool ready = false;
};

struct BatchRenderedVideoSourceMetadata
{
    int width = 0;
    int height = 0;
    double frameRate = 0.0;
    double stretchFactorX = STRETCH_H_100;
    double stretchFactorY = STRETCH_V_100;
    QString reason;
    bool ready = false;
};

struct BatchRenderedVideoRenderSettings
{
    QString source = QStringLiteral("batch-defaults");
    bool resizeEnabled = false;
    int resizeWidth = 0;
    int resizeHeight = 0;
    bool resizeHeightLocked = false;
    bool explicitHeadlessSettings = false;
    bool guiSettingsOwned = false;
    QString reason;
    bool ready = true;
};

struct BatchRenderedVideoOutputPlan
{
    QString outputPath;
    QString reason;
    int inputClipCount = 1;
    bool explicitFileOutput = false;
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
    BatchRenderedVideoFfmpegFilterPlan ffmpegFilterPlan;
    BatchRenderedVideoSourceMetadata sourceMetadata;
    BatchRenderedVideoRenderSettings renderSettings;
    BatchRenderedVideoFfmpegFramePlan ffmpegFramePlan;
    BatchRenderedVideoOutputPlan outputPlan;
    BatchRenderedVideoRunnerPrerequisites runnerPrerequisites;
    bool requestValid = false;
    bool targetReady = false;
    bool encoderReady = false;
    bool ffmpegVideoReady = false;
    bool ffmpegFilterReady = false;
    bool metadataAttempted = false;
    bool metadataReady = false;
    bool ffmpegFrameReady = false;
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

inline BatchRenderedVideoFfmpegFilterPlan
batchRenderedVideoFfmpegFilterPlanForCurrentBuild()
{
    BatchRenderedVideoFfmpegFilterPlan plan;
    plan.colorScaleFilter =
        QStringLiteral("scale=in_color_matrix=bt601:out_color_matrix=bt709");
    plan.filterArguments = QStringLiteral("-vf %1").arg(plan.colorScaleFilter);
    plan.baseColorScaleReady = !plan.colorScaleFilter.isEmpty();
    plan.ready = plan.baseColorScaleReady && !plan.filterArguments.isEmpty();
    if( !plan.ready )
        plan.reason = QStringLiteral("rendered ffmpeg filter plan unavailable");
    return plan;
}

inline bool batchRenderedVideoEncoderProfileRequiresEvenDimensions(
    BatchRenderedVideoEncoderProfile profile)
{
    return profile == BatchRenderedVideoEncoderProfile::H264
        || profile == BatchRenderedVideoEncoderProfile::H265_8;
}

inline QString batchRenderedVideoFfmpegFrameRateArgument(double frameRate)
{
    QLocale locale = QLocale(QLocale::English, QLocale::UnitedKingdom);
    locale.setNumberOptions(QLocale::OmitGroupSeparator);
    return locale.toString(frameRate);
}

inline BatchRenderedVideoFfmpegFramePlan
batchRenderedVideoFfmpegFramePlanFromGuiState(
    int sourceWidth,
    int sourceHeight,
    double frameRate,
    double stretchFactorX,
    double stretchFactorY,
    bool resizeEnabled,
    int resizeWidth,
    int resizeHeight,
    bool resizeHeightLocked,
    BatchRenderedVideoEncoderProfile encoderProfile)
{
    BatchRenderedVideoFfmpegFramePlan plan;
    plan.sourceWidth = sourceWidth;
    plan.sourceHeight = sourceHeight;
    plan.frameRate = frameRate;
    plan.resizeEnabled = resizeEnabled;
    plan.resizeHeightLocked = resizeHeightLocked;

    if( sourceWidth <= 0 || sourceHeight <= 0 )
    {
        plan.reason = QStringLiteral("rendered source dimensions invalid");
        return plan;
    }
    if( !(frameRate > 0.0) )
    {
        plan.reason = QStringLiteral("rendered frame rate invalid");
        return plan;
    }
    if( !(stretchFactorX > 0.0) || !(stretchFactorY > 0.0) )
    {
        plan.reason = QStringLiteral("rendered stretch factors invalid");
        return plan;
    }

    int width = sourceWidth;
    int height = sourceHeight;

    if( resizeEnabled )
    {
        if( resizeWidth <= 0 || (!resizeHeightLocked && resizeHeight <= 0) )
        {
            plan.reason = QStringLiteral("rendered resize dimensions invalid");
            return plan;
        }

        if( resizeHeightLocked )
        {
            height = static_cast<int>(
                static_cast<double>(resizeWidth)
                / static_cast<double>(sourceWidth)
                / stretchFactorX
                * stretchFactorY
                * static_cast<double>(sourceHeight)
                + 0.5);
        }
        else
        {
            height = resizeHeight;
        }
        width = resizeWidth;
        plan.scaled = true;
    }
    else if( stretchFactorX != STRETCH_H_100
          || stretchFactorY != STRETCH_V_100 )
    {
        if( stretchFactorY == STRETCH_V_033 )
        {
            width = sourceWidth * 3;
            height = sourceHeight;
        }
        else
        {
            width = static_cast<int>(
                static_cast<double>(sourceWidth) * stretchFactorX);
            height = static_cast<int>(
                static_cast<double>(sourceHeight) * stretchFactorY);
        }
        plan.stretchApplied = true;
        plan.scaled = true;
    }

    if( batchRenderedVideoEncoderProfileRequiresEvenDimensions(encoderProfile) )
    {
        if( (width % 2) != 0 )
        {
            width += width % 2;
            plan.codecDimensionAdjusted = true;
            plan.scaled = true;
        }
        if( (height % 2) != 0 )
        {
            height += height % 2;
            plan.codecDimensionAdjusted = true;
            plan.scaled = true;
        }
    }

    if( width <= 0 || height <= 0 )
    {
        plan.reason = QStringLiteral("rendered output dimensions invalid");
        return plan;
    }

    plan.outputWidth = width;
    plan.outputHeight = height;
    plan.frameRateArgument =
        batchRenderedVideoFfmpegFrameRateArgument(frameRate);
    plan.frameSizeArgument =
        QStringLiteral("%1x%2").arg(width).arg(height);
    plan.ready = !plan.frameRateArgument.isEmpty()
              && !plan.frameSizeArgument.isEmpty();
    if( !plan.ready )
        plan.reason = QStringLiteral("rendered ffmpeg frame plan unavailable");
    return plan;
}

inline BatchRenderedVideoSourceMetadata batchRenderedVideoSourceMetadata(
    int width,
    int height,
    double frameRate,
    double stretchFactorX,
    double stretchFactorY)
{
    BatchRenderedVideoSourceMetadata metadata;
    metadata.width = width;
    metadata.height = height;
    metadata.frameRate = frameRate;
    metadata.stretchFactorX = stretchFactorX;
    metadata.stretchFactorY = stretchFactorY;

    if( width <= 0 || height <= 0 )
    {
        metadata.reason = QStringLiteral("rendered source dimensions invalid");
        return metadata;
    }
    if( !(frameRate > 0.0) )
    {
        metadata.reason = QStringLiteral("rendered frame rate invalid");
        return metadata;
    }
    if( !(stretchFactorX > 0.0) || !(stretchFactorY > 0.0) )
    {
        metadata.reason = QStringLiteral("rendered stretch factors invalid");
        return metadata;
    }

    metadata.ready = true;
    return metadata;
}

inline BatchRenderedVideoRenderSettings batchRenderedVideoDefaultRenderSettings()
{
    return BatchRenderedVideoRenderSettings();
}

inline BatchRenderedVideoRenderSettings
batchRenderedVideoRenderSettingsFromExplicitResize(
    bool resizeEnabled,
    int resizeWidth,
    int resizeHeight,
    bool resizeHeightLocked)
{
    BatchRenderedVideoRenderSettings settings;
    settings.source = QStringLiteral("explicit-headless");
    settings.resizeEnabled = resizeEnabled;
    settings.resizeWidth = resizeWidth;
    settings.resizeHeight = resizeHeight;
    settings.resizeHeightLocked = resizeHeightLocked;
    settings.explicitHeadlessSettings = true;
    settings.guiSettingsOwned = false;
    if( resizeEnabled
     && (resizeWidth <= 0 || (!resizeHeightLocked && resizeHeight <= 0)) )
    {
        settings.ready = false;
        settings.reason = QStringLiteral("rendered resize dimensions invalid");
    }
    return settings;
}

inline BatchRenderedVideoFfmpegFramePlan
batchRenderedVideoFfmpegFramePlanFromMetadata(
    const BatchRenderedVideoSourceMetadata & metadata,
    const BatchRenderedVideoRenderSettings & settings,
    BatchRenderedVideoEncoderProfile encoderProfile)
{
    if( !metadata.ready )
    {
        BatchRenderedVideoFfmpegFramePlan plan;
        plan.sourceWidth = metadata.width;
        plan.sourceHeight = metadata.height;
        plan.frameRate = metadata.frameRate;
        plan.resizeEnabled = settings.resizeEnabled;
        plan.resizeHeightLocked = settings.resizeHeightLocked;
        plan.reason = metadata.reason.isEmpty()
            ? QStringLiteral("rendered source metadata unavailable")
            : metadata.reason;
        return plan;
    }

    if( !settings.ready )
    {
        BatchRenderedVideoFfmpegFramePlan plan;
        plan.sourceWidth = metadata.width;
        plan.sourceHeight = metadata.height;
        plan.frameRate = metadata.frameRate;
        plan.resizeEnabled = settings.resizeEnabled;
        plan.resizeHeightLocked = settings.resizeHeightLocked;
        plan.reason = settings.reason.isEmpty()
            ? QStringLiteral("rendered render settings unavailable")
            : settings.reason;
        return plan;
    }

    return batchRenderedVideoFfmpegFramePlanFromGuiState(
        metadata.width,
        metadata.height,
        metadata.frameRate,
        metadata.stretchFactorX,
        metadata.stretchFactorY,
        settings.resizeEnabled,
        settings.resizeWidth,
        settings.resizeHeight,
        settings.resizeHeightLocked,
        encoderProfile);
}

inline BatchRenderedVideoOutputPlan batchRenderedVideoOutputPlanFromPaths(
    const QString & inputPath,
    const QString & outputPath,
    const BatchRenderedVideoTarget & target,
    int inputClipCount)
{
    BatchRenderedVideoOutputPlan plan;
    plan.inputClipCount = inputClipCount;
    if( !target.complete || target.extension.isEmpty() )
    {
        plan.reason = QStringLiteral("rendered target incomplete");
        return plan;
    }

    if( inputClipCount < 1 )
    {
        plan.reason = QStringLiteral("rendered input clip count invalid");
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
    const bool explicitFileOutput =
        !explicitDirectory && !outputInfo.suffix().isEmpty();
    plan.explicitFileOutput = explicitFileOutput;

    if( !explicitFileOutput )
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

    if( inputClipCount > 1 )
    {
        plan.reason = QStringLiteral("explicit rendered output file requires a single input clip");
        return plan;
    }

    plan.outputPath = QDir::cleanPath(outputTrimmed);
    plan.ready = true;
    return plan;
}

inline BatchRenderedVideoOutputPlan batchRenderedVideoOutputPlanFromPaths(
    const QString & inputPath,
    const QString & outputPath,
    const BatchRenderedVideoTarget & target)
{
    return batchRenderedVideoOutputPlanFromPaths(
        inputPath,
        outputPath,
        target,
        1);
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
    const BatchExportFormatRequest & request,
    int inputClipCount,
    const BatchRenderedVideoRenderSettings & settings =
        batchRenderedVideoDefaultRenderSettings())
{
    BatchRenderedVideoJobPlan plan;
    plan.request = request;
    plan.renderSettings = settings;
    plan.requestValid = request.format == BatchExportFormat::RenderedVideo
                     && batchRenderedVideoRequestShapeValid(request);

    if( request.format == BatchExportFormat::RenderedVideo )
    {
        plan.target = batchRenderedVideoTargetFromRequest(request);
        plan.encoderPreset =
            batchRenderedVideoEncoderPresetFromTarget(plan.target);
        plan.ffmpegVideoPlan =
            batchRenderedVideoFfmpegVideoPlanFromEncoderPreset(plan.encoderPreset);
        plan.ffmpegFilterPlan =
            batchRenderedVideoFfmpegFilterPlanForCurrentBuild();
        plan.outputPlan =
            batchRenderedVideoOutputPlanFromPaths(
                inputPath,
                outputPath,
                plan.target,
                inputClipCount);
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
    plan.ffmpegFilterReady = plan.ffmpegFilterPlan.ready;
    plan.outputReady = plan.outputPlan.ready;
    plan.preflightReady = plan.requestValid
                       && plan.targetReady
                       && plan.encoderReady
                       && plan.ffmpegVideoReady
                       && plan.ffmpegFilterReady
                       && plan.renderSettings.ready
                       && plan.outputReady;
    plan.runnable = plan.preflightReady && plan.runnerPrerequisites.ready;
    return plan;
}

inline BatchRenderedVideoJobPlan batchRenderedVideoJobPlanFromRequest(
    const QString & inputPath,
    const QString & outputPath,
    const BatchExportFormatRequest & request)
{
    return batchRenderedVideoJobPlanFromRequest(
        inputPath,
        outputPath,
        request,
        1);
}

inline BatchRenderedVideoJobPlan batchRenderedVideoJobPlanFromRequest(
    const QString & inputPath,
    const QString & outputPath,
    const BatchExportFormatRequest & request,
    const BatchRenderedVideoRenderSettings & settings)
{
    return batchRenderedVideoJobPlanFromRequest(
        inputPath,
        outputPath,
        request,
        1,
        settings);
}

inline BatchRenderedVideoJobPlan batchRenderedVideoJobPlanWithMetadata(
    const BatchRenderedVideoJobPlan & preflightPlan,
    const BatchRenderedVideoSourceMetadata & metadata,
    const BatchRenderedVideoRenderSettings & settings)
{
    BatchRenderedVideoJobPlan plan = preflightPlan;
    plan.sourceMetadata = metadata;
    plan.renderSettings = settings;
    plan.metadataAttempted = true;
    plan.metadataReady = metadata.ready;
    if( plan.encoderReady )
    {
        plan.ffmpegFramePlan =
            batchRenderedVideoFfmpegFramePlanFromMetadata(
                metadata,
                settings,
                plan.encoderPreset.profile);
    }
    else
    {
        plan.ffmpegFramePlan.reason =
            QStringLiteral("rendered encoder preset incomplete");
    }
    plan.ffmpegFrameReady = plan.ffmpegFramePlan.ready;
    plan.preflightReady = plan.requestValid
                       && plan.targetReady
                       && plan.encoderReady
                       && plan.ffmpegVideoReady
                       && plan.ffmpegFilterReady
                       && plan.renderSettings.ready
                       && plan.metadataReady
                       && plan.ffmpegFrameReady
                       && plan.outputReady;
    plan.runnable = plan.preflightReady && plan.runnerPrerequisites.ready;
    return plan;
}

inline BatchRenderedVideoJobPlan batchRenderedVideoJobPlanWithMetadata(
    const BatchRenderedVideoJobPlan & preflightPlan,
    const BatchRenderedVideoSourceMetadata & metadata)
{
    return batchRenderedVideoJobPlanWithMetadata(
        preflightPlan,
        metadata,
        preflightPlan.renderSettings);
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
    if( !plan.ffmpegFilterReady )
    {
        return plan.ffmpegFilterPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg filter plan unavailable")
            : plan.ffmpegFilterPlan.reason;
    }
    if( !plan.renderSettings.ready )
    {
        return plan.renderSettings.reason.isEmpty()
            ? QStringLiteral("rendered render settings unavailable")
            : plan.renderSettings.reason;
    }
    if( !plan.outputReady )
    {
        return plan.outputPlan.reason.isEmpty()
            ? QStringLiteral("rendered output path invalid")
            : plan.outputPlan.reason;
    }
    if( plan.metadataAttempted && !plan.metadataReady )
    {
        return plan.sourceMetadata.reason.isEmpty()
            ? QStringLiteral("rendered source metadata unavailable")
            : plan.sourceMetadata.reason;
    }
    if( plan.metadataAttempted && !plan.ffmpegFrameReady )
    {
        return plan.ffmpegFramePlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg frame plan unavailable")
            : plan.ffmpegFramePlan.reason;
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

inline QString batchRenderedVideoFfmpegFilterPlanSummary(
    const BatchRenderedVideoFfmpegFilterPlan & plan)
{
    return QStringLiteral("ffmpeg-filter-source=%1 ffmpeg-filter-base-color-scale-ready=%2 ffmpeg-filter-optional-owned=%3 ffmpeg-filter-moiree-owned=%4 ffmpeg-filter-hdr-owned=%5 ffmpeg-filter-stabilization-owned=%6 ffmpeg-filter-color-scale=%7 ffmpeg-filter-args=%8 ffmpeg-filter-ready=%9 ffmpeg-filter-reason=%10")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.baseColorScaleReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.optionalFiltersOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.moireeFilterOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.hdrBlendOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.stabilizationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.colorScaleFilter.isEmpty() ? QStringLiteral("unspecified") : plan.colorScaleFilter)
        .arg(plan.filterArguments.isEmpty() ? QStringLiteral("unspecified") : plan.filterArguments)
        .arg(plan.ready ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoFfmpegFramePlanSummary(
    const BatchRenderedVideoFfmpegFramePlan & plan)
{
    return QStringLiteral("ffmpeg-frame-source=%1x%2 ffmpeg-frame-size=%3 ffmpeg-frame-rate=%4 ffmpeg-frame-resize=%5 ffmpeg-frame-resize-height-locked=%6 ffmpeg-frame-stretch=%7 ffmpeg-frame-codec-dimension-adjusted=%8 ffmpeg-frame-scaled=%9 ffmpeg-frame-ready=%10 ffmpeg-frame-reason=%11")
        .arg(plan.sourceWidth)
        .arg(plan.sourceHeight)
        .arg(plan.frameSizeArgument.isEmpty() ? QStringLiteral("unspecified") : plan.frameSizeArgument)
        .arg(plan.frameRateArgument.isEmpty() ? QStringLiteral("unspecified") : plan.frameRateArgument)
        .arg(plan.resizeEnabled ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.resizeHeightLocked ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.stretchApplied ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.codecDimensionAdjusted ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.scaled ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.ready ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoSourceMetadataSummary(
    const BatchRenderedVideoSourceMetadata & metadata)
{
    return QStringLiteral("source-metadata=%1x%2 source-fps=%3 source-stretch-x=%4 source-stretch-y=%5 source-metadata-ready=%6 source-metadata-reason=%7")
        .arg(metadata.width)
        .arg(metadata.height)
        .arg(metadata.frameRate)
        .arg(metadata.stretchFactorX)
        .arg(metadata.stretchFactorY)
        .arg(metadata.ready ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(metadata.reason.isEmpty() ? QStringLiteral("none") : metadata.reason);
}

inline QString batchRenderedVideoSourceMetadataSummary(
    const BatchRenderedVideoJobPlan & plan)
{
    return QStringLiteral("%1 source-metadata-attempted=%2")
        .arg(batchRenderedVideoSourceMetadataSummary(plan.sourceMetadata))
        .arg(plan.metadataAttempted ? QStringLiteral("true") : QStringLiteral("false"));
}

inline QString batchRenderedVideoRenderSettingsSummary(
    const BatchRenderedVideoRenderSettings & settings)
{
    return QStringLiteral("render-settings-source=%1 render-settings-explicit-headless=%2 render-settings-gui-owned=%3 render-settings-ready=%4 render-settings-reason=%5 render-settings-resize=%6 render-settings-resize-width=%7 render-settings-resize-height=%8 render-settings-resize-height-locked=%9")
        .arg(settings.source.isEmpty() ? QStringLiteral("unspecified") : settings.source)
        .arg(settings.explicitHeadlessSettings ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(settings.guiSettingsOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(settings.ready ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(settings.reason.isEmpty() ? QStringLiteral("none") : settings.reason)
        .arg(settings.resizeEnabled ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(settings.resizeWidth)
        .arg(settings.resizeHeight)
        .arg(settings.resizeHeightLocked ? QStringLiteral("true") : QStringLiteral("false"));
}

inline QString batchRenderedVideoOutputPlanSummary(
    const BatchRenderedVideoOutputPlan & plan)
{
    return QStringLiteral("rendered-output=%1 rendered-output-explicit-file=%2 rendered-output-input-clips=%3 rendered-output-ready=%4 rendered-output-reason=%5")
        .arg(plan.outputPath.isEmpty() ? QStringLiteral("unspecified") : plan.outputPath)
        .arg(plan.explicitFileOutput ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.inputClipCount)
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
    return QStringLiteral("%1 %2 %3 %4 %5 %6 %7 %8 %9 %10 preflight-ready=%11 runnable=%12 first-blocker=%13")
        .arg(batchExportFormatRequestSummary(plan.request))
        .arg(batchRenderedVideoTargetSummary(plan.target))
        .arg(batchRenderedVideoEncoderPresetSummary(plan.encoderPreset))
        .arg(batchRenderedVideoFfmpegVideoPlanSummary(plan.ffmpegVideoPlan))
        .arg(batchRenderedVideoFfmpegFilterPlanSummary(plan.ffmpegFilterPlan))
        .arg(batchRenderedVideoSourceMetadataSummary(plan))
        .arg(batchRenderedVideoRenderSettingsSummary(plan.renderSettings))
        .arg(batchRenderedVideoFfmpegFramePlanSummary(plan.ffmpegFramePlan))
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
