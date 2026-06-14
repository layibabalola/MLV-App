#include "ReceiptApplier.h"
#include "BatchContext.h"
#include "BatchLogger.h"
#include "ReceiptSafety.h"

#include "../../platform/qt/ReceiptSettings.h"
#include "../../platform/qt/DualIsoPatternMapping.h"

#include <QByteArray>
#include <QFileInfo>
#include <QtGlobal>

#include <cmath>

namespace
{

enum class LookAssistScene
{
    Night,
    ArtificialLights,
    Shade,
    BrightSun
};

struct LookAssistStats
{
    double median = 0.0;
    double p05 = 0.0;
    double p95 = 0.0;
    double p99 = 0.0;
    double clipLow = 0.0;
    double clipHigh = 0.0;
    double dynamicRange = 0.0;
    double medianR = 0.0;
    double medianG = 0.0;
    double medianB = 0.0;
    double balanceR = 0.0;
    double balanceG = 0.0;
    double balanceB = 0.0;
    int balanceSamples = 0;
    double visibleMeanR = 0.0;
    double visibleMeanG = 0.0;
    double visibleMeanB = 0.0;
    int visibleSamples = 0;
    double greenArtifactRatio = 0.0;
    double greenArtifactMeanAxis = 0.0;
    int greenArtifactSamples = 0;
};

struct LookAssistPreset
{
    int exposure = 0;
    int contrast = 0;
    int pivot = 75;
    int shadows = 0;
    int highlights = 0;
    int vibrance = 0;
    int temperatureDelta = 0;
    int tintDelta = 0;
};

struct LookAssistAutoWhiteBalancePatch
{
    bool valid = false;
    int thumbnailX = -1;
    int thumbnailY = -1;
    int rawX = -1;
    int rawY = -1;
    double luma = 0.0;
    double chroma = 0.0;
    double greenAxis = 0.0;
    double blueAmberAxis = 0.0;
    double score = -1.0e9;
};

static QString lookAssistSceneName( LookAssistScene scene )
{
    switch( scene )
    {
    case LookAssistScene::Night:
        return QStringLiteral("night");
    case LookAssistScene::ArtificialLights:
        return QStringLiteral("artificial-lights");
    case LookAssistScene::Shade:
        return QStringLiteral("shade");
    case LookAssistScene::BrightSun:
        return QStringLiteral("bright-sun");
    }
    return QStringLiteral("unknown");
}

static double lookAssistPercentile( const int *histogram, int totalSamples, double fraction )
{
    if( !histogram || totalSamples <= 0 ) return 0.0;

    const int target = qBound( 1, (int)std::ceil( fraction * totalSamples ), totalSamples );
    int cumulative = 0;
    for( int i = 0; i < 256; ++i )
    {
        cumulative += histogram[i];
        if( cumulative >= target ) return (double)i;
    }
    return 255.0;
}

static LookAssistStats analyzeLookAssistThumbnail( const unsigned char *rgb, int width, int height )
{
    LookAssistStats stats;
    if( !rgb || width <= 0 || height <= 0 ) return stats;

    int histogram[256] = { 0 };
    int histogramR[256] = { 0 };
    int histogramG[256] = { 0 };
    int histogramB[256] = { 0 };
    int balanceHistogramR[256] = { 0 };
    int balanceHistogramG[256] = { 0 };
    int balanceHistogramB[256] = { 0 };
    const int totalSamples = width * height;
    double visibleRTotal = 0.0;
    double visibleGTotal = 0.0;
    double visibleBTotal = 0.0;
    double greenArtifactAxisTotal = 0.0;

    for( int i = 0; i < totalSamples; ++i )
    {
        const int base = i * 3;
        const int r = rgb[base + 0];
        const int g = rgb[base + 1];
        const int b = rgb[base + 2];
        const int luma = qBound( 0, ( 54 * r + 183 * g + 19 * b ) >> 8, 255 );
        histogram[luma]++;
        histogramR[r]++;
        histogramG[g]++;
        histogramB[b]++;

        const int maxChannel = qMax( r, qMax( g, b ) );
        const int minChannel = qMin( r, qMin( g, b ) );
        const int saturationProxy = maxChannel - minChannel;
        const double greenAxis = (double)g - ( ( (double)r + (double)b ) * 0.5 );
        if( luma >= 12 )
        {
            visibleRTotal += (double)r;
            visibleGTotal += (double)g;
            visibleBTotal += (double)b;
            stats.visibleSamples++;
        }
        if( luma >= 12
         && g >= 30
         && greenAxis >= 25.0 )
        {
            greenArtifactAxisTotal += greenAxis;
            stats.greenArtifactSamples++;
        }
        if( luma >= 20
         && luma <= 230
         && saturationProxy <= qMax( 14, luma / 5 ) )
        {
            balanceHistogramR[r]++;
            balanceHistogramG[g]++;
            balanceHistogramB[b]++;
            stats.balanceSamples++;
        }
    }

    stats.median = lookAssistPercentile( histogram, totalSamples, 0.50 );
    stats.p05 = lookAssistPercentile( histogram, totalSamples, 0.05 );
    stats.p95 = lookAssistPercentile( histogram, totalSamples, 0.95 );
    stats.p99 = lookAssistPercentile( histogram, totalSamples, 0.99 );
    stats.dynamicRange = stats.p95 - stats.p05;
    stats.medianR = lookAssistPercentile( histogramR, totalSamples, 0.50 );
    stats.medianG = lookAssistPercentile( histogramG, totalSamples, 0.50 );
    stats.medianB = lookAssistPercentile( histogramB, totalSamples, 0.50 );

    if( stats.balanceSamples >= qMax( 32, totalSamples / 100 ) )
    {
        stats.balanceR = lookAssistPercentile( balanceHistogramR, stats.balanceSamples, 0.50 );
        stats.balanceG = lookAssistPercentile( balanceHistogramG, stats.balanceSamples, 0.50 );
        stats.balanceB = lookAssistPercentile( balanceHistogramB, stats.balanceSamples, 0.50 );
    }
    else
    {
        stats.balanceR = stats.medianR;
        stats.balanceG = stats.medianG;
        stats.balanceB = stats.medianB;
    }
    if( stats.visibleSamples > 0 )
    {
        stats.visibleMeanR = visibleRTotal / (double)stats.visibleSamples;
        stats.visibleMeanG = visibleGTotal / (double)stats.visibleSamples;
        stats.visibleMeanB = visibleBTotal / (double)stats.visibleSamples;
        stats.greenArtifactRatio =
            (double)stats.greenArtifactSamples / (double)stats.visibleSamples;
    }
    if( stats.greenArtifactSamples > 0 )
    {
        stats.greenArtifactMeanAxis =
            greenArtifactAxisTotal / (double)stats.greenArtifactSamples;
    }

    int clipLow = histogram[0] + histogram[1] + histogram[2] + histogram[3];
    int clipHigh = histogram[252] + histogram[253] + histogram[254] + histogram[255];
    stats.clipLow = (double)clipLow / (double)totalSamples;
    stats.clipHigh = (double)clipHigh / (double)totalSamples;
    return stats;
}

static LookAssistScene classifyLookAssistScene( const LookAssistStats &stats )
{
    if( stats.p95 >= 220.0 || stats.clipHigh > 0.015 )
        return LookAssistScene::BrightSun;

    if( stats.median < 60.0 )
    {
        if( stats.clipHigh > 0.006 || stats.p99 >= 236.0 || stats.p95 >= 185.0 )
            return LookAssistScene::ArtificialLights;
        return LookAssistScene::Night;
    }

    return LookAssistScene::Shade;
}

static bool lookAssistIsFloorLiftedNightThumbnail( LookAssistScene scene, const LookAssistStats &stats )
{
    return scene == LookAssistScene::Night &&
           stats.median >= 24.0 &&
           stats.p05 >= 18.0 &&
           stats.p95 <= 70.0 &&
           stats.dynamicRange <= 24.0;
}

static bool lookAssistIsFlatNoiseFloorThumbnail( LookAssistScene scene, const LookAssistStats &stats )
{
    return scene == LookAssistScene::Night
        && stats.median <= 34.0
        && stats.p05 <= 34.0
        && stats.p95 <= 34.0
        && stats.p99 <= 34.0
        && ( stats.p99 - stats.p05 ) <= 2.0;
}

static int lookAssistExposureForTarget( double sourceValue, double targetValue, int fallback )
{
    if( sourceValue <= 1.0 || targetValue <= 1.0 ) return fallback;
    return (int)qRound( std::log( targetValue / sourceValue ) / std::log( 2.0 ) * 100.0 );
}

static bool lookAssistHasNeutralBalanceSamples( const LookAssistStats &stats )
{
    return stats.balanceSamples >= 32
        && stats.balanceR > 0.0
        && stats.balanceG > 0.0
        && stats.balanceB > 0.0;
}

static int lookAssistAutoTintCap( LookAssistScene scene, bool processedFloorLiftedBalance )
{
    (void)processedFloorLiftedBalance;
    if( scene == LookAssistScene::BrightSun ) return 8;
    return 22;
}

static LookAssistAutoWhiteBalancePatch findLookAssistAutoWhiteBalancePatch(
        const unsigned char *rgb,
        int width,
        int height,
        int downscaleFactor,
        int rawWidth,
        int rawHeight )
{
    LookAssistAutoWhiteBalancePatch best;
    if( !rgb
     || width <= 0
     || height <= 0
     || downscaleFactor <= 0
     || rawWidth <= 0
     || rawHeight <= 0 )
    {
        return best;
    }

    const int edgeMarginX = qMax( 1, width / 80 );
    const int edgeMarginY = qMax( 1, height / 80 );
    for( int y = edgeMarginY; y < height - edgeMarginY; ++y )
    {
        for( int x = edgeMarginX; x < width - edgeMarginX; ++x )
        {
            const int base = ( y * width + x ) * 3;
            const int r = rgb[base + 0];
            const int g = rgb[base + 1];
            const int b = rgb[base + 2];
            const int maxChannel = qMax( r, qMax( g, b ) );
            const int minChannel = qMin( r, qMin( g, b ) );
            const double chroma = (double)( maxChannel - minChannel );
            const double luma = ( 54.0 * r + 183.0 * g + 19.0 * b ) / 256.0;
            if( luma < 70.0 || luma > 220.0 ) continue;
            if( chroma > qMax( 10.0, luma * 0.16 ) ) continue;

            const double greenAxis = (double)g - ( ( (double)r + (double)b ) * 0.5 );
            const double blueAmberAxis = (double)b - (double)r;
            if( greenAxis > 14.0 ) continue;
            if( std::fabs( blueAmberAxis ) > 30.0 ) continue;

            const double score =
                luma * 0.75
                - chroma * 1.6
                - qMax( 0.0, greenAxis ) * 2.8
                - std::fabs( blueAmberAxis ) * 0.4;
            if( !best.valid || score > best.score )
            {
                best.valid = true;
                best.thumbnailX = x;
                best.thumbnailY = y;
                best.rawX = qBound( 0, x * downscaleFactor + downscaleFactor / 2, rawWidth - 1 );
                best.rawY = qBound( 0, y * downscaleFactor + downscaleFactor / 2, rawHeight - 1 );
                best.luma = luma;
                best.chroma = chroma;
                best.greenAxis = greenAxis;
                best.blueAmberAxis = blueAmberAxis;
                best.score = score;
            }
        }
    }
    return best;
}

static bool lookAssistAutoWhiteBalanceSolutionIsStable(
        const LookAssistAutoWhiteBalancePatch &patch,
        int baseTemperature,
        int baseTint,
        int candidateTemperature,
        int candidateTint )
{
    if( !patch.valid ) return false;

    const int temperatureDelta = candidateTemperature - baseTemperature;
    const int tintDelta = candidateTint - baseTint;
    const bool extremeGreenCorrection =
        candidateTint <= -34
        && temperatureDelta <= -1200
        && patch.luma >= 205.0
        && patch.chroma >= 12.0
        && std::fabs( patch.blueAmberAxis ) >= 14.0;
    if( extremeGreenCorrection )
    {
        return false;
    }

    const bool hardGreenClampFromBrightNeutralPatch =
        candidateTint <= -34
        && patch.luma >= 210.0
        && patch.chroma <= 6.0
        && qAbs( temperatureDelta ) <= 1000;
    if( hardGreenClampFromBrightNeutralPatch )
    {
        return false;
    }

    const bool hardGreenClampFromLowChromaMidtonePatch =
        candidateTint <= -34
        && patch.luma >= 70.0
        && patch.luma <= 160.0
        && patch.chroma <= 8.0
        && qAbs( temperatureDelta ) <= 1200;
    if( hardGreenClampFromLowChromaMidtonePatch )
    {
        return false;
    }

    const bool implausibleDualAxisSwing =
        std::fabs( static_cast<double>( tintDelta ) ) >= 34.0
        && qAbs( temperatureDelta ) >= 1800
        && patch.chroma >= 12.0
        && patch.luma >= 200.0;
    return !implausibleDualAxisSwing;
}

static double lookAssistAutoWhiteBalanceDampingFactor(
        const LookAssistAutoWhiteBalancePatch &patch,
        int baseTemperature,
        int baseTint,
        int candidateTemperature,
        int candidateTint,
        LookAssistScene scene )
{
    if( !patch.valid ) return 1.0;

    const int temperatureDelta = candidateTemperature - baseTemperature;
    const int tintDelta = candidateTint - baseTint;
    double factor = 1.0;

    if( patch.chroma >= 14.0 && qAbs( temperatureDelta ) >= 900 )
    {
        factor = qMin( factor, 0.70 );
    }
    if( patch.chroma >= 10.0 && qAbs( temperatureDelta ) >= 1200 )
    {
        factor = qMin( factor, 0.65 );
    }
    if( patch.chroma >= 10.0 && qAbs( tintDelta ) >= 24 )
    {
        factor = qMin( factor, 0.75 );
    }
    if( scene == LookAssistScene::Night
     && patch.luma < 150.0
     && qAbs( temperatureDelta ) >= 1000 )
    {
        factor = qMin( factor, 0.70 );
    }
    return factor;
}

static LookAssistPreset presetForLookAssistScene( LookAssistScene scene,
                                                  const LookAssistStats &stats,
                                                  const LookAssistStats *colorStats = nullptr )
{
    LookAssistPreset preset;
    int targetMedian = 110;

    switch( scene )
    {
    case LookAssistScene::Night:
        targetMedian = 94;
        preset.contrast = 8;
        preset.pivot = 46;
        preset.shadows = 28;
        preset.highlights = -18;
        preset.vibrance = 3;
        break;
    case LookAssistScene::ArtificialLights:
        targetMedian = 96;
        preset.contrast = 10;
        preset.pivot = 50;
        preset.shadows = 10;
        preset.highlights = -24;
        preset.vibrance = 2;
        break;
    case LookAssistScene::Shade:
        targetMedian = 112;
        preset.contrast = 9;
        preset.pivot = 55;
        preset.shadows = 12;
        preset.highlights = -12;
        preset.vibrance = 5;
        break;
    case LookAssistScene::BrightSun:
        targetMedian = 118;
        preset.contrast = 6;
        preset.pivot = 60;
        preset.shadows = 4;
        preset.highlights = -30;
        preset.vibrance = 0;
        break;
    }

    const bool floorLiftedNightThumbnail =
        lookAssistIsFloorLiftedNightThumbnail( scene, stats );
    const bool flatNoiseFloorThumbnail =
        lookAssistIsFlatNoiseFloorThumbnail( scene, stats );
    const double sourceMedian = floorLiftedNightThumbnail
        ? qMax( 2.0, ( stats.median - stats.p05 ) + 2.0 )
        : qMax( 1.0, stats.median );
    int exposure = lookAssistExposureForTarget( sourceMedian, targetMedian, 0 );
    int maxExposure = 180;
    int minExposure = -140;
    double p95Ceiling = 172.0;
    double p99Ceiling = 218.0;
    if( scene == LookAssistScene::Night )
    {
        maxExposure = ( stats.p99 < 55.0 ) ? 380 : 260;
        if( flatNoiseFloorThumbnail )
            maxExposure = qMin( maxExposure, 170 );
        minExposure = -40;
        p95Ceiling = floorLiftedNightThumbnail ? 124.0 : 142.0;
        p99Ceiling = floorLiftedNightThumbnail ? 160.0 : 188.0;
    }
    else if( scene == LookAssistScene::ArtificialLights )
    {
        maxExposure = 220;
        minExposure = -120;
        p95Ceiling = 150.0;
        p99Ceiling = 194.0;
    }
    else if( scene == LookAssistScene::BrightSun )
    {
        maxExposure = 0;
        minExposure = -180;
        p95Ceiling = 146.0;
        p99Ceiling = 184.0;
    }

    int highlightCap = maxExposure;
    highlightCap = qMin( highlightCap, lookAssistExposureForTarget( stats.p95, p95Ceiling, highlightCap ) );
    highlightCap = qMin( highlightCap, lookAssistExposureForTarget( stats.p99, p99Ceiling, highlightCap ) );
    if( stats.clipHigh > 0.002 )
        highlightCap = qMin( highlightCap, 0 );
    exposure = qMin( exposure, highlightCap );

    exposure = qBound( minExposure, exposure, maxExposure );
    if( scene == LookAssistScene::BrightSun )
        exposure = qMin( exposure, 0 );
    if( scene == LookAssistScene::Night )
        exposure = qMax( exposure, 0 );

    preset.exposure = exposure;

    if( stats.dynamicRange < 100.0 ) preset.contrast += 6;
    else if( stats.dynamicRange < 130.0 ) preset.contrast += 3;
    else if( stats.dynamicRange > 180.0 ) preset.contrast -= 4;

    if( stats.p05 < 18.0 ) preset.shadows += 8;
    if( stats.p05 < 12.0 ) preset.shadows += 6;
    if( floorLiftedNightThumbnail ) preset.shadows = qMax( preset.shadows, 32 );
    if( stats.clipHigh > 0.010 ) preset.highlights -= 8;
    if( stats.clipHigh > 0.020 ) preset.highlights -= 8;
    const double exposureScale = std::pow( 2.0, exposure / 100.0 );
    const double projectedP95 = stats.p95 * exposureScale;
    const double projectedP99 = stats.p99 * exposureScale;
    if( projectedP95 > p95Ceiling - 2.0 ) preset.highlights -= 8;
    if( projectedP99 > p99Ceiling - 2.0 ) preset.highlights -= 8;

    if( scene == LookAssistScene::BrightSun )
    {
        preset.shadows = qMin( preset.shadows, 6 );
        preset.vibrance = qMin( preset.vibrance, 2 );
    }

    const LookAssistStats &balanceStats = colorStats ? *colorStats : stats;
    const bool processedFloorLiftedBalance = colorStats && floorLiftedNightThumbnail;
    const bool lowSignalFloorLiftedBalance =
        processedFloorLiftedBalance &&
        balanceStats.median > 0.0 &&
        balanceStats.median < 32.0;
    const double magentaGreenAxis = balanceStats.balanceG - ( ( balanceStats.balanceR + balanceStats.balanceB ) * 0.5 );
    const double blueAmberAxis = balanceStats.balanceB - balanceStats.balanceR;
    const bool hasNeutralBalance = lookAssistHasNeutralBalanceSamples( balanceStats );
    const int tintCap = lookAssistAutoTintCap( scene, processedFloorLiftedBalance );
    const int tempCap = ( scene == LookAssistScene::BrightSun )
                      ? 250
                      : ( processedFloorLiftedBalance ? 420 : 500 );
    const double tintThreshold = processedFloorLiftedBalance ? 6.0 : 10.0;
    const double tintGain = processedFloorLiftedBalance ? 0.55 : 0.65;
    const double tempThreshold = processedFloorLiftedBalance ? 6.0 : 14.0;
    const double tempGain = processedFloorLiftedBalance ? 16.0 : 18.0;

    if( hasNeutralBalance && std::fabs( magentaGreenAxis ) >= tintThreshold )
    {
        preset.tintDelta = qBound( -tintCap, (int)qRound( magentaGreenAxis * tintGain ), tintCap );
    }

    if( hasNeutralBalance && std::fabs( blueAmberAxis ) >= tempThreshold )
    {
        preset.temperatureDelta = qBound( -tempCap, (int)qRound( blueAmberAxis * tempGain ), tempCap );
    }

    if( hasNeutralBalance && lowSignalFloorLiftedBalance )
    {
        if( magentaGreenAxis > -4.0 )
            preset.tintDelta = qMax( preset.tintDelta, 4 );
        if( blueAmberAxis <= -6.0 )
        {
            const int warmCastTemperatureDelta =
                qBound( -360,
                        (int)qRound( blueAmberAxis * 22.0 ),
                        -96 );
            preset.temperatureDelta =
                qMin( preset.temperatureDelta, warmCastTemperatureDelta );
        }
    }
    if( hasNeutralBalance
     && processedFloorLiftedBalance
     && magentaGreenAxis > 2.0
     && balanceStats.greenArtifactRatio >= 0.004
     && balanceStats.greenArtifactMeanAxis >= 25.0 )
    {
        const int artifactTintNudge =
            qBound( 0,
                    (int)qRound( balanceStats.greenArtifactMeanAxis * 0.18
                               + balanceStats.greenArtifactRatio * 120.0 ),
                    qMin( 6, tintCap ) );
        preset.tintDelta = qBound( -tintCap,
                                   preset.tintDelta + artifactTintNudge,
                                   tintCap );
    }

    preset.contrast = qBound( -100, preset.contrast, 100 );
    preset.pivot = qBound( 0, preset.pivot, 100 );
    preset.shadows = qBound( -100, preset.shadows, 100 );
    preset.highlights = qBound( -100, preset.highlights, 100 );
    preset.vibrance = qBound( -100, preset.vibrance, 100 );
    return preset;
}

static uint16_t headlessAutoCorrectRawBlackLevel( mlvObject_t *mlvObject )
{
    int factor = 1;
    switch( getMlvBitdepth( mlvObject ) )
    {
        case 10: factor = 16;
            break;
        case 12: factor = 4;
            break;
        default:
            break;
    }
    if( getMlvOriginalBlackLevel( mlvObject ) >= (1700 / factor)
     && getMlvOriginalBlackLevel( mlvObject ) <= (2200 / factor) )
        return getMlvOriginalBlackLevel( mlvObject );

    if( getMlvCameraModel( mlvObject ) == 0x80000218
     || getMlvCameraModel( mlvObject ) == 0x80000261 )
        return 1792 / factor;

    return 2048 / factor;
}

static bool headlessDualIsoEnabled( ReceiptSettings *receipt, mlvObject_t *mlvObject )
{
    return receipt
        && mlvObject
        && llrpGetDualIsoValidity( mlvObject ) != DISO_INVALID
        && receipt->dualIso() > 0;
}

static uint16_t headlessAutoCorrectRawWhiteLevel( ReceiptSettings *receipt, mlvObject_t *mlvObject )
{
    if( !mlvObject ) return 0;

    const int originalWhite = getMlvOriginalWhiteLevel( mlvObject );
    int white = originalWhite;

    if( !receipt || !receipt->rawFixesEnabled() )
        return static_cast<uint16_t>( qBound( 0, white, 65535 ) );

    const bool restrictedLossless =
        ( mlvObject->MLVI.videoClass & MLV_VIDEO_CLASS_FLAG_LJ92 )
        && originalWhite > 0
        && originalWhite < 15000;

    if( restrictedLossless && headlessDualIsoEnabled( receipt, mlvObject ) )
    {
        white = originalWhite;
    }

    return static_cast<uint16_t>( qBound( 0, white, 65535 ) );
}

static uint16_t headlessRestrictedLosslessDualIsoOutputWhiteLevel( ReceiptSettings *receipt,
                                                                   mlvObject_t *mlvObject )
{
    if( !mlvObject ) return 0;

    const int originalWhite = getMlvOriginalWhiteLevel( mlvObject );
    const bool restrictedLossless =
        ( mlvObject->MLVI.videoClass & MLV_VIDEO_CLASS_FLAG_LJ92 )
        && originalWhite > 0
        && originalWhite < 15000;

    if( !restrictedLossless || !headlessDualIsoEnabled( receipt, mlvObject ) )
        return static_cast<uint16_t>( qBound( 0, originalWhite, 65535 ) );

    const int publishedWhite =
        mlvObject->llrawproc ? mlvObject->llrawproc->dng_white_level : 0;
    if( publishedWhite > originalWhite && publishedWhite <= 16383 )
    {
        return static_cast<uint16_t>( publishedWhite );
    }

    const int black = headlessAutoCorrectRawBlackLevel( mlvObject );
    const int range = qMax( 1, originalWhite - black );
    const int bitDepth = qBound( 1, (int)std::ceil( std::log2( (double)range ) ), 14 );
    const int shift = qMax( 0, 14 - bitDepth );
    const int scaledWhite = range * ( 1 << shift );
    return static_cast<uint16_t>( qBound( 0, qMax( originalWhite, scaledWhite ), 16383 ) );
}

static void captureHeadlessLookAssistBaseline( ReceiptSettings *receipt,
                                               mlvObject_t *mlvObject )
{
    if( !receipt ) return;

    receipt->setLookAssistBaselineExposure( receipt->exposure() );
    receipt->setLookAssistBaselineContrast( receipt->contrast() );
    receipt->setLookAssistBaselinePivot( receipt->pivot() );
    receipt->setLookAssistBaselineTemperature( receipt->temperature() == -1
                                             ? 6000
                                             : receipt->temperature() );
    receipt->setLookAssistBaselineTint( receipt->tint() );
    receipt->setLookAssistBaselineVibrance( receipt->vibrance() );
    receipt->setLookAssistBaselineShadows( receipt->shadows() );
    receipt->setLookAssistBaselineHighlights( receipt->highlights() );
    receipt->setLookAssistBaselineStretchX( receipt->stretchFactorX() > 0
                                          ? receipt->stretchFactorX()
                                          : 1.0 );
    receipt->setLookAssistBaselineStretchY( receipt->stretchFactorY() > 0
                                          ? receipt->stretchFactorY()
                                          : 1.0 );

    if( receipt->rawBlack() != -1 )
        receipt->setLookAssistBaselineRawBlack( receipt->rawBlack() );
    else if( mlvObject )
        receipt->setLookAssistBaselineRawBlack( (int)getMlvOriginalBlackLevel( mlvObject ) * 10 );
    else
        receipt->setLookAssistBaselineRawBlack( -1 );

    if( receipt->rawWhite() != -1 )
        receipt->setLookAssistBaselineRawWhite( receipt->rawWhite() );
    else if( mlvObject )
        receipt->setLookAssistBaselineRawWhite( (int)getMlvOriginalWhiteLevel( mlvObject ) );
    else
        receipt->setLookAssistBaselineRawWhite( -1 );

    receipt->setLookAssistBaselineChromaSmooth( receipt->chromaSmooth() );
    receipt->setLookAssistBaselineValid( true );
}

static void restoreHeadlessLookAssistBaseline( ReceiptSettings *receipt )
{
    if( !receipt || !receipt->lookAssistBaselineValid() ) return;

    receipt->setExposure( receipt->lookAssistBaselineExposure() );
    receipt->setContrast( receipt->lookAssistBaselineContrast() );
    receipt->setPivot( receipt->lookAssistBaselinePivot() );
    receipt->setTemperature( receipt->lookAssistBaselineTemperature() );
    receipt->setTint( receipt->lookAssistBaselineTint() );
    receipt->setVibrance( receipt->lookAssistBaselineVibrance() );
    receipt->setShadows( receipt->lookAssistBaselineShadows() );
    receipt->setHighlights( receipt->lookAssistBaselineHighlights() );
    receipt->setRawBlack( receipt->lookAssistBaselineRawBlack() );
    receipt->setRawWhite( receipt->lookAssistBaselineRawWhite() );
    receipt->setChromaSmooth( qBound( 0, receipt->lookAssistBaselineChromaSmooth(), 3 ) );
}

static void applyHeadlessRawLevelsAutoFix( ReceiptSettings *receipt,
                                           mlvObject_t *mlvObject,
                                           processingObject_t *processingObject )
{
    if( !receipt || !mlvObject || !processingObject ) return;

    const uint16_t black = headlessAutoCorrectRawBlackLevel( mlvObject );
    const uint16_t white = headlessAutoCorrectRawWhiteLevel( receipt, mlvObject );

    receipt->setRawBlack( black * 10 );
    receipt->setRawWhite( white );

    setMlvBlackLevel( mlvObject, black );
    processingSetBlackLevel( processingObject, black, getMlvBitdepth( mlvObject ) );
    setMlvWhiteLevel( mlvObject, white );
    processingSetWhiteLevel( processingObject, white, getMlvBitdepth( mlvObject ) );
    llrpResetFpmStatus( mlvObject );
    llrpResetBpmStatus( mlvObject );
    llrpResetDngBWLevels( mlvObject );
}

}

