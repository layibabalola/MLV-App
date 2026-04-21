#ifndef TESTS_COMMON_IMAGE_REGRESSION_H
#define TESTS_COMMON_IMAGE_REGRESSION_H

#include <QImage>
#include <QString>

#include <string>

namespace image_regression {

QImage normalize_rgb888(const QImage &image);
std::string sha256_rgb888(const QImage &image);
bool images_match_rgb888(const QImage &expected,
                         const QImage &actual,
                         int tolerance,
                         QString *difference_message = nullptr);

} // namespace image_regression

#endif // TESTS_COMMON_IMAGE_REGRESSION_H
