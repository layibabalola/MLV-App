/* cuda_debayer_parity.cu - bit-exact CUDA port of MLV-App's PLAYBACK bilinear
 * debayer (debayerBasicU16 -> debayer_basic_u16_rows_scalar,
 * src/debayer/debayer.c:63-143), validated against a verbatim CPU reference.
 *
 * WHY this debayer: the playback profiler reports playback_debayer_effective=
 * bilinear, i.e. debayerBasicU16 is the demosaic that runs during playback.
 * The AVX2 fast path in debayer.c is itself a bit-exact SIMD lowering of the
 * scalar row kernel (per the file's own header comment: 4-input averages use
 * 32-bit truncation, 2-input averages use the avg-of-avg trick to match the
 * scalar `(a+b)>>1` truncation). So the SCALAR row kernel is the canonical
 * reference, and matching it is matching the engine.
 *
 * Scope / contract (mirrors the scalar kernel exactly):
 *   - Input: 16-bit single-channel Bayer (the recon output: 16-bit Bayer).
 *     bit_shift = 0 on the playback path (recon already emits 16-bit), so the
 *     pre-shift loop in debayerBasicU16 is a no-op and is omitted here.
 *   - Output: interleaved RGB16 (3 * width * height uint16).
 *   - Interior: the 2x2-block kernel. Each (x,Y) step with x in [1,width-1)
 *     step 2 and Y a top-row flat offset writes the 4 RGB pixels at
 *     (x..x+1, row..row+1) using the fixed 4-input/2-input average pattern.
 *   - Edges: first/last column of each row pair replicate their inward
 *     neighbour; the top and bottom border rows are filled by the two memcpy
 *     fixups in debayerBasicU16 (top row := row1, bottom row := row h-2).
 *
 * The CUDA kernel reproduces the scalar arithmetic verbatim: `(a+b+c+d)>>2`
 * with a uint32 sum (no overflow, truncating) and `(a+b)>>1` with a uint32 sum.
 * Integer truncation is identical on host and device, so the only requirement
 * for bit-exactness is that the index math and average grouping match -- which
 * they do, term for term. No floating point is involved, so --fmad is moot.
 *
 * Build (on Ultra-Magnus): build-cuda.ps1 -Src cuda_debayer_parity.cu -Out ...
 * Run: cuda_debayer_parity.exe [width] [height] [seed]
 *   defaults: 1808 2268 (4.1 MP), seed 12345.
 */

#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <cuda_runtime.h>

#define CK(call) do{ cudaError_t e=(call); if(e!=cudaSuccess){ \
    printf("CUDA err %s:%d: %s\n",__FILE__,__LINE__,cudaGetErrorString(e)); exit(3);} }while(0)

/* ===================================================================== *
 *  CPU REFERENCE - verbatim port of debayerBasicU16 + the scalar row fn  *
 *  (src/debayer/debayer.c:63-143 and 453-501). bit_shift=0 path.         *
 * ===================================================================== */
