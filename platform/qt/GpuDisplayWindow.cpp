/*!
 * \file GpuDisplayWindow.cpp
 * \author Claude
 * \copyright 2026
 * \brief QOpenGLWindow-based GPU preview for NVIDIA Optimus hybrid laptops (see header).
 */

#include "GpuDisplayWindow.h"

#include <QGraphicsView>
#include <QWidget>
#include <QGridLayout>
#include <QLayout>
#include <QThread>
#include <QByteArray>
#include <QSurfaceFormat>
#include <QMutex>
#include <QDir>
#include <QtDebug>
#include <algorithm>
#include <atomic>

namespace
{
/* The active window handle is read on the render thread (present/clear) and
 * written on the GUI thread (install / destructor). std::atomic closes the data
 * race; g_activeMutex serializes the render-thread read+post against the GUI-thread
 * destructor clear so the receiver cannot be freed between the load and the post. */
std::atomic<GpuDisplayWindow *> g_activeWindow{nullptr};
QMutex g_activeMutex;

bool windowEnvFlagEnabled(const QByteArray &value)
{
    if ( value.isEmpty() ) return false;
    const QByteArray normalized = value.trimmed().toLower();
    return normalized == "1" || normalized == "true" || normalized == "yes" || normalized == "on";
}

/* GLSL 1.20 passthrough -- works in the NVIDIA compatibility context a QOpenGLWindow
 * gets by default, no LUT/Bayer uniforms. Milestone 1 displays the already-final CPU
 * RGBA frame; the shared GpuPreviewProcessing shader (zebras/LUTs/Bayer) comes with the
 * shared-renderer milestone. */
const char *kVertexShader =
    "attribute vec2 position;\n"
    "attribute vec2 texCoord;\n"
    "varying vec2 vTexCoord;\n"
    "void main() {\n"
    "    vTexCoord = texCoord;\n"
    "    gl_Position = vec4(position, 0.0, 1.0);\n"
    "}\n";

const char *kFragmentShader =
    "varying vec2 vTexCoord;\n"
    "uniform sampler2D frameTexture;\n"
    "void main() {\n"
    "    gl_FragColor = texture2D(frameTexture, vTexCoord);\n"
    "}\n";
}

const char *GpuDisplayWindow::environmentVariableName()
{
    return "MLVAPP_EXPERIMENTAL_GL_WINDOW_VIEWPORT";
}

bool GpuDisplayWindow::isRequestedByEnvironment()
{
    return windowEnvFlagEnabled(qgetenv(environmentVariableName()));
}

bool GpuDisplayWindow::isActive()
{
    return g_activeWindow.load(std::memory_order_acquire) != nullptr;
}

QString GpuDisplayWindow::rendererDescription()
{
    GpuDisplayWindow *win = g_activeWindow.load(std::memory_order_acquire);
    return win ? win->m_rendererDescription : QString();
}

QSize GpuDisplayWindow::displaySize()
{
    GpuDisplayWindow *win = g_activeWindow.load(std::memory_order_acquire);
    return win ? win->size() : QSize();
}

bool GpuDisplayWindow::installInPreview(QGraphicsView *view)
{
    if ( !view || !isRequestedByEnvironment() ) return false;
    if ( g_activeWindow ) return true;

    GpuDisplayWindow *win = new GpuDisplayWindow();
    QWidget *container = QWidget::createWindowContainer(win);
    container->setObjectName(QStringLiteral("gpuDisplayWindowContainer"));
    container->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
    container->setFocusPolicy(Qt::NoFocus);

    QWidget *host = view->parentWidget();
    bool placed = false;
    if ( host && host->layout() )
    {
        if ( QGridLayout *grid = qobject_cast<QGridLayout *>(host->layout()) )
        {
            const int idx = grid->indexOf(view);
            if ( idx >= 0 )
            {
                int r = 0, c = 0, rs = 1, cs = 1;
                grid->getItemPosition(idx, &r, &c, &rs, &cs);
                grid->removeWidget(view);   // clean swap: don't leave two items in the cell
                container->setParent(host);
                grid->addWidget(container, r, c, rs, cs);
                placed = true;
            }
        }
        if ( !placed )
        {
            host->layout()->replaceWidget(view, container);
            placed = true;
        }
    }
    if ( !placed )
    {
        container->setParent(host ? host : view);
        container->setGeometry(view->geometry());
        container->raise();
    }
    view->hide();
    container->show();

    g_activeWindow.store(win, std::memory_order_release);
    qInfo() << "Experimental GPU window viewport enabled via"
            << environmentVariableName()
            << "- preview renders through a QOpenGLWindow (createWindowContainer).";
    return true;
}

