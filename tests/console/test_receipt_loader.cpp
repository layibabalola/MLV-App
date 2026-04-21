#include "../common/minitest.h"
#include "../common/hash_helpers.h"
#include "../common/repo_paths.h"
#include "../common/test_artifacts.h"

#include "../../src/batch/ReceiptLoader.h"
#include "../../platform/qt/ReceiptSettings.h"

#include <QDir>
#include <QString>

static QString repo_receipt_path(const QString & name)
{
    return repo_file_path(QStringLiteral("receipts/%1").arg(name));
}

static QString receipt_summary(ReceiptSettings & receipt)
{
    QString summary;
    summary += QStringLiteral("pivot=%1;").arg(receipt.pivot());
    summary += QStringLiteral("rawFixes=%1;").arg(receipt.rawFixesEnabled());
    summary += QStringLiteral("focusPixels=%1;").arg(receipt.focusPixels());
    summary += QStringLiteral("dualIso=%1;").arg(receipt.dualIso());
    summary += QStringLiteral("alias=%1;").arg(receipt.dualIsoAliasMap());
    summary += QStringLiteral("fullres=%1;").arg(receipt.dualIsoFrBlending());
    summary += QStringLiteral("rawBlack=%1;").arg(receipt.rawBlack());
    summary += QStringLiteral("rawWhite=%1;").arg(receipt.rawWhite());
    summary += QStringLiteral("cutOut=%1;").arg(receipt.cutOut());
    summary += QStringLiteral("debayer=%1;").arg(receipt.debayer());
    return summary;
}

TEST(ReceiptLoader, LoadsFastProxyReceipt)
{
    ReceiptSettings receipt;
    QString error_message;

    ASSERT_TRUE(ReceiptLoader::loadFromFile(repo_receipt_path(QStringLiteral("FastProxy.marxml")),
                                            &receipt,
                                            &error_message));
    ASSERT_FALSE(receipt.wasNeverLoaded());
    ASSERT_EQ(75, receipt.pivot());
    ASSERT_TRUE(receipt.rawFixesEnabled());
    ASSERT_EQ(1, receipt.focusPixels());
    ASSERT_EQ(143u, receipt.cutOut());
    ASSERT_EQ(static_cast<unsigned int>(2), static_cast<unsigned int>(receipt.debayer()));
    ASSERT_EQ(20470, receipt.rawBlack());
    ASSERT_EQ(2840, receipt.rawWhite());

    test_artifacts::record("receipt.fast_proxy",
                           sha256_qstring(receipt_summary(receipt)));
}

TEST(ReceiptLoader, LoadsFastProxyRcdReceipt)
{
    ReceiptSettings receipt;
    QString error_message;

    ASSERT_TRUE(ReceiptLoader::loadFromFile(repo_receipt_path(QStringLiteral("FastProxyRCD.marxml")),
                                            &receipt,
                                            &error_message));
    ASSERT_EQ(static_cast<unsigned int>(7), static_cast<unsigned int>(receipt.debayer()));
    ASSERT_EQ(1, receipt.dualIsoAliasMap());
    ASSERT_EQ(1, receipt.dualIsoFrBlending());

    test_artifacts::record("receipt.fast_proxy_rcd",
                           sha256_qstring(receipt_summary(receipt)));
}

TEST(ReceiptLoader, MissingReceiptReturnsUsefulError)
{
    ReceiptSettings receipt;
    QString error_message;

    ASSERT_FALSE(ReceiptLoader::loadFromFile(repo_receipt_path(QStringLiteral("missing.marxml")),
                                             &receipt,
                                             &error_message));
    ASSERT_TRUE(!error_message.isEmpty());
}
