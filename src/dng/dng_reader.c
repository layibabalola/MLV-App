/*
 * CinemaDNG sequence reader -- see dng_reader.h.
 *
 * Inverse of src/dng/dng.c's writer. Parses a little-endian TIFF/EP IFD and
 * recovers the bayer frame. Pixel decode is delegated to the existing reusable
 * codec helpers (dng_decompress_image for LJ92) so nothing is reinvented.
 */

#include "dng_reader.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <limits.h>

#include "dng_tag_codes.h"
#include "dng_tag_types.h"
#include "dng_tag_values.h"
#include "dng.h"   /* dng_decompress_image() */

/* ------------------------------------------------------------------ */
/* Little-endian readers. The MLV-App writer always emits byteOrderII  */
/* (0x4949), so we only support little-endian files, but we validate   */
/* the byte order tag and bail otherwise.                              */
/* ------------------------------------------------------------------ */

static uint16_t rd_u16(const uint8_t * p)
{
    return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}

static uint32_t rd_u32(const uint8_t * p)
{
    return (uint32_t)(p[0]
                      | ((uint32_t)p[1] << 8)
                      | ((uint32_t)p[2] << 16)
                      | ((uint32_t)p[3] << 24));
}

/* TIFF directory entry layout: tag(2) type(2) count(4) value/offset(4). */
typedef struct
{
    uint16_t tag;
    uint16_t type;
    uint32_t count;
    uint32_t value;   /* inline value OR offset to value (file-relative) */
} dng_ifd_entry_t;

static size_t type_size(uint16_t type)
{
    switch(type)
    {
        case ttByte: case ttAscii: case ttSByte: case ttUndefined: return 1;
        case ttShort: case ttSShort: case ttUnicode:               return 2;
        case ttLong: case ttSLong: case ttFloat: case ttIFD:       return 4;
        case ttRational: case ttSRational: case ttDouble: case ttComplex: return 8;
        default: return 0;
    }
}

static int scalar_u32_contract(const dng_ifd_entry_t * e)
{
    return e && e->count == 1u && (e->type == ttShort || e->type == ttLong);
}

static int exact_contract(const dng_ifd_entry_t * e, uint16_t type, uint32_t count)
{
    return e && e->type == type && e->count == count;
}

/* Read a single scalar value of an entry as a uint32 (handles inline storage
 * for short/long types only; rationals / arrays go through the buffer). */
static uint32_t entry_scalar_u32(const dng_ifd_entry_t * e)
{
    if(e->type == ttShort || e->type == ttSShort) return e->value & 0xFFFF;
    return e->value;
}

/* Read `nrats` signed/unsigned rationals from `buf` at byte offset `off` into
 * `out` as interleaved num,den pairs (out length = nrats*2). Returns 0 on OK. */
static int read_rationals(const uint8_t * buf, size_t buf_size,
                          uint64_t off, uint32_t nrats, int32_t * out)
{
    const uint64_t bytes = (uint64_t)nrats * 8u;
    if(!dng_reader_range_fits(off, bytes, buf_size)) return 1;
    for(uint32_t i = 0; i < nrats; i++)
    {
        out[i * 2 + 0] = (int32_t)rd_u32(buf + off + i * 8 + 0);
        out[i * 2 + 1] = (int32_t)rd_u32(buf + off + i * 8 + 4);
    }
    return 0;
}

static int rational_denominators_nonzero(const int32_t * values, uint32_t nrats)
{
    if(!values) return 0;
    for(uint32_t i = 0; i < nrats; i++)
        if(values[i * 2u + 1u] == 0) return 0;
    return 1;
}

static int positive_rationals(const int32_t * values, uint32_t nrats)
{
    if(!values) return 0;
    for(uint32_t i = 0; i < nrats; i++)
        if(values[i * 2u] <= 0 || values[i * 2u + 1u] <= 0) return 0;
    return 1;
}

static int parse_exif_iso(const uint8_t * buf, size_t buf_size,
                          uint32_t ifd_offset, dng_frame_info_t * out)
{
    if(!buf || !out || !dng_reader_range_fits(ifd_offset, 2u, buf_size)) return 1;
    const uint16_t nentries = rd_u16(buf + ifd_offset);
    const uint64_t table_bytes = 2u + (uint64_t)nentries * 12u + 4u;
    if(!dng_reader_range_fits(ifd_offset, table_bytes, buf_size)) return 1;

    for(uint16_t i = 0; i < nentries; ++i)
    {
        const uint8_t * ep = buf + ifd_offset + 2u + (size_t)i * 12u;
        dng_ifd_entry_t e;
        e.tag = rd_u16(ep);
        e.type = rd_u16(ep + 2);
        e.count = rd_u32(ep + 4);
        e.value = rd_u32(ep + 8);
        if(e.tag != tcISOSpeedRatings) continue;
        if(out->has_iso) return 1;
        if(!exact_contract(&e, ttShort, 1u)) return 1;
        const uint32_t iso = entry_scalar_u32(&e);
        if(iso == 0u || iso > UINT16_MAX) return 1;
        out->iso = (int32_t)iso;
        out->has_iso = 1;
    }
    return 0;
}