static void cpu_debayer_rows_scalar(uint16_t* debayerto, const uint16_t* bayerdata,
                                    int width, int Y)
{
    const int widthDB = width - 1;
    const int nextRowRGB = width * 3;
    for (int x = 1; x < widthDB; x += 2)
    {
        int pix   = Y + x;
        int pixm1 = pix - width;
        int pixp1 = pix + width;
        int pixp2 = pixp1 + width;

        int bPix[16] = {
            ( pixm1-1 ), ( pixm1 ), ( pixm1+1 ), ( pixm1+2 ),
            ( pix - 1 ), (  pix  ), ( pix + 1 ), ( pix + 2 ),
            ( pixp1-1 ), ( pixp1 ), ( pixp1+1 ), ( pixp1+2 ),
            ( pixp2-1 ), ( pixp2 ), ( pixp2+1 ), ( pixp2+2 ),
        };
        int rgbPix[4] = { (bPix[5]*3), (bPix[6]*3), (bPix[9]*3), (bPix[10]*3) };

        debayerto[ rgbPix[0] ]   = (uint32_t)( bayerdata[bPix[0]] + bayerdata[bPix[2]]
                                 + bayerdata[bPix[8]] + bayerdata[bPix[10]] ) >> 2;
        debayerto[ rgbPix[0]+1 ] = (uint32_t)( bayerdata[bPix[1]] + bayerdata[bPix[6]]
                                 + bayerdata[bPix[4]] + bayerdata[bPix[9]] ) >> 2;
        debayerto[ rgbPix[0]+2 ] = bayerdata[ bPix[5] ];

        debayerto[ rgbPix[1] ]   = (uint32_t)( bayerdata[bPix[2]] + bayerdata[bPix[10]] ) >> 1;
        debayerto[ rgbPix[1]+1 ] = bayerdata[ bPix[6] ];
        debayerto[ rgbPix[1]+2 ] = (uint32_t)( bayerdata[bPix[5]] + bayerdata[bPix[7]] ) >> 1;

        debayerto[ rgbPix[2] ]   = (uint32_t)( bayerdata[bPix[8]] + bayerdata[bPix[10]] ) >> 1;
        debayerto[ rgbPix[2]+1 ] = bayerdata[ bPix[9] ];
        debayerto[ rgbPix[2]+2 ] = (uint32_t)( bayerdata[bPix[5]] + bayerdata[bPix[13]] ) >> 1;

        debayerto[ rgbPix[3] ]   = bayerdata[ bPix[10] ];
        debayerto[ rgbPix[3]+1 ] = (uint32_t)( bayerdata[bPix[6]] + bayerdata[bPix[9]]
                                 + bayerdata[bPix[11]] + bayerdata[bPix[14]] ) >> 2;
        debayerto[ rgbPix[3]+2 ] = (uint32_t)( bayerdata[bPix[5]] + bayerdata[bPix[7]]
                                 + bayerdata[bPix[13]] + bayerdata[bPix[15]] ) >> 2;
    }

    uint16_t* edgePixel = debayerto + (3 * Y);
    edgePixel[0]=edgePixel[3]; edgePixel[1]=edgePixel[4]; edgePixel[2]=edgePixel[5];
    edgePixel += nextRowRGB;
    edgePixel[0]=edgePixel[3]; edgePixel[1]=edgePixel[4]; edgePixel[2]=edgePixel[5];
    edgePixel[-1]=edgePixel[-4]; edgePixel[-2]=edgePixel[-5]; edgePixel[-3]=edgePixel[-6];
    edgePixel += nextRowRGB;
    edgePixel[-1]=edgePixel[-4]; edgePixel[-2]=edgePixel[-5]; edgePixel[-3]=edgePixel[-6];
}

static void cpu_debayer_basic_u16(uint16_t* debayerto, const uint16_t* bayerdata,
                                  int width, int height)
{
    int pixelsizeDB = (height % 2 == 0) ? width*(height-1) : width*(height-2);
    const int step = width * 2;
    for (int Y = width; Y < pixelsizeDB; Y += step)
        cpu_debayer_rows_scalar(debayerto, bayerdata, width, Y);
    /* top/bottom border memcpy fixups */
    memcpy(debayerto, debayerto + (width*3), (size_t)width*3*sizeof(uint16_t));
    memcpy(debayerto + ((size_t)width*(height-1)*3),
           debayerto + ((size_t)width*(height-2)*3), (size_t)width*3*sizeof(uint16_t));
}

/* ===================================================================== *
 *  CUDA PORT - one thread per 2x2 block (x even in [1,widthDB), Y top    *
 *  row). Verbatim arithmetic. Edges handled by a tiny second kernel that *
 *  mirrors the per-row-pair edge fixups + the top/bottom memcpy.         *
 * ===================================================================== */
