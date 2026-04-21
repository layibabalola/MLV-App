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

#endif
