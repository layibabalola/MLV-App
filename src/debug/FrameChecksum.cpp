#include "FrameChecksum.h"

#include <QByteArray>
#include <QtGlobal>

#include <algorithm>
#include <atomic>
#include <mutex>

namespace {

constexpr uint64_t PRIME64_1 = 11400714785074694791ull;
constexpr uint64_t PRIME64_2 = 14029467366897019727ull;
constexpr uint64_t PRIME64_3 = 1609587929392839161ull;
constexpr uint64_t PRIME64_4 = 9650029242287828579ull;
constexpr uint64_t PRIME64_5 = 2870177450012600261ull;
constexpr uint32_t kChecksumRingSize = 256;

struct ChecksumRecord
{
    uint32_t frameIdx = 0;
    uint64_t checksum = 0;
    uint64_t sequence = 0;
};

std::mutex g_checksumMutex;
ChecksumRecord g_checksumRing[kChecksumRingSize] = {};
uint64_t g_checksumSequence = 0;
std::atomic<int> g_enabled{-1};

bool envTruthy(const char * name)
{
    const QByteArray value = qgetenv(name).trimmed();
    if (value.isEmpty()) return false;
    if (value == "0") return false;
    if (value.compare("false", Qt::CaseInsensitive) == 0) return false;
    if (value.compare("off", Qt::CaseInsensitive) == 0) return false;
    if (value.compare("no", Qt::CaseInsensitive) == 0) return false;
    return true;
}

uint64_t read64(const uint8_t * p) noexcept
{
    return static_cast<uint64_t>(p[0])
        | (static_cast<uint64_t>(p[1]) << 8)
        | (static_cast<uint64_t>(p[2]) << 16)
        | (static_cast<uint64_t>(p[3]) << 24)
        | (static_cast<uint64_t>(p[4]) << 32)
        | (static_cast<uint64_t>(p[5]) << 40)
        | (static_cast<uint64_t>(p[6]) << 48)
        | (static_cast<uint64_t>(p[7]) << 56);
}

uint32_t read32(const uint8_t * p) noexcept
{
    return static_cast<uint32_t>(p[0])
        | (static_cast<uint32_t>(p[1]) << 8)
        | (static_cast<uint32_t>(p[2]) << 16)
        | (static_cast<uint32_t>(p[3]) << 24);
}

uint64_t rotl64(uint64_t value, int bits) noexcept
{
    return (value << bits) | (value >> (64 - bits));
}

uint64_t round64(uint64_t acc, uint64_t input) noexcept
{
    acc += input * PRIME64_2;
    acc = rotl64(acc, 31);
    acc *= PRIME64_1;
    return acc;
}

uint64_t mergeRound(uint64_t acc, uint64_t value) noexcept
{
    value = round64(0, value);
    acc ^= value;
    acc = acc * PRIME64_1 + PRIME64_4;
    return acc;
}

uint64_t avalanche(uint64_t hash) noexcept
{
    hash ^= hash >> 33;
    hash *= PRIME64_2;
    hash ^= hash >> 29;
    hash *= PRIME64_3;
    hash ^= hash >> 32;
    return hash;
}

} // namespace

extern "C" uint64_t frame_checksum_compute_seed(const void * buffer,
                                                 size_t size,
                                                 uint64_t seed)
{
    const uint8_t * p = static_cast<const uint8_t *>(buffer);
    const uint8_t * const end = p + size;
    uint64_t hash;

    if (size >= 32)
    {
        const uint8_t * const limit = end - 32;
        uint64_t v1 = seed + PRIME64_1 + PRIME64_2;
        uint64_t v2 = seed + PRIME64_2;
        uint64_t v3 = seed + 0;
        uint64_t v4 = seed - PRIME64_1;

        do
        {
            v1 = round64(v1, read64(p)); p += 8;
            v2 = round64(v2, read64(p)); p += 8;
            v3 = round64(v3, read64(p)); p += 8;
            v4 = round64(v4, read64(p)); p += 8;
        }
        while (p <= limit);

        hash = rotl64(v1, 1)
            + rotl64(v2, 7)
            + rotl64(v3, 12)
            + rotl64(v4, 18);
        hash = mergeRound(hash, v1);
        hash = mergeRound(hash, v2);
        hash = mergeRound(hash, v3);
        hash = mergeRound(hash, v4);
    }
    else
    {
        hash = seed + PRIME64_5;
    }

    hash += size;

    while (p + 8 <= end)
    {
        const uint64_t k1 = round64(0, read64(p));
        hash ^= k1;
        hash = rotl64(hash, 27) * PRIME64_1 + PRIME64_4;
        p += 8;
    }

    if (p + 4 <= end)
    {
        hash ^= static_cast<uint64_t>(read32(p)) * PRIME64_1;
        hash = rotl64(hash, 23) * PRIME64_2 + PRIME64_3;
        p += 4;
    }

    while (p < end)
    {
        hash ^= static_cast<uint64_t>(*p) * PRIME64_5;
        hash = rotl64(hash, 11) * PRIME64_1;
        ++p;
    }

    return avalanche(hash);
}

extern "C" uint64_t frame_checksum_compute(const void * buffer, size_t size)
{
    return frame_checksum_compute_seed(buffer, size, 0);
}

extern "C" void frame_checksum_log_record(uint32_t frame_idx, uint64_t checksum)
{
    std::lock_guard<std::mutex> lock(g_checksumMutex);
    const uint64_t sequence = ++g_checksumSequence;
    ChecksumRecord & record = g_checksumRing[(sequence - 1u) % kChecksumRingSize];
    record.frameIdx = frame_idx;
    record.checksum = checksum;
    record.sequence = sequence;
}

extern "C" uint64_t frame_checksum_log_lookup(uint32_t frame_idx, int * found)
{
    std::lock_guard<std::mutex> lock(g_checksumMutex);
    const uint64_t firstSequence =
        g_checksumSequence > kChecksumRingSize
            ? g_checksumSequence - kChecksumRingSize + 1u
            : 1u;
    for (uint64_t sequence = firstSequence; sequence <= g_checksumSequence; ++sequence)
    {
        const ChecksumRecord & record = g_checksumRing[(sequence - 1u) % kChecksumRingSize];
        if (record.sequence == sequence && record.frameIdx == frame_idx)
        {
            if (found) *found = 1;
            return record.checksum;
        }
    }
    if (found) *found = 0;
    return 0;
}

extern "C" int frame_checksum_verify(uint32_t frame_idx, uint64_t computed)
{
    int found = 0;
    const uint64_t expected = frame_checksum_log_lookup(frame_idx, &found);
    return (found && expected == computed) ? 0 : 1;
}

extern "C" int frame_checksum_enabled(void)
{
    int enabled = g_enabled.load(std::memory_order_acquire);
    if (enabled >= 0) return enabled;
    enabled = envTruthy("MLVAPP_PHASE3_CHECK_FRAME_CHECKSUMS") ? 1 : 0;
    g_enabled.store(enabled, std::memory_order_release);
    return enabled;
}

extern "C" void frame_checksum_reset_for_test(void)
{
    {
        std::lock_guard<std::mutex> lock(g_checksumMutex);
        std::fill(std::begin(g_checksumRing), std::end(g_checksumRing), ChecksumRecord());
        g_checksumSequence = 0;
    }
    g_enabled.store(-1, std::memory_order_release);
}
