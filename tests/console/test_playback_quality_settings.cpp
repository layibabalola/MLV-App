/* Phase 4E: GUI-grade Playback Quality dial — QSettings round-trip and
 * env-var fallthrough behaviour.
 *
 * Verifies:
 *   1. Default mode is Auto.
 *   2. QSettings round-trip writes and reads back the persisted choice.
 *   3. env var MLVAPP_PLAYBACK_PREFER_HQ_MEAN23 takes priority over the
 *      QSettings dial.
 *   4. dualIsoPlaybackPreferHqMean23() consults the QSettings fallback when
 *      the env var is unset.
 *   5. playbackQualityScaleFactorForMode() returns 4 for non-Dual ISO
 *      persisted modes, while Fast/Dual ISO may start at x8 unless the
 *      explicit scale-factor env var overrides it.
 *
 * Skips when QCoreApplication is unavailable (the harness installs one in
 * test_main.cpp; defensive check in case the test is reused). */

#include "../common/minitest.h"

#include <QCoreApplication>
#include <QSettings>
#include <cstdlib>
#include <string>

#include "../../platform/qt/PlaybackQualityPolicy.h"
#include "../../platform/qt/DualIsoPlaybackPolicy.h"

namespace
{

void clearAllPlaybackQualityKeys()
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    set.remove( PlaybackQualitySettings::kKeyQualityMode() );
    set.remove( PlaybackQualitySettings::kKeyPreviewMode() );
    set.remove( PlaybackQualitySettings::kKeyScaleFactorOverride() );
    set.remove( PlaybackQualitySettings::kKeyPreviewResolution() );
    set.remove( PlaybackQualitySettings::kKeyAutoTargetFps() );
    set.remove( PlaybackQualitySettings::kKeyShowQualityIndicator() );
    set.remove( PlaybackQualitySettings::kKeyShowExperimentalPhase3Modes() );
    set.remove( PlaybackQualitySettings::kKeyPhase3Acknowledged() );
    set.sync();
}

void unsetEnv()
{
    /* Use _putenv on Windows / unsetenv on POSIX. We rely on the platform
     * shim ::_putenv() existing under MinGW. */
#ifdef _WIN32
    _putenv("MLVAPP_PLAYBACK_PREFER_HQ_MEAN23=");
    _putenv("MLVAPP_PLAYBACK_SCALE_FACTOR=");
#else
    unsetenv("MLVAPP_PLAYBACK_PREFER_HQ_MEAN23");
    unsetenv("MLVAPP_PLAYBACK_SCALE_FACTOR");
#endif
}

void setEnvOn()
{
#ifdef _WIN32
    _putenv("MLVAPP_PLAYBACK_PREFER_HQ_MEAN23=1");
#else
    setenv("MLVAPP_PLAYBACK_PREFER_HQ_MEAN23", "1", 1);
#endif
}

void setScaleEnv(const char * value)
{
#ifdef _WIN32
    static std::string env;
    env = std::string("MLVAPP_PLAYBACK_SCALE_FACTOR=") + value;
    _putenv(env.c_str());
#else
    setenv("MLVAPP_PLAYBACK_SCALE_FACTOR", value, 1);
#endif
}

} // namespace

TEST(PlaybackQualitySettings, RoundTripQualityMode)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );

    clearAllPlaybackQualityKeys();

    /* Default should be Auto. */
    ASSERT_EQ( static_cast<int>(PlaybackQualityMode::Auto),
               static_cast<int>(playbackQualityModeFromSettings()) );

    playbackQualityModeWriteToSettings( PlaybackQualityMode::HighQuality );
    ASSERT_EQ( static_cast<int>(PlaybackQualityMode::HighQuality),
               static_cast<int>(playbackQualityModeFromSettings()) );

    playbackQualityModeWriteToSettings( PlaybackQualityMode::Auto );
    ASSERT_EQ( static_cast<int>(PlaybackQualityMode::Auto),
               static_cast<int>(playbackQualityModeFromSettings()) );

    playbackQualityModeWriteToSettings( PlaybackQualityMode::Fast );
    ASSERT_EQ( static_cast<int>(PlaybackQualityMode::Fast),
               static_cast<int>(playbackQualityModeFromSettings()) );

    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    set.setValue( PlaybackQualitySettings::kKeyQualityMode(), -1 );
    set.sync();
    ASSERT_EQ( static_cast<int>(PlaybackQualityMode::Auto),
               static_cast<int>(playbackQualityModeFromSettings()) );

    set.setValue( PlaybackQualitySettings::kKeyQualityMode(), 999 );
    set.sync();
    ASSERT_EQ( static_cast<int>(PlaybackQualityMode::Auto),
               static_cast<int>(playbackQualityModeFromSettings()) );

    clearAllPlaybackQualityKeys();
}

