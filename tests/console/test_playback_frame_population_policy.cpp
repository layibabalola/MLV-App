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

// CUDA-ATTRIBUTION-BASELINE-1 round 3 (astra major, prior finding 4 NOT
// RESOLVED): astra's own repro -- "advance approximately three source frames
// per target request and present all 20 issued requests across 60
// source-frame advances" -- reproduced verbatim. The gate figures computed
// from PlaybackFramePopulationPolicy::compute() alone (round 2's fix) read
// 0% loss here, because every one of the 20 target requests WAS presented;
// the 40 source frames that were skipped over before ever becoming a request
// are invisible to that struct. computeSourceFramePopulation() must surface
// them: never-requested must be exactly 40, and the buckets must sum back to
// the 60 offered.
TEST(PlaybackFramePopulationPolicy, AstraRepro_SkippedSourceFramesSurfaceAsNeverRequested)
{
    const double offeredNow = 60.0;
    const double offeredStart = 0.0;
    const uint64_t targetRequests = 20;
    const uint64_t lookaheadRequests = 0;
    const uint64_t presentedViaTarget = 20;
    const uint64_t presentedViaLookahead = 0;

    // The OLD (round 2) gate figure: every target request was presented, so
    // this reads zero loss -- exactly the false pass astra proved.
    const PlaybackFramePopulation legacyPopulation = PlaybackFramePopulationPolicy::compute(
        /*nextRenderRequestSerial=*/targetRequests,
        /*startRequestSerial=*/0,
        /*nextTargetRenderRequestSerial=*/targetRequests,
        /*startTargetRequestSerial=*/0,
        presentedViaTarget );
    ASSERT_EQ(uint64_t(0), legacyPopulation.skippedOrUnpresentedByTargetSerial);

    const PlaybackSourceFramePopulation population =
        PlaybackFramePopulationPolicy::computeSourceFramePopulation(
            offeredNow, offeredStart,
            targetRequests, lookaheadRequests,
            presentedViaTarget, presentedViaLookahead );

    ASSERT_EQ(uint64_t(60), population.offeredSourceFrames);
    ASSERT_EQ(uint64_t(40), population.neverRequestedSourceFrames);
    ASSERT_EQ(uint64_t(0), population.requestedThenSkippedTargetFrames);
    ASSERT_EQ(uint64_t(0), population.requestedThenDiscardedLookaheadFrames);
    ASSERT_EQ(uint64_t(20), population.presentedFrames);
    ASSERT_TRUE(population.partitionSound);
    // The four named buckets sum exactly back to the offered population --
    // proven here, not assumed.
    ASSERT_EQ(population.offeredSourceFrames,
              population.neverRequestedSourceFrames
              + population.requestedThenDiscardedLookaheadFrames
              + population.requestedThenSkippedTargetFrames
              + population.presentedFrames);
    // A run losing source frames must FAIL a 50%-loss gate where the old
    // target-serial-only figure passed it cleanly (66.7% > 50%).
    const double lossRatio =
        static_cast<double>( population.offeredSourceFrames - population.presentedFrames )
        / static_cast<double>( population.offeredSourceFrames );
    ASSERT_TRUE(lossRatio > 0.5);
}

TEST(PlaybackFramePopulationPolicy, SourcePopulation_NoLossIsFullyAccountedFor)
{
    // Normal (non-drop-frame) mode: every offered source frame is requested
    // and presented one at a time -- no skip is possible by construction.
    const PlaybackSourceFramePopulation population =
        PlaybackFramePopulationPolicy::computeSourceFramePopulation(
            /*timelineSourceFramesOfferedNow=*/25.0,
            /*timelineSourceFramesOfferedStart=*/5.0,
            /*requestedTargetFramesBySerialThisSession=*/20,
            /*lookaheadRequestsBySerialThisSession=*/0,
            /*presentedViaTargetFramesThisSession=*/20,
            /*presentedViaLookaheadFramesThisSession=*/0 );

    ASSERT_EQ(uint64_t(20), population.offeredSourceFrames);
    ASSERT_EQ(uint64_t(0), population.neverRequestedSourceFrames);
    ASSERT_EQ(uint64_t(0), population.requestedThenSkippedTargetFrames);
    ASSERT_EQ(uint64_t(0), population.requestedThenDiscardedLookaheadFrames);
    ASSERT_EQ(uint64_t(20), population.presentedFrames);
    ASSERT_TRUE(population.partitionSound);
}

