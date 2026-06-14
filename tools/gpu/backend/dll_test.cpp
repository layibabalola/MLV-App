/* dll_test.cpp - host harness proving the igpu_recon CUDA backend works
 * THROUGH the C ABI by runtime-loading the DLL.
 *
 * It does NOT link igpu_recon_cuda.dll at build time. Instead it
 * LoadLibrary()s it and GetProcAddress()es every ABI symbol - this is the
 * exact integration mechanism MLVApp (Qt/MinGW) will use, so a green run here
 * proves the DLL is loadable and callable across the pure-C boundary.
 *
 * Flow:
 *   1. LoadLibrary("igpu_recon_cuda.dll") + resolve the 8 ABI functions.
 *   2. Load the oracle in.u16 + the 4 LUTs from the vectors dir.
 *   3. create("cuda") / set_clip / set_luts / run(in.u16, CPU16).
 *   4. Compare run() output to oracle out.u16 -> expect max abs diff 0 LSB.
 *   5. Print last_timing (upload / kernel / download / total).
 *
 * Build (MSVC cl, on the host):
 *   cl /EHsc /O2 dll_test.cpp /Fe:dll_test.exe
 *   (no CUDA link; only Win32 LoadLibrary. See build-backend-dll.ps1.)
 *
 * Run:
 *   dll_test.exe [vectors_dir] [dll_path]
 *     vectors_dir default: G:\Temp\mlv-gpu-profile\oracle\vectors
 *     dll_path    default: .\igpu_recon_cuda.dll
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <string>
#include <windows.h>

#include "igpu_recon.h"

/* fixed oracle geometry / LUT sizes (README.txt) */
#define W 1808
#define H 2268
#define RAW2EV_COUNT (1u << 20)
#define EV2RAW_COUNT (24u * 65536u)

/* ABI function pointer typedefs */
typedef igpu_recon_backend* (*pfn_create)(const char*);
typedef void                (*pfn_destroy)(igpu_recon_backend*);
typedef int                 (*pfn_abi_version)(igpu_recon_backend*);
typedef const char*         (*pfn_describe)(igpu_recon_backend*);
typedef int                 (*pfn_set_clip)(igpu_recon_backend*, const igpu_recon_clip_t*);
typedef int                 (*pfn_set_luts)(igpu_recon_backend*, const igpu_recon_luts_t*);
typedef int                 (*pfn_run)(igpu_recon_backend*, const igpu_recon_frame_t*,
                                       const uint16_t*, igpu_recon_out_kind, uint16_t*, unsigned int);
typedef int                 (*pfn_last_timing)(igpu_recon_backend*, igpu_recon_timing_t*);

static void* load_blob(const std::string& path, size_t expect_bytes)
{
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) { fprintf(stderr, "cannot open %s\n", path.c_str()); exit(3); }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    if (expect_bytes && (size_t)sz != expect_bytes) {
        fprintf(stderr, "size mismatch %s: got %ld want %zu\n", path.c_str(), sz, expect_bytes);
        exit(3);
    }
    void* p = malloc(sz);
    if (fread(p, 1, sz, f) != (size_t)sz) { fprintf(stderr, "short read %s\n", path.c_str()); exit(3); }
    fclose(f);
    return p;
}

#define RESOLVE(var, type, name) \
    var = (type)GetProcAddress(dll, name); \
    if (!var) { fprintf(stderr, "GetProcAddress failed for %s\n", name); return 4; }

