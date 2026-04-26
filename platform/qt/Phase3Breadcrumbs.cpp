#include "Phase3Breadcrumbs.h"

#include <algorithm>
#include <atomic>
#include <cstring>

#ifdef Q_OS_WIN
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <windows.h>
#endif

namespace Phase3Breadcrumbs {

namespace {

constexpr uint64_t kRingSize = 256;
static std::atomic_flag g_ringLock = ATOMIC_FLAG_INIT;
static std::atomic<uint64_t> g_nextSequence{0};
struct RingEntry
{
    uint64_t sequence = 0;
    Breadcrumb crumb = {};
};
static RingEntry g_ring[kRingSize] = {};

void lockRing() noexcept
{
    while (g_ringLock.test_and_set(std::memory_order_acquire))
    {
    }
}

bool tryLockRing() noexcept
{
    return !g_ringLock.test_and_set(std::memory_order_acquire);
}

void unlockRing() noexcept
{
    g_ringLock.clear(std::memory_order_release);
}

void copyContext(char (&dest)[44], const char * context) noexcept
{
    std::memset(dest, 0, sizeof(dest));
    if (!context) return;
    for (size_t i = 0; i + 1u < sizeof(dest) && context[i] != '\0'; ++i)
    {
        dest[i] = context[i];
    }
    dest[sizeof(dest) - 1u] = '\0';
}

std::vector<Breadcrumb> snapshotLocked()
{
    std::vector<Breadcrumb> values;
    values.reserve(kRingSize);
    for (const RingEntry & entry : g_ring)
    {
        if (entry.sequence != 0)
        {
            values.push_back(entry.crumb);
        }
    }
    std::sort(values.begin(), values.end(),
              [](const Breadcrumb & a, const Breadcrumb & b) {
                  if (a.timestamp_ns != b.timestamp_ns) return a.timestamp_ns < b.timestamp_ns;
                  if (a.frame_idx != b.frame_idx) return a.frame_idx < b.frame_idx;
                  return a.request_serial < b.request_serial;
              });
    return values;
}

std::vector<Breadcrumb> snapshot()
{
    lockRing();
    std::vector<Breadcrumb> values = snapshotLocked();
    unlockRing();
    return values;
}

} // namespace

void push(uint8_t slotIndex,
          uint8_t fromState,
          uint8_t toState,
          uint64_t timestampNs,
          uint32_t frameIdx,
          uint32_t requestSerial,
          uint8_t phase3Mode,
          const char * context) noexcept
{
    const uint64_t sequence =
        g_nextSequence.fetch_add(1, std::memory_order_relaxed) + 1;
    Breadcrumb crumb = {};
    crumb.timestamp_ns = timestampNs;
    crumb.frame_idx = frameIdx;
    crumb.request_serial = requestSerial;
    crumb.slot_index = slotIndex;
    crumb.from_state = fromState;
    crumb.to_state = toState;
    crumb.phase3_mode = phase3Mode;
    copyContext(crumb.context, context);
    lockRing();
    RingEntry & entry = g_ring[(sequence - 1) % kRingSize];
    entry.crumb = crumb;
    entry.sequence = sequence;
    unlockRing();
}

void push(uint8_t slotIndex,
          uint8_t fromState,
          uint8_t toState,
          uint64_t timestampNs,
          const char * context) noexcept
{
    push(slotIndex, fromState, toState, timestampNs, 0, 0, 0, context);
}

void dumpToFile(FILE * file) noexcept
{
    if (!file) return;
    std::fprintf(file, "=== Phase 3 Breadcrumbs (last 256) ===\n");
    lockRing();
    const uint64_t lastSequence = g_nextSequence.load(std::memory_order_acquire);
    const uint64_t firstSequence =
        lastSequence > kRingSize ? lastSequence - kRingSize + 1u : 1u;
    for (uint64_t sequence = firstSequence; sequence <= lastSequence; ++sequence)
    {
        const RingEntry & entry = g_ring[(sequence - 1u) % kRingSize];
        if (entry.sequence != sequence) continue;
        const Breadcrumb & crumb = entry.crumb;
        std::fprintf(file,
                     "ns=%llu frame=%u request=%u slot=%u from=%u to=%u mode=%u context=%s\n",
                     static_cast<unsigned long long>(crumb.timestamp_ns),
                     static_cast<unsigned int>(crumb.frame_idx),
                     static_cast<unsigned int>(crumb.request_serial),
                     static_cast<unsigned int>(crumb.slot_index),
                     static_cast<unsigned int>(crumb.from_state),
                     static_cast<unsigned int>(crumb.to_state),
                     static_cast<unsigned int>(crumb.phase3_mode),
                     crumb.context);
    }
    unlockRing();
    std::fflush(file);
}

void resetForTest() noexcept
{
    lockRing();
    g_nextSequence.store(0, std::memory_order_relaxed);
    for (RingEntry & entry : g_ring)
    {
        entry = RingEntry();
    }
    unlockRing();
}

std::vector<Breadcrumb> getBreadcrumbsForTest()
{
    return snapshot();
}

#ifdef Q_OS_WIN
static void writeLine(HANDLE h, const char * line) noexcept
{
    DWORD written = 0;
    WriteFile(h, line, static_cast<DWORD>(std::strlen(line)), &written, nullptr);
}

void dumpToWindowsLogFile(const wchar_t * logPath) noexcept
{
    if (!logPath) return;
    HANDLE h = CreateFileW(logPath,
                           FILE_APPEND_DATA,
                           FILE_SHARE_READ,
                           nullptr,
                           OPEN_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL,
                           nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    writeLine(h, "=== Phase 3 Breadcrumbs (last 256) ===\n");
    if (!tryLockRing())
    {
        writeLine(h, "breadcrumb dump unavailable: ring write in progress\n");
        FlushFileBuffers(h);
        CloseHandle(h);
        return;
    }
    char line[256];
    const uint64_t lastSequence = g_nextSequence.load(std::memory_order_acquire);
    const uint64_t firstSequence =
        lastSequence > kRingSize ? lastSequence - kRingSize + 1u : 1u;
    for (uint64_t sequence = firstSequence; sequence <= lastSequence; ++sequence)
    {
        const RingEntry & entry = g_ring[(sequence - 1u) % kRingSize];
        if (entry.sequence != sequence) continue;
        const Breadcrumb & crumb = entry.crumb;
        std::snprintf(line,
                      sizeof(line),
                      "ns=%llu frame=%u request=%u slot=%u from=%u to=%u mode=%u context=%s\n",
                      static_cast<unsigned long long>(crumb.timestamp_ns),
                      static_cast<unsigned int>(crumb.frame_idx),
                      static_cast<unsigned int>(crumb.request_serial),
                      static_cast<unsigned int>(crumb.slot_index),
                      static_cast<unsigned int>(crumb.from_state),
                      static_cast<unsigned int>(crumb.to_state),
                      static_cast<unsigned int>(crumb.phase3_mode),
                      crumb.context);
        writeLine(h, line);
    }
    unlockRing();
    FlushFileBuffers(h);
    CloseHandle(h);
}
#endif

} // namespace Phase3Breadcrumbs