int dng_reader_range_fits(uint64_t offset, uint64_t length, size_t extent_size)
{
    const uint64_t extent = (uint64_t)extent_size;
    return offset <= extent && length <= extent - offset;
}

/* ------------------------------------------------------------------ */
/* Parse one DNG file's IFD0.                                          */
/* ------------------------------------------------------------------ */

int dng_reader_parse_file(const char * path, dng_frame_info_t * out)
{
    if(!path || !out) return 1;
    memset(out, 0, sizeof(*out));
    out->cfa_pattern = 0x02010100; /* default RGGB unless overridden */

    FILE * f = fopen(path, "rb");
    if(!f) return 1;

    /* The header region (TIFF header + all IFDs + inline metadata) sits before
     * the strip. We read a generous prefix so out-of-line values resolve from
     * memory. Use the file's StripOffsets later to read the payload separately.
     * dng.c caps the header at HEADER_SIZE=1536 but the real header size may be
     * smaller; reading up to 64 KiB covers any reasonable metadata region. */
    enum { HDR_READ = 65536 };
    uint8_t * buf = (uint8_t *)malloc(HDR_READ);
    if(!buf) { fclose(f); return 1; }
    size_t got = fread(buf, 1, HDR_READ, f);
    fclose(f);
    if(got < 16) { free(buf); return 1; }

    /* TIFF header: byte order, magic 42, IFD0 offset. */
    uint16_t byte_order = rd_u16(buf + 0);
    uint16_t magic      = rd_u16(buf + 2);
    if(byte_order != 0x4949 /* II */ || magic != 42) { free(buf); return 1; }
    uint32_t ifd0_off = rd_u32(buf + 4);
    if(!dng_reader_range_fits(ifd0_off, 2u, got)) { free(buf); return 1; }

    uint16_t nentries = rd_u16(buf + ifd0_off);
    /* sanity: each entry is 12 bytes; entry table must fit in what we read */
    if(!dng_reader_range_fits(ifd0_off, 2u + (uint64_t)nentries * 12u + 4u, got)) {
        free(buf);
        return 1;
    }

    int have_w = 0, have_h = 0, have_bps = 0, have_compression = 0;
    int have_strip_offset = 0, have_strip_count = 0, have_cfa = 0;
    int have_photometric = 0, have_samples = 0, have_rows = 0;
    int have_planar = 0, have_cfa_repeat = 0;
    int have_black = 0, have_white = 0, have_active = 0;
    int have_exif_ifd = 0;
    int have_model = 0, have_unique_model = 0;
    uint32_t exif_ifd_offset = 0;
    out->compression = DNG_READER_COMPRESSION_NONE;

    for(uint16_t i = 0; i < nentries; i++)
    {
        const uint8_t * ep = buf + ifd0_off + 2 + (size_t)i * 12;
        dng_ifd_entry_t e;
        e.tag   = rd_u16(ep + 0);
        e.type  = rd_u16(ep + 2);
        e.count = rd_u32(ep + 4);
        e.value = rd_u32(ep + 8);

        /* Byte offset of out-of-line data: the 4-byte value field holds an
         * offset (file-relative) when total data size > 4 bytes. */
        const size_t element_size = type_size(e.type);
        if(element_size == 0 || e.count == 0) { free(buf); return 1; }
        uint64_t data_bytes = (uint64_t)element_size * e.count;
        uint64_t data_off   = (data_bytes > 4) ? e.value : (uint64_t)(ep + 8 - buf);

        switch(e.tag)
        {
            case tcImageWidth:
                if(have_w) { free(buf); return 1; }
                if(!scalar_u32_contract(&e)) { free(buf); return 1; }
                out->width = entry_scalar_u32(&e); have_w = 1; break;
            case tcImageLength:
                if(have_h) { free(buf); return 1; }
                if(!scalar_u32_contract(&e)) { free(buf); return 1; }
                out->height = entry_scalar_u32(&e); have_h = 1; break;
            case tcBitsPerSample:
            {
                if(have_bps) { free(buf); return 1; }
                if(!exact_contract(&e, ttShort, 1u)) { free(buf); return 1; }
                const uint32_t value = entry_scalar_u32(&e);
                if(value == 0u || value > 16u) { free(buf); return 1; }
                out->bits_per_sample = (uint16_t)value;
                have_bps = 1;
                break;
            }
            case tcCompression:
            {
                if(have_compression) { free(buf); return 1; }
                if(!exact_contract(&e, ttShort, 1u)) { free(buf); return 1; }
                const uint32_t value = entry_scalar_u32(&e);
                if(value > UINT16_MAX) { free(buf); return 1; }
                out->compression = (uint16_t)value;
                have_compression = 1;
                break;
            }
            case tcPhotometricInterpretation:
                if(have_photometric) { free(buf); return 1; }
                if(!exact_contract(&e, ttShort, 1u)
                   || entry_scalar_u32(&e) != piCFA) { free(buf); return 1; }
                have_photometric = 1;
                break;
            case tcSamplesPerPixel:
                if(have_samples) { free(buf); return 1; }
                if(!exact_contract(&e, ttShort, 1u)
                   || entry_scalar_u32(&e) != 1u) { free(buf); return 1; }
                have_samples = 1;
                break;
            case tcRowsPerStrip:
                if(have_rows) { free(buf); return 1; }
                if(!scalar_u32_contract(&e)) { free(buf); return 1; }
                out->rows_per_strip = entry_scalar_u32(&e);
                have_rows = 1;
                break;
            case tcPlanarConfiguration:
                if(have_planar) { free(buf); return 1; }
                if(!exact_contract(&e, ttShort, 1u)
                   || entry_scalar_u32(&e) != pcInterleaved) { free(buf); return 1; }
                have_planar = 1;
                break;
            case tcStripOffsets:
                if(have_strip_offset) { free(buf); return 1; }
                if(!exact_contract(&e, ttLong, 1u)) { free(buf); return 1; }
                out->strip_offset = entry_scalar_u32(&e); have_strip_offset = 1; break;
            case tcStripByteCounts:
                if(have_strip_count) { free(buf); return 1; }
                if(!exact_contract(&e, ttLong, 1u)) { free(buf); return 1; }
                out->strip_byte_count = entry_scalar_u32(&e); have_strip_count = 1; break;

            case tcCFAPattern:
                /* ttByte count4: 4 bytes give 2x2 mosaic order. Stored inline. */
                if(have_cfa || !exact_contract(&e, ttByte, 4u)
                   || !dng_reader_range_fits(data_off, 4u, got))
                { free(buf); return 1; }
                {
                    const uint8_t * cp = buf + data_off;
                    /* Pack as the writer does: byte0 | byte1<<8 | byte2<<16 | byte3<<24 */
                    out->cfa_pattern = (uint32_t)cp[0]
                                     | ((uint32_t)cp[1] << 8)
                                     | ((uint32_t)cp[2] << 16)
                                     | ((uint32_t)cp[3] << 24);
                    have_cfa = 1;
                }
                break;

            case tcCFARepeatPatternDim:
                if(have_cfa_repeat || !exact_contract(&e, ttShort, 2u)
                   || e.value != UINT32_C(0x00020002)) { free(buf); return 1; }
                have_cfa_repeat = 1;
                break;

            case tcBlackLevel:
                if(have_black) { free(buf); return 1; }
                if(!scalar_u32_contract(&e)) { free(buf); return 1; }
                out->black_level = (int32_t)entry_scalar_u32(&e); have_black = 1; break;
            case tcWhiteLevel:
                if(have_white) { free(buf); return 1; }
                if(!scalar_u32_contract(&e)) { free(buf); return 1; }
                out->white_level = (int32_t)entry_scalar_u32(&e); have_white = 1; break;

            case tcActiveArea:
                if(have_active || !exact_contract(&e, ttLong, 4u)
                   || !dng_reader_range_fits(data_off, 16u, got))
                { free(buf); return 1; }
                {
                    for(int k = 0; k < 4; k++)
                    {
                        const uint32_t value = rd_u32(buf + data_off + k * 4);
                        if(value > (uint32_t)INT32_MAX) { free(buf); return 1; }
                        out->active_area[k] = (int32_t)value;
                    }
                }
                have_active = 1;
                out->has_active_area = 1;
                break;

            case tcDefaultCropOrigin:
                if(out->has_default_crop_origin || !exact_contract(&e, ttShort, 2u)
                   || !dng_reader_range_fits(data_off, 4u, got))
                { free(buf); return 1; }
                out->default_crop_origin[0] = rd_u16(buf + data_off);
                out->default_crop_origin[1] = rd_u16(buf + data_off + 2u);
                out->has_default_crop_origin = 1;
                break;

            case tcDefaultCropSize:
                if(out->has_default_crop_size || !exact_contract(&e, ttShort, 2u)
                   || !dng_reader_range_fits(data_off, 4u, got))
                { free(buf); return 1; }
                out->default_crop_size[0] = rd_u16(buf + data_off);
                out->default_crop_size[1] = rd_u16(buf + data_off + 2u);
                out->has_default_crop_size = 1;
                break;

            case tcColorMatrix1:
                if(out->has_color_matrix1 || !exact_contract(&e, ttSRational, 9u)
                   || read_rationals(buf, got, data_off, 9, out->color_matrix1)
                   || !rational_denominators_nonzero(out->color_matrix1, 9u))
                { free(buf); return 1; }
                out->has_color_matrix1 = 1;
                break;
            case tcColorMatrix2:
                if(out->has_color_matrix2 || !exact_contract(&e, ttSRational, 9u)
                   || read_rationals(buf, got, data_off, 9, out->color_matrix2)
                   || !rational_denominators_nonzero(out->color_matrix2, 9u))
                { free(buf); return 1; }
                out->has_color_matrix2 = 1;
                break;
            case tcForwardMatrix1:
                if(out->has_forward_matrix1 || !exact_contract(&e, ttSRational, 9u)
                   || read_rationals(buf, got, data_off, 9, out->forward_matrix1)
                   || !rational_denominators_nonzero(out->forward_matrix1, 9u))
                { free(buf); return 1; }
                out->has_forward_matrix1 = 1;
                break;
            case tcForwardMatrix2:
                if(out->has_forward_matrix2 || !exact_contract(&e, ttSRational, 9u)
                   || read_rationals(buf, got, data_off, 9, out->forward_matrix2)
                   || !rational_denominators_nonzero(out->forward_matrix2, 9u))
                { free(buf); return 1; }
                out->has_forward_matrix2 = 1;
                break;

            case tcAsShotNeutral:
                if(out->has_as_shot_neutral || !exact_contract(&e, ttRational, 3u)
                   || read_rationals(buf, got, data_off, 3, out->as_shot_neutral)
                   || !positive_rationals(out->as_shot_neutral, 3u))
                { free(buf); return 1; }
                out->has_as_shot_neutral = 1;
                break;

            case tcDefaultScale:
                if(out->has_default_scale || !exact_contract(&e, ttRational, 2u)
                   || read_rationals(buf, got, data_off, 2, out->default_scale)
                   || !positive_rationals(out->default_scale, 2u))
                { free(buf); return 1; }
                out->has_default_scale = 1;
                break;

            case tcFrameRate:
                if(out->has_frame_rate || !exact_contract(&e, ttSRational, 1u)
                   || read_rationals(buf, got, data_off, 1, out->frame_rate)
                   || !positive_rationals(out->frame_rate, 1u))
                { free(buf); return 1; }
                out->has_frame_rate = 1;
                break;

            case tcBaselineExposure:
                if(out->has_baseline_exposure || !exact_contract(&e, ttSRational, 1u)
                   || read_rationals(buf, got, data_off, 1, out->baseline_exposure)
                   || out->baseline_exposure[1] == 0)
                { free(buf); return 1; }
                out->has_baseline_exposure = 1;
                break;

            case tcBaselineExposureOffset:
                if(out->has_baseline_exposure_offset || !exact_contract(&e, ttSRational, 1u)
                   || read_rationals(buf, got, data_off, 1, out->baseline_exposure_offset)
                   || out->baseline_exposure_offset[1] == 0)
                { free(buf); return 1; }
                out->has_baseline_exposure_offset = 1;
                break;

            case tcExifIFD:
                if(have_exif_ifd || !exact_contract(&e, ttLong, 1u)) { free(buf); return 1; }
                exif_ifd_offset = entry_scalar_u32(&e);
                if(exif_ifd_offset == 0u) { free(buf); return 1; }
                have_exif_ifd = 1;
                break;

            case tcISOSpeedRatings:
                /* ISO belongs in the nested EXIF IFD. Reject an ambiguous
                 * duplicate in IFD0 rather than silently preferring one. */
                free(buf); return 1;

            case tcModel:
                if(e.type != ttAscii || e.count == 0
                   /* IDNT.cameraName is the retained/re-exported model and
                    * holds at most 31 text bytes plus NUL. Reject truncation
                    * rather than accepting metadata that cannot round-trip. */
                   || e.count > 32u
                   || !dng_reader_range_fits(data_off, e.count, got)
                   || buf[data_off + e.count - 1u] != 0
                   || (e.count > 1u
                       && memchr(buf + data_off, 0, e.count - 1u) != NULL)
                   || have_model)
                { free(buf); return 1; }
                memcpy(out->camera_model, buf + data_off, e.count);
                have_model = 1;
                break;

            case tcUniqueCameraModel:
                if(e.type != ttAscii || e.count == 0
                   || e.count > sizeof(out->unique_camera_model)
                   || !dng_reader_range_fits(data_off, e.count, got)
                   || buf[data_off + e.count - 1u] != 0
                   || (e.count > 1u
                       && memchr(buf + data_off, 0, e.count - 1u) != NULL)
                   || have_unique_model)
                { free(buf); return 1; }
                memcpy(out->unique_camera_model, buf + data_off, e.count);
                have_unique_model = 1;
                break;

            default: break;
        }
    }

    if(have_exif_ifd && parse_exif_iso(buf, got, exif_ifd_offset, out) != 0)
    { free(buf); return 1; }
    free(buf);

    if(!have_w || !have_h || !have_bps || !have_compression
       || !have_strip_offset || !have_strip_count || !have_cfa
       || !have_photometric || !have_samples || !have_rows || !have_planar
       || !have_cfa_repeat || out->width == 0 || out->height == 0) return 1;
    if(out->rows_per_strip != out->height) return 1;
    if(out->has_active_area
       && (out->active_area[0] < 0 || out->active_area[1] < 0
           || out->active_area[2] <= out->active_area[0]
           || out->active_area[3] <= out->active_area[1]
           || (uint32_t)out->active_area[2] > out->height
           || (uint32_t)out->active_area[3] > out->width)) return 1;
    if(out->has_default_crop_origin != out->has_default_crop_size) return 1;
    if(out->has_default_crop_origin)
    {
        const uint64_t crop_width = out->has_active_area
            ? (uint64_t)(out->active_area[3] - out->active_area[1])
            : (uint64_t)out->width;
        const uint64_t crop_height = out->has_active_area
            ? (uint64_t)(out->active_area[2] - out->active_area[0])
            : (uint64_t)out->height;
        const uint64_t crop_x2 = (uint64_t)out->default_crop_origin[0]
                               + out->default_crop_size[0];
        const uint64_t crop_y2 = (uint64_t)out->default_crop_origin[1]
                               + out->default_crop_size[1];
        if(out->default_crop_size[0] == 0u || out->default_crop_size[1] == 0u
           || crop_x2 > crop_width || crop_y2 > crop_height) return 1;
    }
    if(out->strip_offset == 0 || out->strip_byte_count == 0) return 1;
    if(out->compression != DNG_READER_COMPRESSION_NONE &&
       out->compression != DNG_READER_COMPRESSION_LJ92) return 1;
    out->valid = 1;
    return 0;
}

