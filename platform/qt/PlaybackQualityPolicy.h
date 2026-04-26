#ifndef PLAYBACKQUALITYPOLICY_H
#define PLAYBACKQUALITYPOLICY_H

/*
 * Phase 4E: GUI-grade Playback Quality dial.
 *
 * Three stable user-facing modes plus two hidden Phase 3 dogfood modes.
 * Auto adapts based on measured cadence. The mode is persisted to the existing QSettings store
 * (HKCU\Software\magiclantern.MLVApp\MLVApp\Playback\... on Windows).
 *
 * The MLVAPP_PLAYBACK_PREFER_HQ_MEAN23 and MLVAPP_PLAYBACK_SCALE_FACTOR env
 * vars retain priority for dev/CI overrides; this layer only kicks in when
 * those env vars are unset.
 *
 * Decisions:
 * - Fast: preview rowscale, scale=4, cast present, fastest cadence.
 * - HighQuality: HQ + mean23 + scale=4, cast closed, slower cadence.
 * - Auto: starts at HQ scale=4; if measured cadence misses target, fall
 *   back to Fast for the next slot. Re-evaluates every kAutoSlidingWindow
 *   frames.
 * - Phase3Fast/Phase3HQ: experimental Phase 3 dispatch labels; hidden until
 *   the dogfood gate or personal rollout tier permits them, and inactive until
 *   the one-time acknowledgement is accepted.
 *
 * Dual-ISO clips MUST stay at scale=4 when running HQ (the reconstruction
 * cost only pays for itself at scale=4 on big sensors). Non-dual-ISO HQ
 * playback CAN use scale=2 in Auto mode if there is headroom.
 */

#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <mutex>

#include "Phase3Mode.h"

#ifdef QT_CORE_LIB
#include <QSettings>
#include <QString>
#include <QStringList>
#endif

enum class PlaybackQualityMode : int
{
    Fast = 0,
    HighQuality = 1,
    Auto = 2,
    Phase3Fast = 3,
    Phase3HQ = 4
};

enum class PlaybackQualityTier : int
{
    Dev = 0,
    DailyUse = 1,
    PinnedClip = 2
};

namespace PlaybackQualitySettings
{
    inline constexpr const char * kOrganization() { return "magiclantern.MLVApp"; }
    inline constexpr const char * kApplication() { return "MLVApp"; }
    inline constexpr const char * kKeyQualityMode() { return "Playback/QualityMode"; }
    inline constexpr const char * kKeyAutoTargetFps() { return "Playback/AutoTargetFps"; }
    inline constexpr const char * kKeyShowQualityIndicator() { return "Playback/ShowQualityIndicator"; }
    inline constexpr const char * kKeyShowExperimentalPhase3Modes() { return "Playback/ShowExperimentalPhase3Modes"; }
    inline constexpr const char * kKeyPhase3Acknowledged() { return "Playback/Phase3Acknowledged"; }
    inline constexpr const char * kKeyPhase3FastTier() { return "Playback/Phase3FastTier"; }
    inline constexpr const char * kKeyPhase3FastTierEnteredAt() { return "Playback/Phase3FastTierEnteredAt"; }
    inline constexpr const char * kKeyPhase3FastDailyUseClipsValidated() { return "Playback/Phase3FastDailyUseClipsValidated"; }
    inline constexpr const char * kKeyPhase3HQTier() { return "Playback/Phase3HQTier"; }
    inline constexpr const char * kKeyPhase3HQTierEnteredAt() { return "Playback/Phase3HQTierEnteredAt"; }
    inline constexpr const char * kKeyPhase3HQDailyUseClipsValidated() { return "Playback/Phase3HQDailyUseClipsValidated"; }
    inline constexpr const char * kKeyPhase3FastAutoFallbackFiredEpoch() { return "Playback/Phase3FastAutoFallbackFiredEpoch"; }
    inline constexpr const char * kKeyPhase3HQAutoFallbackFiredEpoch() { return "Playback/Phase3HQAutoFallbackFiredEpoch"; }
    inline constexpr const char * kKeyClipPlaytimePrefix() { return "Playback/ClipPlaytime/"; }

    inline constexpr int kDefaultQualityMode() { return static_cast<int>( PlaybackQualityMode::Fast ); }
    inline constexpr int kDefaultAutoTargetFps() { return 30; }
    inline constexpr int kDefaultShowQualityIndicator() { return 1; }
    inline constexpr int kDefaultShowExperimentalPhase3Modes() { return 0; }
    inline constexpr int kDefaultPhase3Acknowledged() { return 0; }
    inline constexpr long long kTierMinimumAgeMs() { return 7LL * 24LL * 60LL * 60LL * 1000LL; }
    inline constexpr long long kPinnedClipMinimumSeconds() { return 300LL; }
}

