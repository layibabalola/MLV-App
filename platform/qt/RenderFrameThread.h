/*!
 * \file RenderFrameThread.h
 * \author masc4ii
 * \copyright 2017
 * \brief The render thread
 */

#ifndef RENDERFRAMETHREAD_H
#define RENDERFRAMETHREAD_H

#include <QThread>
#include <QMutex>
#include <QString>
#include <QJsonObject>
#include "../../src/mlv_include.h"

#include <vector>

class RenderFrameThread : public QThread
{
    Q_OBJECT

public:
    enum OutputMode
    {
        OutputProcessed8 = 0,
        OutputProcessed16,
        OutputDebayered16
    };

    RenderFrameThread();
    ~RenderFrameThread();
    void init( mlvObject_t *pMlvObject,
          uint8_t *pRawImage,
          uint16_t *pRawImage16 );
    void renderFrame( uint32_t frameNumber,
                      OutputMode outputMode = OutputProcessed8,
                      bool useGpuBilinearDebayer = false );
    bool isFrameReady( void );
    bool isIdle( void );
    bool lastFrameUsedGpuBilinearDebayer( void ) const;
    QString lastGpuBilinearFallbackReason( void ) const;
    QString lastGpuBilinearRendererDescription( void ) const;
    double lastDualIsoPreviewHistogramMilliseconds( void ) const;
    double lastDualIsoPreviewRegressionMilliseconds( void ) const;
    double lastDualIsoPreviewRowscaleMilliseconds( void ) const;
    QJsonObject lastStageTimingTelemetry( void ) const;
    double lastFrameReadyEmitStageTime( void ) const;
    void stop( void );
    void lock( void ){ m_mutex.lock(); }
    void unlock( void ){ m_mutex.unlock(); }

signals:
    void frameReady( void );

private:
    mutable QMutex m_mutex;
    mlvObject_t *m_pMlvObject;
    uint8_t *m_pRawImage;
    uint16_t *m_pRawImage16;
    bool m_initialized;
    bool m_stop;
    bool m_renderFrame;
    bool m_frameReady;
    OutputMode m_outputMode;
    bool m_useGpuBilinearDebayer;
    uint32_t m_frameNumber;
    bool m_loggedGpuBilinearSuccess;
    bool m_lastFrameUsedGpuBilinearDebayer;
    QString m_lastGpuBilinearFallbackReason;
    QString m_lastGpuBilinearRendererDescription;
    double m_lastDualIsoPreviewHistogramMs;
    double m_lastDualIsoPreviewRegressionMs;
    double m_lastDualIsoPreviewRowscaleMs;
    double m_frameRequestStageTime;
    double m_lastRenderThreadQueueWaitMs;
    double m_lastRenderThreadWorkMs;
    double m_lastRenderThreadTotalMs;
    double m_lastFrameReadyEmitStageTime;
    QJsonObject m_lastStageTimingTelemetry;
    std::vector<float> m_gpuBilinearDebayerRawFrame;

    void run( void );
    void drawFrame( void );
};

#endif // RENDERFRAMETHREAD_H
