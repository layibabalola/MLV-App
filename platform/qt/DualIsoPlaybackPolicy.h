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
    /* Mean23 fast-path override: independent of previewOverrideActive.
     * The preview override marker says the automatic missed-target rescue
     * branch fired; explicit user Preview still forces mode=2 rowscale, but
     * automatic rescue keeps HQ recon for dual-ISO clips and uses mean23 as
     * the playback-safe interpolation override. */
    bool playbackForceMean23;
    /* Phase E5: scale-aware alias_map downgrade. Enabled when HQ recon will
     * actually run during playback (same trigger surface as
     * playbackForceMean23). The flag itself is "policy approves" — the
     * caller (MainWindow) is responsible for combining it with the
     * runtime scale factor (>= 4) before flipping the llrawproc field.
     * At scale 4 the 1/16 pixel-count buffer is itself an anti-aliasing
     * operation, so alias_map suppression spends ~4-9 ms/frame producing
     * diminishing returns on already-downsampled data. The diagnostic env
     * var MLVAPP_PLAYBACK_KEEP_ALIAS_MAP_AT_SCALE=1 disables the downgrade
     * for users who notice quality regressions. Receipt-authored values for
     * diso_alias_map are never modified, so paused/scrubbing/export keep the
     * user's intended quality. */
    bool playbackDisableAliasMapAtScale;
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

/* Diagnostic-only escape hatch for the mean23 playback override (peer to
 * MLVAPP_PROFILE_DISABLE_DUALISO_OVERRIDE above). Set to 1 to make this
 * function leave playbackForceMean23 == false even when the override
 * conditions are otherwise satisfied. Useful for A/B'ing AMaZE vs mean23
 * cadence in the headless --profile-playback harness. The receipt's
 * authored interpolation flows through unchanged in either case. */
