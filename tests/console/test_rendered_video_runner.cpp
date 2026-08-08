#include "../common/minitest.h"

#include <string>
#include "../../src/batch/BatchTypes.h"
/* GUI-free: BatchRunner.h includes only QString + BatchTypes.h, so the static stretch
 * helpers are reachable without linking BatchRunner.cpp. */
#include "../../src/batch/BatchRunner.h"

/* Pins the pure, GUI-free pieces of the E4-1 rendered-video runner.
 *
 * Everything here is reachable without linking BatchRunner.cpp (which pulls in
 * MainWindow.h and the whole GUI), which is exactly why these pieces live in
 * BatchTypes.h. The runner's process orchestration is proven end-to-end instead, by
 * running the real binary against tests/fixtures/clips/tiny_dual_iso.mlv. */

/* ---------------------------------------------------------------------------
 * ffmpeg stream-dump parsing -- the aspect check the roadmap's blocking proof names.
 *
 * The two dumps below are VERBATIM ffmpeg n4.4 output captured from this repo's own
 * shipped binary (platform/qt/FFmpeg/ffmpegWin64.zip), not hand-written approximations.
 * That matters: the value of these tests is that they fail if ffmpeg's format changes.
 * ------------------------------------------------------------------------- */

/* Real output for a file THIS runner produced (square pixels, so no [SAR] bracket). */
static QString squarePixelDump()
{
    return QStringLiteral(
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'C:/mlvtmp/e4-1-proof/out/aspect-check.mp4':\n"
        "  Metadata:\n"
        "    encoder         : Lavf58.76.100\n"
        "  Duration: 00:00:00.08, start: 0.000000, bitrate: 265739 kb/s\n"
        "  Stream #0:0(und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(tv, bt709), 5424x2268, 267513 kb/s, 23.98 fps, 23.98 tbr, 11988 tbn, 47.95 tbc (default)\n"
        "    Metadata:\n"
        "      handler_name    : VideoHandler\n" );
}

/* Real output for a deliberately NON-square file, which is how a genuine aspect defect
 * would present: the picture would be re-stretched on playback. */
static QString nonSquarePixelDump()
{
    return QStringLiteral(
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'C:/mlvtmp/e4-1-proof/out/sar-nonsquare.mp4':\n"
        "  Duration: 00:00:01.00, start: 0.000000, bitrate: 108 kb/s\n"
        "  Stream #0:0(und): Video: h264 (High) (avc1 / 0x31637661), yuv420p, 720x576 [SAR 16:15 DAR 4:3], 104 kb/s, 25 fps, 25 tbr, 12800 tbn, 50 tbc (default)\n" );
}

TEST( RenderedVideoRunner, FfmpegDumpYieldsCodecContainerAndGeometry )
{
    const BatchRenderedVideoFfmpegDumpFacts facts =
        batchRenderedVideoFfmpegDumpFacts( squarePixelDump() );

    ASSERT_TRUE( facts.parsed );
    ASSERT_EQ( std::string("h264"), (facts.codecName).toStdString() );
    ASSERT_EQ( std::string("mov,mp4,m4a,3gp,3g2,mj2"), (facts.formatNames).toStdString() );
    ASSERT_EQ( 5424, facts.width );
    ASSERT_EQ( 2268, facts.height );
}

/* The load-bearing case: no [SAR] bracket must be read as SQUARE, and must be labelled
 * as an inference rather than as something ffmpeg asserted. */
TEST( RenderedVideoRunner, AbsentSarBracketMeansSquarePixelsAndIsLabelledImplied )
{
    const BatchRenderedVideoFfmpegDumpFacts facts =
        batchRenderedVideoFfmpegDumpFacts( squarePixelDump() );

    ASSERT_EQ( std::string("1:1(implied)"), (facts.sampleAspectRatio).toStdString() );
    ASSERT_TRUE( batchRenderedVideoSampleAspectIsSquare( facts.sampleAspectRatio ) );
    /* 5424:2268 reduced by gcd 12. A wrong gcd would still "look reasonable", so the
     * exact reduced value is pinned rather than merely a non-empty string. */
    ASSERT_EQ( std::string("452:189"), (facts.displayAspectRatio).toStdString() );
}

/* The teeth. If this check cannot fail, it is not a check -- and a parser that has only
 * ever been run against good input is untested. */
TEST( RenderedVideoRunner, NonSquareSarIsParsedAndRejected )
{
    const BatchRenderedVideoFfmpegDumpFacts facts =
        batchRenderedVideoFfmpegDumpFacts( nonSquarePixelDump() );

    ASSERT_TRUE( facts.parsed );
    ASSERT_EQ( 720, facts.width );
    ASSERT_EQ( 576, facts.height );
    ASSERT_EQ( std::string("16:15"), (facts.sampleAspectRatio).toStdString() );
    ASSERT_EQ( std::string("4:3"), (facts.displayAspectRatio).toStdString() );
    ASSERT_FALSE( batchRenderedVideoSampleAspectIsSquare( facts.sampleAspectRatio ) );
}

