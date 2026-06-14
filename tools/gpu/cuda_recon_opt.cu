// cuda_recon_opt.cu - Tier 2 OPTIMIZATION / max-throughput probe for Dual ISO
// recon on the RTX 4090.
//
// This is a TIMING / THROUGHPUT study, NOT a parity probe. Correctness of the
// recon math is already proven bit-exact by cuda_recon_parity.cu against the CPU
// oracle. This probe reuses the SAME kernel *shapes* (tap counts, stencils, LUT
// gathers) as cuda_recon_probe.cu so the measured kernel time stays representative,
// then layers optimizations on top to find the maximum achievable frame rate.
//
// Because correctness is not the goal here, this is built with --use_fast_math
// (FMA contraction etc.) ALLOWED. The validated parity probe stays --fmad=false;
// this one trades a few ULP for speed deliberately, which is legitimate for a
// playback pipeline where the recon math has already been proven and the final
// output is an 8/16-bit display frame.
//
// Optimizations measured, each INCREMENTALLY so the gain is attributable:
//   M0  BASELINE        : pageable host mem, serial H2D -> kernels -> D2H,
//                         separate per-pixel kernels  (== cuda_recon_probe shape)
//   M1  PINNED          : M0 + cudaHostAlloc pinned host buffers (Bayer in, out)
//   M2  STREAMS         : M1 + double-buffered streams: H2D(N+1) and D2H(N-1)
//                         overlapped with compute(N)  (hide transfers)
//   M3  FUSED           : M2 + per-pixel stage fusion:
//                           (convert20+match) + mean23 + fullres  -> 1 kernel,
//                           mix_halfres kept,
//                           final_blend kept,
//                         alias stages (init/rank/gauss) STAY separate (stencils)
//   M3+ ALLCOMBINED     : the M3 config is already all-combined; reported as best
//   PRESENT (D2D)       : frame time with a device->device copy into a present
//                         buffer instead of D2H  (the real CUDA->GL path; prod
//                         has no readback)
//
// Usage: cuda_recon_opt.exe [width] [height] [iters]
//   defaults: 1808 2268 (4.1 MP), 200 iters (more iters -> tighter warm avg).

#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cmath>
#include <cuda_runtime.h>

#define CK(call) do{ cudaError_t e=(call); if(e!=cudaSuccess){ \
    printf("CUDA err %s:%d: %s\n",__FILE__,__LINE__,cudaGetErrorString(e)); exit(3);} }while(0)

static const int RAW2EV_SIZE = 1<<20;       // raw(20b) -> EV
static const int EV2RAW_SIZE = 24*65536;    // EV -> raw(20b)
static const int CURVE_SIZE  = 1<<20;       // mix/fullres curves
static const int ALIAS_MAX   = 15000;

__device__ __forceinline__ int clampi(int v,int lo,int hi){ return v<lo?lo:(v>hi?hi:v); }

__constant__ int c_isbright[4];

// ============================================================================
//  UNFUSED per-pixel kernels (copied from cuda_recon_probe.cu, faithful shape)
// ============================================================================

__global__ void k_convert20_match(const uint16_t* b14, uint32_t* raw32, int w,int h,
                                  int black_delta, float ev_scale){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    uint32_t v=((uint32_t)b14[i])<<6;
    if(!c_isbright[y&3]){ float fv=((float)v-(float)(black_delta<<6))*ev_scale; v=(uint32_t)fmaxf(0.f,fv); }
    raw32[i]=v;
}

__global__ void k_mean23(const uint32_t* raw32, uint32_t* dark, uint32_t* bright,
                         const int* raw2ev, const int* ev2raw, int w,int h){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    int ym=(y>=2?y-2:y), yp=(y+2<h?y+2:y);
    uint32_t c=raw32[i], a=raw32[ym*w+x], bb=raw32[yp*w+x];
    int evm=(raw2ev[a&0xFFFFF]+raw2ev[bb&0xFFFFF])>>1;
    uint32_t interp=(uint32_t)ev2raw[clampi(evm,0,EV2RAW_SIZE-1)];
    if(c_isbright[y&3]){ bright[i]=c; dark[i]=interp; } else { dark[i]=c; bright[i]=interp; }
}

