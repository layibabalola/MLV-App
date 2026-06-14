/* oracle_main.c - standalone CPU "oracle" for the MLV-App Dual ISO full-20-bit
 * reconstruction (diso_get_full20bit, src/mlv/llrawproc/dualiso.c:4735).
 *
 * Purpose
 *   Produce a bit-exact scalar reference output + test vectors for CUDA parity
 *   testing of the GPU recon backend (tools/gpu/igpu_recon.h). It generates a
 *   DETERMINISTIC synthetic dual-ISO Bayer frame from a fixed closed-form
 *   formula (so the CUDA side can regenerate byte-identical input), runs the
 *   exact documented pre-call sequence + diso_get_full20bit on the scalar path
 *   (MLVAPP_DISABLE_AVX2=1), then dumps:
 *
 *     in.u16          input 14-bit Bayer            uint16 LE [w*h]
 *     out.u16         output 16-bit Bayer           uint16 LE [w*h]
 *     raw2ev.i32      raw(20b) -> EV*65536 LUT       int32  LE [1<<20]
 *     ev2raw.i32      EV -> raw(20b) LUT (base)      int32  LE [24*65536]
 *     mix_curve.f64   mix blend factor              double LE [1<<20]
 *     fullres_curve.f64  fullres confidence (float curve, cast f64) double LE [1<<20]
 *     scalars.txt     captured out-params / context scalars
 *     README.txt      format spec + synthetic formula + scalars
 *
 *   raw2ev / ev2raw / mix_curve are read back from the scratch struct after the
 *   run (the EXACT LUTs the recon used). fullres_curve is rebuilt here with the
 *   report's exact formula because the recon stores it in a file-scope static
 *   inside dualiso.c that is not reachable from the scratch struct; the rebuild
 *   uses the identical constants (fullres_start=4, fullres_transition=4) and the
 *   production-default FLOAT curve (build_fullres_curve_float) cast to double, to
 *   match what final_blend actually gathers.
 *
 * Build: see build-oracle.ps1 (mingw gcc, -O2 -fopenmp -lpthread -lm
 *        -DSTDOUT_SILENT, run with MLVAPP_DISABLE_AVX2=1).
 *
 * This file lives entirely under tools/gpu/oracle/ and does NOT modify any
 * src/ files. It #includes dualiso.c + hist.c directly into this TU and
 * provides a one-line demosaic() stub (the AMaZE branch is never taken for the
 * mean23 oracle).
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>
#include <sys/stat.h>
#if defined(_WIN32)
#include <direct.h>
#define ORACLE_MKDIR(p) _mkdir(p)
#else
#define ORACLE_MKDIR(p) mkdir((p), 0755)
#endif

/* ---- bring in the recon as a single TU --------------------------------- *
 * dualiso.c inline-#includes dualiso_avx2.inc, dualiso_amaze_avx2.inc and
 * chroma_smooth.c (x3). hist.c is compiled into the same TU. The only external
 * symbol diso_get_full20bit's reachable code references that is not defined in
 * dualiso.c is demosaic() (debayer.h) - referenced only in the AMaZE worker
 * (dualiso.c:2809), never entered for interp_method=1. Provide a stub before
 * the include so the symbol resolves.                                       */
#include "../../../src/debayer/debayer.h"

void
#ifdef __MINGW32__
__attribute__ ((force_align_arg_pointer))
#endif
demosaic(amazeinfo_t * inputdata)
{
    /* Unused: the mean23 oracle (interp_method=1) never enters the AMaZE
     * branch. Present only to satisfy the link-time symbol reference. */
    (void)inputdata;
}

#include "../../../src/mlv/llrawproc/hist.c"
#include "../../../src/mlv/llrawproc/dualiso.c"

/* ====================================================================== *
 *  DETERMINISTIC SYNTHETIC DUAL-ISO BAYER GENERATOR
 *
 *  Closed-form, integer, reproducible byte-for-byte on the CUDA side.
 *  See README.txt for the prose spec. All arithmetic below is exact:
 *  integer ops + a single double-precision sin() per pixel (rounded with
 *  floor(.. + 0.5)). To stay byte-identical the CUDA generator must use the
 *  same IEEE-754 double sin() and the same rounding.
 *
 *  Constants (FIXED - do not change without regenerating the CUDA side):     */
#define ORACLE_BLACK_14   2047    /* raw_info.black_level (14-bit)            */
#define ORACLE_WHITE_14   15000   /* raw_info.white_level (14-bit)            */

/* Bright-field pattern per (y & 3): {1,1,0,0}. Bright rows (is_bright==1)
 * simulate the LOW-iso-100 exposure that holds ~3 EV more highlight signal;
 * dark rows (is_bright==0) simulate iso-800 (darker, noisier conceptually).
 * NB this matches iso_patterns[0] in dualiso.c:4762 so auto-detect resolves
 * to pattern index 0.                                                        */
static const int ORACLE_IS_BRIGHT[4] = {1, 1, 0, 0};

