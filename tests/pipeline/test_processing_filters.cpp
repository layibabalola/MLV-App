#include "../common/minitest.h"
#include "../common/hash_helpers.h"

#include "../../src/processing/denoiser/denoiser_2d_median.h"
#include "../../src/processing/rbfilter/rbf_wrapper.h"
#include "../../src/processing/sobel/sobel.h"

extern "C" {
#include "../../src/mlv/llrawproc/patternnoise.h"
}

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace {

std::vector<uint16_t> make_rgb_pattern(int width, int height)
{
    std::vector<uint16_t> image(static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 3);
    for( int y = 0; y < height; ++y )
    {
        for( int x = 0; x < width; ++x )
        {
            const std::size_t index = (static_cast<std::size_t>(y) * static_cast<std::size_t>(width) + static_cast<std::size_t>(x)) * 3;
            image[index + 0] = static_cast<uint16_t>((x * 173 + y * 97 + ((x ^ y) * 29)) % 65535);
            image[index + 1] = static_cast<uint16_t>((x * 41 + y * 211 + (x * y * 7)) % 65535);
            image[index + 2] = static_cast<uint16_t>((x * 19 + y * 59 + ((x + y) * 131)) % 65535);
        }
    }
    return image;
}

std::vector<uint16_t> make_bayer_pattern(int width, int height)
{
    std::vector<uint16_t> image(static_cast<std::size_t>(width) * static_cast<std::size_t>(height));
    for( int y = 0; y < height; ++y )
    {
        for( int x = 0; x < width; ++x )
        {
            const std::size_t index = static_cast<std::size_t>(y) * static_cast<std::size_t>(width) + static_cast<std::size_t>(x);
            image[index] = static_cast<uint16_t>((2048 + x * 37 + y * 91 + ((x ^ y) * 13)) % 14000);
        }
    }
    return image;
}

std::string hash_image(const std::vector<uint16_t> & image)
{
    return sha256_bytes(image.data(), image.size() * sizeof(uint16_t));
}

} // namespace

TEST(ProcessingFilters, MedianDenoiserReuseMatchesFreshContext)
{
    const int width = 18;
    const int height = 14;

    std::vector<uint16_t> fresh = make_rgb_pattern(width, height);
    std::vector<uint16_t> reused = fresh;

    denoise_2d_median_context_t * context = nullptr;
    denoise_2D_median_with_context(reused.data(), width, height, 5, 65, &context);
    denoise_2D_median_with_context(fresh.data(), width, height, 5, 65, NULL);

    ASSERT_EQ(hash_image(fresh), hash_image(reused));

    denoise_2D_median_release(&context);
    ASSERT_TRUE(context == nullptr);
}

TEST(ProcessingFilters, RbfFilterReuseMatchesFreshResultAfterResize)
{
    const int small_width = 16;
    const int small_height = 12;
    std::vector<uint16_t> small_input = make_rgb_pattern(small_width, small_height);
    std::vector<uint16_t> expected_output(small_input.size(), 0);
    std::vector<uint16_t> actual_output(small_input.size(), 0);

    recursive_bf_wrap(small_input.data(), expected_output.data(), 0.0025f, 0.09f, small_width, small_height, 3);

    const int large_width = 28;
    const int large_height = 20;
    std::vector<uint16_t> large_input = make_rgb_pattern(large_width, large_height);
    std::vector<uint16_t> large_output(large_input.size(), 0);
    recursive_bf_wrap(large_input.data(), large_output.data(), 0.0025f, 0.09f, large_width, large_height, 3);

    recursive_bf_wrap(small_input.data(), actual_output.data(), 0.0025f, 0.09f, small_width, small_height, 3);

    ASSERT_EQ(hash_image(expected_output), hash_image(actual_output));
}

