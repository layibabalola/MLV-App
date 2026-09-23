#include "../common/minitest.h"

#include "../../platform/qt/PlaybackFramePopulationPolicy.h"
#include "../../platform/qt/PlaybackPresentedFrameIdentityTracker.h"

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
            targetRequests, /*reusedLookaheadTargetFrames=*/0, lookaheadRequests,
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
            /*reusedLookaheadTargetFramesThisSession=*/0,
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
            /*reusedLookaheadTargetFramesThisSession=*/0,
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
            /*reusedLookaheadTargetFramesThisSession=*/0,
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
            /*reusedLookaheadTargetFramesThisSession=*/0,
            /*lookaheadRequestsBySerialThisSession=*/0,
            /*presentedViaTargetFramesThisSession=*/72,
            /*presentedViaLookaheadFramesThisSession=*/0 );

    ASSERT_EQ(uint64_t(72), population.offeredSourceFrames);
    ASSERT_EQ(uint64_t(0), population.neverRequestedSourceFrames);
    ASSERT_TRUE(population.partitionSound);
}

// CUDA-ATTRIBUTION-BASELINE-1 round 4 (astra major, round-3 PARTIAL):
// astra's own repro -- offered=60, target attempts=20 (all satisfied by
// reusing an already-ready lookahead), lookahead requests=20, target
// presentations=0, lookahead presentations=20. Round-3 code (reuse count
// folded into genuine target demand) reported never_requested=20 and
// requested_then_skipped_target=20 -- 20 phantom "skips" for target attempts
// that were in fact all satisfied, and 20 source frames wrongly hidden from
// never_requested. The reuse count must be backed out so a reused attempt
// lands in exactly one real bucket (here: fully absorbed by the lookahead
// presentation it rode on) rather than manufacturing a fictitious skip.
TEST(PlaybackFramePopulationPolicy, SourcePopulation_ReusedLookaheadTargetAttemptsAreNotSkips)
{
    const PlaybackSourceFramePopulation population =
        PlaybackFramePopulationPolicy::computeSourceFramePopulation(
            /*timelineSourceFramesOfferedNow=*/60.0,
            /*timelineSourceFramesOfferedStart=*/0.0,
            /*requestedTargetFramesBySerialThisSession=*/20,
            /*reusedLookaheadTargetFramesThisSession=*/20,
            /*lookaheadRequestsBySerialThisSession=*/20,
            /*presentedViaTargetFramesThisSession=*/0,
            /*presentedViaLookaheadFramesThisSession=*/20 );

    ASSERT_EQ(uint64_t(60), population.offeredSourceFrames);
    // Astra's correct attribution: all 20 target attempts were reuse, so
    // genuine target demand is zero -- nothing was skipped on the target
    // side.
    ASSERT_EQ(uint64_t(0), population.requestedThenSkippedTargetFrames);
    ASSERT_EQ(uint64_t(0), population.requestedThenDiscardedLookaheadFrames);
    ASSERT_EQ(uint64_t(20), population.presentedFrames);
    // The 40 source frames nothing ever requested (neither a genuine target
    // attempt nor a lookahead) must surface, not be absorbed into a
    // fictitious skipped-target bucket.
    ASSERT_EQ(uint64_t(40), population.neverRequestedSourceFrames);
    ASSERT_TRUE(population.partitionSound);
    ASSERT_EQ(population.offeredSourceFrames,
              population.neverRequestedSourceFrames
              + population.requestedThenDiscardedLookaheadFrames
              + population.requestedThenSkippedTargetFrames
              + population.presentedFrames);
}

// Same successful-reuse scenario, but with offered=20 instead of 60. Round-3
// code produced partition_sound=false here (accountedFor overshot offered)
// even though nothing was actually wrong -- a false failure caused by the
// same bug that produced a false pass in the offered=60 case above. Both
// must resolve consistently once reuse is backed out correctly.
TEST(PlaybackFramePopulationPolicy, SourcePopulation_ReusedLookaheadTargetAttemptsStaySoundAtSmallerOfferedCount)
{
    const PlaybackSourceFramePopulation population =
        PlaybackFramePopulationPolicy::computeSourceFramePopulation(
            /*timelineSourceFramesOfferedNow=*/20.0,
            /*timelineSourceFramesOfferedStart=*/0.0,
            /*requestedTargetFramesBySerialThisSession=*/20,
            /*reusedLookaheadTargetFramesThisSession=*/20,
            /*lookaheadRequestsBySerialThisSession=*/20,
            /*presentedViaTargetFramesThisSession=*/0,
            /*presentedViaLookaheadFramesThisSession=*/20 );

    ASSERT_EQ(uint64_t(20), population.offeredSourceFrames);
    ASSERT_EQ(uint64_t(0), population.requestedThenSkippedTargetFrames);
    ASSERT_EQ(uint64_t(0), population.requestedThenDiscardedLookaheadFrames);
    ASSERT_EQ(uint64_t(20), population.presentedFrames);
    ASSERT_EQ(uint64_t(0), population.neverRequestedSourceFrames);
    ASSERT_TRUE(population.partitionSound);
}

