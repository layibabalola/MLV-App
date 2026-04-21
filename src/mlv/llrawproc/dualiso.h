/*
 * Copyright (C) 2014 David Milligan
 * Copyright (C) 2017 bouncyball
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

#ifndef _dualiso_h
#define _dualiso_h

#include <sys/types.h>
#include "../raw.h"

typedef struct
{
    int * data_x;
    int * data_y;
    double * data_w;
    size_t data_capacity;
} dualiso_preview_scratch_t;

typedef struct
{
    uint32_t * raw_buffer_32;
    uint32_t * dark;
    uint32_t * bright;
    uint32_t * fullres;
    uint32_t * halfres;
    uint32_t * fullres_smooth;
    uint32_t * halfres_smooth;
    uint16_t * overexposed;
    uint16_t * alias_map;
    uint16_t * over_aux;
    double * mix_curve;
    size_t pixel_capacity;
    size_t mix_curve_capacity;
} dualiso_full20bit_scratch_t;

int diso_get_preview(uint16_t * image_data, uint16_t width, uint16_t height, int32_t black, int32_t white, int * iso_pattern, int diso_check, dualiso_preview_scratch_t * scratch);
int diso_get_full20bit(struct raw_info raw_info, uint16_t * image_data, int dark_frame, int iso1, int iso2, int * iso_pattern, int * auto_correction, double * ev_correction, int * black_delta, int interp_method, int use_alias_map, int use_fullres, int chroma_smooth_method, int threads, dualiso_full20bit_scratch_t * scratch);
void free_dualiso_full20bit_scratch(dualiso_full20bit_scratch_t * scratch);

#endif
