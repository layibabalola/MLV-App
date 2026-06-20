/*!
 * \file ExportSettingsDialog.h
 * \author masc4ii
 * \copyright 2017
 * \brief Select codec
 */

#ifndef EXPORTSETTINGSDIALOG_H
#define EXPORTSETTINGSDIALOG_H

#include <QDialog>
#include <Scripting.h>
#include <QListWidgetItem>
#include "ExportCodecIds.h"

namespace Ui {
class ExportSettingsDialog;
}

class ExportSettingsDialog : public QDialog
{
    Q_OBJECT

public:
    explicit ExportSettingsDialog(QWidget *parent = 0,
                                  Scripting *scripting = 0,
                                  uint8_t currentCodecProfile = 0,
                                  uint8_t currentCodecOption = 0,
                                  uint8_t debayerMode = 1,
                                  bool resize = false,
                                  uint16_t resizeWidth = 1920,
                                  uint16_t resizeHeight = 1080,
                                  bool fpsOverride = false,
                                  double fps = 25,
                                  bool exportAudio = true,
                                  bool heightLocked = false,
                                  uint8_t smooth = 0,
                                  bool hdrBlending = false);
    ~ExportSettingsDialog();
    uint8_t encoderSetting(void);
    uint8_t encoderOption(void);
    uint8_t debayerMode(void);
    bool isResizeEnabled(void);
    uint16_t resizeWidth(void);
    uint16_t resizeHeight(void);
    bool isFpsOverride(void);
    double getFps(void);
    bool isExportAudioEnabled(void);
    bool isHeightLocked(void);
    uint8_t smoothSetting(void);
    bool hdrBlending(void);

private slots:
    void on_pushButtonClose_clicked();
    void on_comboBoxCodec_currentIndexChanged(int index);
    void on_checkBoxFpsOverride_toggled(bool checked);
    void on_checkBoxResize_toggled(bool checked);
    void on_comboBoxOption_currentTextChanged(const QString &arg1);
    void on_toolButtonLockHeight_toggled(bool checked);
    void on_comboBoxPostExportScript_currentTextChanged(const QString &arg1);
    void on_checkBoxHdrBlending_toggled(bool checked);
    void on_comboBoxSmoothing_currentIndexChanged(int index);

    void on_toolButtonAddPreset_clicked();
    void on_toolButtonDeletePreset_clicked();
    void on_listWidget_itemChanged(QListWidgetItem *item);
    void on_listWidget_itemClicked(QListWidgetItem *item);

    void on_listWidget_currentItemChanged(QListWidgetItem *current, QListWidgetItem *previous);

private:
    Ui::ExportSettingsDialog *ui;
    Scripting *m_pScripting;
    bool m_blockOnce;
};

#endif // EXPORTSETTINGSDIALOG_H
