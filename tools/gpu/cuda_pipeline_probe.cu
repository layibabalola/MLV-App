/* cuda_pipeline_probe.cu - END-TO-END full-quality GPU playback pipeline probe
 * for the RTX 4090. Extends the Tier-2 moonshot from recon-only to the WHOLE
 * playback pipeline:
 *
 *   upload Bayer (pinned H2D)
 *     -> recon         (Dual ISO full-20-bit -> 16-bit Bayer)   [bit-exact-validated kernels]
 *     -> debayer       (bilinear Bayer -> RGB16)                 [bit-exact-validated port]
 *     -> processing    (3x3 colour matrix + WB scale + gamma LUT) [REPRESENTATIVE, see note]
 *     -> downscale     (RGB16 -> viewport, half-res box average)  [representative scale stage]
 *     -> present        (D2D copy into a present buffer, no readback)
 *
 * All device-resident: NO intermediate host readback. Pinned host input +
 * CUDA streams (the validated max-speed recipe from optimization-results.md).
 *
 * WHAT IS VALIDATED vs REPRESENTATIVE
 *   - recon kernels      : math proven BIT-EXACT (0 LSB) by cuda_recon_parity.cu.
 *                          Reused here in their fast-math timing form (same kernel
 *                          *shapes*: tap counts, stencils, LUT gathers).
 *   - debayer kernel     : proven BIT-EXACT (0 LSB) by cuda_debayer_parity.cu
 *                          against the engine's scalar debayerBasicU16.
 *   - processing pass    : REPRESENTATIVE. It runs the engine's per-pixel op
 *                          shape -- a 3x3 colour-matrix multiply (mirrors the
 *                          cam-matrix / WB-matrix multiplies in
 *                          src/processing/raw_processing.c), a per-channel
 *                          white-balance scale, and a gamma/tone LUT gather
 *                          (mirrors the gamma curve LUT) -- to match the rough
 *                          op-count of the engine's colour stage. It is NOT a
 *                          parity port of the full processing chain (tonemap,
 *                          highlight recon, sharpening, etc. are out of scope),
 *                          so the processing-stage TIME is representative,
 *                          pending a processing-parity port.
 *   - downscale          : representative (half-res box average; the production
 *                          viewport scale is a bilinear resize of similar cost).
 *   - present            : the real production path is D2D into a CUDA<->GL
 *                          registered texture (proven ~0.05-0.11 ms separately
 *                          in glinterop-results.md); D2D copy here stands in.
 *
 * Built with --use_fast_math (FMA + fast intrinsics) -- legitimate for a timing
 * study whose correctness is validated separately. The parity probes stay
 * --fmad=false.
 *
 * Usage: cuda_pipeline_probe.exe [width] [height] [iters] [vp_div]
 *   defaults: 1808 2268 (4.1 MP), 300 iters, viewport divisor 2 (half-res).
 */

#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cmath>
#include <cuda_runtime.h>

#define CK(call) do{ cudaError_t e=(call); if(e!=cudaSuccess){ \
    printf("CUDA err %s:%d: %s\n",__FILE__,__LINE__,cudaGetErrorString(e)); exit(3);} }while(0)

static const int RAW2EV_SIZE = 1<<20;
static const int EV2RAW_SIZE = 24*65536;
static const int CURVE_SIZE  = 1<<20;
static const int GAMMA_SIZE  = 1<<16;     /* 16-bit gamma/tone LUT */
static const int ALIAS_MAX   = 15000;

__device__ __forceinline__ int clampi(int v,int lo,int hi){ return v<lo?lo:(v>hi?hi:v); }
__constant__ int   c_isbright[4];
__constant__ float c_cmat[9];             /* 3x3 colour matrix (representative) */
__constant__ float c_wb[3];               /* per-channel white balance (representative) */

/* ============================ STAGE 1: RECON ============================ *
 * Reused kernel shapes from cuda_recon_opt.cu (validated bit-exact form in
 * cuda_recon_parity.cu). Output is 16-bit Bayer (r20>>4), exactly the engine's
 * recon output that feeds the debayer.                                        */
