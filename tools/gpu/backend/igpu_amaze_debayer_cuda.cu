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
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif
#include <GL/gl.h>
#include <cuda_gl_interop.h>

#ifndef GL_TEXTURE_2D
#define GL_TEXTURE_2D 0x0DE1
#endif

struct igpu_amaze_debayer_backend
{
    std::string description;
    igpu_amaze_debayer_timing_t lastTiming;
    uint16_t * liveReconBayer16 = nullptr;
    float * liveRawFloat = nullptr;
    uint16_t * liveRgb16 = nullptr;
    uint16_t * liveRgba16 = nullptr;
    DeviceBuffers liveDeviceBuffers;
    std::size_t livePixelCount = 0;
    int liveWidth = 0;
    int liveHeight = 0;
    bool liveDeviceBuffersAllocated = false;
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

__global__ void k_pack_rgb16_to_rgba16(const uint16_t * rgb,
                                       uint16_t * rgba,
                                       std::size_t pixelCount)
{
    const std::size_t pixel =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (pixel >= pixelCount) return;

    rgba[pixel * 4u + 0u] = rgb[pixel * 3u + 0u];
    rgba[pixel * 4u + 1u] = rgb[pixel * 3u + 1u];
    rgba[pixel * 4u + 2u] = rgb[pixel * 3u + 2u];
    rgba[pixel * 4u + 3u] = 65535u;
}

__device__ __forceinline__ uint16_t clamp_double_to_u16(double value)
{
    if (value <= 0.0) return 0u;
    if (value >= 65535.0) return 65535u;
    return static_cast<uint16_t>(value);
}

__global__ void k_pack_rgb16_to_rgba16_post_wb_undo(const uint16_t * rgb,
                                                    uint16_t * rgba,
                                                    std::size_t pixelCount,
                                                    int blackLevel,
                                                    double wbMultiplierR,
                                                    double wbMultiplierG,
                                                    double wbMultiplierB)
{
    const std::size_t pixel =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (pixel >= pixelCount) return;

    const std::size_t rgbIndex = pixel * 3u;
    const std::size_t rgbaIndex = pixel * 4u;
    rgba[rgbaIndex + 0u] =
        clamp_double_to_u16((static_cast<double>(rgb[rgbIndex + 0u]) / wbMultiplierR)
                            + static_cast<double>(blackLevel));
    rgba[rgbaIndex + 1u] =
        clamp_double_to_u16((static_cast<double>(rgb[rgbIndex + 1u]) / wbMultiplierG)
                            + static_cast<double>(blackLevel));
    rgba[rgbaIndex + 2u] =
        clamp_double_to_u16((static_cast<double>(rgb[rgbIndex + 2u]) / wbMultiplierB)
                            + static_cast<double>(blackLevel));
    rgba[rgbaIndex + 3u] = 65535u;
}

__device__ __forceinline__ int fc_rggb_absolute(int row, int col)
{
    const int row2 = row & 1;
    const int col2 = col & 1;
    if (row2 == 0 && col2 == 0) return 0;
    if (row2 == 1 && col2 == 1) return 2;
    return 1;
}

__global__ void k_bayer16_to_post_wb_raw_float(const uint16_t * bayer16,
                                               float * rawFloat,
                                               std::size_t pixelCount,
                                               int width,
                                               int blackLevel,
                                               double wbMultiplierR,
                                               double wbMultiplierG,
                                               double wbMultiplierB)
{
    const std::size_t pixel =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (pixel >= pixelCount) return;

    const int row = static_cast<int>(pixel / static_cast<std::size_t>(width));
    const int col = static_cast<int>(pixel - static_cast<std::size_t>(row) * width);
    const int channel = fc_rggb_absolute(row, col);
    const double wb =
        (channel == 0) ? wbMultiplierR : ((channel == 2) ? wbMultiplierB : wbMultiplierG);
    rawFloat[pixel] =
        static_cast<float>((static_cast<double>(bayer16[pixel]) - static_cast<double>(blackLevel)) * wb);
}

void free_live_texture_buffers(igpu_amaze_debayer_backend * backend)
{
    if (!backend) return;
    cudaFree(backend->liveReconBayer16);
    cudaFree(backend->liveRawFloat);
    cudaFree(backend->liveRgb16);
    cudaFree(backend->liveRgba16);
    backend->liveReconBayer16 = nullptr;
    backend->liveRawFloat = nullptr;
    backend->liveRgb16 = nullptr;
    backend->liveRgba16 = nullptr;
    if (backend->liveDeviceBuffersAllocated)
    {
        free_device(&backend->liveDeviceBuffers);
        backend->liveDeviceBuffers = DeviceBuffers();
        backend->liveDeviceBuffersAllocated = false;
    }
    backend->livePixelCount = 0;
    backend->liveWidth = 0;
    backend->liveHeight = 0;
}

void ensure_live_texture_buffers(igpu_amaze_debayer_backend * backend,
                                 int width,
                                 int height)
{
    if (!backend || width <= 0 || height <= 0)
    {
        throw CudaStageProbeError("invalid live texture buffer geometry");
    }

    const std::size_t pixelCount =
        static_cast<std::size_t>(width) * static_cast<std::size_t>(height);
    if (backend->liveReconBayer16
        && backend->liveRawFloat
        && backend->liveRgb16
        && backend->liveRgba16
        && backend->liveDeviceBuffersAllocated
        && backend->livePixelCount == pixelCount
        && backend->liveWidth == width
        && backend->liveHeight == height)
    {
        return;
    }

    free_live_texture_buffers(backend);
    CK(cudaMalloc(&backend->liveReconBayer16, pixelCount * sizeof(uint16_t)));
    CK(cudaMalloc(&backend->liveRawFloat, pixelCount * sizeof(float)));
    CK(cudaMalloc(&backend->liveRgb16, pixelCount * 3u * sizeof(uint16_t)));
    CK(cudaMalloc(&backend->liveRgba16, pixelCount * 4u * sizeof(uint16_t)));
    allocate_device(&backend->liveDeviceBuffers, 1);
    backend->liveDeviceBuffersAllocated = true;
    backend->livePixelCount = pixelCount;
    backend->liveWidth = width;
    backend->liveHeight = height;
}

int copy_gl_r16_texture_to_bayer16(unsigned int glTexture,
                                   uint16_t * dBayer16,
                                   int width,
                                   int height)
{
    if (glTexture == 0 || !dBayer16 || width <= 0 || height <= 0) return -1;

    cudaGraphicsResource * resource = nullptr;
    cudaError_t rc = cudaGraphicsGLRegisterImage(&resource,
                                                 glTexture,
                                                 GL_TEXTURE_2D,
                                                 cudaGraphicsRegisterFlagsReadOnly);
    if (rc != cudaSuccess)
    {
        std::fprintf(stderr,
                     "[igpu_amaze_debayer_cuda] cudaGraphicsGLRegisterImage(GL_R16 source texture) failed: %s\n",
                     cudaGetErrorString(rc));
        return 2;
    }

    rc = cudaGraphicsMapResources(1, &resource, 0);
    if (rc != cudaSuccess)
    {
        std::fprintf(stderr,
                     "[igpu_amaze_debayer_cuda] cudaGraphicsMapResources(GL_R16 source texture) failed: %s\n",
                     cudaGetErrorString(rc));
        cudaGraphicsUnregisterResource(resource);
        return 2;
    }

    cudaArray_t mappedArray = nullptr;
    rc = cudaGraphicsSubResourceGetMappedArray(&mappedArray, resource, 0, 0);
    if (rc == cudaSuccess)
    {
        const std::size_t rowBytes = static_cast<std::size_t>(width) * sizeof(uint16_t);
        rc = cudaMemcpy2DFromArray(dBayer16,
                                   rowBytes,
                                   mappedArray,
                                   0,
                                   0,
                                   rowBytes,
                                   static_cast<std::size_t>(height),
                                   cudaMemcpyDeviceToDevice);
    }

    const cudaError_t unmapRc = cudaGraphicsUnmapResources(1, &resource, 0);
    const cudaError_t unregisterRc = cudaGraphicsUnregisterResource(resource);
    if (rc != cudaSuccess)
    {
        std::fprintf(stderr,
                     "[igpu_amaze_debayer_cuda] GL_R16 source texture device copy failed: %s\n",
                     cudaGetErrorString(rc));
        return 2;
    }
    if (unmapRc != cudaSuccess)
    {
        std::fprintf(stderr,
                     "[igpu_amaze_debayer_cuda] cudaGraphicsUnmapResources(GL_R16 source texture) failed: %s\n",
                     cudaGetErrorString(unmapRc));
        return 2;
    }
    if (unregisterRc != cudaSuccess)
    {
        std::fprintf(stderr,
                     "[igpu_amaze_debayer_cuda] cudaGraphicsUnregisterResource(GL_R16 source texture) failed: %s\n",
                     cudaGetErrorString(unregisterRc));
        return 2;
    }
    return 0;
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
    k_variance_selection_wavefront<<<1, kVarianceWavefrontThreads>>>(d.cfa,
                                                                     d.vcd,
                                                                     d.hcd,
                                                                     d.vcdalt,
                                                                     d.hcdalt,
                                                                     d.cddiffsq,
                                                                     rr1,
                                                                     cc1);
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
    k_nyquist_refine_row_prefix<<<1, kNyquistPrefixThreads>>>(d.nyquist, rr1, cc1);
    CK(cudaGetLastError());
    k_nyquist_area_interpolation<<<grid, block>>>(d.cfa, d.nyquist, d.hvwt, rr1, cc1);
    CK(cudaGetLastError());
    k_green_plane_assembly_row_parallel<<<1, kGreenPlaneThreads>>>(d.cfa,
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
    k_pmwt_refinement_and_rbint_row_parallel<<<1, kPmwtRowThreads>>>(d.cfa,
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

void run_frame_to_device_rgb16(const float * dRaw,
                               uint16_t * dRgb16,
                               DeviceBuffers & d,
                               int width,
                               int height)
{
    for (int top = -16; top < height; top += kTileSize - 32)
    {
        for (int left = -16; left < width; left += kTileSize - 32)
        {
            run_tile(dRaw, dRgb16, d, width, height, top, left);
        }
    }
}

int copy_rgb16_to_gl_rgba16_texture(const uint16_t * dRgb16,
                                    int width,
                                    int height,
                                    unsigned int glTexture)
{
    if (!dRgb16 || width <= 0 || height <= 0 || glTexture == 0) return -1;

    const std::size_t pixelCount =
        static_cast<std::size_t>(width) * static_cast<std::size_t>(height);
    const std::size_t rgbaBytes = pixelCount * 4u * sizeof(uint16_t);
    uint16_t * dRgba16 = nullptr;
    cudaGraphicsResource * resource = nullptr;

    try
    {
        CK(cudaMalloc(&dRgba16, rgbaBytes));
        const int threads = 256;
        const int blocks = static_cast<int>((pixelCount + threads - 1u) / threads);
        k_pack_rgb16_to_rgba16<<<blocks, threads>>>(dRgb16, dRgba16, pixelCount);
        CK(cudaGetLastError());

        cudaError_t rc = cudaGraphicsGLRegisterImage(&resource,
                                                     glTexture,
                                                     GL_TEXTURE_2D,
                                                     cudaGraphicsRegisterFlagsWriteDiscard);
        if (rc != cudaSuccess)
        {
            std::fprintf(stderr,
                         "[igpu_amaze_debayer_cuda] cudaGraphicsGLRegisterImage(GL_RGBA16 texture) failed: %s\n",
                         cudaGetErrorString(rc));
            cudaFree(dRgba16);
            return 2;
        }

        rc = cudaGraphicsMapResources(1, &resource, 0);
        if (rc != cudaSuccess)
        {
            std::fprintf(stderr,
                         "[igpu_amaze_debayer_cuda] cudaGraphicsMapResources(GL_RGBA16 texture) failed: %s\n",
                         cudaGetErrorString(rc));
            cudaGraphicsUnregisterResource(resource);
            cudaFree(dRgba16);
            return 2;
        }

        cudaArray_t mappedArray = nullptr;
        rc = cudaGraphicsSubResourceGetMappedArray(&mappedArray, resource, 0, 0);
        if (rc == cudaSuccess)
        {
            const std::size_t rowBytes = static_cast<std::size_t>(width) * 4u * sizeof(uint16_t);
            rc = cudaMemcpy2DToArray(mappedArray,
                                     0,
                                     0,
                                     dRgba16,
                                     rowBytes,
                                     rowBytes,
                                     static_cast<std::size_t>(height),
                                     cudaMemcpyDeviceToDevice);
        }

        const cudaError_t unmapRc = cudaGraphicsUnmapResources(1, &resource, 0);
        const cudaError_t unregisterRc = cudaGraphicsUnregisterResource(resource);
        cudaFree(dRgba16);
        if (rc != cudaSuccess)
        {
            std::fprintf(stderr,
                         "[igpu_amaze_debayer_cuda] device-to-GL texture copy failed: %s\n",
                         cudaGetErrorString(rc));
            return 2;
        }
        if (unmapRc != cudaSuccess)
        {
            std::fprintf(stderr,
                         "[igpu_amaze_debayer_cuda] cudaGraphicsUnmapResources(GL_RGBA16 texture) failed: %s\n",
                         cudaGetErrorString(unmapRc));
            return 2;
        }
        if (unregisterRc != cudaSuccess)
        {
            std::fprintf(stderr,
                         "[igpu_amaze_debayer_cuda] cudaGraphicsUnregisterResource(GL_RGBA16 texture) failed: %s\n",
                         cudaGetErrorString(unregisterRc));
            return 2;
        }
    }
    catch (const CudaStageProbeError & error)
    {
        std::fprintf(stderr, "[igpu_amaze_debayer_cuda] %s\n", error.what());
        if (resource) cudaGraphicsUnregisterResource(resource);
        cudaFree(dRgba16);
        return -2;
    }

    return 0;
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

int copy_rgb16_to_gl_rgba16_texture_post_wb_undo(const uint16_t * dRgb16,
                                                 int width,
                                                 int height,
                                                 unsigned int glTexture,
                                                 int blackLevel,
                                                 double wbMultiplierR,
                                                 double wbMultiplierG,
                                                 double wbMultiplierB,
                                                 uint16_t * dRgba16Scratch = nullptr)
{
    if (!dRgb16 || width <= 0 || height <= 0 || glTexture == 0) return -1;
    if (wbMultiplierR <= 0.0 || wbMultiplierG <= 0.0 || wbMultiplierB <= 0.0) return -1;

    if (blackLevel < 1000) blackLevel = -1000;

    const std::size_t pixelCount =
        static_cast<std::size_t>(width) * static_cast<std::size_t>(height);
    const std::size_t rgbaBytes = pixelCount * 4u * sizeof(uint16_t);
    uint16_t * dRgba16 = dRgba16Scratch;
    const bool ownsScratch = dRgba16 == nullptr;
    cudaGraphicsResource * resource = nullptr;
    auto releaseScratch = [&]()
    {
        if (ownsScratch && dRgba16)
        {
            cudaFree(dRgba16);
            dRgba16 = nullptr;
        }
    };

    try
    {
        if (ownsScratch) CK(cudaMalloc(&dRgba16, rgbaBytes));
        const int threads = 256;
        const int blocks = static_cast<int>((pixelCount + threads - 1u) / threads);
        k_pack_rgb16_to_rgba16_post_wb_undo<<<blocks, threads>>>(dRgb16,
                                                                 dRgba16,
                                                                 pixelCount,
                                                                 blackLevel,
                                                                 wbMultiplierR,
                                                                 wbMultiplierG,
                                                                 wbMultiplierB);
        CK(cudaGetLastError());

        cudaError_t rc = cudaGraphicsGLRegisterImage(&resource,
                                                     glTexture,
                                                     GL_TEXTURE_2D,
                                                     cudaGraphicsRegisterFlagsWriteDiscard);
        if (rc != cudaSuccess)
        {
            std::fprintf(stderr,
                         "[igpu_amaze_debayer_cuda] cudaGraphicsGLRegisterImage(GL_RGBA16 post-WB texture) failed: %s\n",
                         cudaGetErrorString(rc));
            releaseScratch();
            return 2;
        }

        rc = cudaGraphicsMapResources(1, &resource, 0);
        if (rc != cudaSuccess)
        {
            std::fprintf(stderr,
                         "[igpu_amaze_debayer_cuda] cudaGraphicsMapResources(GL_RGBA16 post-WB texture) failed: %s\n",
                         cudaGetErrorString(rc));
            cudaGraphicsUnregisterResource(resource);
            releaseScratch();
            return 2;
        }

        cudaArray_t mappedArray = nullptr;
        rc = cudaGraphicsSubResourceGetMappedArray(&mappedArray, resource, 0, 0);
        if (rc == cudaSuccess)
        {
            const std::size_t rowBytes = static_cast<std::size_t>(width) * 4u * sizeof(uint16_t);
            rc = cudaMemcpy2DToArray(mappedArray,
                                     0,
                                     0,
                                     dRgba16,
                                     rowBytes,
                                     rowBytes,
                                     static_cast<std::size_t>(height),
                                     cudaMemcpyDeviceToDevice);
        }

        const cudaError_t unmapRc = cudaGraphicsUnmapResources(1, &resource, 0);
        const cudaError_t unregisterRc = cudaGraphicsUnregisterResource(resource);
        releaseScratch();
        if (rc != cudaSuccess)
        {
            std::fprintf(stderr,
                         "[igpu_amaze_debayer_cuda] post-WB device-to-GL texture copy failed: %s\n",
                         cudaGetErrorString(rc));
            return 2;
        }
        if (unmapRc != cudaSuccess)
        {
            std::fprintf(stderr,
                         "[igpu_amaze_debayer_cuda] cudaGraphicsUnmapResources(GL_RGBA16 post-WB texture) failed: %s\n",
                         cudaGetErrorString(unmapRc));
            return 2;
        }
        if (unregisterRc != cudaSuccess)
        {
            std::fprintf(stderr,
                         "[igpu_amaze_debayer_cuda] cudaGraphicsUnregisterResource(GL_RGBA16 post-WB texture) failed: %s\n",
                         cudaGetErrorString(unregisterRc));
            return 2;
        }
        return 0;
    }
    catch (const CudaStageProbeError & error)
    {
        std::fprintf(stderr, "[igpu_amaze_debayer_cuda] %s\n", error.what());
        if (resource) cudaGraphicsUnregisterResource(resource);
        releaseScratch();
        return 2;
    }
}

extern "C" void igpu_amaze_debayer_destroy(igpu_amaze_debayer_backend * backend)
{
    free_live_texture_buffers(backend);
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
        run_frame_to_device_rgb16(dRaw, dRgb16, d, width, height);
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

extern "C" int igpu_amaze_debayer_run_gl_texture(igpu_amaze_debayer_backend * backend,
                                                 const float * in_raw_float,
                                                 unsigned int gl_texture,
                                                 int width,
                                                 int height)
{
    if (!backend || !in_raw_float || gl_texture == 0 || width <= 32 || height <= 32)
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
        run_frame_to_device_rgb16(dRaw, dRgb16, d, width, height);
        CK(cudaDeviceSynchronize());
        backend->lastTiming.kernel_ms = now_ms() - kernelStart;

        const double handoffStart = now_ms();
        const int rc = copy_rgb16_to_gl_rgba16_texture(dRgb16, width, height, gl_texture);
        CK(cudaDeviceSynchronize());
        backend->lastTiming.download_ms = now_ms() - handoffStart;
        backend->lastTiming.total_ms = now_ms() - totalStart;

        free_device(&d);
        cudaFree(dRaw);
        cudaFree(dRgb16);
        return rc;
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

extern "C" int igpu_amaze_debayer_run_post_wb_gl_texture(igpu_amaze_debayer_backend * backend,
                                                         const float * in_raw_float,
                                                         unsigned int gl_texture,
                                                         int width,
                                                         int height,
                                                         int black_level,
                                                         double wb_multiplier_r,
                                                         double wb_multiplier_g,
                                                         double wb_multiplier_b)
{
    if (!backend || !in_raw_float || gl_texture == 0 || width <= 32 || height <= 32
        || wb_multiplier_r <= 0.0 || wb_multiplier_g <= 0.0 || wb_multiplier_b <= 0.0)
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
        run_frame_to_device_rgb16(dRaw, dRgb16, d, width, height);
        CK(cudaDeviceSynchronize());
        backend->lastTiming.kernel_ms = now_ms() - kernelStart;

        const double handoffStart = now_ms();
        const int rc =
            copy_rgb16_to_gl_rgba16_texture_post_wb_undo(dRgb16,
                                                         width,
                                                         height,
                                                         gl_texture,
                                                         black_level,
                                                         wb_multiplier_r,
                                                         wb_multiplier_g,
                                                         wb_multiplier_b);
        CK(cudaDeviceSynchronize());
        backend->lastTiming.download_ms = now_ms() - handoffStart;
        backend->lastTiming.total_ms = now_ms() - totalStart;

        free_device(&d);
        cudaFree(dRaw);
        cudaFree(dRgb16);
        return rc;
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

extern "C" int igpu_amaze_debayer_run_post_wb_gl_texture_from_r16_gl_texture(
    igpu_amaze_debayer_backend * backend,
    unsigned int in_r16_gl_texture,
    unsigned int out_rgba16_gl_texture,
    int width,
    int height,
    int black_level,
    double wb_multiplier_r,
    double wb_multiplier_g,
    double wb_multiplier_b)
{
    if (!backend || in_r16_gl_texture == 0 || out_rgba16_gl_texture == 0
        || width <= 32 || height <= 32
        || wb_multiplier_r <= 0.0 || wb_multiplier_g <= 0.0 || wb_multiplier_b <= 0.0)
    {
        return -1;
    }

    if (black_level < 1000) black_level = -1000;

    const std::size_t pixelCount =
        static_cast<std::size_t>(width) * static_cast<std::size_t>(height);
    const std::size_t rgbBytes = pixelCount * 3u * sizeof(uint16_t);
    std::memset(&backend->lastTiming, 0, sizeof(backend->lastTiming));

    const double totalStart = now_ms();
    try
    {
        ensure_live_texture_buffers(backend, width, height);
        CK(cudaMemset(backend->liveRgb16, 0, rgbBytes));

        const double uploadStart = now_ms();
        const int copyRc = copy_gl_r16_texture_to_bayer16(in_r16_gl_texture,
                                                          backend->liveReconBayer16,
                                                          width,
                                                          height);
        if (copyRc != 0) return copyRc;
        const int threads = 256;
        const int blocks = static_cast<int>((pixelCount + threads - 1u) / threads);
        k_bayer16_to_post_wb_raw_float<<<blocks, threads>>>(backend->liveReconBayer16,
                                                            backend->liveRawFloat,
                                                            pixelCount,
                                                            width,
                                                            black_level,
                                                            wb_multiplier_r,
                                                            wb_multiplier_g,
                                                            wb_multiplier_b);
        CK(cudaGetLastError());
        CK(cudaDeviceSynchronize());
        backend->lastTiming.upload_ms = now_ms() - uploadStart;

        const double kernelStart = now_ms();
        run_frame_to_device_rgb16(backend->liveRawFloat,
                                  backend->liveRgb16,
                                  backend->liveDeviceBuffers,
                                  width,
                                  height);
        CK(cudaDeviceSynchronize());
        backend->lastTiming.kernel_ms = now_ms() - kernelStart;

        const double handoffStart = now_ms();
        const int rc =
            copy_rgb16_to_gl_rgba16_texture_post_wb_undo(backend->liveRgb16,
                                                         width,
                                                         height,
                                                         out_rgba16_gl_texture,
                                                         black_level,
                                                         wb_multiplier_r,
                                                         wb_multiplier_g,
                                                         wb_multiplier_b,
                                                         backend->liveRgba16);
        CK(cudaDeviceSynchronize());
        backend->lastTiming.download_ms = now_ms() - handoffStart;
        backend->lastTiming.total_ms = now_ms() - totalStart;
        return rc;
    }
    catch (const CudaStageProbeError & error)
    {
        std::fprintf(stderr, "[igpu_amaze_debayer_cuda] %s\n", error.what());
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
