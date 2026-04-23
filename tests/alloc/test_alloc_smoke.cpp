#include "../common/minitest.h"
#include "../common/tracking_alloc.h"
#include "../common/test_runtime.h"

#include <QCoreApplication>

TEST(TrackingAlloc, CountsAllocationsAndFrees)
{
    tracking_alloc::reset();

    void * first = TRACKING_MALLOC(16);
    void * second = TRACKING_MALLOC(32);
    ASSERT_TRUE(first != nullptr);
    ASSERT_TRUE(second != nullptr);

    const tracking_alloc::Stats after_alloc = tracking_alloc::stats();
    ASSERT_EQ(static_cast<std::size_t>(2), after_alloc.total_allocations);
    ASSERT_EQ(static_cast<std::size_t>(48), after_alloc.total_bytes);
    ASSERT_EQ(static_cast<std::size_t>(2), after_alloc.outstanding_allocations);

    TRACKING_FREE(first);
    TRACKING_FREE(second);

    const tracking_alloc::Stats after_free = tracking_alloc::stats();
    ASSERT_EQ(static_cast<std::size_t>(2), after_free.total_frees);
    ASSERT_EQ(static_cast<std::size_t>(0), after_free.outstanding_allocations);
    ASSERT_EQ(static_cast<std::size_t>(0), after_free.outstanding_bytes);
}

int main(int argc, char ** argv)
{
    test_runtime::force_single_threaded_pipeline();
    QCoreApplication app(argc, argv);
    return minitest::run_all() == 0 ? 0 : 1;
}
