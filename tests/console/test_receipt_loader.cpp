#include "../common/minitest.h"
#include "../common/hash_helpers.h"
#include "../common/repo_paths.h"
#include "../common/test_artifacts.h"

#include "../../src/batch/ReceiptLoader.h"
#include "../../platform/qt/ReceiptSettings.h"

#include <QDir>
#include <QFile>
#include <QTextStream>
#include <QTemporaryDir>
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
    summary += QStringLiteral("lookAssist=%1;").arg(receipt.lookAssistEnabled());
    summary += QStringLiteral("lookAssistBase=%1;").arg(receipt.lookAssistBaselineValid());
    summary += QStringLiteral("lookAssistStretchX=%1;").arg(receipt.lookAssistBaselineStretchX());
    summary += QStringLiteral("lookAssistStretchY=%1;").arg(receipt.lookAssistBaselineStretchY());
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
    ASSERT_TRUE(receipt.lookAssistEnabled());
    ASSERT_FALSE(receipt.lookAssistBaselineValid());

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

TEST(ReceiptLoader, LoadsLookAssistReceiptState)
{
    QTemporaryDir tempDir;
    ASSERT_TRUE(tempDir.isValid());

    const QString receiptPath = tempDir.filePath(QStringLiteral("lookassist.marxml"));
    QFile file(receiptPath);
    ASSERT_TRUE(file.open(QIODevice::WriteOnly | QIODevice::Text));

    QTextStream out(&file);
    out << "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n";
    out << "<receipt version=\"4\" mlvapp=\"test\">\n";
    out << "  <exposure>34</exposure>\n";
    out << "  <contrast>12</contrast>\n";
    out << "  <pivot>58</pivot>\n";
    out << "  <rawFixesEnabled>1</rawFixesEnabled>\n";
    out << "  <rawBlack>20470</rawBlack>\n";
    out << "  <rawWhite>2840</rawWhite>\n";
    out << "  <lookAssistEnabled>0</lookAssistEnabled>\n";
    out << "  <lookAssistBaselineValid>1</lookAssistBaselineValid>\n";
    out << "  <lookAssistBaselineExposure>10</lookAssistBaselineExposure>\n";
    out << "  <lookAssistBaselineContrast>-4</lookAssistBaselineContrast>\n";
    out << "  <lookAssistBaselinePivot>47</lookAssistBaselinePivot>\n";
    out << "  <lookAssistBaselineVibrance>5</lookAssistBaselineVibrance>\n";
    out << "  <lookAssistBaselineShadows>18</lookAssistBaselineShadows>\n";
    out << "  <lookAssistBaselineHighlights>-12</lookAssistBaselineHighlights>\n";
    out << "  <lookAssistBaselineRawBlack>20490</lookAssistBaselineRawBlack>\n";
    out << "  <lookAssistBaselineRawWhite>3021</lookAssistBaselineRawWhite>\n";
    out << "  <lookAssistBaselineStretchX>1</lookAssistBaselineStretchX>\n";
    out << "  <lookAssistBaselineStretchY>0.3333</lookAssistBaselineStretchY>\n";
    out << "</receipt>\n";
    file.close();

    ReceiptSettings receipt;
    QString error_message;
    ASSERT_TRUE(ReceiptLoader::loadFromFile(receiptPath, &receipt, &error_message));
    ASSERT_TRUE(error_message.isEmpty());
    ASSERT_FALSE(receipt.lookAssistEnabled());
    ASSERT_TRUE(receipt.lookAssistBaselineValid());
    ASSERT_EQ(10, receipt.lookAssistBaselineExposure());
    ASSERT_EQ(-4, receipt.lookAssistBaselineContrast());
    ASSERT_EQ(47, receipt.lookAssistBaselinePivot());
    ASSERT_EQ(5, receipt.lookAssistBaselineVibrance());
    ASSERT_EQ(18, receipt.lookAssistBaselineShadows());
    ASSERT_EQ(-12, receipt.lookAssistBaselineHighlights());
    ASSERT_EQ(20490, receipt.lookAssistBaselineRawBlack());
    ASSERT_EQ(3021, receipt.lookAssistBaselineRawWhite());
    ASSERT_NEAR(1.0, receipt.lookAssistBaselineStretchX(), 0.0001);
    ASSERT_NEAR(0.3333, receipt.lookAssistBaselineStretchY(), 0.0001);
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

TEST(ReceiptLoader, LoadsDualIsoPreviewReceiptMode)
{
    ReceiptSettings receipt;
    QString error_message;

    ASSERT_TRUE(ReceiptLoader::loadFromFile(repo_file_path(QStringLiteral("tests/fixtures/receipts/large_dual_iso_preview.marxml")),
                                            &receipt,
                                            &error_message));
    ASSERT_EQ(2, receipt.dualIso());
    ASSERT_EQ(1, receipt.dualIsoAutoCorrected());
    ASSERT_EQ(1, receipt.dualIsoInterpolation());
    ASSERT_EQ(0, receipt.dualIsoAliasMap());
    ASSERT_EQ(0, receipt.dualIsoFrBlending());
}
