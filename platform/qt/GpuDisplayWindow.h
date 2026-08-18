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
                                     const QSize &displaySize = QSize());
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
        int displayHeight = 0);
    static bool readGpuReconSourceBayer16TextureIfActive(QByteArray *textureBytes,
                                                        int *width,
                                                        int *height,
                                                        QString *reason = nullptr);
    /* Logical size of the active preview window (empty if none). Used by the display
     * scene-geometry calc so the playback preview resolution tracks the QOpenGLWindow
     * surface, not the hidden QGraphicsView. GUI-thread only. */
    static QSize displaySize(void);

    explicit GpuDisplayWindow(QWindow *parent = nullptr);
    ~GpuDisplayWindow() override;

    void setPresentedImage(const QImage &image,
                           const QSize &displaySize = QSize());
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
        int displayHeight);
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
    bool m_loggedContext;
    bool m_loggedPaint;
    bool m_loggedPresented;
    bool m_loggedSetImage;
    bool m_loggedSetGpuTexture;
    QString m_rendererDescription;
};

#endif // GPUDISPLAYWINDOW_H