__device__ __forceinline__ uint32_t convert_one(const uint16_t* b14,int idx,int row,
                                                 int black_delta,float ev_scale){
    uint32_t v=((uint32_t)b14[idx])<<6;
    if(!c_isbright[row&3]){ float fv=((float)v-(float)(black_delta<<6))*ev_scale; v=(uint32_t)fmaxf(0.f,fv); }
    return v;
}
__global__ void k_fused_convert_mean_fullres(const uint16_t* b14,
                                             uint32_t* dark,uint32_t* bright,uint32_t* fullres,
                                             const int* raw2ev,const int* ev2raw,
                                             int w,int h,int black_delta,float ev_scale){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    int ym=(y>=2?y-2:y), yp=(y+2<h?y+2:y);
    uint32_t c =convert_one(b14,i,        y,  black_delta,ev_scale);
    uint32_t a =convert_one(b14,ym*w+x,   ym, black_delta,ev_scale);
    uint32_t bb=convert_one(b14,yp*w+x,   yp, black_delta,ev_scale);
    int evm=(raw2ev[a&0xFFFFF]+raw2ev[bb&0xFFFFF])>>1;
    uint32_t interp=(uint32_t)ev2raw[clampi(evm,0,EV2RAW_SIZE-1)];
    uint32_t dk,br;
    if(c_isbright[y&3]){ br=c; dk=interp; } else { dk=c; br=interp; }
    dark[i]=dk; bright[i]=br;
    fullres[i] = c_isbright[y&3] ? br : dk;
}
__global__ void k_mix_halfres(const uint32_t* dark,const uint32_t* bright,uint32_t* halfres,
                              const int* raw2ev,const int* ev2raw,const float* mixcurve,int w,int h){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    uint32_t br=bright[i], dk=dark[i];
    float k=mixcurve[br&0xFFFFF];
    float bev=(float)raw2ev[br&0xFFFFF], dev=(float)raw2ev[dk&0xFFFFF];
    int mixed=(int)(bev*(1.f-k)+dev*k);
    halfres[i]=(uint32_t)ev2raw[clampi(mixed,0,EV2RAW_SIZE-1)];
}
__global__ void k_alias_init(const uint32_t* fullres,const uint32_t* halfres,uint16_t* aux,
                             const int* raw2ev,int w,int h){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    int d=raw2ev[fullres[i]&0xFFFFF]-raw2ev[halfres[i]&0xFFFFF]; d=d<0?-d:d;
    aux[i]=(uint16_t)clampi(d,0,ALIAS_MAX);
}
__global__ void k_alias_rank(const uint16_t* in,uint16_t* out,int w,int h){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x; int top[5]={0,0,0,0,0};
    #pragma unroll
    for(int dy=-6;dy<=6;dy+=2){ int yy=clampi(y+dy,0,h-1);
        #pragma unroll
        for(int dx=-6;dx<=6;dx+=2){ if(abs(dx)+abs(dy)>8) continue;
            int v=in[yy*w+clampi(x+dx,0,w-1)];
            if(v>top[4]){ int j=4; while(j>0 && v>top[j-1]){ top[j]=top[j-1]; --j; } top[j]=v; } } }
    out[i]=(uint16_t)top[4];
}
__global__ void k_alias_gauss(const uint16_t* in,uint16_t* out,int w,int h){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    const int W[7]={1024,820,657,421,337,173,139}; long sum=0,wsum=0;
    #pragma unroll
    for(int dy=-6;dy<=6;dy+=2){ int yy=clampi(y+dy,0,h-1);
        #pragma unroll
        for(int dx=-6;dx<=6;dx+=2){ int r=(abs(dx)>abs(dy)?abs(dx):abs(dy))/2;
            if(abs(dx)+abs(dy)>8) continue; int wgt=W[r];
            sum+=(long)in[yy*w+clampi(x+dx,0,w-1)]*wgt; wsum+=wgt; } }
    out[i]=(uint16_t)clampi((int)(wsum? sum/wsum:0),0,ALIAS_MAX);
}
/* final_blend writes 16-bit BAYER (recon output that the debayer consumes) */
__global__ void k_final_blend_bayer(const uint32_t* halfres,const uint32_t* fullres,
                              const uint16_t* alias,uint16_t* bayer_out,
                              const int* raw2ev,const int* ev2raw,const float* fullrescurve,
                              const uint32_t* bright,int w,int h){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    float f=fullrescurve[bright[i]&0xFFFFF];
    float a=(float)alias[i]/(float)ALIAS_MAX;
    f=fminf(1.f, f + a*(1.f-f));
    float hrev=(float)raw2ev[halfres[i]&0xFFFFF], fev=(float)raw2ev[fullres[i]&0xFFFFF];
    int outev=(int)(hrev*(1.f-f)+fev*f);
    uint32_t r20=(uint32_t)ev2raw[clampi(outev,0,EV2RAW_SIZE-1)];
    bayer_out[i]=(uint16_t)(r20>>4);
}

