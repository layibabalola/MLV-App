// Wiring/census test for CUDA-PLAYBACK-CONTACT-SHEET-1's --contact-sheet-dir/
// --contact-sheet-frames option: pins (1) the option defaults to OFF and the capture block is
// gated on both fields being set, so an off run performs zero grabs and zero readbacks, (2) the
// capture block runs strictly AFTER playback is stopped (ui->actionPlay->setChecked( false ))
// and its idle-drain wait, which is also strictly after the timed while() loop that drives
// fps/frame-count measurement closes -- so N contact-sheet grabs never land inside the measured
// playback interval or perturb its telemetry, and (3) the CLI wires --contact-sheet-dir/
// --contact-sheet-frames together (main.cpp) with the option struct's defaults reproduced there.
// MainWindow.cpp/main.cpp need a full GUI build (not linked into console_tests), so this test
// reads the sources as text, mirroring test_gpu_window_swap_wiring.cpp's and
// test_playback_smoke_foreground_wiring.cpp's approach for the same reason.
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

QString sliceBetween(const QString & source, const QString & fromMarker, const QString & toMarker)
{
    const int at = source.indexOf(fromMarker);
    if (at < 0) return QString();
    const int next = source.indexOf(toMarker, at);
    if (next <= at) return QString();
    return source.mid(at, next - at);
}

} // namespace

TEST(ContactSheetCaptureWiring, OptionStructDefaultsToOff)
{
    const QString header = readRepoFile(QStringLiteral("platform/qt/MainWindow.h"));
    const QString optionsStruct = sliceBetween(header,
        QStringLiteral("struct GuiPlaybackSmokeOptions"),
        QStringLiteral("int runHeadlessPlaybackProfile"));
    ASSERT_FALSE(optionsStruct.isEmpty());
    ASSERT_TRUE(optionsStruct.contains(QStringLiteral("QString contactSheetDir;")));
    ASSERT_TRUE(optionsStruct.contains(QStringLiteral("int contactSheetFrames = 0;")));
}

TEST(ContactSheetCaptureWiring, CliDefaultsToOffAndRequiresBothFlagsTogether)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/main.cpp"));
    ASSERT_TRUE(source.contains(QStringLiteral("QStringLiteral(\"contact-sheet-dir\")")));
    ASSERT_TRUE(source.contains(QStringLiteral("QStringLiteral(\"contact-sheet-frames\")")));
    // The frames option's own declared default is "0" (off); a caller who never passes either
    // flag gets an options.contactSheetFrames of 0 and an empty options.contactSheetDir.
    const QString framesOptBlock = sliceBetween(source,
        QStringLiteral("contactSheetFramesOpt("),
        QStringLiteral("parser.addOption(contactSheetFramesOpt)"));
    ASSERT_FALSE(framesOptBlock.isEmpty());
    ASSERT_TRUE(framesOptBlock.contains(QStringLiteral("QStringLiteral(\"0\")")));

    // Both-or-neither: -dir set XOR frames > 0 is refused before either option struct field is
    // ever populated, so a caller can never end up with a directory and no frame count or vice
    // versa reaching runGuiPlaybackSmoke().
    ASSERT_TRUE(source.contains(
        QStringLiteral("if (contactSheetDirSet != (contactSheetFrames > 0))")));
}

TEST(ContactSheetCaptureWiring, CaptureBlockIsGatedOnBothDirAndFramesBeingSet)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString smokeBody = sliceBetween(source,
        QStringLiteral("int MainWindow::runGuiPlaybackSmoke(const GuiPlaybackSmokeOptions & options)"),
        QStringLiteral("void MainWindow::importNewMlv(QString fileName)"));
    ASSERT_FALSE(smokeBody.isEmpty());
    ASSERT_TRUE(smokeBody.contains(
        QStringLiteral("if( !options.contactSheetDir.isEmpty() && options.contactSheetFrames > 0 )")));
    // The per-frame grab loop is bounded by options.contactSheetFrames, and nowhere else in the
    // capture block hard-codes a frame count -- an off run (frames == 0) executes the loop body
    // zero times, performing zero grabPresentedFramebufferIfActive() readbacks.
    ASSERT_TRUE(smokeBody.contains(
        QStringLiteral("for( int i = 0; i < options.contactSheetFrames; ++i )")));
    const int guardAt = smokeBody.indexOf(
        QStringLiteral("if( !options.contactSheetDir.isEmpty() && options.contactSheetFrames > 0 )"));
    const int loopAt = smokeBody.indexOf(
        QStringLiteral("for( int i = 0; i < options.contactSheetFrames; ++i )"), guardAt);
    ASSERT_TRUE(guardAt >= 0);
    ASSERT_TRUE(loopAt > guardAt);
}

