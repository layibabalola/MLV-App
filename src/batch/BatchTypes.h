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
    int sampleRate = 0;
    int channels = 0;
    int bitsPerSample = 0;
    qulonglong audioBytes = 0;
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

struct BatchRenderedVideoSourceAudioExtractionPlan
{
    QString source = QStringLiteral("source-audio-extraction-prerequisite-contract");
    QString clipPath;
    QString extractionFormat = QStringLiteral("wav-pcm-s16le");
    QString plannedAudioPath;
    QString reason;
    bool sourceAudioContractReady = false;
    bool sourceAudioKnown = false;
    bool sourceAudioPresent = false;
    bool sourceAudioDiscoveryOwned = false;
    bool extractionPathPlanned = false;
    bool extractionPathReady = false;
    bool sampleFormatReady = false;
    bool sampleRateReady = false;
    bool channelLayoutReady = false;
    bool extractionProcessOwned = false;
    bool tempFileOwned = false;
    bool cleanupOwned = false;
    bool extractionReady = false;
    bool videoOnlyFallbackReady = false;
    bool contractReady = false;
};

struct BatchRenderedVideoSourceAudioExtractionExecutionPlan
{
    QString source = QStringLiteral("source-audio-extraction-execution-contract");
    QString plannedAudioPath;
    QString extractionFormat = QStringLiteral("wav-pcm-s16le");
    QString reason;
    bool extractionPrerequisiteContractReady = false;
    bool sourceAudioKnown = false;
    bool sourceAudioPresent = false;
    bool extractionPathPlanned = false;
    bool extractionPathReady = false;
    bool sampleReadPlanned = false;
    bool wavWritePlanned = false;
    bool tempFileLifecyclePlanned = false;
    bool sampleReadOwned = false;
    bool wavHeaderWriteOwned = false;
    bool wavSampleWriteOwned = false;
    bool tempFileOpenOwned = false;
    bool tempFileFinalizeOwned = false;
    bool cleanupOwned = false;
    bool extractionProcessOwned = false;
    bool tempFileLifecycleReady = false;
    bool extractionExecutionReady = false;
    bool videoOnlyFallbackReady = false;
    bool contractReady = false;
};

struct BatchRenderedVideoFfmpegAudioInputHandoffPlan
{
    QString source = QStringLiteral("ffmpeg-audio-input-handoff-contract");
    QString plannedAudioPath;
    QString plannedInputArguments;
    QString activeAudioArguments = QStringLiteral("-an");
    QString reason;
    bool extractionExecutionContractReady = false;
    bool sourceAudioKnown = false;
    bool sourceAudioPresent = false;
    bool extractionPathPlanned = false;
    bool extractionPathReady = false;
    bool extractionExecutionReady = false;
    bool tempFileLifecycleReady = false;
    bool cleanupOwned = false;
    bool audioInputPlanned = false;
    bool inputArgumentsPlanned = false;
    bool extractionOutputOwned = false;
    bool audioInputOwnershipPlanned = false;
    bool audioInputArgumentHandoffPlanned = false;
    bool audioInputOwned = false;
    bool audioInputArgumentHandoffOwned = false;
    bool audioInputHandoffReady = false;
    bool videoOnlyFallbackReady = false;
    bool contractReady = false;
};

struct BatchRenderedVideoFfmpegAudioInputPlan
{
    QString source = QStringLiteral("ffmpeg-audio-input-contract");
    QString plannedAudioPath;
    QString plannedInputArguments;
    QString activeAudioArguments = QStringLiteral("-an");
    QString reason;
    bool sourceAudioExtractionContractReady = false;
    bool sourceAudioKnown = false;
    bool sourceAudioPresent = false;
    bool extractionPathPlanned = false;
    bool extractionPathReady = false;
    bool extractionReady = false;
    bool tempFileOwned = false;
    bool cleanupOwned = false;
    bool audioInputPlanned = false;
    bool audioInputOwned = false;
    bool audioInputReady = false;
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
    bool sourceAudioExtractionContractReady = false;
    bool sourceAudioInputContractReady = false;
    bool sourceAudioDiscoveryOwned = false;
    bool sourceAudioExtractionOwned = false;
    bool audioInputOwned = false;
    bool audioMuxOwned = false;
    bool audioSyncValidationOwned = false;
    bool videoOnlyFallbackReady = false;
    bool muxReady = false;
    bool contractReady = false;
};

struct BatchRenderedVideoAudioMuxExecutionPlan
{
    QString source = QStringLiteral("audio-mux-execution-contract");
    QString inputState =
        QStringLiteral("ffmpeg-audio-input-or-video-only-fallback");
    QString outputState =
        QStringLiteral("synced-rendered-audio-or-video-only-fallback");
    QString reason;
    bool sourceAudioContractReady = false;
    bool audioInputHandoffContractReady = false;
    bool audioMuxPrerequisitesContractReady = false;
    bool sourceAudioKnown = false;
    bool sourceAudioPresent = false;
    bool sourceAudioDiscoveryOwned = false;
    bool sourceAudioExtractionOwned = false;
    bool audioInputHandoffReady = false;
    bool tempAudioInputOwned = false;
    bool audioMuxPlanned = false;
    bool audioMuxArgumentHandoffPlanned = false;
    bool audioSyncValidationPlanned = false;
    bool audioMuxOwned = false;
    bool audioMuxArgumentHandoffOwned = false;
    bool audioSyncValidationOwned = false;
    bool audioMuxReady = false;
    bool audioSyncValidationReady = false;
    bool muxExecutionReady = false;
    bool videoOnlyFallbackReady = false;
    bool contractReady = false;
};