__global__ void k_fullres(const uint32_t* dark,const uint32_t* bright,uint32_t* fullres,int w,int h){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    fullres[i] = c_isbright[y&3] ? bright[i] : dark[i];
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

// ---- alias stages (inter-pixel stencils, KEPT SEPARATE in all modes) ----
__global__ void k_alias_init(const uint32_t* fullres,const uint32_t* halfres,uint16_t* aux,
                             const int* raw2ev,int w,int h){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    int d=raw2ev[fullres[i]&0xFFFFF]-raw2ev[halfres[i]&0xFFFFF];
    d=d<0?-d:d; aux[i]=(uint16_t)clampi(d,0,ALIAS_MAX);
}

__global__ void k_alias_rank(const uint16_t* in,uint16_t* out,int w,int h){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    int top[5]={0,0,0,0,0};
    #pragma unroll
    for(int dy=-6;dy<=6;dy+=2){
        int yy=clampi(y+dy,0,h-1);
        #pragma unroll
        for(int dx=-6;dx<=6;dx+=2){
            if(abs(dx)+abs(dy)>8) continue;
            int v=in[yy*w+clampi(x+dx,0,w-1)];
            if(v>top[4]){ int j=4; while(j>0 && v>top[j-1]){ top[j]=top[j-1]; --j; } top[j]=v; }
        }
    }
    out[i]=(uint16_t)top[4];
}

__global__ void k_alias_gauss(const uint16_t* in,uint16_t* out,int w,int h){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    const int W[7]={1024,820,657,421,337,173,139};
    long sum=0, wsum=0;
    #pragma unroll
    for(int dy=-6;dy<=6;dy+=2){
        int yy=clampi(y+dy,0,h-1);
        #pragma unroll
        for(int dx=-6;dx<=6;dx+=2){
            int r=(abs(dx)>abs(dy)?abs(dx):abs(dy))/2;
            if(abs(dx)+abs(dy)>8) continue;
            int wgt=W[r];
            sum += (long)in[yy*w+clampi(x+dx,0,w-1)]*wgt; wsum+=wgt;
        }
    }
    out[i]=(uint16_t)clampi((int)(wsum? sum/wsum:0),0,ALIAS_MAX);
}

__global__ void k_final_blend(const uint32_t* halfres,const uint32_t* fullres,
                              const uint16_t* alias,uint16_t* out,
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
    out[i]=(uint16_t)(r20>>4);
}

// ============================================================================
//  FUSED per-pixel kernel for M3.
//  Fuses convert20+match -> mean23 -> fullres into ONE pass. mean23 needs a
//  +/-2 vertical stencil over the *converted* raw32, so the fused kernel
//  recomputes the 3 needed converted samples (center, y-2, y+2) inline instead
//  of doing a global-memory round-trip of raw32. dark/bright/fullres are
//  written out (still needed by mix_halfres / alias / final_blend).
//  This cuts: the raw32 store+load round-trip, the fullres kernel launch, and
//  the mean23 kernel launch.
// ============================================================================
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
    fullres[i] = c_isbright[y&3] ? br : dk;   // fused fullres select
}

static float timed(cudaEvent_t a, cudaEvent_t b){ float ms=0; cudaEventElapsedTime(&ms,a,b); return ms; }

// ----------------------------------------------------------------------------
// Shared device state (allocated once, reused by every mode).
// ----------------------------------------------------------------------------
struct Dev {
    uint16_t *b14, *aliasA, *aliasB, *aliasMap, *out, *present;
    uint32_t *raw32, *dark, *bright, *fullres, *halfres;
    int *raw2ev, *ev2raw; float *mix, *fr;
    int w,h,n;
    dim3 blk, grd;
};

