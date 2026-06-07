/*!
 * \file TimeCodeLabel.h
 * \author masc4ii
 * \copyright 2018
 * \brief Paint TimeCode Image
 */

#ifndef TIMECODELABEL_H
#define TIMECODELABEL_H

#include <QImage>

class TimeCodeLabel
{
public:
    TimeCodeLabel();
    ~TimeCodeLabel();
    QImage getTimeCodeLabel( uint32_t frameNumber, float clipFps );
    QString getTimeCodeFromFps(uint32_t frameNumber, float clipFps, bool space = false);
    void setTimeDurationMode( bool mode );

private:
    QImage *m_tcImage;
    QImage m_chromeImage;
    bool m_mode;
    bool m_chromeValid;
    bool m_chromeMode;
};

#endif // TIMECODELABEL_H
