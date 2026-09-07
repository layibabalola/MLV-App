/*!
 * \file FocusPixelMapManager.cpp
 * \author masc4ii
 * \copyright 2020
 * \brief Check and install focus pixel maps
 */

#include "FocusPixelMapManager.h"
#include <QByteArray>
#include <QPointer>

namespace {
// Shared helper: waits for DownloadManager's downloadsFinished(bool) signal,
// starting the given URL's download only after the wait's connection is
// live (see SyncDownloadWaiter.h for why that ordering matters). On timeout
// or on the sender being destroyed mid-wait, aborts and reports failure --
// this bounds what used to be an unbounded polling loop that repeatedly
// pumped the Qt event queue with a finite deadline, ordered completion/cancel
// semantics, and destroyed-sender safety. It does NOT prove immunity to
// reentrancy from unrelated Qt event sources during the wait (see
// SyncDownloadWaiter.h).
bool waitForDownload(DownloadManager *manager, const QUrl &url, int timeoutMs)
{
    // Watched via QPointer, not the raw `manager` pointer: a synchronous
    // ready notification during startOp followed by the sender being
    // destroyed before startOp returns still latches result.senderDestroyed
    // (see SyncDownloadWaiter.h), and this guard is what stops the
    // downloadSuccess() call below from dereferencing a dead manager.
    QPointer<DownloadManager> guarded(manager);
    SyncDownloadWaiter waiter;
    SyncDownloadWaiter::Result result = waiter.wait(
        manager,
        [manager](std::function<void()> notify) {
            return QObject::connect(manager, &DownloadManager::downloadsFinished,
                                     [notify](bool) { notify(); });
        },
        [manager, url]() { manager->doDownload(url); },
        [manager]() { return manager->isDownloadReady(); },
        [manager]() { manager->abortDownloads(); },
        timeoutMs);
    if (result.senderDestroyed || guarded.isNull()) {
        return false;
    }
    return result.finished && guarded->downloadSuccess();
}
}

//Constructor
FocusPixelMapManager::FocusPixelMapManager(QObject *parent) : QObject(parent)
{
    manager = new DownloadManager();
    m_operationInProgress = false;
    m_downloadTimeoutMs = 30000;
}

//Destructor
FocusPixelMapManager::~FocusPixelMapManager()
{
    delete manager;
}

bool FocusPixelMapManager::isDownloaded(mlvObject_t *pMlvObject)
{
    QString searchName = getMapName( pMlvObject );
    QString fileName = QString( "%1/%2" ).arg( QCoreApplication::applicationDirPath() ).arg( searchName );
    return QFileInfo( fileName ).exists();
}

//Check for fpm in repos online
bool FocusPixelMapManager::isMapAvailable(mlvObject_t *pMlvObject)
{
    SyncDownloadWaiter::ScopedOperationGuard guard(&m_operationInProgress);
    if( !guard.acquired() )
    {
        return false;
    }

    QString searchName = getMapName( pMlvObject );
    QJsonArray files = getMapList();
    if( files.empty() )
    {
        return false;
    }
    foreach( QJsonValue entry, files )
    {
        if( entry.toObject().value( "name" ).toString() == searchName ) return true;
    }
    return false;
}

//Download and install fpm from repos in application
bool FocusPixelMapManager::downloadMap(mlvObject_t *pMlvObject)
{
    SyncDownloadWaiter::ScopedOperationGuard guard(&m_operationInProgress);
    if( !guard.acquired() )
    {
        return false;
    }

    QString searchName = getMapName( pMlvObject );
    QJsonArray files = getMapList();
    if( files.empty() )
    {
        return false;
    }
    foreach( QJsonValue entry, files )
    {
        if( entry.toObject().value( "name" ).toString() == searchName )
        {
            QUrl url( entry.toObject().value( "download_url" ).toString() );
            return waitForDownload( manager, url, m_downloadTimeoutMs );
        }
    }
    return false;
}

