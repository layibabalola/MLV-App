#ifndef MLV_APP_TESTS_PIPELINE_PLAYBACK_PATH_TEST_STATE_H
#define MLV_APP_TESTS_PIPELINE_PLAYBACK_PATH_TEST_STATE_H

#include "../../src/mlv_include.h"

#include <array>
#include <cstddef>
#include <cstdlib>
#include <string>

struct PlaybackPathEnvValue
{
    bool present;
    std::string value;
};

struct PlaybackPathSavedEnv
{
    const char * name;
    PlaybackPathEnvValue value;
};

inline constexpr const char * kPlaybackPathEnvNames[] = {
    "MLVAPP_DISABLE_PHASE4BV2",
    "MLVAPP_DISABLE_PHASE4BV3",
    "MLVAPP_DISABLE_PHASE4BV4_X8",
    "MLVAPP_ENABLE_DUAL_ISO_X2_FULLRES_FIXES",
    "MLVAPP_DISABLE_QUARTERRES_X2_PREVIEW",
    "MLVAPP_ENABLE_DUAL_ISO_X4_FULLRES_FIXES",
    "MLVAPP_ENABLE_DUAL_ISO_FAST_X4_IN_HQ",
    "MLVAPP_PLAYBACK_PREFER_HQ_MEAN23",
    "MLVAPP_PROFILE_DISABLE_DUALISO_OVERRIDE",
    "MLVAPP_DISABLE_DUALISO_PLAYBACK_MEAN23_OVERRIDE",
    "MLVAPP_LOG_PHASE4BV2",
    "MLVAPP_PLAYBACK_AGGRESSIVE_PREVIEW",
    "MLVAPP_PLAYBACK_PREVIEW_MODE",
    "MLVAPP_EXPERIMENTAL_PROCESSED8_PREFETCH",
    "MLVAPP_PROCESSED8_PREFETCH_INDIRECT",
    "MLVAPP_PREFETCH_INDIRECT_X2",
    "MLVAPP_PREFETCH_DEBUG",
    "MLVAPP_PROCESSED8_LOOKAHEAD",
    "MLVAPP_DISABLE_RAW_UINT16_PREFETCH",
    "MLVAPP_SHADOWS_HIGHLIGHTS_PROBE",
    "MLVAPP_DISABLE_QUARTERRES_X2_PROCESSING",
    "MLVAPP_DISABLE_HALFRES_X1_PROCESSING",
    "MLVAPP_DISABLE_HALFRES_X1_PREVIEW",
    "MLVAPP_ENABLE_STANDARD_X1_SH_QUARTERRES",
    "MLVAPP_DISABLE_STANDARD_X1_SH_QUARTERRES",
    "MLVAPP_ENABLE_STANDARD_X2_SH_QUARTERRES",
    "MLVAPP_DISABLE_STANDARD_X2_SH_QUARTERRES",
    "MLVAPP_ENABLE_STANDARD_X4_SH_QUARTERRES",
    "MLVAPP_DISABLE_STANDARD_X4_SH_QUARTERRES",
    "MLVAPP_DISABLE_AGGRESSIVE_X8_SH_QUARTERRES",
    "MLVAPP_DISABLE_AGGRESSIVE_X2_SH_QUARTERRES",
    "MLVAPP_DISABLE_DIRECT8_PREVIEW_QUARTERRES_SH",
};

inline void playbackPathTestSetEnv(const char * name, const char * value)
{
#if defined(_WIN32)
    _putenv_s(name, value);
#else
    setenv(name, value, 1);
#endif
}

inline void playbackPathTestUnsetEnv(const char * name)
{
#if defined(_WIN32)
    _putenv_s(name, "");
#else
    unsetenv(name);
#endif
}

inline PlaybackPathEnvValue playbackPathTestCaptureEnv(const char * name)
{
    const char * value = std::getenv(name);
    return PlaybackPathEnvValue{ value != nullptr, value ? std::string(value) : std::string() };
}

inline void playbackPathTestRestoreEnv(const char * name, const PlaybackPathEnvValue & env)
{
    if (env.present)
    {
        playbackPathTestSetEnv(name, env.value.c_str());
    }
    else
    {
        playbackPathTestUnsetEnv(name);
    }
}

