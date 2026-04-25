# MLV App — Technical Specification: Algorithms and Binary Layouts

> Companion to [docs/03-technical-specification.md](03-technical-specification.md).
> Where 03 names a struct, stage, or contract, this doc gives the actual byte
> layout, the actual formula, or the actual algorithm. Read 03 first for
> navigation; read this for reconstruction. Together with 03 and the README,
> this file aims to satisfy the reconstruction-bar criterion: a senior
> engineer (or competent LLM) should be able to reproduce a working MLV
> reader, LJ92 decoder, the 9-stage processing pipeline, and a CDNG writer
> with no source-code access.

## Table of contents

1. [Purpose](#1-purpose)
2. [MLV file format](#2-mlv-file-format)
3. [MAPP sidecar format](#3-mapp-sidecar-format)
4. [LJ92 codec](#4-lj92-codec)
5. [LLRAWPROC algorithms](#5-llrawproc-algorithms)
6. [The 9-Stage Processing Pipeline](#6-the-9-stage-processing-pipeline)
7. [Debayer algorithms](#7-debayer-algorithms)
8. [Receipt (.marxml) schema](#8-receipt-marxml-schema)
9. [CinemaDNG container layout](#9-cinemadng-container-layout)
10. [`--profile-playback` JSON contract](#10---profile-playback-json-contract)

## 1. Purpose

This is the algorithms-and-binary-layouts companion to `docs/03-technical-specification.md`. Where 03 names a struct or stage, this doc gives the actual byte layout, the actual formula, or the actual algorithm. Read 03 first for the navigation; read this for reconstruction.

## 2. MLV file format

MLV (Magic Lantern Video) is a container that wraps Canon-style raw Bayer data plus per-frame and per-clip metadata blocks. All numeric fields are stored in **little-endian** byte order (the layout matches the Canon DIGIC ARM architecture, which is little-endian). All struct definitions live in `src/mlv/mlv.h` and are wrapped in `#pragma pack(push,1)` ... `#pragma pack(pop)` so there is **no implicit padding** anywhere in this format (`src/mlv/mlv.h:43`, `src/mlv/mlv.h:329`).

Every block in the file shares a common 16-byte prefix: a 4-byte ASCII tag, a 4-byte little-endian `uint32` size that includes the prefix, and an 8-byte little-endian `uint64` hardware-counter timestamp relative to recording start (`src/mlv/mlv.h:45-49`):

```c
typedef struct {
    uint8_t     blockType[4];
    uint32_t    blockSize;
    uint64_t    timestamp;
} mlv_hdr_t;
```

Reading the format reduces to walking the file by `blockSize`-step jumps from the end of the previous block, dispatching on the 4-byte tag.

The video class field in the file header tells the reader how to interpret raw frame payloads. Defined in `src/mlv/mlv.h:27-37`:

| Constant | Value | Meaning |
| --- | --- | --- |
| `MLV_VIDEO_CLASS_RAW` | `0x01` | Bayer raw |
| `MLV_VIDEO_CLASS_YUV` | `0x02` | YUV |
| `MLV_VIDEO_CLASS_JPEG` | `0x03` | JPEG |
| `MLV_VIDEO_CLASS_H264` | `0x04` | H.264 |
| `MLV_VIDEO_CLASS_FLAG_MCRAW` | `0x100` | mcraw container variant |
| `MLV_VIDEO_CLASS_FLAG_LZMA` | `0x80` | LZMA-compressed payload |
| `MLV_VIDEO_CLASS_FLAG_DELTA` | `0x40` | Delta-encoded payload |
| `MLV_VIDEO_CLASS_FLAG_LJ92` | `0x20` | Lossless JPEG 1992 (LJ92) |

The audio class similarly carries `MLV_AUDIO_CLASS_FLAG_LZMA = 0x80` for compressed audio (`src/mlv/mlv.h:37`).

The version stamp embedded in `versionString[8]` of the file header is `"v2.0"` (`src/mlv/mlv.h:26`).

### 2.1 File header (MLVI / `mlv_file_hdr_t`)

The file always opens with an MLVI block (`src/mlv/mlv.h:51-65`):

```c
typedef struct {
    uint8_t     fileMagic[4];        /* "MLVI" */
    uint32_t    blockSize;           /* size of the whole header */
    uint8_t     versionString[8];    /* null-terminated, e.g. "v2.0" */
    uint64_t    fileGuid;            /* UID generated from hw counter, time, PRNG */
    uint16_t    fileNum;             /* 0 .. fileCount-1 */
    uint16_t    fileCount;           /* number of files in this group (spanning) */
    uint32_t    fileFlags;           /* 1=out-of-order, 2=dropped frames,
                                         4=single image, 8=stopped due to error */
    uint16_t    videoClass;          /* see MLV_VIDEO_CLASS_* */
    uint16_t    audioClass;          /* 0=none, 1=WAV */
    uint32_t    videoFrameCount;     /* set to 0 on start, updated on close */
    uint32_t    audioFrameCount;     /* same */
    uint32_t    sourceFpsNom;        /* numerator of source frame rate */
    uint32_t    sourceFpsDenom;      /* denominator (1000 normally, 1001 for NTSC) */
} mlv_file_hdr_t;
```

`fileGuid` ties together the chunks of a spanned recording, and `fileNum` / `fileCount` describe the chunk's position within the group.

### 2.2 Raw image info (RAWI / `mlv_rawi_hdr_t`)

There is one RAWI block per recording (per chunk, but the writer only emits a meaningful one in the first chunk). It owns the Bayer geometry and calibration levels (`src/mlv/mlv.h:89-96`):

```c
typedef struct {
    uint8_t     blockType[4];
    uint32_t    blockSize;
    uint64_t    timestamp;
    uint16_t    xRes;          /* configured video resolution */
    uint16_t    yRes;
    struct raw_info raw_info;  /* full ML Core raw_info, see raw.h */
} mlv_rawi_hdr_t;
```

The embedded `struct raw_info` (`src/mlv/raw.h:138-179`) carries `width`, `height`, `pitch`, `frame_size`, `bits_per_pixel`, `black_level`, `white_level`, the active-area rectangle, the DNG color matrix, the calibration illuminant, and the dynamic-range estimate. Bayer ordering is `RGGB` for Canon-style sensors (the comment at `src/mlv/raw.h:27-39` shows row 0 = `RG RG ...`, row 1 = `GB GB ...`).

A separate **RAWC** block (`mlv_rawc_hdr_t` at `src/mlv/mlv.h:98-130`) carries sensor capture geometry (`sensor_res_x`, `sensor_res_y`, `sensor_crop`, `binning_x/y`, `skipping_x/y`, `offset_x/y`). It is optional and primarily used to detect crop_rec mode for focus-pixel removal.

### 2.3 Video frame (VIDF / `mlv_vidf_hdr_t`)

Each video frame is one VIDF block. The frame payload begins **after** the header plus an EDMAC alignment pad (`src/mlv/mlv.h:67-78`):

```c
typedef struct {
    uint8_t     blockType[4];   /* "VIDF" */
    uint32_t    blockSize;      /* total size including header + pad + payload */
    uint64_t    timestamp;      /* hw counter relative to recording start */
    uint32_t    frameNumber;    /* unique video frame number */
    uint16_t    cropPosX;       /* sensor row/col origin in 8x2 blocks */
    uint16_t    cropPosY;
    uint16_t    panPosX;        /* user pan offset in 1x1 blocks */
    uint16_t    panPosY;
    uint32_t    frameSpace;     /* dummy bytes before frameData (EDMAC alignment) */
 /* uint8_t     frameData[variable]; */
} mlv_vidf_hdr_t;
```

The actual raw payload starts at `block_offset + sizeof(mlv_vidf_hdr_t) + frameSpace`. Payload size is `blockSize - sizeof(mlv_vidf_hdr_t) - frameSpace`. For uncompressed `MLV_VIDEO_CLASS_RAW` payloads, the bytes are packed Bayer at `raw_info.bits_per_pixel` (10, 12, or 14). For LJ92-flagged payloads (`MLV_VIDEO_CLASS_FLAG_LJ92`), the payload is a complete lossless-JPEG stream that the loader hands to `lj92_open` / `lj92_decode` (see section 4).

The **bit-packing layouts** for uncompressed raw are at `src/mlv/raw.h:60-133`. They group 8 sequential pixels into a fixed-byte cell so the unpacker can be a fixed stride loop with no shifts that cross arbitrary boundaries:

- **14-bit** (`raw_pixblock_14`, `src/mlv/raw.h:60-76`): 8 pixels packed into 14 bytes. Bit layout (MSB first):
  ```
  aaaaaaaaaaaaaabb bbbbbbbbbbbbcccc ccccccccccdddddd ddddddddeeeeeeee
  eeeeeeffffffffff ffffgggggggggggg gghhhhhhhhhhhhhh
  ```
  Pixels `a` and `h` are the full-byte-aligned ends; pixels `b..g` are split across byte boundaries via the high/low bitfields shown.
- **12-bit** (`raw_pixblock_12`, `src/mlv/raw.h:91-105`): 8 pixels packed into 12 bytes.
- **10-bit** (`raw_pixblock_10`, `src/mlv/raw.h:119-133`): 8 pixels packed into 10 bytes.

All three structs are `__attribute__((packed,aligned(2)))`. The pixel pair `a` / `h` corresponds to the Bayer pattern: on even rows `a` is red and `h` is green; on odd rows `a` is green and `h` is blue (`src/mlv/raw.h:74` comment "even lines: green; odd lines: blue").

The Bayer pattern itself is RGGB starting at the top-left active pixel (`src/mlv/raw.h:27-39`):

```
0   RG RG RG RG RG RG ...   <-- first line (even)
1   GB GB GB GB GB GB ...   <-- second line (odd)
2   RG RG RG RG RG RG ...
3   GB GB GB GB GB GB ...
```

The CFA pattern code in `raw_info.cfa_pattern` defaults to `0x02010100` (RGBG); the loader also handles `0x01000201` (GBRG), `0x00010102` (BGGR) and `0x01020001` (GRBG) by shifting the buffer one row/column to renormalise (see `getMlvRawFrameUint16` at `src/mlv/video_mlv.c:252-289`).

### 2.4 Audio frame (AUDF / `mlv_audf_hdr_t`)

Per-frame audio packets carry raw PCM (or alaw/mulaw) bytes (`src/mlv/mlv.h:80-87`):

```c
typedef struct {
    uint8_t     blockType[4];   /* "AUDF" */
    uint32_t    blockSize;
    uint64_t    timestamp;
    uint32_t    frameNumber;
    uint32_t    frameSpace;     /* EDMAC alignment pad */
 /* uint8_t     frameData[variable]; */
} mlv_audf_hdr_t;
```

A single **WAVI** block (`mlv_wavi_hdr_t` at `src/mlv/mlv.h:132-142`) describes the WAV-style format (`format`, `channels`, `samplingRate`, `bytesPerSecond`, `blockAlign`, `bitsPerSample`).

### 2.5 Metadata blocks

The format defines a fixed catalogue of metadata blocks. Each follows the common `blockType[4] / blockSize / timestamp` prefix. The full inventory in `src/mlv/mlv.h`:

| Tag | Struct | Header line | Purpose |
| --- | --- | --- | --- |
| `EXPO` | `mlv_expo_hdr_t` | 144-153 | ISO mode/value, analog ISO, digital gain (1024 = 1 EV), shutter (microseconds) |
| `LENS` | `mlv_lens_hdr_t` | 155-168 | Focal length, focus distance, aperture x100, IS / AF mode, lens ID, lens name (32 chars), serial (32 chars) |
| `ELNS` | `mlv_elns_hdr_t` | 170-183 | Extended lens info (focal range min/max, aperture range, version, extender, capability bits, variable-length lens name) |
| `RTCI` | `mlv_rtci_hdr_t` | 185-200 | RTC clock (`tm_sec` ... `tm_yday`, `tm_isdst`, GMT offset, time-zone string) |
| `IDNT` | `mlv_idnt_hdr_t` | 202-209 | Camera name (32 chars), `cameraModel` (DIGIC PROP id), serial (32 chars) |
| `XREF` | `mlv_xref_hdr_t` + `mlv_xref_t[]` | 211-225 | Out-of-order-recovery cross-reference; one entry per VIDF/AUDF (file number, frame type, file offset) |
| `INFO` | `mlv_info_hdr_t` | 227-232 | Free-form user info string (variable length follows header) |
| `DISO` | `mlv_diso_hdr_t` | 234-240 | Dual-ISO recording: `dualMode` bitmask (`0=off`, `1=odd lines`, `2=even lines`) and `isoValue` for the second ISO |
| `MARK` | `mlv_mark_hdr_t` | 242-247 | User marker (button press); `type` field codes which button |
| `STYL` | `mlv_styl_hdr_t` | 249-259 | Picture style ID, contrast, sharpness, saturation, colortone, name (16 chars) |
| `ELVL` | `mlv_elvl_hdr_t` | 261-267 | Electronic level: roll x100, pitch x100 |
| `WBAL` | `mlv_wbal_hdr_t` | 269-280 | White balance mode (`AUTO/SUNNY/SHADE/CLOUDY/TUNGSTEN/FLUORESCENT/FLASH/CUSTOM/KELVIN`), Kelvin value, R/G/B gains (1024 = 1.0, dcraw convention), GM and BA shift |
| `DEBG` | `mlv_debg_hdr_t` | 282-289 | Debug log lines (variable-length string; `length` is real length, block padded to 32-bit) |
| `VERS` | `mlv_vers_hdr_t` | 291-300 | Module version string ("`<module> <version>`"), once per module |
| `DARK` | `mlv_dark_hdr_t` | 302-327 | Embedded averaged dark frame: dimensions, bit depth, B/W levels, exposure, binning + raw pixel payload |

Quoted definition for the most-referenced metadata blocks:

```c
typedef struct {
    uint8_t     blockType[4];   /* "EXPO" */
    uint32_t    blockSize;
    uint64_t    timestamp;
    uint32_t    isoMode;        /* 0=manual, 1=auto */
    uint32_t    isoValue;
    uint32_t    isoAnalog;      /* hardware-amplified ISO */
    uint32_t    digitalGain;    /* 1024 = 1 EV */
    uint64_t    shutterValue;   /* exposure in microseconds */
} mlv_expo_hdr_t;                                                /* mlv.h:144-153 */

typedef struct {
    uint8_t     blockType[4];   /* "RTCI" */
    uint32_t    blockSize;
    uint64_t    timestamp;
    uint16_t    tm_sec, tm_min, tm_hour;
    uint16_t    tm_mday, tm_mon, tm_year;     /* year since 1900 */
    uint16_t    tm_wday, tm_yday;
    uint16_t    tm_isdst, tm_gmtoff;
    uint8_t     tm_zone[8];
} mlv_rtci_hdr_t;                                                /* mlv.h:185-200 */

typedef struct {
    uint8_t     blockType[4];   /* "WBAL" */
    uint32_t    blockSize;
    uint64_t    timestamp;
    uint32_t    wb_mode;        /* AUTO=0,SUNNY=1,CLOUDY=2,TUNGSTEN=3,
                                   FLUORESCENT=4,FLASH=5,CUSTOM=6,SHADE=8,KELVIN=9 */
    uint32_t    kelvin;         /* if wb_mode == KELVIN */
    uint32_t    wbgain_r;       /* if wb_mode == CUSTOM; 1024 = 1.0 */
    uint32_t    wbgain_g;       /* (dcraw convention: 1/canon_gain) */
    uint32_t    wbgain_b;
    uint32_t    wbs_gm;         /* WB shift, range -9..9 */
    uint32_t    wbs_ba;
} mlv_wbal_hdr_t;                                                /* mlv.h:269-280 */

typedef struct {
    uint8_t     blockType[4];   /* "DISO" */
    uint32_t    blockSize;
    uint64_t    timestamp;
    uint32_t    dualMode;       /* 0=off, 1=odd lines hi-ISO, 2=even lines hi-ISO */
    uint32_t    isoValue;       /* second ISO for dual-ISO */
} mlv_diso_hdr_t;                                                /* mlv.h:234-240 */
```

The DARK block carries an embedded averaged dark-frame for in-clip subtraction. It is unusual in that `timestamp` is intentionally set to the maximum value of `uint64_t` (`src/mlv/mlv.h:305`) so it sorts to the end of any time-ordered block list. Pixel data follows the header at `block_offset + sizeof(mlv_dark_hdr_t)`.

### 2.6 Block ordering and spanned files

The writing camera emits an MLVI block first, then a RAWI (and optionally RAWC, IDNT, EXPO, LENS, RTCI, WAVI, WBAL, STYL, DISO, DARK, VERS) once per recording, then any number of VIDF/AUDF blocks **interleaved in time order**. Camera firmware can emit metadata blocks (EXPO, LENS, MARK, ELVL ...) at any time mid-stream when the parameter changes, so a robust reader must walk every block, not seek by frame index alone. Out-of-order writes are tolerated when `fileFlags & 1` is set (`src/mlv/mlv.h:58`); in that case an XREF block (added in post by `mlv_dump` or similar) gives sorted access (`src/mlv/mlv.h:218-225`).

Long recordings split into multiple chunks because of FAT32 4 GiB limits and live performance constraints. Naming convention is the original `clip.MLV` plus zero-padded sequence files `clip.M00`, `clip.M01`, ..., up to `clip.M99`. The loader at `src/mlv/video_mlv.c:97-163` (`load_all_chunks`) implements the discovery: it opens the base file, then probes the next file by overwriting the last two bytes of the extension with `"00"`, `"01"`, ... and stops at the first miss. If the base file is not a `.MLV` (e.g. a directly-loaded `.M00`), `seq_number` is set to `100` so no sequence probing happens (`src/mlv/video_mlv.c:121-125`). All chunks share `fileGuid` and `fileCount`; each chunk's `fileNum` increases.

The reader always sorts and merges frame headers from all chunks into a single time-ordered `frame_index_t[]` keyed by `frame_time` (`src/mlv/mlv_object.h:21-30`, sort at `src/mlv/video_mlv.c:172-192`).

**Chunk discovery details** (`src/mlv/video_mlv.c:97-163`):
- The discovery probe stops at `seq_number == 99` (`while(seq_number < 99)` at `video_mlv.c:128`) so sequence numbers `M00`..`M98` are valid; `M99` is the upper bound.
- The probe stops at the first miss - i.e. if `M00` and `M01` exist but `M02` does not, only two chunks are loaded; subsequent chunks (`M03` ...) are silently ignored. This means a deletion in the middle of a chunk run truncates the recording.
- File handles for all chunks are stored in the `FILE **file` array on `mlvObject_t` (`src/mlv/mlv_object.h:57`), one entry per chunk, paired with a per-chunk `pthread_mutex_t main_file_mutex` (`mlv_object.h:59`). The mutex is held while seeking and reading so concurrent decode threads can share the file handle.

## 3. MAPP sidecar format

The MAPP file is MLVApp's accelerator cache for opening a clip. Without it, opening a multi-GiB MLV requires walking every block in every chunk to build the frame index; with it, opening reduces to a single sidecar read.

**Extension**: `.MAPP`. The path is derived from the MLV path by replacing the extension - `strrchr(filename, '.')` then `memcpy(dot, ".MAPP\0", 6)` (`src/mlv/video_mlv.c:716-717`, identical at `src/mlv/video_mlv.c:825-826`).

**Header struct** (`src/mlv/mlv_object.h:33-44`):

```c
#define MAPP_VERSION 3
typedef struct {
    uint8_t     fileMagic[4];   /* "MAPP" */
    uint64_t    mapp_size;      /* total .MAPP file size including audio payload */
    uint8_t     mapp_version;   /* MAPP_VERSION (currently 3) */
    uint32_t    block_num;      /* total MLV block count across all chunks */
    uint32_t    video_frames;   /* total video frame count */
    uint32_t    audio_frames;   /* total audio frame count */
    uint32_t    vers_blocks;    /* total VERS blocks */
    uint64_t    audio_size;     /* total audio data size in bytes */
    uint64_t    df_offset;      /* file offset to embedded DARK frame, if any */
} mapp_header_t;
```

**File layout** (write order at `src/mlv/video_mlv.c:752-782`):

1. `mapp_header_t`
2. `mlv_file_hdr_t  MLVI`
3. `mlv_rawi_hdr_t  RAWI`
4. `mlv_rawc_hdr_t  RAWC`
5. `mlv_idnt_hdr_t  IDNT`
6. `mlv_expo_hdr_t  EXPO`
7. `mlv_lens_hdr_t  LENS`
8. `mlv_elns_hdr_t  ELNS`
9. `mlv_rtci_hdr_t  RTCI`
10. `mlv_wbal_hdr_t  WBAL`
11. `mlv_styl_hdr_t  STYL`
12. `mlv_wavi_hdr_t  WAVI`
13. `mlv_diso_hdr_t  DISO`
14. `mlv_dark_hdr_t  DARK`
15. `camera_id_t  camid`
16. `frame_index_t[video_frames]` video index
17. `frame_index_t[audio_frames]` audio index
18. `frame_index_t[vers_blocks]` vers index
19. Audio payload bytes (`audio_size`, copied straight from `video->audio_data`)

`frame_index_t` itself is 32 bytes packed (`src/mlv/mlv_object.h:21-30`):

```c
typedef struct {
    uint16_t frame_type;     /* VIDF=1, AUDF=2, VERS=3 */
    uint16_t chunk_num;      /* which .M0n chunk holds this frame */
    uint32_t frame_number;
    uint32_t frame_size;
    uint64_t frame_offset;   /* offset to start of frame data within chunk */
    uint64_t frame_time;     /* microseconds since recording start */
    uint64_t block_offset;   /* offset to start of the block header */
} frame_index_t;
```

**What is cached**: every metadata header MLVApp routinely needs at open time, the per-frame seek index for video and audio, the VERS block index, the audio PCM stream, and the offset of any embedded DARK frame. Pixel data and per-frame thumbnails are **not** cached - playback still pulls VIDF payloads from the original `.MLV/.M0n` files via the cached `block_offset` / `frame_offset`.

**Invalidation rules** (validated in `load_mapp` at `src/mlv/video_mlv.c:818-983`):

- Magic must equal `"MAPP"` (`video_mlv.c:852-855`); any mismatch makes the loader fall back to a full-walk open.
- `mapp_version` must equal `MAPP_VERSION` (currently 3); old MAPPs are rejected with the message "Wrong MAPP version: %d. Please rebuild all MAPPs" (`video_mlv.c:858-862`). Bumping `MAPP_VERSION` in `mlv_object.h:33` is how MLVApp forces a global cache rebuild after any sidecar-affecting change.
- `mapp_size` must equal the actual file size on disk (`video_mlv.c:864-872`). This is the truncation / partial-write check.

There is no MLV mtime/size hash in the header, so a MAPP can drift if the underlying MLV is replaced atomically without updating the MAPP. In practice MLVApp regenerates the MAPP whenever the user opens the clip via the `MLV_OPEN_MAPP` path (`save_mapp` is called at `src/mlv/video_mlv.c:2196`).

**Open modes and audio**: the `open_mode` argument to `openMlvClip` decides whether MAPP is read at all. When `open_mode == MLV_OPEN_MAPP`, the loader first attempts `load_mapp`; on success it skips the full MLV walk and goes straight to the playback-ready state. In preview mode (`MLV_OPEN_PREVIEW`) the audio payload portion of MAPP is intentionally **not** loaded because preview never needs PCM (`src/mlv/video_mlv.c:1607`, `1833`). The audio block in MAPP is therefore lazy: only the index and offsets are mandatory; the PCM is consumed only by full-open paths.

**Per-clip extension story**: the choice of an `.MAPP` sidecar (rather than embedding into the MLV) means MLVApp can index a read-only `.MLV` (e.g. on a write-protected SD card or a network share) without modifying it - the cache lives next to the file in a writable directory. It also means a stale MAPP can be "fixed" by simply deleting it; the next open rebuilds.

**Per-camera calibration data (`src/mlv/camid/`)**: the `camera_id_t  camid` block written to MAPP at step 15 above is populated from MLVApp's bundled per-camera-body calibration tables under `src/mlv/camid/` (one header file per supported body — colour-matrix coefficients, white-balance presets, lens corrections, etc.). These tables are **not transcribed in this docs set** because they are large hand-curated data, not algorithms. A clean reimplementation has two reasonable options:

1. **Treat `src/mlv/camid/` as data, not code**: copy the headers verbatim into the new build. They are MIT/GPL-compatible with the surrounding source.
2. **Use the MLV's IDNT block + Adobe DNG generic matrices**: every supported body emits `IDNT.cameraName` and `RAWI.dng_matrix1/2` (`02 §2.5`) which together carry the manufacturer-published cam-matrix. Combined with the Adobe DNG SDK's standard `CalibrationIlluminant1 = StdA, CalibrationIlluminant2 = D65` interpolation rule, this is sufficient for clip playback at the cost of slight per-body colour drift relative to the bundled `camid/` tables.

Either path produces a rebuilable application; option 1 reproduces MLVApp's exact colour, option 2 produces "DNG-conformant generic" colour.

## 4. LJ92 codec

LJ92 is **lossless JPEG 1992** as standardised in ITU-T T.81 / ISO 10918-1. It uses Huffman coding of prediction residuals; the predictor is selectable (modes 0-7) and operates on the previous pixel ("a"), the pixel above ("b") and the pixel above-left ("c"). Implementation in `src/mlv/liblj92/lj92.{c,h}`, copyright Andrew Baldwin 2014 (MIT license).

### 4.1 Public API (`src/mlv/liblj92/lj92.h`)

The full header:

```c
enum LJ92_ERRORS {
    LJ92_ERROR_NONE      =  0,
    LJ92_ERROR_CORRUPT   = -1,
    LJ92_ERROR_NO_MEMORY = -2,
    LJ92_ERROR_BAD_HANDLE= -3,
    LJ92_ERROR_TOO_WIDE  = -4,
    LJ92_ERROR_ENCODER   = -5
};

typedef struct _ljp* lj92;

/* Parse an LJ92 stream. Returns LJ92_ERROR_NONE on success;
 * the resulting handle must be released with lj92_close. */
int  lj92_open  (lj92* lj,
                 uint8_t* data, int datalen,
                 int* width, int* height, int* bitdepth, int* components);

/* Decode the previously-opened stream into a 16-bit tile.
 * Writes writeLength uint16 values, then skips skipLength uint16 values
 * before the next row. Optional linearization LUT applied per pixel. */
int  lj92_decode(lj92 lj,
                 uint16_t* target, int writeLength, int skipLength,
                 uint16_t* linearize, int linearizeLength);

void lj92_close (lj92 lj);

/* Encode a grayscale 16-bit image at the given bit depth.
 * Returns malloc'd encoded buffer in *encoded; caller frees. */
int  lj92_encode(uint16_t* image, int width, int height, int bitdepth,
                 int readLength, int skipLength,
                 uint16_t* delinearize, int delinearizeLength,
                 uint8_t** encoded, int* encodedLength);
```

The opaque `lj92` handle is `_ljp*` (`src/mlv/liblj92/lj92.c:38-72`), holding the data pointer, parsed Huffman tables, geometry, current bit/byte offsets, and the row caches.

### 4.2 Predictor modes

`parseScan` reads byte 3 of the start-of-scan marker (after `Ls` and `Ns` bytes) as the predictor index (`src/mlv/liblj92/lj92.c:519-522`):

```c
int compcount = self->data[self->ix+2];
int pred      = self->data[self->ix+3+2*compcount];
if (pred<0 || pred>7) return ret;
if (pred==6) return parsePred6(self);   // Fast path
```

The full predictor table for the generic path is at `src/mlv/liblj92/lj92.c:548-573`. In ITU-T T.81 Table H.1 terms (`Px` is the predicted value, `left` = pixel `a`, `lastrow[colx]` = pixel `b`, `lastrow[prev_colx]` = pixel `c`):

| Mode | Formula | Notes |
| --- | --- | --- |
| 0 | `Px = 0` | "no prediction" - rejected at runtime |
| 1 | `Px = a` (left neighbour) | Used by Magic Lantern Dual-ISO encoder |
| 2 | `Px = b` (above) | |
| 3 | `Px = c` (above-left) | |
| 4 | `Px = a + b - c` | |
| 5 | `Px = a + ((b - c) >> 1)` | |
| 6 | `Px = b + ((a - c) >> 1)` | Most common ML lossless raw; `parsePred6` fast path |
| 7 | `Px = (a + b) / 2` | |

Edge cases:
- First pixel of the image: `Px = 1 << (bits-1)` (the half-range default; `lj92.c:433`, `lj92.c:539`).
- First pixel of any non-first row: `Px = lastrow[c]` (the pixel directly above; `lj92.c:471`, `lj92.c:544`).
- First row: `Px = thisrow[prev]` (the pixel to the left; `lj92.c:447`, `lj92.c:542`).

Decoding for any single pixel is `out = (Px + decoded_diff) mod 65536`, then optionally remapped via the `linearize[]` LUT (`lj92.c:577-583`).

### 4.3 Predictor 1 vs predictor 6 in MLVApp

The encoder side always emits predictor 6 (`src/mlv/liblj92/lj92.c:1022`, `e[w++] = 6; // Predictor` in `writeHead`). The fast `parsePred6` path is therefore optimal for **anything MLVApp has encoded** - which covers the vast majority of in-camera lossless raw written by `mlv_rec` / `mlv_lite` for non-Dual-ISO clips.

Magic Lantern's Dual-ISO recording module emits a different predictor. The fixtures we test against in `tests/fixtures/clips/` (and any 14-bit Dual-ISO `.MLV` produced by ML's `dual_iso` module) decode through **predictor 1** (left neighbour). This matters because the `parsePred6` fast path is not reached and decoding falls through to the slower generic switch loop at `lj92.c:548`. Per the MEMORY note `lj92_predictor_dualiso.md`, optimization work on Dual-ISO clip playback must target the generic predictor path, not pred6.

### 4.4 Huffman parsing

`parseHuff` (`src/mlv/liblj92/lj92.c:88`) reads the Define-Huffman-Table marker. The table is the standard JPEG DHT: 16 bytes giving the count of codes of each length 1..16, followed by the actual code values. `lj92.c` builds either the slow-path tables (`SLOW_HUFF` define) - `maxcode`, `mincode`, `valptr`, `huffval`, `huffsize`, `huffcode` - or the default fast lookup table `hufflut` indexed by `huffbits` bits at a time (`lj92.c:55-65`). The fast path trades a larger LUT (typically 64KB) for branchless per-symbol decode.

After the DHT, `find()` (`lj92.c:74-84`) scans for the next marker by stepping byte-by-byte until `0xFF` followed by a marker byte; this cleanly handles the byte-stuffing convention (`0xFF 0x00` represents a literal `0xFF` in the entropy-coded stream).

### 4.5 Thread safety

The decoder is **not internally locked**. The `_ljp` handle owns mutable parse state (`ix`, `cnt`, `b`, `outrow[2]`, `rowcache`) (`lj92.c:38-72`). Two threads decoding the same handle concurrently corrupt that state.

The supported pattern is **one `lj92` instance per worker thread**. Each call to `lj92_open` allocates a fresh handle with its own parse cursor, so spawning N parallel decode workers means calling `lj92_open` N times against the same input bytes, decoding into N disjoint output tiles, and `lj92_close`-ing each instance from its owning thread. The input `data`/`datalen` buffer is read-only during parse and is safe to share read-only across threads.

## 5. LLRAWPROC algorithms

LLRAWPROC is the "low-level raw processing" stage that runs immediately after raw decompression and **before** debayer / colour processing. The single entry point is `applyLLRawProcObject` (`src/mlv/llrawproc/llrawproc.c:200-478`). Stage gating happens at the top of the function: if `video->llrawproc->fix_raw == 0` the entire stage is bypassed (`llrawproc.c:202-203`).

The pipeline order inside `applyLLRawProcObject`:

1. Dark-frame subtraction (if enabled and `df_init` succeeds) - `llrawproc.c:206-215`
2. 10/12-bit -> 14-bit upscale (only when `bits_per_pixel < 14`) - `llrawproc.c:221-224`, function at `llrawproc.c:55-70`
3. Reset DNG B/W levels and refresh raw2ev / ev2raw LUTs if black level changed - `llrawproc.c:227-237`
4. Vertical-stripe correction - `llrawproc.c:240-251`
5. Focus-pixel removal - `llrawproc.c:253-276`
6. Bad-pixel removal - `llrawproc.c:278-298`
7. Pattern noise (only when not in valid Dual-ISO) - `llrawproc.c:301-310`
8. Dual-ISO 20-bit reconstruction (mode 1 only) - `llrawproc.c:312-440`, includes a *second* focus-pixel and bad-pixel pass against the reconstructed buffer (`llrawproc.c:378-422`)
9. Chroma smoothing (skipped when 20-bit Dual-ISO is active because that path runs its own internal smooth) - `llrawproc.c:443-456`
10. 14-bit -> original-bit-depth downscale (skipped when 20-bit Dual-ISO is active) - `llrawproc.c:459-462`

LUT helpers `get_raw2ev` and `get_ev2raw` give 16-bit -> log2(EV) and back, and are rebuilt whenever black level changes (`llrawproc.c:230-237`, definitions in `pixelproc.c`).

### 5.1 Focus pixel removal

**Source**: `src/mlv/llrawproc/pixelproc.{c,h}` (function `fix_focus_pixels`); pixel maps live in `pixel_maps/<cameraId>_<rawW>x<rawH>.fpm` files at the repo root.

**Input**: 14-bit (post-upscale) raw buffer.
**Output**: same buffer with focus pixels overwritten by interpolated neighbours.

**What it does**: Canon DSLRs/MLs that use phase-detect focus dots (EOSM, EOSM2, 100D, 650D, 700D - codes `0x80000331`, `0x80000355`, `0x80000346`, `0x80000301`, `0x80000326`) leave systematic dark pixels at fixed sensor coordinates. The map is keyed by **camera model + raw frame width x raw frame height**, e.g. `pixel_maps/80000301_5280x2244.fpm` for the 650D at one of its modes. There are hundreds of `.fpm` files because every recording resolution has a different active-area crop and the focus-dot positions land on different absolute coordinates.

Each focus pixel is replaced via neighbour interpolation - either the MLVFS strategy (`fpi_method = FPI_MLVFS`) or the raw2dng strategy (`fpi_method = FPI_RAW2DNG`) (enum `FPI_MLVFS, FPI_RAW2DNG` at `llrawproc.h:51`).

**On/off semantics** (enum `FP_OFF, FP_ON, FP_CROPREC` at `llrawproc.h:47`):
- `FP_OFF` (0): skip
- `FP_ON` (1): use the standard map for this camera+resolution
- `FP_CROPREC` (2): force the crop_rec map; auto-detected via `llrpDetectFocusDotFixMode` from the RAWC binning/skipping fields (`llrawproc.c:480-505`)

When data is LJ92-compressed, `unified_mode = 5` is forced (`llrawproc.c:259`, `llrawproc.c:383`). After Dual-ISO 20-bit reconstruction, focus pixels are re-fixed against the reconstructed buffer (`llrawproc.c:378-400`).

**Map file format**: each `.fpm` is a plain-text list of `x y` coordinate pairs (one pair per line) in the active-area coordinate system of the recording. The map filename encodes the camera DIGIC PROP id (hex, lowercase, no `0x` prefix) and the raw frame width x height in pixels. Example: `pixel_maps/80000301_5280x3508.fpm` is the focus-pixel map for the 650D (`cameraModel == 0x80000301`) at 5280x3508. The loader matches against `video->IDNT.cameraModel` and the active-area dimensions from RAWI; if no map exists, the stage silently no-ops for that recording (status fields `fpm_status` < 3 gate retries; status reset is via `llrpResetFpmStatus` at `llrawproc.h:102`).

**Status state machine**: `fpm_status` increments after a load attempt. Once it reaches 3 (three failed loads), the stage stops trying for this clip until reset. This keeps the per-frame check cheap when a clip has no map. The same pattern applies to `bpm_status` (`llrawproc.c:279`, `llrawproc.h:103`).

### 5.2 Bad pixel removal

**Source**: `src/mlv/llrawproc/pixelproc.c` (function `fix_bad_pixels`).

**Input**: 14-bit raw buffer.
**Output**: same buffer with single dead/hot pixels interpolated.

**What it does**: maintains a list of single dead or stuck-on pixels per camera body (and per frame, when `BPS_FORCE` aggressive search is enabled). Each pixel is replaced by interpolation from its Bayer-cell neighbours.

**On/off semantics** (`llrawproc.h:55-65`):
- `BP_OFF` (0): skip
- `BP_ON` (1): use the cached map plus normal heuristic search (`BPS_NORMAL`)
- `FP_AGGRESSIVE` (2): also search every frame for new outliers (`BPS_FORCE`)
- `BPI_MLVFS` vs `BPI_RAW2DNG`: which interpolation routine

Like focus pixels, bad-pixel processing is repeated against the reconstructed buffer after Dual-ISO 20-bit (`llrawproc.c:402-422`).

### 5.3 Dark frame subtraction

**Source**: `src/mlv/llrawproc/darkframe.{c,h}`. Functions: `df_init`, `df_subtract`, `df_validate`.

**Input**: raw buffer, before bit-depth normalisation.
**Output**: per-pixel dark-current pedestal subtracted.

**Two modes** (`darkframe.h:34`):
- `DF_OFF` (0): skip
- `DF_EXT` (1): use an external reference dark `.MLV` whose path was set via `llrpInitDarkFrameExtFileName` (`llrawproc.c:730-734`). The external clip is opened via `openMlvClip` and its averaged frame data is held in `video->llrawproc->dark_frame_data` (size `dark_frame_size`).
- `DF_INT` (2): use the embedded DARK block from this clip - the in-camera averaged dark frame stored as a `mlv_dark_hdr_t` with `samplesAveraged` count (`mlv.h:302-327`).

`df_init` is called every frame; if it returns 0 (success) the subtraction runs, otherwise the stage is silently skipped (`llrawproc.c:206-215`). For external frames `df_validate` checks compatibility (resolution, bit depth, B/W levels) before allowing the path to be assigned.

For DNG bit-unpacking the helper `dng_unpack_image_bits` (declared at `darkframe.h:29`) handles 10/12/14-bit dark payloads uniformly.

### 5.4 Dual ISO

**Source**: `src/mlv/llrawproc/dualiso.{c,h}`.

**Algorithm**: the camera's `dual_iso` module records alternating ISO pairs (e.g. ISO 100 + ISO 800) on alternating row-pairs. The high-ISO rows recover shadow detail; the low-ISO rows recover highlight detail. Reconstruction interpolates between the two streams to produce a single 20-bit (effectively higher-DR) frame.

**Mode constants** (enum `DISO_OFF, DISO_20BIT, DISO_FAST` at `llrawproc.h:79`):
- `DISO_OFF` (0): skip Dual-ISO processing entirely; the alternating-line raw is left in the buffer (looks zebra-striped if previewed)
- `DISO_20BIT` (1): full reconstruction via `diso_get_full20bit` (`dualiso.h:29`, body at `dualiso.c:2085`). Output is 16-bit values representing 20-bit data (the `dng_bit_depth` is forced to 16 at `llrawproc.c:367-370`). This is the "Match Exposures" / HQ path - matches the "Match Exposures" toggle in the GUI.
- `DISO_FAST` (2): preview-only path via `diso_get_preview` (`dualiso.h:28`). Currently commented out in `applyLLRawProcObject` (`llrawproc.c:430-439`) - the call site exists but is disabled. There is also a third "always-on" semantic referenced in earlier docs (`mode 3`); in the current source the dispatch is just modes 1 vs 2.

**Match Exposures**: the 20-bit path internally calls `match_exposures` (`dualiso.c:963`, helper `_match_exposures` at `dualiso.c:627`) which does the histogram-matching between the alternating-line ISO pairs. The `dual_iso` toggle in the GUI selects whether this matching uses the **mid-line histogram** or the full **alternating-line** histogram - the `interp_method` argument to `diso_get_full20bit` (passed as `video->llrawproc->diso_averaging`, enum `DISOI_AMAZE / DISOI_MEAN23` at `llrawproc.h:83`) drives the decision.

**Validity gate**: Dual-ISO processing only runs when `diso_validity != 0` (`llrawproc.c:313`). Validity is computed in `llrpSetDualIsoValidity` (`llrawproc.c:658-705`):
- `DISO_FORCED`: user pressed "Force Dual ISO" - the second ISO is taken to equal the primary
- `DISO_VALID`: a DISO block exists with non-zero `dualMode` and the secondary ISO is parseable
- `DISO_INVALID`: no DISO block or zero `dualMode` - skip

After validity is set, `iso1` / `iso2` are clamped into `[100, 3200]` (`llrawproc.c:691`).

**Restricted lossless rescaling** (`llrawproc.c:323-346`): if the source is LJ92-compressed *and* `white_level < 15000`, the writer used a restricted bit range (typically 10-12 bits but stored in 14-bit cells). Before Dual-ISO reconstruction the buffer is rescaled to the full 14-bit range using `scale_restricted_range` (`llrawproc.c:117-143`), and the processing module's black/white levels are updated via `processingSetBlackAndWhiteLevel(... , 14)`.

**Auxiliary toggles** for the 20-bit path (passed through to `diso_get_full20bit`):
- `diso_alias_map` (`llrawproc.h:87`): enable the alias-map post-step
- `diso_frblending` (`llrawproc.h:90`, default 1): enable full-resolution blending
- `chroma_smooth_method` (folded in here so `chroma_smooth` is skipped in the outer pipeline at `llrawproc.c:443`)

After 20-bit reconstruction the LUTs are rebuilt against the new black level and then reverted (`llrawproc.c:373-376` and `424-427`) so the post-Dual-ISO bad/focus-pixel passes see the right ev2raw mapping.

**ISO determination** (`llrawproc.c:660-700`): when `DISO_FORCED`, both `iso1` and `iso2` are set to the primary `EXPO.isoValue`. When `DISO_VALID`, the secondary ISO is derived from `DISO.isoValue` with this encoding:
- `iso2 < -6`: `iso2 = iso1 / 2^(|iso2| - 6)` (negative offsets in stops down)
- `-6 <= iso2 < 0`: `iso2 = iso1 * 2^(7 + iso2)`
- `0 <= iso2 < 100`: `iso2 = iso1 * 2^iso2 / (iso1 / 100)`
- `iso2 >= 100`: literal ISO value
The result is clamped to `[100, 3200]`.

**Output bit depth**: the 20-bit reconstruction outputs into a 16-bit-cell buffer where the meaningful range is the upper 16 bits of a virtual 20-bit value. After this stage `dng_bit_depth = 16`, `dng_black_level = black << bits_shift`, `dng_white_level = white << bits_shift` (`llrawproc.c:367-370`). DNG export then writes 16-bit cDNG. The downstream undo-14bit step is **skipped** when 20-bit Dual-ISO is active (`llrawproc.c:459`) because the buffer no longer holds 14-bit values.

#### 5.4.1 20-bit reconstruction kernel

`diso_get_full20bit` (`dualiso.c:2678-2894`) is the body of the
"Match Exposures" path. The full sequence end-to-end:

**Step 1 — Identify which rows are bright vs dark**
(`identify_bright_and_dark_fields`, `dualiso.c:1050`). Returns
`is_bright[h]`: a per-row boolean. Together with the CFA-pattern
identification (`identify_rggb_or_gbrg`, `dualiso.c:990`) this fixes
the macros `BRIGHT_ROW` (`y` is a high-ISO row) and `DARK_ROW` (low-ISO).

**Step 2 — Convert 14-bit cells to 20-bit cells in-place**
(`convert_to_20bit`, `dualiso.c:1644-1659`). Each input pixel is
left-shifted by 4 (or stored in a separate `uint32_t* raw_buffer_32`
for the duration of reconstruction). Levels are stretched too:
`black20 = black << 4`, `white20 = white << 4`. Working in 20-bit
gives the blend math 4 extra bits of headroom for the bright-row
darkening multiply.

**Step 3 — Per-row exposure-match scalar derivation**
(`_match_exposures`, `dualiso.c:1200-1390`). This is the line-fit:

```c
/* Quick "interpolated" reconstruction: for each native pixel in a
 * dark row, use the average of the two bright pixels two rows above
 * and below as the matching bright value (and vice versa).         */
for (y = y0; y < h-2; y += 3)
  for (x = 0; x < w; x += 3) {
    int pa = pixel20to16(x, y-2) - black;       /* same parity row */
    int pb = pixel20to16(x, y+2) - black;
    int pn = pixel20to16(x, y) - black;
    int pi = (pa + pb + 1) / 2;                  /* interp. opposite */
    if (pa >= clip || pb >= clip) pi = clip0;    /* discard saturated */
    interp[x + y*w] = pi;                        /* "other ISO" estimate */
    native[x + y*w] = pn;                        /* this ISO actual    */
  }

/* Robust line-fit between (median_dark, median_bright) origins:
 * pick highlights between 98th and 99.9th percentile of the
 * non-saturated bright values and find the slope `a = 2^-ev` that
 * maximises the count of (d, b) points satisfying |d - (a*b + b0)| < 50.
 */
double a = 0, b = 0;
int best_score = 0;
for (double ev = 0; ev < 6; ev += 0.002) {
    double test_a = pow(2, -ev);
    double test_b = dmed - bmed * test_a;
    int score = 0;
    for (i = 0; i < hi_n; i++) {
        int e = hi_dark[i] - (hi_bright[i]*test_a + test_b);
        if (ABS(e) < 50) score++;                /* RANSAC-style       */
    }
    if (score > best_score) { best_score = score; a = test_a; b = test_b; }
}

/* Apply the correction (in 20-bit space; b20 = b * 16): */
for (y = 0; y < h; y++)
  for (x = 0; x < w; x++) {
      int p = pixel32(x, y);
      if (BRIGHT_ROW) p = (p - black20)*a + black20 + b20*a;  /* darken */
      else            p = p - b20 + b20*a;                    /* offset */
      set_pixel20(x, y, p);
  }

corr_ev = log2(1/a);                              /* return ISO delta */
```

The slope `a` is the linear correction factor that brings high-ISO
rows down to low-ISO scale (so `1/a = 2^ev_diff`), and `b` is the
black-level offset between the two streams. After this step both ISO
streams share a common scale and pedestal.

**Step 4 — Per-stream interpolation (AMaZE-half-image or mean23)**
(`amaze_interpolate` at `dualiso.c:1787` or `mean23_interpolate` at
`dualiso.c:2091`). Either path produces two full-resolution buffers
`dark[]` (low-ISO with bright rows interpolated) and `bright[]`
(high-ISO with dark rows interpolated):

```c
/* AMaZE-half-image (interp_method = DISOI_AMAZE = 0):
 * Pack only the rows of each ISO stream into a half-height buffer.
 * Greens are pre-divided by 2 to approximate the eventual WB
 * (improves AMaZE's behaviour because it expects RGGB green ratios
 *  near 1:1):                                                       */
for (y = 0; y < h; y++) {
    if (BRIGHT_ROW != want_bright) continue;
    for (x = 0; x < w; x++) {
        int p = pixel32(x, y);
        if (x%2 != y%2) p = (p - black)/2 + black;   /* halve greens */
        rawData[squeezed_y][x] = p;
    }
    squeezed[y] = squeezed_y++;
}
/* Run AMaZE on the squeezed half-height buffer (multithreaded;
 * pthread fan-out into chunks of >= 32 rows). */
demosaic(&amazeinfo);

/* mean23_interpolate (interp_method = DISOI_MEAN23 = 1):
 * Cheaper substitute - average-of-2 vertical (rows) and average-of-3
 * horizontal (cols) per missing CFA position. Used when AMaZE is
 * blacklisted at this resolution.                                   */
```

**Step 5 — Border interpolation, full-res reconstruction, alias map**
(`border_interpolate`, `fullres_reconstruction`, `build_alias_map` at
`dualiso.c:2166`, `:2215`, `:2244`). The first fills the top/bottom 2
rows that AMaZE skipped; the second produces a "use-only-the-stream-
that's-not-clipped" full-resolution map; the third builds an
edge/aliasing weight `alias_map[]` from the difference between
chroma-smoothed half-res and full-res.

**Step 6 — Half-res blend (`mix_images`, `dualiso.c:2408-2562`)**.
For each pixel a cosine-shaped mixing curve in EV space picks the
weighting between the two streams:

```c
/* Mixing curve: smooth cosine fade between dark-only and bright-only
 * across the `overlap` EV stops where both ISOs have valid signal.   */
double max_ev  = log2(white/64 - black/64);
double overlap = lowiso_dr - corr_ev;            /* DR shared by both */
overlap -= MIN(3, overlap - 3);                   /* underestimate     */

for (i = 0; i < (1<<20); i++) {
    double ev = log2(MAX(i/64.0 - black/64.0, 1)) + corr_ev;
    double c  = -cos(MAX(MIN(ev - (max_ev - overlap), overlap), 0)
                     * M_PI / overlap);
    mix_curve[i] = (c + 1) / 2;                   /* 0..1: 0=bright,1=dark */
}

/* Per-pixel blend in EV space: */
for (y = 0; y < h; y++)
  for (x = 0; x < w; x++) {
      int b = bright[x + y*w];
      int d = dark  [x + y*w];
      int bev = raw2ev[b];
      int dev = raw2ev[d];
      double k = COERCE(mix_curve[b & 0xFFFFF], 0, 1);
      int mixed = bev * (1-k) + dev * k;
      halfres[x + y*w] = ev2raw[mixed];           /* back to linear */
  }
```

The blend is parameterised by the **bright** pixel's value (not a
threshold) — so dark midtones (`b` small ⇒ `k ≈ 1` ⇒ use dark stream
which has the better SNR there) and bright highlights (`b` near
`white_darkened` ⇒ `k ≈ 0` ⇒ use bright stream which has not clipped)
both come from their respective best-DR source.

**Step 7 — Final blend (`final_blend`, `dualiso.c:2564-2661`)**.
Combines half-res, full-res, full-res-smooth, alias-map, and an
overexposed mask into the per-pixel output:

```c
double f = fullres_curve[bright[x+y*w] & 0xFFFFF];   /* full-res weight */
double c = alias_map ? alias_map[x+y*w]/ALIAS_MAP_MAX : 0;
double ovf = overexposed[x+y*w] / 200.0;
c = MAX(c, ovf);
f = MAX(f, c);                                       /* edges/aliases */
double n = MAX(ovf, 1-f);                            /* noisy/over   */
double fev = n * frsev + (1-n) * frev;               /* smooth or raw */

/* limit fullres in dark areas to avoid black spots */
int sig = (dark[x+y*w] + bright[x+y*w]) / 2;
f = MAX(0, MIN(f, (double)(sig - black) / (4*dark_noise)));

output = hrev * (1-f) + fev * f;                     /* halfres + fullres */
output = COERCE(output, -10*EV_RESOLUTION, 14*EV_RESOLUTION-1);
raw_set_pixel32(x, y, ev2raw[output]);
```

**Step 8 — Convert 20-bit cells back to 16-bit storage**
(`convert_20_to_16bit`, `dualiso.c:2663-2676`). The buffer is
right-shifted by 4 with dithering (`raw_set_pixel_20to16_rand`).
Black/white levels are also divided by 16 so the downstream pipeline
sees a 16-bit DNG with the correct effective range. The 20-bit
reconstruction is now committed; subsequent stages (chroma smooth,
pattern noise, debayer) all operate on the 16-bit-stored,
20-bit-effective buffer.

**Effective range**: the pre-shift buffer holds values in
`[black20, white20] ⊂ [0, 1<<20]`. After step 8 they are stored in
`[black, white]/16 ⊂ [0, 65535]` but the *information* still
discriminates 1<<20 = 1.05M levels because the underlying interpolation
was done at full 20-bit precision. The dithering in
`raw_set_pixel_20to16_rand` prevents banding at the storage step.

### 5.5 Vertical stripes

**Source**: `src/mlv/llrawproc/stripes.{c,h}`. Function: `fix_vertical_stripes` (`stripes.h:35-43`).

**Input**: 14-bit raw, black/white levels.
**Output**: column-mean-corrected raw.

**What it does**: Canon's column ADCs have small per-column DC offsets. Stripes correction computes the mean of each of the 8 column phases (relative to the Bayer pattern) over the dark portion of the frame, derives 8 multiplicative coefficients, and applies them per pixel. The coefficients are cached in:

```c
typedef struct {
    int correction_needed;
    int coefficients[8];
} stripes_correction;                      /* stripes.h:30-33 */
```

`compute_stripes` is a one-shot flag: when set, the next call recomputes coefficients; subsequent frames reuse them (`llrawproc.c:240-251`).

**On/off** (enum `VS_OFF, VS_ON, VS_FORCE` at `llrawproc.h:42`):
- `VS_OFF` (0): skip
- `VS_ON` (1): apply only when `correction_needed == 1` (heuristic detected stripes)
- `VS_FORCE` (2): always apply, even if heuristic says no

The `compute_stripes` flag is reset by `llrpComputeStripesOn` (`llrawproc.h:45`, `llrawproc.c:528-531`) - typically called when the user changes a control that could affect calibration (B/W levels, ISO selection). Subsequent frames reuse the same 8 coefficients which keeps the per-frame cost to one multiply-add per pixel.

The `coefficients[8]` indexing follows the 8-pixel column phase (4 columns x 2 Bayer rows = 8 distinct phases). For the standard RGGB pattern: phases 0,1 = R/G of even rows, phases 2,3 = G/B of odd rows, repeated; the column-phase offset determines which of the 8 coefficients applies to a given `(x,y)`.

### 5.6 Chroma smoothing

**Source**: `src/mlv/llrawproc/chroma_smooth.c`. Three kernel sizes are generated from the same source via the macros `CHROMA_SMOOTH_2X2`, `CHROMA_SMOOTH_3X3`, and the default 5x5 (`chroma_smooth.c:1-16`):

```c
#ifdef CHROMA_SMOOTH_2X2
#define CHROMA_SMOOTH_FUNC chroma_smooth_2x2
#define CHROMA_SMOOTH_MAX_XY_IJ 2
#define CHROMA_SMOOTH_FILTER_SIZE 5
#define CHROMA_SMOOTH_MEDIAN opt_med5
#elif defined(CHROMA_SMOOTH_3X3)
#define CHROMA_SMOOTH_FUNC chroma_smooth_3x3
#define CHROMA_SMOOTH_MAX_XY_IJ 2
#define CHROMA_SMOOTH_FILTER_SIZE 9
#define CHROMA_SMOOTH_MEDIAN opt_med9
#else
#define CHROMA_SMOOTH_FUNC chroma_smooth_5x5
#define CHROMA_SMOOTH_MAX_XY_IJ 4
#define CHROMA_SMOOTH_FILTER_SIZE 25
#define CHROMA_SMOOTH_MEDIAN opt_med25
#endif
```

**Algorithm** (`chroma_smooth.c:22-`): for each red pixel, compute the median of `red - interpolated_green` across the kernel (5, 9, or 25 samples) - that median is the "true" red-vs-green difference. Same for blue. Interpolation runs in **EV space** (via `raw2ev` / `ev2raw`) which suppresses colour artefacts in high-contrast regions. The interpolation direction (horizontal mean of left/right vs vertical mean of top/bottom) is chosen per-region by minimising `sum(abs(t-b))` vs `sum(abs(l-r))`.

**Kernel selection** (enum `CS_OFF, CS_2x2, CS_3x3, CS_5x5` at `llrawproc.h:67`):
- `CS_OFF` (0): skip
- `CS_2x2` (1): tightest filter, minimal blur
- `CS_3x3` (2): default for many setups
- `CS_5x5` (3): heavy smoothing, used when fixed-pattern noise is dominant

Dispatch is by simple `if/else` on the integer value (`pixelproc.c:149-155`, also called inside the Dual-ISO 20-bit path at `dualiso.c:2391-2397`).

**Suppression**: chroma smoothing is **not** run in the outer pipeline when 20-bit Dual-ISO is active because the reconstruction already performs an internal smooth (`llrawproc.c:443`).

### 5.7 Pattern noise

**Source**: `src/mlv/llrawproc/patternnoise.{c,h}`. Function: `fix_pattern_noise(int16_t *raw, int w, int h, int white, int debug_flags)` (`patternnoise.h:16`).

**Input**: signed 16-bit raw buffer (note: signed; the function accepts `int16_t*`), width/height, white level, debug flags.
**Output**: same buffer with row-pattern and column-pattern noise subtracted.

**What it does** (algorithm comment at `patternnoise.h:1-12`): the CMV12000 sensor (and similar) produce a per-row and per-column scalar offset that varies frame-to-frame. The fix estimates that offset from low-contrast regions of the image (where pattern noise stands out against the underlying flat content) and subtracts it. A dark frame helps but does not eliminate the effect because the pattern is not constant.

**Debug flag bits** (`patternnoise.h:18-24`):

| Flag | Value | Effect |
| --- | --- | --- |
| `FIXPN_DBG_COLNOISE` | 0 | (default channel selector for column noise) |
| `FIXPN_DBG_ROWNOISE` | 1 | switch debug output to row noise |
| `FIXPN_DBG_DENOISED` | 2 | output the denoised image |
| `FIXPN_DBG_NOISE` | 4 | output the extracted noise pattern |
| `FIXPN_DBG_MASK` | 8 | output the low-contrast mask |

In production the call uses `debug_flags = 0` (`llrawproc.c:306`).

**On/off semantics** (enum `PN_OFF, PN_ON` at `llrawproc.h:71`):
- `PN_OFF` (0): skip
- `PN_ON` (1): apply

**Suppression**: pattern-noise correction is suppressed when valid Dual-ISO is being processed (`llrawproc.c:301`: `if (!video->llrawproc->diso_validity && video->llrawproc->pattern_noise)`). This is because the alternating-line ISO structure of Dual-ISO confuses the row-pattern estimator - rows of opposite ISO have a real luma offset that the estimator would interpret as noise and incorrectly subtract.

#### 5.7.1 Estimator algorithm

The full pipeline (`fix_pattern_noise`, `patternnoise.c:443-487`) is
**columns-first, then rows via transpose-then-columns-again** — there
is no per-row code path; the same column estimator is reused after
swapping width and height:

```c
void fix_pattern_noise(int16_t *raw, int w, int h, int white, ...) {
    /* 1. Fix vertical (column) noise on the original raw  */
    fix_column_noise_rggb(raw, w, h, white, scratch);

    /* 2. Transpose, fix "column" noise of the transposed frame
     *    (which is the same as the original's row noise),
     *    then transpose back.                                       */
    transpose(raw, raw_t, w, h);
    fix_column_noise_rggb(raw_t, h, w, white, scratch);
    transpose(raw_t, raw, h, w);
}
```

Within each direction the algorithm splits the Bayer mosaic into four
half-resolution planes (R, G1, G2, B; `extract_channel`,
`patternnoise.c:380-390`) and processes each plane independently,
recombining at the end (`set_channel`, `:395-405`). Per plane:

**Step A — horizontal edge-aware blur**
(`horizontal_edge_aware_blur_rggb`, `patternnoise.c:182-274`). For
each pixel, walk left and right while neighbours stay within
`thr = 500` of the centre's average-green (capped at `strength = 50`
samples each side). Take the **median** of `R-G`, `B-G`, `G1`, `G2`
in that flat window. The output is a strongly horizontally smoothed
copy `denoised[]` that preserves edges:

```c
average(in_g1, in_g2, avg_g, w, h);          /* G average plane     */
subtract(in_r, avg_g, dif_rg, w, h);          /* R - G chroma plane  */
subtract(in_b, avg_g, dif_bg, w, h);          /* B - G chroma plane  */

for (y = 0; y < h; y++)
  for (x = 0; x < w; x++) {
      int p0 = avg_g[x + y*w];
      int xl = x-1, xr = x+1;
      while (xr < x+strength && |avg_g[xr+y*w] - p0| <= thr) xr++;
      while (xl > x-strength && |avg_g[xl+y*w] - p0| <= thr) xl--;
      int n = xr - xl - 1;
      out_g1[i] = median(g1[xl+1..xr-1]);
      out_g2[i] = median(g2[xl+1..xr-1]);
      out_r[i]  = median(rg[xl+1..xr-1]) + (out_g1[i] + out_g2[i])/2;
      out_b[i]  = median(bg[xl+1..xr-1]) + (out_g1[i] + out_g2[i])/2;
  }
```

The blur is computed in chroma space (`R-G`, `B-G`) so that flat
patches of differing hue but similar luma still fuse cleanly.

**Step B — derive per-column offset**
(`fix_column_noise`, `patternnoise.c:279-375`):

```c
/* The "noise" is whatever the blur removed.                          */
subtract(original, denoised, noise, w, h);

/* Mask out edges (high horizontal gradient) and bright pixels.       */
horizontal_gradient(original, hgrad, w, h);    /* hgrad[i] = in[i-2] - in[i+2] */
for (y = 0; y < h; y++)
  for (x = 0; x < w; x++)
    mask[i] = (|hgrad[i]| > 500) || (original[i] >= white);

/* Per-column offset = median of unmasked noise samples in that col.  */
for (x = 0; x < w; x++) {
    int n = 0;
    for (y = 0; y < h; y++)
        if (!mask[x + y*w]) noise_row[n++] = noise[x + y*w];
    col_offsets[x] = (n < 10) ? 0 : -median_int_wirth(noise_row, n);
}

/* Apply, then subtract the median-of-offsets to prevent global cast. */
for (i = 0; i < w*h; i++)
    original[i] = COERCE(original[i] + col_offsets[col(i)], -32767, 32767);

int mc = median_int_wirth(col_offsets, w);
for (i = 0; i < w*h; i++)
    original[i] = COERCE(original[i] - mc, 0, 32760);
```

**Estimator summary**:

- **Mask construction**: a pixel qualifies for offset estimation if
  (a) `|horizontal_gradient| <= 500` (i.e. not on a strong edge —
  so flat patches are kept and edge-text is rejected) **and**
  (b) `pixel < white` (so saturated pixels do not bias the median).
  Setting the white level too low (e.g. with the `white = 0` debug
  call) collapses the mask and the algorithm is no-op safe.
- **Statistic**: per-column **median** (`median_int_wirth`, the
  Wirth-algorithm linear-time selector). Median over mean is chosen
  because the mask cannot eliminate every edge pixel; one tail-of-
  distribution outlier would shift a mean badly but barely moves a
  median.
- **Threshold**: a column with fewer than 10 unmasked samples is
  given offset 0 (skipped) — the noise estimate is too noisy itself.
- **Final de-bias**: subtract the median of all column offsets from
  the image to keep the global tone unchanged. Only the *pattern*
  is removed; the *level* is preserved.
- **Order**: columns first on the original buffer; rows second via
  the transpose trick. Both passes use the same kernel; they are not
  interleaved.

**Worked numeric example** (a 5-column slice of a single Bayer plane,
say the R plane after extraction; values in 16-bit signed):

```
original:                12100  12095  12102  12098  12099
denoised (after blur):   12099  12099  12099  12099  12099
noise (orig - denoised):     1     -4      3     -1      0
mask (edges/white):          0      0      0      0      0   (all good)
```

For each column, the median of unmasked `noise` values down the column
(here we have only one row, so just the value itself):

```
col_offsets[]:  -median(noise[col])  =>  -1   4   -3   1   0
```

Apply the offset — column-2 gets `+4` (raising it from 12095 to
12099):

```
after add offset:        12099  12099  12099  12099  12099
```

Now the median of `col_offsets[] = {-1, 4, -3, 1, 0}` is `0`, so the
final de-bias subtracts 0 and the result stays at 12099. If the slice
had been `{12100, 12099, 12102, 12101, 12100}`, the offsets would
have been `{-1, 0, -3, -2, -1}` with median `-1`, and the final result
would be `12100` — the column-noise is removed but the global mean is
preserved.

Real-world inputs of course have many rows, so the per-column median
runs over hundreds or thousands of samples, and the mask removes the
ones that lie on edges. The estimator is intentionally simple and
robust; the cost is borne by the median-of-window blur (Step A),
which is why the function comment notes *"this step takes a lot of
time"*.

**Stage interaction summary** (post-LLRAWPROC buffer state):

| Branch taken | Buffer bit depth | DNG B/W levels | Chroma smoothing |
| --- | --- | --- | --- |
| `fix_raw == 0` | original | original | unchanged |
| Plain pipeline | original (10/12/14-bit downscaled if input < 14) | original | applied per `chroma_smooth` enum |
| Dual-ISO 20-bit | 16 (cells), 20 (effective) | shifted to 16-bit range | applied internally inside `diso_get_full20bit`; outer skip |
| Restricted-lossless + Dual-ISO 20-bit | 16 (cells), 20 (effective) | rescaled to 14-bit then shifted to 16-bit | as above |

## 6. The 9-Stage Processing Pipeline

The CPU processing pipeline is implemented across two C functions in
`src/processing/raw_processing.c`:

* `applyProcessingObject()` (`src/processing/raw_processing.c:507`) is the
  outer multithreaded driver. It performs setup, optional shadow/highlight
  blur prep, optional dual-ISO highest-green analysis, the main per-row
  pipeline (single thread or pthread fan-out), and three image-wide
  post-stages: 2D median denoise, recursive bilateral denoise, CA/colour
  moiree, sharpen + grain.
* `apply_processing_object()` (`src/processing/raw_processing.c:1079`) is
  the inner per-pixel kernel that runs the colour/levels/creative chain on
  one chunk of rows. It is invoked once per worker thread.

Together they implement the conceptual 9 stages below. The actual order
in source matches the documented order with two refinements: (1)
shadows/highlights pre-blurring is its own preparation pass before the
main loop, not a stand-alone post-stage, and (2) sharpen, denoise and
grain are applied at the image level after the per-pixel chain returns.

The full sequence (source-grounded, in execution order):

| # | Stage | Where applied | Source anchor |
|---|---|---|---|
| 1 | Linear / raw ingest (16-bit get_frame_transformed) | outer | `raw_processing.c:517` |
| 2 | Black/white level + exposure compensation (precalc LUT) | inner | `raw_processing.c:1124-1127` |
| 3 | White balance (3x3) + tint application | inner | `raw_processing.c:1136-1191` (fast path), `1263-1406` (general) |
| 4 | Saturation + vibrance + hue rotation (toning) | inner | `raw_processing.c:1547-1631` |
| 5 | Highlight/shadow + dark/light recovery (clarity, contrast curve) | inner pre-pass | `raw_processing.c:1209-1261` (expo-correction); pre-blur `raw_processing.c:528-561` |
| 6 | Gradation curve (Y mode + per-channel R/G/B) | inner | `raw_processing.c:1644-1656` |
| 7 | Hue-vs-* + Lum-vs-Sat curves (4 canvases) | inner | `raw_processing.c:1488-1545` |
| 8 | Output gamma + Profile Preset (transfer + tonemap + gamut) | inner | `raw_processing.c:1408-1412` (gamma LUT); profile table `image_profiles.c:1-95` |
| 9 | Sharpen + denoise + grain (+ optional LUT/filter) | outer | denoise `raw_processing.c:648-690`; CA `692-701`; sharpen+grain `707-879`; LUT/filter `1678-1686` |

Two data-driven fast paths short-circuit the general path when the
receipt has no creative adjustments and no recovery sliders:

* `processing_can_use_basic_matrix_fast_path()`
  (`raw_processing.c:923`) — fused matrix + tonemap + gamma loop at
  `raw_processing.c:1136-1191`.
* `processing_can_use_direct_8bit_output()` /
  `apply_processing_object_8bit_fast()` (`raw_processing.c:934`,
  `1063`, kernel body in `raw_processing_8bit_kernel.inc`) — emits 8-bit
  RGB directly for preview when no creative or denoise stages are
  active. Runtime AVX2/FMA dispatch is handled in
  `apply_processing_object_8bit_fast_rows_dispatch_init()`
  (`raw_processing.c:1015`).

### Stage 1 — Linear / raw ingest

Raw uint16 data is consumed in-place from `inputImage` after
`get_frame_transformed()` applies orientation/stretch transformations.
Setup timing is captured into `g_processing_last_setup_ms`
(`raw_processing.c:515-525`).

```c
/* raw_processing.c:515-525 */
const double setup_start = omp_get_wtime();
get_frame_transformed(processing, inputImage, imageX, imageY);
int img_s = imageX * imageY * 3;
/* derive deterministic per-frame random seeds for grain */
uint32_t randomseed1 = ((uint32_t *)inputImage)[0] ^ ((uint32_t *)(inputImage+img_s))[-1] ^ frameIndex;
/* ... */
g_processing_last_setup_ms = (omp_get_wtime() - setup_start) * 1000.0;
```

* Inputs: `uint16_t * inputImage`, `imageX`, `imageY`, `frameIndex`.
* Outputs: orientation-correct `inputImage`, four random seeds for the
  grain stage.
* Key params: `processingObject_t::transformation` (orientation),
  `processingObject_t::shadows_highlights.blur_image` (resized here when
  `imageChanged`).

### Stage 2 — Black/white level + exposure (precalculated LUT)

The first per-pixel transform applies a single uint16->uint16 LUT
populated by the level/exposure precompute in
`processing_update_curves()` (`processing.c:384`). This is the loop body
at `raw_processing.c:1122-1132`.

```c
/* raw_processing.c:1124-1127 */
for (int i = 0; i < img_s; ++i)
{
    /* Black + white level */
    img[i] = processing->pre_calc_levels[ img[i] ];
}
```

* Key params: `processing->pre_calc_levels[65536]` (fed by
  `processingSetExposureStops`, `processingSetBlackLevel`,
  `processingSetWhiteLevel`).
* Inputs: 16-bit raw RGB (interleaved post-debayer).
* Outputs: 16-bit, levels-normalised pixels in place.

### Stage 3 — White balance + tint

Two implementations exist. The general path
(`raw_processing.c:1263-1406`) computes a 3x3 multiplication
`proper_wb_matrix` per pixel, performs Reinhard-style chroma desat to
keep colours in gamut, then optionally applies the AgX compression
matrix. The fast path (`raw_processing.c:1136-1191`) hoists the matrix
constants into local floats and fuses WB, tonemap and gamma in one
sweep.

#### 6.3.1 Kelvin -> RGB multipliers

MLV App does **not** evaluate Planck's law per-call. Instead it
linearly interpolates a 17-row table of empirically-measured 5D Mark II
EXIF white-balance values (`src/processing/processing.c:18-22`) — the
comment says *"Measurements taken from 5D Mark II RAW photos using
EXIFtool, surely Canon can't be wrong about WB multipliers?"*:

```c
/* processing.c:19-22 (verbatim) */
static const int    wb_kelvin[] = {  2000,  2500,  3000,  3506,  4000,
    4503,  5011,  5517,  6018,  6509,  7040,  7528,  8056,  8534,  9032,
    9531, 10000 };
static const double wb_red[]    = { 1.134, 1.349, 1.596, 1.731, 1.806,
    1.954, 2.081, 2.197, 2.291, 2.365, 2.444, 2.485, 2.528, 2.566,
    2.612, 2.660, 2.702 };
static const double wb_green[]  = { 1.155, 1.137, 1.112, 1.056, 1.000,
    1.000, 1.000, 1.000, 1.000, 1.000, 1.000, 1.000, 1.000, 1.000,
    1.000, 1.000, 1.000 };
static const double wb_blue[]   = { 4.587, 3.985, 3.184, 2.524, 2.103,
    1.903, 1.760, 1.641, 1.542, 1.476, 1.414, 1.390, 1.363, 1.333,
    1.296, 1.263, 1.229 };
```

The lookup function `get_kelvin_multipliers_rgb`
(`processing.c:217-251`) clamps the requested temperature to
`[2000, 10000]`, finds the bracket `wb_kelvin[k] <= K < wb_kelvin[k+1]`,
and returns a linearly-interpolated `(r, g, b)` triple:

```c
/* processing.c:241-250 */
double diff1 = wb_kelvin[k+1] - wb_kelvin[k];
double diff2 = wb_kelvin[k+1] - kelvin;
double w1 = diff2 / diff1;
double w2 = 1.0 - w1;
multiplier_output[0] = w1*wb_red[k]   + w2*wb_red[k+1];
multiplier_output[1] = w1*wb_green[k] + w2*wb_green[k+1];
multiplier_output[2] = w1*wb_blue[k]  + w2*wb_blue[k+1];
```

A more rigorous Planck-locus + Daylight-D-curve transform is present
but **commented out** (`processing.c:2024-2033`); the supporting code
(`Planck_law`, `BlackBody_to_XYZ` with a 8001-row 1K-step blackbody
LUT, `Kelvin_Daylight_to_XYZ`) lives in `src/processing/white_balance.c`
and is built but not wired in by default.

**Tint application** (`raw_processing.c:2010-2046`): tint is non-linear
in the slider value so that small near-zero adjustments are fine and
extremes ramp up:

```c
/* raw_processing.c:2012-2017 - non-linear tint reshape */
int is_negative = (WBTint < 0.0);
if (is_negative) WBTint = -WBTint;
WBTint /= 10.0;
WBTint  = pow(WBTint, 1.75) * 10.0;
if (is_negative) WBTint = -WBTint;

/* raw_processing.c:2035-2046 - apply tint and re-normalise */
get_kelvin_multipliers_rgb(WBKelvin, processing->wb_multipliers);
processing->wb_multipliers[2] += (WBTint / 11.0);   /* B channel  */
processing->wb_multipliers[0] += (WBTint / 19.0);   /* R channel  */

/* Make all channel multipliers >= 1 (divide by lowest) */
double lowest = MIN(MIN(wb_multipliers[0], wb_multipliers[1]),
                    wb_multipliers[2]);
for (int i = 0; i < 3; ++i) wb_multipliers[i] /= lowest;
```

Note the asymmetry: positive tint pushes both blue (`/11`) and red
(`/19`) up, which is mathematically equivalent to subtracting green —
the magenta direction. Negative tint subtracts blue + red (the green
direction). The denominators are tuned so the slider feels balanced
under the Canon empirical multipliers above.

**Where the multipliers are applied**: in `processing_update_matrices`
(`processing.c:434-508`) the three multipliers scale the R/G/B *rows*
of the working matrix:

```c
/* processing.c:455-457 */
for (int i = 0; i < 3; ++i) temp_matrix_b[i] *= wb_multipliers[0];  /* R row */
for (int i = 3; i < 6; ++i) temp_matrix_b[i] *= wb_multipliers[1];  /* G row */
for (int i = 6; i < 9; ++i) temp_matrix_b[i] *= wb_multipliers[2];  /* B row */
```

That `temp_matrix_b` is composed *into* the per-pixel `pre_calc_matrix`
LUT used at the top of the inner kernel
(`raw_processing.c:1264-1266`), so the WB scale is applied **in
camera-RGB space**, *before* the per-pixel cam-matrix multiply that
takes the pixel into the working RGB gamut. The `proper_wb_matrix`
quoted earlier in this section (`raw_processing.c:1357-1359`) is then a
*second* pass that re-undoes the basic WB and applies the chromatic
adaptation to the destination gamut — see the `undo_basic_wb_matrix`
construction at `raw_processing.c:2055-2080`.

CIECAM02 chromatic adaptation (`processing.c:33-37`,
`processing.c:46-51`) is defined but **commented out** in the matrix
composition (`processing.c:450, 460-462`); the live path uses the
identity for both XYZ↔Cone-space steps. This is a deliberate
reduce-to-known-good decision documented in the surrounding comments
(*"No ciecam for now"*).



```c
/* raw_processing.c:1264-1266 — general WB */
float pix0 = (pm[0][pix[0]]) * expo_correction;
float pix1 = (pm[4][pix[1]]) * expo_correction;
float pix2 = (pm[8][pix[2]]) * expo_correction;

/* raw_processing.c:1357-1359 — proper_wb_matrix multiply */
result[0] = pix0b * proper_wb_matrix[0] + pix1b * proper_wb_matrix[1] + pix2b * proper_wb_matrix[2];
result[1] = pix0b * proper_wb_matrix[3] + pix1b * proper_wb_matrix[4] + pix2b * proper_wb_matrix[5];
result[2] = pix0b * proper_wb_matrix[6] + pix1b * proper_wb_matrix[7] + pix2b * proper_wb_matrix[8];
```

* Inputs: per-pixel uint16, `pre_calc_matrix[][]` (camera-RGB to
  working-space LUT), `proper_wb_matrix[9]` (white balance + cam matrix
  composed in `processing_update_matrices()` at `processing.c:434`).
* Outputs: working-space float per pixel (clamped via `LIMIT16`).
* Key params: `processing->wb_kelvin`, `processing->wb_tint`,
  `processing->use_cam_matrix`, `processing->exr_mode`,
  `processing->AgX`.

### Stage 4 — Saturation + vibrance + hue rotation (toning)

```c
/* raw_processing.c:1549-1595 — vibrance */
int32_t Y1 = ((pix[0] << 2) + (pix[1] * 11) + pix[2]) >> 4;
int32_t Y2 = Y1 - 65536;
int32_t pix0 = processing->pre_calc_vibrance[pix[0] - Y2] + Y1;
/* ... weighted by raw saturation ... */

/* raw_processing.c:1597-1615 — saturation */
int32_t pix0 = processing->pre_calc_sat[pix[0] - Y2] + Y1;

/* raw_processing.c:1621-1631 — toning (hue rotation as wet/dry blend) */
for (int i = 0; i < 3; i++)
{
    pix[i] = pix[i] * processing->toning_dry + pix[i] * processing->toning_wet[i];
}
```

* Inputs: gamma-corrected uint16 RGB.
* Outputs: same buffer in place.
* Key params: `processing->vibrance`, `processing->saturation`,
  `processing->toning_dry`, `processing->toning_wet[3]`.
* Gating: all three loops are wrapped in
  `if (processing->allow_creative_adjustments)`. PROFILE_*_LOG and
  PROFILE_LINEAR set `allow_creative_adjustments = 0`
  (`image_profiles.c:26,33,40,47,54,61,68,75,82,89`), which bypasses the
  whole creative chain.

### Stage 5 — Highlight/shadow recovery + clarity (recovery curves)

The recovery is split across two locations: a blurring preparation pass
in the outer driver, and an expo-correction lookup inside the per-pixel
loop.

```c
/* raw_processing.c:534-560 — blur preparation (only when active) */
int blur_radius = (int)(((sqrt(pow(imageX,2.0)+pow(imageY,2.0)) / 440.0 - 1.0)/2 + 0.5)*4.0);
recursive_bf_wrap(inputImage, get_buffer(processing->shadows_highlights.blur_image),
                  0.0005f, 0.075f+(((float)100.0-40.0f)/666.6f), imageX, imageY, 3);

/* raw_processing.c:1230-1232 — shadow/highlight curve lookup */
expo_correction *= processing->shadows_highlights.shadow_highlight_curve[LIMIT16(bval)];

/* raw_processing.c:1244-1248 — clarity curve, both sides */
double factor = processing->clarity_curve[LIMIT16(cval)];
expo_correction *= factor * factor;
```

* Inputs: pre-blurred copy of `inputImage` for shadow/highlight masking;
  per-pixel luma proxy `bval`/`cval` derived from the matrix LUT
  (raw_processing.c:1217-1219, 1240-1242).
* Outputs: a multiplicative `expo_correction` and a parallel
  `expo_correction_gradient` consumed by the WB/exposure step.
* Key params: `processing->shadows_highlights.shadows`,
  `.highlights`, `.shadow_highlight_curve[65536]`,
  `processing->clarity`, `.clarity_curve[65536]`,
  `processing->contrast`, `.contrast_curve[65536]`,
  `processing->gradient_contrast`,
  `.gradient_contrast_curve[65536]`.

### Stage 6 — Gradation curve (Y + per-channel R/G/B)

```c
/* raw_processing.c:1644-1656 */
for (uint16_t * pix = img; pix < img_end; pix += 3)
{
    pix[0] = processing->gcurve_y[ pix[0] ];
    pix[1] = processing->gcurve_y[ pix[1] ];
    pix[2] = processing->gcurve_y[ pix[2] ];
    pix[0] = processing->gcurve_r[ pix[0] ];
    pix[1] = processing->gcurve_g[ pix[1] ];
    pix[2] = processing->gcurve_b[ pix[2] ];
}
```

The gradation editor's master curve is applied to all three channels
through the Y LUT, then per-channel R/G/B trims layer on top. There is
also a separate "Contrast curve" (`pre_calc_curve_r`) applied just
before gradation at `raw_processing.c:1633-1642` that stages the curve
in a single LUT for performance.

* Inputs: gamma-applied uint16 RGB.
* Outputs: same buffer in place.
* Key params: `processing->gcurve_y[65536]`,
  `processing->gcurve_r[65536]`, `.gcurve_g`, `.gcurve_b`,
  `processing->pre_calc_curve_r[65536]`.

### Stage 7 — Hue-vs-* / Lum-vs-Sat curves (4 canvases)

```c
/* raw_processing.c:1488-1545 */
fromRGBtoHSV(rgb, hsl);
/* ... raw saturation proxy ... */
uint16_t hue = (uint16_t)(hsl[0] * 100.0);
hsl[2] *= 1.0 + (processing->hue_vs_luma[hue]       * sat * 2);  /* Hue-vs-Lum   */
hsl[1] *= 1.0 + (processing->hue_vs_saturation[hue] *       2);  /* Hue-vs-Sat   */
hsl[0] +=  60 * (processing->hue_vs_hue[hue]);                   /* Hue-vs-Hue   */
uint16_t luma = (uint16_t)((hsl[2]) * 36000.0);
hsl[1] *= 1.0 + (processing->luma_vs_saturation[luma] * 2);      /* Lum-vs-Sat   */
fromHSVtoRGB(hsl, rgb);
```

The four canvases are sampled into `hue_vs_hue[36000]`,
`hue_vs_saturation[36000]`, `hue_vs_luma[36000]`, and
`luma_vs_saturation[36000]` LUTs. The whole HSV detour is gated by
`hue_vs_*_used` flags so a neutral receipt skips the conversion.

* Inputs: post-gradation uint16 RGB.
* Outputs: same buffer.
* Key params: per-curve XML arrays parsed by `ReceiptLoader`
  (`hueVsHue`, `hueVsSaturation`, `hueVsLuminance`, `lumaVsSaturation`).

### Stage 8 — Output gamma + Profile Preset (transfer + tonemap + gamut)

A single 16-bit gamma LUT applied per channel realises the receipt's
output transfer function. The gamma table is pre-computed by
`processingSetGamma()` from the profile's transfer expression
(parsed by `tinyexpr`) and the chosen tone-mapping kernel.

```c
/* raw_processing.c:1408-1412 */
for (int i = 0; i < 3; i++)
{
    pix[i] = processing->pre_calc_gamma[ LIMIT16((uint32_t)pix[i]) ];
}
```

The 13 built-in profiles in `image_profiles.c:1-95` each declare a
`tonemap_function`, `gamma_power`, `colour_gamut`, and a
`transfer_function` expression. Examples:

| Profile | Tonemap | Gamma | Gamut | Transfer (excerpt) |
|---|---|---|---|---|
| STANDARD | None | 3.15 | Rec709 | `pow(x, 1/3.15)` |
| TONEMAPPED | Reinhard | 3.15 | Rec709 | `pow(x / (1.0 + x), 1/3.15)` |
| FILM | Tangent | 3.465 | Rec709 | `pow(atan(x) / atan(8.0), 1/3.465)` |
| ALEXA_LOG | AlexaLogC | 1.0 | AlexaWideGamutRGB | `0.247190*log10(5.555556*x+0.052272)+0.385537` |
| CINEON_LOG | CineonLog | 1.0 | AlexaWideGamutRGB | `((log10(x*(1.0-0.0108)+0.0108))*300.0+685.0)/1023.0` |
| SONY_LOG_3 | SonySLog | 1.0 | SonySGamut3 | piecewise log/linear |
| LINEAR | None | 1.0 | Rec709 | `x` |
| SRGB | sRGB | 1.0 | Rec709 | piecewise IEC 61966-2-1 |
| REC709 | Rec709 | 1.0 | Rec709 | piecewise BT.709 |
| Davinci WGI | DavinciIntermediate | 1.0 | DavinciWideGamut | piecewise log |
| FUJI_FLOG | None | 1.0 | Rec2020 | piecewise log |
| CANON_LOG | CanonLog | 1.0 | Canon_Cinema | `0.529136*log10(10.1596*x+1)+0.0730597` |
| PANASONIC_VLOG | PanasonicVLog | 1.0 | PanasonicV | `0.241514*log10(x+0.00873)+0.598206` |

* Inputs: linear or working-space uint16 RGB.
* Outputs: encoded uint16 RGB in target colour space.
* Key params: `processing->pre_calc_gamma`, `colour_gamut`,
  `tonemap_function`, `transfer_function` expression, AgX inverse
  matrix (`raw_processing.c:1658-1669`).

### Stage 9 — Sharpen + denoise + grain (+ LUT + filter)

These run in the outer driver after the per-pixel kernel returns. The
ordering is: 2D median denoise → recursive bilateral denoise →
CA/colour-moiree removal → sharpen (in YCbCr if chroma separation is
on) → grain. LUT and filter happen inside the inner kernel after
gamma.

```c
/* raw_processing.c:651-657 — 2D median denoise */
denoise_2D_median_with_context(outputImage, imageX, imageY,
    processing->denoiserWindow, processing->denoiserStrength,
    &processing->denoiser_context);

/* raw_processing.c:667-671 — recursive bilateral denoise */
recursive_bf_wrap(inputImage, outputImage,
    0.0025f, 0.075f+(((float)processing->rbfDenoiserRange-40.0f)/666.6f),
    imageX, imageY, 3);

/* raw_processing.c:697-699 — CA / colour moiree */
CACorrection(imageX, imageY, inputImage, outputImage,
    (uint16_t)(100-processing->ca_desaturate)<<9, processing->ca_radius);

/* raw_processing.c:794-820 — sharpen (sobel-masked optional) */
int32_t sharp = ka[row[x]] - ky[p_row[x]] - ky[n_row[x]]
              - kx[row[x-3]] - kx[row[x+3]];

/* raw_processing.c:858-879 — monochrome grain */
int grain = (randomval % strength) - (strength >> 2);
outputImage[i+0] = LIMIT16(outputImage[i+0] + grain);
```

* Inputs: encoded uint16 RGB from stage 8.
* Outputs: final uint16 RGB written to `outputImage`.
* Key params: `processing->denoiserStrength`,
  `.denoiserWindow`, `.rbfDenoiserLuma`, `.rbfDenoiserChroma`,
  `.rbfDenoiserRange`, `.ca_desaturate`, `.ca_radius`,
  `.sharpen` (via `processingGetSharpening`), `.sh_masking`,
  `.grainStrength`, `.grainLumaWeight`,
  `.lut_on`/`.lut`/`.filter_on`/`.filter`.

The neutral path that skips denoise/sharpen/grain returns from
`applyProcessingObject` immediately at `raw_processing.c:707-710`.

## 7. Debayer Algorithms

The debayer surface is `src/debayer/debayer.h`
(`src/debayer/debayer.h:1-61`). MLV App routes through several
public C entry points and the multi-algorithm wrapper
`debayerLibRtProcess()` from `src/librtprocess/`.

| Name | Public function | Source path | Performance class | Bit depths |
|---|---|---|---|---|
| None (passthrough float) | `debayerEasy(... type=2)` -> `debayerNoneThread` | `src/debayer/debayer.c:442-454`, `src/debayer/debayer.c:457-511` | trivial copy | float in / uint16 out |
| None (uint16 fast) | `debayerNoneU16` | `src/debayer/debayer.h:9`, `src/debayer/debayer.c:19-41` | trivial copy + bit shift | uint16 in / uint16 out |
| Simple 2x2 | `debayerEasy(... type!=2)` -> `debayerSimpleThread` | `src/debayer/debayer.c:405-439`, `src/debayer/debayer.c:457-511` | preview-grade | float in / uint16 out |
| Bilinear | `debayerBasic` | `src/debayer/debayer.h:22`, `src/debayer/debayer.c:269-402` | playback-grade | float in / uint16 out |
| Bilinear (uint16 fast) | `debayerBasicU16` | `src/debayer/debayer.h:15`, `src/debayer/debayer.c:43-149` | playback-grade | uint16 in / uint16 out |
| AMaZE (legacy in-tree) | `debayerAmaze` -> `demosaic` | `src/debayer/debayer.h:24`, `src/debayer/debayer.c:152-264` | high quality | float in / uint16 out |
| AMaZE (librtprocess) | `debayerLibRtProcess(algorithm=default)` -> `lrtpAmazeDemosaic` | `src/debayer/debayer.c:513-563`, line 542 | high quality | float in / uint16 out |
| LMMSE | `debayerLibRtProcess(algorithm=4)` -> `lrtpLmmseDemosaic` | `src/debayer/debayer.c:532-533` | high quality | float in / uint16 out |
| IGV | `debayerLibRtProcess(algorithm=5)` -> `lrtpIgvDemosaic` | `src/debayer/debayer.c:534-535` | high quality | float in / uint16 out |
| AHD | `debayerLibRtProcess(algorithm=6)` -> `lrtpAhdDemosaic` | `src/debayer/debayer.c:536-537` | high quality | float in / uint16 out |
| RCD | `debayerLibRtProcess(algorithm=7)` -> `lrtpRcdDemosaic` | `src/debayer/debayer.c:538-539` | high quality | float in / uint16 out |
| DCB | `debayerLibRtProcess(algorithm=8)` -> `lrtpDcbDemosaic` | `src/debayer/debayer.c:540-541` | high quality | float in / uint16 out |
| AHD (in-tree) | `debayerAhd` | `src/debayer/debayer.h:28` | high quality | float in / uint16 out |
| AMaZE-cached | shares `debayerAmaze`; cache lives in `mlvObject_t::rawCache` | toggled via `setMlvAlwaysUseAmaze` (`MainWindow.cpp:2893-2895`) | cache-amortised | float in / uint16 out |

### 7.1 Bilinear kernel reference

The `debayerBasicU16` kernel (`src/debayer/debayer.c:43-149`) walks the
input one 2x2 cell at a time, but the iteration starts at offset
`(x=1, y=1)` (the *second* pixel of the *second* row), so the cell
origin in the kernel sits on the **B pixel** of the canonical RGGB
pattern. The four output pixels written per inner-loop iteration are
therefore:

| Output index | Position | CFA phase |
|---|---|---|
| `rgbPix[0]` | `(x,   y  )` | Blue pixel |
| `rgbPix[1]` | `(x+1, y  )` | Green-on-blue row |
| `rgbPix[2]` | `(x,   y+1)` | Green-on-red row |
| `rgbPix[3]` | `(x+1, y+1)` | Red pixel |

The 4x4 neighbourhood `bPix[16]` (`debayer.c:81-86`) indexes the row
above (`y-1`), the current row, the row below, and the row two below:

```
       col-1   col    col+1  col+2
y-1:  bPix[ 0] bPix[ 1] bPix[ 2] bPix[ 3]   (G B G B)
y  :  bPix[ 4] bPix[ 5] bPix[ 6] bPix[ 7]   (G B G B)   <- B row
y+1:  bPix[ 8] bPix[ 9] bPix[10] bPix[11]   (R G R G)   <- R row
y+2:  bPix[12] bPix[13] bPix[14] bPix[15]   (R G R G)
```

Per-CFA-phase interpolation rules (with `N/E/S/W` = orthogonal
neighbours, `NW/NE/SW/SE` = diagonal neighbours one row + one column
away):

| Phase | R channel | G channel | B channel |
|---|---|---|---|
| **Blue pixel** (`bPix[5]`) | mean of 4 diagonal reds (NW, NE, SW, SE) | mean of 4 orthogonal greens (N, E, S, W) | self |
| **Green-on-blue row** (`bPix[6]`) | mean of above + below reds | self | mean of left + right blues |
| **Green-on-red row** (`bPix[9]`) | mean of left + right reds | self | mean of above + below blues |
| **Red pixel** (`bPix[10]`) | self | mean of 4 orthogonal greens (N, E, S, W) | mean of 4 diagonal blues (NW, NE, SW, SE) |

Edge handling: the inner loop runs `Y = width` (skip top row) to
`pixelsizeDB` (skip bottom row) and `x = 1` (skip left col) to
`widthDB` (skip right col). After the per-row kernel returns, the
four borders are filled by **replicating the nearest valid neighbour**
(`debayer.c:130-148`): the first column of each output row is copied
from the second column, the last column from the second-to-last, and
two final `memcpy` calls (`debayer.c:147-148`) duplicate the second
row into the top row and the second-to-last into the bottom row.

Pseudocode (one 2x2 cell, RGGB, with the kernel rooted at the B pixel):

```c
/* Blue pixel (bPix[5]) - self for B; diagonal reds; orthogonal greens */
out[R] = (bayer[bPix[ 0]] + bayer[bPix[ 2]]
       +  bayer[bPix[ 8]] + bayer[bPix[10]]) >> 2;   /* 4 diagonal reds  */
out[G] = (bayer[bPix[ 1]] + bayer[bPix[ 4]]
       +  bayer[bPix[ 6]] + bayer[bPix[ 9]]) >> 2;   /* 4 orthogonal Gs  */
out[B] = bayer[bPix[5]];                              /* self             */

/* Green on B row (bPix[6]) - L/R reds; self; above/below blues */
out[R] = (bayer[bPix[ 2]] + bayer[bPix[10]]) >> 1;   /* above + below R  */
out[G] = bayer[bPix[6]];                              /* self             */
out[B] = (bayer[bPix[ 5]] + bayer[bPix[ 7]]) >> 1;   /* left + right B   */

/* Green on R row (bPix[9]) - above/below reds; self; L/R blues */
out[R] = (bayer[bPix[ 8]] + bayer[bPix[10]]) >> 1;   /* above + below R  */
out[G] = bayer[bPix[9]];                              /* self             */
out[B] = (bayer[bPix[ 5]] + bayer[bPix[13]]) >> 1;   /* left + right B   */

/* Red pixel (bPix[10]) - self for R; orthogonal greens; diagonal blues */
out[R] = bayer[bPix[10]];                             /* self             */
out[G] = (bayer[bPix[ 6]] + bayer[bPix[ 9]]
       +  bayer[bPix[11]] + bayer[bPix[14]]) >> 2;   /* 4 orthogonal Gs  */
out[B] = (bayer[bPix[ 5]] + bayer[bPix[ 7]]
       +  bayer[bPix[13]] + bayer[bPix[15]]) >> 2;   /* 4 diagonal blues */
```

The optional `bit_shift` argument (`debayer.c:50-58`) left-shifts the
input in place before interpolation when the source bit-depth is below
16 (so a 14-bit RAW becomes a 16-bit fixed-point input before averaging,
preventing rounding loss in the `>> 1` / `>> 2` divides).

For non-RGGB sensors (e.g. GBRG) the cell origin is shifted by one
row or column so the kernel's `(x=1, y=1)` start lands on the
appropriate phase. MLV App's RAW pipeline normalises the CFA pattern
to RGGB before debayer (`raw_info.cfa_pattern == 0x02010100`, see
03b §2.3), so the kernel above is the only path the bilinear
implementation actually exercises.

### librtprocess routing

`debayerLibRtProcess()` is a thin wrapper that allocates the
2D float planes (`red`, `green`, `blue`) demanded by librtprocess,
calls into `lrtpLmmseDemosaic` / `lrtpIgvDemosaic` /
`lrtpAhdDemosaic` / `lrtpRcdDemosaic` / `lrtpDcbDemosaic` /
`lrtpAmazeDemosaic` (defined under `src/librtprocess/`), and packs the
result back into interleaved uint16 RGB:

```c
/* src/debayer/debayer.c:531-543 */
if(      algorithm == 4 ) lrtpLmmseDemosaic( imagefloat2d, red2d, green2d, blue2d, width, height );
else if( algorithm == 5 ) lrtpIgvDemosaic  ( imagefloat2d, red2d, green2d, blue2d, width, height );
else if( algorithm == 6 ) lrtpAhdDemosaic  ( imagefloat2d, red2d, green2d, blue2d, width, height, camMatrix );
else if( algorithm == 7 ) lrtpRcdDemosaic  ( imagefloat2d, red2d, green2d, blue2d, width, height );
else if( algorithm == 8 ) lrtpDcbDemosaic  ( imagefloat2d, red2d, green2d, blue2d, width, height );
else                      lrtpAmazeDemosaic( imagefloat2d, red2d, green2d, blue2d, width, height );
```

### Threading

* `debayerEasy`, `debayerBasicU16`, `debayerNoneU16` use `#pragma omp
  parallel for` with the requested thread count.
* `debayerAmaze` partitions rows manually with `pthread_create` /
  `pthread_join` (`src/debayer/debayer.c:188-243`). Chunk height is
  rounded down to multiples of 2 to keep the CFA phase aligned, and the
  loop refuses to fan out when the per-thread chunk height is <= 32 (a
  guard against AMaZE crashes on tiny strips).

### GPU debayer

The only GPU-accelerated demosaic is the GPU Bilinear shader. It is
exposed at the receipt level as `--playback-debayer bilinear` plus a
`--gpu-bilinear-debayer-backend` toggle that's enumerated by
`playback_profile_gpu_bilinear_debayer_backend_name()`
(`MainWindow.cpp:187-200`). Receipt sample fields recording its state
include `gpu_bilinear_debayer_active`,
`gpu_bilinear_debayer_renderer`, and
`gpu_bilinear_debayer_fallback_reason`
(`MainWindow.cpp:2210-2231`). All other algorithms in the table run on
the CPU.

### Receipt-side index mapping

`ReceiptSettings` exposes the algorithms as the enum used by the
combo box (`MainWindow.cpp:2876-2899`):

```cpp
case ReceiptSettings::None:        setMlvUseNoneDebayer(...);
case ReceiptSettings::Simple:      setMlvUseSimpleDebayer(...);
case ReceiptSettings::Bilinear:    setMlvDontAlwaysUseAmaze(...);
case ReceiptSettings::LMMSE:       setMlvUseLmmseDebayer(...);
case ReceiptSettings::IGV:         setMlvUseIgvDebayer(...);
case ReceiptSettings::AMaZE:       setMlvAlwaysUseAmaze(...);
case ReceiptSettings::AHD:         setMlvUseAhdDebayer(...);
case ReceiptSettings::RCD:         /* librtprocess RCD */
case ReceiptSettings::DCB:         /* librtprocess DCB */
```

The XML serialisation is a single integer `<debayer>` tag — the sample
in `receipts/CanonLog.marxml:98` is `<debayer>5</debayer>` (IGV).

## 8. Receipt (.marxml) Schema

`ReceiptLoader` is the standalone, headless `.marxml` parser used in
batch mode. It is intentionally MainWindow-independent — see
`src/batch/ReceiptLoader.h:9-40`.

* `loadFromFile(path, receipt, errorMsg)`
  (`src/batch/ReceiptLoader.h:21`, `ReceiptLoader.cpp:29-90`) opens the
  file, scans for the `<receipt>` root, reads its `version` attribute,
  and dispatches into `parseXmlElements`.
* `parseXmlElements(reader, receipt, version)`
  (`ReceiptLoader.h:37`, `ReceiptLoader.cpp:154-731`) is a 577-line
  if/else cascade, one branch per known tag, mirroring
  `MainWindow::readXmlElementsFromFile()`. Unknown tags are consumed
  via `Rxml->readElementText(); Rxml->readNext();` so future tags do
  not break legacy parsers (`ReceiptLoader.cpp:725-729`).
* `printCdngSettings(receipt)`
  (`ReceiptLoader.cpp:94-146`) emits a CDNG-relevant subset for
  diagnostic logging via `BatchLogger::out`.

### Outer XML structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<receipt version="4" mlvapp="1.14">
    <!-- exactly one tag per receipt knob; tags can appear in any order;
         unknown tags are skipped without erroring. -->
    <exposure>100</exposure>
    <contrast>0</contrast>
    ...
</receipt>
```

The `version` attribute drives compatibility scaling:

* `version < 2` rescales `saturation`, `ls`, `ds`, and `lightening` by
  the `FACTOR_DS / FACTOR_LS / FACTOR_LIGHTEN` constants
  (`ReceiptLoader.cpp:13-15`, `207-238`).
* `version == 2` shifts the `<profile>` enum and seeds default
  `gamma`/`gamut`/`allowCreativeAdjustments`
  (`ReceiptLoader.cpp:341-373, 396-413`).
* `version < 4` multiplies `<rawBlack>` by 10 to migrate the
  pre-fixed-point representation (`ReceiptLoader.cpp:574-578`).

### Sample fully-populated receipt block

Excerpt from `receipts/CanonLog.marxml:1-99` (Canon Log preset):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<receipt version="4" mlvapp="1.14">
    <exposure>100</exposure>
    <contrast>0</contrast>
    <pivot>75</pivot>
    <temperature>4500</temperature>
    <tint>0</tint>
    <clarity>0</clarity>
    <vibrance>0</vibrance>
    <saturation>0</saturation>
    <ds>20</ds>
    <dr>70</dr>
    <ls>0</ls>
    <lr>50</lr>
    <lightening>0</lightening>
    <gradationCurve>1e-05;1e-05;1;1;?1e-05;1e-05;1;1;?1e-05;1e-05;1;1;?1e-05;1e-05;1;1;</gradationCurve>
    <hueVsHue>0;0;1;0;</hueVsHue>
    <hueVsSaturation>0;0;1;0;</hueVsSaturation>
    <hueVsLuminance>0;0;1;0;</hueVsLuminance>
    <lumaVsSaturation>0;0;1;0;</lumaVsSaturation>
    <highlightReconstruction>0</highlightReconstruction>
    <camMatrixUsed>1</camMatrixUsed>
    <chromaSeparation>0</chromaSeparation>
    <tonemap>1</tonemap>
    <transferFunction>(0.529136 * (log10 ( 10.1596 * x + 1 ))) + 0.0730597</transferFunction>
    <gamut>6</gamut>
    <gamma>100</gamma>
    <allowCreativeAdjustments>0</allowCreativeAdjustments>
    <exrMode>1</exrMode>
    <agx>0</agx>
    <dualIso>0</dualIso>
    <rawBlack>17910</rawBlack>
    <rawWhite>16200</rawWhite>
    <cutIn>1</cutIn>
    <cutOut>6</cutOut>
    <debayer>5</debayer>
</receipt>
```

### Alphabetic tag reference

Type column legend: `int` = parsed via `toInt()`; `uint` = `toUInt()`;
`bool` = `(bool)toInt()`; `double` = `toDouble()`;
`str` = passed through verbatim; `tuple` = semicolon/?-separated string
parsed downstream by `ReceiptSettings`. "Stage" refers to the §6 stage
that consumes the value (or the subsystem if outside the per-pixel
chain).

| Tag | Type | Notes / version-quirks | Stage |
|---|---|---|---|
| `agx` | bool | enable AgX colour compression matrix | 8 |
| `allowCreativeAdjustments` | bool | profile-side default may force off | 4-7 |
| `badPixels` | int | bad-pixel detection enable | preproc |
| `bpiMethod` | int | bad-pixel interpolation method | preproc |
| `bpsMethod` | int | bad-pixel search method | preproc |
| `caBlue` | int | chromatic aberration blue shift | 9 |
| `caDesaturate` | int | CA/colour-moiree desaturation strength | 9 |
| `caRadius` | int | CA correction radius | 9 |
| `caRed` | int | CA red shift | 9 |
| `camMatrixUsed` | int | 0 disables `proper_wb_matrix` | 3 |
| `chromaBlur` | int | radius for YCbCr chroma blur | 9 |
| `chromaSeparation` | bool | switches sharpen into YCbCr space | 9 |
| `chromaSmooth` | int | chroma-smooth pass select | preproc |
| `clarity` | int | local-contrast slider | 5 |
| `contrast` | int | global contrast | 5 |
| `cutIn` | int | first frame to render (1-based) | timeline |
| `cutOut` | int | last frame to render | timeline |
| `darkFrameEnabled` | int | apply dark-frame subtraction | preproc |
| `darkFrameFileName` | str | path to MLV dark frame | preproc |
| `debayer` | int | enum (None/Simple/Bilinear/LMMSE/IGV/AMaZE/AHD/RCD/DCB) | debayer |
| `deflickerTarget` | int | 0 disables deflicker | preproc |
| `denoiserStrength` | int | 0 disables 2D median denoise | 9 |
| `denoiserWindow` | int | 2D median window size | 9 |
| `dr` | int | dark recovery (range) | 5 |
| `ds` | int | dark recovery (strength); v0/1 rescaled by FACTOR_DS | 5 |
| `dualIso` | int | 0 = none, otherwise iso2 ID | preproc |
| `dualIsoAliasMap` | int | alias-map style | preproc |
| `dualIsoAutoCorrected` | int | auto-correct EV between ISOs | preproc |
| `dualIsoBlack` | uint | dual-ISO black floor | preproc |
| `dualIsoBlackDelta` | int | black-level delta override | preproc |
| `dualIsoEvCorrection` | int | manual EV stops | preproc |
| `dualIsoForced` | int | force pattern even when MLV not flagged | preproc |
| `dualIsoFrBlending` | int | full-resolution blending mode | preproc |
| `dualIsoInterpolation` | int | row interpolation algorithm | preproc |
| `dualIsoPattern` | int | sensor row pattern | preproc |
| `dualIsoWhite` | uint | dual-ISO white ceiling | preproc |
| `exposure` | int | exposure stops * scale | 2 |
| `exrMode` | bool | EXR-mode skips chroma desat in WB stage | 3,8 |
| `filterEnabled` | bool | Filter object on/off | 9 |
| `filterIndex` | uint | which film filter | 9 |
| `filterStrength` | int | filter blend strength | 9 |
| `focusPixels` | int | focus-pixel correction enable | preproc |
| `fpiMethod` | int | focus-pixel interpolation method | preproc |
| `gamma` | int | gamma * 100 | 8 |
| `gamut` | int | colour gamut enum (Rec709, Rec2020, ...) | 8 |
| `gradationCurve` | tuple | 4 RGB+Y curve handles | 6 |
| `gradientAngle` | int | linear-gradient angle | 5 |
| `gradientContrast` | int | gradient-layer contrast adjustment | 5 |
| `gradientEnabled` | bool | enable graduated filter | 5 |
| `gradientExposure` | int | gradient exposure stops | 5 |
| `gradientLength` | int | feather length | 5 |
| `gradientStartX`,`gradientStartY` | int | gradient origin | 5 |
| `grainLumaWeight` | int | luma weighting for grain | 9 |
| `grainStrength` | int | 0 disables grain | 9 |
| `highlightReconstruction` | bool | green channel reconstruction | 3 |
| `highlights` | int | highlight tone slider | 5 |
| `hueVsHue` | tuple | Hue-vs-Hue spline handles | 7 |
| `hueVsLuminance` | tuple | Hue-vs-Lum spline handles | 7 |
| `hueVsSaturation` | tuple | Hue-vs-Sat spline handles | 7 |
| `lightening` | int | global lift; v0/1 rescaled by FACTOR_LIGHTEN | 5 |
| `lr` | int | light recovery (range) | 5 |
| `ls` | int | light recovery (strength); v0/1 rescaled by FACTOR_LS | 5 |
| `lumaVsSaturation` | tuple | Lum-vs-Sat spline handles | 7 |
| `lutEnabled` | bool | enable 3D LUT | 9 |
| `lutName` | str | LUT file path / preset name | 9 |
| `lutStrength` | int | LUT blend amount | 9 |
| `patternNoise` | int | pattern-noise removal mode | preproc |
| `pivot` | int | contrast pivot point | 5 |
| `profile` | int | image-profile enum; remapped on v0/1/2 | 8 |
| `rawBlack` | int | raw black level; v<4 multiplied by 10 | 2 |
| `rawFixesEnabled` | bool | master gate for the raw-fix block | preproc |
| `rawWhite` | int | raw white level | 2 |
| `rbfDenoiserChroma` | int | RBF chroma denoise blend | 9 |
| `rbfDenoiserLuma` | int | RBF luma denoise blend | 9 |
| `rbfDenoiserRange` | int | RBF range parameter | 9 |
| `saturation` | int | global saturation; v0/1 mapped 0..100 -> -100..100 | 4 |
| `sharpen` | int | sharpen amount | 9 |
| `sharpenMasking` | int | sobel-mask intensity | 9 |
| `shadows` | int | shadow tone slider | 5 |
| `stretchFactorX`,`stretchFactorY` | double | non-square pixel correction | output |
| `tone` | int | hue/tone direction | 4 |
| `tonemap` | int | tonemap function enum | 8 |
| `toningStrength` | int | toning blend strength | 4 |
| `transferFunction` | str | tinyexpr expression for output transfer | 8 |
| `temperature` | int | white-balance Kelvin | 3 |
| `tint` | int | white-balance tint | 3 |
| `upsideDown` | bool | rotate 180 | output |
| `verticalStripes` | int | vertical-stripe correction mode | preproc |
| `vibrance` | int | vibrance slider | 4 |
| `vidstabAccuracy`,`vidstabShakiness`,`vidstabSmoothing`, `vidstabStepsize`,`vidstabZoom` | int | libvidstab parameters | post |
| `vidstabEnable`,`vidstabTripod` | bool | libvidstab toggles | post |
| `vignetteRadius`,`vignetteShape`,`vignetteStrength` | int | vignette mask shape | 5 |

Tags that appear in receipts but are NOT in `ReceiptLoader.cpp` (because
they are emitted by other writers or only consumed by MainWindow) are
silently swallowed by the catch-all branch at
`ReceiptLoader.cpp:725-729`. `SourceFileName` and `Mark` are not part of
the standalone batch parser; they are MainWindow-only session/timeline
metadata and not honoured by `ReceiptLoader::parseXmlElements`.

### Apply order

The receipt is loaded once, then `ReceiptApplier` (see
`src/batch/ReceiptApplier.cpp`) pushes each value through the matching
`processingSet*` setter in `src/processing/raw_processing.c`. Because
all values are pre-applied to the `processingObject_t` before
`applyProcessingObject()` runs, the apply order at render time is
strictly the §6 stage order — receipt tag order in the XML is
irrelevant.

### Special tags

* `cutIn` / `cutOut` — frame range to render (1-based, inclusive). Read
  at `ReceiptLoader.cpp:710-718`. Used by both batch and the timeline.
* `darkFrameFileName` — path to a companion MLV used as a dark frame
  (`ReceiptLoader.cpp:564-572`).
* `transferFunction` — string expression evaluated by `tinyexpr`
  (`src/processing/tinyexpr/`) when computing the gamma LUT
  (`ReceiptLoader.cpp:381-385`).
* `lutName` — verbatim path stored on the receipt; LUT loading happens
  later in `apply_lut`.
* `dualIso*` — preprocessing-only fields consumed by
  `src/mlv/llrawproc/llrawproc.c` before debayer.
* `SourceFileName`, `Mark` — *not* parsed by `ReceiptLoader`; they
  appear only in MainWindow's session XML, which is a superset of the
  receipt schema.

## 9. CinemaDNG Container Layout

MLV App's CinemaDNG writer lives in `src/dng/dng.c` (1189 lines). The
public API in `src/dng/dng.h:53-62` is:

```c
dngObject_t * initDngObject(mlvObject_t * mlv_data, int raw_state, double fps, int32_t par[4]);
int           saveDngFrame (mlvObject_t * mlv_data, dngObject_t * dng_data, uint32_t frame_index, char * dng_filename, const char *props_filename);
void          freeDngObject(dngObject_t * dng_data);
```

`raw_state` is one of `UNCOMPRESSED_RAW`, `COMPRESSED_RAW`,
`UNCOMPRESSED_ORIG`, `COMPRESSED_ORIG` (`src/dng/dng.h:28-31`).

### TIFF-EP variant

Each `.dng` is a single-IFD TIFF/EP file (DNG version
`0x00000401` = 1.4.0.0, see `src/dng/dng.c:726`) plus an EXIF sub-IFD.
The header layout (`src/dng/dng.c:41-49, 315`):

```
+---------------------------+
|  TIFF header (8 bytes)    |  byteOrderII (II) + magic 42 + IFD0 offset = 8
+---------------------------+
|  IFD0 (41 entries)        |  the DNG raw image
+---------------------------+
|  EXIF IFD (11 entries)    |  capture metadata
+---------------------------+
|  Variable-length data     |  matrices, strings, rationals
|                           |
+---------------------------+ data_offset == raw image start
|  Raw image data           |  packed or losless-JPEG payload
+---------------------------+
```

There is no thumbnail IFD — IFD0 is the full raw frame, tagged
`tcNewSubFileType = sfMainImage` (`dng.c:706`). The entire pre-image
header is sized at `HEADER_SIZE = 1536` bytes
(`dng.c:49`).

### Key tags written to IFD0 (`src/dng/dng.c:704-747`)

| Tag | Type | Value source | Meaning |
|---|---|---|---|
| `tcNewSubFileType` | LONG | `sfMainImage` | this IFD is the primary image |
| `tcImageWidth` | LONG | `RAWI.xRes` | full sensor width |
| `tcImageLength` | LONG | `RAWI.yRes` | full sensor height |
| `tcBitsPerSample` | SHORT | `llrawproc->dng_bit_depth` | 10/12/14/16 depending on camera |
| `tcCompression` | SHORT | `ccUncompressed` (1) when `raw_output_state` is even, else `ccJPEG` (7 = lossless JPEG / LJ92) | `dng.c:710` |
| `tcPhotometricInterpretation` | SHORT | `piCFA` (32803) | colour filter array |
| `tcFillOrder` | SHORT | 1 | MSB-first |
| `tcMake` | ASCII | `mlv_data->IDNT.cameraName` derivative | camera maker |
| `tcModel` | ASCII | `mlv_data->IDNT.cameraName` | camera model |
| `tcStripOffsets` | LONG | `data_offset` (set at `dng.c:766` after string/array resolution) | byte offset to raw payload |
| `tcOrientation` | SHORT | 1 | top-left |
| `tcSamplesPerPixel` | SHORT | 1 | mosaic → one channel |
| `tcRowsPerStrip` | SHORT | `RAWI.yRes` | one strip = whole image |
| `tcStripByteCounts` | LONG | `dng_data->image_size` | size of raw payload |
| `tcPlanarConfiguration` | SHORT | `pcInterleaved` | n/a for CFA but spec-compliant |
| `tcSoftware` | ASCII | `"MLV App"` (`dng.c:52`) | writer ID |
| `tcDateTime` | ASCII | `format_datetime()` from MLV header | clip timestamp |
| `tcCFARepeatPatternDim` | SHORT[2] | `0x00020002` | 2x2 CFA |
| `tcCFAPattern` | BYTE[4] | `RAWI.raw_info.cfa_pattern`, defaulting to `0x02010100` (RGGB) at `dng.c:682-684` | Bayer phase |
| `tcExifIFD` | LONG | `exif_ifd_offset` (`dng.c:523`) | pointer to EXIF IFD |
| `tcDNGVersion` | BYTE[4] | `0x00000401` | DNG 1.4.0.0 |
| `tcUniqueCameraModel` | ASCII | `unique_model` | maker + model joined |
| `tcBlackLevel` | LONG | computed `black_level` | per-channel black floor |
| `tcWhiteLevel` | LONG | computed `white_level` | per-channel white ceiling |
| `tcDefaultScale` | RATIONAL[2] | `par[]` from `initDngObject` | non-square pixel ratio |
| `tcDefaultCropOrigin` | SHORT[2] | `RAWI.raw_info.crop.origin` | active-area offset |
| `tcDefaultCropSize` | SHORT[2] | `(active_area.x2-x1, active_area.y2-y1)` | active-area extent |
| `tcColorMatrix1` | SRATIONAL[9] | `camid->ColorMatrix1` | XYZ→camera under illuminant 1 |
| `tcColorMatrix2` | SRATIONAL[9] | `camid->ColorMatrix2` | XYZ→camera under illuminant 2 |
| `tcAsShotNeutral` | RATIONAL[3] | `wbal[]` | as-shot WB neutral RGB |
| `tcBaselineExposure` | SRATIONAL | `basline_exposure` | exposure offset baseline |
| `tcCameraSerialNumber` | ASCII | from MLV `IDNT` block | body serial |
| `tcCalibrationIlluminant1` | SHORT | `lsStandardLightA` | matrix-1 illuminant = StdA |
| `tcCalibrationIlluminant2` | SHORT | `lsD65` | matrix-2 illuminant = D65 |
| `tcActiveArea` | LONG[4] | `raw_info.dng_active_area` | top/left/bottom/right |
| `tcForwardMatrix1` | SRATIONAL[9] | `camid->ForwardMatrix1` | working space → XYZ under StdA |
| `tcForwardMatrix2` | SRATIONAL[9] | `camid->ForwardMatrix2` | working space → XYZ under D65 |
| `tcTimeCodes` | BYTE[8] | `add_timecode(frame_rate_f, tc_frame, ...)` | SMPTE timecode |
| `tcFrameRate` | SRATIONAL[2] | `frame_rate[]` | framerate |
| `tcReelName` | ASCII | clip basename | reel/clip ID |
| `tcBaselineExposureOffset` | SRATIONAL | 0/1 | required by DNG 1.4 |

### Key EXIF IFD tags (`src/dng/dng.c:749-762`)

| Tag | Source field |
|---|---|
| `tcExposureTime` | `EXPO.shutterValue / 1000` |
| `tcFNumber` | `LENS.aperture / 100` |
| `tcISOSpeedRatings` | `EXPO.isoValue` |
| `tcSensitivityType` | `stISOSpeed` |
| `tcExifVersion` | `0x30333230` ("0230") |
| `tcSubjectDistance` | `LENS.focalDist` |
| `tcFocalLength` | `LENS.focalLength` |
| `tcFocalPlaneXResolutionExif` | `focal_resolution_x` |
| `tcFocalPlaneYResolutionExif` | `focal_resolution_x` (same) |
| `tcFocalPlaneResolutionUnitExif` | `camid->focal_unit` |
| `tcLensModelExif` | `LENS.lensName` |

### Sequencing

Output is one `.dng` per frame. The naming convention (from
`MainWindow.cpp:4753-4758`) is:

```
<clipBaseName>_1_<YY>-<MM>-<DD>_0001_C0000_<frameNumber:%06d>.dng
```

Frame numbers are zero-padded to six digits via
`getMlvFrameNumber(mlvObject, frame), 6, 10, QChar('0')`. This matches
common DaVinci Resolve / Premiere CinemaDNG ingest expectations.

### Audio companion

When a clip has audio, MLV App writes a single `.wav` companion file in
the same folder as the `.dng` sequence (the audio writer is in
`src/mlv/wave.c`, called from MainWindow audio export paths). DNG IFDs
do not embed audio — the companion model keeps the sequence NLE-friendly.

### Compression flag values

* `ccUncompressed = 1` — bit-packed raw data is written via
  `dng_pack_image_bits()` (`src/dng/dng.h:55`,
  `src/dng/dng.c:819`).
* `ccJPEG = 7` — losless JPEG (LJ92) payload produced by
  `dng_compress_image()` (`src/dng/dng.h:56`). The encoder writes one
  full-frame LJ92 strip; metadata sample on decode round-trips through
  `dng_decompress_image()` (`src/dng/dng.h:57`).

The choice between the two is data-driven by `raw_output_state %
2` (`src/dng/dng.c:710`).

## 10. `--profile-playback` JSON Contract

The `--profile-playback` flag is parsed in `platform/qt/main.cpp`:

* Pre-Qt detection at `platform/qt/main.cpp:48` so the right OpenGL
  backend is selected before `QApplication` exists:
  `if (std::strcmp(argv[i], "--profile-playback") == 0) return true;`
* Parser registration at `platform/qt/main.cpp:357-359`.
* Dispatch at `platform/qt/main.cpp:702-705`:
  `if (profile_playback) { return runPlaybackProfile(a); }`

`runPlaybackProfile` (in `MainWindow.cpp`) constructs a hidden
`MainWindow`, parses the additional CLI options, then calls
`MainWindow::runHeadlessPlaybackProfile()` at
`platform/qt/MainWindow.cpp:1929`. That function plays the input clip
through the normal render pipeline, capturing one JSON sample per
presented frame and writing the aggregate document to disk at
`MainWindow.cpp:2466-2479`.

### Cadence

* One sample is appended to the in-memory `frameSamples` array per
  presented frame (`MainWindow.cpp:2167, 2283`).
* Warm-up frames are skipped (the `if (!warmup)` guard at
  `MainWindow.cpp:2165`).
* On completion, all samples and the metadata block are wrapped in a
  single `documentRoot` object and serialised once with
  `QJsonDocument::Indented` (`MainWindow.cpp:2466-2479`).

### Top-level JSON shape

```json
{
  "metadata": {
    "captured_at_utc": "2026-04-24T15:11:22Z",
    "input_clip": "/abs/path/to/clip.MLV",
    "receipt": "/abs/path/to/clip.marxml",
    "output": "/abs/path/to/profile.json",
    "total_frames": 240,
    "start_frame": 0,
    "measured_frames": 230,
    "worker_threads_request": "auto",
    "worker_threads_effective": 8,
    "raw_cache_mb": 2048,
    "cache_cpu_cores": 4,
    "scope": "histogram",
    "playback_policy_active": true,
    "playback_debayer_request": "auto",
    "playback_debayer_effective": "amaze-cached",
    "playback_processing_request": "auto",
    "playback_processing_effective": "subset",
    "playback_processing_supported": true,
    "playback_debayer_uses_caching": true,
    "gpu_preview_processing_backend_request": "auto",
    "gpu_bilinear_debayer_backend_request": "auto",
    "gpu_bilinear_debayer_probe_available": true,
    "gpu_bilinear_debayer_probe_renderer": "Intel(R) UHD Graphics 770",
    "dual_iso_mode_selected": "preview",
    "dual_iso_mode_effective": "preview",
    "average_latency_ms": 14.32,
    "average_cadence_ms": 16.71,
    "run_metadata": { "build_sha": "...", "qt_version": "5.15.16" }
  },
  "frames": [
    {
      "sample_index": 0,
      "requested_frame": 1,
      "completed_frame": 1,
      "request_ns": 12340000,
      "completion_ns": 12354000,
      "latency_ms": 14.0,
      "engine_completion_ns": 12352000,
      "engine_latency_ms": 12.0,
      "presentation_overhead_ms": 2.0,
      "draw_frame_ready_total_ms": 1.8,
      "playback_processing_subset_active": true,
      "gpu_bilinear_debayer_active": false,
      "dual_iso_preview_histogram_ms": 0.1,
      "cadence_ms": 16.7
    }
  ]
}
```

### Metadata field reference (`MainWindow.cpp:2349-2452`)

| Field | Type | Source line | Meaning |
|---|---|---|---|
| `captured_at_utc` | string | 2350 | wall clock at run start, ISO 8601 |
| `input_clip` | string | 2351 | absolute MLV path |
| `receipt` | string | 2352 | absolute receipt path or `""` |
| `output` | string | 2353 | absolute JSON output path |
| `total_frames` | int | 2354 | count of frames in clip |
| `start_frame` | int | 2355 | first frame index measured |
| `measured_frames` | int | 2356 | number of post-warmup samples |
| `worker_threads_request` | string | 2357 | `"auto"` or numeric override |
| `worker_threads_effective` | int | 2361 | result of `mlvappEffectiveWorkerThreadCount()` |
| `raw_cache_mb` | int | 2363 | cache budget |
| `cache_cpu_cores` | int | 2364 | cache worker threads |
| `zebras` | bool | 2365 | zebra overlay state |
| `fast_open` | bool | 2366 | preview-only open mode |
| `window_visible` | bool | 2367 | whether the GUI was shown |
| `wait_for_paint` | bool | 2368 | sample-stop policy |
| `measurement_model` | string | 2369 | human-readable explainer |
| `scope` | string | 2373 | none/histogram/waveform/parade/vectorscope |
| `playback_policy_active` | bool | 2374 | `Play` toggled vs single-frame mode |
| `playback_debayer_request` | string | 2383 | enum from `playback_profile_debayer_request_name` |
| `playback_debayer_effective` | string | 2387 | what actually ran |
| `playback_processing_request` | string | 2389 | full / subset / receipt / auto |
| `playback_processing_selected` | string | 2393 | post-resolution selection label |
| `playback_processing_effective` | string | 2395 | `"subset"` or `"receipt"` |
| `playback_processing_supported` | bool | 2399 | whether subset path was eligible |
| `playback_processing_reason` | string | 2401 | why subset/receipt was chosen |
| `playback_debayer_receipt` | string | 2403 | receipt-side debayer name |
| `playback_debayer_uses_caching` | bool | 2405 | true when amaze-cached + cache size > 0 |
| `playback_debayer_cache_threads_active` | bool | 2407 | cache worker pool live |
| `playback_debayer_engine_mode` | int | 2409 | `doesMlvAlwaysUseAmaze()` |
| `gpu_preview_processing_backend_request` | string | 2411 | env-controlled preview shader backend |
| `gpu_preview_processing_environment_requested` | bool | 2415 | `MLVAPP_GPU_PREVIEW_PROCESSING` set |
| `gpu_bilinear_debayer_backend_request` | string | 2417 | requested GPU debayer backend |
| `gpu_bilinear_debayer_environment_requested` | bool | 2421 | `MLVAPP_GPU_BILINEAR_DEBAYER` set |
| `gpu_bilinear_debayer_probe_available` | bool | 2423 | runtime probe success |
| `gpu_bilinear_debayer_probe_reason` | string | 2425 | probe failure reason if any |
| `gpu_bilinear_debayer_probe_renderer` | string | 2427 | OpenGL `GL_RENDERER` |
| `dual_iso_mode_selected` | string | 2429 | requested mode |
| `dual_iso_mode_effective` | string | 2431 | actual mode after resolution |
| `dual_iso_preview_runtime_active` | bool | 2433 | preview kernel is live |
| `dual_iso_preview_override_active` | bool | 2435 | env override forced preview |
| `qt_opengl_environment` | string | 2437 | `QT_OPENGL` value |
| `qt_qpa_platform_environment` | string | 2439 | `QT_QPA_PLATFORM` value |
| `play_start_preroll_active` | bool | 2441 | preroll requested |
| `play_start_preroll_eligible` | bool | 2443 | conditions met for preroll |
| `play_start_preroll_disabled_by_environment` | bool | 2445 | env kill-switch |
| `play_to_first_frame_measured` | bool | 2447 | first-frame metric captured |
| `play_to_first_frame_ms` | double | 2449 | elapsed ms; `-1.0` if not measured |
| `average_latency_ms` | double | 2451 | mean of `frames[].latency_ms` |
| `average_cadence_ms` | double | 2452 | mean of `frames[].cadence_ms` |
| `run_metadata` | object | 2462 | embedded `CrashForensics::runMetadataJson()` |

### Per-frame sample field reference (`MainWindow.cpp:2167-2283`)

| Field | Type | Source line | Meaning |
|---|---|---|---|
| `sample_index` | int | 2168 | 0-based index within `frames[]` |
| `requested_frame` | int | 2169 | 1-based frame the harness asked for |
| `completed_frame` | int | 2170 | frame actually presented |
| `request_ns` | int64 | 2174 | `frameRequest` timestamp |
| `completion_ns` | int64 | 2175 | `frameReady` timestamp |
| `latency_ms` | double | 2176 | `(completion_ns - request_ns) / 1e6` |
| `gpu16_preview_active` | bool | 2177 | render-thread used 16-bit GPU preview |
| `gpu_preview_processing_active` | bool | 2181 | render-thread used GPU processing |
| `playback_processing_subset_active` | bool | 2192 | reduced subset of stages was run |
| `gpu_bilinear_debayer_active` | bool | 2210 | GPU bilinear debayer ran for this frame |
| `gpu_bilinear_debayer_renderer` | string | 2217/2224 | `GL_RENDERER` if probed |
| `gpu_bilinear_debayer_fallback_reason` | string | 2229 | only set when GPU path fell back |
| `engine_completion_ns` | int64 | 2235 | engine ready before presentation |
| `engine_latency_ms` | double | 2236 | request -> engine ready |
| `presentation_overhead_ms` | double | 2238 | engine ready -> on-screen |
| `draw_frame_ready_queue_ms` | double | 2240 | scene-graph queue time |
| `draw_frame_ready_scene_ms` | double | 2242 | scene-graph build time |
| `draw_frame_ready_image_ms` | double | 2244 | image upload time |
| `draw_frame_ready_present_ms` | double | 2246 | present time |
| `draw_frame_ready_scopes_ms` | double | 2248 | scope-overlay render time |
| `draw_frame_ready_overlay_ms` | double | 2250 | overlay render time |
| `draw_frame_ready_total_ms` | double | 2252 | sum of `draw_frame_ready_*` legs |
| `engine_latency_direct_measured` | bool | 2254 | true when engine timestamp was real |
| `dual_iso_preview_histogram_ms` | double | 2256 | dual-ISO histogram pass time |
| `dual_iso_preview_regression_ms` | double | 2258 | dual-ISO regression pass time |
| `dual_iso_preview_rowscale_ms` | double | 2260 | dual-ISO row-scale pass time |
| (stage-timing telemetry) | various | 2262-2270 | every key from `m_lastPresentedStageTimingTelemetry` is splatted in (the per-stage `processing_*_ms` getters from `raw_processing.c:1772-...`) |
| `paint_completion_ns` | int64 | 2273 | only present when `wait_for_paint` |
| `paint_latency_ms` | double | 2274 | request -> viewport paint |
| `post_ui_paint_ms` | double | 2276 | completion -> paint |
| `cadence_ms` | double | 2281 | `completion_ns - previousCompletionNs`; absent on the first sample |

### Cross-reference

The same field tables appear, with cadence narrative, in §12 of
`docs/03-technical-specification.md`. Part B is the source-grounded
canonical version; the spec section restates them in the human-facing
narrative.