//Download and install all fpm for current camera from repos in application
bool FocusPixelMapManager::downloadAllMaps(mlvObject_t *pMlvObject)
{
    SyncDownloadWaiter::ScopedOperationGuard guard(&m_operationInProgress);
    if( !guard.acquired() )
    {
        return false;
    }

    bool installed = false;
    QString searchName = QString( "%1" ).arg( pMlvObject->IDNT.cameraModel, 0, 16 );
    QJsonArray files = getMapList();
    if( files.empty() )
    {
        return false;
    }
    foreach( QJsonValue entry, files )
    {
        if( entry.toObject().value( "name" ).toString().startsWith( searchName ) )
        {
            QUrl url( entry.toObject().value( "download_url" ).toString() );
            if( waitForDownload( manager, url, m_downloadTimeoutMs ) ) installed = true;
            else return false;
        }
    }

    return installed;
}

//Update all the downloaded maps
int FocusPixelMapManager::updateAllMaps( bool justCheck )
{
    SyncDownloadWaiter::ScopedOperationGuard guard(&m_operationInProgress);
    if( !guard.acquired() )
    {
        return 0;
    }

    int installed = 0;
    QJsonArray files = getMapList();
    if( files.empty() )
    {
        return 0;
    }
    foreach( QJsonValue entry, files )
    {
        QString fileName = entry.toObject().value( "name" ).toString();
        QByteArray shaJson = entry.toObject().value( "sha" ).toString().toUtf8();
        QByteArray shaFile;

        QFile f( QString( "%1/%2" ).arg( QCoreApplication::applicationDirPath() ).arg( fileName ) );
        if( f.open(QFile::ReadOnly ) )
        {
            QCryptographicHash hash(QCryptographicHash::Sha1);
            QByteArray header = QString( "blob %1" ).arg( f.size() ).toUtf8();
            hash.addData( header.data(), header.size() + 1 );
            hash.addData( f.readAll() );
            shaFile = hash.result().toHex();

            //qDebug() << "File" << shaFile;
            //qDebug() << "Json" << shaJson;

            if( shaFile != shaJson )
            {
                if( justCheck )
                {
                    installed++;
                }
                else
                {
                    QUrl url( entry.toObject().value( "download_url" ).toString() );
                    if( waitForDownload( manager, url, m_downloadTimeoutMs ) ) installed++;
                    else return installed;
                }
            }
        }
    }

    return installed;
}

//Get map list online from repos
QJsonArray FocusPixelMapManager::getMapList()
{
    // NOTE: intentionally callable while a guard from an outer function
    // (isMapAvailable/downloadMap/downloadAllMaps/updateAllMaps) is held --
    // this helper does not try to re-acquire the mutually-exclusive guard.
    QUrl url( "https://api.github.com/repos/ilia3101/MLV-App/contents/pixel_maps" );
    bool ok = waitForDownload( manager, url, m_downloadTimeoutMs );
    if( !ok )
    {
        // Timeout, destroyed sender, or a failed request: return an empty
        // list. Do NOT fall back to a previously-downloaded/cached catalog
        // as if it were a fresh success.
        QJsonArray a;
        return a;
    }

    QString fileName = QString( "%1/pixel_maps" ).arg( QCoreApplication::applicationDirPath() );
    QFile file( fileName );
    if( !file.open( QIODevice::ReadOnly | QIODevice::Text ) )
    {
        qDebug() << "open paths json file failed.";
        QJsonArray a;
        return a;
    }
    QJsonDocument doc = QJsonDocument::fromJson( file.readAll() );
    file.close();

    /* JSON is invalid */
    if (doc.isNull()) {
        qDebug() << "paths json file invalid.";
        QJsonArray a;
        return a;
    }

    return doc.array();
}

//Get the map name for the current clip
QString FocusPixelMapManager::getMapName(mlvObject_t *pMlvObject)
{
    QString searchName = QString( "%1_%2x%3.fpm" ).arg( pMlvObject->IDNT.cameraModel, 0, 16 )
            .arg( pMlvObject->RAWI.raw_info.width )
            .arg( pMlvObject->RAWI.raw_info.height );

    return searchName;
}
