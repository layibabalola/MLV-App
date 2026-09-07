#include "../common/minitest.h"
#include "../../platform/qt/DownloadManager.h"

#include <QByteArray>
#include <QDir>
#include <QEventLoop>
#include <QFile>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QTemporaryDir>
#include <QTimer>
#include <QUrl>

namespace {

// Scoped current-directory switch so saveToDisk's relative basename writes
// land in a QTemporaryDir instead of wherever the test binary runs from,
// proving the real production save path without touching real map dirs.
class ScopedCurrentDir
{
public:
    explicit ScopedCurrentDir(const QString &path) : m_previous(QDir::currentPath())
    {
        QDir::setCurrent(path);
    }
    ~ScopedCurrentDir() { QDir::setCurrent(m_previous); }

private:
    QString m_previous;
};

// A controllable fake QNetworkReply: emits finished()/errorOccurred() only
// when told to, and can synchronously re-emit finished() from inside its
// abort() override to exercise the reentrancy ordering guarantees.
class FakeReply : public QNetworkReply
{
    Q_OBJECT
public:
    explicit FakeReply(const QNetworkRequest &request, QObject *parent = nullptr)
        : QNetworkReply(parent)
    {
        setRequest(request);
        setUrl(request.url());
        setOperation(QNetworkAccessManager::GetOperation);
        open(QIODevice::ReadOnly);
    }

    void abort() override
    {
        if (m_finished) {
            return;
        }
        if (m_finishSynchronouslyOnAbort) {
            finishNow(false, QByteArray());
        }
    }

    void succeedAfter(int ms, const QByteArray &payload)
    {
        QTimer::singleShot(ms, this, [this, payload]() { finishNow(true, payload); });
    }

    void succeedNow(const QByteArray &payload) { finishNow(true, payload); }

    void failNow() { finishNow(false, QByteArray()); }

    // If a late finished() must simulate arriving AFTER abort()/timeout
    // already gave up on this reply, call this directly without going
    // through DownloadManager's tracking (it already removed the reply).
    void emitLateFinishedIgnoringState(const QByteArray &payload)
    {
        m_payload = payload;
        setError(QNetworkReply::NoError, QString());
        setFinished(true);
        emit finished();
    }

    bool m_finishSynchronouslyOnAbort = true;

protected:
    qint64 readData(char *data, qint64 maxSize) override
    {
        const qint64 available = m_payload.size() - m_pos;
        if (available <= 0) {
            return -1;
        }
        const qint64 n = qMin(available, maxSize);
        memcpy(data, m_payload.constData() + m_pos, static_cast<size_t>(n));
        m_pos += n;
        return n;
    }

    qint64 bytesAvailable() const override
    {
        return (m_payload.size() - m_pos) + QIODevice::bytesAvailable();
    }

    bool isSequential() const override { return true; }

private:
    void finishNow(bool success, const QByteArray &payload)
    {
        if (m_finished) {
            return;
        }
        m_finished = true;
        m_payload = payload;
        if (!success) {
            setError(QNetworkReply::OperationCanceledError, QStringLiteral("fake failure"));
        } else {
            setError(QNetworkReply::NoError, QString());
        }
        setFinished(true);
        emit finished();
    }

    QByteArray m_payload;
    qint64 m_pos = 0;
    bool m_finished = false;
};

class FakeNetworkAccessManager : public QNetworkAccessManager
{
    Q_OBJECT
public:
    QList<FakeReply *> createdReplies;
    // When true, the created reply is finished synchronously before
    // createRequest() returns -- simulates a reply that is already in the
    // "finished" state at the moment DownloadManager registers/tracks it.
    bool finishSynchronouslyOnCreate = false;

protected:
    QNetworkReply * createRequest(Operation, const QNetworkRequest &request, QIODevice *) override
    {
        FakeReply *reply = new FakeReply(request, this);
        createdReplies.append(reply);
        if (finishSynchronouslyOnCreate) {
            reply->succeedNow(QByteArray("[]"));
        }
        return reply;
    }
};

// Runs a local bounded event loop until DownloadManager::downloadsFinished
// fires or timeoutMs elapses. Returns true if the signal fired.
bool waitForCompletion(DownloadManager &manager, bool *outSuccess, int timeoutMs = 2000)
{
    QEventLoop loop;
    bool fired = false;
    QMetaObject::Connection c = QObject::connect(&manager, &DownloadManager::downloadsFinished,
        [&](bool success) {
            fired = true;
            if (outSuccess) {
                *outSuccess = success;
            }
            loop.quit();
        });
    QTimer::singleShot(timeoutMs, &loop, &QEventLoop::quit);
    loop.exec();
    QObject::disconnect(c);
    return fired;
}

} // namespace

