#include "GpuPreviewProcessing.h"

#include "../../src/processing/raw_processing.h"

#include <QGuiApplication>
#include <QOffscreenSurface>
#include <QOpenGLContext>
#include <QOpenGLFramebufferObject>
#include <QOpenGLFramebufferObjectFormat>
#include <QOpenGLFunctions>
#include <QOpenGLShaderProgram>
#include <QOpenGLTexture>
#include <QSurfaceFormat>
#include <QVector2D>
#include <QVector3D>
#include <QtGlobal>

#include <algorithm>
#include <cfloat>
#include <cmath>
#include <cstring>
#include <omp.h>

namespace
{
constexpr float kRec709RgbToY[3] = {
    0.2126729f,
    0.7151522f,
    0.0721750f
};
constexpr int kLutTextureEdge = 256;
constexpr GLfloat kQuadVertices[16] = {
    -1.0f,  1.0f, 0.0f, 1.0f,
     1.0f,  1.0f, 1.0f, 1.0f,
    -1.0f, -1.0f, 0.0f, 0.0f,
     1.0f, -1.0f, 1.0f, 0.0f,
};

bool envFlagEnabled(const QByteArray & value)
{
    if ( value.isEmpty() ) return false;

    const QByteArray normalized = value.trimmed().toLower();
    return normalized == "1"
        || normalized == "true"
        || normalized == "yes"
        || normalized == "on";
}

uint64_t fnv1a64_append(uint64_t hash, const void * data, size_t size)
{
    const uint8_t * bytes = static_cast<const uint8_t *>(data);
    for (size_t index = 0; index < size; ++index)
    {
        hash ^= bytes[index];
        hash *= 1099511628211ull;
    }
    return hash;
}

float clamp01(float value)
{
    return std::max(0.0f, std::min(1.0f, value));
}

float reinhardTonemap(float value)
{
    return (value < 0.0f) ? value : value / (1.0f + value);
}

float reinhardForColour(float value)
{
    return (value < 0.5f)
        ? value
        : (reinhardTonemap((value - 0.5f) / 0.5f) * 0.5f + 0.5f);
}

float reinhardForBlue(float value)
{
    return (value < 0.7f)
        ? value
        : (reinhardTonemap((value - 0.7f) / 0.3f) * 0.3f + 0.7f);
}

const uint16_t * lutValues(const QByteArray & bytes)
{
    return reinterpret_cast<const uint16_t *>(bytes.constData());
}

uint16_t sampleLut(const QByteArray & lutBytes, float normalized)
{
    if ( lutBytes.size() < static_cast<int>(65536u * sizeof(uint16_t)) )
    {
        return 0;
    }

    const int index = std::max(0, std::min(65535,
        static_cast<int>(normalized * 65535.0f + 0.5f)));
    return lutValues(lutBytes)[index];
}

float sampleNormalizedLut(const QByteArray & lutBytes, float normalized)
{
    if ( lutBytes.size() < static_cast<int>(65536u * sizeof(uint16_t)) )
    {
        return 0.0f;
    }

    const int index = std::max(0, std::min(65535,
        static_cast<int>(normalized * 65535.0f + 0.5f)));
    return reinterpret_cast<const uint16_t *>(lutBytes.constData())[index] / 65535.0f;
}

/* Hue-vs / luma-vs creative curves are float[36000] in [-1,1]. The CPU
 * reference reads them directly; the GPU uploads each as an R32F texture and
 * samples by the same integer index, so both stay bit-aligned. The luma index
 * is clamped to [0,35999]: hue_vs_luma can boost V (hsv[2]) to >= 1.0, and the
 * production path then indexes luma_vs_saturation[] out of bounds (the array is
 * exactly 36000 entries, and V == 1.0 already yields index 36000). That read is
 * undefined on the CPU, so both this reference and the shader clamp to the last
 * valid sample instead of matching undefined behaviour. */
constexpr int kHueVsCurveSamples = 36000;

float sampleHueVsCurve(const QByteArray & curveBytes, int index)
{
    if ( curveBytes.size() < static_cast<int>(kHueVsCurveSamples * sizeof(float)) )
    {
        return 0.0f;
    }
    const int clamped = std::max(0, std::min(kHueVsCurveSamples - 1, index));
    return reinterpret_cast<const float *>(curveBytes.constData())[clamped];
}

/* In-loop simple-contrast factor: processing->contrast_curve is a double[65536]
 * of per-luma exposure multipliers (raw_processing.c:2954), indexed by the
 * integer luma of the matrix-applied pixel. Carried as float[65536]; the factor
 * is a smooth exposure multiply and the result is quantised to uint16, so the
 * double->float narrowing is well within tolerance. */
constexpr int kInLoopContrastSamples = 65536;

float sampleInLoopContrastFactor(const QByteArray & curveBytes, int index)
{
    if ( curveBytes.size() < static_cast<int>(kInLoopContrastSamples * sizeof(float)) )
    {
        return 1.0f;
    }
    const int clamped = std::max(0, std::min(kInLoopContrastSamples - 1, index));
    return reinterpret_cast<const float *>(curveBytes.constData())[clamped];
}

/* Float32 mirror of fromRGBtoHSV (processing.c:282-308): hsv[0]=H in [0,360),
 * hsv[1]=S, hsv[2]=V. Kept byte-identical (same >= tie ordering) so the CPU
 * reference and the GLSL port agree. */
void previewFromRGBtoHSV(const float rgb[3], float hsv[3])
{
    hsv[0] = 0.0f;
    hsv[2] = std::max(rgb[0], std::max(rgb[1], rgb[2]));
    const float delta = hsv[2] - std::min(rgb[0], std::min(rgb[1], rgb[2]));
    if ( delta < FLT_MIN )
    {
        hsv[1] = 0.0f;
    }
    else
    {
        hsv[1] = delta / hsv[2];
        if ( rgb[0] >= hsv[2] )
        {
            hsv[0] = (rgb[1] - rgb[2]) / delta;
            if ( hsv[0] < 0.0f ) hsv[0] += 6.0f;
        }
        else if ( rgb[1] >= hsv[2] )
        {
            hsv[0] = 2.0f + (rgb[2] - rgb[0]) / delta;
        }
        else
        {
            hsv[0] = 4.0f + (rgb[0] - rgb[1]) / delta;
        }
    }
    hsv[0] *= 60.0f;
}

/* Float32 mirror of fromHSVtoRGB (processing.c:310-363). */
void previewFromHSVtoRGB(const float hsv[3], float rgb[3])
{
    if ( hsv[1] < FLT_MIN )
    {
        rgb[0] = rgb[1] = rgb[2] = hsv[2];
        return;
    }
    const float h = hsv[0] / 60.0f;
    const int i = static_cast<int>(h);
    const float f = h - static_cast<float>(i);
    const float p = hsv[2] * (1.0f - hsv[1]);
    if ( i & 1 )
    {
        const float q = hsv[2] * (1.0f - (hsv[1] * f));
        if ( i == 1 ) { rgb[0] = q; rgb[1] = hsv[2]; rgb[2] = p; }
        else if ( i == 3 ) { rgb[0] = p; rgb[1] = q; rgb[2] = hsv[2]; }
        else { rgb[0] = hsv[2]; rgb[1] = p; rgb[2] = q; }
    }
    else
    {
        const float t = hsv[2] * (1.0f - (hsv[1] * (1.0f - f)));
        if ( i == 0 ) { rgb[0] = hsv[2]; rgb[1] = t; rgb[2] = p; }
        else if ( i == 2 ) { rgb[0] = p; rgb[1] = hsv[2]; rgb[2] = t; }
        else { rgb[0] = t; rgb[1] = p; rgb[2] = hsv[2]; }
    }
}

void applyPreviewProcessingPixel(const GpuPreviewProcessingConfig & config,
                                 const uint16_t * inputPixel,
                                 uint16_t * outputPixel)
{
    float color[3];
    for (int channel = 0; channel < 3; ++channel)
    {
        color[channel] =
            sampleLut(config.levelsLut, inputPixel[channel] / 65535.0f) / 65535.0f;
    }

    float matrixApplied[3];
    matrixApplied[0] = sampleNormalizedLut(config.matrixLutR, color[0]);
    matrixApplied[1] = sampleNormalizedLut(config.matrixLutG, color[1]);
    matrixApplied[2] = sampleNormalizedLut(config.matrixLutB, color[2]);

    if ( config.applyInLoopContrast )
    {
        /* In-loop simple-contrast factor (raw_processing.c:2941-2954): a per-pixel
         * exposure multiply by contrast_curve[cval], where cval is the integer
         * luma (4R+11G+B)>>4 of the matrix-applied (pre camera-WB) pixel. Applied
         * to the matrix value before the camera matrix and gamma, matching
         * pix0 = wb_r * expo_correction. */
        const int32_t matR = static_cast<int32_t>(matrixApplied[0] * 65535.0f + 0.5f);
        const int32_t matG = static_cast<int32_t>(matrixApplied[1] * 65535.0f + 0.5f);
        const int32_t matB = static_cast<int32_t>(matrixApplied[2] * 65535.0f + 0.5f);
        const int32_t cval = ((matR << 2) + (matG * 11) + matB) >> 4;
        const float factor = sampleInLoopContrastFactor(config.inLoopContrastCurve, cval);
        matrixApplied[0] *= factor;
        matrixApplied[1] *= factor;
        matrixApplied[2] *= factor;
    }

    if ( config.useCameraMatrix )
    {
        float wbApplied[3];
        wbApplied[0] = config.properWbMatrix[0] * matrixApplied[0]
                     + config.properWbMatrix[1] * matrixApplied[1]
                     + config.properWbMatrix[2] * matrixApplied[2];
        wbApplied[1] = config.properWbMatrix[3] * matrixApplied[0]
                     + config.properWbMatrix[4] * matrixApplied[1]
                     + config.properWbMatrix[5] * matrixApplied[2];
        wbApplied[2] = config.properWbMatrix[6] * matrixApplied[0]
                     + config.properWbMatrix[7] * matrixApplied[1]
                     + config.properWbMatrix[8] * matrixApplied[2];

        if ( config.applyGamutCompression )
        {
            const float y = config.rgbToY[0] * wbApplied[0]
                          + config.rgbToY[1] * wbApplied[1]
                          + config.rgbToY[2] * wbApplied[2];
            const float minChannel =
                std::min(std::min(wbApplied[0], wbApplied[1]), wbApplied[2]);
            float gamutReference[3];
            for (int channel = 0; channel < 3; ++channel)
            {
                const float yToMinChannel =
                    (y != 0.0f) ? ((y - wbApplied[channel]) / y) : 0.0f;
                const float tonemapped = (channel == 0)
                    ? reinhardForColour(yToMinChannel)
                    : reinhardForBlue(yToMinChannel);
                gamutReference[channel] = -(tonemapped * y) + y;
            }

            const float gamutMin =
                std::min(std::min(gamutReference[0], gamutReference[1]), gamutReference[2]);
            float desaturateFactor = 1.0f;
            const float denominator = y - minChannel;
            if ( y > 0.0f && std::fabs(denominator) > 1e-8f )
            {
                desaturateFactor = (y - gamutMin) / denominator;
            }

            for (int channel = 0; channel < 3; ++channel)
            {
                wbApplied[channel] = (wbApplied[channel] - y) * desaturateFactor + y;
            }
        }

        std::memcpy(matrixApplied, wbApplied, sizeof(matrixApplied));
    }

    if ( config.applyAgx )
    {
        /* AgX forward, after WB/gamut and before gamma (raw_processing_8bit_kernel
         * .inc:264-281): clip negatives, scale to 16-bit, apply the compressed-
         * gamut matrix (double accumulation, float coeffs), then (uint16_t)LIMIT16
         * = clamp+truncate, and renormalize so the gamma LUT indexes identically.
         * preview routes through the direct8 kernel that this mirrors. */
        double v[3];
        for (int channel = 0; channel < 3; ++channel)
        {
            v[channel] = static_cast<double>(matrixApplied[channel]) * 65535.0;
            if ( v[channel] < 0.0 ) v[channel] = 0.0;
        }
        const double a[3] = {
            v[0] * config.agxForward[0] + v[1] * config.agxForward[1] + v[2] * config.agxForward[2],
            v[0] * config.agxForward[3] + v[1] * config.agxForward[4] + v[2] * config.agxForward[5],
            v[0] * config.agxForward[6] + v[1] * config.agxForward[7] + v[2] * config.agxForward[8] };
        for (int channel = 0; channel < 3; ++channel)
        {
            const double clamped = a[channel] < 0.0 ? 0.0 : (a[channel] > 65535.0 ? 65535.0 : a[channel]);
            matrixApplied[channel] = static_cast<float>(static_cast<uint16_t>(clamped)) / 65535.0f;
        }
    }

    uint16_t gammaOut[3];
    for (int channel = 0; channel < 3; ++channel)
    {
        const float gammaInput = clamp01(matrixApplied[channel]);
        gammaOut[channel] = sampleLut(config.gammaLut, gammaInput);
    }

    if ( config.applyHueVs )
    {
        /* Hue-vs / luma-vs creative curves, the FIRST creative stage, matching
         * raw_processing.c:3523-3578: RGB->HSV, four signed-curve adjustments
         * indexed by hue (H*100) and luma (V*36000), HSV->RGB, then rounded back
         * to uint16. Float32 mirrors the shader (+/-1 LSB vs the CPU double
         * pipeline, within engine tolerance). The luma curve index is clamped
         * (see sampleHueVsCurve) rather than matching the production OOB read. */
        float rgb[3];
        for (int channel = 0; channel < 3; ++channel)
        {
            rgb[channel] = static_cast<float>(gammaOut[channel]) / 65535.0f;
        }
        float hsl[3];
        previewFromRGBtoHSV(rgb, hsl);

        float sat = 0.0f;
        if ( !(gammaOut[0] == 0 && gammaOut[1] == 0 && gammaOut[2] == 0) )
        {
            const uint16_t biggest = std::max(std::max(gammaOut[0], gammaOut[1]), gammaOut[2]);
            const uint16_t smallest = std::min(std::min(gammaOut[0], gammaOut[1]), gammaOut[2]);
            sat = (static_cast<float>(biggest) - static_cast<float>(smallest))
                / static_cast<float>(biggest);
        }
        sat = 2.0f * sat / (sat * sat + 1.0f);
        if ( sat > 1.0f ) sat = 1.0f;

        const int hueIndex = static_cast<int>(hsl[0] * 100.0f);
        hsl[2] *= 1.0f + (sampleHueVsCurve(config.hueVsLumaCurve, hueIndex) * sat * 2.0f);
        if ( hsl[2] < 0.0f ) hsl[2] = 0.0f;
        hsl[1] *= 1.0f + (sampleHueVsCurve(config.hueVsSaturationCurve, hueIndex) * 2.0f);
        if ( hsl[1] < 0.0f ) hsl[1] = 0.0f;
        hsl[0] += 60.0f * sampleHueVsCurve(config.hueVsHueCurve, hueIndex);
        if ( hsl[0] < 0.0f ) hsl[0] += 360.0f;
        else if ( hsl[0] >= 360.0f ) hsl[0] -= 360.0f;
        const int lumaIndex = static_cast<int>(hsl[2] * 36000.0f);
        hsl[1] *= 1.0f + (sampleHueVsCurve(config.lumaVsSaturationCurve, lumaIndex) * 2.0f);
        if ( hsl[1] < 0.0f ) hsl[1] = 0.0f;

        previewFromHSVtoRGB(hsl, rgb);
        for (int channel = 0; channel < 3; ++channel)
        {
            const int rounded = static_cast<int>(rgb[channel] * 65535.0f + 0.5f);
            gammaOut[channel] = static_cast<uint16_t>(
                rounded < 0 ? 0 : (rounded > 65535 ? 65535 : rounded));
        }
    }

    if ( config.applyVibrance )
    {
        /* Vibrance, before saturation, matching raw_processing.c:3587-3644.
         * pix0 = trunc((pix-Y1)*vibrance) + Y1 (pre_calc_vibrance LUT reduced);
         * positive vibrance blends toward the original by the pixel's own
         * saturation, negative vibrance is just pix0. Float32 mirrors the shader. */
        const int32_t Y1 = ((static_cast<int32_t>(gammaOut[0]) << 2)
                          + (static_cast<int32_t>(gammaOut[1]) * 11)
                          + static_cast<int32_t>(gammaOut[2])) >> 4;
        int32_t pix0[3];
        for (int channel = 0; channel < 3; ++channel)
        {
            pix0[channel] = static_cast<int32_t>(std::trunc(
                (static_cast<float>(gammaOut[channel]) - static_cast<float>(Y1)) * config.vibrance)) + Y1;
        }
        if ( config.vibrance > 1.0f )
        {
            const int biggest = std::max(std::max(int(gammaOut[0]), int(gammaOut[1])), int(gammaOut[2]));
            const int smallest = std::min(std::min(int(gammaOut[0]), int(gammaOut[1])), int(gammaOut[2]));
            float sat = (biggest > 0)
                ? (static_cast<float>(biggest) - static_cast<float>(smallest)) / static_cast<float>(biggest)
                : 0.0f;
            sat = 2.0f * sat / (sat * sat + 1.0f);
            if ( sat > 1.0f ) sat = 1.0f;
            for (int channel = 0; channel < 3; ++channel)
            {
                const float blended = static_cast<float>(gammaOut[channel]) * sat
                                    + static_cast<float>(pix0[channel]) * (1.0f - sat);
                const int32_t b = static_cast<int32_t>(blended);
                gammaOut[channel] = static_cast<uint16_t>(b < 0 ? 0 : (b > 65535 ? 65535 : b));
            }
        }
        else
        {
            for (int channel = 0; channel < 3; ++channel)
            {
                gammaOut[channel] = static_cast<uint16_t>(
                    pix0[channel] < 0 ? 0 : (pix0[channel] > 65535 ? 65535 : pix0[channel]));
            }
        }
    }

    if ( config.applySaturation )
    {
        /* Cross-channel saturation, before toning, matching raw_processing.c
         * 3646-3671: Y1 = (4R+11G+B)>>4 integer luma; out = LIMIT16(trunc((pix-Y1)
         * *saturation) + Y1). The LUT pre_calc_sat[index]=trunc((index-65536)*sat)
         * with index=pix-Y1+65536 reduces to this direct form (float32, matching
         * the shader; +/-1 LSB vs the CPU double LUT, within engine tolerance). */
        const int32_t Y1 = ((static_cast<int32_t>(gammaOut[0]) << 2)
                          + (static_cast<int32_t>(gammaOut[1]) * 11)
                          + static_cast<int32_t>(gammaOut[2])) >> 4;
        for (int channel = 0; channel < 3; ++channel)
        {
            const float chroma = std::trunc(
                (static_cast<float>(gammaOut[channel]) - static_cast<float>(Y1)) * config.saturation);
            const int32_t saturated = static_cast<int32_t>(chroma) + Y1;
            gammaOut[channel] = static_cast<uint16_t>(
                saturated < 0 ? 0 : (saturated > 65535 ? 65535 : saturated));
        }
    }

    for (int channel = 0; channel < 3; ++channel)
    {
        uint16_t value = gammaOut[channel];
        if ( config.applyToning )
        {
            /* Per-channel scalar gain, before the contrast curve, matching
             * raw_processing.c:3685 (truncate to uint16, no clamp). */
            value = static_cast<uint16_t>(static_cast<float>(value) * config.toningGain[channel]);
        }
        if ( config.applyCreativeCurves )
        {
            /* Post-gamma creative curves, mirroring raw_processing.c:3696-3738:
             * contrast curve (pre_calc_curve_r) on all channels, then gradation
             * gcurve_y on all channels, then per-channel gcurve_r/g/b. */
            value = sampleLut(config.contrastCurveLut, value / 65535.0f);
            value = sampleLut(config.gradationLutY, value / 65535.0f);
            const QByteArray & perChannelGradation =
                (channel == 0) ? config.gradationLutR
              : (channel == 1) ? config.gradationLutG
                               : config.gradationLutB;
            value = sampleLut(perChannelGradation, value / 65535.0f);
        }
        outputPixel[channel] = value;
    }

    if ( config.applyAgx )
    {
        /* AgX inverse, at the very end after gamma + all creative stages
         * (raw_processing_8bit_kernel.inc:358-368): undo the compressed-gamut
         * matrix (double accumulation, float coeffs), then (uint16_t)LIMIT16 =
         * clamp+truncate. */
        const double f0 = static_cast<double>(outputPixel[0]);
        const double f1 = static_cast<double>(outputPixel[1]);
        const double f2 = static_cast<double>(outputPixel[2]);
        const double iv[3] = {
            f0 * config.agxInverse[0] + f1 * config.agxInverse[1] + f2 * config.agxInverse[2],
            f0 * config.agxInverse[3] + f1 * config.agxInverse[4] + f2 * config.agxInverse[5],
            f0 * config.agxInverse[6] + f1 * config.agxInverse[7] + f2 * config.agxInverse[8] };
        for (int channel = 0; channel < 3; ++channel)
        {
            const double clamped = iv[channel] < 0.0 ? 0.0 : (iv[channel] > 65535.0 ? 65535.0 : iv[channel]);
            outputPixel[channel] = static_cast<uint16_t>(clamped);
        }
    }
}

QSurfaceFormat previewProcessingSurfaceFormat()
{
    QSurfaceFormat format;
    format.setRenderableType(QSurfaceFormat::OpenGL);
    format.setVersion(2, 0);
    format.setProfile(QSurfaceFormat::NoProfile);
    return format;
}

QString rendererDescriptionFromFunctions(QOpenGLFunctions * functions)
{
    if ( !functions ) return QStringLiteral("unknown");
    const GLubyte * renderer = functions->glGetString(GL_RENDERER);
    if ( !renderer ) return QStringLiteral("unknown");
    return QString::fromLatin1(reinterpret_cast<const char *>(renderer));
}

QOpenGLTexture * createLookupTexture()
{
    QOpenGLTexture * texture = new QOpenGLTexture(QOpenGLTexture::Target2D);
    texture->setFormat(QOpenGLTexture::RGBA16_UNorm);
    texture->setSize(kLutTextureEdge, kLutTextureEdge);
    texture->setMipLevels(1);
    texture->allocateStorage(QOpenGLTexture::RGBA, QOpenGLTexture::UInt16);
    texture->setWrapMode(QOpenGLTexture::ClampToEdge);
    texture->setMinMagFilters(QOpenGLTexture::Nearest, QOpenGLTexture::Nearest);
    return texture;
}

/* hue-vs / luma-vs curves are signed float[36000] in [-1,1]; an R32F texture
 * holds them exactly (the uint16 LUT path would quantize to ~3e-5 and break
 * bit-alignment with the CPU reference and the production float curves).
 * 256 * 141 = 36096 >= 36000 entries, sampled by integer index. */
constexpr int kHueVsCurveTextureWidth = 256;
constexpr int kHueVsCurveTextureHeight = 141;

QOpenGLTexture * createCurveTexture()
{
    QOpenGLTexture * texture = new QOpenGLTexture(QOpenGLTexture::Target2D);
    texture->setFormat(QOpenGLTexture::R32F);
    texture->setSize(kHueVsCurveTextureWidth, kHueVsCurveTextureHeight);
    texture->setMipLevels(1);
    texture->allocateStorage(QOpenGLTexture::Red, QOpenGLTexture::Float32);
    texture->setWrapMode(QOpenGLTexture::ClampToEdge);
    texture->setMinMagFilters(QOpenGLTexture::Nearest, QOpenGLTexture::Nearest);
    return texture;
}

QByteArray packCurveTextureR32F(const QByteArray & curveBytes)
{
    QByteArray packed(kHueVsCurveTextureWidth * kHueVsCurveTextureHeight
                      * static_cast<int>(sizeof(float)), Qt::Uninitialized);
    std::memset(packed.data(), 0, static_cast<size_t>(packed.size()));
    const int wanted = static_cast<int>(kHueVsCurveSamples * sizeof(float));
    if ( curveBytes.size() >= wanted )
    {
        std::memcpy(packed.data(), curveBytes.constData(), static_cast<size_t>(wanted));
    }
    return packed;
}

/* The in-loop contrast curve has 65536 entries (16-bit luma index) = 256*256,
 * so it fills a full R32F lookup texture. A neutral (empty) curve packs to all
 * 1.0 so the multiply is inert. */
QOpenGLTexture * createContrastCurveTexture()
{
    QOpenGLTexture * texture = new QOpenGLTexture(QOpenGLTexture::Target2D);
    texture->setFormat(QOpenGLTexture::R32F);
    texture->setSize(kLutTextureEdge, kLutTextureEdge);
    texture->setMipLevels(1);
    texture->allocateStorage(QOpenGLTexture::Red, QOpenGLTexture::Float32);
    texture->setWrapMode(QOpenGLTexture::ClampToEdge);
    texture->setMinMagFilters(QOpenGLTexture::Nearest, QOpenGLTexture::Nearest);
    return texture;
}

QByteArray packContrastCurveR32F(const QByteArray & curveBytes)
{
    const int count = kLutTextureEdge * kLutTextureEdge;
    QByteArray packed(count * static_cast<int>(sizeof(float)), Qt::Uninitialized);
    float * dst = reinterpret_cast<float *>(packed.data());
    const int wanted = static_cast<int>(kInLoopContrastSamples * sizeof(float));
    if ( curveBytes.size() >= wanted )
    {
        std::memcpy(packed.data(), curveBytes.constData(), static_cast<size_t>(wanted));
    }
    else
    {
        for (int index = 0; index < count; ++index) dst[index] = 1.0f;
    }
    return packed;
}

QOpenGLTexture * createFrameTexture(int width, int height)
{
    QOpenGLTexture * texture = new QOpenGLTexture(QOpenGLTexture::Target2D);
    texture->setFormat(QOpenGLTexture::RGBA16_UNorm);
    texture->setSize(width, height);
    texture->setMipLevels(1);
    texture->allocateStorage(QOpenGLTexture::RGBA, QOpenGLTexture::UInt16);
    texture->setWrapMode(QOpenGLTexture::ClampToEdge);
    texture->setMinMagFilters(QOpenGLTexture::Nearest, QOpenGLTexture::Nearest);
    return texture;
}

QByteArray packRgb16Texture(const uint16_t * inputRgb16, int pixelCount)
{
    QByteArray packed(static_cast<int>(pixelCount * 4u * sizeof(uint16_t)), Qt::Uninitialized);
    uint16_t * packedValues = reinterpret_cast<uint16_t *>(packed.data());
    for (int pixelIndex = 0; pixelIndex < pixelCount; ++pixelIndex)
    {
        packedValues[pixelIndex * 4 + 0] = inputRgb16[pixelIndex * 3 + 0];
        packedValues[pixelIndex * 4 + 1] = inputRgb16[pixelIndex * 3 + 1];
        packedValues[pixelIndex * 4 + 2] = inputRgb16[pixelIndex * 3 + 2];
        packedValues[pixelIndex * 4 + 3] = 65535;
    }
    return packed;
}

void unpackRgb16Readback(const QByteArray & readbackRgba16,
                         uint16_t * outputRgb16,
                         int width,
                         int height)
{
    const uint16_t * pixels = reinterpret_cast<const uint16_t *>(readbackRgba16.constData());
    for (int y = 0; y < height; ++y)
    {
        const int sourceY = height - 1 - y;
        for (int x = 0; x < width; ++x)
        {
            const int sourceIndex = (sourceY * width + x) * 4;
            const int destIndex = (y * width + x) * 3;
            outputRgb16[destIndex + 0] = pixels[sourceIndex + 0];
            outputRgb16[destIndex + 1] = pixels[sourceIndex + 1];
            outputRgb16[destIndex + 2] = pixels[sourceIndex + 2];
        }
    }
}

bool buildSubsetProgram(QOpenGLShaderProgram * program, QString * reason)
{
    if ( !program ) return false;

    const QByteArray vertexShader = gpuPreviewProcessingVertexShaderSource();
    const QByteArray fragmentShader = gpuPreviewProcessingSubsetFragmentShaderSource();
    if ( !program->addShaderFromSourceCode(QOpenGLShader::Vertex, vertexShader)
      || !program->addShaderFromSourceCode(QOpenGLShader::Fragment, fragmentShader)
      || !program->link() )
    {
        if ( reason )
        {
            *reason = QStringLiteral("QOpenGLShaderProgram preview-processing subset setup failed: %1")
                .arg(program->log());
        }
        return false;
    }
    return true;
}

bool makePreviewProcessingContextCurrent(QOffscreenSurface * surface,
                                         QOpenGLContext * context,
                                         QOpenGLFunctions ** functions,
                                         QString * reason,
                                         QString * rendererDescription)
{
    auto fail = [&](const QString & why) -> bool
    {
        if ( reason ) *reason = why;
        if ( rendererDescription && rendererDescription->isEmpty() )
        {
            *rendererDescription = QStringLiteral("unknown");
        }
        return false;
    };

    if ( !qobject_cast<QGuiApplication *>(QCoreApplication::instance()) )
    {
        return fail(QStringLiteral("QOffscreenSurface requires a QGuiApplication instance"));
    }

    if ( !surface || !context ) return fail(QStringLiteral("QOffscreenSurface setup received null objects"));

    const QSurfaceFormat format = previewProcessingSurfaceFormat();
    surface->setFormat(format);
    surface->create();
    if ( !surface->isValid() )
    {
        return fail(QStringLiteral("QOffscreenSurface creation failed"));
    }

    context->setFormat(surface->requestedFormat());
    if ( !context->create() )
    {
        return fail(QStringLiteral("QOpenGLContext creation failed"));
    }

    if ( !context->makeCurrent(surface) )
    {
        return fail(QStringLiteral("QOffscreenSurface makeCurrent failed"));
    }

    QOpenGLFunctions * glFunctions = context->functions();
    if ( !glFunctions )
    {
        context->doneCurrent();
        return fail(QStringLiteral("QOpenGLContext did not expose QOpenGLFunctions"));
    }

    const QString renderer = rendererDescriptionFromFunctions(glFunctions);
    if ( rendererDescription ) *rendererDescription = renderer;
    if ( functions ) *functions = glFunctions;

    /* GPU preview is hardware-only in production: a software rasterizer (llvmpipe,
     * WARP, ...) is slower than the CPU path and offers no benefit, so it is
     * rejected and the caller falls back to CPU. The MLVAPP_GPU_PREVIEW_ALLOW_SOFTWARE
     * diagnostic escape hatch (off by default) lets the CPU-vs-GPU parity harness
     * validate shader *logic* on a software GL backend in headless CI, where no
     * hardware GPU is available; it never changes default/production behaviour. */
    if ( gpuPreviewProcessingRendererIsSoftware(renderer)
      && !envFlagEnabled(qgetenv("MLVAPP_GPU_PREVIEW_ALLOW_SOFTWARE")) )
    {
        context->doneCurrent();
        return fail(QStringLiteral("software rasterizer renderer: %1").arg(renderer));
    }

    return true;
}
}