bool GpuDisplayWindow::presentImageIfActive(const QImage &image)
{
    QMutexLocker lock(&g_activeMutex);
    GpuDisplayWindow *win = g_activeWindow.load(std::memory_order_acquire);
    if ( !win ) return false;

    if ( QThread::currentThread() == win->thread() )
    {
        // Same (GUI) thread: setPresentedImage deep-copies synchronously, and the
        // destructor runs on this same thread, so there is no race here.
        win->setPresentedImage(image);
    }
    else
    {
        // Render thread: the source QImage is often a NON-owning view over a frame
        // buffer the prefetch pipeline frees right after this returns. Deep-copy NOW
        // (while it is alive) so the deferred GUI-thread upload reads owned memory --
        // the copy() inside setPresentedImage would run too late. The mutex keeps the
        // destructor from freeing `win` between this load and the queued post.
        const QImage owned = image.copy();
        QMetaObject::invokeMethod(win, [win, owned]() { win->setPresentedImage(owned); },
                                  Qt::QueuedConnection);
    }
    return true;
}

bool GpuDisplayWindow::clearIfActive()
{
    QMutexLocker lock(&g_activeMutex);
    GpuDisplayWindow *win = g_activeWindow.load(std::memory_order_acquire);
    if ( !win ) return false;

    if ( QThread::currentThread() == win->thread() )
        win->clearPresented();
    else
        QMetaObject::invokeMethod(win, [win]() { win->clearPresented(); }, Qt::QueuedConnection);
    return true;
}

GpuDisplayWindow::GpuDisplayWindow(QWindow *parent)
    : QOpenGLWindow(QOpenGLWindow::NoPartialUpdate, parent)
    , m_program(nullptr)
    , m_texture(nullptr)
    , m_textureDirty(false)
    , m_loggedContext(false)
    , m_loggedPaint(false)
    , m_loggedPresented(false)
    , m_loggedSetImage(false)
{
    QSurfaceFormat fmt = format();
    fmt.setSwapInterval(0);
    setFormat(fmt);
}

GpuDisplayWindow::~GpuDisplayWindow()
{
    {
        // Clear the active handle under the same lock the present path uses, so a
        // render-thread present cannot post to this window after it is cleared/freed.
        QMutexLocker lock(&g_activeMutex);
        if ( g_activeWindow.load(std::memory_order_acquire) == this )
            g_activeWindow.store(nullptr, std::memory_order_release);
    }
    // Tear down GL objects only while the context is valid (makeCurrent() is void on
    // QOpenGLWindow); anything missed here is reclaimed when the context is destroyed.
    if ( isValid() )
    {
        makeCurrent();
        destroyTexture();
        delete m_program;
        m_program = nullptr;
        doneCurrent();
    }
}

void GpuDisplayWindow::setPresentedImage(const QImage &image)
{
    m_pendingImage = image.format() == QImage::Format_RGBA8888
        ? image.copy()
        : image.convertToFormat(QImage::Format_RGBA8888);
    m_textureDirty = true;
    if ( !m_loggedSetImage )
    {
        qInfo().nospace() << "gpu_window setPresentedImage: first frame received ("
                          << m_pendingImage.width() << "x" << m_pendingImage.height() << ").";
        m_loggedSetImage = true;
    }
    update();
}

void GpuDisplayWindow::clearPresented()
{
    m_pendingImage = QImage();
    m_textureDirty = true;
    update();
}

void GpuDisplayWindow::initializeGL()
{
    initializeOpenGLFunctions();
    if ( m_loggedContext ) return;

    const GLubyte *renderer = glGetString(GL_RENDERER);
    const GLubyte *vendor = glGetString(GL_VENDOR);
    const GLubyte *version = glGetString(GL_VERSION);
    m_rendererDescription = renderer
        ? QString::fromLatin1(reinterpret_cast<const char *>(renderer))
        : QStringLiteral("unknown");
    qInfo().nospace()
        << "Experimental GPU window viewport initialized (renderer=" << m_rendererDescription
        << ", vendor=" << (vendor ? reinterpret_cast<const char *>(vendor) : "unknown")
        << ", version=" << (version ? reinterpret_cast<const char *>(version) : "unknown")
        << ").";
    m_loggedContext = true;
}

void GpuDisplayWindow::ensureProgram()
{
    if ( m_program ) return;
    m_program = new QOpenGLShaderProgram(this);
    m_program->bindAttributeLocation(QStringLiteral("position"), 0);
    m_program->bindAttributeLocation(QStringLiteral("texCoord"), 1);
    if ( !m_program->addShaderFromSourceCode(QOpenGLShader::Vertex, kVertexShader)
      || !m_program->addShaderFromSourceCode(QOpenGLShader::Fragment, kFragmentShader)
      || !m_program->link() )
    {
        qWarning() << "Experimental GPU window viewport shader setup failed:" << m_program->log();
        delete m_program;
        m_program = nullptr;
    }
}