// Run the unfused per-pixel + alias chain on a given stream (no transfers).
static inline void run_unfused_compute(Dev& d, cudaStream_t s){
    k_convert20_match<<<d.grd,d.blk,0,s>>>(d.b14,d.raw32,d.w,d.h,256,1.7f);
    k_mean23<<<d.grd,d.blk,0,s>>>(d.raw32,d.dark,d.bright,d.raw2ev,d.ev2raw,d.w,d.h);
    k_fullres<<<d.grd,d.blk,0,s>>>(d.dark,d.bright,d.fullres,d.w,d.h);
    k_mix_halfres<<<d.grd,d.blk,0,s>>>(d.dark,d.bright,d.halfres,d.raw2ev,d.ev2raw,d.mix,d.w,d.h);
    k_alias_init<<<d.grd,d.blk,0,s>>>(d.fullres,d.halfres,d.aliasA,d.raw2ev,d.w,d.h);
    k_alias_rank<<<d.grd,d.blk,0,s>>>(d.aliasA,d.aliasB,d.w,d.h);
    k_alias_gauss<<<d.grd,d.blk,0,s>>>(d.aliasB,d.aliasMap,d.w,d.h);
    k_final_blend<<<d.grd,d.blk,0,s>>>(d.halfres,d.fullres,d.aliasMap,d.out,d.raw2ev,d.ev2raw,d.fr,d.bright,d.w,d.h);
}

// Run the FUSED per-pixel chain (M3) + alias chain.
static inline void run_fused_compute(Dev& d, cudaStream_t s){
    k_fused_convert_mean_fullres<<<d.grd,d.blk,0,s>>>(d.b14,d.dark,d.bright,d.fullres,
                                                      d.raw2ev,d.ev2raw,d.w,d.h,256,1.7f);
    k_mix_halfres<<<d.grd,d.blk,0,s>>>(d.dark,d.bright,d.halfres,d.raw2ev,d.ev2raw,d.mix,d.w,d.h);
    k_alias_init<<<d.grd,d.blk,0,s>>>(d.fullres,d.halfres,d.aliasA,d.raw2ev,d.w,d.h);
    k_alias_rank<<<d.grd,d.blk,0,s>>>(d.aliasA,d.aliasB,d.w,d.h);
    k_alias_gauss<<<d.grd,d.blk,0,s>>>(d.aliasB,d.aliasMap,d.w,d.h);
    k_final_blend<<<d.grd,d.blk,0,s>>>(d.halfres,d.fullres,d.aliasMap,d.out,d.raw2ev,d.ev2raw,d.fr,d.bright,d.w,d.h);
}

// kernels-only timing helper (compute on default stream, event-bracketed)
static double measure_kernels_only(Dev& d,int iters,bool fused){
    cudaEvent_t a,b; CK(cudaEventCreate(&a)); CK(cudaEventCreate(&b));
    double acc=0; int warm=iters-10; if(warm<1)warm=1;
    for(int it=0;it<iters;++it){
        CK(cudaEventRecord(a));
        if(fused) run_fused_compute(d,0); else run_unfused_compute(d,0);
        CK(cudaEventRecord(b)); CK(cudaEventSynchronize(b));
        if(it>=10) acc+=timed(a,b);
    }
    CK(cudaEventDestroy(a)); CK(cudaEventDestroy(b));
    return acc/warm;
}

// ----------------------------------------------------------------------------
//  Full-frame modes. Each returns avg warm frame time (ms).
//  serial: H2D -> compute -> (D2H | D2D-present). pinned/pageable host chosen by
//  caller. fused chooses the kernel chain.
// ----------------------------------------------------------------------------
enum Sink { SINK_D2H, SINK_D2D_PRESENT };

static double measure_serial(Dev& d, uint16_t* h_in, uint16_t* h_out,
                             int iters, bool fused, Sink sink){
    cudaEvent_t a,b; CK(cudaEventCreate(&a)); CK(cudaEventCreate(&b));
    double acc=0; int warm=iters-10; if(warm<1)warm=1;
    size_t inB=(size_t)d.n*2, outB=(size_t)d.n*2;
    for(int it=0;it<iters;++it){
        CK(cudaEventRecord(a));
        CK(cudaMemcpyAsync(d.b14,h_in,inB,cudaMemcpyHostToDevice,0));
        if(fused) run_fused_compute(d,0); else run_unfused_compute(d,0);
        if(sink==SINK_D2H) CK(cudaMemcpyAsync(h_out,d.out,outB,cudaMemcpyDeviceToHost,0));
        else               CK(cudaMemcpyAsync(d.present,d.out,outB,cudaMemcpyDeviceToDevice,0));
        CK(cudaEventRecord(b)); CK(cudaEventSynchronize(b));
        if(it>=10) acc+=timed(a,b);
    }
    CK(cudaEventDestroy(a)); CK(cudaEventDestroy(b));
    return acc/warm;
}

