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
