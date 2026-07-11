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

#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "hist.h"

#define MIN(a,b) (((a)<(b))?(a):(b))

/**
 * Initialize a histogram
 */
struct histogram * hist_create(uint16_t white)
{
    struct histogram * hist = (struct histogram *)malloc(sizeof(struct histogram));
    if(hist != NULL)
    {
        hist->bin_capacity = 0;
        hist->data = NULL;
        hist->overflow_data = NULL;
        if(!hist_reset(hist, white)) { free(hist); return NULL; }
    }
    return hist;
}

int hist_reset(struct histogram * hist, uint16_t white)
{
    const uint32_t required = (uint32_t)white + 1;
    if(hist == NULL) return 0;
    if(required > hist->bin_capacity)
    {
        uint16_t * next = (uint16_t *)realloc(hist->data, (size_t)required * sizeof(uint16_t));
        if(next == NULL) return 0;
        hist->data = next;
        hist->bin_capacity = required;
    }
    hist->white = white;
    hist->count = 0;
    memset(hist->data, 0, (size_t)required * sizeof(uint16_t));
    free(hist->overflow_data);
    hist->overflow_data = NULL;
    return 1;
}

/**
 * Add data to a histogram
 */
static __attribute__((cold, noinline)) void hist_allocate_overflow(struct histogram * hist)
{
    const size_t bin_count = (size_t)hist->white + 1;
    hist->overflow_data = (uint32_t *)calloc(bin_count, sizeof(uint32_t));
    if(hist->overflow_data == NULL) abort();
}

void hist_add(struct histogram * hist, uint16_t * data, uint32_t size, uint16_t skip)
{
    const uint32_t step = (uint32_t)skip + 1;
    for(uint32_t i = 0; i < size; i += step)
    {
        const uint16_t bin = MIN(hist->white, data[i]);
        if(__builtin_expect(hist->data[bin] != UINT16_MAX, 1))
        {
            hist->data[bin]++;
        }
        else
        {
            if(hist->overflow_data == NULL) hist_allocate_overflow(hist);
            hist->overflow_data[bin]++;
        }
    }
    hist->count += size / step;
}

/**
 * Compute the median
 */
uint16_t hist_median(struct histogram * hist)
{
    uint32_t middle = hist->count / 2;
    uint32_t current = 0;
    
    for(uint32_t i = 0; i <= hist->white; i++)
    {
        current += hist_get_bin(hist, (uint16_t)i);
        if(current > middle) return (uint16_t)i;
    }
    return 0;
}

uint32_t hist_get_bin(const struct histogram * hist, uint16_t bin)
{
    if(hist == NULL || bin > hist->white) return 0;
    const uint32_t overflow = hist->overflow_data != NULL ? hist->overflow_data[bin] : 0;
    return (uint32_t)hist->data[bin] + overflow;
}

/**
 * Free memory resources for a histogram
 */
void hist_destroy(struct histogram * hist)
{
    if(hist == NULL) return;
    free(hist->data);
    free(hist->overflow_data);
    free(hist);
}
