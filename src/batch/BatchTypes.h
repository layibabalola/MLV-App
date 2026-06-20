#ifndef BATCHTYPES_H
#define BATCHTYPES_H

#include "../../platform/qt/ExportCodecIds.h"
#include "../../platform/qt/StretchFactors.h"

#include <QDir>
#include <QFileInfo>
#include <QLocale>
#include <QString>
#include <QStandardPaths>

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

struct BatchRenderedVideoOptionalFilterPlan
{
    QString source = QStringLiteral("optional-filter-ownership-contract");
    QString baseFilterArguments;
    QString reason;
    bool baseFilterContractReady = false;
    bool optionalFiltersRequested = false;
    bool optionalFilterGraphOwned = false;
    bool moireeFilterOwned = false;
    bool hdrBlendOwned = false;
    bool stabilizationOwned = false;
    bool optionalFilterOrderOwned = false;
    bool optionalFilterParityOwned = false;
    bool optionalFilterExecutionReady = false;
    bool contractReady = false;
};

struct BatchRenderedVideoSourceAudioPlan
{
    QString source = QStringLiteral("video-only-undiscovered");
    QString clipPath;
    QString audioState = QStringLiteral("unknown");
    QString reason;
    bool discoveryOwned = false;
    bool discoveryAttempted = false;
    bool sourceAudioKnown = false;
    bool sourceAudioPresent = false;
    bool extractionOwned = false;
    bool muxInputOwned = false;
    bool syncValidationOwned = false;
    bool videoOnlyFallbackReady = false;
    bool contractReady = false;
};

struct BatchRenderedVideoAudioMuxPrerequisitesPlan
{
    QString source = QStringLiteral("audio-mux-prerequisite-contract");
    QString inputState = QStringLiteral("rendered-source-audio-discovery");
    QString outputState =
        QStringLiteral("ffmpeg-audio-input-or-video-only-fallback");
    QString reason;
    bool sourceAudioContractReady = false;
    bool sourceAudioDiscoveryOwned = false;
    bool sourceAudioExtractionOwned = false;
    bool audioInputOwned = false;
    bool audioMuxOwned = false;
    bool audioSyncValidationOwned = false;
    bool videoOnlyFallbackReady = false;
    bool muxReady = false;
    bool contractReady = false;
};

