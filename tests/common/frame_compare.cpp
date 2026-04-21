#include "frame_compare.h"

#include <cmath>
#include <limits>
#include <sstream>

template <typename T>
static frame_compare_result_t compare_frames_impl(const T * reference,
                                                  const T * actual,
                                                  int width,
                                                  int height,
                                                  int channels,
                                                  T per_pixel_tolerance,
                                                  double max_value)
{
    const std::size_t sample_count = static_cast<std::size_t>(width) *
                                     static_cast<std::size_t>(height) *
                                     static_cast<std::size_t>(channels);

    double abs_sum = 0.0;
    double squared_sum = 0.0;
    std::uint64_t pixels_exceeding = 0;
    uint16_t max_abs = 0;

    for (std::size_t index = 0; index < sample_count; ++index) {
        const int delta = static_cast<int>(actual[index]) - static_cast<int>(reference[index]);
        const uint16_t abs_delta = static_cast<uint16_t>(std::abs(delta));
        if (abs_delta > max_abs) {
            max_abs = abs_delta;
        }
        if (abs_delta > per_pixel_tolerance) {
            pixels_exceeding += 1;
        }
        abs_sum += static_cast<double>(abs_delta);
        squared_sum += static_cast<double>(delta) * static_cast<double>(delta);
    }

    const double sample_count_double = static_cast<double>(sample_count);
    const double mean_abs = sample_count == 0 ? 0.0 : abs_sum / sample_count_double;
    const double mse = sample_count == 0 ? 0.0 : squared_sum / sample_count_double;
    const double psnr = mse == 0.0
        ? std::numeric_limits<double>::infinity()
        : 20.0 * std::log10(max_value) - 10.0 * std::log10(mse);

    return {psnr, max_abs, mean_abs, pixels_exceeding};
}

frame_compare_result_t compare_frames_u16(const uint16_t * reference,
                                          const uint16_t * actual,
                                          int width,
                                          int height,
                                          int channels,
                                          uint16_t per_pixel_tolerance)
{
    return compare_frames_impl(reference, actual, width, height, channels,
                               per_pixel_tolerance, 65535.0);
}

frame_compare_result_t compare_frames_u8(const uint8_t * reference,
                                         const uint8_t * actual,
                                         int width,
                                         int height,
                                         int channels,
                                         uint8_t per_pixel_tolerance)
{
    return compare_frames_impl(reference, actual, width, height, channels,
                               per_pixel_tolerance, 255.0);
}

std::string frame_compare_summary(const frame_compare_result_t & result)
{
    std::ostringstream stream;
    stream.setf(std::ios::fixed);
    stream.precision(6);
    if (std::isinf(result.psnr_db)) {
        stream << "psnr=inf";
    } else {
        stream << "psnr=" << result.psnr_db;
    }
    stream << ";max=" << result.max_abs_diff
           << ";mean=" << result.mean_abs_diff
           << ";exceeding=" << result.pixels_exceeding_tolerance;
    return stream.str();
}
