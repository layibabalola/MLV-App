/*!
 * \file test_phase3_infrastructure.cpp
 * \brief Phase 3 infrastructure tests: rollback gates, diagnostics, and
 *        deterministic debug helpers. These tests intentionally do not
 *        enable parallel playback behavior.
 */

#include "../common/minitest.h"

#include "../../platform/qt/Phase3Breadcrumbs.h"
#include "../../platform/qt/Phase3Checksums.h"
#include "../../platform/qt/Phase3Mode.h"
#include "../../platform/qt/Phase3StageTelemetry.h"
#include "../../src/batch/WorkerThreadCount.h"
#include "../../src/debug/ForceSingleThread.h"
#include "../../src/debug/FrameChecksum.h"
#include "../../src/debug/StageTiming.h"
#include "../../src/debug/StageTimingCsvSink.h"

#include <QByteArray>
#include <QDir>
#include <QFile>
#include <QTemporaryDir>
#include <QtGlobal>
#include <QStringList>

#include <cstdio>
#include <cstring>
#include <omp.h>
#include <string>
#include <vector>

namespace {

class EnvGuard {
public:
    explicit EnvGuard(const char * name)
        : m_name(name),
          m_wasSet(qEnvironmentVariableIsSet(name)),
          m_value(qgetenv(name))
    {}

    ~EnvGuard()
    {
        if (m_wasSet)
        {
            qputenv(m_name, m_value);
        }
        else
        {
            qunsetenv(m_name);
        }
    }

private:
    const char * m_name;
    bool m_wasSet;
    QByteArray m_value;
};

std::string readTextFile(const QString & path)
{
    QFile file(path);
    ASSERT_TRUE(file.open(QIODevice::ReadOnly | QIODevice::Text));
    return QString::fromUtf8(file.readAll()).toStdString();
}

void clearPhase3KillSwitches()
{
    qunsetenv("MLVAPP_DISABLE_PHASE3");
    qunsetenv("MLVAPP_DISABLE_PHASE3_3A");
    qunsetenv("MLVAPP_DISABLE_PHASE3_3B");
    qunsetenv("MLVAPP_DISABLE_PHASE3_3C");
    qunsetenv("MLVAPP_DISABLE_PHASE3_3D");
    phase3SetLiveFallbackActive(false);
}

} // namespace

TEST(Phase3Mode, DefaultKillSwitchStateKeepsModesAvailable)
{
    EnvGuard disableAll("MLVAPP_DISABLE_PHASE3");
    EnvGuard disable3A("MLVAPP_DISABLE_PHASE3_3A");
    EnvGuard disable3B("MLVAPP_DISABLE_PHASE3_3B");
    EnvGuard disable3C("MLVAPP_DISABLE_PHASE3_3C");
    EnvGuard disable3D("MLVAPP_DISABLE_PHASE3_3D");
    clearPhase3KillSwitches();

    phase3ReloadKillSwitchesForTest();

    ASSERT_FALSE(phase3KillSwitchActive(Phase3Mode::Disabled));
    ASSERT_FALSE(phase3KillSwitchActive(Phase3Mode::DecodeAheadOnly));
    ASSERT_FALSE(phase3KillSwitchActive(Phase3Mode::DecodeRecon));
    ASSERT_FALSE(phase3KillSwitchActive(Phase3Mode::DecodeReconProcess));
    ASSERT_FALSE(phase3KillSwitchActive(Phase3Mode::Full));
}

TEST(Phase3Mode, GlobalKillSwitchDisablesEveryExperimentalMode)
{
    EnvGuard disableAll("MLVAPP_DISABLE_PHASE3");
    qputenv("MLVAPP_DISABLE_PHASE3", QByteArrayLiteral("1"));

    phase3ReloadKillSwitchesForTest();

    ASSERT_FALSE(phase3KillSwitchActive(Phase3Mode::Disabled));
    ASSERT_TRUE(phase3KillSwitchActive(Phase3Mode::DecodeAheadOnly));
    ASSERT_TRUE(phase3KillSwitchActive(Phase3Mode::DecodeRecon));
    ASSERT_TRUE(phase3KillSwitchActive(Phase3Mode::DecodeReconProcess));
    ASSERT_TRUE(phase3KillSwitchActive(Phase3Mode::Full));
}

