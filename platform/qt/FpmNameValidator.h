#ifndef FPM_NAME_VALIDATOR_H
#define FPM_NAME_VALIDATOR_H

#include <QFileInfo>
#include <QRegularExpression>
#include <QString>
#include <QUrl>

inline bool isValidFpmName(const QString &name)
{
    // Absolute anchors also reject an otherwise valid name followed by a newline.
    static const QRegularExpression pattern(QStringLiteral("\\A[0-9a-f]+_[0-9]+x[0-9]+\\.fpm\\z"));
    return pattern.match(name).hasMatch();
}

// The two fixed, hardcoded basenames that the app's own metadata-cache
// downloads write under applicationDirPath() (FocusPixelMapManager's map
// catalog and Updater's release list). Exact-match only: this is not a
// prefix/extension allowance, so lookalikes such as "releases.exe" or
// "pixel_maps/evil" (whose basename per QFileInfo is "evil") are rejected,
// and any trailing traversal/ADS/newline/NUL content breaks the equality
// and is rejected too.
inline bool isAllowedMetadataCacheBasename(const QString &name)
{
    return name == QLatin1String("pixel_maps") || name == QLatin1String("releases");
}

// Shared download filename policy: exactly safe FPM basenames plus the two
// fixed metadata cache names above. Nothing else -- no generic extensionless
// or unrestricted path allowance.
inline bool isAllowedDownloadBasename(const QString &name)
{
    return isValidFpmName(name) || isAllowedMetadataCacheBasename(name);
}

// QtCore-only inline helper mapping a download URL to the on-disk basename
// callers should save under, or an empty string if the URL's basename is not
// on the allowlist. Preserves the original extraction:
// QFileInfo(url.path()).fileName().
inline QString allowedDownloadBasenameForUrl(const QUrl &url)
{
    const QString basename = QFileInfo(url.path()).fileName();
    return isAllowedDownloadBasename(basename) ? basename : QString();
}

#endif
