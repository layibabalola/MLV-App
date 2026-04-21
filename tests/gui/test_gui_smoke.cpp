#include "../../platform/qt/ColorToolButton.h"
#include "../../platform/qt/GpuDisplayViewport.h"
#include "../../platform/qt/Histogram.h"
#include "../../platform/qt/ScopesLabel.h"
#include "../../platform/qt/VectorScope.h"
#include "../../platform/qt/WaveFormMonitor.h"
#include "../common/image_regression.h"
#include "../common/repo_paths.h"
#include "../common/test_runtime.h"

#include <QApplication>
#include <QFile>
#include <QFrame>
#include <QGraphicsPixmapItem>
#include <QGraphicsScene>
#include <QGraphicsView>
#include <QGuiApplication>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMap>
#include <QPalette>
#include <QPainter>
#include <QtTest/QtTest>

#include <cstring>
#include <memory>
#include <vector>

namespace {

QImage presenter_expected_orientation(const QImage &submitted)
{
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    return submitted.flipped(Qt::Vertical);
#else
    return submitted.mirrored(false, true);
#endif
}

QMap<QString, QString> load_expected_hashes()
{
    const QString manifest_path = repo_file_path(QStringLiteral("tests/fixtures/golden/gui_hashes.json"));
    QFile file(manifest_path);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        return {};
    }

    const QJsonDocument document = QJsonDocument::fromJson(file.readAll());
    if (!document.isObject()) {
        return {};
    }

    QMap<QString, QString> hashes;
    const QJsonObject object = document.object();
    for (auto it = object.begin(); it != object.end(); ++it) {
        hashes.insert(it.key(), it.value().toString());
    }
    return hashes;
}

void set_raw_pixel(std::vector<uint8_t> &raw,
                   int width,
                   int x,
                   int y,
                   uint8_t red,
                   uint8_t green,
                   uint8_t blue)
{
    const int offset = ((y * width) + x) * 3;
    raw[static_cast<std::size_t>(offset + 0)] = red;
    raw[static_cast<std::size_t>(offset + 1)] = green;
    raw[static_cast<std::size_t>(offset + 2)] = blue;
}

std::vector<uint8_t> make_scope_raw_pattern(int width, int height)
{
    std::vector<uint8_t> raw(static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 3u, 0);

    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            uint8_t red = 0;
            uint8_t green = 0;
            uint8_t blue = 0;

            if (y < height / 2) {
                if (x < width / 4) {
                    red = 128;
                } else if (x < width / 2) {
                    green = 255;
                } else if (x < (width * 3) / 4) {
                    blue = 255;
                } else {
                    red = 128;
                    green = 128;
                    blue = 128;
                }
            } else {
                if (x < width / 4) {
                    red = 0;
                    green = 0;
                    blue = 0;
                } else if (x < width / 2) {
                    red = 255;
                    green = 255;
                } else if (x < (width * 3) / 4) {
                    green = 255;
                    blue = 255;
                } else {
                    red = 255;
                    blue = 255;
                }
            }

            set_raw_pixel(raw, width, x, y, red, green, blue);
        }
    }

    return raw;
}

QImage make_scope_source_image()
{
    const int width = 16;
    const int height = 8;
    const std::vector<uint8_t> raw = make_scope_raw_pattern(width, height);

    QImage image(width, height, QImage::Format_RGB888);
    for (int y = 0; y < height; ++y) {
        uint8_t *line = image.scanLine(y);
        const uint8_t *raw_line = raw.data() + (y * width * 3);
        memcpy(line, raw_line, static_cast<std::size_t>(width) * 3u);
    }
    return image;
}

QImage make_presenter_pattern()
{
    QImage image(4, 4, QImage::Format_RGB888);
    const QRgb colors[16] = {
        qRgb(255, 0, 0),     qRgb(0, 255, 0),     qRgb(0, 0, 255),     qRgb(255, 255, 255),
        qRgb(0, 0, 0),       qRgb(255, 255, 0),   qRgb(0, 255, 255),   qRgb(255, 0, 255),
        qRgb(255, 255, 255), qRgb(0, 0, 255),     qRgb(0, 255, 0),     qRgb(255, 0, 0),
        qRgb(255, 0, 255),   qRgb(0, 255, 255),   qRgb(255, 255, 0),   qRgb(0, 0, 0),
    };

    int index = 0;
    for (int y = 0; y < image.height(); ++y) {
        for (int x = 0; x < image.width(); ++x) {
            image.setPixel(x, y, colors[index++]);
        }
    }

    return image;
}