struct BatchRenderedVideoFfmpegAudioPlan
{
    QString source = QStringLiteral("video-only-contract");
    QString audioArguments = QStringLiteral("-an");
    QString reason;
    bool videoOnlyCommandReady = false;
    bool sourceAudioDiscoveryOwned = false;
    bool sourceAudioExtractionOwned = false;
    bool audioInputOwned = false;
    bool audioMuxOwned = false;
    bool audioSyncOwned = false;
    bool contractReady = false;
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

struct BatchRenderedVideoReceiptApplicationPlan
{
    QString source = QStringLiteral("receipt-application-input-output-contract");
    QString inputState = QStringLiteral("open-mlv-runtime-plus-batch-receipt");
    QString outputState = QStringLiteral("receipt-applied-mlv-processing-state");
    QString reason;
    bool sourceMetadataReady = false;
    bool frameGeometryReady = false;
    bool inputContractReady = false;
    bool outputContractReady = false;
    bool applyToMlvOwned = false;
    bool processingObjectMutationOwned = false;
    bool cacheInvalidationOwned = false;
    bool cutStretchStateOwned = false;
    bool lookAssistApplicationOwned = false;
    bool receiptValidationOwned = false;
    bool applicationReady = false;
    bool contractReady = false;
};

struct BatchRenderedVideoFrameProcessingPlan
{
    QString source = QStringLiteral("headless-rendered-frame-contract");
    QString rawFramePixelFormat = QStringLiteral("rgb48");
    QString outputSize;
    QString reason;
    bool sourceMetadataReady = false;
    bool frameGeometryReady = false;
    bool receiptApplicationContractReady = false;
    bool debayerContractReady = false;
    bool previewProcessingContractReady = false;
    bool resizeProcessingContractReady = false;
    bool rgb48FrameBufferContractReady = false;
    bool frameIterationContractReady = false;
    bool receiptApplicationOwned = false;
    bool debayerOwned = false;
    bool previewProcessingOwned = false;
    bool resizeProcessingOwned = false;
    bool rgb48FrameBufferOwned = false;
    bool frameIterationOwned = false;
    bool processingParityValidationOwned = false;
    bool processingParityReady = false;
    bool frameProcessingReady = false;
    bool contractReady = false;
};

struct BatchRenderedVideoFfmpegBinaryPlan
{
    QString source = QStringLiteral("default-executable-name");
    QString requestedExecutable = QStringLiteral("ffmpeg");
    QString resolvedExecutable = QStringLiteral("ffmpeg");
    QString reason;
    bool pathSearchOwned = false;
    bool pathSearchAttempted = false;
    bool foundOnPath = false;
    bool commandExecutableReady = true;
};

struct BatchRenderedVideoFfmpegCommandPlan
{
    QString source = QStringLiteral("gui-rawvideo-pipe");
    QString executable = QStringLiteral("ffmpeg");
    QString rawInputPixelFormat = QStringLiteral("rgb48");
    QString rawInputArguments;
    QString colorTagSource = QStringLiteral("rec709-default");
    int colorTag = 1;
    QString colorArguments;
    QString arguments;
    QString commandLine;
    QString reason;
    bool rawVideoPipeInputReady = false;
    QString audioArguments;
    bool audioInputOwned = false;
    bool executionOwned = false;
    bool outputVerificationOwned = false;
    bool ready = false;
};

struct BatchRenderedVideoFfmpegExecutionPlan
{
    QString source = QStringLiteral("command-contract");
    QString executable;
    QString commandLine;
    QString reason;
    bool commandReady = false;
    bool processLaunchOwned = false;
    bool stdinPipeOwned = false;
    bool rawFrameFeedOwned = false;
    bool stderrCaptureOwned = false;
    bool exitCodeValidationOwned = false;
    bool timeoutOwned = false;
    bool cleanupOwned = false;
    bool executionReady = false;
    bool contractReady = false;
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

struct BatchRenderedVideoOutputVerificationPlan
{
    QString source = QStringLiteral("planned-output-contract");
    QString expectedOutputPath;
    QString expectedExtension;
    QString reason;
    bool outputPathReady = false;
    bool extensionMatchesTarget = false;
    bool fileExistenceCheckOwned = false;
    bool nonEmptyCheckOwned = false;
    bool mediaProbeOwned = false;
    bool codecContainerCheckOwned = false;
    bool frameCountCheckOwned = false;
    bool receiptOrHashOwned = false;
    bool verificationExecutionOwned = false;
    bool contractReady = false;
};

struct BatchRenderedVideoOutputVerificationExecutionPlan
{
    QString source = QStringLiteral("post-ffmpeg-output-verification-contract");
    QString expectedOutputPath;
    QString expectedExtension;
    QString mediaProbeExecutable = QStringLiteral("ffprobe");
    QString reason;
    bool outputVerificationContractReady = false;
    bool ffmpegExecutionContractReady = false;
    bool fileExistenceCheckOwned = false;
    bool nonEmptyCheckOwned = false;
    bool mediaProbeExecutionOwned = false;
    bool codecContainerValidationOwned = false;
    bool frameCountValidationOwned = false;
    bool receiptHashValidationOwned = false;
    bool verificationExecutionReady = false;
    bool contractReady = false;
};

struct BatchRenderedVideoRunnerPrerequisites
{
    bool processingParityReady = false;
    bool frameProcessingReady = false;
    bool audioMuxReady = false;
    bool ffmpegExecutionReady = false;
    bool outputVerificationReady = false;
    bool headlessRunnerReady = false;
    QString reason = QStringLiteral("rendered processing parity, frame processing, audio muxing, ffmpeg execution, output verification, and headless rendered-export runner are not implemented");
    bool ready = false;
};

struct BatchRenderedVideoJobPlan
{
    BatchExportFormatRequest request;
    BatchRenderedVideoTarget target;
    BatchRenderedVideoEncoderPreset encoderPreset;
    BatchRenderedVideoFfmpegVideoPlan ffmpegVideoPlan;
    BatchRenderedVideoFfmpegFilterPlan ffmpegFilterPlan;
    BatchRenderedVideoOptionalFilterPlan optionalFilterPlan;
    BatchRenderedVideoSourceAudioPlan sourceAudioPlan;
    BatchRenderedVideoAudioMuxPrerequisitesPlan audioMuxPrerequisitesPlan;
    BatchRenderedVideoFfmpegAudioPlan ffmpegAudioPlan;
    BatchRenderedVideoSourceMetadata sourceMetadata;
    BatchRenderedVideoRenderSettings renderSettings;
    BatchRenderedVideoFfmpegFramePlan ffmpegFramePlan;
    BatchRenderedVideoReceiptApplicationPlan receiptApplicationPlan;
    BatchRenderedVideoFrameProcessingPlan frameProcessingPlan;
    BatchRenderedVideoFfmpegBinaryPlan ffmpegBinaryPlan;
    BatchRenderedVideoFfmpegCommandPlan ffmpegCommandPlan;
    BatchRenderedVideoFfmpegExecutionPlan ffmpegExecutionPlan;
    BatchRenderedVideoOutputPlan outputPlan;
    BatchRenderedVideoOutputVerificationPlan outputVerificationPlan;
    BatchRenderedVideoOutputVerificationExecutionPlan outputVerificationExecutionPlan;
    BatchRenderedVideoRunnerPrerequisites runnerPrerequisites;
    bool requestValid = false;
    bool targetReady = false;
    bool encoderReady = false;
    bool ffmpegVideoReady = false;
    bool ffmpegFilterReady = false;
    bool optionalFilterContractReady = false;
    bool sourceAudioContractReady = false;
    bool audioMuxPrerequisitesContractReady = false;
    bool ffmpegAudioContractReady = false;
    bool metadataAttempted = false;
    bool metadataReady = false;
    bool ffmpegFrameReady = false;
    bool receiptApplicationContractReady = false;
    bool frameProcessingContractReady = false;
    bool ffmpegBinaryCommandReady = false;
    bool ffmpegCommandReady = false;
    bool ffmpegExecutionContractReady = false;
    bool outputReady = false;
    bool outputVerificationContractReady = false;
    bool outputVerificationExecutionContractReady = false;
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

inline BatchRenderedVideoOptionalFilterPlan
batchRenderedVideoOptionalFilterPlanFromFilterPlan(
    const BatchRenderedVideoFfmpegFilterPlan & filterPlan)
{
    BatchRenderedVideoOptionalFilterPlan plan;
    plan.baseFilterArguments = filterPlan.filterArguments;
    plan.baseFilterContractReady = filterPlan.ready;
    plan.optionalFilterGraphOwned = filterPlan.optionalFiltersOwned;
    plan.moireeFilterOwned = filterPlan.moireeFilterOwned;
    plan.hdrBlendOwned = filterPlan.hdrBlendOwned;
    plan.stabilizationOwned = filterPlan.stabilizationOwned;

    if( !filterPlan.ready )
    {
        plan.reason = filterPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg filter plan unavailable")
            : filterPlan.reason;
        return plan;
    }

    plan.contractReady = plan.baseFilterContractReady
                      && !plan.baseFilterArguments.isEmpty();
    plan.optionalFilterExecutionReady =
        plan.optionalFiltersRequested
     && plan.optionalFilterGraphOwned
     && plan.moireeFilterOwned
     && plan.hdrBlendOwned
     && plan.stabilizationOwned
     && plan.optionalFilterOrderOwned
     && plan.optionalFilterParityOwned;
    if( !plan.contractReady )
    {
        plan.reason =
            QStringLiteral("rendered optional-filter contract unavailable");
    }
    return plan;
}

inline BatchRenderedVideoSourceAudioPlan
batchRenderedVideoSourceAudioPlanForCurrentBuild(
    const QString & clipPath = QString())
{
    BatchRenderedVideoSourceAudioPlan plan;
    const QString normalizedPath =
        QDir::fromNativeSeparators(clipPath.trimmed());
    if( !normalizedPath.isEmpty() )
        plan.clipPath = QDir::cleanPath(normalizedPath);
    plan.videoOnlyFallbackReady = true;
    plan.contractReady = plan.videoOnlyFallbackReady;
    if( !plan.contractReady )
    {
        plan.reason =
            QStringLiteral("rendered source audio contract unavailable");
    }
    return plan;
}

inline BatchRenderedVideoAudioMuxPrerequisitesPlan
batchRenderedVideoAudioMuxPrerequisitesPlanFromSourceAudio(
    const BatchRenderedVideoSourceAudioPlan & sourceAudioPlan)
{
    BatchRenderedVideoAudioMuxPrerequisitesPlan plan;
    plan.sourceAudioContractReady = sourceAudioPlan.contractReady;
    plan.sourceAudioDiscoveryOwned = sourceAudioPlan.discoveryOwned;
    plan.sourceAudioExtractionOwned = sourceAudioPlan.extractionOwned;
    plan.audioInputOwned = sourceAudioPlan.muxInputOwned;
    plan.audioSyncValidationOwned = sourceAudioPlan.syncValidationOwned;
    plan.videoOnlyFallbackReady = sourceAudioPlan.videoOnlyFallbackReady;

    if( !sourceAudioPlan.contractReady )
    {
        plan.reason = sourceAudioPlan.reason.isEmpty()
            ? QStringLiteral("rendered source audio contract unavailable")
            : sourceAudioPlan.reason;
        return plan;
    }

    plan.contractReady = plan.sourceAudioContractReady
                      && plan.videoOnlyFallbackReady;
    plan.muxReady = plan.sourceAudioDiscoveryOwned
                 && plan.sourceAudioExtractionOwned
                 && plan.audioInputOwned
                 && plan.audioMuxOwned
                 && plan.audioSyncValidationOwned;
    if( !plan.contractReady )
    {
        plan.reason =
            QStringLiteral("rendered audio mux prerequisite contract unavailable");
    }
    return plan;
}

inline BatchRenderedVideoFfmpegAudioPlan
batchRenderedVideoFfmpegAudioPlanForCurrentBuild(
    const BatchRenderedVideoSourceAudioPlan & sourceAudioPlan,
    const BatchRenderedVideoAudioMuxPrerequisitesPlan & audioMuxPlan)
{
    BatchRenderedVideoFfmpegAudioPlan plan;
    plan.sourceAudioDiscoveryOwned = audioMuxPlan.sourceAudioDiscoveryOwned;
    plan.sourceAudioExtractionOwned = audioMuxPlan.sourceAudioExtractionOwned;
    plan.audioInputOwned = audioMuxPlan.audioInputOwned;
    plan.audioMuxOwned = audioMuxPlan.audioMuxOwned;
    plan.audioSyncOwned = audioMuxPlan.audioSyncValidationOwned;
    plan.videoOnlyCommandReady = !plan.audioArguments.isEmpty()
                              && audioMuxPlan.videoOnlyFallbackReady;

    if( !sourceAudioPlan.contractReady )
    {
        plan.reason = sourceAudioPlan.reason.isEmpty()
            ? QStringLiteral("rendered source audio contract unavailable")
            : sourceAudioPlan.reason;
        return plan;
    }
    if( !audioMuxPlan.contractReady )
    {
        plan.reason = audioMuxPlan.reason.isEmpty()
            ? QStringLiteral("rendered audio mux prerequisite contract unavailable")
            : audioMuxPlan.reason;
        return plan;
    }

    plan.contractReady = plan.videoOnlyCommandReady
                      && audioMuxPlan.contractReady;
    if( !plan.contractReady )
        plan.reason = QStringLiteral("rendered ffmpeg audio contract unavailable");
    return plan;
}

inline BatchRenderedVideoFfmpegAudioPlan
batchRenderedVideoFfmpegAudioPlanForCurrentBuild(
    const BatchRenderedVideoSourceAudioPlan & sourceAudioPlan)
{
    return batchRenderedVideoFfmpegAudioPlanForCurrentBuild(
        sourceAudioPlan,
        batchRenderedVideoAudioMuxPrerequisitesPlanFromSourceAudio(
            sourceAudioPlan));
}

inline BatchRenderedVideoFfmpegAudioPlan
batchRenderedVideoFfmpegAudioPlanForCurrentBuild()
{
    const BatchRenderedVideoSourceAudioPlan sourceAudioPlan =
        batchRenderedVideoSourceAudioPlanForCurrentBuild();
    return batchRenderedVideoFfmpegAudioPlanForCurrentBuild(sourceAudioPlan);
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

inline BatchRenderedVideoReceiptApplicationPlan
batchRenderedVideoReceiptApplicationPlanFromContracts(
    const BatchRenderedVideoSourceMetadata & metadata,
    const BatchRenderedVideoFfmpegFramePlan & framePlan)
{
    BatchRenderedVideoReceiptApplicationPlan plan;
    plan.sourceMetadataReady = metadata.ready;
    plan.frameGeometryReady = framePlan.ready;

    if( !metadata.ready )
    {
        plan.reason = metadata.reason.isEmpty()
            ? QStringLiteral("rendered source metadata unavailable")
            : metadata.reason;
        return plan;
    }
    if( !framePlan.ready )
    {
        plan.reason = framePlan.reason.isEmpty()
            ? QStringLiteral("rendered frame geometry unavailable")
            : framePlan.reason;
        return plan;
    }

    plan.inputContractReady = !plan.inputState.isEmpty()
                           && plan.sourceMetadataReady
                           && plan.frameGeometryReady;
    plan.outputContractReady = !plan.outputState.isEmpty()
                            && plan.inputContractReady;
    plan.contractReady = plan.inputContractReady
                      && plan.outputContractReady;
    plan.applicationReady = plan.applyToMlvOwned
                         && plan.processingObjectMutationOwned
                         && plan.cacheInvalidationOwned
                         && plan.cutStretchStateOwned
                         && plan.lookAssistApplicationOwned
                         && plan.receiptValidationOwned;
    if( !plan.contractReady )
    {
        plan.reason =
            QStringLiteral("rendered receipt application contract unavailable");
    }
    return plan;
}

inline BatchRenderedVideoFrameProcessingPlan
batchRenderedVideoFrameProcessingPlanFromFramePlan(
    const BatchRenderedVideoSourceMetadata & metadata,
    const BatchRenderedVideoFfmpegFramePlan & framePlan,
    const BatchRenderedVideoReceiptApplicationPlan & receiptPlan)
{
    BatchRenderedVideoFrameProcessingPlan plan;
    plan.sourceMetadataReady = metadata.ready;
    plan.frameGeometryReady = framePlan.ready;
    plan.outputSize = framePlan.frameSizeArgument;

    if( !metadata.ready )
    {
        plan.reason = metadata.reason.isEmpty()
            ? QStringLiteral("rendered source metadata unavailable")
            : metadata.reason;
        return plan;
    }
    if( !framePlan.ready )
    {
        plan.reason = framePlan.reason.isEmpty()
            ? QStringLiteral("rendered frame geometry unavailable")
            : framePlan.reason;
        return plan;
    }

    plan.contractReady = !plan.rawFramePixelFormat.isEmpty()
                      && !plan.outputSize.isEmpty()
                      && plan.sourceMetadataReady
                      && plan.frameGeometryReady
                      && receiptPlan.contractReady;
    if( plan.contractReady )
    {
        plan.receiptApplicationContractReady = receiptPlan.contractReady;
        plan.debayerContractReady = true;
        plan.previewProcessingContractReady = true;
        plan.resizeProcessingContractReady = true;
        plan.rgb48FrameBufferContractReady = true;
        plan.frameIterationContractReady = true;
    }
    plan.receiptApplicationOwned = receiptPlan.applicationReady;
    plan.processingParityReady = plan.receiptApplicationOwned
                              && plan.debayerOwned
                              && plan.previewProcessingOwned
                              && plan.resizeProcessingOwned
                              && plan.processingParityValidationOwned;
    plan.frameProcessingReady = plan.processingParityReady
                             && plan.rgb48FrameBufferOwned
                             && plan.frameIterationOwned;
    if( !plan.contractReady )
    {
        plan.reason =
            QStringLiteral("rendered frame-processing contract unavailable");
    }
    return plan;
}

inline BatchRenderedVideoFrameProcessingPlan
batchRenderedVideoFrameProcessingPlanFromFramePlan(
    const BatchRenderedVideoSourceMetadata & metadata,
    const BatchRenderedVideoFfmpegFramePlan & framePlan)
{
    return batchRenderedVideoFrameProcessingPlanFromFramePlan(
        metadata,
        framePlan,
        batchRenderedVideoReceiptApplicationPlanFromContracts(
            metadata,
            framePlan));
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

inline BatchRenderedVideoOutputVerificationPlan
batchRenderedVideoOutputVerificationPlanFromOutput(
    const BatchRenderedVideoOutputPlan & outputPlan,
    const BatchRenderedVideoTarget & target)
{
    BatchRenderedVideoOutputVerificationPlan plan;
    plan.expectedOutputPath = outputPlan.outputPath;
    plan.expectedExtension = target.extension;

    if( !outputPlan.ready )
    {
        plan.reason = outputPlan.reason.isEmpty()
            ? QStringLiteral("rendered output path invalid")
            : outputPlan.reason;
        return plan;
    }
    if( plan.expectedExtension.isEmpty() )
    {
        plan.reason = QStringLiteral("rendered target extension unavailable");
        return plan;
    }

    plan.outputPathReady = !plan.expectedOutputPath.isEmpty();
    const QFileInfo outputInfo(plan.expectedOutputPath);
    plan.extensionMatchesTarget =
        outputInfo.suffix().toLower()
            == plan.expectedExtension.mid(1).toLower();
    plan.contractReady = plan.outputPathReady
                      && plan.extensionMatchesTarget;
    if( !plan.contractReady )
    {
        plan.reason = QStringLiteral("rendered output verification contract unavailable");
    }
    return plan;
}

inline BatchRenderedVideoFfmpegBinaryPlan
batchRenderedVideoFfmpegBinaryPlanFromRequestedName(
    const QString & requestedExecutable = QStringLiteral("ffmpeg"))
{
    BatchRenderedVideoFfmpegBinaryPlan plan;
    const QString requested = requestedExecutable.trimmed();
    plan.requestedExecutable = requested.isEmpty()
        ? QStringLiteral("ffmpeg")
        : requested;
    if( !requested.isEmpty() && requested != QStringLiteral("ffmpeg") )
        plan.source = QStringLiteral("requested-executable");
    plan.resolvedExecutable = plan.requestedExecutable;
    plan.commandExecutableReady = !plan.resolvedExecutable.isEmpty();
    if( !plan.commandExecutableReady )
    {
        plan.reason = QStringLiteral("ffmpeg executable name is empty");
    }
    return plan;
}

inline BatchRenderedVideoFfmpegBinaryPlan
batchRenderedVideoFfmpegBinaryPlanFromResolvedPath(
    const QString & requestedExecutable,
    const QString & resolvedExecutable)
{
    BatchRenderedVideoFfmpegBinaryPlan plan =
        batchRenderedVideoFfmpegBinaryPlanFromRequestedName(
            requestedExecutable);
    plan.source = QStringLiteral("path-search");
    plan.pathSearchOwned = true;
    plan.pathSearchAttempted = true;

    const QString resolved = resolvedExecutable.trimmed();
    if( !resolved.isEmpty() )
    {
        plan.resolvedExecutable = QDir::cleanPath(resolved);
        plan.foundOnPath = true;
        plan.commandExecutableReady = true;
        plan.reason.clear();
    }
    else
    {
        plan.foundOnPath = false;
        plan.commandExecutableReady = !plan.requestedExecutable.isEmpty();
        plan.reason = QStringLiteral("ffmpeg executable not found on PATH");
    }
    return plan;
}

inline BatchRenderedVideoFfmpegBinaryPlan
batchRenderedVideoFfmpegBinaryPlanFromCurrentEnvironment(
    const QString & requestedExecutable = QStringLiteral("ffmpeg"))
{
    const QString requested = requestedExecutable.trimmed().isEmpty()
        ? QStringLiteral("ffmpeg")
        : requestedExecutable.trimmed();
    return batchRenderedVideoFfmpegBinaryPlanFromResolvedPath(
        requested,
        QStandardPaths::findExecutable(requested));
}

inline QString batchRenderedVideoCommandExecutableForDisplay(
    const QString & executable)
{
    if( executable.contains(QLatin1Char(' '))
     || executable.contains(QLatin1Char('\t')) )
    {
        return QStringLiteral("\"%1\"").arg(executable);
    }
    return executable;
}

inline BatchRenderedVideoFfmpegCommandPlan
batchRenderedVideoFfmpegCommandPlanFromParts(
    const BatchRenderedVideoFfmpegFramePlan & framePlan,
    const BatchRenderedVideoFfmpegFilterPlan & filterPlan,
    const BatchRenderedVideoFfmpegAudioPlan & audioPlan,
    const BatchRenderedVideoFfmpegVideoPlan & videoPlan,
    const BatchRenderedVideoOutputPlan & outputPlan,
    const BatchRenderedVideoFfmpegBinaryPlan & binaryPlan)
{
    BatchRenderedVideoFfmpegCommandPlan plan;
    plan.executable = binaryPlan.resolvedExecutable;
    if( !binaryPlan.commandExecutableReady )
    {
        plan.reason = binaryPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg executable unavailable")
            : binaryPlan.reason;
        return plan;
    }
    if( !framePlan.ready )
    {
        plan.reason = framePlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg frame plan unavailable")
            : framePlan.reason;
        return plan;
    }
    if( !filterPlan.ready )
    {
        plan.reason = filterPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg filter plan unavailable")
            : filterPlan.reason;
        return plan;
    }
    if( !audioPlan.contractReady )
    {
        plan.reason = audioPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg audio contract unavailable")
            : audioPlan.reason;
        return plan;
    }
    if( !videoPlan.ready )
    {
        plan.reason = videoPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg video plan unavailable")
            : videoPlan.reason;
        return plan;
    }
    if( !outputPlan.ready )
    {
        plan.reason = outputPlan.reason.isEmpty()
            ? QStringLiteral("rendered output path invalid")
            : outputPlan.reason;
        return plan;
    }

    plan.rawInputArguments =
        QStringLiteral("-r %1 -y -f rawvideo -s %2 -pix_fmt %3 -i -")
            .arg(framePlan.frameRateArgument)
            .arg(framePlan.frameSizeArgument)
            .arg(plan.rawInputPixelFormat);
    plan.rawVideoPipeInputReady =
        !plan.rawInputArguments.isEmpty()
     && !framePlan.frameRateArgument.isEmpty()
     && !framePlan.frameSizeArgument.isEmpty();
    plan.colorArguments =
        QStringLiteral("-color_primaries %1 -color_trc %1 -colorspace bt709")
            .arg(plan.colorTag);
    plan.audioArguments = audioPlan.audioArguments;
    plan.audioInputOwned = audioPlan.audioInputOwned;

    plan.arguments =
        QStringLiteral("%1 %2 %3 %4 %5 \"%6\"")
            .arg(plan.rawInputArguments)
            .arg(videoPlan.videoArguments)
            .arg(plan.colorArguments)
            .arg(filterPlan.filterArguments)
            .arg(plan.audioArguments)
            .arg(outputPlan.outputPath);
    plan.commandLine =
        QStringLiteral("%1 %2")
            .arg(batchRenderedVideoCommandExecutableForDisplay(plan.executable),
                 plan.arguments);
    plan.ready = !plan.executable.isEmpty()
              && plan.rawVideoPipeInputReady
              && !videoPlan.videoArguments.isEmpty()
              && !filterPlan.filterArguments.isEmpty()
              && !plan.colorArguments.isEmpty()
              && !plan.audioArguments.isEmpty()
              && !outputPlan.outputPath.isEmpty()
              && !plan.arguments.isEmpty()
              && !plan.commandLine.isEmpty();
    if( !plan.ready )
        plan.reason = QStringLiteral("rendered ffmpeg command plan unavailable");
    return plan;
}

inline BatchRenderedVideoFfmpegCommandPlan
batchRenderedVideoFfmpegCommandPlanFromParts(
    const BatchRenderedVideoFfmpegFramePlan & framePlan,
    const BatchRenderedVideoFfmpegFilterPlan & filterPlan,
    const BatchRenderedVideoFfmpegVideoPlan & videoPlan,
    const BatchRenderedVideoOutputPlan & outputPlan)
{
    return batchRenderedVideoFfmpegCommandPlanFromParts(
        framePlan,
        filterPlan,
        batchRenderedVideoFfmpegAudioPlanForCurrentBuild(),
        videoPlan,
        outputPlan,
        batchRenderedVideoFfmpegBinaryPlanFromRequestedName());
}

inline BatchRenderedVideoFfmpegExecutionPlan
batchRenderedVideoFfmpegExecutionPlanFromCommand(
    const BatchRenderedVideoFfmpegCommandPlan & commandPlan)
{
    BatchRenderedVideoFfmpegExecutionPlan plan;
    plan.executable = commandPlan.executable;
    plan.commandLine = commandPlan.commandLine;
    plan.commandReady = commandPlan.ready;

    if( !commandPlan.ready )
    {
        plan.reason = commandPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg command plan unavailable")
            : commandPlan.reason;
        return plan;
    }

    plan.contractReady = !plan.executable.isEmpty()
                      && !plan.commandLine.isEmpty();
    plan.executionReady = plan.processLaunchOwned
                       && plan.stdinPipeOwned
                       && plan.rawFrameFeedOwned
                       && plan.stderrCaptureOwned
                       && plan.exitCodeValidationOwned
                       && plan.timeoutOwned
                       && plan.cleanupOwned;
    if( !plan.contractReady )
    {
        plan.reason =
            QStringLiteral("rendered ffmpeg execution contract unavailable");
    }
    return plan;
}

inline BatchRenderedVideoOutputVerificationExecutionPlan
batchRenderedVideoOutputVerificationExecutionPlanFromContracts(
    const BatchRenderedVideoOutputVerificationPlan & outputVerificationPlan,
    const BatchRenderedVideoFfmpegExecutionPlan & ffmpegExecutionPlan)
{
    BatchRenderedVideoOutputVerificationExecutionPlan plan;
    plan.expectedOutputPath = outputVerificationPlan.expectedOutputPath;
    plan.expectedExtension = outputVerificationPlan.expectedExtension;
    plan.outputVerificationContractReady =
        outputVerificationPlan.contractReady;
    plan.ffmpegExecutionContractReady = ffmpegExecutionPlan.contractReady;

    if( !outputVerificationPlan.contractReady )
    {
        plan.reason = outputVerificationPlan.reason.isEmpty()
            ? QStringLiteral("rendered output verification contract unavailable")
            : outputVerificationPlan.reason;
        return plan;
    }
    if( !ffmpegExecutionPlan.contractReady )
    {
        plan.reason = ffmpegExecutionPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg execution contract unavailable")
            : ffmpegExecutionPlan.reason;
        return plan;
    }

    plan.contractReady = !plan.expectedOutputPath.isEmpty()
                      && !plan.expectedExtension.isEmpty()
                      && !plan.mediaProbeExecutable.isEmpty()
                      && plan.outputVerificationContractReady
                      && plan.ffmpegExecutionContractReady;
    plan.verificationExecutionReady = plan.fileExistenceCheckOwned
                                   && plan.nonEmptyCheckOwned
                                   && plan.mediaProbeExecutionOwned
                                   && plan.codecContainerValidationOwned
                                   && plan.frameCountValidationOwned
                                   && plan.receiptHashValidationOwned;
    if( !plan.contractReady )
    {
        plan.reason =
            QStringLiteral("rendered output verification execution contract unavailable");
    }
    return plan;
}

inline BatchRenderedVideoRunnerPrerequisites
batchRenderedVideoRunnerPrerequisitesForCurrentBuild()
{
    BatchRenderedVideoRunnerPrerequisites prerequisites;
    prerequisites.processingParityReady = false;
    prerequisites.frameProcessingReady = false;
    prerequisites.audioMuxReady = false;
    prerequisites.ffmpegExecutionReady = false;
    prerequisites.outputVerificationReady = false;
    prerequisites.headlessRunnerReady = false;
    prerequisites.ready = prerequisites.processingParityReady
                       && prerequisites.frameProcessingReady
                       && prerequisites.audioMuxReady
                       && prerequisites.ffmpegExecutionReady
                       && prerequisites.outputVerificationReady
                       && prerequisites.headlessRunnerReady;
    if( !prerequisites.ready )
    {
        prerequisites.reason = QStringLiteral("rendered processing parity, frame processing, audio muxing, ffmpeg execution, output verification, and headless rendered-export runner are not implemented");
    }
    return prerequisites;
}

inline BatchRenderedVideoJobPlan batchRenderedVideoJobPlanFromRequest(
    const QString & inputPath,
    const QString & outputPath,
    const BatchExportFormatRequest & request,
    int inputClipCount,
    const BatchRenderedVideoRenderSettings & settings,
    const BatchRenderedVideoFfmpegBinaryPlan & binaryPlan)
{
    BatchRenderedVideoJobPlan plan;
    plan.request = request;
    plan.renderSettings = settings;
    plan.ffmpegBinaryPlan = binaryPlan;
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
        plan.optionalFilterPlan =
            batchRenderedVideoOptionalFilterPlanFromFilterPlan(
                plan.ffmpegFilterPlan);
        plan.sourceAudioPlan =
            batchRenderedVideoSourceAudioPlanForCurrentBuild(inputPath);
        plan.audioMuxPrerequisitesPlan =
            batchRenderedVideoAudioMuxPrerequisitesPlanFromSourceAudio(
                plan.sourceAudioPlan);
        plan.ffmpegAudioPlan =
            batchRenderedVideoFfmpegAudioPlanForCurrentBuild(
                plan.sourceAudioPlan,
                plan.audioMuxPrerequisitesPlan);
        plan.outputPlan =
            batchRenderedVideoOutputPlanFromPaths(
                inputPath,
                outputPath,
                plan.target,
                inputClipCount);
        plan.outputVerificationPlan =
            batchRenderedVideoOutputVerificationPlanFromOutput(
                plan.outputPlan,
                plan.target);
        plan.outputVerificationExecutionPlan =
            batchRenderedVideoOutputVerificationExecutionPlanFromContracts(
                plan.outputVerificationPlan,
                plan.ffmpegExecutionPlan);
        plan.receiptApplicationPlan.reason =
            QStringLiteral("rendered source metadata unavailable");
    }
    else
    {
        plan.outputPlan.reason = QStringLiteral("not a rendered-video request");
        plan.sourceAudioPlan.reason =
            QStringLiteral("not a rendered-video request");
        plan.audioMuxPrerequisitesPlan.reason =
            QStringLiteral("not a rendered-video request");
        plan.ffmpegAudioPlan.reason =
            QStringLiteral("not a rendered-video request");
        plan.optionalFilterPlan.reason =
            QStringLiteral("not a rendered-video request");
        plan.receiptApplicationPlan.reason =
            QStringLiteral("not a rendered-video request");
        plan.frameProcessingPlan.reason =
            QStringLiteral("not a rendered-video request");
        plan.outputVerificationPlan.reason =
            QStringLiteral("not a rendered-video request");
        plan.outputVerificationExecutionPlan.reason =
            QStringLiteral("not a rendered-video request");
    }

    plan.runnerPrerequisites =
        batchRenderedVideoRunnerPrerequisitesForCurrentBuild();
    plan.targetReady = plan.target.complete;
    plan.encoderReady = plan.encoderPreset.ready;
    plan.ffmpegVideoReady = plan.ffmpegVideoPlan.ready;
    plan.ffmpegFilterReady = plan.ffmpegFilterPlan.ready;
    plan.optionalFilterContractReady =
        plan.optionalFilterPlan.contractReady;
    plan.sourceAudioContractReady =
        plan.sourceAudioPlan.contractReady;
    plan.audioMuxPrerequisitesContractReady =
        plan.audioMuxPrerequisitesPlan.contractReady;
    plan.ffmpegAudioContractReady =
        plan.ffmpegAudioPlan.contractReady;
    plan.ffmpegBinaryCommandReady =
        plan.ffmpegBinaryPlan.commandExecutableReady;
    plan.outputReady = plan.outputPlan.ready;
    plan.outputVerificationContractReady =
        plan.outputVerificationPlan.contractReady;
    plan.outputVerificationExecutionContractReady =
        plan.outputVerificationExecutionPlan.contractReady;
    plan.preflightReady = plan.requestValid
                       && plan.targetReady
                       && plan.encoderReady
                       && plan.ffmpegVideoReady
                       && plan.ffmpegFilterReady
                       && plan.optionalFilterContractReady
                       && plan.sourceAudioContractReady
                       && plan.audioMuxPrerequisitesContractReady
                       && plan.ffmpegAudioContractReady
                       && plan.ffmpegBinaryCommandReady
                       && plan.renderSettings.ready
                       && plan.outputReady
                       && plan.outputVerificationContractReady;
    plan.runnable = plan.preflightReady && plan.runnerPrerequisites.ready;
    return plan;
}

inline BatchRenderedVideoJobPlan batchRenderedVideoJobPlanFromRequest(
    const QString & inputPath,
    const QString & outputPath,
    const BatchExportFormatRequest & request,
    int inputClipCount,
    const BatchRenderedVideoRenderSettings & settings =
        batchRenderedVideoDefaultRenderSettings())
{
    return batchRenderedVideoJobPlanFromRequest(
        inputPath,
        outputPath,
        request,
        inputClipCount,
        settings,
        batchRenderedVideoFfmpegBinaryPlanFromRequestedName());
}

inline BatchRenderedVideoJobPlan batchRenderedVideoJobPlanFromRequest(
    const QString & inputPath,
    const QString & outputPath,
    const BatchExportFormatRequest & request,
    int inputClipCount,
    const BatchRenderedVideoFfmpegBinaryPlan & binaryPlan)
{
    return batchRenderedVideoJobPlanFromRequest(
        inputPath,
        outputPath,
        request,
        inputClipCount,
        batchRenderedVideoDefaultRenderSettings(),
        binaryPlan);
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

inline BatchRenderedVideoJobPlan batchRenderedVideoJobPlanFromRequest(
    const QString & inputPath,
    const QString & outputPath,
    const BatchExportFormatRequest & request,
    const BatchRenderedVideoRenderSettings & settings,
    const BatchRenderedVideoFfmpegBinaryPlan & binaryPlan)
{
    return batchRenderedVideoJobPlanFromRequest(
        inputPath,
        outputPath,
        request,
        1,
        settings,
        binaryPlan);
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
    plan.receiptApplicationPlan =
        batchRenderedVideoReceiptApplicationPlanFromContracts(
            metadata,
            plan.ffmpegFramePlan);
    plan.receiptApplicationContractReady =
        plan.receiptApplicationPlan.contractReady;
    plan.frameProcessingPlan =
        batchRenderedVideoFrameProcessingPlanFromFramePlan(
            metadata,
            plan.ffmpegFramePlan,
            plan.receiptApplicationPlan);
    plan.frameProcessingContractReady =
        plan.frameProcessingPlan.contractReady;
    plan.optionalFilterPlan =
        batchRenderedVideoOptionalFilterPlanFromFilterPlan(
            plan.ffmpegFilterPlan);
    plan.optionalFilterContractReady =
        plan.optionalFilterPlan.contractReady;
    plan.ffmpegCommandPlan =
        batchRenderedVideoFfmpegCommandPlanFromParts(
            plan.ffmpegFramePlan,
            plan.ffmpegFilterPlan,
            plan.ffmpegAudioPlan,
            plan.ffmpegVideoPlan,
            plan.outputPlan,
            plan.ffmpegBinaryPlan);
    plan.ffmpegCommandReady = plan.ffmpegCommandPlan.ready;
    plan.ffmpegExecutionPlan =
        batchRenderedVideoFfmpegExecutionPlanFromCommand(
            plan.ffmpegCommandPlan);
    plan.ffmpegExecutionContractReady =
        plan.ffmpegExecutionPlan.contractReady;
    plan.outputVerificationExecutionPlan =
        batchRenderedVideoOutputVerificationExecutionPlanFromContracts(
            plan.outputVerificationPlan,
            plan.ffmpegExecutionPlan);
    plan.outputVerificationExecutionContractReady =
        plan.outputVerificationExecutionPlan.contractReady;
    plan.preflightReady = plan.requestValid
                       && plan.targetReady
                       && plan.encoderReady
                       && plan.ffmpegVideoReady
                       && plan.ffmpegFilterReady
                       && plan.optionalFilterContractReady
                       && plan.sourceAudioContractReady
                       && plan.audioMuxPrerequisitesContractReady
                       && plan.ffmpegAudioContractReady
                       && plan.ffmpegBinaryCommandReady
                       && plan.renderSettings.ready
                       && plan.metadataReady
                       && plan.ffmpegFrameReady
                       && plan.receiptApplicationContractReady
                       && plan.frameProcessingContractReady
                       && plan.ffmpegCommandReady
                       && plan.ffmpegExecutionContractReady
                       && plan.outputReady
                       && plan.outputVerificationContractReady
                       && plan.outputVerificationExecutionContractReady;
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
    if( !plan.optionalFilterContractReady )
    {
        return plan.optionalFilterPlan.reason.isEmpty()
            ? QStringLiteral("rendered optional-filter contract unavailable")
            : plan.optionalFilterPlan.reason;
    }
    if( !plan.sourceAudioContractReady )
    {
        return plan.sourceAudioPlan.reason.isEmpty()
            ? QStringLiteral("rendered source audio contract unavailable")
            : plan.sourceAudioPlan.reason;
    }
    if( !plan.audioMuxPrerequisitesContractReady )
    {
        return plan.audioMuxPrerequisitesPlan.reason.isEmpty()
            ? QStringLiteral("rendered audio mux prerequisite contract unavailable")
            : plan.audioMuxPrerequisitesPlan.reason;
    }
    if( !plan.ffmpegAudioContractReady )
    {
        return plan.ffmpegAudioPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg audio contract unavailable")
            : plan.ffmpegAudioPlan.reason;
    }
    if( !plan.ffmpegBinaryCommandReady )
    {
        return plan.ffmpegBinaryPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg executable unavailable")
            : plan.ffmpegBinaryPlan.reason;
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
    if( !plan.outputVerificationContractReady )
    {
        return plan.outputVerificationPlan.reason.isEmpty()
            ? QStringLiteral("rendered output verification contract unavailable")
            : plan.outputVerificationPlan.reason;
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
    if( plan.metadataAttempted && !plan.receiptApplicationContractReady )
    {
        return plan.receiptApplicationPlan.reason.isEmpty()
            ? QStringLiteral("rendered receipt application contract unavailable")
            : plan.receiptApplicationPlan.reason;
    }
    if( plan.metadataAttempted && !plan.frameProcessingContractReady )
    {
        return plan.frameProcessingPlan.reason.isEmpty()
            ? QStringLiteral("rendered frame-processing contract unavailable")
            : plan.frameProcessingPlan.reason;
    }
    if( plan.metadataAttempted && !plan.ffmpegCommandReady )
    {
        return plan.ffmpegCommandPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg command plan unavailable")
            : plan.ffmpegCommandPlan.reason;
    }
    if( plan.metadataAttempted && !plan.ffmpegExecutionContractReady )
    {
        return plan.ffmpegExecutionPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg execution contract unavailable")
            : plan.ffmpegExecutionPlan.reason;
    }
    if( plan.metadataAttempted && !plan.outputVerificationExecutionContractReady )
    {
        return plan.outputVerificationExecutionPlan.reason.isEmpty()
            ? QStringLiteral("rendered output verification execution contract unavailable")
            : plan.outputVerificationExecutionPlan.reason;
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

inline QString batchRenderedVideoOptionalFilterPlanSummary(
    const BatchRenderedVideoOptionalFilterPlan & plan)
{
    return QStringLiteral("optional-filter-source=%1 optional-filter-base-contract-ready=%2 optional-filter-base-args=%3 optional-filter-requested=%4 optional-filter-graph-owned=%5 optional-filter-moiree-owned=%6 optional-filter-hdr-owned=%7 optional-filter-stabilization-owned=%8 optional-filter-order-owned=%9 optional-filter-parity-owned=%10 optional-filter-exec-ready=%11 optional-filter-contract-ready=%12 optional-filter-reason=%13")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.baseFilterContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.baseFilterArguments.isEmpty() ? QStringLiteral("unspecified") : plan.baseFilterArguments)
        .arg(plan.optionalFiltersRequested ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.optionalFilterGraphOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.moireeFilterOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.hdrBlendOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.stabilizationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.optionalFilterOrderOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.optionalFilterParityOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.optionalFilterExecutionReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.contractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoSourceAudioPlanSummary(
    const BatchRenderedVideoSourceAudioPlan & plan)
{
    return QStringLiteral("source-audio-source=%1 source-audio-clip=%2 source-audio-state=%3 source-audio-discovery-owned=%4 source-audio-discovery-attempted=%5 source-audio-known=%6 source-audio-present=%7 source-audio-extraction-owned=%8 source-audio-mux-input-owned=%9 source-audio-sync-validation-owned=%10 source-audio-video-only-ready=%11 source-audio-contract-ready=%12 source-audio-reason=%13")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.clipPath.isEmpty() ? QStringLiteral("unspecified") : plan.clipPath)
        .arg(plan.audioState.isEmpty() ? QStringLiteral("unspecified") : plan.audioState)
        .arg(plan.discoveryOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.discoveryAttempted ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioKnown ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioPresent ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.muxInputOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.syncValidationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.videoOnlyFallbackReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.contractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoAudioMuxPrerequisitesPlanSummary(
    const BatchRenderedVideoAudioMuxPrerequisitesPlan & plan)
{
    return QStringLiteral("audio-mux-source=%1 audio-mux-input=%2 audio-mux-output=%3 audio-mux-source-contract-ready=%4 audio-mux-video-only-ready=%5 audio-mux-source-discovery-owned=%6 audio-mux-extraction-owned=%7 audio-mux-input-owned=%8 audio-mux-mux-owned=%9 audio-mux-sync-owned=%10 audio-mux-ready=%11 audio-mux-contract-ready=%12 audio-mux-reason=%13")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.inputState.isEmpty() ? QStringLiteral("unspecified") : plan.inputState)
        .arg(plan.outputState.isEmpty() ? QStringLiteral("unspecified") : plan.outputState)
        .arg(plan.sourceAudioContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.videoOnlyFallbackReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioDiscoveryOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioExtractionOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioInputOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioSyncValidationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.muxReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.contractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoFfmpegAudioPlanSummary(
    const BatchRenderedVideoFfmpegAudioPlan & plan)
{
    return QStringLiteral("ffmpeg-audio-source=%1 ffmpeg-audio-args=%2 ffmpeg-audio-video-only-ready=%3 ffmpeg-audio-source-discovery-owned=%4 ffmpeg-audio-extraction-owned=%5 ffmpeg-audio-input-owned=%6 ffmpeg-audio-mux-owned=%7 ffmpeg-audio-sync-owned=%8 ffmpeg-audio-contract-ready=%9 ffmpeg-audio-reason=%10")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.audioArguments.isEmpty() ? QStringLiteral("unspecified") : plan.audioArguments)
        .arg(plan.videoOnlyCommandReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioDiscoveryOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioExtractionOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioInputOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioSyncOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.contractReady ? QStringLiteral("true") : QStringLiteral("false"))
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

inline QString batchRenderedVideoReceiptApplicationPlanSummary(
    const BatchRenderedVideoReceiptApplicationPlan & plan)
{
    return QStringLiteral("receipt-application-source=%1 receipt-application-input=%2 receipt-application-output=%3 receipt-application-metadata-ready=%4 receipt-application-frame-geometry-ready=%5 receipt-application-input-contract-ready=%6 receipt-application-output-contract-ready=%7 receipt-application-apply-owned=%8 receipt-application-processing-object-owned=%9 receipt-application-cache-invalidation-owned=%10 receipt-application-cut-stretch-owned=%11 receipt-application-look-assist-owned=%12 receipt-application-validation-owned=%13 receipt-application-ready=%14 receipt-application-contract-ready=%15 receipt-application-reason=%16")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.inputState.isEmpty() ? QStringLiteral("unspecified") : plan.inputState)
        .arg(plan.outputState.isEmpty() ? QStringLiteral("unspecified") : plan.outputState)
        .arg(plan.sourceMetadataReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.frameGeometryReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.inputContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.outputContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.applyToMlvOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.processingObjectMutationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.cacheInvalidationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.cutStretchStateOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.lookAssistApplicationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.receiptValidationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.applicationReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.contractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoFrameProcessingPlanSummary(
    const BatchRenderedVideoFrameProcessingPlan & plan)
{
    return QStringLiteral("render-processing-source=%1 render-processing-pix-fmt=%2 render-processing-output-size=%3 render-processing-metadata-ready=%4 render-processing-frame-geometry-ready=%5 render-processing-receipt-contract-ready=%6 render-processing-debayer-contract-ready=%7 render-processing-preview-contract-ready=%8 render-processing-resize-contract-ready=%9 render-processing-rgb48-buffer-contract-ready=%10 render-processing-frame-iteration-contract-ready=%11 render-processing-receipt-owned=%12 render-processing-debayer-owned=%13 render-processing-preview-owned=%14 render-processing-resize-owned=%15 render-processing-rgb48-buffer-owned=%16 render-processing-frame-iteration-owned=%17 render-processing-parity-validation-owned=%18 render-processing-parity-ready=%19 render-processing-frame-ready=%20 render-processing-contract-ready=%21 render-processing-reason=%22")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.rawFramePixelFormat.isEmpty() ? QStringLiteral("unspecified") : plan.rawFramePixelFormat)
        .arg(plan.outputSize.isEmpty() ? QStringLiteral("unspecified") : plan.outputSize)
        .arg(plan.sourceMetadataReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.frameGeometryReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.receiptApplicationContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.debayerContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.previewProcessingContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.resizeProcessingContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.rgb48FrameBufferContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.frameIterationContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.receiptApplicationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.debayerOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.previewProcessingOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.resizeProcessingOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.rgb48FrameBufferOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.frameIterationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.processingParityValidationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.processingParityReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.frameProcessingReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.contractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoFfmpegCommandPlanSummary(
    const BatchRenderedVideoFfmpegCommandPlan & plan)
{
    return QStringLiteral("ffmpeg-command-source=%1 ffmpeg-command-exe=%2 ffmpeg-command-raw-pix-fmt=%3 ffmpeg-command-raw-input=%4 ffmpeg-command-color-source=%5 ffmpeg-command-color-tag=%6 ffmpeg-command-color-args=%7 ffmpeg-command-audio-args=%8 ffmpeg-command-audio-owned=%9 ffmpeg-command-execution-owned=%10 ffmpeg-command-output-verification-owned=%11 ffmpeg-command-args=%12 ffmpeg-command-ready=%13 ffmpeg-command-reason=%14")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.executable.isEmpty() ? QStringLiteral("unspecified") : plan.executable)
        .arg(plan.rawInputPixelFormat.isEmpty() ? QStringLiteral("unspecified") : plan.rawInputPixelFormat)
        .arg(plan.rawInputArguments.isEmpty() ? QStringLiteral("unspecified") : plan.rawInputArguments)
        .arg(plan.colorTagSource.isEmpty() ? QStringLiteral("unspecified") : plan.colorTagSource)
        .arg(plan.colorTag)
        .arg(plan.colorArguments.isEmpty() ? QStringLiteral("unspecified") : plan.colorArguments)
        .arg(plan.audioArguments.isEmpty() ? QStringLiteral("unspecified") : plan.audioArguments)
        .arg(plan.audioInputOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.executionOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.outputVerificationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.arguments.isEmpty() ? QStringLiteral("unspecified") : plan.arguments)
        .arg(plan.ready ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoFfmpegExecutionPlanSummary(
    const BatchRenderedVideoFfmpegExecutionPlan & plan)
{
    return QStringLiteral("ffmpeg-execution-source=%1 ffmpeg-execution-exe=%2 ffmpeg-execution-command-ready=%3 ffmpeg-execution-process-launch-owned=%4 ffmpeg-execution-stdin-pipe-owned=%5 ffmpeg-execution-raw-frame-feed-owned=%6 ffmpeg-execution-stderr-capture-owned=%7 ffmpeg-execution-exit-code-owned=%8 ffmpeg-execution-timeout-owned=%9 ffmpeg-execution-cleanup-owned=%10 ffmpeg-execution-ready=%11 ffmpeg-execution-contract-ready=%12 ffmpeg-execution-reason=%13")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.executable.isEmpty() ? QStringLiteral("unspecified") : plan.executable)
        .arg(plan.commandReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.processLaunchOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.stdinPipeOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.rawFrameFeedOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.stderrCaptureOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.exitCodeValidationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.timeoutOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.cleanupOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.executionReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.contractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoFfmpegBinaryPlanSummary(
    const BatchRenderedVideoFfmpegBinaryPlan & plan)
{
    return QStringLiteral("ffmpeg-binary-source=%1 ffmpeg-binary-request=%2 ffmpeg-binary-resolved=%3 ffmpeg-binary-path-search-owned=%4 ffmpeg-binary-path-search-attempted=%5 ffmpeg-binary-found=%6 ffmpeg-binary-command-ready=%7 ffmpeg-binary-reason=%8")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.requestedExecutable.isEmpty() ? QStringLiteral("unspecified") : plan.requestedExecutable)
        .arg(plan.resolvedExecutable.isEmpty() ? QStringLiteral("unspecified") : plan.resolvedExecutable)
        .arg(plan.pathSearchOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.pathSearchAttempted ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.foundOnPath ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.commandExecutableReady ? QStringLiteral("true") : QStringLiteral("false"))
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

inline QString batchRenderedVideoOutputVerificationPlanSummary(
    const BatchRenderedVideoOutputVerificationPlan & plan)
{
    return QStringLiteral("output-verification-source=%1 output-verification-path=%2 output-verification-extension=%3 output-verification-path-ready=%4 output-verification-extension-match=%5 output-verification-file-exists-owned=%6 output-verification-nonempty-owned=%7 output-verification-probe-owned=%8 output-verification-codec-container-owned=%9 output-verification-frame-count-owned=%10 output-verification-receipt-hash-owned=%11 output-verification-execution-owned=%12 output-verification-contract-ready=%13 output-verification-reason=%14")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.expectedOutputPath.isEmpty() ? QStringLiteral("unspecified") : plan.expectedOutputPath)
        .arg(plan.expectedExtension.isEmpty() ? QStringLiteral("unspecified") : plan.expectedExtension)
        .arg(plan.outputPathReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extensionMatchesTarget ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.fileExistenceCheckOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.nonEmptyCheckOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.mediaProbeOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.codecContainerCheckOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.frameCountCheckOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.receiptOrHashOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.verificationExecutionOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.contractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoOutputVerificationExecutionPlanSummary(
    const BatchRenderedVideoOutputVerificationExecutionPlan & plan)
{
    return QStringLiteral("output-verification-exec-source=%1 output-verification-exec-path=%2 output-verification-exec-extension=%3 output-verification-exec-probe=%4 output-verification-exec-output-contract-ready=%5 output-verification-exec-ffmpeg-contract-ready=%6 output-verification-exec-file-exists-owned=%7 output-verification-exec-nonempty-owned=%8 output-verification-exec-probe-owned=%9 output-verification-exec-codec-container-owned=%10 output-verification-exec-frame-count-owned=%11 output-verification-exec-receipt-hash-owned=%12 output-verification-exec-ready=%13 output-verification-exec-contract-ready=%14 output-verification-exec-reason=%15")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.expectedOutputPath.isEmpty() ? QStringLiteral("unspecified") : plan.expectedOutputPath)
        .arg(plan.expectedExtension.isEmpty() ? QStringLiteral("unspecified") : plan.expectedExtension)
        .arg(plan.mediaProbeExecutable.isEmpty() ? QStringLiteral("unspecified") : plan.mediaProbeExecutable)
        .arg(plan.outputVerificationContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.ffmpegExecutionContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.fileExistenceCheckOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.nonEmptyCheckOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.mediaProbeExecutionOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.codecContainerValidationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.frameCountValidationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.receiptHashValidationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.verificationExecutionReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.contractReady ? QStringLiteral("true") : QStringLiteral("false"))
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
    return QStringLiteral("runner-processing-parity-ready=%1 runner-frame-processing-ready=%2 runner-audio-mux-ready=%3 runner-ffmpeg-execution-ready=%4 runner-output-verification-ready=%5 runner-headless-export-ready=%6 runner-ready=%7 runner-reason=%8")
        .arg(prerequisites.processingParityReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(prerequisites.frameProcessingReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(prerequisites.audioMuxReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(prerequisites.ffmpegExecutionReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(prerequisites.outputVerificationReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(prerequisites.headlessRunnerReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(prerequisites.ready ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(prerequisites.reason.isEmpty() ? QStringLiteral("none") : prerequisites.reason);
}

inline QString batchRenderedVideoJobPlanSummary(
    const BatchRenderedVideoJobPlan & plan)
{
    const QString blocker = batchRenderedVideoJobPlanFirstBlocker(plan);
    return QStringLiteral("%1 %2 %3 %4 %5 %6 %7 %8 %9 %10 %11 %12 %13 %14 %15 %16 %17 %18 %19 %20 %21 preflight-ready=%22 runnable=%23 first-blocker=%24")
        .arg(batchExportFormatRequestSummary(plan.request))
        .arg(batchRenderedVideoTargetSummary(plan.target))
        .arg(batchRenderedVideoEncoderPresetSummary(plan.encoderPreset))
        .arg(batchRenderedVideoFfmpegVideoPlanSummary(plan.ffmpegVideoPlan))
        .arg(batchRenderedVideoFfmpegFilterPlanSummary(plan.ffmpegFilterPlan))
        .arg(batchRenderedVideoOptionalFilterPlanSummary(
            plan.optionalFilterPlan))
        .arg(batchRenderedVideoSourceAudioPlanSummary(plan.sourceAudioPlan))
        .arg(batchRenderedVideoAudioMuxPrerequisitesPlanSummary(
            plan.audioMuxPrerequisitesPlan))
        .arg(batchRenderedVideoFfmpegAudioPlanSummary(plan.ffmpegAudioPlan))
        .arg(batchRenderedVideoFfmpegBinaryPlanSummary(plan.ffmpegBinaryPlan))
        .arg(batchRenderedVideoSourceMetadataSummary(plan))
        .arg(batchRenderedVideoRenderSettingsSummary(plan.renderSettings))
        .arg(batchRenderedVideoFfmpegFramePlanSummary(plan.ffmpegFramePlan))
        .arg(batchRenderedVideoReceiptApplicationPlanSummary(
            plan.receiptApplicationPlan))
        .arg(batchRenderedVideoFrameProcessingPlanSummary(
            plan.frameProcessingPlan))
        .arg(batchRenderedVideoFfmpegCommandPlanSummary(plan.ffmpegCommandPlan))
        .arg(batchRenderedVideoFfmpegExecutionPlanSummary(
            plan.ffmpegExecutionPlan))
        .arg(batchRenderedVideoOutputPlanSummary(plan.outputPlan))
        .arg(batchRenderedVideoOutputVerificationPlanSummary(
            plan.outputVerificationPlan))
        .arg(batchRenderedVideoOutputVerificationExecutionPlanSummary(
            plan.outputVerificationExecutionPlan))
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