const char * gpuPreviewProcessingEnvironmentVariableName(void)
{
    return "MLVAPP_EXPERIMENTAL_GPU_PROCESSING";
}

bool gpuPreviewProcessingRequestedByEnvironment(void)
{
    return envFlagEnabled(qgetenv(gpuPreviewProcessingEnvironmentVariableName()));
}

QByteArray gpuPreviewProcessingVertexShaderSource(void)
{
    return QByteArrayLiteral(
        "attribute vec2 position;\n"
        "attribute vec2 texCoord;\n"
        "varying vec2 vTexCoord;\n"
        "void main()\n"
        "{\n"
        "    gl_Position = vec4(position, 0.0, 1.0);\n"
        "    vTexCoord = texCoord;\n"
        "}\n");
}

QByteArray gpuPreviewProcessingDisplayFragmentShaderSource(void)
{
    return QByteArrayLiteral(
        "uniform sampler2D frameTexture;\n"
        "uniform sampler2D levelsLut;\n"
        "uniform sampler2D matrixLutR;\n"
        "uniform sampler2D matrixLutG;\n"
        "uniform sampler2D matrixLutB;\n"
        "uniform sampler2D gammaLut;\n"
        "uniform vec2 textureSize;\n"
        "uniform int samplingMode;\n"
        "uniform float zebraEnabled;\n"
        "uniform float zebraUnderThreshold;\n"
        "uniform float zebraOverThreshold;\n"
        "uniform float previewProcessingEnabled;\n"
        "uniform float previewUseCameraMatrix;\n"
        "uniform float previewApplyGamutCompression;\n"
        "uniform vec3 previewProperWbRow0;\n"
        "uniform vec3 previewProperWbRow1;\n"
        "uniform vec3 previewProperWbRow2;\n"
        "uniform vec3 previewRgbToY;\n"
        "varying vec2 vTexCoord;\n"
        "float cubicWeight(float x)\n"
        "{\n"
        "    x = abs(x);\n"
        "    if (x <= 1.0)\n"
        "    {\n"
        "        return ((1.5 * x - 2.5) * x * x) + 1.0;\n"
        "    }\n"
        "    if (x < 2.0)\n"
        "    {\n"
        "        return (((-0.5 * x + 2.5) * x) - 4.0) * x + 2.0;\n"
        "    }\n"
        "    return 0.0;\n"
        "}\n"
        "vec4 sampleBicubic(vec2 uv)\n"
        "{\n"
        "    vec2 coord = uv * textureSize - 0.5;\n"
        "    vec2 base = floor(coord);\n"
        "    vec2 f = coord - base;\n"
        "    vec4 sum = vec4(0.0);\n"
        "    float totalWeight = 0.0;\n"
        "    for (int j = -1; j <= 2; ++j)\n"
        "    {\n"
        "        for (int i = -1; i <= 2; ++i)\n"
        "        {\n"
        "            float wx = cubicWeight(float(i) - f.x);\n"
        "            float wy = cubicWeight(float(j) - f.y);\n"
        "            float w = wx * wy;\n"
        "            vec2 sampleCoord = (base + vec2(float(i), float(j)) + 0.5) / textureSize;\n"
        "            sum += texture2D(frameTexture, sampleCoord) * w;\n"
        "            totalWeight += w;\n"
        "        }\n"
        "    }\n"
        "    if (totalWeight <= 0.0)\n"
        "    {\n"
        "        return texture2D(frameTexture, uv);\n"
        "    }\n"
        "    return sum / totalWeight;\n"
        "}\n"
        "float sampleU16Lut(sampler2D lut, float value)\n"
        "{\n"
        "    float clamped = clamp(value, 0.0, 1.0);\n"
        "    float index = floor(clamped * 65535.0 + 0.5);\n"
        "    float x = mod(index, 256.0);\n"
        "    float y = floor(index / 256.0);\n"
        "    vec2 uv = (vec2(x, y) + vec2(0.5)) / vec2(256.0, 256.0);\n"
        "    return texture2D(lut, uv).r;\n"
        "}\n"
        "float reinhardTonemap(float x)\n"
        "{\n"
        "    return (x < 0.0) ? x : x / (1.0 + x);\n"
        "}\n"
        "float reinhardForColour(float x)\n"
        "{\n"
        "    return (x < 0.5) ? x : (reinhardTonemap((x - 0.5) / 0.5) * 0.5 + 0.5);\n"
        "}\n"
        "float reinhardForBlue(float x)\n"
        "{\n"
        "    return (x < 0.7) ? x : (reinhardTonemap((x - 0.7) / 0.3) * 0.3 + 0.7);\n"
        "}\n"
        "vec3 applyPreviewProcessing(vec3 color)\n"
        "{\n"
        "    if (previewProcessingEnabled <= 0.5)\n"
        "    {\n"
        "        return color;\n"
        "    }\n"
        "    vec3 leveled = vec3(sampleU16Lut(levelsLut, color.r), sampleU16Lut(levelsLut, color.g), sampleU16Lut(levelsLut, color.b));\n"
        "    vec3 matrixApplied = vec3(sampleU16Lut(matrixLutR, leveled.r), sampleU16Lut(matrixLutG, leveled.g), sampleU16Lut(matrixLutB, leveled.b));\n"
        "    if (previewUseCameraMatrix > 0.5)\n"
        "    {\n"
        "        vec3 wbApplied = vec3(dot(previewProperWbRow0, matrixApplied), dot(previewProperWbRow1, matrixApplied), dot(previewProperWbRow2, matrixApplied));\n"
        "        if (previewApplyGamutCompression > 0.5)\n"
        "        {\n"
        "            float Y = dot(previewRgbToY, wbApplied);\n"
        "            float minChannel = min(min(wbApplied.r, wbApplied.g), wbApplied.b);\n"
        "            vec3 gamutReference = vec3(-(reinhardForColour((Y != 0.0) ? ((Y - wbApplied.r) / Y) : 0.0) * Y) + Y,\n"
        "                                      -(reinhardForBlue((Y != 0.0) ? ((Y - wbApplied.g) / Y) : 0.0) * Y) + Y,\n"
        "                                      -(reinhardForBlue((Y != 0.0) ? ((Y - wbApplied.b) / Y) : 0.0) * Y) + Y);\n"
        "            float gamutMin = min(min(gamutReference.r, gamutReference.g), gamutReference.b);\n"
        "            float desaturateFactor = 1.0;\n"
        "            float denom = Y - minChannel;\n"
        "            if (Y > 0.0 && abs(denom) > 0.00000001)\n"
        "            {\n"
        "                desaturateFactor = (Y - gamutMin) / denom;\n"
        "            }\n"
        "            wbApplied = (wbApplied - vec3(Y)) * desaturateFactor + vec3(Y);\n"
        "        }\n"
        "        matrixApplied = wbApplied;\n"
        "    }\n"
        "    matrixApplied = clamp(matrixApplied, 0.0, 1.0);\n"
        "    return vec3(sampleU16Lut(gammaLut, matrixApplied.r), sampleU16Lut(gammaLut, matrixApplied.g), sampleU16Lut(gammaLut, matrixApplied.b));\n"
        "}\n"
        "vec4 applyDisplayProcessing(vec4 color)\n"
        "{\n"
        "    color.rgb = applyPreviewProcessing(color.rgb);\n"
        "    if (zebraEnabled > 0.5)\n"
        "    {\n"
        "        float maxChannel = max(max(color.r, color.g), color.b);\n"
        "        float minChannel = min(min(color.r, color.g), color.b);\n"
        "        float lightness = (maxChannel + minChannel) * 0.5;\n"
        "        if (lightness >= zebraOverThreshold)\n"
        "        {\n"
        "            return vec4(1.0, 0.0, 0.0, color.a);\n"
        "        }\n"
        "        if (lightness <= zebraUnderThreshold)\n"
        "        {\n"
        "            return vec4(0.0, 0.0, 1.0, color.a);\n"
        "        }\n"
        "    }\n"
        "    return color;\n"
        "}\n"
        "void main()\n"
        "{\n"
        "    vec4 sampledColor;\n"
        "    if (samplingMode == 2)\n"
        "    {\n"
        "        sampledColor = sampleBicubic(vTexCoord);\n"
        "    }\n"
        "    else\n"
        "    {\n"
        "        sampledColor = texture2D(frameTexture, vTexCoord);\n"
        "    }\n"
        "    gl_FragColor = applyDisplayProcessing(sampledColor);\n"
        "}\n");
}

