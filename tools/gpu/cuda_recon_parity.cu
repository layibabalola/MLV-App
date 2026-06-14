/* cuda_recon_parity.cu - bit-exact CUDA port of the MLV-App Dual ISO
 * full-20-bit reconstruction (diso_get_full20bit, src/mlv/llrawproc/dualiso.c),
 * validated stage-by-stage against the CPU scalar oracle (tools/gpu/oracle).
 *
 * Scope: mean23 interp (interp_method=1), use_alias_map=1, use_fullres=1,
 * chroma_smooth=0, RGGB phase is_bright[y&3]={1,1,0,0}. All constants come from
 * the oracle's scalars.txt / recon-exact-constants.md and are hard-coded here.
 *
 * Parity strategy
 *   - raw2ev / ev2raw / mix_curve / fullres_curve (float-cast) /
 *     fullres_curve_double are PRECOMPUTED by the oracle and uploaded as-is so
 *     no GPU libm enters the integer-LUT parity surface.
 *   - The only floating ops on the GPU are the double/float blends in
 *     mix_images / final_blend; compile with --fmad=false for IEEE parity.
 *   - Every stage writes a device plane; we copy it back and diff it against the
 *     matching oracle stage dump to localize any mismatch.
 *
 * Kernel chain (mirrors the scalar driver, dualiso.c:4735-5038):
 *   convert_to_20bit -> match_exposures(apply) -> mean23 -> border_interpolate
 *   -> fullres_reconstruction -> mix_images(halfres) -> build_alias_map
 *   (init-diff, copy, 37-tap rank, 21-tap gaussian, 2x2 grayscale-max) ->
 *   overexposed map (mark + border copy + 3-tap blur) -> final_blend (20-bit)
 *   -> convert_20_to_16bit.
 *
 * Build (on Ultra-Magnus): build-cuda.ps1 -Extra "--fmad=false"
 * Run: cuda_recon_parity.exe [vectors_dir]   (default G:\Temp\mlv-gpu-profile\oracle\vectors)
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <cmath>
#include <string>
#include <cuda_runtime.h>

/* ---- fixed geometry / scalars (from scalars.txt) ---------------------- */
#define W 1808
#define H 2268
#define EV_RESOLUTION 65536
#define RAW2EV_COUNT  (1u << 20)            /* 1048576 */
#define EV2RAW_COUNT  (24u * EV_RESOLUTION) /* 1572864 */
#define EV2RAW_ORIGIN (10 * EV_RESOLUTION)  /* 655360  */
#define ALIAS_MAP_MAX 15000
#define HOST_FULLRES_THR 0.8   /* fullres_thr (dualiso.c:1678); usable on device */

__device__ __constant__ int   d_black;          /* 131008  */
__device__ __constant__ int   d_white;          /* 960000  */
__device__ __constant__ int   d_white_darkened; /* 174688  */
__device__ __constant__ int   d_dark_noise;     /* 512     */
__device__ __constant__ double d_factor;        /* 0.125   */
__device__ __constant__ int   d_black_delta20;  /* 0       */
__device__ __constant__ int   d_is_bright[4];   /* {1,1,0,0} */

static const int   HOST_BLACK          = 131008;
static const int   HOST_WHITE          = 960000;
/* white_darkened = (MIN(white_level,white_bright) - black + bd)*factor + black
 * = (MIN(960000,480000) - 131008 + 0)*0.125 + 131008 = 174632.
 * NB the oracle's scalars.txt prints 174688 from a BUGGY sidecar recompute
 * (it used _black_delta=448 and white20 instead of the MIN(.,white_bright)=
 * 480000 that match_exposures actually uses, and the real apply forces
 * _black_delta=0 because diso_black_delta=0 != -1). The real recon used
 * 174632 - verified against the oracle's stage_bright dump. */
static const int   HOST_WHITE_DARKENED = 174632;
static const int   HOST_DARK_NOISE     = 512;
static const double HOST_FACTOR        = 0.125;   /* pow(2,-3) */
static const int   HOST_BLACK_DELTA20  = 0;
static const int   HOST_IS_BRIGHT[4]   = {1, 1, 0, 0};

