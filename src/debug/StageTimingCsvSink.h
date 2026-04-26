#ifndef MLVAPP_STAGE_TIMING_CSV_SINK_H
#define MLVAPP_STAGE_TIMING_CSV_SINK_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

int stage_timing_csv_sink_open(const char * path);
void stage_timing_csv_sink_write_event(uint32_t frame_idx,
                                       uint64_t request_serial,
                                       uint8_t slot,
                                       const char * stage,
                                       const char * event,
                                       uint64_t ns,
                                       uint8_t phase3_mode,
                                       uint32_t clip_generation);
void stage_timing_csv_sink_close(void);
int stage_timing_csv_sink_enabled(void);

#ifdef __cplusplus
}
#endif

#endif // MLVAPP_STAGE_TIMING_CSV_SINK_H
