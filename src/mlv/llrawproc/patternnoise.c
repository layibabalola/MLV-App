/*
 * Pattern noise correction
 * Copyright (C) 2015 A1ex
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

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "wirth.h"
#include "math.h"
#include "patternnoise.h"

static int g_debug_flags;
//#ifndef WIN32
#define MIN(a,b) \
({ __typeof__ ((a)+(b)) _a = (a); \
__typeof__ ((a)+(b)) _b = (b); \
_a < _b ? _a : _b; })

#define MAX(a,b) \
({ __typeof__ ((a)+(b)) _a = (a); \
__typeof__ ((a)+(b)) _b = (b); \
_a > _b ? _a : _b; })
//#else
//#define MIN min
//#define MAX max
//#endif

#define COERCE(x,lo,hi) MAX(MIN((x),(hi)),(lo))
#define COUNT(x)        ((int)(sizeof(x)/sizeof((x)[0])))

#define FIXPN_FULL_RES_PLANES 7
#define FIXPN_HALF_RES_PLANES 8

enum {
    FIXPN_FULL_RAW_T = 0,
    FIXPN_FULL_AVG_G = 1,
    FIXPN_FULL_DIF_RG = 2,
    FIXPN_FULL_DIF_BG = 3,
    FIXPN_FULL_NOISE = 4,
    FIXPN_FULL_MASK = 5,
    FIXPN_FULL_HGRAD = 6
};

static int pattern_noise_ensure_buffer(void ** buffer, size_t * capacity, size_t required_elements, size_t element_size)
{
    if (required_elements == 0)
    {
        return 1;
    }

    if (*buffer && (*capacity >= required_elements))
    {
        return 1;
    }

    if (required_elements > (((size_t)-1) / element_size))
    {
        return 0;
    }

    void * resized = realloc(*buffer, required_elements * element_size);
    if (!resized)
    {
        return 0;
    }

    *buffer = resized;
    *capacity = required_elements;
    return 1;
}

static int pattern_noise_prepare(pattern_noise_scratch_t * scratch, int w, int h)
{
    size_t full_pixels = (size_t)w * h;
    size_t half_pixels = (size_t)(w / 2) * (size_t)(h / 2);
    size_t max_half = MAX((size_t)(w / 2), (size_t)(h / 2));
    size_t int_scratch = max_half * 2;

    return pattern_noise_ensure_buffer((void **)&scratch->full_res_planes,
                                       &scratch->full_res_capacity,
                                       full_pixels * FIXPN_FULL_RES_PLANES,
                                       sizeof(scratch->full_res_planes[0]))
        && pattern_noise_ensure_buffer((void **)&scratch->half_res_planes,
                                       &scratch->half_res_capacity,
                                       half_pixels * FIXPN_HALF_RES_PLANES,
                                       sizeof(scratch->half_res_planes[0]))
        && pattern_noise_ensure_buffer((void **)&scratch->int_scratch,
                                       &scratch->int_scratch_capacity,
                                       int_scratch,
                                       sizeof(scratch->int_scratch[0]));
}

static int16_t * pattern_noise_full_plane(pattern_noise_scratch_t * scratch, int plane_index, size_t plane_pixels)
{
    return scratch->full_res_planes + ((size_t)plane_index * plane_pixels);
}

static int16_t * pattern_noise_half_plane(pattern_noise_scratch_t * scratch, int plane_index, size_t plane_pixels)
{
    return scratch->half_res_planes + ((size_t)plane_index * plane_pixels);
}

void free_pattern_noise_scratch(pattern_noise_scratch_t * scratch)
{
    if (!scratch)
    {
        return;
    }

    free(scratch->full_res_planes);
    free(scratch->half_res_planes);
    free(scratch->int_scratch);
    scratch->full_res_planes = NULL;
    scratch->half_res_planes = NULL;
    scratch->int_scratch = NULL;
    scratch->full_res_capacity = 0;
    scratch->half_res_capacity = 0;
    scratch->int_scratch_capacity = 0;
}

/* out = a - b */
static void subtract(int16_t * a, int16_t * b, int16_t * out, int w, int h)
{
    #pragma omp parallel for
    for (int i = 0; i < w*h; i++)
    {
        out[i] = a[i] - b[i];
    }
}

