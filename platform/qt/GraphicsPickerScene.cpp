/*!
 * \file GraphicsPickerScene.cpp
 * \author masc4ii
 * \copyright 2017
 * \brief A GraphicsScene with picker functionality
 */

#include "GraphicsPickerScene.h"
#include <QDebug>
#include <QMimeData>
#include <QUrl>

//Constructor
GraphicsPickerScene::GraphicsPickerScene(QObject *parent) :
    QGraphicsScene(parent)
{
    m_pickerState = NoPicker;
    m_isGradientAdjustment = false;
    m_isMousePressed = false;
}

//Set wb picker on/off
void GraphicsPickerScene::setWbPickerActive(bool on)
{
    if( on ) m_pickerState = WbPicker;
    else m_pickerState = NoPicker;
}

//Set bp picker on/off
void GraphicsPickerScene::setBpPickerActive(bool on)
{
    if( on ) m_pickerState = BpPicker;
    else m_pickerState = NoPicker;
}

//Enable / disable Gradient adjustment
void GraphicsPickerScene::setGradientAdjustment(bool on)
{
    m_isGradientAdjustment = on;
    m_isMousePressed = false;
}

//Click event
void GraphicsPickerScene::mousePressEvent(QGraphicsSceneMouseEvent *event)
{
    QGraphicsScene::mousePressEvent(event);
    m_isMousePressed = true;
    if( m_pickerState == WbPicker )
    {
        m_pickerState = NoPicker;
        emit wbPicked( event->scenePos().x(), event->scenePos().y() );
    }
    if( m_pickerState == BpPicker )
    {
        m_pickerState = NoPicker;
        emit bpPicked( event->scenePos().x(), event->scenePos().y() );
    }
    if( m_isGradientAdjustment )
    {
        emit gradientAnchor( event->scenePos().x(), event->scenePos().y() );
    }
}

//Mouse release event
void GraphicsPickerScene::mouseReleaseEvent(QGraphicsSceneMouseEvent *event)
{
    QGraphicsScene::mouseReleaseEvent(event);
    m_isMousePressed = false;
    if( m_isGradientAdjustment )
    {
        emit gradientFinalPos( event->scenePos().x(), event->scenePos().y(), true );
    }
}

//Mouse move event
void GraphicsPickerScene::mouseMoveEvent(QGraphicsSceneMouseEvent *event)
{
    QGraphicsScene::mouseMoveEvent(event);
    if( m_isGradientAdjustment && m_isMousePressed )
    {
        emit gradientFinalPos( event->scenePos().x(), event->scenePos().y(), false );
    }
}

//Drop Event for opening MLV files
void GraphicsPickerScene::dropEvent(QGraphicsSceneDragDropEvent *event)
{
    // toLocalFile() returns native filesystem paths for both drive-letter
    // URLs (file:///E:/foo -> "E:/foo") and UNC URLs
    // (file://server/share/foo -> "//server/share/foo"). The previous
    // implementation used QUrl::path() which loses the host on UNC,
    // mangling \\ultra-magnus\e\... drag-drops to "e/..." (2026-04-24).
    const QList<QUrl> urls = event->mimeData()->urls();
    QStringList list;
    list.reserve( urls.size() );
    for( const QUrl & url : urls )
    {
        const QString localPath = url.toLocalFile();
        if( !localPath.isEmpty() )
            list.append( localPath );
    }
    emit filesDropped( list );
    event->acceptProposedAction();
}
