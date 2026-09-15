/*!
 * \file SyncDownloadWaiter.h
 * \brief Bounded, header-only wait helper for a single asynchronous
 *        "completion" notification (e.g. a download finishing), used to
 *        replace unbounded `while (!ready) qApp->processEvents();` polling
 *        loops with a deadline-bounded local QEventLoop.
 *
 * IMPORTANT LIMITATION (documented honestly, not aspirational): the internal
 * QEventLoop is run with QEventLoop::ExcludeUserInputEvents. That ONLY
 * excludes literal mouse/keyboard input events from being dispatched while
 * waiting. It does NOT prevent other queued signals, timers, or callbacks
 * from running reentrantly against shared state during the wait -- this
 * helper bounds the wait with a deadline and gives ordered
 * completion/cancel/destroyed-sender semantics, it does not eliminate
 * reentrancy from unrelated Qt event sources.
 */
#ifndef SYNCDOWNLOADWAITER_H
#define SYNCDOWNLOADWAITER_H

#include <QEventLoop>
#include <QMetaObject>
#include <QObject>
#include <QTimer>

#include <functional>

class SyncDownloadWaiter : public QObject
{
public:
    struct Result
    {
        bool finished = false;
        bool timedOut = false;
        // Set whenever destroyedSender's destroyed() signal fires during the
        // call, EVEN IF a ready notification had already latched `finished`
        // synchronously first (e.g. inside `startOp`). Callers must check
        // this before dereferencing the sender they passed as
        // destroyedSender -- `finished == true` alone does not prove the
        // sender is still alive, because a synchronous ready notification
        // and a subsequent same-call sender deletion can both occur before
        // `startOp` returns.
        bool senderDestroyed = false;
    };

    // A small reusable, instance-owned mutual-exclusion guard. It operates
    // on a bool flag passed in by pointer -- it is deliberately NOT a
    // static/global guard, so each owning object (e.g. each
    // FocusPixelMapManager instance) gets independent, deterministic
    // acquire/release behaviour that tests can drive directly.
    class ScopedOperationGuard
    {
    public:
        explicit ScopedOperationGuard(bool *flag)
            : m_flag(flag)
            , m_acquired(false)
        {
            if (m_flag && !*m_flag) {
                *m_flag = true;
                m_acquired = true;
            }
        }

        ~ScopedOperationGuard()
        {
            if (m_acquired && m_flag) {
                *m_flag = false;
            }
        }

        bool acquired() const { return m_acquired; }

        ScopedOperationGuard(const ScopedOperationGuard &) = delete;
        ScopedOperationGuard & operator=(const ScopedOperationGuard &) = delete;

    private:
        bool *m_flag;
        bool m_acquired;
    };

    explicit SyncDownloadWaiter(QObject *parent = nullptr) : QObject(parent) {}

    // Waits for a single completion notification.
    //
    //  destroyedSender: watched via QObject::destroyed(); if it fires before
    //      completion, the wait resolves as not-finished/not-timed-out
    //      without touching the (now dead) sender. Pass nullptr to skip.
    //  connectReady: called ONCE, synchronously, before `startOp` -- must
    //      perform the real QObject::connect to whatever "operation
    //      complete" signal is relevant (any signature) and invoke the
    //      supplied `notify` callable from inside that connection. Doing the
    //      connect here, before `startOp` kicks off the underlying request,
    //      is what avoids missing an already-in-flight/synchronous
    //      completion due to connection ordering.
    //  startOp: invoked after the connection above is live; this is where
    //      the caller should actually start/kick off the asynchronous
    //      operation (e.g. call doDownload()). May be empty if the operation
    //      was already started by the caller.
    //  alreadyReady: polled once after `startOp`, before entering the event
    //      loop, to catch operations that completed synchronously.
    //  onTimeout: invoked when the deadline elapses, AFTER the timed-out
    //      Result has already been recorded and latched (`resolved = true`).
    //      If invoking this cancellation callback synchronously triggers a
    //      "success" completion notification reentrantly, that later
    //      notification is ignored because `resolved` is already true --
    //      the timeout result is never overwritten by a late success.
    //  deadlineMs: production default is 30000ms; tests should inject a
    //      small value.
    Result wait(QObject *destroyedSender,
                const std::function<QMetaObject::Connection(std::function<void()>)> &connectReady,
                const std::function<void()> &startOp,
                const std::function<bool()> &alreadyReady,
                const std::function<void()> &onTimeout,
                int deadlineMs = 30000)
    {
        Result result;
        bool resolved = false;
        QEventLoop loop;

        std::function<void()> notify = [&resolved, &result, &loop]() {
            if (resolved) {
                return;
            }
            resolved = true;
            result.finished = true;
            result.timedOut = false;
            loop.quit();
        };

        QMetaObject::Connection readyConnection = connectReady ? connectReady(notify)
                                                                : QMetaObject::Connection();

        QMetaObject::Connection destroyedConnection;
        if (destroyedSender) {
            destroyedConnection = QObject::connect(destroyedSender, &QObject::destroyed, this,
                [&resolved, &result, &loop]() {
                    // Always latch that the sender died, even if a ready
                    // notification already resolved the wait synchronously
                    // (e.g. during `startOp`) -- production code must not
                    // dereference a dead sender just because finished==true.
                    result.senderDestroyed = true;
                    if (resolved) {
                        return;
                    }
                    resolved = true;
                    result.finished = false;
                    result.timedOut = false;
                    loop.quit();
                });
        }

        QTimer timer;
        timer.setSingleShot(true);
        QMetaObject::Connection timeoutConnection = QObject::connect(&timer, &QTimer::timeout, this,
            [&resolved, &result, &loop, &onTimeout]() {
                if (resolved) {
                    return;
                }
                // Latch the timeout result BEFORE running the cancellation
                // callback: if that callback synchronously triggers the
                // "ready" notification reentrantly, `resolved` already
                // guards it out above.
                resolved = true;
                result.finished = false;
                result.timedOut = true;
                if (onTimeout) {
                    onTimeout();
                }
                loop.quit();
            });
        timer.start(deadlineMs);

        if (startOp) {
            startOp();
        }

        if (!resolved && alreadyReady && alreadyReady()) {
            resolved = true;
            result.finished = true;
            result.timedOut = false;
        }

        if (!resolved) {
            loop.exec(QEventLoop::ExcludeUserInputEvents);
        }

        timer.stop();
        QObject::disconnect(readyConnection);
        if (destroyedConnection) {
            QObject::disconnect(destroyedConnection);
        }
        QObject::disconnect(timeoutConnection);

        return result;
    }
};

#endif // SYNCDOWNLOADWAITER_H