QByteArray gpuPreviewProcessingSubsetFragmentShaderSource(void)
{
    return QByteArrayLiteral(
        "uniform sampler2D frameTexture;\n"
        "uniform sampler2D levelsLut;\n"
        "uniform sampler2D matrixLutR;\n"
        "uniform sampler2D matrixLutG;\n"
        "uniform sampler2D matrixLutB;\n"
        "uniform sampler2D gammaLut;\n"
        "uniform sampler2D contrastCurveLut;\n"
        "uniform sampler2D gradationLutY;\n"
        "uniform sampler2D gradationLutR;\n"
        "uniform sampler2D gradationLutG;\n"
        "uniform sampler2D gradationLutB;\n"
        "uniform sampler2D hueVsHueCurve;\n"
        "uniform sampler2D hueVsSaturationCurve;\n"
        "uniform sampler2D hueVsLumaCurve;\n"
        "uniform sampler2D lumaVsSaturationCurve;\n"
        "uniform sampler2D inLoopContrastCurve;\n"
        "uniform float previewProcessingEnabled;\n"
        "uniform float previewApplyCreativeCurves;\n"
        "uniform float previewApplyToning;\n"
        "uniform vec3 previewToningGain;\n"
        "uniform float previewApplyVibrance;\n"
        "uniform float previewVibrance;\n"
        "uniform float previewApplySaturation;\n"
        "uniform float previewSaturation;\n"
        "uniform float previewApplyHueVs;\n"
        "uniform float previewApplyInLoopContrast;\n"
        "uniform float previewApplyAgx;\n"
        "uniform vec3 previewAgxFwd0;\n"
        "uniform vec3 previewAgxFwd1;\n"
        "uniform vec3 previewAgxFwd2;\n"
        "uniform vec3 previewAgxInv0;\n"
        "uniform vec3 previewAgxInv1;\n"
        "uniform vec3 previewAgxInv2;\n"
        "uniform float previewUseCameraMatrix;\n"
        "uniform float previewApplyGamutCompression;\n"
        "uniform vec3 previewProperWbRow0;\n"
        "uniform vec3 previewProperWbRow1;\n"
        "uniform vec3 previewProperWbRow2;\n"
        "uniform vec3 previewRgbToY;\n"
        "varying vec2 vTexCoord;\n"
        "float sampleU16Lut(sampler2D lut, float value)\n"
        "{\n"
        "    float clamped = clamp(value, 0.0, 1.0);\n"
        "    float index = floor(clamped * 65535.0 + 0.5);\n"
        "    float x = mod(index, 256.0);\n"
        "    float y = floor(index / 256.0);\n"
        "    vec2 uv = (vec2(x, y) + vec2(0.5)) / vec2(256.0, 256.0);\n"
        "    return texture2D(lut, uv).r;\n"
        "}\n"
        "float sampleHueVsCurve(sampler2D curve, float idx)\n"
        "{\n"
        "    float i = clamp(idx, 0.0, 35999.0);\n"
        "    float x = mod(i, 256.0);\n"
        "    float y = floor(i / 256.0);\n"
        "    vec2 uv = (vec2(x, y) + vec2(0.5)) / vec2(256.0, 141.0);\n"
        "    return texture2D(curve, uv).r;\n"
        "}\n"
        "float sampleContrastCurve(sampler2D curve, float idx)\n"
        "{\n"
        "    float i = clamp(idx, 0.0, 65535.0);\n"
        "    float x = mod(i, 256.0);\n"
        "    float y = floor(i / 256.0);\n"
        "    vec2 uv = (vec2(x, y) + vec2(0.5)) / vec2(256.0, 256.0);\n"
        "    return texture2D(curve, uv).r;\n"
        "}\n"
        "vec3 truncToZero(vec3 v)\n"
        "{\n"
        "    return sign(v) * floor(abs(v));\n"
        "}\n"
        "vec3 previewFromRGBtoHSV(vec3 rgb)\n"
        "{\n"
        "    float V = max(rgb.r, max(rgb.g, rgb.b));\n"
        "    float delta = V - min(rgb.r, min(rgb.g, rgb.b));\n"
        "    float H = 0.0;\n"
        "    float S = 0.0;\n"
        "    if (delta >= 1.17549435e-38)\n"
        "    {\n"
        "        S = delta / V;\n"
        "        if (rgb.r >= V)\n"
        "        {\n"
        "            H = (rgb.g - rgb.b) / delta;\n"
        "            if (H < 0.0) H += 6.0;\n"
        "        }\n"
        "        else if (rgb.g >= V)\n"
        "        {\n"
        "            H = 2.0 + (rgb.b - rgb.r) / delta;\n"
        "        }\n"
        "        else\n"
        "        {\n"
        "            H = 4.0 + (rgb.r - rgb.g) / delta;\n"
        "        }\n"
        "    }\n"
        "    return vec3(H * 60.0, S, V);\n"
        "}\n"
        "vec3 previewFromHSVtoRGB(vec3 hsv)\n"
        "{\n"
        "    if (hsv.g < 1.17549435e-38)\n"
        "    {\n"
        "        return vec3(hsv.b);\n"
        "    }\n"
        "    float h = hsv.x / 60.0;\n"
        "    float fi = floor(h);\n"
        "    int i = int(fi);\n"
        "    float f = h - fi;\n"
        "    float p = hsv.z * (1.0 - hsv.y);\n"
        "    if (mod(fi, 2.0) >= 0.5)\n"
        "    {\n"
        "        float q = hsv.z * (1.0 - (hsv.y * f));\n"
        "        if (i == 1) return vec3(q, hsv.z, p);\n"
        "        else if (i == 3) return vec3(p, q, hsv.z);\n"
        "        return vec3(hsv.z, p, q);\n"
        "    }\n"
        "    float t = hsv.z * (1.0 - (hsv.y * (1.0 - f)));\n"
        "    if (i == 0) return vec3(hsv.z, t, p);\n"
        "    else if (i == 2) return vec3(p, hsv.z, t);\n"
        "    return vec3(t, p, hsv.z);\n"
        "}\n"
        "float reinhardTonemap(float x)\n"
        "{\n"
        "    return (x < 0.0) ? x : x / (1.0 + x);\n"
        "}\n"
        "float reinhardForColour(float x)\n"
        "{\n"
        "    return (x < 0.5) ? x : (reinhardTonemap((x - 0.5) / 0.5) * 0.5 + 0.5);\n"
        "}\n"
        "float reinhardForBlue(float x)\n"
        "{\n"
        "    return (x < 0.7) ? x : (reinhardTonemap((x - 0.7) / 0.3) * 0.3 + 0.7);\n"
        "}\n"
        "vec3 applyPreviewProcessing(vec3 color)\n"
        "{\n"
        "    if (previewProcessingEnabled <= 0.5)\n"
        "    {\n"
        "        return color;\n"
        "    }\n"
        "    vec3 leveled = vec3(sampleU16Lut(levelsLut, color.r), sampleU16Lut(levelsLut, color.g), sampleU16Lut(levelsLut, color.b));\n"
        "    vec3 matrixApplied = vec3(sampleU16Lut(matrixLutR, leveled.r), sampleU16Lut(matrixLutG, leveled.g), sampleU16Lut(matrixLutB, leveled.b));\n"
        "    if (previewApplyInLoopContrast > 0.5)\n"
        "    {\n"
        "        vec3 m16 = floor(matrixApplied * 65535.0 + 0.5);\n"
        "        float cval = floor((m16.r * 4.0 + m16.g * 11.0 + m16.b) / 16.0);\n"
        "        matrixApplied *= sampleContrastCurve(inLoopContrastCurve, cval);\n"
        "    }\n"
        "    if (previewUseCameraMatrix > 0.5)\n"
        "    {\n"
        "        vec3 wbApplied = vec3(dot(previewProperWbRow0, matrixApplied), dot(previewProperWbRow1, matrixApplied), dot(previewProperWbRow2, matrixApplied));\n"
        "        if (previewApplyGamutCompression > 0.5)\n"
        "        {\n"
        "            float Y = dot(previewRgbToY, wbApplied);\n"
        "            float minChannel = min(min(wbApplied.r, wbApplied.g), wbApplied.b);\n"
        "            vec3 gamutReference = vec3(-(reinhardForColour((Y != 0.0) ? ((Y - wbApplied.r) / Y) : 0.0) * Y) + Y,\n"
        "                                      -(reinhardForBlue((Y != 0.0) ? ((Y - wbApplied.g) / Y) : 0.0) * Y) + Y,\n"
        "                                      -(reinhardForBlue((Y != 0.0) ? ((Y - wbApplied.b) / Y) : 0.0) * Y) + Y);\n"
        "            float gamutMin = min(min(gamutReference.r, gamutReference.g), gamutReference.b);\n"
        "            float desaturateFactor = 1.0;\n"
        "            float denom = Y - minChannel;\n"
        "            if (Y > 0.0 && abs(denom) > 0.00000001)\n"
        "            {\n"
        "                desaturateFactor = (Y - gamutMin) / denom;\n"
        "            }\n"
        "            wbApplied = (wbApplied - vec3(Y)) * desaturateFactor + vec3(Y);\n"
        "        }\n"
        "        matrixApplied = wbApplied;\n"
        "    }\n"
        "    if (previewApplyAgx > 0.5)\n"
        "    {\n"
        "        vec3 v = max(matrixApplied * 65535.0, 0.0);\n"
        "        vec3 a = vec3(dot(previewAgxFwd0, v), dot(previewAgxFwd1, v), dot(previewAgxFwd2, v));\n"
        "        matrixApplied = floor(clamp(a, 0.0, 65535.0)) / 65535.0;\n"
        "    }\n"
        "    matrixApplied = clamp(matrixApplied, 0.0, 1.0);\n"
        "    vec3 result = vec3(sampleU16Lut(gammaLut, matrixApplied.r), sampleU16Lut(gammaLut, matrixApplied.g), sampleU16Lut(gammaLut, matrixApplied.b));\n"
        "    if (previewApplyHueVs > 0.5)\n"
        "    {\n"
        "        vec3 hv = result * 65535.0;\n"
        "        vec3 hsl = previewFromRGBtoHSV(result);\n"
        "        float hsat = 0.0;\n"
        "        if (!(hv.r < 0.5 && hv.g < 0.5 && hv.b < 0.5))\n"
        "        {\n"
        "            float hbig = max(max(hv.r, hv.g), hv.b);\n"
        "            float hsmall = min(min(hv.r, hv.g), hv.b);\n"
        "            hsat = (hbig > 0.0) ? (hbig - hsmall) / hbig : 0.0;\n"
        "        }\n"
        "        hsat = 2.0 * hsat / (hsat * hsat + 1.0);\n"
        "        hsat = min(hsat, 1.0);\n"
        "        float hueIndex = floor(hsl.x * 100.0);\n"
        "        hsl.z *= 1.0 + (sampleHueVsCurve(hueVsLumaCurve, hueIndex) * hsat * 2.0);\n"
        "        hsl.z = max(hsl.z, 0.0);\n"
        "        hsl.y *= 1.0 + (sampleHueVsCurve(hueVsSaturationCurve, hueIndex) * 2.0);\n"
        "        hsl.y = max(hsl.y, 0.0);\n"
        "        hsl.x += 60.0 * sampleHueVsCurve(hueVsHueCurve, hueIndex);\n"
        "        if (hsl.x < 0.0) hsl.x += 360.0;\n"
        "        else if (hsl.x >= 360.0) hsl.x -= 360.0;\n"
        "        float lumaIndex = floor(hsl.z * 36000.0);\n"
        "        hsl.y *= 1.0 + (sampleHueVsCurve(lumaVsSaturationCurve, lumaIndex) * 2.0);\n"
        "        hsl.y = max(hsl.y, 0.0);\n"
        "        vec3 hrgb = previewFromHSVtoRGB(hsl);\n"
        "        result = clamp(floor(hrgb * 65535.0 + 0.5), 0.0, 65535.0) / 65535.0;\n"
        "    }\n"
        "    if (previewApplyVibrance > 0.5)\n"
        "    {\n"
        "        vec3 vv = result * 65535.0;\n"
        "        float vibY = floor((vv.r * 4.0 + vv.g * 11.0 + vv.b) / 16.0);\n"
        "        vec3 vpix0 = truncToZero((vv - vec3(vibY)) * previewVibrance) + vec3(vibY);\n"
        "        if (previewVibrance > 1.0)\n"
        "        {\n"
        "            float vbig = max(max(vv.r, vv.g), vv.b);\n"
        "            float vsmall = min(min(vv.r, vv.g), vv.b);\n"
        "            float vs = (vbig > 0.0) ? (vbig - vsmall) / vbig : 0.0;\n"
        "            vs = 2.0 * vs / (vs * vs + 1.0);\n"
        "            vs = min(vs, 1.0);\n"
        "            result = clamp(vv * vs + vpix0 * (1.0 - vs), 0.0, 65535.0) / 65535.0;\n"
        "        }\n"
        "        else\n"
        "        {\n"
        "            result = clamp(vpix0, 0.0, 65535.0) / 65535.0;\n"
        "        }\n"
        "    }\n"
        "    if (previewApplySaturation > 0.5)\n"
        "    {\n"
        "        vec3 sv = result * 65535.0;\n"
        "        float satY = floor((sv.r * 4.0 + sv.g * 11.0 + sv.b) / 16.0);\n"
        "        result = clamp(truncToZero((sv - vec3(satY)) * previewSaturation) + vec3(satY), 0.0, 65535.0) / 65535.0;\n"
        "    }\n"
        "    if (previewApplyToning > 0.5)\n"
        "    {\n"
        "        result = clamp(truncToZero(result * 65535.0 * previewToningGain), 0.0, 65535.0) / 65535.0;\n"
        "    }\n"
        "    if (previewApplyCreativeCurves > 0.5)\n"
        "    {\n"
        "        result = vec3(sampleU16Lut(contrastCurveLut, result.r), sampleU16Lut(contrastCurveLut, result.g), sampleU16Lut(contrastCurveLut, result.b));\n"
        "        result = vec3(sampleU16Lut(gradationLutY, result.r), sampleU16Lut(gradationLutY, result.g), sampleU16Lut(gradationLutY, result.b));\n"
        "        result = vec3(sampleU16Lut(gradationLutR, result.r), sampleU16Lut(gradationLutG, result.g), sampleU16Lut(gradationLutB, result.b));\n"
        "    }\n"
        "    if (previewApplyAgx > 0.5)\n"
        "    {\n"
        "        vec3 f = result * 65535.0;\n"
        "        vec3 iv = vec3(dot(previewAgxInv0, f), dot(previewAgxInv1, f), dot(previewAgxInv2, f));\n"
        "        result = floor(clamp(iv, 0.0, 65535.0)) / 65535.0;\n"
        "    }\n"
        "    return result;\n"
        "}\n"
        "void main()\n"
        "{\n"
        "    vec2 sourceCoord = vec2(vTexCoord.x, 1.0 - vTexCoord.y);\n"
        "    vec4 sampledColor = texture2D(frameTexture, sourceCoord);\n"
        "    gl_FragColor = vec4(applyPreviewProcessing(sampledColor.rgb), sampledColor.a);\n"
        "}\n");
}