/* -----------------------------------------------------------------------
 * applyToMlv()
 *
 * Replicates the NET EFFECT of MainWindow::setSliders() on the
 * mlvObject_t / processingObject_t, bypassing the GUI signal chain.
 *
 * The code below is a faithful extraction of every C API call that
 * setSliders() triggers through its setToolButton*() → toolButton*Changed()
 * signal chain, plus the direct struct assignments for dual ISO.
 *
 * ORDERING matches setSliders() exactly.
 * ----------------------------------------------------------------------- */

void ReceiptApplier::applyToMlv(ReceiptSettings *receipt,
                                 mlvObject_t *mlvObject,
                                 processingObject_t *processingObject)
{
    /* ---- Raw fixes enable/disable ---- */
    llrpSetFixRawMode( mlvObject, (int)receipt->rawFixesEnabled() );

    /* ---- Focus pixels ----
     * -1 = auto-detect (first load), else use receipt value */
    if( receipt->focusPixels() == -1 )
    {
        llrpSetFocusPixelMode( mlvObject, llrpDetectFocusDotFixMode( mlvObject ) );
    }
    else
    {
        llrpSetFocusPixelMode( mlvObject, receipt->focusPixels() );
    }
    llrpResetFpmStatus( mlvObject );
    llrpResetBpmStatus( mlvObject );

    /* ---- Focus pixels interpolation method ---- */
    llrpSetFocusPixelInterpolationMethod( mlvObject, receipt->fpiMethod() );

    /* ---- Bad pixels ---- */
    llrpSetBadPixelMode( mlvObject, receipt->badPixels() );
    llrpResetBpmStatus( mlvObject );

    /* ---- Bad pixels search method ---- */
    llrpSetBadPixelSearchMethod( mlvObject, receipt->bpsMethod() );
    llrpResetBpmStatus( mlvObject );

    /* ---- Bad pixels interpolation method ---- */
    llrpSetBadPixelInterpolationMethod( mlvObject, receipt->bpiMethod() );

    /* ---- Chroma smooth ----
     * Receipt stores toolButton index 0-3 which maps 1:1 to
     * CS_OFF=0, CS_2x2=1, CS_3x3=2, CS_5x5=3 enum values. */
    llrpSetChromaSmoothMode( mlvObject, receipt->chromaSmooth() );

    /* ---- Pattern noise ---- */
    llrpSetPatternNoiseMode( mlvObject, receipt->patternNoise() );

    /* ---- Upside down ---- */
    processingSetTransformation( processingObject, receipt->upsideDown() );

    /* ---- Vertical stripes ----
     * -1 = auto-detect: enable for Canon 5D3 (cameraModel 0x80000285) */
    if( receipt->verticalStripes() == -1 )
    {
        if( getMlvCameraModel( mlvObject ) == 0x80000285 )
            llrpSetVerticalStripeMode( mlvObject, 1 );
        else
            llrpSetVerticalStripeMode( mlvObject, 0 );
    }
    else
    {
        llrpSetVerticalStripeMode( mlvObject, receipt->verticalStripes() );
    }
    llrpComputeStripesOn( mlvObject );
    llrpResetFpmStatus( mlvObject );
    llrpResetBpmStatus( mlvObject );

    /* ==== Dual ISO — complex logic faithfully extracted from setSliders() ==== */

    receiptSanitizeClipLocalDualIsoState( receipt, mlvObject );

    /* Step 1: Resolve dualIsoForced (-1 = uninitialized) */
    if( receipt->dualIsoForced() == -1 )
    {
        receipt->setDualIsoForced( llrpGetDualIsoValidity( mlvObject ) );
    }
    else if( receipt->dualIsoForced() == DISO_FORCED
             && llrpGetDualIsoValidity( mlvObject ) == DISO_VALID )
    {
        receipt->setDualIsoForced( DISO_VALID );
    }
    else if( receipt->dualIsoForced() == DISO_VALID
             && llrpGetDualIsoValidity( mlvObject ) != DISO_VALID )
    {
        receipt->setDualIsoForced( DISO_FORCED );
    }

    /* Step 2: If forced, set validity flag */
    if( receipt->dualIsoForced() == DISO_FORCED )
    {
        llrpSetDualIsoValidity( mlvObject, 1 );
    }

    /* Step 3: Reset diso_auto_correction sign (matches GUI logic) */
    if( mlvObject->llrawproc->diso_auto_correction > 0 )
    {
        mlvObject->llrawproc->diso_auto_correction =
            -mlvObject->llrawproc->diso_auto_correction;
    }

    /* Step 4: Handle auto-corrected vs non-auto-corrected dual ISO */
    const int requestedDualIsoMode = receipt->dualIso();
    if( !receipt->dualIsoAutoCorrected() )
    {
        if( requestedDualIsoMode != 2 && receipt->dualIsoForced() == DISO_VALID )
        {
            /* Enable dual ISO if the two ISO levels actually differ */
            if( mlvObject->llrawproc->diso1 != mlvObject->llrawproc->diso2 )
            {
                receipt->setDualIso( 1 );
            }
            else
            {
                receipt->setDualIso( 0 );
            }
        }
        else if( requestedDualIsoMode != 2 )
        {
            receipt->setDualIso( 0 );
        }

        if( receipt->dualIsoForced() == DISO_VALID || requestedDualIsoMode == 2 )
        {
            mlvObject->llrawproc->diso_pattern = 0;
            mlvObject->llrawproc->diso_auto_correction = -1;
            mlvObject->llrawproc->diso_ev_correction = 1;
            mlvObject->llrawproc->diso_black_delta = -1;
        }
        else
        {
            /* Not VALID — set pattern/ev/black to zeroed defaults
             * (mirrors the GUI setting combobox=0, sliders=0) */
            mlvObject->llrawproc->diso_pattern = 0;
            mlvObject->llrawproc->diso_ev_correction = 0;
            mlvObject->llrawproc->diso_black_delta = 0;
        }

        /* Forced overrides — after the above branches */
        if( receipt->dualIsoForced() == DISO_FORCED )
        {
            mlvObject->llrawproc->diso_pattern = 0;
            mlvObject->llrawproc->diso_auto_correction = -2;
            mlvObject->llrawproc->diso_ev_correction = 1;
            mlvObject->llrawproc->diso_black_delta = -1;
        }
    }
    else
    {
        /* Auto-corrected: apply receipt values through the same UI-to-core
         * mapping used by on_DualIsoPatternComboBox_currentIndexChanged,
         *  on_horizontalSliderDualIsoEvCorrection_valueChanged,
         *  on_horizontalSliderDualIsoBlackDelta_valueChanged) */
        mlvObject->llrawproc->diso_pattern =
            dualIsoCorePatternFromUiIndex( receipt->dualIsoPattern() );

        /* EV correction: receipt stores the slider int value.
         * Slider value 1 is special (triggers auto-correct toggle),
         * other values are divided by 200.0 for the actual EV offset. */
        int evSliderVal = receipt->dualIsoEvCorrection();
        if( evSliderVal != 1 )
        {
            mlvObject->llrawproc->diso_ev_correction = evSliderVal / 200.0;
        }
        else
        {
            /* Value 1 = toggle auto correction sign */
            mlvObject->llrawproc->diso_auto_correction =
                -mlvObject->llrawproc->diso_auto_correction;
        }

        /* Black delta: -1 is special (triggers auto-correct toggle),
         * other values are used directly. */
        int bdSliderVal = receipt->dualIsoBlackDelta();
        if( bdSliderVal != -1 )
        {
            mlvObject->llrawproc->diso_black_delta = bdSliderVal;
        }
        else
        {
            mlvObject->llrawproc->diso_auto_correction =
                -mlvObject->llrawproc->diso_auto_correction;
        }
    }

    /* Step 5: Set dual ISO mode and reset levels */
    llrpSetDualIsoMode( mlvObject, receipt->dualIso() );
    processingSetBlackAndWhiteLevel( mlvObject->processing,
                                     getMlvBlackLevel( mlvObject ),
                                     getMlvWhiteLevel( mlvObject ),
                                     getMlvBitdepth( mlvObject ) );
    llrpResetDngBWLevels( mlvObject );

    /* Step 6: Dual ISO interpolation / alias map / fullres blending */
    llrpSetDualIsoInterpolationMethod( mlvObject, receipt->dualIsoInterpolation() );
    llrpSetDualIsoAliasMapMode( mlvObject, receipt->dualIsoAliasMap() );
    llrpSetDualIsoFullResBlendingMode( mlvObject, receipt->dualIsoFrBlending() );

    /* ---- Deflicker target ---- */
    llrpSetDeflickerTarget( mlvObject, receipt->deflickerTarget() );

    /* ---- Dark frame ----
     * Load external dark frame file first (if valid), then set mode. */
    {
        QString dfName = receipt->darkFrameFileName();
        if( QFileInfo( dfName ).exists()
            && dfName.endsWith( QStringLiteral(".MLV"), Qt::CaseInsensitive ) )
        {
#ifdef Q_OS_UNIX
            QByteArray dfBytes = dfName.toUtf8();
#else
            QByteArray dfBytes = dfName.toLatin1();
#endif
            char errorMessage[256] = { 0 };
            int ret = llrpValidateExtDarkFrame( mlvObject, dfBytes.data(), errorMessage );
            if( !ret )
            {
                llrpInitDarkFrameExtFileName( mlvObject, dfBytes.data() );
                if( errorMessage[0] )
                {
                    BatchLogger::err( QStringLiteral("[BATCH] WARNING dark frame: %1\n")
                                          .arg( QString(errorMessage) ) );
                }
            }
            else
            {
                BatchLogger::err( QStringLiteral("[BATCH] WARNING dark frame rejected: %1\n")
                                      .arg( QString(errorMessage) ) );
                llrpFreeDarkFrameExtFileName( mlvObject );
            }
        }
        else
        {
            llrpFreeDarkFrameExtFileName( mlvObject );
        }

        /* Set dark frame mode (0=off, 1=ext, 2=int).
         * If ext/int requested but no dark frame available, force off. */
        int dfMode = receipt->darkFrameEnabled();
        if( dfMode == -1 )
        {
            /* Auto-detect: use internal dark frame if available */
            if( llrpGetDarkFrameIntStatus( mlvObject ) )
                dfMode = 2;
            else
                dfMode = 0;
        }
        if( dfMode > 0 && !llrpGetDarkFrameExtStatus( mlvObject )
                       && !llrpGetDarkFrameIntStatus( mlvObject ) )
        {
            dfMode = 0;
        }
        llrpSetDarkFrameMode( mlvObject, dfMode );

        /* Dark frame affects dual ISO correction — match GUI behavior */
        if( mlvObject->llrawproc->diso_auto_correction > 0 )
        {
            mlvObject->llrawproc->diso_auto_correction =
                -mlvObject->llrawproc->diso_auto_correction;
            mlvObject->llrawproc->diso_black_delta = -1;
        }
        llrpResetBpmStatus( mlvObject );
        llrpComputeStripesOn( mlvObject );
    }

    /* ---- Raw black / white levels ----
     * -1 = use file defaults (do not override). */
    if( receipt->rawWhite() != -1 )
    {
        int wl = receipt->rawWhite();
        /* Clamp: white must be above black */
        int bl = (receipt->rawBlack() != -1) ? (int)(receipt->rawBlack() / 10.0)
                                             : getMlvBlackLevel( mlvObject );
        if( wl <= bl + 1 ) wl = bl + 2;

        setMlvWhiteLevel( mlvObject, wl );
        processingSetWhiteLevel( processingObject, wl, getMlvBitdepth( mlvObject ) );
        llrpResetFpmStatus( mlvObject );
        llrpResetBpmStatus( mlvObject );
    }

    if( receipt->rawBlack() != -1 )
    {
        double rawBlack = receipt->rawBlack() / 10.0;
        /* Clamp: black must be below white */
        if( rawBlack >= getMlvWhiteLevel( mlvObject ) - 1 )
            rawBlack = getMlvWhiteLevel( mlvObject ) - 2;

        setMlvBlackLevel( mlvObject, rawBlack );
        processingSetBlackLevel( processingObject, rawBlack, getMlvBitdepth( mlvObject ) );
        llrpResetFpmStatus( mlvObject );
        llrpResetBpmStatus( mlvObject );
    }

    /* ---- AgX tonemap ----
     * Phase E7: previously the AgX gate in processing_can_use_basic_matrix_fast_path
     * returned false for AgX-on receipts, so the indirect path was taken regardless
     * of what the receipt requested -- which masked the fact that ReceiptApplier
     * never propagated <agx>0/1</agx> to the processing object. Now that AgX
     * receipts can use the direct8 fast path, the AgX flag must reach the
     * processing object verbatim, otherwise the runtime default of AgX=1 (set
     * in initProcessingObject) sticks regardless of what the user requested. */
    if( receipt->agx() )
    {
        processingEnableAgX( processingObject );
    }
    else
    {
        processingDisableAgX( processingObject );
    }

    /* Final cache reset — ensures all settings take effect on next frame read */
    resetMlvCache( mlvObject );
    resetMlvCachedFrame( mlvObject );
}