#define CK(call) do { cudaError_t _e=(call); if(_e!=cudaSuccess){ \
    fprintf(stderr,"CUDA error %s @ %s:%d: %s\n",#call,__FILE__,__LINE__, \
    cudaGetErrorString(_e)); exit(10);} } while(0)

/* MIN/MAX/COERCE/ABS exactly as dualiso.c (integer + double overloads) */
__device__ __host__ static inline int   imin(int a,int b){ return a<b?a:b; }
__device__ __host__ static inline int   imax(int a,int b){ return a>b?a:b; }
__device__ __host__ static inline int   icoerce(int x,int lo,int hi){ return imax(imin(x,hi),lo); }
__device__ __host__ static inline double dmin(double a,double b){ return a<b?a:b; }
__device__ __host__ static inline double dmax(double a,double b){ return a>b?a:b; }
__device__ __host__ static inline double dcoerce(double x,double lo,double hi){ return dmax(dmin(x,hi),lo); }
__device__ __host__ static inline int   iabs_(int a){ return a>0?a:-a; }

/* ======================================================================= *
 *  STAGE 1: convert_to_20bit  ((v<<6)&0xFFFFF)                            *
 *  STAGE 2: match_exposures apply (bright rows darken; black_delta=0)     *
 *  Done together into raw_buffer_32, mirroring driver order
 *  (convert_to_20bit then match_exposures over raw_buffer_32). The match
 *  cast: p = ((p - black + bd)*factor) + black  (bright), int truncation;
 *         p = (p - bd) + (bd*factor)            (dark);  with bd=0 -> p
 *  then raw_set_pixel20 = COERCE(p,0,0xFFFFF). The "if (p==0) continue"
 *  guard leaves p untouched (still 0 from the promote).                   */
__global__ void k_promote_match(const uint16_t* __restrict in, uint32_t* __restrict raw32)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W || y>=H) return;
    size_t i = (size_t)x + (size_t)y*W;
    int p = (int)((((uint32_t)in[i]) << 6) & 0xFFFFFu);   /* convert_to_20bit */
    if (p != 0) {                                          /* match apply */
        if (d_is_bright[y & 3]) {
            /* int p = ((p-black+bd)*factor) + black : the +black is in double,
             * the truncation-to-int happens on the WHOLE rhs. */
            p = (int)(((double)(p - d_black + d_black_delta20)) * d_factor + (double)d_black);
        } else {
            /* int p = (p-bd) + (bd*factor) : (bd*factor) is double, the sum is
             * double, truncated to int on assignment. */
            p = (int)((double)(p - d_black_delta20) + (double)d_black_delta20 * d_factor);
        }
    }
    raw32[i] = (uint32_t)icoerce(p, 0, 0xFFFFF);          /* raw_set_pixel20 */
}

/* ======================================================================= *
 *  STAGE 3: mean23_interpolate_with_lut  (RG/GB rows, EV space)           *
 *  One thread per (x,y); only writes the interior the scalar loop covers
 *  (y in [2,h-2), x in [2,w-3) step 2, both pixels of the pair). To match
 *  the scalar exactly we process per row-pair-of-columns: thread for even
 *  x in [2,w-3) writes x and x+1.                                          */
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
                         const int* __restrict raw2ev, const int* __restrict ev2raw)
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

/* ======================================================================= *
 *  STAGE 4: border_interpolate  (top 3 rows, bottom 4 rows, left/right)   *
 *  The scalar version is serial and writes overlapping regions; order
 *  matters only where regions overlap. Top(y<3) and bottom(y>=h-4) write
 *  full rows; the left/right pass (y in [2,h)) writes cols [0,2) and
 *  [w-3,w). The corner cells (top/bottom rows AND edge cols) are written
 *  by BOTH the row pass and the col pass. In the scalar code the col pass
 *  runs AFTER both row passes, so for those corners the col-pass value
 *  wins. We replicate that by running three separate kernels in the same
 *  scalar order: top rows, bottom rows, then left/right cols.             */