/* ------------------------------------------------------------------ */
/* Strip decode.                                                       */
/* ------------------------------------------------------------------ */

int dng_reader_decode_strip(const dng_frame_info_t * info,
                            const uint8_t * strip, size_t strip_size,
                            uint16_t * out16)
{
    if(!info || !strip || !out16 || !info->valid) return 1;

    if(info->width == 0 || info->height == 0
       || info->width > (uint32_t)INT_MAX || info->height > (uint32_t)INT_MAX
       || (size_t)info->width > SIZE_MAX / (size_t)info->height) return 1;
    const int w   = (int)info->width;
    const int h   = (int)info->height;
    uint32_t bpp = info->bits_per_sample;
    const size_t pixels = (size_t)w * (size_t)h;
    if(pixels == 0 || pixels > SIZE_MAX / sizeof(uint16_t)) return 1;

    if(info->compression == DNG_READER_COMPRESSION_LJ92)
    {
        /* Reuse the existing LJ92 wrapper. It re-reads true geometry/bitdepth
         * from the JPEG SOF, so the passed w/h/bpp are advisory. */
        return dng_decompress_image(out16, pixels * sizeof(uint16_t),
                                    (uint16_t *)strip, strip_size, w, h, bpp);
    }

    /* Uncompressed strip. Two sub-cases:
     *   (a) true 16-bit pass-through (UNCOMPRESSED_ORIG): byteCount == w*h*2,
     *       payload is plain 16-bit samples byte-swapped to big-endian.
     *   (b) bit-packed at `bpp`, big-endian within 16-bit words (the common
     *       14-bit case). The MLV-App writer emits this via
     *       dng_pack_image_bits(..., big_endian=1) (src/dng/dng.c). Its proven
     *       inverse is dng_unpack_image_bits(), which expects little-endian
     *       16-bit words, so we first byte-swap each word back BE->LE and then
     *       call that exact helper -- making the round-trip bit-identical to the
     *       writer instead of re-deriving the rotate math by hand. */
    const size_t bytes_16bit = pixels * sizeof(uint16_t);

    if(bpp == 16u)
    {
        if(strip_size != bytes_16bit) return 1;
        /* (a) 16-bit pass-through: byte-swap each sample. */
        const uint8_t * src = strip;
        for(size_t i = 0; i < pixels; i++)
            out16[i] = (uint16_t)((src[i * 2] << 8) | src[i * 2 + 1]);
        return 0;
    }

    /* (b) bit-packed. Byte-swap each 16-bit word from the DNG's big-endian
     * layout back to little-endian, then delegate to the proven inverse of the
     * writer (dng_unpack_image_bits). The +2 word tail guards the 32-bit fetch
     * dng_unpack_image_bits performs on the final word. */
    if(bpp == 0 || bpp > 15 || pixels > (SIZE_MAX - 7u) / (size_t)bpp) return 1;
    const size_t packed_bits = pixels * (size_t)bpp;
    if((packed_bits & 15u) != 0) return 1;
    const size_t packed_bytes = packed_bits / 8u;
    if(strip_size != packed_bytes) return 1;

    if(packed_bytes > SIZE_MAX - 1u) return 1;
    const size_t payload_words = (packed_bytes + 1u) / 2u;
    if(payload_words > SIZE_MAX / sizeof(uint16_t) - 2u) return 1;
    size_t le_words = payload_words + 2u;
    uint16_t * le = (uint16_t *)calloc(le_words, sizeof(uint16_t));
    if(!le) return 1;
    const uint8_t * sp = strip;
    size_t src_words = packed_bytes / 2;
    for(size_t i = 0; i < src_words; i++)
        le[i] = (uint16_t)((sp[i * 2] << 8) | sp[i * 2 + 1]); /* BE -> LE */
    if(packed_bytes & 1) /* trailing odd byte (rare) */
        le[src_words] = (uint16_t)(sp[packed_bytes - 1] << 8);

    /* dng_unpack_image_bits signature is (output, input, w, h, bpp). */
    dng_unpack_image_bits(out16, le, w, h, bpp);

    free(le);
    return 0;
}