void GpuDisplayWindow::destroyTexture()
{
    if ( m_texture )
    {
        delete m_texture;
        m_texture = nullptr;
    }
}

void GpuDisplayWindow::updateTextureIfNeeded()
{
    if ( !m_textureDirty ) return;
    m_textureDirty = false;

    if ( m_pendingImage.isNull() )
    {
        destroyTexture();
        return;
    }
    ensureProgram();
    if ( !m_program ) return;

    if ( !m_texture
      || m_texture->width() != m_pendingImage.width()
      || m_texture->height() != m_pendingImage.height() )
    {
        destroyTexture();
        m_texture = new QOpenGLTexture(QOpenGLTexture::Target2D);
        m_texture->setFormat(QOpenGLTexture::RGBA8_UNorm);
        m_texture->setSize(m_pendingImage.width(), m_pendingImage.height());
        m_texture->setMipLevels(1);
        m_texture->allocateStorage(QOpenGLTexture::RGBA, QOpenGLTexture::UInt8);
        m_texture->setWrapMode(QOpenGLTexture::ClampToEdge);
        m_texture->setMinMagFilters(QOpenGLTexture::Linear, QOpenGLTexture::Linear);
    }
    m_texture->setData(m_pendingImage);
}

void GpuDisplayWindow::paintGL()
{
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_CULL_FACE);
    glDisable(GL_BLEND);

    const qreal dpr = devicePixelRatio();
    const int fbw = static_cast<int>(width() * dpr);
    const int fbh = static_cast<int>(height() * dpr);
    glViewport(0, 0, std::max(1, fbw), std::max(1, fbh));
    glClearColor(0.0f, 0.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);

    updateTextureIfNeeded();
    if ( !m_texture || !m_program || width() <= 0 || height() <= 0 )
    {
        if ( !m_loggedPaint )
        { qInfo() << "gpu_window paintGL: no texture/program yet (clearing)."; m_loggedPaint = true; }
        return;
    }

    // Fit the frame to the window, preserving aspect ratio (letterbox).
    const float winAspect = static_cast<float>(width()) / static_cast<float>(height());
    const float imgAspect = static_cast<float>(m_texture->width()) / static_cast<float>(m_texture->height());
    float sx = 1.0f;
    float sy = 1.0f;
    if ( imgAspect > winAspect ) sy = winAspect / imgAspect;
    else                         sx = imgAspect / winAspect;

    // Interleaved [x, y, u, v]; screen-top maps to texture v=0 (matches the corrected
    // GpuDisplayViewport quad so QImage row 0 / image top shows at the top -> upright).
    const float verts[16] = {
        -sx,  sy, 0.0f, 0.0f,
         sx,  sy, 1.0f, 0.0f,
        -sx, -sy, 0.0f, 1.0f,
         sx, -sy, 1.0f, 1.0f,
    };

    m_program->bind();
    m_texture->bind(0);
    m_program->setUniformValue("frameTexture", 0);
    m_program->enableAttributeArray(0);
    m_program->enableAttributeArray(1);
    m_program->setAttributeArray(0, GL_FLOAT, verts, 2, 4 * sizeof(float));
    m_program->setAttributeArray(1, GL_FLOAT, verts + 2, 2, 4 * sizeof(float));
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    m_program->disableAttributeArray(0);
    m_program->disableAttributeArray(1);
    m_texture->release();
    m_program->release();

    if ( !m_loggedPresented )
    {
        qInfo().nospace() << "gpu_window paintGL: presented a frame texture ("
                          << m_texture->width() << "x" << m_texture->height() << ").";
        m_loggedPresented = true;
    }

    // Diagnostic (env-gated MLVAPP_WINDOW_READBACK_DIR): read back the ACTUAL rendered
    // framebuffer so the on-screen output can be compared against the clean input
    // displayImage. Reveals upload/shader corruption (interleaving/banding) -- but NOT
    // DWM alpha compositing, which happens after this readback. Capped; inert by default.
    static const QByteArray rbDir = qgetenv("MLVAPP_WINDOW_READBACK_DIR");
    if ( !rbDir.isEmpty() )
    {
        static int rbCounter = 0;
        if ( rbCounter < 24 && fbw > 0 && fbh > 0 )
        {
            QImage out(fbw, fbh, QImage::Format_RGBA8888);
            glReadPixels(0, 0, fbw, fbh, GL_RGBA, GL_UNSIGNED_BYTE, out.bits());
            static const bool mk = QDir().mkpath(QString::fromLocal8Bit(rbDir));
            Q_UNUSED(mk);
            const QString p = QString::fromLocal8Bit(rbDir)
                + QStringLiteral("/rb_%1.png").arg(rbCounter, 5, 10, QChar('0'));
            out.mirrored(false, true).save(p, "PNG");   // GL bottom-up -> top-down
            ++rbCounter;
        }
    }
}
