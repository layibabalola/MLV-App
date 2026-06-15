/* igpu_amaze_debayer_cuda.cu - CUDA backend for the generic AMaZE debayer C ABI.
 *
 * The AMaZE math kernels are reused from the reviewed full-stage probe:
 * tools/gpu/cuda_amaze_debayer_stage_probe.cu. This file adds the production
 * ABI wrapper plus full-frame tile orchestration; it does not change GUI/UX
 * claims by itself.
 */

#include "igpu_amaze_debayer.h"

#define CUDA_AMAZE_DEBAYER_STAGE_PROBE_LIBRARY 1
#define CUDA_AMAZE_DEBAYER_STAGE_PROBE_NO_MAIN 1
#include "../cuda_amaze_debayer_stage_probe.cu"

#include <chrono>
#include <cstring>
#include <sstream>

struct igpu_amaze_debayer_backend
{
    std::string description;
    igpu_amaze_debayer_timing_t lastTiming;
};

namespace
{
double now_ms()
{
    using clock = std::chrono::steady_clock;
    const auto now = clock::now().time_since_epoch();
    return std::chrono::duration<double, std::milli>(now).count();
}

__global__ void k_tile_load_float(const float * raw,
                                  float * cfa,
                                  float * rgbgreen,
                                  int width,
                                  int height,
                                  int top,
                                  int left,
                                  int rr1,
                                  int cc1)
{
    const int cc = blockIdx.x * blockDim.x + threadIdx.x;
    const int rr = blockIdx.y * blockDim.y + threadIdx.y;
    if (rr >= rr1 || cc >= cc1) return;

    const int row = reflect_index(top + rr, height);
    const int col = reflect_index(left + cc, width);
    const int idx = rr * kTileSize + cc;
    const float normalized =
        raw[static_cast<std::size_t>(row) * width + col] / 65535.0f;
    cfa[idx] = normalized;
    rgbgreen[idx] = (fc_rggb(rr, cc) == 1) ? normalized : 0.0f;
}

__device__ uint16_t truncate_amaze_float_to_u16(float value)
{
    if (!(value > 0.0f)) return 0;
    if (!(value < 65535.0f)) return 65535;
    return static_cast<uint16_t>(static_cast<uint32_t>(value));
}

__global__ void k_store_tile_rgb16(const float * red,
                                   const float * green,
                                   const float * blue,
                                   uint16_t * outRgb16,
                                   int width,
                                   int height,
                                   int top,
                                   int left,
                                   int rr1,
                                   int cc1)
{
    const int cc = blockIdx.x * blockDim.x + threadIdx.x;
    const int rr = blockIdx.y * blockDim.y + threadIdx.y;
    if (rr < 16 || rr >= rr1 - 16 || cc < 16 || cc >= cc1 - 16) return;

    const int row = top + rr;
    const int col = left + cc;
    if (row < 0 || row >= height || col < 0 || col >= width) return;

    const int tileIndex = rr * kTileSize + cc;
    const std::size_t outIndex =
        (static_cast<std::size_t>(row) * width + col) * 3u;
    outRgb16[outIndex + 0] = truncate_amaze_float_to_u16(red[tileIndex]);
    outRgb16[outIndex + 1] = truncate_amaze_float_to_u16(green[tileIndex]);
    outRgb16[outIndex + 2] = truncate_amaze_float_to_u16(blue[tileIndex]);
}

void run_tile(const float * dRaw,
              uint16_t * dRgb16,
              DeviceBuffers & d,
              int width,
              int height,
              int top,
              int left)
{
    const int bottom = imin_i(top + kTileSize, height + 16);
    const int right = imin_i(left + kTileSize, width + 16);
    const int rr1 = bottom - top;
    const int cc1 = right - left;

    clear_device_stages(d);

    const dim3 block(16, 16);
    const dim3 grid((kTileSize + block.x - 1) / block.x,
                    (kTileSize + block.y - 1) / block.y);

    k_tile_load_float<<<grid, block>>>(dRaw,
                                       d.cfa,
                                       d.rgbgreen,
                                       width,
                                       height,
                                       top,
                                       left,
                                       rr1,
                                       cc1);
    CK(cudaGetLastError());
    k_gradients<<<grid, block>>>(d.cfa, d.dirwts0, d.dirwts1, d.delhvsqsum, rr1, cc1);
    CK(cudaGetLastError());
    k_diagonal_precursors<<<grid, block>>>(d.cfa, d.delp, d.delm, d.dgrbsq1p, d.dgrbsq1m, rr1, cc1);
    CK(cudaGetLastError());
    k_green_interpolation<<<grid, block>>>(d.cfa,
                                           d.dirwts0,
                                           d.dirwts1,
                                           d.vcd,
                                           d.hcd,
                                           d.vcdalt,
                                           d.hcdalt,
                                           d.dgintv,
                                           d.dginth,
                                           rr1,
                                           cc1);
    CK(cudaGetLastError());
    k_variance_selection_scalar<<<1, 1>>>(d.cfa, d.vcd, d.hcd, d.vcdalt, d.hcdalt, d.cddiffsq, rr1, cc1);
    CK(cudaGetLastError());
    k_hvwt_adaptive_weights<<<grid, block>>>(d.dirwts0,
                                             d.dirwts1,
                                             d.vcd,
                                             d.hcd,
                                             d.dgintv,
                                             d.dginth,
                                             d.hvwt,
                                             rr1,
                                             cc1);
    CK(cudaGetLastError());
    k_nyquist_test<<<grid, block>>>(d.cddiffsq, d.delhvsqsum, d.nyquist, rr1, cc1);
    CK(cudaGetLastError());
    k_nyquist_refine_scalar<<<1, 1>>>(d.nyquist, rr1, cc1);
    CK(cudaGetLastError());
    k_nyquist_area_interpolation<<<grid, block>>>(d.cfa, d.nyquist, d.hvwt, rr1, cc1);
    CK(cudaGetLastError());
    k_green_plane_assembly_scalar<<<1, 1>>>(d.cfa,
                                            d.vcd,
                                            d.hcd,
                                            d.nyquist,
                                            d.hvwt,
                                            d.dgrb0,
                                            d.rgbgreen,
                                            d.dgrb2h,
                                            d.dgrb2v,
                                            rr1,
                                            cc1);
    CK(cudaGetLastError());
    k_nyquist_green_refinement<<<grid, block>>>(d.cfa,
                                                d.vcd,
                                                d.hcd,
                                                d.nyquist,
                                                d.dgrb2h,
                                                d.dgrb2v,
                                                d.dgrb0,
                                                d.rgbgreen,
                                                rr1,
                                                cc1);
    CK(cudaGetLastError());
    k_diagonal_rb_interpolation<<<grid, block>>>(d.cfa,
                                                 d.delp,
                                                 d.delm,
                                                 d.dgrbsq1p,
                                                 d.dgrbsq1m,
                                                 d.rbm,
                                                 d.rbp,
                                                 d.pmwt,
                                                 rr1,
                                                 cc1);
    CK(cudaGetLastError());
    k_pmwt_refinement_and_rbint_scalar<<<1, 1>>>(d.cfa,
                                                 d.rbm,
                                                 d.rbp,
                                                 d.pmwt,
                                                 d.pmwtalt,
                                                 d.rbint,
                                                 rr1,
                                                 cc1);
    CK(cudaGetLastError());
    k_diagonal_green_correction<<<grid, block>>>(d.cfa,
                                                 d.dirwts0,
                                                 d.dirwts1,
                                                 d.hvwt,
                                                 d.pmwt,
                                                 d.rbint,
                                                 d.dgrb0,
                                                 d.rgbgreen,
                                                 rr1,
                                                 cc1);
    CK(cudaGetLastError());
    k_chrominance_coset_split<<<grid, block>>>(d.dgrb0, d.dgrb1, rr1, cc1);
    CK(cudaGetLastError());
    k_fancy_chrominance_interpolation<<<grid, block>>>(d.dgrb0, d.dgrb1, rr1, cc1);
    CK(cudaGetLastError());
    k_final_output_planes<<<grid, block>>>(d.rgbgreen,
                                           d.hvwt,
                                           d.dgrb0,
                                           d.dgrb1,
                                           d.red,
                                           d.green,
                                           d.blue,
                                           rr1,
                                           cc1);
    CK(cudaGetLastError());
    k_store_tile_rgb16<<<grid, block>>>(d.red,
                                        d.green,
                                        d.blue,
                                        dRgb16,
                                        width,
                                        height,
                                        top,
                                        left,
                                        rr1,
                                        cc1);
    CK(cudaGetLastError());
}
} // namespace