inline bool playbackQualityEnvVarTruthy(const char * raw)
{
    if (!raw || !*raw) return false;
    return std::strcmp(raw, "0") != 0
        && std::strcmp(raw, "false") != 0
        && std::strcmp(raw, "FALSE") != 0
        && std::strcmp(raw, "False") != 0;
}

inline bool playbackQualityModeIsPhase3( PlaybackQualityMode mode )
{
    return mode == PlaybackQualityMode::Phase3Fast
        || mode == PlaybackQualityMode::Phase3HQ;
}

inline bool playbackQualityModeIsExperimental( PlaybackQualityMode mode )
{
    return playbackQualityModeIsPhase3( mode );
}

inline PlaybackQualityMode playbackQualitySelectionAfterAcknowledgement(
    PlaybackQualityMode previous,
    PlaybackQualityMode requested,
    bool acknowledged )
{
    if ( playbackQualityModeIsPhase3( requested ) && !acknowledged )
    {
        return previous;
    }
    return requested;
}

inline const char * playbackQualityTierName( PlaybackQualityTier tier )
{
    switch ( tier )
    {
        case PlaybackQualityTier::Dev: return "Dev";
        case PlaybackQualityTier::DailyUse: return "Daily-Use";
        case PlaybackQualityTier::PinnedClip: return "Pinned-Clip";
    }
    return "Dev";
}

/* Returns the user's persisted QualityMode (Fast=0, HighQuality=1, Auto=2). */
#ifdef QT_CORE_LIB
inline PlaybackQualityMode playbackQualityModeFromSettings()
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    const int raw = set.value( PlaybackQualitySettings::kKeyQualityMode(),
                               PlaybackQualitySettings::kDefaultQualityMode() ).toInt();
    if ( raw < 0 || raw > 4 ) return PlaybackQualityMode::Fast;
    return static_cast<PlaybackQualityMode>( raw );
}

inline int playbackQualityAutoTargetFpsFromSettings()
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    const int raw = set.value( PlaybackQualitySettings::kKeyAutoTargetFps(),
                               PlaybackQualitySettings::kDefaultAutoTargetFps() ).toInt();
    if ( raw == 24 || raw == 30 || raw == 60 ) return raw;
    return PlaybackQualitySettings::kDefaultAutoTargetFps();
}

inline bool playbackQualityShowIndicatorFromSettings()
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    return set.value( PlaybackQualitySettings::kKeyShowQualityIndicator(),
                      PlaybackQualitySettings::kDefaultShowQualityIndicator() ).toBool();
}

inline bool playbackQualityShowExperimentalPhase3ModesFromSettings()
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    return set.value( PlaybackQualitySettings::kKeyShowExperimentalPhase3Modes(),
                      PlaybackQualitySettings::kDefaultShowExperimentalPhase3Modes() ).toBool();
}

inline bool playbackQualityPhase3AcknowledgedFromSettings()
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    return set.value( PlaybackQualitySettings::kKeyPhase3Acknowledged(),
                      PlaybackQualitySettings::kDefaultPhase3Acknowledged() ).toBool();
}

inline void playbackQualityModeWriteToSettings( PlaybackQualityMode mode )
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    set.setValue( PlaybackQualitySettings::kKeyQualityMode(),
                  static_cast<int>( mode ) );
}

inline void playbackQualityAutoTargetFpsWriteToSettings( int targetFps )
{
    int v = targetFps;
    if ( v != 24 && v != 30 && v != 60 )
    {
        v = PlaybackQualitySettings::kDefaultAutoTargetFps();
    }
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    set.setValue( PlaybackQualitySettings::kKeyAutoTargetFps(), v );
}

inline void playbackQualityShowIndicatorWriteToSettings( bool show )
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    set.setValue( PlaybackQualitySettings::kKeyShowQualityIndicator(),
                  show ? 1 : 0 );
}

inline void playbackQualityShowExperimentalPhase3ModesWriteToSettings( bool show )
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    set.setValue( PlaybackQualitySettings::kKeyShowExperimentalPhase3Modes(),
                  show ? 1 : 0 );
}

inline void playbackQualityPhase3AcknowledgedWriteToSettings( bool acknowledged )
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    set.setValue( PlaybackQualitySettings::kKeyPhase3Acknowledged(),
                  acknowledged ? 1 : 0 );
}

