// cuda_recon_probe.cu - Tier 2 TIMING probe for Dual ISO recon on the RTX 4090.
//
// Implements the recon kernel chain (per
// .claude-state/profiling/20260614-tier2-cuda/recon-algorithm-map.md) with
// realistic memory-access patterns + LUT gathers, on a synthetic full-res Bayer
// frame, and measures H2D / per-kernel / D2H with cudaEvents.
//
// Goal: answer "can full-res Dual ISO recon clear the 41.7 ms / 24 fps budget on
// the GPU, with no per-frame readback?"  This is honest TIMING: the kernel
// structure, tap counts, stencils and LUT-gather patterns are real, so the
// measured kernel time is representative. LUT and Bayer *contents* are synthetic
// (timing is independent of values). Bit-exact PARITY vs the CPU is a separate
// task; the kernels here are faithful in shape, approximate in exact constants
// (notably the alias rank/Gaussian footprints, documented inline).
//
// Usage: cuda_recon_probe.exe [width] [height] [iters]
//   defaults: 1808 2268 (4.1 MP, the test-clip resolution) , 60 iters.

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

// Stage 1: 14-bit -> 20-bit + per-row exposure-match apply (match scalars from CPU).
__global__ void k_convert20_match(const uint16_t* b14, uint32_t* raw32, int w,int h,
                                  int black_delta, float ev_scale){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    uint32_t v=((uint32_t)b14[i])<<6;
    if(!c_isbright[y&3]){ float fv=((float)v-(float)(black_delta<<6))*ev_scale; v=(uint32_t)fmaxf(0.f,fv); }
    raw32[i]=v;
}

// Stage 2: mean23 interpolation of the missing field via EV space (vertical +/-2 stencil).
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

// Stage 3: fullres reconstruction (per-pixel field select).
__global__ void k_fullres(const uint32_t* dark,const uint32_t* bright,uint32_t* fullres,int w,int h){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    fullres[i] = c_isbright[y&3] ? bright[i] : dark[i];
}

// Stage 4: mix -> halfres (per-pixel LUT-gather blend).
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

// Stage 5a: alias map init-diff (|EV(fullres)-EV(halfres)|).
__global__ void k_alias_init(const uint32_t* fullres,const uint32_t* halfres,uint16_t* aux,
                             const int* raw2ev,int w,int h){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    int d=raw2ev[fullres[i]&0xFFFFF]-raw2ev[halfres[i]&0xFFFFF];
    d=d<0?-d:d; aux[i]=(uint16_t)clampi(d,0,ALIAS_MAX);
}

// Stage 5b: ~37-tap radius-6 step-2 diamond, 5th-largest rank filter.
// Footprint approximates the engine's 37-tap selection (dualiso.c:1683).
__global__ void k_alias_rank(const uint16_t* in,uint16_t* out,int w,int h){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    int top[5]={0,0,0,0,0};
    #pragma unroll
    for(int dy=-6;dy<=6;dy+=2){
        int yy=clampi(y+dy,0,h-1);
        #pragma unroll
        for(int dx=-6;dx<=6;dx+=2){
            if(abs(dx)+abs(dy)>8) continue;       // diamond -> ~37 taps
            int v=in[yy*w+clampi(x+dx,0,w-1)];
            if(v>top[4]){ int j=4; while(j>0 && v>top[j-1]){ top[j]=top[j-1]; --j; } top[j]=v; }
        }
    }
    out[i]=(uint16_t)top[4];
}

// Stage 5c: 21-tap radius-6 step-2 weighted Gaussian (ring weights /1024 per the map).
__global__ void k_alias_gauss(const uint16_t* in,uint16_t* out,int w,int h){
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w||y>=h) return; int i=y*w+x;
    const int W[7]={1024,820,657,421,337,173,139};   // by ring = max(|dx|,|dy|)/2
    long sum=0, wsum=0;
    #pragma unroll
    for(int dy=-6;dy<=6;dy+=2){
        int yy=clampi(y+dy,0,h-1);
        #pragma unroll
        for(int dx=-6;dx<=6;dx+=2){
            int r=(abs(dx)>abs(dy)?abs(dx):abs(dy))/2;
            if(abs(dx)+abs(dy)>8) continue;            // 21-tap diamond subset
            int wgt=W[r];
            sum += (long)in[yy*w+clampi(x+dx,0,w-1)]*wgt; wsum+=wgt;
        }
    }
    out[i]=(uint16_t)clampi((int)(wsum? sum/wsum:0),0,ALIAS_MAX);
}

