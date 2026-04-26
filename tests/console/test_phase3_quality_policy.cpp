#include "../common/minitest.h"

#include "../../platform/qt/PlaybackQualityPolicy.h"

#include <QCoreApplication>
#include <QSettings>
#include <QStringList>

namespace {

void clearPhase3PlaybackQualityKeys()
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    set.remove( PlaybackQualitySettings::kKeyQualityMode() );
    set.remove( PlaybackQualitySettings::kKeyShowExperimentalPhase3Modes() );
    set.remove( PlaybackQualitySettings::kKeyPhase3Acknowledged() );
    set.remove( PlaybackQualitySettings::kKeyPhase3FastTier() );
    set.remove( PlaybackQualitySettings::kKeyPhase3FastTierEnteredAt() );
    set.remove( PlaybackQualitySettings::kKeyPhase3FastDailyUseClipsValidated() );
    set.remove( PlaybackQualitySettings::kKeyPhase3HQTier() );
    set.remove( PlaybackQualitySettings::kKeyPhase3HQTierEnteredAt() );
    set.remove( PlaybackQualitySettings::kKeyPhase3HQDailyUseClipsValidated() );
    set.remove( PlaybackQualitySettings::kKeyPhase3FastAutoFallbackFiredEpoch() );
    set.remove( PlaybackQualitySettings::kKeyPhase3HQAutoFallbackFiredEpoch() );
    set.remove( QString::fromLatin1( PlaybackQualitySettings::kKeyClipPlaytimePrefix() )
                + QStringLiteral("clip-a") );
    set.remove( QString::fromLatin1( PlaybackQualitySettings::kKeyClipPlaytimePrefix() )
                + QStringLiteral("clip-b") );
    set.sync();
}

} // namespace

TEST(Phase3_EXP, QualityModeEnumIncludesPhase3)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );
    clearPhase3PlaybackQualityKeys();

    ASSERT_EQ( 3, static_cast<int>( PlaybackQualityMode::Phase3Fast ) );
    ASSERT_EQ( 4, static_cast<int>( PlaybackQualityMode::Phase3HQ ) );
    playbackQualityShowExperimentalPhase3ModesWriteToSettings( true );
    playbackQualityPhase3AcknowledgedWriteToSettings( true );
    playbackQualityModeWriteToSettings( PlaybackQualityMode::Phase3HQ );
    ASSERT_EQ( static_cast<int>( PlaybackQualityMode::Phase3HQ ),
               static_cast<int>( playbackQualityModeFromSettings() ) );
    ASSERT_EQ( static_cast<int>( Phase3Mode::DecodeReconProcess ),
               static_cast<int>( phase3ModeFor( PlaybackQualityMode::Phase3HQ ) ) );

    clearPhase3PlaybackQualityKeys();
}

TEST(Phase3_EXP, HiddenByDefaultUntilDogfoodGateOrTier)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );
    clearPhase3PlaybackQualityKeys();

    ASSERT_FALSE( playbackQualityShowExperimentalPhase3ModesFromSettings() );
    ASSERT_FALSE( playbackQualityPhase3ModeSelectable( PlaybackQualityMode::Phase3Fast ) );
    ASSERT_EQ( static_cast<int>( Phase3Mode::Disabled ),
               static_cast<int>( phase3ModeFor( PlaybackQualityMode::Phase3Fast ) ) );

    playbackQualityShowExperimentalPhase3ModesWriteToSettings( true );
    ASSERT_TRUE( playbackQualityPhase3ModeSelectable( PlaybackQualityMode::Phase3Fast ) );
    ASSERT_EQ( static_cast<int>( Phase3Mode::Disabled ),
               static_cast<int>( phase3ModeFor( PlaybackQualityMode::Phase3Fast ) ) );
    playbackQualityPhase3AcknowledgedWriteToSettings( true );
    ASSERT_EQ( static_cast<int>( Phase3Mode::DecodeReconProcess ),
               static_cast<int>( phase3ModeFor( PlaybackQualityMode::Phase3Fast ) ) );

    playbackQualityShowExperimentalPhase3ModesWriteToSettings( false );
    playbackQualityTierWriteToSettings(
        PlaybackQualityMode::Phase3Fast, PlaybackQualityTier::DailyUse, 1234 );
    ASSERT_TRUE( playbackQualityPhase3ModeSelectable( PlaybackQualityMode::Phase3Fast ) );

    clearPhase3PlaybackQualityKeys();
}

