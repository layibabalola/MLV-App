/*!
 * \file FocusPixelMapManager.h
 * \author masc4ii
 * \copyright 2020
 * \brief Check and install focus pixel maps
 */

#ifndef FOCUSPIXELMAPMANAGER_H
#define FOCUSPIXELMAPMANAGER_H

#include <QObject>
#include "../../src/mlv_include.h"
#include "DownloadManager.h"
#include "SyncDownloadWaiter.h"
#include <QDebug>
#include <QFile>
#include <QJsonDocument>
#include <QJsonArray>
#include <QJsonObject>

class FocusPixelMapManager : public QObject
{
    Q_OBJECT
public:
    explicit FocusPixelMapManager( QObject *parent = nullptr );
    ~FocusPixelMapManager();

    bool isDownloaded( mlvObject_t *pMlvObject );
    bool isMapAvailable( mlvObject_t *pMlvObject );
    bool downloadMap( mlvObject_t *pMlvObject );
    bool downloadAllMaps( mlvObject_t *pMlvObject );
    int updateAllMaps( bool justCheck );

private:
    DownloadManager *manager;
    // Instance-level (non-static) mutual-exclusion flag: acquired via
    // SyncDownloadWaiter::ScopedOperationGuard at the top of each of the
    // four public entry points below, before any work is done. If one of
    // these is reentered while already held (e.g. from a queued callback
    // that runs during a wait() call), the reentrant call returns the
    // existing failure-convention default for that function without doing
    // any work.
    bool m_operationInProgress;
    // Production default 30000ms; not currently exposed for override since
    // no test in this card's scope drives FocusPixelMapManager's waits
    // directly (SyncDownloadWaiter and DownloadManager are tested standalone).
    int m_downloadTimeoutMs;
    QJsonArray getMapList( void );
    QString getMapName( mlvObject_t *pMlvObject );
};

#endif // FOCUSPIXELMAPMANAGER_H
