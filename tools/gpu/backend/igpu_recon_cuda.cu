/* igpu_recon_cuda.cu - CUDA backend implementing the igpu_recon C ABI
 * (tools/gpu/igpu_recon.h). This is the deployable form of the validated
 * parity kernel chain in tools/gpu/cuda_recon_parity.cu: the __global__
 * kernels below are copied verbatim from that file (0-LSB-correct vs the CPU
 * oracle, tools/gpu/oracle), wrapped behind the opaque backend handle and
 * exported with extern "C" __declspec(dllexport).
 *
 * WHY A DLL
 *   MLVApp is Qt/MinGW; nvcc requires MSVC. The MinGW app cannot compile CUDA,
 *   so the backend ships as an MSVC/nvcc DLL and the app loads it at runtime
 *   (LoadLibrary/GetProcAddress) through this pure-C ABI. A MinGW host calling
 *   an MSVC/nvcc DLL works precisely because the boundary is plain C (cdecl,
 *   no C++ name-mangling, POD structs).
 *
 * SCOPE / PARITY (v1)
 *   interp_method=1 (mean23), use_alias_map=1, use_fullres=1, chroma_smooth=0,
 *   RGGB phase is_bright[y&3]={1,1,0,0}. The host supplies the 4 precomputed
 *   LUTs (raw2ev, ev2raw, mix_curve, fullres_curve) so no GPU libm enters the
 *   integer-LUT parity surface; build with --fmad=false for IEEE blends.
 *
 * Build (on Ultra-Magnus, via vcvars + nvcc):
 *   nvcc -arch=sm_89 --fmad=false -shared -Xcompiler "/MD"
 *        -o igpu_recon_cuda.dll igpu_recon_cuda.cu
 *   (see backend/build-backend-dll.ps1; it also emits the import .lib/.exp)
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <cmath>
#include <string>
#include <cuda_runtime.h>

#include "igpu_recon.h"

/* Export mechanism note:
 *   The canonical igpu_recon.h declares the ABI prototypes WITHOUT any export
 *   attribute (it is the cross-compiler contract, deliberately attribute-free,
 *   and is NOT modified here). MSVC/nvcc forbids ADDING dllexport on a later
 *   redeclaration once a plain prototype has been seen, so we cannot put
 *   __declspec(dllexport) on the definitions below after including the header.
 *   Instead the symbols are exported by name via igpu_recon_cuda.def passed to
 *   the linker (/DEF), which is the standard header-independent export path and
 *   yields the identical extern "C" exported entry points + import lib.
 *   We still mark IGPU_API on the definitions so the intent is explicit; it is
 *   defined empty here (the .def does the exporting). */
#define IGPU_API

/* ---- fixed geometry / scalars (mirrors cuda_recon_parity.cu) ----------- */
#define EV_RESOLUTION 65536
#define RAW2EV_COUNT  (1u << 20)            /* 1048576 */
#define EV2RAW_COUNT  (24u * EV_RESOLUTION) /* 1572864 */
#define EV2RAW_ORIGIN (10 * EV_RESOLUTION)  /* 655360  */
#define ALIAS_MAP_MAX 15000
#define HOST_FULLRES_THR 0.8   /* fullres_thr (dualiso.c:1678) */
#define HOST_FACTOR   0.125    /* pow(2,-3); fixed for the 3 EV / 8x ISO ratio */

/* Device constants are set per set_clip()/set_luts(); kept in __constant__ so
 * the verbatim kernels reference them exactly as the parity probe did. */
__device__ __constant__ int   d_black;
__device__ __constant__ int   d_white;
__device__ __constant__ int   d_white_darkened;
__device__ __constant__ int   d_dark_noise;
__device__ __constant__ double d_factor;
__device__ __constant__ int   d_black_delta20;
__device__ __constant__ int   d_is_bright[4];

#define CK(call) do { cudaError_t _e=(call); if(_e!=cudaSuccess){ \
    fprintf(stderr,"[igpu_recon_cuda] CUDA error %s @ %s:%d: %s\n",#call,__FILE__,__LINE__, \
    cudaGetErrorString(_e)); return -1;} } while(0)

/* same as CK but returns NULL (for create()) */
#define CKN(call) do { cudaError_t _e=(call); if(_e!=cudaSuccess){ \
    fprintf(stderr,"[igpu_recon_cuda] CUDA error %s @ %s:%d: %s\n",#call,__FILE__,__LINE__, \
    cudaGetErrorString(_e)); return NULL;} } while(0)

/* MIN/MAX/COERCE/ABS exactly as dualiso.c (integer + double overloads) */
__device__ __host__ static inline int   imin(int a,int b){ return a<b?a:b; }
__device__ __host__ static inline int   imax(int a,int b){ return a>b?a:b; }
__device__ __host__ static inline int   icoerce(int x,int lo,int hi){ return imax(imin(x,hi),lo); }
__device__ __host__ static inline double dmin(double a,double b){ return a<b?a:b; }
__device__ __host__ static inline double dmax(double a,double b){ return a>b?a:b; }
__device__ __host__ static inline double dcoerce(double x,double lo,double hi){ return dmax(dmin(x,hi),lo); }
__device__ __host__ static inline int   iabs_(int a){ return a>0?a:-a; }

/* ===================================================================== *
 *  VALIDATED KERNEL CHAIN (verbatim from cuda_recon_parity.cu)          *
 *  The only edit is that W/H are passed as kernel args instead of being *
 *  compile-time #defines, so the backend handles per-clip geometry.     *
 * ===================================================================== */

/* STAGE 1+2: convert_to_20bit + match_exposures apply */
__global__ void k_promote_match(const uint16_t* __restrict in, uint32_t* __restrict raw32,
                                int W, int H)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W || y>=H) return;
    size_t i = (size_t)x + (size_t)y*W;
    int p = (int)((((uint32_t)in[i]) << 6) & 0xFFFFFu);   /* convert_to_20bit */
    if (p != 0) {                                          /* match apply */
        if (d_is_bright[y & 3]) {
            p = (int)(((double)(p - d_black + d_black_delta20)) * d_factor + (double)d_black);
        } else {
            p = (int)((double)(p - d_black_delta20) + (double)d_black_delta20 * d_factor);
        }
    }
    raw32[i] = (uint32_t)icoerce(p, 0, 0xFFFFF);          /* raw_set_pixel20 */
}

