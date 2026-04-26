#ifndef DECODEWORKER_H
#define DECODEWORKER_H

#include <QThread>

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
};

#endif // DECODEWORKER_H
