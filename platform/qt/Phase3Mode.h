#ifndef PHASE3MODE_H
#define PHASE3MODE_H

#include <cstdint>

enum class Phase3Mode : uint8_t
{
    Disabled = 0,
    DecodeAheadOnly = 1,
    DecodeRecon = 2,
    DecodeReconProcess = 3,
    Full = 4
};

const char * phase3ModeName(Phase3Mode mode) noexcept;

void phase3InitKillSwitches() noexcept;
void phase3ReloadKillSwitchesForTest() noexcept;
bool phase3KillSwitchActive(Phase3Mode mode) noexcept;
void phase3SetLiveFallbackActive(bool active) noexcept;
bool phase3LiveFallbackActive(void) noexcept;

#endif // PHASE3MODE_H