/* STAGE 3: mean23_interpolate_with_lut */
__device__ static inline int mean2_noerr(int a,int b,int white){
    if (a>=white || b>=white) return white;
    return (a+b)/2;
}
__device__ static inline int mean3_noerr(int a,int b,int c,int white){
    int m=(a+b+c)/3;
    if (a>=white||b>=white||c>=white) return imax(m,white);
    return m;
}
__global__ void k_mean23(const uint32_t* __restrict raw32,
                         uint32_t* __restrict dark, uint32_t* __restrict bright,
                         const int* __restrict raw2ev, const int* __restrict ev2raw,
                         int W, int H)
{
    int y  = blockIdx.y*blockDim.y + threadIdx.y;        /* row */
    int xi = blockIdx.x*blockDim.x + threadIdx.x;        /* index over even x's */
    if (y<2 || y>=H-2) return;
    int x = 2 + 2*xi;
    if (x >= W-3) return;

    int bright_row = d_is_bright[y & 3];
    int row_offset = y*W;
    const uint32_t* raw_row     = raw32 + row_offset;
    const uint32_t* raw_y_m2    = raw32 + (y-2)*W;
    const uint32_t* raw_y_p2    = raw32 + (y+2)*W;
    uint32_t* native = (bright_row ? bright : dark) + row_offset;
    uint32_t* interp = (bright_row ? dark : bright) + row_offset;
    int is_rg = (y % 2 == 0);
    int row_white_ev = raw2ev[bright_row ? d_white : d_white_darkened];
    int s = (d_is_bright[y&3] == d_is_bright[(y+1)&3]) ? -1 : 1;
    const uint32_t* raw_y_s   = raw32 + (y+s)*W;
    const uint32_t* raw_y_far = raw32 + (y-2*s)*W;

    if (is_rg) {
        int ri = mean2_noerr(raw2ev[raw_y_m2[x]], raw2ev[raw_y_p2[x]], row_white_ev);
        int gi = mean3_noerr(raw2ev[raw_y_s[x+2]], raw2ev[raw_y_s[x]], raw2ev[raw_y_far[x+1]], row_white_ev);
        interp[x]   = ev2raw[ri];
        interp[x+1] = ev2raw[gi];
        native[x]   = raw_row[x];
        native[x+1] = raw_row[x+1];
    } else {
        int bi = mean2_noerr(raw2ev[raw_y_m2[x+1]], raw2ev[raw_y_p2[x+1]], row_white_ev);
        int gi = mean3_noerr(raw2ev[raw_y_s[x+1]], raw2ev[raw_y_s[x-1]], raw2ev[raw_y_far[x]], row_white_ev);
        interp[x]   = ev2raw[gi];
        interp[x+1] = ev2raw[bi];
        native[x]   = raw_row[x];
        native[x+1] = raw_row[x+1];
    }
}

/* STAGE 4: border_interpolate (top rows, bottom rows, then cols - scalar order) */
__global__ void k_border_toprows(const uint32_t* __restrict raw32,
                                 uint32_t* __restrict dark, uint32_t* __restrict bright,
                                 int W, int H)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;   /* 0..2 */
    if (x>=W || y>=3) return;
    int br = d_is_bright[y & 3];
    uint32_t* native = br ? bright : dark;
    uint32_t* interp = br ? dark : bright;
    interp[x + y*W] = raw32[x + (y+2)*W];
    native[x + y*W] = raw32[x + y*W];
}
__global__ void k_border_botrows(const uint32_t* __restrict raw32,
                                 uint32_t* __restrict dark, uint32_t* __restrict bright,
                                 int W, int H)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int yy = blockIdx.y*blockDim.y + threadIdx.y;  /* 0..3 -> y = H-4+yy */
    if (x>=W || yy>=4) return;
    int y = H-4+yy;
    int br = d_is_bright[y & 3];
    uint32_t* native = br ? bright : dark;
    uint32_t* interp = br ? dark : bright;
    interp[x + y*W] = raw32[x + (y-2)*W];
    native[x + y*W] = raw32[x + y*W];
}
__global__ void k_border_cols(const uint32_t* __restrict raw32,
                              uint32_t* __restrict dark, uint32_t* __restrict bright,
                              int W, int H)
{
    int y = blockIdx.x*blockDim.x + threadIdx.x;   /* 2..H-1 */
    if (y<2 || y>=H) return;
    int br = d_is_bright[y & 3];
    uint32_t* native = br ? bright : dark;
    uint32_t* interp = br ? dark : bright;
    for (int x=0; x<2; x++) {
        interp[x + y*W] = raw32[x + (y-2)*W];
        native[x + y*W] = raw32[x + y*W];
    }
    for (int x=W-3; x<W; x++) {
        interp[x + y*W] = raw32[(x-2) + (y-2)*W];
        native[x + y*W] = raw32[(x-2) + y*W];
    }
}

/* STAGE 5: fullres_reconstruction */
__global__ void k_fullres(uint32_t* __restrict fullres,
                          const uint32_t* __restrict dark, const uint32_t* __restrict bright,
                          int W, int H)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W || y>=H) return;
    size_t i=(size_t)x+(size_t)y*W;
    if (d_is_bright[y & 3]) {
        uint32_t f = bright[i];
        fullres[i] = (f < (uint32_t)d_white_darkened) ? f : (uint32_t)imax((int)f, (int)dark[i]);
    } else {
        fullres[i] = dark[i];
    }
}

/* STAGE 6: mix_images halfres blend */
__global__ void k_mix_halfres(uint32_t* __restrict halfres,
                              const uint32_t* __restrict bright, const uint32_t* __restrict dark,
                              const int* __restrict raw2ev, const int* __restrict ev2raw,
                              const double* __restrict mix_curve,
                              int W, int H)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W || y>=H) return;
    size_t i=(size_t)x+(size_t)y*W;
    int b = (int)bright[i];
    int d = (int)dark[i];
    int bev = raw2ev[b];
    int dev = raw2ev[d];
    double k = dcoerce(mix_curve[b & 0xFFFFF], 0.0, 1.0);
    int mixed = (int)((double)bev*(1.0-k) + (double)dev*k);
    halfres[i] = ev2raw[mixed];
}

