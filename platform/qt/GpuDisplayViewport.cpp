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
#include <QVector3D>
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

QOpenGLTexture * createOrResizeLookupTexture(QOpenGLTexture * texture, int width, int height)
{
    if ( texture
      && texture->width() == width
      && texture->height() == height )
    {
        return texture;
    }

    delete texture;
    texture = new QOpenGLTexture(QOpenGLTexture::Target2D);
    texture->setFormat(QOpenGLTexture::RGBA16_UNorm);
    texture->setSize(width, height);
    texture->setMipLevels(1);
    texture->allocateStorage(QOpenGLTexture::RGBA, QOpenGLTexture::UInt16);
    texture->setWrapMode(QOpenGLTexture::ClampToEdge);
    texture->setMinMagFilters(QOpenGLTexture::Nearest, QOpenGLTexture::Nearest);
    return texture;
}
}

GpuDisplayViewport::GpuDisplayViewport(QWidget *parent)
    : QOpenGLWidget(parent)
    , m_loggedContext(false)
    , m_loggedTexturePath(false)
    , m_textureDirty(false)
    , m_texturePresentationActive(false)
    , m_samplingModeDirty(false)
    , m_processingTexturesDirty(false)
    , m_pendingTextureIs16Bit(false)
    , m_textureIs16Bit(false)
    , m_view(qobject_cast<QGraphicsView *>(parent))
    , m_fallbackItem(nullptr)
    , m_pendingTextureWidth(0)
    , m_pendingTextureHeight(0)
    , m_program(nullptr)
    , m_texture(nullptr)
    , m_levelsLutTexture(nullptr)
    , m_matrixLutRTexture(nullptr)
    , m_matrixLutGTexture(nullptr)
    , m_matrixLutBTexture(nullptr)
    , m_gammaLutTexture(nullptr)
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
    const bool previewProcessingReady =
        m_presentationOptions.previewProcessing.enabled
        && m_levelsLutTexture
        && m_matrixLutRTexture
        && m_matrixLutGTexture
        && m_matrixLutBTexture
        && m_gammaLutTexture;
    m_program->setUniformValue("frameTexture", 0);
    m_program->setUniformValue("textureSize",
                               QVector2D(static_cast<float>(pendingWidth()),
                                         static_cast<float>(pendingHeight())));
    m_program->setUniformValue("samplingMode", static_cast<int>(m_presentationOptions.samplingMode));
    m_program->setUniformValue("zebraEnabled", m_presentationOptions.showZebras ? 1.0f : 0.0f);
    m_program->setUniformValue("zebraUnderThreshold", m_presentationOptions.zebraUnderThreshold);
    m_program->setUniformValue("zebraOverThreshold", m_presentationOptions.zebraOverThreshold);
    m_program->setUniformValue("previewProcessingEnabled", previewProcessingReady ? 1.0f : 0.0f);
    m_program->setUniformValue("previewUseCameraMatrix", m_presentationOptions.previewProcessing.useCameraMatrix ? 1.0f : 0.0f);
    m_program->setUniformValue("previewApplyGamutCompression", m_presentationOptions.previewProcessing.applyGamutCompression ? 1.0f : 0.0f);
    m_program->setUniformValue("previewProperWbRow0",
                               QVector3D(m_presentationOptions.previewProcessing.properWbMatrix[0],
                                         m_presentationOptions.previewProcessing.properWbMatrix[1],
                                         m_presentationOptions.previewProcessing.properWbMatrix[2]));
    m_program->setUniformValue("previewProperWbRow1",
                               QVector3D(m_presentationOptions.previewProcessing.properWbMatrix[3],
                                         m_presentationOptions.previewProcessing.properWbMatrix[4],
                                         m_presentationOptions.previewProcessing.properWbMatrix[5]));
    m_program->setUniformValue("previewProperWbRow2",
                               QVector3D(m_presentationOptions.previewProcessing.properWbMatrix[6],
                                         m_presentationOptions.previewProcessing.properWbMatrix[7],
                                         m_presentationOptions.previewProcessing.properWbMatrix[8]));
    m_program->setUniformValue("previewRgbToY",
                               QVector3D(m_presentationOptions.previewProcessing.rgbToY[0],
                                         m_presentationOptions.previewProcessing.rgbToY[1],
                                         m_presentationOptions.previewProcessing.rgbToY[2]));
    m_texture->bind(0);
    if ( previewProcessingReady && m_levelsLutTexture )
    {
        m_program->setUniformValue("levelsLut", 1);
        m_levelsLutTexture->bind(1);
    }
    if ( previewProcessingReady && m_matrixLutRTexture )
    {
        m_program->setUniformValue("matrixLutR", 2);
        m_matrixLutRTexture->bind(2);
    }
    if ( previewProcessingReady && m_matrixLutGTexture )
    {
        m_program->setUniformValue("matrixLutG", 3);
        m_matrixLutGTexture->bind(3);
    }
    if ( previewProcessingReady && m_matrixLutBTexture )
    {
        m_program->setUniformValue("matrixLutB", 4);
        m_matrixLutBTexture->bind(4);
    }
    if ( previewProcessingReady && m_gammaLutTexture )
    {
        m_program->setUniformValue("gammaLut", 5);
        m_gammaLutTexture->bind(5);
    }

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
    if ( previewProcessingReady && m_levelsLutTexture ) m_levelsLutTexture->release();
    if ( previewProcessingReady && m_matrixLutRTexture ) m_matrixLutRTexture->release();
    if ( previewProcessingReady && m_matrixLutGTexture ) m_matrixLutGTexture->release();
    if ( previewProcessingReady && m_matrixLutBTexture ) m_matrixLutBTexture->release();
    if ( previewProcessingReady && m_gammaLutTexture ) m_gammaLutTexture->release();
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
        destroyProcessingTextures();
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
    destroyProcessingTextures();
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
    m_processingTexturesDirty = true;
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
    m_processingTexturesDirty = true;
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
    m_processingTexturesDirty = false;
    cleanupGLResources();
    if ( m_fallbackItem ) m_fallbackItem->setVisible(true);
    update();
}

