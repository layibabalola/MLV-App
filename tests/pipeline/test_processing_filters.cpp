#include "../common/minitest.h"
#include "../common/hash_helpers.h"

#include "../../src/processing/denoiser/denoiser_2d_median.h"
#include "../../src/processing/rbfilter/rbf_wrapper.h"
#include "../../src/processing/sobel/sobel.h"

#include <cstddef>
#include <cstdint>

extern "C" {
#include "../../src/mlv/llrawproc/pixelproc.h"
#include "../../src/mlv/llrawproc/patternnoise.h"
#include "../../src/processing/raw_processing.h"
}

#include <algorithm>
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

void unset_env_for_test(const char * name)
{
#if defined(_WIN32)
    _putenv_s(name, "");
#else
    unsetenv(name);
#endif
}

void set_env_for_test(const char * name, const char * value)
{
#if defined(_WIN32)
    _putenv_s(name, value);
#else
    setenv(name, value, 1);
#endif
}

class ProcessingPlaybackPreviewModeScope
{
public:
    ProcessingPlaybackPreviewModeScope()
        : preview_(processingPlaybackPreviewModeEnabled())
        , aggressive_(processingPlaybackAggressivePreviewModeEnabled())
        , scale_factor_(processingPlaybackPreviewScaleFactor())
    {
    }

    ~ProcessingPlaybackPreviewModeScope()
    {
        processingSetPlaybackPreviewScaleFactor(scale_factor_);
        processingSetPlaybackAggressivePreviewMode(aggressive_);
        processingSetPlaybackPreviewMode(preview_);
    }

private:
    int preview_;
    int aggressive_;
    int scale_factor_;
};

uint16_t limit_u16_from_i32(std::int32_t value)
{
    if( value < 0 ) return 0;
    if( value > 65535 ) return 65535;
    return static_cast<uint16_t>(value);
}

int coerce_int(int value, int low, int high)
{
    return std::max(std::min(value, high), low);
}

int median5_copy(int values[5])
{
    int copy[5] = { values[0], values[1], values[2], values[3], values[4] };
    std::sort(copy, copy + 5);
    return copy[2];
}

