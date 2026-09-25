// Wiring/census test: pins that --gui-smoke-playback's full-screen measured playback
// (CUDA-PERF-PLAYBACK-FULLSCREEN-1) (1) is entered/left ONLY from
// MainWindow::runGuiPlaybackSmoke(), never from normal (non-smoke) startup, (2) runs in
// the order foreground -> fullscreen -> foreground re-verify -> play trigger, (3) verifies
// full-screen geometry and logs the outcome, (4) is restored at session end via a scope
// guard that fires on every early-return path (not only the success path), (5) never
// unhides the actionFullscreen menu entry, and (6) that the fullscreen/viewport telemetry
// reuses the existing MLVAPP_PLAYBACK_SMOKE_TELEMETRY gate rather than a new ad hoc flag.
// MainWindow.cpp/main.cpp need a full GUI build (not linked into console_tests), so this
// test reads the sources as text -- the call sites are pinned by markers, not by exercising
// a live window (mirrors test_playback_smoke_foreground_wiring.cpp's approach).
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

TEST(PlaybackSmokeFullscreenWiring, HeaderDeclaresTheFullscreenApi)
{
    const QString header = readRepoFile(QStringLiteral("platform/qt/MainWindow.h"));
    ASSERT_TRUE(header.contains(QStringLiteral("void enterPlaybackSmokeFullscreen( void );")));
    ASSERT_TRUE(header.contains(QStringLiteral("void leavePlaybackSmokeFullscreen( void );")));
    ASSERT_TRUE(header.contains(QStringLiteral("QSize playbackSmokeViewportSize( void ) const;")));
}

TEST(PlaybackSmokeFullscreenWiring, MenuActionStaysHiddenThisRoundNeverUnhidesIt)
{
    // Explicit round-scope guardrail: the menu entry must stay hidden -- only the smoke
    // path may drive actionFullscreen, via trigger(), never by making it user-visible.
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    ASSERT_TRUE(source.contains(QStringLiteral("ui->actionFullscreen->setVisible( false );")));
    ASSERT_FALSE(source.contains(QStringLiteral("ui->actionFullscreen->setVisible( true )")));
}

TEST(PlaybackSmokeFullscreenWiring, EntryAndExitAreCalledExactlyOnceAndOnlyFromGuiPlaybackSmoke)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));

    // enterPlaybackSmokeFullscreen(): exactly one call site (the definition itself uses
    // the qualified "MainWindow::enterPlaybackSmokeFullscreen" spelling, so this
    // unqualified search counts only the call site).
    ASSERT_EQ(1, countOccurrences(source, QStringLiteral("enterPlaybackSmokeFullscreen();")));
    // leavePlaybackSmokeFullscreen(): exactly one call site, from the guard destructor --
    // never called directly, so restoration cannot be skipped by a return that forgets it.
    ASSERT_EQ(1, countOccurrences(source, QStringLiteral("window->leavePlaybackSmokeFullscreen();")));

    const QString smokeBody = functionBody(source,
        QStringLiteral("int MainWindow::runGuiPlaybackSmoke(const GuiPlaybackSmokeOptions & options)"),
        QStringLiteral("void MainWindow::importNewMlv(QString fileName)"));
    ASSERT_FALSE(smokeBody.isEmpty());
    ASSERT_TRUE(smokeBody.contains(QStringLiteral("enterPlaybackSmokeFullscreen();")));
    ASSERT_TRUE(smokeBody.contains(QStringLiteral("PlaybackSmokeFullscreenGuard")));

    // The generic Play toggle handler (fires in BOTH smoke and normal GUI mode) must never
    // enter/leave full screen directly -- only runGuiPlaybackSmoke() may.
    const QString toggleBody = functionBody(source,
        QStringLiteral("void MainWindow::on_actionPlay_toggled(bool checked)"),
        QStringLiteral("bool MainWindow::eventFilter"));
    const QString toggleSlice = toggleBody.isEmpty()
        ? source.mid(source.indexOf(QStringLiteral("void MainWindow::on_actionPlay_toggled(bool checked)")), 4000)
        : toggleBody;
    ASSERT_FALSE(toggleSlice.isEmpty());
    ASSERT_FALSE(toggleSlice.contains(QStringLiteral("enterPlaybackSmokeFullscreen")));
    ASSERT_FALSE(toggleSlice.contains(QStringLiteral("leavePlaybackSmokeFullscreen")));
}