// If reuse accounting itself is broken (more reuse claimed than target
// requests issued -- an invariant violation this policy cannot cause on its
// own but must not hide), the third-state/UNKNOWN rule applies: fail closed
// rather than silently clamping to a plausible-looking number.
TEST(PlaybackFramePopulationPolicy, SourcePopulation_ReuseCountExceedingRequestsIsFlaggedNotHidden)
{
    const PlaybackSourceFramePopulation population =
        PlaybackFramePopulationPolicy::computeSourceFramePopulation(
            /*timelineSourceFramesOfferedNow=*/30.0,
            /*timelineSourceFramesOfferedStart=*/0.0,
            /*requestedTargetFramesBySerialThisSession=*/10,
            /*reusedLookaheadTargetFramesThisSession=*/15, // more reuse than requests
            /*lookaheadRequestsBySerialThisSession=*/0,
            /*presentedViaTargetFramesThisSession=*/0,
            /*presentedViaLookaheadFramesThisSession=*/0 );

    ASSERT_TRUE(!population.partitionSound);
}

// CUDA-ATTRIBUTION-BASELINE-1 round 5 (sol BLOCKER, made executable): sol's
// repro, verbatim -- 60 offered source frames, 60 presentation ATTEMPTS
// (target requests), but the frames actually shown touch only 20 distinct
// displayFrame indices (each one presented 3 times, as a drop-frame
// catch-up silently re-presenting a stale frame instead of advancing would
// produce). Nothing upstream of notePlaybackSmokePresentedFrame() dedups by
// displayFrame before it counts a presentation, so a plain event count
// cannot tell this apart from 60 distinct frames shown once each.
//
// This test reproduces the FALSE PASS by feeding computeSourceFramePopulation
// the raw event count (60) exactly as pre-round-5 MainWindow did -- no
// identity tracking involved. It offered=60, presented=60, 0% loss,
// partition_sound=true: sol's blocker, confirmed live on this policy.
TEST(PlaybackFramePopulationPolicy, SolRepro_RawPresentationEventCountFalselyReportsZeroLoss)
{
    const uint64_t offered = 60;
    const uint64_t targetRequests = 60;
    const uint64_t rawPresentationEventCount = 60; // NOT deduped by source-frame identity

    const PlaybackSourceFramePopulation population =
        PlaybackFramePopulationPolicy::computeSourceFramePopulation(
            /*timelineSourceFramesOfferedNow=*/static_cast<double>(offered),
            /*timelineSourceFramesOfferedStart=*/0.0,
            targetRequests,
            /*reusedLookaheadTargetFramesThisSession=*/0,
            /*lookaheadRequestsBySerialThisSession=*/0,
            rawPresentationEventCount,
            /*presentedViaLookaheadFramesThisSession=*/0 );

    ASSERT_EQ(uint64_t(60), population.presentedFrames);
    ASSERT_EQ(uint64_t(0), population.requestedThenSkippedTargetFrames);
    ASSERT_TRUE(population.partitionSound);
    const double lossRatio =
        static_cast<double>(population.offeredSourceFrames - population.presentedFrames)
        / static_cast<double>(population.offeredSourceFrames);
    ASSERT_TRUE(lossRatio == 0.0);
}

