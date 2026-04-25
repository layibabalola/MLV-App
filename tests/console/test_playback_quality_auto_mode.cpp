/* Phase 4E: GUI-grade Playback Quality dial — Auto-mode cadence sampler.
 *
 * Verifies the PlaybackQualityAutoSampler's adaptive logic:
 *   - Until 16 frames are recorded, the sampler optimistically picks HQ x4.
 *   - With cadences below frame budget, sampler stays at HQ x4 (steady).
 *   - With cadences above frame budget, sampler downgrades to Fast.
 *   - With huge headroom and a non-DI clip, sampler upgrades to HQ x2.
 *   - Dual-ISO clips never get x2; they stay at x4 even with headroom.
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

} // namespace

TEST(PlaybackQualityAutoSampler, OptimisticUntilWindowFull)
{
    PlaybackQualityAutoSampler s;
    /* No samples yet — should pick HQ x4. */
    auto d = s.decideNextSlot( 30, /*dualIsoActive*/false );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );

    /* Even after 15 frames (one short of full window), still optimistic. */
    feed_n( s, 100.0, kWin - 1 ); /* 100 ms is way over budget for 30 fps */
    d = s.decideNextSlot( 30, false );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
}

TEST(PlaybackQualityAutoSampler, DowngradesToFastOnCadenceMiss)
{
    PlaybackQualityAutoSampler s;
    /* 30 fps target = 33.33 ms budget. Feed 80 ms cadence (way over). */
    feed_n( s, 80.0, kWin );
    auto d = s.decideNextSlot( 30, /*dualIsoActive*/false );
    ASSERT_EQ( 1, d.scaleFactor );
    ASSERT_FALSE( d.useHqMean23 );

    /* Same logic at 24 fps target. */
    PlaybackQualityAutoSampler s24;
    feed_n( s24, 60.0, kWin );
    d = s24.decideNextSlot( 24, false );
    ASSERT_EQ( 1, d.scaleFactor );
    ASSERT_FALSE( d.useHqMean23 );
}

TEST(PlaybackQualityAutoSampler, StaysAtHqx4WhenMeetingTarget)
{
    PlaybackQualityAutoSampler s;
    /* 30 fps target = 33.33 ms; feed exactly at budget — within tolerance. */
    feed_n( s, 33.0, kWin );
    auto d = s.decideNextSlot( 30, /*dualIsoActive*/false );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );

    /* Slightly over budget but within +10% tolerance — also stay at HQ x4. */
    PlaybackQualityAutoSampler s2;
    feed_n( s2, 36.0, kWin ); /* 36 ms vs 33.33 ms budget = +8% */
    d = s2.decideNextSlot( 30, false );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
}

TEST(PlaybackQualityAutoSampler, UpgradesToHqx2OnNonDIWithHeadroom)
{
    PlaybackQualityAutoSampler s;
    /* 30 fps target = 33.33 ms; feed 15 ms (way under budget). */
    feed_n( s, 15.0, kWin );
    auto d = s.decideNextSlot( 30, /*dualIsoActive*/false );
    ASSERT_EQ( 2, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
}

TEST(PlaybackQualityAutoSampler, DualIsoNeverDowngradesToHqx2)
{
    PlaybackQualityAutoSampler s;
    /* Same headroom as the previous test, but dual ISO active. */
    feed_n( s, 15.0, kWin );
    auto d = s.decideNextSlot( 30, /*dualIsoActive*/true );
    ASSERT_EQ( 4, d.scaleFactor );
    ASSERT_TRUE( d.useHqMean23 );
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
    ASSERT_EQ( 1, d.scaleFactor ); /* downgraded */

    s.reset();
    d = s.decideNextSlot( 30, false );
    ASSERT_EQ( 4, d.scaleFactor ); /* optimistic again */
    ASSERT_TRUE( d.useHqMean23 );
}

TEST(PlaybackQualityAutoSampler, SlidingWindowEvictsOldSamples)
{
    PlaybackQualityAutoSampler s;
    /* Fill with 80 ms (over budget) ... */
    feed_n( s, 80.0, kWin );
    auto d = s.decideNextSlot( 30, false );
    ASSERT_EQ( 1, d.scaleFactor );

    /* ... then feed 16 fast frames; the slow ones should be fully evicted. */
    feed_n( s, 15.0, kWin );
    d = s.decideNextSlot( 30, false );
    ASSERT_EQ( 2, d.scaleFactor ); /* upgrade to HQ x2 */
    ASSERT_TRUE( d.useHqMean23 );
}

TEST(PlaybackQualityAutoSampler, IgnoresNonPositiveFrameMs)
{
    PlaybackQualityAutoSampler s;
    /* Inject zero/negative samples — should be ignored. */
    s.recordFrameMs( 0.0 );
    s.recordFrameMs( -1.0 );
    feed_n( s, 80.0, kWin ); /* still triggers the over-budget downgrade */
    auto d = s.decideNextSlot( 30, false );
    ASSERT_EQ( 1, d.scaleFactor );
}