__global__ void k_debayer_interior(uint16_t* __restrict debayerto,
                                   const uint16_t* __restrict bayerdata,
                                   int width, int pixelsizeDB)
{
    /* xi indexes the odd-x sequence (x=1,3,5,...); yi indexes row pairs */
    int xi = blockIdx.x*blockDim.x + threadIdx.x;
    int yi = blockIdx.y*blockDim.y + threadIdx.y;
    int x = 1 + 2*xi;
    int Y = width + 2*width*yi;          /* top-row flat offset of this row pair */
    const int widthDB = width - 1;
    if (x >= widthDB) return;
    if (Y >= pixelsizeDB) return;

    int pix   = Y + x;
    int pixm1 = pix - width;
    int pixp1 = pix + width;
    int pixp2 = pixp1 + width;
    int bPix[16] = {
        ( pixm1-1 ), ( pixm1 ), ( pixm1+1 ), ( pixm1+2 ),
        ( pix - 1 ), (  pix  ), ( pix + 1 ), ( pix + 2 ),
        ( pixp1-1 ), ( pixp1 ), ( pixp1+1 ), ( pixp1+2 ),
        ( pixp2-1 ), ( pixp2 ), ( pixp2+1 ), ( pixp2+2 ),
    };
    int rgbPix[4] = { (bPix[5]*3), (bPix[6]*3), (bPix[9]*3), (bPix[10]*3) };

    debayerto[ rgbPix[0] ]   = (uint32_t)( bayerdata[bPix[0]] + bayerdata[bPix[2]]
                             + bayerdata[bPix[8]] + bayerdata[bPix[10]] ) >> 2;
    debayerto[ rgbPix[0]+1 ] = (uint32_t)( bayerdata[bPix[1]] + bayerdata[bPix[6]]
                             + bayerdata[bPix[4]] + bayerdata[bPix[9]] ) >> 2;
    debayerto[ rgbPix[0]+2 ] = bayerdata[ bPix[5] ];

    debayerto[ rgbPix[1] ]   = (uint32_t)( bayerdata[bPix[2]] + bayerdata[bPix[10]] ) >> 1;
    debayerto[ rgbPix[1]+1 ] = bayerdata[ bPix[6] ];
    debayerto[ rgbPix[1]+2 ] = (uint32_t)( bayerdata[bPix[5]] + bayerdata[bPix[7]] ) >> 1;

    debayerto[ rgbPix[2] ]   = (uint32_t)( bayerdata[bPix[8]] + bayerdata[bPix[10]] ) >> 1;
    debayerto[ rgbPix[2]+1 ] = bayerdata[ bPix[9] ];
    debayerto[ rgbPix[2]+2 ] = (uint32_t)( bayerdata[bPix[5]] + bayerdata[bPix[13]] ) >> 1;

    debayerto[ rgbPix[3] ]   = bayerdata[ bPix[10] ];
    debayerto[ rgbPix[3]+1 ] = (uint32_t)( bayerdata[bPix[6]] + bayerdata[bPix[9]]
                             + bayerdata[bPix[11]] + bayerdata[bPix[14]] ) >> 2;
    debayerto[ rgbPix[3]+2 ] = (uint32_t)( bayerdata[bPix[5]] + bayerdata[bPix[7]]
                             + bayerdata[bPix[13]] + bayerdata[bPix[15]] ) >> 2;
}

/* Edge fixups: one thread per row pair, replicate the inward RGB neighbour into
 * the left/right border columns. Matches the scalar tail (edgePixel ...). */
__global__ void k_debayer_edges(uint16_t* __restrict debayerto, int width, int pixelsizeDB)
{
    int yi = blockIdx.x*blockDim.x + threadIdx.x;
    int Y = width + 2*width*yi;
    if (Y >= pixelsizeDB) return;
    const int nextRowRGB = width * 3;
    uint16_t* edgePixel = debayerto + (3 * Y);
    edgePixel[0]=edgePixel[3]; edgePixel[1]=edgePixel[4]; edgePixel[2]=edgePixel[5];
    edgePixel += nextRowRGB;
    edgePixel[0]=edgePixel[3]; edgePixel[1]=edgePixel[4]; edgePixel[2]=edgePixel[5];
    edgePixel[-1]=edgePixel[-4]; edgePixel[-2]=edgePixel[-5]; edgePixel[-3]=edgePixel[-6];
    edgePixel += nextRowRGB;
    edgePixel[-1]=edgePixel[-4]; edgePixel[-2]=edgePixel[-5]; edgePixel[-3]=edgePixel[-6];
}

/* top/bottom border row copies (== the two memcpy fixups in debayerBasicU16) */
__global__ void k_debayer_border_rows(uint16_t* __restrict debayerto, int width, int height)
{
    int c = blockIdx.x*blockDim.x + threadIdx.x;   /* 0..width*3-1 */
    if (c >= width*3) return;
    debayerto[c] = debayerto[width*3 + c];                                   /* top := row1 */
    debayerto[(size_t)width*(height-1)*3 + c] =
        debayerto[(size_t)width*(height-2)*3 + c];                           /* bottom := row h-2 */
}

