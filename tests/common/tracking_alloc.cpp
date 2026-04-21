#include "tracking_alloc.h"

#include <cstdlib>
#include <mutex>
#include <unordered_map>

namespace tracking_alloc {

static std::mutex g_mutex;
static std::unordered_map<void *, std::size_t> g_sizes;
static Stats g_stats = {0, 0, 0, 0, 0, 0};

void reset()
{
    std::lock_guard<std::mutex> lock(g_mutex);
    g_sizes.clear();
    g_stats = {0, 0, 0, 0, 0, 0};
}

void * allocate(std::size_t size)
{
    void * pointer = std::malloc(size);
    if (!pointer) {
        return nullptr;
    }

    std::lock_guard<std::mutex> lock(g_mutex);
    g_sizes[pointer] = size;
    g_stats.total_allocations += 1;
    g_stats.total_bytes += size;
    g_stats.outstanding_allocations += 1;
    g_stats.outstanding_bytes += size;
    if (g_stats.outstanding_bytes > g_stats.peak_bytes) {
        g_stats.peak_bytes = g_stats.outstanding_bytes;
    }
    return pointer;
}

void deallocate(void * pointer)
{
    if (!pointer) {
        return;
    }

    std::lock_guard<std::mutex> lock(g_mutex);
    const auto found = g_sizes.find(pointer);
    if (found != g_sizes.end()) {
        g_stats.total_frees += 1;
        g_stats.outstanding_allocations -= 1;
        g_stats.outstanding_bytes -= found->second;
        g_sizes.erase(found);
    }
    std::free(pointer);
}

Stats stats()
{
    std::lock_guard<std::mutex> lock(g_mutex);
    return g_stats;
}

} // namespace tracking_alloc
