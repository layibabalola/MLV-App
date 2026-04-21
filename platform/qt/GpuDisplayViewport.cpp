/*!
 * \file GpuDisplayViewport.cpp
 * \author Codex
 * \copyright 2026
 * \brief Optional OpenGL-backed viewport for the existing QGraphicsView preview.
 */

#include "GpuDisplayViewport.h"

#include <algorithm>
#include <QByteArray>
#include <QColor>
#include <QGraphicsPixmapItem>
#include <QGraphicsView>
#include <QOpenGLContext>
#include <QPalette>
#include <QPolygonF>
#include <QSurfaceFormat>
#include <QVector2D>
#include <QtDebug>

namespace
{
constexpr GLfloat kQuadVertices[16] = {
    -1.0f,  1.0f, 0.0f, 1.0f,
     1.0f,  1.0f, 1.0f, 1.0f,
    -1.0f, -1.0f, 0.0f, 0.0f,
     1.0f, -1.0f, 1.0f, 0.0f,
};

bool envFlagEnabled(const QByteArray &value)
{
    if ( value.isEmpty() ) return false;

    QByteArray normalized = value.trimmed().toLower();
    return normalized == "1"
        || normalized == "true"
        || normalized == "yes"
        || normalized == "on";
}
}

GpuDisplayViewport::GpuDisplayViewport(QWidget *parent)
    : QOpenGLWidget(parent)
    , m_loggedContext(false)
    , m_loggedTexturePath(false)
    , m_textureDirty(false)
    , m_texturePresentationActive(false)
    , m_samplingModeDirty(false)
    , m_pendingTextureIs16Bit(false)
    , m_textureIs16Bit(false)
    , m_view(qobject_cast<QGraphicsView *>(parent))
    , m_fallbackItem(nullptr)
    , m_pendingTextureWidth(0)
    , m_pendingTextureHeight(0)
    , m_program(nullptr)
    , m_texture(nullptr)
{
    setUpdateBehavior(QOpenGLWidget::NoPartialUpdate);
    setAutoFillBackground(false);
}

GpuDisplayViewport::~GpuDisplayViewport()
{
    cleanupGLResources();
}

bool GpuDisplayViewport::isRequestedByEnvironment()
{
    return envFlagEnabled(qgetenv(environmentVariableName()));
}

const char *GpuDisplayViewport::environmentVariableName()
{
    return "MLVAPP_EXPERIMENTAL_GL_VIEWPORT";
}

bool GpuDisplayViewport::installOn(QGraphicsView *view)
{
    if ( !view || !isRequestedByEnvironment() ) return false;
    if ( from(view) ) return true;

    GpuDisplayViewport *viewport = new GpuDisplayViewport(view);
    view->setViewport(viewport);
    view->setViewportUpdateMode(QGraphicsView::FullViewportUpdate);
    view->setCacheMode(QGraphicsView::CacheNone);
    qInfo() << "Experimental GPU viewport enabled via"
            << environmentVariableName()
            << "- QGraphicsView now renders through QOpenGLWidget.";
    return true;
}

bool GpuDisplayViewport::isInstalledOn(const QGraphicsView *view)
{
    return from(view) != nullptr;
}

bool GpuDisplayViewport::hasPresentedImage(const QGraphicsView *view)
{
    const GpuDisplayViewport *viewport = from(view);
    return viewport && viewport->hasPendingFrame();
}

bool GpuDisplayViewport::isTexturePresentationActive(const QGraphicsView *view)
{
    const GpuDisplayViewport *viewport = from(view);
    return viewport && viewport->m_texturePresentationActive;
}

GpuDisplayViewport::SamplingMode GpuDisplayViewport::samplingModeFor(const QGraphicsView *view)
{
    const GpuDisplayViewport *viewport = from(view);
    return viewport ? viewport->m_presentationOptions.samplingMode : SamplingLinear;
}