bool ReceiptApplier::applyHeadlessLookAssist(ReceiptSettings *receipt,
                                             mlvObject_t *mlvObject,
                                             processingObject_t *processingObject,
                                             uint32_t analysisFrame)
{
    static const bool s_noLookAssist =
        qEnvironmentVariableIntValue( "MLVAPP_NO_LOOK_ASSIST" ) != 0;
    if( s_noLookAssist || !receipt || !mlvObject || !processingObject || !receipt->lookAssistEnabled() )
    {
        if( receipt )
            receipt->setLookAssistBaselineValid( false );
        BatchLogger::out( QStringLiteral(
            "[BATCH] LOOK_ASSIST skip env_disabled=%1 receipt=%2 mlv=%3 enabled=%4\n" )
            .arg( s_noLookAssist ? QStringLiteral("true") : QStringLiteral("false") )
            .arg( receipt ? QStringLiteral("true") : QStringLiteral("false") )
            .arg( mlvObject ? QStringLiteral("true") : QStringLiteral("false") )
            .arg( receipt && receipt->lookAssistEnabled()
                  ? QStringLiteral("true")
                  : QStringLiteral("false") ) );
        return false;
    }

    const int totalFrames = static_cast<int>( getMlvFrames( mlvObject ) );
    if( totalFrames <= 0 )
    {
        receipt->setLookAssistBaselineValid( false );
        BatchLogger::err( QStringLiteral("[BATCH] WARNING LOOK_ASSIST skipped: clip has no frames\n") );
        return false;
    }
    const int frameIndex = qBound( 0,
                                   static_cast<int>( analysisFrame ),
                                   totalFrames - 1 );

    if( receipt->lookAssistBaselineValid() )
        restoreHeadlessLookAssistBaseline( receipt );
    else
        captureHeadlessLookAssistBaseline( receipt, mlvObject );

    applyHeadlessRawLevelsAutoFix( receipt, mlvObject, processingObject );

    const int raw_w = mlvObject->RAWI.xRes;
    const int raw_h = mlvObject->RAWI.yRes;
    if( raw_w <= 0 || raw_h <= 0 )
    {
        receipt->setLookAssistBaselineValid( false );
        BatchLogger::err( QStringLiteral(
            "[BATCH] WARNING LOOK_ASSIST skipped: invalid raw size width=%1 height=%2\n" )
            .arg( raw_w )
            .arg( raw_h ) );
        return false;
    }

    int downscaleFactor = 8;
    if( raw_w > 4000 || raw_h > 2500 ) downscaleFactor = 12;
    else if( raw_w > 2800 || raw_h > 1900 ) downscaleFactor = 10;
    else if( raw_w > 1800 || raw_h > 1200 ) downscaleFactor = 8;
    else downscaleFactor = 6;

    const int width = raw_w / downscaleFactor;
    const int height = raw_h / downscaleFactor;
    if( width <= 0 || height <= 0 )
    {
        receipt->setLookAssistBaselineValid( false );
        BatchLogger::err( QStringLiteral(
            "[BATCH] WARNING LOOK_ASSIST skipped: invalid thumbnail size downscale=%1 width=%2 height=%3\n" )
            .arg( downscaleFactor )
            .arg( width )
            .arg( height ) );
        return false;
    }

    QByteArray thumbnail;
    thumbnail.resize( width * height * 3 );
    get_area_average_downscale_raw_thumnail(
        mlvObject,
        frameIndex,
        downscaleFactor,
        reinterpret_cast<unsigned char *>( thumbnail.data() ) );

    const LookAssistStats stats = analyzeLookAssistThumbnail(
        reinterpret_cast<const unsigned char *>( thumbnail.constData() ),
        width,
        height );
    if( stats.median <= 0.0 )
    {
        receipt->setLookAssistBaselineValid( false );
        BatchLogger::err( QStringLiteral(
            "[BATCH] WARNING LOOK_ASSIST skipped: empty analysis stats frame=%1 width=%2 height=%3\n" )
            .arg( frameIndex )
            .arg( width )
            .arg( height ) );
        return false;
    }

    const LookAssistScene scene = classifyLookAssistScene( stats );
    const bool floorLiftedNightThumbnail =
        lookAssistIsFloorLiftedNightThumbnail( scene, stats );
    const int colorDownscaleFactor = floorLiftedNightThumbnail
                                   ? qMax( 3, downscaleFactor / 3 )
                                   : downscaleFactor;
    const int colorWidth = raw_w / colorDownscaleFactor;
    const int colorHeight = raw_h / colorDownscaleFactor;
    const bool canAnalyzeProcessedColor =
        floorLiftedNightThumbnail && colorWidth > 0 && colorHeight > 0;
    bool chromaSmoothAutoApplied = false;
    if( canAnalyzeProcessedColor
     && receipt->chromaSmooth() == 0
     && headlessRestrictedLosslessDualIsoOutputWhiteLevel( receipt, mlvObject )
        > getMlvOriginalWhiteLevel( mlvObject ) )
    {
        receipt->setChromaSmooth( 1 );
        llrpSetChromaSmoothMode( mlvObject, 1 );
        llrpResetFpmStatus( mlvObject );
        llrpResetBpmStatus( mlvObject );
        chromaSmoothAutoApplied = true;
    }

    LookAssistStats processedColorStats;
    bool useProcessedColorStats = false;
    QByteArray processedThumbnail;
    if( canAnalyzeProcessedColor )
    {
        processedThumbnail.resize( colorWidth * colorHeight * 3 );
        get_area_average_downscale_thumnail(
            mlvObject,
            frameIndex,
            colorDownscaleFactor,
            1,
            reinterpret_cast<unsigned char *>( processedThumbnail.data() ) );
        processedColorStats = analyzeLookAssistThumbnail(
            reinterpret_cast<const unsigned char *>( processedThumbnail.constData() ),
            colorWidth,
            colorHeight );
        const int minColorBalanceSamples = qMax( 32, ( colorWidth * colorHeight ) / 100 );
        useProcessedColorStats =
            processedColorStats.median > 0.0 &&
            processedColorStats.balanceSamples >= minColorBalanceSamples;
    }

    LookAssistPreset preset = presetForLookAssistScene(
        scene,
        stats,
        useProcessedColorStats ? &processedColorStats : nullptr );
    const int baseTemperature = receipt->temperature() == -1
                              ? 6000
                              : qBound( 2000, receipt->temperature(), 10000 );
    const int baseTint = qBound( -100, receipt->tint(), 100 );
    processingSetWhiteBalance( processingObject, baseTemperature, baseTint / 10.0 );

    const unsigned char *autoWbThumbnail =
        useProcessedColorStats
        ? reinterpret_cast<const unsigned char *>( processedThumbnail.constData() )
        : reinterpret_cast<const unsigned char *>( thumbnail.constData() );
    const int autoWbWidth = useProcessedColorStats ? colorWidth : width;
    const int autoWbHeight = useProcessedColorStats ? colorHeight : height;
    const int autoWbDownscaleFactor = useProcessedColorStats
                                    ? colorDownscaleFactor
                                    : downscaleFactor;
    const LookAssistAutoWhiteBalancePatch autoWbPatch =
        findLookAssistAutoWhiteBalancePatch(
            autoWbThumbnail,
            autoWbWidth,
            autoWbHeight,
            autoWbDownscaleFactor,
            raw_w,
            raw_h );

    bool autoWhiteBalanceValid = false;
    QString autoWhiteBalanceSource = QStringLiteral("none");
    QString autoWhiteBalanceDecision = QStringLiteral("none");
    double autoWhiteBalanceDamping = 1.0;
    int autoWhiteBalanceTemperature = baseTemperature;
    int autoWhiteBalanceTint = baseTint;
    int autoWhiteBalanceCandidateTemperature = baseTemperature;
    int autoWhiteBalanceCandidateTint = baseTint;
    if( autoWbPatch.valid )
    {
        autoWhiteBalanceSource = useProcessedColorStats
                               ? QStringLiteral("processed-neutral-patch")
                               : QStringLiteral("raw-neutral-patch");
        autoWhiteBalanceDecision = QStringLiteral("candidate");
        findMlvWhiteBalance(
            mlvObject,
            static_cast<uint64_t>( frameIndex ),
            autoWbPatch.rawX,
            autoWbPatch.rawY,
            &autoWhiteBalanceTemperature,
            &autoWhiteBalanceTint,
            0 );
        autoWhiteBalanceTemperature =
            qBound( 2000, autoWhiteBalanceTemperature, 10000 );
        autoWhiteBalanceTint =
            qBound( -100, autoWhiteBalanceTint, 100 );
        autoWhiteBalanceTint =
            qBound( -35, autoWhiteBalanceTint, 18 );
        autoWhiteBalanceCandidateTemperature = autoWhiteBalanceTemperature;
        autoWhiteBalanceCandidateTint = autoWhiteBalanceTint;
        if( lookAssistAutoWhiteBalanceSolutionIsStable( autoWbPatch,
                                                        baseTemperature,
                                                        baseTint,
                                                        autoWhiteBalanceTemperature,
                                                        autoWhiteBalanceTint ) )
        {
            autoWhiteBalanceDamping =
                lookAssistAutoWhiteBalanceDampingFactor(
                    autoWbPatch,
                    baseTemperature,
                    baseTint,
                    autoWhiteBalanceTemperature,
                    autoWhiteBalanceTint,
                    scene );
            if( autoWhiteBalanceDamping < 0.999 )
            {
                autoWhiteBalanceTemperature =
                    qBound( 2000,
                            baseTemperature
                            + qRound( (autoWhiteBalanceTemperature - baseTemperature)
                                      * autoWhiteBalanceDamping ),
                            10000 );
                autoWhiteBalanceTint =
                    qBound( -100,
                            baseTint
                            + qRound( (autoWhiteBalanceTint - baseTint)
                                      * autoWhiteBalanceDamping ),
                            100 );
                autoWhiteBalanceDecision = QStringLiteral("accepted-damped");
            }
            else
            {
                autoWhiteBalanceDecision = QStringLiteral("accepted");
            }
            preset.temperatureDelta = autoWhiteBalanceTemperature - baseTemperature;
            preset.tintDelta = autoWhiteBalanceTint - baseTint;
            autoWhiteBalanceValid = true;
        }
        else
        {
            autoWhiteBalanceSource = QStringLiteral("rejected-extreme-color-cast");
            autoWhiteBalanceDecision = QStringLiteral("rejected-unstable");
        }
    }

    const int temperature =
        qBound( 2000, baseTemperature + preset.temperatureDelta, 10000 );
    const int tint =
        qBound( -100, baseTint + preset.tintDelta, 100 );

    receipt->setExposure( preset.exposure );
    receipt->setContrast( preset.contrast );
    receipt->setPivot( preset.pivot );
    receipt->setTemperature( temperature );
    receipt->setTint( tint );
    receipt->setVibrance( preset.vibrance );
    receipt->setShadows( preset.shadows );
    receipt->setHighlights( preset.highlights );
    receipt->setLookAssistBaselineValid( true );

    processingSetWhiteBalance( processingObject, temperature, tint / 10.0 );
    resetMlvCache( mlvObject );
    resetMlvCachedFrame( mlvObject );

    BatchLogger::out( QStringLiteral(
        "[BATCH] LOOK_ASSIST applied frame=%1 scene=%2 median=%3 p95=%4 p99=%5 exposure=%6 temperature=%7 tint=%8 autoWbValid=%9 autoWbSource=%10 autoWbDecision=%11 autoWbDamping=%12 autoWbCandidateTemp=%13 autoWbCandidateTint=%14 chromaSmoothAuto=%15 rawBlack=%16 rawWhite=%17\n" )
        .arg( frameIndex )
        .arg( lookAssistSceneName( scene ) )
        .arg( stats.median, 0, 'f', 2 )
        .arg( stats.p95, 0, 'f', 2 )
        .arg( stats.p99, 0, 'f', 2 )
        .arg( preset.exposure )
        .arg( temperature )
        .arg( tint )
        .arg( autoWhiteBalanceValid ? QStringLiteral("true") : QStringLiteral("false") )
        .arg( autoWhiteBalanceSource )
        .arg( autoWhiteBalanceDecision )
        .arg( autoWhiteBalanceDamping, 0, 'f', 3 )
        .arg( autoWbPatch.valid ? autoWhiteBalanceCandidateTemperature : 0 )
        .arg( autoWbPatch.valid ? autoWhiteBalanceCandidateTint : 0 )
        .arg( chromaSmoothAutoApplied ? QStringLiteral("true") : QStringLiteral("false") )
        .arg( getMlvBlackLevel( mlvObject ) )
        .arg( getMlvWhiteLevel( mlvObject ) ) );

    return true;
}