// Double-buffered, 3-stream pipeline: upload(N+1) and download(N-1) overlap
// compute(N). We measure STEADY-STATE per-frame throughput as wall time across
// the whole warm pipeline / frame count (the right metric for sustained fps:
// individual frame latency is higher, but the GPU emits one finished frame per
// pipeline step). Requires double-buffered b14/out + pinned host for true async.
struct Pipe {
    uint16_t *b14[2], *out[2];          // double-buffered I/O device side
    uint32_t *raw32[2],*dark[2],*bright[2],*fullres[2],*halfres[2];
    uint16_t *aliasA[2],*aliasB[2],*aliasMap[2];
    uint16_t *present;                   // single present target (D2D sink)
};

static double measure_streamed(Dev& base, Pipe& pp, uint16_t* h_in, uint16_t* h_out,
                               int iters, bool fused, Sink sink){
    cudaStream_t sUp, sCmp, sDn;
    CK(cudaStreamCreate(&sUp)); CK(cudaStreamCreate(&sCmp)); CK(cudaStreamCreate(&sDn));
    // per-slot events to chain dependencies across streams
    cudaEvent_t evUp[2], evCmp[2];
    for(int k=0;k<2;k++){ CK(cudaEventCreate(&evUp[k])); CK(cudaEventCreate(&evCmp[k])); }

    size_t inB=(size_t)base.n*2, outB=(size_t)base.n*2;
    int warm=iters-10; if(warm<1)warm=1;

    cudaEvent_t wallA, wallB; CK(cudaEventCreate(&wallA)); CK(cudaEventCreate(&wallB));

    auto compute_slot=[&](int slot){
        Dev d=base;
        d.b14=pp.b14[slot]; d.out=pp.out[slot];
        d.raw32=pp.raw32[slot]; d.dark=pp.dark[slot]; d.bright=pp.bright[slot];
        d.fullres=pp.fullres[slot]; d.halfres=pp.halfres[slot];
        d.aliasA=pp.aliasA[slot]; d.aliasB=pp.aliasB[slot]; d.aliasMap=pp.aliasMap[slot];
        if(fused) run_fused_compute(d,sCmp); else run_unfused_compute(d,sCmp);
    };

    // Warm the pipeline (10 iters) untimed, then time the steady state.
    for(int it=0; it<iters; ++it){
        if(it==10){ CK(cudaDeviceSynchronize()); CK(cudaEventRecord(wallA)); }
        int slot=it&1;
        // Upload N on sUp (pinned async). Record evUp.
        CK(cudaMemcpyAsync(pp.b14[slot],h_in,inB,cudaMemcpyHostToDevice,sUp));
        CK(cudaEventRecord(evUp[slot],sUp));
        // Compute waits on its upload.
        CK(cudaStreamWaitEvent(sCmp,evUp[slot],0));
        compute_slot(slot);
        CK(cudaEventRecord(evCmp[slot],sCmp));
        // Download / present waits on compute.
        CK(cudaStreamWaitEvent(sDn,evCmp[slot],0));
        if(sink==SINK_D2H) CK(cudaMemcpyAsync(h_out,pp.out[slot],outB,cudaMemcpyDeviceToHost,sDn));
        else               CK(cudaMemcpyAsync(pp.present,pp.out[slot],outB,cudaMemcpyDeviceToDevice,sDn));
    }
    CK(cudaDeviceSynchronize()); CK(cudaEventRecord(wallB)); CK(cudaEventSynchronize(wallB));
    double wall=timed(wallA,wallB);   // ms over `warm` frames steady state

    CK(cudaStreamDestroy(sUp)); CK(cudaStreamDestroy(sCmp)); CK(cudaStreamDestroy(sDn));
    return wall/warm;
}