/* ---------------------------------------------------------------------------
 * CASE SELECTOR
 *
 * Each case is a (name, W, H, iso1, iso2, pattern_variant) tuple. The CUDA
 * probe regenerates in.u16 byte-identically from the same (W,H,iso pair,
 * variant) + the same closed-form, so every case is fully reproducible on the
 * device side and the scalars.txt sidecar carries everything the probe needs.
 *
 * pattern_variant:
 *   0  = "smooth": diagonal gradient + mid-freq sinusoid (original base case).
 *        Bright rows already touch WHITE near the corner, so the clip branch is
 *        lightly exercised, but the bright field is mostly below white.
 *   1  = "clipped": a boosted/offset field designed so a LARGE fraction of the
 *        BRIGHT rows reach or exceed WHITE (saturates), forcing the recon's
 *        fullres clip branch (f >= white_darkened -> MAX(f, dark)), the
 *        overexposed map (bright >= white_darkened, dark >= white), and
 *        final_blend's overexposed + dark-area cap paths to fire over a wide
 *        region. Dark rows stay informative (1/8 exposure, clamped at white).
 *------------------------------------------------------------------------- */
typedef struct {
    const char * name;
    int W, H;
    int iso1, iso2;
    int variant;
} oracle_case_t;

static const oracle_case_t ORACLE_CASES[] = {
    /* name        W      H      iso1  iso2   variant */
    { "base",      1808,  2268,  100,  800,   0 },  /* the original parity case   */
    { "res8k",     8192,  4320,  100,  800,   0 },  /* Case A: 8K, same pattern   */
    { "clipped",   1808,  2268,  100,  800,   1 },  /* Case B: saturated highlights */
    { "iso1600",   1808,  2268,  100,  1600,  0 },  /* Case C: corr_ev=4, iso 100/1600 */
};
#define ORACLE_NUM_CASES ((int)(sizeof(ORACLE_CASES)/sizeof(ORACLE_CASES[0])))

/* Base scene "irradiance" field S(x,y) in [0, 1]: a smooth diagonal gradient
 * plus a mid-frequency sinusoidal texture, fully deterministic. Returns a
 * value in [0,1]. */
static double oracle_scene(int x, int y, int W, int H)
{
    /* diagonal gradient 0..1 */
    double gx = (double)x / (double)(W - 1);
    double gy = (double)y / (double)(H - 1);
    double grad = 0.5 * (gx + gy);                 /* [0,1] */
    /* mid-frequency texture, amplitude 0.18, period ~64px in x and ~48px in y */
    double tex = 0.18 * sin((x * (2.0 * M_PI / 64.0)) + (y * (2.0 * M_PI / 48.0)));
    double s = grad + tex;
    if (s < 0.0) s = 0.0;
    if (s > 1.0) s = 1.0;
    return s;
}

/* Clipped variant scene S'(x,y): same diagonal gradient + texture, then boosted
 * by gain 1.8 and DC offset 0.35 so a large contiguous region maps above 1.0
 * (which the bright-row formula below clamps to WHITE). The pre-clamp value is
 * returned UNclamped (can exceed 1.0); the caller clamps after the iso scale so
 * BRIGHT rows saturate while DARK rows (1/ratio gain) stay well below white. */
static double oracle_scene_clipped(int x, int y, int W, int H)
{
    double gx = (double)x / (double)(W - 1);
    double gy = (double)y / (double)(H - 1);
    double grad = 0.5 * (gx + gy);
    double tex = 0.18 * sin((x * (2.0 * M_PI / 64.0)) + (y * (2.0 * M_PI / 48.0)));
    double s = grad + tex;
    if (s < 0.0) s = 0.0;
    /* boost + offset, intentionally NOT clamped to 1.0 here */
    double s2 = s * 1.8 + 0.35;
    if (s2 < 0.0) s2 = 0.0;
    return s2;   /* may exceed 1.0 -> bright rows clip to WHITE */
}

/* Produce the 14-bit Bayer pixel for (x,y) for a given case.
 *
 * The bright/dark exposure ratio is the ISO ratio (iso2/iso1), so
 * match_exposures on the ISO-ratio path computes corr_ev = log2(iso2/iso1) and
 * matches without aborting:
 *   variant 0/iso 100/800  -> ratio 8  -> corr_ev 3
 *   variant 0/iso 100/1600 -> ratio 16 -> corr_ev 4
 *
 *   signal_span = WHITE - BLACK
 *   dark rows : value = BLACK + S * (signal_span / ratio)   (high iso, /ratio)
 *   bright rows: value = BLACK + S * (signal_span)          (low iso)
 *
 * Both clamp to [BLACK, WHITE]. For variant 1 the bright-row S exceeds 1.0 over
 * a wide region so bright values saturate at WHITE (clip-branch coverage). */
