/*!
 * \file PlaybackFrameRange.h
 * \brief Small playback frame/cut-range normalization helpers.
 */

#ifndef PLAYBACKFRAMERANGE_H
#define PLAYBACKFRAMERANGE_H

#include <algorithm>
#include <cstdint>

namespace playback_frame_range {

struct CutRange
{
    int cutIn = 0;
    int cutOut = 0;
    bool valid = false;
    bool changed = false;
};

inline int clampFrameIndex( int requestedFrame, int totalFrames, bool *changed = nullptr )
{
    if( totalFrames <= 0 )
    {
        if( changed ) *changed = requestedFrame != 0;
        return 0;
    }

    const int clamped = std::max( 0, std::min( requestedFrame, totalFrames - 1 ) );
    if( changed ) *changed = clamped != requestedFrame;
    return clamped;
}

inline bool isValidFrameNumber( uint32_t frameNumber, int totalFrames )
{
    return totalFrames > 0 && frameNumber < static_cast<uint32_t>( totalFrames );
}

inline CutRange normalizeCutRange( int cutIn, int cutOut, int totalFrames )
{
    CutRange result;
    if( totalFrames <= 0 )
    {
        result.changed = cutIn != 0 || cutOut != 0;
        return result;
    }

    result.valid = true;
    result.cutIn = std::max( 1, std::min( cutIn, totalFrames ) );
    if( cutOut < result.cutIn || cutOut > totalFrames )
    {
        result.cutOut = totalFrames;
    }
    else
    {
        result.cutOut = cutOut;
    }
    if( result.cutOut < result.cutIn )
    {
        result.cutOut = result.cutIn;
    }

    result.changed = result.cutIn != cutIn || result.cutOut != cutOut;
    return result;
}

inline int firstFrameIndex( const CutRange &range )
{
    return range.valid ? range.cutIn - 1 : 0;
}

inline int lastFrameIndex( const CutRange &range )
{
    return range.valid ? range.cutOut - 1 : 0;
}

} // namespace playback_frame_range

#endif // PLAYBACKFRAMERANGE_H