inline bool dualIsoPlaybackMean23OverrideDisabledViaEnv()
{
    static int cached = -1;
    if (cached < 0)
    {
        const char * v = std::getenv("MLVAPP_DISABLE_DUALISO_PLAYBACK_MEAN23_OVERRIDE");
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

/* Phase E5: opt-in scale-aware alias_map downgrade.
 *
 * The hypothesis that alias_map has "diminishing returns on already-
 * downsampled data" (because the 4x4 downsample is itself an anti-
 * aliasing operation) split cleanly when measured: alias_map can be
 * safely disabled at scale 4 (SSIM 0.9999 on M16-1210, ~4-9 ms/frame
 * win). So we ship alias_map disable as opt-in (default OFF) and leave
 * the public-facing path otherwise unchanged.
 *
 * Set MLVAPP_PLAYBACK_DOWNGRADE_ALIAS_MAP_AT_SCALE=1 to enable the
 * alias_map downgrade. */
inline bool dualIsoPlaybackDowngradeAliasMapAtScaleViaEnv()
{
    static int cached = -1;
    if (cached < 0)
    {
        const char * v = std::getenv("MLVAPP_PLAYBACK_DOWNGRADE_ALIAS_MAP_AT_SCALE");
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

/* Opt-in: prefer HQ Dual ISO recon with mean23 interpolation during playback
 * over the legacy preview-rowscale-forced override. Closes the structural magenta
 * cast that preview rowscale introduces (preview's global linear gain is
 * fundamentally different from HQ matched-pair recon and produces a
 * deterministic chroma bias on bright lanes). The GUI Playback Quality modes
 * now use this path for Dual ISO playback by default; the env var remains
 * the explicit override for tests and external harnesses. Set
 *   MLVAPP_PLAYBACK_PREFER_HQ_MEAN23=1
 * to enable.
 *
 * Tradeoff: HQ recon is much slower than preview rowscale at full sensor
 * resolution. The GUI compensates by using the pipeline-resolution model
 * (for example x8 Dual ISO Fast/Auto paths) so HQ + mean23 runs after early
 * Bayer-domain reduction instead of at full sensor size.
 *
 * Without either this env var or a GUI fallback, playback still records the
 * automatic rescue branch and keeps its alias_map relaxation. With the opt-in,
 * even that rescue marker is suppressed and playback uses receipt HQ + mean23. */
inline bool dualIsoPlaybackPreferHqMean23ViaEnv()
{
    static int cached = -1;
    if (cached < 0)
    {
        const char * v = std::getenv("MLVAPP_PLAYBACK_PREFER_HQ_MEAN23");
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

/* Phase 4E: GUI-grade override hook for the HQ+mean23 playback toggle.
 *
 * This is consulted by effectiveDualIsoPlaybackRuntimeSettings() AFTER the
 * env-var check, so MLVAPP_PLAYBACK_PREFER_HQ_MEAN23 retains priority for
 * dev/CI overrides. The MainWindow installs a callback that returns the
 * effective desire derived from QSettings (Playback/QualityMode == 1 or 2).
 *
 * Header-only by design: QSettings construction would force a Qt dependency
 * here, which the headless console-test harness (which links this header
 * into pipeline_stubs / non-GUI TUs) doesn't want to pay. Instead, the GUI
 * code installs a function pointer at startup. */
using DualIsoPlaybackPreferHqMean23Fallback = bool (*)();

inline DualIsoPlaybackPreferHqMean23Fallback &
dualIsoPlaybackPreferHqMean23FallbackRef()
{
    static DualIsoPlaybackPreferHqMean23Fallback fallback = nullptr;
    return fallback;
}

inline void
setDualIsoPlaybackPreferHqMean23Fallback(DualIsoPlaybackPreferHqMean23Fallback fallback)
{
    dualIsoPlaybackPreferHqMean23FallbackRef() = fallback;
}

/* Convenience: true if env var is set OR (env var unset AND fallback says
 * yes). Used by the runtime settings function below. */
inline bool dualIsoPlaybackPreferHqMean23()
{
    /* Env var takes priority. We check the env var directly here (not via
     * the cached ViaEnv() helper) so the "unset" case correctly falls
     * through to the GUI fallback. */
    const char * v = std::getenv("MLVAPP_PLAYBACK_PREFER_HQ_MEAN23");
    if (v && *v)
    {
        if (std::strcmp(v, "0") != 0
            && std::strcmp(v, "false") != 0
            && std::strcmp(v, "FALSE") != 0
            && std::strcmp(v, "False") != 0)
        {
            return true;
        }
        return false;
    }
    DualIsoPlaybackPreferHqMean23Fallback fallback =
        dualIsoPlaybackPreferHqMean23FallbackRef();
    return fallback ? fallback() : false;
}

inline DualIsoPlaybackRuntimeSettings effectiveDualIsoPlaybackRuntimeSettings(bool playbackActive,
                                                                              bool rawFixEnabled,
                                                                              int dualIsoValidity,
                                                                              int selectedMode,
                                                                              int selectedInterpolation,
                                                                              int selectedAliasMap,
                                                                              int selectedFullResBlending,
                                                                              bool playbackFastProcessingSubsetActive = false,
                                                                              int effectiveScale = 1)
{
    if (dualIsoPlaybackOverrideDisabledViaEnv())
    {
        /* Force receipt-driven path: pretend playback is inactive, no
         * preview override, the receipt's mode flows through. */
        playbackActive = false;
    }

    const bool explicitPreviewSelected = (selectedMode == 2);
    const bool preferHqMean23 = dualIsoPlaybackPreferHqMean23();
    /* When the user has opted into HQ-during-playback (via env var or via
     * the GUI Playback Quality dial that sets the QSettings-backed
     * fallback), suppress the automatic rescue marker entirely so the
     * receipt's selectedMode (typically 1 = HQ recon) flows through. The
     * mean23 override below then catches the now-still-HQ playback path and
     * writes the playbackForceMean23 flag, giving us HQ + mean23 (cast
     * closed) at the cost of cadence. */
    /* Scale-aware suppression (2026-06-29): the AUTO preview-override at x2 reduced-res
     * playback can fall out of HQ/full20 at bright-content transitions (the dual_iso
     * full20 timing flag drops 1->0 -> path=0/non-HQ) and renders a MAGENTA cliff
     * (M16-1243 bright atrium f330+, proven by the peak-frame A/B: -PreferHqMean23 /
     * disable-override both clear it by keeping HQ/full20). Suppress the AUTO override
     * at x2 so the hqWillRunDuringPlayback block below sets playbackForceMean23 (HQ +
     * mean23, "cast closed") -- the proven neutral path that is also FPS-neutral-or-better
     * (the cliff's path=0 fallback was itself slow). Explicit user preview (selectedMode==2
     * -> explicitPreviewSelected) is UNTOUCHED, and x1 still records the rescue marker
     * while keeping HQ recon/full-res blending.
     * NOTE: x4/x8 subset show the same magenta (SEQ389) and likely want the same suppression;
     * scoped to ==2 here as the validated case, x4/x8 is a flagged fast-follow. */
    const bool suppressAutoPreviewOverrideAtScale = (effectiveScale == 2);
    const bool previewOverrideActive = playbackActive
                                    && (rawFixEnabled || playbackFastProcessingSubsetActive)
                                    && (dualIsoValidity != 0)
                                    && (selectedMode > 0)
                                    && !preferHqMean23
                                    && !suppressAutoPreviewOverrideAtScale;

    DualIsoPlaybackRuntimeSettings settings = {
        selectedMode,
        selectedInterpolation,
        selectedAliasMap,
        selectedFullResBlending,
        previewOverrideActive,
        false
    };

    if( explicitPreviewSelected )
    {
        settings.mode = 2;
        settings.interpolation = 1;
        settings.aliasMap = 0;
        settings.fullResBlending = 0;
    }
    else if( previewOverrideActive )
    {
        /* Keep the automatic rescue visible, but do not move dual-ISO clips
         * into the preview rowscale family: mode=2/fullResBlending=0 is the
         * aliasing failure path. Retain HQ recon plus receipt-authored
         * full-res blending, and keep only the low-risk alias_map relaxation. */
        settings.aliasMap = 0;
    }

    /* Mean23 playback override: only applies when the receipt-driven HQ
     * path is going to actually run during playback. That happens when
     * the preview override is suppressed (env or invalid Dual ISO) yet
     * playback is active and the receipt asks for HQ (mode == 1). The
     * override leaves the interpolation field's authored value alone
     * (so paused/scrubbing/export keep AMaZE) and instead surfaces a
     * separate flag the caller writes to llrawproc->diso_playback_force_mean23. */
    const bool hqWillRunDuringPlayback = playbackActive
                                       && rawFixEnabled
                                       && (dualIsoValidity != 0)
                                       && (settings.mode == 1);
    if (hqWillRunDuringPlayback && !dualIsoPlaybackMean23OverrideDisabledViaEnv())
    {
        settings.playbackForceMean23 = true;
    }
    /* Phase E5 scale-aware downgrade: same trigger surface as the mean23
     * override (HQ recon will run during playback). Each stage has its
     * own opt-in env var because their visual costs differ by orders of
     * magnitude (alias_map OFF: SSIM 0.9999, safe; FR blending OFF:
     * SSIM 0.0001, broken). The actual scale-factor comparison happens
     * at the caller — these flags only express "policy approves IF
     * scale >= 4". Default OFF for both because the user prompt's "8-
     * 15 ms savings" estimate over-stated the alias_map alone: real
     * savings are ~4-9 ms p50 (still meaningful at high scales). */
    if( hqWillRunDuringPlayback && dualIsoPlaybackDowngradeAliasMapAtScaleViaEnv() )
    {
        settings.playbackDisableAliasMapAtScale = true;
    }

    return settings;
}

#endif