int dng_reader_strip_allocation_size(const dng_frame_info_t * info,
                                     uint64_t file_size,
                                     size_t * allocation_size)
{
    if(!info || !allocation_size || !info->valid
       || info->strip_byte_count == 0
       || info->strip_offset > file_size
       || info->strip_byte_count > file_size - info->strip_offset
       || info->strip_byte_count > (uint64_t)(SIZE_MAX - 4u))
        return 0;

    *allocation_size = (size_t)info->strip_byte_count + 4u;
    return 1;
}

int dng_reader_processing_metadata_matches(const dng_frame_info_t * expected,
                                           const dng_frame_info_t * candidate)
{
    if(!expected || !candidate || !expected->valid || !candidate->valid) return 0;
    if(candidate->width != expected->width
        || candidate->height != expected->height
        || candidate->bits_per_sample != expected->bits_per_sample
        || candidate->cfa_pattern != expected->cfa_pattern
        || candidate->black_level != expected->black_level
        || candidate->white_level != expected->white_level
        || candidate->has_color_matrix1 != expected->has_color_matrix1
        || candidate->has_color_matrix2 != expected->has_color_matrix2
        || candidate->has_forward_matrix1 != expected->has_forward_matrix1
        || candidate->has_forward_matrix2 != expected->has_forward_matrix2
        || candidate->has_as_shot_neutral != expected->has_as_shot_neutral
        || candidate->has_default_scale != expected->has_default_scale
        || candidate->has_frame_rate != expected->has_frame_rate
        || candidate->has_active_area != expected->has_active_area
        || candidate->has_default_crop_origin != expected->has_default_crop_origin
        || candidate->has_default_crop_size != expected->has_default_crop_size
        || candidate->has_baseline_exposure != expected->has_baseline_exposure
        || candidate->has_baseline_exposure_offset != expected->has_baseline_exposure_offset
        || candidate->has_iso != expected->has_iso
        || candidate->iso != expected->iso
        || strcmp(candidate->camera_model, expected->camera_model) != 0
        || strcmp(candidate->unique_camera_model, expected->unique_camera_model) != 0
        || memcmp(candidate->active_area, expected->active_area,
                  sizeof(expected->active_area)) != 0)
        return 0;

    return (!expected->has_color_matrix1
            || memcmp(candidate->color_matrix1, expected->color_matrix1,
                      sizeof(expected->color_matrix1)) == 0)
        && (!expected->has_color_matrix2
            || memcmp(candidate->color_matrix2, expected->color_matrix2,
                      sizeof(expected->color_matrix2)) == 0)
        && (!expected->has_forward_matrix1
            || memcmp(candidate->forward_matrix1, expected->forward_matrix1,
                      sizeof(expected->forward_matrix1)) == 0)
        && (!expected->has_forward_matrix2
            || memcmp(candidate->forward_matrix2, expected->forward_matrix2,
                      sizeof(expected->forward_matrix2)) == 0)
        && (!expected->has_as_shot_neutral
            || memcmp(candidate->as_shot_neutral, expected->as_shot_neutral,
                      sizeof(expected->as_shot_neutral)) == 0)
        && (!expected->has_default_scale
            || memcmp(candidate->default_scale, expected->default_scale,
                      sizeof(expected->default_scale)) == 0)
        && (!expected->has_frame_rate
            || memcmp(candidate->frame_rate, expected->frame_rate,
                      sizeof(expected->frame_rate)) == 0)
        && (!expected->has_default_crop_origin
            || memcmp(candidate->default_crop_origin, expected->default_crop_origin,
                      sizeof(expected->default_crop_origin)) == 0)
        && (!expected->has_default_crop_size
            || memcmp(candidate->default_crop_size, expected->default_crop_size,
                      sizeof(expected->default_crop_size)) == 0)
        && (!expected->has_baseline_exposure
            || memcmp(candidate->baseline_exposure, expected->baseline_exposure,
                      sizeof(expected->baseline_exposure)) == 0)
        && (!expected->has_baseline_exposure_offset
            || memcmp(candidate->baseline_exposure_offset,
                      expected->baseline_exposure_offset,
                      sizeof(expected->baseline_exposure_offset)) == 0);
}

