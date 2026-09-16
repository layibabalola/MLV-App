/*!
 * \file AsyncH2dCounterContract.h
 * \brief Counter record contract for the async host-to-device (H2D) GPU
 *        upload path, and a pure validator over it. PLAY-C2-SUBMIT-2-ACCEPT
 *        consumes this contract to accept a submitted counter record set.
 */

#ifndef ASYNCH2DCOUNTERCONTRACT_H
#define ASYNCH2DCOUNTERCONTRACT_H

#include <cstdint>
#include <set>
#include <string>
#include <vector>

namespace async_h2d_counter_contract {

/*! \brief One async H2D upload counter sample for a single frame id.
 *
 * buildIdentity is the emitting build's identity string; every record in a
 * submitted set must match the caller's expected build identity, so a
 * record from a stale or mixed build is never silently accepted alongside
 * current-build records.
 */
struct AsyncH2dCounterRecord
{
    int frameId = -1;
    int fires = 0;
    std::uint64_t bytes = 0;
    std::string buildIdentity;
};

enum class AsyncH2dCounterVerdict
{
    Valid,
    Empty,
    NegativeFrameId,
    DuplicateFrameId,
    FiresZeroWithBytesPositive,
    BuildIdentityMismatch,
};

/*! \brief Pure validator over an async H2D counter record set.
 *
 * Every rule is checked in a fixed order so a caller gets the first
 * violation deterministically.
 */
class AsyncH2dCounterValidator
{
public:
    static AsyncH2dCounterVerdict evaluate(
        const std::vector<AsyncH2dCounterRecord> &records,
        const std::string &expectedBuildIdentity )
    {
        if( records.empty() ) return AsyncH2dCounterVerdict::Empty;

        std::set<int> seenFrameIds;
        for( const AsyncH2dCounterRecord &record : records )
        {
            if( record.frameId < 0 )
                return AsyncH2dCounterVerdict::NegativeFrameId;
            if( !seenFrameIds.insert( record.frameId ).second )
                return AsyncH2dCounterVerdict::DuplicateFrameId;
            if( record.fires == 0 && record.bytes > 0 )
                return AsyncH2dCounterVerdict::FiresZeroWithBytesPositive;
            if( record.buildIdentity != expectedBuildIdentity )
                return AsyncH2dCounterVerdict::BuildIdentityMismatch;
        }
        return AsyncH2dCounterVerdict::Valid;
    }

    static bool accepts( const std::vector<AsyncH2dCounterRecord> &records,
                          const std::string &expectedBuildIdentity )
    {
        return evaluate( records, expectedBuildIdentity ) == AsyncH2dCounterVerdict::Valid;
    }
};

/*! \brief Injectable source of async H2D counter records.
 *
 * The real async H2D path binds an implementation that reads live CUDA
 * counters; tests bind a FAKE that returns a caller-constructed record set,
 * so AsyncH2dCounterValidator is exercised without any GPU/CUDA dependency.
 */
class AsyncH2dCounterBackend
{
public:
    virtual ~AsyncH2dCounterBackend() = default;
    virtual std::vector<AsyncH2dCounterRecord> collectRecords() const = 0;
};

} // namespace async_h2d_counter_contract

#endif // ASYNCH2DCOUNTERCONTRACT_H