inline const char * playbackQualityTierKeyForMode( PlaybackQualityMode mode )
{
    return mode == PlaybackQualityMode::Phase3HQ
        ? PlaybackQualitySettings::kKeyPhase3HQTier()
        : PlaybackQualitySettings::kKeyPhase3FastTier();
}

inline const char * playbackQualityTierEnteredAtKeyForMode( PlaybackQualityMode mode )
{
    return mode == PlaybackQualityMode::Phase3HQ
        ? PlaybackQualitySettings::kKeyPhase3HQTierEnteredAt()
        : PlaybackQualitySettings::kKeyPhase3FastTierEnteredAt();
}

inline const char * playbackQualityValidatedClipsKeyForMode( PlaybackQualityMode mode )
{
    return mode == PlaybackQualityMode::Phase3HQ
        ? PlaybackQualitySettings::kKeyPhase3HQDailyUseClipsValidated()
        : PlaybackQualitySettings::kKeyPhase3FastDailyUseClipsValidated();
}

inline const char * playbackQualityAutoFallbackEpochKeyForMode( PlaybackQualityMode mode )
{
    return mode == PlaybackQualityMode::Phase3HQ
        ? PlaybackQualitySettings::kKeyPhase3HQAutoFallbackFiredEpoch()
        : PlaybackQualitySettings::kKeyPhase3FastAutoFallbackFiredEpoch();
}

inline PlaybackQualityTier playbackQualityTierFromSettings( PlaybackQualityMode mode )
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    const int raw = set.value( playbackQualityTierKeyForMode( mode ),
                               static_cast<int>( PlaybackQualityTier::Dev ) ).toInt();
    if ( raw < static_cast<int>( PlaybackQualityTier::Dev )
      || raw > static_cast<int>( PlaybackQualityTier::PinnedClip ) )
    {
        return PlaybackQualityTier::Dev;
    }
    return static_cast<PlaybackQualityTier>( raw );
}

inline qint64 playbackQualityTierEnteredAtFromSettings( PlaybackQualityMode mode )
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    return set.value( playbackQualityTierEnteredAtKeyForMode( mode ), 0 ).toLongLong();
}

inline QStringList playbackQualityValidatedClipsFromSettings( PlaybackQualityMode mode )
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    return set.value( playbackQualityValidatedClipsKeyForMode( mode ) ).toString()
        .split( QLatin1Char(','), Qt::SkipEmptyParts );
}

inline qint64 playbackQualityAutoFallbackEpochFromSettings( PlaybackQualityMode mode )
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    return set.value( playbackQualityAutoFallbackEpochKeyForMode( mode ), 0 ).toLongLong();
}

inline void playbackQualityAutoFallbackEpochWriteToSettings( PlaybackQualityMode mode,
                                                             qint64 epochMs )
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    set.setValue( playbackQualityAutoFallbackEpochKeyForMode( mode ), epochMs );
}

inline qint64 playbackQualityClipPlaytimeSecondsFromSettings( const QString & fingerprint )
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    return set.value( QString::fromLatin1( PlaybackQualitySettings::kKeyClipPlaytimePrefix() )
                      + fingerprint, 0 ).toLongLong();
}

inline void playbackQualityTierWriteToSettings( PlaybackQualityMode mode,
                                                PlaybackQualityTier tier,
                                                qint64 nowMs )
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    set.setValue( playbackQualityTierKeyForMode( mode ), static_cast<int>( tier ) );
    set.setValue( playbackQualityTierEnteredAtKeyForMode( mode ), nowMs );
}

inline void playbackQualityValidatedClipsWriteToSettings( PlaybackQualityMode mode,
                                                          const QStringList & fingerprints )
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    set.setValue( playbackQualityValidatedClipsKeyForMode( mode ),
                  fingerprints.join( QLatin1Char(',') ) );
}

inline void playbackQualityClipPlaytimeSecondsWriteToSettings( const QString & fingerprint,
                                                               qint64 seconds )
{
    QSettings set( QSettings::UserScope,
                   PlaybackQualitySettings::kOrganization(),
                   PlaybackQualitySettings::kApplication() );
    set.setValue( QString::fromLatin1( PlaybackQualitySettings::kKeyClipPlaytimePrefix() )
                  + fingerprint, seconds );
}

