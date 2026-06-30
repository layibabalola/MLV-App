/*!
 * \file GpuDisplayWindow.cpp
 * \author Claude
 * \copyright 2026
 * \brief QOpenGLWindow-based GPU preview for NVIDIA Optimus hybrid laptops (see header).
 */

#include "GpuDisplayWindow.h"
#include "GpuDebayer.h"

#include <QGraphicsView>
#include <QWidget>
#include <QGridLayout>
#include <QLayout>
#include <QThread>
#include <QByteArray>
#include <QSurfaceFormat>
#include <QOpenGLContext>
#include <QElapsedTimer>
#include <QMutex>
#include <QDir>
#include <QtDebug>
#include <algorithm>
#include <atomic>
#include <cstring>
#include <limits>

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

bool GpuDisplayWindow::presentGpuPlaybackReconAmazePostWbTextureIfActive(
    const uint16_t *rawInputBayer14,
    size_t rawInputBayer14Words,
    const llrpGpuPlaybackReconState_t *state,
    int blackLevel,
    const double wbMultipliers[3],
    QString *reason,
    llrpGpuPlaybackReconTiming_t *timing,
    QString *handoffMode,
    bool validationProbeTexture,
    const uint16_t *retainedDeviceBayer16,
    int retainedDeviceWidth,
    int retainedDeviceHeight,
    int displayWidth,
    int displayHeight)
{
    QMutexLocker lock(&g_activeMutex);
    GpuDisplayWindow *win = g_activeWindow.load(std::memory_order_acquire);
    if ( !win )
    {
        if ( reason ) *reason = QStringLiteral("GPU window texture-present requires an active GPU display window");
        return false;
    }
    if ( QThread::currentThread() != win->thread() )
    {
        if ( reason ) *reason = QStringLiteral("GPU window texture-present must run on the GUI thread");
        return false;
    }
    return win->setPresentedGpuPlaybackReconAmazePostWbTexture(
        rawInputBayer14,
        rawInputBayer14Words,
        state,
        blackLevel,
        wbMultipliers,
        reason,
        timing,
        handoffMode,
        validationProbeTexture,
        retainedDeviceBayer16,
        retainedDeviceWidth,
        retainedDeviceHeight,
        displayWidth,
        displayHeight);
}

bool GpuDisplayWindow::readGpuReconSourceBayer16TextureIfActive(
    QByteArray *textureBytes,
    int *width,
    int *height,
    QString *reason)
{
    QMutexLocker lock(&g_activeMutex);
    GpuDisplayWindow *win = g_activeWindow.load(std::memory_order_acquire);
    if ( !win )
    {
        if ( reason ) *reason = QStringLiteral("GPU window recon-source texture readback requires an active GPU display window");
        return false;
    }
    if ( QThread::currentThread() != win->thread() )
    {
        if ( reason ) *reason = QStringLiteral("GPU window recon-source texture readback must run on the GUI thread");
        return false;
    }
    return win->readGpuReconSourceBayer16Texture(textureBytes, width, height, reason);
}

GpuDisplayWindow::GpuDisplayWindow(QWindow *parent)
    : QOpenGLWindow(QOpenGLWindow::NoPartialUpdate, parent)
    , m_program(nullptr)
    , m_texture(nullptr)
    , m_gpuReconSourceTexture(nullptr)
    , m_pendingTextureWidth(0)
    , m_pendingTextureHeight(0)
    , m_pendingDisplayWidth(0)
    , m_pendingDisplayHeight(0)
    , m_gpuReconSourceTextureCurrent(false)
    , m_pendingTextureFromGpuRecon(false)
    , m_textureFromGpuRecon(false)
    , m_texturePresentationActive(false)
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
    m_pendingTextureFromGpuRecon = false;
    m_gpuReconSourceTextureCurrent = false;
    m_texturePresentationActive = false;
    m_pendingDisplayWidth = m_pendingImage.width();
    m_pendingDisplayHeight = m_pendingImage.height();
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
    m_pendingTextureWidth = 0;
    m_pendingTextureHeight = 0;
    m_pendingDisplayWidth = 0;
    m_pendingDisplayHeight = 0;
    m_gpuReconSourceTextureCurrent = false;
    m_pendingTextureFromGpuRecon = false;
    m_texturePresentationActive = false;
    m_textureDirty = true;
    update();
}