/* ------------------------------------------------------------------ */
/* Per-frame fetch.                                                    */
/* ------------------------------------------------------------------ */

int dng_sequence_get_bayer16(const dng_sequence_t * seq, uint32_t index, uint16_t * out16)
{
    if(!seq || !out16 || index >= seq->count || !seq->info.valid) return 1;

    /* Each DNG in the folder is parsed lazily for its OWN strip geometry: the
     * sequence-level info (frame 0) drives buffer sizing, but per-frame strip
     * offset / byte count / compression can legitimately differ (e.g. variable
     * LJ92 sizes). Re-parse this frame's IFD to get exact strip bounds. */
    dng_frame_info_t fi;
    if(dng_reader_parse_file(seq->paths[index], &fi) != 0) return 1;

    /* Guard against geometry mismatch with the sequence -- the caller's buffer
     * is sized for seq->info dimensions. */
    if(!dng_reader_processing_metadata_matches(&seq->info, &fi)) return 1;

    FILE * f = fopen(seq->paths[index], "rb");
    if(!f) return 1;

    if(fi.strip_offset > (uint64_t)LONG_MAX
       || fseek(f, 0, SEEK_END) != 0) { fclose(f); return 1; }
    const long file_end = ftell(f);
    size_t allocation_size = 0;
    if(file_end < 0
       || !dng_reader_strip_allocation_size(&fi, (uint64_t)file_end, &allocation_size)
       || fseek(f, (long)fi.strip_offset, SEEK_SET) != 0) {
        fclose(f);
        return 1;
    }

    uint8_t * strip = (uint8_t *)malloc(allocation_size); /* +4 LJ92 safety */
    if(!strip) { fclose(f); return 1; }
    size_t rd = fread(strip, 1, (size_t)fi.strip_byte_count, f);
    fclose(f);
    if(rd != fi.strip_byte_count) { free(strip); return 1; }

    int ret = dng_reader_decode_strip(&fi, strip, rd, out16);
    free(strip);
    return ret;
}

