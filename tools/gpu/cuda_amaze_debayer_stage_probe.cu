/* cuda_amaze_debayer_stage_probe.cu - first CUDA stage probe for the generic
 * AMaZE debayer path (src/debayer/amaze_demosaic.c).
 *
 * This is deliberately NOT the production GPU AMaZE backend and does not make
 * GPU AMaZE available in MLVApp. It ports the first deterministic AMaZE tile
 * stages to CUDA and compares them against a host oracle:
 *
 *   - 16-pixel reflected tile halo load + CFA normalization
 *   - green-site seed plane
 *   - horizontal/vertical gradient weights
 *   - diagonal precursor weights used by later R/B interpolation
 *   - first green-direction interpolation color-difference planes
 *   - scalar-order variance selection and saturation-bound refinement
 *   - adaptive horizontal/vertical green interpolation weights (hvwt)
 *
 * The purpose is to land a small, reviewable, real-device AMaZE slice before
 * the full demosaic port. Full P-pre remains blocked until the final GPU AMaZE
 * RGB16 output compares against CPU AMaZE on real hardware.
 *
 * Build/run on Ultra-Magnus:
 *   pwsh -File amaze-debayer-stage-build-run.ps1
 */

#include <cuda_runtime.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

namespace
{
constexpr int kTileSize = 160;
constexpr int kTileHalf = 80;
constexpr int kTileSamples = kTileSize * kTileSize;
constexpr int kHalfTileSamples = kTileSize * kTileHalf;
constexpr float kEps = 1.0e-5f;
constexpr float kAdaptiveRatioThreshold = 0.75f;
constexpr float kClipPoint = 1.0f;
constexpr float kClipPoint8 = 0.8f;
constexpr float kTolerance = 1.0e-6f;

struct CaseSpec
{
    const char * name;
    int width;
    int height;
    int top;
    int left;
};

struct StageBuffers
{
    std::vector<float> cfa;
    std::vector<float> rgbgreen;
    std::vector<float> dirwts0;
    std::vector<float> dirwts1;
    std::vector<float> delhvsqsum;
    std::vector<float> delp;
    std::vector<float> delm;
    std::vector<float> dgrbsq1p;
    std::vector<float> dgrbsq1m;
    std::vector<float> vcd;
    std::vector<float> hcd;
    std::vector<float> vcdalt;
    std::vector<float> hcdalt;
    std::vector<float> dgintv;
    std::vector<float> dginth;
    std::vector<float> cddiffsq;
    std::vector<float> hvwt;