TEST(Phase3_EXP, CycleIncludesOnlySelectableExperimentalModes)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );
    clearPhase3PlaybackQualityKeys();

    ASSERT_EQ( static_cast<int>( PlaybackQualityMode::Fast ),
               playbackQualityNextModeForCycle(
                   static_cast<int>( PlaybackQualityMode::Auto ) ) );

    playbackQualityShowExperimentalPhase3ModesWriteToSettings( true );
    ASSERT_EQ( static_cast<int>( PlaybackQualityMode::Phase3Fast ),
               playbackQualityNextModeForCycle(
                   static_cast<int>( PlaybackQualityMode::Auto ) ) );
    ASSERT_EQ( static_cast<int>( PlaybackQualityMode::Phase3HQ ),
               playbackQualityNextModeForCycle(
                   static_cast<int>( PlaybackQualityMode::Phase3Fast ) ) );
    ASSERT_EQ( static_cast<int>( PlaybackQualityMode::Fast ),
               playbackQualityNextModeForCycle(
                   static_cast<int>( PlaybackQualityMode::Phase3HQ ) ) );

    clearPhase3PlaybackQualityKeys();
    playbackQualityTierWriteToSettings(
        PlaybackQualityMode::Phase3HQ, PlaybackQualityTier::DailyUse, 1234 );
    ASSERT_EQ( static_cast<int>( PlaybackQualityMode::Phase3HQ ),
               playbackQualityNextModeForCycle(
                   static_cast<int>( PlaybackQualityMode::Auto ) ) );
    ASSERT_EQ( static_cast<int>( PlaybackQualityMode::Fast ),
               playbackQualityNextModeForCycle(
                   static_cast<int>( PlaybackQualityMode::Phase3HQ ) ) );

    clearPhase3PlaybackQualityKeys();
}

TEST(Phase3_EXP, QSettingsAcknowledgedKey)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );
    clearPhase3PlaybackQualityKeys();

    ASSERT_FALSE( playbackQualityPhase3AcknowledgedFromSettings() );
    playbackQualityPhase3AcknowledgedWriteToSettings( true );
    ASSERT_TRUE( playbackQualityPhase3AcknowledgedFromSettings() );
    playbackQualityPhase3AcknowledgedWriteToSettings( false );
    ASSERT_FALSE( playbackQualityPhase3AcknowledgedFromSettings() );

    clearPhase3PlaybackQualityKeys();
}

TEST(Phase3_EXP, AckCancelLeavesPriorModeIntact)
{
    const PlaybackQualityMode prior = PlaybackQualityMode::Auto;
    ASSERT_EQ( static_cast<int>( prior ),
               static_cast<int>( playbackQualitySelectionAfterAcknowledgement(
                   prior, PlaybackQualityMode::Phase3Fast, false ) ) );
    ASSERT_EQ( static_cast<int>( PlaybackQualityMode::Phase3Fast ),
               static_cast<int>( playbackQualitySelectionAfterAcknowledgement(
                   prior, PlaybackQualityMode::Phase3Fast, true ) ) );
}