void chroma_smooth_2x2_reference(std::vector<uint16_t> & image,
                                 int width,
                                 int height,
                                 int black,
                                 int white,
                                 int * raw2ev,
                                 int * ev2raw)
{
    constexpr int ev_resolution = 65536;
    const std::vector<uint16_t> input = image;

    for( int y = 4; y < height - 5; y += 2 )
    {
        for( int x = 4; x < width - 4; x += 2 )
        {
            int med_r[5];
            int med_b[5];
            int eh = 0;
            int ev = 0;

            const int sample_i[5] = { -2, 0, 0, 0, 2 };
            const int sample_j[5] = {  0, -2, 0, 2, 0 };

            for( int k = 0; k < 5; ++k )
            {
                const int base = (x + sample_i[k]) + (y + sample_j[k]) * width;
                const int r = input[base];
                const int b = input[base + 1 + width];
                const int g1 = raw2ev[input[base + 1]];
                const int g2 = raw2ev[input[base + width]];
                const int g3 = raw2ev[input[base - 1]];
                const int g5 = raw2ev[input[base + 2 + width]];
                const int gr = (g1 + g3) / 2;
                const int gb = (g2 + g5) / 2;
                eh += std::abs(g1 - g3) + std::abs(g2 - g5);
                med_r[k] = raw2ev[r] - gr;
                med_b[k] = raw2ev[b] - gb;
            }

            const int drh = median5_copy(med_r);
            const int dbh = median5_copy(med_b);

            for( int k = 0; k < 5; ++k )
            {
                const int base = (x + sample_i[k]) + (y + sample_j[k]) * width;
                const int r = input[base];
                const int b = input[base + 1 + width];
                const int g1 = raw2ev[input[base + 1]];
                const int g2 = raw2ev[input[base + width]];
                const int g4 = raw2ev[input[base - width]];
                const int g6 = raw2ev[input[base + 1 + 2 * width]];
                const int gr = (g2 + g4) / 2;
                const int gb = (g1 + g6) / 2;
                ev += std::abs(g2 - g4) + std::abs(g1 - g6);
                med_r[k] = raw2ev[r] - gr;
                med_b[k] = raw2ev[b] - gb;
            }

            const int drv = median5_copy(med_r);
            const int dbv = median5_copy(med_b);

            const int g1 = raw2ev[input[x + 1 + y * width]];
            const int g2 = raw2ev[input[x + (y + 1) * width]];
            const int g3 = raw2ev[input[x - 1 + y * width]];
            const int g4 = raw2ev[input[x + (y - 1) * width]];
            const int g5 = raw2ev[input[x + 2 + (y + 1) * width]];
            const int g6 = raw2ev[input[x + 1 + (y + 2) * width]];

            const int grv = (g2 + g4) / 2;
            const int grh = (g1 + g3) / 2;
            const int gbv = (g1 + g6) / 2;
            const int gbh = (g2 + g5) / 2;
            int gr = ev < eh ? grv : grh;
            int gb = ev < eh ? gbv : gbh;
            int dr = ev < eh ? drv : drh;
            int db = ev < eh ? dbv : dbh;

            const int r0 = input[x + y * width];
            const int b0 = input[x + 1 + (y + 1) * width];
            const int threshold = 64;
            if( r0 < black + threshold
             || b0 < black + threshold
             || std::abs(drv - drh) < threshold
             || std::abs(grv - grh) < threshold
             || std::abs(gbv - gbh) < threshold )
            {
                dr = (drv + drh) / 2;
                db = (dbv + dbh) / 2;
                gr = (g1 + g2 + g3 + g4) / 4;
                gb = (g1 + g2 + g5 + g6) / 4;
            }

            if( r0 < white )
            {
                image[x + y * width] =
                    static_cast<uint16_t>(ev2raw[coerce_int(gr + dr,
                                                            -10 * ev_resolution,
                                                            14 * ev_resolution - 1)]);
            }
            if( b0 < white )
            {
                image[x + 1 + (y + 1) * width] =
                    static_cast<uint16_t>(ev2raw[coerce_int(gb + db,
                                                            -10 * ev_resolution,
                                                            14 * ev_resolution - 1)]);
            }
        }
    }
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

TEST(ProcessingFilters, RbfFilterOutputLutMatchesSeparateLevelsPass)
{
    const int width = 22;
    const int height = 18;
    const float sigma_spatial = 0.0005f;
    const float sigma_range = 0.165f;

    std::vector<uint16_t> input = make_rgb_pattern(width, height);
    std::vector<uint16_t> expected_output(input.size(), 0);
    std::vector<uint16_t> actual_output(input.size(), 0);
    std::vector<uint16_t> levels_lut(65536);
    for( std::size_t i = 0; i < levels_lut.size(); ++i )
    {
        const uint32_t value = static_cast<uint32_t>(i);
        levels_lut[i] = static_cast<uint16_t>((value * 257u + (value >> 3) + 19u) & 0xffffu);
    }

    recursive_bf_wrap(input.data(),
                      expected_output.data(),
                      sigma_spatial,
                      sigma_range,
                      width,
                      height,
                      3);
    for( uint16_t & pixel : expected_output )
    {
        pixel = levels_lut[pixel];
    }

    recursive_bf_wrap_with_output_lut(input.data(),
                                      actual_output.data(),
                                      sigma_spatial,
                                      sigma_range,
                                      width,
                                      height,
                                      3,
                                      levels_lut.data());

    ASSERT_EQ(hash_image(expected_output), hash_image(actual_output));
}

TEST(ProcessingFilters, RbfFilterCurveIndexOutputMatchesSeparateLevelsAndMatrixPass)
{
    const int width = 22;
    const int height = 18;
    const float sigma_spatial = 0.0005f;
    const float sigma_range = 0.165f;

    std::vector<uint16_t> input = make_rgb_pattern(width, height);
    std::vector<uint16_t> filtered_rgb(input.size(), 0);
    std::vector<uint16_t> expected_output(input.size(), 0);
    std::vector<uint16_t> actual_output(input.size(), 0);
    std::vector<uint16_t> levels_lut(65536);
    std::vector<std::int32_t> curve_r(65536);
    std::vector<std::int32_t> curve_g(65536);
    std::vector<std::int32_t> curve_b(65536);
    for( std::size_t i = 0; i < levels_lut.size(); ++i )
    {
        const uint32_t value = static_cast<uint32_t>(i);
        levels_lut[i] = static_cast<uint16_t>((value * 257u + (value >> 3) + 19u) & 0xffffu);
        curve_r[i] = static_cast<std::int32_t>(i) - 5000;
        curve_g[i] = static_cast<std::int32_t>((i * 3u) / 4u);
        curve_b[i] = 8000 - static_cast<std::int32_t>(i / 2u);
    }

    recursive_bf_wrap_with_output_lut(input.data(),
                                      filtered_rgb.data(),
                                      sigma_spatial,
                                      sigma_range,
                                      width,
                                      height,
                                      3,
                                      levels_lut.data());
    for( std::size_t index = 0; index < filtered_rgb.size(); index += 3 )
    {
        const std::int32_t curve_index =
            ((curve_r[filtered_rgb[index + 0]] << 2)
           + (curve_g[filtered_rgb[index + 1]] * 11)
           +  curve_b[filtered_rgb[index + 2]]) >> 4;
        const uint16_t mask = limit_u16_from_i32(curve_index);
        expected_output[index + 0] = mask;
        expected_output[index + 1] = mask;
        expected_output[index + 2] = mask;
    }

    recursive_bf_wrap_with_curve_index_lut(input.data(),
                                           actual_output.data(),
                                           sigma_spatial,
                                           sigma_range,
                                           width,
                                           height,
                                           3,
                                           levels_lut.data(),
                                           curve_r.data(),
                                           curve_g.data(),
                                           curve_b.data());

    ASSERT_EQ(hash_image(expected_output), hash_image(actual_output));
}

TEST(ProcessingFilters, ShadowsHighlightsProbeTelemetryIsOptInByDefault)
{
    unset_env_for_test("MLVAPP_SHADOWS_HIGHLIGHTS_PROBE");

    const int width = 24;
    const int height = 20;
    std::vector<uint16_t> input = make_rgb_pattern(width, height);
    std::vector<uint16_t> output(input.size(), 0);

    processingObject_t * processing = initProcessingObject();
    ASSERT_TRUE(processing != nullptr);
    processingSetShadows(processing, 0.30);
    processingSetHighlights(processing, -0.20);

    applyProcessingObject(processing,
                          width,
                          height,
                          input.data(),
                          output.data(),
                          2,
                          1,
                          0);

    ASSERT_TRUE(processingGetLastShadowsHighlightsPrepMilliseconds() > 0.0);
    ASSERT_TRUE(processingGetLastShadowsHighlightsFilterMilliseconds() > 0.0);
    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterHalfresDownsampleMilliseconds());
    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterHalfresRbfMilliseconds());
    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterHalfresUpsampleMilliseconds());
    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterQuarterresDownsampleMilliseconds());
    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterQuarterresRbfMilliseconds());
    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterQuarterresUpsampleMilliseconds());

    freeProcessingObject(processing);
}

