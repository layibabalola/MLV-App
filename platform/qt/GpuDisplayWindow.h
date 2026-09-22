/*!
 * \file GpuDisplayWindow.h
 * \author Claude
 * \copyright 2026
 * \brief QOpenGLWindow-based GPU preview for NVIDIA Optimus hybrid laptops.
 *
 * A QOpenGLWidget set as a QGraphicsView viewport (GpuDisplayViewport) renders
 * correctly into its offscreen FBO but is NEVER composited to the panel on an NVIDIA
 * Optimus hybrid -> solid black (proven on the Dell RTX 3060 across drivers and even
 * software llvmpipe; it is the QOpenGLWidget-into-backing-store composition that fails,
 * not the GPU). A QOpenGLWindow is a real native window with its OWN swapchain that DWM
 * presents directly, so it DOES display on the hybrid (proven on the Dell: a
 * QOpenGLWindow cycles colors on the panel, both top-level and createWindowContainer-
 * embedded, rendering on the discrete 3060). This is QTBUG-68329's accepted workaround.
 *
 * This hosts the preview frame in a QOpenGLWindow embedded via
 * QWidget::createWindowContainer in place of the QGraphicsView, gated by
 * MLVAPP_EXPERIMENTAL_GL_WINDOW_VIEWPORT. Milestone 1: the CPU presentImage path
 * (passthrough RGBA). Later milestones add the GPU no-readback texture paths and a
 * shared renderer with GpuDisplayViewport.
 */
#ifndef GPUDISPLAYWINDOW_H
#define GPUDISPLAYWINDOW_H

#include <QImage>
#include <QByteArray>
#include <QSize>
#include <QString>
#include <QOpenGLFunctions>
#include <QOpenGLShaderProgram>
#include <QOpenGLTexture>
#include <QOpenGLWindow>
#include "../../src/mlv/llrawproc/llrawproc.h"
#include <cstddef>
#include <cstdint>

class QGraphicsView;

class GpuDisplayWindow : public QOpenGLWindow, protected QOpenGLFunctions
{
    Q_OBJECT
public:
    static const char *environmentVariableName(void);
    static bool isRequestedByEnvironment(void);
    static bool isActive(void);

    /* Replace the QGraphicsView with a QOpenGLWindow container in the same layout
     * slot and register it as the active preview window. Returns true on success. */
    static bool installInPreview(QGraphicsView *view);

