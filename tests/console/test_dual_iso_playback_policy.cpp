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