inline bool playbackQualityCanPromoteToDailyUse( PlaybackQualityTier tier,
                                                 qint64 tierEnteredAtMs,
                                                 qint64 nowMs,
                                                 qint64 autoFallbackEpochMs )
{
    if ( tier != PlaybackQualityTier::Dev ) return false;
    if ( tierEnteredAtMs <= 0 ) return false;
    if ( nowMs - tierEnteredAtMs < PlaybackQualitySettings::kTierMinimumAgeMs() ) return false;
    if ( autoFallbackEpochMs > 0
      && nowMs - autoFallbackEpochMs < PlaybackQualitySettings::kTierMinimumAgeMs() ) return false;
    return true;
}

inline bool playbackQualityCanPromoteToPinnedClip( PlaybackQualityTier tier,
                                                   qint64 tierEnteredAtMs,
                                                   qint64 nowMs,
                                                   const QStringList & validatedFingerprints,
                                                   const QStringList & requiredFingerprints )
{
    if ( tier != PlaybackQualityTier::DailyUse ) return false;
    if ( tierEnteredAtMs <= 0 ) return false;
    if ( nowMs - tierEnteredAtMs < PlaybackQualitySettings::kTierMinimumAgeMs() ) return false;
    for ( const QString & required : requiredFingerprints )
    {
        if ( !validatedFingerprints.contains( required ) ) return false;
        if ( playbackQualityClipPlaytimeSecondsFromSettings( required )
             < PlaybackQualitySettings::kPinnedClipMinimumSeconds() )
        {
            return false;
        }
    }
    return !requiredFingerprints.isEmpty();
}

inline bool playbackQualityPhase3ModeSelectable( PlaybackQualityMode mode )
{
    if ( !playbackQualityModeIsPhase3( mode ) ) return true;
    return playbackQualityShowExperimentalPhase3ModesFromSettings()
        || playbackQualityTierFromSettings( mode ) >= PlaybackQualityTier::DailyUse;
}

inline Phase3Mode phase3ModeFor( PlaybackQualityMode mode )
{
    if ( !playbackQualityModeIsPhase3( mode ) ) return Phase3Mode::Disabled;
    if ( !playbackQualityPhase3ModeSelectable( mode ) ) return Phase3Mode::Disabled;
    if ( !playbackQualityPhase3AcknowledgedFromSettings() ) return Phase3Mode::Disabled;
    return Phase3Mode::DecodeAheadOnly;
}

inline int playbackQualityNextModeForCycle( int currentMode )
{
    int modes[5] = {
        static_cast<int>( PlaybackQualityMode::Fast ),
        static_cast<int>( PlaybackQualityMode::HighQuality ),
        static_cast<int>( PlaybackQualityMode::Auto ),
        0,
        0
    };
    int count = 3;
    if ( playbackQualityPhase3ModeSelectable( PlaybackQualityMode::Phase3Fast ) )
        modes[count++] = static_cast<int>( PlaybackQualityMode::Phase3Fast );
    if ( playbackQualityPhase3ModeSelectable( PlaybackQualityMode::Phase3HQ ) )
        modes[count++] = static_cast<int>( PlaybackQualityMode::Phase3HQ );

    for ( int index = 0; index < count; ++index )
    {
        if ( modes[index] == currentMode )
            return modes[(index + 1) % count];
    }
    return modes[0];
}
#endif // QT_CORE_LIB

/* Effective HQ-mean23 desire considering env override + GUI fallback.
 * Env var takes priority; otherwise HighQuality and Auto modes ask for HQ. */
inline bool playbackQualityWantsHqMean23( PlaybackQualityMode mode )
{
    /* Env var takes precedence: matches existing
     * dualIsoPlaybackPreferHqMean23ViaEnv() semantics. */
    const char * env = std::getenv("MLVAPP_PLAYBACK_PREFER_HQ_MEAN23");
    if ( env && *env )
    {
        return playbackQualityEnvVarTruthy( env );
    }
    return mode == PlaybackQualityMode::HighQuality
        || mode == PlaybackQualityMode::Auto
        || mode == PlaybackQualityMode::Phase3HQ;
}

/* Effective playback scale factor considering env override + GUI fallback.
 * Returns 1, 2, or 4 (clamped). For Auto mode, the dynamic decision is
 * made by the cadence sampler; this returns the mode's *initial* scale
 * factor. */
