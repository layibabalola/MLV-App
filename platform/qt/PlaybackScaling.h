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

/* Phase 4D: bilinear upscale cache. Stores per-output-row source-y indices
 * and per-output-column source-x indices plus 8-bit fractional weights
 * (0..256, where 256 means "right at the next sample"). Bilinear is the
 * preferred path when the render thread is upscaling Phase 4B's downsampled
 * output back to the display target — nearest-neighbour shows visible jaggies
 * and moire on diagonal edges, bilinear smooths them. */
struct BilinearPlaybackScaleCache
{
    int sourceWidth = 0;
    int sourceHeight = 0;
    int targetWidth = 0;
    int targetHeight = 0;
    /* For each output column x: x0SourceOffsets[x] = sourceX0 * 3 (RGB byte
     * offset of the left neighbour); xWeights[x] = (sourceX - sourceX0) * 256
     * clamped into [0, 256], stored as int16 so the multiply-add stays in
     * 16-bit integer space.  x1SourceOffsets[x] = sourceX1 * 3 where
     * sourceX1 = min(sourceWidth-1, sourceX0+1) (clamped at the right edge). */
    std::vector<int> x0SourceOffsets;
    std::vector<int> x1SourceOffsets;
    std::vector<int16_t> xWeights;
    /* For each output row y: y0RowOffsets[y] = sourceY0 * sourceWidth * 3;
     * y1RowOffsets[y] = sourceY1 * sourceWidth * 3 (sourceY1 clamped at the
     * bottom edge); yWeights[y] = (sourceY - sourceY0) * 256. */
    std::vector<int> y0RowOffsets;
    std::vector<int> y1RowOffsets;
    std::vector<int16_t> yWeights;
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

/* Phase 4D: bilinear RGB8 -> RGB8 scaler. Used by RenderFrameThread when the
 * render path produced a half-/quarter-resolution buffer (Phase 4B) and the
 * display target is larger.  Cache is populated on the first call for a given
 * source/target size combination and reused across frames; the heavy work is
 * the per-row sampling loop, which is OMP-parallel mirroring the existing
 * nearest-neighbour helper.
 *
 * The integer-math formulation uses 8-bit fractional weights (Q0.8) and keeps
 * the per-channel computation in 16-bit accumulators:
 *
 *   top    = src(x0,y0)*(256-fx) + src(x1,y0)*fx       // <= 65535, fits u16
 *   bottom = src(x0,y1)*(256-fx) + src(x1,y1)*fx       // <= 65535, fits u16
 *   out    = (top*(256-fy) + bottom*fy + 32768) >> 16  // round-to-nearest
 *
 * The +32768 / >>16 form is the standard Q0.8 dual-bilinear rounding
 * convention (top/bottom are already Q8.0 in 0..65535, the second weight pair
 * scales them to Q16.0 then we round-shift back to Q8.0).  This keeps
 * everything in 32-bit ints with no float ops per pixel. */
inline bool playbackBuildBilinearScaledRgb8(const uint8_t *source,
                                            int sourceWidth,
                                            int sourceHeight,
                                            int targetWidth,
                                            int targetHeight,
                                            std::vector<uint8_t> &scaledBuffer,
                                            BilinearPlaybackScaleCache &cache)
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
        cache.x0SourceOffsets.resize( static_cast<size_t>( targetWidth ) );
        cache.x1SourceOffsets.resize( static_cast<size_t>( targetWidth ) );
        cache.xWeights.resize( static_cast<size_t>( targetWidth ) );
        cache.y0RowOffsets.resize( static_cast<size_t>( targetHeight ) );
        cache.y1RowOffsets.resize( static_cast<size_t>( targetHeight ) );
        cache.yWeights.resize( static_cast<size_t>( targetHeight ) );