TEST(PlaybackSmokeFullscreenWiring, NormalGuiStartupNeverEntersFullscreen)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/main.cpp"));
    const int normalGuiAt = source.indexOf(QStringLiteral("/* Normal GUI mode — unchanged */"));
    ASSERT_TRUE(normalGuiAt >= 0);
    const QString normalGuiTail = source.mid(normalGuiAt);
    ASSERT_FALSE(normalGuiTail.contains(QStringLiteral("enterPlaybackSmokeFullscreen")));
    ASSERT_TRUE(normalGuiTail.contains(QStringLiteral("w.show();")));
}

TEST(PlaybackSmokeFullscreenWiring, OrderIsForegroundThenFullscreenThenReverifiedForegroundThenPlay)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString smokeBody = functionBody(source,
        QStringLiteral("int MainWindow::runGuiPlaybackSmoke(const GuiPlaybackSmokeOptions & options)"),
        QStringLiteral("void MainWindow::importNewMlv(QString fileName)"));
    ASSERT_FALSE(smokeBody.isEmpty());

    const int targetFramesAt = smokeBody.indexOf(QStringLiteral("m_playbackSmokeTargetPresentedFrames ="));
    const int firstForegroundAt = smokeBody.indexOf(QStringLiteral("forcePlaybackSmokeWindowForeground();"), targetFramesAt);
    const int fullscreenAt = smokeBody.indexOf(QStringLiteral("enterPlaybackSmokeFullscreen();"), firstForegroundAt);
    const int secondForegroundAt = smokeBody.indexOf(
        QStringLiteral("forcePlaybackSmokeWindowForeground();"), fullscreenAt);
    const int triggerAt = smokeBody.indexOf(QStringLiteral("ui->actionPlay->trigger();"), secondForegroundAt);

    ASSERT_TRUE(targetFramesAt >= 0);
    ASSERT_TRUE(firstForegroundAt > targetFramesAt);
    ASSERT_TRUE(fullscreenAt > firstForegroundAt);
    ASSERT_TRUE(secondForegroundAt > fullscreenAt);
    ASSERT_TRUE(secondForegroundAt > firstForegroundAt); // distinct call sites, not the same match re-found
    ASSERT_TRUE(triggerAt > secondForegroundAt);
}

TEST(PlaybackSmokeFullscreenWiring, GuardRestoresOnEveryPathIncludingEarlyReturns)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    ASSERT_TRUE(source.contains(QStringLiteral(
        "struct PlaybackSmokeFullscreenGuard\n"
        "    {\n"
        "        MainWindow *window;\n"
        "        ~PlaybackSmokeFullscreenGuard() { if( window ) window->leavePlaybackSmokeFullscreen(); }\n"
        "    } playbackSmokeFullscreenGuard{ this };")));

    // At least one early-return error path exists textually AFTER the guard is
    // constructed within runGuiPlaybackSmoke() -- proving there is something for the
    // guard's destructor to actually protect, not just the final success return.
    const QString smokeBody = functionBody(source,
        QStringLiteral("int MainWindow::runGuiPlaybackSmoke(const GuiPlaybackSmokeOptions & options)"),
        QStringLiteral("void MainWindow::importNewMlv(QString fileName)"));
    ASSERT_FALSE(smokeBody.isEmpty());
    const int guardAt = smokeBody.indexOf(QStringLiteral("PlaybackSmokeFullscreenGuard"));
    ASSERT_TRUE(guardAt >= 0);
    const QString afterGuard = smokeBody.mid(guardAt);
    ASSERT_TRUE(countOccurrences(afterGuard, QStringLiteral("return 7;")) >= 1);
    ASSERT_TRUE(countOccurrences(afterGuard, QStringLiteral("return 0;")) >= 1);
}

TEST(PlaybackSmokeFullscreenWiring, EnterTriggersTheExistingActionAndVerifiesGeometryOnBothAxes)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString body = functionBody(source,
        QStringLiteral("void MainWindow::enterPlaybackSmokeFullscreen( void )"),
        QStringLiteral("void MainWindow::leavePlaybackSmokeFullscreen( void )"));
    ASSERT_FALSE(body.isEmpty());

    ASSERT_TRUE(body.contains(QStringLiteral("if( !ui->actionFullscreen->isChecked() )")));
    ASSERT_TRUE(body.contains(QStringLiteral("ui->actionFullscreen->trigger();")));
    // Main-window geometry check.
    ASSERT_TRUE(body.contains(QStringLiteral("mainVerified = isFullScreen() && screenSize.isValid() && size() == screenSize;")));
    // GPU display window/container geometry check, only when that path is active.
    ASSERT_TRUE(body.contains(QStringLiteral("if( GpuDisplayWindow::isActive() )")));
    ASSERT_TRUE(body.contains(QStringLiteral("gpuViewport = GpuDisplayWindow::displaySize();")));
    ASSERT_TRUE(body.contains(QStringLiteral("gpuVerified = screenSize.isValid() && gpuViewport == screenSize;")));
    // Bounded, not unbounded, wait for the resize to settle.
    ASSERT_TRUE(body.contains(QStringLiteral("for( int attempt = 0; attempt < 200; ++attempt )")));

    const int verifiedAt = body.indexOf(QStringLiteral("const bool verified = mainVerified && gpuVerified;"));
    const int logAt = body.indexOf(
        QStringLiteral("gui_smoke.fullscreen_request requested=1 verified=%1 screen=%2x%3 "));
    ASSERT_TRUE(verifiedAt >= 0);
    ASSERT_TRUE(logAt > verifiedAt);
    ASSERT_TRUE(body.contains(QStringLiteral("window=%4x%5 gpu_viewport=%6x%7 dpr=%8")));
}