__global__ void k_mix_halfres_avx2(uint32_t* __restrict halfres,
                                   const uint32_t* __restrict bright,
                                   const uint32_t* __restrict dark,
                                   const int* __restrict raw2ev,
                                   const int* __restrict ev2raw,
                                   const double* __restrict mix_curve,
                                   int W, int H)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W || y>=H) return;
    size_t i=(size_t)x+(size_t)y*W;

    int b = (int)bright[i];
    int d = (int)dark[i];
    float bev = (float)raw2ev[b];
    float dev = (float)raw2ev[d];
    float k = (float)mix_curve[b & 0xFFFFF];
    k = fminf(fmaxf(k, 0.0f), 1.0f);

    float mixed_f = fmaf(k, dev - bev, bev);
    int mixed = __float2int_rz(mixed_f);
    mixed = icoerce(mixed, -10*EV_RESOLUTION, 14*EV_RESOLUTION - 1);
    halfres[i] = ev2raw[mixed];
}

/* STAGE 7: build_alias_map */
__global__ void k_alias_init(uint16_t* __restrict alias_map,
                             const uint32_t* __restrict fullres_smooth,
                             const uint32_t* __restrict halfres_smooth,
                             const uint32_t* __restrict bright,
                             const int* __restrict raw2ev,
                             const double* __restrict fullres_curve_d,
                             int W, int H)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W || y>=H) return;
    size_t i=(size_t)x+(size_t)y*W;
    if (fullres_curve_d[bright[i]] > HOST_FULLRES_THR) return;
    int f = (int)fullres_smooth[i];
    int hh = (int)halfres_smooth[i];
    int fe = raw2ev[f];
    int he = raw2ev[hh];
    int e_lin = iabs_(f - hh);
    e_lin = imax(e_lin - d_dark_noise*3/2, 0);
    int e_log = iabs_(fe - he);
    alias_map[i] = (uint16_t)imin(imin(e_lin/2, e_log/16), 65530);
}

#define TOP5(value) do { \
    uint16_t av=(uint16_t)(value); \
    if (av>best4){ if (av>best3){ best4=best3; \
        if (av>best2){ best3=best2; \
            if (av>best1){ best2=best1; \
                if (av>best0){ best1=best0; best0=av; } else { best1=av; } \
            } else { best2=av; } \
        } else { best3=av; } \
    } else { best4=av; } } \
} while(0)
#define TOP5R(row,off) TOP5((row)[x + (off)])
__global__ void k_alias_rank(const uint16_t* __restrict alias_map,
                             uint16_t* __restrict alias_aux,
                             const uint32_t* __restrict bright,
                             const double* __restrict fullres_curve_d,
                             int W, int H)
{
    int x = 6 + blockIdx.x*blockDim.x + threadIdx.x;
    int y = 6 + blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W-6 || y>=H-6) return;
    if (fullres_curve_d[bright[x + (size_t)y*W]] > HOST_FULLRES_THR) return;

    uint16_t best0=0,best1=0,best2=0,best3=0,best4=0;
    const uint16_t* ym6 = alias_map + (size_t)(y-6)*W;
    const uint16_t* ym4 = alias_map + (size_t)(y-4)*W;
    const uint16_t* ym2 = alias_map + (size_t)(y-2)*W;
    const uint16_t* y0  = alias_map + (size_t)(y  )*W;
    const uint16_t* yp2 = alias_map + (size_t)(y+2)*W;
    const uint16_t* yp4 = alias_map + (size_t)(y+4)*W;
    const uint16_t* yp6 = alias_map + (size_t)(y+6)*W;
                       TOP5R(ym6,-2); TOP5R(ym6,0); TOP5R(ym6,2);
        TOP5R(ym4,-4); TOP5R(ym4,-2); TOP5R(ym4,0); TOP5R(ym4,2); TOP5R(ym4,4);
    TOP5R(ym2,-6);TOP5R(ym2,-4);TOP5R(ym2,-2);TOP5R(ym2,0);TOP5R(ym2,2);TOP5R(ym2,4);TOP5R(ym2,6);
    TOP5R(y0, -6);TOP5R(y0, -4);TOP5R(y0, -2);TOP5R(y0, 0);TOP5R(y0, 2);TOP5R(y0, 4);TOP5R(y0, 6);
    TOP5R(yp2,-6);TOP5R(yp2,-4);TOP5R(yp2,-2);TOP5R(yp2,0);TOP5R(yp2,2);TOP5R(yp2,4);TOP5R(yp2,6);
        TOP5R(yp4,-4); TOP5R(yp4,-2); TOP5R(yp4,0); TOP5R(yp4,2); TOP5R(yp4,4);
                       TOP5R(yp6,-2); TOP5R(yp6,0); TOP5R(yp6,2);
    alias_aux[x + (size_t)y*W] = best4;
}

__global__ void k_alias_gauss(uint16_t* __restrict alias_map,
                              const uint16_t* __restrict alias_aux,
                              const uint32_t* __restrict bright,
                              const double* __restrict fullres_curve_d,
                              int W, int H)
{
    int x = 6 + blockIdx.x*blockDim.x + threadIdx.x;
    int y = 6 + blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W-6 || y>=H-6) return;
    if (fullres_curve_d[bright[x + (size_t)y*W]] > HOST_FULLRES_THR) return;
    const uint16_t* A = alias_aux;
    #define AX(dx,dy) ((int)A[(x+(dx)) + (size_t)(y+(dy))*W])
    int c =
    (AX(0,0))
    + (AX(0,-2)+AX(-2,0)+AX(2,0)+AX(0,2)) * 820 / 1024
    + (AX(-2,-2)+AX(2,-2)+AX(-2,2)+AX(2,2)) * 657 / 1024
    + (AX(0,-2)+AX(-2,0)+AX(2,0)+AX(0,2)) * 421 / 1024
    + (AX(-2,-2)+AX(2,-2)+AX(-2,-2)+AX(2,-2)+AX(-2,2)+AX(2,2)+AX(-2,2)+AX(2,2)) * 337 / 1024
    + (AX(-2,-2)+AX(2,-2)+AX(-2,2)+AX(2,2)) * 173 / 1024
    + (AX(0,-6)+AX(-6,0)+AX(6,0)+AX(0,6)) * 139 / 1024
    + (AX(-2,-6)+AX(2,-6)+AX(-6,-2)+AX(6,-2)+AX(-6,2)+AX(6,2)+AX(-2,6)+AX(2,6)) * 111 / 1024
    + (AX(-2,-6)+AX(2,-6)+AX(-6,-2)+AX(6,-2)+AX(-6,2)+AX(6,2)+AX(-2,6)+AX(2,6)) * 57 / 1024;
    #undef AX
    alias_map[x + (size_t)y*W] = (uint16_t)c;
}