TEST(DownloadManager, NormalDelayedSuccessWritesFileToDisk)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    ScopedCurrentDir cwd(dir.path());

    FakeNetworkAccessManager net;
    DownloadManager manager(&net);

    manager.doDownload(QUrl("https://example.invalid/pixel_maps"));
    ASSERT_EQ(1, net.createdReplies.size());
    net.createdReplies.last()->succeedAfter(10, QByteArray("[]"));

    bool success = false;
    ASSERT_TRUE(waitForCompletion(manager, &success));
    ASSERT_TRUE(success);
    ASSERT_TRUE(manager.downloadSuccess());
    ASSERT_TRUE(QFile::exists(dir.filePath("pixel_maps")));
}

TEST(DownloadManager, AlreadyFinishedReplyIsHandledOnceNotDouble)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    ScopedCurrentDir cwd(dir.path());

    FakeNetworkAccessManager net;
    net.finishSynchronouslyOnCreate = true; // reply is already finished by the time get() returns
    DownloadManager manager(&net);

    manager.doDownload(QUrl("https://example.invalid/pixel_maps"));
    ASSERT_TRUE(manager.isDownloadReady()); // doDownload() handled it inline, not double-processed
    ASSERT_TRUE(manager.downloadSuccess());
    ASSERT_TRUE(QFile::exists(dir.filePath("pixel_maps")));

    // A duplicate finished() dispatch for the same (already-finalized) reply
    // must not crash or reprocess.
    FakeReply *reply = net.createdReplies.last();
    emit reply->finished();
    ASSERT_TRUE(manager.isDownloadReady());
}

TEST(DownloadManager, TimeoutPathAbortMarksOperationFailed)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    ScopedCurrentDir cwd(dir.path());

    FakeNetworkAccessManager net;
    DownloadManager manager(&net);

    manager.doDownload(QUrl("https://example.invalid/pixel_maps"));
    FakeReply *reply = net.createdReplies.last();

    bool success = true;
    bool fired = false;
    QEventLoop loop;
    QMetaObject::Connection c = QObject::connect(&manager, &DownloadManager::downloadsFinished,
        [&](bool s) { success = s; fired = true; loop.quit(); });
    manager.abortDownloads();
    // abortDownloads() emits synchronously (direct connection, same thread),
    // which can happen before this local loop is ever entered -- a quit()
    // call before exec() would otherwise be lost and hang forever. Only run
    // the loop if the signal has not already fired.
    if (!fired) {
        loop.exec();
    }
    QObject::disconnect(c);

    ASSERT_TRUE(fired);
    ASSERT_FALSE(success);
    ASSERT_FALSE(QFile::exists(dir.filePath("pixel_maps")));
    Q_UNUSED(reply);
}

TEST(DownloadManager, SynchronousFinishDuringAbortStaysFailed)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    ScopedCurrentDir cwd(dir.path());

    FakeNetworkAccessManager net;
    DownloadManager manager(&net);

    manager.doDownload(QUrl("https://example.invalid/pixel_maps"));
    FakeReply *reply = net.createdReplies.last();
    reply->m_finishSynchronouslyOnAbort = true; // abort() re-emits finished() synchronously

    bool success = true;
    bool fired = false;
    QEventLoop loop;
    QMetaObject::Connection c = QObject::connect(&manager, &DownloadManager::downloadsFinished,
        [&](bool s) { success = s; fired = true; loop.quit(); });
    manager.abortDownloads();
    if (!fired) {
        loop.exec();
    }
    QObject::disconnect(c);

    ASSERT_TRUE(fired);
    ASSERT_FALSE(success);
    ASSERT_FALSE(QFile::exists(dir.filePath("pixel_maps")));
}

TEST(DownloadManager, StaleLateFinishedAfterAbortDoesNotWriteFile)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    ScopedCurrentDir cwd(dir.path());

    FakeNetworkAccessManager net;
    DownloadManager manager(&net);

    manager.doDownload(QUrl("https://example.invalid/pixel_maps"));
    FakeReply *reply = net.createdReplies.last();
    reply->m_finishSynchronouslyOnAbort = false; // abort() does NOT finish it now

    manager.abortDownloads(); // snapshots + clears tracking, then calls abort()

    // Simulate a queued finished() arriving late, after abort() already
    // removed this reply from tracking.
    reply->emitLateFinishedIgnoringState(QByteArray("[]"));

    ASSERT_FALSE(QFile::exists(dir.filePath("pixel_maps")));
}

TEST(DownloadManager, RejectedBasenamePropagatesFailureWithoutWrite)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    ScopedCurrentDir cwd(dir.path());

    FakeNetworkAccessManager net;
    DownloadManager manager(&net);

    manager.doDownload(QUrl("https://example.invalid/not-on-the-allowlist.exe"));
    net.createdReplies.last()->succeedAfter(5, QByteArray("payload"));

    bool success = true;
    ASSERT_TRUE(waitForCompletion(manager, &success));
    ASSERT_FALSE(success);
    ASSERT_FALSE(manager.downloadSuccess());
    ASSERT_TRUE(QDir(dir.path()).entryList(QDir::Files).isEmpty());
}

