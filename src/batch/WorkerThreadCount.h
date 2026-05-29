#ifndef MLVAPP_WORKER_THREAD_COUNT_H
#define MLVAPP_WORKER_THREAD_COUNT_H

#include <QByteArray>
#include <QThread>
#include <QtGlobal>

inline bool mlvappWorkerThreadOverrideActive()
{
    bool singleThreadOk = false;
    const int singleThread =
        qEnvironmentVariableIntValue("MLVAPP_FORCE_SINGLETHREAD", &singleThreadOk);
    if (singleThreadOk && singleThread > 0) {
        return true;
    }

    bool forcedOk = false;
    const int forced =
        qEnvironmentVariableIntValue("MLVAPP_FORCE_THREADS", &forcedOk);
    return forcedOk && forced > 0;
}

inline bool mlvappEnvFlagEnabled(const char *name)
{
    const QByteArray value = qgetenv(name);
    if (value.isNull()) {
        return false;
    }

    const QByteArray normalized = value.trimmed().toLower();
    return normalized.isEmpty()
        || (normalized != QByteArrayLiteral("0")
            && normalized != QByteArrayLiteral("false")
            && normalized != QByteArrayLiteral("off"));
}

/* Test and debug override.
 * When MLVAPP_FORCE_THREADS is set to a positive integer, prefer that value
 * over the host's ideal thread count so pipeline outputs can be reproduced
 * deterministically across machines. */
inline int mlvappEffectiveWorkerThreadCount()
{
    bool singleThreadOk = false;
    const int singleThread =
        qEnvironmentVariableIntValue("MLVAPP_FORCE_SINGLETHREAD", &singleThreadOk);
    if (singleThreadOk && singleThread > 0) {
        return 1;
    }

    bool ok = false;
    const int forced = qEnvironmentVariableIntValue("MLVAPP_FORCE_THREADS", &ok);
    if (ok && forced > 0) {
        return forced;
    }

    const int ideal = QThread::idealThreadCount();
    return ideal > 0 ? ideal : 1;
}

/* GUI playback can become slower when CPU-bound VMs spawn more llrawproc and
 * processing workers than the host can run without contention. Keep explicit
 * test/user thread overrides exact, but cap auto playback to a small default. */
inline int mlvappEffectivePlaybackWorkerThreadCount()
{
    const int workerThreads = mlvappEffectiveWorkerThreadCount();
    if (mlvappWorkerThreadOverrideActive()
        || mlvappEnvFlagEnabled("MLVAPP_DISABLE_PLAYBACK_THREAD_CAP")) {
        return workerThreads;
    }

    bool capOk = false;
    int cap = qEnvironmentVariableIntValue("MLVAPP_PLAYBACK_MAX_THREADS", &capOk);
    if (!capOk || cap <= 0) {
        cap = 6;
    }

    return qBound(1, workerThreads, cap);
}

#endif