TEST(PlaybackSmokeFullscreenWiring, LeaveOnlyTogglesWhenStillCheckedAndMirrorsCtrlF)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString body = functionBody(source,
        QStringLiteral("void MainWindow::leavePlaybackSmokeFullscreen( void )"),
        QStringLiteral("QSize MainWindow::playbackSmokeViewportSize( void ) const"));
    ASSERT_FALSE(body.isEmpty());
    ASSERT_TRUE(body.contains(QStringLiteral("if( ui->actionFullscreen->isChecked() )")));
    ASSERT_TRUE(body.contains(QStringLiteral("ui->actionFullscreen->trigger();")));
}

TEST(PlaybackSmokeFullscreenWiring, ForcePlaybackSmokeWindowForegroundIsFullscreenSafe)
{
    // Mutation-check the specific guards that keep the second (post-fullscreen) call to
    // forcePlaybackSmokeWindowForeground() from silently undoing full screen.
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString body = functionBody(source,
        QStringLiteral("void MainWindow::forcePlaybackSmokeWindowForeground( void )"),
        QStringLiteral("void MainWindow::enterPlaybackSmokeFullscreen( void )"));
    ASSERT_FALSE(body.isEmpty());
    ASSERT_TRUE(body.contains(QStringLiteral("const bool wasFullScreen = isFullScreen();")));
    ASSERT_TRUE(body.contains(QStringLiteral("if( !wasFullScreen ) showNormal();")));
    ASSERT_TRUE(body.contains(QStringLiteral("ShowWindow( target, wasFullScreen ? SW_SHOW : SW_SHOWNORMAL );")));
}

// --- Fullscreen telemetry: gated on the existing MLVAPP_PLAYBACK_SMOKE_TELEMETRY flag,
// mirroring PlaybackSmokeForegroundWiring's "not a new ad hoc flag" pin.

TEST(PlaybackSmokeFullscreenWiring, SessionBeginSamplesFullscreenAndViewportGatedOnTelemetry)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const int beginAt = source.indexOf(QStringLiteral("void MainWindow::beginPlaybackSmokeTelemetry( void )"));
    ASSERT_TRUE(beginAt >= 0);
    const QString tail = source.mid(beginAt, 2500);
    ASSERT_TRUE(tail.contains(QStringLiteral(
        "m_playbackSmokeFullscreenAtBegin =\n"
        "        m_playbackSmokeFrameTelemetry && isFullScreen();")));
    ASSERT_TRUE(tail.contains(QStringLiteral("if( m_playbackSmokeFrameTelemetry )")));
    ASSERT_TRUE(tail.contains(QStringLiteral("playbackSmokeViewportSize();")));
}

TEST(PlaybackSmokeFullscreenWiring, GateResamplesFullscreenAndViewportFreshNotReusedFromBegin)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    ASSERT_TRUE(source.contains(QStringLiteral("const bool fullscreenAtGate = isFullScreen();")));
    ASSERT_TRUE(source.contains(QStringLiteral("const QSize viewportAtGate = playbackSmokeViewportSize();")));
}

TEST(PlaybackSmokeFullscreenWiring, ForegroundLineCarriesTheNewFullscreenAndViewportFields)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const int lineAt = source.indexOf(QStringLiteral("playback_smoke.foreground session=%1"));
    ASSERT_TRUE(lineAt >= 0);
    const QString tail = source.mid(lineAt, 1200);
    ASSERT_TRUE(tail.contains(QStringLiteral("fullscreen_at_begin=%6 fullscreen_at_gate=%7")));
    ASSERT_TRUE(tail.contains(QStringLiteral("viewport_at_begin=%8x%9 viewport_at_gate=%10x%11")));
}