TEST(ContactSheetCaptureWiring, CaptureRunsStrictlyAfterPlaybackStopsAndAfterTheTimedLoopCloses)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString smokeBody = sliceBetween(source,
        QStringLiteral("int MainWindow::runGuiPlaybackSmoke(const GuiPlaybackSmokeOptions & options)"),
        QStringLiteral("void MainWindow::importNewMlv(QString fileName)"));
    ASSERT_FALSE(smokeBody.isEmpty());

    const int timedLoopAt = smokeBody.indexOf(
        QStringLiteral("while( playbackClock.elapsed() < durationMs && ui->actionPlay->isChecked() )"));
    const int stopPlaybackAt = smokeBody.indexOf(QStringLiteral("ui->actionPlay->setChecked( false );"));
    const int idleDrainAt = smokeBody.indexOf(
        QStringLiteral("for( int attempt = 0; attempt < 400 && m_pRenderThread && !m_pRenderThread->isIdle(); ++attempt )"),
        stopPlaybackAt);
    const int captureGuardAt = smokeBody.indexOf(
        QStringLiteral("if( !options.contactSheetDir.isEmpty() && options.contactSheetFrames > 0 )"));
    const int doneLineAt = smokeBody.indexOf(QStringLiteral("out << \"[GUI-SMOKE] DONE"));

    ASSERT_TRUE(timedLoopAt >= 0);
    ASSERT_TRUE(stopPlaybackAt > timedLoopAt);
    ASSERT_TRUE(idleDrainAt > stopPlaybackAt);
    ASSERT_TRUE(captureGuardAt > idleDrainAt);
    ASSERT_TRUE(doneLineAt > captureGuardAt);

    // The capture block's own grabs must never be attributed to the playback_smoke frame
    // counters or the GPU swap-telemetry session that the measured interval's fps/swap-cadence
    // numbers are computed from: finishPlaybackSmokeTelemetry() runs synchronously inside
    // on_actionPlay_toggled(false), which setChecked(false) above triggers before this test's
    // captureGuardAt position, and it is the one place m_playbackSmokeActive is cleared and the
    // swap-telemetry session is closed.
    const QString togglePlayBody = sliceBetween(source,
        QStringLiteral("void MainWindow::on_actionPlay_toggled(bool checked)"),
        QStringLiteral("void MainWindow::on_actionShowZebras_triggered()"));
    ASSERT_FALSE(togglePlayBody.isEmpty());
    ASSERT_TRUE(togglePlayBody.contains(QStringLiteral("finishPlaybackSmokeTelemetry( \"play-stop\" );")));
    const int checkedFalseBranchAt = togglePlayBody.indexOf(QStringLiteral("if( !checked )"));
    const int finishCallAt = togglePlayBody.indexOf(
        QStringLiteral("finishPlaybackSmokeTelemetry( \"play-stop\" );"), checkedFalseBranchAt);
    ASSERT_TRUE(checkedFalseBranchAt >= 0);
    ASSERT_TRUE(finishCallAt > checkedFalseBranchAt);
}

TEST(ContactSheetCaptureWiring, EachSidecarCarriesTheFieldsTheComposerAndOwnerNeed)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString smokeBody = sliceBetween(source,
        QStringLiteral("int MainWindow::runGuiPlaybackSmoke(const GuiPlaybackSmokeOptions & options)"),
        QStringLiteral("void MainWindow::importNewMlv(QString fileName)"));
    ASSERT_FALSE(smokeBody.isEmpty());
    static const char * kRequiredFields[] = {
        "index", "captured_utc", "serial", "display_frame", "elapsed_ms",
        "texture_source", "path", "look_assist_enabled", "look_assist_scene",
        "look_assist_exposure", "look_assist_contrast", "look_assist_pivot",
        "look_assist_temperature", "look_assist_tint", "settled", "saved",
    };
    for (const char * field : kRequiredFields) {
        const QString needle = QStringLiteral("QStringLiteral(\"%1\")").arg(QString::fromLatin1(field));
        ASSERT_TRUE(smokeBody.contains(needle));
    }
}

