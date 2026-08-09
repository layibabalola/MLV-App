# Canonical lane-health class vocabulary and lease boundary. ASCII only.
#
# SWEEP-CLASS-1 part (a). This file is the SINGLE authority for two things that were
# previously restated in three places and agreed in none:
#
#   1. the lane-state class list, and
#   2. the boundary at which an adopted lease is called DARK.
#
# WHY ONE FILE. Before this, `.claude-state/RESUME.md` declared one vocabulary in prose,
# `tools/coordination/sweep-lane-health.ps1` emitted a second, and the untracked Tier-A
# board sweep emitted a third. Three authorities for one predicate is not a documentation
# problem, it is two of them being wrong at any given moment with nothing to detect it.
# Every consumer now reads THIS file, so the vocabularies converge by construction rather
# than by anyone remembering to update the others.
#
# RESUME.md must CITE this file rather than restate the list. It cannot be the source
# itself: `.claude-state/` is gitignored (.gitignore:52), so it can never be reviewed
# through a gated range, and `owner-of.ps1` returns `unknown` for it, which `lane-commit`
# treats as a hard block.
#
# THE BOUNDARY, and why the old one was wrong.
# Old rule: LIVE while age <= declared; EXPIRED while age <= 2*declared; DARK beyond.
# New rule: LIVE while age <= min(declared, 30) + 20; DARK beyond. EXPIRED is DELETED.
#
# The board's liveness threshold has always been min(declared,30)+20 with a 20-minute
# floor. The `2 * declared` form is a SECOND boundary function for the same predicate, and
# the two agree at exactly one point, declared = 20. Everywhere else they disagree:
#
#   declared |  2*d  | min(d,30)+20 | effect of the old rule
#   ---------|-------|--------------|------------------------------------------------
#        5   |  10m  |     25m      | sol declares 5, so it was called DARK at 10m
#            |       |              | against a 25m board threshold - it spent its
#            |       |              | turns reporting its own false alarm
#       20   |  40m  |     40m      | the ONLY value where the two agree
#       30   |  60m  |     50m      | DARK fired 10 MINUTES LATE, board-wide, for the
#            |       |              | entire history of the 30-minute default (part (e))
#       60   | 120m  |     50m      | a long declaration bought 70 extra minutes of
#            |       |              | undetected silence
#
# EXPIRED existed ONLY to name the interval between the two boundaries. With one boundary
# there is no such interval, so the class is removed rather than kept as a synonym - a
# vocabulary entry that can never be emitted is a third authority waiting to happen.
@{
    # Every lane-state a sweep may emit. Consumers validate against this list; anything
    # outside it is a defect in the emitter, not a new state.
    LaneStateClasses = @(
        'LIVE'                # lease is within the board threshold
        'DARK'                # lease is beyond the board threshold
        'MISSING'             # registered lane has no lease file at all
        'PENDING-ADOPTION'    # lane has not adopted lease gating
        'LEASE-UNPARSEABLE'   # lease file exists but cannot be read as a lease
        'DISPLACED'           # lease names a session other than the registered seat
        'SEATED-UNLEASED'     # registered, lease lapsed, dormancy DECLARED in the note
        'UNREGISTERED-LEASE'  # a lease exists for a lane with no registry seat
    )

    # Board liveness boundary: min(leaseMinutes, Cap) + Grace.
    DarkThresholdCapMinutes   = 30
    DarkThresholdGraceMinutes = 20
    DarkThresholdFormula      = 'min(leaseMinutes,30)+20'

    # Identity states for inbound-watch heartbeats. UNKNOWN and UNATTRIBUTED are
    # deliberately distinct from OK: a green token that also means "could not tell" is
    # worth less than no token at all.
    WatchHeartbeatClasses = @('OK', 'STALE', 'UNKNOWN', 'UNATTRIBUTED', 'FOREIGN')
    WatchHeartbeatFreshMinutes = 5
}