QString GpuDisplayViewport::rendererDescriptionFor(const QGraphicsView *view)
{
    const GpuDisplayViewport *viewport = from(view);
    return viewport ? viewport->m_rendererDescription : QString();
}

bool GpuDisplayViewport::presentImage(QGraphicsView *view,
                                      QGraphicsPixmapItem *fallbackItem,
                                      const QImage &image,
                                      const PresentationOptions &options)
{
    GpuDisplayViewport *viewport = from(view);
    if ( !viewport || image.isNull() )
    {
        if ( fallbackItem ) fallbackItem->setVisible(true);
        return false;
    }

    viewport->setFallbackItem(fallbackItem);
    viewport->setPresentedImage(image, options);
    return true;
}

bool GpuDisplayViewport::presentRgb16(QGraphicsView *view,
                                      QGraphicsPixmapItem *fallbackItem,
                                      const uint16_t *imageData,
                                      int width,
                                      int height,
                                      const PresentationOptions &options)
{
    GpuDisplayViewport *viewport = from(view);
    if ( !viewport || !imageData || width <= 0 || height <= 0 )
    {
        if ( fallbackItem ) fallbackItem->setVisible(true);
        return false;
    }

    viewport->setFallbackItem(fallbackItem);
    viewport->setPresentedRgb16(imageData, width, height, options);
    return true;
}

void GpuDisplayViewport::clearPresentedImage(QGraphicsView *view,
                                             QGraphicsPixmapItem *fallbackItem)
{
    GpuDisplayViewport *viewport = from(view);
    if ( !viewport )
    {
        if ( fallbackItem ) fallbackItem->setVisible(true);
        return;
    }

    if ( fallbackItem ) viewport->setFallbackItem(fallbackItem);
    viewport->clearPresentedImage();
}

void GpuDisplayViewport::initializeGL()
{
    initializeOpenGLFunctions();

    if ( context() )
    {
        connect(context(), &QOpenGLContext::aboutToBeDestroyed, this, [this]()
        {
            cleanupGLResources();
        }, Qt::UniqueConnection);
    }

    if ( m_loggedContext ) return;

    QOpenGLContext *glContext = context();
    if ( !glContext )
    {
        qWarning() << "Experimental GPU viewport was requested, but no OpenGL context is available.";
        return;
    }

    const QSurfaceFormat format = glContext->format();
    QOpenGLFunctions *functions = glContext->functions();
    const GLubyte *renderer = functions ? functions->glGetString(GL_RENDERER) : nullptr;
    const GLubyte *vendor = functions ? functions->glGetString(GL_VENDOR) : nullptr;
    const GLubyte *version = functions ? functions->glGetString(GL_VERSION) : nullptr;
    m_rendererDescription = renderer
        ? QString::fromLatin1(reinterpret_cast<const char *>(renderer))
        : QStringLiteral("unknown");

    qInfo().nospace()
        << "Experimental GPU viewport initialized ("
        << format.majorVersion() << '.'
        << format.minorVersion()
        << ", renderer=" << m_rendererDescription
        << ", vendor=" << (vendor ? reinterpret_cast<const char *>(vendor) : "unknown")
        << ", version=" << (version ? reinterpret_cast<const char *>(version) : "unknown")
        << ").";

    m_loggedContext = true;
}

