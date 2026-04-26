#ifndef PHASE3CHECKSUMS_H
#define PHASE3CHECKSUMS_H

#include "Phase3Mode.h"

#include <QString>

#include <cstddef>
#include <cstdint>
#include <vector>

namespace Phase3Checksums {

struct Record
{
    uint64_t frameIndex = 0;
    uint64_t requestSerial = 0;
    Phase3Mode mode = Phase3Mode::Disabled;
    int scaleFactor = 0;
    int width = 0;
    int height = 0;
    uint64_t hash = 0;
};

uint64_t xxhash64(const void * data, std::size_t size, uint64_t seed = 0) noexcept;
const char * csvHeader() noexcept;
bool writeCsv(const QString & path,
              const std::vector<Record> & records,
              QString * errorMessage = nullptr);

} // namespace Phase3Checksums

#endif // PHASE3CHECKSUMS_H