/* -----------------------------------------------------------------------
 * printFingerprint()
 *
 * Reads ACTUAL runtime state from the mlvObject/processingObject and
 * prints a structured line.  This proves settings reached the pipeline,
 * not just the ReceiptSettings parser.
 * ----------------------------------------------------------------------- */

void ReceiptApplier::printFingerprint(mlvObject_t *mlvObject,
                                       processingObject_t * /* processingObject */)
{
    llrawprocObject_t *llr = mlvObject->llrawproc;

    BatchLogger::out( QStringLiteral(
        "[BATCH] FINGERPRINT"
        " fixRaw=%1"
        " focusPixels=%2"
        " fpiMethod=%3"
        " badPixels=%4"
        " bpsMethod=%5"
        " bpiMethod=%6"
        " chromaSmooth=%7"
        " patternNoise=%8"
        " verticalStripes=%9"
        " deflickerTarget=%10"
        " dualIso=%11"
        " disoValidity=%12"
        " disoPattern=%13"
        " disoAutoCorr=%14"
        " disoEvCorr=%15"
        " disoBlackDelta=%16"
        " disoAveraging=%17"
        " disoAliasMap=%18"
        " disoFrBlending=%19"
        " darkFrame=%20"
        " rawBlack=%21"
        " rawWhite=%22"
        "\n")
        .arg( llr->fix_raw )
        .arg( llr->focus_pixels )
        .arg( llr->fpi_method )
        .arg( llr->bad_pixels )
        .arg( llr->bps_method )
        .arg( llr->bpi_method )
        .arg( llr->chroma_smooth )
        .arg( llr->pattern_noise )
        .arg( llr->vertical_stripes )
        .arg( llr->deflicker_target )
        .arg( llr->dual_iso )
        .arg( llr->diso_validity )
        .arg( llr->diso_pattern )
        .arg( llr->diso_auto_correction )
        .arg( llr->diso_ev_correction, 0, 'f', 4 )
        .arg( llr->diso_black_delta )
        .arg( llr->diso_averaging )
        .arg( llr->diso_alias_map )
        .arg( llr->diso_frblending )
        .arg( llr->dark_frame )
        .arg( getMlvBlackLevel( mlvObject ) )
        .arg( getMlvWhiteLevel( mlvObject ) )
    );
}