void GpuDisplayViewport::paintGL()
{
    const QColor clearColor = m_view
        ? m_view->backgroundBrush().color()
        : palette().color(QPalette::Window);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_CULL_FACE);
    glDisable(GL_BLEND);
    glClearColor(clearColor.redF(), clearColor.greenF(), clearColor.blueF(), 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);

    updateTextureIfNeeded();
    if ( !m_texture || !m_program || !m_view )
    {
        m_texturePresentationActive = false;
        return;
    }

    const QRectF targetRect = targetRectInViewport();
    if ( targetRect.isEmpty() )
    {
        m_texturePresentationActive = false;
        return;
    }

    const qreal dpr = devicePixelRatioF();
    const float fbWidth = static_cast<float>(width() * dpr);
    const float fbHeight = static_cast<float>(height() * dpr);
    if ( fbWidth <= 0.0f || fbHeight <= 0.0f )
    {
        m_texturePresentationActive = false;
        return;
    }
    const float leftPx = static_cast<float>(targetRect.left() * dpr);
    const float topPx = static_cast<float>(targetRect.top() * dpr);
    const float rightPx = static_cast<float>((targetRect.left() + targetRect.width()) * dpr);
    const float bottomPx = static_cast<float>((targetRect.top() + targetRect.height()) * dpr);

    GLfloat vertices[16];
    std::copy(std::begin(kQuadVertices), std::end(kQuadVertices), std::begin(vertices));
    vertices[0] = (leftPx / fbWidth) * 2.0f - 1.0f;
    vertices[1] = 1.0f - (topPx / fbHeight) * 2.0f;
    vertices[4] = (rightPx / fbWidth) * 2.0f - 1.0f;
    vertices[5] = vertices[1];
    vertices[8] = vertices[0];
    vertices[9] = 1.0f - (bottomPx / fbHeight) * 2.0f;
    vertices[12] = vertices[4];
    vertices[13] = vertices[9];

    m_program->bind();
    m_program->setUniformValue("frameTexture", 0);
    m_program->setUniformValue("textureSize",
                               QVector2D(static_cast<float>(pendingWidth()),
                                         static_cast<float>(pendingHeight())));
    m_program->setUniformValue("samplingMode", static_cast<int>(m_presentationOptions.samplingMode));
    m_program->setUniformValue("zebraEnabled", m_presentationOptions.showZebras ? 1.0f : 0.0f);
    m_program->setUniformValue("zebraUnderThreshold", m_presentationOptions.zebraUnderThreshold);
    m_program->setUniformValue("zebraOverThreshold", m_presentationOptions.zebraOverThreshold);
    m_texture->bind(0);

    const int posLoc = m_program->attributeLocation("position");
    const int texLoc = m_program->attributeLocation("texCoord");
    m_program->enableAttributeArray(posLoc);
    m_program->enableAttributeArray(texLoc);
    m_program->setAttributeArray(posLoc, GL_FLOAT, vertices, 2, 4 * sizeof(GLfloat));
    m_program->setAttributeArray(texLoc, GL_FLOAT, vertices + 2, 2, 4 * sizeof(GLfloat));

    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);

    m_program->disableAttributeArray(posLoc);
    m_program->disableAttributeArray(texLoc);
    m_texture->release();
    m_program->release();
    m_texturePresentationActive = true;
}

void GpuDisplayViewport::resizeGL(int, int)
{
}

GpuDisplayViewport *GpuDisplayViewport::from(QGraphicsView *view)
{
    if ( !view ) return nullptr;
    return qobject_cast<GpuDisplayViewport *>(view->viewport());
}

const GpuDisplayViewport *GpuDisplayViewport::from(const QGraphicsView *view)
{
    return from(const_cast<QGraphicsView *>(view));
}

void GpuDisplayViewport::cleanupGLResources()
{
    m_texturePresentationActive = false;

    QOpenGLContext *glContext = context();
    if ( !glContext )
    {
        destroyTexture();
        if ( m_program )
        {
            delete m_program;
            m_program = nullptr;
        }
        return;
    }

    const bool needsCurrent = QOpenGLContext::currentContext() != glContext;
    const bool madeCurrent = needsCurrent ? (makeCurrent(), true) : false;

    destroyTexture();
    if ( m_program )
    {
        delete m_program;
        m_program = nullptr;
    }

    if ( madeCurrent ) doneCurrent();
}

void GpuDisplayViewport::setFallbackItem(QGraphicsPixmapItem *item)
{
    m_fallbackItem = item;
}

