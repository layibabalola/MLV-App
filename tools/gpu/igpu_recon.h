/* igpu_recon.h - C ABI boundary for the GPU Dual ISO reconstruction backend.
 *
 * WHY THIS EXISTS
 *   MLVApp is built with Qt/MinGW. NVIDIA nvcc requires MSVC as its host
 *   compiler, so CUDA code cannot be compiled directly into the MinGW app. This
 *   header is the plain C ABI both sides agree on: the CUDA backend is built with
 *   MSVC/nvcc into a DLL exposing these symbols, and the MinGW app loads it.
 *   The same ABI is the cross-platform seam - a future OpenCL/Vulkan/Metal
 *   backend (or a pure-CPU reference) implements the identical contract.
 *
 * SCOPE (from .claude-state/profiling/20260614-tier2-cuda/recon-algorithm-map.md)
 *   The dominant recon stages (mix-halfres, final_blend) are embarrassingly
 *   parallel per-pixel LUT-gathers; interp/fullres are small stencils; alias_map
 *   is a 3-pass stencil pipeline. The one GPU-hostile stage, `match` (Wirth
 *   median + RANSAC slope), emits only 3 scalars, so it stays on the CPU and its
 *   results are passed in via igpu_recon_frame_t. Everything else runs device-side.
 *
 * DATA CONTRACT (matches diso_get_full20bit, src/mlv/llrawproc/dualiso.c:4735)
 *   Input  : uint16_t[w*h] single-channel RGGB Bayer, 14-bit values in 16-bit
 *            storage (caller already ran make_14bit).
 *   Output : uint16_t[w*h] RGGB Bayer, 16-bit (0..65535) - OR a GL R16 texture
 *            for the zero-readback playback path. The GL texture form carries
 *            the same single-channel Bayer16 values and is intended as a
 *            device-resident handoff to later debayer/present stages.
 *   The backend works internally in the 20-bit domain (raw<<6) and converts back.
 */
#ifndef IGPU_RECON_H
#define IGPU_RECON_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define IGPU_RECON_ABI_VERSION 1

typedef struct igpu_recon_backend igpu_recon_backend; /* opaque handle */

/* Per-clip geometry + static levels. Set once per clip (or on resolution/level
 * change). Defines the H2D upload size and the Bayer phase lattice. */
typedef struct {
    int      width;          /* full-res Bayer width  */
    int      height;         /* full-res Bayer height */
    int      black_level;    /* raw black (14-bit domain) */
    int      white_level;    /* raw white (14-bit domain) */
    int      is_bright[4];   /* per-(y%4) bright/dark ISO field flags (RGGB phase) */
} igpu_recon_clip_t;

/* Per-frame controls. The `match` scalars are computed on the CPU each frame
 * (Wirth median + RANSAC) and handed in here; the device applies the affine
 * rescale inside convert_to_20bit. Mode flags mirror diso_get_full20bit. */
typedef struct {
    double   ev_correction;       /* exposure-match EV between the two ISO fields */
    int      black_delta;         /* match black offset                          */
    int      white_darkened;      /* darkened-field clip level                    */
    double   dark_noise;          /* used by final_blend dark-area cap           */
    int      interp_method;       /* 1 = mean23 (only supported path for v1)     */
    int      use_alias_map;       /* run the 3-pass alias map                    */
    int      use_fullres;         /* run fullres reconstruction                  */
    int      chroma_smooth_method;/* 0 = off (v1); 2/3/5 add a chroma stencil    */
    int      apply_dither;        /* final_blend / convert16 dithering           */
} igpu_recon_frame_t;

/* Read-only LUTs. Uploaded once and reused; rebuild on the host only when the
 * key inputs (black/white/corr_ev) change, then re-set here. Sizes are fixed by
 * the engine (see dualiso.c). NULL pointers mean "unchanged since last set". */
typedef struct {
    const int*    raw2ev;        /* [1<<20]      raw(20b) -> EV*EV_RESOLUTION    */
    const int*    ev2raw;        /* [24*65536]   EV -> raw(20b)                  */
    const double* mix_curve;     /* [1<<20]      mix blend factor                */
    const double* fullres_curve; /* [1<<20]      fullres confidence              */
    const float*  randn05;       /* [1024]       dither noise cache              */
} igpu_recon_luts_t;

/* Where the reconstructed frame is delivered. */
typedef enum {
    IGPU_OUT_CPU16      = 0, /* fill out_bayer16 (uint16[w*h]) - parity/export    */
    IGPU_OUT_GL_TEXTURE = 1  /* write into a CUDA-GL-interop GL_R16 texture        */
} igpu_recon_out_kind;

/* Per-run timings (ms) for profiling against the 41.7 ms / 24 fps budget. */
typedef struct {
    double upload_ms;   /* H2D: Bayer (+ LUTs on change) */
    double match_ms;    /* CPU match estimate (host-side, reported for context) */
    double kernel_ms;   /* device recon chain */
    double download_ms; /* D2H (CPU16) or interop map/present (GL_TEXTURE) */
    double total_ms;
} igpu_recon_timing_t;

/* ---- lifecycle ---- */

/* backend_name: "cuda" | "cpu" (reference). Returns NULL on failure. */
igpu_recon_backend* igpu_recon_create(const char* backend_name);
void                igpu_recon_destroy(igpu_recon_backend* b);

/* Query: ABI version the backend was built against, and a human name/renderer. */
int                 igpu_recon_abi_version(igpu_recon_backend* b);
const char*         igpu_recon_describe(igpu_recon_backend* b); /* e.g. "CUDA / RTX 4090" */

/* Configure (allocates device buffers sized to the clip). Returns 0 on success. */
int igpu_recon_set_clip(igpu_recon_backend* b, const igpu_recon_clip_t* clip);
int igpu_recon_set_luts(igpu_recon_backend* b, const igpu_recon_luts_t* luts);

/* ---- per-frame ---- */

/* Reconstruct one frame.
 *   in_bayer14  : const uint16_t[w*h], 14-bit RGGB (required).
 *   out_kind    : CPU16 -> out_bayer16 must be uint16_t[w*h]; GL_TEXTURE ->
 *                 gl_texture is a valid GL_TEXTURE_2D with GL_R16 storage in a
 *                 current CUDA-compatible OpenGL context. The backend registers
 *                 it for interop during the call and writes Bayer16 device-side.
 * Returns 0 on success, non-zero on error. */
int igpu_recon_run(igpu_recon_backend* b,
                   const igpu_recon_frame_t* frame,
                   const uint16_t* in_bayer14,
                   igpu_recon_out_kind out_kind,
                   uint16_t* out_bayer16,
                   unsigned int gl_texture);

/* Fill `t` with timings from the most recent igpu_recon_run. Returns 0 on success. */
int igpu_recon_last_timing(igpu_recon_backend* b, igpu_recon_timing_t* t);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* IGPU_RECON_H */