// Same 60-offered/60-attempt session as above, but this time the presented
// count going into the policy comes from PlaybackPresentedFrameIdentityTracker
// -- the round-5 fix -- fed the same 60 presentation events, 20 of them
// distinct, exactly as notePlaybackSmokePresentedFrame() now does via
// MainWindow::m_playbackSmokePresentedFrameIdentity. The other 40 offered
// source frames must come back accounted for as genuinely unaccounted
// (requested_then_skipped_target_frames), not silently folded into
// "presented". Reversing the mechanism (feeding the raw event count, as in
// the test above, instead of the tracker's deduped count) makes every
// assertion below fail -- verified by hand per this round's standing check.
TEST(PlaybackFramePopulationPolicy, SolRepro_DistinctFrameIdentityTrackerSurfacesTheDuplicatePresentationLoss)
{
    PlaybackPresentedFrameIdentityTracker identity;
    for (uint64_t i = 0; i < 60; ++i)
    {
        const uint64_t displayFrame = i % 20; // only 20 distinct source frames, each shown 3x
        identity.notePresentedFrame(displayFrame, /*viaLookahead=*/false);
    }
    ASSERT_EQ(uint64_t(20), identity.distinctTargetPresentedCount());
    ASSERT_EQ(uint64_t(0), identity.distinctLookaheadPresentedCount());

    const uint64_t offered = 60;
    const uint64_t targetRequests = 60;

    const PlaybackSourceFramePopulation population =
        PlaybackFramePopulationPolicy::computeSourceFramePopulation(
            /*timelineSourceFramesOfferedNow=*/static_cast<double>(offered),
            /*timelineSourceFramesOfferedStart=*/0.0,
            targetRequests,
            /*reusedLookaheadTargetFramesThisSession=*/0,
            /*lookaheadRequestsBySerialThisSession=*/0,
            identity.distinctTargetPresentedCount(),
            identity.distinctLookaheadPresentedCount() );

    ASSERT_EQ(uint64_t(60), population.offeredSourceFrames);
    // Only 20 distinct source frames actually reached presentation -- the
    // repeated re-presentations of the same 20 are not new information and
    // must not inflate this bucket to 60.
    ASSERT_EQ(uint64_t(20), population.presentedFrames);
    // The other 40 offered source frames: 60 target attempts, only 20
    // distinct ones ever shown, so 40 come back requested-then-skipped.
    ASSERT_EQ(uint64_t(40), population.requestedThenSkippedTargetFrames);
    ASSERT_EQ(uint64_t(0), population.neverRequestedSourceFrames);
    ASSERT_TRUE(population.partitionSound);
    ASSERT_EQ(population.offeredSourceFrames,
              population.neverRequestedSourceFrames
              + population.requestedThenDiscardedLookaheadFrames
              + population.requestedThenSkippedTargetFrames
              + population.presentedFrames);
    // The true loss -- 66.7%, matching sol's repro -- is now visible, where
    // the raw-event-count test above reported 0%.
    const double lossRatio =
        static_cast<double>(population.offeredSourceFrames - population.presentedFrames)
        / static_cast<double>(population.offeredSourceFrames);
    ASSERT_TRUE(lossRatio > 0.5);
}

// The identity tracker must also keep target- and lookahead-origin
// presentations in separate sets -- the same displayFrame value presented
// once via each origin is two distinct pieces of information (a target
// request and a lookahead both reached that source frame), not one.
TEST(PlaybackFramePopulationPolicy, IdentityTracker_TargetAndLookaheadOriginsAreCountedIndependently)
{
    PlaybackPresentedFrameIdentityTracker identity;
    identity.notePresentedFrame(/*displayFrame=*/7, /*viaLookahead=*/false);
    identity.notePresentedFrame(/*displayFrame=*/7, /*viaLookahead=*/true);
    identity.notePresentedFrame(/*displayFrame=*/7, /*viaLookahead=*/false); // duplicate, same origin

    ASSERT_EQ(uint64_t(1), identity.distinctTargetPresentedCount());
    ASSERT_EQ(uint64_t(1), identity.distinctLookaheadPresentedCount());
}

// reset() must fully clear both origin sets -- a stale entry surviving into
// the next playback-smoke session would undercount that session's loss.
TEST(PlaybackFramePopulationPolicy, IdentityTracker_ResetClearsBothOriginSets)
{
    PlaybackPresentedFrameIdentityTracker identity;
    identity.notePresentedFrame(/*displayFrame=*/1, /*viaLookahead=*/false);
    identity.notePresentedFrame(/*displayFrame=*/2, /*viaLookahead=*/true);
    identity.reset();

    ASSERT_EQ(uint64_t(0), identity.distinctTargetPresentedCount());
    ASSERT_EQ(uint64_t(0), identity.distinctLookaheadPresentedCount());

    identity.notePresentedFrame(/*displayFrame=*/1, /*viaLookahead=*/false);
    ASSERT_EQ(uint64_t(1), identity.distinctTargetPresentedCount());
}