QByteArray gpuPreviewProcessingPackLookupTextureRgba16(const QByteArray & sourceLut)
{
    QByteArray packed(kLutTextureEdge * kLutTextureEdge * 4 * static_cast<int>(sizeof(uint16_t)),
                      Qt::Uninitialized);
    std::memset(packed.data(), 0, static_cast<size_t>(packed.size()));
    if ( sourceLut.size() < static_cast<int>(65536u * sizeof(uint16_t)) )
    {
        return packed;
    }

    const uint16_t * sourceValues = reinterpret_cast<const uint16_t *>(sourceLut.constData());
    uint16_t * destValues = reinterpret_cast<uint16_t *>(packed.data());
    for (int index = 0; index < 65536; ++index)
    {
        const uint16_t value = sourceValues[index];
        destValues[index * 4 + 0] = value;
        destValues[index * 4 + 1] = value;
        destValues[index * 4 + 2] = value;
        destValues[index * 4 + 3] = 65535;
    }
    return packed;
}

bool gpuPreviewProcessingRendererIsSoftware(const QString & rendererDescription)
{
    const QString normalized = rendererDescription.trimmed().toLower();
    return normalized.contains(QStringLiteral("llvmpipe"))
        || normalized.contains(QStringLiteral("softpipe"))
        || normalized.contains(QStringLiteral("software rasterizer"))
        || normalized.contains(QStringLiteral("software renderer"))
        || normalized.contains(QStringLiteral("swiftshader"))
        || normalized.contains(QStringLiteral("warp"))
        || normalized.contains(QStringLiteral("microsoft basic render"))
        || normalized.contains(QStringLiteral("gdi generic"));
}

