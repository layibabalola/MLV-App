#include "../../platform/qt/ColorToolButton.h"
#include "../../platform/qt/DualIsoPlaybackPolicy.h"
#include "../../platform/qt/GpuDisplayViewport.h"
#include "../../platform/qt/GpuPreviewProcessing.h"
#include "../../platform/qt/Histogram.h"
#include "../../platform/qt/MainWindowGpuPreviewPolicy.h"
#include "../../platform/qt/ScopesLabel.h"
#include "../../platform/qt/VectorScope.h"
#include "../../platform/qt/WaveFormMonitor.h"
#include "../../platform/qt/ZebraThresholds.h"
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
#include <QtTest/QtTest>

#include <cmath>
#include <cstring>
#include <memory>
#include <vector>

#ifdef Q_OS_WIN
#include <windows.h>
#endif

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
            if (lightness >= preview_zebra::kOverThreshold8Bit) {
                pixel[0] = 255;
                pixel[1] = 0;
                pixel[2] = 0;
            }
            if (lightness <= preview_zebra::kUnderThreshold8Bit) {
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

QByteArray make_identity_lut_bytes()
{
    QByteArray lut(static_cast<int>(65536u * sizeof(uint16_t)), 0);
    uint16_t *values = reinterpret_cast<uint16_t *>(lut.data());
    for (int index = 0; index < 65536; ++index) {
        values[index] = static_cast<uint16_t>(index);
    }
    return lut;
}

QByteArray make_scaled_lut_bytes(float factor)
{
    QByteArray lut(static_cast<int>(65536u * sizeof(uint16_t)), 0);
    uint16_t *values = reinterpret_cast<uint16_t *>(lut.data());
    for (int index = 0; index < 65536; ++index) {
        const int scaled = static_cast<int>(std::lround(static_cast<double>(index) * factor));
        values[index] = static_cast<uint16_t>(qBound(0, scaled, 65535));
    }
    return lut;
}

GpuPreviewProcessingConfig make_synthetic_preview_processing_config()
{
    GpuPreviewProcessingConfig config;
    config.enabled = true;
    config.useCameraMatrix = true;
    config.applyGamutCompression = true;

    config.properWbMatrix[0] = 1.0f;
    config.properWbMatrix[1] = 0.04f;
    config.properWbMatrix[2] = 0.00f;
    config.properWbMatrix[3] = 0.02f;
    config.properWbMatrix[4] = 0.98f;
    config.properWbMatrix[5] = 0.02f;
    config.properWbMatrix[6] = 0.00f;
    config.properWbMatrix[7] = 0.05f;
    config.properWbMatrix[8] = 0.95f;

    config.rgbToY[0] = 0.2126729f;
    config.rgbToY[1] = 0.7151522f;
    config.rgbToY[2] = 0.0721750f;
    config.levelsLut = make_identity_lut_bytes();
    config.matrixLutR = make_scaled_lut_bytes(0.80f);
    config.matrixLutG = make_scaled_lut_bytes(1.05f);
    config.matrixLutB = make_scaled_lut_bytes(1.10f);
    config.gammaLut = make_identity_lut_bytes();
    config.signature = 0xBEEFull;
    return config;
}

QImage rgb16_to_qimage(const std::vector<uint16_t> &rgb16, int width, int height)
{
    QImage image(width, height, QImage::Format_RGB888);
    for (int y = 0; y < height; ++y) {
        uint8_t *line = image.scanLine(y);
        for (int x = 0; x < width; ++x) {
            const std::size_t base = (static_cast<std::size_t>(y) * static_cast<std::size_t>(width) + static_cast<std::size_t>(x)) * 3u;
            line[x * 3 + 0] = static_cast<uint8_t>((rgb16[base + 0] + 128u) >> 8);
            line[x * 3 + 1] = static_cast<uint8_t>((rgb16[base + 1] + 128u) >> 8);
            line[x * 3 + 2] = static_cast<uint8_t>((rgb16[base + 2] + 128u) >> 8);
        }
    }
    return image;
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

QImage make_scopeslabel_scope_signature(const QImage &image)
{
    const QRect compare_rect(8, 4, image.width() - 16, image.height() - 8);
    QImage cropped = image.copy(compare_rect);
    cropped = cropped.scaled(64, 20, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
    return quantize_rgb888(cropped, 64);
}

void draw_scope_grid_lines(QImage &image, ScopesLabel::ScopeType type)
{
    QPainter painter(&image);
    QPen pen;
    pen.setStyle(Qt::DotLine);
    pen.setWidth(1);
    pen.setBrush(QColor(200, 200, 200, 96));
    painter.setPen(pen);

    const int width = image.width();
    const int height = image.height();
    if (type == ScopesLabel::ScopeHistogram) {
        painter.drawLine(width * 0.1, 0, width * 0.1, height - 1);
        painter.drawLine(width * 0.25, 0, width * 0.25, height - 1);
        painter.drawLine(width * 0.5, 0, width * 0.5, height - 1);
        painter.drawLine(width * 0.75, 0, width * 0.75, height - 1);
        painter.drawLine(width * 0.9, 0, width * 0.9, height - 1);
    } else if (type == ScopesLabel::ScopeWaveForm || type == ScopesLabel::ScopeRgbParade) {
        painter.drawLine(0, height * 0.1, width, height * 0.1);
        painter.drawLine(0, height * 0.25, width, height * 0.25);
        painter.drawLine(0, height * 0.5, width, height * 0.5);
        painter.drawLine(0, height * 0.75, width, height * 0.75);
        painter.drawLine(0, height * 0.9, width, height * 0.9);
    }
}

QImage render_expected_scope_label(const std::vector<uint8_t> &raw,
                                   int width,
                                   int height,
                                   bool under,
                                   bool over,
                                   ScopesLabel::ScopeType type)
{
    QImage scope_image;
    if (type == ScopesLabel::ScopeHistogram) {
        Histogram histogram;
        scope_image = histogram.getHistogramFromRaw(const_cast<uint8_t *>(raw.data()), width, height, under, over);
    } else if (type == ScopesLabel::ScopeWaveForm) {
        WaveFormMonitor waveform(width);
        scope_image = waveform.getWaveFormMonitorFromRaw(const_cast<uint8_t *>(raw.data()), width, height);
    } else if (type == ScopesLabel::ScopeRgbParade) {
        WaveFormMonitor waveform(width);
        scope_image = waveform.getParadeFromRaw(const_cast<uint8_t *>(raw.data()), width, height);
    } else if (type == ScopesLabel::ScopeVectorScope) {
        VectorScope vector_scope(511, 160);
        scope_image = vector_scope.getVectorScopeFromRaw(const_cast<uint8_t *>(raw.data()), width, height);
    } else {
        scope_image = QImage(511, 160, QImage::Format_RGB888);
        scope_image.fill(Qt::black);
    }

    QImage scaled = scope_image.scaled(511, 160, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
    draw_scope_grid_lines(scaled, type);
    return image_regression::normalize_rgb888(scaled);
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
    void mainWindowGpuPreviewPolicyAllowsGpu16OnlyWithoutScopes();
    void mainWindowGpuPreviewPolicyUsesGpuShaderZebrasWhenViewportInstalled();
    void mainWindowGpuPreviewPolicyBuildsExpectedPresenterOptions();
    void gpuViewportFallsBackToPixmapWhenNotInstalled();
    void gpuViewportQueuesAndClearsPresentedFrame();
    void gpuViewportQueuesRgb16Frame();
    void gpuViewportPresentsRgb888PatternExactly();
    void gpuViewportPresentsRgb16PatternExactly();
    void mainWindowGpuPreviewPolicyAllowsExperimentalProcessingOnlyWhenCompatible();
    void mainWindowGpuPreviewPolicyAllowsExperimentalBilinearDebayerOnlyWhenCompatible();
    void dualIsoPlaybackPolicyKeepsExplicitPreviewAndPlaybackOverrideSeparate();
    void gpuViewportRgb888ZebraProcessingMatchesCpuReference();
    void gpuViewportZebraProcessingMatchesCpuReference();
    void gpuViewportPreviewProcessingMatchesCpuReference();
    void gpuViewportPreviewProcessingWithZebrasMatchesCpuReference();
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

void GuiSmokeTest::mainWindowGpuPreviewPolicyAllowsGpu16OnlyWithoutScopes()
{
    MainWindowGpuPreviewPolicyState state;
    state.gpuViewportInstalled = true;
    state.renderThreadUsing16BitPreview = true;

    QVERIFY(mainWindowAllowsGpu16PreviewRender(state));
    QVERIFY(mainWindowUsesGpu16PreviewPresentation(state));
    QVERIFY(!mainWindowUsesGpuImagePresentation(state));

    state.histogramEnabled = true;
    QVERIFY(!mainWindowAllowsGpu16PreviewRender(state));
    QVERIFY(!mainWindowUsesGpu16PreviewPresentation(state));
    QVERIFY(mainWindowUsesGpuImagePresentation(state));

    state.histogramEnabled = false;
    state.waveformEnabled = true;
    QVERIFY(!mainWindowAllowsGpu16PreviewRender(state));
    QVERIFY(mainWindowUsesGpuImagePresentation(state));

    state.waveformEnabled = false;
    state.paradeEnabled = true;
    QVERIFY(!mainWindowAllowsGpu16PreviewRender(state));
    QVERIFY(mainWindowUsesGpuImagePresentation(state));

    state.paradeEnabled = false;
    state.vectorScopeEnabled = true;
    QVERIFY(!mainWindowAllowsGpu16PreviewRender(state));
    QVERIFY(mainWindowUsesGpuImagePresentation(state));

    state = MainWindowGpuPreviewPolicyState();
    state.gpuViewportInstalled = true;
    state.renderThreadUsing16BitPreview = false;
    QVERIFY(mainWindowAllowsGpu16PreviewRender(state));
    QVERIFY(!mainWindowUsesGpu16PreviewPresentation(state));
    QVERIFY(mainWindowUsesGpuImagePresentation(state));

    state.gpuViewportInstalled = false;
    QVERIFY(!mainWindowAllowsGpu16PreviewRender(state));
    QVERIFY(!mainWindowUsesGpu16PreviewPresentation(state));
    QVERIFY(!mainWindowUsesGpuImagePresentation(state));
}

void GuiSmokeTest::mainWindowGpuPreviewPolicyBuildsExpectedPresenterOptions()
{
    MainWindowGpuPreviewPolicyState state;
    state.gpuViewportInstalled = true;
    state.zebrasEnabled = true;
    state.transformationMode = Qt::FastTransformation;

    GpuDisplayViewport::PresentationOptions options =
        mainWindowBuildGpuPresentationOptions(state);
    QVERIFY(options.showZebras);
    QCOMPARE(options.samplingMode, GpuDisplayViewport::SamplingNearest);
    QCOMPARE(options.zebraUnderThreshold, preview_zebra::kUnderThresholdNormalized);
    QCOMPARE(options.zebraOverThreshold, preview_zebra::kOverThresholdNormalized);

    state.transformationMode = Qt::SmoothTransformation;
    state.betterResizerEnabled = false;
    options = mainWindowBuildGpuPresentationOptions(state);
    QCOMPARE(options.samplingMode, GpuDisplayViewport::SamplingLinear);

    state.betterResizerEnabled = true;
    options = mainWindowBuildGpuPresentationOptions(state);
    QCOMPARE(options.samplingMode, GpuDisplayViewport::SamplingBicubic);

    state.renderThreadUsing16BitPreview = true;
    options = mainWindowBuildGpuPresentationOptions(state);
    QCOMPARE(options.samplingMode, GpuDisplayViewport::SamplingBicubic);
    QVERIFY(options.showZebras);
}

void GuiSmokeTest::mainWindowGpuPreviewPolicyUsesGpuShaderZebrasWhenViewportInstalled()
{
    MainWindowGpuPreviewPolicyState state;
    QVERIFY(!mainWindowUsesGpuShaderZebraProcessing(state));

    state.zebrasEnabled = true;
    QVERIFY(!mainWindowUsesGpuShaderZebraProcessing(state));

    state.gpuViewportInstalled = true;
    QVERIFY(mainWindowUsesGpuShaderZebraProcessing(state));

    state.renderThreadUsing16BitPreview = true;
    QVERIFY(mainWindowUsesGpuShaderZebraProcessing(state));

    state.histogramEnabled = true;
    QVERIFY(mainWindowUsesGpuShaderZebraProcessing(state));
}

void GuiSmokeTest::mainWindowGpuPreviewPolicyAllowsExperimentalProcessingOnlyWhenCompatible()
{
    MainWindowGpuPreviewPolicyState state;
    state.gpuViewportInstalled = true;
    state.gpuPreviewProcessingBackendRequest = GpuPreviewProcessingBackendRequest::Gpu;
    state.gpuPreviewProcessingCompatible = true;
    state.renderThreadUsing16BitPreview = true;
    state.renderThreadUsingGpuProcessingPreview = true;

    QVERIFY(mainWindowAllowsGpuPreviewProcessing(state));
    QVERIFY(mainWindowUsesGpuPreviewProcessing(state));

    state.gpuPreviewProcessingCompatible = false;
    QVERIFY(!mainWindowAllowsGpuPreviewProcessing(state));
    QVERIFY(!mainWindowUsesGpuPreviewProcessing(state));

    state.gpuPreviewProcessingCompatible = true;
    state.histogramEnabled = true;
    QVERIFY(!mainWindowAllowsGpuPreviewProcessing(state));

    state = MainWindowGpuPreviewPolicyState();
    state.gpuViewportInstalled = true;
    state.gpuPreviewProcessingBackendRequest = GpuPreviewProcessingBackendRequest::Auto;
    state.gpuPreviewProcessingEnvironmentRequested = false;
    state.gpuPreviewProcessingCompatible = true;
    state.renderThreadUsing16BitPreview = true;
    state.renderThreadUsingGpuProcessingPreview = true;
    QVERIFY(!mainWindowAllowsGpuPreviewProcessing(state));

    state.gpuPreviewProcessingEnvironmentRequested = true;
    QVERIFY(mainWindowAllowsGpuPreviewProcessing(state));
    QVERIFY(mainWindowUsesGpuPreviewProcessing(state));

    state.gpuPreviewProcessingBackendRequest = GpuPreviewProcessingBackendRequest::Cpu;
    QVERIFY(!mainWindowAllowsGpuPreviewProcessing(state));
    QVERIFY(!mainWindowUsesGpuPreviewProcessing(state));
}

void GuiSmokeTest::mainWindowGpuPreviewPolicyAllowsExperimentalBilinearDebayerOnlyWhenCompatible()
{
    MainWindowGpuPreviewPolicyState state;
    state.gpuViewportInstalled = true;
    state.gpuPreviewProcessingBackendRequest = GpuPreviewProcessingBackendRequest::Gpu;
    state.gpuPreviewProcessingCompatible = true;
    state.renderThreadUsing16BitPreview = true;
    state.renderThreadUsingGpuProcessingPreview = true;
    state.gpuBilinearDebayerBackendRequest = GpuBilinearDebayerBackendRequest::Gpu;
    state.gpuBilinearDebayerCompatible = true;
    state.renderThreadUsingGpuBilinearDebayer = true;

    QVERIFY(mainWindowAllowsGpuBilinearDebayer(state));
    QVERIFY(mainWindowUsesGpuBilinearDebayer(state));

    state.gpuBilinearDebayerCompatible = false;
    QVERIFY(!mainWindowAllowsGpuBilinearDebayer(state));
    QVERIFY(!mainWindowUsesGpuBilinearDebayer(state));

    state.gpuBilinearDebayerCompatible = true;
    state.renderThreadUsingGpuBilinearDebayer = false;
    QVERIFY(mainWindowAllowsGpuBilinearDebayer(state));
    QVERIFY(!mainWindowUsesGpuBilinearDebayer(state));

    state.renderThreadUsingGpuBilinearDebayer = true;
    state.gpuPreviewProcessingCompatible = false;
    QVERIFY(!mainWindowAllowsGpuBilinearDebayer(state));
    QVERIFY(!mainWindowUsesGpuBilinearDebayer(state));

    state = MainWindowGpuPreviewPolicyState();
    state.gpuViewportInstalled = true;
    state.gpuPreviewProcessingBackendRequest = GpuPreviewProcessingBackendRequest::Auto;
    state.gpuPreviewProcessingEnvironmentRequested = true;
    state.gpuPreviewProcessingCompatible = true;
    state.renderThreadUsing16BitPreview = true;
    state.renderThreadUsingGpuProcessingPreview = true;
    state.gpuBilinearDebayerBackendRequest = GpuBilinearDebayerBackendRequest::Auto;
    state.gpuBilinearDebayerEnvironmentRequested = false;
    state.gpuBilinearDebayerCompatible = true;
    state.renderThreadUsingGpuBilinearDebayer = true;
    QVERIFY(!mainWindowAllowsGpuBilinearDebayer(state));

    state.gpuBilinearDebayerEnvironmentRequested = true;
    QVERIFY(mainWindowAllowsGpuBilinearDebayer(state));
    QVERIFY(mainWindowUsesGpuBilinearDebayer(state));

    state.gpuBilinearDebayerBackendRequest = GpuBilinearDebayerBackendRequest::Cpu;
    QVERIFY(!mainWindowAllowsGpuBilinearDebayer(state));
    QVERIFY(!mainWindowUsesGpuBilinearDebayer(state));
}

void GuiSmokeTest::dualIsoPlaybackPolicyKeepsExplicitPreviewAndPlaybackOverrideSeparate()
{
    DualIsoPlaybackRuntimeSettings settings = effectiveDualIsoPlaybackRuntimeSettings(false,
                                                                                      true,
                                                                                      1,
                                                                                      2,
                                                                                      0,
                                                                                      1,
                                                                                      1);
    QCOMPARE(settings.mode, 2);
    QCOMPARE(settings.interpolation, 1);
    QCOMPARE(settings.aliasMap, 0);
    QCOMPARE(settings.fullResBlending, 0);
    QVERIFY(!settings.previewOverrideActive);

    settings = effectiveDualIsoPlaybackRuntimeSettings(true,
                                                       true,
                                                       1,
                                                       1,
                                                       0,
                                                       1,
                                                       1);
    QCOMPARE(settings.mode, 2);
    QCOMPARE(settings.interpolation, 1);
    QCOMPARE(settings.aliasMap, 0);
    QCOMPARE(settings.fullResBlending, 0);
    QVERIFY(settings.previewOverrideActive);

    settings = effectiveDualIsoPlaybackRuntimeSettings(false,
                                                       true,
                                                       1,
                                                       1,
                                                       0,
                                                       1,
                                                       1);
    QCOMPARE(settings.mode, 1);
    QCOMPARE(settings.interpolation, 0);
    QCOMPARE(settings.aliasMap, 1);
    QCOMPARE(settings.fullResBlending, 1);
    QVERIFY(!settings.previewOverrideActive);
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

void GuiSmokeTest::gpuViewportRgb888ZebraProcessingMatchesCpuReference()
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
    QVERIFY(GpuDisplayViewport::presentImage(view.get(), item, submitted, options));

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

void GuiSmokeTest::gpuViewportPreviewProcessingMatchesCpuReference()
{
    if (QGuiApplication::platformName() == QStringLiteral("offscreen")) {
        QSKIP("GL viewport parity needs a platform plugin that can create an OpenGL context");
    }

    qputenv(GpuDisplayViewport::environmentVariableName(), QByteArrayLiteral("1"));

    const QImage submitted = make_presenter_pattern();
    const std::vector<uint16_t> rgb16 = make_presenter_pattern_rgb16();
    const GpuPreviewProcessingConfig processing = make_synthetic_preview_processing_config();
    std::vector<uint16_t> expected16(rgb16.size(), 0);
    gpuPreviewProcessingApplyCpuReference(processing, rgb16.data(), expected16.data(), submitted.width() * submitted.height());
    const QImage expected = presenter_expected_orientation(rgb16_to_qimage(expected16, submitted.width(), submitted.height()));

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
        QSKIP("GPU preview-processing parity is skipped on llvmpipe because the software GL stack does not produce stable shader output here.");
    }

    GpuDisplayViewport::PresentationOptions options;
    options.samplingMode = GpuDisplayViewport::SamplingNearest;
    options.previewProcessing = processing;
    QVERIFY(GpuDisplayViewport::presentRgb16(view.get(), item, rgb16.data(), submitted.width(), submitted.height(), options));

    const QImage actual = crop_presented_frame(view.get(), item);
    if (actual.isNull()) {
        QSKIP("OpenGL framebuffer capture is unavailable in this environment");
    }

    const QImage trimmed = trim_rounding_border(actual, submitted.size());
    QCOMPARE(trimmed.size(), expected.size());
    QString difference_message;
    QVERIFY2(image_regression::images_match_rgb888(expected, trimmed, 1, &difference_message),
             qPrintable(difference_message));

    GpuDisplayViewport::clearPresentedImage(view.get(), item);
    qunsetenv(GpuDisplayViewport::environmentVariableName());
}

void GuiSmokeTest::gpuViewportPreviewProcessingWithZebrasMatchesCpuReference()
{
    if (QGuiApplication::platformName() == QStringLiteral("offscreen")) {
        QSKIP("GL viewport parity needs a platform plugin that can create an OpenGL context");
    }

    qputenv(GpuDisplayViewport::environmentVariableName(), QByteArrayLiteral("1"));

    const QImage submitted = make_presenter_pattern();
    const std::vector<uint16_t> rgb16 = make_presenter_pattern_rgb16();
    const GpuPreviewProcessingConfig processing = make_synthetic_preview_processing_config();
    std::vector<uint16_t> expected16(rgb16.size(), 0);
    gpuPreviewProcessingApplyCpuReference(processing, rgb16.data(), expected16.data(), submitted.width() * submitted.height());
    const QImage expected = presenter_expected_orientation(apply_cpu_zebras(rgb16_to_qimage(expected16, submitted.width(), submitted.height())));

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
        QSKIP("GPU preview-processing parity is skipped on llvmpipe because the software GL stack does not produce stable shader output here.");
    }

    GpuDisplayViewport::PresentationOptions options;
    options.samplingMode = GpuDisplayViewport::SamplingNearest;
    options.showZebras = true;
    options.previewProcessing = processing;
    QVERIFY(GpuDisplayViewport::presentRgb16(view.get(), item, rgb16.data(), submitted.width(), submitted.height(), options));

    const QImage actual = crop_presented_frame(view.get(), item);
    if (actual.isNull()) {
        QSKIP("OpenGL framebuffer capture is unavailable in this environment");
    }

    const QImage trimmed = trim_rounding_border(actual, submitted.size());
    QCOMPARE(trimmed.size(), expected.size());
    QString difference_message;
    QVERIFY2(image_regression::images_match_rgb888(expected, trimmed, 1, &difference_message),
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
    const std::vector<uint8_t> raw = make_scope_raw_pattern(16, 8);
    const QImage actual = render_scopes_label_output(raw, 16, 8, false, false, ScopesLabel::ScopeWaveForm);
    QVERIFY(!actual.isNull());
    const QImage expected = render_expected_scope_label(raw, 16, 8, false, false, ScopesLabel::ScopeWaveForm);
    const QImage actual_signature = make_scopeslabel_scope_signature(actual);
    const QImage expected_signature = make_scopeslabel_scope_signature(expected);
    QString difference_message;
    QVERIFY2(image_regression::images_match_rgb888(expected_signature, actual_signature, 64, &difference_message),
             qPrintable(difference_message));
}

void GuiSmokeTest::scopesLabelDispatchesRawParadeExactly()
{
    const QMap<QString, QString> expected_hashes = load_expected_hashes();
    const std::vector<uint8_t> raw = make_scope_raw_pattern(16, 8);
    const QImage actual = render_scopes_label_output(raw, 16, 8, false, false, ScopesLabel::ScopeRgbParade);
    QVERIFY(!actual.isNull());
    assert_expected_hash(expected_hashes,
                         QStringLiteral("scopeslabel.raw_parade.signature"),
                         make_scopeslabel_scope_signature(actual));
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
    test_runtime::prefer_desktop_opengl_on_windows();
#ifdef Q_OS_WIN
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
#endif
    if ( qEnvironmentVariableIsEmpty("QT_QPA_PLATFORM") )
    {
        // Default GUI smoke runs to the offscreen platform so local launches do not
        // create native-window/OpenGL fail-fast dialogs in this workspace.
        qputenv("QT_QPA_PLATFORM", QByteArrayLiteral("offscreen"));
    }
    qputenv("QT_SCALE_FACTOR", QByteArrayLiteral("1"));
    qputenv("QT_SCREEN_SCALE_FACTORS", QByteArrayLiteral("1"));
    QApplication app(argc, argv);

    GuiSmokeTest test;
    return QTest::qExec(&test, argc, argv);
}

#include "test_gui_smoke.moc"