int main(int argc, char** argv)
{
    std::string vdir = "G:\\Temp\\mlv-gpu-profile\\oracle\\vectors";
    std::string dllpath = "igpu_recon_cuda.dll";
    if (argc >= 2) vdir = argv[1];
    if (argc >= 3) dllpath = argv[2];
    auto P = [&](const char* n) { return vdir + "\\" + n; };
    const size_t n = (size_t)W * H;

    printf("[dll_test] vectors dir : %s\n", vdir.c_str());
    printf("[dll_test] dll path    : %s\n", dllpath.c_str());

    /* ---- 1. runtime-load the DLL + resolve the ABI ---- */
    HMODULE dll = LoadLibraryA(dllpath.c_str());
    if (!dll) {
        fprintf(stderr, "[dll_test] LoadLibrary('%s') failed (err=%lu)\n",
                dllpath.c_str(), (unsigned long)GetLastError());
        return 4;
    }
    printf("[dll_test] LoadLibrary OK\n");

    pfn_create      f_create;
    pfn_destroy     f_destroy;
    pfn_abi_version f_abi;
    pfn_describe    f_describe;
    pfn_set_clip    f_set_clip;
    pfn_set_luts    f_set_luts;
    pfn_run         f_run;
    pfn_last_timing f_timing;
    RESOLVE(f_create,   pfn_create,      "igpu_recon_create");
    RESOLVE(f_destroy,  pfn_destroy,     "igpu_recon_destroy");
    RESOLVE(f_abi,      pfn_abi_version, "igpu_recon_abi_version");
    RESOLVE(f_describe, pfn_describe,    "igpu_recon_describe");
    RESOLVE(f_set_clip, pfn_set_clip,    "igpu_recon_set_clip");
    RESOLVE(f_set_luts, pfn_set_luts,    "igpu_recon_set_luts");
    RESOLVE(f_run,      pfn_run,         "igpu_recon_run");
    RESOLVE(f_timing,   pfn_last_timing, "igpu_recon_last_timing");
    printf("[dll_test] resolved all 8 ABI symbols via GetProcAddress\n");

    /* ---- 2. load oracle in.u16 + out.u16 + the 4 LUTs ---- */
    uint16_t* h_in  = (uint16_t*)load_blob(P("in.u16"),  n * sizeof(uint16_t));
    uint16_t* h_out = (uint16_t*)load_blob(P("out.u16"), n * sizeof(uint16_t));
    int*    h_raw2ev = (int*)   load_blob(P("raw2ev.i32"),     (size_t)RAW2EV_COUNT * sizeof(int));
    int*    h_ev2raw = (int*)   load_blob(P("ev2raw.i32"),     (size_t)EV2RAW_COUNT * sizeof(int));
    double* h_mix    = (double*)load_blob(P("mix_curve.f64"),  (size_t)RAW2EV_COUNT * sizeof(double));
    double* h_frc    = (double*)load_blob(P("fullres_curve.f64"), (size_t)RAW2EV_COUNT * sizeof(double));
    printf("[dll_test] loaded in.u16 + out.u16 + 4 LUTs\n");

    /* ---- 3. create / configure / run THROUGH the ABI ---- */
    igpu_recon_backend* b = f_create("cuda");
    if (!b) { fprintf(stderr, "[dll_test] create('cuda') returned NULL\n"); return 4; }
    printf("[dll_test] create OK  abi=%d  describe=\"%s\"\n", f_abi(b), f_describe(b));

    igpu_recon_clip_t clip;
    memset(&clip, 0, sizeof(clip));
    clip.width  = W;
    clip.height = H;
    clip.black_level = 131008;   /* black20  (scalars.txt) */
    clip.white_level = 960000;   /* white20  (scalars.txt) */
    clip.is_bright[0] = 1; clip.is_bright[1] = 1;
    clip.is_bright[2] = 0; clip.is_bright[3] = 0;
    if (f_set_clip(b, &clip) != 0) { fprintf(stderr, "[dll_test] set_clip failed\n"); return 4; }
    printf("[dll_test] set_clip OK  (%dx%d  black=%d white=%d)\n",
           clip.width, clip.height, clip.black_level, clip.white_level);

    igpu_recon_luts_t luts;
    memset(&luts, 0, sizeof(luts));
    luts.raw2ev        = h_raw2ev;
    luts.ev2raw        = h_ev2raw;
    luts.mix_curve     = h_mix;
    luts.fullres_curve = h_frc;
    luts.randn05       = NULL;   /* dither off in v1 */
    if (f_set_luts(b, &luts) != 0) { fprintf(stderr, "[dll_test] set_luts failed\n"); return 4; }
    printf("[dll_test] set_luts OK  (uploaded 4 LUTs)\n");

    igpu_recon_frame_t frame;
    memset(&frame, 0, sizeof(frame));
    frame.ev_correction       = 3.0;
    frame.black_delta         = 0;
    frame.white_darkened      = 174632;
    frame.dark_noise          = 512.0;
    frame.interp_method       = 1;
    frame.use_alias_map       = 1;
    frame.use_fullres         = 1;
    frame.chroma_smooth_method= 0;
    frame.apply_dither        = 0;

    uint16_t* result = (uint16_t*)malloc(n * sizeof(uint16_t));
    memset(result, 0, n * sizeof(uint16_t));

    int rc = f_run(b, &frame, h_in, IGPU_OUT_CPU16, result, 0);
    if (rc != 0) { fprintf(stderr, "[dll_test] run() returned %d\n", rc); return 4; }
    printf("[dll_test] run OK\n");

    /* ---- 4. compare to oracle out.u16 ---- */
    long long maxabs = 0; double sumabs = 0; size_t le0 = 0, le1 = 0, le2 = 0;
    size_t fx = 0, fy = 0; long long fd = 0; int found = 0;
    for (size_t i = 0; i < n; i++) {
        long long d = (long long)result[i] - (long long)h_out[i];
        long long a = d < 0 ? -d : d;
        if (a > maxabs) maxabs = a;
        sumabs += (double)a;
        if (a <= 0) le0++;
        if (a <= 1) le1++;
        if (a <= 2) le2++;
        if (a > 0 && !found) { found = 1; fx = i % W; fy = i / W; fd = d; }
    }
    printf("\n[dll_test] PARITY THROUGH THE ABI vs oracle out.u16:\n");
    printf("  max abs diff   = %lld LSB\n", maxabs);
    printf("  mean abs diff  = %.6f LSB\n", sumabs / (double)n);
    printf("  within 0 LSB   = %.4f%% (%zu/%zu)\n", 100.0 * le0 / n, le0, n);
    printf("  within 1 LSB   = %.4f%% (%zu/%zu)\n", 100.0 * le1 / n, le1, n);
    printf("  within 2 LSB   = %.4f%% (%zu/%zu)\n", 100.0 * le2 / n, le2, n);
    if (found) printf("  first mismatch @(%zu,%zu) dll-oracle=%lld\n", fx, fy, fd);

    /* ---- 5. timing ---- */
    igpu_recon_timing_t t;
    if (f_timing(b, &t) == 0) {
        printf("\n[dll_test] last_timing (ms):\n");
        printf("  upload   = %.3f\n", t.upload_ms);
        printf("  kernel   = %.3f\n", t.kernel_ms);
        printf("  download = %.3f\n", t.download_ms);
        printf("  total    = %.3f\n", t.total_ms);
    }

    f_destroy(b);
    FreeLibrary(dll);

    printf("\n[dll_test] RESULT: %s (0 LSB target, through the ABI)\n",
           (maxabs == 0) ? "PASS" : (maxabs <= 2 ? "CLOSE" : "FAIL"));
    return (maxabs == 0) ? 0 : 1;
}