TEST(PlaybackQualitySettings, RoundTripAutoTargetFps)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );

    clearAllPlaybackQualityKeys();

    /* Default 30. */
    ASSERT_EQ( 30, playbackQualityAutoTargetFpsFromSettings() );

    playbackQualityAutoTargetFpsWriteToSettings( 24 );
    ASSERT_EQ( 24, playbackQualityAutoTargetFpsFromSettings() );

    playbackQualityAutoTargetFpsWriteToSettings( 60 );
    ASSERT_EQ( 60, playbackQualityAutoTargetFpsFromSettings() );

    /* Garbage values clamp back to default 30. */
    playbackQualityAutoTargetFpsWriteToSettings( 99 );
    ASSERT_EQ( 30, playbackQualityAutoTargetFpsFromSettings() );

    clearAllPlaybackQualityKeys();
}

TEST(PlaybackQualitySettings, RoundTripPreviewMode)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );

    clearAllPlaybackQualityKeys();

    ASSERT_EQ( static_cast<int>(PlaybackPreviewMode::SharpSmooth),
               static_cast<int>(playbackPreviewModeFromSettings()) );

    playbackPreviewModeWriteToSettings( PlaybackPreviewMode::AggressivePerformance );
    ASSERT_EQ( static_cast<int>(PlaybackPreviewMode::AggressivePerformance),
               static_cast<int>(playbackPreviewModeFromSettings()) );

    playbackPreviewModeWriteToSettings( PlaybackPreviewMode::SharpSmooth );
    ASSERT_EQ( static_cast<int>(PlaybackPreviewMode::SharpSmooth),
               static_cast<int>(playbackPreviewModeFromSettings()) );

    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    set.setValue( PlaybackQualitySettings::kKeyPreviewMode(), -1 );
    set.sync();
    ASSERT_EQ( static_cast<int>(PlaybackPreviewMode::SharpSmooth),
               static_cast<int>(playbackPreviewModeFromSettings()) );

    set.setValue( PlaybackQualitySettings::kKeyPreviewMode(), 999 );
    set.sync();
    ASSERT_EQ( static_cast<int>(PlaybackPreviewMode::SharpSmooth),
               static_cast<int>(playbackPreviewModeFromSettings()) );

    clearAllPlaybackQualityKeys();
}

TEST(PlaybackQualitySettings, RoundTripPreviewResolution)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );

    clearAllPlaybackQualityKeys();

    ASSERT_EQ( static_cast<int>(PlaybackPreviewResolution::Auto),
               static_cast<int>(playbackPreviewResolutionFromSettings()) );

    playbackPreviewResolutionWriteToSettings( PlaybackPreviewResolution::Full );
    ASSERT_EQ( static_cast<int>(PlaybackPreviewResolution::Full),
               static_cast<int>(playbackPreviewResolutionFromSettings()) );

    playbackPreviewResolutionWriteToSettings( PlaybackPreviewResolution::Half );
    ASSERT_EQ( static_cast<int>(PlaybackPreviewResolution::Half),
               static_cast<int>(playbackPreviewResolutionFromSettings()) );

    playbackPreviewResolutionWriteToSettings( PlaybackPreviewResolution::Quarter );
    ASSERT_EQ( static_cast<int>(PlaybackPreviewResolution::Quarter),
               static_cast<int>(playbackPreviewResolutionFromSettings()) );

    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    set.setValue( PlaybackQualitySettings::kKeyPreviewResolution(), -1 );
    set.sync();
    ASSERT_EQ( static_cast<int>(PlaybackPreviewResolution::Auto),
               static_cast<int>(playbackPreviewResolutionFromSettings()) );

    set.setValue( PlaybackQualitySettings::kKeyPreviewResolution(), 999 );
    set.sync();
    ASSERT_EQ( static_cast<int>(PlaybackPreviewResolution::Auto),
               static_cast<int>(playbackPreviewResolutionFromSettings()) );

    clearAllPlaybackQualityKeys();
}