inline int playbackQualityScaleFactorForMode( PlaybackQualityMode mode,
                                              bool dualIsoActive )
{
    /* Env var takes precedence. */
    const char * env = std::getenv("MLVAPP_PLAYBACK_SCALE_FACTOR");
    if ( env && *env )
    {
        const int v = std::atoi(env);
        if ( v == 1 || v == 2 || v == 4 ) return v;
    }
    switch ( mode )
    {
        case PlaybackQualityMode::Fast:
            /* Fast mode previously returned 1 (full resolution) which made
             * it counterintuitively slower than HQ on Dual ISO content:
             * Fast at scale=1 ran the entire pipeline at full sensor
             * resolution (preview rowscale + debayer + processing + 16->8
             * conversion all at 5K), while HQ at scale=4 ran a more
             * expensive recon but on 1/16 the pixels with debayer
             * bypassed entirely. Net result on the user's 5K M16-1210
             * clip: Fast = 13 fps GUI, HQ = 15 fps GUI — confusing UX.
             *
             * Returning 4 here makes Fast actually fastest: Fast
             * (preview rowscale at scale=4) ~24 fps GUI vs HQ
             * (matched-pair at scale=4) ~13-15 fps GUI on the same clip.
             * Cast still present in Fast mode (only HQ's matched-pair
             * recon closes it); image is downsampled (1/4 each axis)
             * during playback only — pause/scrub/export still produce
             * full-resolution output.
             *
             * Users who want full-resolution playback unconditionally
             * can set MLVAPP_PLAYBACK_SCALE_FACTOR=1 (handled above). */
            return 4;
        case PlaybackQualityMode::HighQuality:
            return 4;
        case PlaybackQualityMode::Auto:
            return 4; /* start optimistic; sampler may downgrade */
        case PlaybackQualityMode::Phase3Fast:
            return 4;
        case PlaybackQualityMode::Phase3HQ:
            return 4;
    }
    (void)dualIsoActive;
    return 1;
}

/* Cadence sampler used by Auto mode. Maintains a sliding window of frame
 * timings and decides which mode the next slot should use.
 *
 * Thread-safety: timer events run on the GUI thread and the read paths
 * from RenderFrameThread don't share state with this sampler, so a simple
 * mutex around the deque is enough. The whole class is intentionally
 * header-only and stateless across runs. */
struct PlaybackQualityAutoSampler
{
    static constexpr size_t kSlidingWindow = 16;

    void recordFrameMs( double frameMs )
    {
        std::lock_guard<std::mutex> lock( m_mutex );
        if ( frameMs <= 0.0 ) return;
        m_window.push_back( frameMs );
        if ( m_window.size() > kSlidingWindow ) m_window.pop_front();
    }

    void reset()
    {
        std::lock_guard<std::mutex> lock( m_mutex );
        m_window.clear();
    }

    /* Returns the recommended scale factor for the next slot.
     * targetFps is the user's chosen target (24, 30, or 60).
     * dualIsoActive forces scale=4 when HQ is selected (no scale=2 for DI).
     *
     * Logic:
     *   - if window not full yet -> stay at HQ scale=4 (gather more data)
     *   - else compute avg cadence
     *   - if avg cadence > 1.10 * frame budget (under-meeting target by >10%)
     *       -> downgrade to Fast (scale=1, no HQ)
     *   - else if avg cadence < 0.65 * frame budget (lots of headroom)
     *       and !dualIsoActive
     *       -> upgrade HQ to scale=2 (try sharper)
     *   - else stay at HQ scale=4 */
    struct Decision
    {
        int scaleFactor;       /* 1, 2, or 4 */
        bool useHqMean23;      /* true => HQ + mean23, false => Fast preview */
    };

    Decision decideNextSlot( int targetFps, bool dualIsoActive ) const
    {
        std::lock_guard<std::mutex> lock( m_mutex );
        if ( targetFps <= 0 ) targetFps = 30;
        const double frameBudgetMs = 1000.0 / static_cast<double>( targetFps );

        if ( m_window.size() < kSlidingWindow )
        {
            /* Optimistic start: HQ scale=4 until we have a full window. */
            return Decision{ 4, true };
        }

        double sum = 0.0;
        for ( const double v : m_window ) sum += v;
        const double avgMs = sum / static_cast<double>( m_window.size() );

        if ( avgMs > frameBudgetMs * 1.10 )
        {
            /* Missing target by >10%: drop to Fast. */
            return Decision{ 1, false };
        }
        if ( !dualIsoActive && avgMs < frameBudgetMs * 0.65 )
        {
            /* Plenty of headroom on a non-DI clip: try sharper HQ. */
            return Decision{ 2, true };
        }
        /* Steady state: HQ scale=4. */
        return Decision{ 4, true };
    }

private:
    mutable std::mutex m_mutex;
    std::deque<double> m_window;
};

#endif // PLAYBACKQUALITYPOLICY_H
