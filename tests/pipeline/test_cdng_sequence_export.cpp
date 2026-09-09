#include "../../src/batch/CdngSequenceExport.h"
#include "../../src/batch/BatchContext.h"
#include "../../src/batch/BatchPrompts.h"
#include "../common/minitest.h"

#ifdef QT_WIDGETS_LIB
#error The pipeline test target must remain buildable without QtWidgets.
#endif

namespace {
struct RestoreBatchPromptFlags
{
    const bool batchMode = BatchContext::isBatchMode();
    const bool skipErrors = BatchContext::skipErrors();
    ~RestoreBatchPromptFlags()
    {
        BatchContext::setBatchMode(batchMode);
        BatchContext::setSkipErrors(skipErrors);
    }
};
}

TEST(CdngSequenceExport, BatchPromptSkipRequiresExplicitPolicy)
{
    RestoreBatchPromptFlags restore;
    BatchContext::setBatchMode(true);
    BatchContext::setSkipErrors(false);
    ASSERT_FALSE(BatchPrompts::shouldSkipFrame("fixture", 3, "write failed"));
    BatchContext::setSkipErrors(true);
    ASSERT_TRUE(BatchPrompts::shouldSkipFrame("fixture", 3, "write failed"));
    ASSERT_FALSE(BatchPrompts::shouldContinue("fixture", "disk full"));
}

TEST(CdngSequenceExport, UnavailableGuiPromptsAbortWithoutCallback)
{
    RestoreBatchPromptFlags restore;
    BatchContext::setBatchMode(false);
    BatchContext::setSkipErrors(true);
    ASSERT_FALSE(BatchPrompts::shouldSkipFrame("fixture", 3, "write failed"));
    ASSERT_FALSE(BatchPrompts::shouldContinue("fixture", "disk full"));
}

TEST(CdngSequenceExport, LookAssistAnchorUsesOriginalCutIn)
{
    ASSERT_EQ(CdngSequenceExport::lookAssistAnalysisFrameIndex(1, 20), 0u);
    ASSERT_EQ(CdngSequenceExport::lookAssistAnalysisFrameIndex(9, 20), 8u);
}

TEST(CdngSequenceExport, StretchHelpersPreserveReceiptAndRawAspect)
{
    double x = 0.0;
    double y = 0.0;
    CdngSequenceExport::effectiveStretchFactors(-1.0, -1.0, 3.0f, &x, &y);
    ASSERT_NEAR(x, STRETCH_H_100, 0.0001);
    ASSERT_NEAR(y, STRETCH_V_300, 0.0001);
    CdngSequenceExport::effectiveStretchFactors(1.0, 1.0, 3.0f, &x, &y);
    ASSERT_NEAR(x, STRETCH_H_100, 0.0001);
    ASSERT_NEAR(y, STRETCH_V_300, 0.0001);
}

TEST(CdngSequenceExport, CutOutClampKeepsInvalidRanges)
{
    ASSERT_EQ(CdngSequenceExport::cutOutClampedForMaxFrames(0, 9, 2), 9u);
    ASSERT_EQ(CdngSequenceExport::cutOutClampedForMaxFrames(4, 9, 2), 5u);
    ASSERT_EQ(CdngSequenceExport::cutOutClampedForMaxFrames(4, 3, 2), 3u);
}

