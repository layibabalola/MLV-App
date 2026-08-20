#ifndef RAW_ASPECT_STRETCH_POLICY_H
#define RAW_ASPECT_STRETCH_POLICY_H

#include <cmath>
#include <limits>
#include "../../platform/qt/StretchFactors.h"

struct RawAspectStretchSelection
{
    int horizontalIndex = 0;
    int verticalIndex = 0;
    double horizontalFactor = 1.0;
    double verticalFactor = 1.0;
    bool valid = false;
};

struct RawAspectRenderedDimensions
{
    int width = 0;
    int height = 0;
    bool valid = false;
};

/* Convert the receipt stretch controls into an integer output geometry without
 * silently dropping a horizontal factor from the legacy vertical-1/3
 * representation.  Rendered paths historically represent V_033 as horizontal
 * x3, so the effective X scale is stretchX*3 and the effective Y scale is 1.
 * Preserve the legacy positive-value truncation used by GUI and batch paths;
 * the only behavioral repair here is retaining the horizontal factor when
 * V_033 is represented as horizontal x3. Callers provide the largest
 * dimension their downstream encoder can represent. */
inline RawAspectRenderedDimensions rawAspectRenderedDimensions(
    int sourceWidth,
    int sourceHeight,
    double stretchFactorX,
    double stretchFactorY,
    int maximumDimension)
{
    RawAspectRenderedDimensions result;
    if(sourceWidth <= 0 || sourceHeight <= 0 || maximumDimension <= 0
       || !std::isfinite(stretchFactorX) || !std::isfinite(stretchFactorY)
       || stretchFactorX <= 0.0 || stretchFactorY <= 0.0)
        return result;

    const double effectiveX = stretchFactorX
        * (stretchFactorY == STRETCH_V_033 ? 3.0 : 1.0);
    const double effectiveY = stretchFactorY == STRETCH_V_033
        ? 1.0 : stretchFactorY;
    const double scaledWidth = static_cast<double>(sourceWidth) * effectiveX;
    const double scaledHeight = static_cast<double>(sourceHeight) * effectiveY;
    if(!std::isfinite(scaledWidth) || !std::isfinite(scaledHeight)
       || scaledWidth <= 0.0 || scaledHeight <= 0.0)
        return result;

    const double truncatedWidth = std::floor(scaledWidth);
    const double truncatedHeight = std::floor(scaledHeight);
    if(truncatedWidth < 1.0 || truncatedHeight < 1.0
       || truncatedWidth > static_cast<double>(maximumDimension)
       || truncatedHeight > static_cast<double>(maximumDimension))
        return result;

    result.width = static_cast<int>(truncatedWidth);
    result.height = static_cast<int>(truncatedHeight);
    result.valid = true;
    return result;
}

/* Select the exact pair of application stretch controls whose Y/X ratio
 * represents RAWC sampling_y/sampling_x. Index 3's vertical factor is 1/3;
 * the GUI implements that as an equivalent horizontal x3 presentation. */
inline RawAspectStretchSelection rawAspectStretchSelectionForRatio(double aspectRatio)
{
    RawAspectStretchSelection result;
    if(!std::isfinite(aspectRatio) || aspectRatio <= 0.0) return result;
    constexpr double horizontalRatio[] = { 1.0, 5.0/4.0, 4.0/3.0, 3.0/2.0,
                                           5.0/3.0, 7.0/4.0, 9.0/5.0, 2.0 };
    constexpr double verticalRatio[] = { 1.0, 5.0/3.0, 3.0, 1.0/3.0 };
    constexpr double horizontalFactor[] = {
        STRETCH_H_100, STRETCH_H_125, STRETCH_H_133, STRETCH_H_150,
        STRETCH_H_167, STRETCH_H_175, STRETCH_H_180, STRETCH_H_200
    };
    constexpr double verticalFactor[] = {
        STRETCH_V_100, STRETCH_V_167, STRETCH_V_300, STRETCH_V_033
    };
    double bestError = std::numeric_limits<double>::infinity();
    for(int h = 0; h < 8; ++h)
    {
        for(int v = 0; v < 4; ++v)
        {
            const double candidate = verticalRatio[v] / horizontalRatio[h];
            const double error = std::fabs(std::log(candidate / aspectRatio));
            if(error < bestError)
            {
                bestError = error;
                result.horizontalIndex = h;
                result.verticalIndex = v;
                result.horizontalFactor = horizontalFactor[h];
                result.verticalFactor = verticalFactor[v];
            }
        }
    }
    result.valid = bestError <= 0.0002;
    return result;
}

#endif