bool gpuPreviewProcessingIsSupported(const processingObject_t * processing,
                                     QString * reason)
{
    auto reject = [&](const QString & why) -> bool
    {
        if ( reason ) *reason = why;
        return false;
    };

    if ( !processing ) return reject(QStringLiteral("processing object missing"));
    if ( processing->highlight_reconstruction ) return reject(QStringLiteral("highlight reconstruction enabled"));
    /* allow_creative_adjustments no longer has its own reject: the GPU subset
     * shader ports every creative-family stage -- the in-loop simple-contrast
     * factor, hue-vs / luma-vs curves, vibrance, saturation, toning, the contrast
     * curve (pre_calc_curve_r) and the gradation curves (gcurve_*). Clips are
     * still failed closed below on the non-creative features the shader does not
     * implement (gradient, LUT, filter, AgX, denoise, grain, CA, sharpen, chroma
     * separation/blur, clarity, shadows/highlights, vignette, non-Rec709 gamut),
     * which are gated independently of the creative flag. */
    if ( processing->gradient_enable ) return reject(QStringLiteral("gradient enabled"));
    if ( processing->lut_on ) return reject(QStringLiteral("LUT enabled"));
    if ( processing->filter_on ) return reject(QStringLiteral("filter enabled"));
    /* AgX is now supported: a forward compressed-gamut matmul before gamma and
     * the inverse after the creative curves, carried as uniform matrices (no new
     * textures). No reject. */
    /* EXR/cyan-highlight mode is compatible with the preview subset as long as
     * gamut compression stays disabled. The config builder already derives that
     * via applyGamutCompression = false, so do not reject it here. */
    if ( processing->denoiserStrength > 0 ) return reject(QStringLiteral("median denoiser enabled"));
    if ( processing->rbfDenoiserLuma > 0 || processing->rbfDenoiserChroma > 0 ) return reject(QStringLiteral("RBF denoiser enabled"));
    if ( processing->grainStrength > 0 ) return reject(QStringLiteral("grain enabled"));
    if ( processing->ca_desaturate > 0 ) return reject(QStringLiteral("CA correction enabled"));
    if ( processing->sharpen > 0.005 ) return reject(QStringLiteral("sharpening enabled"));
    if ( processing->cs_zone.use_cs ) return reject(QStringLiteral("chroma separation enabled"));
    if ( processing->cs_zone.chroma_blur_radius > 0 ) return reject(QStringLiteral("chroma blur enabled"));
    if ( std::fabs(processing->clarity) >= 0.01 ) return reject(QStringLiteral("clarity enabled"));
    if ( std::fabs(processing->shadows_highlights.shadows) >= 0.01
      || std::fabs(processing->shadows_highlights.highlights) >= 0.01 )
    {
        return reject(QStringLiteral("shadows/highlights enabled"));
    }
    if ( processing->vignette_strength != 0 ) return reject(QStringLiteral("vignette enabled"));
    /* Non-Rec709 gamut is now supported: the gamut is baked into proper_wb_matrix
     * (already applied in-shader) and the gamut-compression luma weights are
     * derived per-gamut in gpuPreviewProcessingBuildConfig (rgbToY). No reject. */

    if ( reason ) reason->clear();
    return true;
}

