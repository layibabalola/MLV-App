#include "../common/minitest.h"

#include "../../platform/qt/DualIsoPlaybackPolicy.h"

TEST(DualIsoPlaybackPolicy, PlaybackForcesFastPreviewSettingsForValidDualIso)
{
    const DualIsoPlaybackRuntimeSettings settings = effectiveDualIsoPlaybackRuntimeSettings(true,
                                                                                            true,
                                                                                            1,
                                                                                            1,
                                                                                            0,
                                                                                            1,
                                                                                            1);

    ASSERT_TRUE(settings.previewOverrideActive);
    ASSERT_EQ(2, settings.mode);
    ASSERT_EQ(1, settings.interpolation);
    ASSERT_EQ(0, settings.aliasMap);
    ASSERT_EQ(0, settings.fullResBlending);
}

TEST(DualIsoPlaybackPolicy, PausedPlaybackRestoresReceiptSettings)
{
    const DualIsoPlaybackRuntimeSettings settings = effectiveDualIsoPlaybackRuntimeSettings(false,
                                                                                            true,
                                                                                            1,
                                                                                            1,
                                                                                            0,
                                                                                            1,
                                                                                            1);

    ASSERT_FALSE(settings.previewOverrideActive);
    ASSERT_EQ(1, settings.mode);
    ASSERT_EQ(0, settings.interpolation);
    ASSERT_EQ(1, settings.aliasMap);
    ASSERT_EQ(1, settings.fullResBlending);
}

TEST(DualIsoPlaybackPolicy, InvalidOrDisabledDualIsoDoesNotForcePreview)
{
    DualIsoPlaybackRuntimeSettings settings = effectiveDualIsoPlaybackRuntimeSettings(true,
                                                                                      false,
                                                                                      1,
                                                                                      1,
                                                                                      0,
                                                                                      1,
                                                                                      1);
    ASSERT_FALSE(settings.previewOverrideActive);
    ASSERT_EQ(1, settings.mode);

    settings = effectiveDualIsoPlaybackRuntimeSettings(true,
                                                       true,
                                                       0,
                                                       1,
                                                       0,
                                                       1,
                                                       1);
    ASSERT_FALSE(settings.previewOverrideActive);
    ASSERT_EQ(1, settings.mode);
}

/* Scale-aware suppression (2026-06-29): at x2 the AUTO preview-override is
 * suppressed (it cliffs to a magenta fallback at bright-content transitions),
 * leaving selectedMode=1 (HQ) so the mean23 override engages -- the proven
 * neutral path. Explicit user preview (selectedMode==2) is still honored, and
 * x1 (default effectiveScale) is unaffected. */
TEST(DualIsoPlaybackPolicy, SuppressesAutoPreviewOverrideAtScale2)
{
    /* x2: auto override suppressed -> HQ mode retained (mean23 then engages). */
    const DualIsoPlaybackRuntimeSettings x2 =
        effectiveDualIsoPlaybackRuntimeSettings(true, true, 1, 1, 0, 1, 1, false, 2);
    ASSERT_FALSE(x2.previewOverrideActive);
    ASSERT_EQ(1, x2.mode);

    /* x2 with explicit user preview (selectedMode==2): still forced to mode 2. */
    const DualIsoPlaybackRuntimeSettings x2explicit =
        effectiveDualIsoPlaybackRuntimeSettings(true, true, 1, 2, 0, 1, 1, false, 2);
    ASSERT_EQ(2, x2explicit.mode);

    /* x1 (default effectiveScale=1): auto override UNCHANGED (regression guard). */
    const DualIsoPlaybackRuntimeSettings x1 =
        effectiveDualIsoPlaybackRuntimeSettings(true, true, 1, 1, 0, 1, 1, false, 1);
    ASSERT_TRUE(x1.previewOverrideActive);
    ASSERT_EQ(2, x1.mode);
}