TEST(Phase3_TIE, TierStateTransitions)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );
    clearPhase3PlaybackQualityKeys();

    const qint64 nowMs = 20LL * 24LL * 60LL * 60LL * 1000LL;
    const qint64 oldEnough = nowMs - PlaybackQualitySettings::kTierMinimumAgeMs();
    ASSERT_TRUE( playbackQualityCanPromoteToDailyUse(
        PlaybackQualityTier::Dev, oldEnough, nowMs, 0 ) );

    playbackQualityTierWriteToSettings(
        PlaybackQualityMode::Phase3Fast, PlaybackQualityTier::DailyUse, oldEnough );
    playbackQualityClipPlaytimeSecondsWriteToSettings(
        QStringLiteral("clip-a"), PlaybackQualitySettings::kPinnedClipMinimumSeconds() );
    playbackQualityClipPlaytimeSecondsWriteToSettings(
        QStringLiteral("clip-b"), PlaybackQualitySettings::kPinnedClipMinimumSeconds() );
    const QStringList required{ QStringLiteral("clip-a"), QStringLiteral("clip-b") };
    playbackQualityValidatedClipsWriteToSettings( PlaybackQualityMode::Phase3Fast, required );
    ASSERT_TRUE( playbackQualityCanPromoteToPinnedClip(
        playbackQualityTierFromSettings( PlaybackQualityMode::Phase3Fast ),
        playbackQualityTierEnteredAtFromSettings( PlaybackQualityMode::Phase3Fast ),
        nowMs,
        playbackQualityValidatedClipsFromSettings( PlaybackQualityMode::Phase3Fast ),
        required ) );

    clearPhase3PlaybackQualityKeys();
}

TEST(Phase3_TIE, PromoteRejectedIfTooSoon)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );
    clearPhase3PlaybackQualityKeys();

    const qint64 nowMs = 20LL * 24LL * 60LL * 60LL * 1000LL;
    const qint64 sixDaysOld = nowMs - PlaybackQualitySettings::kTierMinimumAgeMs() + 1;
    const qint64 sevenDaysOld = nowMs - PlaybackQualitySettings::kTierMinimumAgeMs();
    ASSERT_FALSE( playbackQualityCanPromoteToDailyUse(
        PlaybackQualityTier::Dev, sixDaysOld, nowMs, 0 ) );
    ASSERT_TRUE( playbackQualityCanPromoteToDailyUse(
        PlaybackQualityTier::Dev, sevenDaysOld, nowMs, 0 ) );
    playbackQualityAutoFallbackEpochWriteToSettings(
        PlaybackQualityMode::Phase3Fast, nowMs - 1 );
    ASSERT_FALSE( playbackQualityCanPromoteToDailyUse(
        PlaybackQualityTier::Dev,
        sevenDaysOld,
        nowMs,
        playbackQualityAutoFallbackEpochFromSettings( PlaybackQualityMode::Phase3Fast ) ) );

    clearPhase3PlaybackQualityKeys();
}

TEST(Phase3_TIE, DemoteAlwaysAllowed)
{
    if ( !QCoreApplication::instance() ) SKIP_TEST( "Requires QCoreApplication" );
    clearPhase3PlaybackQualityKeys();

    playbackQualityTierWriteToSettings(
        PlaybackQualityMode::Phase3HQ, PlaybackQualityTier::PinnedClip, 1234 );
    ASSERT_EQ( static_cast<int>( PlaybackQualityTier::PinnedClip ),
               static_cast<int>( playbackQualityTierFromSettings( PlaybackQualityMode::Phase3HQ ) ) );
    playbackQualityTierWriteToSettings(
        PlaybackQualityMode::Phase3HQ, PlaybackQualityTier::Dev, 5678 );
    ASSERT_EQ( static_cast<int>( PlaybackQualityTier::Dev ),
               static_cast<int>( playbackQualityTierFromSettings( PlaybackQualityMode::Phase3HQ ) ) );
    ASSERT_EQ( 5678LL,
               playbackQualityTierEnteredAtFromSettings( PlaybackQualityMode::Phase3HQ ) );

    clearPhase3PlaybackQualityKeys();
}