/* ========================== STAGE 2: DEBAYER =========================== *
 * Bilinear Bayer->RGB16, the validated bit-exact port (one thread per 2x2
 * block; same arithmetic as cuda_debayer_parity.cu). Writes interleaved RGB16.
 * Edge replication folded into a tiny guard so the timing reflects one launch. */
__global__ void k_debayer_interior(uint16_t* __restrict rgb,
                                   const uint16_t* __restrict bayer,
                                   int width, int pixelsizeDB){
    int xi=blockIdx.x*blockDim.x+threadIdx.x, yi=blockIdx.y*blockDim.y+threadIdx.y;
    int x=1+2*xi; int Y=width+2*width*yi; const int widthDB=width-1;
    if(x>=widthDB||Y>=pixelsizeDB) return;
    int pix=Y+x, pixm1=pix-width, pixp1=pix+width, pixp2=pixp1+width;
    int b[16]={ pixm1-1,pixm1,pixm1+1,pixm1+2, pix-1,pix,pix+1,pix+2,
                pixp1-1,pixp1,pixp1+1,pixp1+2, pixp2-1,pixp2,pixp2+1,pixp2+2 };
    int o0=b[5]*3,o1=b[6]*3,o2=b[9]*3,o3=b[10]*3;
    rgb[o0]   = (uint32_t)(bayer[b[0]]+bayer[b[2]]+bayer[b[8]]+bayer[b[10]])>>2;
    rgb[o0+1] = (uint32_t)(bayer[b[1]]+bayer[b[6]]+bayer[b[4]]+bayer[b[9]])>>2;
    rgb[o0+2] = bayer[b[5]];
    rgb[o1]   = (uint32_t)(bayer[b[2]]+bayer[b[10]])>>1;
    rgb[o1+1] = bayer[b[6]];
    rgb[o1+2] = (uint32_t)(bayer[b[5]]+bayer[b[7]])>>1;
    rgb[o2]   = (uint32_t)(bayer[b[8]]+bayer[b[10]])>>1;
    rgb[o2+1] = bayer[b[9]];
    rgb[o2+2] = (uint32_t)(bayer[b[5]]+bayer[b[13]])>>1;
    rgb[o3]   = bayer[b[10]];
    rgb[o3+1] = (uint32_t)(bayer[b[6]]+bayer[b[9]]+bayer[b[11]]+bayer[b[14]])>>2;
    rgb[o3+2] = (uint32_t)(bayer[b[5]]+bayer[b[7]]+bayer[b[13]]+bayer[b[15]])>>2;
}

/* ======================== STAGE 3: PROCESSING ========================= *
 * REPRESENTATIVE per-pixel colour stage. Op shape mirrors raw_processing.c:
 *   - per-channel white-balance scale (3 mul)
 *   - 3x3 colour-matrix multiply (9 mul + 6 add)   [cam/WB matrix multiplies]
 *   - gamma/tone LUT gather per channel (3 gathers) [gamma curve LUT]
 * Operates in place on the interleaved RGB16 buffer. */
