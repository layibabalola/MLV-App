// Wiring/census test: pins that CUDA-PERF-DISPLAY-WAKE-1's display-wake request (1) is set once
// on play start and released once on stop AND on window close, never per frame, (2) is Windows-
// only (SetThreadExecutionState under Q_OS_WIN, a no-op elsewhere) and never gated on any
// telemetry flag, and (3) that beginPlaybackSmokeTelemetry() logs playback_smoke.display_required
// once per playback session. MainWindow.cpp needs a full GUI build (not linked into
// console_tests), so this test reads the source as text -- the call sites are pinned by markers,
// not by exercising a live window (mirrors test_playback_smoke_foreground_wiring.cpp's approach
// for the same reason).
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

QString functionBody(const QString & source, const QString & signature, const QString & nextSignature)
{
    const int at = source.indexOf(signature);
    const int next = source.indexOf(nextSignature, at);
    if (at < 0 || next <= at) return QString();
    return source.mid(at, next - at);
}

} // namespace

TEST(PlaybackDisplayWakeWiring, HelperIsWindowsOnlyAndUsesTheRightFlags)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString body = functionBody(source,
        QStringLiteral("static void setPlaybackDisplayRequiredExecutionState( bool required )"),
        QStringLiteral("/* spaceTag argument options"));
    ASSERT_FALSE(body.isEmpty());

    const int ifdefAt = body.indexOf(QStringLiteral("#ifdef Q_OS_WIN"));
    const int callAt = body.indexOf(QStringLiteral(
        "SetThreadExecutionState( required ? ( ES_CONTINUOUS | ES_DISPLAY_REQUIRED ) : ES_CONTINUOUS );"));
    const int elseAt = body.indexOf(QStringLiteral("#else"), callAt);
    ASSERT_TRUE(ifdefAt >= 0);
    ASSERT_TRUE(callAt > ifdefAt);
    ASSERT_TRUE(elseAt > callAt);
}

TEST(PlaybackDisplayWakeWiring, CalledExactlyThreeTimesNeverPerFrame)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    // Three call sites: play-start (checked), play-stop (!checked), closeEvent -- each passes a
    // literal bool argument, unlike the definition's "( bool required )" parameter, so counting
    // the two literal-argument spellings excludes the (unqualified, since this is a free
    // function, not a member) definition line.
    const int callCount =
        countOccurrences(source, QStringLiteral("setPlaybackDisplayRequiredExecutionState( true )")) +
        countOccurrences(source, QStringLiteral("setPlaybackDisplayRequiredExecutionState( false )"));
    ASSERT_EQ(3, callCount);
}

TEST(PlaybackDisplayWakeWiring, SetOnPlayStartBeforeSmokeTelemetryBegins)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString toggleBody = functionBody(source,
        QStringLiteral("void MainWindow::on_actionPlay_toggled(bool checked)"),
        QStringLiteral("//Zebras en-/disabled"));
    ASSERT_FALSE(toggleBody.isEmpty());

    const int checkedBlockAt = toggleBody.indexOf(QStringLiteral("if( checked )\n    {"));
    ASSERT_TRUE(checkedBlockAt >= 0);
    const QString checkedBlock = toggleBody.mid(checkedBlockAt);

    const int acquireAt = checkedBlock.indexOf(QStringLiteral("setPlaybackDisplayRequiredExecutionState( true );"));
    const int beginAt = checkedBlock.indexOf(QStringLiteral("beginPlaybackSmokeTelemetry();"));
    ASSERT_TRUE(acquireAt >= 0);
    ASSERT_TRUE(beginAt > acquireAt);
}

TEST(PlaybackDisplayWakeWiring, ReleasedOnStopUnconditionallyNotGatedOnSmokeActive)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString toggleBody = functionBody(source,
        QStringLiteral("void MainWindow::on_actionPlay_toggled(bool checked)"),
        QStringLiteral("//Zebras en-/disabled"));
    ASSERT_FALSE(toggleBody.isEmpty());

    const int stopBlockAt = toggleBody.indexOf(QStringLiteral("if( !checked )\n    {"));
    ASSERT_TRUE(stopBlockAt >= 0);
    const int finishAt = toggleBody.indexOf(QStringLiteral("finishPlaybackSmokeTelemetry( \"play-stop\" );"), stopBlockAt);
    const int releaseAt = toggleBody.indexOf(QStringLiteral("setPlaybackDisplayRequiredExecutionState( false );"), stopBlockAt);
    ASSERT_TRUE(finishAt > stopBlockAt);
    ASSERT_TRUE(releaseAt >= 0 && releaseAt < finishAt);
}

TEST(PlaybackDisplayWakeWiring, ReleasedOnWindowCloseBeforeAnyOtherCloseWork)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString body = functionBody(source,
        QStringLiteral("void MainWindow::closeEvent(QCloseEvent *event)"),
        QStringLiteral("//Disable WBPicker"));
    ASSERT_FALSE(body.isEmpty());

    const int releaseAt = body.indexOf(QStringLiteral("setPlaybackDisplayRequiredExecutionState( false );"));
    const int setCheckedAt = body.indexOf(QStringLiteral("ui->actionPlay->setChecked( false );"));
    ASSERT_TRUE(releaseAt >= 0);
    ASSERT_TRUE(setCheckedAt > releaseAt);
}

TEST(PlaybackDisplayWakeWiring, BeginTelemetryLogsDisplayRequiredOncePerSession)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString body = functionBody(source,
        QStringLiteral("void MainWindow::beginPlaybackSmokeTelemetry( void )"),
        QStringLiteral("void MainWindow::notePlaybackSmokePresentedFrame("));
    ASSERT_FALSE(body.isEmpty());

    // Exactly one occurrence inside a single begin-session function body: logged once per
    // playback session, not once per frame.
    const int logLineCount = countOccurrences(body, QStringLiteral("playback_smoke.display_required session=%1"));
    ASSERT_EQ(1, logLineCount);
}
