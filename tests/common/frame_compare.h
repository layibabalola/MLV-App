#ifndef MLV_APP_FRAME_COMPARE_H
#define MLV_APP_FRAME_COMPARE_H

#include <cstddef>
#include <cstdint>
#include <string>

struct frame_compare_result_t {
    double psnr_db;
    uint16_t max_abs_diff;
    double mean_abs_diff;
    std::uint64_t pixels_exceeding_tolerance;
};

frame_compare_result_t compare_frames_u16(const uint16_t * reference,
                                          const uint16_t * actual,
                                          int width,
                                          int height,
                                          int channels,
                                          uint16_t per_pixel_tolerance);

frame_compare_result_t compare_frames_u8(const uint8_t * reference,
                                         const uint8_t * actual,
                                         int width,
                                         int height,
                                         int channels,
                                         uint8_t per_pixel_tolerance);

std::string frame_compare_summary(const frame_compare_result_t & result);

/*
 * Tolerance evaluator for backend-parametric comparisons.
 *
 * The backend-parametric harness records a comparison between two backends
 * (typically CPU reference vs. GPU offscreen output) and asks: "are these
 * close enough to treat as parity?" The answer is expressed as two thresholds:
 *
 *   - max_abs_diff_threshold: the largest allowed per-sample deviation.
 *     For 16-bit preview pipelines rounded through LUTs, values of 1..3 are
 *     typical; vendor drivers vary in how they handle the last bit.
 *
 *   - max_mismatch_fraction: the largest allowed fraction of samples whose
 *     per-pixel tolerance was already exceeded in the underlying compare.
 *     A value of 0.001 (0.1%) tolerates a sparse handful of pixels exceeding
 *     the per-pixel tolerance used during the original compare.
 *
 * Callers pass the frame_compare_result_t produced by compare_frames_u16 (or
 * u8) together with the total sample count (width * height * channels) that
 * was fed to the comparator. If the result already has pixels_exceeding set,
 * the fraction is computed against total_samples. Returns true when BOTH
 * thresholds pass. The populated reason string is always written (even on
 * success) so callers can log diagnostic detail.
 */
struct frame_tolerance_verdict_t {
    bool passed;
    uint16_t observed_max_abs_diff;
    double observed_mismatch_fraction;
    std::string detail;
};

frame_tolerance_verdict_t evaluate_frame_tolerance(const frame_compare_result_t & result,
                                                   std::size_t total_samples,
                                                   uint16_t max_abs_diff_threshold,
                                                   double max_mismatch_fraction);

#endif