void GpuDisplayViewport::updateTextureIfNeeded()
{
    if ( !m_textureDirty && !m_samplingModeDirty && !m_processingTexturesDirty )
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

    updateProcessingTexturesIfNeeded();
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
        qInfo() << "Experimental GPU viewport is presenting frames from a persistent OpenGL texture with shader-side sampling and display overlays.";
        m_loggedTexturePath = true;
    }
}

void GpuDisplayViewport::ensureProgram()
{
    if ( m_program ) return;
    const QByteArray vertexShader = gpuPreviewProcessingVertexShaderSource();
    const QByteArray fragmentShader = gpuPreviewProcessingDisplayFragmentShaderSource();

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

void GpuDisplayViewport::destroyProcessingTextures()
{
    if ( m_levelsLutTexture )
    {
        delete m_levelsLutTexture;
        m_levelsLutTexture = nullptr;
    }
    if ( m_matrixLutRTexture )
    {
        delete m_matrixLutRTexture;
        m_matrixLutRTexture = nullptr;
    }
    if ( m_matrixLutGTexture )
    {
        delete m_matrixLutGTexture;
        m_matrixLutGTexture = nullptr;
    }
    if ( m_matrixLutBTexture )
    {
        delete m_matrixLutBTexture;
        m_matrixLutBTexture = nullptr;
    }
    if ( m_gammaLutTexture )
    {
        delete m_gammaLutTexture;
        m_gammaLutTexture = nullptr;
    }
}

void GpuDisplayViewport::updateProcessingTexturesIfNeeded()
{
    if ( !m_processingTexturesDirty && !m_presentationOptions.previewProcessing.enabled )
    {
        return;
    }

    if ( !m_presentationOptions.previewProcessing.enabled )
    {
        destroyProcessingTextures();
        m_processingTexturesDirty = false;
        return;
    }

    const QByteArray & levelsLut = m_presentationOptions.previewProcessing.levelsLut;
    const QByteArray & matrixLutR = m_presentationOptions.previewProcessing.matrixLutR;
    const QByteArray & matrixLutG = m_presentationOptions.previewProcessing.matrixLutG;
    const QByteArray & matrixLutB = m_presentationOptions.previewProcessing.matrixLutB;
    const QByteArray & gammaLut = m_presentationOptions.previewProcessing.gammaLut;
    if ( levelsLut.size() < static_cast<int>(65536u * sizeof(uint16_t))
      || matrixLutR.size() < static_cast<int>(65536u * sizeof(uint16_t))
      || matrixLutG.size() < static_cast<int>(65536u * sizeof(uint16_t))
      || matrixLutB.size() < static_cast<int>(65536u * sizeof(uint16_t))
      || gammaLut.size() < static_cast<int>(65536u * sizeof(uint16_t)) )
    {
        destroyProcessingTextures();
        m_processingTexturesDirty = false;
        return;
    }

    const QByteArray levelsBytes = gpuPreviewProcessingPackLookupTextureRgba16(levelsLut);
    const QByteArray matrixRBytes = gpuPreviewProcessingPackLookupTextureRgba16(matrixLutR);
    const QByteArray matrixGBytes = gpuPreviewProcessingPackLookupTextureRgba16(matrixLutG);
    const QByteArray matrixBBytes = gpuPreviewProcessingPackLookupTextureRgba16(matrixLutB);
    const QByteArray gammaBytes = gpuPreviewProcessingPackLookupTextureRgba16(gammaLut);

    m_levelsLutTexture = createOrResizeLookupTexture(m_levelsLutTexture, 256, 256);
    m_matrixLutRTexture = createOrResizeLookupTexture(m_matrixLutRTexture, 256, 256);
    m_matrixLutGTexture = createOrResizeLookupTexture(m_matrixLutGTexture, 256, 256);
    m_matrixLutBTexture = createOrResizeLookupTexture(m_matrixLutBTexture, 256, 256);
    m_gammaLutTexture = createOrResizeLookupTexture(m_gammaLutTexture, 256, 256);

    m_levelsLutTexture->setData(QOpenGLTexture::RGBA,
                                QOpenGLTexture::UInt16,
                                levelsBytes.constData());
    m_matrixLutRTexture->setData(QOpenGLTexture::RGBA,
                                 QOpenGLTexture::UInt16,
                                 matrixRBytes.constData());
    m_matrixLutGTexture->setData(QOpenGLTexture::RGBA,
                                 QOpenGLTexture::UInt16,
                                 matrixGBytes.constData());
    m_matrixLutBTexture->setData(QOpenGLTexture::RGBA,
                                 QOpenGLTexture::UInt16,
                                 matrixBBytes.constData());
    m_gammaLutTexture->setData(QOpenGLTexture::RGBA,
                               QOpenGLTexture::UInt16,
                               gammaBytes.constData());
    m_processingTexturesDirty = false;
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