TEST(Phase3Mode, Hierarchical3AKillSwitchDisablesEveryPhase3Mode)
{
    EnvGuard disableAll("MLVAPP_DISABLE_PHASE3");
    EnvGuard disable3A("MLVAPP_DISABLE_PHASE3_3A");
    EnvGuard disable3B("MLVAPP_DISABLE_PHASE3_3B");
    EnvGuard disable3C("MLVAPP_DISABLE_PHASE3_3C");
    EnvGuard disable3D("MLVAPP_DISABLE_PHASE3_3D");
    clearPhase3KillSwitches();
    qputenv("MLVAPP_DISABLE_PHASE3_3A", QByteArrayLiteral("1"));

    phase3ReloadKillSwitchesForTest();

    ASSERT_TRUE(phase3KillSwitchActive(Phase3Mode::DecodeAheadOnly));
    ASSERT_TRUE(phase3KillSwitchActive(Phase3Mode::DecodeRecon));
    ASSERT_TRUE(phase3KillSwitchActive(Phase3Mode::DecodeReconProcess));
    ASSERT_TRUE(phase3KillSwitchActive(Phase3Mode::Full));
}

TEST(Phase3Mode, Hierarchical3BKillSwitchDisablesItsSubphaseAndLater)
{
    EnvGuard disableAll("MLVAPP_DISABLE_PHASE3");
    EnvGuard disable3A("MLVAPP_DISABLE_PHASE3_3A");
    EnvGuard disable3B("MLVAPP_DISABLE_PHASE3_3B");
    EnvGuard disable3C("MLVAPP_DISABLE_PHASE3_3C");
    EnvGuard disable3D("MLVAPP_DISABLE_PHASE3_3D");
    clearPhase3KillSwitches();
    qputenv("MLVAPP_DISABLE_PHASE3_3B", QByteArrayLiteral("1"));

    phase3ReloadKillSwitchesForTest();

    ASSERT_FALSE(phase3KillSwitchActive(Phase3Mode::DecodeAheadOnly));
    ASSERT_TRUE(phase3KillSwitchActive(Phase3Mode::DecodeRecon));
    ASSERT_TRUE(phase3KillSwitchActive(Phase3Mode::DecodeReconProcess));
    ASSERT_TRUE(phase3KillSwitchActive(Phase3Mode::Full));
}

TEST(Phase3Mode, LiveFallbackUsesAtomicStateNotEnvironmentMutation)
{
    EnvGuard disableAll("MLVAPP_DISABLE_PHASE3");
    clearPhase3KillSwitches();
    phase3ReloadKillSwitchesForTest();

    ASSERT_FALSE(phase3KillSwitchActive(Phase3Mode::Full));
    phase3SetLiveFallbackActive(true);
    ASSERT_TRUE(phase3KillSwitchActive(Phase3Mode::DecodeAheadOnly));
    ASSERT_TRUE(phase3KillSwitchActive(Phase3Mode::Full));
    phase3SetLiveFallbackActive(false);
    ASSERT_FALSE(phase3KillSwitchActive(Phase3Mode::Full));
}

TEST(Phase3_STM, ForceSingleThreadEnvOverridesWorkerAndOpenMpCounts)
{
    EnvGuard forceSingle("MLVAPP_FORCE_SINGLETHREAD");
    EnvGuard forceThreads("MLVAPP_FORCE_THREADS");
    EnvGuard ompThreads("OMP_NUM_THREADS");
    EnvGuard ompDynamic("OMP_DYNAMIC");
    qputenv("MLVAPP_FORCE_SINGLETHREAD", QByteArrayLiteral("1"));
    qputenv("MLVAPP_FORCE_THREADS", QByteArrayLiteral("8"));
    mlvapp_force_singlethread_reset_for_test();

    mlvapp_force_singlethread_init();

    ASSERT_EQ(1, mlvapp_is_forced_singlethread());
    ASSERT_EQ(1, mlvappEffectiveWorkerThreadCount());
    ASSERT_EQ(1, omp_get_max_threads());
    ASSERT_EQ(std::string("1"), std::string(qgetenv("OMP_NUM_THREADS").constData()));
}

TEST(Phase3_TEL, StreamingSinkWritesPerEventCsvRows)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    const QString path = dir.filePath(QStringLiteral("stages.csv"));

    ASSERT_EQ(1, stage_timing_csv_sink_open(path.toLocal8Bit().constData()));
    ASSERT_TRUE(stage_timing_csv_sink_enabled() != 0);
    stage_timing_csv_sink_write_event(17, 123, 2, MLV_STAGE_DECODE, "enter", 1000, 4, 9);
    stage_timing_csv_sink_write_event(17, 123, 2, MLV_STAGE_DECODE, "leave", 2000, 4, 9);
    stage_timing_csv_sink_close();

    const std::string text = readTextFile(path);
    ASSERT_TRUE(text.find("frame_idx,request_serial,slot,stage,event,ns,phase3_mode,clip_generation") != std::string::npos);
    ASSERT_TRUE(text.find("17,123,2,decode,enter,1000,4,9") != std::string::npos);
    ASSERT_TRUE(text.find("17,123,2,decode,leave,2000,4,9") != std::string::npos);
}

