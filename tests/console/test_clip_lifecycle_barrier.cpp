#include "../common/minitest.h"
#include "../../platform/qt/ClipLifecycleBarrier.h"
#include "../../platform/qt/PlaybackPrepPresentationPolicy.h"

#include <chrono>
#include <future>
#include <thread>

TEST(ClipLifecycleBarrier, MutationWaitsForBorrowerRelease)
{
    ClipLifecycleBarrier barrier;
    const uint64_t generation = barrier.generation();
    auto borrower = barrier.tryBorrow( generation );
    ASSERT_TRUE( borrower );
    ASSERT_EQ( 1, barrier.borrowerCount() );

    const uint64_t mutationGeneration = barrier.beginMutation();
    ASSERT_FALSE( barrier.acceptingRequests() );
    ASSERT_NE( generation, mutationGeneration );

    auto waiter = std::async( std::launch::async, [&barrier]() {
        barrier.waitForNoBorrowers();
        return true;
    } );

    ASSERT_TRUE( waiter.wait_for( std::chrono::milliseconds( 25 ) )
                 == std::future_status::timeout );

    borrower.release();

    ASSERT_TRUE( waiter.wait_for( std::chrono::seconds( 1 ) )
                 == std::future_status::ready );
    ASSERT_TRUE( waiter.get() );
    ASSERT_EQ( 0, barrier.borrowerCount() );
}

TEST(ClipLifecycleBarrier, MutationRejectsNewAndStaleBorrowersUntilReopened)
{
    ClipLifecycleBarrier barrier;
    const uint64_t originalGeneration = barrier.generation();

    const uint64_t mutationGeneration = barrier.beginMutation();
    ASSERT_FALSE( barrier.acceptingRequests() );
    ASSERT_FALSE( barrier.tryBorrow( originalGeneration ) );
    ASSERT_FALSE( barrier.tryBorrow( mutationGeneration ) );

    barrier.reopen();
    ASSERT_TRUE( barrier.acceptingRequests() );
    ASSERT_FALSE( barrier.tryBorrow( originalGeneration ) );

    auto borrower = barrier.tryBorrow( mutationGeneration );
    ASSERT_TRUE( borrower );
    ASSERT_EQ( mutationGeneration, borrower.generation() );
}

TEST(PlaybackPrepPresentationPolicy, SupersededQueuedTaskSkipsCompute)
{
    ASSERT_FALSE( playbackPrepTaskShouldCompute( 41, 42, 7, 7 ) );
    ASSERT_FALSE( playbackPrepTaskShouldCompute( 42, 42, 7, 8 ) );
    ASSERT_TRUE( playbackPrepTaskShouldCompute( 42, 42, 7, 7 ) );
}

TEST(PlaybackPrepPresentationPolicy, CompletedCurrentGenerationFrameCannotStarveOnNewerSerial)
{
    ASSERT_TRUE( playbackPrepCompletedResultShouldPresent( 7, 7 ) );
    ASSERT_FALSE( playbackPrepCompletedResultShouldPresent( 7, 8 ) );
}