TEST(DownloadManager, MultiRequestOneFailsOneLaterSucceedsAggregateStaysFailed)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    ScopedCurrentDir cwd(dir.path());

    FakeNetworkAccessManager net;
    DownloadManager manager(&net);

    manager.doDownload(QUrl("https://example.invalid/pixel_maps"));
    manager.doDownload(QUrl("https://example.invalid/releases"));
    ASSERT_EQ(2, net.createdReplies.size());

    net.createdReplies.at(0)->failNow();
    net.createdReplies.at(1)->succeedAfter(5, QByteArray("[]"));

    bool success = true;
    ASSERT_TRUE(waitForCompletion(manager, &success));
    ASSERT_FALSE(success);
}

TEST(DownloadManager, FailFirstSucceedLastKeepsGetterAndSignalConsistent)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    ScopedCurrentDir cwd(dir.path());

    FakeNetworkAccessManager net;
    DownloadManager manager(&net);

    manager.doDownload(QUrl("https://example.invalid/pixel_maps"));
    manager.doDownload(QUrl("https://example.invalid/releases"));
    ASSERT_EQ(2, net.createdReplies.size());

    // Fail first, succeed last: the LAST reply to finish must not flip the
    // aggregate back to success in either the signal or the getter.
    net.createdReplies.at(0)->failNow();
    net.createdReplies.at(1)->succeedAfter(5, QByteArray("[]"));

    bool signalSuccess = true;
    ASSERT_TRUE(waitForCompletion(manager, &signalSuccess));
    ASSERT_FALSE(signalSuccess);
    ASSERT_FALSE(manager.downloadSuccess()); // getter must agree with the signal
}

TEST(DownloadManager, AbortWithOneSuccessAndOnePendingClearsStaleSuccessGetter)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    ScopedCurrentDir cwd(dir.path());

    FakeNetworkAccessManager net;
    DownloadManager manager(&net);

    manager.doDownload(QUrl("https://example.invalid/pixel_maps"));
    manager.doDownload(QUrl("https://example.invalid/releases"));
    ASSERT_EQ(2, net.createdReplies.size());

    // First request succeeds while the second is still pending.
    net.createdReplies.at(0)->succeedNow(QByteArray("[]"));
    ASSERT_TRUE(manager.downloadSuccess());
    ASSERT_FALSE(manager.isDownloadReady()); // second reply still in flight

    net.createdReplies.at(1)->m_finishSynchronouslyOnAbort = false;

    bool success = true;
    bool fired = false;
    QEventLoop loop;
    QMetaObject::Connection c = QObject::connect(&manager, &DownloadManager::downloadsFinished,
        [&](bool s) { success = s; fired = true; loop.quit(); });
    manager.abortDownloads();
    if (!fired) {
        loop.exec();
    }
    QObject::disconnect(c);

    ASSERT_TRUE(fired);
    ASSERT_FALSE(success);
    // The stale true from the earlier successful reply must not survive an
    // abort of the still-pending sibling request.
    ASSERT_FALSE(manager.downloadSuccess());
}

TEST(DownloadManager, AbortWhenIdleDoesNotEmit)
{
    FakeNetworkAccessManager net;
    DownloadManager manager(&net);

    bool fired = false;
    QMetaObject::Connection c = QObject::connect(&manager, &DownloadManager::downloadsFinished,
        [&](bool) { fired = true; });
    manager.abortDownloads(); // no active operation at all
    QObject::disconnect(c);

    ASSERT_FALSE(fired);
}

TEST(DownloadManager, RepeatedAbortEmitsExactlyOnce)
{
    QTemporaryDir dir;
    ASSERT_TRUE(dir.isValid());
    ScopedCurrentDir cwd(dir.path());

    FakeNetworkAccessManager net;
    DownloadManager manager(&net);

    manager.doDownload(QUrl("https://example.invalid/pixel_maps"));
    net.createdReplies.last()->m_finishSynchronouslyOnAbort = false;

    int fireCount = 0;
    QMetaObject::Connection c = QObject::connect(&manager, &DownloadManager::downloadsFinished,
        [&](bool) { fireCount++; });
    manager.abortDownloads();      // active -> emits once
    manager.abortDownloads();      // already idle -> must not emit again
    QObject::disconnect(c);

    ASSERT_EQ(1, fireCount);
}

// The reentrancy guard concept (ScopedOperationGuard) lives on
// FocusPixelMapManager, not on DownloadManager itself -- DownloadManager has
// no single-caller-at-a-time entry point of its own to guard (multiple
// concurrent doDownload() calls are the supported multi-request case above).
// That guard is exercised structurally by
// tools/repo_hygiene/test_fpm_no_process_events.py and covered in intent by
// SyncDownloadWaiter's own ScopedOperationGuard unit test; no separate
// DownloadManager-level guard test is added here.

#include "test_download_manager.moc"
