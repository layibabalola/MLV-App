#ifndef CLIP_LIFECYCLE_BARRIER_H
#define CLIP_LIFECYCLE_BARRIER_H

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <mutex>

class ClipLifecycleBarrier
{
public:
    class Borrower
    {
    public:
        Borrower() = default;

        Borrower( ClipLifecycleBarrier *barrier, uint64_t generation )
            : m_barrier( barrier )
            , m_generation( generation )
        {}

        Borrower( const Borrower & ) = delete;
        Borrower &operator=( const Borrower & ) = delete;

        Borrower( Borrower &&other ) noexcept
            : m_barrier( other.m_barrier )
            , m_generation( other.m_generation )
        {
            other.m_barrier = nullptr;
        }

        Borrower &operator=( Borrower &&other ) noexcept
        {
            if( this != &other )
            {
                release();
                m_barrier = other.m_barrier;
                m_generation = other.m_generation;
                other.m_barrier = nullptr;
            }
            return *this;
        }

        ~Borrower()
        {
            release();
        }

        explicit operator bool() const
        {
            return m_barrier != nullptr;
        }

        uint64_t generation() const
        {
            return m_generation;
        }

        void release()
        {
            if( !m_barrier ) return;
            m_barrier->releaseBorrower();
            m_barrier = nullptr;
        }

    private:
        ClipLifecycleBarrier *m_barrier = nullptr;
        uint64_t m_generation = 0;
    };

    uint64_t generation() const
    {
        return m_generation.load( std::memory_order_acquire );
    }

    bool acceptingRequests() const
    {
        return m_acceptingRequests.load( std::memory_order_acquire );
    }

    Borrower tryBorrow( uint64_t expectedGeneration )
    {
        std::unique_lock<std::mutex> lock( m_mutex );
        if( !m_acceptingRequests.load( std::memory_order_acquire )
         || expectedGeneration != m_generation.load( std::memory_order_acquire ) )
        {
            return Borrower();
        }

        ++m_borrowers;
        return Borrower( this, expectedGeneration );
    }

    uint64_t beginMutation()
    {
        std::lock_guard<std::mutex> lock( m_mutex );
        m_acceptingRequests.store( false, std::memory_order_release );
        const uint64_t generation =
            m_generation.fetch_add( 1, std::memory_order_acq_rel ) + 1;
        m_idleCv.notify_all();
        return generation;
    }

    void reopen()
    {
        std::lock_guard<std::mutex> lock( m_mutex );
        m_acceptingRequests.store( true, std::memory_order_release );
        m_idleCv.notify_all();
    }

    void waitForNoBorrowers()
    {
        std::unique_lock<std::mutex> lock( m_mutex );
        m_idleCv.wait( lock, [this] { return m_borrowers == 0; } );
    }

    template <class Rep, class Period>
    bool waitForNoBorrowersFor( const std::chrono::duration<Rep, Period> &timeout )
    {
        std::unique_lock<std::mutex> lock( m_mutex );
        return m_idleCv.wait_for( lock, timeout, [this] { return m_borrowers == 0; } );
    }

    int borrowerCount() const
    {
        std::lock_guard<std::mutex> lock( m_mutex );
        return m_borrowers;
    }

private:
    void releaseBorrower()
    {
        std::lock_guard<std::mutex> lock( m_mutex );
        if( m_borrowers > 0 )
        {
            --m_borrowers;
        }
        m_idleCv.notify_all();
    }

    std::atomic<uint64_t> m_generation{0};
    std::atomic<bool> m_acceptingRequests{true};
    mutable std::mutex m_mutex;
    std::condition_variable m_idleCv;
    int m_borrowers = 0;
};

#endif
