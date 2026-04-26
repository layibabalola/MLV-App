# Phase 3 Personal Rollout Tiers

Phase 3 playback modes are gated for one-developer dogfooding. The goal is to make experimental playback explicit while still allowing steady promotion after real use.

## Tiers

| Tier | Meaning | Availability |
|---|---|---|
| Dev | First-use dogfood tier | Hidden unless `Playback/ShowExperimentalPhase3Modes` is enabled |
| Daily-Use | Trusted enough for normal daily playback | Visible, still labeled Experimental |
| Pinned-Clip | Trusted on the pinned Dual ISO clip set | Reserved for later label relaxation |

## Promotion Rules

Dev to Daily-Use requires seven days in Dev and no recent Phase 3 auto-fallback event for that mode.

Daily-Use to Pinned-Clip requires seven days in Daily-Use plus at least 300 seconds of Phase 3 playback on each available pinned clip fingerprint.

Demotion is always allowed and resets the tier timestamp.

## Settings

Phase 3 tier state is stored in the normal MLVApp QSettings namespace:

- `Playback/Phase3FastTier`
- `Playback/Phase3FastTierEnteredAt`
- `Playback/Phase3FastDailyUseClipsValidated`
- `Playback/Phase3HQTier`
- `Playback/Phase3HQTierEnteredAt`
- `Playback/Phase3HQDailyUseClipsValidated`
- `Playback/ClipPlaytime/<sha256[0..15]>`

`Playback/ShowExperimentalPhase3Modes` is the dogfood visibility gate. `Playback/Phase3Acknowledged` stores the one-time acknowledgement for selecting a Phase 3 mode.

## Pinned Clips

The current pinned names are matched by basename, case-insensitive:

- `M16-1210`
- `M15-1355`
- `M16-1327`
- `M29-1756`

Moving a clip changes its fingerprint because the key is based on absolute path. That is acceptable for this personal rollout gate.

## Auto-Fallback

Phase 3 auto-fallback writes per-mode fallback epochs. Tier promotion reads those epochs and refuses promotion while a fallback is inside the seven-day window.

## Rollback

`MLVAPP_DISABLE_PHASE3=1` remains the top-level rollback. It forces serial behavior even if a Phase 3 quality mode is stored in settings.