__global__ void k_alias_gray(uint16_t* __restrict alias_map, int W, int H)
{
    int x = 2 + 2*(blockIdx.x*blockDim.x + threadIdx.x);
    int y = 2 + 2*(blockIdx.y*blockDim.y + threadIdx.y);
    if (x>=W-2 || y>=H-2) return;
    int a = alias_map[x   +     y*W];
    int b = alias_map[x+1 +     y*W];
    int c = alias_map[x   + (y+1)*W];
    int d = alias_map[x+1 + (y+1)*W];
    int C = imax(imax(a,b), imax(c,d));
    C = imin(C, ALIAS_MAP_MAX);
    alias_map[x   +     y*W] = (uint16_t)C;
    alias_map[x+1 +     y*W] = (uint16_t)C;
    alias_map[x   + (y+1)*W] = (uint16_t)C;
    alias_map[x+1 + (y+1)*W] = (uint16_t)C;
}

/* STAGE 8: overexposed map */
__global__ void k_over_mark(uint16_t* __restrict over_aux,
                            const uint32_t* __restrict bright, const uint32_t* __restrict dark,
                            int W, int H)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W || y>=H) return;
    size_t i=(size_t)x+(size_t)y*W;
    over_aux[i] = (bright[i] >= (uint32_t)d_white_darkened || dark[i] >= (uint32_t)d_white) ? 100 : 0;
}
__global__ void k_over_bordercopy(uint16_t* __restrict overexposed,
                                  const uint16_t* __restrict over_aux,
                                  int W, int H)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W || y>=H) return;
    size_t i=(size_t)x+(size_t)y*W;
    if (y<3 || y>=H-3) { overexposed[i] = over_aux[i]; return; }
    if (x<3 || x>=W-3) { overexposed[i] = over_aux[i]; return; }
    /* interior handled by blur kernel */
}
__global__ void k_over_blur(uint16_t* __restrict overexposed,
                            const uint16_t* __restrict over_aux,
                            int W, int H)
{
    int x = 3 + blockIdx.x*blockDim.x + threadIdx.x;
    int y = 3 + blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W-3 || y>=H-3) return;
    const uint16_t* O = over_aux;
    #define OX(dx,dy) ((int)O[(x+(dx)) + (size_t)(y+(dy))*W])
    int v =
    (OX(0,0))
    + (OX(0,-1)+OX(-1,0)+OX(1,0)+OX(0,1)) * 820 / 1024
    + (OX(-1,-1)+OX(1,-1)+OX(-1,1)+OX(1,1)) * 657 / 1024
    + 0;
    #undef OX
    overexposed[x + (size_t)y*W] = (uint16_t)v;
}

/* STAGE 9: final_blend -> raw_buffer_32 (20-bit) */
__global__ void k_final_blend(uint32_t* __restrict raw32,
                              const uint32_t* __restrict bright,
                              const uint32_t* __restrict halfres_smooth,
                              const uint32_t* __restrict fullres,
                              const uint32_t* __restrict fullres_smooth,
                              const uint32_t* __restrict dark,
                              const uint16_t* __restrict overexposed,
                              const uint16_t* __restrict alias_map,
                              const int* __restrict raw2ev, const int* __restrict ev2raw,
                              const double* __restrict fullres_curve_f,
                              int W, int H)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W || y>=H) return;
    size_t i=(size_t)x+(size_t)y*W;

    int b   = (int)bright[i];
    int hr  = (int)halfres_smooth[i];
    int fr  = (int)fullres[i];
    int frs = (int)fullres_smooth[i];
    int hrev  = raw2ev[hr];
    int frev  = raw2ev[fr];
    int frsev = raw2ev[frs];

    double f = (double)(float)fullres_curve_f[b & 0xFFFFF];

    double c = 0.0;
    {
        int co = (int)alias_map[i];
        c = dcoerce((double)co / (double)ALIAS_MAP_MAX, 0.0, 1.0);
    }
    double ovf = dcoerce((double)overexposed[i] / 200.0, 0.0, 1.0);
    c = dmax(c, ovf);

    double noisy_or_overexposed = dmax(ovf, 1.0 - f);
    f = dmax(f, c);
    double fev = noisy_or_overexposed * (double)frsev + (1.0 - noisy_or_overexposed) * (double)frev;

    int sig = ((int)dark[i] + (int)bright[i]) / 2;
    f = dmax(0.0, dmin(f, (double)(sig - d_black) / (double)(4*d_dark_noise)));

    int output = (int)((double)hrev * (1.0 - f) + fev * f);
    output = icoerce(output, -10*EV_RESOLUTION, 14*EV_RESOLUTION - 1);
    raw32[i] = (uint32_t)ev2raw[output];
}

/* STAGE 10: convert_20_to_16bit (dither off) */
__global__ void k_convert16(uint16_t* __restrict out, const uint32_t* __restrict raw32,
                            int W, int H)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W || y>=H) return;
    size_t i=(size_t)x+(size_t)y*W;
    double v = (double)raw32[i];
    int o = (int)(v/16.0 + 0.5);
    out[i] = (uint16_t)icoerce(o, 0, 0xFFFF);
}

