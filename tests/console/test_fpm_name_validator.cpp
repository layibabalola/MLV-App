#include "../common/minitest.h"
#include "../../platform/qt/FpmNameValidator.h"

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
