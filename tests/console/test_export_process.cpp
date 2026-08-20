#include "../common/minitest.h"
#include "../../platform/qt/ExportProcess.h"

#include <QCoreApplication>
#include <QRegularExpression>

TEST( ExportProcess, RawVideoInvocationPreservesProgramAndArgumentBoundaries )
{
    const QString program = QStringLiteral("C:/Program Files/ffmpeg & tools/ffmpeg.exe");
    const export_process::Invocation invocation =
        export_process::rawVideoInvocation( program, QStringLiteral("23.976"), QStringLiteral("1920x1080") );

    ASSERT_TRUE( program == invocation.program );
    ASSERT_EQ( 11, invocation.arguments.size() );
    ASSERT_TRUE( QStringLiteral("23.976") == invocation.arguments.at( 1 ) );
    ASSERT_TRUE( QStringLiteral("1920x1080") == invocation.arguments.at( 6 ) );
    ASSERT_TRUE( QStringLiteral("-") == invocation.arguments.at( 10 ) );
}

TEST( ExportProcess, UserPathsStaySingleArgumentsWithoutQuotingOrEscaping )
{
    export_process::Invocation invocation =
        export_process::rawVideoInvocation( QStringLiteral("ffmpeg"), QStringLiteral("24"), QStringLiteral("1920x1080") );
    const QString path = QStringLiteral("C:/shots/O'Brien & crew/quote \"test\".mov");
    invocation.arguments << QStringLiteral("-c:v") << QStringLiteral("cfhd") << path;

    ASSERT_TRUE( path == invocation.arguments.constLast() );
    ASSERT_FALSE( invocation.arguments.constLast().startsWith( QLatin1Char('"') ) );
    ASSERT_FALSE( invocation.arguments.constLast().endsWith( QLatin1Char('"') ) );
}

TEST( ExportProcess, TemplateReplacementPreservesSpacesQuotesAndShellMetacharacters )
{
    const QString output = QStringLiteral("C:/shots/O'Brien & crew/quote \"test\".mov");
    const export_process::Invocation invocation = export_process::invocationFromTemplate(
        QStringLiteral("ffmpeg"),
        QStringLiteral("-y -i - -c:v cfhd __MLVAPP_OUTPUT__"),
        { { QStringLiteral("__MLVAPP_OUTPUT__"), output } } );

    ASSERT_EQ( 6, invocation.arguments.size() );
    ASSERT_TRUE( output == invocation.arguments.constLast() );
}

TEST( ExportProcess, TemplateReplacementCanInjectPathInsideFilterArgument )
{
    const QString transform = QStringLiteral("vidstabtransform=input=C:/take with spaces/transforms.trf:smoothing=10");
    const export_process::Invocation invocation = export_process::invocationFromTemplate(
        QStringLiteral("ffmpeg"),
        QStringLiteral("-i - -vf __MLVAPP_FILTER__ __MLVAPP_OUTPUT__"),
        { { QStringLiteral("__MLVAPP_FILTER__"), transform },
          { QStringLiteral("__MLVAPP_OUTPUT__"), QStringLiteral("C:/out clip.mov") } } );

    ASSERT_EQ( 5, invocation.arguments.size() );
    ASSERT_TRUE( transform == invocation.arguments.at( 3 ) );
    ASSERT_TRUE( QStringLiteral("C:/out clip.mov") == invocation.arguments.constLast() );
}

TEST( ExportProcess, MissingExecutableFailsToStartWithDiagnosticsAvailable )
{
    export_process::StreamingProcess process;
    export_process::Invocation invocation;
    invocation.program = QStringLiteral("definitely-missing-mlvapp-ffmpeg-executable");

    ASSERT_FALSE( process.start( invocation, 100 ) );
}

TEST( ExportProcess, NonzeroChildExitCapturesStderrMarker )
{
    export_process::Invocation invocation;
#ifdef Q_OS_WIN
    invocation.program = QStringLiteral("cmd.exe");
    invocation.arguments << QStringLiteral("/D")
                         << QStringLiteral("/S")
                         << QStringLiteral("/C")
                         << QStringLiteral("echo STDERR-DIAG-MARKER 1>&2 & exit /b 7");
#else
    invocation.program = QStringLiteral("/bin/sh");
    invocation.arguments << QStringLiteral("-c")
                         << QStringLiteral("echo STDERR-DIAG-MARKER 1>&2; exit 7");
#endif

    export_process::StreamingProcess process;
    ASSERT_TRUE( process.start( invocation ) );
    ASSERT_FALSE( process.finish() );
    ASSERT_TRUE( process.diagnostics().contains( QStringLiteral("STDERR-DIAG-MARKER") ) );
}

TEST( ExportProcess, PipelineStreamsAcrossTwoProcessesWithoutShellParsing )
{
    QVector<export_process::Invocation> stages;
#ifdef Q_OS_WIN
    stages << export_process::Invocation{ QStringLiteral("more.com"), {} }
           << export_process::Invocation{ QStringLiteral("findstr.exe"),
                                          { QStringLiteral("PIPELINE-MARKER") } };
#else
    stages << export_process::Invocation{ QStringLiteral("/bin/cat"), {} }
           << export_process::Invocation{ QStringLiteral("/bin/grep"),
                                          { QStringLiteral("PIPELINE-MARKER") } };
#endif
    export_process::StreamingPipeline pipeline;
    const QByteArray input = QByteArrayLiteral("PIPELINE-MARKER\n");
    ASSERT_TRUE( pipeline.start( stages ) );
    ASSERT_TRUE( pipeline.writeAll( input.constData(), input.size() ) );
    ASSERT_TRUE( pipeline.finish() );
    ASSERT_TRUE( pipeline.diagnostics().contains( QStringLiteral("PIPELINE-MARKER") ) );
}

