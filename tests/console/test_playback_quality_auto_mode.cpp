/* Phase 4E: GUI-grade Playback Quality dial — Auto-mode cadence sampler.
 *
 * Verifies the PlaybackQualityAutoSampler's adaptive logic:
 *   - Until 16 frames are recorded, the sampler optimistically picks HQ x4.
 *   - With cadences below frame budget, sampler stays at HQ x4 (steady).
 *   - With cadences above frame budget, Sharp/Smooth downgrades to Fast x4.
 *   - After warmup, Aggressive Dual ISO switches to HQ x8.
 *   - With cadences above frame budget, Aggressive non-DI switches to HQ x8.
 *   - With huge headroom and no validated capability, sampler holds HQ x4.
 *   - With huge headroom plus validated capability, sampler upgrades to HQ x2.
 *   - Sharp/Smooth Dual-ISO clips never get x2; they stay at x4 even with headroom.
 *   - reset() clears the window and re-enters optimistic warmup. */

#include "../common/minitest.h"

#include "../../platform/qt/PlaybackQualityPolicy.h"

namespace
{

constexpr size_t kWin = PlaybackQualityAutoSampler::kSlidingWindow;

void feed_n( PlaybackQualityAutoSampler & s, double ms, size_t n )
{
    for ( size_t i = 0; i < n; ++i ) s.recordFrameMs( ms );
}

void expect_reason( const char * expected,
                    PlaybackQualityAutoDecisionReason actual )
{
    ASSERT_EQ( std::string( expected ),
               std::string( playbackQualityAutoDecisionReasonName( actual ) ) );
}

} // namespace

TEST(PlaybackQualityModeOverride, ParsesNumbersAndNames)
{
    int mode = -1;
    ASSERT_TRUE( playbackQualityModeParseOverride( "0", &mode ) );
    ASSERT_EQ( static_cast<int>( PlaybackQualityMode::Fast ), mode );

    ASSERT_TRUE( playbackQualityModeParseOverride( "prioritize-smoothness", &mode ) );
    ASSERT_EQ( static_cast<int>( PlaybackQualityMode::Fast ), mode );

    ASSERT_TRUE( playbackQualityModeParseOverride( "HQ", &mode ) );
    ASSERT_EQ( static_cast<int>( PlaybackQualityMode::HighQuality ), mode );

    ASSERT_TRUE( playbackQualityModeParseOverride( "prioritize_quality", &mode ) );
    ASSERT_EQ( static_cast<int>( PlaybackQualityMode::HighQuality ), mode );

    ASSERT_TRUE( playbackQualityModeParseOverride( "auto", &mode ) );
    ASSERT_EQ( static_cast<int>( PlaybackQualityMode::Auto ), mode );

    ASSERT_TRUE( playbackQualityModeParseOverride( "phase3-fast", &mode ) );
    ASSERT_EQ( static_cast<int>( PlaybackQualityMode::Phase3Fast ), mode );

    ASSERT_TRUE( playbackQualityModeParseOverride( "phase3_hq", &mode ) );
    ASSERT_EQ( static_cast<int>( PlaybackQualityMode::Phase3HQ ), mode );
}

TEST(PlaybackQualityModeOverride, RejectsInvalidValues)
{
    int mode = 123;
    ASSERT_FALSE( playbackQualityModeParseOverride( "", &mode ) );
    ASSERT_EQ( 123, mode );

    ASSERT_FALSE( playbackQualityModeParseOverride( "turbo", &mode ) );
    ASSERT_EQ( 123, mode );

    ASSERT_FALSE( playbackQualityModeParseOverride( "8", &mode ) );
    ASSERT_EQ( 123, mode );
}

TEST(PlaybackQualityAutoTelemetry, ConvertsMillisecondsToFpsEquivalent)
{
    ASSERT_NEAR( 62.5, playbackQualityFpsEquivalentForFrameMs( 16.0 ), 0.001 );
    ASSERT_NEAR( 30.0, playbackQualityFpsEquivalentForFrameMs( 1000.0 / 30.0 ), 0.001 );
    ASSERT_NEAR( 0.0, playbackQualityFpsEquivalentForFrameMs( 0.0 ), 0.001 );
    ASSERT_NEAR( 0.0, playbackQualityFpsEquivalentForFrameMs( -5.0 ), 0.001 );
}

