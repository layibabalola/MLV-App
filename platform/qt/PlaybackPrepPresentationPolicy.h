#ifndef PLAYBACKPREPPRESENTATIONPOLICY_H
#define PLAYBACKPREPPRESENTATIONPOLICY_H

#include <cstdint>

inline bool playbackPrepTaskShouldCompute( uint64_t taskSerial,
                                           uint64_t latestSerial,
                                           uint64_t taskGeneration,
                                           uint64_t activeGeneration )
{
    return taskSerial == latestSerial && taskGeneration == activeGeneration;
}

inline bool playbackPrepCompletedResultShouldPresent( uint64_t taskGeneration,
                                                       uint64_t activeGeneration )
{
    // A newer serial may arrive while expensive prep is running. The finished
    // frame is still safe to present when its display generation is current;
    // rejecting it by serial can starve continuous playback indefinitely.
    return taskGeneration == activeGeneration;
}

#endif
