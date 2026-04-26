#include "Phase3Mode.h"

#include <QByteArray>
#include <QtGlobal>

#include <atomic>

namespace {

static std::atomic<bool> g_initialized{false};
static std::atomic<bool> g_disableAll{false};
static std::atomic<bool> g_disable3A{false};
static std::atomic<bool> g_disable3B{false};
static std::atomic<bool> g_disable3C{false};
static std::atomic<bool> g_disable3D{false};
static std::atomic<bool> g_liveFallback{false};

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

void readEnv()
{
    g_disableAll.store(envTruthy("MLVAPP_DISABLE_PHASE3"), std::memory_order_release);
    g_disable3A.store(envTruthy("MLVAPP_DISABLE_PHASE3_3A"), std::memory_order_release);
    g_disable3B.store(envTruthy("MLVAPP_DISABLE_PHASE3_3B"), std::memory_order_release);
    g_disable3C.store(envTruthy("MLVAPP_DISABLE_PHASE3_3C"), std::memory_order_release);
    g_disable3D.store(envTruthy("MLVAPP_DISABLE_PHASE3_3D"), std::memory_order_release);
}

} // namespace

const char * phase3ModeName(Phase3Mode mode) noexcept
{
    switch (mode)
    {
        case Phase3Mode::Disabled: return "disabled";
        case Phase3Mode::DecodeAheadOnly: return "decode_ahead_only";
        case Phase3Mode::DecodeRecon: return "decode_recon";
        case Phase3Mode::DecodeReconProcess: return "decode_recon_process";
        case Phase3Mode::Full: return "full";
    }
    return "unknown";
}

void phase3InitKillSwitches() noexcept
{
    bool expected = false;
    if (g_initialized.compare_exchange_strong(expected, true, std::memory_order_acq_rel))
    {
        readEnv();
    }
}

void phase3ReloadKillSwitchesForTest() noexcept
{
    readEnv();
    g_initialized.store(true, std::memory_order_release);
    g_liveFallback.store(false, std::memory_order_release);
}

void phase3SetLiveFallbackActive(bool active) noexcept
{
    g_liveFallback.store(active, std::memory_order_release);
}

bool phase3LiveFallbackActive(void) noexcept
{
    return g_liveFallback.load(std::memory_order_acquire);
}

bool phase3KillSwitchActive(Phase3Mode mode) noexcept
{
    phase3InitKillSwitches();

    if (mode == Phase3Mode::Disabled)
    {
        return false;
    }
    if (g_liveFallback.load(std::memory_order_acquire))
    {
        return true;
    }
    if (g_disableAll.load(std::memory_order_acquire))
    {
        return true;
    }
    if (mode >= Phase3Mode::DecodeAheadOnly
        && g_disable3A.load(std::memory_order_acquire))
    {
        return true;
    }
    if (mode >= Phase3Mode::DecodeRecon
        && g_disable3B.load(std::memory_order_acquire))
    {
        return true;
    }
    if (mode >= Phase3Mode::DecodeReconProcess
        && g_disable3C.load(std::memory_order_acquire))
    {
        return true;
    }
    if (mode >= Phase3Mode::Full
        && g_disable3D.load(std::memory_order_acquire))
    {
        return true;
    }
    return false;
}