TEST(ContactSheetCaptureWiring, SeekModeDefaultsToOffSoPlaybackPassCaptureIsTheDefault)
{
    const QString header = readRepoFile(QStringLiteral("platform/qt/MainWindow.h"));
    const QString optionsStruct = sliceBetween(header,
        QStringLiteral("struct GuiPlaybackSmokeOptions"),
        QStringLiteral("int runHeadlessPlaybackProfile"));
    ASSERT_FALSE(optionsStruct.isEmpty());
    ASSERT_TRUE(optionsStruct.contains(QStringLiteral("bool contactSheetSeekMode = false;")));
}

TEST(ContactSheetCaptureWiring, CliWiresTheSeekModeFlag)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/main.cpp"));
    ASSERT_TRUE(source.contains(QStringLiteral("QStringLiteral(\"contact-sheet-seek-mode\")")));
    ASSERT_TRUE(source.contains(
        QStringLiteral("options.contactSheetSeekMode = parser.isSet(contactSheetSeekModeOpt);")));
}

TEST(ContactSheetCaptureWiring, PlaybackModeRestartsPlaybackOnlyAfterTheMeasuredIntervalAndPlaybackStopAndIdleDrain)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString smokeBody = sliceBetween(source,
        QStringLiteral("int MainWindow::runGuiPlaybackSmoke(const GuiPlaybackSmokeOptions & options)"),
        QStringLiteral("void MainWindow::importNewMlv(QString fileName)"));
    ASSERT_FALSE(smokeBody.isEmpty());

    const int timedLoopAt = smokeBody.indexOf(
        QStringLiteral("while( playbackClock.elapsed() < durationMs && ui->actionPlay->isChecked() )"));
    const int stopPlaybackAt = smokeBody.indexOf(QStringLiteral("ui->actionPlay->setChecked( false );"));
    const int idleDrainAt = smokeBody.indexOf(
        QStringLiteral("for( int attempt = 0; attempt < 400 && m_pRenderThread && !m_pRenderThread->isIdle(); ++attempt )"),
        stopPlaybackAt);
    const int captureActiveAt = smokeBody.indexOf(QStringLiteral("m_contactSheetCaptureActive = true;"));
    const int restartPlayAt = smokeBody.indexOf(QStringLiteral("ui->actionPlay->trigger();"), captureActiveAt);

    ASSERT_TRUE(timedLoopAt >= 0);
    ASSERT_TRUE(stopPlaybackAt > timedLoopAt);
    ASSERT_TRUE(idleDrainAt > stopPlaybackAt);
    // The playback-mode branch (which sets m_contactSheetCaptureActive and then restarts
    // playback) lives inside the same capture guard as the seek-mode branch, so it too must
    // sit strictly after the measured interval's stop + idle-drain -- never inside it.
    ASSERT_TRUE(captureActiveAt > idleDrainAt);
    ASSERT_TRUE(restartPlayAt > captureActiveAt);
}

TEST(ContactSheetCaptureWiring, PerPresentedFrameHookIsCalledFromTheSameSiteAsPlaybackSmokeTelemetryAndGatedOnItsOwnFlag)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));

    // Declared next to notePlaybackSmokePresentedFrame and called from the same real-
    // presented-frame call site (finishPresentedFrame), immediately after it -- so every
    // playback-mode contact-sheet grab is of a frame actually presented during playback,
    // the same event that drives the playback_smoke fps/frame counters.
    const int noteCallAt = source.indexOf(
        QStringLiteral("notePlaybackSmokePresentedFrame( displayFrame, readyFrame, requestContext );"));
    const int hookCallAt = source.indexOf(
        QStringLiteral("noteContactSheetPresentedFrame( displayFrame, readyFrame, requestContext );"));
    ASSERT_TRUE(noteCallAt >= 0);
    ASSERT_TRUE(hookCallAt > noteCallAt);
    ASSERT_TRUE(hookCallAt - noteCallAt < 200);

    // Gated on its own flag, never on m_playbackSmokeActive -- it must keep working (and keep
    // counting) after the measured interval's playback_smoke session has already closed.
    const QString hookBody = sliceBetween(source,
        QStringLiteral("void MainWindow::noteContactSheetPresentedFrame("),
        QStringLiteral("void MainWindow::finishPlaybackSmokeTelemetry"));
    ASSERT_FALSE(hookBody.isEmpty());
    ASSERT_TRUE(hookBody.contains(QStringLiteral("if( !m_contactSheetCaptureActive ) return;")));
    ASSERT_FALSE(hookBody.contains(QStringLiteral("m_playbackSmokeActive")));
}