struct BatchRenderedVideoFfmpegAudioPlan
{
    QString source = QStringLiteral("video-only-to-mux-transition-contract");
    QString audioArguments = QStringLiteral("-an");
    QString muxTransitionArguments;
    QString reason;
    bool videoOnlyCommandReady = false;
    bool sourceAudioDiscoveryOwned = false;
    bool sourceAudioExtractionOwned = false;
    bool audioInputContractReady = false;
    bool audioInputOwned = false;
    bool audioMuxExecutionContractReady = false;
    bool audioMuxPlanned = false;
    bool audioMuxArgumentHandoffPlanned = false;
    bool audioSyncValidationPlanned = false;
    bool audioMuxOwned = false;
    bool audioMuxArgumentHandoffOwned = false;
    bool audioSyncOwned = false;
    bool audioMuxExecutionReady = false;
    bool muxedAudioCommandPlanned = false;
    bool muxedAudioCommandReady = false;
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
    QString audioTransitionSource;
    QString audioTransitionArguments;
    bool audioContractReady = false;
    bool audioMuxExecutionContractReady = false;
    bool audioMuxPlanned = false;
    bool audioMuxArgumentHandoffPlanned = false;
    bool audioSyncValidationPlanned = false;
    bool audioMuxExecutionReady = false;
    bool muxedAudioCommandPlanned = false;
    bool muxedAudioCommandReady = false;
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
    BatchRenderedVideoSourceAudioExtractionPlan sourceAudioExtractionPlan;
    BatchRenderedVideoSourceAudioExtractionExecutionPlan sourceAudioExtractionExecutionPlan;
    BatchRenderedVideoFfmpegAudioInputHandoffPlan ffmpegAudioInputHandoffPlan;
    BatchRenderedVideoFfmpegAudioInputPlan ffmpegAudioInputPlan;
    BatchRenderedVideoAudioMuxPrerequisitesPlan audioMuxPrerequisitesPlan;
    BatchRenderedVideoAudioMuxExecutionPlan audioMuxExecutionPlan;
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
    bool sourceAudioExtractionContractReady = false;
    bool sourceAudioExtractionExecutionContractReady = false;
    bool ffmpegAudioInputHandoffContractReady = false;
    bool ffmpegAudioInputContractReady = false;
    bool audioMuxPrerequisitesContractReady = false;
    bool audioMuxExecutionContractReady = false;
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

inline BatchRenderedVideoSourceAudioPlan
batchRenderedVideoSourceAudioPlanFromDiscoveredAudio(
    const QString & clipPath,
    bool sourceAudioPresent,
    int channels,
    int sampleRate,
    int bitsPerSample,
    qulonglong audioBytes)
{
    BatchRenderedVideoSourceAudioPlan plan =
        batchRenderedVideoSourceAudioPlanForCurrentBuild(clipPath);
    plan.source = QStringLiteral("open-mlv-audio-metadata");
    plan.audioState = sourceAudioPresent
        ? QStringLiteral("present")
        : QStringLiteral("absent");
    plan.discoveryOwned = true;
    plan.discoveryAttempted = true;
    plan.sourceAudioKnown = true;
    plan.sourceAudioPresent = sourceAudioPresent;
    plan.channels = sourceAudioPresent ? channels : 0;
    plan.sampleRate = sourceAudioPresent ? sampleRate : 0;
    plan.bitsPerSample = sourceAudioPresent ? bitsPerSample : 0;
    plan.audioBytes = sourceAudioPresent ? audioBytes : 0;
    plan.videoOnlyFallbackReady = true;

    if( sourceAudioPresent
     && (plan.channels <= 0
      || plan.sampleRate <= 0
      || plan.bitsPerSample <= 0) )
    {
        plan.reason = QStringLiteral("rendered source audio metadata invalid");
        plan.contractReady = false;
        return plan;
    }

    plan.contractReady = plan.sourceAudioKnown
                      && plan.videoOnlyFallbackReady;
    if( !plan.contractReady )
    {
        plan.reason =
            QStringLiteral("rendered source audio discovery contract unavailable");
    }
    return plan;
}

inline QString batchRenderedVideoSourceAudioExtractionPathFromOutputPath(
    const QString & outputPath)
{
    const QString cleaned = QDir::cleanPath(outputPath.trimmed());
    if( cleaned.isEmpty() ) return QString();

    const QFileInfo outputInfo(cleaned);
    const QString baseName = outputInfo.completeBaseName().isEmpty()
        ? outputInfo.fileName()
        : outputInfo.completeBaseName();
    if( baseName.isEmpty() ) return QString();

    return QDir::cleanPath(
        outputInfo.dir().filePath(baseName + QStringLiteral(".source-audio.wav")));
}

inline BatchRenderedVideoSourceAudioExtractionPlan
batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
    const BatchRenderedVideoSourceAudioPlan & sourceAudioPlan,
    const QString & plannedAudioPath)
{
    BatchRenderedVideoSourceAudioExtractionPlan plan;
    plan.clipPath = sourceAudioPlan.clipPath;
    plan.plannedAudioPath = QDir::cleanPath(plannedAudioPath.trimmed());
    plan.sourceAudioContractReady = sourceAudioPlan.contractReady;
    plan.sourceAudioKnown = sourceAudioPlan.sourceAudioKnown;
    plan.sourceAudioPresent = sourceAudioPlan.sourceAudioPresent;
    plan.sourceAudioDiscoveryOwned = sourceAudioPlan.discoveryOwned;
    plan.videoOnlyFallbackReady = sourceAudioPlan.videoOnlyFallbackReady;
    plan.extractionPathPlanned =
        plan.sourceAudioKnown && plan.sourceAudioPresent;
    plan.extractionPathReady =
        !plan.extractionPathPlanned || !plan.plannedAudioPath.isEmpty();
    plan.sampleFormatReady =
        !plan.sourceAudioPresent || sourceAudioPlan.bitsPerSample > 0;
    plan.sampleRateReady =
        !plan.sourceAudioPresent || sourceAudioPlan.sampleRate > 0;
    plan.channelLayoutReady =
        !plan.sourceAudioPresent || sourceAudioPlan.channels > 0;

    if( !sourceAudioPlan.contractReady )
    {
        plan.reason = sourceAudioPlan.reason.isEmpty()
            ? QStringLiteral("rendered source audio contract unavailable")
            : sourceAudioPlan.reason;
        return plan;
    }

    plan.contractReady = plan.sourceAudioContractReady
                      && plan.videoOnlyFallbackReady
                      && plan.extractionPathReady
                      && plan.sampleFormatReady
                      && plan.sampleRateReady
                      && plan.channelLayoutReady;
    plan.extractionReady = plan.extractionProcessOwned
                        && plan.tempFileOwned
                        && plan.cleanupOwned;
    if( !plan.contractReady )
    {
        plan.reason =
            QStringLiteral("rendered source audio extraction prerequisite contract unavailable");
    }
    return plan;
}

inline BatchRenderedVideoSourceAudioExtractionPlan
batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
    const BatchRenderedVideoSourceAudioPlan & sourceAudioPlan)
{
    return batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
        sourceAudioPlan,
        QString());
}

inline BatchRenderedVideoSourceAudioExtractionExecutionPlan
batchRenderedVideoSourceAudioExtractionExecutionPlanFromExtraction(
    const BatchRenderedVideoSourceAudioExtractionPlan & extractionPlan)
{
    BatchRenderedVideoSourceAudioExtractionExecutionPlan plan;
    plan.plannedAudioPath = extractionPlan.plannedAudioPath;
    plan.extractionFormat = extractionPlan.extractionFormat;
    plan.extractionPrerequisiteContractReady = extractionPlan.contractReady;
    plan.sourceAudioKnown = extractionPlan.sourceAudioKnown;
    plan.sourceAudioPresent = extractionPlan.sourceAudioPresent;
    plan.extractionPathPlanned = extractionPlan.extractionPathPlanned;
    plan.extractionPathReady = extractionPlan.extractionPathReady;
    plan.videoOnlyFallbackReady = extractionPlan.videoOnlyFallbackReady;
    plan.sampleReadPlanned = plan.sourceAudioKnown
                          && plan.sourceAudioPresent;
    plan.wavWritePlanned = plan.sampleReadPlanned;
    plan.tempFileLifecyclePlanned = plan.sampleReadPlanned
                                 && plan.extractionPathReady;

    if( !extractionPlan.contractReady )
    {
        plan.reason = extractionPlan.reason.isEmpty()
            ? QStringLiteral("rendered source audio extraction prerequisite contract unavailable")
            : extractionPlan.reason;
        return plan;
    }

    plan.contractReady = plan.extractionPrerequisiteContractReady
                      && plan.videoOnlyFallbackReady
                      && (!plan.sampleReadPlanned
                       || (plan.wavWritePlanned
                        && plan.tempFileLifecyclePlanned));
    plan.tempFileLifecycleReady = plan.tempFileOpenOwned
                               && plan.tempFileFinalizeOwned
                               && plan.cleanupOwned;
    plan.extractionExecutionReady = plan.extractionProcessOwned
                                 && plan.sampleReadOwned
                                 && plan.wavHeaderWriteOwned
                                 && plan.wavSampleWriteOwned
                                 && plan.tempFileLifecycleReady;
    if( !plan.contractReady )
    {
        plan.reason =
            QStringLiteral("rendered source audio extraction execution contract unavailable");
    }
    return plan;
}

