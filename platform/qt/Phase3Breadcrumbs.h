#ifndef PHASE3BREADCRUMBS_H
#define PHASE3BREADCRUMBS_H

#include <cstdint>
#include <cstdio>
#include <vector>

#include <QtGlobal>

namespace Phase3Breadcrumbs {

#pragma pack(push, 1)
struct Breadcrumb
{
    uint64_t timestamp_ns;
    uint32_t frame_idx;
    uint64_t request_serial;
    uint8_t slot_index;
    uint8_t from_state;
    uint8_t to_state;
    uint8_t phase3_mode;
    char context[40];
};
#pragma pack(pop)

static_assert(sizeof(Breadcrumb) == 64, "Breadcrumb must stay 64 bytes");

void push(uint8_t slotIndex,
          uint8_t fromState,
          uint8_t toState,
          uint64_t timestampNs,
          uint32_t frameIdx,
          uint64_t requestSerial,
          uint8_t phase3Mode,
          const char * context = nullptr) noexcept;
void push(uint8_t slotIndex,
          uint8_t fromState,
          uint8_t toState,
          uint64_t timestampNs,
          const char * context = nullptr) noexcept;
void dumpToFile(FILE * file) noexcept;
void resetForTest() noexcept;
std::vector<Breadcrumb> getBreadcrumbsForTest();

#ifdef Q_OS_WIN
void dumpToWindowsLogFile(const wchar_t * logPath) noexcept;
#endif

} // namespace Phase3Breadcrumbs

#endif // PHASE3BREADCRUMBS_H
