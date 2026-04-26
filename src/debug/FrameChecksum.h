#ifndef MLVAPP_FRAME_CHECKSUM_H
#define MLVAPP_FRAME_CHECKSUM_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

uint64_t frame_checksum_compute(const void * buffer, size_t size);
uint64_t frame_checksum_compute_seed(const void * buffer, size_t size, uint64_t seed);
void frame_checksum_log_record(uint32_t frame_idx, uint64_t checksum);
uint64_t frame_checksum_log_lookup(uint32_t frame_idx, int * found);
int frame_checksum_verify(uint32_t frame_idx, uint64_t computed);
int frame_checksum_enabled(void);
void frame_checksum_reset_for_test(void);

#ifdef __cplusplus
}
#endif

#endif // MLVAPP_FRAME_CHECKSUM_H