static uint16_t oracle_pixel(int x, int y, const oracle_case_t * c)
{
    int span  = ORACLE_WHITE_14 - ORACLE_BLACK_14;   /* 12953 */
    int ratio = c->iso2 / c->iso1;                   /* 8 or 16 */
    double s = (c->variant == 1)
                 ? oracle_scene_clipped(x, y, c->W, c->H)
                 : oracle_scene(x, y, c->W, c->H);
    int is_bright = ORACLE_IS_BRIGHT[y & 3];
    double v;
    if (is_bright)
        v = (double)ORACLE_BLACK_14 + s * (double)span;
    else
        v = (double)ORACLE_BLACK_14 + s * ((double)span / (double)ratio);
    int iv = (int)floor(v + 0.5);
    if (iv < ORACLE_BLACK_14) iv = ORACLE_BLACK_14;   /* no sub-black for this synthetic */
    if (iv > ORACLE_WHITE_14) iv = ORACLE_WHITE_14;
    return (uint16_t)iv;
}

/* ---- little-endian dump helpers (host is LE x86; write raw bytes) ------- */
static int dump_blob(const char * dir, const char * name, const void * data, size_t bytes)
{
    char path[1024];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    FILE * f = fopen(path, "wb");
    if (!f) { fprintf(stderr, "[oracle] cannot open %s for write\n", path); return 0; }
    size_t w = fwrite(data, 1, bytes, f);
    fclose(f);
    if (w != bytes) { fprintf(stderr, "[oracle] short write to %s (%zu/%zu)\n", path, w, bytes); return 0; }
    fprintf(stderr, "[oracle] wrote %s (%zu bytes)\n", name, bytes);
    return 1;
}