// Stage 6: final blend (per-pixel LUT-gather), fused to 16-bit output.
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
    out[i]=(uint16_t)(r20>>4);   // 20-bit -> 16-bit
}

static float timed(cudaEvent_t a, cudaEvent_t b){ float ms=0; cudaEventElapsedTime(&ms,a,b); return ms; }

int main(int argc,char**argv){
    int w = argc>1?atoi(argv[1]):1808;
    int h = argc>2?atoi(argv[2]):2268;
    int iters = argc>3?atoi(argv[3]):60;
    int n=w*h;
    cudaDeviceProp p; CK(cudaGetDeviceProperties(&p,0));
    printf("device=%s  %dx%d (%.1f MP)  iters=%d\n", p.name, w,h, n/1e6, iters);

    // ---- host synthetic data ----
    uint16_t* h_b14=(uint16_t*)malloc((size_t)n*2);
    for(int i=0;i<n;i++) h_b14[i]=(uint16_t)((i*2654435761u)&0x3FFF);
    int* h_raw2ev=(int*)malloc((size_t)RAW2EV_SIZE*4);
    for(int i=0;i<RAW2EV_SIZE;i++) h_raw2ev[i]=(int)(logf((float)i+1.f)*10000.f);
    int* h_ev2raw=(int*)malloc((size_t)EV2RAW_SIZE*4);
    for(int i=0;i<EV2RAW_SIZE;i++) h_ev2raw[i]=i%(1<<20);
    float* h_mix=(float*)malloc((size_t)CURVE_SIZE*4);
    float* h_fr=(float*)malloc((size_t)CURVE_SIZE*4);
    for(int i=0;i<CURVE_SIZE;i++){ h_mix[i]=0.5f; h_fr[i]=0.5f; }
    int isbright[4]={1,1,0,0};

    // ---- device buffers ----
    uint16_t *d_b14,*d_aliasA,*d_aliasB,*d_aliasMap,*d_out;
    uint32_t *d_raw32,*d_dark,*d_bright,*d_fullres,*d_halfres;
    int *d_raw2ev,*d_ev2raw; float *d_mix,*d_fr;
    CK(cudaMalloc(&d_b14,(size_t)n*2));
    CK(cudaMalloc(&d_aliasA,(size_t)n*2)); CK(cudaMalloc(&d_aliasB,(size_t)n*2));
    CK(cudaMalloc(&d_aliasMap,(size_t)n*2)); CK(cudaMalloc(&d_out,(size_t)n*2));
    CK(cudaMalloc(&d_raw32,(size_t)n*4)); CK(cudaMalloc(&d_dark,(size_t)n*4));
    CK(cudaMalloc(&d_bright,(size_t)n*4)); CK(cudaMalloc(&d_fullres,(size_t)n*4));
    CK(cudaMalloc(&d_halfres,(size_t)n*4));
    CK(cudaMalloc(&d_raw2ev,(size_t)RAW2EV_SIZE*4)); CK(cudaMalloc(&d_ev2raw,(size_t)EV2RAW_SIZE*4));
    CK(cudaMalloc(&d_mix,(size_t)CURVE_SIZE*4)); CK(cudaMalloc(&d_fr,(size_t)CURVE_SIZE*4));

    // LUT upload once (timed separately - rebuilt on host only on key change).
    cudaEvent_t e0,e1; CK(cudaEventCreate(&e0)); CK(cudaEventCreate(&e1));
    CK(cudaEventRecord(e0));
    CK(cudaMemcpy(d_raw2ev,h_raw2ev,(size_t)RAW2EV_SIZE*4,cudaMemcpyHostToDevice));
    CK(cudaMemcpy(d_ev2raw,h_ev2raw,(size_t)EV2RAW_SIZE*4,cudaMemcpyHostToDevice));
    CK(cudaMemcpy(d_mix,h_mix,(size_t)CURVE_SIZE*4,cudaMemcpyHostToDevice));
    CK(cudaMemcpy(d_fr,h_fr,(size_t)CURVE_SIZE*4,cudaMemcpyHostToDevice));
    CK(cudaEventRecord(e1)); CK(cudaEventSynchronize(e1));
    printf("LUT upload (once): %.2f ms\n", timed(e0,e1));
    CK(cudaMemcpyToSymbol(c_isbright,isbright,sizeof(isbright)));

    dim3 blk(16,16), grd((w+15)/16,(h+15)/16);
    const int NS=8; const char* names[NS]={"H2D_bayer","convert20+match","mean23","fullres","mix_halfres","alias(init+rank+gauss)","final_blend","D2H_out"};
    double acc[NS]={0};
    cudaEvent_t ev[NS+1]; for(int i=0;i<=NS;i++) CK(cudaEventCreate(&ev[i]));
    uint16_t* h_out=(uint16_t*)malloc((size_t)n*2);

    for(int it=0; it<iters; ++it){
        int s=0;
        CK(cudaEventRecord(ev[s++]));                                   // start
        CK(cudaMemcpyAsync(d_b14,h_b14,(size_t)n*2,cudaMemcpyHostToDevice)); CK(cudaEventRecord(ev[s++])); // H2D
        k_convert20_match<<<grd,blk>>>(d_b14,d_raw32,w,h,256,1.7f);     CK(cudaEventRecord(ev[s++]));
        k_mean23<<<grd,blk>>>(d_raw32,d_dark,d_bright,d_raw2ev,d_ev2raw,w,h); CK(cudaEventRecord(ev[s++]));
        k_fullres<<<grd,blk>>>(d_dark,d_bright,d_fullres,w,h);          CK(cudaEventRecord(ev[s++]));
        k_mix_halfres<<<grd,blk>>>(d_dark,d_bright,d_halfres,d_raw2ev,d_ev2raw,d_mix,w,h); CK(cudaEventRecord(ev[s++]));
        k_alias_init<<<grd,blk>>>(d_fullres,d_halfres,d_aliasA,d_raw2ev,w,h);
        k_alias_rank<<<grd,blk>>>(d_aliasA,d_aliasB,w,h);
        k_alias_gauss<<<grd,blk>>>(d_aliasB,d_aliasMap,w,h);            CK(cudaEventRecord(ev[s++]));
        k_final_blend<<<grd,blk>>>(d_halfres,d_fullres,d_aliasMap,d_out,d_raw2ev,d_ev2raw,d_fr,d_bright,w,h); CK(cudaEventRecord(ev[s++]));
        CK(cudaMemcpyAsync(h_out,d_out,(size_t)n*2,cudaMemcpyDeviceToHost)); CK(cudaEventRecord(ev[s++]));
        CK(cudaEventSynchronize(ev[NS]));
        if(it>=10) for(int k=0;k<NS;k++) acc[k]+=timed(ev[k],ev[k+1]);  // warm only
    }
    int warm=iters-10; if(warm<1) warm=1;
    double frame=0;
    printf("\n--- warm per-stage (avg of %d iters), %.1f MP ---\n", warm, n/1e6);
    for(int k=0;k<NS;k++){ double ms=acc[k]/warm; frame+=ms; printf("  %-26s %7.3f ms\n", names[k], ms); }
    printf("  %-26s %7.3f ms\n","FRAME TOTAL",frame);
    printf("\n  => %.1f fps-equivalent ; budget(24fps)=41.67 ms ; %s\n",
           1000.0/frame, frame<=41.67?"WITHIN BUDGET":"over budget");
    // device kernels only (exclude transfers) - the no-readback playback relevant number:
    double kern=frame-acc[0]/warm-acc[NS-1]/warm;
    printf("  device kernels only (no H2D/D2H): %.3f ms  (%.1f fps-eq)\n", kern, 1000.0/kern);
    return 0;
}
