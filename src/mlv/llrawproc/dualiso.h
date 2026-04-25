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
#include <stdint.h>
#include "../raw.h"

typedef struct
{
    int * data_x;
    int * data_y;
    double * data_w;
    size_t data_capacity;
    uint16_t * output_image;
    size_t output_capacity;
    double last_histogram_ms;
    double last_regression_ms;
    double last_rowscale_ms;
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

    int * histogram_match_dark;
    int * histogram_match_bright;
    int * histogram_match_tmp;
    int * histogram_match_hi_dark;
    int * histogram_match_hi_bright;
    size_t histogram_match_pixel_capacity;
    size_t histogram_match_sample_capacity;
    size_t histogram_match_highlight_capacity;

    int * identify_histograms;
    size_t identify_histogram_capacity;

    int * amaze_squeezed;
    float ** amaze_rawData_rows;
    float ** amaze_red_rows;
    float ** amaze_green_rows;
    float ** amaze_blue_rows;
    float * amaze_rawData_storage;
    float * amaze_red_storage;
    float * amaze_green_storage;
    float * amaze_blue_storage;
    uint32_t * amaze_gray;
    uint8_t * amaze_edge_direction;
    int * amaze_startchunk_y;
    int * amaze_endchunk_y;
    void * amaze_thread_id;
    void * amaze_arguments;
    size_t amaze_row_capacity;
    size_t amaze_row_width;
    size_t amaze_plane_cell_capacity;
    size_t amaze_pixel_capacity;
    size_t amaze_thread_capacity;

    uint16_t * alias_aux;
    size_t alias_aux_capacity;
} dualiso_full20bit_scratch_t;

int diso_get_preview(uint16_t * image_data, uint16_t width, uint16_t height, int32_t black, int32_t white, int * iso_pattern, int diso_check, dualiso_preview_scratch_t * scratch);
int diso_get_full20bit(struct raw_info raw_info, uint16_t * image_data, int dark_frame, int iso1, int iso2, int * iso_pattern, int * auto_correction, double * ev_correction, int * black_delta, int interp_method, int use_alias_map, int use_fullres, int chroma_smooth_method, int threads, dualiso_full20bit_scratch_t * scratch);
void free_dualiso_full20bit_scratch(dualiso_full20bit_scratch_t * scratch);

/* Test-only: counts how many times the HQ recon entered AMaZE (which == 0)
 * vs mean23 (which == 1) since the last reset. Used by pipeline tests to
 * verify the playback-mean23 override actually flipped the path without a
 * full pixel diff. Implemented as thread-local counters in dualiso.c. */
void dualiso_debug_note_hq_path(int which);
void dualiso_debug_reset_hq_path_counters(void);
unsigned long long dualiso_debug_hq_amaze_count(void);
unsigned long long dualiso_debug_hq_mean23_count(void);

/* Phase E5: thread-local counters for the alias_map and full-res blending
 * stages. Incremented when each stage is taken (use_alias_map != 0 /
 * use_fullres != 0) inside diso_get_full20bit. The reset peer is
 * dualiso_debug_reset_hq_path_counters above. Tests verify that the
 * scale-aware downgrade actually short-circuits these stages without
 * having to diff pixels against an alias_map-on reference. */
unsigned long long dualiso_debug_alias_map_taken_count(void);
unsigned long long dualiso_debug_fullres_blend_taken_count(void);

#endif