TEST(PlaybackQualitySettings, PreviewResolutionProxyLevelMapping)
{
    ASSERT_EQ( -1, playbackPreviewResolutionToProxyLevel( PlaybackPreviewResolution::Auto ) );
    ASSERT_EQ( 0, playbackPreviewResolutionToProxyLevel( PlaybackPreviewResolution::Full ) );
    ASSERT_EQ( 1, playbackPreviewResolutionToProxyLevel( PlaybackPreviewResolution::Half ) );
    ASSERT_EQ( 2, playbackPreviewResolutionToProxyLevel( PlaybackPreviewResolution::Quarter ) );
}

TEST(PlaybackQualitySettings, RoundTripShowIndicator)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );

    clearAllPlaybackQualityKeys();

    /* Default true. */
    ASSERT_TRUE( playbackQualityShowIndicatorFromSettings() );

    playbackQualityShowIndicatorWriteToSettings( false );
    ASSERT_FALSE( playbackQualityShowIndicatorFromSettings() );

    playbackQualityShowIndicatorWriteToSettings( true );
    ASSERT_TRUE( playbackQualityShowIndicatorFromSettings() );

    clearAllPlaybackQualityKeys();
}

TEST(PlaybackQualitySettings, ScaleFactorForMode)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );
    unsetEnv();

    ASSERT_EQ( 4, playbackQualityScaleFactorForMode( PlaybackQualityMode::Fast,        false ) );
    ASSERT_EQ( 8, playbackQualityScaleFactorForMode( PlaybackQualityMode::Fast,        true ) );
    ASSERT_EQ( 4, playbackQualityScaleFactorForMode( PlaybackQualityMode::HighQuality, false ) );
    ASSERT_EQ( 4, playbackQualityScaleFactorForMode( PlaybackQualityMode::Auto,        false ) );
    ASSERT_EQ( 4, playbackQualityScaleFactorForMode( PlaybackQualityMode::Phase3Fast,  false ) );
    ASSERT_EQ( 4, playbackQualityScaleFactorForMode( PlaybackQualityMode::Phase3HQ,    false ) );
}

TEST(PlaybackQualitySettings, ScaleFactorEnvAllowsEight)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );
    unsetEnv();

    setScaleEnv("8");
    ASSERT_EQ( 8, playbackQualityScaleFactorForMode( PlaybackQualityMode::Fast,        false ) );
    ASSERT_EQ( 8, playbackQualityScaleFactorForMode( PlaybackQualityMode::HighQuality, false ) );
    ASSERT_EQ( 8, playbackQualityScaleFactorForMode( PlaybackQualityMode::Auto,        false ) );

    setScaleEnv("16");
    ASSERT_EQ( 4, playbackQualityScaleFactorForMode( PlaybackQualityMode::Fast, false ) );

    unsetEnv();
}

TEST(PlaybackQualitySettings, EnvVarOverridesGuiHqMean23)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );

    clearAllPlaybackQualityKeys();
    unsetEnv();

    /* GUI mode set to Fast — now asks for HQ-mean23 so Dual ISO playback
     * avoids the legacy preview-rowscale magenta cast. */
    playbackQualityModeWriteToSettings( PlaybackQualityMode::Fast );
    ASSERT_TRUE( playbackQualityWantsHqMean23( PlaybackQualityMode::Fast ) );

    /* Env var ON still forces HQ-mean23 ON regardless of GUI mode. */
    setEnvOn();
    ASSERT_TRUE( playbackQualityWantsHqMean23( PlaybackQualityMode::Fast ) );

    unsetEnv();
    /* GUI mode HighQuality — should pick up HQ-mean23. */
    ASSERT_TRUE( playbackQualityWantsHqMean23( PlaybackQualityMode::HighQuality ) );
    ASSERT_TRUE( playbackQualityWantsHqMean23( PlaybackQualityMode::Auto ) );
    ASSERT_FALSE( playbackQualityWantsHqMean23( PlaybackQualityMode::Phase3Fast ) );
    ASSERT_TRUE( playbackQualityWantsHqMean23( PlaybackQualityMode::Phase3HQ ) );

    clearAllPlaybackQualityKeys();
}