/* Phase E5 policy-header tests: the scale-aware downgrade flags are
 * opt-in via MLVAPP_PLAYBACK_DOWNGRADE_ALIAS_MAP_AT_SCALE=1. With the env
 * unset (default), the flags must always be FALSE regardless of the
 * other policy state — so paused/scrubbing/export are unaffected and
 * playback also runs the receipt-authored alias_map/FR-blending. */
TEST(DualIsoPlaybackPolicy, PhaseE5_DowngradeDefaultOffWithoutEnvOptIn)
{
    /* Note: the env-disable cache is process-global and might already be
     * primed by an earlier test run. The cache itself is internal to the
     * header (a static int local to the inline function), so we can't
     * reset it from here. The test relies on the caller not having set
     * the env var before invoking — true for the pipeline_tests.exe
     * default invocation. */

    /* Preview override active (rowscale playback): downgrade flags must
     * be FALSE because the HQ recon won't run anyway. */
    {
        const DualIsoPlaybackRuntimeSettings settings = effectiveDualIsoPlaybackRuntimeSettings(
            /*playbackActive=*/true,
            /*rawFixEnabled=*/true,
            /*dualIsoValidity=*/1,
            /*selectedMode=*/1,
            /*selectedInterpolation=*/0,
            /*selectedAliasMap=*/1,
            /*selectedFullResBlending=*/1);
        ASSERT_TRUE(settings.previewOverrideActive);
        ASSERT_EQ(2, settings.mode);
        /* HQ won't run -> mean23 override not needed; alias_map/FR
         * downgrade not needed either. (The preview-rowscale path
         * already forces aliasMap=0/FR=0.) */
        ASSERT_FALSE(settings.playbackForceMean23);
        ASSERT_FALSE(settings.playbackDisableAliasMapAtScale);
    }

    /* Paused: nothing should fire. */
    {
        const DualIsoPlaybackRuntimeSettings settings = effectiveDualIsoPlaybackRuntimeSettings(
            /*playbackActive=*/false,
            /*rawFixEnabled=*/true,
            /*dualIsoValidity=*/1,
            /*selectedMode=*/1,
            /*selectedInterpolation=*/0,
            /*selectedAliasMap=*/1,
            /*selectedFullResBlending=*/1);
        ASSERT_FALSE(settings.previewOverrideActive);
        ASSERT_FALSE(settings.playbackForceMean23);
        ASSERT_FALSE(settings.playbackDisableAliasMapAtScale);
    }

    /* Invalid dual ISO: nothing should fire even during playback. */
    {
        const DualIsoPlaybackRuntimeSettings settings = effectiveDualIsoPlaybackRuntimeSettings(
            /*playbackActive=*/true,
            /*rawFixEnabled=*/true,
            /*dualIsoValidity=*/0,
            /*selectedMode=*/1,
            /*selectedInterpolation=*/0,
            /*selectedAliasMap=*/1,
            /*selectedFullResBlending=*/1);
        ASSERT_FALSE(settings.previewOverrideActive);
        ASSERT_FALSE(settings.playbackForceMean23);
        ASSERT_FALSE(settings.playbackDisableAliasMapAtScale);
    }
}

/* Phase E5 documents: alias_map OFF is visually safe (SSIM 0.9999 on the
 * user's 5K M16-1210 clip at scale 4). The policy header reflects that
 * split: MLVAPP_PLAYBACK_DOWNGRADE_ALIAS_MAP_AT_SCALE only flips alias_map.
 * (The env var is cached on first call; this test verifies the name exists
 * and the public signature compiles. Runtime behaviour is covered by the
 * pipeline tests in tests/pipeline/test_dual_iso_pipeline.cpp.) */
TEST(DualIsoPlaybackPolicy, PhaseE5_EnvHelpersExist)
{
    /* Just call them to make sure the names compile and they return a
     * deterministic result for the current process state. */
    const bool alias_env = dualIsoPlaybackDowngradeAliasMapAtScaleViaEnv();
    /* Default to OFF when no env is set in the test process (the
     * minitest harness doesn't set these). */
    ASSERT_FALSE(alias_env);
}