extern "C" igpu_amaze_debayer_backend * igpu_amaze_debayer_create(const char * backend_name)
{
    if (backend_name && std::strcmp(backend_name, "cuda") != 0) return nullptr;

    try
    {
        int deviceCount = 0;
        CK(cudaGetDeviceCount(&deviceCount));
        if (deviceCount < 1) return nullptr;
        CK(cudaSetDevice(0));

        cudaDeviceProp prop;
        CK(cudaGetDeviceProperties(&prop, 0));

        igpu_amaze_debayer_backend * backend = new igpu_amaze_debayer_backend();
        backend->description = std::string("CUDA AMaZE / ") + prop.name;
        std::memset(&backend->lastTiming, 0, sizeof(backend->lastTiming));
        return backend;
    }
    catch (const CudaStageProbeError &)
    {
        return nullptr;
    }
}

extern "C" void igpu_amaze_debayer_destroy(igpu_amaze_debayer_backend * backend)
{
    delete backend;
}

extern "C" int igpu_amaze_debayer_abi_version(igpu_amaze_debayer_backend *)
{
    return IGPU_AMAZE_DEBAYER_ABI_VERSION;
}

extern "C" const char * igpu_amaze_debayer_describe(igpu_amaze_debayer_backend * backend)
{
    return backend ? backend->description.c_str() : "unavailable";
}

