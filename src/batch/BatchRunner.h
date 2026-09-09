#ifndef BATCHRUNNER_H
#define BATCHRUNNER_H

#include <QString>
#include "BatchTypes.h"
#include "RawAspectStretchPolicy.h"
#include "CdngSequenceExport.h"

#include <cstdint>

class ReceiptSettings;

/* Orchestrates headless batch CDNG export.
 * Called from main.cpp after CLI args are parsed.
 * Opens each MLV, calls CdngSequenceExport::exportCdngSequence(), logs results. */
class BatchRunner
{
public:
    /* Run the batch export.
     * inputPath: single .mlv file or folder of .mlv files
     * outputPath: root output directory
     * Returns process exit code (see CLAUDE.md exit code table). */
    static int run(const QString &inputPath, const QString &outputPath);

    /* Compatibility forwards for existing console callers; implementation lives in the GUI-free exporter. */
    static uint32_t lookAssistAnalysisFrameIndex(uint32_t cutIn, uint32_t effectiveCutIn)
    { return CdngSequenceExport::lookAssistAnalysisFrameIndex(cutIn, effectiveCutIn); }
    static uint32_t cutOutClampedForMaxFrames(uint32_t cutIn, uint32_t cutOut, uint32_t maxFrames)
    { return CdngSequenceExport::cutOutClampedForMaxFrames(cutIn, cutOut, maxFrames); }
    static double effectiveStretchFactorX(double x)
    { return CdngSequenceExport::effectiveStretchFactorX(x); }
    static double effectiveStretchFactorY(double y, float aspect)
    { return CdngSequenceExport::effectiveStretchFactorY(y, aspect); }
    static void effectiveStretchFactors(double x, double y, float aspect, double *outX, double *outY)
    { CdngSequenceExport::effectiveStretchFactors(x, y, aspect, outX, outY); }
    static BatchRenderedVideoSourceMetadata renderedVideoSourceMetadataFromClipState(int width, int height, double frameRate, double x, double y, int frameCount = 0)
    { return CdngSequenceExport::renderedVideoSourceMetadataFromClipState(width, height, frameRate, x, y, frameCount); }

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