void GpuDisplayViewport::setPresentedImage(const QImage &image, const PresentationOptions &options)
{
    m_pendingImage = image.format() == QImage::Format_RGBA8888
        ? image.copy()
        : image.convertToFormat(QImage::Format_RGBA8888);
    m_pendingTextureBytes.clear();
    m_pendingTextureWidth = m_pendingImage.width();
    m_pendingTextureHeight = m_pendingImage.height();
    m_pendingTextureIs16Bit = false;
    m_textureDirty = true;
    m_samplingModeDirty = m_samplingModeDirty || (m_presentationOptions.samplingMode != options.samplingMode);
    m_presentationOptions = options;
    m_texturePresentationActive = false;
    if ( m_fallbackItem ) m_fallbackItem->setVisible(false);
    update();
}

void GpuDisplayViewport::setPresentedRgb16(const uint16_t *imageData,
                                           int width,
                                           int height,
                                           const PresentationOptions &options)
{
    const size_t pixelCount = static_cast<size_t>(width) * static_cast<size_t>(height);
    const size_t bytesNeeded = pixelCount * 4u * sizeof(uint16_t);
    m_pendingTextureBytes.resize(static_cast<int>(bytesNeeded));

    uint16_t * rgba = reinterpret_cast<uint16_t *>(m_pendingTextureBytes.data());
    for ( size_t i = 0; i < pixelCount; ++i )
    {
        rgba[i * 4 + 0] = imageData[i * 3 + 0];
        rgba[i * 4 + 1] = imageData[i * 3 + 1];
        rgba[i * 4 + 2] = imageData[i * 3 + 2];
        rgba[i * 4 + 3] = 65535;
    }

    m_pendingImage = QImage();
    m_pendingTextureWidth = width;
    m_pendingTextureHeight = height;
    m_pendingTextureIs16Bit = true;
    m_textureDirty = true;
    m_samplingModeDirty = m_samplingModeDirty || (m_presentationOptions.samplingMode != options.samplingMode);
    m_presentationOptions = options;
    m_texturePresentationActive = false;
    if ( m_fallbackItem ) m_fallbackItem->setVisible(false);
    update();
}

void GpuDisplayViewport::clearPresentedImage()
{
    m_pendingImage = QImage();
    m_pendingTextureBytes.clear();
    m_pendingTextureWidth = 0;
    m_pendingTextureHeight = 0;
    m_pendingTextureIs16Bit = false;
    m_textureDirty = false;
    m_samplingModeDirty = false;
    cleanupGLResources();
    if ( m_fallbackItem ) m_fallbackItem->setVisible(true);
    update();
}

void GpuDisplayViewport::updateTextureIfNeeded()
{
    if ( !m_textureDirty && !m_samplingModeDirty )
    {
        if ( m_fallbackItem && m_texture ) m_fallbackItem->setVisible(false);
        return;
    }

    if ( !hasPendingFrame() )
    {
        destroyTexture();
        m_textureDirty = false;
        m_texturePresentationActive = false;
        if ( m_fallbackItem ) m_fallbackItem->setVisible(true);
        return;
    }

    ensureProgram();
    if ( !m_program )
    {
        m_texturePresentationActive = false;
        if ( m_fallbackItem ) m_fallbackItem->setVisible(true);
        return;
    }

    if ( !m_texture
      || m_texture->width() != pendingWidth()
      || m_texture->height() != pendingHeight()
      || m_textureIs16Bit != m_pendingTextureIs16Bit )
    {
        destroyTexture();
        m_texture = new QOpenGLTexture(QOpenGLTexture::Target2D);
        m_texture->setFormat(m_pendingTextureIs16Bit ? QOpenGLTexture::RGBA16_UNorm
                                                     : QOpenGLTexture::RGBA8_UNorm);
        m_texture->setSize(pendingWidth(), pendingHeight());
        m_texture->setMipLevels(1);
        m_texture->allocateStorage(QOpenGLTexture::RGBA,
                                   m_pendingTextureIs16Bit ? QOpenGLTexture::UInt16
                                                           : QOpenGLTexture::UInt8);
        m_texture->setWrapMode(QOpenGLTexture::ClampToEdge);
        m_textureIs16Bit = m_pendingTextureIs16Bit;
    }

    applySamplingMode();
    if ( m_pendingTextureIs16Bit )
    {
        m_texture->setData(QOpenGLTexture::RGBA,
                           QOpenGLTexture::UInt16,
                           m_pendingTextureBytes.constData());
    }
    else
    {
        m_texture->setData(m_pendingImage);
    }
    m_textureDirty = false;

    if ( m_fallbackItem ) m_fallbackItem->setVisible(false);
    if ( !m_loggedTexturePath )
    {
        qInfo() << "Experimental GPU viewport is presenting frames from a persistent OpenGL texture.";
        m_loggedTexturePath = true;
    }
}