extern "C" int igpu_amaze_debayer_run(igpu_amaze_debayer_backend * backend,
                                      const float * in_raw_float,
                                      uint16_t * out_rgb16,
                                      int width,
                                      int height)
{
    if (!backend || !in_raw_float || !out_rgb16 || width <= 32 || height <= 32)
    {
        return -1;
    }

    float * dRaw = nullptr;
    uint16_t * dRgb16 = nullptr;
    DeviceBuffers d;
    const std::size_t pixelCount =
        static_cast<std::size_t>(width) * static_cast<std::size_t>(height);
    const std::size_t rawBytes = pixelCount * sizeof(float);
    const std::size_t rgbBytes = pixelCount * 3u * sizeof(uint16_t);
    std::memset(&backend->lastTiming, 0, sizeof(backend->lastTiming));

    const double totalStart = now_ms();
    try
    {
        CK(cudaMalloc(&dRaw, rawBytes));
        CK(cudaMalloc(&dRgb16, rgbBytes));
        allocate_device(&d, 1);
        CK(cudaMemset(dRgb16, 0, rgbBytes));

        const double uploadStart = now_ms();
        CK(cudaMemcpy(dRaw, in_raw_float, rawBytes, cudaMemcpyHostToDevice));
        CK(cudaDeviceSynchronize());
        backend->lastTiming.upload_ms = now_ms() - uploadStart;

        const double kernelStart = now_ms();
        for (int top = -16; top < height; top += kTileSize - 32)
        {
            for (int left = -16; left < width; left += kTileSize - 32)
            {
                run_tile(dRaw, dRgb16, d, width, height, top, left);
            }
        }
        CK(cudaDeviceSynchronize());
        backend->lastTiming.kernel_ms = now_ms() - kernelStart;

        const double downloadStart = now_ms();
        CK(cudaMemcpy(out_rgb16, dRgb16, rgbBytes, cudaMemcpyDeviceToHost));
        CK(cudaDeviceSynchronize());
        backend->lastTiming.download_ms = now_ms() - downloadStart;
        backend->lastTiming.total_ms = now_ms() - totalStart;

        free_device(&d);
        cudaFree(dRaw);
        cudaFree(dRgb16);
        return 0;
    }
    catch (const CudaStageProbeError & error)
    {
        std::fprintf(stderr, "[igpu_amaze_debayer_cuda] %s\n", error.what());
        free_device(&d);
        cudaFree(dRaw);
        cudaFree(dRgb16);
        backend->lastTiming.total_ms = now_ms() - totalStart;
        return -2;
    }
}

extern "C" int igpu_amaze_debayer_last_timing(igpu_amaze_debayer_backend * backend,
                                              igpu_amaze_debayer_timing_t * timing)
{
    if (!backend || !timing) return -1;
    *timing = backend->lastTiming;
    return 0;
}