        /* Map output sample x onto the source by aligning sample centres.  We
         * want srcCentreX = (x + 0.5) * sourceWidth / targetWidth - 0.5 so the
         * pixel grids line up; doing this in fixed-point Q0.8:
         *   numerator = (2*x + 1) * sourceWidth * 128 / targetWidth - 128
         * The result is an integer "256 * srcCentreX". */
        for( int x = 0; x < targetWidth; ++x )
        {
            int64_t numerator =
                static_cast<int64_t>( 2 * x + 1 ) * static_cast<int64_t>( sourceWidth ) * 128
                  / static_cast<int64_t>( targetWidth )
                - 128;
            int x0 = static_cast<int>( numerator >> 8 );
            int weight = static_cast<int>( numerator - ( static_cast<int64_t>( x0 ) << 8 ) );
            if( x0 < 0 )
            {
                x0 = 0;
                weight = 0;
            }
            int x1 = x0 + 1;
            if( x1 >= sourceWidth )
            {
                x1 = sourceWidth - 1;
                if( x0 >= sourceWidth )
                {
                    x0 = sourceWidth - 1;
                    weight = 0;
                }
            }
            cache.x0SourceOffsets[static_cast<size_t>( x )] = x0 * 3;
            cache.x1SourceOffsets[static_cast<size_t>( x )] = x1 * 3;
            cache.xWeights[static_cast<size_t>( x )] = static_cast<int16_t>( weight );
        }

        for( int y = 0; y < targetHeight; ++y )
        {
            int64_t numerator =
                static_cast<int64_t>( 2 * y + 1 ) * static_cast<int64_t>( sourceHeight ) * 128
                  / static_cast<int64_t>( targetHeight )
                - 128;
            int y0 = static_cast<int>( numerator >> 8 );
            int weight = static_cast<int>( numerator - ( static_cast<int64_t>( y0 ) << 8 ) );
            if( y0 < 0 )
            {
                y0 = 0;
                weight = 0;
            }
            int y1 = y0 + 1;
            if( y1 >= sourceHeight )
            {
                y1 = sourceHeight - 1;
                if( y0 >= sourceHeight )
                {
                    y0 = sourceHeight - 1;
                    weight = 0;
                }
            }
            cache.y0RowOffsets[static_cast<size_t>( y )] = y0 * sourceWidth * 3;
            cache.y1RowOffsets[static_cast<size_t>( y )] = y1 * sourceWidth * 3;
            cache.yWeights[static_cast<size_t>( y )] = static_cast<int16_t>( weight );
        }
    }

    #pragma omp parallel for if(targetHeight >= 32)
    for( int y = 0; y < targetHeight; ++y )
    {
        const uint8_t *rowTop =
            source + static_cast<size_t>( cache.y0RowOffsets[static_cast<size_t>( y )] );
        const uint8_t *rowBottom =
            source + static_cast<size_t>( cache.y1RowOffsets[static_cast<size_t>( y )] );
        const int fy = cache.yWeights[static_cast<size_t>( y )];
        const int fyComp = 256 - fy;
        uint8_t *dstRow =
            scaledBuffer.data() + static_cast<size_t>( y ) * static_cast<size_t>( targetWidth ) * 3u;

        for( int x = 0; x < targetWidth; ++x )
        {
            const int x0Off = cache.x0SourceOffsets[static_cast<size_t>( x )];
            const int x1Off = cache.x1SourceOffsets[static_cast<size_t>( x )];
            const int fx = cache.xWeights[static_cast<size_t>( x )];
            const int fxComp = 256 - fx;

            const uint8_t *p00 = rowTop + x0Off;
            const uint8_t *p10 = rowTop + x1Off;
            const uint8_t *p01 = rowBottom + x0Off;
            const uint8_t *p11 = rowBottom + x1Off;
            uint8_t *dstPixel = dstRow + static_cast<size_t>( x ) * 3u;

            /* Channel 0 */
            const int top0    = p00[0] * fxComp + p10[0] * fx;
            const int bottom0 = p01[0] * fxComp + p11[0] * fx;
            dstPixel[0] = static_cast<uint8_t>(
                ( top0 * fyComp + bottom0 * fy + 32768 ) >> 16 );

            /* Channel 1 */
            const int top1    = p00[1] * fxComp + p10[1] * fx;
            const int bottom1 = p01[1] * fxComp + p11[1] * fx;
            dstPixel[1] = static_cast<uint8_t>(
                ( top1 * fyComp + bottom1 * fy + 32768 ) >> 16 );

            /* Channel 2 */
            const int top2    = p00[2] * fxComp + p10[2] * fx;
            const int bottom2 = p01[2] * fxComp + p11[2] * fx;
            dstPixel[2] = static_cast<uint8_t>(
                ( top2 * fyComp + bottom2 * fy + 32768 ) >> 16 );
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
