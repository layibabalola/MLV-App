#include "ForceSingleThread.h"

#include <QByteArray>
#include <QtGlobal>

#include <atomic>
#include <cstdlib>
#include <omp.h>

namespace {

std::atomic<int> g_initialized{0};
std::atomic<int> g_forcedSingleThread{0};

bool envTruthy(const char * name)
{
    const QByteArray value = qgetenv(name).trimmed();
    if (value.isEmpty()) return false;
    if (value == "0") return false;
    if (value.compare("false", Qt::CaseInsensitive) == 0) return false;
    if (value.compare("off", Qt::CaseInsensitive) == 0) return false;
    if (value.compare("no", Qt::CaseInsensitive) == 0) return false;
    return true;
}

void setOmpThreadsEnv()
{
#ifdef _WIN32
    _putenv_s("OMP_NUM_THREADS", "1");
    _putenv_s("OMP_DYNAMIC", "FALSE");
#else
    setenv("OMP_NUM_THREADS", "1", 1);
    setenv("OMP_DYNAMIC", "FALSE", 1);
#endif
}

} // namespace

extern "C" void mlvapp_force_singlethread_init(void)
{
    int expected = 0;
    if (!g_initialized.compare_exchange_strong(expected, 1, std::memory_order_acq_rel))
    {
        return;
    }

    if (!envTruthy("MLVAPP_FORCE_SINGLETHREAD"))
    {
        g_forcedSingleThread.store(0, std::memory_order_release);
        return;
    }

    omp_set_dynamic(0);
    omp_set_num_threads(1);
    setOmpThreadsEnv();
    g_forcedSingleThread.store(1, std::memory_order_release);
}

extern "C" int mlvapp_is_forced_singlethread(void)
{
    return g_forcedSingleThread.load(std::memory_order_acquire);
}

extern "C" void mlvapp_force_singlethread_reset_for_test(void)
{
    g_initialized.store(0, std::memory_order_release);
    g_forcedSingleThread.store(0, std::memory_order_release);
}
