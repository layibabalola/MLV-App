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

TEST( ExportProcess, MissingExecutableFailsToStartWithDiagnosticsAvailable )
{
    export_process::StreamingProcess process;
    export_process::Invocation invocation;
    invocation.program = QStringLiteral("definitely-missing-mlvapp-ffmpeg-executable");

    ASSERT_FALSE( process.start( invocation, 100 ) );
}