bool GpuDisplayWindow::setPresentedGpuPlaybackReconAmazePostWbTexture(
    const uint16_t *rawInputBayer14,
    size_t rawInputBayer14Words,
    const llrpGpuPlaybackReconState_t *state,
    int blackLevel,
    const double wbMultipliers[3],
    QString *reason,
    llrpGpuPlaybackReconTiming_t *timing,
    QString *handoffMode,
    bool validationProbeTexture,
    const uint16_t *retainedDeviceBayer16,
    int retainedDeviceWidth,
    int retainedDeviceHeight,
    int displayWidth,
    int displayHeight)
{
    auto fail = [&](const QString &why) -> bool
    {
        if ( reason ) *reason = why;
        if ( handoffMode ) handoffMode->clear();
        m_gpuReconSourceTextureCurrent = false;
        m_pendingTextureFromGpuRecon = false;
        m_texturePresentationActive = false;
        return false;
    };

    if ( !state || !state->valid || state->width <= 0 || state->height <= 0 || !wbMultipliers )
    {
        return fail(QStringLiteral("GPU window playback recon texture-present input is invalid"));
    }

    const int texWidth = state->width;
    const int texHeight = state->height;
    const bool retainedDeviceValid =
        retainedDeviceBayer16
        && retainedDeviceWidth == texWidth
        && retainedDeviceHeight == texHeight
        && !validationProbeTexture;
    const size_t expectedWords =
        static_cast<size_t>(texWidth) * static_cast<size_t>(texHeight);
    if ( !rawInputBayer14 && !retainedDeviceValid )
    {
        return fail(QStringLiteral("GPU window playback recon texture-present input is invalid"));
    }
    if ( !retainedDeviceValid && rawInputBayer14Words < expectedWords )
    {
        return fail(QStringLiteral("GPU window playback recon texture-present Bayer input is incomplete"));
    }
    if ( handoffMode ) handoffMode->clear();

    QElapsedTimer wallTimer;
    wallTimer.start();
    auto elapsedMs = [&wallTimer]() -> double
    {
        return static_cast<double>(wallTimer.nsecsElapsed()) / 1000000.0;
    };
    double contextMs = 0.0;
    double setupMs = 0.0;
    double reconWallMs = 0.0;
    double amazeWallMs = 0.0;
    double postMs = 0.0;

    QOpenGLContext *glContext = context();
    if ( !glContext )
    {
        return fail(QStringLiteral("GPU window texture-present requires an initialized OpenGL context"));
    }

    const double contextStartMs = elapsedMs();
    const bool needsCurrent = QOpenGLContext::currentContext() != glContext;
    const bool madeCurrent = needsCurrent ? (makeCurrent(), true) : false;
    contextMs = elapsedMs() - contextStartMs;

    const double setupStartMs = elapsedMs();
    ensureProgram();
    if ( !m_program )
    {
        if ( madeCurrent ) doneCurrent();
        return fail(QStringLiteral("GPU window texture-present shader setup failed"));
    }

    if ( !m_texture
      || m_texture->width() != texWidth
      || m_texture->height() != texHeight
      || !m_textureFromGpuRecon )
    {
        destroyTexture();
        m_texture = new QOpenGLTexture(QOpenGLTexture::Target2D);
        m_texture->setFormat(QOpenGLTexture::RGBA16_UNorm);
        m_texture->setSize(texWidth, texHeight);
        m_texture->setMipLevels(1);
        m_texture->allocateStorage(QOpenGLTexture::RGBA, QOpenGLTexture::UInt16);
        m_texture->setWrapMode(QOpenGLTexture::ClampToEdge);
    }
    m_textureFromGpuRecon = true;
    if ( !m_gpuReconSourceTexture
      || m_gpuReconSourceTexture->width() != texWidth
      || m_gpuReconSourceTexture->height() != texHeight )
    {
        delete m_gpuReconSourceTexture;
        m_gpuReconSourceTexture = new QOpenGLTexture(QOpenGLTexture::Target2D);
        m_gpuReconSourceTexture->setFormat(QOpenGLTexture::R16_UNorm);
        m_gpuReconSourceTexture->setSize(texWidth, texHeight);
        m_gpuReconSourceTexture->setMipLevels(1);
        m_gpuReconSourceTexture->allocateStorage(QOpenGLTexture::Red, QOpenGLTexture::UInt16);
        m_gpuReconSourceTexture->setWrapMode(QOpenGLTexture::ClampToEdge);
    }
    m_gpuReconSourceTextureCurrent = false;
    applySamplingMode();
    setupMs = elapsedMs() - setupStartMs;

    int rc = -1;
    llrpGpuPlaybackReconTiming_t reconTiming;
    memset(&reconTiming, 0, sizeof(reconTiming));
    GpuAmazeDebayerBackendTiming amazeTiming;
    QString amazeReason;
    QString amazeRenderer;
    QString handoffModeValue;
    QString directFailureReason;
    bool reconOk = false;
    bool amazeOk = false;
    bool sourceTextureCurrent = false;

    {
        llrpGpuPlaybackReconTiming_t directReconTiming;
        memset(&directReconTiming, 0, sizeof(directReconTiming));
        GpuAmazeDebayerBackendTiming directAmazeTiming;
        QString directAmazeReason;
        QString directAmazeRenderer;
        const uint16_t *deviceBayer16 = nullptr;
        int deviceWidth = 0;
        int deviceHeight = 0;
        int directRc = -1;
        const double directReconStartMs = elapsedMs();
        bool directReconOk = false;
        if ( retainedDeviceValid )
        {
            deviceBayer16 = retainedDeviceBayer16;
            deviceWidth = retainedDeviceWidth;
            deviceHeight = retainedDeviceHeight;
            directRc = 0;
            directReconOk = true;
        }
        else
        {
            directReconOk =
                llrpGpuPlaybackReconRunDeviceBayer16(state,
                                                     rawInputBayer14,
                                                     expectedWords * sizeof(uint16_t),
                                                     &deviceBayer16,
                                                     &deviceWidth,
                                                     &deviceHeight,
                                                     &directRc,
                                                     &directReconTiming) != 0;
        }
        const double directReconWallMs = elapsedMs() - directReconStartMs;
        if ( directReconOk
          && deviceBayer16
          && deviceWidth == texWidth
          && deviceHeight == texHeight )
        {
            const double directAmazeStartMs = elapsedMs();
            const bool directAmazeOk =
                gpuAmazeDebayerRenderPostWbGlTextureFromDeviceBayer16(
                    deviceBayer16,
                    m_texture->textureId(),
                    texWidth,
                    texHeight,
                    blackLevel,
                    wbMultipliers,
                    &directAmazeReason,
                    &directAmazeRenderer,
                    &directAmazeTiming);
            const double directAmazeWallMs = elapsedMs() - directAmazeStartMs;
            if ( directAmazeOk )
            {
                int validationCopyRc = 0;
                const bool validationCopyOk =
                    !validationProbeTexture
                    || llrpGpuPlaybackReconCopyLastDeviceBayer16ToGlTexture(
                        m_gpuReconSourceTexture->textureId(),
                        &validationCopyRc) != 0;
                if ( validationCopyOk )
                {
                    rc = directRc;
                    reconTiming = directReconTiming;
                    amazeTiming = directAmazeTiming;
                    amazeReason = directAmazeReason;
                    amazeRenderer = directAmazeRenderer;
                    Q_UNUSED(amazeReason);
                    Q_UNUSED(amazeRenderer);
                    reconWallMs = directReconWallMs;
                    amazeWallMs = directAmazeWallMs;
                    reconOk = true;
                    amazeOk = true;
                    handoffModeValue = retainedDeviceValid
                        ? QStringLiteral("retained_device_bayer16")
                        : QStringLiteral("direct_device_bayer16");
                    sourceTextureCurrent = validationProbeTexture;
                }
                else
                {
                    directFailureReason =
                        QStringLiteral(
                            "GPU window direct device proof texture copy failed (rc=%1)")
                            .arg(validationCopyRc);
                }
            }
            else
            {
                directFailureReason = directAmazeReason.isEmpty()
                    ? QStringLiteral("GPU window AMaZE direct device texture-present failed")
                    : directAmazeReason;
            }
        }
        else
        {
            directFailureReason =
                QStringLiteral("GPU window playback recon direct device handoff failed (recon_rc=%1)")
                    .arg(directRc);
        }
    }

    if ( !reconOk || !amazeOk )
    {
        const double reconStartMs = elapsedMs();
        reconOk =
            llrpGpuPlaybackReconRunGlTexture(state,
                                             rawInputBayer14,
                                             expectedWords * sizeof(uint16_t),
                                             m_gpuReconSourceTexture->textureId(),
                                             &rc,
                                             &reconTiming) != 0;
        reconWallMs = elapsedMs() - reconStartMs;
        const double amazeStartMs = elapsedMs();
        amazeOk =
            reconOk
            && gpuAmazeDebayerRenderPostWbGlTextureFromR16GlTexture(
                m_gpuReconSourceTexture->textureId(),
                m_texture->textureId(),
                texWidth,
                texHeight,
                blackLevel,
                wbMultipliers,
                &amazeReason,
                &amazeRenderer,
                &amazeTiming);
        amazeWallMs = elapsedMs() - amazeStartMs;
        if ( reconOk && amazeOk )
        {
            handoffModeValue = QStringLiteral("gl_r16_bridge");
            sourceTextureCurrent = true;
        }
        else if ( !directFailureReason.isEmpty() && amazeReason.isEmpty() )
        {
            amazeReason = directFailureReason;
        }
    }

    const bool ok = reconOk && amazeOk;
    if ( timing )
    {
        memset(timing, 0, sizeof(*timing));
        timing->available = reconTiming.available || amazeTiming.available;
        timing->upload_ms =
            (reconTiming.available ? reconTiming.upload_ms : 0.0)
            + (amazeTiming.available ? amazeTiming.uploadMs : 0.0);
        timing->kernel_ms =
            (reconTiming.available ? reconTiming.kernel_ms : 0.0)
            + (amazeTiming.available ? amazeTiming.kernelMs : 0.0);
        timing->interop_ms =
            (reconTiming.available ? reconTiming.interop_ms : 0.0)
            + (amazeTiming.available ? amazeTiming.downloadMs : 0.0);
        timing->total_ms =
            (reconTiming.available ? reconTiming.total_ms : 0.0)
            + (amazeTiming.available ? amazeTiming.totalMs : 0.0);
    }
    if ( !ok )
    {
        destroyTexture();
        if ( madeCurrent ) doneCurrent();
        if ( rc == LLRP_GPU_PLAYBACK_RECON_RC_UNSUPPORTED_STATE )
        {
            return fail(QStringLiteral(
                "GPU window playback recon AMaZE texture handoff skipped for unsupported live Dual ISO state (rc=%1)").arg(rc));
        }
        if ( reconOk && !amazeReason.isEmpty() )
        {
            return fail(amazeReason);
        }
        return fail(QStringLiteral(
            "GPU window playback recon AMaZE texture handoff failed (recon_rc=%1)").arg(rc));
    }

    const double postStartMs = elapsedMs();
    m_pendingImage = QImage();
    m_pendingTextureWidth = texWidth;
    m_pendingTextureHeight = texHeight;
    m_pendingDisplayWidth = displayWidth > 0 ? displayWidth : texWidth;
    m_pendingDisplayHeight = displayHeight > 0 ? displayHeight : texHeight;
    m_gpuReconSourceTextureCurrent = sourceTextureCurrent;
    m_pendingTextureFromGpuRecon = true;
    m_textureFromGpuRecon = true;
    m_textureDirty = false;
    m_texturePresentationActive = false;
    if ( madeCurrent ) doneCurrent();
    update();
    postMs = elapsedMs() - postStartMs;
    if ( handoffMode ) *handoffMode = handoffModeValue;
    if ( timing )
    {
        timing->wall_ms = elapsedMs();
        timing->host_gap_ms = timing->wall_ms - timing->total_ms;
        timing->context_ms = contextMs;
        timing->setup_ms = setupMs;
        timing->recon_wall_ms = reconWallMs;
        timing->amaze_wall_ms = amazeWallMs;
        timing->post_ms = postMs;
    }
    return true;
}

