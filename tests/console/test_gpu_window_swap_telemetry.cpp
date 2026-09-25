#include "../common/minitest.h"

#include "../../platform/qt/GpuWindowSwapTelemetry.h"

TEST(GpuWindowSwapTelemetryPolicy, ZeroSwapsReportsZeroFpsAndZeroGap)
{
    GpuWindowSwapTelemetryCounters counters;

    const GpuWindowSwapTelemetrySummary summary =
        GpuWindowSwapTelemetryPolicy::summarize(counters);

    ASSERT_EQ(static_cast<quint64>(0), summary.swapCount);
    ASSERT_NEAR(0.0, summary.swapFps, 1e-9);
    ASSERT_NEAR(0.0, summary.maxGapMs, 1e-9);
}

TEST(GpuWindowSwapTelemetryPolicy, SingleSwapReportsZeroFpsNotDivideByZero)
{
    GpuWindowSwapTelemetryCounters counters;
    counters.swapCount = 1;
    counters.firstSwapQpcMs = 1000.0;
    counters.lastSwapQpcMs = 1000.0;

    const GpuWindowSwapTelemetrySummary summary =
        GpuWindowSwapTelemetryPolicy::summarize(counters);

    ASSERT_NEAR(0.0, summary.swapFps, 1e-9);
}

TEST(GpuWindowSwapTelemetryPolicy, ZeroSpanWithMultipleSwapsReportsZeroFpsNotInfinity)
{
    // Same QPC ms twice in a row (clock-granularity edge case) must not divide by zero.
    GpuWindowSwapTelemetryCounters counters;
    counters.swapCount = 3;
    counters.firstSwapQpcMs = 500.0;
    counters.lastSwapQpcMs = 500.0;

    const GpuWindowSwapTelemetrySummary summary =
        GpuWindowSwapTelemetryPolicy::summarize(counters);

    ASSERT_NEAR(0.0, summary.swapFps, 1e-9);
}

TEST(GpuWindowSwapTelemetryPolicy, EvenCadenceReportsExpectedFpsAndGapLocation)
{
    GpuWindowSwapTelemetryCounters counters;
    counters.swapCount = 61;          // 60 intervals
    counters.firstSwapQpcMs = 0.0;
    counters.lastSwapQpcMs = 1000.0;  // 60 swaps spread over 1000 ms after the first
    counters.maxGapMs = 16.6667;
    counters.maxGapBeforeSerial = 12;
    counters.maxGapAfterSerial = 13;

    const GpuWindowSwapTelemetrySummary summary =
        GpuWindowSwapTelemetryPolicy::summarize(counters);

    ASSERT_NEAR(60.0, summary.swapFps, 0.01);
    ASSERT_NEAR(16.6667, summary.maxGapMs, 1e-6);
    ASSERT_EQ(static_cast<quint64>(12), summary.maxGapBeforeSerial);
    ASSERT_EQ(static_cast<quint64>(13), summary.maxGapAfterSerial);
}

TEST(GpuWindowSwapTelemetryPolicy, SwapCountPassesThroughUnchanged)
{
    GpuWindowSwapTelemetryCounters counters;
    counters.swapCount = 4242;
    counters.firstSwapQpcMs = 0.0;
    counters.lastSwapQpcMs = 1.0;

    const GpuWindowSwapTelemetrySummary summary =
        GpuWindowSwapTelemetryPolicy::summarize(counters);

    ASSERT_EQ(static_cast<quint64>(4242), summary.swapCount);
}
