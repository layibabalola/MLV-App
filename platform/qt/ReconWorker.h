#ifndef RECONWORKER_H
#define RECONWORKER_H

#include <QThread>

#include "../../src/mlv/llrawproc/llrawproc.h"

class RenderFrameThread;

class ReconWorker : public QThread
{
public:
    explicit ReconWorker( RenderFrameThread *parent );
    ~ReconWorker() override;

protected:
    void run( void ) override;

private:
    RenderFrameThread *m_parent;
    llrawprocWorkerState_t m_workerState;
};

#endif // RECONWORKER_H