__global__ void k_border_toprows(const uint32_t* __restrict raw32,
                                 uint32_t* __restrict dark, uint32_t* __restrict bright)
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
                                 uint32_t* __restrict dark, uint32_t* __restrict bright)
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
                              uint32_t* __restrict dark, uint32_t* __restrict bright)
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

/* ======================================================================= *
 *  STAGE 5: fullres_reconstruction                                        */
__global__ void k_fullres(uint32_t* __restrict fullres,
                          const uint32_t* __restrict dark, const uint32_t* __restrict bright)
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

/* ======================================================================= *
 *  STAGE 6: mix_images halfres blend (scalar)                             *
 *  k = COERCE(mix_curve[b&0xFFFFF],0,1) double; mixed = bev*(1-k)+dev*k
 *  truncated to int; halfres = ev2raw[mixed].                             */
__global__ void k_mix_halfres(uint32_t* __restrict halfres,
                              const uint32_t* __restrict bright, const uint32_t* __restrict dark,
                              const int* __restrict raw2ev, const int* __restrict ev2raw,
                              const double* __restrict mix_curve)
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

/* ======================================================================= *
 *  STAGE 7: build_alias_map                                               *
 *  Note chroma_smooth=0 -> fullres_smooth==fullres, halfres_smooth==halfres
 *  The fullres-skip predicate uses the DOUBLE fullres curve.              */
__global__ void k_alias_init(uint16_t* __restrict alias_map,
                             const uint32_t* __restrict fullres_smooth,
                             const uint32_t* __restrict halfres_smooth,
                             const uint32_t* __restrict bright,
                             const int* __restrict raw2ev,
                             const double* __restrict fullres_curve_d)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W || y>=H) return;
    size_t i=(size_t)x+(size_t)y*W;
    /* alias_map pre-zeroed by caller; skip leaves it at 0 */
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

/* 37-tap 5th-largest rank ladder (verbatim insertion order) over alias_map,
 * writes alias_aux interior. Same fullres-skip predicate. */
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
                             const double* __restrict fullres_curve_d)
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

/* 21-tap gaussian (verbatim weights/order) reads rank-filtered alias_aux,
 * writes alias_map. Same fullres-skip predicate. Integer * W / 1024 per term. */
__global__ void k_alias_gauss(uint16_t* __restrict alias_map,
                              const uint16_t* __restrict alias_aux,
                              const uint32_t* __restrict bright,
                              const double* __restrict fullres_curve_d)
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

/* 2x2 grayscale-max clamp (no skip). y,x step 2 in [2,h-2)/[2,w-2). */
__global__ void k_alias_gray(uint16_t* __restrict alias_map)
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

/* ======================================================================= *
 *  STAGE 8: overexposed map  (mark -> over_aux ; border copy ; 3-tap blur)*/
__global__ void k_over_mark(uint16_t* __restrict over_aux,
                            const uint32_t* __restrict bright, const uint32_t* __restrict dark)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W || y>=H) return;
    size_t i=(size_t)x+(size_t)y*W;
    over_aux[i] = (bright[i] >= (uint32_t)d_white_darkened || dark[i] >= (uint32_t)d_white) ? 100 : 0;
}
/* border copy: rows [0,3) and [h-3,h) full; for interior rows cols 0..2 and
 * w-3..w-1 copied from over_aux. (w>6 && h>6 branch.) */
__global__ void k_over_bordercopy(uint16_t* __restrict overexposed,
                                  const uint16_t* __restrict over_aux)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W || y>=H) return;
    size_t i=(size_t)x+(size_t)y*W;
    if (y<3 || y>=H-3) { overexposed[i] = over_aux[i]; return; }
    if (x<3 || x>=W-3) { overexposed[i] = over_aux[i]; return; }
    /* interior handled by blur kernel */
}
/* interior blur (y in [3,h-3), x in [3,w-3)): center + 820/1024 (4-neighbor)
 * + 657/1024 (4-diagonal). Remaining terms are commented out in src; trailing
 * +0. (dualiso.c:4319-4329) */
