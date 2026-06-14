// cuda_smoke.cu - validates the nvcc/MSVC/device build+run path on the 4090.
// Prints device info and runs a vector-add kernel with verification.
#include <cstdio>
#include <cstdlib>
#include <cuda_runtime.h>

__global__ void vadd(const float* a, const float* b, float* c, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) c[i] = a[i] + b[i];
}

int main() {
    int devCount = 0;
    if (cudaGetDeviceCount(&devCount) != cudaSuccess || devCount == 0) {
        printf("FAIL: no CUDA device\n");
        return 2;
    }
    cudaDeviceProp p;
    cudaGetDeviceProperties(&p, 0);
    printf("device: %s  cc=sm_%d%d  globalMem=%.1f GB  SMs=%d  clock=%.2f GHz\n",
           p.name, p.major, p.minor, p.totalGlobalMem / 1e9, p.multiProcessorCount,
           p.clockRate / 1e6);

    const int n = 1 << 22;             // 4M elements
    const size_t sz = (size_t)n * sizeof(float);
    float *ha = (float*)malloc(sz), *hb = (float*)malloc(sz), *hc = (float*)malloc(sz);
    for (int i = 0; i < n; ++i) { ha[i] = (float)i; hb[i] = 2.0f * i; }

    float *da, *db, *dc;
    cudaMalloc(&da, sz); cudaMalloc(&db, sz); cudaMalloc(&dc, sz);
    cudaMemcpy(da, ha, sz, cudaMemcpyHostToDevice);
    cudaMemcpy(db, hb, sz, cudaMemcpyHostToDevice);
    vadd<<<(n + 255) / 256, 256>>>(da, db, dc, n);
    cudaError_t e = cudaDeviceSynchronize();
    if (e != cudaSuccess) { printf("FAIL kernel: %s\n", cudaGetErrorString(e)); return 3; }
    cudaMemcpy(hc, dc, sz, cudaMemcpyDeviceToHost);

    int bad = 0;
    for (int i = 0; i < n; ++i) if (hc[i] != ha[i] + hb[i]) ++bad;
    printf("vector_add: %s (%d mismatches of %d)\n", bad ? "FAIL" : "OK", bad, n);
    cudaFree(da); cudaFree(db); cudaFree(dc); free(ha); free(hb); free(hc);
    return bad ? 4 : 0;
}