int main(int argc, char** argv)
{
    int w = argc>1 ? atoi(argv[1]) : 1808;
    int h = argc>2 ? atoi(argv[2]) : 2268;
    unsigned seed = argc>3 ? (unsigned)strtoul(argv[3],0,10) : 12345u;
    size_t n = (size_t)w*h;
    size_t rgb = n*3;

    cudaDeviceProp p; CK(cudaGetDeviceProperties(&p,0));
    printf("=== cuda_debayer_parity :: playback bilinear debayer (debayerBasicU16) ===\n");
    printf("device=%s sm_%d%d  %dx%d (%.2f MP)  seed=%u\n", p.name,p.major,p.minor,w,h,n/1e6,seed);

    /* synthetic 16-bit Bayer input (stands in for the recon's 16-bit Bayer out).
     * A deterministic LCG with full 16-bit range exercises every average path. */
    uint16_t* h_bayer = (uint16_t*)malloc(n*sizeof(uint16_t));
    unsigned s = seed;
    for (size_t i=0;i<n;i++){ s = s*1664525u + 1013904223u; h_bayer[i] = (uint16_t)(s>>16); }

    /* CPU reference */
    uint16_t* h_ref = (uint16_t*)calloc(rgb,sizeof(uint16_t));
    cpu_debayer_basic_u16(h_ref, h_bayer, w, h);

    /* GPU */
    uint16_t *d_bayer,*d_rgb;
    CK(cudaMalloc(&d_bayer, n*sizeof(uint16_t)));
    CK(cudaMalloc(&d_rgb,   rgb*sizeof(uint16_t)));
    CK(cudaMemcpy(d_bayer,h_bayer,n*sizeof(uint16_t),cudaMemcpyHostToDevice));
    CK(cudaMemset(d_rgb,0,rgb*sizeof(uint16_t)));

    int pixelsizeDB = (h%2==0)? w*(h-1) : w*(h-2);
    int nXi = (w-1)/2;             /* number of odd x in [1,w-1) */
    int nYi = (pixelsizeDB - w + (2*w) - 1) / (2*w);  /* row pairs covered */
    dim3 blk(16,16), grd((nXi+15)/16,(nYi+15)/16);
    k_debayer_interior<<<grd,blk>>>(d_rgb,d_bayer,w,pixelsizeDB);
    CK(cudaGetLastError());
    { int t=256; k_debayer_edges<<<(nYi+t-1)/t,t>>>(d_rgb,w,pixelsizeDB); CK(cudaGetLastError()); }
    { int t=256; k_debayer_border_rows<<<(w*3+t-1)/t,t>>>(d_rgb,w,h); CK(cudaGetLastError()); }
    CK(cudaDeviceSynchronize());

    uint16_t* h_gpu = (uint16_t*)malloc(rgb*sizeof(uint16_t));
    CK(cudaMemcpy(h_gpu,d_rgb,rgb*sizeof(uint16_t),cudaMemcpyDeviceToHost));

    /* diff */
    long long maxabs=0; double sumabs=0; size_t nz=0, fx=0,fy=0,fc=0; long long fd=0; int found=0;
    for (size_t i=0;i<rgb;i++){
        long long d=(long long)h_gpu[i]-(long long)h_ref[i]; long long a=d<0?-d:d;
        if(a>maxabs)maxabs=a; sumabs+=(double)a;
        if(a){ nz++; if(!found){found=1; size_t px=i/3; fx=px%w; fy=px/w; fc=i%3; fd=d;} }
    }
    printf("\n[debayer parity] GPU vs CPU reference (interleaved RGB16, %zu samples):\n", rgb);
    printf("  max abs diff  = %lld LSB\n", maxabs);
    printf("  mean abs diff = %.8f LSB\n", sumabs/(double)rgb);
    printf("  mismatched    = %zu / %zu (%.6f%%)\n", nz, rgb, 100.0*nz/rgb);
    if (found) printf("  first mismatch @ pixel(%zu,%zu) channel=%zu  gpu-cpu=%lld\n", fx,fy,fc,fd);
    printf("\n[debayer parity] RESULT: %s\n", (maxabs==0)?"BIT-EXACT (0 LSB)":(maxabs<=1?"WITHIN 1 LSB":"NEEDS-WORK"));
    return (maxabs<=1)?0:1;
}
