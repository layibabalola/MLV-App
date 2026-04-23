#include "../common/minitest.h"
#include "../../src/batch/WorkerThreadCount.h"

TEST(WorkerThreadCount, HonorsForcedThreadOverride)
{
    const QByteArray original = qgetenv("MLVAPP_FORCE_THREADS");
    const bool had_original = !original.isNull();

    qputenv("MLVAPP_FORCE_THREADS", QByteArrayLiteral("1"));
    ASSERT_EQ(1, mlvappEffectiveWorkerThreadCount());

    if (had_original) {
        qputenv("MLVAPP_FORCE_THREADS", original);
    } else {
        qunsetenv("MLVAPP_FORCE_THREADS");
    }
}

TEST(WorkerThreadCount, FallsBackToPositiveWorkerCount)
{
    const QByteArray original = qgetenv("MLVAPP_FORCE_THREADS");
    const bool had_original = !original.isNull();

    qunsetenv("MLVAPP_FORCE_THREADS");
    ASSERT_TRUE(mlvappEffectiveWorkerThreadCount() > 0);

    if (had_original) {
        qputenv("MLVAPP_FORCE_THREADS", original);
    }
}
