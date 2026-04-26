/* Phase 4E: GUI-grade Playback Quality dial — QSettings round-trip and
 * env-var fallthrough behaviour.
 *
 * Verifies:
 *   1. Default mode is Fast.
 *   2. QSettings round-trip writes and reads back the persisted choice.
 *   3. env var MLVAPP_PLAYBACK_PREFER_HQ_MEAN23 takes priority over the
 *      QSettings dial.
 *   4. dualIsoPlaybackPreferHqMean23() consults the QSettings fallback when
 *      the env var is unset.
 *   5. playbackQualityScaleFactorForMode() returns 4 for all persisted modes
 *      unless the explicit scale-factor env var overrides it.
 *
 * Skips when QCoreApplication is unavailable (the harness installs one in
 * test_main.cpp; defensive check in case the test is reused). */

#include "../common/minitest.h"

#include <QCoreApplication>
#include <QSettings>
#include <cstdlib>

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

} // namespace

TEST(PlaybackQualitySettings, RoundTripQualityMode)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );

    clearAllPlaybackQualityKeys();

    /* Default should be Fast. */
    ASSERT_EQ( static_cast<int>(PlaybackQualityMode::Fast),
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
    ASSERT_EQ( 4, playbackQualityScaleFactorForMode( PlaybackQualityMode::HighQuality, false ) );
    ASSERT_EQ( 4, playbackQualityScaleFactorForMode( PlaybackQualityMode::Auto,        false ) );
    ASSERT_EQ( 4, playbackQualityScaleFactorForMode( PlaybackQualityMode::Phase3Fast,  false ) );
    ASSERT_EQ( 4, playbackQualityScaleFactorForMode( PlaybackQualityMode::Phase3HQ,    false ) );
}

TEST(PlaybackQualitySettings, EnvVarOverridesGuiHqMean23)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );

    clearAllPlaybackQualityKeys();
    unsetEnv();

    /* GUI mode set to Fast — would normally be HQ-mean23-OFF. */
    playbackQualityModeWriteToSettings( PlaybackQualityMode::Fast );
    ASSERT_FALSE( playbackQualityWantsHqMean23( PlaybackQualityMode::Fast ) );

    /* Env var ON forces HQ-mean23 ON regardless of GUI mode. */
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