inline BatchRenderedVideoFfmpegAudioInputHandoffPlan
batchRenderedVideoFfmpegAudioInputHandoffPlanFromExtractionExecution(
    const BatchRenderedVideoSourceAudioExtractionExecutionPlan & executionPlan)
{
    BatchRenderedVideoFfmpegAudioInputHandoffPlan plan;
    plan.plannedAudioPath = executionPlan.plannedAudioPath;
    plan.extractionExecutionContractReady = executionPlan.contractReady;
    plan.sourceAudioKnown = executionPlan.sourceAudioKnown;
    plan.sourceAudioPresent = executionPlan.sourceAudioPresent;
    plan.extractionPathPlanned = executionPlan.extractionPathPlanned;
    plan.extractionPathReady = executionPlan.extractionPathReady;
    plan.extractionExecutionReady = executionPlan.extractionExecutionReady;
    plan.tempFileLifecycleReady = executionPlan.tempFileLifecycleReady;
    plan.cleanupOwned = executionPlan.cleanupOwned;
    plan.videoOnlyFallbackReady = executionPlan.videoOnlyFallbackReady;
    plan.audioInputPlanned = plan.sourceAudioKnown
                          && plan.sourceAudioPresent;
    if( plan.audioInputPlanned && !plan.plannedAudioPath.isEmpty() )
    {
        plan.plannedInputArguments =
            QStringLiteral("-i \"%1\"").arg(plan.plannedAudioPath);
    }
    plan.inputArgumentsPlanned = plan.audioInputPlanned
                              && !plan.plannedInputArguments.isEmpty();
    plan.audioInputOwnershipPlanned = plan.inputArgumentsPlanned;
    plan.audioInputArgumentHandoffPlanned = plan.inputArgumentsPlanned;

    if( !executionPlan.contractReady )
    {
        plan.reason = executionPlan.reason.isEmpty()
            ? QStringLiteral("rendered source audio extraction execution contract unavailable")
            : executionPlan.reason;
        return plan;
    }

    plan.contractReady = plan.extractionExecutionContractReady
                      && plan.videoOnlyFallbackReady
                      && (!plan.audioInputPlanned
                       || (plan.inputArgumentsPlanned
                        && plan.audioInputOwnershipPlanned
                        && plan.audioInputArgumentHandoffPlanned));
    plan.audioInputHandoffReady = plan.extractionOutputOwned
                               && plan.audioInputOwned
                               && plan.audioInputArgumentHandoffOwned
                               && plan.extractionExecutionReady
                               && plan.tempFileLifecycleReady;
    if( !plan.contractReady )
    {
        plan.reason =
            QStringLiteral("rendered ffmpeg audio input handoff contract unavailable");
    }
    return plan;
}

inline BatchRenderedVideoFfmpegAudioInputPlan
batchRenderedVideoFfmpegAudioInputPlanFromHandoff(
    const BatchRenderedVideoSourceAudioExtractionPlan & extractionPlan,
    const BatchRenderedVideoFfmpegAudioInputHandoffPlan & handoffPlan)
{
    BatchRenderedVideoFfmpegAudioInputPlan plan;
    plan.plannedAudioPath = handoffPlan.plannedAudioPath;
    plan.plannedInputArguments = handoffPlan.plannedInputArguments;
    plan.activeAudioArguments = handoffPlan.activeAudioArguments;
    plan.sourceAudioExtractionContractReady = extractionPlan.contractReady;
    plan.sourceAudioKnown = handoffPlan.sourceAudioKnown;
    plan.sourceAudioPresent = handoffPlan.sourceAudioPresent;
    plan.extractionPathPlanned = handoffPlan.extractionPathPlanned;
    plan.extractionPathReady = handoffPlan.extractionPathReady;
    plan.extractionReady = handoffPlan.extractionExecutionReady;
    plan.tempFileOwned = handoffPlan.tempFileLifecycleReady;
    plan.cleanupOwned = handoffPlan.cleanupOwned;
    plan.videoOnlyFallbackReady = handoffPlan.videoOnlyFallbackReady;
    plan.audioInputPlanned = handoffPlan.audioInputPlanned;
    plan.audioInputOwned = handoffPlan.audioInputOwned
                        && handoffPlan.audioInputArgumentHandoffOwned;
    plan.audioInputReady = handoffPlan.audioInputHandoffReady;

    if( !extractionPlan.contractReady )
    {
        plan.reason = extractionPlan.reason.isEmpty()
            ? QStringLiteral("rendered source audio extraction prerequisite contract unavailable")
            : extractionPlan.reason;
        return plan;
    }
    if( !handoffPlan.contractReady )
    {
        plan.reason = handoffPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg audio input handoff contract unavailable")
            : handoffPlan.reason;
        return plan;
    }

    plan.contractReady = plan.sourceAudioExtractionContractReady
                      && handoffPlan.contractReady
                      && plan.videoOnlyFallbackReady
                      && (!plan.audioInputPlanned
                       || !plan.plannedInputArguments.isEmpty());
    if( !plan.contractReady )
    {
        plan.reason =
            QStringLiteral("rendered ffmpeg audio input contract unavailable");
    }
    return plan;
}

inline BatchRenderedVideoFfmpegAudioInputPlan
batchRenderedVideoFfmpegAudioInputPlanFromExtraction(
    const BatchRenderedVideoSourceAudioExtractionPlan & extractionPlan,
    const BatchRenderedVideoSourceAudioExtractionExecutionPlan & executionPlan)
{
    return batchRenderedVideoFfmpegAudioInputPlanFromHandoff(
        extractionPlan,
        batchRenderedVideoFfmpegAudioInputHandoffPlanFromExtractionExecution(
            executionPlan));
}

inline BatchRenderedVideoFfmpegAudioInputPlan
batchRenderedVideoFfmpegAudioInputPlanFromExtraction(
    const BatchRenderedVideoSourceAudioExtractionPlan & extractionPlan)
{
    return batchRenderedVideoFfmpegAudioInputPlanFromExtraction(
        extractionPlan,
        batchRenderedVideoSourceAudioExtractionExecutionPlanFromExtraction(
            extractionPlan));
}

inline BatchRenderedVideoAudioMuxPrerequisitesPlan
batchRenderedVideoAudioMuxPrerequisitesPlanFromSourceAudio(
    const BatchRenderedVideoSourceAudioPlan & sourceAudioPlan,
    const BatchRenderedVideoSourceAudioExtractionPlan & extractionPlan,
    const BatchRenderedVideoFfmpegAudioInputPlan & audioInputPlan)
{
    BatchRenderedVideoAudioMuxPrerequisitesPlan plan;
    plan.sourceAudioContractReady = sourceAudioPlan.contractReady;
    plan.sourceAudioExtractionContractReady = extractionPlan.contractReady;
    plan.sourceAudioInputContractReady = audioInputPlan.contractReady;
    plan.sourceAudioDiscoveryOwned = sourceAudioPlan.discoveryOwned;
    plan.sourceAudioExtractionOwned = extractionPlan.extractionProcessOwned;
    plan.audioInputOwned = audioInputPlan.audioInputOwned;
    plan.audioSyncValidationOwned = sourceAudioPlan.syncValidationOwned;
    plan.videoOnlyFallbackReady = sourceAudioPlan.videoOnlyFallbackReady;

    if( !sourceAudioPlan.contractReady )
    {
        plan.reason = sourceAudioPlan.reason.isEmpty()
            ? QStringLiteral("rendered source audio contract unavailable")
            : sourceAudioPlan.reason;
        return plan;
    }
    if( !extractionPlan.contractReady )
    {
        plan.reason = extractionPlan.reason.isEmpty()
            ? QStringLiteral("rendered source audio extraction prerequisite contract unavailable")
            : extractionPlan.reason;
        return plan;
    }
    if( !audioInputPlan.contractReady )
    {
        plan.reason = audioInputPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg audio input contract unavailable")
            : audioInputPlan.reason;
        return plan;
    }

    plan.contractReady = plan.sourceAudioContractReady
                      && plan.sourceAudioExtractionContractReady
                      && plan.sourceAudioInputContractReady
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

inline BatchRenderedVideoAudioMuxPrerequisitesPlan
batchRenderedVideoAudioMuxPrerequisitesPlanFromSourceAudio(
    const BatchRenderedVideoSourceAudioPlan & sourceAudioPlan,
    const BatchRenderedVideoSourceAudioExtractionPlan & extractionPlan)
{
    return batchRenderedVideoAudioMuxPrerequisitesPlanFromSourceAudio(
        sourceAudioPlan,
        extractionPlan,
        batchRenderedVideoFfmpegAudioInputPlanFromExtraction(
            extractionPlan));
}

inline BatchRenderedVideoAudioMuxPrerequisitesPlan
batchRenderedVideoAudioMuxPrerequisitesPlanFromSourceAudio(
    const BatchRenderedVideoSourceAudioPlan & sourceAudioPlan)
{
    return batchRenderedVideoAudioMuxPrerequisitesPlanFromSourceAudio(
        sourceAudioPlan,
        batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
            sourceAudioPlan));
}

