#include "ReconWorker.h"

#include "RenderFrameThread.h"

ReconWorker::ReconWorker( RenderFrameThread *parent )
    : QThread()
    , m_parent( parent )
{
    llrpInitWorkerState( &m_workerState );
}

ReconWorker::~ReconWorker()
{
    wait();
    llrpFreeWorkerState( &m_workerState );
}

void ReconWorker::run( void )
{
    while( m_parent )
    {
        RenderFrameThread::ReconQueueEntry entry;
        if( !m_parent->takeReconRequestForWorker( &entry ) )
        {
            break;
        }
        m_parent->reconFrameForWorker( entry, &m_workerState );
        m_parent->signalReconDoneFromWorker( entry.slotIndex );
    }
}