bool GpuDisplayWindow::readGpuReconSourceBayer16Texture(QByteArray *textureBytes,
                                                       int *width,
                                                       int *height,
                                                       QString *reason)
{
    auto fail = [reason](const QString &why) -> bool
    {
        if ( reason ) *reason = why;
        return false;
    };

    if ( textureBytes ) textureBytes->clear();
    if ( width ) *width = 0;
    if ( height ) *height = 0;
    if ( !textureBytes )
    {
        return fail(QStringLiteral("GPU window recon-source texture readback requires output storage"));
    }
    if ( !m_gpuReconSourceTexture
      || !m_gpuReconSourceTextureCurrent
      || !m_pendingTextureFromGpuRecon
      || m_pendingTextureWidth <= 0
      || m_pendingTextureHeight <= 0 )
    {
        return fail(QStringLiteral("GPU window recon-source texture readback requires an active GPU recon source texture"));
    }

#if defined(QT_OPENGL_ES_2)
    return fail(QStringLiteral("GL texture readback via glGetTexImage is unavailable on OpenGL ES"));
#else
    QOpenGLContext *glContext = context();
    if ( !glContext )
    {
        return fail(QStringLiteral("GPU window recon-source texture readback requires an initialized OpenGL context"));
    }
    const bool needsCurrent = QOpenGLContext::currentContext() != glContext;
    const bool madeCurrent = needsCurrent ? (makeCurrent(), true) : false;
    QOpenGLFunctions *gl = glContext->functions();
    if ( !gl )
    {
        if ( madeCurrent ) doneCurrent();
        return fail(QStringLiteral("GPU window recon-source texture readback requires OpenGL functions"));
    }
    using GlGetTexImageFn = void (*)(GLenum, GLint, GLenum, GLenum, void *);
    GlGetTexImageFn glGetTexImageProc =
        reinterpret_cast<GlGetTexImageFn>(glContext->getProcAddress("glGetTexImage"));
    if ( !glGetTexImageProc )
    {
        if ( madeCurrent ) doneCurrent();
        return fail(QStringLiteral("GL texture readback function glGetTexImage is unavailable"));
    }

    const size_t byteCount =
        static_cast<size_t>(m_pendingTextureWidth)
        * static_cast<size_t>(m_pendingTextureHeight)
        * sizeof(uint16_t);
    if ( byteCount > static_cast<size_t>(std::numeric_limits<int>::max()) )
    {
        if ( madeCurrent ) doneCurrent();
        return fail(QStringLiteral("GPU window recon-source texture is too large to read back"));
    }
    textureBytes->resize(static_cast<int>(byteCount));
    m_gpuReconSourceTexture->bind(0);
    gl->glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glGetTexImageProc(GL_TEXTURE_2D,
                      0,
                      GL_RED,
                      GL_UNSIGNED_SHORT,
                      textureBytes->data());
    const GLenum error = gl->glGetError();
    m_gpuReconSourceTexture->release();
    if ( madeCurrent ) doneCurrent();
    if ( error != GL_NO_ERROR )
    {
        textureBytes->clear();
        return fail(QStringLiteral("glGetTexImage failed for GPU window recon-source texture with GL error 0x%1")
                    .arg(static_cast<unsigned int>(error), 0, 16));
    }
    if ( width ) *width = m_pendingTextureWidth;
    if ( height ) *height = m_pendingTextureHeight;
    if ( reason ) reason->clear();
    return true;
#endif
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
    if ( m_texture || m_gpuReconSourceTexture )
    {
        gpuAmazeDebayerResetR16TextureBackendResources();
        llrpGpuPlaybackReconResetGlTextureResources();
    }
    if ( m_texture )
    {
        delete m_texture;
        m_texture = nullptr;
    }
    if ( m_gpuReconSourceTexture )
    {
        delete m_gpuReconSourceTexture;
        m_gpuReconSourceTexture = nullptr;
    }
    m_pendingTextureFromGpuRecon = false;
    m_gpuReconSourceTextureCurrent = false;
    m_textureFromGpuRecon = false;
    m_texturePresentationActive = false;
    m_pendingTextureWidth = 0;
    m_pendingTextureHeight = 0;
    m_pendingDisplayWidth = 0;
    m_pendingDisplayHeight = 0;
}

