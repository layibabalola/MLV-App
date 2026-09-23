#include "../common/minitest.h"

#include "../../platform/qt/PlaybackFramePopulationPolicy.h"

// CUDA-ATTRIBUTION-BASELINE-1 round 2 (astra major 4 / sol major 1): on
// 2bc8cc0a, requested_frames_by_serial (all render requests, including
// speculative lookaheads) was the only wrap-immune population figure, and it
// was used as the validation-gate denominator. With lookahead enabled, most
// of those requests are intentionally discarded before presentation, so
// every one of them read as "skipped" -- inflating loss far above the true
// figure. This test fails on that behavior (the target-only population
// would be indistinguishable from the all-requests population) and passes
// once the target-only counter excludes lookaheads.
TEST(PlaybackFramePopulationPolicy, LookaheadRequestsDoNotInflateTargetOnlySkipCount)
{
    // One target request per presented frame (20 presented), but each target
    // request also issues 2 speculative lookahead requests that are
    // discarded (never presented) -- 20 target + 40 lookahead = 60 total
    // serial advances, matching astra's repro shape ("~60 source frames").
    const uint64_t presentedFrames = 20;
    const uint64_t targetRequests = 20;
    const uint64_t lookaheadRequests = 40;
    const uint64_t allRequests = targetRequests + lookaheadRequests;

    const PlaybackFramePopulation population = PlaybackFramePopulationPolicy::compute(
        /*nextRenderRequestSerial=*/allRequests,
        /*startRequestSerial=*/0,
        /*nextTargetRenderRequestSerial=*/targetRequests,
        /*startTargetRequestSerial=*/0,
        presentedFrames );

    ASSERT_EQ(allRequests, population.requestedFramesBySerial);
    ASSERT_EQ(targetRequests, population.requestedTargetFramesBySerial);
    ASSERT_EQ(lookaheadRequests, population.lookaheadRequestsBySerial);
    // All 20 target requests were presented: nothing was actually skipped.
    ASSERT_EQ(uint64_t(0), population.skippedOrUnpresentedByTargetSerial);
    // The all-requests figure falsely reports 40 "skipped" frames -- every
    // discarded lookahead -- which is exactly the overcount this round
    // fixes. Kept for backward compatibility, not the gate.
    ASSERT_EQ(uint64_t(40), population.skippedOrUnpresentedBySerial);
}

TEST(PlaybackFramePopulationPolicy, NoLookaheadMakesBothPopulationsAgree)
{
    const PlaybackFramePopulation population = PlaybackFramePopulationPolicy::compute(
        /*nextRenderRequestSerial=*/30,
        /*startRequestSerial=*/10,
        /*nextTargetRenderRequestSerial=*/30,
        /*startTargetRequestSerial=*/10,
        /*presentedFrames=*/18 );

    ASSERT_EQ(uint64_t(20), population.requestedFramesBySerial);
    ASSERT_EQ(uint64_t(20), population.requestedTargetFramesBySerial);
    ASSERT_EQ(uint64_t(0), population.lookaheadRequestsBySerial);
    ASSERT_EQ(uint64_t(2), population.skippedOrUnpresentedBySerial);
    ASSERT_EQ(uint64_t(2), population.skippedOrUnpresentedByTargetSerial);
}

TEST(PlaybackFramePopulationPolicy, GenuineSkipsStillCountAgainstTargetPopulation)
{
    // 10 target requests issued, only 6 presented -- 4 genuinely skipped,
    // independent of any lookahead noise.
    const PlaybackFramePopulation population = PlaybackFramePopulationPolicy::compute(
        /*nextRenderRequestSerial=*/50,
        /*startRequestSerial=*/0,
        /*nextTargetRenderRequestSerial=*/10,
        /*startTargetRequestSerial=*/0,
        /*presentedFrames=*/6 );

    ASSERT_EQ(uint64_t(4), population.skippedOrUnpresentedByTargetSerial);
}

TEST(PlaybackFramePopulationPolicy, DeltaNeverGoesNegativeAcrossCounterReset)
{
    ASSERT_EQ(uint64_t(0), PlaybackFramePopulationPolicy::nonNegativeDelta(5, 10));
}
