/*!
 * \file ZebraThresholds.h
 * \author Codex
 * \copyright 2026
 * \brief Shared zebra thresholds for CPU and GPU preview paths.
 */

#ifndef ZEBRATHRESHOLDS_H
#define ZEBRATHRESHOLDS_H

namespace preview_zebra
{
static constexpr int kUnderThreshold8Bit = 3;
static constexpr int kOverThreshold8Bit = 252;
static constexpr float kUnderThresholdNormalized =
    static_cast<float>(kUnderThreshold8Bit) / 255.0f;
static constexpr float kOverThresholdNormalized =
    static_cast<float>(kOverThreshold8Bit) / 255.0f;
}

#endif // ZEBRATHRESHOLDS_H
