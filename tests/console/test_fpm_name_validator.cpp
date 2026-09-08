#include "../common/minitest.h"
#include "../../platform/qt/FpmNameValidator.h"

#include <QUrl>

// Characterization test (PR84 regression): DownloadManager::saveFileName used
// to gate ALL downloads through isValidFpmName alone. FocusPixelMapManager's
// catalog URL (.../contents/pixel_maps) and Updater's release-list URL
// (.../releases) both resolve to basenames with no "<hex>_<W>x<H>.fpm" shape,
// so the strict-only policy rejects them -- proving the caller regression.
TEST(FpmNameValidator, StrictFpmPolicyAloneRejectsMetadataCacheCallers)
{
    ASSERT_FALSE(isValidFpmName(QStringLiteral("pixel_maps")));
    ASSERT_FALSE(isValidFpmName(QStringLiteral("releases")));
}

TEST(FpmNameValidator, AcceptsOnlyCompletePixelMapNames)
{
    ASSERT_TRUE(isValidFpmName(QStringLiteral("80000331_1808x1190.fpm")));
    ASSERT_TRUE(isValidFpmName(QStringLiteral("abcdef09_1x2.fpm")));
    const QStringList invalid = {
        "../evil.fpm", "map.exe", "80000331_1808x1190.fpm.bak", "",
        "80000331_1808x1190.fpm\n", "80000331_1808x1190.fpm\r\n",
        "80000331_1808x1190.FPM", "ABCDEF_1x2.fpm", "g_1x2.fpm",
        "a_-1x2.fpm", "a_1x.fpm", " a_1x2.fpm", "a_1x2.fpm ",
        "dir/a_1x2.fpm", "dir\\a_1x2.fpm", "a_1x2.fpm:stream"
    };
    for (const QString &name : invalid)
        ASSERT_FALSE(isValidFpmName(name));
    ASSERT_FALSE(isValidFpmName(QStringLiteral("a_1x2.fpm") + QChar(0)));
}

TEST(FpmNameValidator, AllowsMetadataCacheBasenamesExactly)
{
    ASSERT_TRUE(isAllowedMetadataCacheBasename(QStringLiteral("pixel_maps")));
    ASSERT_TRUE(isAllowedMetadataCacheBasename(QStringLiteral("releases")));
    ASSERT_TRUE(isAllowedDownloadBasename(QStringLiteral("pixel_maps")));
    ASSERT_TRUE(isAllowedDownloadBasename(QStringLiteral("releases")));
}

// Metadata lookalikes must NOT get a free pass -- exact match only, no
// prefix/extension allowance.
TEST(FpmNameValidator, RejectsMetadataCacheLookalikes)
{
    const QStringList lookalikes = {
        "releases.exe", "releases.fpm", "Releases", "RELEASES",
        "pixel_maps.exe", "pixel_maps ", " pixel_maps", "pixel_mapsx",
        "xpixel_maps", "releases\n", "releases\r\n",
        "releases:stream", "pixel_maps:stream", "../releases", "../pixel_maps"
    };
    for (const QString &name : lookalikes) {
        ASSERT_FALSE(isAllowedMetadataCacheBasename(name));
        ASSERT_FALSE(isAllowedDownloadBasename(name));
    }
    // A literal "\x00" in a const char* is indistinguishable from an empty
    // suffix once QString reads it via strlen, so the embedded-NUL case has
    // to be built explicitly to actually keep the trailing NUL.
    ASSERT_FALSE(isAllowedMetadataCacheBasename(QStringLiteral("releases") + QChar(0)));
    ASSERT_FALSE(isAllowedDownloadBasename(QStringLiteral("releases") + QChar(0)));
}

// allowedDownloadBasenameForUrl is the exact helper DownloadManager::saveFileName
// delegates to. Exercise it directly against the real caller URLs so this test
// covers the same policy the production save path runs.
TEST(FpmNameValidator, AllowsExactCatalogUrlsUsedByRealCallers)
{
    // FocusPixelMapManager::getMapList()
    ASSERT_TRUE(allowedDownloadBasenameForUrl(
                    QUrl(QStringLiteral("https://api.github.com/repos/ilia3101/MLV-App/contents/pixel_maps")))
                == QStringLiteral("pixel_maps"));
    // Updater via mlvAppUpdateReleasesUrl()
    ASSERT_TRUE(allowedDownloadBasenameForUrl(
                    QUrl(QStringLiteral("https://api.github.com/repos/ilia3101/MLV-App/releases")))
                == QStringLiteral("releases"));
}

TEST(FpmNameValidator, AllowsValidRawFpmUrl)
{
    ASSERT_TRUE(allowedDownloadBasenameForUrl(
                    QUrl(QStringLiteral("https://raw.githubusercontent.com/ilia3101/MLV-App/master/"
                                         "pixel_maps/80000331_1808x1190.fpm")))
                == QStringLiteral("80000331_1808x1190.fpm"));
}

TEST(FpmNameValidator, RejectsInvalidUrlFilenames)
{
    const QStringList urls = {
        "https://example.com/map.exe",
        "https://example.com/pixel_maps/evil",
        "https://example.com/pixel_maps/evil.fpm",
        "https://example.com/releases/evil",
        "https://example.com/80000331_1808x1190.fpm.bak",
        "https://example.com/",
        "https://example.com",
    };
    for (const QString &url : urls)
        ASSERT_TRUE(allowedDownloadBasenameForUrl(QUrl(url)).isEmpty());
}

// A path segment named "pixel_maps" or "releases" that is NOT the final
// basename must not smuggle a different, unsafe basename through.
TEST(FpmNameValidator, MetadataLookalikeDirectoriesDoNotAllowArbitraryBasenames)
{
    ASSERT_TRUE(allowedDownloadBasenameForUrl(
                    QUrl(QStringLiteral("https://example.com/repos/ilia3101/MLV-App/contents/pixel_maps/evil")))
                    .isEmpty());
    ASSERT_TRUE(allowedDownloadBasenameForUrl(
                    QUrl(QStringLiteral("https://example.com/releases/../../etc/passwd")))
                    .isEmpty());
}

TEST(FpmNameValidator, RejectsTraversalAdsNewlineAndNulInUrls)
{
    const QStringList urls = {
        "https://example.com/pixel_maps/../evil.fpm",
        "https://example.com/a_1x2.fpm%3Astream",
        "https://example.com/a_1x2.fpm%0A",
        "https://example.com/a_1x2.fpm%00",
        "https://example.com/releases%0A",
        "https://example.com/pixel_maps%00",
    };
    for (const QString &url : urls)
        ASSERT_TRUE(allowedDownloadBasenameForUrl(QUrl(url)).isEmpty());
}