TEST( ExportProcess, PipelineDrainsHighVolumeStderrWithoutStalling )
{
    QVector<export_process::Invocation> stages;
    stages << export_process::Invocation{
                  QCoreApplication::applicationFilePath(),
                  { QStringLiteral("--pipeline-noisy-helper") } }
           << export_process::Invocation{
                  QCoreApplication::applicationFilePath(),
                  { QStringLiteral("--pipeline-match-helper") } };
    export_process::StreamingPipeline pipeline;
    const QByteArray input = QByteArrayLiteral("PIPELINE-DATA\n");
    ASSERT_TRUE( pipeline.start( stages ) );
    ASSERT_TRUE( pipeline.writeAll( input.constData(), input.size(), 10000 ) );
    const bool finished = pipeline.finish( 30000 );
    if ( !finished ) {
        std::cerr << "High-volume stderr pipeline failed: "
                  << pipeline.diagnostics().toStdString() << "\n";
    }
    // The matcher returns zero only after receiving the exact line and EOF, so
    // finish() proves transfer. The shared diagnostic tail intentionally makes
    // no cross-process ordering guarantee; assert only its deterministic cap.
    ASSERT_TRUE( finished );
    const QString diagnostic_text = pipeline.diagnostics();
    const QByteArray diagnostic_bytes = diagnostic_text.toUtf8();
    ASSERT_TRUE( diagnostic_bytes.size() <= 8192 );

    bool saw_first_noise = false;
    bool saw_last_noise = false;
    const QStringList diagnostic_lines = diagnostic_text.split(
        QRegularExpression(QStringLiteral("[\\r\\n]+")), Qt::SkipEmptyParts );
    for ( const QString &line : diagnostic_lines ) {
        const QString trimmed = line.trimmed();
        saw_first_noise = saw_first_noise || trimmed == QStringLiteral("NOISY-STDERR-1");
        saw_last_noise = saw_last_noise || trimmed == QStringLiteral("NOISY-STDERR-4000");
    }
    ASSERT_FALSE( saw_first_noise );
    ASSERT_TRUE( saw_last_noise );
}

TEST( ExportProcess, PipelineMidStreamChildDeathIsFailureWithDiagnostics )
{
    QVector<export_process::Invocation> stages;
#ifdef Q_OS_WIN
    stages << export_process::Invocation{
                  QStringLiteral("cmd.exe"),
                  { QStringLiteral("/D"), QStringLiteral("/S"), QStringLiteral("/C"),
                    QStringLiteral("echo MIDSTREAM-CHILD-DIED 1>&2 & exit /b 7") } }
           << export_process::Invocation{ QStringLiteral("more.com"), {} };
#else
    stages << export_process::Invocation{
                  QStringLiteral("/bin/sh"),
                  { QStringLiteral("-c"), QStringLiteral("echo MIDSTREAM-CHILD-DIED >&2; exit 7") } }
           << export_process::Invocation{ QStringLiteral("/bin/cat"), {} };
#endif
    export_process::StreamingPipeline pipeline;
    ASSERT_TRUE( pipeline.start( stages ) );
    const QByteArray input( 1024 * 1024, 'x' );
    const bool writeOk = pipeline.writeAll( input.constData(), input.size(), 5000 );
    const bool finishOk = writeOk && pipeline.finish( 10000 );
    if( !writeOk ) pipeline.cancel();
    ASSERT_FALSE( finishOk );
    ASSERT_TRUE( pipeline.diagnostics().contains( QStringLiteral("MIDSTREAM-CHILD-DIED") ) );
}

TEST( ExportProcess, PipelineCancellationTerminatesAllStagesWithinBound )
{
    QVector<export_process::Invocation> stages;
#ifdef Q_OS_WIN
    stages << export_process::Invocation{
                  QStringLiteral("cmd.exe"),
                  { QStringLiteral("/D"), QStringLiteral("/S"), QStringLiteral("/C"),
                    QStringLiteral("echo CANCEL-MARKER 1>&2 & more") } }
           << export_process::Invocation{ QStringLiteral("more.com"), {} };
#else
    stages << export_process::Invocation{
                  QStringLiteral("/bin/sh"),
                  { QStringLiteral("-c"), QStringLiteral("echo CANCEL-MARKER >&2; cat") } }
           << export_process::Invocation{ QStringLiteral("/bin/cat"), {} };
#endif
    export_process::StreamingPipeline pipeline;
    ASSERT_TRUE( pipeline.start( stages ) );
    const QByteArray input = QByteArrayLiteral("cancel-data\n");
    ASSERT_TRUE( pipeline.writeAll( input.constData(), input.size() ) );
    QElapsedTimer timer;
    timer.start();
    pipeline.cancel();
    ASSERT_TRUE( timer.elapsed() < 6000 );
    ASSERT_TRUE( pipeline.diagnostics().contains( QStringLiteral("CANCEL-MARKER") ) );
}