TEST(ProcessingFilters, RbfFilterReuseStaysStableAfterStateChanges)
{
    const int width = 20;
    const int height = 16;
    const float sigma_spatial = 0.0035f;
    const float sigma_range = 0.11f;

    std::vector<uint16_t> target_input = make_rgb_pattern(width, height);
    std::vector<uint16_t> expected_output(target_input.size(), 0);
    std::vector<uint16_t> first_actual_output(target_input.size(), 0);
    std::vector<uint16_t> second_actual_output(target_input.size(), 0);

    recursive_bf_wrap(target_input.data(),
                      expected_output.data(),
                      sigma_spatial,
                      sigma_range,
                      width,
                      height,
                      3);

    const int warm_width = 32;
    const int warm_height = 24;
    std::vector<uint16_t> warm_input = make_rgb_pattern(warm_width, warm_height);
    std::vector<uint16_t> warm_output(warm_input.size(), 0);
    recursive_bf_wrap(warm_input.data(), warm_output.data(), 0.0020f, 0.07f, warm_width, warm_height, 3);

    recursive_bf_wrap(target_input.data(),
                      first_actual_output.data(),
                      sigma_spatial,
                      sigma_range,
                      width,
                      height,
                      3);

    recursive_bf_wrap(target_input.data(),
                      second_actual_output.data(),
                      sigma_spatial,
                      sigma_range,
                      width,
                      height,
                      3);

    ASSERT_EQ(hash_image(expected_output), hash_image(first_actual_output));
    ASSERT_EQ(hash_image(expected_output), hash_image(second_actual_output));
}

TEST(ProcessingFilters, SobelScratchReuseMatchesFreshResultAfterResize)
{
    const int target_width = 18;
    const int target_height = 14;
    const int warm_width = 30;
    const int warm_height = 22;

    std::vector<uint16_t> target_input = make_rgb_pattern(target_width, target_height);
    std::vector<uint16_t> warm_input = make_rgb_pattern(warm_width, warm_height);

    uint16_t * expected_gray = nullptr;
    uint16_t * expected_h = nullptr;
    uint16_t * expected_v = nullptr;
    uint16_t * expected_contour = nullptr;
    sobelFilter(target_input.data(),
                &expected_gray,
                &expected_h,
                &expected_v,
                &expected_contour,
                target_width,
                target_height);

    const std::size_t warm_pixels = static_cast<std::size_t>(warm_width) * static_cast<std::size_t>(warm_height);
    std::vector<uint16_t> reuse_gray(warm_pixels);
    std::vector<uint16_t> reuse_h(warm_pixels);
    std::vector<uint16_t> reuse_v(warm_pixels);
    std::vector<uint16_t> reuse_contour(warm_pixels);

    sobelFilterInto(warm_input.data(),
                    reuse_gray.data(),
                    reuse_h.data(),
                    reuse_v.data(),
                    reuse_contour.data(),
                    warm_width,
                    warm_height);

    sobelFilterInto(target_input.data(),
                    reuse_gray.data(),
                    reuse_h.data(),
                    reuse_v.data(),
                    reuse_contour.data(),
                    target_width,
                    target_height);

    const std::size_t target_pixels = static_cast<std::size_t>(target_width) * static_cast<std::size_t>(target_height);
    ASSERT_EQ(0, std::memcmp(expected_contour, reuse_contour.data(), target_pixels * sizeof(uint16_t)));

    std::free(expected_gray);
    std::free(expected_h);
    std::free(expected_v);
    std::free(expected_contour);
}

TEST(ProcessingFilters, PatternNoiseScratchReuseMatchesFreshResultAfterResize)
{
    const int target_width = 32;
    const int target_height = 24;
    const int warm_width = 48;
    const int warm_height = 36;
    const int white_level = 15000;

    std::vector<uint16_t> expected_input = make_bayer_pattern(target_width, target_height);
    std::vector<uint16_t> actual_input = expected_input;
    std::vector<uint16_t> warm_input = make_bayer_pattern(warm_width, warm_height);

    pattern_noise_scratch_t fresh_scratch = {};
    fix_pattern_noise(reinterpret_cast<int16_t *>(expected_input.data()),
                      target_width,
                      target_height,
                      white_level,
                      0,
                      &fresh_scratch);
    free_pattern_noise_scratch(&fresh_scratch);

    pattern_noise_scratch_t reused_scratch = {};
    fix_pattern_noise(reinterpret_cast<int16_t *>(warm_input.data()),
                      warm_width,
                      warm_height,
                      white_level,
                      0,
                      &reused_scratch);

    int16_t * full_res_block = reused_scratch.full_res_planes;
    int16_t * half_res_block = reused_scratch.half_res_planes;
    int * int_block = reused_scratch.int_scratch;

    fix_pattern_noise(reinterpret_cast<int16_t *>(actual_input.data()),
                      target_width,
                      target_height,
                      white_level,
                      0,
                      &reused_scratch);

    ASSERT_EQ(hash_image(expected_input), hash_image(actual_input));
    ASSERT_TRUE(reused_scratch.full_res_planes == full_res_block);
    ASSERT_TRUE(reused_scratch.half_res_planes == half_res_block);
    ASSERT_TRUE(reused_scratch.int_scratch == int_block);

    free_pattern_noise_scratch(&reused_scratch);
}