TEST(Phase3_TEL, ScaffoldSequenceWritesEightOrderedStageEvents)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    const QString path = dir.filePath(QStringLiteral("stages.csv"));

    ASSERT_EQ(1, stage_timing_csv_sink_open(path.toLocal8Bit().constData()));
    const char * stages[] = {
        MLV_STAGE_DECODE,
        MLV_STAGE_DECODE,
        MLV_STAGE_RECON,
        MLV_STAGE_RECON,
        MLV_STAGE_PROCESS,
        MLV_STAGE_PROCESS,
        MLV_STAGE_DISPLAY,
        MLV_STAGE_DISPLAY
    };
    const char * events[] = {
        "enter",
        "leave",
        "enter",
        "leave",
        "enter",
        "leave",
        "enter",
        "leave"
    };
    for( uint64_t i = 0; i < 8; ++i )
    {
        stage_timing_csv_sink_write_event( 21,
                                           0x100000005ull,
                                           3,
                                           stages[i],
                                           events[i],
                                           1000 + i,
                                           static_cast<uint8_t>( Phase3Mode::Full ),
                                           11 );
    }
    stage_timing_csv_sink_close();

    const QString text = QString::fromStdString(readTextFile(path)).trimmed();
    const QStringList lines = text.split(QLatin1Char('\n'), Qt::SkipEmptyParts);
    ASSERT_EQ(9, lines.size());
    ASSERT_EQ(std::string("frame_idx,request_serial,slot,stage,event,ns,phase3_mode,clip_generation"),
              lines[0].trimmed().toStdString());
    for( int i = 0; i < 8; ++i )
    {
        const QStringList cells = lines[i + 1].trimmed().split(QLatin1Char(','));
        ASSERT_EQ(8, cells.size());
        ASSERT_EQ(std::string("21"), cells[0].toStdString());
        ASSERT_EQ(std::string("4294967301"), cells[1].toStdString());
        ASSERT_EQ(std::string("3"), cells[2].toStdString());
        ASSERT_EQ(std::string(stages[i]), cells[3].toStdString());
        ASSERT_EQ(std::string(events[i]), cells[4].toStdString());
        ASSERT_EQ(1000 + i, cells[5].toInt());
        ASSERT_EQ(std::string("4"), cells[6].toStdString());
        ASSERT_EQ(std::string("11"), cells[7].toStdString());
    }
}

TEST(Phase3Telemetry, BatchWriterRemainsAvailableForOfflineReports)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    const QString path = dir.filePath(QStringLiteral("stages.csv"));

    std::vector<Phase3StageTelemetry::Event> events;
    events.push_back({17, 123, 2, "decode", "start", 1000, Phase3Mode::Full, 9});
    events.push_back({17, 123, 2, "decode", "end", 2000, Phase3Mode::Full, 9});

    QString error;
    ASSERT_TRUE(Phase3StageTelemetry::writeCsv(path, events, &error));
    const std::string text = readTextFile(path);

    ASSERT_TRUE(text.find(Phase3StageTelemetry::csvHeader()) != std::string::npos);
    ASSERT_TRUE(text.find("17,123,2,decode,start,1000,full,9") != std::string::npos);
    ASSERT_TRUE(text.find("17,123,2,decode,end,2000,full,9") != std::string::npos);
}

TEST(Phase3_CHK, Xxhash64KnownVectorsAreSharedByQtAndCApis)
{
    ASSERT_EQ(0xef46db3751d8e999ull, Phase3Checksums::xxhash64("", 0));
    ASSERT_EQ(0xef46db3751d8e999ull, frame_checksum_compute("", 0));
    const char hello[] = "hello";
    ASSERT_EQ(0x26c7827d889f6da3ull,
              Phase3Checksums::xxhash64(hello, std::strlen(hello)));
    ASSERT_EQ(0x26c7827d889f6da3ull,
              frame_checksum_compute(hello, std::strlen(hello)));
}

TEST(Phase3_CHK, FrameChecksumRingVerifiesNewestRecords)
{
    frame_checksum_reset_for_test();
    for (uint32_t i = 0; i < 300; ++i)
    {
        frame_checksum_log_record(i, 0x1000ull + i);
    }

    int found = 0;
    ASSERT_EQ(0ull, frame_checksum_log_lookup(0, &found));
    ASSERT_EQ(0, found);
    ASSERT_EQ(0x112bull, frame_checksum_log_lookup(299, &found));
    ASSERT_EQ(1, found);
    ASSERT_EQ(0, frame_checksum_verify(299, 0x112bull));
    ASSERT_NE(0, frame_checksum_verify(299, 0x9999ull));
}