TEST(PlaybackQualityAutoSampler, OptimisticUntilWindowFull)
{
    PlaybackQualityAutoSampler s;
    /* No samples yet — should pick HQ x4. */
    auto d = s.decideNextSlot( 30, /*dualIsoActive*/false );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
    expect_reason( "warmup_hq", d.reason );
    ASSERT_FALSE( d.sharperHeadroomScaleAllowed );
    ASSERT_EQ( 0u, d.sampleCount );
    ASSERT_NEAR( 1000.0 / 30.0, d.frameBudgetMs, 0.001 );
    ASSERT_NEAR( 0.0, d.averageFrameMs, 0.001 );

    /* Even after 15 frames (one short of full window), still optimistic. */
    feed_n( s, 100.0, kWin - 1 ); /* 100 ms is way over budget for 30 fps */
    d = s.decideNextSlot( 30, false );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
    expect_reason( "warmup_hq", d.reason );
    ASSERT_EQ( kWin - 1, d.sampleCount );
}

TEST(PlaybackQualityAutoSampler, AggressiveDualIsoStartsAtHqx8)
{
    PlaybackQualityAutoSampler s;
    auto d = s.decideNextSlot( 30,
                               /*dualIsoActive*/true,
                               /*aggressivePreviewActive*/true );
    ASSERT_EQ( 8, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
    expect_reason( "aggressive_dual_iso_deep_hq", d.reason );

    /* A partial warmup window should also stay in the deep early-reduced
     * preview instead of spending the first slot in the slower x4 state. */
    feed_n( s, 80.0, kWin - 1 );
    d = s.decideNextSlot( 30,
                          /*dualIsoActive*/true,
                          /*aggressivePreviewActive*/true );
    ASSERT_EQ( 8, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
    expect_reason( "aggressive_dual_iso_deep_hq", d.reason );
    ASSERT_EQ( kWin - 1, d.sampleCount );
}

TEST(PlaybackQualityAutoSampler, DowngradesToFastOnCadenceMiss)
{
    PlaybackQualityAutoSampler s;
    /* 30 fps target = 33.33 ms budget. Feed 80 ms cadence (way over). */
    feed_n( s, 80.0, kWin );
    auto d = s.decideNextSlot( 30, /*dualIsoActive*/false );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_FALSE( d.useHqMean23 );
    expect_reason( "missed_target_fast", d.reason );
    ASSERT_EQ( kWin, d.sampleCount );
    ASSERT_NEAR( 80.0, d.averageFrameMs, 0.001 );

    /* Same logic at 24 fps target. */
    PlaybackQualityAutoSampler s24;
    feed_n( s24, 60.0, kWin );
    d = s24.decideNextSlot( 24, false );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_FALSE( d.useHqMean23 );
    expect_reason( "missed_target_fast", d.reason );
    ASSERT_NEAR( 1000.0 / 24.0, d.frameBudgetMs, 0.001 );
}

TEST(PlaybackQualityAutoSampler, AggressiveCadenceMissUsesHqx8)
{
    PlaybackQualityAutoSampler s;
    feed_n( s, 80.0, kWin );
    auto d = s.decideNextSlot( 30,
                               /*dualIsoActive*/true,
                               /*aggressivePreviewActive*/true );
    ASSERT_EQ( 8, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );

    PlaybackQualityAutoSampler nonDi;
    feed_n( nonDi, 80.0, kWin );
    d = nonDi.decideNextSlot( 30,
                              /*dualIsoActive*/false,
                              /*aggressivePreviewActive*/true );
    ASSERT_EQ( 8, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
    expect_reason( "missed_target_aggressive_deep_hq", d.reason );
}

TEST(PlaybackQualityAutoSampler, AggressiveDualIsoUsesHqx8AfterWarmup)
{
    PlaybackQualityAutoSampler s;
    /* 24 fps target = 41.67 ms. This is meeting target, but Aggressive
     * Dual ISO still chooses the deeper early-reduced preview after warmup. */
    feed_n( s, 35.0, kWin );
    auto d = s.decideNextSlot( 24,
                               /*dualIsoActive*/true,
                               /*aggressivePreviewActive*/true );
    ASSERT_EQ( 8, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );

    PlaybackQualityAutoSampler nonDi;
    feed_n( nonDi, 35.0, kWin );
    d = nonDi.decideNextSlot( 24,
                              /*dualIsoActive*/false,
                              /*aggressivePreviewActive*/true );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
    expect_reason( "steady_hq", d.reason );
}

TEST(PlaybackQualityAutoSampler, StaysAtHqx4WhenMeetingTarget)
{
    PlaybackQualityAutoSampler s;
    /* 30 fps target = 33.33 ms; feed exactly at budget — within tolerance. */
    feed_n( s, 33.0, kWin );
    auto d = s.decideNextSlot( 30, /*dualIsoActive*/false );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
    expect_reason( "steady_hq", d.reason );

    /* Slightly over budget but within +10% tolerance — also stay at HQ x4. */
    PlaybackQualityAutoSampler s2;
    feed_n( s2, 36.0, kWin ); /* 36 ms vs 33.33 ms budget = +8% */
    d = s2.decideNextSlot( 30, false );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
    expect_reason( "steady_hq", d.reason );
}

TEST(PlaybackQualityAutoSampler, UpgradesToHqx2OnNonDIWithHeadroom)
{
    PlaybackQualityAutoSampler s;
    /* 30 fps target = 33.33 ms; feed 15 ms (way under budget). */
    feed_n( s, 15.0, kWin );
    auto d = s.decideNextSlot( 30,
                               /*dualIsoActive*/false,
                               /*aggressivePreviewActive*/false,
                               /*sharperHeadroomScaleAllowed*/true );
    ASSERT_EQ( 2, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
    expect_reason( "headroom_non_dual_iso_sharper_hq", d.reason );
    ASSERT_TRUE( d.sharperHeadroomScaleAllowed );
}

TEST(PlaybackQualityAutoSampler, HoldsHqx4UntilHeadroomCapabilityIsValidated)
{
    PlaybackQualityAutoSampler s;
    feed_n( s, 15.0, kWin );
    auto d = s.decideNextSlot( 30, /*dualIsoActive*/false );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
    expect_reason( "headroom_waiting_for_validated_capability", d.reason );
    ASSERT_FALSE( d.sharperHeadroomScaleAllowed );
}

TEST(PlaybackQualityAutoCapabilityTracker, LatchesOnlyAfterValidatedNoReadback)
{
    PlaybackQualityAutoCapabilityTracker tracker;
    ASSERT_FALSE( tracker.validatedNoReadbackObserved() );
    ASSERT_FALSE( tracker.sharperHeadroomScaleAllowed() );
    ASSERT_FALSE( tracker.lastObservationDemotedCapability() );

    ASSERT_FALSE( tracker.notePresentedPipeline( false ) );
    ASSERT_FALSE( tracker.validatedNoReadbackObserved() );
    ASSERT_FALSE( tracker.sharperHeadroomScaleAllowed() );
    ASSERT_FALSE( tracker.lastObservationDemotedCapability() );

    ASSERT_FALSE( tracker.notePresentedPipeline( false,
                                                 /*gpuTextureNoReadbackCandidate*/true ) );
    ASSERT_FALSE( tracker.validatedNoReadbackObserved() );
    ASSERT_FALSE( tracker.lastObservationDemotedCapability() );

    ASSERT_TRUE( tracker.notePresentedPipeline( true ) );
    ASSERT_TRUE( tracker.validatedNoReadbackObserved() );
    ASSERT_TRUE( tracker.sharperHeadroomScaleAllowed() );
    ASSERT_FALSE( tracker.lastObservationDemotedCapability() );

    ASSERT_TRUE( tracker.notePresentedPipeline( false ) );
    ASSERT_TRUE( tracker.validatedNoReadbackObserved() );
    ASSERT_FALSE( tracker.lastObservationDemotedCapability() );

    PlaybackQualityAutoSampler s;
    feed_n( s, 15.0, kWin );
    auto d = s.decideNextSlot( 30,
                               /*dualIsoActive*/false,
                               /*aggressivePreviewActive*/false,
                               tracker.sharperHeadroomScaleAllowed() );
    ASSERT_EQ( 2, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
    expect_reason( "headroom_non_dual_iso_sharper_hq", d.reason );

    ASSERT_FALSE( tracker.notePresentedPipeline(
        false, /*gpuTextureNoReadbackCandidate*/true ) );
    ASSERT_FALSE( tracker.validatedNoReadbackObserved() );
    ASSERT_FALSE( tracker.sharperHeadroomScaleAllowed() );
    ASSERT_TRUE( tracker.lastObservationDemotedCapability() );

    ASSERT_TRUE( tracker.notePresentedPipeline( true,
                                                /*gpuTextureNoReadbackCandidate*/true ) );
    ASSERT_TRUE( tracker.validatedNoReadbackObserved() );
    ASSERT_TRUE( tracker.sharperHeadroomScaleAllowed() );
    ASSERT_FALSE( tracker.lastObservationDemotedCapability() );

    tracker.reset();
    ASSERT_FALSE( tracker.validatedNoReadbackObserved() );
    ASSERT_FALSE( tracker.sharperHeadroomScaleAllowed() );
    ASSERT_FALSE( tracker.lastObservationDemotedCapability() );
}

TEST(PlaybackQualityAutoSampler, DualIsoNeverDowngradesToHqx2)
{
    PlaybackQualityAutoSampler s;
    /* Same headroom as the previous test, but dual ISO active. */
    feed_n( s, 15.0, kWin );
    auto d = s.decideNextSlot( 30, /*dualIsoActive*/true );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
    expect_reason( "steady_hq", d.reason );
}

TEST(PlaybackQualityAutoSampler, EdgeCaseExactlyAtTarget)
{
    PlaybackQualityAutoSampler s;
    /* Exactly at budget should stay at HQ x4 (within +10% tolerance,
     * not enough headroom for x2 upgrade which needs <65% of budget). */
    const double budgetMs = 1000.0 / 30.0;
    feed_n( s, budgetMs, kWin );
    auto d = s.decideNextSlot( 30, false );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
}

TEST(PlaybackQualityAutoSampler, ResetReturnsToOptimisticWarmup)
{
    PlaybackQualityAutoSampler s;
    feed_n( s, 80.0, kWin );
    auto d = s.decideNextSlot( 30, false );
    ASSERT_EQ( 4, d.scaleFactor ); /* downgraded */
    ASSERT_FALSE( d.useHqMean23 );

    s.reset();
    d = s.decideNextSlot( 30, false );
    ASSERT_EQ( 4, d.scaleFactor ); /* optimistic again */
    ASSERT_TRUE( d.useHqMean23 );
    expect_reason( "warmup_hq", d.reason );
    ASSERT_EQ( 0u, d.sampleCount );
}

TEST(PlaybackQualityAutoSampler, SlidingWindowEvictsOldSamples)
{
    PlaybackQualityAutoSampler s;
    /* Fill with 80 ms (over budget) ... */
    feed_n( s, 80.0, kWin );
    auto d = s.decideNextSlot( 30, false );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_FALSE( d.useHqMean23 );

    /* ... then feed 16 fast frames; the slow ones should be fully evicted. */
    feed_n( s, 15.0, kWin );
    d = s.decideNextSlot( 30,
                          /*dualIsoActive*/false,
                          /*aggressivePreviewActive*/false,
                          /*sharperHeadroomScaleAllowed*/true );
    ASSERT_EQ( 2, d.scaleFactor ); /* upgrade to HQ x2 */
    ASSERT_TRUE( d.useHqMean23 );
    expect_reason( "headroom_non_dual_iso_sharper_hq", d.reason );
}

TEST(PlaybackQualityAutoSampler, IgnoresNonPositiveFrameMs)
{
    PlaybackQualityAutoSampler s;
    /* Inject zero/negative samples — should be ignored. */
    s.recordFrameMs( 0.0 );
    s.recordFrameMs( -1.0 );
    feed_n( s, 80.0, kWin ); /* still triggers the over-budget downgrade */
    auto d = s.decideNextSlot( 30, false );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_FALSE( d.useHqMean23 );
}
