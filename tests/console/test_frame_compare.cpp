#include "../common/minitest.h"
#include "../common/frame_compare.h"
#include "../common/hash_helpers.h"
#include "../common/test_artifacts.h"

#include <array>
#include <cmath>
#include <cstdint>

TEST(FrameCompare, IdenticalU16FramesAreExact)
{
    const std::array<uint16_t, 4> reference = {0, 1024, 4096, 65535};
    const frame_compare_result_t result = compare_frames_u16(reference.data(),
                                                             reference.data(),
                                                             2, 2, 1, 0);

    ASSERT_EQ(static_cast<uint16_t>(0), result.max_abs_diff);
    ASSERT_EQ(static_cast<std::uint64_t>(0), result.pixels_exceeding_tolerance);
    ASSERT_TRUE(std::isinf(result.psnr_db));

    test_artifacts::record("frame_compare.identical_u16",
                           sha256_string(frame_compare_summary(result)));
}

TEST(FrameCompare, OffByOneDifferenceIsMeasured)
{
    const std::array<uint8_t, 4> reference = {10, 20, 30, 40};
    const std::array<uint8_t, 4> actual = {10, 21, 30, 40};
    const frame_compare_result_t result = compare_frames_u8(reference.data(),
                                                            actual.data(),
                                                            2, 2, 1, 0);

    ASSERT_EQ(static_cast<uint16_t>(1), result.max_abs_diff);
    ASSERT_EQ(static_cast<std::uint64_t>(1), result.pixels_exceeding_tolerance);
    ASSERT_NEAR(0.25, result.mean_abs_diff, 1e-9);

    test_artifacts::record("frame_compare.off_by_one_u8",
                           sha256_string(frame_compare_summary(result)));
}