TEST(ContactSheetCaptureWiring, PlaybackModeSidecarRecordsRenderPathAndPlaybackPathTrue)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString hookBody = sliceBetween(source,
        QStringLiteral("void MainWindow::noteContactSheetPresentedFrame("),
        QStringLiteral("void MainWindow::finishPlaybackSmokeTelemetry"));
    ASSERT_FALSE(hookBody.isEmpty());
    ASSERT_TRUE(hookBody.contains(QStringLiteral("QStringLiteral(\"render_path\")")));
    ASSERT_TRUE(hookBody.contains(
        QStringLiteral("frameJson.insert( QStringLiteral(\"playback_path\"), true );")));
    ASSERT_TRUE(hookBody.contains(
        QStringLiteral("mainWindowGpuPlaybackPipelineStatusToken( contactFramePipelineStatus )")));
}

TEST(ContactSheetCaptureWiring, SeekModeSidecarRecordsPlaybackPathFalse)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString smokeBody = sliceBetween(source,
        QStringLiteral("int MainWindow::runGuiPlaybackSmoke(const GuiPlaybackSmokeOptions & options)"),
        QStringLiteral("void MainWindow::importNewMlv(QString fileName)"));
    ASSERT_FALSE(smokeBody.isEmpty());
    // Not sliceBetween(..., "else"): the seek branch's own body contains several inner
    // if/else fallback chains, whose first "else" would truncate the slice long before the
    // sidecar-writing code below it. Slice to the playback-mode branch's own distinctive
    // first statement instead, which only appears after the seek branch's closing brace.
    const QString seekBranch = sliceBetween(smokeBody,
        QStringLiteral("if( options.contactSheetSeekMode )"),
        QStringLiteral("m_contactSheetCaptureDir = options.contactSheetDir;"));
    ASSERT_FALSE(seekBranch.isEmpty());
    ASSERT_TRUE(seekBranch.contains(
        QStringLiteral("frameJson.insert( QStringLiteral(\"playback_path\"), false );")));
}

TEST(ContactSheetCaptureWiring, GpuWindowReadbackIsTriedBeforeAnyCpuPathFallback)
{
    const QString source = readRepoFile(QStringLiteral("platform/qt/MainWindow.cpp"));
    const QString smokeBody = sliceBetween(source,
        QStringLiteral("int MainWindow::runGuiPlaybackSmoke(const GuiPlaybackSmokeOptions & options)"),
        QStringLiteral("void MainWindow::importNewMlv(QString fileName)"));
    ASSERT_FALSE(smokeBody.isEmpty());
    const int guardAt = smokeBody.indexOf(
        QStringLiteral("if( !options.contactSheetDir.isEmpty() && options.contactSheetFrames > 0 )"));
    ASSERT_TRUE(guardAt >= 0);
    const QString captureBlock = smokeBody.mid(guardAt);
    const int gpuActiveAt = captureBlock.indexOf(QStringLiteral("GpuDisplayWindow::isActive()"));
    const int gpuReadbackAt = captureBlock.indexOf(
        QStringLiteral("GpuDisplayWindow::grabPresentedFramebufferIfActive("));
    const int viewportFallbackAt = captureBlock.indexOf(
        QStringLiteral("app_internal_gl_viewport_grab"));
    const int pixmapFallbackAt = captureBlock.indexOf(
        QStringLiteral("app_internal_presented_pixmap"));
    ASSERT_TRUE(gpuActiveAt >= 0);
    ASSERT_TRUE(gpuReadbackAt > gpuActiveAt);
    ASSERT_TRUE(viewportFallbackAt > gpuReadbackAt);
    ASSERT_TRUE(pixmapFallbackAt > viewportFallbackAt);
}