    /* Route a CPU RGBA frame (or a clear) to the active window. Return true when the
     * window handled it (so the QOpenGLWidget/pixmap path can be skipped). Safe to call
     * from any thread; work is marshaled to the window's GUI thread. */
    static QSize effectiveDisplaySizeForImage(const QSize &imageSize,
                                              const QSize &requestedDisplaySize);
    static bool presentImageIfActive(const QImage &image,
                                     const QSize &displaySize = QSize(),
                                     quint64 presentationSerial = 0);
    static bool clearIfActive(void);
    static QString rendererDescription(void);
    static bool presentGpuPlaybackReconAmazePostWbTextureIfActive(
        const uint16_t *rawInputBayer14,
        size_t rawInputBayer14Words,
        const llrpGpuPlaybackReconState_t *state,
        int blackLevel,
        const double wbMultipliers[3],
        QString *reason = nullptr,
        llrpGpuPlaybackReconTiming_t *timing = nullptr,
        QString *handoffMode = nullptr,
        bool validationProbeTexture = false,
        const uint16_t *retainedDeviceBayer16 = nullptr,
        int retainedDeviceWidth = 0,
        int retainedDeviceHeight = 0,
        int displayWidth = 0,
        int displayHeight = 0,
        quint64 presentationSerial = 0);
    static bool readGpuReconSourceBayer16TextureIfActive(QByteArray *textureBytes,
                                                        int *width,
                                                        int *height,
                                                        QString *reason = nullptr);
    /* Read back the frame this window's OWN swapchain is about to present: makes the
     * context current, calls this window's real paintGL() SYNCHRONOUSLY on the GUI
     * thread (the same function Qt's own paint-event cycle calls), reads the resulting
     * default framebuffer with glReadPixels from inside that same paintGL() call --
     * before any further swap can leave it stale -- and only then swaps. Because
     * paintGL() runs normally (it is not suppressed or isolated in any way), it promotes
     * whatever frame is currently pending exactly as a real paint would, for BOTH the
     * QImage and the GPU-recon texture route; the pixels read back are therefore always
     * exactly what THIS call just drew and swapped. Owner-visible effect: a capture
     * presents a pending frame one paint early, rather than waiting for Qt's own next
     * paint event -- there is no "stale-but-valid" gap, because capture and paint are the
     * same synchronous operation and never race. Never a screen-region capture: pixels
     * come only from this window's own default framebuffer. GUI-thread only; call after a
     * completed present. Image size is the window's real device-pixel framebuffer size.
     * presentedSerial, when non-null, receives the presentationSerial (see
     * presentImageIfActive / presentGpuPlaybackReconAmazePostWbTextureIfActive) of the
     * frame this call actually presented, and presentedSerialValid reports whether that
     * route supplied a real serial (false before any frame has ever been presented, after
     * clearIfActive(), or when a route did not supply a presentationSerial). */
    static bool grabPresentedFramebufferIfActive(QImage *outImage,
                                                 QString *reason = nullptr,
                                                 quint64 *presentedSerial = nullptr,
                                                 bool *presentedSerialValid = nullptr);
    /* Logical size of the active preview window (empty if none). Used by the display
     * scene-geometry calc so the playback preview resolution tracks the QOpenGLWindow
     * surface, not the hidden QGraphicsView. GUI-thread only. */
    static QSize displaySize(void);

    explicit GpuDisplayWindow(QWindow *parent = nullptr);
    ~GpuDisplayWindow() override;

    void setPresentedImage(const QImage &image,
                           const QSize &displaySize = QSize(),
                           quint64 presentationSerial = 0);
    void clearPresented(void);
    bool setPresentedGpuPlaybackReconAmazePostWbTexture(
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
        int displayHeight,
        quint64 presentationSerial = 0);
    bool readGpuReconSourceBayer16Texture(QByteArray *textureBytes,
                                          int *width,
                                          int *height,
                                          QString *reason);

protected:
    void initializeGL() override;
    void paintGL() override;

private:
    void ensureProgram(void);
    void updateTextureIfNeeded(void);
    void destroyTexture(void);
    void applySamplingMode(void);

    QOpenGLShaderProgram *m_program;
    QOpenGLTexture *m_texture;
    QOpenGLTexture *m_gpuReconSourceTexture;
    QImage m_pendingImage;
    int m_pendingTextureWidth;
    int m_pendingTextureHeight;
    int m_pendingDisplayWidth;
    int m_pendingDisplayHeight;
    bool m_gpuReconSourceTextureCurrent;
    bool m_pendingTextureFromGpuRecon;
    bool m_textureFromGpuRecon;
    bool m_texturePresentationActive;
    bool m_textureDirty;
    quint64 m_pendingPresentationSerial;
    bool m_pendingPresentationSerialValid;
    quint64 m_presentedSerial;
    bool m_presentedSerialValid;
    /* Set only for the duration of a synchronous grabPresentedFramebufferIfActive()
     * call: tells paintGL() to glReadPixels into m_captureReadback* right after it
     * finishes drawing, so the readback and the draw it reads back are the same call. */
    bool m_captureReadbackRequested;
    bool m_captureReadbackSucceeded;
    QImage m_captureReadbackImage;
    QString m_captureReadbackError;
    bool m_loggedContext;
    bool m_loggedPaint;
    bool m_loggedPresented;
    bool m_loggedSetImage;
    bool m_loggedSetGpuTexture;
    QString m_rendererDescription;
};

#endif // GPUDISPLAYWINDOW_H
