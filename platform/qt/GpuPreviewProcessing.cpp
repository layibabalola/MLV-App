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

    for (int channel = 0; channel < 3; ++channel)
    {
        const float gammaInput = clamp01(matrixApplied[channel]);
        outputPixel[channel] = sampleLut(config.gammaLut, gammaInput);
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

    if ( gpuPreviewProcessingRendererIsSoftware(renderer) )
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
        "            if (Y > 0.0 && abs(denom) > 0.000001)\n"
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
        "uniform float previewProcessingEnabled;\n"
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
        "            if (Y > 0.0 && abs(denom) > 0.000001)\n"
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
        "void main()\n"
        "{\n"
        "    vec4 sampledColor = texture2D(frameTexture, vTexCoord);\n"
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
    if ( processing->allow_creative_adjustments ) return reject(QStringLiteral("creative adjustments enabled"));
    if ( processing->gradient_enable ) return reject(QStringLiteral("gradient enabled"));
    if ( processing->lut_on ) return reject(QStringLiteral("LUT enabled"));
    if ( processing->filter_on ) return reject(QStringLiteral("filter enabled"));
    if ( processing->AgX ) return reject(QStringLiteral("AgX enabled"));
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
    if ( processing->colour_gamut != GAMUT_Rec709 ) return reject(QStringLiteral("unsupported gamut"));

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
    std::memcpy(config.rgbToY, kRec709RgbToY, sizeof(config.rgbToY));
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

    QOpenGLTexture * frameTexture = createFrameTexture(width, height);
    QOpenGLTexture * levelsTexture = createLookupTexture();
    QOpenGLTexture * matrixRTexture = createLookupTexture();
    QOpenGLTexture * matrixGTexture = createLookupTexture();
    QOpenGLTexture * matrixBTexture = createLookupTexture();
    QOpenGLTexture * gammaTexture = createLookupTexture();

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
    program.release();
    fbo.release();
    context.doneCurrent();

    delete frameTexture;
    delete levelsTexture;
    delete matrixRTexture;
    delete matrixGTexture;
    delete matrixBTexture;
    delete gammaTexture;

    if ( reason ) reason->clear();
    return true;
}
