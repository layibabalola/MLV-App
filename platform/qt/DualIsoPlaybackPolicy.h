#ifndef DUALISOPLAYBACKPOLICY_H
#define DUALISOPLAYBACKPOLICY_H

struct DualIsoPlaybackRuntimeSettings
{
    int mode;
    int interpolation;
    int aliasMap;
    int fullResBlending;
    bool previewOverrideActive;
};

inline DualIsoPlaybackRuntimeSettings effectiveDualIsoPlaybackRuntimeSettings(bool playbackActive,
                                                                              bool rawFixEnabled,
                                                                              int dualIsoValidity,
                                                                              int selectedMode,
                                                                              int selectedInterpolation,
                                                                              int selectedAliasMap,
                                                                              int selectedFullResBlending)
{
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