int main(int argc, char ** argv)
{
    /* 0) Force the scalar parity path BEFORE the first recon call. The env is
     *    also expected to be set by the runner; set it here too for safety. */
#if defined(_WIN32)
    _putenv("MLVAPP_DISABLE_AVX2=1");
#else
    setenv("MLVAPP_DISABLE_AVX2", "1", 1);
#endif

    /* Argument forms (backward compatible):
     *   oracle.exe                               -> base case, outdir "."
     *   oracle.exe <W> <H> [outdir]              -> legacy: explicit geometry,
     *                                               base-case iso/variant
     *   oracle.exe --case <name> [outdir]        -> named case from ORACLE_CASES
     *   oracle.exe --list                        -> print case table and exit
     *
     * The named-case form is the one the parity-breadth runner uses; it carries
     * W/H + iso pair + pattern variant so each case is self-describing. */
    int W = 1808, H = 2268;
    int sel_iso1 = 100, sel_iso2 = 800, sel_variant = 0;
    const char * case_name = "base";
    const char * outdir = ".";

    if (argc >= 2 && strcmp(argv[1], "--list") == 0) {
        printf("oracle cases:\n");
        for (int i = 0; i < ORACLE_NUM_CASES; i++) {
            const oracle_case_t * c = &ORACLE_CASES[i];
            printf("  %-10s %5dx%-5d  iso %d/%-5d  variant=%d\n",
                   c->name, c->W, c->H, c->iso1, c->iso2, c->variant);
        }
        return 0;
    }

    if (argc >= 3 && strcmp(argv[1], "--case") == 0) {
        const oracle_case_t * found = NULL;
        for (int i = 0; i < ORACLE_NUM_CASES; i++) {
            if (strcmp(argv[2], ORACLE_CASES[i].name) == 0) { found = &ORACLE_CASES[i]; break; }
        }
        if (!found) {
            fprintf(stderr, "[oracle] unknown case '%s' (use --list)\n", argv[2]);
            return 2;
        }
        W = found->W; H = found->H;
        sel_iso1 = found->iso1; sel_iso2 = found->iso2; sel_variant = found->variant;
        case_name = found->name;
        if (argc >= 4) outdir = argv[3];
    } else {
        /* legacy positional form: <W> <H> [outdir] */
        if (argc >= 3) { W = atoi(argv[1]); H = atoi(argv[2]); }
        if (argc >= 4) outdir = argv[3];
    }
    if (W <= 16 || H <= 16) { fprintf(stderr, "[oracle] bad geometry %dx%d\n", W, H); return 2; }

    /* the resolved case used by the synthetic generator + scalars sidecar */
    oracle_case_t the_case;
    the_case.name = case_name;
    the_case.W = W; the_case.H = H;
    the_case.iso1 = sel_iso1; the_case.iso2 = sel_iso2;
    the_case.variant = sel_variant;
    fprintf(stderr, "[oracle] case=%s  geometry=%dx%d  iso=%d/%d  variant=%d\n",
            case_name, W, H, sel_iso1, sel_iso2, sel_variant);

    /* create output dir if missing (ignore "already exists") */
    if (ORACLE_MKDIR(outdir) != 0) {
        struct stat st;
        if (stat(outdir, &st) != 0) {
            fprintf(stderr, "[oracle] cannot create outdir %s\n", outdir);
            return 4;
        }
    }

    fprintf(stderr, "[oracle] geometry %dx%d  outdir=%s\n", W, H, outdir);

    size_t n = (size_t)W * (size_t)H;

    /* 1) generate the deterministic synthetic 14-bit Bayer frame */
    uint16_t * img = (uint16_t *)malloc(n * sizeof(uint16_t));
    if (!img) { fprintf(stderr, "[oracle] OOM input\n"); return 4; }
    for (int y = 0; y < H; y++)
        for (int x = 0; x < W; x++)
            img[(size_t)x + (size_t)y * W] = oracle_pixel(x, y, &the_case);

    /* keep a pristine copy of the input for the in.u16 dump (recon overwrites
     * img in place with the 16-bit output). */
    uint16_t * in_copy = (uint16_t *)malloc(n * sizeof(uint16_t));
    if (!in_copy) { fprintf(stderr, "[oracle] OOM input copy\n"); return 4; }
    memcpy(in_copy, img, n * sizeof(uint16_t));

    /* 2) raw_info (14-bit input; the recon promotes to 20-bit internally).
     *    active_area = {0,0,W,H} exactly mirrors the production pre-call setup
     *    (llrawproc.c:1188-1194). With this active_area compute_black_noise
     *    samples an empty strip -> num==0 -> dark_noise falls back to the
     *    deterministic default 8 (14-bit) -> *64 = 512 (20-bit). This matches
     *    the app's real behavior on this geometry. NB raw.h active_area layout
     *    is {y1,x1,y2,x2}. */
    struct raw_info ri;
    memset(&ri, 0, sizeof ri);
    ri.width = W; ri.height = H; ri.pitch = W;
    ri.bits_per_pixel = 14;
    ri.black_level = ORACLE_BLACK_14;
    ri.white_level = ORACLE_WHITE_14;
    ri.cfa_pattern = 0x02010100;            /* RGGB -> no 1-line skip */
    ri.active_area.x1 = 0; ri.active_area.y1 = 0;
    ri.active_area.x2 = W; ri.active_area.y2 = H;

    /* 3) scratch + diso params.
     *    auto_correction = -1  (ISO-ratio path inside match_exposures).
     *    ev_correction   =  1  (the sentinel that tells match_exposures to KEEP
     *                           the ISO-ratio result instead of overwriting it
     *                           with -*ev_correction; see dualiso.c:2498 and the
     *                           caller's has_explicit_auto_match==false branch
     *                           in llrawproc.c:1221-1234). With iso 100/800 this
     *                           yields _ev_correction = log2(8) = 3.0 EV >= 0.5,
     *                           so match_exposures returns 1 (matched, no abort)
     *                           and darkens the bright rows in place.
     *    black_delta = 0. */
    dualiso_full20bit_scratch_t sc;
    memset(&sc, 0, sizeof sc);

    int    diso_pattern         = 0;     /* 0 = auto-detect (resolves to {1,1,0,0}) */
    int    diso_auto_correction = -1;    /* ISO-ratio                                */
    double diso_ev_correction   = 1.0;   /* sentinel: use ISO-ratio (see above)      */
    int    diso_black_delta     = 0;
    int    interp_method        = 1;     /* mean23 (scalar, integer-EV-LUT)          */
    int    use_alias_map        = 1;     /* exercise the 3-pass alias map            */
    int    use_fullres          = 1;     /* fullres reconstruction on                */
    int    chroma_smooth_method = 0;     /* off                                       */
    int    threads              = 1;     /* single-thread for determinism            */
    int    dark_frame           = 0;

    fprintf(stderr, "[oracle] running diso_get_full20bit (mean23 scalar)...\n");
    int ok = diso_get_full20bit(ri, img, dark_frame, sel_iso1, sel_iso2,
                                &diso_pattern, &diso_auto_correction,
                                &diso_ev_correction, &diso_black_delta,
                                interp_method, use_alias_map, use_fullres,
                                chroma_smooth_method, threads, &sc);
    if (!ok) {
        fprintf(stderr, "[oracle] recon FAILED (pattern/overlap). pattern=%d ev_corr=%f\n",
                diso_pattern, diso_ev_correction);
        return 4;
    }

    /* out-params captured AFTER the run:
     *   diso_ev_correction : match_exposures sets *ev_correction = -_ev_correction
     *                        (negative); corr_ev = ABS(it) is what the recon used.
     *   diso_black_delta   : *black_delta = _black_delta/64 (14-bit-ish units).
     *   diso_pattern       : resolved ISO pattern (negative encoding -(i+1)).
     * white_darkened is computed internally and not returned through the API;
     * we recompute it from the documented formula for the sidecar (see below). */
    double corr_ev = fabs(diso_ev_correction);

    /* Recompute white_darkened exactly as match_exposures does (dualiso.c:2548).
     * Critical subtleties that an earlier version of this sidecar got WRONG:
     *   - match_exposures uses white = MIN(raw_info.white_level, *white_darkened
     *     seed) where the seed is white_bright = white/2 (driver:4924). With the
     *     driver's level math (raw_info.white_level = white = 960000,
     *     white_bright = 480000) that MIN is 480000, NOT 960000.
     *   - The APPLY _black_delta is 0, NOT the ISO-ratio 448: the driver passes
     *     diso_black_delta = 0, and since 0 != -1, match_exposures overwrites
     *     _black_delta with 0*64 = 0 (dualiso.c:2503-2506) before darkening.
     *   - The cast truncates the WHOLE rhs: (int)((white-black+bd)*factor + black),
     *     i.e. +black is in double then truncated (dualiso.c:2548 assigns to an
     *     int *white_darkened target via int arithmetic on a double expression).
     * Driver level math: black20 = 2047*64, white20 = 15000*64,
     * white_bright20 = (15000/2)*64. Result here: 174632 (matches stage_bright). */
    int black20 = ORACLE_BLACK_14 * 64;
    int white20 = ORACLE_WHITE_14 * 64;
    int white_bright20 = (ORACLE_WHITE_14 / 2) * 64;
    int wd_white = (white20 < white_bright20) ? white20 : white_bright20; /* MIN -> 480000 */
    int _black_delta_20 = 0; /* apply value: diso_black_delta=0 != -1 forces 0 */
    double wd_factor = pow(2.0, -corr_ev);
    int white_darkened = (int)((double)(wd_white - black20 + _black_delta_20) * wd_factor + (double)black20);

    /* black_delta seen back from the recon */
    int black_delta_out = diso_black_delta;

    fprintf(stderr, "[oracle] recon OK. corr_ev=%f black_delta=%d pattern=%d white_darkened=%d\n",
            corr_ev, black_delta_out, diso_pattern, white_darkened);

    /* ---- gather the LUTs the recon actually used (from scratch) --------- */
    /* raw2ev: scratch->ev_raw2ev [1<<20] int */
    /* ev2raw: scratch->ev2raw_0  [24*EV_RESOLUTION] int (base array; the recon
     *         uses ev2raw_0 + 10*EV_RESOLUTION as the usable origin). We dump
     *         the FULL base array to match igpu_recon_luts_t.ev2raw sizing. */
    if (!sc.ev_raw2ev || !sc.ev2raw_0) {
        fprintf(stderr, "[oracle] LUTs missing from scratch (ev_raw2ev=%p ev2raw_0=%p)\n",
                (void*)sc.ev_raw2ev, (void*)sc.ev2raw_0);
        return 4;
    }

    /* mix_curve: find the valid slot the recon built (fresh scratch -> slot 0). */
    double * mix_curve = NULL;
    for (int slot = 0; slot < DUALISO_MIX_CURVE_CACHE_SLOTS; slot++) {
        if (sc.mix_curve_valid[slot] && sc.mix_curve[slot]) { mix_curve = sc.mix_curve[slot]; break; }
    }
    if (!mix_curve) {
        fprintf(stderr, "[oracle] no valid mix_curve slot found\n");
        return 4;
    }

    /* fullres_curve: rebuild with the exact report formula (float curve =
     * production default in final_blend), cast to double for the dump. black is
     * the 20-bit black (black20). */
    size_t lut20 = (size_t)1 << 20;
    double * fullres_curve = (double *)malloc(lut20 * sizeof(double));
    if (!fullres_curve) { fprintf(stderr, "[oracle] OOM fullres_curve\n"); return 4; }
    {
        const double fullres_start = 4.0;
        const double fullres_transition = 4.0;
        for (size_t i = 0; i < lut20; i++) {
            double ev2 = log2(MAX((double)i / 64.0 - (double)black20 / 64.0, 1.0));
            double c2 = -cos(COERCE(ev2 - fullres_start, 0.0, fullres_transition) * M_PI / fullres_transition);
            double f = (c2 + 1.0) / 2.0;
            fullres_curve[i] = (double)(float)f;   /* match build_fullres_curve_float */
        }
    }

    /* fullres_curve_double: the DOUBLE curve (build_fullres_curve, dualiso.c:2698)
     * used ONLY by build_alias_map's fullres-skip predicate
     * (fullres_curve[bright[..]] > fullres_thr). This differs from the float
     * curve cast above by the float rounding, which can flip the > 0.8 predicate
     * at a handful of pixels - so the CUDA alias map needs this exact double
     * table for its skip test. final_blend still uses the float curve. */
    double * fullres_curve_double = (double *)malloc(lut20 * sizeof(double));
    if (!fullres_curve_double) { fprintf(stderr, "[oracle] OOM fullres_curve_double\n"); return 4; }
    {
        const double fullres_start = 4.0;
        const double fullres_transition = 4.0;
        for (size_t i = 0; i < lut20; i++) {
            double ev2 = log2(MAX((double)i / 64.0 - (double)black20 / 64.0, 1.0));
            double c2 = -cos(COERCE(ev2 - fullres_start, 0.0, fullres_transition) * M_PI / fullres_transition);
            double f = (c2 + 1.0) / 2.0;
            fullres_curve_double[i] = f;           /* match build_fullres_curve (double) */
        }
    }

    /* ---- write all dumps ---------------------------------------------- */
    int all_ok = 1;
    all_ok &= dump_blob(outdir, "in.u16",  in_copy, n * sizeof(uint16_t));
    all_ok &= dump_blob(outdir, "out.u16", img,     n * sizeof(uint16_t));
    all_ok &= dump_blob(outdir, "raw2ev.i32", sc.ev_raw2ev, lut20 * sizeof(int));
    all_ok &= dump_blob(outdir, "ev2raw.i32", sc.ev2raw_0, (size_t)DUALISO_EV2RAW_LUT_COUNT * sizeof(int));
    all_ok &= dump_blob(outdir, "mix_curve.f64", mix_curve, lut20 * sizeof(double));
    all_ok &= dump_blob(outdir, "fullres_curve.f64", fullres_curve, lut20 * sizeof(double));
    all_ok &= dump_blob(outdir, "fullres_curve_double.f64", fullres_curve_double, lut20 * sizeof(double));

    /* ---- PER-STAGE INTERMEDIATE DUMPS (for CUDA stage-by-stage parity) ----
     * Read back the recon's scratch planes AFTER diso_get_full20bit returned.
     * The buffers persist (the recon reuses them across calls and never frees
     * until free_dualiso_full20bit_scratch). Each plane is w*h.
     *
     * IMPORTANT chroma_smooth=0 aliasing: the driver sets
     *   fullres_smooth = fullres ; halfres_smooth = halfres
     * (dualiso.c:4872/4876) and only points them at scratch->{fullres,halfres}
     * _smooth when chroma_smooth_method != 0. With chroma_smooth_method=0 the
     * scratch->fullres_smooth / scratch->halfres_smooth planes are NEVER
     * written, so we dump scratch->fullres for "fullres_smooth" and
     * scratch->halfres for "halfres_smooth" - exactly the buffers the kernel
     * chain consumes. The CUDA side must mirror this aliasing.
     *
     * NOTE raw_buffer_32 holds the FINAL 20-bit blend output here (final_blend
     * does raw_set_pixel32(x,y,ev2raw[output]) into raw_buffer_32), NOT the
     * post-convert_to_20bit promote. That early stage is reproduced on the CUDA
     * side directly from in.u16 and checked against the documented formula, so
     * we instead dump:
     *   - stage_match.u32   raw_buffer_32 right after match_exposures... but the
     *     recon has already overwritten it with the final blend. We cannot
     *     recover the post-promote/post-match buffer without instrumenting
     *     src/, which is out of scope. So stage_match/promote parity is checked
     *     on the CUDA side against the closed-form (v<<6)&0xFFFFF + match cast.
     * The plane dumps we CAN recover post-return are the persistent stage
     * outputs: dark, bright, fullres, halfres, alias_map, overexposed, and the
     * final raw_buffer_32 (== final_blend 20-bit output). */
    if (sc.dark)        all_ok &= dump_blob(outdir, "stage_dark.u32",           sc.dark,        n * sizeof(uint32_t));
    if (sc.bright)      all_ok &= dump_blob(outdir, "stage_bright.u32",         sc.bright,      n * sizeof(uint32_t));
    if (sc.fullres)     all_ok &= dump_blob(outdir, "stage_fullres.u32",        sc.fullres,     n * sizeof(uint32_t));
    if (sc.halfres)     all_ok &= dump_blob(outdir, "stage_halfres.u32",        sc.halfres,     n * sizeof(uint32_t));
    /* chroma_smooth=0 -> *_smooth == base; dump the base planes under the
     * smooth name so the CUDA probe can compare its smooth-stage buffers. */
    if (sc.fullres)     all_ok &= dump_blob(outdir, "stage_fullres_smooth.u32", sc.fullres,     n * sizeof(uint32_t));
    if (sc.halfres)     all_ok &= dump_blob(outdir, "stage_halfres_smooth.u32", sc.halfres,     n * sizeof(uint32_t));
    if (sc.alias_map)   all_ok &= dump_blob(outdir, "stage_alias_map.u16",      sc.alias_map,   n * sizeof(uint16_t));
    if (sc.overexposed) all_ok &= dump_blob(outdir, "stage_overexposed.u16",    sc.overexposed, n * sizeof(uint16_t));
    if (sc.raw_buffer_32) all_ok &= dump_blob(outdir, "stage_final20.u32",      sc.raw_buffer_32, n * sizeof(uint32_t));

    /* ---- scalars sidecar ---------------------------------------------- */
    {
        char path[1024];
        snprintf(path, sizeof(path), "%s/scalars.txt", outdir);
        FILE * f = fopen(path, "w");
        if (f) {
            fprintf(f, "# Dual ISO full-20-bit recon oracle - captured scalars\n");
            fprintf(f, "case=%s\n", case_name);
            fprintf(f, "pattern_variant=%d\n", sel_variant);
            fprintf(f, "width=%d\n", W);
            fprintf(f, "height=%d\n", H);
            fprintf(f, "black_level_14=%d\n", ORACLE_BLACK_14);
            fprintf(f, "white_level_14=%d\n", ORACLE_WHITE_14);
            fprintf(f, "iso1=%d\n", sel_iso1);
            fprintf(f, "iso2=%d\n", sel_iso2);
            fprintf(f, "cfa_pattern=0x02010100\n");
            fprintf(f, "interp_method=%d\n", interp_method);
            fprintf(f, "use_alias_map=%d\n", use_alias_map);
            fprintf(f, "use_fullres=%d\n", use_fullres);
            fprintf(f, "chroma_smooth_method=%d\n", chroma_smooth_method);
            fprintf(f, "threads=%d\n", threads);
            fprintf(f, "is_bright=%d,%d,%d,%d\n",
                    ORACLE_IS_BRIGHT[0], ORACLE_IS_BRIGHT[1], ORACLE_IS_BRIGHT[2], ORACLE_IS_BRIGHT[3]);
            fprintf(f, "# --- captured out-params (post-recon) ---\n");
            fprintf(f, "ev_correction=%.17g\n", diso_ev_correction);   /* negative as stored */
            fprintf(f, "corr_ev=%.17g\n", corr_ev);                    /* ABS(ev_correction) */
            fprintf(f, "black_delta=%d\n", black_delta_out);
            fprintf(f, "iso_pattern=%d\n", diso_pattern);
            fprintf(f, "# --- 20-bit derived levels (probe reads these) ---\n");
            fprintf(f, "black20=%d\n", black20);
            fprintf(f, "white20=%d\n", white20);
            fprintf(f, "white_bright20=%d\n", white_bright20);
            fprintf(f, "white_darkened=%d\n", white_darkened);
            fprintf(f, "factor=%.17g            # pow(2,-corr_ev), the match-exposures darken factor\n", wd_factor);
            fprintf(f, "black_delta20=%d        # APPLY value (diso_black_delta=0 != -1 -> 0)\n", _black_delta_20);
            fprintf(f, "dark_noise20=512        # default-8 (14-bit) fallback *64 for active_area={0,0,W,H}\n");
            fprintf(f, "dark_noise_20bit=512   # legacy alias of dark_noise20\n");
            fclose(f);
            fprintf(stderr, "[oracle] wrote scalars.txt\n");
        } else {
            fprintf(stderr, "[oracle] cannot write scalars.txt\n");
            all_ok = 0;
        }
    }

    /* ---- README sidecar ----------------------------------------------- */
    {
        char path[1024];
        snprintf(path, sizeof(path), "%s/README.txt", outdir);
        FILE * f = fopen(path, "w");
        if (f) {
            fprintf(f,
"MLV-App Dual ISO full-20-bit recon CPU ORACLE - test vectors\n"
"============================================================\n"
"\n"
"Generated by tools/gpu/oracle/oracle_main.c (mean23 scalar reference path,\n"
"MLVAPP_DISABLE_AVX2=1). Mirrors diso_get_full20bit (src/mlv/llrawproc/\n"
"dualiso.c:4735) with interp_method=1, use_alias_map=1, use_fullres=1,\n"
"chroma_smooth_method=0, iso1=100, iso2=800, threads=1.\n"
"\n"
"All binary files are raw little-endian, no header. Host is x86-64 LE.\n"
"\n"
"FILES\n"
"-----\n"
"  in.u16             uint16 LE, %d elements (W*H). Synthetic 14-bit Bayer\n"
"                     input (values in [black,white]). See FORMULA below.\n"
"  out.u16            uint16 LE, %d elements (W*H). 16-bit Bayer recon output\n"
"                     (the bit-exact scalar reference; CUDA must match this).\n"
"  raw2ev.i32         int32  LE, %u elements (1<<20). raw(20-bit) -> EV*65536.\n"
"                     The EXACT LUT the recon used (read back from scratch).\n"
"  ev2raw.i32         int32  LE, %u elements (24*65536). EV -> raw(20-bit),\n"
"                     BASE array. Usable origin is element 10*65536 (=655360);\n"
"                     valid EV index range [-10*65536, 14*65536). Read back\n"
"                     from scratch. Matches igpu_recon_luts_t.ev2raw sizing.\n"
"  mix_curve.f64      double LE, %u elements (1<<20). Halfres mix blend factor\n"
"                     k(raw20). The EXACT curve the recon built (read back from\n"
"                     scratch slot). Keyed on (black,white,corr_ev,overlap).\n"
"  fullres_curve.f64  double LE, %u elements (1<<20). Fullres confidence curve.\n"
"                     Rebuilt here with dualiso.c's exact formula (FLOAT curve,\n"
"                     production default in final_blend, cast to double):\n"
"                       fullres_start=4, fullres_transition=4, black=black20\n"
"                       ev2 = log2(max(i/64 - black/64, 1))\n"
"                       c2  = -cos(coerce(ev2-4, 0, 4) * PI / 4)\n"
"                       f   = (double)(float)((c2+1)/2)\n"
"                     (stored static in dualiso.c, not reachable via scratch.)\n"
"  scalars.txt        key=value sidecar of geometry, params, captured out-\n"
"                     params and derived 20-bit levels.\n"
"  README.txt         this file.\n"
"\n"
"GEOMETRY\n"
"--------\n"
"  width  W = %d\n"
"  height H = %d\n"
"  Bayer phase: RGGB (cfa_pattern 0x02010100). Element (x,y) at index x + y*W.\n"
"\n"
"SYNTHETIC DUAL-ISO BAYER FORMULA (fixed, closed-form, byte-reproducible)\n"
"-----------------------------------------------------------------------\n"
"  Constants: BLACK=%d, WHITE=%d (14-bit), span = WHITE-BLACK = %d.\n"
"  Bright-field pattern per (y&3): is_bright = {1,1,0,0}\n"
"     (matches iso_patterns[0]; auto-detect resolves to this).\n"
"\n"
"  Scene irradiance S(x,y) in [0,1] (double precision):\n"
"     gx   = x / (W-1)\n"
"     gy   = y / (H-1)\n"
"     grad = 0.5 * (gx + gy)\n"
"     tex  = 0.18 * sin( x*(2*PI/64) + y*(2*PI/48) )\n"
"     S    = clamp(grad + tex, 0, 1)\n"
"\n"
"  Pixel value (14-bit), is_bright = is_bright[y&3]:\n"
"     if is_bright:  v = BLACK + S * span            (low ISO 100, ~3 EV more)\n"
"     else        :  v = BLACK + S * (span / 8)      (high ISO 800, 2^-3)\n"
"     pixel = clamp( floor(v + 0.5), BLACK, WHITE )    (uint16)\n"
"\n"
"  The 8x bright/dark ratio == 3 EV == log2(800/100), so match_exposures on\n"
"  the ISO-ratio path (auto_correction=-1, ev_correction sentinel=1) computes\n"
"  corr_ev = 3.0 (>= 0.5) and matches without aborting.\n"
"\n"
"  CUDA REPRODUCTION: use IEEE-754 double sin()/floor and the same rounding\n"
"  (floor(v+0.5)) to regenerate in.u16 byte-identically.\n"
"\n"
"CAPTURED SCALARS (also in scalars.txt)\n"
"--------------------------------------\n"
"  ev_correction  = %.17g   (as stored: -corr_ev)\n"
"  corr_ev        = %.17g   (= ABS(ev_correction))\n"
"  black_delta    = %d\n"
"  iso_pattern    = %d   (negative -(i+1) auto-discovered encoding)\n"
"  black20        = %d\n"
"  white20        = %d\n"
"  white_bright20 = %d\n"
"  white_darkened = %d   (recomputed from match_exposures formula)\n"
"  dark_noise(20) = 512  (default 8 14-bit fallback *64; active_area={0,0,W,H})\n"
"\n"
"PARITY NOTES\n"
"------------\n"
"  - Output dither is OFF: randn05_cache is never initialized in the codebase\n"
"    (fast_randn_init has no caller), so convert_20_to_16bit reduces to\n"
"    (int)(value/16.0 + 0.5) clamped [0,65535]. Deterministic.\n"
"  - This is the SCALAR path (MLVAPP_DISABLE_AVX2=1). The AVX2 path is ~1 LSB\n"
"    drift and is NOT the parity reference.\n"
"  - For GPU parity, upload raw2ev/ev2raw/mix_curve/fullres_curve as-is and\n"
"    compile with --fmad=false to avoid FMA contraction in the float blends.\n",
                (int)n, (int)n,
                (unsigned)lut20, (unsigned)DUALISO_EV2RAW_LUT_COUNT,
                (unsigned)lut20, (unsigned)lut20,
                W, H,
                ORACLE_BLACK_14, ORACLE_WHITE_14, (ORACLE_WHITE_14 - ORACLE_BLACK_14),
                diso_ev_correction, corr_ev, black_delta_out, diso_pattern,
                black20, white20, white_bright20, white_darkened);
            fclose(f);
            fprintf(stderr, "[oracle] wrote README.txt\n");
        } else {
            fprintf(stderr, "[oracle] cannot write README.txt\n");
            all_ok = 0;
        }
    }

    free(fullres_curve);
    free(fullres_curve_double);
    free_dualiso_full20bit_scratch(&sc);
    free(in_copy);
    free(img);

    if (!all_ok) { fprintf(stderr, "[oracle] one or more dumps failed\n"); return 4; }
    fprintf(stderr, "[oracle] DONE\n");
    return 0;
}