GpuPreviewProcessingConfig gpuPreviewProcessingBuildConfig(
    const processingObject_t * processing,
    QString * reason)
{
    GpuPreviewProcessingConfig config;
    if ( !gpuPreviewProcessingIsSupported(processing, reason) )
    {
        return config;
    }

    config.enabled = true;
    config.useCameraMatrix = processing->use_cam_matrix > 0;
    config.applyGamutCompression = config.useCameraMatrix && !processing->exr_mode;
    /* Exposure for the supported subset is preserved through the copied LUTs:
     * negative exposure is already folded into pre_calc_matrix, while positive
     * exposure is already folded into pre_calc_gamma. Keep the source value in
     * the config so tests and future readers can verify that relationship. */
    config.sourceExposureStops = static_cast<float>(processing->exposure_stops);
    for (int index = 0; index < 9; ++index)
    {
        config.properWbMatrix[index] = static_cast<float>(processing->proper_wb_matrix[index]);
    }
    /* Gamut-compression desaturation weights. raw_processing derives rgb_to_Y
     * per gamut (second row of inverse(gamut matrix), raw_processing.c ~2680).
     * Rec709 keeps the exact hardcoded constants so the validated golden output
     * is byte-stable; other gamuts use the engine-derived weights so the GPU
     * gamut-compression path matches production. The matrix itself already
     * carries the gamut via proper_wb_matrix. */
    if ( processing->colour_gamut == GAMUT_Rec709 )
    {
        std::memcpy(config.rgbToY, kRec709RgbToY, sizeof(config.rgbToY));
    }
    else
    {
        double gamutRgbToY[3] = { 0.0, 0.0, 0.0 };
        processingGamutRgbToY(processing->colour_gamut, gamutRgbToY);
        for (int index = 0; index < 3; ++index)
        {
            config.rgbToY[index] = static_cast<float>(gamutRgbToY[index]);
        }
    }
    config.levelsLut = QByteArray(
        reinterpret_cast<const char *>(processing->pre_calc_levels),
        static_cast<int>(65536u * sizeof(uint16_t)));
    config.matrixLutR.resize(static_cast<int>(65536u * sizeof(uint16_t)));
    config.matrixLutG.resize(static_cast<int>(65536u * sizeof(uint16_t)));
    config.matrixLutB.resize(static_cast<int>(65536u * sizeof(uint16_t)));
    uint16_t * matrixR = reinterpret_cast<uint16_t *>(config.matrixLutR.data());
    uint16_t * matrixG = reinterpret_cast<uint16_t *>(config.matrixLutG.data());
    uint16_t * matrixB = reinterpret_cast<uint16_t *>(config.matrixLutB.data());
    for (int index = 0; index < 65536; ++index)
    {
        matrixR[index] = static_cast<uint16_t>(qBound(0, processing->pre_calc_matrix[0][index], 65535));
        matrixG[index] = static_cast<uint16_t>(qBound(0, processing->pre_calc_matrix[4][index], 65535));
        matrixB[index] = static_cast<uint16_t>(qBound(0, processing->pre_calc_matrix[8][index], 65535));
    }
    config.gammaLut = QByteArray(
        reinterpret_cast<const char *>(processing->pre_calc_gamma),
        static_cast<int>(65536u * sizeof(uint16_t)));

    /* Slice 1: post-gamma creative curves (contrast + gradation) ported to the
     * GPU subset shader. gpuPreviewProcessingIsSupported only accepts
     * allow_creative_adjustments when the not-yet-ported creative stages are
     * neutral, so these prebuilt uint16[65536] LUTs are safe to apply verbatim.
     * Order matches raw_processing.c:3696-3738. */
    config.applyCreativeCurves = processing->allow_creative_adjustments != 0;
    config.applyToning = processing->allow_creative_adjustments != 0
                      && (processing->toning_dry < 0.998f
                          || std::fabs(processing->toning_wet[0]) > 0.0005f
                          || std::fabs(processing->toning_wet[1]) > 0.0005f
                          || std::fabs(processing->toning_wet[2]) > 0.0005f);
    config.toningGain[0] = static_cast<float>(processing->toning_dry + processing->toning_wet[0]);
    config.toningGain[1] = static_cast<float>(processing->toning_dry + processing->toning_wet[1]);
    config.toningGain[2] = static_cast<float>(processing->toning_dry + processing->toning_wet[2]);
    config.applyVibrance = processing->allow_creative_adjustments != 0
                        && (processing->vibrance > 1.01 || processing->vibrance < 0.99);
    config.vibrance = static_cast<float>(processing->vibrance);
    config.applySaturation = processing->allow_creative_adjustments != 0
                          && (processing->saturation > 1.01 || processing->saturation < 0.99);
    config.saturation = static_cast<float>(processing->saturation);
    config.applyHueVs = processing->allow_creative_adjustments != 0
                     && (processing->hue_vs_hue_used
                      || processing->hue_vs_saturation_used
                      || processing->hue_vs_luma_used
                      || processing->luma_vs_saturation_used);
    if ( config.applyHueVs )
    {
        /* hue-vs / luma-vs curves are float[36000] in [-1,1]; copied verbatim so
         * the CPU reference and the R32F GPU textures share the exact values
         * (raw_processing.c:3523-3578). */
        const int curveBytes = static_cast<int>(36000u * sizeof(float));
        config.hueVsHueCurve = QByteArray(
            reinterpret_cast<const char *>(processing->hue_vs_hue), curveBytes);
        config.hueVsSaturationCurve = QByteArray(
            reinterpret_cast<const char *>(processing->hue_vs_saturation), curveBytes);
        config.hueVsLumaCurve = QByteArray(
            reinterpret_cast<const char *>(processing->hue_vs_luma), curveBytes);
        config.lumaVsSaturationCurve = QByteArray(
            reinterpret_cast<const char *>(processing->luma_vs_saturation), curveBytes);
    }
    config.applyInLoopContrast = processing->allow_creative_adjustments != 0
                              && std::fabs(processing->contrast) >= 0.01;
    config.sourceContrast = static_cast<float>(processing->contrast);
    if ( config.applyInLoopContrast )
    {
        /* contrast_curve is double[65536] per-luma exposure factors; narrow to
         * float[65536] so the GPU R32F texture and the CPU reference share the
         * exact same values (raw_processing.c:2954). */
        config.inLoopContrastCurve.resize(static_cast<int>(65536u * sizeof(float)));
        float * dst = reinterpret_cast<float *>(config.inLoopContrastCurve.data());
        for (int index = 0; index < 65536; ++index)
        {
            dst[index] = static_cast<float>(processing->contrast_curve[index]);
        }
    }
    config.applyAgx = processing->AgX != 0;
    if ( config.applyAgx )
    {
        /* AgX is a forward compressed-gamut matmul before gamma + the inverse
         * after the creative curves (raw_processing_8bit_kernel.inc:264-281 /
         * 358-368). Carry float-narrowed copies of the engine's double matrices;
         * +/-1 LSB vs the production double path, within the parity tolerance. */
        double fwd[9] = { 0.0 };
        double inv[9] = { 0.0 };
        processingAgxMatrices(fwd, inv);
        for (int index = 0; index < 9; ++index)
        {
            config.agxForward[index] = static_cast<float>(fwd[index]);
            config.agxInverse[index] = static_cast<float>(inv[index]);
        }
    }
    if ( config.applyCreativeCurves )
    {
        config.contrastCurveLut = QByteArray(
            reinterpret_cast<const char *>(processing->pre_calc_curve_r),
            static_cast<int>(65536u * sizeof(uint16_t)));
        config.gradationLutY = QByteArray(
            reinterpret_cast<const char *>(processing->gcurve_y),
            static_cast<int>(65536u * sizeof(uint16_t)));
        config.gradationLutR = QByteArray(
            reinterpret_cast<const char *>(processing->gcurve_r),
            static_cast<int>(65536u * sizeof(uint16_t)));
        config.gradationLutG = QByteArray(
            reinterpret_cast<const char *>(processing->gcurve_g),
            static_cast<int>(65536u * sizeof(uint16_t)));
        config.gradationLutB = QByteArray(
            reinterpret_cast<const char *>(processing->gcurve_b),
            static_cast<int>(65536u * sizeof(uint16_t)));
    }

    uint64_t hash = 1469598103934665603ull;
    hash = fnv1a64_append(hash, &config.useCameraMatrix, sizeof(config.useCameraMatrix));
    hash = fnv1a64_append(hash, &config.applyGamutCompression, sizeof(config.applyGamutCompression));
    hash = fnv1a64_append(hash, &config.sourceExposureStops, sizeof(config.sourceExposureStops));
    hash = fnv1a64_append(hash, config.properWbMatrix, sizeof(config.properWbMatrix));
    hash = fnv1a64_append(hash, config.rgbToY, sizeof(config.rgbToY));
    hash = fnv1a64_append(hash, config.levelsLut.constData(), static_cast<size_t>(config.levelsLut.size()));
    hash = fnv1a64_append(hash, config.matrixLutR.constData(), static_cast<size_t>(config.matrixLutR.size()));
    hash = fnv1a64_append(hash, config.matrixLutG.constData(), static_cast<size_t>(config.matrixLutG.size()));
    hash = fnv1a64_append(hash, config.matrixLutB.constData(), static_cast<size_t>(config.matrixLutB.size()));
    hash = fnv1a64_append(hash, config.gammaLut.constData(), static_cast<size_t>(config.gammaLut.size()));
    hash = fnv1a64_append(hash, &config.applyCreativeCurves, sizeof(config.applyCreativeCurves));
    hash = fnv1a64_append(hash, &config.applyToning, sizeof(config.applyToning));
    hash = fnv1a64_append(hash, config.toningGain, sizeof(config.toningGain));
    hash = fnv1a64_append(hash, &config.applyVibrance, sizeof(config.applyVibrance));
    hash = fnv1a64_append(hash, &config.vibrance, sizeof(config.vibrance));
    hash = fnv1a64_append(hash, &config.applySaturation, sizeof(config.applySaturation));
    hash = fnv1a64_append(hash, &config.saturation, sizeof(config.saturation));
    hash = fnv1a64_append(hash, &config.applyHueVs, sizeof(config.applyHueVs));
    hash = fnv1a64_append(hash, &config.applyInLoopContrast, sizeof(config.applyInLoopContrast));
    hash = fnv1a64_append(hash, &config.sourceContrast, sizeof(config.sourceContrast));
    hash = fnv1a64_append(hash, &config.applyAgx, sizeof(config.applyAgx));
    hash = fnv1a64_append(hash, config.agxForward, sizeof(config.agxForward));
    hash = fnv1a64_append(hash, config.agxInverse, sizeof(config.agxInverse));
    hash = fnv1a64_append(hash, config.inLoopContrastCurve.constData(), static_cast<size_t>(config.inLoopContrastCurve.size()));
    hash = fnv1a64_append(hash, config.hueVsHueCurve.constData(), static_cast<size_t>(config.hueVsHueCurve.size()));
    hash = fnv1a64_append(hash, config.hueVsSaturationCurve.constData(), static_cast<size_t>(config.hueVsSaturationCurve.size()));
    hash = fnv1a64_append(hash, config.hueVsLumaCurve.constData(), static_cast<size_t>(config.hueVsLumaCurve.size()));
    hash = fnv1a64_append(hash, config.lumaVsSaturationCurve.constData(), static_cast<size_t>(config.lumaVsSaturationCurve.size()));
    hash = fnv1a64_append(hash, config.contrastCurveLut.constData(), static_cast<size_t>(config.contrastCurveLut.size()));
    hash = fnv1a64_append(hash, config.gradationLutY.constData(), static_cast<size_t>(config.gradationLutY.size()));
    hash = fnv1a64_append(hash, config.gradationLutR.constData(), static_cast<size_t>(config.gradationLutR.size()));
    hash = fnv1a64_append(hash, config.gradationLutG.constData(), static_cast<size_t>(config.gradationLutG.size()));
    hash = fnv1a64_append(hash, config.gradationLutB.constData(), static_cast<size_t>(config.gradationLutB.size()));
    config.signature = hash;
    return config;
}