void GpuDisplayWindow::applySamplingMode()
{
    if ( !m_texture ) return;
    m_texture->setMinMagFilters(QOpenGLTexture::Linear, QOpenGLTexture::Linear);
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

    if ( m_textureFromGpuRecon )
    {
        destroyTexture();
    }
    const QImage uploadImage =
        m_pendingImage.format() == QImage::Format_RGBA8888
            ? m_pendingImage
            : m_pendingImage.convertToFormat(QImage::Format_RGBA8888);
    if ( !m_texture
      || m_texture->width() != uploadImage.width()
      || m_texture->height() != uploadImage.height() )
    {
        destroyTexture();
        m_texture = new QOpenGLTexture(QOpenGLTexture::Target2D);
        m_texture->setFormat(QOpenGLTexture::RGBA8_UNorm);
        m_texture->setSize(uploadImage.width(), uploadImage.height());
        m_texture->setMipLevels(1);
        m_texture->allocateStorage(QOpenGLTexture::RGBA, QOpenGLTexture::UInt8);
        m_texture->setWrapMode(QOpenGLTexture::ClampToEdge);
        m_texture->setMinMagFilters(QOpenGLTexture::Linear, QOpenGLTexture::Linear);
    }
    m_texture->setData(QOpenGLTexture::RGBA,
                       QOpenGLTexture::UInt8,
                       uploadImage.constBits());
    m_pendingTextureFromGpuRecon = false;
    m_gpuReconSourceTextureCurrent = false;
    m_textureFromGpuRecon = false;
    m_pendingTextureWidth = uploadImage.width();
    m_pendingTextureHeight = uploadImage.height();
    m_pendingDisplayWidth = m_pendingImage.width();
    m_pendingDisplayHeight = m_pendingImage.height();
    m_texturePresentationActive = false;
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
        m_texturePresentationActive = false;
        if ( !m_loggedPaint )
        { qInfo() << "gpu_window paintGL: no texture/program yet (clearing)."; m_loggedPaint = true; }
        return;
    }

    // Fit the frame to the window, preserving aspect ratio (letterbox).
    const float winAspect = static_cast<float>(width()) / static_cast<float>(height());
    const int displayWidth = m_pendingDisplayWidth > 0 ? m_pendingDisplayWidth : m_texture->width();
    const int displayHeight = m_pendingDisplayHeight > 0 ? m_pendingDisplayHeight : m_texture->height();
    const float imgAspect = static_cast<float>(displayWidth) / static_cast<float>(displayHeight);
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
    m_texturePresentationActive = true;

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
