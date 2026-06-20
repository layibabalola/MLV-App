#ifndef BATCHRUNNER_H
#define BATCHRUNNER_H

#include <QString>
#include "BatchTypes.h"

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

    static BatchRenderedVideoSourceMetadata renderedVideoSourceMetadataFromClipState(
        int width,
        int height,
        double frameRate,
        double receiptStretchX,
        double receiptStretchY )
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
            receiptStretchY );
    }

private:
    BatchRunner() = delete; /* Pure static */

    /* Export a single MLV file.  receipt may be default-constructed
     * (no receipt loaded) or populated from .marxml parsing.
     * Returns ProcessResult. */
    static ProcessResult exportSingleFile(const QString &mlvPath,
                                          const QString &outputRoot,
                                          ReceiptSettings *receipt);
};

#endif // BATCHRUNNER_H
