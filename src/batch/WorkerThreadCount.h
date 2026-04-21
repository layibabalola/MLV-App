#ifndef MLVAPP_WORKER_THREAD_COUNT_H
#define MLVAPP_WORKER_THREAD_COUNT_H

#include <QThread>
#include <QtGlobal>

/* Test and debug override.
 * When MLVAPP_FORCE_THREADS is set to a positive integer, prefer that value
 * over the host's ideal thread count so pipeline outputs can be reproduced
 * deterministically across machines. */
inline int mlvappEffectiveWorkerThreadCount()
{
    bool ok = false;
    const int forced = qEnvironmentVariableIntValue("MLVAPP_FORCE_THREADS", &ok);
    if (ok && forced > 0) {
        return forced;
    }

    const int ideal = QThread::idealThreadCount();
    return ideal > 0 ? ideal : 1;
}

#endif