GpuPreviewProcessingBackendAvailability gpuPreviewProcessingProbeGpuBackend(void)
{
    GpuPreviewProcessingBackendAvailability availability;
    QOffscreenSurface surface;
    QOpenGLContext context;
    QOpenGLFunctions * glFunctions = nullptr;
    if ( !makePreviewProcessingContextCurrent(&surface,
                                              &context,
                                              &glFunctions,
                                              &availability.reason,
                                              &availability.rendererDescription) )
    {
        return availability;
    }

    QOpenGLShaderProgram program;
    QString shaderReason;
    const bool shaderReady = buildSubsetProgram(&program, &shaderReason);
    context.doneCurrent();
    if ( !shaderReady )
    {
        availability.reason = shaderReason;
        return availability;
    }

    availability.available = true;
    availability.reason.clear();
    return availability;
}

void gpuPreviewProcessingApplyCpuReference(const GpuPreviewProcessingConfig & config,
                                           const uint16_t * inputRgb16,
                                           uint16_t * outputRgb16,
                                           int pixelCount)
{
    if ( !inputRgb16 || !outputRgb16 || pixelCount <= 0 )
    {
        return;
    }

    if ( !config.enabled )
    {
        if ( inputRgb16 != outputRgb16 )
        {
            std::memcpy(outputRgb16,
                        inputRgb16,
                        static_cast<size_t>(pixelCount) * 3u * sizeof(uint16_t));
        }
        return;
    }

    #pragma omp parallel for if(pixelCount >= 2048)
    for (int pixelIndex = 0; pixelIndex < pixelCount; ++pixelIndex)
    {
        const uint16_t * inputPixel = inputRgb16 + pixelIndex * 3;
        uint16_t * outputPixel = outputRgb16 + pixelIndex * 3;
        applyPreviewProcessingPixel(config, inputPixel, outputPixel);
    }
}