/* STAGE 9+10: AVX2-compatible final_blend fused directly to 16-bit.
 * Mirrors final_blend_row_avx2(... fuse_to_16bit=1): float raw2ev values,
 * explicit fused multiply-adds, and per-row dither cursor `(y*7 + x) & 1023`.
 */
__global__ void k_final_blend16_avx2(uint16_t* __restrict out,
                                     const uint32_t* __restrict bright,
                                     const uint32_t* __restrict halfres_smooth,
                                     const uint32_t* __restrict fullres,
                                     const uint32_t* __restrict fullres_smooth,
                                     const uint32_t* __restrict dark,
                                     const uint16_t* __restrict overexposed,
                                     const uint16_t* __restrict alias_map,
                                     const int* __restrict raw2ev,
                                     const int* __restrict ev2raw,
                                     const double* __restrict fullres_curve_f,
                                     const float* __restrict randn05,
                                     int W, int H)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W || y>=H) return;
    size_t i=(size_t)x+(size_t)y*W;

    int b   = (int)bright[i];
    int hr  = (int)halfres_smooth[i];
    int fr  = (int)fullres[i];
    int frs = (int)fullres_smooth[i];
    int d   = (int)dark[i];

    float hrev  = (float)raw2ev[hr];
    float frev  = (float)raw2ev[fr];
    float frsev = (float)raw2ev[frs];
    float f = (float)fullres_curve_f[b & 0xFFFFF];

    int co = (int)alias_map[i];
    float c_amap = (float)co * (1.0f / (float)ALIAS_MAP_MAX);
    c_amap = fminf(fmaxf(c_amap, 0.0f), 1.0f);

    float ovf = (float)overexposed[i] * (1.0f / 200.0f);
    ovf = fminf(fmaxf(ovf, 0.0f), 1.0f);

    float c = fmaxf(c_amap, ovf);
    float noisy_or_over = fmaxf(ovf, 1.0f - f);
    f = fmaxf(f, c);

    float fev = fmaf(noisy_or_over, frsev - frev, frev);

    int sig = (d + b) >> 1;
    float cap = (float)(sig - d_black) * (1.0f / (4.0f * (float)d_dark_noise));
    f = fmaxf(0.0f, fminf(f, cap));

    float outf = fmaf(f, fev - hrev, hrev);
    int output = __float2int_rz(outf);
    output = icoerce(output, -10*EV_RESOLUTION, 14*EV_RESOLUTION - 1);

    int raw20 = ev2raw[output];
    float dither = randn05[(y * 7 + x) & 1023];
    float rounded = fmaf((float)raw20, 1.0f / 16.0f, dither);
    rounded += 0.5f;
    int v = __float2int_rz(rounded);
    out[i] = (uint16_t)icoerce(v, 0, 0xFFFF);
}

/* ===================================================================== *
 *  BACKEND HANDLE + ABI                                                  *
 * ===================================================================== */

struct igpu_recon_backend {
    int   device;
    char  describe[256];

    /* clip geometry / static levels */
    int   width, height;
    int   black, white;
    int   is_bright[4];
    int   have_clip;
    int   have_luts;

    /* device buffers (W*H sized; allocated on set_clip) */
    uint16_t *d_in, *d_out, *d_alias, *d_aliasaux, *d_over, *d_overaux;
    uint32_t *d_raw32, *d_dark, *d_bright, *d_fullres, *d_halfres;

    /* device LUTs (uploaded once on set_luts) */
    int      *dd_raw2ev, *dd_ev2raw;     /* ev2raw is BASE; origin = +EV2RAW_ORIGIN */
    double   *dd_mix, *dd_frc_f, *dd_frc_d;
    float    *df_randn05;

    /* timing */
    igpu_recon_timing_t last_timing;

    cudaEvent_t ev_start, ev_after_up, ev_after_kernel, ev_after_dl;
};

static void free_clip_buffers(igpu_recon_backend* b) {
    if (!b) return;
    if (b->d_in)       cudaFree(b->d_in);
    if (b->d_out)      cudaFree(b->d_out);
    if (b->d_alias)    cudaFree(b->d_alias);
    if (b->d_aliasaux) cudaFree(b->d_aliasaux);
    if (b->d_over)     cudaFree(b->d_over);
    if (b->d_overaux)  cudaFree(b->d_overaux);
    if (b->d_raw32)    cudaFree(b->d_raw32);
    if (b->d_dark)     cudaFree(b->d_dark);
    if (b->d_bright)   cudaFree(b->d_bright);
    if (b->d_fullres)  cudaFree(b->d_fullres);
    if (b->d_halfres)  cudaFree(b->d_halfres);
    b->d_in=b->d_out=b->d_alias=b->d_aliasaux=b->d_over=b->d_overaux=NULL;
    b->d_raw32=b->d_dark=b->d_bright=b->d_fullres=b->d_halfres=NULL;
}

static void free_lut_buffers(igpu_recon_backend* b) {
    if (!b) return;
    if (b->dd_raw2ev) cudaFree(b->dd_raw2ev);
    if (b->dd_ev2raw) cudaFree(b->dd_ev2raw);
    if (b->dd_mix)    cudaFree(b->dd_mix);
    if (b->dd_frc_f)  cudaFree(b->dd_frc_f);
    if (b->dd_frc_d)  cudaFree(b->dd_frc_d);
    if (b->df_randn05) cudaFree(b->df_randn05);
    b->dd_raw2ev=b->dd_ev2raw=NULL;
    b->dd_mix=b->dd_frc_f=b->dd_frc_d=NULL;
    b->df_randn05=NULL;
}