/* out = (a + b) / 2 */
static void average(int16_t * a, int16_t * b, int16_t * out, int w, int h)
{
    #pragma omp parallel for
    for (int i = 0; i < w*h; i++)
    {
        out[i] = ((int)a[i] + (int)b[i]) / 2;
    }
}

/* w and h are the size of input buffer; the output buffer will have the dimensions swapped */
static void transpose(int16_t * in, int16_t * out, int w, int h)
{
    #pragma omp parallel for collapse(2)
    for (int y = 0; y < h; y++)
    {
        for (int x = 0; x < w; x++)
        {
            out[y + x*h] = in[x + y*w];
        }
    }
}

static void horizontal_gradient(int16_t * in, int16_t * out, int w, int h)
{
    #pragma omp parallel for
    for (int i = 2; i < w*h-2; i++)
    {
        out[i] = in[i-2] - in[i+2];
    }
    
    out[0] = out[1] = out[w*h-1] = out[w*h-2] = 0;
}

static void horizontal_edge_aware_blur_rggb(
                                            int16_t * in_r,  int16_t * in_g1,  int16_t * in_g2,  int16_t * in_b,
                                            int16_t * out_r, int16_t * out_g1, int16_t * out_g2, int16_t * out_b,
                                            int w, int h, int strength, int thr,
                                            pattern_noise_scratch_t * scratch)
{
    #define NMAX 128
    int16_t g1[NMAX];
    int16_t g2[NMAX];
    int16_t rg[NMAX];
    int16_t bg[NMAX];
    if (strength > NMAX)
    {
#ifndef STDOUT_SILENT
        printf("FIXME: blur too strong\n");
#endif
        return;
    }
    
    strength /= 2;
    
    /* precompute average green, red-green and blue-green */
    size_t full_pixels = (size_t)w * h;
    int16_t * avg_g  = pattern_noise_full_plane(scratch, FIXPN_FULL_AVG_G, full_pixels);
    int16_t * dif_rg = pattern_noise_full_plane(scratch, FIXPN_FULL_DIF_RG, full_pixels);
    int16_t * dif_bg = pattern_noise_full_plane(scratch, FIXPN_FULL_DIF_BG, full_pixels);
    average(in_g1, in_g2, avg_g, w, h);
    subtract(in_r, avg_g, dif_rg, w, h);
    subtract(in_b, avg_g, dif_bg, w, h);

    for (int y = 0; y < h; y++)
    {
        int prev_xl = -1;
        int prev_xr = -1;
        for (int x = 0; x < w; x++)
        {
            int p0 = avg_g[x + y*w];
            int num = 0;
            
            /* range of pixels similar to p0 */
            /* it will contain at least 1 pixel, and at most from 2*strength + 1 pixels */
            int xl = x-1;
            int xr = x+1;
            
            /* go to the right, until crossing the threshold */
            while (xr < MIN(x + strength, w))
            {
                int p = avg_g[xr + y*w];
                if (abs(p - p0) > thr)
                    break;
                xr++;
            }
            
            /* same, to the left */
            while (xl >= MAX(x - strength, 0))
            {
                int p = avg_g[xl + y*w];
                if (abs(p - p0) > thr)
                    break;
                xl--;
            }
            
            if(xl == prev_xl && xr == prev_xr)
            {
                //don't recompute the median if we selected the same pixels
                out_g1[x + y*w] = out_g1[x - 1 + y*w];
                out_g2[x + y*w] = out_g2[x - 1 + y*w];
                out_r [x + y*w] = out_r [x - 1 + y*w];
                out_b [x + y*w] = out_b [x - 1 + y*w];
            }
            else
            {
                num = xr - xl - 1;
                int size = num * sizeof(int16_t);
                memcpy(g1, &(in_g1[xl + 1 + y*w]), size);
                memcpy(g2, &(in_g2[xl + 1 + y*w]), size);
                memcpy(rg, &(dif_rg[xl + 1 + y*w]), size);
                memcpy(bg, &(dif_bg[xl + 1 + y*w]), size);
                
                int mg1 = median_short_wirth(g1, num);
                int mg2 = median_short_wirth(g2, num);
                int mg = (mg1 + mg2) / 2;
                out_g1[x + y*w] = mg1;
                out_g2[x + y*w] = mg2;
                out_r [x + y*w] = median_short_wirth(rg, num) + mg;
                out_b [x + y*w] = median_short_wirth(bg, num) + mg;
            }
            
            prev_xl = xl;
            prev_xr = xr;
        }
    }
}

