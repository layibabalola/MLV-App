#include "../common/minitest.h"
#include "../../src/batch/WorkerThreadCount.h"

namespace {

class EnvGuard
{
public:
    explicit EnvGuard(const char *name)
        : m_name(name),
          m_original(qgetenv(name)),
          m_hadOriginal(!m_original.isNull())
    {}

    ~EnvGuard()
    {
        if (m_hadOriginal) {
            qputenv(m_name, m_original);
        } else {
            qunsetenv(m_name);
        }
    }

private:
    const char *m_name;
    QByteArray m_original;
    bool m_hadOriginal;
};

} // namespace

TEST(WorkerThreadCount, HonorsForcedThreadOverride)
{
    EnvGuard forceThreads("MLVAPP_FORCE_THREADS");
    EnvGuard forceSingle("MLVAPP_FORCE_SINGLETHREAD");

    qunsetenv("MLVAPP_FORCE_SINGLETHREAD");
    qputenv("MLVAPP_FORCE_THREADS", QByteArrayLiteral("1"));

    ASSERT_EQ(1, mlvappEffectiveWorkerThreadCount());
}

TEST(WorkerThreadCount, FallsBackToPositiveWorkerCount)
{
    EnvGuard forceThreads("MLVAPP_FORCE_THREADS");
    EnvGuard forceSingle("MLVAPP_FORCE_SINGLETHREAD");

    qunsetenv("MLVAPP_FORCE_THREADS");
    qunsetenv("MLVAPP_FORCE_SINGLETHREAD");

    ASSERT_TRUE(mlvappEffectiveWorkerThreadCount() > 0);
}

TEST(WorkerThreadCount, PlaybackWorkerCountHonorsForcedOverride)
{
    EnvGuard forceThreads("MLVAPP_FORCE_THREADS");
    EnvGuard forceSingle("MLVAPP_FORCE_SINGLETHREAD");
    EnvGuard playbackCap("MLVAPP_PLAYBACK_MAX_THREADS");
    EnvGuard disableCap("MLVAPP_DISABLE_PLAYBACK_THREAD_CAP");

    qunsetenv("MLVAPP_FORCE_SINGLETHREAD");
    qputenv("MLVAPP_FORCE_THREADS", QByteArrayLiteral("8"));
    qputenv("MLVAPP_PLAYBACK_MAX_THREADS", QByteArrayLiteral("4"));
    qunsetenv("MLVAPP_DISABLE_PLAYBACK_THREAD_CAP");

    ASSERT_EQ(8, mlvappEffectivePlaybackWorkerThreadCount());
}

TEST(WorkerThreadCount, PlaybackWorkerCountCapsAutoThreads)
{
    EnvGuard forceThreads("MLVAPP_FORCE_THREADS");
    EnvGuard forceSingle("MLVAPP_FORCE_SINGLETHREAD");
    EnvGuard playbackCap("MLVAPP_PLAYBACK_MAX_THREADS");
    EnvGuard disableCap("MLVAPP_DISABLE_PLAYBACK_THREAD_CAP");

    qunsetenv("MLVAPP_FORCE_THREADS");
    qunsetenv("MLVAPP_FORCE_SINGLETHREAD");
    qputenv("MLVAPP_PLAYBACK_MAX_THREADS", QByteArrayLiteral("1"));
    qunsetenv("MLVAPP_DISABLE_PLAYBACK_THREAD_CAP");

    ASSERT_EQ(1, mlvappEffectivePlaybackWorkerThreadCount());
}

TEST(WorkerThreadCount, PlaybackWorkerCountCanDisableCap)
{
    EnvGuard forceThreads("MLVAPP_FORCE_THREADS");
    EnvGuard forceSingle("MLVAPP_FORCE_SINGLETHREAD");
    EnvGuard playbackCap("MLVAPP_PLAYBACK_MAX_THREADS");
    EnvGuard disableCap("MLVAPP_DISABLE_PLAYBACK_THREAD_CAP");

    qunsetenv("MLVAPP_FORCE_THREADS");
    qunsetenv("MLVAPP_FORCE_SINGLETHREAD");
    qputenv("MLVAPP_PLAYBACK_MAX_THREADS", QByteArrayLiteral("1"));
    qputenv("MLVAPP_DISABLE_PLAYBACK_THREAD_CAP", QByteArrayLiteral("1"));

    ASSERT_EQ(mlvappEffectiveWorkerThreadCount(),
              mlvappEffectivePlaybackWorkerThreadCount());
}

TEST(WorkerThreadCount, PlaybackOpenMpTargetCapsOversubscribedTeams)
{
    ASSERT_EQ(6, mlvappPlaybackOpenMpThreadTargetFor(6, 16));
}

TEST(WorkerThreadCount, PlaybackOpenMpTargetRespectsLowerUserLimit)
{
    ASSERT_EQ(4, mlvappPlaybackOpenMpThreadTargetFor(6, 4));
}

TEST(WorkerThreadCount, PlaybackOpenMpTargetFallsBackToCurrentForInvalidWorkerCount)
{
    ASSERT_EQ(3, mlvappPlaybackOpenMpThreadTargetFor(0, 3));
}