void GpuDisplayViewport::ensureProgram()
{
    if ( m_program ) return;

    static const char *vertexShader =
        "attribute vec2 position;\n"
        "attribute vec2 texCoord;\n"
        "varying vec2 vTexCoord;\n"
        "void main()\n"
        "{\n"
        "    gl_Position = vec4(position, 0.0, 1.0);\n"
        "    vTexCoord = texCoord;\n"
        "}\n";

    static const char *fragmentShader =
        "uniform sampler2D frameTexture;\n"
        "uniform vec2 textureSize;\n"
        "uniform int samplingMode;\n"
        "uniform float zebraEnabled;\n"
        "uniform float zebraUnderThreshold;\n"
        "uniform float zebraOverThreshold;\n"
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
        "vec4 applyDisplayProcessing(vec4 color)\n"
        "{\n"
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
        "}\n";

    m_program = new QOpenGLShaderProgram(this);
    if ( !m_program->addShaderFromSourceCode(QOpenGLShader::Vertex, vertexShader)
      || !m_program->addShaderFromSourceCode(QOpenGLShader::Fragment, fragmentShader)
      || !m_program->link() )
    {
        qWarning() << "Experimental GPU viewport shader setup failed:"
                   << m_program->log();
        delete m_program;
        m_program = nullptr;
    }
}

void GpuDisplayViewport::destroyTexture()
{
    if ( !m_texture ) return;
    delete m_texture;
    m_texture = nullptr;
    m_texturePresentationActive = false;
    m_samplingModeDirty = false;
    m_textureIs16Bit = false;
}

void GpuDisplayViewport::applySamplingMode()
{
    if ( !m_texture ) return;

    const bool useNearest = m_presentationOptions.samplingMode == SamplingNearest;
    const bool useBicubic = m_presentationOptions.samplingMode == SamplingBicubic;
    const QOpenGLTexture::Filter filter = (useNearest || useBicubic)
        ? QOpenGLTexture::Nearest
        : QOpenGLTexture::Linear;
    m_texture->setMinMagFilters(filter, filter);
    m_samplingModeDirty = false;
}

QRectF GpuDisplayViewport::targetRectInViewport() const
{
    if ( !m_view ) return QRectF();

    QRectF sceneRect = m_view->sceneRect();
    if ( m_fallbackItem )
    {
        const QRectF itemRect = m_fallbackItem->sceneBoundingRect();
        if ( !itemRect.isEmpty() ) sceneRect = itemRect;
    }
    if ( sceneRect.isEmpty() ) return QRectF();

    return m_view->mapFromScene(sceneRect).boundingRect();
}

int GpuDisplayViewport::pendingWidth() const
{
    return m_pendingTextureIs16Bit ? m_pendingTextureWidth : m_pendingImage.width();
}

int GpuDisplayViewport::pendingHeight() const
{
    return m_pendingTextureIs16Bit ? m_pendingTextureHeight : m_pendingImage.height();
}

bool GpuDisplayViewport::hasPendingFrame() const
{
    return m_pendingTextureIs16Bit
        ? (!m_pendingTextureBytes.isEmpty() && m_pendingTextureWidth > 0 && m_pendingTextureHeight > 0)
        : !m_pendingImage.isNull();
}