inline void playbackPathTestResetEnvCaches()
{
    mlv_phase4bv_reset_env_cache_for_testing();
    processingResetShadowsHighlightsProbeModeCacheForTesting();
    processingResetShadowsHighlightsQuarterresEnvCacheForTesting();
}

class ScopedPlaybackPathTestState
{
public:
    enum class Processed8PrefetchPolicy
    {
        Default,
        Disabled,
    };

    explicit ScopedPlaybackPathTestState(
        Processed8PrefetchPolicy prefetchPolicy = Processed8PrefetchPolicy::Default)
        : m_env(captureEnv())
        , m_fastX4HqPathMode(mlvPlaybackFastX4HqPathMode())
        , m_aggressivePreviewMode(mlvPlaybackAggressivePreviewMode())
        , m_proxyLevel(mlvPlaybackProxyLevel())
        , m_processingPreviewMode(processingPlaybackPreviewModeEnabled())
        , m_processingAggressivePreviewMode(processingPlaybackAggressivePreviewModeEnabled())
        , m_processingPreviewScaleFactor(processingPlaybackPreviewScaleFactor())
    {
        resetToCleanBaseline(prefetchPolicy);
    }

    ~ScopedPlaybackPathTestState()
    {
        for (const PlaybackPathSavedEnv & saved : m_env)
        {
            playbackPathTestRestoreEnv(saved.name, saved.value);
        }

        mlv_phase4bv_reset_env_cache_for_testing();
        processingResetShadowsHighlightsProbeModeCacheForTesting();
        processingResetShadowsHighlightsQuarterresEnvCacheForTesting();

        mlvSetPlaybackFastX4HqPathMode(m_fastX4HqPathMode);
        mlvSetPlaybackAggressivePreviewMode(m_aggressivePreviewMode);
        mlvSetPlaybackProxyLevel(m_proxyLevel);
        processingSetPlaybackPreviewMode(m_processingPreviewMode);
        processingSetPlaybackAggressivePreviewMode(m_processingAggressivePreviewMode);
        processingSetPlaybackPreviewScaleFactor(m_processingPreviewScaleFactor);
    }

    ScopedPlaybackPathTestState(const ScopedPlaybackPathTestState &) = delete;
    ScopedPlaybackPathTestState & operator=(const ScopedPlaybackPathTestState &) = delete;

private:
    static constexpr std::size_t kEnvCount =
        sizeof(kPlaybackPathEnvNames) / sizeof(kPlaybackPathEnvNames[0]);

    using EnvSnapshot = std::array<PlaybackPathSavedEnv, kEnvCount>;

    static EnvSnapshot captureEnv()
    {
        EnvSnapshot env = {};
        for (std::size_t index = 0; index < kEnvCount; ++index)
        {
            env[index] = PlaybackPathSavedEnv{
                kPlaybackPathEnvNames[index],
                playbackPathTestCaptureEnv(kPlaybackPathEnvNames[index]),
            };
        }
        return env;
    }

    static void resetToCleanBaseline(Processed8PrefetchPolicy prefetchPolicy)
    {
        for (const char * name : kPlaybackPathEnvNames)
        {
            playbackPathTestUnsetEnv(name);
        }

        if (prefetchPolicy == Processed8PrefetchPolicy::Disabled)
        {
            playbackPathTestSetEnv("MLVAPP_EXPERIMENTAL_PROCESSED8_PREFETCH", "0");
        }

        mlvSetPlaybackFastX4HqPathMode(0);
        mlvSetPlaybackAggressivePreviewMode(0);
        mlvSetPlaybackProxyLevel(-1);
        processingSetPlaybackPreviewMode(0);
        processingSetPlaybackAggressivePreviewMode(0);
        processingSetPlaybackPreviewScaleFactor(1);
        playbackPathTestResetEnvCaches();
    }

    EnvSnapshot m_env;
    int m_fastX4HqPathMode;
    int m_aggressivePreviewMode;
    int m_proxyLevel;
    int m_processingPreviewMode;
    int m_processingAggressivePreviewMode;
    int m_processingPreviewScaleFactor;
};

#endif