/* Find and apply a scalar offset to each column, to reduce pattern noise */
/* original: input and output */
/* denoised: input only */
static void fix_column_noise(int16_t * original, int16_t * denoised, int w, int h, int white, pattern_noise_scratch_t * scratch)
{
    /* let's say the difference between original and denoised is mostly noise */
    size_t full_pixels = (size_t)w * h;
    int16_t * noise = pattern_noise_full_plane(scratch, FIXPN_FULL_NOISE, full_pixels);
    subtract(original, denoised, noise, w, h);
    
    /* from this noise, keep the FPN part (constant offset for each line/column) */
    int* col_offsets = scratch->int_scratch;
    int* noise_row = scratch->int_scratch + w;
    int  noise_row_num = 0;
    
    /* certain areas will give false readings, mask them out */
    int16_t * mask  = pattern_noise_full_plane(scratch, FIXPN_FULL_MASK, full_pixels);
    int16_t * hgrad = pattern_noise_full_plane(scratch, FIXPN_FULL_HGRAD, full_pixels);
    
    horizontal_gradient(original, hgrad, w, h);

    #pragma omp parallel for collapse(2)
    for (int y = 0; y < h; y++)
    {
        for (int x = 0; x < w; x++)
        {
            int pixel = original[x + y*w];
            int hgradient = abs(hgrad[x + y*w]);
            
            mask[x + y*w] =
            (hgradient > 500) ||   /* mask out pixels on a strong edge, that is clearly not pattern noise */
            (pixel >= white);      /* mask out bright pixels (caveat: you really need to set the correct white level for this to work) */
        }
    }
    
    if (g_debug_flags & FIXPN_DBG_DENOISED)
    {
        /* debug: show denoised image */
        for (int i = 0; i < w*h; i++)
            original[i] = denoised[i];
        goto end;
    }
    else if (g_debug_flags & FIXPN_DBG_NOISE)
    {
        /* debug: show the noise image */
        for (int i = 0; i < w*h; i++)
        {
            if (mask[i]) noise[i] = -100;
            original[i] = noise[i] + 100;
        }
        goto end;
    }
    else if (g_debug_flags & FIXPN_DBG_MASK)
    {
        /* debug: show the mask */
        for (int i = 0; i < w*h; i++)
            original[i] = mask[i] * 1000;
        goto end;
    }
    
    /* take the median value for each column, in the noise image */
    for (int x = 0; x < w; x++)
    {
        noise_row_num = 0;
        for (int y = 0; y < h; y++)
        {
            if (mask[x + y*w] == 0)
            {
                noise_row[noise_row_num++] = noise[x + y*w];
            }
        }
        
        int offset = (noise_row_num < 10) ? 0 : -median_int_wirth(noise_row, noise_row_num);
        
        col_offsets[x] = offset;
    }
    
    /* almost done, now apply the offsets */
    #pragma omp parallel for collapse(2)
    for (int y = 0; y < h; y++)
    {
        for (int x = 0; x < w; x++)
        {
            original[x + y*w] = COERCE((int)original[x + y*w] + col_offsets[x], -32767, 32767);
        }
    }
    
    /* remove median from offsets, to prevent color cast */
    /* note: median modifies the array, so we do this after applying the offsets to the image */
    int mc = median_int_wirth(col_offsets, w);

    #pragma omp parallel for
    for (int i = 0; i < w*h; i++)
    {
        /* FIXME: clamping to 32766 causes overflow */
        original[i] = COERCE((int)original[i] - mc, 0, 32760);
    }
    
end:
}

/* extract a color channel from a Bayer image */
/* w and h are the size of the input buffer; output will be half-res */
/* dx and dy can be 0 or 1 */
static void extract_channel(int16_t * in, int16_t * out, int w, int h, int dx, int dy)
{
    #pragma omp parallel for collapse(2)
    for (int y = dy; y < h; y += 2)
    {
        for (int x = dx; x < w; x += 2)
        {
            out[(x/2) + (y/2)*(w/2)] = in[x + y*w];
        }
    }
}

