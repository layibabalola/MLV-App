/*!
 * \file PlaybackFramePopulationPolicy.h
 * \brief Pure arithmetic: how many frames were actually asked for during a
 *        playback smoke session, and how many of those never reached
 *        presentation -- extracted so it can be unit-tested without the GUI.
 *
 * CUDA-ATTRIBUTION-BASELINE-1 round 2 (astra major 4 / sol major 1): a
 * monotonic render-request-serial delta is immune to timeline-position loop
 * wraps, but MainWindow::m_nextRenderRequestSerial advances for BOTH the one
 * target request drawFrame() issues per call and every speculative
 * render-lookahead request -- most of which are intentionally discarded
 * (never the frame playback ends up waiting on) and never reach
 * presentation. Using that counter's delta alone as the presented-frame
 * population denominator counts every discarded lookahead as "skipped",
 * overcounting loss whenever lookahead is enabled. The target-only counter
 * (MainWindow::m_nextTargetRenderRequestSerial) is both wrap-immune (same
 * monotonic-counter argument) and free of that overcount.
 */

#ifndef PLAYBACKFRAMEPOPULATIONPOLICY_H
#define PLAYBACKFRAMEPOPULATIONPOLICY_H

#include <cstdint>

struct PlaybackFramePopulation
{
    uint64_t requestedFramesBySerial = 0;
    uint64_t skippedOrUnpresentedBySerial = 0;
    uint64_t requestedTargetFramesBySerial = 0;
    uint64_t skippedOrUnpresentedByTargetSerial = 0;
    uint64_t lookaheadRequestsBySerial = 0;
};

class PlaybackFramePopulationPolicy
{
public:
    static uint64_t nonNegativeDelta( uint64_t current, uint64_t start )
    {
        return current >= start ? current - start : 0;
    }

    /*! \param nextRenderRequestSerial the ALL-requests counter's current
     *         value (target requests + speculative lookaheads).
     *  \param startRequestSerial that counter's value when the smoke session
     *         started.
     *  \param nextTargetRenderRequestSerial the TARGET-only counter's
     *         current value.
     *  \param startTargetRequestSerial that counter's value when the smoke
     *         session started.
     *  \param presentedFrames frames that actually reached presentation.
     */
    static PlaybackFramePopulation compute( uint64_t nextRenderRequestSerial,
                                            uint64_t startRequestSerial,
                                            uint64_t nextTargetRenderRequestSerial,
                                            uint64_t startTargetRequestSerial,
                                            uint64_t presentedFrames )
    {
        PlaybackFramePopulation result;
        result.requestedFramesBySerial =
            nonNegativeDelta( nextRenderRequestSerial, startRequestSerial );
        result.requestedTargetFramesBySerial =
            nonNegativeDelta( nextTargetRenderRequestSerial, startTargetRequestSerial );
        result.skippedOrUnpresentedBySerial =
            result.requestedFramesBySerial >= presentedFrames
                ? result.requestedFramesBySerial - presentedFrames
                : 0;
        result.skippedOrUnpresentedByTargetSerial =
            result.requestedTargetFramesBySerial >= presentedFrames
                ? result.requestedTargetFramesBySerial - presentedFrames
                : 0;
        result.lookaheadRequestsBySerial =
            result.requestedFramesBySerial >= result.requestedTargetFramesBySerial
                ? result.requestedFramesBySerial - result.requestedTargetFramesBySerial
                : 0;
        return result;
    }
};

#endif // PLAYBACKFRAMEPOPULATIONPOLICY_H
