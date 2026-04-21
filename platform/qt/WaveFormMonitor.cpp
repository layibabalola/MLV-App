/*!
 * \file WaveFormMonitor.cpp
 * \author masc4ii
 * \copyright 2017
 * \brief Draws a RGB WaveForm Monitor for an image
 */

#include "WaveFormMonitor.h"

namespace
{
static inline uint8_t clampWaveformChannel( uint32_t value, double factor )
{
    const double scaledValue = value * factor;
    return scaledValue > 255.0 ? 255 : static_cast<uint8_t>( scaledValue );
}
}

//The higher this values, the higher the performance
//The lower this values, the higher the quality
//We skip only columns, because it is really ugly if not...
#define MERGE 8 //must be 2^x

//Constructor
WaveFormMonitor::WaveFormMonitor( uint16_t width )
{
    uint16_t imgX = width / MERGE;
    if( width % MERGE > 0 ) imgX++;
    m_pWaveForm = new QImage( imgX, 256, QImage::Format_RGB888 );
}

//Destructor
WaveFormMonitor::~WaveFormMonitor()
{
    delete m_pWaveForm;
}

//Make waveform monitor from Raw Image (8bit R, 8bit G, 8bit B,...)
QImage WaveFormMonitor::getWaveFormMonitorFromRaw(uint8_t *m_pRawImage, uint16_t width, uint16_t height)
{
    double factor = 10.0; //Intensity Factor, maybe make it a parameter one day...

    uint32_t tableR[256] = {0};
    uint32_t tableG[256] = {0};
    uint32_t tableB[256] = {0};

    for( int x = 0; x < width; x = x + MERGE )
    {
        //Sum the columns
        for( int y = 0; y < height; y++ )
        {
            //Merging and skipping lines for performance
            tableR[ m_pRawImage[ ( ( x + ( width * y ) ) * 3 ) + 0 ] ]++;
            tableG[ m_pRawImage[ ( ( x + ( width * y ) ) * 3 ) + 1 ] ]++;
            tableB[ m_pRawImage[ ( ( x + ( width * y ) ) * 3 ) + 2 ] ]++;
        }

        for( uint16_t y = 0; y <= 255; y++ )
        {
            uint8_t *pixel = m_pWaveForm->scanLine( y ) + ( ( x / MERGE ) * 3 );
            pixel[0] = clampWaveformChannel( tableR[255 - y], factor );
            pixel[1] = clampWaveformChannel( tableG[255 - y], factor );
            pixel[2] = clampWaveformChannel( tableB[255 - y], factor );

            //Reset
            tableR[255 - y] = 0;
            tableG[255 - y] = 0;
            tableB[255 - y] = 0;
        }
    }
    return *m_pWaveForm;
}

//Make Parade from Raw Image (8bit R, 8bit G, 8bit B,...)
QImage WaveFormMonitor::getParadeFromRaw(uint8_t *m_pRawImage, uint16_t width, uint16_t height)
{
    double factor = 10.0; //Intensity Factor, maybe make it a parameter one day...

    uint32_t tableR[256] = {0};
    uint32_t tableG[256] = {0};
    uint32_t tableB[256] = {0};

    for( int x = 0; x < width; x = x + MERGE + 3 )
    {
        //Sum the columns
        for( int y = 0; y < height; y++ )
        {
            //Merging and skipping lines for performance
            tableR[ m_pRawImage[ ( ( x + ( width * y ) ) * 3 ) + 0 ] ]++;
            tableG[ m_pRawImage[ ( ( x + ( width * y ) ) * 3 ) + 1 ] ]++;
            tableB[ m_pRawImage[ ( ( x + ( width * y ) ) * 3 ) + 2 ] ]++;
        }

        for( uint16_t y = 0; y <= 255; y++ )
        {
            uint8_t *redPixel = m_pWaveForm->scanLine( y ) + ( ( x / MERGE / 3 ) * 3 );
            redPixel[0] = clampWaveformChannel( tableR[255 - y], factor );
            redPixel[1] = 0;
            redPixel[2] = 0;

            uint8_t *greenPixel = m_pWaveForm->scanLine( y ) + ( ( ( x / MERGE / 3 ) + ( width / MERGE / 3 ) ) * 3 );
            greenPixel[0] = 0;
            greenPixel[1] = clampWaveformChannel( tableG[255 - y], factor );
            greenPixel[2] = 0;

            uint8_t *bluePixel = m_pWaveForm->scanLine( y ) + ( ( ( x / MERGE / 3 ) + ( width * 2 / MERGE / 3 ) ) * 3 );
            bluePixel[0] = 0;
            bluePixel[1] = 0;
            bluePixel[2] = clampWaveformChannel( tableB[255 - y], factor );

            //Reset
            tableR[255 - y] = 0;
            tableG[255 - y] = 0;
            tableB[255 - y] = 0;
        }
    }
    return *m_pWaveForm;
}

