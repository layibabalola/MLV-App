#pragma once

#include <QByteArray>
#include <QElapsedTimer>
#include <QProcess>
#include <QString>
#include <QStringList>

namespace export_process
{

struct Invocation
{
    QString program;
    QStringList arguments;
};

inline Invocation rawVideoInvocation( const QString &program,
                                      const QString &fps,
                                      const QString &resolution )
{
    return Invocation{
        program,
        QStringList{ QStringLiteral("-r"), fps,
                     QStringLiteral("-y"),
                     QStringLiteral("-f"), QStringLiteral("rawvideo"),
                     QStringLiteral("-s"), resolution,
                     QStringLiteral("-pix_fmt"), QStringLiteral("rgb48"),
                     QStringLiteral("-i"), QStringLiteral("-") }
    };
}

class StreamingProcess
{
public:
    bool start( const Invocation &invocation, int timeoutMs = 10000 )
    {
        m_process.setProcessChannelMode( QProcess::SeparateChannels );
        m_process.start( invocation.program, invocation.arguments, QIODevice::ReadWrite );
        if( m_process.waitForStarted( timeoutMs ) ) return true;
        captureDiagnostics();
        return false;
    }

    bool writeAll( const char *data, qint64 size, int timeoutMs = 30000 )
    {
        qint64 written = 0;
        while( written < size )
        {
            captureDiagnostics();
            const qint64 accepted = m_process.write( data + written, size - written );
            if( accepted < 0 )
            {
                captureDiagnostics();
                return false;
            }
            written += accepted;
            if( written < size && !m_process.waitForBytesWritten( timeoutMs ) )
            {
                captureDiagnostics();
                return false;
            }
            captureDiagnostics();
        }
        return true;
    }

    bool finish( int timeoutMs = 120000 )
    {
        m_process.closeWriteChannel();
        QElapsedTimer timer;
        timer.start();
        while( m_process.state() != QProcess::NotRunning )
        {
            captureDiagnostics();
            const qint64 remaining = timeoutMs - timer.elapsed();
            if( remaining <= 0 )
            {
                cancel();
                captureDiagnostics();
                return false;
            }
            m_process.waitForReadyRead( static_cast<int>( qMin<qint64>( remaining, 100 ) ) );
        }
        captureDiagnostics();
        return m_process.exitStatus() == QProcess::NormalExit && m_process.exitCode() == 0;
    }

    void cancel()
    {
        if( m_process.state() == QProcess::NotRunning ) return;
        m_process.terminate();
        if( !m_process.waitForFinished( 2000 ) )
        {
            m_process.kill();
            m_process.waitForFinished( 2000 );
        }
    }

    QString diagnostics() const { return QString::fromUtf8( m_diagnosticTail ); }

private:
    void captureDiagnostics()
    {
        m_diagnosticTail.append( m_process.readAllStandardError() );
        m_diagnosticTail.append( m_process.readAllStandardOutput() );
        static const int kMaxDiagnosticBytes = 8192;
        if( m_diagnosticTail.size() > kMaxDiagnosticBytes )
            m_diagnosticTail = m_diagnosticTail.right( kMaxDiagnosticBytes );
    }

    QProcess m_process;
    QByteArray m_diagnosticTail;
};

} // namespace export_process
