#ifndef MLV_APP_TRACKING_ALLOC_H
#define MLV_APP_TRACKING_ALLOC_H

#include <cstddef>

namespace tracking_alloc {

struct Stats {
    std::size_t total_allocations;
    std::size_t total_frees;
    std::size_t total_bytes;
    std::size_t outstanding_allocations;
    std::size_t outstanding_bytes;
    std::size_t peak_bytes;
};

void reset();
void * allocate(std::size_t size);
void deallocate(void * pointer);
Stats stats();

} // namespace tracking_alloc

#define TRACKING_MALLOC(SIZE) ::tracking_alloc::allocate((SIZE))
#define TRACKING_FREE(PTR) ::tracking_alloc::deallocate((PTR))

#endif
