#ifndef PHASE3STAGETELEMETRY_H
#define PHASE3STAGETELEMETRY_H

#include "Phase3Mode.h"

#include <QString>

#include <cstdint>
#include <vector>

namespace Phase3StageTelemetry {

struct Event
{
    uint64_t frameIndex = 0;
    uint64_t requestSerial = 0;
    int slot = -1;
    const char * stage = "";
    const char * event = "";
    uint64_t ns = 0;
    Phase3Mode mode = Phase3Mode::Disabled;
    uint64_t clipGeneration = 0;
};

const char * csvHeader() noexcept;
uint64_t monotonicNs() noexcept;
bool writeCsv(const QString & path,
              const std::vector<Event> & events,
              QString * errorMessage = nullptr);

} // namespace Phase3StageTelemetry

#endif // PHASE3STAGETELEMETRY_H
