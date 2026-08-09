#ifndef DECODEWORKER_H
#define DECODEWORKER_H

#include <QThread>

#include "../../src/mlv/llrawproc/llrawproc.h"

class RenderFrameThread;

class DecodeWorker : public QThread
{
public:
    explicit DecodeWorker( RenderFrameThread *parent );
    ~DecodeWorker() override;

protected:
    void run( void ) override;

private:
    RenderFrameThread *m_parent;
    llrawprocWorkerState_t m_workerState;
};

#endif // DECODEWORKER_H
