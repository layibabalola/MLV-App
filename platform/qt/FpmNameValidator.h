#ifndef FPM_NAME_VALIDATOR_H
#define FPM_NAME_VALIDATOR_H

#include <QRegularExpression>
#include <QString>

inline bool isValidFpmName(const QString &name)
{
    // Absolute anchors also reject an otherwise valid name followed by a newline.
    static const QRegularExpression pattern(QStringLiteral("\\A[0-9a-f]+_[0-9]+x[0-9]+\\.fpm\\z"));
    return pattern.match(name).hasMatch();
}

#endif
