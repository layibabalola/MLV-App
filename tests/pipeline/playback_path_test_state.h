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

struct PlaybackPathProcessState
{
    std::array<PlaybackPathSavedEnv, 32> env;
    int fastX4HqPathMode;
    int aggressivePreviewMode;
    int proxyLevel;
    int processingPreviewMode;
    int processingAggressivePreviewMode;
    int processingPreviewScaleFactor;
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
        : m_state(captureProcessState())
    {
        resetProcessStateForTest(prefetchPolicy);
    }

    ~ScopedPlaybackPathTestState()
    {
        applyProcessState(m_state);
    }

    ScopedPlaybackPathTestState(const ScopedPlaybackPathTestState &) = delete;
    ScopedPlaybackPathTestState & operator=(const ScopedPlaybackPathTestState &) = delete;

    static void resetProcessStateForTest(
        Processed8PrefetchPolicy prefetchPolicy = Processed8PrefetchPolicy::Default)
    {
        PlaybackPathProcessState state = initialProcessState();
        if (prefetchPolicy == Processed8PrefetchPolicy::Disabled)
        {
            setEnvInState(state, "MLVAPP_EXPERIMENTAL_PROCESSED8_PREFETCH", "0");
        }
        applyProcessState(state);
    }

    static constexpr std::size_t kEnvCount =
        sizeof(kPlaybackPathEnvNames) / sizeof(kPlaybackPathEnvNames[0]);

    using EnvSnapshot = std::array<PlaybackPathSavedEnv, kEnvCount>;

private:
    static_assert(kEnvCount == 32, "PlaybackPathProcessState env snapshot size must match env name list");

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

    static PlaybackPathProcessState captureProcessState()
    {
        return PlaybackPathProcessState{
            captureEnv(),
            mlvPlaybackFastX4HqPathMode(),
            mlvPlaybackAggressivePreviewMode(),
            mlvPlaybackProxyLevel(),
            processingPlaybackPreviewModeEnabled(),
            processingPlaybackAggressivePreviewModeEnabled(),
            processingPlaybackPreviewScaleFactor(),
        };
    }

    static const PlaybackPathProcessState & initialProcessState()
    {
        static const PlaybackPathProcessState state = captureProcessState();
        return state;
    }

    static void applyProcessState(const PlaybackPathProcessState & state)
    {
        for (const PlaybackPathSavedEnv & saved : state.env)
        {
            playbackPathTestRestoreEnv(saved.name, saved.value);
        }
        playbackPathTestResetEnvCaches();
        mlvSetPlaybackFastX4HqPathMode(state.fastX4HqPathMode);
        mlvSetPlaybackAggressivePreviewMode(state.aggressivePreviewMode);
        mlvSetPlaybackProxyLevel(state.proxyLevel);
        processingSetPlaybackPreviewMode(state.processingPreviewMode);
        processingSetPlaybackAggressivePreviewMode(state.processingAggressivePreviewMode);
        processingSetPlaybackPreviewScaleFactor(state.processingPreviewScaleFactor);
    }

    static void setEnvInState(PlaybackPathProcessState & state,
                              const char * name,
                              const char * value)
    {
        for (PlaybackPathSavedEnv & saved : state.env)
        {
            if (std::string(saved.name) == std::string(name))
            {
                saved.value = PlaybackPathEnvValue{ true, std::string(value) };
                return;
            }
        }
    }

    PlaybackPathProcessState m_state;
};

#endif
