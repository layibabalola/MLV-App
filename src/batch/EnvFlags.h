#ifndef MLVAPP_ENV_FLAGS_H
#define MLVAPP_ENV_FLAGS_H

#include <QByteArray>

/* Single source of truth for "is this MLVAPP_* env flag turned on" semantics,
 * shared by src/batch/WorkerThreadCount.h and platform/qt/main.cpp.
 *
 * An empty value (variable set to "") is treated as false at both call
 * sites. Only "1", "true", "yes" and "on" (case-insensitive, surrounding
 * whitespace trimmed) are true; everything else, including previously
 * accepted values like "2", "y", "enable" or "no", is false. */
inline bool mlvappEnvFlagEnabled(const QByteArray& raw)
{
    const QByteArray normalized = raw.trimmed().toLower();
    if (normalized.isEmpty()) {
        return false;
    }

    return normalized == QByteArrayLiteral("1")
        || normalized == QByteArrayLiteral("true")
        || normalized == QByteArrayLiteral("yes")
        || normalized == QByteArrayLiteral("on");
}

#endif
