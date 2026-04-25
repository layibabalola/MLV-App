#ifndef DUALISOPLAYBACKPOLICY_H
#define DUALISOPLAYBACKPOLICY_H

#include <cstdlib>
#include <cstring>

struct DualIsoPlaybackRuntimeSettings
{
    int mode;
    int interpolation;
    int aliasMap;
    int fullResBlending;
    bool previewOverrideActive;
};

/* Diagnostic-only escape hatch for the playback Dual ISO preview override.
 * Set MLVAPP_PROFILE_DISABLE_DUALISO_OVERRIDE=1 to make this function
 * honour the receipt's selectedMode even during active playback. Useful
 * for paused-vs-playing pipeline-stage diff captures (otherwise the
 * receipt's <dualIso>1</dualIso> is silently coerced to 2 during
 * playback and headless --profile-playback runs cannot exercise the
 * full HQ recon path). NOT for production playback — the override
 * exists because full HQ recon is too slow to maintain framerate. */
inline bool dualIsoPlaybackOverrideDisabledViaEnv()
{
    static int cached = -1;
    if (cached < 0)
    {
        const char * v = std::getenv("MLVAPP_PROFILE_DISABLE_DUALISO_OVERRIDE");
        if (v && *v && std::strcmp(v, "0") != 0
                  && std::strcmp(v, "false") != 0
                  && std::strcmp(v, "FALSE") != 0
                  && std::strcmp(v, "False") != 0)
        {
            cached = 1;
        }
        else
        {
            cached = 0;
        }
    }
    return cached != 0;
}

inline DualIsoPlaybackRuntimeSettings effectiveDualIsoPlaybackRuntimeSettings(bool playbackActive,
                                                                              bool rawFixEnabled,
                                                                              int dualIsoValidity,
                                                                              int selectedMode,
                                                                              int selectedInterpolation,
                                                                              int selectedAliasMap,
                                                                              int selectedFullResBlending)
{
    if (dualIsoPlaybackOverrideDisabledViaEnv())
    {
        /* Force receipt-driven path: pretend playback is inactive, no
         * preview override, the receipt's mode flows through. */
        playbackActive = false;
    }

    const bool explicitPreviewSelected = (selectedMode == 2);
    const bool previewOverrideActive = playbackActive
                                    && rawFixEnabled
                                    && (dualIsoValidity != 0)
                                    && (selectedMode > 0);

    DualIsoPlaybackRuntimeSettings settings = {
        selectedMode,
        selectedInterpolation,
        selectedAliasMap,
        selectedFullResBlending,
        previewOverrideActive
    };

    if( explicitPreviewSelected || previewOverrideActive )
    {
        settings.mode = 2;
        settings.interpolation = 1;
        settings.aliasMap = 0;
        settings.fullResBlending = 0;
    }

    return settings;
}

#endif