    StageBuffers()
        : cfa(kTileSamples, 0.0f)
        , rgbgreen(kTileSamples, 0.0f)
        , dirwts0(kTileSamples, 0.0f)
        , dirwts1(kTileSamples, 0.0f)
        , delhvsqsum(kTileSamples, 0.0f)
        , delp(kHalfTileSamples, 0.0f)
        , delm(kHalfTileSamples, 0.0f)
        , dgrbsq1p(kHalfTileSamples, 0.0f)
        , dgrbsq1m(kHalfTileSamples, 0.0f)
        , vcd(kTileSamples, 0.0f)
        , hcd(kTileSamples, 0.0f)
        , vcdalt(kTileSamples, 0.0f)
        , hcdalt(kTileSamples, 0.0f)
        , dgintv(kTileSamples, 0.0f)
        , dginth(kTileSamples, 0.0f)
        , cddiffsq(kTileSamples, 0.0f)
        , hvwt(kHalfTileSamples, 0.0f)
    {
    }
};

struct DeviceBuffers
{
    uint16_t * raw = nullptr;
    float * cfa = nullptr;
    float * rgbgreen = nullptr;
    float * dirwts0 = nullptr;
    float * dirwts1 = nullptr;
    float * delhvsqsum = nullptr;
    float * delp = nullptr;
    float * delm = nullptr;
    float * dgrbsq1p = nullptr;
    float * dgrbsq1m = nullptr;
    float * vcd = nullptr;
    float * hcd = nullptr;
    float * vcdalt = nullptr;
    float * hcdalt = nullptr;
    float * dgintv = nullptr;
    float * dginth = nullptr;
    float * cddiffsq = nullptr;
    float * hvwt = nullptr;
};

struct CompareStats
{
    std::size_t mismatches = 0;
    std::size_t bitMismatches = 0;
    float maxAbs = 0.0f;
    std::size_t maxIndex = 0;
};

__host__ __device__ int imin_i(int a, int b)
{
    return a < b ? a : b;
}

__host__ __device__ int reflect_index(int value, int limit)
{
    if (value < 0) return -value;
    if (value >= limit) return 2 * limit - value - 2;
    return value;
}

__host__ __device__ int fc_rggb(int row, int col)
{
    const int row2 = row & 1;
    const int col2 = col & 1;
    if (row2 == 0 && col2 == 0) return 0;
    if (row2 == 1 && col2 == 1) return 2;
    return 1;
}

__host__ __device__ float sqr_f(float value)
{
    return value * value;
}

__host__ __device__ float min_f(float a, float b)
{
    return a < b ? a : b;
}

__host__ __device__ float max_f(float a, float b)
{
    return a > b ? a : b;
}

__host__ __device__ float coerce_f(float x, float lo, float hi)
{
    return max_f(min_f(x, hi), lo);
}

__host__ __device__ float ulim_f(float a, float b, float c)
{
    return (b < c) ? coerce_f(a, b, c) : coerce_f(a, c, b);
}

__host__ __device__ float xdiv2f_probe(float value)
{
    union Bits
    {
        float f;
        uint32_t u;
    };
    Bits bits = {value};
    if ((bits.u & 0x7fffffffu) != 0)
    {
        bits.u -= 1u << 23;
    }
    return bits.f;
}

std::vector<uint16_t> make_raw_pattern(int width, int height, uint32_t seed)
{
    std::vector<uint16_t> raw(static_cast<std::size_t>(width) *
                              static_cast<std::size_t>(height));
    for (int y = 0; y < height; ++y)
    {
        for (int x = 0; x < width; ++x)
        {
            uint32_t v = seed;
            v ^= static_cast<uint32_t>(x + 1) * 0x45d9f3bu;
            v ^= static_cast<uint32_t>(y + 7) * 0x119de1f3u;
            v ^= (v >> 16);
            v *= 0x7feb352du;
            v ^= (v >> 15);
            raw[static_cast<std::size_t>(y) * width + x] =
                static_cast<uint16_t>(v & 0xffffu);
        }
    }
    return raw;
}

void cpu_stage_probe(const std::vector<uint16_t> & raw,
                     const CaseSpec & spec,
                     StageBuffers * out)
{
    const int bottom = imin_i(spec.top + kTileSize, spec.height + 16);
    const int right = imin_i(spec.left + kTileSize, spec.width + 16);
    const int rr1 = bottom - spec.top;
    const int cc1 = right - spec.left;
    const int v1 = kTileSize;
    const int v2 = 2 * kTileSize;
    const int p1 = -kTileSize + 1;
    const int m1 = kTileSize + 1;

    for (int rr = 0; rr < rr1; ++rr)
    {
        for (int cc = 0; cc < cc1; ++cc)
        {
            const int row = reflect_index(spec.top + rr, spec.height);
            const int col = reflect_index(spec.left + cc, spec.width);
            const int idx = rr * kTileSize + cc;
            const float normalized =
                static_cast<float>(raw[static_cast<std::size_t>(row) * spec.width + col]) /
                65535.0f;
            out->cfa[idx] = normalized;
            out->rgbgreen[idx] = (fc_rggb(rr, cc) == 1) ? normalized : 0.0f;
        }
    }

    for (int rr = 2; rr < rr1 - 2; ++rr)
    {
        for (int cc = 2; cc < cc1 - 2; ++cc)
        {
            const int idx = rr * kTileSize + cc;
            const float delh = std::fabs(out->cfa[idx + 1] - out->cfa[idx - 1]);
            const float delv = std::fabs(out->cfa[idx + v1] - out->cfa[idx - v1]);
            out->dirwts0[idx] =
                kEps + std::fabs(out->cfa[idx + v2] - out->cfa[idx]) +
                std::fabs(out->cfa[idx] - out->cfa[idx - v2]) + delv;
            out->dirwts1[idx] =
                kEps + std::fabs(out->cfa[idx + 2] - out->cfa[idx]) +
                std::fabs(out->cfa[idx] - out->cfa[idx - 2]) + delh;
            out->delhvsqsum[idx] = sqr_f(delh) + sqr_f(delv);
        }
    }

    for (int rr = 6; rr < rr1 - 6; ++rr)
    {
        for (int cc = 6, idx = rr * kTileSize + cc;
             cc < cc1 - 6;
             cc += 2, idx += 2)
        {
            const int halfIdx = idx >> 1;
            if ((fc_rggb(rr, 2) & 1) == 0)
            {
                out->delp[halfIdx] = std::fabs(out->cfa[idx + p1] - out->cfa[idx - p1]);
                out->delm[halfIdx] = std::fabs(out->cfa[idx + m1] - out->cfa[idx - m1]);
                out->dgrbsq1p[halfIdx] =
                    sqr_f(out->cfa[idx + 1] - out->cfa[idx + 1 - p1]) +
                    sqr_f(out->cfa[idx + 1] - out->cfa[idx + 1 + p1]);
                out->dgrbsq1m[halfIdx] =
                    sqr_f(out->cfa[idx + 1] - out->cfa[idx + 1 - m1]) +
                    sqr_f(out->cfa[idx + 1] - out->cfa[idx + 1 + m1]);
            }
            else
            {
                out->dgrbsq1p[halfIdx] =
                    sqr_f(out->cfa[idx] - out->cfa[idx - p1]) +
                    sqr_f(out->cfa[idx] - out->cfa[idx + p1]);
                out->dgrbsq1m[halfIdx] =
                    sqr_f(out->cfa[idx] - out->cfa[idx - m1]) +
                    sqr_f(out->cfa[idx] - out->cfa[idx + m1]);
                out->delp[halfIdx] =
                    std::fabs(out->cfa[idx + 1 + p1] - out->cfa[idx + 1 - p1]);
                out->delm[halfIdx] =
                    std::fabs(out->cfa[idx + 1 + m1] - out->cfa[idx + 1 - m1]);
            }
        }
    }

    for (int rr = 4; rr < rr1 - 4; ++rr)
    {
        for (int cc = 4; cc < cc1 - 4; ++cc)
        {
            const int idx = rr * kTileSize + cc;
            const float cru =
                out->cfa[idx - v1] * (out->dirwts0[idx - v2] + out->dirwts0[idx]) /
                (out->dirwts0[idx - v2] * (kEps + out->cfa[idx]) +
                 out->dirwts0[idx] * (kEps + out->cfa[idx - v2]));
            const float crd =
                out->cfa[idx + v1] * (out->dirwts0[idx + v2] + out->dirwts0[idx]) /
                (out->dirwts0[idx + v2] * (kEps + out->cfa[idx]) +
                 out->dirwts0[idx] * (kEps + out->cfa[idx + v2]));
            const float crl =
                out->cfa[idx - 1] * (out->dirwts1[idx - 2] + out->dirwts1[idx]) /
                (out->dirwts1[idx - 2] * (kEps + out->cfa[idx]) +
                 out->dirwts1[idx] * (kEps + out->cfa[idx - 2]));
            const float crr =
                out->cfa[idx + 1] * (out->dirwts1[idx + 2] + out->dirwts1[idx]) /
                (out->dirwts1[idx + 2] * (kEps + out->cfa[idx]) +
                 out->dirwts1[idx] * (kEps + out->cfa[idx + 2]));

            const float guha = out->cfa[idx - v1] + xdiv2f_probe(out->cfa[idx] - out->cfa[idx - v2]);
            const float gdha = out->cfa[idx + v1] + xdiv2f_probe(out->cfa[idx] - out->cfa[idx + v2]);
            const float glha = out->cfa[idx - 1] + xdiv2f_probe(out->cfa[idx] - out->cfa[idx - 2]);
            const float grha = out->cfa[idx + 1] + xdiv2f_probe(out->cfa[idx] - out->cfa[idx + 2]);

            float guar = (std::fabs(1.0f - cru) < kAdaptiveRatioThreshold) ? out->cfa[idx] * cru : guha;
            float gdar = (std::fabs(1.0f - crd) < kAdaptiveRatioThreshold) ? out->cfa[idx] * crd : gdha;
            float glar = (std::fabs(1.0f - crl) < kAdaptiveRatioThreshold) ? out->cfa[idx] * crl : glha;
            float grar = (std::fabs(1.0f - crr) < kAdaptiveRatioThreshold) ? out->cfa[idx] * crr : grha;

            const float hwt = out->dirwts1[idx - 1] / (out->dirwts1[idx - 1] + out->dirwts1[idx + 1]);
            const float vwt = out->dirwts0[idx - v1] / (out->dirwts0[idx + v1] + out->dirwts0[idx - v1]);
            const float gintvha = vwt * gdha + (1.0f - vwt) * guha;
            const float ginthha = hwt * grha + (1.0f - hwt) * glha;
            const float vinterp = vwt * gdar + (1.0f - vwt) * guar;
            const float hinterp = hwt * grar + (1.0f - hwt) * glar;

            if ((fc_rggb(rr, cc) & 1) != 0)
            {
                out->vcd[idx] = out->cfa[idx] - vinterp;
                out->hcd[idx] = out->cfa[idx] - hinterp;
                out->vcdalt[idx] = out->cfa[idx] - gintvha;
                out->hcdalt[idx] = out->cfa[idx] - ginthha;
            }
            else
            {
                out->vcd[idx] = vinterp - out->cfa[idx];
                out->hcd[idx] = hinterp - out->cfa[idx];
                out->vcdalt[idx] = gintvha - out->cfa[idx];
                out->hcdalt[idx] = ginthha - out->cfa[idx];
            }

            if (out->cfa[idx] > kClipPoint8 || gintvha > kClipPoint8 || ginthha > kClipPoint8)
            {
                guar = guha;
                gdar = gdha;
                glar = glha;
                grar = grha;
                out->vcd[idx] = out->vcdalt[idx];
                out->hcd[idx] = out->hcdalt[idx];
            }

            out->dgintv[idx] = std::min(sqr_f(guha - gdha), sqr_f(guar - gdar));
            out->dginth[idx] = std::min(sqr_f(glha - grha), sqr_f(glar - grar));
        }
    }

    for (int rr = 4; rr < rr1 - 4; ++rr)
    {
        for (int cc = 4; cc < cc1 - 4; ++cc)
        {
            const int idx = rr * kTileSize + cc;
            const float hcdvar =
                3.0f * (sqr_f(out->hcd[idx - 2]) +
                        sqr_f(out->hcd[idx]) +
                        sqr_f(out->hcd[idx + 2])) -
                sqr_f(out->hcd[idx - 2] + out->hcd[idx] + out->hcd[idx + 2]);
            const float hcdaltvar =
                3.0f * (sqr_f(out->hcdalt[idx - 2]) +
                        sqr_f(out->hcdalt[idx]) +
                        sqr_f(out->hcdalt[idx + 2])) -
                sqr_f(out->hcdalt[idx - 2] + out->hcdalt[idx] + out->hcdalt[idx + 2]);
            const float vcdvar =
                3.0f * (sqr_f(out->vcd[idx - v2]) +
                        sqr_f(out->vcd[idx]) +
                        sqr_f(out->vcd[idx + v2])) -
                sqr_f(out->vcd[idx - v2] + out->vcd[idx] + out->vcd[idx + v2]);
            const float vcdaltvar =
                3.0f * (sqr_f(out->vcdalt[idx - v2]) +
                        sqr_f(out->vcdalt[idx]) +
                        sqr_f(out->vcdalt[idx + v2])) -
                sqr_f(out->vcdalt[idx - v2] + out->vcdalt[idx] + out->vcdalt[idx + v2]);

            if (hcdaltvar < hcdvar) out->hcd[idx] = out->hcdalt[idx];
            if (vcdaltvar < vcdvar) out->vcd[idx] = out->vcdalt[idx];

            if ((fc_rggb(rr, cc) & 1) != 0)
            {
                const float ginth = -out->hcd[idx] + out->cfa[idx];
                const float gintv = -out->vcd[idx] + out->cfa[idx];
                if (out->hcd[idx] > 0.0f)
                {
                    const float bounded = -ulim_f(ginth, out->cfa[idx - 1], out->cfa[idx + 1]) + out->cfa[idx];
                    if (3.0f * out->hcd[idx] > (ginth + out->cfa[idx]))
                    {
                        out->hcd[idx] = bounded;
                    }
                    else
                    {
                        const float hwt = 1.0f - 3.0f * out->hcd[idx] / (kEps + ginth + out->cfa[idx]);
                        out->hcd[idx] = hwt * out->hcd[idx] + (1.0f - hwt) * bounded;
                    }
                }
                if (out->vcd[idx] > 0.0f)
                {
                    const float bounded = -ulim_f(gintv, out->cfa[idx - v1], out->cfa[idx + v1]) + out->cfa[idx];
                    if (3.0f * out->vcd[idx] > (gintv + out->cfa[idx]))
                    {
                        out->vcd[idx] = bounded;
                    }
                    else
                    {
                        const float vwt = 1.0f - 3.0f * out->vcd[idx] / (kEps + gintv + out->cfa[idx]);
                        out->vcd[idx] = vwt * out->vcd[idx] + (1.0f - vwt) * bounded;
                    }
                }
                if (ginth > kClipPoint)
                {
                    out->hcd[idx] = -ulim_f(ginth, out->cfa[idx - 1], out->cfa[idx + 1]) + out->cfa[idx];
                }
                if (gintv > kClipPoint)
                {
                    out->vcd[idx] = -ulim_f(gintv, out->cfa[idx - v1], out->cfa[idx + v1]) + out->cfa[idx];
                }
            }
            else
            {
                const float ginth = out->hcd[idx] + out->cfa[idx];
                const float gintv = out->vcd[idx] + out->cfa[idx];
                if (out->hcd[idx] < 0.0f)
                {
                    const float bounded = ulim_f(ginth, out->cfa[idx - 1], out->cfa[idx + 1]) - out->cfa[idx];
                    if (3.0f * out->hcd[idx] < -(ginth + out->cfa[idx]))
                    {
                        out->hcd[idx] = bounded;
                    }
                    else
                    {
                        const float hwt = 1.0f + 3.0f * out->hcd[idx] / (kEps + ginth + out->cfa[idx]);
                        out->hcd[idx] = hwt * out->hcd[idx] + (1.0f - hwt) * bounded;
                    }
                }
                if (out->vcd[idx] < 0.0f)
                {
                    const float bounded = ulim_f(gintv, out->cfa[idx - v1], out->cfa[idx + v1]) - out->cfa[idx];
                    if (3.0f * out->vcd[idx] < -(gintv + out->cfa[idx]))
                    {
                        out->vcd[idx] = bounded;
                    }
                    else
                    {
                        const float vwt = 1.0f + 3.0f * out->vcd[idx] / (kEps + gintv + out->cfa[idx]);
                        out->vcd[idx] = vwt * out->vcd[idx] + (1.0f - vwt) * bounded;
                    }
                }
                if (ginth > kClipPoint)
                {
                    out->hcd[idx] = ulim_f(ginth, out->cfa[idx - 1], out->cfa[idx + 1]) - out->cfa[idx];
                }
                if (gintv > kClipPoint)
                {
                    out->vcd[idx] = ulim_f(gintv, out->cfa[idx - v1], out->cfa[idx + v1]) - out->cfa[idx];
                }
                out->cddiffsq[idx] = sqr_f(out->vcd[idx] - out->hcd[idx]);
            }
        }
    }

    const int v3 = 3 * kTileSize;
    const float epssq = kEps * kEps;
    for (int rr = 6; rr < rr1 - 6; ++rr)
    {
        for (int cc = 6 + (fc_rggb(rr, 2) & 1), idx = rr * kTileSize + cc;
             cc < cc1 - 6;
             cc += 2, idx += 2)
        {
            const float uave =
                out->vcd[idx] + out->vcd[idx - v1] + out->vcd[idx - v2] + out->vcd[idx - v3];
            const float dave =
                out->vcd[idx] + out->vcd[idx + v1] + out->vcd[idx + v2] + out->vcd[idx + v3];
            const float lave =
                out->hcd[idx] + out->hcd[idx - 1] + out->hcd[idx - 2] + out->hcd[idx - 3];
            const float rave =
                out->hcd[idx] + out->hcd[idx + 1] + out->hcd[idx + 2] + out->hcd[idx + 3];

            const float dgrbvvaru =
                sqr_f(out->vcd[idx] - uave) +
                sqr_f(out->vcd[idx - v1] - uave) +
                sqr_f(out->vcd[idx - v2] - uave) +
                sqr_f(out->vcd[idx - v3] - uave);
            const float dgrbvvard =
                sqr_f(out->vcd[idx] - dave) +
                sqr_f(out->vcd[idx + v1] - dave) +
                sqr_f(out->vcd[idx + v2] - dave) +
                sqr_f(out->vcd[idx + v3] - dave);
            const float dgrbhvarl =
                sqr_f(out->hcd[idx] - lave) +
                sqr_f(out->hcd[idx - 1] - lave) +
                sqr_f(out->hcd[idx - 2] - lave) +
                sqr_f(out->hcd[idx - 3] - lave);
            const float dgrbhvarr =
                sqr_f(out->hcd[idx] - rave) +
                sqr_f(out->hcd[idx + 1] - rave) +
                sqr_f(out->hcd[idx + 2] - rave) +
                sqr_f(out->hcd[idx + 3] - rave);

            const float hwt = out->dirwts1[idx - 1] /
                              (out->dirwts1[idx - 1] + out->dirwts1[idx + 1]);
            const float vwt = out->dirwts0[idx - v1] /
                              (out->dirwts0[idx + v1] + out->dirwts0[idx - v1]);

            const float vcdvar = epssq + vwt * dgrbvvard + (1.0f - vwt) * dgrbvvaru;
            const float hcdvar = epssq + hwt * dgrbhvarr + (1.0f - hwt) * dgrbhvarl;

            const float dgrbvvaru1 =
                out->dgintv[idx] + out->dgintv[idx - v1] + out->dgintv[idx - v2];
            const float dgrbvvard1 =
                out->dgintv[idx] + out->dgintv[idx + v1] + out->dgintv[idx + v2];
            const float dgrbhvarl1 =
                out->dginth[idx] + out->dginth[idx - 1] + out->dginth[idx - 2];
            const float dgrbhvarr1 =
                out->dginth[idx] + out->dginth[idx + 1] + out->dginth[idx + 2];

            const float vcdvar1 = epssq + vwt * dgrbvvard1 + (1.0f - vwt) * dgrbvvaru1;
            const float hcdvar1 = epssq + hwt * dgrbhvarr1 + (1.0f - hwt) * dgrbhvarl1;

            const float varwt = hcdvar / (vcdvar + hcdvar);
            const float diffwt = hcdvar1 / (vcdvar1 + hcdvar1);
            if ((0.5f - varwt) * (0.5f - diffwt) > 0.0f &&
                std::fabs(0.5f - diffwt) < std::fabs(0.5f - varwt))
            {
                out->hvwt[idx >> 1] = varwt;
            }
            else
            {
                out->hvwt[idx >> 1] = diffwt;
            }
        }
    }
}

__global__ void k_tile_load(const uint16_t * raw,
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
        static_cast<float>(raw[static_cast<std::size_t>(row) * width + col]) /
        65535.0f;
    cfa[idx] = normalized;
    rgbgreen[idx] = (fc_rggb(rr, cc) == 1) ? normalized : 0.0f;
}

__global__ void k_gradients(const float * cfa,
                            float * dirwts0,
                            float * dirwts1,
                            float * delhvsqsum,
                            int rr1,
                            int cc1)
{
    const int cc = blockIdx.x * blockDim.x + threadIdx.x;
    const int rr = blockIdx.y * blockDim.y + threadIdx.y;
    if (rr < 2 || rr >= rr1 - 2 || cc < 2 || cc >= cc1 - 2) return;

    const int v1 = kTileSize;
    const int v2 = 2 * kTileSize;
    const int idx = rr * kTileSize + cc;
    const float delh = fabsf(cfa[idx + 1] - cfa[idx - 1]);
    const float delv = fabsf(cfa[idx + v1] - cfa[idx - v1]);
    dirwts0[idx] =
        kEps + fabsf(cfa[idx + v2] - cfa[idx]) +
        fabsf(cfa[idx] - cfa[idx - v2]) + delv;
    dirwts1[idx] =
        kEps + fabsf(cfa[idx + 2] - cfa[idx]) +
        fabsf(cfa[idx] - cfa[idx - 2]) + delh;
    delhvsqsum[idx] = sqr_f(delh) + sqr_f(delv);
}

__global__ void k_diagonal_precursors(const float * cfa,
                                      float * delp,
                                      float * delm,
                                      float * dgrbsq1p,
                                      float * dgrbsq1m,
                                      int rr1,
                                      int cc1)
{
    const int cc = blockIdx.x * blockDim.x + threadIdx.x;
    const int rr = blockIdx.y * blockDim.y + threadIdx.y;
    if (rr < 6 || rr >= rr1 - 6 || cc < 6 || cc >= cc1 - 6) return;
    if (((cc - 6) & 1) != 0) return;

    const int p1 = -kTileSize + 1;
    const int m1 = kTileSize + 1;
    const int idx = rr * kTileSize + cc;
    const int halfIdx = idx >> 1;
    if ((fc_rggb(rr, 2) & 1) == 0)
    {
        delp[halfIdx] = fabsf(cfa[idx + p1] - cfa[idx - p1]);
        delm[halfIdx] = fabsf(cfa[idx + m1] - cfa[idx - m1]);
        dgrbsq1p[halfIdx] =
            sqr_f(cfa[idx + 1] - cfa[idx + 1 - p1]) +
            sqr_f(cfa[idx + 1] - cfa[idx + 1 + p1]);
        dgrbsq1m[halfIdx] =
            sqr_f(cfa[idx + 1] - cfa[idx + 1 - m1]) +
            sqr_f(cfa[idx + 1] - cfa[idx + 1 + m1]);
    }
    else
    {
        dgrbsq1p[halfIdx] =
            sqr_f(cfa[idx] - cfa[idx - p1]) +
            sqr_f(cfa[idx] - cfa[idx + p1]);
        dgrbsq1m[halfIdx] =
            sqr_f(cfa[idx] - cfa[idx - m1]) +
            sqr_f(cfa[idx] - cfa[idx + m1]);
        delp[halfIdx] = fabsf(cfa[idx + 1 + p1] - cfa[idx + 1 - p1]);
        delm[halfIdx] = fabsf(cfa[idx + 1 + m1] - cfa[idx + 1 - m1]);
    }
}

__global__ void k_green_interpolation(const float * cfa,
                                      const float * dirwts0,
                                      const float * dirwts1,
                                      float * vcd,
                                      float * hcd,
                                      float * vcdalt,
                                      float * hcdalt,
                                      float * dgintv,
                                      float * dginth,
                                      int rr1,
                                      int cc1)
{
    const int cc = blockIdx.x * blockDim.x + threadIdx.x;
    const int rr = blockIdx.y * blockDim.y + threadIdx.y;
    if (rr < 4 || rr >= rr1 - 4 || cc < 4 || cc >= cc1 - 4) return;

    const int v1 = kTileSize;
    const int v2 = 2 * kTileSize;
    const int idx = rr * kTileSize + cc;

    const float cru =
        cfa[idx - v1] * (dirwts0[idx - v2] + dirwts0[idx]) /
        (dirwts0[idx - v2] * (kEps + cfa[idx]) +
         dirwts0[idx] * (kEps + cfa[idx - v2]));
    const float crd =
        cfa[idx + v1] * (dirwts0[idx + v2] + dirwts0[idx]) /
        (dirwts0[idx + v2] * (kEps + cfa[idx]) +
         dirwts0[idx] * (kEps + cfa[idx + v2]));
    const float crl =
        cfa[idx - 1] * (dirwts1[idx - 2] + dirwts1[idx]) /
        (dirwts1[idx - 2] * (kEps + cfa[idx]) +
         dirwts1[idx] * (kEps + cfa[idx - 2]));
    const float crr =
        cfa[idx + 1] * (dirwts1[idx + 2] + dirwts1[idx]) /
        (dirwts1[idx + 2] * (kEps + cfa[idx]) +
         dirwts1[idx] * (kEps + cfa[idx + 2]));

    const float guha = cfa[idx - v1] + xdiv2f_probe(cfa[idx] - cfa[idx - v2]);
    const float gdha = cfa[idx + v1] + xdiv2f_probe(cfa[idx] - cfa[idx + v2]);
    const float glha = cfa[idx - 1] + xdiv2f_probe(cfa[idx] - cfa[idx - 2]);
    const float grha = cfa[idx + 1] + xdiv2f_probe(cfa[idx] - cfa[idx + 2]);

    float guar = (fabsf(1.0f - cru) < kAdaptiveRatioThreshold) ? cfa[idx] * cru : guha;
    float gdar = (fabsf(1.0f - crd) < kAdaptiveRatioThreshold) ? cfa[idx] * crd : gdha;
    float glar = (fabsf(1.0f - crl) < kAdaptiveRatioThreshold) ? cfa[idx] * crl : glha;
    float grar = (fabsf(1.0f - crr) < kAdaptiveRatioThreshold) ? cfa[idx] * crr : grha;

    const float hwt = dirwts1[idx - 1] / (dirwts1[idx - 1] + dirwts1[idx + 1]);
    const float vwt = dirwts0[idx - v1] / (dirwts0[idx + v1] + dirwts0[idx - v1]);
    const float gintvha = vwt * gdha + (1.0f - vwt) * guha;
    const float ginthha = hwt * grha + (1.0f - hwt) * glha;
    const float vinterp = vwt * gdar + (1.0f - vwt) * guar;
    const float hinterp = hwt * grar + (1.0f - hwt) * glar;

    if ((fc_rggb(rr, cc) & 1) != 0)
    {
        vcd[idx] = cfa[idx] - vinterp;
        hcd[idx] = cfa[idx] - hinterp;
        vcdalt[idx] = cfa[idx] - gintvha;
        hcdalt[idx] = cfa[idx] - ginthha;
    }
    else
    {
        vcd[idx] = vinterp - cfa[idx];
        hcd[idx] = hinterp - cfa[idx];
        vcdalt[idx] = gintvha - cfa[idx];
        hcdalt[idx] = ginthha - cfa[idx];
    }

    if (cfa[idx] > kClipPoint8 || gintvha > kClipPoint8 || ginthha > kClipPoint8)
    {
        guar = guha;
        gdar = gdha;
        glar = glha;
        grar = grha;
        vcd[idx] = vcdalt[idx];
        hcd[idx] = hcdalt[idx];
    }

    dgintv[idx] = fminf(sqr_f(guha - gdha), sqr_f(guar - gdar));
    dginth[idx] = fminf(sqr_f(glha - grha), sqr_f(glar - grar));
}

__global__ void k_variance_selection_scalar(float * cfa,
                                            float * vcd,
                                            float * hcd,
                                            const float * vcdalt,
                                            const float * hcdalt,
                                            float * cddiffsq,
                                            int rr1,
                                            int cc1)
{
    if (blockIdx.x != 0 || blockIdx.y != 0 || threadIdx.x != 0 || threadIdx.y != 0) return;

    const int v1 = kTileSize;
    const int v2 = 2 * kTileSize;
    for (int rr = 4; rr < rr1 - 4; ++rr)
    {
        for (int cc = 4; cc < cc1 - 4; ++cc)
        {
            const int idx = rr * kTileSize + cc;
            const float hcdvar =
                3.0f * (sqr_f(hcd[idx - 2]) +
                        sqr_f(hcd[idx]) +
                        sqr_f(hcd[idx + 2])) -
                sqr_f(hcd[idx - 2] + hcd[idx] + hcd[idx + 2]);
            const float hcdaltvar =
                3.0f * (sqr_f(hcdalt[idx - 2]) +
                        sqr_f(hcdalt[idx]) +
                        sqr_f(hcdalt[idx + 2])) -
                sqr_f(hcdalt[idx - 2] + hcdalt[idx] + hcdalt[idx + 2]);
            const float vcdvar =
                3.0f * (sqr_f(vcd[idx - v2]) +
                        sqr_f(vcd[idx]) +
                        sqr_f(vcd[idx + v2])) -
                sqr_f(vcd[idx - v2] + vcd[idx] + vcd[idx + v2]);
            const float vcdaltvar =
                3.0f * (sqr_f(vcdalt[idx - v2]) +
                        sqr_f(vcdalt[idx]) +
                        sqr_f(vcdalt[idx + v2])) -
                sqr_f(vcdalt[idx - v2] + vcdalt[idx] + vcdalt[idx + v2]);

            if (hcdaltvar < hcdvar) hcd[idx] = hcdalt[idx];
            if (vcdaltvar < vcdvar) vcd[idx] = vcdalt[idx];

            if ((fc_rggb(rr, cc) & 1) != 0)
            {
                const float ginth = -hcd[idx] + cfa[idx];
                const float gintv = -vcd[idx] + cfa[idx];
                if (hcd[idx] > 0.0f)
                {
                    const float bounded = -ulim_f(ginth, cfa[idx - 1], cfa[idx + 1]) + cfa[idx];
                    if (3.0f * hcd[idx] > (ginth + cfa[idx]))
                    {
                        hcd[idx] = bounded;
                    }
                    else
                    {
                        const float hwt = 1.0f - 3.0f * hcd[idx] / (kEps + ginth + cfa[idx]);
                        hcd[idx] = hwt * hcd[idx] + (1.0f - hwt) * bounded;
                    }
                }
                if (vcd[idx] > 0.0f)
                {
                    const float bounded = -ulim_f(gintv, cfa[idx - v1], cfa[idx + v1]) + cfa[idx];
                    if (3.0f * vcd[idx] > (gintv + cfa[idx]))
                    {
                        vcd[idx] = bounded;
                    }
                    else
                    {
                        const float vwt = 1.0f - 3.0f * vcd[idx] / (kEps + gintv + cfa[idx]);
                        vcd[idx] = vwt * vcd[idx] + (1.0f - vwt) * bounded;
                    }
                }
                if (ginth > kClipPoint)
                {
                    hcd[idx] = -ulim_f(ginth, cfa[idx - 1], cfa[idx + 1]) + cfa[idx];
                }
                if (gintv > kClipPoint)
                {
                    vcd[idx] = -ulim_f(gintv, cfa[idx - v1], cfa[idx + v1]) + cfa[idx];
                }
            }
            else
            {
                const float ginth = hcd[idx] + cfa[idx];
                const float gintv = vcd[idx] + cfa[idx];
                if (hcd[idx] < 0.0f)
                {
                    const float bounded = ulim_f(ginth, cfa[idx - 1], cfa[idx + 1]) - cfa[idx];
                    if (3.0f * hcd[idx] < -(ginth + cfa[idx]))
                    {
                        hcd[idx] = bounded;
                    }
                    else
                    {
                        const float hwt = 1.0f + 3.0f * hcd[idx] / (kEps + ginth + cfa[idx]);
                        hcd[idx] = hwt * hcd[idx] + (1.0f - hwt) * bounded;
                    }
                }
                if (vcd[idx] < 0.0f)
                {
                    const float bounded = ulim_f(gintv, cfa[idx - v1], cfa[idx + v1]) - cfa[idx];
                    if (3.0f * vcd[idx] < -(gintv + cfa[idx]))
                    {
                        vcd[idx] = bounded;
                    }
                    else
                    {
                        const float vwt = 1.0f + 3.0f * vcd[idx] / (kEps + gintv + cfa[idx]);
                        vcd[idx] = vwt * vcd[idx] + (1.0f - vwt) * bounded;
                    }
                }
                if (ginth > kClipPoint)
                {
                    hcd[idx] = ulim_f(ginth, cfa[idx - 1], cfa[idx + 1]) - cfa[idx];
                }
                if (gintv > kClipPoint)
                {
                    vcd[idx] = ulim_f(gintv, cfa[idx - v1], cfa[idx + v1]) - cfa[idx];
                }
                cddiffsq[idx] = sqr_f(vcd[idx] - hcd[idx]);
            }
        }
    }
}

__global__ void k_hvwt_adaptive_weights(const float * dirwts0,
                                        const float * dirwts1,
                                        const float * vcd,
                                        const float * hcd,
                                        const float * dgintv,
                                        const float * dginth,
                                        float * hvwt,
                                        int rr1,
                                        int cc1)
{
    const int cc = blockIdx.x * blockDim.x + threadIdx.x;
    const int rr = blockIdx.y * blockDim.y + threadIdx.y;
    if (rr < 6 || rr >= rr1 - 6) return;

    const int ccStart = 6 + (fc_rggb(rr, 2) & 1);
    if (cc < ccStart || cc >= cc1 - 6 || ((cc - ccStart) & 1) != 0) return;

    const int v1 = kTileSize;
    const int v2 = 2 * kTileSize;
    const int v3 = 3 * kTileSize;
    const float epssq = kEps * kEps;
    const int idx = rr * kTileSize + cc;

    const float uave = vcd[idx] + vcd[idx - v1] + vcd[idx - v2] + vcd[idx - v3];
    const float dave = vcd[idx] + vcd[idx + v1] + vcd[idx + v2] + vcd[idx + v3];
    const float lave = hcd[idx] + hcd[idx - 1] + hcd[idx - 2] + hcd[idx - 3];
    const float rave = hcd[idx] + hcd[idx + 1] + hcd[idx + 2] + hcd[idx + 3];

    const float dgrbvvaru =
        sqr_f(vcd[idx] - uave) +
        sqr_f(vcd[idx - v1] - uave) +
        sqr_f(vcd[idx - v2] - uave) +
        sqr_f(vcd[idx - v3] - uave);
    const float dgrbvvard =
        sqr_f(vcd[idx] - dave) +
        sqr_f(vcd[idx + v1] - dave) +
        sqr_f(vcd[idx + v2] - dave) +
        sqr_f(vcd[idx + v3] - dave);
    const float dgrbhvarl =
        sqr_f(hcd[idx] - lave) +
        sqr_f(hcd[idx - 1] - lave) +
        sqr_f(hcd[idx - 2] - lave) +
        sqr_f(hcd[idx - 3] - lave);
    const float dgrbhvarr =
        sqr_f(hcd[idx] - rave) +
        sqr_f(hcd[idx + 1] - rave) +
        sqr_f(hcd[idx + 2] - rave) +
        sqr_f(hcd[idx + 3] - rave);

    const float hwt = dirwts1[idx - 1] / (dirwts1[idx - 1] + dirwts1[idx + 1]);
    const float vwt = dirwts0[idx - v1] / (dirwts0[idx + v1] + dirwts0[idx - v1]);

    const float vcdvar = epssq + vwt * dgrbvvard + (1.0f - vwt) * dgrbvvaru;
    const float hcdvar = epssq + hwt * dgrbhvarr + (1.0f - hwt) * dgrbhvarl;

    const float dgrbvvaru1 = dgintv[idx] + dgintv[idx - v1] + dgintv[idx - v2];
    const float dgrbvvard1 = dgintv[idx] + dgintv[idx + v1] + dgintv[idx + v2];
    const float dgrbhvarl1 = dginth[idx] + dginth[idx - 1] + dginth[idx - 2];
    const float dgrbhvarr1 = dginth[idx] + dginth[idx + 1] + dginth[idx + 2];

    const float vcdvar1 = epssq + vwt * dgrbvvard1 + (1.0f - vwt) * dgrbvvaru1;
    const float hcdvar1 = epssq + hwt * dgrbhvarr1 + (1.0f - hwt) * dgrbhvarl1;

    const float varwt = hcdvar / (vcdvar + hcdvar);
    const float diffwt = hcdvar1 / (vcdvar1 + hcdvar1);
    if ((0.5f - varwt) * (0.5f - diffwt) > 0.0f &&
        fabsf(0.5f - diffwt) < fabsf(0.5f - varwt))
    {
        hvwt[idx >> 1] = varwt;
    }
    else
    {
        hvwt[idx >> 1] = diffwt;
    }
}

void check_cuda(cudaError_t rc, const char * call, const char * file, int line)
{
    if (rc == cudaSuccess) return;
    std::fprintf(stderr,
                 "CUDA error %s @ %s:%d: %s\n",
                 call,
                 file,
                 line,
                 cudaGetErrorString(rc));
    std::exit(10);
}

#define CK(call) check_cuda((call), #call, __FILE__, __LINE__)

void allocate_device(DeviceBuffers * d, std::size_t rawCount)
{
    CK(cudaMalloc(&d->raw, rawCount * sizeof(uint16_t)));
    CK(cudaMalloc(&d->cfa, kTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->rgbgreen, kTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->dirwts0, kTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->dirwts1, kTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->delhvsqsum, kTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->delp, kHalfTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->delm, kHalfTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->dgrbsq1p, kHalfTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->dgrbsq1m, kHalfTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->vcd, kTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->hcd, kTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->vcdalt, kTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->hcdalt, kTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->dgintv, kTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->dginth, kTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->cddiffsq, kTileSamples * sizeof(float)));
    CK(cudaMalloc(&d->hvwt, kHalfTileSamples * sizeof(float)));
}

void free_device(DeviceBuffers * d)
{
    cudaFree(d->raw);
    cudaFree(d->cfa);
    cudaFree(d->rgbgreen);
    cudaFree(d->dirwts0);
    cudaFree(d->dirwts1);
    cudaFree(d->delhvsqsum);
    cudaFree(d->delp);
    cudaFree(d->delm);
    cudaFree(d->dgrbsq1p);
    cudaFree(d->dgrbsq1m);
    cudaFree(d->vcd);
    cudaFree(d->hcd);
    cudaFree(d->vcdalt);
    cudaFree(d->hcdalt);
    cudaFree(d->dgintv);
    cudaFree(d->dginth);
    cudaFree(d->cddiffsq);
    cudaFree(d->hvwt);
}

void clear_device_stages(const DeviceBuffers & d)
{
    CK(cudaMemset(d.cfa, 0, kTileSamples * sizeof(float)));
    CK(cudaMemset(d.rgbgreen, 0, kTileSamples * sizeof(float)));
    CK(cudaMemset(d.dirwts0, 0, kTileSamples * sizeof(float)));
    CK(cudaMemset(d.dirwts1, 0, kTileSamples * sizeof(float)));
    CK(cudaMemset(d.delhvsqsum, 0, kTileSamples * sizeof(float)));
    CK(cudaMemset(d.delp, 0, kHalfTileSamples * sizeof(float)));
    CK(cudaMemset(d.delm, 0, kHalfTileSamples * sizeof(float)));
    CK(cudaMemset(d.dgrbsq1p, 0, kHalfTileSamples * sizeof(float)));
    CK(cudaMemset(d.dgrbsq1m, 0, kHalfTileSamples * sizeof(float)));
    CK(cudaMemset(d.vcd, 0, kTileSamples * sizeof(float)));
    CK(cudaMemset(d.hcd, 0, kTileSamples * sizeof(float)));
    CK(cudaMemset(d.vcdalt, 0, kTileSamples * sizeof(float)));
    CK(cudaMemset(d.hcdalt, 0, kTileSamples * sizeof(float)));
    CK(cudaMemset(d.dgintv, 0, kTileSamples * sizeof(float)));
    CK(cudaMemset(d.dginth, 0, kTileSamples * sizeof(float)));
    CK(cudaMemset(d.cddiffsq, 0, kTileSamples * sizeof(float)));
    CK(cudaMemset(d.hvwt, 0, kHalfTileSamples * sizeof(float)));
}

void copy_device_to_host(const DeviceBuffers & d, StageBuffers * out)
{
    CK(cudaMemcpy(out->cfa.data(), d.cfa, kTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->rgbgreen.data(), d.rgbgreen, kTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->dirwts0.data(), d.dirwts0, kTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->dirwts1.data(), d.dirwts1, kTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->delhvsqsum.data(), d.delhvsqsum, kTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->delp.data(), d.delp, kHalfTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->delm.data(), d.delm, kHalfTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->dgrbsq1p.data(), d.dgrbsq1p, kHalfTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->dgrbsq1m.data(), d.dgrbsq1m, kHalfTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->vcd.data(), d.vcd, kTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->hcd.data(), d.hcd, kTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->vcdalt.data(), d.vcdalt, kTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->hcdalt.data(), d.hcdalt, kTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->dgintv.data(), d.dgintv, kTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->dginth.data(), d.dginth, kTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->cddiffsq.data(), d.cddiffsq, kTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
    CK(cudaMemcpy(out->hvwt.data(), d.hvwt, kHalfTileSamples * sizeof(float), cudaMemcpyDeviceToHost));
}

uint32_t float_bits(float value)
{
    uint32_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

CompareStats compare_array(const std::vector<float> & cpu,
                           const std::vector<float> & gpu)
{
    CompareStats stats;
    for (std::size_t i = 0; i < cpu.size(); ++i)
    {
        const float diff = std::fabs(cpu[i] - gpu[i]);
        if (diff > stats.maxAbs)
        {
            stats.maxAbs = diff;
            stats.maxIndex = i;
        }
        if (diff > kTolerance)
        {
            ++stats.mismatches;
        }
        if (float_bits(cpu[i]) != float_bits(gpu[i]))
        {
            ++stats.bitMismatches;
        }
    }
    return stats;
}

bool report_compare(const char * caseName,
                    const char * arrayName,
                    const std::vector<float> & cpu,
                    const std::vector<float> & gpu)
{
    const CompareStats stats = compare_array(cpu, gpu);
    std::cout << "  " << caseName << "." << arrayName
              << ": max_abs=" << std::setprecision(9) << stats.maxAbs
              << " mismatches_gt_" << kTolerance << "=" << stats.mismatches
              << " bit_mismatches=" << stats.bitMismatches
              << " max_index=" << stats.maxIndex
              << "\n";
    return stats.mismatches == 0;
}

bool run_case(const CaseSpec & spec)
{
    const int bottom = imin_i(spec.top + kTileSize, spec.height + 16);
    const int right = imin_i(spec.left + kTileSize, spec.width + 16);
    const int rr1 = bottom - spec.top;
    const int cc1 = right - spec.left;
    const std::vector<uint16_t> raw =
        make_raw_pattern(spec.width, spec.height, 0xA5A50000u ^
                         static_cast<uint32_t>(spec.top * 131 + spec.left));

    StageBuffers cpu;
    StageBuffers gpu;
    cpu_stage_probe(raw, spec, &cpu);

    DeviceBuffers d;
    allocate_device(&d, raw.size());
    CK(cudaMemcpy(d.raw,
                  raw.data(),
                  raw.size() * sizeof(uint16_t),
                  cudaMemcpyHostToDevice));
    clear_device_stages(d);

    const dim3 block(16, 16);
    const dim3 grid((kTileSize + block.x - 1) / block.x,
                    (kTileSize + block.y - 1) / block.y);
    k_tile_load<<<grid, block>>>(d.raw,
                                 d.cfa,
                                 d.rgbgreen,
                                 spec.width,
                                 spec.height,
                                 spec.top,
                                 spec.left,
                                 rr1,
                                 cc1);
    CK(cudaGetLastError());
    k_gradients<<<grid, block>>>(d.cfa,
                                 d.dirwts0,
                                 d.dirwts1,
                                 d.delhvsqsum,
                                 rr1,
                                 cc1);
    CK(cudaGetLastError());
    k_diagonal_precursors<<<grid, block>>>(d.cfa,
                                           d.delp,
                                           d.delm,
                                           d.dgrbsq1p,
                                           d.dgrbsq1m,
                                           rr1,
                                           cc1);
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
    k_variance_selection_scalar<<<1, 1>>>(d.cfa,
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
    CK(cudaDeviceSynchronize());

    copy_device_to_host(d, &gpu);
    free_device(&d);

    std::cout << "[case] " << spec.name
              << " image=" << spec.width << "x" << spec.height
              << " tile_top_left=(" << spec.top << "," << spec.left << ")"
              << " tile_extent=" << rr1 << "x" << cc1 << "\n";

    bool ok = true;
    ok = report_compare(spec.name, "cfa", cpu.cfa, gpu.cfa) && ok;
    ok = report_compare(spec.name, "rgbgreen", cpu.rgbgreen, gpu.rgbgreen) && ok;
    ok = report_compare(spec.name, "dirwts0", cpu.dirwts0, gpu.dirwts0) && ok;
    ok = report_compare(spec.name, "dirwts1", cpu.dirwts1, gpu.dirwts1) && ok;
    ok = report_compare(spec.name, "delhvsqsum", cpu.delhvsqsum, gpu.delhvsqsum) && ok;
    ok = report_compare(spec.name, "delp", cpu.delp, gpu.delp) && ok;
    ok = report_compare(spec.name, "delm", cpu.delm, gpu.delm) && ok;
    ok = report_compare(spec.name, "dgrbsq1p", cpu.dgrbsq1p, gpu.dgrbsq1p) && ok;
    ok = report_compare(spec.name, "dgrbsq1m", cpu.dgrbsq1m, gpu.dgrbsq1m) && ok;
    ok = report_compare(spec.name, "vcd", cpu.vcd, gpu.vcd) && ok;
    ok = report_compare(spec.name, "hcd", cpu.hcd, gpu.hcd) && ok;
    ok = report_compare(spec.name, "vcdalt", cpu.vcdalt, gpu.vcdalt) && ok;
    ok = report_compare(spec.name, "hcdalt", cpu.hcdalt, gpu.hcdalt) && ok;
    ok = report_compare(spec.name, "dgintv", cpu.dgintv, gpu.dgintv) && ok;
    ok = report_compare(spec.name, "dginth", cpu.dginth, gpu.dginth) && ok;
    ok = report_compare(spec.name, "cddiffsq", cpu.cddiffsq, gpu.cddiffsq) && ok;
    ok = report_compare(spec.name, "hvwt", cpu.hvwt, gpu.hvwt) && ok;
    return ok;
}
}

int main()
{
    int deviceCount = 0;
    CK(cudaGetDeviceCount(&deviceCount));
    if (deviceCount < 1)
    {
        std::fprintf(stderr, "No CUDA devices found.\n");
        return 2;
    }

    CK(cudaSetDevice(0));
    cudaDeviceProp prop;
    CK(cudaGetDeviceProperties(&prop, 0));
    std::cout << "=== cuda_amaze_debayer_stage_probe ===\n"
              << "device=" << prop.name << " sm_" << prop.major << prop.minor
              << " tolerance=" << kTolerance << "\n";

    const CaseSpec cases[] = {
        {"top_left_halo", 1808, 2268, -16, -16},
        {"interior_full_tile", 1808, 2268, 112, 112},
        {"bottom_right_halo", 1808, 2268, 2160, 1680},
    };

    bool ok = true;
    for (const CaseSpec & spec : cases)
    {
        ok = run_case(spec) && ok;
    }

    std::cout << "\n[amaze-stage-probe] RESULT: "
              << (ok ? "PASS" : "FAIL")
              << " (generic AMaZE tile/gradient/green-interpolation/variance-selection/hvwt stages)\n";
    return ok ? 0 : 1;
}
