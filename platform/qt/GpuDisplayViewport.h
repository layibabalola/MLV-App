/*!
 * \file GpuDisplayViewport.h
 * \author Codex
 * \copyright 2026
 * \brief Optional OpenGL-backed viewport for the existing QGraphicsView preview.
 */

#ifndef GPUDISPLAYVIEWPORT_H
#define GPUDISPLAYVIEWPORT_H

#include <QImage>
#include <QOpenGLFunctions>
#include <QtOpenGL/qopenglshaderprogram.h>
#include <QtOpenGL/qopengltexture.h>
#include <QtOpenGLWidgets/qopenglwidget.h>

class QGraphicsPixmapItem;
class QGraphicsView;

class GpuDisplayViewport : public QOpenGLWidget, protected QOpenGLFunctions
{
    Q_OBJECT
public:
    enum SamplingMode
    {
        SamplingNearest = 0,
        SamplingLinear = 1,
        SamplingBicubic = 2
    };

    struct PresentationOptions
    {
        SamplingMode samplingMode;
        bool showZebras;
        float zebraUnderThreshold;
        float zebraOverThreshold;

        PresentationOptions()
            : samplingMode(SamplingLinear)
            , showZebras(false)
            , zebraUnderThreshold(3.0f / 255.0f)
            , zebraOverThreshold(252.0f / 255.0f)
        {
        }
    };

    explicit GpuDisplayViewport(QWidget *parent = nullptr);
    ~GpuDisplayViewport() override;

    static bool isRequestedByEnvironment(void);
    static const char *environmentVariableName(void);
    static bool installOn(QGraphicsView *view);
    static bool isInstalledOn(const QGraphicsView *view);
    static bool hasPresentedImage(const QGraphicsView *view);
    static bool isTexturePresentationActive(const QGraphicsView *view);
    static SamplingMode samplingModeFor(const QGraphicsView *view);
    static QString rendererDescriptionFor(const QGraphicsView *view);
    static bool presentImage(QGraphicsView *view,
                             QGraphicsPixmapItem *fallbackItem,
                             const QImage &image,
                             const PresentationOptions &options = PresentationOptions());
    static bool presentRgb16(QGraphicsView *view,
                             QGraphicsPixmapItem *fallbackItem,
                             const uint16_t *imageData,
                             int width,
                             int height,
                             const PresentationOptions &options = PresentationOptions());
    static void clearPresentedImage(QGraphicsView *view,
                                    QGraphicsPixmapItem *fallbackItem = nullptr);

protected:
    void initializeGL() override;
    void paintGL() override;
    void resizeGL(int w, int h) override;

private:
    static GpuDisplayViewport *from(QGraphicsView *view);
    static const GpuDisplayViewport *from(const QGraphicsView *view);

    void cleanupGLResources(void);
    void setFallbackItem(QGraphicsPixmapItem *item);
    void setPresentedImage(const QImage &image, const PresentationOptions &options);
    void setPresentedRgb16(const uint16_t *imageData, int width, int height, const PresentationOptions &options);
    void clearPresentedImage(void);
    void updateTextureIfNeeded(void);
    void ensureProgram(void);
    void destroyTexture(void);
    void applySamplingMode(void);
    QRectF targetRectInViewport(void) const;
    int pendingWidth(void) const;
    int pendingHeight(void) const;
    bool hasPendingFrame(void) const;

    bool m_loggedContext;
    bool m_loggedTexturePath;
    bool m_textureDirty;
    bool m_texturePresentationActive;
    bool m_samplingModeDirty;
    bool m_pendingTextureIs16Bit;
    bool m_textureIs16Bit;
    QGraphicsView *m_view;
    QGraphicsPixmapItem *m_fallbackItem;
    QImage m_pendingImage;
    QByteArray m_pendingTextureBytes;
    int m_pendingTextureWidth;
    int m_pendingTextureHeight;
    PresentationOptions m_presentationOptions;
    QString m_rendererDescription;
    QOpenGLShaderProgram *m_program;
    QOpenGLTexture *m_texture;
};

#endif // GPUDISPLAYVIEWPORT_H