TEST(ProcessingFilters, SharpOddHeightPlaybackPreviewKeepsFullresShadowsHighlights)
{
    set_env_for_test("MLVAPP_SHADOWS_HIGHLIGHTS_PROBE", "0");
    processingResetShadowsHighlightsProbeModeCacheForTesting();
    ProcessingPlaybackPreviewModeScope playback_scope;
    processingSetPlaybackPreviewMode(1);
    processingSetPlaybackAggressivePreviewMode(0);
    processingSetPlaybackPreviewScaleFactor(8);

    const int width = 48;
    const int height = 35;
    std::vector<uint16_t> input = make_rgb_pattern(width, height);
    std::vector<uint16_t> output(input.size(), 0);

    processingObject_t * processing = initProcessingObject();
    ASSERT_TRUE(processing != nullptr);
    processingSetShadows(processing, 0.30);
    processingSetHighlights(processing, -0.20);

    applyProcessingObject(processing,
                          width,
                          height,
                          input.data(),
                          output.data(),
                          2,
                          1,
                          0);

    ASSERT_TRUE(processingGetLastShadowsHighlightsFilterFullresMilliseconds() > 0.0);
    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterHalfresDownsampleMilliseconds());
    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterHalfresRbfMilliseconds());
    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterHalfresUpsampleMilliseconds());
    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterQuarterresDownsampleMilliseconds());
    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterQuarterresRbfMilliseconds());
    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterQuarterresUpsampleMilliseconds());

    freeProcessingObject(processing);
    unset_env_for_test("MLVAPP_SHADOWS_HIGHLIGHTS_PROBE");
    processingResetShadowsHighlightsProbeModeCacheForTesting();
}