__global__ void k_process(uint16_t* __restrict rgb, const uint16_t* __restrict gamma,
                          int width, int height){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=width||y>=height) return; size_t i=((size_t)y*width+x)*3;
    /* white balance */
    float r=(float)rgb[i+0]*c_wb[0], g=(float)rgb[i+1]*c_wb[1], b=(float)rgb[i+2]*c_wb[2];
    /* 3x3 colour matrix */
    float R=c_cmat[0]*r+c_cmat[1]*g+c_cmat[2]*b;
    float G=c_cmat[3]*r+c_cmat[4]*g+c_cmat[5]*b;
    float B=c_cmat[6]*r+c_cmat[7]*g+c_cmat[8]*b;
    /* gamma / tone LUT gather (clamp index to 16-bit) */
    int Ri=clampi((int)R,0,GAMMA_SIZE-1), Gi=clampi((int)G,0,GAMMA_SIZE-1), Bi=clampi((int)B,0,GAMMA_SIZE-1);
    rgb[i+0]=gamma[Ri]; rgb[i+1]=gamma[Gi]; rgb[i+2]=gamma[Bi];
}

/* ======================== STAGE 4: DOWNSCALE ========================== *
 * RGB16 full-res -> viewport (1/div in each axis) by box average of a div*div
 * block. Representative of the production bilinear viewport resize. */
__global__ void k_downscale(const uint16_t* __restrict src, uint16_t* __restrict dst,
                            int sw,int sh,int dw,int dh,int div){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=dw||y>=dh) return;
    int sx0=x*div, sy0=y*div; uint32_t ar=0,ag=0,ab=0; int cnt=0;
    for(int dy=0;dy<div;dy++){ int sy=sy0+dy; if(sy>=sh)break;
        for(int dx=0;dx<div;dx++){ int sx=sx0+dx; if(sx>=sw)break;
            size_t si=((size_t)sy*sw+sx)*3; ar+=src[si]; ag+=src[si+1]; ab+=src[si+2]; cnt++; } }
    size_t di=((size_t)y*dw+x)*3;
    dst[di]=(uint16_t)(ar/cnt); dst[di+1]=(uint16_t)(ag/cnt); dst[di+2]=(uint16_t)(ab/cnt);
}

static float timed(cudaEvent_t a, cudaEvent_t b){ float ms=0; cudaEventElapsedTime(&ms,a,b); return ms; }

/* ---- device state for the whole pipeline (single-buffer; serial timing) ---- */
struct Dev {
    uint16_t *b14;                         /* uploaded Bayer (recon input) */
    uint32_t *dark,*bright,*fullres,*halfres;
    uint16_t *aliasA,*aliasB,*aliasMap;
    uint16_t *bayer;                       /* recon output (16-bit Bayer) */
    uint16_t *rgb;                         /* debayered + processed RGB16 (full res) */
    uint16_t *vp;                          /* downscaled viewport RGB16 */
    uint16_t *present;                     /* present target (D2D sink) */
    int      *raw2ev,*ev2raw; float *mix,*fr; uint16_t *gamma;
    int w,h,vw,vh,vdiv,n; dim3 blk,grd,grdv;
};