TEST(PlaybackFramePopulationPolicy, SourcePopulation_DiscardedLookaheadAndSkippedTargetBothCounted)
{
    // 100 offered source frames; 70 got a target request but only 50 of
    // those were presented (20 genuinely skipped); 20 speculative lookahead
    // requests were issued, of which 5 were the ones actually presented
    // (15 discarded); the remaining 10 offered frames never got any request
    // at all (70 target + 20 lookahead = 90 request-touches, leaving 10 of
    // the 100 offered frames untouched by either).
    const PlaybackSourceFramePopulation population =
        PlaybackFramePopulationPolicy::computeSourceFramePopulation(
            /*timelineSourceFramesOfferedNow=*/100.0,
            /*timelineSourceFramesOfferedStart=*/0.0,
            /*requestedTargetFramesBySerialThisSession=*/70,
            /*lookaheadRequestsBySerialThisSession=*/20,
            /*presentedViaTargetFramesThisSession=*/50,
            /*presentedViaLookaheadFramesThisSession=*/5 );

    ASSERT_EQ(uint64_t(100), population.offeredSourceFrames);
    ASSERT_EQ(uint64_t(20), population.requestedThenSkippedTargetFrames);
    ASSERT_EQ(uint64_t(15), population.requestedThenDiscardedLookaheadFrames);
    ASSERT_EQ(uint64_t(55), population.presentedFrames);
    ASSERT_EQ(uint64_t(10), population.neverRequestedSourceFrames);
    ASSERT_TRUE(population.partitionSound);
    ASSERT_EQ(population.offeredSourceFrames,
              population.neverRequestedSourceFrames
              + population.requestedThenDiscardedLookaheadFrames
              + population.requestedThenSkippedTargetFrames
              + population.presentedFrames);
}

// CUDA-ATTRIBUTION-BASELINE-1 round 3: if a presentation is ever classified
// against a request class that issued fewer requests than were presented
// from it (an invariant violation elsewhere, not something this policy can
// happen on its own), the clamp must fire and partitionSound must go false
// -- the third-state/UNKNOWN rule -- rather than silently reporting a
// partition that merely sums by construction.
TEST(PlaybackFramePopulationPolicy, SourcePopulation_UnsoundInputsAreFlaggedNotHidden)
{
    const PlaybackSourceFramePopulation population =
        PlaybackFramePopulationPolicy::computeSourceFramePopulation(
            /*timelineSourceFramesOfferedNow=*/50.0,
            /*timelineSourceFramesOfferedStart=*/0.0,
            /*requestedTargetFramesBySerialThisSession=*/10,
            /*lookaheadRequestsBySerialThisSession=*/0,
            /*presentedViaTargetFramesThisSession=*/15, // more presented than requested
            /*presentedViaLookaheadFramesThisSession=*/0 );

    ASSERT_TRUE(!population.partitionSound);
}

TEST(PlaybackFramePopulationPolicy, SourcePopulation_OfferedAccumulatorIsWrapImmune)
{
    // A loop wrap only ever moves m_newPosDropMode; the RAW per-tick advance
    // added to m_playbackTimelineSourceFramesOffered is captured before that
    // subtraction, so the accumulator itself never decreases and a session
    // spanning several wraps still reports the true cumulative offered count.
    const double beforeWraps = 1000.0;
    const double afterThreeLapsOf24Frames = beforeWraps + ( 24.0 * 3.0 );

    const PlaybackSourceFramePopulation population =
        PlaybackFramePopulationPolicy::computeSourceFramePopulation(
            afterThreeLapsOf24Frames, beforeWraps,
            /*requestedTargetFramesBySerialThisSession=*/72,
            /*lookaheadRequestsBySerialThisSession=*/0,
            /*presentedViaTargetFramesThisSession=*/72,
            /*presentedViaLookaheadFramesThisSession=*/0 );

    ASSERT_EQ(uint64_t(72), population.offeredSourceFrames);
    ASSERT_EQ(uint64_t(0), population.neverRequestedSourceFrames);
    ASSERT_TRUE(population.partitionSound);
}
