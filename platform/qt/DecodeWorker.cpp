#include "DecodeWorker.h"

#include "RenderFrameThread.h"

DecodeWorker::DecodeWorker( RenderFrameThread *parent )
    : QThread()
    , m_parent( parent )
{
    llrpInitWorkerState( &m_workerState );
}

DecodeWorker::~DecodeWorker()
{
    wait();
    llrpFreeWorkerState( &m_workerState );
}

void DecodeWorker::run( void )
{
    while( m_parent )
    {
        RenderFrameThread::DecodeQueueEntry entry;
        if( !m_parent->takeDecodeRequestForWorker( &entry ) )
        {
            break;
        }
        m_parent->decodeFrameForWorker( entry, &m_workerState );
        m_parent->signalDecodeDoneFromWorker( entry.slotIndex );
    }
}