/* Fail closed: unparsable text must report NOTHING rather than a confident default,
 * because `parsed` is what gates the codec/container/aspect failures downstream. */
TEST( RenderedVideoRunner, UnparsableDumpClaimsNothing )
{
    for( const QString & text : QStringList{ QString(),
                                             QStringLiteral("ffmpeg version n4.4\n"),
                                             QStringLiteral("Input #0, mov,mp4, from 'x':\n  Stream #0:0: Audio: aac, 48000 Hz\n") } )
    {
        const BatchRenderedVideoFfmpegDumpFacts facts =
            batchRenderedVideoFfmpegDumpFacts( text );
        ASSERT_FALSE( facts.parsed );
        ASSERT_EQ( 0, facts.width );
        ASSERT_TRUE( facts.sampleAspectRatio.isEmpty() );
    }
}

TEST( RenderedVideoRunner, EmptySampleAspectIsTreatedAsSquare )
{
    /* ffprobe renders an UNSET sample aspect ratio as "0:1"; that is not a degenerate
     * ratio and must not be reported as an aspect defect. */
    ASSERT_TRUE( batchRenderedVideoSampleAspectIsSquare( QString() ) );
    ASSERT_TRUE( batchRenderedVideoSampleAspectIsSquare( QStringLiteral("1:1") ) );
    ASSERT_TRUE( batchRenderedVideoSampleAspectIsSquare( QStringLiteral("0:1") ) );
    ASSERT_FALSE( batchRenderedVideoSampleAspectIsSquare( QStringLiteral("16:15") ) );
    ASSERT_FALSE( batchRenderedVideoSampleAspectIsSquare( QStringLiteral("64:45") ) );
}

/* ---------------------------------------------------------------------------
 * Atomic-write temporary path.
 * ------------------------------------------------------------------------- */

/* THE MARKER MUST GO BEFORE THE EXTENSION. ffmpeg selects its muxer from the output
 * file extension, so a trailing marker makes it fail with "Unable to find a suitable
 * output format" -- observed exactly that way on the first end-to-end run of this path,
 * and this test is what stops it coming back. */
TEST( RenderedVideoRunner, PartialOutputPathKeepsTheExtensionLast )
{
    ASSERT_EQ( std::string("C:/out/clip.mlvapp-partial.mp4"), (batchRenderedVideoPartialOutputPath( QStringLiteral("C:/out/clip.mp4") )).toStdString() );
    /* A dotted directory or basename must not confuse the suffix split. */
    ASSERT_EQ( std::string("C:/my.dir/clip.v2.mlvapp-partial.mov"), (batchRenderedVideoPartialOutputPath( QStringLiteral("C:/my.dir/clip.v2.mov") )).toStdString() );
    /* No extension at all -> marker simply appended. */
    ASSERT_EQ( std::string("C:/out/clip.mlvapp-partial"), (batchRenderedVideoPartialOutputPath( QStringLiteral("C:/out/clip") )).toStdString() );
}

/* Fail closed rather than writing to a bare marker file. */
TEST( RenderedVideoRunner, PartialOutputPathRefusesEmptyInput )
{
    ASSERT_TRUE( batchRenderedVideoPartialOutputPath( QString() ).isEmpty() );
    ASSERT_TRUE( batchRenderedVideoPartialOutputPath( QStringLiteral("   ") ).isEmpty() );
}

/* ---------------------------------------------------------------------------
 * Re-aiming the ffmpeg command at the atomic-write temporary.
 * ------------------------------------------------------------------------- */

static BatchRenderedVideoFfmpegCommandPlan readyCommandPlan()
{
    BatchRenderedVideoFfmpegCommandPlan plan;
    plan.ready = true;
    plan.rawInputArguments = QStringLiteral("-r 23.976 -y -f rawvideo -s 5424x2268 -pix_fmt rgb48 -i -");
    plan.videoArguments    = QStringLiteral("-c:v libx264 -preset medium -crf 14 -pix_fmt yuv420p");
    plan.colorArguments    = QStringLiteral("-color_primaries bt709 -color_trc bt709 -colorspace bt709");
    plan.filterArguments   = QStringLiteral("-vf scale=in_color_matrix=bt601:out_color_matrix=bt709");
    plan.audioArguments    = QString();
    return plan;
}

/* The comment on batchRenderedVideoFfmpegArgumentsWithOutputPath claims the ONLY
 * difference from the assembled plan is the output token. This asserts that claim
 * literally: rebuild the expected string from the same parts and compare. */
