#ifndef MLV_APP_MLV_PIPELINE_FIXTURE_H
#define MLV_APP_MLV_PIPELINE_FIXTURE_H

#include "../../platform/qt/ReceiptSettings.h"
#include "../../src/mlv_include.h"

#include <QString>
#include <vector>

class MlvPipelineFixture
{
public:
    MlvPipelineFixture();
    ~MlvPipelineFixture();

    bool openTinyDualIso(QString * error_message);
    bool openClipFile(const QString & clip_path, QString * error_message);
    bool loadReceipt(const QString & relative_path, QString * error_message);
    bool applyReceipt(QString * error_message);

    std::vector<uint16_t> renderFrame16(uint64_t frame_index, int threads = 1) const;
    std::vector<uint8_t> renderFrame8(uint64_t frame_index, int threads = 1) const;

    int width() const;
    int height() const;

    mlvObject_t * video() const { return m_video; }
    processingObject_t * processing() const { return m_processing; }
    ReceiptSettings & receipt() { return m_receipt; }

private:
    bool openClip(const QString & clip_path, QString * error_message);
    void applyDebayerSelection();
    void resetSingleThreadedRuntime();

    mlvObject_t * m_video;
    processingObject_t * m_processing;
    ReceiptSettings m_receipt;
};

#endif
