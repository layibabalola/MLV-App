#include "image_regression.h"

#include "hash_helpers.h"

namespace image_regression {

QImage normalize_rgb888(const QImage &image)
{
    if (image.isNull()) {
        return QImage();
    }

    return image.format() == QImage::Format_RGB888
        ? image.copy()
        : image.convertToFormat(QImage::Format_RGB888);
}

std::string sha256_rgb888(const QImage &image)
{
    const QImage normalized = normalize_rgb888(image);
    if (normalized.isNull()) {
        return std::string();
    }

    QByteArray payload;
    payload.reserve(16 + (normalized.width() * normalized.height() * 3));
    payload.append("RGB888\n");
    payload.append(QString::number(normalized.width()).toUtf8());
    payload.append("x");
    payload.append(QString::number(normalized.height()).toUtf8());
    payload.append("\n");

    for (int y = 0; y < normalized.height(); ++y) {
        payload.append(reinterpret_cast<const char *>(normalized.constScanLine(y)),
                       normalized.width() * 3);
    }

    return sha256_bytes(payload.constData(), static_cast<std::size_t>(payload.size()));
}

bool images_match_rgb888(const QImage &expected,
                         const QImage &actual,
                         int tolerance,
                         QString *difference_message)
{
    const QImage expected_rgb = normalize_rgb888(expected);
    const QImage actual_rgb = normalize_rgb888(actual);

    if (expected_rgb.size() != actual_rgb.size()) {
        if (difference_message) {
            *difference_message = QStringLiteral("Size mismatch: expected %1x%2, actual %3x%4")
                .arg(expected_rgb.width())
                .arg(expected_rgb.height())
                .arg(actual_rgb.width())
                .arg(actual_rgb.height());
        }
        return false;
    }

    for (int y = 0; y < expected_rgb.height(); ++y) {
        const uint8_t *expected_line = expected_rgb.constScanLine(y);
        const uint8_t *actual_line = actual_rgb.constScanLine(y);
        for (int x = 0; x < expected_rgb.width(); ++x) {
            const int red_delta = qAbs(int(expected_line[0]) - int(actual_line[0]));
            const int green_delta = qAbs(int(expected_line[1]) - int(actual_line[1]));
            const int blue_delta = qAbs(int(expected_line[2]) - int(actual_line[2]));
            if (red_delta > tolerance || green_delta > tolerance || blue_delta > tolerance) {
                if (difference_message) {
                    *difference_message = QStringLiteral(
                        "Pixel mismatch at (%1,%2): expected [%3,%4,%5], actual [%6,%7,%8], tolerance=%9")
                        .arg(x)
                        .arg(y)
                        .arg(expected_line[0])
                        .arg(expected_line[1])
                        .arg(expected_line[2])
                        .arg(actual_line[0])
                        .arg(actual_line[1])
                        .arg(actual_line[2])
                        .arg(tolerance);
                }
                return false;
            }
            expected_line += 3;
            actual_line += 3;
        }
    }

    if (difference_message) {
        difference_message->clear();
    }
    return true;
}

} // namespace image_regression