TEST( RenderedVideoRunner, ArgumentsWithOutputPathDifferOnlyInTheOutputToken )
{
    const BatchRenderedVideoFfmpegCommandPlan plan = readyCommandPlan();
    const QString retargeted = batchRenderedVideoFfmpegArgumentsWithOutputPath(
        plan, QStringLiteral("C:/out/clip.mlvapp-partial.mp4") );

    const QString expected = QStringLiteral("%1 %2 %3 %4 %5 \"%6\"")
        .arg(plan.rawInputArguments)
        .arg(plan.videoArguments)
        .arg(plan.colorArguments)
        .arg(plan.filterArguments)
        .arg(plan.audioArguments)
        .arg(QStringLiteral("C:/out/clip.mlvapp-partial.mp4"));

    ASSERT_EQ( expected.toStdString(), retargeted.toStdString() );
    /* Every encoder/colour/filter decision survived the re-aim. */
    ASSERT_TRUE( retargeted.contains( QStringLiteral("-c:v libx264") ) );
    ASSERT_TRUE( retargeted.contains( QStringLiteral("-crf 14") ) );
    ASSERT_TRUE( retargeted.contains( QStringLiteral("out_color_matrix=bt709") ) );
}

/* An unrunnable invocation is the correct answer for a caller that forgot to check --
 * far better than a truncated command that ffmpeg would partially honour. */
TEST( RenderedVideoRunner, ArgumentsWithOutputPathRefuseUnreadyPlanOrEmptyPath )
{
    BatchRenderedVideoFfmpegCommandPlan notReady = readyCommandPlan();
    notReady.ready = false;
    ASSERT_TRUE( batchRenderedVideoFfmpegArgumentsWithOutputPath(
        notReady, QStringLiteral("C:/out/clip.mp4") ).isEmpty() );

    ASSERT_TRUE( batchRenderedVideoFfmpegArgumentsWithOutputPath(
        readyCommandPlan(), QString() ).isEmpty() );
}

/* ---------------------------------------------------------------------------
 * Runner prerequisites.
 * ------------------------------------------------------------------------- */

/* The gate that kept E4-1 unimplemented was a hardcoded `false`. These assertions are
 * what make flipping it back a test failure rather than a silent regression. */
TEST( RenderedVideoRunner, VideoOnlyPrerequisitesAreReadyAndAudioGapIsExplicit )
{
    const BatchRenderedVideoRunnerPrerequisites prerequisites =
        batchRenderedVideoRunnerPrerequisitesForCurrentBuild();

    ASSERT_TRUE( prerequisites.processingParityReady );
    ASSERT_TRUE( prerequisites.frameProcessingReady );
    ASSERT_TRUE( prerequisites.ffmpegExecutionReady );
    ASSERT_TRUE( prerequisites.outputVerificationReady );
    ASSERT_TRUE( prerequisites.headlessRunnerReady );
    ASSERT_TRUE( prerequisites.videoOnlyRunnerReady );
    ASSERT_TRUE( prerequisites.ready );

    /* Audio muxing is deferred to E4-2, and the deferral must stay LEGIBLE: a reader of
     * the summary has to be able to see the gap rather than infer it from silence. */
    ASSERT_FALSE( prerequisites.audioMuxReady );
    ASSERT_FALSE( prerequisites.limitation.isEmpty() );
    ASSERT_TRUE( prerequisites.reason.isEmpty() );

    const QString summary =
        batchRenderedVideoRunnerPrerequisitesSummary( prerequisites );
    ASSERT_TRUE( summary.contains( QStringLiteral("runner-ready=true") ) );
    ASSERT_TRUE( summary.contains( QStringLiteral("runner-audio-mux-ready=false") ) );
    ASSERT_FALSE( summary.contains( QStringLiteral("runner-limitation=none") ) );
}

/* Stretch defaults shared by the CDNG and rendered runners. A receipt that was never
 * loaded leaves stretch at -1, and a headless export that skips the GUI's fill-in emits
 * squeezed output for binned modes (5D3 1080p is binning_x=3/binning_y=1). */
TEST( RenderedVideoRunner, UnsetStretchFallsBackToTheClipAspectBands )
{
    /* An explicit receipt value always wins. */
    ASSERT_NEAR( 2.0, BatchRunner::effectiveStretchFactorX( 2.0 ), 1e-9 );
    ASSERT_NEAR( 1.75, BatchRunner::effectiveStretchFactorY( 1.75, 3.0f ), 1e-9 );

    /* Unset (-1) falls back to the band the clip's RAWC aspect lands in. */
    ASSERT_NEAR( STRETCH_H_100, BatchRunner::effectiveStretchFactorX( -1.0 ), 1e-9 );
    ASSERT_NEAR( STRETCH_V_100, BatchRunner::effectiveStretchFactorY( -1.0, 1.0f ), 1e-9 );
    ASSERT_NEAR( STRETCH_V_167, BatchRunner::effectiveStretchFactorY( -1.0, 1.67f ), 1e-9 );
    ASSERT_NEAR( STRETCH_V_300, BatchRunner::effectiveStretchFactorY( -1.0, 3.0f ), 1e-9 );

    /* No RAWC info at all is treated as square, not as an out-of-band value. */
    ASSERT_NEAR( STRETCH_V_100, BatchRunner::effectiveStretchFactorY( -1.0, 0.0f ), 1e-9 );
}