inline BatchRenderedVideoAudioMuxExecutionPlan
batchRenderedVideoAudioMuxExecutionPlanFromContracts(
    const BatchRenderedVideoSourceAudioPlan & sourceAudioPlan,
    const BatchRenderedVideoFfmpegAudioInputHandoffPlan & handoffPlan,
    const BatchRenderedVideoAudioMuxPrerequisitesPlan & prerequisitesPlan)
{
    BatchRenderedVideoAudioMuxExecutionPlan plan;
    plan.sourceAudioContractReady = sourceAudioPlan.contractReady;
    plan.audioInputHandoffContractReady = handoffPlan.contractReady;
    plan.audioMuxPrerequisitesContractReady = prerequisitesPlan.contractReady;
    plan.sourceAudioKnown = sourceAudioPlan.sourceAudioKnown;
    plan.sourceAudioPresent = sourceAudioPlan.sourceAudioPresent;
    plan.sourceAudioDiscoveryOwned =
        prerequisitesPlan.sourceAudioDiscoveryOwned;
    plan.sourceAudioExtractionOwned =
        prerequisitesPlan.sourceAudioExtractionOwned;
    plan.audioInputHandoffReady = handoffPlan.audioInputHandoffReady;
    plan.tempAudioInputOwned = handoffPlan.audioInputOwned
                            && handoffPlan.audioInputArgumentHandoffOwned;
    plan.videoOnlyFallbackReady = prerequisitesPlan.videoOnlyFallbackReady;
    plan.audioMuxPlanned = plan.sourceAudioKnown && plan.sourceAudioPresent;
    plan.audioMuxArgumentHandoffPlanned =
        plan.audioMuxPlanned && handoffPlan.inputArgumentsPlanned;
    plan.audioSyncValidationPlanned = plan.audioMuxPlanned;

    if( !sourceAudioPlan.contractReady )
    {
        plan.reason = sourceAudioPlan.reason.isEmpty()
            ? QStringLiteral("rendered source audio contract unavailable")
            : sourceAudioPlan.reason;
        return plan;
    }
    if( !handoffPlan.contractReady )
    {
        plan.reason = handoffPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg audio input handoff contract unavailable")
            : handoffPlan.reason;
        return plan;
    }
    if( !prerequisitesPlan.contractReady )
    {
        plan.reason = prerequisitesPlan.reason.isEmpty()
            ? QStringLiteral("rendered audio mux prerequisite contract unavailable")
            : prerequisitesPlan.reason;
        return plan;
    }

    plan.audioMuxReady = plan.audioInputHandoffReady
                      && plan.tempAudioInputOwned
                      && plan.audioMuxOwned
                      && plan.audioMuxArgumentHandoffOwned;
    plan.audioSyncValidationReady = plan.audioMuxReady
                                 && plan.audioSyncValidationOwned;
    plan.muxExecutionReady = plan.audioMuxReady
                           && plan.audioSyncValidationReady;
    plan.contractReady = plan.sourceAudioContractReady
                      && plan.audioInputHandoffContractReady
                      && plan.audioMuxPrerequisitesContractReady
                      && plan.videoOnlyFallbackReady
                      && (!plan.audioMuxPlanned
                       || (plan.audioMuxArgumentHandoffPlanned
                        && plan.audioSyncValidationPlanned));
    if( !plan.contractReady )
    {
        plan.reason =
            QStringLiteral("rendered audio mux execution contract unavailable");
    }
    return plan;
}

inline BatchRenderedVideoFfmpegAudioPlan
batchRenderedVideoFfmpegAudioPlanForCurrentBuild(
    const BatchRenderedVideoSourceAudioPlan & sourceAudioPlan,
    const BatchRenderedVideoAudioMuxPrerequisitesPlan & audioMuxPlan,
    const BatchRenderedVideoAudioMuxExecutionPlan & audioMuxExecutionPlan)
{
    BatchRenderedVideoFfmpegAudioPlan plan;
    plan.sourceAudioDiscoveryOwned =
        audioMuxExecutionPlan.sourceAudioDiscoveryOwned;
    plan.sourceAudioExtractionOwned =
        audioMuxExecutionPlan.sourceAudioExtractionOwned;
    plan.audioInputContractReady = audioMuxPlan.sourceAudioInputContractReady;
    plan.audioInputOwned = audioMuxPlan.audioInputOwned;
    plan.audioMuxExecutionContractReady =
        audioMuxExecutionPlan.contractReady;
    plan.audioMuxPlanned = audioMuxExecutionPlan.audioMuxPlanned;
    plan.audioMuxArgumentHandoffPlanned =
        audioMuxExecutionPlan.audioMuxArgumentHandoffPlanned;
    plan.audioSyncValidationPlanned =
        audioMuxExecutionPlan.audioSyncValidationPlanned;
    plan.audioMuxOwned = audioMuxExecutionPlan.audioMuxOwned;
    plan.audioMuxArgumentHandoffOwned =
        audioMuxExecutionPlan.audioMuxArgumentHandoffOwned;
    plan.audioSyncOwned = audioMuxExecutionPlan.audioSyncValidationOwned;
    plan.audioMuxExecutionReady = audioMuxExecutionPlan.muxExecutionReady;
    plan.muxedAudioCommandPlanned = plan.audioMuxPlanned
                                  && plan.audioMuxArgumentHandoffPlanned
                                  && plan.audioSyncValidationPlanned;
    if( plan.muxedAudioCommandPlanned )
    {
        plan.muxTransitionArguments =
            QStringLiteral("deferred-until-audio-mux-execution-owned");
    }
    plan.muxedAudioCommandReady = plan.muxedAudioCommandPlanned
                               && plan.audioMuxExecutionReady
                               && plan.audioInputOwned
                               && plan.audioMuxOwned
                               && plan.audioMuxArgumentHandoffOwned
                               && plan.audioSyncOwned;
    plan.videoOnlyCommandReady = !plan.audioArguments.isEmpty()
                              && audioMuxPlan.videoOnlyFallbackReady
                              && audioMuxExecutionPlan.videoOnlyFallbackReady;

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
    if( !audioMuxExecutionPlan.contractReady )
    {
        plan.reason = audioMuxExecutionPlan.reason.isEmpty()
            ? QStringLiteral("rendered audio mux execution contract unavailable")
            : audioMuxExecutionPlan.reason;
        return plan;
    }

    plan.contractReady = plan.videoOnlyCommandReady
                      && audioMuxPlan.contractReady
                      && audioMuxExecutionPlan.contractReady
                      && (!plan.audioMuxPlanned
                       || plan.muxedAudioCommandPlanned);
    if( !plan.contractReady )
        plan.reason = QStringLiteral("rendered ffmpeg audio contract unavailable");
    return plan;
}