/* run the full compute chain (recon -> debayer -> process -> downscale) on s */
static inline void run_pipeline_compute(Dev& d, cudaStream_t s){
    int pixelsizeDB=(d.h%2==0)? d.w*(d.h-1):d.w*(d.h-2);
    int nXi=(d.w-1)/2, nYi=(pixelsizeDB-d.w+(2*d.w)-1)/(2*d.w);
    dim3 grdDB((nXi+15)/16,(nYi+15)/16);
    /* recon */
    k_fused_convert_mean_fullres<<<d.grd,d.blk,0,s>>>(d.b14,d.dark,d.bright,d.fullres,d.raw2ev,d.ev2raw,d.w,d.h,256,1.7f);
    k_mix_halfres<<<d.grd,d.blk,0,s>>>(d.dark,d.bright,d.halfres,d.raw2ev,d.ev2raw,d.mix,d.w,d.h);
    k_alias_init<<<d.grd,d.blk,0,s>>>(d.fullres,d.halfres,d.aliasA,d.raw2ev,d.w,d.h);
    k_alias_rank<<<d.grd,d.blk,0,s>>>(d.aliasA,d.aliasB,d.w,d.h);
    k_alias_gauss<<<d.grd,d.blk,0,s>>>(d.aliasB,d.aliasMap,d.w,d.h);
    k_final_blend_bayer<<<d.grd,d.blk,0,s>>>(d.halfres,d.fullres,d.aliasMap,d.bayer,d.raw2ev,d.ev2raw,d.fr,d.bright,d.w,d.h);
    /* debayer */
    k_debayer_interior<<<grdDB,d.blk,0,s>>>(d.rgb,d.bayer,d.w,pixelsizeDB);
    /* processing (representative) */
    k_process<<<d.grd,d.blk,0,s>>>(d.rgb,d.gamma,d.w,d.h);
    /* downscale to viewport */
    k_downscale<<<d.grdv,d.blk,0,s>>>(d.rgb,d.vp,d.w,d.h,d.vw,d.vh,d.vdiv);
}

/* per-stage cudaEvent breakdown (averaged warm), compute-only on default stream */
static void measure_stage_breakdown(Dev& d,int iters){
    int pixelsizeDB=(d.h%2==0)? d.w*(d.h-1):d.w*(d.h-2);
    int nXi=(d.w-1)/2, nYi=(pixelsizeDB-d.w+(2*d.w)-1)/(2*d.w);
    dim3 grdDB((nXi+15)/16,(nYi+15)/16);
    const int NS=5; const char* names[NS]={"recon","debayer","process","downscale","TOTAL-compute"};
    double acc[NS]={0,0,0,0,0}; int warm=iters-10; if(warm<1)warm=1;
    cudaEvent_t ev[6]; for(int k=0;k<6;k++) CK(cudaEventCreate(&ev[k]));
    for(int it=0;it<iters;it++){
        CK(cudaEventRecord(ev[0]));
        k_fused_convert_mean_fullres<<<d.grd,d.blk>>>(d.b14,d.dark,d.bright,d.fullres,d.raw2ev,d.ev2raw,d.w,d.h,256,1.7f);
        k_mix_halfres<<<d.grd,d.blk>>>(d.dark,d.bright,d.halfres,d.raw2ev,d.ev2raw,d.mix,d.w,d.h);
        k_alias_init<<<d.grd,d.blk>>>(d.fullres,d.halfres,d.aliasA,d.raw2ev,d.w,d.h);
        k_alias_rank<<<d.grd,d.blk>>>(d.aliasA,d.aliasB,d.w,d.h);
        k_alias_gauss<<<d.grd,d.blk>>>(d.aliasB,d.aliasMap,d.w,d.h);
        k_final_blend_bayer<<<d.grd,d.blk>>>(d.halfres,d.fullres,d.aliasMap,d.bayer,d.raw2ev,d.ev2raw,d.fr,d.bright,d.w,d.h);
        CK(cudaEventRecord(ev[1]));
        k_debayer_interior<<<grdDB,d.blk>>>(d.rgb,d.bayer,d.w,pixelsizeDB);
        CK(cudaEventRecord(ev[2]));
        k_process<<<d.grd,d.blk>>>(d.rgb,d.gamma,d.w,d.h);
        CK(cudaEventRecord(ev[3]));
        k_downscale<<<d.grdv,d.blk>>>(d.rgb,d.vp,d.w,d.h,d.vw,d.vh,d.vdiv);
        CK(cudaEventRecord(ev[4]));
        CK(cudaEventSynchronize(ev[4]));
        if(it>=10){
            acc[0]+=timed(ev[0],ev[1]); acc[1]+=timed(ev[1],ev[2]);
            acc[2]+=timed(ev[2],ev[3]); acc[3]+=timed(ev[3],ev[4]); acc[4]+=timed(ev[0],ev[4]);
        }
    }
    printf("\n--- PER-STAGE compute breakdown (cudaEvent, warm avg over %d) ---\n",warm);
    for(int k=0;k<NS;k++) printf("  %-16s %8.4f ms  (%6.1f fps-eq)\n",names[k],acc[k]/warm,1000.0/(acc[k]/warm));
    for(int k=0;k<6;k++) cudaEventDestroy(ev[k]);
}

