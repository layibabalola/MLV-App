/*!
 * \file RenderFrameThread.cpp
 * \author masc4ii
 * \copyright 2017
 * \brief The render thread
 */

#include "RenderFrameThread.h"
#include "debug/StageTiming.h"
#include <QDebug>

//Constructor
RenderFrameThread::RenderFrameThread()
{
    m_stop = false;
    m_initialized = false;
    m_renderFrame = false;
    m_frameReady = false;
    m_pRawImage16 = nullptr;
    m_use16BitOutput = false;
}

//Destructor
RenderFrameThread::~RenderFrameThread()
{

}

//Init all objects
void RenderFrameThread::init(mlvObject_t *pMlvObject, uint8_t *pRawImage, uint16_t *pRawImage16)
{
    m_mutex.lock();
    m_frameReady = false;
    m_pMlvObject = pMlvObject;
    m_pRawImage = pRawImage;
    m_pRawImage16 = pRawImage16;
    m_mutex.unlock();
}

//Start rendering
void RenderFrameThread::renderFrame(uint32_t frameNumber, bool use16BitOutput)
{
    m_mutex.lock();
    m_frameNumber = frameNumber;
    m_use16BitOutput = use16BitOutput;
    m_renderFrame = true;
    m_frameReady = false;
    m_mutex.unlock();
}

//Is rendering finished?
bool RenderFrameThread::isFrameReady()
{
    m_mutex.lock();
    bool retVal = m_frameReady;
    m_mutex.unlock();
    return retVal;
}

//Returns if there is a frame in the pipeline...
bool RenderFrameThread::isIdle()
{
    m_mutex.lock();
    bool retVal = m_renderFrame;
    m_mutex.unlock();
    return !retVal;
}

//Stop the thread
void RenderFrameThread::stop()
{
    m_mutex.lock();
    m_stop = true;
    m_mutex.unlock();
    this->thread()->quit();
}

//Main loop of the thread
void RenderFrameThread::run(void)
{
    m_mutex.lock();
    while( !m_stop )
    {
        if( m_renderFrame )
        {
            drawFrame();
            m_renderFrame = false;
            m_frameReady = true;
        }
        m_mutex.unlock();
        msleep(1);
        m_mutex.lock();
    }
    m_stop = false;
    m_mutex.unlock();
}

//render the picture
void RenderFrameThread::drawFrame()
{
    const double render_start = mlv_stage_timing_now();
    if ( m_use16BitOutput && m_pRawImage16 )
    {
        getMlvProcessedFrame16( m_pMlvObject, m_frameNumber, m_pRawImage16, QThread::idealThreadCount() );
        mlv_stage_timing_note("render_thread_draw16", m_frameNumber, render_start);
    }
    else
    {
        getMlvProcessedFrame8( m_pMlvObject, m_frameNumber, m_pRawImage, QThread::idealThreadCount() );
        mlv_stage_timing_note("render_thread_draw", m_frameNumber, render_start);
    }
    emit frameReady();
}