QImage apply_cpu_zebras(const QImage &submitted)
{
    QImage zebra = submitted.convertToFormat(QImage::Format_RGB888);
    for (int y = 0; y < zebra.height(); ++y) {
        uint8_t *line = zebra.scanLine(y);
        for (int x = 0; x < zebra.width(); ++x) {
            uint8_t *pixel = line + (x * 3);
            const int max_channel = qMax(pixel[0], qMax(pixel[1], pixel[2]));
            const int min_channel = qMin(pixel[0], qMin(pixel[1], pixel[2]));
            const int lightness = (max_channel + min_channel) / 2;
            if (lightness >= 252) {
                pixel[0] = 255;
                pixel[1] = 0;
                pixel[2] = 0;
            }
            if (lightness <= 3) {
                pixel[0] = 0;
                pixel[1] = 0;
                pixel[2] = 255;
            }
        }
    }
    return zebra;
}

std::vector<uint16_t> make_presenter_pattern_rgb16()
{
    const QImage image = make_presenter_pattern();
    std::vector<uint16_t> rgb16(static_cast<std::size_t>(image.width()) * static_cast<std::size_t>(image.height()) * 3u, 0);

    for (int y = 0; y < image.height(); ++y) {
        for (int x = 0; x < image.width(); ++x) {
            const QRgb pixel = image.pixel(x, y);
            const int offset = ((y * image.width()) + x) * 3;
            rgb16[static_cast<std::size_t>(offset + 0)] = qRed(pixel) ? 65535 : 0;
            rgb16[static_cast<std::size_t>(offset + 1)] = qGreen(pixel) ? 65535 : 0;
            rgb16[static_cast<std::size_t>(offset + 2)] = qBlue(pixel) ? 65535 : 0;
        }
    }

    return rgb16;
}

QImage crop_presented_frame(QGraphicsView *view, QGraphicsPixmapItem *item)
{
    auto *viewport = qobject_cast<GpuDisplayViewport *>(view->viewport());
    if (!viewport) {
        return QImage();
    }

    QImage framebuffer;
    for (int attempt = 0; attempt < 5; ++attempt) {
        QApplication::processEvents();
        viewport->update();
        viewport->repaint();
        QApplication::processEvents();
        framebuffer = viewport->grabFramebuffer();
        if (!framebuffer.isNull()) {
            break;
        }
        QTest::qWait(20);
    }

    if (framebuffer.isNull()) {
        return QImage();
    }

    const QRect logical_rect =
        view->mapFromScene(item->sceneBoundingRect()).boundingRect().intersected(view->viewport()->rect());
    if (logical_rect.isEmpty()) {
        return QImage();
    }

    const qreal dpr = viewport->devicePixelRatioF();
    const QRect device_rect(qRound(logical_rect.x() * dpr),
                            qRound(logical_rect.y() * dpr),
                            qRound(logical_rect.width() * dpr),
                            qRound(logical_rect.height() * dpr));
    QImage cropped = framebuffer.copy(device_rect);
    if (cropped.isNull()) {
        return QImage();
    }

    if (device_rect.size() != logical_rect.size()) {
        cropped = cropped.scaled(logical_rect.size(), Qt::IgnoreAspectRatio, Qt::FastTransformation);
    }

    return image_regression::normalize_rgb888(cropped);
}

QImage trim_rounding_border(const QImage &image, const QSize &expected_size)
{
    QImage trimmed = image;
    if (trimmed.width() == expected_size.width() + 1) {
        trimmed = trimmed.copy(0, 0, expected_size.width(), trimmed.height());
    }
    if (trimmed.height() == expected_size.height() + 1) {
        trimmed = trimmed.copy(0, 0, trimmed.width(), expected_size.height());
    }
    return trimmed;
}