/* serial full-frame: H2D(pinned) -> pipeline -> present (D2D, no readback) */
static double measure_serial(Dev& d, uint16_t* h_in, int iters){
    cudaEvent_t a,b; CK(cudaEventCreate(&a)); CK(cudaEventCreate(&b));
    double acc=0; int warm=iters-10; if(warm<1)warm=1;
    size_t inB=(size_t)d.n*2, vpB=(size_t)d.vw*d.vh*3*2;
    for(int it=0;it<iters;it++){
        CK(cudaEventRecord(a));
        CK(cudaMemcpyAsync(d.b14,h_in,inB,cudaMemcpyHostToDevice,0));
        run_pipeline_compute(d,0);
        CK(cudaMemcpyAsync(d.present,d.vp,vpB,cudaMemcpyDeviceToDevice,0));   /* no-readback present */
        CK(cudaEventRecord(b)); CK(cudaEventSynchronize(b));
        if(it>=10) acc+=timed(a,b);
    }
    cudaEventDestroy(a); cudaEventDestroy(b);
    return acc/warm;
}

/* double-buffered 3-stream: upload(N+1) overlaps compute(N), present is D2D.
 * Steady-state per-frame = wall / frames (sustained-fps metric). */
struct Pipe {
    uint16_t *b14[2];
    uint32_t *dark[2],*bright[2],*fullres[2],*halfres[2];
    uint16_t *aliasA[2],*aliasB[2],*aliasMap[2],*bayer[2],*rgb[2],*vp[2];
    uint16_t *present;
};
static double measure_streamed(Dev& base, Pipe& pp, uint16_t* h_in, int iters){
    cudaStream_t sUp,sCmp,sDn;
    CK(cudaStreamCreate(&sUp)); CK(cudaStreamCreate(&sCmp)); CK(cudaStreamCreate(&sDn));
    cudaEvent_t evUp[2],evCmp[2];
    for(int k=0;k<2;k++){ CK(cudaEventCreate(&evUp[k])); CK(cudaEventCreate(&evCmp[k])); }
    size_t inB=(size_t)base.n*2, vpB=(size_t)base.vw*base.vh*3*2;
    int warm=iters-10; if(warm<1)warm=1;
    cudaEvent_t wA,wB; CK(cudaEventCreate(&wA)); CK(cudaEventCreate(&wB));
    auto compute_slot=[&](int slot){
        Dev d=base;
        d.b14=pp.b14[slot]; d.dark=pp.dark[slot]; d.bright=pp.bright[slot];
        d.fullres=pp.fullres[slot]; d.halfres=pp.halfres[slot];
        d.aliasA=pp.aliasA[slot]; d.aliasB=pp.aliasB[slot]; d.aliasMap=pp.aliasMap[slot];
        d.bayer=pp.bayer[slot]; d.rgb=pp.rgb[slot]; d.vp=pp.vp[slot];
        run_pipeline_compute(d,sCmp);
    };
    for(int it=0;it<iters;it++){
        if(it==10){ CK(cudaDeviceSynchronize()); CK(cudaEventRecord(wA)); }
        int slot=it&1;
        CK(cudaMemcpyAsync(pp.b14[slot],h_in,inB,cudaMemcpyHostToDevice,sUp));
        CK(cudaEventRecord(evUp[slot],sUp));
        CK(cudaStreamWaitEvent(sCmp,evUp[slot],0));
        compute_slot(slot);
        CK(cudaEventRecord(evCmp[slot],sCmp));
        CK(cudaStreamWaitEvent(sDn,evCmp[slot],0));
        CK(cudaMemcpyAsync(pp.present,pp.vp[slot],vpB,cudaMemcpyDeviceToDevice,sDn));
    }
    CK(cudaDeviceSynchronize()); CK(cudaEventRecord(wB)); CK(cudaEventSynchronize(wB));
    double wall=timed(wA,wB);
    cudaStreamDestroy(sUp); cudaStreamDestroy(sCmp); cudaStreamDestroy(sDn);
    return wall/warm;
}

