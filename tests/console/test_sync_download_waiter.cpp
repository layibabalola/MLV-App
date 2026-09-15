#include "../common/minitest.h"
#include "../../platform/qt/SyncDownloadWaiter.h"

#include <QObject>
#include <QTimer>

namespace {

// QtCore-only fake "operation" -- a plain QObject with a no-arg completion
// signal, driven by a QTimer. No network, no QSignalSpy.
class FakeOperation : public QObject
{
    Q_OBJECT
public:
    explicit FakeOperation(QObject *parent = nullptr) : QObject(parent) {}

    void completeAfter(int ms)
    {
        QTimer::singleShot(ms, this, [this]() { emit ready(); });
    }

    void completeNow()
    {
        emit ready();
    }

signals:
    void ready();
};

QMetaObject::Connection connectReadyFor(FakeOperation *op, std::function<void()> notify)
{
    return QObject::connect(op, &FakeOperation::ready, [notify]() { notify(); });
}

} // namespace

TEST(SyncDownloadWaiter, DelayedSuccessBeforeDeadlineFinishes)
{
    FakeOperation op;
    SyncDownloadWaiter waiter;
    SyncDownloadWaiter::Result result = waiter.wait(
        &op,
        [&op](std::function<void()> notify) { return connectReadyFor(&op, notify); },
        [&op]() { op.completeAfter(10); },
        []() { return false; },
        std::function<void()>(),
        200);

    ASSERT_TRUE(result.finished);
    ASSERT_FALSE(result.timedOut);
}

TEST(SyncDownloadWaiter, AlreadyReadyAtConnectTimeSkipsTheWait)
{
    FakeOperation op;
    bool startOpCalled = false;
    SyncDownloadWaiter waiter;
    SyncDownloadWaiter::Result result = waiter.wait(
        &op,
        [&op](std::function<void()> notify) { return connectReadyFor(&op, notify); },
        [&startOpCalled]() { startOpCalled = true; },
        []() { return true; }, // already ready before the loop is ever entered
        std::function<void()>(),
        30000);

    ASSERT_TRUE(result.finished);
    ASSERT_FALSE(result.timedOut);
    ASSERT_TRUE(startOpCalled);
}

TEST(SyncDownloadWaiter, TimeoutInvokesCancelCallback)
{
    FakeOperation op;
    bool cancelInvoked = false;
    SyncDownloadWaiter waiter;
    SyncDownloadWaiter::Result result = waiter.wait(
        &op,
        [&op](std::function<void()> notify) { return connectReadyFor(&op, notify); },
        std::function<void()>(), // never fires ready
        []() { return false; },
        [&cancelInvoked]() { cancelInvoked = true; },
        10);

    ASSERT_FALSE(result.finished);
    ASSERT_TRUE(result.timedOut);
    ASSERT_TRUE(cancelInvoked);
}

TEST(SyncDownloadWaiter, ExactlyOnceEvenIfSignalFiresTwice)
{
    FakeOperation op;
    int notifyCount = 0;
    SyncDownloadWaiter waiter;

    SyncDownloadWaiter::Result result = waiter.wait(
        &op,
        [&op, &notifyCount](std::function<void()> notify) {
            return QObject::connect(&op, &FakeOperation::ready, [notify, &notifyCount]() {
                notifyCount++;
                notify();
            });
        },
        [&op]() {
            // Fire twice back-to-back; the second must be a no-op for the
            // waiter's result (though our own counter still increments).
            op.completeNow();
            op.completeNow();
        },
        []() { return false; },
        std::function<void()>(),
        1000);

    ASSERT_TRUE(result.finished);
    ASSERT_EQ(2, notifyCount); // signal really did fire twice
}

TEST(SyncDownloadWaiter, SynchronousSuccessDuringCancelDoesNotOverwriteTimeout)
{
    FakeOperation op;
    SyncDownloadWaiter waiter;

    SyncDownloadWaiter::Result result = waiter.wait(
        &op,
        [&op](std::function<void()> notify) { return connectReadyFor(&op, notify); },
        std::function<void()>(),
        []() { return false; },
        [&op]() {
            // Cancellation callback synchronously fires a "success" signal.
            // The already-latched timeout result must survive this.
            op.completeNow();
        },
        10);

    ASSERT_FALSE(result.finished);
    ASSERT_TRUE(result.timedOut);
}

TEST(SyncDownloadWaiter, DestroyedSenderMidWaitResolvesSafely)
{
    FakeOperation *op = new FakeOperation();
    SyncDownloadWaiter waiter;

    SyncDownloadWaiter::Result result = waiter.wait(
        op,
        [op](std::function<void()> notify) { return connectReadyFor(op, notify); },
        [op]() { QTimer::singleShot(5, [op]() { delete op; }); },
        []() { return false; },
        std::function<void()>(),
        1000);

    ASSERT_FALSE(result.finished);
    ASSERT_FALSE(result.timedOut);
}

TEST(SyncDownloadWaiter, ReadyThenSenderDeletedInsideStartOpLatchesSenderDestroyed)
{
    FakeOperation *op = new FakeOperation();
    SyncDownloadWaiter waiter;

    // Inside startOp: fire ready() synchronously (latching finished=true
    // before startOp returns), then delete the sender before startOp
    // returns. A caller must not be able to treat finished==true as proof
    // the sender is still alive and dereference it.
    SyncDownloadWaiter::Result result = waiter.wait(
        op,
        [op](std::function<void()> notify) { return connectReadyFor(op, notify); },
        [op]() {
            op->completeNow();
            delete op;
        },
        []() { return false; },
        std::function<void()>(),
        1000);

    ASSERT_TRUE(result.finished);
    ASSERT_FALSE(result.timedOut);
    ASSERT_TRUE(result.senderDestroyed);
}

TEST(SyncDownloadWaiter, ScopedOperationGuardAcquireReleaseAndReentrancy)
{
    bool flag = false;
    {
        SyncDownloadWaiter::ScopedOperationGuard outer(&flag);
        ASSERT_TRUE(outer.acquired());
        ASSERT_TRUE(flag);

        SyncDownloadWaiter::ScopedOperationGuard inner(&flag);
        ASSERT_FALSE(inner.acquired());
        ASSERT_TRUE(flag); // outer still holds it
    }
    ASSERT_FALSE(flag);

    SyncDownloadWaiter::ScopedOperationGuard again(&flag);
    ASSERT_TRUE(again.acquired());
}

#include "test_sync_download_waiter.moc"
