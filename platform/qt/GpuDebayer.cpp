#include "GpuDebayer.h"

#include "GpuPreviewProcessing.h"

#include <QByteArray>
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

#include <cstring>

namespace
{
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

QSurfaceFormat debayerSurfaceFormat()
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

QOpenGLTexture * createRawTexture(int width, int height)
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

QByteArray packRawFrameTexture(const float * inputRawFrame, int pixelCount)
{
    QByteArray packed(static_cast<int>(pixelCount * 4u * sizeof(uint16_t)), Qt::Uninitialized);
    uint16_t * packedValues = reinterpret_cast<uint16_t *>(packed.data());
    for (int pixelIndex = 0; pixelIndex < pixelCount; ++pixelIndex)
    {
        const int clamped = qBound(0,
                                   static_cast<int>(inputRawFrame[pixelIndex] + 0.5f),
                                   65535);
        packedValues[pixelIndex * 4 + 0] = static_cast<uint16_t>(clamped);
        packedValues[pixelIndex * 4 + 1] = 0;
        packedValues[pixelIndex * 4 + 2] = 0;
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

QByteArray gpuBilinearDebayerFragmentShaderSource(void)
{
    return QByteArrayLiteral(
        "uniform sampler2D rawTexture;\n"
        "uniform vec2 textureSize;\n"
        "varying vec2 vTexCoord;\n"
        "float sampleRaw(vec2 logicalPixel)\n"
        "{\n"
        "    vec2 clamped = clamp(logicalPixel, vec2(0.0), textureSize - vec2(1.0));\n"
        "    vec2 uv = vec2(clamped.x + 0.5, textureSize.y - clamped.y - 0.5) / textureSize;\n"
        "    return texture2D(rawTexture, uv).r;\n"
        "}\n"
        "vec3 debayerBilinear(vec2 outputPixel)\n"
        "{\n"
        "    vec2 corePixel = clamp(outputPixel, vec2(1.0), textureSize - vec2(2.0));\n"
        "    float xParity = mod(corePixel.x, 2.0);\n"
        "    float yParity = mod(corePixel.y, 2.0);\n"
        "    float raw = sampleRaw(corePixel);\n"
        "    float up = sampleRaw(corePixel + vec2(0.0, -1.0));\n"
        "    float down = sampleRaw(corePixel + vec2(0.0, 1.0));\n"
        "    float left = sampleRaw(corePixel + vec2(-1.0, 0.0));\n"
        "    float right = sampleRaw(corePixel + vec2(1.0, 0.0));\n"
        "    float upLeft = sampleRaw(corePixel + vec2(-1.0, -1.0));\n"
        "    float upRight = sampleRaw(corePixel + vec2(1.0, -1.0));\n"
        "    float downLeft = sampleRaw(corePixel + vec2(-1.0, 1.0));\n"
        "    float downRight = sampleRaw(corePixel + vec2(1.0, 1.0));\n"
        "    if (xParity < 0.5 && yParity < 0.5)\n"
        "    {\n"
        "        return vec3(raw,\n"
        "                    (up + down + left + right) * 0.25,\n"
        "                    (upLeft + upRight + downLeft + downRight) * 0.25);\n"
        "    }\n"
        "    if (xParity > 0.5 && yParity > 0.5)\n"
        "    {\n"
        "        return vec3((upLeft + upRight + downLeft + downRight) * 0.25,\n"
        "                    (up + down + left + right) * 0.25,\n"
        "                    raw);\n"
        "    }\n"
        "    if (xParity > 0.5)\n"
        "    {\n"
        "        return vec3((left + right) * 0.5,\n"
        "                    raw,\n"
        "                    (up + down) * 0.5);\n"
        "    }\n"
        "    return vec3((up + down) * 0.5,\n"
        "                raw,\n"
        "                (left + right) * 0.5);\n"
        "}\n"
        "void main()\n"
        "{\n"
        "    vec2 outputPixel = floor(vec2(vTexCoord.x * textureSize.x,\n"
        "                                  (1.0 - vTexCoord.y) * textureSize.y));\n"
        "    gl_FragColor = vec4(debayerBilinear(outputPixel), 1.0);\n"
        "}\n");
}

bool buildBilinearDebayerProgram(QOpenGLShaderProgram * program, QString * reason)
{
    if ( !program ) return false;

    const QByteArray vertexShader = gpuPreviewProcessingVertexShaderSource();
    const QByteArray fragmentShader = gpuBilinearDebayerFragmentShaderSource();
    if ( !program->addShaderFromSourceCode(QOpenGLShader::Vertex, vertexShader)
      || !program->addShaderFromSourceCode(QOpenGLShader::Fragment, fragmentShader)
      || !program->link() )
    {
        if ( reason )
        {
            *reason = QStringLiteral("QOpenGLShaderProgram bilinear debayer setup failed: %1")
                .arg(program->log());
        }
        return false;
    }
    return true;
}

bool makeDebayerContextCurrent(QOffscreenSurface * surface,
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

    const QSurfaceFormat format = debayerSurfaceFormat();
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

const char * gpuBilinearDebayerEnvironmentVariableName(void)
{
    return "MLVAPP_EXPERIMENTAL_GPU_DEBAYER";
}

bool gpuBilinearDebayerRequestedByEnvironment(void)
{
    return envFlagEnabled(qgetenv(gpuBilinearDebayerEnvironmentVariableName()));
}

GpuBilinearDebayerBackendAvailability gpuBilinearDebayerProbeBackend(void)
{
    GpuBilinearDebayerBackendAvailability availability;
    QOffscreenSurface surface;
    QOpenGLContext context;
    QOpenGLFunctions * glFunctions = nullptr;
    if ( !makeDebayerContextCurrent(&surface,
                                    &context,
                                    &glFunctions,
                                    &availability.reason,
                                    &availability.rendererDescription) )
    {
        return availability;
    }

    QOpenGLShaderProgram program;
    QString shaderReason;
    const bool shaderReady = buildBilinearDebayerProgram(&program, &shaderReason);
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

bool gpuBilinearDebayerApplyGpuOffscreen(const float * inputRawFrame,
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

    if ( !inputRawFrame || !outputRgb16 || width <= 2 || height <= 2 )
    {
        return fail(QStringLiteral("GPU bilinear debayer input/output buffers are invalid"));
    }

    QOffscreenSurface surface;
    QOpenGLContext context;
    QOpenGLFunctions * glFunctions = nullptr;
    if ( !makeDebayerContextCurrent(&surface,
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
    if ( !buildBilinearDebayerProgram(&program, reason) )
    {
        context.doneCurrent();
        return false;
    }

    const QByteArray packedFrame = packRawFrameTexture(inputRawFrame, width * height);
    QOpenGLTexture * rawTexture = createRawTexture(width, height);
    rawTexture->setData(QOpenGLTexture::RGBA,
                        QOpenGLTexture::UInt16,
                        packedFrame.constData());

    fbo.bind();
    glFunctions->glViewport(0, 0, width, height);
    glFunctions->glDisable(GL_DEPTH_TEST);
    glFunctions->glDisable(GL_BLEND);
    glFunctions->glClearColor(0.0f, 0.0f, 0.0f, 1.0f);
    glFunctions->glClear(GL_COLOR_BUFFER_BIT);

    program.bind();
    program.setUniformValue("rawTexture", 0);
    program.setUniformValue("textureSize",
                            QVector2D(static_cast<float>(width),
                                      static_cast<float>(height)));

    rawTexture->bind(0);

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
    rawTexture->release();
    program.release();
    fbo.release();
    context.doneCurrent();

    delete rawTexture;

    if ( reason ) reason->clear();
    return true;
}
