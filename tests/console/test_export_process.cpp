#include "../common/minitest.h"
#include "../../platform/qt/ExportProcess.h"

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
