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

#ifndef DOWNLOADMANAGER_H
#define DOWNLOADMANAGER_H

#include <QObject>
#include <QCoreApplication>
#include <QFile>
#include <QFileInfo>
#include <QList>
#include <QNetworkAccessManager>
#include <QNetworkRequest>
#include <QNetworkReply>
#include <QStringList>
#include <QTimer>
#include <QUrl>

class DownloadManager : public QObject
{
    Q_OBJECT
    // Not owned when injected externally (tests supply a fake manager); when
    // nullptr is passed to the constructor, an internal one is created and
    // parented to `this` so it is destroyed automatically.
    QNetworkAccessManager *manager;
    bool m_ownsManager;
    QList<QNetworkReply *> currentDownloads;
    bool m_downloadSucess;
    // Sticky failure flag for the current "operation" (the run of requests
    // between idle points): once any request in the operation fails, a
    // later request finishing successfully must not flip the aggregate
    // result back to success.
    bool m_operationFailed;

public:
    // externalManager: optional, not owned by DownloadManager -- purely for
    // deterministic tests with a fake QNetworkAccessManager subclass.
    // Passing nullptr (the default, matching the historic no-arg
    // constructor's behaviour) creates and owns one internally.
    explicit DownloadManager(QNetworkAccessManager *externalManager = nullptr, QObject *parent = nullptr);
    void doDownload(const QUrl &url);
    QString saveFileName(const QUrl &url);
    bool saveToDisk(const QString &filename, QIODevice *data);
    bool isDownloadReady();
    bool downloadSuccess();
    // Aborts all currently-tracked requests for the active operation. Active
    // replies are snapshotted and removed from tracking BEFORE abort() is
    // called on each snapshot copy, because QNetworkReply::abort() can
    // synchronously re-emit finished() through the manager; downloadFinished()
    // ignores replies no longer present in the tracking list, so this
    // ordering prevents a reentrant abort-triggered finish from being
    // double-processed. Marks the operation failed and emits
    // downloadsFinished(false) exactly once.
    void abortDownloads();

signals:
    // Emitted exactly once per operation (the run of requests between idle
    // points) when the manager becomes idle again -- i.e. all currently
    // tracked requests for that operation have finished, whether by success,
    // failure, or abort. `success` is false if ANY request in the operation
    // failed or was aborted.
    void downloadsFinished(bool success);

public slots:
    void execute();
    void downloadFinished(QNetworkReply *reply);
};

#endif // DOWNLOADMANAGER_H
