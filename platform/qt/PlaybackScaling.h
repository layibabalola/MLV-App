#ifndef PLAYBACKSCALING_H
#define PLAYBACKSCALING_H

#include <QImage>

#include <algorithm>
#include <cstdint>
#include <vector>

struct FastPlaybackScaleCache
{
    int sourceWidth = 0;
    int sourceHeight = 0;
    int targetWidth = 0;
    int targetHeight = 0;
    std::vector<int> xOffsets;
    std::vector<int> yOffsets;
};

inline bool playbackBuildFastScaledRgb8(const uint8_t *source,
                                        int sourceWidth,
                                        int sourceHeight,
                                        int targetWidth,
                                        int targetHeight,
                                        std::vector<uint8_t> &scaledBuffer,
                                        FastPlaybackScaleCache &cache)
{
    if( !source
     || sourceWidth <= 0
     || sourceHeight <= 0
     || targetWidth <= 0
     || targetHeight <= 0 )
    {
        scaledBuffer.clear();
        return false;
    }

    const size_t targetPixels =
        static_cast<size_t>( targetWidth ) * static_cast<size_t>( targetHeight );
    scaledBuffer.resize( targetPixels * 3u );

    if( cache.sourceWidth != sourceWidth
     || cache.sourceHeight != sourceHeight
     || cache.targetWidth != targetWidth
     || cache.targetHeight != targetHeight )
    {
        cache.sourceWidth = sourceWidth;
        cache.sourceHeight = sourceHeight;
        cache.targetWidth = targetWidth;
        cache.targetHeight = targetHeight;
        cache.xOffsets.resize( static_cast<size_t>( targetWidth ) );
        cache.yOffsets.resize( static_cast<size_t>( targetHeight ) );

        for( int x = 0; x < targetWidth; ++x )
        {
            const int srcX = std::min( sourceWidth - 1,
                                       static_cast<int>(
                                           ( static_cast<uint64_t>( x ) * static_cast<uint64_t>( sourceWidth ) )
                                           / static_cast<uint64_t>( targetWidth ) ) );
            cache.xOffsets[static_cast<size_t>( x )] = srcX * 3;
        }

        for( int y = 0; y < targetHeight; ++y )
        {
            const int srcY = std::min( sourceHeight - 1,
                                       static_cast<int>(
                                           ( static_cast<uint64_t>( y ) * static_cast<uint64_t>( sourceHeight ) )
                                           / static_cast<uint64_t>( targetHeight ) ) );
            cache.yOffsets[static_cast<size_t>( y )] = srcY * sourceWidth * 3;
        }
    }

    #pragma omp parallel for if(targetHeight >= 32)
    for( int y = 0; y < targetHeight; ++y )
    {
        const uint8_t *srcRow =
            source + static_cast<size_t>( cache.yOffsets[static_cast<size_t>( y )] );
        uint8_t *dstRow =
            scaledBuffer.data() + static_cast<size_t>( y ) * static_cast<size_t>( targetWidth ) * 3u;

        int x = 0;
        for( ; x + 3 < targetWidth; x += 4 )
        {
            const uint8_t *srcPixel0 =
                srcRow + static_cast<size_t>( cache.xOffsets[static_cast<size_t>( x )] );
            const uint8_t *srcPixel1 =
                srcRow + static_cast<size_t>( cache.xOffsets[static_cast<size_t>( x + 1 )] );
            const uint8_t *srcPixel2 =
                srcRow + static_cast<size_t>( cache.xOffsets[static_cast<size_t>( x + 2 )] );
            const uint8_t *srcPixel3 =
                srcRow + static_cast<size_t>( cache.xOffsets[static_cast<size_t>( x + 3 )] );
            uint8_t *dstPixel = dstRow + static_cast<size_t>( x ) * 3u;

            dstPixel[0] = srcPixel0[0];
            dstPixel[1] = srcPixel0[1];
            dstPixel[2] = srcPixel0[2];
            dstPixel[3] = srcPixel1[0];
            dstPixel[4] = srcPixel1[1];
            dstPixel[5] = srcPixel1[2];
            dstPixel[6] = srcPixel2[0];
            dstPixel[7] = srcPixel2[1];
            dstPixel[8] = srcPixel2[2];
            dstPixel[9] = srcPixel3[0];
            dstPixel[10] = srcPixel3[1];
            dstPixel[11] = srcPixel3[2];
        }

        for( ; x < targetWidth; ++x )
        {
            const uint8_t *srcPixel =
                srcRow + static_cast<size_t>( cache.xOffsets[static_cast<size_t>( x )] );
            uint8_t *dstPixel = dstRow + static_cast<size_t>( x ) * 3u;
            dstPixel[0] = srcPixel[0];
            dstPixel[1] = srcPixel[1];
            dstPixel[2] = srcPixel[2];
        }
    }

    return true;
}

inline QImage playbackWrapRgb8Image(uint8_t *data, int width, int height, int bytesPerLine = 0)
{
    if( !data || width <= 0 || height <= 0 ) return QImage();
    // Most RGB8 buffers in this codebase (playbackScaledImage8 from
    // playbackBuildFastScaledRgb8, displayImageBacking, the rendered
    // processed8 slot) are tightly packed at width*3 bytes/row, NOT
    // padded to a 4-byte boundary. Qt's no-bytesPerLine constructor
    // would compute an aligned stride and lie about the buffer layout
    // for any width%4 != 0, leading to qt_convert_rgb888_to_rgb32_ssse3
    // reading past the buffer end on the final row (SIGSEGV crash on
    // 2026-04-24). Default the explicit bytesPerLine to the packed
    // value so the QImage tells Qt the truth about the actual stride.
    // Callers whose buffers ARE padded (e.g. PlaybackPrepResult after
    // 2026-04-24, which intentionally pads to align scanlines for the
    // GUI-side .convertToFormat path) MUST pass the padded stride
    // explicitly.
    const int bpl = (bytesPerLine > 0) ? bytesPerLine : (width * 3);
    return QImage( data, width, height, bpl, QImage::Format_RGB888 );
}

inline QImage playbackBuildFastScaledImage(const uint8_t *source,
                                           int sourceWidth,
                                           int sourceHeight,
                                           int targetWidth,
                                           int targetHeight,
                                           std::vector<uint8_t> &scaledBuffer,
                                           FastPlaybackScaleCache &cache)
{
    if( !playbackBuildFastScaledRgb8( source,
                                      sourceWidth,
                                      sourceHeight,
                                      targetWidth,
                                      targetHeight,
                                      scaledBuffer,
                                      cache ) )
    {
        return QImage();
    }

    return playbackWrapRgb8Image( scaledBuffer.data(), targetWidth, targetHeight );
}

#endif // PLAYBACKSCALING_H
