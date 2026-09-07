#include "../common/minitest.h"

#include "../../platform/qt/DualIsoLevelSyncPolicy.h"

using dual_iso_level_sync_policy::shouldSync;

TEST(DualIsoLevelSyncPolicy, TrueOnlyWhenAllThreeInputsAreTrue)
{
    ASSERT_TRUE(shouldSync(true, true, true));
}

TEST(DualIsoLevelSyncPolicy, FalseWhenSourceNotReady)
{
    ASSERT_FALSE(shouldSync(false, true, true));
}

TEST(DualIsoLevelSyncPolicy, FalseWhenBakeNotPending)
{
    ASSERT_FALSE(shouldSync(true, false, true));
}

TEST(DualIsoLevelSyncPolicy, FalseWhenLevelsAlreadyInSync)
{
    ASSERT_FALSE(shouldSync(true, true, false));
}

/* No dual-mode gate. The out-of-sync signal alone is
 * authoritative -- a normal (non-dual-ISO) clip whose levels are out of
 * sync must still sync, so the predicate has no fourth "dual mode" input.
 * This case pins that: sourceReady and levelsOutOfSync true, bakePending
 * true, is indistinguishable from a dual-ISO clip in this policy and must
 * still sync. */
TEST(DualIsoLevelSyncPolicy, NormalRawClipOutOfSyncStillSyncs)
{
    ASSERT_TRUE(shouldSync(/*sourceReady=*/true, /*bakePending=*/true, /*levelsOutOfSync=*/true));
}

TEST(DualIsoLevelSyncPolicy, AllFalseIsFalse)
{
    ASSERT_FALSE(shouldSync(false, false, false));
}

TEST(DualIsoLevelSyncPolicy, CompleteInputMatrix)
{
    const bool cases[][4] = {
        { false, false, false, false },
        { false, false, true,  false },
        { false, true,  false, false },
        { false, true,  true,  false },
        { true,  false, false, false },
        { true,  false, true,  false },
        { true,  true,  false, false },
        { true,  true,  true,  true  },
    };
    for (const auto &c : cases)
        ASSERT_EQ(shouldSync(c[0], c[1], c[2]), c[3]);
}