/* set a color channel into a Bayer image */
/* w and h are the size of the output buffer (full-size image); input will be half-res */
/* dx and dy can be 0 or 1 */
static void set_channel(int16_t * out, int16_t * in, int w, int h, int dx, int dy)
{
    #pragma omp parallel for collapse(2)
    for (int y = dy; y < h; y += 2)
    {
        for (int x = dx; x < w; x += 2)
        {
            out[x + y*w] = in[(x/2) + (y/2)*(w/2)];
        }
    }
}

static void fix_column_noise_rggb(int16_t * raw, int w, int h, int white, pattern_noise_scratch_t * scratch)
{
    size_t half_pixels = (size_t)(w / 2) * (size_t)(h / 2);
    /* assume Bayer order [RGGB] */
    int16_t * r        = pattern_noise_half_plane(scratch, 0, half_pixels);   /* red channel (bottom left) */
    int16_t * g1       = pattern_noise_half_plane(scratch, 1, half_pixels);   /* top-left green */
    int16_t * g2       = pattern_noise_half_plane(scratch, 2, half_pixels);   /* bottom-right green */
    int16_t * b        = pattern_noise_half_plane(scratch, 3, half_pixels);   /* blue channel (top right) */
    int16_t * rs       = pattern_noise_half_plane(scratch, 4, half_pixels);   /* r  after smoothing */
    int16_t * g1s      = pattern_noise_half_plane(scratch, 5, half_pixels);   /* g1 after smoothing */
    int16_t * g2s      = pattern_noise_half_plane(scratch, 6, half_pixels);   /* g2 after smoothing */
    int16_t * bs       = pattern_noise_half_plane(scratch, 7, half_pixels);   /* b  after smoothing */
    
    /* extract half-res color channels from Bayer data */
    extract_channel(raw, r,  w, h, 0, 0);
    extract_channel(raw, g1, w, h, 1, 0);
    extract_channel(raw, g2, w, h, 0, 1);
    extract_channel(raw, b,  w, h, 1, 1);
    
    /* strong horizontal denoising (1-D median blur on G, R-G and B-G, stop on edge */
    /* (this step takes a lot of time) */
    horizontal_edge_aware_blur_rggb(r, g1, g2, b, rs, g1s, g2s, bs, w/2, h/2, 50, 500, scratch);
    
    /* after blurring horizontally, the difference reveals vertical FPN */
    fix_column_noise(r,  rs,  w/2, h/2, white, scratch);
    fix_column_noise(g1, g1s, w/2, h/2, white, scratch);
    fix_column_noise(g2, g2s, w/2, h/2, white, scratch);
    fix_column_noise(b,  bs,  w/2, h/2, white, scratch);
    
    /* commit changes */
    set_channel(raw, r,  w, h, 0, 0);
    set_channel(raw, g1, w, h, 1, 0);
    set_channel(raw, g2, w, h, 0, 1);
    set_channel(raw, b,  w, h, 1, 1);
}

void fix_pattern_noise(int16_t * raw, int w, int h, int white, int debug_flags, pattern_noise_scratch_t * scratch)
{
    pattern_noise_scratch_t local_scratch = {0};
    int using_local_scratch = 0;

    if (!scratch)
    {
        scratch = &local_scratch;
        using_local_scratch = 1;
    }

    if (!pattern_noise_prepare(scratch, w, h))
    {
        if (using_local_scratch)
        {
            free_pattern_noise_scratch(scratch);
        }
        return;
    }

    g_debug_flags = debug_flags;
    
    /* fix vertical noise, then transpose and repeat for the horizontal one */
    /* not very efficient, but at least avoids duplicate code */
    /* note: when debugging, we process only one direction */
    if (!g_debug_flags || !(g_debug_flags & FIXPN_DBG_ROWNOISE))
    {
        fix_column_noise_rggb(raw, w, h, white, scratch);
    }
    
    if (!g_debug_flags || (g_debug_flags & FIXPN_DBG_ROWNOISE))
    {
        /* transpose, process just like before, then transpose back */
        size_t full_pixels = (size_t)w * h;
        int16_t * raw_t = pattern_noise_full_plane(scratch, FIXPN_FULL_RAW_T, full_pixels);
        transpose(raw, raw_t, w, h);
        fix_column_noise_rggb(raw_t, h, w, white, scratch);
        transpose(raw_t, raw, h, w);
    }

    if (using_local_scratch)
    {
        free_pattern_noise_scratch(scratch);
    }
}
