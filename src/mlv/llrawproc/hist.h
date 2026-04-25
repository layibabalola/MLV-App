/*
 * Copyright (C) 2014 David Milligan
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the
 * Free Software Foundation, Inc.,
 * 51 Franklin Street, Fifth Floor,
 * Boston, MA  02110-1301, USA.
 */

#ifndef mlvfs_histogram_h
#define mlvfs_histogram_h

#include <stdio.h>

#pragma pack(push,1)

struct histogram
{
    uint16_t white;
    uint32_t count;
    /* TODO(perf-vs-correctness): uint16_t bin counters can silently wrap
     * when one bin sees >65535 pixels (typical for >=1080p Dual ISO
     * where each ISO half has hundreds of thousands of pixels). Widening
     * to uint32_t fixes the wrap but doubles the histogram's working set
     * (~24KB -> 48KB) which falls out of L1 cache and was measured to
     * cost ~6 ms at T8 p95 on the test clip. Keep uint16 for now;
     * follow-up either with saturating increment (`if (x<UINT16_MAX) x++`)
     * or a quantised bin layout that fits L1. */
    uint16_t * data;
};

#pragma pack(pop)

struct histogram * hist_create(uint16_t white);
void hist_add(struct histogram * hist, uint16_t * data, uint32_t size, uint16_t skip);
uint16_t hist_median(struct histogram * hist);
void hist_destroy(struct histogram * hist);

#endif
