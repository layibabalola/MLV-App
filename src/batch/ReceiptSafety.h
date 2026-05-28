#ifndef RECEIPTSAFETY_H
#define RECEIPTSAFETY_H

#include "../../platform/qt/ReceiptSettings.h"
#include "../../src/mlv_include.h"

/* Receipts can be copied, imported, or used as defaults across unrelated
 * clips. Dual ISO correction fields are clip-local; carrying them across
 * clips can make the recon lock onto the wrong row pattern and corrupt both
 * GUI playback and exports. Keep creative receipt settings, but force the
 * technical Dual ISO detection state back to clip-local auto correction. */
static inline bool receiptSanitizeClipLocalDualIsoState( ReceiptSettings *receipt,
                                                         mlvObject_t *mlvObject )
{
    if( !receipt || !mlvObject || !mlvObject->llrawproc ) return false;

    bool changed = false;
    const int detectedValidity = llrpGetDualIsoValidity( mlvObject );

    if( detectedValidity == DISO_VALID )
    {
        if( receipt->dualIsoForced() != DISO_VALID )
        {
            receipt->setDualIsoForced( DISO_VALID );
            changed = true;
        }

        if( receipt->dualIso() != DISO_20BIT && receipt->dualIso() != DISO_FAST )
        {
            receipt->setDualIso( DISO_20BIT );
            changed = true;
        }

        if( receipt->dualIsoAutoCorrected() != 0 )
        {
            receipt->setDualIsoAutoCorrected( 0 );
            changed = true;
        }
        if( receipt->dualIsoPattern() != 0 )
        {
            receipt->setDualIsoPattern( 0 );
            changed = true;
        }
        if( receipt->dualIsoEvCorrection() != 1 )
        {
            receipt->setDualIsoEvCorrection( 1 );
            changed = true;
        }
        if( receipt->dualIsoBlackDelta() != -1 )
        {
            receipt->setDualIsoBlackDelta( -1 );
            changed = true;
        }
        if( receipt->dualIsoWhite() != 0 )
        {
            receipt->setDualIsoWhite( 0 );
            changed = true;
        }
        if( receipt->dualIsoBlack() != 0 )
        {
            receipt->setDualIsoBlack( 0 );
            changed = true;
        }

        const int originalBlack = getMlvOriginalBlackLevel( mlvObject );
        const int originalWhite = getMlvOriginalWhiteLevel( mlvObject );
        const int originalRawBlackSlider = originalBlack * 10;
        if( originalBlack > 0
         && receipt->rawBlack() != -1
         && receipt->rawBlack() != originalRawBlackSlider )
        {
            receipt->setRawBlack( originalRawBlackSlider );
            changed = true;
        }
        if( originalWhite > 0
         && receipt->rawWhite() != -1
         && receipt->rawWhite() != originalWhite )
        {
            receipt->setRawWhite( originalWhite );
            changed = true;
        }
    }
    else if( detectedValidity == DISO_INVALID
          && receipt->dualIsoForced() == DISO_VALID )
    {
        receipt->setDualIsoForced( DISO_INVALID );
        receipt->setDualIso( DISO_OFF );
        receipt->setDualIsoAutoCorrected( 0 );
        receipt->setDualIsoPattern( 0 );
        receipt->setDualIsoEvCorrection( 0 );
        receipt->setDualIsoBlackDelta( 0 );
        receipt->setDualIsoWhite( 0 );
        receipt->setDualIsoBlack( 0 );
        changed = true;
    }

    return changed;
}

#endif // RECEIPTSAFETY_H