int main(int argc,char**argv){
    int w = argc>1?atoi(argv[1]):1808;
    int h = argc>2?atoi(argv[2]):2268;
    int iters = argc>3?atoi(argv[3]):200;
    int n=w*h;
    cudaDeviceProp p; CK(cudaGetDeviceProperties(&p,0));
    printf("=== cuda_recon_opt :: max-throughput study (FMA/fast-math ON) ===\n");
    printf("device=%s  %dx%d (%.2f MP)  iters=%d (warm=%d)\n", p.name, w,h, n/1e6, iters, iters-10);

    // ---- host synthetic data ----
    uint16_t* h_b14_paged=(uint16_t*)malloc((size_t)n*2);
    for(int i=0;i<n;i++) h_b14_paged[i]=(uint16_t)((i*2654435761u)&0x3FFF);
    uint16_t* h_out_paged=(uint16_t*)malloc((size_t)n*2);

    uint16_t *h_b14_pin=nullptr, *h_out_pin=nullptr;
    CK(cudaHostAlloc((void**)&h_b14_pin,(size_t)n*2,cudaHostAllocDefault));
    CK(cudaHostAlloc((void**)&h_out_pin,(size_t)n*2,cudaHostAllocDefault));
    memcpy(h_b14_pin,h_b14_paged,(size_t)n*2);

    int* h_raw2ev=(int*)malloc((size_t)RAW2EV_SIZE*4);
    for(int i=0;i<RAW2EV_SIZE;i++) h_raw2ev[i]=(int)(logf((float)i+1.f)*10000.f);
    int* h_ev2raw=(int*)malloc((size_t)EV2RAW_SIZE*4);
    for(int i=0;i<EV2RAW_SIZE;i++) h_ev2raw[i]=i%(1<<20);
    float* h_mix=(float*)malloc((size_t)CURVE_SIZE*4);
    float* h_fr=(float*)malloc((size_t)CURVE_SIZE*4);
    for(int i=0;i<CURVE_SIZE;i++){ h_mix[i]=0.5f; h_fr[i]=0.5f; }
    int isbright[4]={1,1,0,0};

    // ---- shared single-buffer device state (M0..M1, kernels-only, serial) ----
    Dev d; d.w=w; d.h=h; d.n=n; d.blk=dim3(16,16); d.grd=dim3((w+15)/16,(h+15)/16);
    CK(cudaMalloc(&d.b14,(size_t)n*2));
    CK(cudaMalloc(&d.aliasA,(size_t)n*2)); CK(cudaMalloc(&d.aliasB,(size_t)n*2));
    CK(cudaMalloc(&d.aliasMap,(size_t)n*2)); CK(cudaMalloc(&d.out,(size_t)n*2));
    CK(cudaMalloc(&d.present,(size_t)n*2));
    CK(cudaMalloc(&d.raw32,(size_t)n*4)); CK(cudaMalloc(&d.dark,(size_t)n*4));
    CK(cudaMalloc(&d.bright,(size_t)n*4)); CK(cudaMalloc(&d.fullres,(size_t)n*4));
    CK(cudaMalloc(&d.halfres,(size_t)n*4));
    CK(cudaMalloc(&d.raw2ev,(size_t)RAW2EV_SIZE*4)); CK(cudaMalloc(&d.ev2raw,(size_t)EV2RAW_SIZE*4));
    CK(cudaMalloc(&d.mix,(size_t)CURVE_SIZE*4)); CK(cudaMalloc(&d.fr,(size_t)CURVE_SIZE*4));
    CK(cudaMemcpy(d.raw2ev,h_raw2ev,(size_t)RAW2EV_SIZE*4,cudaMemcpyHostToDevice));
    CK(cudaMemcpy(d.ev2raw,h_ev2raw,(size_t)EV2RAW_SIZE*4,cudaMemcpyHostToDevice));
    CK(cudaMemcpy(d.mix,h_mix,(size_t)CURVE_SIZE*4,cudaMemcpyHostToDevice));
    CK(cudaMemcpy(d.fr,h_fr,(size_t)CURVE_SIZE*4,cudaMemcpyHostToDevice));
    CK(cudaMemcpyToSymbol(c_isbright,isbright,sizeof(isbright)));

    // ---- double-buffered pipeline state (M2/M3 streamed) ----
    Pipe pp;
    for(int k=0;k<2;k++){
        CK(cudaMalloc(&pp.b14[k],(size_t)n*2));   CK(cudaMalloc(&pp.out[k],(size_t)n*2));
        CK(cudaMalloc(&pp.aliasA[k],(size_t)n*2));CK(cudaMalloc(&pp.aliasB[k],(size_t)n*2));
        CK(cudaMalloc(&pp.aliasMap[k],(size_t)n*2));
        CK(cudaMalloc(&pp.raw32[k],(size_t)n*4)); CK(cudaMalloc(&pp.dark[k],(size_t)n*4));
        CK(cudaMalloc(&pp.bright[k],(size_t)n*4));CK(cudaMalloc(&pp.fullres[k],(size_t)n*4));
        CK(cudaMalloc(&pp.halfres[k],(size_t)n*4));
    }
    CK(cudaMalloc(&pp.present,(size_t)n*2));

    // ===================== MEASUREMENTS =====================
    // kernels-only (context for transfer share)
    double kern_unfused = measure_kernels_only(d, iters, false);
    double kern_fused   = measure_kernels_only(d, iters, true);

    // M0 baseline: pageable host, serial, unfused, D2H
    double m0 = measure_serial(d, h_b14_paged, h_out_paged, iters, false, SINK_D2H);
    // M1 pinned: pinned host, serial, unfused, D2H
    double m1 = measure_serial(d, h_b14_pin, h_out_pin, iters, false, SINK_D2H);
    // M2 streams: pinned + double-buffer overlap, unfused, D2H
    double m2 = measure_streamed(d, pp, h_b14_pin, h_out_pin, iters, false, SINK_D2H);
    // M3 fused (= all combined): pinned + streams + fused per-pixel, D2H
    double m3 = measure_streamed(d, pp, h_b14_pin, h_out_pin, iters, true, SINK_D2H);

    // PRESENT (D2D) variants: serial-pinned and all-combined, present sink
    double pres_serial = measure_serial(d, h_b14_pin, h_out_pin, iters, false, SINK_D2D_PRESENT);
    double pres_best   = measure_streamed(d, pp, h_b14_pin, h_out_pin, iters, true, SINK_D2D_PRESENT);

    auto fps=[](double ms){ return 1000.0/ms; };
    printf("\n--- kernels only (no transfers) ---\n");
    printf("  unfused chain : %7.3f ms (%.1f fps-eq)\n", kern_unfused, fps(kern_unfused));
    printf("  fused   chain : %7.3f ms (%.1f fps-eq)\n", kern_fused,   fps(kern_fused));

    printf("\n--- FULL FRAME (incl transfers), avg warm ---\n");
    printf("  %-34s %8.3f ms  %7.1f fps\n","M0 baseline (pageable,serial,D2H)",m0,fps(m0));
    printf("  %-34s %8.3f ms  %7.1f fps\n","M1 +pinned host",m1,fps(m1));
    printf("  %-34s %8.3f ms  %7.1f fps\n","M2 +streams (overlap)",m2,fps(m2));
    printf("  %-34s %8.3f ms  %7.1f fps\n","M3 +fused (ALL COMBINED, D2H)",m3,fps(m3));

    printf("\n--- NO-READBACK PLAYBACK (D2D present, real CUDA->GL path) ---\n");
    printf("  %-34s %8.3f ms  %7.1f fps\n","serial+pinned, D2D present",pres_serial,fps(pres_serial));
    printf("  %-34s %8.3f ms  %7.1f fps\n","ALL COMBINED + D2D present (BEST)",pres_best,fps(pres_best));

    printf("\n=== HEADLINE: best sustained throughput @ %.2f MP ===\n", n/1e6);
    printf("  with readback (D2H)     : %.3f ms  (%.1f fps)\n", m3, fps(m3));
    printf("  no readback (D2D/GL)    : %.3f ms  (%.1f fps)\n", pres_best, fps(pres_best));
    printf("  baseline was            : %.3f ms  (%.1f fps)\n", m0, fps(m0));
    printf("  speedup (D2H best/base) : %.2fx ; (D2D best/base) : %.2fx\n",
           m0/m3, m0/pres_best);
    printf("  budget(24fps)=41.67ms : D2H=%s  D2D=%s\n",
           m3<=41.67?"WITHIN":"OVER", pres_best<=41.67?"WITHIN":"OVER");
    return 0;
}