bool gpuPreviewProcessingApplyGpuOffscreen(const GpuPreviewProcessingConfig & config,
                                           const uint16_t * inputRgb16,
                                           uint16_t * outputRgb16,
                                           int width,
                                           int height,
                                           QString * reason,
                                           QString * rendererDescription)
{
    auto fail = [&](const QString & why) -> bool
    {
        if ( reason ) *reason = why;
        return false;
    };

    if ( !inputRgb16 || !outputRgb16 || width <= 0 || height <= 0 )
    {
        return fail(QStringLiteral("preview-processing GPU offscreen input/output buffers are invalid"));
    }

    if ( !config.enabled )
    {
        const int pixelCount = width * height;
        if ( inputRgb16 != outputRgb16 )
        {
            std::memcpy(outputRgb16,
                        inputRgb16,
                        static_cast<size_t>(pixelCount) * 3u * sizeof(uint16_t));
        }
        if ( reason ) reason->clear();
        return true;
    }

    QOffscreenSurface surface;
    QOpenGLContext context;
    QOpenGLFunctions * glFunctions = nullptr;
    if ( !makePreviewProcessingContextCurrent(&surface,
                                              &context,
                                              &glFunctions,
                                              reason,
                                              rendererDescription) )
    {
        return false;
    }

    QOpenGLFramebufferObjectFormat fboFormat;
    fboFormat.setAttachment(QOpenGLFramebufferObject::NoAttachment);
    fboFormat.setTextureTarget(GL_TEXTURE_2D);
    fboFormat.setInternalTextureFormat(GL_RGBA16);
    QOpenGLFramebufferObject fbo(width, height, fboFormat);
    if ( !fbo.isValid() )
    {
        context.doneCurrent();
        return fail(QStringLiteral("QOpenGLFramebufferObject creation failed"));
    }

    QOpenGLShaderProgram program;
    if ( !buildSubsetProgram(&program, reason) )
    {
        context.doneCurrent();
        return false;
    }

    const QByteArray packedFrame = packRgb16Texture(inputRgb16, width * height);
    const QByteArray levelsBytes = gpuPreviewProcessingPackLookupTextureRgba16(config.levelsLut);
    const QByteArray matrixRBytes = gpuPreviewProcessingPackLookupTextureRgba16(config.matrixLutR);
    const QByteArray matrixGBytes = gpuPreviewProcessingPackLookupTextureRgba16(config.matrixLutG);
    const QByteArray matrixBBytes = gpuPreviewProcessingPackLookupTextureRgba16(config.matrixLutB);
    const QByteArray gammaBytes = gpuPreviewProcessingPackLookupTextureRgba16(config.gammaLut);
    const QByteArray contrastBytes = gpuPreviewProcessingPackLookupTextureRgba16(
        config.applyCreativeCurves ? config.contrastCurveLut : config.gammaLut);
    const QByteArray gradationYBytes = gpuPreviewProcessingPackLookupTextureRgba16(
        config.applyCreativeCurves ? config.gradationLutY : config.gammaLut);
    const QByteArray gradationRBytes = gpuPreviewProcessingPackLookupTextureRgba16(
        config.applyCreativeCurves ? config.gradationLutR : config.gammaLut);
    const QByteArray gradationGBytes = gpuPreviewProcessingPackLookupTextureRgba16(
        config.applyCreativeCurves ? config.gradationLutG : config.gammaLut);
    const QByteArray gradationBBytes = gpuPreviewProcessingPackLookupTextureRgba16(
        config.applyCreativeCurves ? config.gradationLutB : config.gammaLut);
    const QByteArray hueVsHueBytes = packCurveTextureR32F(
        config.applyHueVs ? config.hueVsHueCurve : QByteArray());
    const QByteArray hueVsSaturationBytes = packCurveTextureR32F(
        config.applyHueVs ? config.hueVsSaturationCurve : QByteArray());
    const QByteArray hueVsLumaBytes = packCurveTextureR32F(
        config.applyHueVs ? config.hueVsLumaCurve : QByteArray());
    const QByteArray lumaVsSaturationBytes = packCurveTextureR32F(
        config.applyHueVs ? config.lumaVsSaturationCurve : QByteArray());
    const QByteArray inLoopContrastBytes = packContrastCurveR32F(
        config.applyInLoopContrast ? config.inLoopContrastCurve : QByteArray());

    QOpenGLTexture * frameTexture = createFrameTexture(width, height);
    QOpenGLTexture * levelsTexture = createLookupTexture();
    QOpenGLTexture * matrixRTexture = createLookupTexture();
    QOpenGLTexture * matrixGTexture = createLookupTexture();
    QOpenGLTexture * matrixBTexture = createLookupTexture();
    QOpenGLTexture * gammaTexture = createLookupTexture();
    QOpenGLTexture * contrastCurveTexture = createLookupTexture();
    QOpenGLTexture * gradationYTexture = createLookupTexture();
    QOpenGLTexture * gradationRTexture = createLookupTexture();
    QOpenGLTexture * gradationGTexture = createLookupTexture();
    QOpenGLTexture * gradationBTexture = createLookupTexture();
    QOpenGLTexture * hueVsHueTexture = createCurveTexture();
    QOpenGLTexture * hueVsSaturationTexture = createCurveTexture();
    QOpenGLTexture * hueVsLumaTexture = createCurveTexture();
    QOpenGLTexture * lumaVsSaturationTexture = createCurveTexture();
    QOpenGLTexture * inLoopContrastTexture = createContrastCurveTexture();

    frameTexture->setData(QOpenGLTexture::RGBA,
                          QOpenGLTexture::UInt16,
                          packedFrame.constData());
    levelsTexture->setData(QOpenGLTexture::RGBA,
                           QOpenGLTexture::UInt16,
                           levelsBytes.constData());
    matrixRTexture->setData(QOpenGLTexture::RGBA,
                            QOpenGLTexture::UInt16,
                            matrixRBytes.constData());
    matrixGTexture->setData(QOpenGLTexture::RGBA,
                            QOpenGLTexture::UInt16,
                            matrixGBytes.constData());
    matrixBTexture->setData(QOpenGLTexture::RGBA,
                            QOpenGLTexture::UInt16,
                            matrixBBytes.constData());
    gammaTexture->setData(QOpenGLTexture::RGBA,
                          QOpenGLTexture::UInt16,
                          gammaBytes.constData());
    contrastCurveTexture->setData(QOpenGLTexture::RGBA, QOpenGLTexture::UInt16, contrastBytes.constData());
    gradationYTexture->setData(QOpenGLTexture::RGBA, QOpenGLTexture::UInt16, gradationYBytes.constData());
    gradationRTexture->setData(QOpenGLTexture::RGBA, QOpenGLTexture::UInt16, gradationRBytes.constData());
    gradationGTexture->setData(QOpenGLTexture::RGBA, QOpenGLTexture::UInt16, gradationGBytes.constData());
    gradationBTexture->setData(QOpenGLTexture::RGBA, QOpenGLTexture::UInt16, gradationBBytes.constData());
    hueVsHueTexture->setData(QOpenGLTexture::Red, QOpenGLTexture::Float32, hueVsHueBytes.constData());
    hueVsSaturationTexture->setData(QOpenGLTexture::Red, QOpenGLTexture::Float32, hueVsSaturationBytes.constData());
    hueVsLumaTexture->setData(QOpenGLTexture::Red, QOpenGLTexture::Float32, hueVsLumaBytes.constData());
    lumaVsSaturationTexture->setData(QOpenGLTexture::Red, QOpenGLTexture::Float32, lumaVsSaturationBytes.constData());
    inLoopContrastTexture->setData(QOpenGLTexture::Red, QOpenGLTexture::Float32, inLoopContrastBytes.constData());

    fbo.bind();
    glFunctions->glViewport(0, 0, width, height);
    glFunctions->glDisable(GL_DEPTH_TEST);
    glFunctions->glDisable(GL_BLEND);
    glFunctions->glClearColor(0.0f, 0.0f, 0.0f, 1.0f);
    glFunctions->glClear(GL_COLOR_BUFFER_BIT);

    program.bind();
    program.setUniformValue("frameTexture", 0);
    program.setUniformValue("levelsLut", 1);
    program.setUniformValue("matrixLutR", 2);
    program.setUniformValue("matrixLutG", 3);
    program.setUniformValue("matrixLutB", 4);
    program.setUniformValue("gammaLut", 5);
    program.setUniformValue("contrastCurveLut", 6);
    program.setUniformValue("gradationLutY", 7);
    program.setUniformValue("gradationLutR", 8);
    program.setUniformValue("gradationLutG", 9);
    program.setUniformValue("gradationLutB", 10);
    program.setUniformValue("hueVsHueCurve", 11);
    program.setUniformValue("hueVsSaturationCurve", 12);
    program.setUniformValue("hueVsLumaCurve", 13);
    program.setUniformValue("lumaVsSaturationCurve", 14);
    program.setUniformValue("inLoopContrastCurve", 15);
    program.setUniformValue("previewApplyHueVs", config.applyHueVs ? 1.0f : 0.0f);
    program.setUniformValue("previewApplyInLoopContrast", config.applyInLoopContrast ? 1.0f : 0.0f);
    program.setUniformValue("previewApplyAgx", config.applyAgx ? 1.0f : 0.0f);
    program.setUniformValue("previewAgxFwd0", QVector3D(config.agxForward[0], config.agxForward[1], config.agxForward[2]));
    program.setUniformValue("previewAgxFwd1", QVector3D(config.agxForward[3], config.agxForward[4], config.agxForward[5]));
    program.setUniformValue("previewAgxFwd2", QVector3D(config.agxForward[6], config.agxForward[7], config.agxForward[8]));
    program.setUniformValue("previewAgxInv0", QVector3D(config.agxInverse[0], config.agxInverse[1], config.agxInverse[2]));
    program.setUniformValue("previewAgxInv1", QVector3D(config.agxInverse[3], config.agxInverse[4], config.agxInverse[5]));
    program.setUniformValue("previewAgxInv2", QVector3D(config.agxInverse[6], config.agxInverse[7], config.agxInverse[8]));
    program.setUniformValue("previewApplyCreativeCurves", config.applyCreativeCurves ? 1.0f : 0.0f);
    program.setUniformValue("previewApplyToning", config.applyToning ? 1.0f : 0.0f);
    program.setUniformValue("previewToningGain",
                            QVector3D(config.toningGain[0], config.toningGain[1], config.toningGain[2]));
    program.setUniformValue("previewApplyVibrance", config.applyVibrance ? 1.0f : 0.0f);
    program.setUniformValue("previewVibrance", config.vibrance);
    program.setUniformValue("previewApplySaturation", config.applySaturation ? 1.0f : 0.0f);
    program.setUniformValue("previewSaturation", config.saturation);
    program.setUniformValue("previewProcessingEnabled", config.enabled ? 1.0f : 0.0f);
    program.setUniformValue("previewUseCameraMatrix", config.useCameraMatrix ? 1.0f : 0.0f);
    program.setUniformValue("previewApplyGamutCompression", config.applyGamutCompression ? 1.0f : 0.0f);
    program.setUniformValue("previewProperWbRow0",
                            QVector3D(config.properWbMatrix[0],
                                      config.properWbMatrix[1],
                                      config.properWbMatrix[2]));
    program.setUniformValue("previewProperWbRow1",
                            QVector3D(config.properWbMatrix[3],
                                      config.properWbMatrix[4],
                                      config.properWbMatrix[5]));
    program.setUniformValue("previewProperWbRow2",
                            QVector3D(config.properWbMatrix[6],
                                      config.properWbMatrix[7],
                                      config.properWbMatrix[8]));
    program.setUniformValue("previewRgbToY",
                            QVector3D(config.rgbToY[0],
                                      config.rgbToY[1],
                                      config.rgbToY[2]));

    frameTexture->bind(0);
    levelsTexture->bind(1);
    matrixRTexture->bind(2);
    matrixGTexture->bind(3);
    matrixBTexture->bind(4);
    gammaTexture->bind(5);
    contrastCurveTexture->bind(6);
    gradationYTexture->bind(7);
    gradationRTexture->bind(8);
    gradationGTexture->bind(9);
    gradationBTexture->bind(10);
    hueVsHueTexture->bind(11);
    hueVsSaturationTexture->bind(12);
    hueVsLumaTexture->bind(13);
    lumaVsSaturationTexture->bind(14);
    inLoopContrastTexture->bind(15);

    const int posLoc = program.attributeLocation("position");
    const int texLoc = program.attributeLocation("texCoord");
    program.enableAttributeArray(posLoc);
    program.enableAttributeArray(texLoc);
    program.setAttributeArray(posLoc, GL_FLOAT, kQuadVertices, 2, 4 * sizeof(GLfloat));
    program.setAttributeArray(texLoc, GL_FLOAT, kQuadVertices + 2, 2, 4 * sizeof(GLfloat));
    glFunctions->glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    glFunctions->glFinish();

    QByteArray readback(static_cast<int>(width * height * 4u * sizeof(uint16_t)), Qt::Uninitialized);
    glFunctions->glReadPixels(0,
                              0,
                              width,
                              height,
                              GL_RGBA,
                              GL_UNSIGNED_SHORT,
                              readback.data());

    unpackRgb16Readback(readback, outputRgb16, width, height);

    program.disableAttributeArray(posLoc);
    program.disableAttributeArray(texLoc);
    frameTexture->release();
    levelsTexture->release();
    matrixRTexture->release();
    matrixGTexture->release();
    matrixBTexture->release();
    gammaTexture->release();
    contrastCurveTexture->release();
    gradationYTexture->release();
    gradationRTexture->release();
    gradationGTexture->release();
    gradationBTexture->release();
    hueVsHueTexture->release();
    hueVsSaturationTexture->release();
    hueVsLumaTexture->release();
    lumaVsSaturationTexture->release();
    inLoopContrastTexture->release();
    program.release();
    fbo.release();

    delete frameTexture;
    delete levelsTexture;
    delete matrixRTexture;
    delete matrixGTexture;
    delete matrixBTexture;
    delete gammaTexture;
    delete contrastCurveTexture;
    delete gradationYTexture;
    delete gradationRTexture;
    delete gradationGTexture;
    delete gradationBTexture;
    delete hueVsHueTexture;
    delete hueVsSaturationTexture;
    delete hueVsLumaTexture;
    delete lumaVsSaturationTexture;
    delete inLoopContrastTexture;
    context.doneCurrent();

    if ( reason ) reason->clear();
    return true;
}
