/*
 * CinemaDNG sequence reader.
 *
 * MLV-App historically only WROTE CinemaDNG (see src/dng/dng.c). This module
 * is the inverse: it parses a single .dng file's TIFF/EP IFD (per the layout
 * produced by dng.c) to recover geometry / calibration metadata, and decodes a
 * frame's bayer payload (uncompressed bit-packed strip OR LJ92 lossless-JPEG)
 * into a uint16 bayer buffer that is byte-for-byte equivalent to what the MLV
 * raw path yields post bit-unpack / LJ92-decode.
 *
 * It additionally enumerates a folder of .dng/.DNG files in natural sort order
 * so a sequence can be played back as a clip. The actual per-frame decode is
 * delegated to the existing reusable codec helpers in dng.c
 * (dng_decompress_image) and lj92 so no demosaic / processing code is
 * reinvented -- the decoded bayer frame is fed straight into the existing
 * AMaZE / processing / display pipeline.
 *
 * This module has NO dependency on Qt or mlvObject_t; it is plain C so it can
 * be reused headlessly (e.g. by a future batch path).
 */

#ifndef _dng_reader_h
#define _dng_reader_h

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* TIFF Compression tag values a reader keys on (mirrors dng_tag_values.h). */
#define DNG_READER_COMPRESSION_NONE 1  /* packed-bigendian bayer strip      */
#define DNG_READER_COMPRESSION_LJ92 7  /* LJ92 lossless-JPEG bayer stream   */

/* Per-file metadata recovered from one DNG's IFD0 (+ a couple EXIF tags). */
typedef struct
{
    int      valid;            /* 1 once parsed OK                          */

    uint32_t width;            /* tcImageWidth (256)                        */
    uint32_t height;           /* tcImageLength (257)                       */
    uint16_t bits_per_sample;  /* tcBitsPerSample (258)                     */
    uint16_t compression;      /* tcCompression (259): 1 = none, 7 = LJ92   */

    uint64_t strip_offset;     /* tcStripOffsets (273) -- byte pos of strip */
    uint64_t strip_byte_count; /* tcStripByteCounts (279) -- strip length   */

    uint32_t cfa_pattern;      /* packed 2x2 (default RGGB = 0x02010100)    */

    int32_t  black_level;      /* tcBlackLevel (50714)                      */
    int32_t  white_level;      /* tcWhiteLevel (50717)                      */
    int32_t  active_area[4];   /* tcActiveArea (50829): y1,x1,y2,x2         */

    /* Color science (each as 9 signed rationals -> stored as int*2 num/den
     * pairs exactly like camid expects: [num,den, num,den, ...]).
     * has_* is 0 when the tag was absent in the file. */
    int      has_color_matrix1;
    int32_t  color_matrix1[18];
    int      has_color_matrix2;
    int32_t  color_matrix2[18];
    int      has_forward_matrix1;
    int32_t  forward_matrix1[18];
    int      has_forward_matrix2;
    int32_t  forward_matrix2[18];

    int      has_as_shot_neutral; /* tcAsShotNeutral (50728): 3 rationals  */
    int32_t  as_shot_neutral[6];  /* [num,den]*3 for R,G,B                 */

    /* EXIF-ish odds and ends (best effort; 0 when absent). */
    int32_t  iso;              /* tcISOSpeedRatings (34855)                 */

    char     camera_model[64]; /* tcUniqueCameraModel (50708) or Model     */
} dng_frame_info_t;

/* A whole enumerated folder of DNG frames. */
typedef struct
{
    char **  paths;     /* paths[i] = absolute/relative path to frame i     */
    uint32_t count;     /* number of frames                                 */
    dng_frame_info_t info; /* metadata from frame 0 (assumed homogeneous)   */
} dng_sequence_t;

/* Parse a single DNG file's IFD0 into *out. Returns 0 on success, non-zero on
 * error (not a DNG / unreadable / unsupported). Does NOT decode pixels. */
int dng_reader_parse_file(const char * path, dng_frame_info_t * out);

/* Return non-zero only when [offset, offset + length) is wholly contained in
 * an extent of extent_size bytes.  Kept public so hostile TIFF offset tests
 * exercise the exact predicate used by the parser. */
int dng_reader_range_fits(uint64_t offset, uint64_t length, size_t extent_size);

/* Enumerate *.dng / *.DNG in dirPath (natural sorted), parse frame 0's IFD,
 * and fill *seq. Returns 0 on success. On success the caller owns seq and must
 * call dng_sequence_free(). Returns non-zero (and leaves seq zeroed) if the
 * folder has no readable DNG frames. */
int dng_sequence_open(const char * dirPath, dng_sequence_t * seq);

/* Free the path list held by a sequence (does not free seq itself). */
void dng_sequence_free(dng_sequence_t * seq);

/* Decode frame `index` of an opened sequence into `out16` (caller-allocated,
 * must hold width*height uint16). Returns 0 on success, non-zero on error.
 * The output is a row-major uint16 bayer frame (values in the low bits at
 * bits_per_sample), identical in layout to getMlvRawFrameUint16's output. */
int dng_sequence_get_bayer16(const dng_sequence_t * seq, uint32_t index, uint16_t * out16);

/* Lower-level: decode an already-read strip blob. `info` describes geometry /
 * compression. `strip` / `strip_size` are the raw bytes read from the file at
 * info->strip_offset. Exposed for reuse / testing. Returns 0 on success. */
int dng_reader_decode_strip(const dng_frame_info_t * info,
                            const uint8_t * strip, size_t strip_size,
                            uint16_t * out16);

/* Validate a parsed strip against the retained file extent before allocation.
 * Exposed for hostile sizing tests; returns 0 and writes count+4 on success. */
int dng_reader_strip_allocation_size(const dng_frame_info_t * info,
                                     uint64_t file_size,
                                     size_t * allocation_size);

/* Reject per-frame processing metadata drift that would otherwise be decoded
 * into buffers and calibration state derived from frame zero. Strip offsets,
 * byte counts and compression may vary per frame; geometry/CFA/range may not. */
int dng_reader_processing_metadata_matches(const dng_frame_info_t * expected,
                                           const dng_frame_info_t * candidate);

#ifdef __cplusplus
}
#endif

#endif /* _dng_reader_h */
