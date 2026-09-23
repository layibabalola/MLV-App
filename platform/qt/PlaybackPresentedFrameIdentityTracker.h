/*!
 * \file PlaybackPresentedFrameIdentityTracker.h
 * \brief Distinct-source-frame dedup for playback-smoke presentation
 *        counting -- extracted so it can be unit-tested without the GUI.
 *
 * CUDA-ATTRIBUTION-BASELINE-1 round 5 (sol BLOCKER): MainWindow used to feed
 * PlaybackFramePopulationPolicy::computeSourceFramePopulation() raw
 * presentation-EVENT counts (one ++ per completed present). Nothing upstream
 * dedups by source-frame (displayFrame) index before that count happens, so
 * the same source frame presented three times inflated the "presented"
 * bucket exactly as much as three distinct frames presented once each --
 * identity-blind, and able to certify partition_sound=true / 0% loss on a
 * session that silently re-presented stale frames instead of advancing.
 *
 * This tracker records the DISTINCT displayFrame indices actually reaching
 * presentation, split by request origin (target vs. lookahead) the same way
 * the event counters it replaces were split. Its .size() is what should feed
 * computeSourceFramePopulation()'s presentedViaTargetFramesThisSession /
 * presentedViaLookaheadFramesThisSession parameters, not a raw event count.
 *
 * Bounded: a playback-smoke session is time-boxed (-Seconds, typically tens
 * of seconds) and displayFrame values are drawn from the loaded clip's
 * finite frame range, so the distinct-value count this tracker holds cannot
 * exceed min(clip frame count, session duration x clip fps) -- both bounded
 * in practice, and reset (cleared) at every playback-smoke session start.
 */

#ifndef PLAYBACKPRESENTEDFRAMEIDENTITYTRACKER_H
#define PLAYBACKPRESENTEDFRAMEIDENTITYTRACKER_H

#include <cstdint>

#include <QSet>

class PlaybackPresentedFrameIdentityTracker
{
public:
    void reset()
    {
        m_distinctTargetFrames.clear();
        m_distinctLookaheadFrames.clear();
    }

    /*! \param displayFrame the source-frame index that just reached
     *         presentation.
     *  \param viaLookahead true iff the presenting request's
     *         PresentationRequestContext::playbackLookaheadRequest was true.
     */
    void notePresentedFrame( uint64_t displayFrame, bool viaLookahead )
    {
        if( viaLookahead )
            m_distinctLookaheadFrames.insert( displayFrame );
        else
            m_distinctTargetFrames.insert( displayFrame );
    }

    uint64_t distinctTargetPresentedCount() const
    {
        return static_cast<uint64_t>( m_distinctTargetFrames.size() );
    }

    uint64_t distinctLookaheadPresentedCount() const
    {
        return static_cast<uint64_t>( m_distinctLookaheadFrames.size() );
    }

private:
    QSet<uint64_t> m_distinctTargetFrames;
    QSet<uint64_t> m_distinctLookaheadFrames;
};

#endif // PLAYBACKPRESENTEDFRAMEIDENTITYTRACKER_H
