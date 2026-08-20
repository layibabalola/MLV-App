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
        default: return 1;
    }
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
    if(off + (uint64_t)nrats * 8 > buf_size) return 1;
    for(uint32_t i = 0; i < nrats; i++)
    {
        out[i * 2 + 0] = (int32_t)rd_u32(buf + off + i * 8 + 0);
        out[i * 2 + 1] = (int32_t)rd_u32(buf + off + i * 8 + 4);
    }
    return 0;
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
    if(ifd0_off + 2 > got) { free(buf); return 1; }

    uint16_t nentries = rd_u16(buf + ifd0_off);
    /* sanity: each entry is 12 bytes; entry table must fit in what we read */
    if((uint64_t)ifd0_off + 2 + (uint64_t)nentries * 12 > got) { free(buf); return 1; }

    int have_w = 0, have_h = 0;
    out->bits_per_sample = 14;            /* sensible default */
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
        uint64_t data_bytes = (uint64_t)type_size(e.type) * e.count;
        uint64_t data_off   = (data_bytes > 4) ? e.value : (uint64_t)(ep + 8 - buf);

        switch(e.tag)
        {
            case tcImageWidth:  out->width  = entry_scalar_u32(&e); have_w = 1; break;
            case tcImageLength: out->height = entry_scalar_u32(&e); have_h = 1; break;
            case tcBitsPerSample: out->bits_per_sample = (uint16_t)entry_scalar_u32(&e); break;
            case tcCompression:   out->compression     = (uint16_t)entry_scalar_u32(&e); break;
            case tcStripOffsets:    out->strip_offset     = entry_scalar_u32(&e); break;
            case tcStripByteCounts: out->strip_byte_count = entry_scalar_u32(&e); break;

            case tcCFAPattern:
                /* ttByte count4: 4 bytes give 2x2 mosaic order. Stored inline. */
                if(e.count == 4 && data_off + 4 <= got)
                {
                    const uint8_t * cp = buf + data_off;
                    /* Pack as the writer does: byte0 | byte1<<8 | byte2<<16 | byte3<<24 */
                    out->cfa_pattern = (uint32_t)cp[0]
                                     | ((uint32_t)cp[1] << 8)
                                     | ((uint32_t)cp[2] << 16)
                                     | ((uint32_t)cp[3] << 24);
                }
                break;

            case tcBlackLevel: out->black_level = (int32_t)entry_scalar_u32(&e); break;
            case tcWhiteLevel: out->white_level = (int32_t)entry_scalar_u32(&e); break;

            case tcActiveArea:
                if(e.count == 4 && data_off + 16 <= got)
                {
                    for(int k = 0; k < 4; k++)
                        out->active_area[k] = (int32_t)rd_u32(buf + data_off + k * 4);
                }
                break;

            case tcColorMatrix1:
                if(e.count == 9 && !read_rationals(buf, got, data_off, 9, out->color_matrix1))
                    out->has_color_matrix1 = 1;
                break;
            case tcColorMatrix2:
                if(e.count == 9 && !read_rationals(buf, got, data_off, 9, out->color_matrix2))
                    out->has_color_matrix2 = 1;
                break;
            case tcForwardMatrix1:
                if(e.count == 9 && !read_rationals(buf, got, data_off, 9, out->forward_matrix1))
                    out->has_forward_matrix1 = 1;
                break;
            case tcForwardMatrix2:
                if(e.count == 9 && !read_rationals(buf, got, data_off, 9, out->forward_matrix2))
                    out->has_forward_matrix2 = 1;
                break;

            case tcAsShotNeutral:
                if(e.count == 3 && !read_rationals(buf, got, data_off, 3, out->as_shot_neutral))
                    out->has_as_shot_neutral = 1;
                break;

            case tcISOSpeedRatings:
                out->iso = (int32_t)entry_scalar_u32(&e);
                break;

            case tcUniqueCameraModel:
            case tcModel:
                if(out->camera_model[0] == 0 && data_off < got)
                {
                    size_t n = e.count;
                    if(n >= sizeof(out->camera_model)) n = sizeof(out->camera_model) - 1;
                    if(data_off + n <= got)
                    {
                        memcpy(out->camera_model, buf + data_off, n);
                        out->camera_model[n] = 0;
                    }
                }
                break;

            default: break;
        }
    }

    free(buf);

    if(!have_w || !have_h || out->width == 0 || out->height == 0) return 1;
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
    if(pixels == 0) return 1;

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
    const size_t bytes_16bit = pixels * 2;

    /* Recover bits-per-sample when the BitsPerSample(258) tag is absent or zero
     * (some writer paths fill the IFD before the per-frame bit depth is known
     * and emit 0). The packed strip size unambiguously encodes the real bit
     * depth: bpp = strip_byte_count * 8 / pixels. Without this, bpp==0 makes the
     * unpack mask 0 and every output pixel becomes zero (the all-zeros bug). */
    if((bpp == 0 || bpp > 16) && strip_size < bytes_16bit)
    {
        uint32_t derived = (uint32_t)((uint64_t)strip_size * 8u / pixels);
        if(derived >= 8 && derived <= 16) bpp = derived;
    }

    if(strip_size >= bytes_16bit && (bpp == 0 || bpp >= 16))
    {
        /* (a) 16-bit pass-through: byte-swap each sample. */
        const uint8_t * src = strip;
        for(size_t i = 0; i < pixels; i++)
            out16[i] = (uint16_t)((src[i * 2] << 8) | src[i * 2 + 1]);
        return 0;
    }

    if(bpp == 0 || bpp > 16) return 1; /* cannot determine packing */

    /* (b) bit-packed. Byte-swap each 16-bit word from the DNG's big-endian
     * layout back to little-endian, then delegate to the proven inverse of the
     * writer (dng_unpack_image_bits). The +2 word tail guards the 32-bit fetch
     * dng_unpack_image_bits performs on the final word. */
    const size_t packed_bytes = ((size_t)pixels * bpp + 7) / 8;
    if(strip_size < packed_bytes) return 1;

    size_t le_words = (packed_bytes + 1) / 2 + 2;
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
    if(fi.width != seq->info.width || fi.height != seq->info.height) return 1;

    FILE * f = fopen(seq->paths[index], "rb");
    if(!f) return 1;

    if(fseek(f, (long)fi.strip_offset, SEEK_SET) != 0) { fclose(f); return 1; }

    uint8_t * strip = (uint8_t *)malloc(fi.strip_byte_count + 4); /* +4 LJ92 safety */
    if(!strip) { fclose(f); return 1; }
    size_t rd = fread(strip, 1, fi.strip_byte_count, f);
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
                uint32_t ncap = cap ? cap * 2 : 64;
                char ** np = (char **)realloc(paths, ncap * sizeof(char *));
                if(!np) break;
                paths = np; cap = ncap;
            }
            char * p = join_path(dirPath, fd.name);
            if(p) paths[count++] = p;
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
                uint32_t ncap = cap ? cap * 2 : 64;
                char ** np = (char **)realloc(paths, ncap * sizeof(char *));
                if(!np) break;
                paths = np; cap = ncap;
            }
            char * p = join_path(dirPath, de->d_name);
            if(p) paths[count++] = p;
        }
        closedir(d);
    }
#endif

    if(count == 0)
    {
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