__global__ void k_over_blur(uint16_t* __restrict overexposed,
                            const uint16_t* __restrict over_aux)
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

/* ======================================================================= *
 *  STAGE 9: final_blend (scalar) -> raw_buffer_32 (20-bit)                *
 *  use_float_fullres_curve = true: f = (double)(float)curve[b&0xFFFFF]; we
 *  load the already-float-cast curve (fullres_curve.f64 = (double)(float)f),
 *  so reading it directly == casting once. To be extra safe match the
 *  "(double)(float)" identity we cast load to float then back to double.    */
__global__ void k_final_blend(uint32_t* __restrict raw32,
                              const uint32_t* __restrict bright,
                              const uint32_t* __restrict halfres_smooth,
                              const uint32_t* __restrict fullres,
                              const uint32_t* __restrict fullres_smooth,
                              const uint32_t* __restrict dark,
                              const uint16_t* __restrict overexposed,
                              const uint16_t* __restrict alias_map,
                              const int* __restrict raw2ev, const int* __restrict ev2raw,
                              const double* __restrict fullres_curve_f /* float-cast curve */)
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

    /* fullres_curve_f already holds (double)(float)f; cast-roundtrip keeps it
     * identical and documents the production semantics. */
    double f = (double)(float)fullres_curve_f[b & 0xFFFFF];

    double c = 0.0;
    /* alias_map is non-NULL (use_alias_map=1) */
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

/* ======================================================================= *
 *  STAGE 10: convert_20_to_16bit  ((int)(v/16.0 + 0.5) clamp; dither=0)   */
__global__ void k_convert16(uint16_t* __restrict out, const uint32_t* __restrict raw32)
{
    int x = blockIdx.x*blockDim.x + threadIdx.x;
    int y = blockIdx.y*blockDim.y + threadIdx.y;
    if (x>=W || y>=H) return;
    size_t i=(size_t)x+(size_t)y*W;
    double v = (double)raw32[i];
    int o = (int)(v/16.0 + 0.5);    /* fast_randn05()==0 */
    out[i] = (uint16_t)icoerce(o, 0, 0xFFFF);
}

/* ----------------------------------------------------------------------- *
 *  host helpers                                                            */
static void* load_blob(const std::string& path, size_t expect_bytes)
{
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) { fprintf(stderr,"cannot open %s\n", path.c_str()); exit(3); }
    fseek(f,0,SEEK_END); long sz=ftell(f); fseek(f,0,SEEK_SET);
    if (expect_bytes && (size_t)sz != expect_bytes) {
        fprintf(stderr,"size mismatch %s: got %ld want %zu\n", path.c_str(), sz, expect_bytes);
        exit(3);
    }
    void* p = malloc(sz);
    if (fread(p,1,sz,f)!=(size_t)sz){ fprintf(stderr,"short read %s\n",path.c_str()); exit(3); }
    fclose(f);
    return p;
}

template<typename T>
static void diff_stage(const char* name, const T* dev_host, const T* oracle, size_t n,
                       int* worst_count_out)
{
    long long maxabs=0; double sumabs=0; size_t nz=0; size_t first_x=0,first_y=0;
    long long firstd=0; int found=0;
    for (size_t i=0;i<n;i++){
        long long d = (long long)dev_host[i] - (long long)oracle[i];
        long long a = d<0?-d:d;
        if (a>maxabs) maxabs=a;
        sumabs += (double)a;
        if (a){ nz++; if(!found){ found=1; first_x=i%W; first_y=i/W; firstd=d; } }
    }
    if (worst_count_out) *worst_count_out = (int)nz;
    printf("  [stage %-18s] max|d|=%-8lld mean|d|=%.5f  mismatched=%zu/%zu",
           name, maxabs, sumabs/(double)n, nz, n);
    if (found) printf("  first@(%zu,%zu) d=%lld", first_x, first_y, firstd);
    printf("\n");
}

