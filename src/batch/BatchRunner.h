#ifndef BATCHRUNNER_H
#define BATCHRUNNER_H

#include <QString>
#include "BatchTypes.h"
#include "RawAspectStretchPolicy.h"

#include <cstdint>

class ReceiptSettings;

/* Orchestrates headless batch CDNG export.
 * Called from main.cpp after CLI args are parsed.
 * Opens each MLV, calls MainWindow::exportCdngSequence(), logs results. */
class BatchRunner
{
public:
    /* Run the batch export.
     * inputPath: single .mlv file or folder of .mlv files
     * outputPath: root output directory
     * Returns process exit code (see CLAUDE.md exit code table). */
    static int run(const QString &inputPath, const QString &outputPath);

    /* Pick the 0-based frame that headless Look Assist analyzes to generate the
     * clip-wide DNG look defaults (BaselineExposure / AsShotNeutral / raw black
     * and white levels).
     *
     * It MUST be the ORIGINAL receipt cut-in (cutIn - 1), never effectiveCutIn.
     * The --resume flag advances effectiveCutIn past frames a previous run
     * already exported, so anchoring analysis on effectiveCutIn would make a
     * resumed run bake DIFFERENT look defaults into the remaining DNGs than the
     * first run baked into the earlier frames of the same sequence. Look defaults
     * are clip-wide, so the anchor must be stable across separate (and resumed)
     * process invocations. effectiveCutIn is accepted only to document the value
     * that must be ignored here; defined inline so it is unit-testable without
     * linking the GUI-dependent BatchRunner.cpp. */
    static uint32_t lookAssistAnalysisFrameIndex( uint32_t cutIn,
                                                  uint32_t /*effectiveCutIn*/ )
    {
        return cutIn > 0 ? cutIn - 1 : 0;
    }

    static uint32_t cutOutClampedForMaxFrames( uint32_t cutIn,
                                               uint32_t cutOut,
                                               uint32_t maxFrames )
    {
        if( maxFrames == 0 || cutIn == 0 || cutOut < cutIn )
        {
            return cutOut;
        }

        const uint64_t limitedCutOut =
            static_cast<uint64_t>( cutIn ) + static_cast<uint64_t>( maxFrames ) - 1;
        if( limitedCutOut < static_cast<uint64_t>( cutOut ) )
        {
            return static_cast<uint32_t>( limitedCutOut );
        }
        return cutOut;
    }

    /* Horizontal/vertical stretch actually used for an export, given what the receipt
     * carries and what the clip's RAWC block reports.
     *
     * A receipt that was never loaded leaves stretch at -1. The GUI fills that gap on
     * first load (MainWindow::setSliders) by deriving the vertical stretch from the
     * clip's binning/skipping aspect, and a headless export that skips the same step
     * emits squeezed output for binned modes (5D3 1080p is binning_x=3/binning_y=1 ->
     * needs a 3:1 stretch). Both the CDNG runner and the rendered-video runner need the
     * identical answer, so the exact horizontal/vertical pair lives in the shared
     * RawAspectStretchPolicy rather than being written twice. Header-inline so tests can pin it
     * without linking the GUI-dependent BatchRunner.cpp.
     *
     * clipAspectRatio is getMlvAspectRatio() (sampling_y/sampling_x); 0 means the clip
     * carries no RAWC info and is treated as square. */
    static double effectiveStretchFactorX( double receiptStretchX )
    {
        return receiptStretchX > 0.0 ? receiptStretchX : STRETCH_H_100;
    }

    static double effectiveStretchFactorY( double receiptStretchY,
                                           float clipAspectRatio )
    {
        if( receiptStretchY > 0.0 )
            return receiptStretchY;

        float aspectV = clipAspectRatio;
        if( aspectV == 0.0f ) aspectV = 1.0f; /* no RAWC info -> treat as square */
        if( aspectV > 0.9f && aspectV < 1.1f )      return STRETCH_V_100;
        if( aspectV > 1.6f && aspectV < 1.7f )      return STRETCH_V_167;
        if( aspectV > 2.9f && aspectV < 3.1f )      return STRETCH_V_300;
        return STRETCH_V_033;
    }

    static void effectiveStretchFactors( double receiptStretchX,
                                         double receiptStretchY,
                                         float clipAspectRatio,
                                         double * stretchX,
                                         double * stretchY )
    {
        if( !stretchX || !stretchY ) return;
        const double aspect = clipAspectRatio == 0.0f ? 1.0
                                                       : clipAspectRatio;
        const RawAspectStretchSelection selection =
            rawAspectStretchSelectionForRatio( aspect );
        constexpr double neutralTolerance = 0.0001;
        const bool neutralReceipt = receiptStretchX > 0.0
            && receiptStretchY > 0.0
            && std::fabs( receiptStretchX - 1.0 ) <= neutralTolerance
            && std::fabs( receiptStretchY - 1.0 ) <= neutralTolerance;
        if( selection.valid && ( receiptStretchY <= 0.0 || neutralReceipt ) )
        {
            *stretchX = selection.horizontalFactor;
            *stretchY = selection.verticalFactor;
            return;
        }
        *stretchX = effectiveStretchFactorX( receiptStretchX );
        *stretchY = effectiveStretchFactorY( receiptStretchY,
                                              clipAspectRatio );
    }

    static BatchRenderedVideoSourceMetadata renderedVideoSourceMetadataFromClipState(
        int width,
        int height,
        double frameRate,
        double receiptStretchX,
        double receiptStretchY,
        int frameCount = 0 )
    {
        if( receiptStretchX <= 0.0 )
            receiptStretchX = STRETCH_H_100;
        if( receiptStretchY <= 0.0 )
            receiptStretchY = STRETCH_V_100;

        return batchRenderedVideoSourceMetadata(
            width,
            height,
            frameRate,
            receiptStretchX,
            receiptStretchY,
            frameCount );
    }

private:
    BatchRunner() = delete; /* Pure static */

    /* Export a single MLV file.  receipt may be default-constructed
     * (no receipt loaded) or populated from .marxml parsing.
     * Returns ProcessResult. */
    static ProcessResult exportSingleFile(const QString &mlvPath,
                                          const QString &outputRoot,
                                          ReceiptSettings *receipt);

    /* Export ONE clip to ONE rendered-video file (E4-1, H.264 first).
     *
     * Opens the clip, applies the receipt through the same ReceiptApplier the CDNG
     * runner uses, completes the rendered job plan against the clip's real geometry,
     * pipes RGB48 frames into ffmpeg through export_process::StreamingPipeline, writes
     * atomically via batchRenderedVideoPartialOutputPath, then verifies the result with
     * the planned media probe. Emits no dialog and pumps no event loop.
     *
     * Returns a process exit code from the CLAUDE.md table: 0 success, 2 usage/plan
     * refusal, 3 input unreadable, 4 export failure. */
    static int exportRenderedVideoFile(
        const QString &mlvPath,
        ReceiptSettings *receipt,
        const BatchRenderedVideoJobPlan &preflightPlan,
        const BatchRenderedVideoRenderSettings &renderSettings);
};

#endif // BATCHRUNNER_H
