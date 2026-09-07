/*!
 * \file DualIsoLevelSyncPolicy.h
 * \brief Pure decision for whether the dual-ISO black/white level sync must
 *        run before a GPU-preview processing config bake.
 */

#ifndef DUALISOLEVELSYNCPOLICY_H
#define DUALISOLEVELSYNCPOLICY_H

namespace dual_iso_level_sync_policy {

/*! \brief Decide whether the wait/sync/invalidate block must run before the
 *         config bake.
 *
 * \param sourceReady    A loaded MLV/object is present (the caller's existing
 *                        null-object guard); callers must not evaluate
 *                        \p levelsOutOfSync when this is false.
 * \param bakePending     The GPU-preview processing config is about to be
 *                        (re)baked for the current settings.
 * \param levelsOutOfSync The dual-ISO black/white levels are stale relative
 *                        to the current recon scale.
 *
 * No dual-mode gate: the out-of-sync signal alone is authoritative, so a
 * normal (non-dual-ISO) clip whose levels are out of sync must still sync.
 */
inline bool shouldSync( bool sourceReady, bool bakePending, bool levelsOutOfSync )
{
    return sourceReady && bakePending && levelsOutOfSync;
}

} // namespace dual_iso_level_sync_policy

#endif // DUALISOLEVELSYNCPOLICY_H
