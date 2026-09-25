// Wiring/census test: pins that GpuDisplayWindow's swap telemetry (1) is wired to BOTH
// real swap paths -- Qt's own automatic swap (QOpenGLWindow::frameSwapped) and the
// explicit manual swapBuffers() in grabPresentedFramebufferIfActive -- (2) reuses the
// existing MLVAPP_PLAYBACK_SMOKE_TELEMETRY gate rather than a new ad hoc flag, and (3) is
// actually reset/read by MainWindow's playback smoke session. GpuDisplayWindow.cpp itself
// needs a full GL/GUI build (not linked into console_tests), so this test reads the
// sources as text -- the emit sites are pinned by call-site markers, not by exercising a
// live GL window.
#include "../common/minitest.h"
#include "../common/repo_paths.h"

#include <QFile>
#include <QString>
#include <QTextStream>

namespace
{

QString readRepoFile(const QString & relativePath)
{
    const QString path = repo_file_path(relativePath);
    ASSERT_FALSE(path.isEmpty());
    QFile file(path);
    ASSERT_TRUE(file.open(QIODevice::ReadOnly | QIODevice::Text));
    QTextStream stream(&file);
    return stream.readAll();
}

int countOccurrences(const QString & haystack, const QString & needle)
{
    int count = 0;
    int from = 0;
    while (true) {
        const int at = haystack.indexOf(needle, from);
        if (at < 0) break;
        ++count;
        from = at + needle.length();
    }
    return count;
}

} // namespace

TEST(GpuWindowSwapWiring, HeaderDeclaresTheSwapTelemetryApi)
{
    const QString header = readRepoFile(QStringLiteral("platform/qt/GpuDisplayWindow.h"));
    ASSERT_TRUE(header.contains(QStringLiteral("static void resetSwapTelemetry(quint64 sessionId)")));
    ASSERT_TRUE(header.contains(QStringLiteral("static GpuWindowSwapTelemetrySnapshot swapTelemetrySnapshot(void)")));
    ASSERT_TRUE(header.contains(QStringLiteral("#include \"GpuWindowSwapTelemetry.h\"")));
}

TEST(GpuWindowSwapWiring, AutomaticSwapPathIsConnectedToTheRecorder)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/GpuDisplayWindow.cpp"));
    // Real swap path 1: Qt's own automatic swap after paintGL(), signaled by frameSwapped().
    ASSERT_TRUE(source.contains(
        QStringLiteral("connect(this, &QOpenGLWindow::frameSwapped, this, &GpuDisplayWindow::noteRealSwap)")));
}

TEST(GpuWindowSwapWiring, ManualCaptureSwapPathAlsoCallsTheRecorder)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/GpuDisplayWindow.cpp"));
    // Real swap path 2: the explicit swapBuffers() in grabPresentedFramebufferIfActive,
    // which runs outside Qt's own paint-event cycle so frameSwapped() never fires for it.
    const int swapBuffersCalls = countOccurrences(source, QStringLiteral("glContext->swapBuffers(win)"));
    const int noteRealSwapCalls = countOccurrences(source, QStringLiteral("win->noteRealSwap()"));
    ASSERT_EQ(1, swapBuffersCalls);
    ASSERT_EQ(1, noteRealSwapCalls);

    const int swapBuffersAt = source.indexOf(QStringLiteral("glContext->swapBuffers(win)"));
    const int noteRealSwapAt = source.indexOf(QStringLiteral("win->noteRealSwap()"));
    ASSERT_TRUE(swapBuffersAt >= 0 && noteRealSwapAt > swapBuffersAt);
}

TEST(GpuWindowSwapWiring, GatingReusesTheExistingSmokeTelemetryEnvVarNotANewFlag)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/GpuDisplayWindow.cpp"));
    ASSERT_TRUE(source.contains(QStringLiteral(
        "qEnvironmentVariableIsSet( \"MLVAPP_PLAYBACK_SMOKE_TELEMETRY\" )")));
    // The recorder must check the gate before doing any per-swap work.
    ASSERT_TRUE(source.contains(QStringLiteral("if ( !swapTelemetryEnabled() ) return;")));
}

TEST(GpuWindowSwapWiring, PlaybackSmokeSessionResetsAndReadsSwapTelemetry)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    ASSERT_TRUE(source.contains(
        QStringLiteral("GpuDisplayWindow::resetSwapTelemetry( m_playbackSmokeSessionId )")));
    ASSERT_TRUE(source.contains(
        QStringLiteral("GpuDisplayWindow::swapTelemetrySnapshot()")));
    ASSERT_TRUE(source.contains(QStringLiteral("playback_smoke.gpu_window_swaps")));
}