extern "C" {

IGPU_API
igpu_recon_backend* igpu_recon_create(const char* backend_name)
{
    if (backend_name && strcmp(backend_name, "cuda") != 0) {
        fprintf(stderr, "[igpu_recon_cuda] unsupported backend '%s' (this DLL is cuda)\n",
                backend_name);
        return NULL;
    }
    int devcount = 0;
    cudaError_t e = cudaGetDeviceCount(&devcount);
    if (e != cudaSuccess || devcount < 1) {
        fprintf(stderr, "[igpu_recon_cuda] no CUDA device: %s\n",
                cudaGetErrorString(e));
        return NULL;
    }
    int dev = 0;
    CKN(cudaSetDevice(dev));
    cudaDeviceProp prop;
    CKN(cudaGetDeviceProperties(&prop, dev));

    igpu_recon_backend* b = (igpu_recon_backend*)calloc(1, sizeof(igpu_recon_backend));
    if (!b) return NULL;
    b->device = dev;
    snprintf(b->describe, sizeof(b->describe), "CUDA / %s", prop.name);

    /* timing events */
    if (cudaEventCreate(&b->ev_start)        != cudaSuccess ||
        cudaEventCreate(&b->ev_after_up)     != cudaSuccess ||
        cudaEventCreate(&b->ev_after_kernel) != cudaSuccess ||
        cudaEventCreate(&b->ev_after_dl)     != cudaSuccess) {
        fprintf(stderr, "[igpu_recon_cuda] cudaEventCreate failed\n");
        free(b);
        return NULL;
    }
    return b;
}

IGPU_API
void igpu_recon_destroy(igpu_recon_backend* b)
{
    if (!b) return;
    free_clip_buffers(b);
    free_lut_buffers(b);
    if (b->ev_start)        cudaEventDestroy(b->ev_start);
    if (b->ev_after_up)     cudaEventDestroy(b->ev_after_up);
    if (b->ev_after_kernel) cudaEventDestroy(b->ev_after_kernel);
    if (b->ev_after_dl)     cudaEventDestroy(b->ev_after_dl);
    free(b);
}

IGPU_API
int igpu_recon_abi_version(igpu_recon_backend* b)
{
    (void)b;
    return IGPU_RECON_ABI_VERSION;
}

IGPU_API
const char* igpu_recon_describe(igpu_recon_backend* b)
{
    if (!b) return "CUDA / (no backend)";
    return b->describe;
}

IGPU_API
int igpu_recon_set_clip(igpu_recon_backend* b, const igpu_recon_clip_t* clip)
{
    if (!b || !clip) return -1;
    if (clip->width <= 0 || clip->height <= 0) return -1;

    CK(cudaSetDevice(b->device));

    /* (re)allocate device buffers sized to the clip */
    free_clip_buffers(b);
    b->width  = clip->width;
    b->height = clip->height;
    b->black  = clip->black_level;
    b->white  = clip->white_level;
    for (int i=0;i<4;i++) b->is_bright[i] = clip->is_bright[i];

    const size_t n = (size_t)b->width * (size_t)b->height;
    CK(cudaMalloc(&b->d_in,      n*sizeof(uint16_t)));
    CK(cudaMalloc(&b->d_out,     n*sizeof(uint16_t)));
    CK(cudaMalloc(&b->d_alias,   n*sizeof(uint16_t)));
    CK(cudaMalloc(&b->d_aliasaux,n*sizeof(uint16_t)));
    CK(cudaMalloc(&b->d_over,    n*sizeof(uint16_t)));
    CK(cudaMalloc(&b->d_overaux, n*sizeof(uint16_t)));
    CK(cudaMalloc(&b->d_raw32,   n*sizeof(uint32_t)));
    CK(cudaMalloc(&b->d_dark,    n*sizeof(uint32_t)));
    CK(cudaMalloc(&b->d_bright,  n*sizeof(uint32_t)));
    CK(cudaMalloc(&b->d_fullres, n*sizeof(uint32_t)));
    CK(cudaMalloc(&b->d_halfres, n*sizeof(uint32_t)));

    /* push the static per-clip constants to __constant__.
     * white_darkened is recomputed in set_clip exactly as the engine does,
     * so the validated parity constant (174632 for the oracle config) is
     * REPRODUCED from the levels rather than hard-coded:
     *   dualiso.c:4828-4833  white_bright = ((white20/64)/2)*64
     *   dualiso.c:2465,2548  white = MIN(white20, white_bright);
     *                        white_darkened = (white - black + bd)*factor + black
     * with bd=0 and factor=0.125 (the 3-EV / 8x ISO ratio path).
     * Check: black20=131008 white20=960000 ->
     *   white_bright = ((960000/64)/2)*64 = 480000
     *   white = MIN(960000,480000) = 480000
     *   white_darkened = (480000-131008)*0.125 + 131008 = 174632  (matches oracle) */
    int black20 = b->black;
    int white20 = b->white;
    int white_bright = ((white20 / 64) / 2) * 64;
    int wmin = (white20 < white_bright) ? white20 : white_bright;
    int black_delta20 = 0;
    int white_darkened = (int)(((double)(wmin - black20 + black_delta20)) * HOST_FACTOR) + black20;
    /* dark_noise default 8 (14-bit) * 64 = 512 in 20-bit; v1 fixed. */
    int dark_noise20 = 512;
    double factor = HOST_FACTOR;

    CK(cudaMemcpyToSymbol(d_black,          &black20,        sizeof(int)));
    CK(cudaMemcpyToSymbol(d_white,          &white20,        sizeof(int)));
    CK(cudaMemcpyToSymbol(d_white_darkened, &white_darkened, sizeof(int)));
    CK(cudaMemcpyToSymbol(d_dark_noise,     &dark_noise20,   sizeof(int)));
    CK(cudaMemcpyToSymbol(d_factor,         &factor,         sizeof(double)));
    CK(cudaMemcpyToSymbol(d_black_delta20,  &black_delta20,  sizeof(int)));
    CK(cudaMemcpyToSymbol(d_is_bright,      b->is_bright,    sizeof(int)*4));

    b->have_clip = 1;
    return 0;
}

IGPU_API
int igpu_recon_set_luts(igpu_recon_backend* b, const igpu_recon_luts_t* luts)
{
    if (!b || !luts) return -1;
    CK(cudaSetDevice(b->device));

    /* allocate LUT buffers if not already present */
    if (!b->dd_raw2ev) CK(cudaMalloc(&b->dd_raw2ev, (size_t)RAW2EV_COUNT*sizeof(int)));
    if (!b->dd_ev2raw) CK(cudaMalloc(&b->dd_ev2raw, (size_t)EV2RAW_COUNT*sizeof(int)));
    if (!b->dd_mix)    CK(cudaMalloc(&b->dd_mix,    (size_t)RAW2EV_COUNT*sizeof(double)));
    if (!b->dd_frc_f)  CK(cudaMalloc(&b->dd_frc_f,  (size_t)RAW2EV_COUNT*sizeof(double)));
    if (!b->dd_frc_d)  CK(cudaMalloc(&b->dd_frc_d,  (size_t)RAW2EV_COUNT*sizeof(double)));

    /* NULL pointer means "unchanged since last set" per the ABI contract. */
    if (luts->raw2ev)
        CK(cudaMemcpy(b->dd_raw2ev, luts->raw2ev, (size_t)RAW2EV_COUNT*sizeof(int),  cudaMemcpyHostToDevice));
    if (luts->ev2raw)
        CK(cudaMemcpy(b->dd_ev2raw, luts->ev2raw, (size_t)EV2RAW_COUNT*sizeof(int),  cudaMemcpyHostToDevice));
    if (luts->mix_curve)
        CK(cudaMemcpy(b->dd_mix,    luts->mix_curve, (size_t)RAW2EV_COUNT*sizeof(double), cudaMemcpyHostToDevice));
    if (luts->fullres_curve) {
        /* the ABI exposes ONE fullres curve. final_blend uses the float-cast
         * curve; alias_map uses the double curve. fullres_curve.f64 was built
         * as (double)(float)f, so the float-cast and double values coincide to
         * within the float<->double roundtrip the kernels already perform.
         * Upload the same host curve to both device slots. */
        CK(cudaMemcpy(b->dd_frc_f, luts->fullres_curve, (size_t)RAW2EV_COUNT*sizeof(double), cudaMemcpyHostToDevice));
        CK(cudaMemcpy(b->dd_frc_d, luts->fullres_curve, (size_t)RAW2EV_COUNT*sizeof(double), cudaMemcpyHostToDevice));
    }
    if (luts->randn05) {
        if (!b->df_randn05) CK(cudaMalloc(&b->df_randn05, (size_t)1024*sizeof(float)));
        CK(cudaMemcpy(b->df_randn05, luts->randn05, (size_t)1024*sizeof(float), cudaMemcpyHostToDevice));
    }

    b->have_luts = 1;
    return 0;
}

IGPU_API
int igpu_recon_run(igpu_recon_backend* b,
                   const igpu_recon_frame_t* frame,
                   const uint16_t* in_bayer14,
                   igpu_recon_out_kind out_kind,
                   uint16_t* out_bayer16,
                   unsigned int gl_texture)
{
    (void)gl_texture;
    if (!b || !frame || !in_bayer14) return -1;
    if (!b->have_clip || !b->have_luts) {
        fprintf(stderr, "[igpu_recon_cuda] run() before set_clip/set_luts\n");
        return -1;
    }
    if (frame->interp_method != 1 ||
        !frame->use_alias_map ||
        !frame->use_fullres ||
        frame->chroma_smooth_method != 0 ||
        (frame->apply_dither != 0 && frame->apply_dither != 1)) {
        fprintf(stderr, "[igpu_recon_cuda] unsupported v1 frame controls "
                        "(interp=%d alias=%d fullres=%d chroma=%d dither=%d)\n",
                frame->interp_method,
                frame->use_alias_map,
                frame->use_fullres,
                frame->chroma_smooth_method,
                frame->apply_dither);
        return 3;
    }
    if (frame->apply_dither && !b->df_randn05) {
        fprintf(stderr, "[igpu_recon_cuda] dither requested without randn05 LUT\n");
        return 3;
    }
    if (out_kind == IGPU_OUT_GL_TEXTURE) {
        /* interop path not implemented in v1; return non-zero per spec. */
        return 2;
    }
    if (out_kind != IGPU_OUT_CPU16 || !out_bayer16) return -1;

    CK(cudaSetDevice(b->device));

    const int W = b->width, H = b->height;
    const size_t n = (size_t)W * (size_t)H;
    int* dd_ev2raw_origin = b->dd_ev2raw + EV2RAW_ORIGIN;

    int black20 = b->black;
    int white20 = b->white;
    int white_darkened = frame->white_darkened;
    int black_delta20 = frame->black_delta;
    int dark_noise20 = (int)(frame->dark_noise + 0.5);
    double corr_ev = fabs(frame->ev_correction);
    double factor = pow(2.0, -corr_ev);

    if (white_darkened <= 0 || dark_noise20 <= 0 || corr_ev < 0.5) {
        fprintf(stderr, "[igpu_recon_cuda] invalid frame constants "
                        "(white_darkened=%d dark_noise=%d corr_ev=%.4f)\n",
                white_darkened, dark_noise20, corr_ev);
        return 3;
    }

    CK(cudaMemcpyToSymbol(d_black,          &black20,        sizeof(int)));
    CK(cudaMemcpyToSymbol(d_white,          &white20,        sizeof(int)));
    CK(cudaMemcpyToSymbol(d_white_darkened, &white_darkened, sizeof(int)));
    CK(cudaMemcpyToSymbol(d_dark_noise,     &dark_noise20,   sizeof(int)));
    CK(cudaMemcpyToSymbol(d_factor,         &factor,         sizeof(double)));
    CK(cudaMemcpyToSymbol(d_black_delta20,  &black_delta20,  sizeof(int)));
    CK(cudaMemcpyToSymbol(d_is_bright,      b->is_bright,    sizeof(int)*4));

    dim3 bt(16,16);
    dim3 gt((W+15)/16,(H+15)/16);

    /* scratch buffers that the recon pre-zeroes */
    CK(cudaMemset(b->d_alias,   0, n*sizeof(uint16_t)));
    CK(cudaMemset(b->d_dark,    0, n*sizeof(uint32_t)));
    CK(cudaMemset(b->d_bright,  0, n*sizeof(uint32_t)));
    CK(cudaMemset(b->d_fullres, 0, n*sizeof(uint32_t)));
    CK(cudaMemset(b->d_halfres, 0, n*sizeof(uint32_t)));
    CK(cudaMemset(b->d_over,    0, n*sizeof(uint16_t)));
    CK(cudaMemset(b->d_overaux, 0, n*sizeof(uint16_t)));

    /* ---- timed: H2D upload (14-bit Bayer) ---- */
    CK(cudaEventRecord(b->ev_start, 0));
    CK(cudaMemcpy(b->d_in, in_bayer14, n*sizeof(uint16_t), cudaMemcpyHostToDevice));
    CK(cudaEventRecord(b->ev_after_up, 0));

    /* ---- timed: kernel chain ---- */
    /* STAGE 1+2 */
    k_promote_match<<<gt,bt>>>(b->d_in, b->d_raw32, W, H);

    /* STAGE 3: mean23 over even-x indices */
    {
        int nx = (W-3-2+1)/2 + 1;
        dim3 g((nx+15)/16,(H+15)/16);
        k_mean23<<<g,bt>>>(b->d_raw32, b->d_dark, b->d_bright,
                           b->dd_raw2ev, dd_ev2raw_origin, W, H);
    }
    /* STAGE 4: border (top, bottom, then cols - scalar order) */
    { dim3 g((W+15)/16,(3+15)/16); k_border_toprows<<<g,bt>>>(b->d_raw32,b->d_dark,b->d_bright,W,H); }
    { dim3 g((W+15)/16,(4+15)/16); k_border_botrows<<<g,bt>>>(b->d_raw32,b->d_dark,b->d_bright,W,H); }
    { int t=256; dim3 g((H+t-1)/t); k_border_cols<<<g,t>>>(b->d_raw32,b->d_dark,b->d_bright,W,H); }

    /* STAGE 5: fullres */
    k_fullres<<<gt,bt>>>(b->d_fullres,b->d_dark,b->d_bright,W,H);

    /* STAGE 6: mix halfres */
    if (frame->apply_dither) {
        k_mix_halfres_avx2<<<gt,bt>>>(b->d_halfres,b->d_bright,b->d_dark,
                                      b->dd_raw2ev,dd_ev2raw_origin,b->dd_mix,W,H);
    } else {
        k_mix_halfres<<<gt,bt>>>(b->d_halfres,b->d_bright,b->d_dark,
                                 b->dd_raw2ev,dd_ev2raw_origin,b->dd_mix,W,H);
    }

    /* STAGE 7: alias map (fullres_smooth==fullres, halfres_smooth==halfres) */
    k_alias_init<<<gt,bt>>>(b->d_alias,b->d_fullres,b->d_halfres,b->d_bright,
                            b->dd_raw2ev,b->dd_frc_d,W,H);
    CK(cudaMemcpy(b->d_aliasaux,b->d_alias,n*sizeof(uint16_t),cudaMemcpyDeviceToDevice));
    { dim3 g((W-12+15)/16,(H-12+15)/16); k_alias_rank<<<g,bt>>>(b->d_alias,b->d_aliasaux,b->d_bright,b->dd_frc_d,W,H); }
    { dim3 g((W-12+15)/16,(H-12+15)/16); k_alias_gauss<<<g,bt>>>(b->d_alias,b->d_aliasaux,b->d_bright,b->dd_frc_d,W,H); }
    { int nx=(W-2-2)/2+1, ny=(H-2-2)/2+1; dim3 g((nx+15)/16,(ny+15)/16); k_alias_gray<<<g,bt>>>(b->d_alias,W,H); }

    /* STAGE 8: overexposed */
    k_over_mark<<<gt,bt>>>(b->d_overaux,b->d_bright,b->d_dark,W,H);
    k_over_bordercopy<<<gt,bt>>>(b->d_over,b->d_overaux,W,H);
    { dim3 g((W-6+15)/16,(H-6+15)/16); k_over_blur<<<g,bt>>>(b->d_over,b->d_overaux,W,H); }

    if (frame->apply_dither) {
        /* STAGE 9+10: default AVX2 export shape, fused to 16-bit with row dither. */
        k_final_blend16_avx2<<<gt,bt>>>(b->d_out,b->d_bright,b->d_halfres,b->d_fullres,b->d_fullres,
                                        b->d_dark,b->d_over,b->d_alias,
                                        b->dd_raw2ev,dd_ev2raw_origin,b->dd_frc_f,b->df_randn05,W,H);
    } else {
        /* STAGE 9: scalar export shape, final blend to raw32. */
        k_final_blend<<<gt,bt>>>(b->d_raw32,b->d_bright,b->d_halfres,b->d_fullres,b->d_fullres,
                                 b->d_dark,b->d_over,b->d_alias,
                                 b->dd_raw2ev,dd_ev2raw_origin,b->dd_frc_f,W,H);

        /* STAGE 10: convert 20->16 without AVX2 row dither. */
        k_convert16<<<gt,bt>>>(b->d_out,b->d_raw32,W,H);
    }

    CK(cudaGetLastError());
    CK(cudaEventRecord(b->ev_after_kernel, 0));

    /* ---- timed: D2H download ---- */
    CK(cudaMemcpy(out_bayer16, b->d_out, n*sizeof(uint16_t), cudaMemcpyDeviceToHost));
    CK(cudaEventRecord(b->ev_after_dl, 0));
    CK(cudaEventSynchronize(b->ev_after_dl));

    /* collect timings */
    float up=0, kern=0, dl=0, tot=0;
    CK(cudaEventElapsedTime(&up,   b->ev_start,        b->ev_after_up));
    CK(cudaEventElapsedTime(&kern, b->ev_after_up,     b->ev_after_kernel));
    CK(cudaEventElapsedTime(&dl,   b->ev_after_kernel, b->ev_after_dl));
    CK(cudaEventElapsedTime(&tot,  b->ev_start,        b->ev_after_dl));
    b->last_timing.upload_ms   = (double)up;
    b->last_timing.match_ms    = 0.0;   /* CPU match is host-side; not run here */
    b->last_timing.kernel_ms   = (double)kern;
    b->last_timing.download_ms = (double)dl;
    b->last_timing.total_ms    = (double)tot;
    return 0;
}

IGPU_API
int igpu_recon_last_timing(igpu_recon_backend* b, igpu_recon_timing_t* t)
{
    if (!b || !t) return -1;
    *t = b->last_timing;
    return 0;
}

} /* extern "C" */