QImage normalize_scope_pixmap(const QPixmap &pixmap)
{
    QImage image = pixmap.toImage();
    const QSize logical_size = pixmap.deviceIndependentSize().toSize();
    if (logical_size.isValid() && logical_size != image.size()) {
        image = image.scaled(logical_size, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
    }
    return image_regression::normalize_rgb888(image);
}

QImage quantize_rgb888(const QImage &image, int quantum)
{
    QImage quantized = image_regression::normalize_rgb888(image);
    if (quantum <= 1) {
        return quantized;
    }

    for (int y = 0; y < quantized.height(); ++y) {
        uint8_t *line = quantized.scanLine(y);
        for (int x = 0; x < quantized.width(); ++x) {
            uint8_t *pixel = line + (x * 3);
            for (int channel = 0; channel < 3; ++channel) {
                pixel[channel] = static_cast<uint8_t>((pixel[channel] / quantum) * quantum);
            }
        }
    }

    return quantized;
}

QImage make_scopeslabel_signature(const QImage &image)
{
    const QRect compare_rect(8, 4, image.width() - 16, image.height() - 8);
    QImage cropped = image.copy(compare_rect);
    cropped = cropped.scaled(64, 20, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
    return quantize_rgb888(cropped, 64);
}

QImage render_scopes_label_output(const std::vector<uint8_t> &raw,
                                  int width,
                                  int height,
                                  bool under,
                                  bool over,
                                  ScopesLabel::ScopeType type)
{
    ScopesLabel label;
    label.resize(511, 160);

    label.setScope(const_cast<uint8_t *>(raw.data()),
                   static_cast<uint16_t>(width),
                   static_cast<uint16_t>(height),
                   under,
                   over,
                   type);

    const QPixmap pixmap = label.pixmap();
    if (pixmap.isNull()) {
        return QImage();
    }

    return normalize_scope_pixmap(pixmap);
}

QGraphicsView *make_presenter_view(QGraphicsScene &scene,
                                   QGraphicsPixmapItem *item,
                                   const QSize &pattern_size)
{
    auto *view = new QGraphicsView(&scene);
    view->setFrameShape(QFrame::NoFrame);
    view->setAlignment(Qt::AlignLeft | Qt::AlignTop);
    view->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    view->setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    view->setSceneRect(QRectF(QPointF(0.0, 0.0), QSizeF(pattern_size)));
    item->setOffset(0.0, 0.0);
    view->resize(pattern_size.width() + 8, pattern_size.height() + 8);
    return view;
}

void assert_expected_hash(const QMap<QString, QString> &expected_hashes,
                          const QString &key,
                          const QImage &image)
{
    const QString actual_hash = QString::fromStdString(image_regression::sha256_rgb888(image));
    QVERIFY2(expected_hashes.contains(key),
             qPrintable(QStringLiteral("Missing golden hash for key %1 (actual=%2)").arg(key, actual_hash)));
    QCOMPARE(actual_hash, expected_hashes.value(key));
}

} // namespace

class GuiSmokeTest : public QObject
{
    Q_OBJECT

private slots:
    void checkedStateUpdatesPalette();
    void gpuViewportFallsBackToPixmapWhenNotInstalled();
    void gpuViewportQueuesAndClearsPresentedFrame();
    void gpuViewportQueuesRgb16Frame();
    void gpuViewportPresentsRgb888PatternExactly();
    void gpuViewportPresentsRgb16PatternExactly();
    void gpuViewportZebraProcessingMatchesCpuReference();
    void histogramRegressionMatchesGolden();
    void vectorScopeRegressionMatchesGolden();
    void waveformRegressionMatchesGolden();
    void scopesLabelDispatchesRawHistogramExactly();
    void scopesLabelDispatchesRawWaveformExactly();
    void scopesLabelDispatchesRawParadeExactly();
    void scopesLabelDispatchesRawVectorScopeExactly();
};

void GuiSmokeTest::checkedStateUpdatesPalette()
{
    ColorToolButton button;
    button.setCheckable(true);

    const QColor original_button = button.palette().color(QPalette::Button);
    button.setChecked(true);
    QCOMPARE(button.palette().color(QPalette::Button), QColor(127, 127, 127));
    QCOMPARE(button.palette().color(QPalette::ButtonText), QColor(Qt::white));

    button.setChecked(false);
    QCOMPARE(button.palette().color(QPalette::Button), original_button);
}

void GuiSmokeTest::gpuViewportFallsBackToPixmapWhenNotInstalled()
{
    qunsetenv(GpuDisplayViewport::environmentVariableName());

    QGraphicsScene scene;
    QGraphicsPixmapItem *item = scene.addPixmap(QPixmap(4, 4));
    QGraphicsView view(&scene);

    QVERIFY(!GpuDisplayViewport::isInstalledOn(&view));

    QImage image(8, 6, QImage::Format_RGB888);
    image.fill(Qt::blue);

    QVERIFY(!GpuDisplayViewport::presentImage(&view, item, image));
    QVERIFY(item->isVisible());
    QVERIFY(!GpuDisplayViewport::hasPresentedImage(&view));
    QVERIFY(!GpuDisplayViewport::isTexturePresentationActive(&view));
    QCOMPARE(GpuDisplayViewport::samplingModeFor(&view), GpuDisplayViewport::SamplingLinear);

    GpuDisplayViewport::clearPresentedImage(&view, item);
    QVERIFY(item->isVisible());
}

void GuiSmokeTest::gpuViewportQueuesAndClearsPresentedFrame()
{
    qputenv(GpuDisplayViewport::environmentVariableName(), QByteArrayLiteral("1"));

    QGraphicsScene scene;
    QGraphicsPixmapItem *item = scene.addPixmap(QPixmap(4, 4));
    QGraphicsView view(&scene);
    view.resize(64, 64);
    view.show();
    QApplication::processEvents();

    QVERIFY(GpuDisplayViewport::installOn(&view));
    QVERIFY(GpuDisplayViewport::isInstalledOn(&view));
    QVERIFY(!GpuDisplayViewport::hasPresentedImage(&view));
    QVERIFY(!GpuDisplayViewport::isTexturePresentationActive(&view));
    QCOMPARE(GpuDisplayViewport::samplingModeFor(&view), GpuDisplayViewport::SamplingLinear);

    QImage image(8, 6, QImage::Format_RGB888);
    image.fill(Qt::red);
    GpuDisplayViewport::PresentationOptions options;
    options.samplingMode = GpuDisplayViewport::SamplingBicubic;

    QVERIFY(GpuDisplayViewport::presentImage(&view, item, image, options));
    QVERIFY(GpuDisplayViewport::hasPresentedImage(&view));
    QCOMPARE(GpuDisplayViewport::samplingModeFor(&view), GpuDisplayViewport::SamplingBicubic);
    QVERIFY(!item->isVisible());

    QApplication::processEvents();
    QVERIFY(GpuDisplayViewport::hasPresentedImage(&view));

    options.samplingMode = GpuDisplayViewport::SamplingNearest;
    QVERIFY(GpuDisplayViewport::presentImage(&view, item, image, options));
    QCOMPARE(GpuDisplayViewport::samplingModeFor(&view), GpuDisplayViewport::SamplingNearest);
    QApplication::processEvents();
    QVERIFY(GpuDisplayViewport::hasPresentedImage(&view));

    GpuDisplayViewport::clearPresentedImage(&view, item);
    QVERIFY(!GpuDisplayViewport::hasPresentedImage(&view));
    QVERIFY(!GpuDisplayViewport::isTexturePresentationActive(&view));
    QVERIFY(item->isVisible());

    qunsetenv(GpuDisplayViewport::environmentVariableName());
}

void GuiSmokeTest::gpuViewportQueuesRgb16Frame()
{
    qputenv(GpuDisplayViewport::environmentVariableName(), QByteArrayLiteral("1"));

    QGraphicsScene scene;
    QGraphicsPixmapItem *item = scene.addPixmap(QPixmap(4, 4));
    QGraphicsView view(&scene);
    view.resize(64, 64);
    view.show();
    QApplication::processEvents();

    QVERIFY(GpuDisplayViewport::installOn(&view));

    const uint16_t rgb16[] = {
        65535, 0, 0,
        0, 65535, 0,
        0, 0, 65535,
        65535, 65535, 65535
    };

    GpuDisplayViewport::PresentationOptions options;
    options.samplingMode = GpuDisplayViewport::SamplingLinear;
    QVERIFY(GpuDisplayViewport::presentRgb16(&view, item, rgb16, 2, 2, options));
    QVERIFY(GpuDisplayViewport::hasPresentedImage(&view));
    QVERIFY(!item->isVisible());
    QApplication::processEvents();
    QVERIFY(GpuDisplayViewport::hasPresentedImage(&view));

    GpuDisplayViewport::clearPresentedImage(&view, item);
    QVERIFY(item->isVisible());
    qunsetenv(GpuDisplayViewport::environmentVariableName());
}

void GuiSmokeTest::gpuViewportPresentsRgb888PatternExactly()
{
    if (QGuiApplication::platformName() == QStringLiteral("offscreen")) {
        QSKIP("GL viewport parity needs a platform plugin that can create an OpenGL context");
    }

    const QMap<QString, QString> expected_hashes = load_expected_hashes();
    qputenv(GpuDisplayViewport::environmentVariableName(), QByteArrayLiteral("1"));

    const QImage submitted = make_presenter_pattern();
    const QImage expected = presenter_expected_orientation(submitted);

    QGraphicsScene scene;
    QPixmap fallback_pixmap(submitted.size());
    fallback_pixmap.fill(Qt::black);
    QGraphicsPixmapItem *item = scene.addPixmap(fallback_pixmap);
    std::unique_ptr<QGraphicsView> view(make_presenter_view(scene, item, submitted.size()));

    QVERIFY(GpuDisplayViewport::installOn(view.get()));
    view->show();
    QApplication::processEvents();
    GpuDisplayViewport::PresentationOptions options;
    options.samplingMode = GpuDisplayViewport::SamplingNearest;
    QVERIFY(GpuDisplayViewport::presentImage(view.get(), item, submitted, options));

    const QImage actual = crop_presented_frame(view.get(), item);
    if (actual.isNull()) {
        QSKIP("OpenGL framebuffer capture is unavailable in this environment");
    }

    const QImage trimmed = trim_rounding_border(actual, submitted.size());
    QCOMPARE(trimmed.size(), expected.size());
    assert_expected_hash(expected_hashes, QStringLiteral("gpu.viewport.rgb888.pattern_nearest"), trimmed);

    GpuDisplayViewport::clearPresentedImage(view.get(), item);
    qunsetenv(GpuDisplayViewport::environmentVariableName());
}

void GuiSmokeTest::gpuViewportPresentsRgb16PatternExactly()
{
    if (QGuiApplication::platformName() == QStringLiteral("offscreen")) {
        QSKIP("GL viewport parity needs a platform plugin that can create an OpenGL context");
    }

    const QMap<QString, QString> expected_hashes = load_expected_hashes();
    qputenv(GpuDisplayViewport::environmentVariableName(), QByteArrayLiteral("1"));

    const QImage submitted = make_presenter_pattern();
    const QImage expected = presenter_expected_orientation(submitted);
    const std::vector<uint16_t> rgb16 = make_presenter_pattern_rgb16();

    QGraphicsScene scene;
    QPixmap fallback_pixmap(submitted.size());
    fallback_pixmap.fill(Qt::black);
    QGraphicsPixmapItem *item = scene.addPixmap(fallback_pixmap);
    std::unique_ptr<QGraphicsView> view(make_presenter_view(scene, item, submitted.size()));

    QVERIFY(GpuDisplayViewport::installOn(view.get()));
    view->show();
    QApplication::processEvents();

    GpuDisplayViewport::PresentationOptions options;
    options.samplingMode = GpuDisplayViewport::SamplingNearest;
    QVERIFY(GpuDisplayViewport::presentRgb16(view.get(),
                                             item,
                                             rgb16.data(),
                                             submitted.width(),
                                             submitted.height(),
                                             options));

    const QImage actual = crop_presented_frame(view.get(), item);
    if (actual.isNull()) {
        QSKIP("OpenGL framebuffer capture is unavailable in this environment");
    }

    const QImage trimmed = trim_rounding_border(actual, submitted.size());
    QCOMPARE(trimmed.size(), expected.size());
    assert_expected_hash(expected_hashes, QStringLiteral("gpu.viewport.rgb16.pattern_nearest"), trimmed);

    GpuDisplayViewport::clearPresentedImage(view.get(), item);
    qunsetenv(GpuDisplayViewport::environmentVariableName());
}

void GuiSmokeTest::gpuViewportZebraProcessingMatchesCpuReference()
{
    if (QGuiApplication::platformName() == QStringLiteral("offscreen")) {
        QSKIP("GL viewport parity needs a platform plugin that can create an OpenGL context");
    }

    qputenv(GpuDisplayViewport::environmentVariableName(), QByteArrayLiteral("1"));

    QImage submitted(4, 4, QImage::Format_RGB888);
    submitted.fill(Qt::gray);
    submitted.setPixel(0, 0, qRgb(255, 255, 255));
    submitted.setPixel(1, 0, qRgb(0, 0, 0));
    submitted.setPixel(2, 0, qRgb(250, 250, 250));
    submitted.setPixel(3, 0, qRgb(5, 5, 5));

    const QImage expected = presenter_expected_orientation(apply_cpu_zebras(submitted));
    std::vector<uint16_t> rgb16(static_cast<std::size_t>(submitted.width()) * static_cast<std::size_t>(submitted.height()) * 3u, 0);
    for (int y = 0; y < submitted.height(); ++y) {
        for (int x = 0; x < submitted.width(); ++x) {
            const QRgb pixel = submitted.pixel(x, y);
            const int offset = ((y * submitted.width()) + x) * 3;
            rgb16[static_cast<std::size_t>(offset + 0)] = static_cast<uint16_t>(qRed(pixel) * 257u);
            rgb16[static_cast<std::size_t>(offset + 1)] = static_cast<uint16_t>(qGreen(pixel) * 257u);
            rgb16[static_cast<std::size_t>(offset + 2)] = static_cast<uint16_t>(qBlue(pixel) * 257u);
        }
    }

    QGraphicsScene scene;
    QPixmap fallback_pixmap(submitted.size());
    fallback_pixmap.fill(Qt::black);
    QGraphicsPixmapItem *item = scene.addPixmap(fallback_pixmap);
    std::unique_ptr<QGraphicsView> view(make_presenter_view(scene, item, submitted.size()));

    QVERIFY(GpuDisplayViewport::installOn(view.get()));
    view->show();
    QApplication::processEvents();
    const QString renderer = GpuDisplayViewport::rendererDescriptionFor(view.get());
    if (renderer.contains(QStringLiteral("llvmpipe"), Qt::CaseInsensitive)) {
        QSKIP("GPU zebra parity is skipped on llvmpipe because the software GL stack does not produce stable shader-processed output here.");
    }

    GpuDisplayViewport::PresentationOptions options;
    options.samplingMode = GpuDisplayViewport::SamplingNearest;
    options.showZebras = true;
    QVERIFY(GpuDisplayViewport::presentRgb16(view.get(),
                                             item,
                                             rgb16.data(),
                                             submitted.width(),
                                             submitted.height(),
                                             options));

    const QImage actual = crop_presented_frame(view.get(), item);
    if (actual.isNull()) {
        QSKIP("OpenGL framebuffer capture is unavailable in this environment");
    }

    const QImage trimmed = trim_rounding_border(actual, submitted.size());
    QCOMPARE(trimmed.size(), expected.size());
    QString difference_message;
    QVERIFY2(image_regression::images_match_rgb888(expected, trimmed, 0, &difference_message),
             qPrintable(difference_message));

    GpuDisplayViewport::clearPresentedImage(view.get(), item);
    qunsetenv(GpuDisplayViewport::environmentVariableName());
}

void GuiSmokeTest::histogramRegressionMatchesGolden()
{
    const QMap<QString, QString> expected_hashes = load_expected_hashes();
    const QImage source = make_scope_source_image();

    Histogram histogram;
    QImage image = histogram.getHistogramFromImg(const_cast<QImage *>(&source));

    QCOMPARE(image.size(), QSize(511, 140));
    assert_expected_hash(expected_hashes, QStringLiteral("scope.histogram.synthetic_rgb888"), image);
}

void GuiSmokeTest::vectorScopeRegressionMatchesGolden()
{
    const QMap<QString, QString> expected_hashes = load_expected_hashes();
    std::vector<uint8_t> raw = make_scope_raw_pattern(16, 8);

    VectorScope scope(128, 128);
    QImage image = scope.getVectorScopeFromRaw(raw.data(), 16, 8);

    QCOMPARE(image.size(), QSize(128, 128));
    QCOMPARE(image.pixelColor(64, 64), QColor(128, 128, 128));
    assert_expected_hash(expected_hashes, QStringLiteral("scope.vectorscope.synthetic_raw"), image);
}

void GuiSmokeTest::waveformRegressionMatchesGolden()
{
    const QMap<QString, QString> expected_hashes = load_expected_hashes();
    std::vector<uint8_t> raw = make_scope_raw_pattern(16, 8);

    WaveFormMonitor waveform(16);
    QImage image = waveform.getWaveFormMonitorFromRaw(raw.data(), 16, 8);

    QCOMPARE(image.size(), QSize(2, 256));
    QCOMPARE(image.pixelColor(0, 127), QColor(40, 0, 0));
    QCOMPARE(image.pixelColor(0, 255), QColor(40, 80, 80));
    QCOMPARE(image.pixelColor(1, 0), QColor(0, 40, 80));
    QCOMPARE(image.pixelColor(1, 255), QColor(80, 40, 0));
    assert_expected_hash(expected_hashes, QStringLiteral("scope.waveform.synthetic_raw"), image);
}

void GuiSmokeTest::scopesLabelDispatchesRawHistogramExactly()
{
    const QMap<QString, QString> expected_hashes = load_expected_hashes();
    const std::vector<uint8_t> raw = make_scope_raw_pattern(16, 8);
    const QImage actual = render_scopes_label_output(raw, 16, 8, true, true, ScopesLabel::ScopeHistogram);
    QVERIFY(!actual.isNull());
    assert_expected_hash(expected_hashes, QStringLiteral("scopeslabel.raw_histogram"), actual);
}

void GuiSmokeTest::scopesLabelDispatchesRawWaveformExactly()
{
    const QMap<QString, QString> expected_hashes = load_expected_hashes();
    const std::vector<uint8_t> raw = make_scope_raw_pattern(16, 8);
    const QImage actual = render_scopes_label_output(raw, 16, 8, false, false, ScopesLabel::ScopeWaveForm);
    QVERIFY(!actual.isNull());
    assert_expected_hash(expected_hashes,
                         QStringLiteral("scopeslabel.raw_waveform.signature"),
                         make_scopeslabel_signature(actual));
}

void GuiSmokeTest::scopesLabelDispatchesRawParadeExactly()
{
    const QMap<QString, QString> expected_hashes = load_expected_hashes();
    const std::vector<uint8_t> raw = make_scope_raw_pattern(16, 8);
    const QImage actual = render_scopes_label_output(raw, 16, 8, false, false, ScopesLabel::ScopeRgbParade);
    QVERIFY(!actual.isNull());
    assert_expected_hash(expected_hashes,
                         QStringLiteral("scopeslabel.raw_parade.signature"),
                         make_scopeslabel_signature(actual));
}

void GuiSmokeTest::scopesLabelDispatchesRawVectorScopeExactly()
{
    const QMap<QString, QString> expected_hashes = load_expected_hashes();
    const std::vector<uint8_t> raw = make_scope_raw_pattern(16, 8);
    const QImage actual = render_scopes_label_output(raw, 16, 8, false, false, ScopesLabel::ScopeVectorScope);
    QVERIFY(!actual.isNull());
    assert_expected_hash(expected_hashes, QStringLiteral("scopeslabel.raw_vectorscope"), actual);
}

int main(int argc, char ** argv)
{
    test_runtime::force_single_threaded_pipeline();
    qputenv("QT_SCALE_FACTOR", QByteArrayLiteral("1"));
    qputenv("QT_SCREEN_SCALE_FACTORS", QByteArrayLiteral("1"));
    QApplication app(argc, argv);

    GuiSmokeTest test;
    return QTest::qExec(&test, argc, argv);
}

#include "test_gui_smoke.moc"