TEST(Phase3_CHK, EnvGateControlsRuntimeChecksumVerification)
{
    EnvGuard enabled("MLVAPP_PHASE3_CHECK_FRAME_CHECKSUMS");
    qputenv("MLVAPP_PHASE3_CHECK_FRAME_CHECKSUMS", QByteArrayLiteral("1"));
    frame_checksum_reset_for_test();
    ASSERT_EQ(1, frame_checksum_enabled());

    qputenv("MLVAPP_PHASE3_CHECK_FRAME_CHECKSUMS", QByteArrayLiteral("0"));
    frame_checksum_reset_for_test();
    ASSERT_EQ(0, frame_checksum_enabled());
}

TEST(Phase3Checksums, WritesChecksumCsvWithPlanSchema)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    const QString path = dir.filePath(QStringLiteral("checksums.csv"));

    std::vector<Phase3Checksums::Record> records;
    records.push_back({5, 99, Phase3Mode::DecodeReconProcess, 4, 1920, 1080, 0x1234ull});

    QString error;
    ASSERT_TRUE(Phase3Checksums::writeCsv(path, records, &error));
    const std::string text = readTextFile(path);

    ASSERT_TRUE(text.find("frame_idx,request_serial,phase3_mode,scale_factor,width,height,checksum_hex")
                != std::string::npos);
    ASSERT_TRUE(text.find("5,99,decode_recon_process,4,1920,1080,0000000000001234")
                != std::string::npos);
}

TEST(Phase3_CRA, RingWrapKeepsNewestBreadcrumbsInOrder)
{
    Phase3Breadcrumbs::resetForTest();
    for (uint32_t i = 0; i < 300; ++i)
    {
        Phase3Breadcrumbs::push(static_cast<uint8_t>(i % 4),
                                1,
                                2,
                                i,
                                i,
                                i + 1000,
                                static_cast<uint8_t>(Phase3Mode::Full),
                                "test");
    }

    const std::vector<Phase3Breadcrumbs::Breadcrumb> crumbs =
        Phase3Breadcrumbs::getBreadcrumbsForTest();
    ASSERT_EQ(static_cast<std::size_t>(256), crumbs.size());
    ASSERT_EQ(44ull, crumbs.front().timestamp_ns);
    ASSERT_EQ(44u, crumbs.front().frame_idx);
    ASSERT_EQ(299ull, crumbs.back().timestamp_ns);
    ASSERT_EQ(299u, crumbs.back().frame_idx);
    ASSERT_EQ(1299u, crumbs.back().request_serial);
    ASSERT_EQ(1u, crumbs.back().from_state);
    ASSERT_EQ(2u, crumbs.back().to_state);
    ASSERT_EQ(static_cast<uint8_t>(Phase3Mode::Full), crumbs.back().phase3_mode);
    ASSERT_EQ(std::string("test"), std::string(crumbs.back().context));
}

TEST(Phase3_CRA, BreadcrumbPreservesFullRequestSerial)
{
    Phase3Breadcrumbs::resetForTest();
    const uint64_t requestSerial = 0x100000123ull;
    Phase3Breadcrumbs::push(2,
                            3,
                            4,
                            55,
                            77,
                            requestSerial,
                            static_cast<uint8_t>(Phase3Mode::Full),
                            "serial64");

    const std::vector<Phase3Breadcrumbs::Breadcrumb> crumbs =
        Phase3Breadcrumbs::getBreadcrumbsForTest();
    ASSERT_EQ(static_cast<std::size_t>(1), crumbs.size());
    ASSERT_EQ(requestSerial, crumbs.front().request_serial);
}

TEST(Phase3_CRA, DumpToFileIsCrashSafeText)
{
    Phase3Breadcrumbs::resetForTest();
    Phase3Breadcrumbs::push(1,
                            7,
                            8,
                            77,
                            42,
                            420,
                            static_cast<uint8_t>(Phase3Mode::DecodeRecon),
                            "dump");

    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    const QByteArray path =
        QDir::toNativeSeparators(dir.filePath(QStringLiteral("breadcrumbs.txt"))).toLocal8Bit();
    FILE * file = std::fopen(path.constData(), "wb+");
    ASSERT_TRUE(file != nullptr);
    Phase3Breadcrumbs::dumpToFile(file);
    std::rewind(file);
    char buffer[512] = {};
    const std::size_t read = std::fread(buffer, 1, sizeof(buffer) - 1, file);
    std::fclose(file);

    ASSERT_TRUE(read > 0);
    const std::string text(buffer);
    ASSERT_TRUE(text.find("Phase 3 Breadcrumbs") != std::string::npos);
    ASSERT_TRUE(text.find("frame=42") != std::string::npos);
    ASSERT_TRUE(text.find("request=420") != std::string::npos);
    ASSERT_TRUE(text.find("from=7") != std::string::npos);
    ASSERT_TRUE(text.find("to=8") != std::string::npos);
    ASSERT_TRUE(text.find("context=dump") != std::string::npos);
}