TEST(PlaybackQualitySettings, FastDualIsoHqPathCoversScaleTwoAndFour)
{
    ASSERT_FALSE( playbackQualityWantsFastDualIsoHqPath( PlaybackQualityMode::Fast, 1, true ) );
    ASSERT_TRUE( playbackQualityWantsFastDualIsoHqPath( PlaybackQualityMode::Fast, 2, true ) );
    ASSERT_TRUE( playbackQualityWantsFastDualIsoHqPath( PlaybackQualityMode::Fast, 4, true ) );
    ASSERT_FALSE( playbackQualityWantsFastDualIsoHqPath( PlaybackQualityMode::Fast, 8, true ) );
    ASSERT_FALSE( playbackQualityWantsFastDualIsoHqPath( PlaybackQualityMode::HighQuality, 4, true ) );
    ASSERT_FALSE( playbackQualityWantsFastDualIsoHqPath( PlaybackQualityMode::Fast, 4, false ) );
}

/* Verify the DualIsoPlaybackPolicy fallback hook routes through to the
 * GUI-derived QSettings choice. The MainWindow installs a static method
 * pointer; we mimic that here with a lambda-style function. */
static int g_fallback_call_count = 0;
static bool fallback_returns_true() { ++g_fallback_call_count; return true; }
static bool fallback_returns_false() { ++g_fallback_call_count; return false; }

TEST(PlaybackQualitySettings, DualIsoFallbackHookIsConsulted)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );

    unsetEnv();

    /* Save and restore the existing fallback so we don't disturb other tests. */
    DualIsoPlaybackPreferHqMean23Fallback prior =
        dualIsoPlaybackPreferHqMean23FallbackRef();

    setDualIsoPlaybackPreferHqMean23Fallback( &fallback_returns_true );
    g_fallback_call_count = 0;
    ASSERT_TRUE( dualIsoPlaybackPreferHqMean23() );
    ASSERT_TRUE( g_fallback_call_count >= 1 );

    setDualIsoPlaybackPreferHqMean23Fallback( &fallback_returns_false );
    ASSERT_FALSE( dualIsoPlaybackPreferHqMean23() );

    /* With env var ON, fallback is bypassed. */
    setEnvOn();
    g_fallback_call_count = 0;
    setDualIsoPlaybackPreferHqMean23Fallback( &fallback_returns_false );
    ASSERT_TRUE( dualIsoPlaybackPreferHqMean23() ); // env on wins
    ASSERT_EQ( 0, g_fallback_call_count );          // fallback not called

    unsetEnv();
    setDualIsoPlaybackPreferHqMean23Fallback( prior );
}

TEST(PlaybackQualitySettings, GuiFallbackSuppressesDualIsoPreviewRowscale)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );

    unsetEnv();

    DualIsoPlaybackPreferHqMean23Fallback prior =
        dualIsoPlaybackPreferHqMean23FallbackRef();
    setDualIsoPlaybackPreferHqMean23Fallback( &fallback_returns_true );

    const DualIsoPlaybackRuntimeSettings settings =
        effectiveDualIsoPlaybackRuntimeSettings(
            /*playbackActive=*/true,
            /*rawFixEnabled=*/true,
            /*dualIsoValidity=*/1,
            /*selectedMode=*/1,
            /*selectedInterpolation=*/0,
            /*selectedAliasMap=*/1,
            /*selectedFullResBlending=*/1);

    ASSERT_FALSE( settings.previewOverrideActive );
    ASSERT_EQ( 1, settings.mode );
    ASSERT_EQ( 0, settings.interpolation );
    ASSERT_EQ( 1, settings.aliasMap );
    ASSERT_EQ( 1, settings.fullResBlending );
    ASSERT_TRUE( settings.playbackForceMean23 );

    setDualIsoPlaybackPreferHqMean23Fallback( prior );
}