int main(int argc,char**argv){
    int w=argc>1?atoi(argv[1]):1808;
    int h=argc>2?atoi(argv[2]):2268;
    int iters=argc>3?atoi(argv[3]):300;
    int vdiv=argc>4?atoi(argv[4]):2;
    int vw=w/vdiv, vh=h/vdiv, n=w*h;
    cudaDeviceProp p; CK(cudaGetDeviceProperties(&p,0));
    printf("=== cuda_pipeline_probe :: END-TO-END GPU playback pipeline (fast-math ON) ===\n");
    printf("device=%s sm_%d%d\n",p.name,p.major,p.minor);
    printf("full res=%dx%d (%.2f MP)  viewport=%dx%d (1/%d)  iters=%d (warm=%d)\n",
           w,h,n/1e6,vw,vh,vdiv,iters,iters-10);
    printf("pipeline: H2D(pinned) -> recon -> debayer -> process(repr) -> downscale -> present(D2D)\n");

    /* host synthetic Bayer (pinned for async) */
    uint16_t* h_pin=nullptr; CK(cudaHostAlloc((void**)&h_pin,(size_t)n*2,cudaHostAllocDefault));
    unsigned s=2654435761u; for(int i=0;i<n;i++){ s=s*1664525u+1013904223u; h_pin[i]=(uint16_t)((s>>16)&0x3FFF); }

    /* LUTs / curves (representative shapes; recon parity validated elsewhere) */
    int* h_raw2ev=(int*)malloc((size_t)RAW2EV_SIZE*4);
    for(int i=0;i<RAW2EV_SIZE;i++) h_raw2ev[i]=(int)(logf((float)i+1.f)*10000.f);
    int* h_ev2raw=(int*)malloc((size_t)EV2RAW_SIZE*4);
    for(int i=0;i<EV2RAW_SIZE;i++) h_ev2raw[i]=i%(1<<20);
    float* h_mix=(float*)malloc((size_t)CURVE_SIZE*4); float* h_fr=(float*)malloc((size_t)CURVE_SIZE*4);
    for(int i=0;i<CURVE_SIZE;i++){ h_mix[i]=0.5f; h_fr[i]=0.5f; }
    uint16_t* h_gamma=(uint16_t*)malloc((size_t)GAMMA_SIZE*2);
    for(int i=0;i<GAMMA_SIZE;i++) h_gamma[i]=(uint16_t)(powf((float)i/65535.f,1.f/2.222f)*65535.f);
    int isbright[4]={1,1,0,0};
    /* representative cam matrix (mild colour mix) + WB + identity-ish */
    float cmat[9]={1.6f,-0.4f,-0.2f, -0.2f,1.4f,-0.2f, 0.0f,-0.3f,1.3f};
    float wb[3]={1.9f,1.0f,1.5f};

    Dev d; d.w=w; d.h=h; d.vw=vw; d.vh=vh; d.vdiv=vdiv; d.n=n;
    d.blk=dim3(16,16); d.grd=dim3((w+15)/16,(h+15)/16); d.grdv=dim3((vw+15)/16,(vh+15)/16);
    CK(cudaMalloc(&d.b14,(size_t)n*2));
    CK(cudaMalloc(&d.dark,(size_t)n*4)); CK(cudaMalloc(&d.bright,(size_t)n*4));
    CK(cudaMalloc(&d.fullres,(size_t)n*4)); CK(cudaMalloc(&d.halfres,(size_t)n*4));
    CK(cudaMalloc(&d.aliasA,(size_t)n*2)); CK(cudaMalloc(&d.aliasB,(size_t)n*2)); CK(cudaMalloc(&d.aliasMap,(size_t)n*2));
    CK(cudaMalloc(&d.bayer,(size_t)n*2));
    CK(cudaMalloc(&d.rgb,(size_t)n*3*2));
    CK(cudaMalloc(&d.vp,(size_t)vw*vh*3*2));
    CK(cudaMalloc(&d.present,(size_t)vw*vh*3*2));
    CK(cudaMalloc(&d.raw2ev,(size_t)RAW2EV_SIZE*4)); CK(cudaMalloc(&d.ev2raw,(size_t)EV2RAW_SIZE*4));
    CK(cudaMalloc(&d.mix,(size_t)CURVE_SIZE*4)); CK(cudaMalloc(&d.fr,(size_t)CURVE_SIZE*4));
    CK(cudaMalloc(&d.gamma,(size_t)GAMMA_SIZE*2));
    CK(cudaMemcpy(d.raw2ev,h_raw2ev,(size_t)RAW2EV_SIZE*4,cudaMemcpyHostToDevice));
    CK(cudaMemcpy(d.ev2raw,h_ev2raw,(size_t)EV2RAW_SIZE*4,cudaMemcpyHostToDevice));
    CK(cudaMemcpy(d.mix,h_mix,(size_t)CURVE_SIZE*4,cudaMemcpyHostToDevice));
    CK(cudaMemcpy(d.fr,h_fr,(size_t)CURVE_SIZE*4,cudaMemcpyHostToDevice));
    CK(cudaMemcpy(d.gamma,h_gamma,(size_t)GAMMA_SIZE*2,cudaMemcpyHostToDevice));
    CK(cudaMemcpyToSymbol(c_isbright,isbright,sizeof(isbright)));
    CK(cudaMemcpyToSymbol(c_cmat,cmat,sizeof(cmat)));
    CK(cudaMemcpyToSymbol(c_wb,wb,sizeof(wb)));

    /* double-buffered pipeline state */
    Pipe pp;
    for(int k=0;k<2;k++){
        CK(cudaMalloc(&pp.b14[k],(size_t)n*2));
        CK(cudaMalloc(&pp.dark[k],(size_t)n*4)); CK(cudaMalloc(&pp.bright[k],(size_t)n*4));
        CK(cudaMalloc(&pp.fullres[k],(size_t)n*4)); CK(cudaMalloc(&pp.halfres[k],(size_t)n*4));
        CK(cudaMalloc(&pp.aliasA[k],(size_t)n*2)); CK(cudaMalloc(&pp.aliasB[k],(size_t)n*2)); CK(cudaMalloc(&pp.aliasMap[k],(size_t)n*2));
        CK(cudaMalloc(&pp.bayer[k],(size_t)n*2));
        CK(cudaMalloc(&pp.rgb[k],(size_t)n*3*2));
        CK(cudaMalloc(&pp.vp[k],(size_t)vw*vh*3*2));
    }
    CK(cudaMalloc(&pp.present,(size_t)vw*vh*3*2));

    /* ====================== MEASURE ====================== */
    measure_stage_breakdown(d, iters);
    double serial = measure_serial(d, h_pin, iters);
    double streamed = measure_streamed(d, pp, h_pin, iters);

    auto fps=[](double ms){ return 1000.0/ms; };
    printf("\n--- FULL END-TO-END FRAME TIME (incl pinned H2D + D2D present) ---\n");
    printf("  serial (pinned, D2D present)            %8.4f ms  (%6.1f fps)\n", serial, fps(serial));
    printf("  streamed (pinned+overlap, D2D present)  %8.4f ms  (%6.1f fps)\n", streamed, fps(streamed));

    printf("\n=== HEADLINE: full-quality GPU playback frame time @ %.2f MP ===\n", n/1e6);
    printf("  end-to-end GPU frame (best)  : %.4f ms  (%.1f fps-eq)\n", streamed, fps(streamed));
    printf("  CPU decode floor (LJ92)      : ~7-9 ms @ 4.1 MP -- OVERLAPPED via prefetch,\n");
    printf("                                 NOT additive to GPU frame time at steady state.\n");
    printf("  24fps budget (41.67 ms)      : %s\n", streamed<=41.67?"WITHIN":"OVER");
    return 0;
}
