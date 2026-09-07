/****************************************************************************
**
** Copyright (C) 2009 Nokia Corporation and/or its subsidiary(-ies).
** All rights reserved.
** Contact: Nokia Corporation (qt-info@nokia.com)
**
** This file is part of the examples of the Qt Toolkit.
**
** $QT_BEGIN_LICENSE:LGPL$
** Commercial Usage
** Licensees holding valid Qt Commercial licenses may use this file in
** accordance with the Qt Commercial License Agreement provided with the
** Software or, alternatively, in accordance with the terms contained in
** a written agreement between you and Nokia.
**
** GNU Lesser General Public License Usage
** Alternatively, this file may be used under the terms of the GNU Lesser
** General Public License version 2.1 as published by the Free Software
** Foundation and appearing in the file LICENSE.LGPL included in the
** packaging of this file.  Please review the following information to
** ensure the GNU Lesser General Public License version 2.1 requirements
** will be met: http://www.gnu.org/licenses/old-licenses/lgpl-2.1.html.
**
** In addition, as a special exception, Nokia gives you certain additional
** rights.  These rights are described in the Nokia Qt LGPL Exception
** version 1.1, included in the file LGPL_EXCEPTION.txt in this package.
**
** GNU General Public License Usage
** Alternatively, this file may be used under the terms of the GNU
** General Public License version 3.0 as published by the Free Software
** Foundation and appearing in the file LICENSE.GPL included in the
** packaging of this file.  Please review the following information to
** ensure the GNU General Public License version 3.0 requirements will be
** met: http://www.gnu.org/copyleft/gpl.html.
**
** If you have questions regarding the use of this file, please contact
** Nokia at qt-info@nokia.com.
** $QT_END_LICENSE$
**
****************************************************************************/

#include "DownloadManager.h"
#include "AtomicFileReplace.h"
#include "FpmNameValidator.h"

DownloadManager::DownloadManager(QNetworkAccessManager *externalManager, QObject *parent)
    : QObject(parent)
{
    if (externalManager) {
        manager = externalManager;
        m_ownsManager = false;
    } else {
        manager = new QNetworkAccessManager(this);
        m_ownsManager = true;
    }
    m_downloadSucess = false;
    m_operationFailed = false;
    connect(manager, SIGNAL(finished(QNetworkReply*)),
            SLOT(downloadFinished(QNetworkReply*)));
}

void DownloadManager::doDownload(const QUrl &url)
{
    if (currentDownloads.isEmpty()) {
        // Idle -> starting a fresh operation: reset the sticky failure flag.
        m_operationFailed = false;
    }
    m_downloadSucess = false;
    QNetworkRequest request(url);

    QNetworkReply *reply = manager->get(request);

    currentDownloads.append(reply);

    if (reply->isFinished()) {
        // Edge case: the fake/real reply was already finished at the moment
        // we registered it (e.g. a synchronous fake in tests). Handle it now
        // rather than waiting for the manager's aggregate finished() signal.
        // downloadFinished() removes the reply from currentDownloads, so if
        // the manager's finished(reply) signal also fires later for the same
        // reply, its membership guard makes that a no-op -- no double
        // dispatch.
        downloadFinished(reply);
    }
}

void DownloadManager::abortDownloads()
{
    const QList<QNetworkReply *> snapshot = currentDownloads;
    if (snapshot.isEmpty()) {
        // Idle, or an operation already aborted/finished: nothing active to
        // tear down. Completion must emit exactly once per active operation,
        // so a repeated or idle abort() call is a silent no-op.
        return;
    }
    currentDownloads.clear();
    m_operationFailed = true;
    // Aggregate failure sticks even if an earlier reply in this operation
    // had already succeeded -- do not leave a stale success getter behind.
    m_downloadSucess = false;

    for (QNetworkReply *reply : snapshot) {
        reply->abort();
        reply->deleteLater();
    }

    emit downloadsFinished(false);
}

QString DownloadManager::saveFileName(const QUrl &url)
{
    return allowedDownloadBasenameForUrl(url);
}

bool DownloadManager::saveToDisk(const QString &filename, QIODevice *data)
{
    if (!writeAtomically(filename, data->readAll())) {
        fprintf(stderr, "Could not atomically save %s\n", qPrintable(filename));
        return false;
    }

    return true;
}

bool DownloadManager::isDownloadReady()
{
    return currentDownloads.empty();
}

bool DownloadManager::downloadSuccess()
{
    // Must agree with the downloadsFinished(bool) signal's aggregate: a
    // fail-first/succeed-last sequence within one operation is still an
    // overall failure, so this cannot simply reflect the last reply's
    // individual outcome (m_downloadSucess).
    return !m_operationFailed;
}

void DownloadManager::execute()
{
    QStringList args = QCoreApplication::instance()->arguments();
    args.takeFirst();           // skip the first argument, which is the program's name
    if (args.isEmpty()) {
        printf("Qt Download example - downloads all URLs in parallel\n"
               "Usage: download url1 [url2... urlN]\n"
               "\n"
               "Downloads the URLs passed in the command-line to the local directory\n"
               "If the target file already exists, a .0, .1, .2, etc. is appended to\n"
               "differentiate.\n");
        QCoreApplication::instance()->quit();
        return;
    }

    foreach (QString arg, args) {
        QUrl url = QUrl::fromEncoded(arg.toLocal8Bit());
        doDownload(url);
    }
}

void DownloadManager::downloadFinished(QNetworkReply *reply)
{
    if (!currentDownloads.contains(reply)) {
        // Already finalized: either a late queued finished() arriving after
        // abort()/timeout already removed this reply, or a duplicate
        // dispatch for a reply that was already-finished at registration
        // time. Ignore it -- do NOT resurrect finalized state or write a
        // stale file.
        return;
    }
    currentDownloads.removeAll(reply);

    m_downloadSucess = false;
    QUrl url = reply->url();
    if (reply->error()) {
        fprintf(stderr, "Download of %s failed: %s\n",
                url.toEncoded().constData(),
                qPrintable(reply->errorString()));
        m_downloadSucess = false;
    } else {
        QString filename = saveFileName(url);
        if (filename.isEmpty()) {
            fprintf(stderr, "Rejected invalid pixel-map filename from %s\n",
                    url.toEncoded().constData());
        } else {
            m_downloadSucess = saveToDisk(filename, reply);
        }
    }

    if (!m_downloadSucess) {
        m_operationFailed = true;
    }

    reply->deleteLater();

    if (currentDownloads.isEmpty()) {
        // Operation idle: emit exactly once, aggregate failure sticks even
        // if this particular reply succeeded.
        emit downloadsFinished(!m_operationFailed);
    }
}