/* ------------------------------------------------------------------ */
/* Folder enumeration with natural sort.                               */
/* ------------------------------------------------------------------ */

static int has_dng_ext(const char * name)
{
    size_t n = strlen(name);
    if(n < 4) return 0;
    const char * e = name + n - 4;
    return (e[0] == '.'
            && (e[1] == 'd' || e[1] == 'D')
            && (e[2] == 'n' || e[2] == 'N')
            && (e[3] == 'g' || e[3] == 'G'));
}

/* Natural comparison: digit runs compared numerically so frame_2 < frame_10. */
static int natural_cmp(const char * a, const char * b)
{
    while(*a && *b)
    {
        if(isdigit((unsigned char)*a) && isdigit((unsigned char)*b))
        {
            /* skip leading zeros */
            while(*a == '0' && isdigit((unsigned char)a[1])) a++;
            while(*b == '0' && isdigit((unsigned char)b[1])) b++;
            const char * as = a; const char * bs = b;
            while(isdigit((unsigned char)*a)) a++;
            while(isdigit((unsigned char)*b)) b++;
            size_t la = (size_t)(a - as), lb = (size_t)(b - bs);
            if(la != lb) return (la < lb) ? -1 : 1;
            int c = strncmp(as, bs, la);
            if(c) return c;
        }
        else
        {
            int ca = tolower((unsigned char)*a);
            int cb = tolower((unsigned char)*b);
            if(ca != cb) return (ca < cb) ? -1 : 1;
            a++; b++;
        }
    }
    if(*a) return 1;
    if(*b) return -1;
    return 0;
}