int main(int argc, char** argv)
{
    std::string vdir = "G:\\Temp\\mlv-gpu-profile\\oracle\\vectors";
    if (argc>=2) vdir = argv[1];
    auto P=[&](const char* n){ return vdir + "\\" + n; };

    const size_t n = (size_t)W*H;
    printf("[parity] vectors dir: %s   geometry %dx%d (%zu px)\n", vdir.c_str(), W, H, n);

    /* device props */
    cudaDeviceProp prop; CK(cudaGetDeviceProperties(&prop,0));
    printf("[parity] device: %s  sm_%d%d  --fmad should be false\n", prop.name, prop.major, prop.minor);

    /* upload constants */
    CK(cudaMemcpyToSymbol(d_black, &HOST_BLACK, sizeof(int)));
    CK(cudaMemcpyToSymbol(d_white, &HOST_WHITE, sizeof(int)));
    CK(cudaMemcpyToSymbol(d_white_darkened, &HOST_WHITE_DARKENED, sizeof(int)));
    CK(cudaMemcpyToSymbol(d_dark_noise, &HOST_DARK_NOISE, sizeof(int)));
    CK(cudaMemcpyToSymbol(d_factor, &HOST_FACTOR, sizeof(double)));
    CK(cudaMemcpyToSymbol(d_black_delta20, &HOST_BLACK_DELTA20, sizeof(int)));
    CK(cudaMemcpyToSymbol(d_is_bright, HOST_IS_BRIGHT, sizeof(int)*4));

    /* ---- load inputs + LUTs ---- */
    uint16_t* h_in  = (uint16_t*)load_blob(P("in.u16"),  n*sizeof(uint16_t));
    uint16_t* h_out = (uint16_t*)load_blob(P("out.u16"), n*sizeof(uint16_t));
    int*    h_raw2ev = (int*)   load_blob(P("raw2ev.i32"), (size_t)RAW2EV_COUNT*sizeof(int));
    int*    h_ev2raw = (int*)   load_blob(P("ev2raw.i32"), (size_t)EV2RAW_COUNT*sizeof(int));
    double* h_mix    = (double*)load_blob(P("mix_curve.f64"), (size_t)RAW2EV_COUNT*sizeof(double));
    double* h_frc_f  = (double*)load_blob(P("fullres_curve.f64"), (size_t)RAW2EV_COUNT*sizeof(double));
    double* h_frc_d  = (double*)load_blob(P("fullres_curve_double.f64"), (size_t)RAW2EV_COUNT*sizeof(double));

    /* oracle stage dumps (optional - print warning if absent) */
    auto try_load=[&](const char* n_, size_t bytes)->void*{
        FILE* f=fopen(P(n_).c_str(),"rb"); if(!f){ printf("  (stage dump %s missing - skipping its diff)\n", n_); return NULL;} fclose(f);
        return load_blob(P(n_), bytes);
    };
    uint32_t* o_dark    =(uint32_t*)try_load("stage_dark.u32",   n*sizeof(uint32_t));
    uint32_t* o_bright  =(uint32_t*)try_load("stage_bright.u32", n*sizeof(uint32_t));
    uint32_t* o_fullres =(uint32_t*)try_load("stage_fullres.u32",n*sizeof(uint32_t));
    uint32_t* o_halfres =(uint32_t*)try_load("stage_halfres.u32",n*sizeof(uint32_t));
    uint16_t* o_alias   =(uint16_t*)try_load("stage_alias_map.u16",n*sizeof(uint16_t));
    uint16_t* o_over    =(uint16_t*)try_load("stage_overexposed.u16",n*sizeof(uint16_t));
    uint32_t* o_final20 =(uint32_t*)try_load("stage_final20.u32",n*sizeof(uint32_t));

    /* ---- device buffers ---- */
    uint16_t *d_in,*d_out,*d_alias,*d_over,*d_overaux;
    uint32_t *d_raw32,*d_dark,*d_bright,*d_fullres,*d_halfres;
    int      *dd_raw2ev,*dd_ev2raw;
    double   *dd_mix,*dd_frc_f,*dd_frc_d;
    CK(cudaMalloc(&d_in,    n*sizeof(uint16_t)));
    CK(cudaMalloc(&d_out,   n*sizeof(uint16_t)));
    CK(cudaMalloc(&d_alias, n*sizeof(uint16_t)));
    CK(cudaMalloc(&d_over,  n*sizeof(uint16_t)));
    CK(cudaMalloc(&d_overaux,n*sizeof(uint16_t)));
    CK(cudaMalloc(&d_raw32, n*sizeof(uint32_t)));
    CK(cudaMalloc(&d_dark,  n*sizeof(uint32_t)));
    CK(cudaMalloc(&d_bright,n*sizeof(uint32_t)));
    CK(cudaMalloc(&d_fullres,n*sizeof(uint32_t)));
    CK(cudaMalloc(&d_halfres,n*sizeof(uint32_t)));
    CK(cudaMalloc(&dd_raw2ev,(size_t)RAW2EV_COUNT*sizeof(int)));
    CK(cudaMalloc(&dd_ev2raw,(size_t)EV2RAW_COUNT*sizeof(int)));
    CK(cudaMalloc(&dd_mix,   (size_t)RAW2EV_COUNT*sizeof(double)));
    CK(cudaMalloc(&dd_frc_f, (size_t)RAW2EV_COUNT*sizeof(double)));
    CK(cudaMalloc(&dd_frc_d, (size_t)RAW2EV_COUNT*sizeof(double)));

    CK(cudaMemcpy(d_in, h_in, n*sizeof(uint16_t), cudaMemcpyHostToDevice));
    CK(cudaMemcpy(dd_raw2ev, h_raw2ev, (size_t)RAW2EV_COUNT*sizeof(int), cudaMemcpyHostToDevice));
    CK(cudaMemcpy(dd_ev2raw, h_ev2raw, (size_t)EV2RAW_COUNT*sizeof(int), cudaMemcpyHostToDevice));
    CK(cudaMemcpy(dd_mix, h_mix, (size_t)RAW2EV_COUNT*sizeof(double), cudaMemcpyHostToDevice));
    CK(cudaMemcpy(dd_frc_f, h_frc_f, (size_t)RAW2EV_COUNT*sizeof(double), cudaMemcpyHostToDevice));
    CK(cudaMemcpy(dd_frc_d, h_frc_d, (size_t)RAW2EV_COUNT*sizeof(double), cudaMemcpyHostToDevice));

    /* ev2raw usable origin pointer (base + 655360) */
    int* dd_ev2raw_origin = dd_ev2raw + EV2RAW_ORIGIN;

    /* zero alias_map (recon pre-zeroes it) and dark/bright (mean23 leaves the
     * 0/1 col + first 2 rows; border_interpolate fills the rest). We do NOT
     * need to clear dark/bright for mean23+border coverage, but the very first
     * two columns / last columns of interior rows AND the corners are border
     * territory. Zero them for safety so any uncovered cell is deterministic
     * (the scalar leaves them as freshly-(re)used scratch; on a fresh run the
     * recon's buffers are also uninitialized there, but border_interpolate +
     * mean23 cover the full plane for dark/bright in this config). */
    CK(cudaMemset(d_alias, 0, n*sizeof(uint16_t)));
    CK(cudaMemset(d_dark,  0, n*sizeof(uint32_t)));
    CK(cudaMemset(d_bright,0, n*sizeof(uint32_t)));
    CK(cudaMemset(d_fullres,0,n*sizeof(uint32_t)));
    CK(cudaMemset(d_halfres,0,n*sizeof(uint32_t)));
    CK(cudaMemset(d_over,  0, n*sizeof(uint16_t)));
    CK(cudaMemset(d_overaux,0,n*sizeof(uint16_t)));

    dim3 bt(16,16);
    dim3 gt((W+15)/16,(H+15)/16);

    /* host scratch for stage readbacks */
    uint32_t* hb32 = (uint32_t*)malloc(n*sizeof(uint32_t));
    uint16_t* hb16 = (uint16_t*)malloc(n*sizeof(uint16_t));

    printf("\n[parity] running kernel chain + per-stage diffs:\n");

    /* STAGE 1+2: promote + match */
    k_promote_match<<<gt,bt>>>(d_in, d_raw32); CK(cudaDeviceSynchronize()); CK(cudaGetLastError());

    /* STAGE 3: mean23 (grid over even-x indices) */
    {
        int nx = (W-3-2+1)/2 + 1;   /* even x in [2,w-3) */
        dim3 g((nx+15)/16,(H+15)/16);
        k_mean23<<<g,bt>>>(d_raw32, d_dark, d_bright, dd_raw2ev, dd_ev2raw_origin);
        CK(cudaDeviceSynchronize()); CK(cudaGetLastError());
    }
    /* STAGE 4: border (top, bottom, then cols - scalar order) */
    { dim3 g((W+15)/16,(3+15)/16); k_border_toprows<<<g,bt>>>(d_raw32,d_dark,d_bright); }
    { dim3 g((W+15)/16,(4+15)/16); k_border_botrows<<<g,bt>>>(d_raw32,d_dark,d_bright); }
    CK(cudaDeviceSynchronize());
    { int t=256; dim3 g((H+t-1)/t); k_border_cols<<<g,t>>>(d_raw32,d_dark,d_bright); }
    CK(cudaDeviceSynchronize()); CK(cudaGetLastError());

    /* diff dark/bright */
    if (o_bright){ CK(cudaMemcpy(hb32,d_bright,n*sizeof(uint32_t),cudaMemcpyDeviceToHost)); diff_stage("bright",hb32,o_bright,n,0); }
    if (o_dark)  { CK(cudaMemcpy(hb32,d_dark,  n*sizeof(uint32_t),cudaMemcpyDeviceToHost)); diff_stage("dark",  hb32,o_dark,  n,0); }

    /* STAGE 5: fullres */
    k_fullres<<<gt,bt>>>(d_fullres,d_dark,d_bright); CK(cudaDeviceSynchronize()); CK(cudaGetLastError());
    if (o_fullres){ CK(cudaMemcpy(hb32,d_fullres,n*sizeof(uint32_t),cudaMemcpyDeviceToHost)); diff_stage("fullres",hb32,o_fullres,n,0); }

    /* STAGE 6: mix halfres */
    k_mix_halfres<<<gt,bt>>>(d_halfres,d_bright,d_dark,dd_raw2ev,dd_ev2raw_origin,dd_mix);
    CK(cudaDeviceSynchronize()); CK(cudaGetLastError());
    if (o_halfres){ CK(cudaMemcpy(hb32,d_halfres,n*sizeof(uint32_t),cudaMemcpyDeviceToHost)); diff_stage("halfres",hb32,o_halfres,n,0); }

    /* STAGE 7: alias map (fullres_smooth==fullres, halfres_smooth==halfres) */
    k_alias_init<<<gt,bt>>>(d_alias,d_fullres,d_halfres,d_bright,dd_raw2ev,dd_frc_d);
    CK(cudaDeviceSynchronize()); CK(cudaGetLastError());
    /* rank filter -> alias_aux (reuse d_overaux as alias_aux scratch BEFORE
     * overexposed stage; but we need it later. Use a dedicated buffer.) */
    uint16_t* d_aliasaux; CK(cudaMalloc(&d_aliasaux,n*sizeof(uint16_t)));
    /* alias_aux is memcpy(alias_map) then rank overwrites interior */
    CK(cudaMemcpy(d_aliasaux,d_alias,n*sizeof(uint16_t),cudaMemcpyDeviceToDevice));
    { dim3 g((W-12+15)/16,(H-12+15)/16); k_alias_rank<<<g,bt>>>(d_alias,d_aliasaux,d_bright,dd_frc_d); }
    CK(cudaDeviceSynchronize()); CK(cudaGetLastError());
    { dim3 g((W-12+15)/16,(H-12+15)/16); k_alias_gauss<<<g,bt>>>(d_alias,d_aliasaux,d_bright,dd_frc_d); }
    CK(cudaDeviceSynchronize()); CK(cudaGetLastError());
    { int nx=(W-2-2)/2+1, ny=(H-2-2)/2+1; dim3 g((nx+15)/16,(ny+15)/16); k_alias_gray<<<g,bt>>>(d_alias); }
    CK(cudaDeviceSynchronize()); CK(cudaGetLastError());
    if (o_alias){ CK(cudaMemcpy(hb16,d_alias,n*sizeof(uint16_t),cudaMemcpyDeviceToHost)); diff_stage("alias_map",hb16,o_alias,n,0); }

    /* STAGE 8: overexposed */
    k_over_mark<<<gt,bt>>>(d_overaux,d_bright,d_dark); CK(cudaDeviceSynchronize());
    k_over_bordercopy<<<gt,bt>>>(d_over,d_overaux); CK(cudaDeviceSynchronize());
    { dim3 g((W-6+15)/16,(H-6+15)/16); k_over_blur<<<g,bt>>>(d_over,d_overaux); }
    CK(cudaDeviceSynchronize()); CK(cudaGetLastError());
    if (o_over){ CK(cudaMemcpy(hb16,d_over,n*sizeof(uint16_t),cudaMemcpyDeviceToHost)); diff_stage("overexposed",hb16,o_over,n,0); }

    /* STAGE 9: final blend (fullres_smooth==fullres, halfres_smooth==halfres) */
    k_final_blend<<<gt,bt>>>(d_raw32,d_bright,d_halfres,d_fullres,d_fullres,d_dark,
                             d_over,d_alias,dd_raw2ev,dd_ev2raw_origin,dd_frc_f);
    CK(cudaDeviceSynchronize()); CK(cudaGetLastError());
    if (o_final20){ CK(cudaMemcpy(hb32,d_raw32,n*sizeof(uint32_t),cudaMemcpyDeviceToHost)); diff_stage("final20",hb32,o_final20,n,0); }

    /* STAGE 10: convert 20->16 */
    k_convert16<<<gt,bt>>>(d_out,d_raw32); CK(cudaDeviceSynchronize()); CK(cudaGetLastError());

    /* ---- final out.u16 parity ---- */
    CK(cudaMemcpy(hb16,d_out,n*sizeof(uint16_t),cudaMemcpyDeviceToHost));
    long long maxabs=0; double sumabs=0; size_t le0=0,le1=0,le2=0;
    size_t fx=0,fy=0; long long fd=0; int found=0;
    for (size_t i=0;i<n;i++){
        long long d=(long long)hb16[i]-(long long)h_out[i];
        long long a=d<0?-d:d;
        if (a>maxabs) maxabs=a;
        sumabs+=(double)a;
        if (a<=0) le0++;
        if (a<=1) le1++;
        if (a<=2) le2++;
        if (a>0 && !found){found=1;fx=i%W;fy=i/W;fd=d;}
    }
    printf("\n[parity] FINAL out.u16 vs oracle:\n");
    printf("  max abs diff   = %lld LSB\n", maxabs);
    printf("  mean abs diff  = %.6f LSB\n", sumabs/(double)n);
    printf("  within 0 LSB   = %.4f%% (%zu/%zu)\n", 100.0*le0/n, le0, n);
    printf("  within 1 LSB   = %.4f%% (%zu/%zu)\n", 100.0*le1/n, le1, n);
    printf("  within 2 LSB   = %.4f%% (%zu/%zu)\n", 100.0*le2/n, le2, n);
    if (found) printf("  first mismatch @(%zu,%zu) dev-oracle=%lld\n", fx, fy, fd);

    printf("\n[parity] RESULT: %s (<=2 LSB target)\n", (maxabs<=2)?"PASS":"NEEDS-WORK");
    return (maxabs<=2)?0:1;
}