TEST(ProcessingFilters, AggressiveOddHeightPlaybackPreviewUsesHalfresShadowsHighlights)
{
    set_env_for_test("MLVAPP_SHADOWS_HIGHLIGHTS_PROBE", "0");
    processingResetShadowsHighlightsProbeModeCacheForTesting();
    ProcessingPlaybackPreviewModeScope playback_scope;
    processingSetPlaybackPreviewMode(1);
    processingSetPlaybackAggressivePreviewMode(1);
    processingSetPlaybackPreviewScaleFactor(4);

    const int width = 48;
    const int height = 35;
    std::vector<uint16_t> input = make_rgb_pattern(width, height);
    std::vector<uint16_t> output(input.size(), 0);

    processingObject_t * processing = initProcessingObject();
    ASSERT_TRUE(processing != nullptr);
    processingSetShadows(processing, 0.30);
    processingSetHighlights(processing, -0.20);

    applyProcessingObject(processing,
                          width,
                          height,
                          input.data(),
                          output.data(),
                          2,
                          1,
                          0);

    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterFullresMilliseconds());
    ASSERT_TRUE(processingGetLastShadowsHighlightsFilterHalfresRbfMilliseconds() > 0.0);
    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterQuarterresRbfMilliseconds());
    ASSERT_TRUE(processingGetLastShadowsHighlightsFilterMilliseconds() > 0.0);

    freeProcessingObject(processing);
    unset_env_for_test("MLVAPP_SHADOWS_HIGHLIGHTS_PROBE");
    processingResetShadowsHighlightsProbeModeCacheForTesting();
}

TEST(ProcessingFilters, AggressiveX8PlaybackPreviewUsesQuarterresShadowsHighlights)
{
    set_env_for_test("MLVAPP_SHADOWS_HIGHLIGHTS_PROBE", "0");
    processingResetShadowsHighlightsProbeModeCacheForTesting();
    ProcessingPlaybackPreviewModeScope playback_scope;
    processingSetPlaybackPreviewMode(1);
    processingSetPlaybackAggressivePreviewMode(1);
    processingSetPlaybackPreviewScaleFactor(8);

    const int width = 226;
    const int height = 283;
    std::vector<uint16_t> input = make_rgb_pattern(width, height);
    std::vector<uint16_t> output(input.size(), 0);

    processingObject_t * processing = initProcessingObject();
    ASSERT_TRUE(processing != nullptr);
    processingSetShadows(processing, 0.30);
    processingSetHighlights(processing, -0.20);

    applyProcessingObject(processing,
                          width,
                          height,
                          input.data(),
                          output.data(),
                          2,
                          1,
                          0);

    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterFullresMilliseconds());
    ASSERT_EQ(0.0, processingGetLastShadowsHighlightsFilterHalfresRbfMilliseconds());
    ASSERT_TRUE(processingGetLastShadowsHighlightsFilterQuarterresRbfMilliseconds() > 0.0);
    ASSERT_TRUE(processingGetLastShadowsHighlightsFilterMilliseconds() > 0.0);

    freeProcessingObject(processing);
    unset_env_for_test("MLVAPP_SHADOWS_HIGHLIGHTS_PROBE");
    processingResetShadowsHighlightsProbeModeCacheForTesting();
}

TEST(ProcessingFilters, ChromaSmooth2x2MatchesScalarReference)
{
    const int width = 34;
    const int height = 30;
    const int black = 256;
    const int white = 15000;

    std::vector<uint16_t> expected = make_bayer_pattern(width, height);
    std::vector<uint16_t> actual = expected;

    int * raw2ev = get_raw2ev(black);
    int * ev2raw = get_ev2raw(black);
    ASSERT_TRUE(raw2ev != nullptr);
    ASSERT_TRUE(ev2raw != nullptr);

    chroma_smooth_scratch_t scratch = { nullptr, 0 };
    chroma_smooth_2x2_reference(expected, width, height, black, white, raw2ev, ev2raw);
    chroma_smooth(2, actual.data(), width, height, black, white, raw2ev, ev2raw, &scratch);

    ASSERT_EQ(hash_image(expected), hash_image(actual));

    std::free(scratch.buffer);
    free_luts(raw2ev, ev2raw);
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