static int path_qsort_cmp(const void * pa, const void * pb)
{
    const char * a = *(const char * const *)pa;
    const char * b = *(const char * const *)pb;
    return natural_cmp(a, b);
}

/* Cross-platform directory listing. On Windows we use the CRT _findfirst API
 * via <io.h>; elsewhere POSIX dirent. Both produce full joined paths. */
#if defined(_WIN32)
#include <io.h>
#else
#include <dirent.h>
#endif

static char * join_path(const char * dir, const char * name)
{
    size_t dl = strlen(dir);
    int need_sep = (dl > 0 && dir[dl - 1] != '/' && dir[dl - 1] != '\\');
    size_t nl = strlen(name);
    char * out = (char *)malloc(dl + (need_sep ? 1 : 0) + nl + 1);
    if(!out) return NULL;
    memcpy(out, dir, dl);
    size_t pos = dl;
    if(need_sep) out[pos++] = '/';
    memcpy(out + pos, name, nl);
    out[pos + nl] = 0;
    return out;
}

int dng_sequence_open(const char * dirPath, dng_sequence_t * seq)
{
    if(!dirPath || !seq) return 1;
    memset(seq, 0, sizeof(*seq));

    char ** paths = NULL;
    uint32_t count = 0, cap = 0;
    int enumeration_failed = 0;

#if defined(_WIN32)
    {
        /* Glob the directory then filter by extension. */
        char * pattern = join_path(dirPath, "*");
        if(!pattern) return 1;
        struct _finddata_t fd;
        intptr_t h = _findfirst(pattern, &fd);
        free(pattern);
        if(h == -1) return 1;
        do {
            if(fd.attrib & _A_SUBDIR) continue;
            if(!has_dng_ext(fd.name)) continue;
            if(count >= cap)
            {
                if(cap > UINT32_MAX / 2u) { enumeration_failed = 1; break; }
                uint32_t ncap = cap ? cap * 2u : 64u;
#if UINTPTR_MAX <= UINT32_MAX
                if(ncap > SIZE_MAX / sizeof(char *)) { enumeration_failed = 1; break; }
#endif
                char ** np = (char **)realloc(paths, (size_t)ncap * sizeof(char *));
                if(!np) { enumeration_failed = 1; break; }
                paths = np; cap = ncap;
            }
            char * p = join_path(dirPath, fd.name);
            if(!p) { enumeration_failed = 1; break; }
            paths[count++] = p;
        } while(_findnext(h, &fd) == 0);
        _findclose(h);
    }
#else
    {
        DIR * d = opendir(dirPath);
        if(!d) return 1;
        struct dirent * de;
        while((de = readdir(d)) != NULL)
        {
            if(!has_dng_ext(de->d_name)) continue;
            if(count >= cap)
            {
                if(cap > UINT32_MAX / 2u) { enumeration_failed = 1; break; }
                uint32_t ncap = cap ? cap * 2u : 64u;
#if UINTPTR_MAX <= UINT32_MAX
                if(ncap > SIZE_MAX / sizeof(char *)) { enumeration_failed = 1; break; }
#endif
                char ** np = (char **)realloc(paths, (size_t)ncap * sizeof(char *));
                if(!np) { enumeration_failed = 1; break; }
                paths = np; cap = ncap;
            }
            char * p = join_path(dirPath, de->d_name);
            if(!p) { enumeration_failed = 1; break; }
            paths[count++] = p;
        }
        closedir(d);
    }
#endif

    if(enumeration_failed || count == 0)
    {
        for(uint32_t i = 0; i < count; ++i) free(paths[i]);
        free(paths);
        return 1;
    }

    qsort(paths, count, sizeof(char *), path_qsort_cmp);

    /* Parse frame 0 for sequence-level metadata. */
    dng_frame_info_t info;
    if(dng_reader_parse_file(paths[0], &info) != 0)
    {
        for(uint32_t i = 0; i < count; i++) free(paths[i]);
        free(paths);
        return 1;
    }

    seq->paths = paths;
    seq->count = count;
    seq->info  = info;
    return 0;
}

void dng_sequence_free(dng_sequence_t * seq)
{
    if(!seq) return;
    if(seq->paths)
    {
        for(uint32_t i = 0; i < seq->count; i++) free(seq->paths[i]);
        free(seq->paths);
    }
    seq->paths = NULL;
    seq->count = 0;
    seq->info.valid = 0;
}