inline BatchRenderedVideoFfmpegAudioPlan
batchRenderedVideoFfmpegAudioPlanForCurrentBuild(
    const BatchRenderedVideoSourceAudioPlan & sourceAudioPlan,
    const BatchRenderedVideoAudioMuxPrerequisitesPlan & audioMuxPlan)
{
    const BatchRenderedVideoSourceAudioExtractionPlan extractionPlan =
        batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
            sourceAudioPlan);
    const BatchRenderedVideoSourceAudioExtractionExecutionPlan executionPlan =
        batchRenderedVideoSourceAudioExtractionExecutionPlanFromExtraction(
            extractionPlan);
    const BatchRenderedVideoFfmpegAudioInputHandoffPlan handoffPlan =
        batchRenderedVideoFfmpegAudioInputHandoffPlanFromExtractionExecution(
            executionPlan);
    return batchRenderedVideoFfmpegAudioPlanForCurrentBuild(
        sourceAudioPlan,
        audioMuxPlan,
        batchRenderedVideoAudioMuxExecutionPlanFromContracts(
            sourceAudioPlan,
            handoffPlan,
            audioMuxPlan));
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
    plan.audioTransitionSource = audioPlan.source;
    plan.audioTransitionArguments = audioPlan.muxTransitionArguments;
    plan.audioContractReady = audioPlan.contractReady;
    plan.audioMuxExecutionContractReady = audioPlan.audioMuxExecutionContractReady;
    plan.audioMuxPlanned = audioPlan.audioMuxPlanned;
    plan.audioMuxArgumentHandoffPlanned =
        audioPlan.audioMuxArgumentHandoffPlanned;
    plan.audioSyncValidationPlanned = audioPlan.audioSyncValidationPlanned;
    plan.audioMuxExecutionReady = audioPlan.audioMuxExecutionReady;
    plan.muxedAudioCommandPlanned = audioPlan.muxedAudioCommandPlanned;
    plan.muxedAudioCommandReady = audioPlan.muxedAudioCommandReady;
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
        plan.sourceAudioPlan =
            batchRenderedVideoSourceAudioPlanForCurrentBuild(inputPath);
        plan.sourceAudioExtractionPlan =
            batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
                plan.sourceAudioPlan,
                batchRenderedVideoSourceAudioExtractionPathFromOutputPath(
                    plan.outputPlan.outputPath));
        plan.sourceAudioExtractionExecutionPlan =
            batchRenderedVideoSourceAudioExtractionExecutionPlanFromExtraction(
                plan.sourceAudioExtractionPlan);
        plan.ffmpegAudioInputHandoffPlan =
            batchRenderedVideoFfmpegAudioInputHandoffPlanFromExtractionExecution(
                plan.sourceAudioExtractionExecutionPlan);
        plan.ffmpegAudioInputPlan =
            batchRenderedVideoFfmpegAudioInputPlanFromHandoff(
                plan.sourceAudioExtractionPlan,
                plan.ffmpegAudioInputHandoffPlan);
        plan.audioMuxPrerequisitesPlan =
            batchRenderedVideoAudioMuxPrerequisitesPlanFromSourceAudio(
                plan.sourceAudioPlan,
                plan.sourceAudioExtractionPlan,
                plan.ffmpegAudioInputPlan);
        plan.audioMuxExecutionPlan =
            batchRenderedVideoAudioMuxExecutionPlanFromContracts(
                plan.sourceAudioPlan,
                plan.ffmpegAudioInputHandoffPlan,
                plan.audioMuxPrerequisitesPlan);
        plan.ffmpegAudioPlan =
            batchRenderedVideoFfmpegAudioPlanForCurrentBuild(
                plan.sourceAudioPlan,
                plan.audioMuxPrerequisitesPlan,
                plan.audioMuxExecutionPlan);
        plan.receiptApplicationPlan.reason =
            QStringLiteral("rendered source metadata unavailable");
    }
    else
    {
        plan.outputPlan.reason = QStringLiteral("not a rendered-video request");
        plan.sourceAudioPlan.reason =
            QStringLiteral("not a rendered-video request");
        plan.sourceAudioExtractionPlan.reason =
            QStringLiteral("not a rendered-video request");
        plan.sourceAudioExtractionExecutionPlan.reason =
            QStringLiteral("not a rendered-video request");
        plan.ffmpegAudioInputHandoffPlan.reason =
            QStringLiteral("not a rendered-video request");
        plan.ffmpegAudioInputPlan.reason =
            QStringLiteral("not a rendered-video request");
        plan.audioMuxPrerequisitesPlan.reason =
            QStringLiteral("not a rendered-video request");
        plan.audioMuxExecutionPlan.reason =
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
    plan.sourceAudioExtractionContractReady =
        plan.sourceAudioExtractionPlan.contractReady;
    plan.sourceAudioExtractionExecutionContractReady =
        plan.sourceAudioExtractionExecutionPlan.contractReady;
    plan.ffmpegAudioInputHandoffContractReady =
        plan.ffmpegAudioInputHandoffPlan.contractReady;
    plan.ffmpegAudioInputContractReady =
        plan.ffmpegAudioInputPlan.contractReady;
    plan.audioMuxPrerequisitesContractReady =
        plan.audioMuxPrerequisitesPlan.contractReady;
    plan.audioMuxExecutionContractReady =
        plan.audioMuxExecutionPlan.contractReady;
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
                       && plan.sourceAudioExtractionContractReady
                       && plan.sourceAudioExtractionExecutionContractReady
                       && plan.ffmpegAudioInputHandoffContractReady
                       && plan.ffmpegAudioInputContractReady
                       && plan.audioMuxPrerequisitesContractReady
                       && plan.audioMuxExecutionContractReady
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

inline BatchRenderedVideoJobPlan batchRenderedVideoJobPlanWithSourceAudio(
    const BatchRenderedVideoJobPlan & preflightPlan,
    const BatchRenderedVideoSourceAudioPlan & sourceAudioPlan)
{
    BatchRenderedVideoJobPlan plan = preflightPlan;
    plan.sourceAudioPlan = sourceAudioPlan;
    plan.sourceAudioExtractionPlan =
        batchRenderedVideoSourceAudioExtractionPlanFromSourceAudio(
            plan.sourceAudioPlan,
            batchRenderedVideoSourceAudioExtractionPathFromOutputPath(
                plan.outputPlan.outputPath));
    plan.sourceAudioExtractionExecutionPlan =
        batchRenderedVideoSourceAudioExtractionExecutionPlanFromExtraction(
            plan.sourceAudioExtractionPlan);
    plan.ffmpegAudioInputHandoffPlan =
        batchRenderedVideoFfmpegAudioInputHandoffPlanFromExtractionExecution(
            plan.sourceAudioExtractionExecutionPlan);
    plan.ffmpegAudioInputPlan =
        batchRenderedVideoFfmpegAudioInputPlanFromHandoff(
            plan.sourceAudioExtractionPlan,
            plan.ffmpegAudioInputHandoffPlan);
    plan.audioMuxPrerequisitesPlan =
        batchRenderedVideoAudioMuxPrerequisitesPlanFromSourceAudio(
            plan.sourceAudioPlan,
            plan.sourceAudioExtractionPlan,
            plan.ffmpegAudioInputPlan);
    plan.audioMuxExecutionPlan =
        batchRenderedVideoAudioMuxExecutionPlanFromContracts(
            plan.sourceAudioPlan,
            plan.ffmpegAudioInputHandoffPlan,
            plan.audioMuxPrerequisitesPlan);
    plan.ffmpegAudioPlan =
        batchRenderedVideoFfmpegAudioPlanForCurrentBuild(
            plan.sourceAudioPlan,
            plan.audioMuxPrerequisitesPlan,
            plan.audioMuxExecutionPlan);
    plan.sourceAudioContractReady =
        plan.sourceAudioPlan.contractReady;
    plan.sourceAudioExtractionContractReady =
        plan.sourceAudioExtractionPlan.contractReady;
    plan.sourceAudioExtractionExecutionContractReady =
        plan.sourceAudioExtractionExecutionPlan.contractReady;
    plan.ffmpegAudioInputHandoffContractReady =
        plan.ffmpegAudioInputHandoffPlan.contractReady;
    plan.ffmpegAudioInputContractReady =
        plan.ffmpegAudioInputPlan.contractReady;
    plan.audioMuxPrerequisitesContractReady =
        plan.audioMuxPrerequisitesPlan.contractReady;
    plan.audioMuxExecutionContractReady =
        plan.audioMuxExecutionPlan.contractReady;
    plan.ffmpegAudioContractReady =
        plan.ffmpegAudioPlan.contractReady;

    if( plan.metadataAttempted )
    {
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
    }

    plan.preflightReady = plan.requestValid
                       && plan.targetReady
                       && plan.encoderReady
                       && plan.ffmpegVideoReady
                       && plan.ffmpegFilterReady
                       && plan.optionalFilterContractReady
                       && plan.sourceAudioContractReady
                       && plan.sourceAudioExtractionContractReady
                       && plan.sourceAudioExtractionExecutionContractReady
                       && plan.ffmpegAudioInputHandoffContractReady
                       && plan.ffmpegAudioInputContractReady
                       && plan.audioMuxPrerequisitesContractReady
                       && plan.audioMuxExecutionContractReady
                       && plan.ffmpegAudioContractReady
                       && plan.ffmpegBinaryCommandReady
                       && plan.renderSettings.ready
                       && plan.outputReady
                       && plan.outputVerificationContractReady;
    if( plan.metadataAttempted )
    {
        plan.preflightReady = plan.preflightReady
                           && plan.metadataReady
                           && plan.ffmpegFrameReady
                           && plan.receiptApplicationContractReady
                           && plan.frameProcessingContractReady
                           && plan.ffmpegCommandReady
                           && plan.ffmpegExecutionContractReady
                           && plan.outputVerificationExecutionContractReady;
    }
    plan.runnable = plan.preflightReady && plan.runnerPrerequisites.ready;
    return plan;
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
                       && plan.sourceAudioExtractionContractReady
                       && plan.sourceAudioExtractionExecutionContractReady
                       && plan.ffmpegAudioInputHandoffContractReady
                       && plan.ffmpegAudioInputContractReady
                       && plan.audioMuxPrerequisitesContractReady
                       && plan.audioMuxExecutionContractReady
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
    if( !plan.sourceAudioExtractionContractReady )
    {
        return plan.sourceAudioExtractionPlan.reason.isEmpty()
            ? QStringLiteral("rendered source audio extraction prerequisite contract unavailable")
            : plan.sourceAudioExtractionPlan.reason;
    }
    if( !plan.sourceAudioExtractionExecutionContractReady )
    {
        return plan.sourceAudioExtractionExecutionPlan.reason.isEmpty()
            ? QStringLiteral("rendered source audio extraction execution contract unavailable")
            : plan.sourceAudioExtractionExecutionPlan.reason;
    }
    if( !plan.ffmpegAudioInputHandoffContractReady )
    {
        return plan.ffmpegAudioInputHandoffPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg audio input handoff contract unavailable")
            : plan.ffmpegAudioInputHandoffPlan.reason;
    }
    if( !plan.ffmpegAudioInputContractReady )
    {
        return plan.ffmpegAudioInputPlan.reason.isEmpty()
            ? QStringLiteral("rendered ffmpeg audio input contract unavailable")
            : plan.ffmpegAudioInputPlan.reason;
    }
    if( !plan.audioMuxPrerequisitesContractReady )
    {
        return plan.audioMuxPrerequisitesPlan.reason.isEmpty()
            ? QStringLiteral("rendered audio mux prerequisite contract unavailable")
            : plan.audioMuxPrerequisitesPlan.reason;
    }
    if( !plan.audioMuxExecutionContractReady )
    {
        return plan.audioMuxExecutionPlan.reason.isEmpty()
            ? QStringLiteral("rendered audio mux execution contract unavailable")
            : plan.audioMuxExecutionPlan.reason;
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
    return QStringLiteral("source-audio-source=%1 source-audio-clip=%2 source-audio-state=%3 source-audio-sample-rate=%4 source-audio-channels=%5 source-audio-bits=%6 source-audio-bytes=%7 source-audio-discovery-owned=%8 source-audio-discovery-attempted=%9 source-audio-known=%10 source-audio-present=%11 source-audio-extraction-owned=%12 source-audio-mux-input-owned=%13 source-audio-sync-validation-owned=%14 source-audio-video-only-ready=%15 source-audio-contract-ready=%16 source-audio-reason=%17")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.clipPath.isEmpty() ? QStringLiteral("unspecified") : plan.clipPath)
        .arg(plan.audioState.isEmpty() ? QStringLiteral("unspecified") : plan.audioState)
        .arg(plan.sampleRate)
        .arg(plan.channels)
        .arg(plan.bitsPerSample)
        .arg(plan.audioBytes)
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

inline QString batchRenderedVideoSourceAudioExtractionPlanSummary(
    const BatchRenderedVideoSourceAudioExtractionPlan & plan)
{
    return QStringLiteral("source-audio-extraction-source=%1 source-audio-extraction-clip=%2 source-audio-extraction-format=%3 source-audio-extraction-path=%4 source-audio-extraction-source-contract-ready=%5 source-audio-extraction-known=%6 source-audio-extraction-present=%7 source-audio-extraction-discovery-owned=%8 source-audio-extraction-path-planned=%9 source-audio-extraction-path-ready=%10 source-audio-extraction-format-ready=%11 source-audio-extraction-sample-rate-ready=%12 source-audio-extraction-channel-layout-ready=%13 source-audio-extraction-process-owned=%14 source-audio-extraction-temp-file-owned=%15 source-audio-extraction-cleanup-owned=%16 source-audio-extraction-ready=%17 source-audio-extraction-video-only-ready=%18 source-audio-extraction-contract-ready=%19 source-audio-extraction-reason=%20")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.clipPath.isEmpty() ? QStringLiteral("unspecified") : plan.clipPath)
        .arg(plan.extractionFormat.isEmpty() ? QStringLiteral("unspecified") : plan.extractionFormat)
        .arg(plan.plannedAudioPath.isEmpty() ? QStringLiteral("unspecified") : plan.plannedAudioPath)
        .arg(plan.sourceAudioContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioKnown ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioPresent ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioDiscoveryOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionPathPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionPathReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sampleFormatReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sampleRateReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.channelLayoutReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionProcessOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.tempFileOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.cleanupOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.videoOnlyFallbackReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.contractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoSourceAudioExtractionExecutionPlanSummary(
    const BatchRenderedVideoSourceAudioExtractionExecutionPlan & plan)
{
    return QStringLiteral("source-audio-extraction-exec-source=%1 source-audio-extraction-exec-path=%2 source-audio-extraction-exec-format=%3 source-audio-extraction-exec-prereq-contract-ready=%4 source-audio-extraction-exec-known=%5 source-audio-extraction-exec-present=%6 source-audio-extraction-exec-path-planned=%7 source-audio-extraction-exec-path-ready=%8 source-audio-extraction-exec-sample-read-planned=%9 source-audio-extraction-exec-wav-write-planned=%10 source-audio-extraction-exec-temp-lifecycle-planned=%11 source-audio-extraction-exec-sample-read-owned=%12 source-audio-extraction-exec-wav-header-owned=%13 source-audio-extraction-exec-wav-sample-owned=%14 source-audio-extraction-exec-temp-open-owned=%15 source-audio-extraction-exec-temp-finalize-owned=%16 source-audio-extraction-exec-cleanup-owned=%17 source-audio-extraction-exec-process-owned=%18 source-audio-extraction-exec-temp-lifecycle-ready=%19 source-audio-extraction-exec-ready=%20 source-audio-extraction-exec-video-only-ready=%21 source-audio-extraction-exec-contract-ready=%22 source-audio-extraction-exec-reason=%23")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.plannedAudioPath.isEmpty() ? QStringLiteral("unspecified") : plan.plannedAudioPath)
        .arg(plan.extractionFormat.isEmpty() ? QStringLiteral("unspecified") : plan.extractionFormat)
        .arg(plan.extractionPrerequisiteContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioKnown ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioPresent ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionPathPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionPathReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sampleReadPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.wavWritePlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.tempFileLifecyclePlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sampleReadOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.wavHeaderWriteOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.wavSampleWriteOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.tempFileOpenOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.tempFileFinalizeOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.cleanupOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionProcessOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.tempFileLifecycleReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionExecutionReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.videoOnlyFallbackReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.contractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoFfmpegAudioInputHandoffPlanSummary(
    const BatchRenderedVideoFfmpegAudioInputHandoffPlan & plan)
{
    return QStringLiteral("ffmpeg-audio-input-handoff-source=%1 ffmpeg-audio-input-handoff-path=%2 ffmpeg-audio-input-handoff-planned-args=%3 ffmpeg-audio-input-handoff-active-args=%4 ffmpeg-audio-input-handoff-extraction-exec-contract-ready=%5 ffmpeg-audio-input-handoff-known=%6 ffmpeg-audio-input-handoff-present=%7 ffmpeg-audio-input-handoff-path-planned=%8 ffmpeg-audio-input-handoff-path-ready=%9 ffmpeg-audio-input-handoff-extraction-ready=%10 ffmpeg-audio-input-handoff-temp-lifecycle-ready=%11 ffmpeg-audio-input-handoff-cleanup-owned=%12 ffmpeg-audio-input-handoff-planned=%13 ffmpeg-audio-input-handoff-args-planned=%14 ffmpeg-audio-input-handoff-output-owned=%15 ffmpeg-audio-input-handoff-ownership-planned=%16 ffmpeg-audio-input-handoff-args-handoff-planned=%17 ffmpeg-audio-input-handoff-owned=%18 ffmpeg-audio-input-handoff-args-handoff-owned=%19 ffmpeg-audio-input-handoff-ready=%20 ffmpeg-audio-input-handoff-video-only-ready=%21 ffmpeg-audio-input-handoff-contract-ready=%22 ffmpeg-audio-input-handoff-reason=%23")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.plannedAudioPath.isEmpty() ? QStringLiteral("unspecified") : plan.plannedAudioPath)
        .arg(plan.plannedInputArguments.isEmpty() ? QStringLiteral("unspecified") : plan.plannedInputArguments)
        .arg(plan.activeAudioArguments.isEmpty() ? QStringLiteral("unspecified") : plan.activeAudioArguments)
        .arg(plan.extractionExecutionContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioKnown ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioPresent ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionPathPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionPathReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionExecutionReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.tempFileLifecycleReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.cleanupOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioInputPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.inputArgumentsPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionOutputOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioInputOwnershipPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioInputArgumentHandoffPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioInputOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioInputArgumentHandoffOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioInputHandoffReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.videoOnlyFallbackReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.contractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoFfmpegAudioInputPlanSummary(
    const BatchRenderedVideoFfmpegAudioInputPlan & plan)
{
    return QStringLiteral("ffmpeg-audio-input-source=%1 ffmpeg-audio-input-path=%2 ffmpeg-audio-input-planned-args=%3 ffmpeg-audio-input-active-args=%4 ffmpeg-audio-input-extraction-contract-ready=%5 ffmpeg-audio-input-known=%6 ffmpeg-audio-input-present=%7 ffmpeg-audio-input-path-planned=%8 ffmpeg-audio-input-path-ready=%9 ffmpeg-audio-input-extraction-ready=%10 ffmpeg-audio-input-temp-file-owned=%11 ffmpeg-audio-input-cleanup-owned=%12 ffmpeg-audio-input-planned=%13 ffmpeg-audio-input-owned=%14 ffmpeg-audio-input-ready=%15 ffmpeg-audio-input-video-only-ready=%16 ffmpeg-audio-input-contract-ready=%17 ffmpeg-audio-input-reason=%18")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.plannedAudioPath.isEmpty() ? QStringLiteral("unspecified") : plan.plannedAudioPath)
        .arg(plan.plannedInputArguments.isEmpty() ? QStringLiteral("unspecified") : plan.plannedInputArguments)
        .arg(plan.activeAudioArguments.isEmpty() ? QStringLiteral("unspecified") : plan.activeAudioArguments)
        .arg(plan.sourceAudioExtractionContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioKnown ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioPresent ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionPathPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionPathReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.extractionReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.tempFileOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.cleanupOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioInputPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioInputOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioInputReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.videoOnlyFallbackReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.contractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoAudioMuxPrerequisitesPlanSummary(
    const BatchRenderedVideoAudioMuxPrerequisitesPlan & plan)
{
    return QStringLiteral("audio-mux-source=%1 audio-mux-input=%2 audio-mux-output=%3 audio-mux-source-contract-ready=%4 audio-mux-extraction-contract-ready=%5 audio-mux-audio-input-contract-ready=%6 audio-mux-video-only-ready=%7 audio-mux-source-discovery-owned=%8 audio-mux-extraction-owned=%9 audio-mux-input-owned=%10 audio-mux-mux-owned=%11 audio-mux-sync-owned=%12 audio-mux-ready=%13 audio-mux-contract-ready=%14 audio-mux-reason=%15")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.inputState.isEmpty() ? QStringLiteral("unspecified") : plan.inputState)
        .arg(plan.outputState.isEmpty() ? QStringLiteral("unspecified") : plan.outputState)
        .arg(plan.sourceAudioContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioExtractionContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioInputContractReady ? QStringLiteral("true") : QStringLiteral("false"))
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

inline QString batchRenderedVideoAudioMuxExecutionPlanSummary(
    const BatchRenderedVideoAudioMuxExecutionPlan & plan)
{
    return QStringLiteral("audio-mux-exec-source=%1 audio-mux-exec-input=%2 audio-mux-exec-output=%3 audio-mux-exec-source-contract-ready=%4 audio-mux-exec-handoff-contract-ready=%5 audio-mux-exec-prereq-contract-ready=%6 audio-mux-exec-known=%7 audio-mux-exec-present=%8 audio-mux-exec-discovery-owned=%9 audio-mux-exec-extraction-owned=%10 audio-mux-exec-handoff-ready=%11 audio-mux-exec-temp-input-owned=%12 audio-mux-exec-mux-planned=%13 audio-mux-exec-args-handoff-planned=%14 audio-mux-exec-sync-planned=%15 audio-mux-exec-mux-owned=%16 audio-mux-exec-args-handoff-owned=%17 audio-mux-exec-sync-owned=%18 audio-mux-exec-mux-ready=%19 audio-mux-exec-sync-ready=%20 audio-mux-exec-ready=%21 audio-mux-exec-video-only-ready=%22 audio-mux-exec-contract-ready=%23 audio-mux-exec-reason=%24")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.inputState.isEmpty() ? QStringLiteral("unspecified") : plan.inputState)
        .arg(plan.outputState.isEmpty() ? QStringLiteral("unspecified") : plan.outputState)
        .arg(plan.sourceAudioContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioInputHandoffContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxPrerequisitesContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioKnown ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioPresent ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioDiscoveryOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioExtractionOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioInputHandoffReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.tempAudioInputOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxArgumentHandoffPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioSyncValidationPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxArgumentHandoffOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioSyncValidationOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioSyncValidationReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.muxExecutionReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.videoOnlyFallbackReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.contractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.reason.isEmpty() ? QStringLiteral("none") : plan.reason);
}

inline QString batchRenderedVideoFfmpegAudioPlanSummary(
    const BatchRenderedVideoFfmpegAudioPlan & plan)
{
    return QStringLiteral("ffmpeg-audio-source=%1 ffmpeg-audio-args=%2 ffmpeg-audio-mux-transition-args=%3 ffmpeg-audio-video-only-ready=%4 ffmpeg-audio-source-discovery-owned=%5 ffmpeg-audio-extraction-owned=%6 ffmpeg-audio-input-contract-ready=%7 ffmpeg-audio-input-owned=%8 ffmpeg-audio-mux-exec-contract-ready=%9 ffmpeg-audio-mux-planned=%10 ffmpeg-audio-mux-args-handoff-planned=%11 ffmpeg-audio-sync-planned=%12 ffmpeg-audio-mux-owned=%13 ffmpeg-audio-mux-args-handoff-owned=%14 ffmpeg-audio-sync-owned=%15 ffmpeg-audio-mux-exec-ready=%16 ffmpeg-audio-mux-command-planned=%17 ffmpeg-audio-mux-command-ready=%18 ffmpeg-audio-contract-ready=%19 ffmpeg-audio-reason=%20")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.audioArguments.isEmpty() ? QStringLiteral("unspecified") : plan.audioArguments)
        .arg(plan.muxTransitionArguments.isEmpty() ? QStringLiteral("unspecified") : plan.muxTransitionArguments)
        .arg(plan.videoOnlyCommandReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioDiscoveryOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.sourceAudioExtractionOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioInputContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioInputOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxExecutionContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxArgumentHandoffPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioSyncValidationPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxArgumentHandoffOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioSyncOwned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxExecutionReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.muxedAudioCommandPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.muxedAudioCommandReady ? QStringLiteral("true") : QStringLiteral("false"))
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
    return QStringLiteral("ffmpeg-command-source=%1 ffmpeg-command-exe=%2 ffmpeg-command-raw-pix-fmt=%3 ffmpeg-command-raw-input=%4 ffmpeg-command-color-source=%5 ffmpeg-command-color-tag=%6 ffmpeg-command-color-args=%7 ffmpeg-command-audio-args=%8 ffmpeg-command-audio-transition-source=%9 ffmpeg-command-audio-transition-args=%10 ffmpeg-command-audio-contract-ready=%11 ffmpeg-command-audio-mux-exec-contract-ready=%12 ffmpeg-command-audio-mux-planned=%13 ffmpeg-command-audio-mux-args-handoff-planned=%14 ffmpeg-command-audio-sync-planned=%15 ffmpeg-command-audio-mux-exec-ready=%16 ffmpeg-command-audio-mux-command-planned=%17 ffmpeg-command-audio-mux-command-ready=%18 ffmpeg-command-audio-owned=%19 ffmpeg-command-execution-owned=%20 ffmpeg-command-output-verification-owned=%21 ffmpeg-command-args=%22 ffmpeg-command-ready=%23 ffmpeg-command-reason=%24")
        .arg(plan.source.isEmpty() ? QStringLiteral("unspecified") : plan.source)
        .arg(plan.executable.isEmpty() ? QStringLiteral("unspecified") : plan.executable)
        .arg(plan.rawInputPixelFormat.isEmpty() ? QStringLiteral("unspecified") : plan.rawInputPixelFormat)
        .arg(plan.rawInputArguments.isEmpty() ? QStringLiteral("unspecified") : plan.rawInputArguments)
        .arg(plan.colorTagSource.isEmpty() ? QStringLiteral("unspecified") : plan.colorTagSource)
        .arg(plan.colorTag)
        .arg(plan.colorArguments.isEmpty() ? QStringLiteral("unspecified") : plan.colorArguments)
        .arg(plan.audioArguments.isEmpty() ? QStringLiteral("unspecified") : plan.audioArguments)
        .arg(plan.audioTransitionSource.isEmpty()
            ? QStringLiteral("unspecified")
            : plan.audioTransitionSource)
        .arg(plan.audioTransitionArguments.isEmpty()
            ? QStringLiteral("unspecified")
            : plan.audioTransitionArguments)
        .arg(plan.audioContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxExecutionContractReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxArgumentHandoffPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioSyncValidationPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.audioMuxExecutionReady ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.muxedAudioCommandPlanned ? QStringLiteral("true") : QStringLiteral("false"))
        .arg(plan.muxedAudioCommandReady ? QStringLiteral("true") : QStringLiteral("false"))
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
    return QStringLiteral("%1 %2 %3 %4 %5 %6 %7 %8 %9 %10 %11 %12 %13 %14 %15 %16 %17 %18 %19 %20 %21 %22 %23 %24 %25 %26 preflight-ready=%27 runnable=%28 first-blocker=%29")
        .arg(batchExportFormatRequestSummary(plan.request))
        .arg(batchRenderedVideoTargetSummary(plan.target))
        .arg(batchRenderedVideoEncoderPresetSummary(plan.encoderPreset))
        .arg(batchRenderedVideoFfmpegVideoPlanSummary(plan.ffmpegVideoPlan))
        .arg(batchRenderedVideoFfmpegFilterPlanSummary(plan.ffmpegFilterPlan))
        .arg(batchRenderedVideoOptionalFilterPlanSummary(
            plan.optionalFilterPlan))
        .arg(batchRenderedVideoSourceAudioPlanSummary(plan.sourceAudioPlan))
        .arg(batchRenderedVideoSourceAudioExtractionPlanSummary(
            plan.sourceAudioExtractionPlan))
        .arg(batchRenderedVideoSourceAudioExtractionExecutionPlanSummary(
            plan.sourceAudioExtractionExecutionPlan))
        .arg(batchRenderedVideoFfmpegAudioInputHandoffPlanSummary(
            plan.ffmpegAudioInputHandoffPlan))
        .arg(batchRenderedVideoFfmpegAudioInputPlanSummary(
            plan.ffmpegAudioInputPlan))
        .arg(batchRenderedVideoAudioMuxPrerequisitesPlanSummary(
            plan.audioMuxPrerequisitesPlan))
        .arg(batchRenderedVideoAudioMuxExecutionPlanSummary(
            plan.audioMuxExecutionPlan))
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
