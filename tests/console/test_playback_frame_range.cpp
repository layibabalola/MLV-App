#include "../common/minitest.h"
#include "../../platform/qt/PlaybackFrameRange.h"

TEST( PlaybackFrameRange, ZeroCutRangeRepairsToWholeClip )
{
    const playback_frame_range::CutRange range =
        playback_frame_range::normalizeCutRange( 0, 0, 60 );

    ASSERT_TRUE( range.valid );
    ASSERT_TRUE( range.changed );
    ASSERT_EQ( 1, range.cutIn );
    ASSERT_EQ( 60, range.cutOut );
    ASSERT_EQ( 0, playback_frame_range::firstFrameIndex( range ) );
    ASSERT_EQ( 59, playback_frame_range::lastFrameIndex( range ) );
}

TEST( PlaybackFrameRange, CutInAboveClipClampsToLastFrame )
{
    const playback_frame_range::CutRange range =
        playback_frame_range::normalizeCutRange( 75, 10, 60 );

    ASSERT_TRUE( range.valid );
    ASSERT_TRUE( range.changed );
    ASSERT_EQ( 60, range.cutIn );
    ASSERT_EQ( 60, range.cutOut );
    ASSERT_EQ( 59, playback_frame_range::firstFrameIndex( range ) );
    ASSERT_EQ( 59, playback_frame_range::lastFrameIndex( range ) );
}

TEST( PlaybackFrameRange, NegativeRequestedFrameClampsBeforeUnsignedRenderRequest )
{
    bool changed = false;
    const int frame =
        playback_frame_range::clampFrameIndex( -1, 60, &changed );

    ASSERT_TRUE( changed );
    ASSERT_EQ( 0, frame );
}

TEST( PlaybackFrameRange, PastEndRequestedFrameClampsBeforeRenderRequest )
{
    bool changed = false;
    const int frame =
        playback_frame_range::clampFrameIndex( 429496, 60, &changed );

    ASSERT_TRUE( changed );
    ASSERT_EQ( 59, frame );
}

TEST( PlaybackFrameRange, UnsignedSentinelIsRejectedAtRenderBoundary )
{
    ASSERT_FALSE( playback_frame_range::isValidFrameNumber( 0xFFFFFFFFu, 60 ) );
    ASSERT_FALSE( playback_frame_range::isValidFrameNumber( 60u, 60 ) );
    ASSERT_TRUE( playback_frame_range::isValidFrameNumber( 59u, 60 ) );
}
