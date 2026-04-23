#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

#ifdef __cplusplus
extern "C" {
#endif
#include "../../src/mlv/liblj92/lj92.h"
#ifdef __cplusplus
}
#endif

extern "C" int LLVMFuzzerTestOneInput(const unsigned char * data, unsigned long long size)
{
    if (!data || size == 0 || size > static_cast<unsigned long long>(std::numeric_limits<int>::max())) {
        return 0;
    }

    lj92 decoder = nullptr;
    int width = 0;
    int height = 0;
    int bitdepth = 0;
    int components = 0;

    const int open_result = lj92_open(&decoder,
                                      const_cast<uint8_t *>(reinterpret_cast<const uint8_t *>(data)),
                                      static_cast<int>(size),
                                      &width,
                                      &height,
                                      &bitdepth,
                                      &components);

    if (open_result != LJ92_ERROR_NONE || !decoder) {
        return 0;
    }

    const bool valid_shape = width > 0 && height > 0 && components > 0;
    const std::size_t pixel_count = valid_shape
        ? static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * static_cast<std::size_t>(components)
        : 0u;

    if (pixel_count > 0u && pixel_count <= (16u * 1024u * 1024u)) {
        std::vector<uint16_t> decoded(pixel_count);
        lj92_decode(decoder,
                    decoded.data(),
                    width * components,
                    0,
                    nullptr,
                    0);
    }

    lj92_close(decoder);
    return 0;
}
