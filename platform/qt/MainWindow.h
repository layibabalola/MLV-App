/*!
 * \file MainWindow.h
 * \author masc4ii
 * \copyright 2017
 * \brief The main window
 */

#ifndef MAINWINDOW_H
#define MAINWINDOW_H

#include <QMainWindow>
#include <QFileDialog>
#include <QDebug>
#include <QTimerEvent>
#include <QResizeEvent>
#include <QFileOpenEvent>
#include <QThreadPool>
#include <QProcess>
#include <QVector>
#include <QImage>
#include <QPixmap>
#include <QGraphicsPixmapItem>
#include <QCloseEvent>
#include <QXmlStreamWriter>
#include <QActionGroup>
#include <QSortFilterProxyModel>
#include <QItemSelectionModel>
#include "SessionModel.h"
#include "../../src/mlv_include.h"
#include "InfoDialog.h"
#include "StatusDialog.h"
#include "AudioWave.h"
#include "ReceiptSettings.h"
#include "AudioPlayback.h"
#include "GraphicsPickerScene.h"
#include "MainWindowGpuPreviewPolicy.h"
#include "GpuPreviewProcessing.h"
#include "RenderFrameThread.h"
#include "GradientElement.h"
#include "CrossElement.h"
#include "TimeCodeLabel.h"
#include "DoubleClickLabel.h"
#include "Scripting.h"
#include "ReceiptCopyMaskDialog.h"
#include "QRecentFilesMenu.h"
#include "PlaybackQualityPolicy.h"
#include "batch/BatchTypes.h"
#include <atomic>
#include <deque>
#include <functional>
#include <condition_variable>
#include <mutex>
#include <thread>

namespace Ui {
class MainWindow;
}

class MainWindow : public QMainWindow
{
    Q_OBJECT

public:
    explicit MainWindow(int &argc, char **argv, QWidget *parent = 0);
    ~MainWindow();

    enum class PlaybackProfileScope
    {
        None = 0,
        Histogram,
        Waveform,
        Parade,
        Vectorscope
    };

    enum class PlaybackProfileDebayerRequest
    {
        Auto = 0,
        Receipt,
        None,
        Simple,
        Bilinear,
        LMMSE,
        IGV,
        AMaZE,
        AHD,
        RCD,
        DCB,
        AmazeCached
    };

    enum class PlaybackProfileProcessingRequest
    {
        Auto = 0,
        Receipt,
        Subset
    };

    struct PlaybackProfileOptions
    {
        QString inputPath;
        QString receiptPath;
        QString outputPath;
        int startFrame = 0;
        int frameCount = 0;
        int workerThreads = 1;
        bool forceWorkerThreads = true;
        uint64_t rawCacheMB = 0;
        int cacheCpuCores = 1;
        bool zebras = false;
        bool fastOpen = false;
        bool showWindow = false;
        bool waitForPaint = false;
        PlaybackProfileScope scope = PlaybackProfileScope::None;
        PlaybackProfileDebayerRequest playbackDebayer =
            PlaybackProfileDebayerRequest::Auto;
        PlaybackProfileProcessingRequest playbackProcessing =
            PlaybackProfileProcessingRequest::Auto;
        GpuPreviewProcessingBackendRequest gpuPreviewProcessingBackend =
            GpuPreviewProcessingBackendRequest::Auto;
        GpuBilinearDebayerBackendRequest gpuBilinearDebayerBackend =
            GpuBilinearDebayerBackendRequest::Auto;
    };

    /* Progress-only callback for exportCdngSequence.
     * framesDone:   frames completed so far (exported + skipped)
     * totalFrames:  total frames to export
     * Return true to continue, false to abort (e.g. user pressed abort). */
    using ProgressCallback = std::function<bool(int framesDone, int totalFrames)>;

    /* Static CDNG export helper — callable from both GUI and batch mode.
     * Error decisions go through BatchPrompts directly.
     * ProgressCallback is for progress updates and abort-polling only. */
    static ProcessResult exportCdngSequence(
        mlvObject_t *mlvObject,
        const QString &outDir,
        const QString &clipBaseName,
        int codecProfile,
        int codecOption,
        uint32_t cutIn,
        uint32_t cutOut,
        double stretchX,
        double stretchY,
        bool audioExport,
        bool rawFixEnabled,
        ProgressCallback progressCallback = nullptr);

    int runHeadlessPlaybackProfile(const PlaybackProfileOptions & options);

protected:
    void timerEvent( QTimerEvent *t );
    void resizeEvent( QResizeEvent *event );
    bool event( QEvent *event );
    void dragEnterEvent( QDragEnterEvent *event );
    void dropEvent( QDropEvent *event );
    void closeEvent( QCloseEvent *event );
    bool eventFilter(QObject *watched, QEvent *event);

signals:
    void frameReady( void );

private slots:
    void openMlvSet( QStringList list );
    void timerFrameEvent( void );
    void on_actionOpen_triggered();
    void on_actionTranscodeAndImport_triggered();
    void on_actionFcpxmlImportAssistant_triggered();
    void on_actionFcpxmlSelectionAssistant_triggered();
    void on_actionAbout_triggered();
    void on_actionAboutQt_triggered();
    void on_horizontalSliderPosition_valueChanged(int position);
    void on_actionClip_Information_triggered();
    void on_horizontalSliderGamma_valueChanged(int position);
    void on_horizontalSliderExposure_valueChanged(int position);
    void on_horizontalSliderExposureGradient_valueChanged(int position);
    void on_horizontalSliderContrast_valueChanged(int position);
    void on_horizontalSliderPivot_valueChanged(int position);
    void on_horizontalSliderContrastGradient_valueChanged(int position);
    void on_horizontalSliderTemperature_valueChanged(int position);
    void on_horizontalSliderTint_valueChanged(int position);
    void on_horizontalSliderClarity_valueChanged(int position);
    void on_horizontalSliderVibrance_valueChanged(int position);
    void on_horizontalSliderSaturation_valueChanged(int position);
    void on_horizontalSliderDS_valueChanged(int position);
    void on_horizontalSliderDR_valueChanged(int position);
    void on_horizontalSliderLS_valueChanged(int position);
    void on_horizontalSliderLR_valueChanged(int position);
    void on_horizontalSliderLighten_valueChanged(int position);
    void on_horizontalSliderShadows_valueChanged(int position);
    void on_horizontalSliderHighlights_valueChanged(int position);
    void on_horizontalSliderSharpen_valueChanged(int position);
    void on_horizontalSliderShMasking_valueChanged(int position);
    void on_horizontalSliderChromaBlur_valueChanged(int position);
    void on_horizontalSliderDenoiseStrength_valueChanged(int position);
    void on_horizontalSliderRbfDenoiseLuma_valueChanged(int position);
    void on_horizontalSliderRbfDenoiseChroma_valueChanged(int position);
    void on_horizontalSliderRbfDenoiseRange_valueChanged(int position);
    void on_horizontalSliderGrainStrength_valueChanged(int position);
    void on_horizontalSliderGrainLumaWeight_valueChanged(int position);
    void on_horizontalSliderLutStrength_valueChanged(int position);
    void on_horizontalSliderFilterStrength_valueChanged(int position);
    void on_horizontalSliderVignetteStrength_valueChanged(int position);
    void on_horizontalSliderVignetteRadius_valueChanged(int position);
    void on_horizontalSliderVignetteShape_valueChanged(int position);
    void on_horizontalSliderCaRed_valueChanged(int position);
    void on_horizontalSliderCaBlue_valueChanged(int position);
    void on_horizontalSliderCaDesaturate_valueChanged(int position);
    void on_horizontalSliderCaRadius_valueChanged(int position);
    void on_horizontalSliderRawWhite_valueChanged(int position);
    void on_horizontalSliderRawBlack_valueChanged(int position);
    void on_horizontalSliderDualIsoEvCorrection_valueChanged(int position);
    void on_horizontalSliderDualIsoBlackDelta_valueChanged(int position);
    void on_horizontalSliderTone_valueChanged(int position);
    void on_horizontalSliderToningStrength_valueChanged(int position);
    void on_horizontalSliderVidstabStepsize_valueChanged(int position);
    void on_horizontalSliderVidstabShakiness_valueChanged(int position);
    void on_horizontalSliderVidstabAccuracy_valueChanged(int position);
    void on_horizontalSliderVidstabZoom_valueChanged(int position);
    void on_horizontalSliderVidstabSmoothing_valueChanged(int position);

    void on_horizontalSliderExposure_doubleClicked();
    void on_horizontalSliderExposureGradient_doubleClicked();
    void on_horizontalSliderContrast_doubleClicked();
    void on_horizontalSliderPivot_doubleClicked();
    void on_horizontalSliderContrastGradient_doubleClicked();
    void on_horizontalSliderTemperature_doubleClicked();
    void on_horizontalSliderTint_doubleClicked();
    void on_horizontalSliderClarity_doubleClicked();
    void on_horizontalSliderVibrance_doubleClicked();
    void on_horizontalSliderSaturation_doubleClicked();
    void on_horizontalSliderDS_doubleClicked();
    void on_horizontalSliderDR_doubleClicked();
    void on_horizontalSliderLS_doubleClicked();
    void on_horizontalSliderLR_doubleClicked();
    void on_horizontalSliderLighten_doubleClicked();
    void on_horizontalSliderShadows_doubleClicked();
    void on_horizontalSliderHighlights_doubleClicked();
    void on_horizontalSliderSharpen_doubleClicked();
    void on_horizontalSliderShMasking_doubleClicked();
    void on_horizontalSliderChromaBlur_doubleClicked();
    void on_horizontalSliderDenoiseStrength_doubleClicked();
    void on_horizontalSliderRbfDenoiseLuma_doubleClicked();
    void on_horizontalSliderRbfDenoiseChroma_doubleClicked();
    void on_horizontalSliderRbfDenoiseRange_doubleClicked();
    void on_horizontalSliderGrainStrength_doubleClicked();
    void on_horizontalSliderGrainLumaWeight_doubleClicked();
    void on_horizontalSliderLutStrength_doubleClicked();
    void on_horizontalSliderFilterStrength_doubleClicked();
    void on_horizontalSliderVignetteStrength_doubleClicked();
    void on_horizontalSliderVignetteRadius_doubleClicked();
    void on_horizontalSliderVignetteShape_doubleClicked();
    void on_horizontalSliderCaRed_doubleClicked();
    void on_horizontalSliderCaBlue_doubleClicked();
    void on_horizontalSliderCaDesaturate_doubleClicked();
    void on_horizontalSliderCaRadius_doubleClicked();
    void on_horizontalSliderRawWhite_doubleClicked();
    void on_horizontalSliderRawBlack_doubleClicked();
    void on_horizontalSliderDualIsoEvCorrection_doubleClicked();
    void on_horizontalSliderDualIsoBlackDelta_doubleClicked();
    void on_horizontalSliderTone_doubleClicked();
    void on_horizontalSliderToningStrength_doubleClicked();
    void on_horizontalSliderVidstabStepsize_doubleClicked();
    void on_horizontalSliderVidstabShakiness_doubleClicked();
    void on_horizontalSliderVidstabAccuracy_doubleClicked();
    void on_horizontalSliderVidstabZoom_doubleClicked();
    void on_horizontalSliderVidstabSmoothing_doubleClicked();

    void on_actionGoto_First_Frame_triggered();
    void on_actionExport_triggered();
    void on_actionExportCurrentFrame_triggered();
    void on_checkBoxHighLightReconstruction_toggled(bool checked);
    void on_comboBoxUseCameraMatrix_currentIndexChanged(int index);
    void on_checkBoxCreativeAdjustments_toggled(bool checked);
    void on_checkBoxExrMode_toggled(bool checked);
    void on_checkBoxAgX_toggled(bool checked);
    void on_checkBoxChromaSeparation_toggled(bool checked);
    void on_comboBoxProfile_currentIndexChanged(int index);
    void on_comboBoxProfile_activated(int index);
    void on_comboBoxTonemapFct_currentIndexChanged(int index);
    void on_comboBoxProcessingGamut_currentIndexChanged(int index);
    void on_comboBoxFilterName_currentIndexChanged(int index);
    void on_comboBoxDenoiseWindow_currentIndexChanged(int index);
    void on_actionZoomFit_triggered(bool on);
    void on_actionZoom100_triggered();
    void on_actionShowHistogram_triggered(void);
    void on_actionShowWaveFormMonitor_triggered(void);
    void on_actionShowParade_triggered(void);
    void on_actionShowVectorScope_triggered(void);
    void on_actionUseNoneDebayer_triggered();
    void on_actionUseSimpleDebayer_triggered();
    void on_actionUseBilinear_triggered();
    void on_actionUseLmmseDebayer_triggered();
    void on_actionUseIgvDebayer_triggered();
    void on_actionUseAhdDebayer_triggered();
    void on_actionUseRcdDebayer_triggered();
    void on_actionUseDcbDebayer_triggered();
    void on_actionAlwaysUseAMaZE_triggered();
    void on_actionCaching_triggered();
    void on_actionDontSwitchDebayerForPlayback_triggered();
    void on_actionUseFastProcessingForPlayback_triggered();
    /* Phase 4E: Playback Quality dial (Fast / HighQuality / Auto). */
    void on_actionPlaybackQualityFast_triggered();
    void on_actionPlaybackQualityHQ_triggered();
    void on_actionPlaybackQualityAuto_triggered();
    void on_actionPlaybackShowQualityIndicator_triggered();
    void on_actionPlaybackAutoTarget24_triggered();
    void on_actionPlaybackAutoTarget30_triggered();
    void on_actionPlaybackAutoTarget60_triggered();
    void cyclePlaybackQualityMode();
    void on_actionExportSettings_triggered();
    void on_actionResetReceipt_triggered();
    void on_actionCopyRecept_triggered();
    void on_actionPasteReceipt_triggered();
    void on_actionNewSession_triggered();
    void on_actionOpenSession_triggered();
    void on_actionSaveSession_triggered();
    void on_actionSaveAsSession_triggered();
    void on_actionSaveSessionMetadata_triggered();
    void on_actionImportReceipt_triggered();
    void on_actionExportReceipt_triggered();
    void on_actionUseDefaultReceipt_triggered(bool checked);
    void on_actionNext_Clip_triggered();
    void on_actionPrevious_Clip_triggered();
    void on_actionSelectAllClips_triggered();
    void on_actionDeleteSelectedClips_triggered();
    void on_actionHelp_triggered();
    void on_actionCreateAllMappFilesNow_triggered();
    void on_actionBetterResizer_triggered();
    void on_actionShowInstalledFocusPixelMaps_triggered();
    void on_actionShowInstalledBadPixelMaps_triggered();
    void on_actionViewerBackgroundColor_triggered();
    void on_listViewSession_activated(const QModelIndex &index);
    void on_tableViewSession_activated(const QModelIndex &index);
    void on_dockWidgetSession_visibilityChanged(bool visible);
    void on_dockWidgetEdit_visibilityChanged(bool visible);
    void on_actionShowAudioTrack_toggled(bool checked);
    void on_listViewSession_customContextMenuRequested(const QPoint &pos);
    void on_tableViewSession_customContextMenuRequested(const QPoint &pos);
    void deleteFileFromSession( void );
    void renameActiveClip( void );
    void on_actionShowInFinder_triggered( void );
    void on_actionOpenWithExternalApplication_triggered( void );
    void rightClickShowFile( void );
    void selectAllFiles( void );
    void pictureCustomContextMenuRequested(const QPoint &pos);
    void on_labelScope_customContextMenuRequested(const QPoint &pos);
    void on_label_GammaVal_doubleClicked( void );
    void on_label_ExposureVal_doubleClicked( void );
    void on_label_ExposureGradient_doubleClicked( void );
    void on_label_ContrastVal_doubleClicked( void );
    void on_label_PivotVal_doubleClicked( void );
    void on_label_ContrastGradientVal_doubleClicked( void );
    void on_label_TemperatureVal_doubleClicked( void );
    void on_label_TintVal_doubleClicked( void );
    void on_label_ClarityVal_doubleClicked( void );
    void on_label_VibranceVal_doubleClicked( void );
    void on_label_SaturationVal_doubleClicked( void );
    void on_label_DrVal_doubleClicked( void );
    void on_label_DsVal_doubleClicked( void );
    void on_label_LrVal_doubleClicked( void );
    void on_label_LsVal_doubleClicked( void );
    void on_label_LightenVal_doubleClicked( void );
    void on_label_ShadowsVal_doubleClicked( void );
    void on_label_HighlightsVal_doubleClicked( void );
    void on_label_Sharpen_doubleClicked( void );
    void on_label_ShMasking_doubleClicked( void );
    void on_label_ChromaBlur_doubleClicked( void );
    void on_label_DenoiseStrength_doubleClicked( void );
    void on_label_RbfDenoiseLuma_doubleClicked( void );
    void on_label_RbfDenoiseChroma_doubleClicked( void );
    void on_label_RbfDenoiseRange_doubleClicked( void );
    void on_label_GrainStrength_doubleClicked( void );
    void on_label_GrainLumaWeight_doubleClicked( void );
    void on_labelAudioTrack_sizeChanged( void );
    void on_label_LutStrengthVal_doubleClicked( void );
    void on_label_FilterStrengthVal_doubleClicked( void );
    void on_label_VignetteStrengthVal_doubleClicked( void );
    void on_label_VignetteRadiusVal_doubleClicked( void );
    void on_label_VignetteShapeVal_doubleClicked( void );
    void on_label_CaRedVal_doubleClicked( void );
    void on_label_CaBlueVal_doubleClicked( void );
    void on_label_CaDesaturateVal_doubleClicked( void );
    void on_label_CaRadiusVal_doubleClicked( void );
    void on_label_RawWhiteVal_doubleClicked( void );
    void on_label_RawBlackVal_doubleClicked( void );
    void on_DualIsoEvCorrectionVal_doubleClicked( void );
    void on_DualIsoBlackDeltaVal_doubleClicked( void );
    void on_label_ToneVal_doubleClicked( void );
    void on_label_ToningStrengthVal_doubleClicked( void );
    void on_label_VidstabStepsizeVal_doubleClicked( void );
    void on_label_VidstabShakinessVal_doubleClicked( void );
    void on_label_VidstabAccuracyVal_doubleClicked( void );
    void on_label_VidstabZoomVal_doubleClicked( void );
    void on_label_VidstabSmoothingVal_doubleClicked( void );
    void on_actionFullscreen_triggered(bool checked);
    void exportHandler( void );
    void on_actionPlay_triggered(bool checked);
    void on_actionPlay_toggled(bool checked);
    void on_actionShowZebras_triggered();
    void toolButtonFocusPixelsChanged( void );
    void toolButtonFocusPixelsIntMethodChanged( void );
    void toolButtonBadPixelsChanged( void );
    void toolButtonBadPixelsSearchMethodChanged( void );
    void toolButtonBadPixelsIntMethodChanged( void );
    void toolButtonChromaSmoothChanged( void );
    void toolButtonPatternNoiseChanged( void );
    void toolButtonUpsideDownChanged( void );
    void toolButtonVerticalStripesChanged( void );
    void on_spinBoxDeflickerTarget_valueChanged(int arg1);
    void toolButtonDualIsoChanged( void );
    void on_DualIsoPatternComboBox_currentIndexChanged(int index);
    void on_toolButtonDualIsoMatchExposures1_clicked();
    void on_toolButtonDualIsoMatchExposures2_clicked();
    void toolButtonDualIsoInterpolationChanged( void );
    void toolButtonDualIsoAliasMapChanged( void );
    void toolButtonDualIsoFullresBlendingChanged( void );
    void toolButtonDarkFrameSubtractionChanged( bool checked );
    void toolButtonGCurvesChanged( void );
    void on_toolButtonGCurvesReset_clicked();
    void on_toolButtonGCurvesResetOne_clicked();
    void on_toolButtonHueVsHueReset_clicked();
    void on_toolButtonHueVsHueResetDefaultPoints_clicked();
    void on_toolButtonHueVsSatReset_clicked();
    void on_toolButtonHueVsSatResetDefaultPoints_clicked();
    void on_toolButtonHueVsLumaReset_clicked();
    void on_toolButtonHueVsLumaResetDefaultPoints_clicked();
    void on_toolButtonLumaVsSatReset_clicked();
    void on_actionNextFrame_triggered();
    void on_actionPreviousFrame_triggered();
    void on_checkBoxRawFixEnable_clicked(bool checked);
    void on_checkBoxLutEnable_clicked(bool checked);
    void on_checkBoxFilterEnable_clicked(bool checked);
    void on_checkBoxVidstabEnable_toggled(bool checked);
    void on_checkBoxVidstabTripod_toggled(bool checked);
    void on_toolButtonDeleteBpm_clicked( void );
    void on_toolButtonBadPixelsSearchMethodEdit_toggled(bool checked);
    void on_toolButtonBadPixelsCrosshairEnable_toggled(bool checked);
    void badPixelPicked( int x, int y );
    void on_actionWhiteBalancePicker_toggled(bool checked);
    void whiteBalancePicked( int x, int y );
    void on_toolButtonWbMode_clicked();
    void gradientAnchorPicked( int x, int y );
    void gradientFinalPosPicked(int x, int y , bool isFinished);
    void on_groupBoxRawCorrection_toggled(bool arg1);
    void on_groupBoxCutInOut_toggled(bool arg1);
    void on_groupBoxDebayer_toggled(bool arg1);
    void on_groupBoxProfiles_toggled(bool arg1);
    void on_groupBoxProcessing_toggled(bool arg1);
    void on_groupBoxDetails_toggled(bool arg1);
    void on_groupBoxHsl_toggled(bool arg1);
    void on_groupBoxToning_toggled(bool arg1);
    void on_groupBoxColorWheels_toggled(bool arg1);
    void on_groupBoxLut_toggled(bool arg1);
    void on_groupBoxFilter_toggled(bool arg1);
    void on_groupBoxVignette_toggled(bool arg1);
    void on_groupBoxLinearGradient_toggled(bool arg1);
    void on_groupBoxTransformation_toggled(bool arg1);
    void exportAbort( void );
    void drawFrameReady( void );

    void on_toolButtonGradientPaint_toggled(bool checked);
    void on_checkBoxGradientEnable_toggled(bool checked);
    void on_spinBoxGradientX_valueChanged(int arg1);
    void on_spinBoxGradientY_valueChanged(int arg1);
    void on_spinBoxGradientLength_valueChanged(int arg1);
    void on_labelGradientAngle_doubleClicked( void );
    void on_dialGradientAngle_valueChanged(int value);
    void gradientGraphicElementMoved( int x, int y );
    void gradientGraphicElementHovered( bool isHovered );

    void on_toolButtonCutIn_clicked(void);
    void on_toolButtonCutOut_clicked(void);
    void on_toolButtonCutInDelete_clicked(void);
    void on_toolButtonCutOutDelete_clicked(void);
    void on_spinBoxCutIn_valueChanged(int arg1);
    void on_spinBoxCutOut_valueChanged(int arg1);

    void on_actionPreviewDisabled_triggered();
    void on_actionPreviewList_triggered();
    void on_actionPreviewPicture_triggered();
    void on_actionPreviewPictureBottom_triggered();
    void on_actionPreviewTableModeBottom_triggered();

    void on_comboBoxHStretch_currentIndexChanged(int index);
    void on_comboBoxVStretch_currentIndexChanged(int index);

    void mpTcLabel_customContextMenuRequested(const QPoint &pos);
    void on_actionTimecodePositionMiddle_triggered();
    void on_actionTimecodePositionRight_triggered();
    void tcLabelDoubleClicked();
    void on_actionToggleTimecodeDisplay_triggered();

    void on_toolButtonDarkFrameSubtractionFile_clicked();
    void on_lineEditDarkFrameFile_textChanged(const QString &arg1);

    void on_actionCheckForUpdates_triggered(void);
    void updateCheck(void);

    void on_toolButtonLoadLut_clicked();
    void on_toolButtonNextLut_clicked();
    void on_toolButtonPrevLut_clicked();
    void on_lineEditLutName_textChanged(const QString &arg1);

    void on_toolButtonRawBlackAutoCorrect_clicked();

    void on_actionSelectExternalApplication_triggered();
    void openRecentSession( QString fileName );

    void on_actionDarkThemeStandard_triggered(bool checked);
    void on_actionDarkThemeModern_triggered(bool checked);

    void on_comboBoxDebayer_currentIndexChanged( int index );

    void on_actionMarkRed_triggered();
    void on_actionMarkYellow_triggered();
    void on_actionMarkGreen_triggered();
    void on_actionUnmark_triggered();

    void on_actionShowRedClips_toggled(bool arg1);
    void on_actionShowYellowClips_toggled(bool arg1);
    void on_actionShowGreenClips_toggled(bool arg1);
    void on_actionShowUnmarkedClips_toggled(bool arg1);

    void on_lineEditTransferFunction_textChanged(const QString &arg1);
    void onPlaybackPrepResultReady( void );

private:
    struct DisplayPreviewCacheEntry
    {
        bool valid = false;
        bool zoomFit = false;
        bool betterResizer = false;
        bool zebras = false;
        bool gpuScaling = false;
        uint64_t frameIndex = 0;
        uint64_t signature = 0;
        int sourceWidth = 0;
        int sourceHeight = 0;
        int sceneWidth = 0;
        int sceneHeight = 0;
        int imageWidth = 0;
        int imageHeight = 0;
        int transformationMode = 0;
        int devicePixelRatioMilli = 0;
        uint8_t underOver = 0;
        QImage image;
        QPixmap pixmap;
    };

    using PresentationRequestContext = RenderFrameThread::ReadyFrame::PresentationContext;

    // Immutable inputs to the playback-prep worker. Non-owning views
    // (`scopeSourceImage` / `sourceImage`) point into this task for the
    // background copy path when needed, and fall back to ready-slot data
    // otherwise.
    struct PlaybackPrepTask
    {
        RenderFrameThread::ReadyFrame readyFrame;
        PresentationRequestContext requestContext;
        uint64_t requestSerial = 0;
        double displayStart = 0.0;
        uint64_t displayFrame = 0;
        double stretchX = 1.0;
        double stretchY = 1.0;
        const uint8_t *scopeSourceImage = nullptr;
        size_t scopeSourceImageSize = 0;
        const uint8_t *sourceImage = nullptr;
        size_t sourceImageSize = 0;
        int sourceWidth = 0;
        int sourceHeight = 0;
        int sceneWidth = 0;
        int sceneHeight = 0;
        int transformationMode = 0;
        int devicePixelRatioMilli = 0;
        const uint16_t *sourceImage16 = nullptr;
        size_t sourceImage16Size = 0;
        bool gpu16PreviewActive = false;
        bool gpuPreviewProcessingActive = false;
        bool cpuPreviewProcessingActive = false;
        bool useGpuImagePresentation = false;
        bool useGpuShaderZebras = false;
        bool zoomFitEnabled = false;
        bool zebrasEnabled = false;
        bool betterResizerEnabled = false;
        bool displayPreviewCachingAllowed = false;
        bool playbackFastScaleActive = false;
        GpuDisplayViewport::PresentationOptions gpuPresentationOptions;
    };

    // Worker output. Composes the originating Task so the presenter has the
    // full input context without field duplication.
    struct PlaybackPrepResult
    {
        PlaybackPrepTask task;
        int preparedWidth = 0;
        int preparedHeight = 0;
        // Bytes per scanline in preparedImage. Always rounded up to a
        // multiple of 4 so the buffer is safe to wrap as a Format_RGB888
        // QImage on the GUI thread (Qt requires 32-bit-aligned scanlines;
        // an unpadded width*3 stride for odd widths overshoots the buffer
        // by up to one row inside qt_convert_rgb888_to_rgb32_ssse3 and
        // segfaults — see the 2026-04-24 crash investigation).
        int preparedBytesPerLine = 0;
        uint8_t underOver = 0;
        double imageBuildMs = 0.0;
        std::vector<uint8_t> preparedImage;
        std::vector<uint8_t> scopeSourceImage;
    };

    Ui::MainWindow *ui;
    InfoDialog *m_pInfoDialog;
    StatusDialog *m_pStatusDialog;
    AudioWave *m_pAudioWave;
    AudioPlayback *m_pAudioPlayback;
    RenderFrameThread *m_pRenderThread;
    mlvObject_t *m_pMlvObject;
    processingObject_t *m_pProcessingObject;
    QGraphicsPixmapItem *m_pGraphicsItem;
    GradientElement *m_pGradientElement;
    QVector<CrossElement*> m_pBadPixelCrosses;
    GraphicsPickerScene* m_pScene;
    TimeCodeLabel* m_pTimeCodeImage;
    ReceiptCopyMaskDialog *m_pCopyMask;
    Scripting* m_pScripting;
    uint8_t m_timeCodePosition;
    QLabel *m_pCachingStatus;
    QLabel *m_pFpsStatus;
    QLabel *m_pFrameNumber;
    QLabel *m_pChosenDebayer;
    QLabel *m_pPlaybackQualityIndicator = nullptr; // Phase 4E
    QActionGroup *m_darkFrameGroup;
    QActionGroup *m_previewDebayerGroup;
    QActionGroup *m_sessionListGroup;
    QActionGroup *m_playbackElementGroup;
    QActionGroup *m_scopeGroup;
    QActionGroup *m_playbackQualityGroup = nullptr;       // Phase 4E
    QActionGroup *m_playbackAutoTargetFpsGroup = nullptr; // Phase 4E
    DoubleClickLabel *m_pTcLabel;
    bool m_tcModeDuration;
    uint8_t *m_pRawImage;
    uint16_t *m_pRawImage16;
    uint32_t m_cacheSizeMB;
    uint8_t m_codecProfile;
    uint8_t m_codecOption;
    uint8_t m_exportDebayerMode;
    uint8_t m_previewMode;
    uint8_t m_wbMode;
    bool m_frameChanged;
    int m_currentFrameIndex;
    double m_newPosDropMode;
    bool m_dontDraw;
    bool m_frameStillDrawing;
    bool m_fileLoaded;
    bool m_inOpeningProcess;
    bool m_setSliders;
    int m_timerId;
    int m_timerCacheId;
    int8_t m_countTimeDown;
    bool m_resizeFilterEnabled;
    bool m_resizeFilterHeightLocked;
    uint8_t m_smoothFilterSetting;
    uint16_t m_resizeWidth;
    uint16_t m_resizeHeight;
    bool m_fpsOverride;
    double m_frameRate;
    bool m_tryToSyncAudio;
    bool m_audioExportEnabled;
    bool m_hdrExport;
    bool m_exportAbortPressed;
    bool m_zoomTo100Center;
    bool m_zoomModeChanged;
    bool m_playbackStopped;
    bool m_dualIsoPlaybackPreviewActive;
    /* Phase 4E: Playback Quality state. m_playbackQualityMode reflects the
     * persisted user choice; m_playbackQualityActiveScale and
     * m_playbackQualityActiveHq reflect the *effective* state for the next
     * frame (equal to the user choice for Fast/HighQuality, dynamically
     * decided by the auto sampler for Auto). */
    int m_playbackQualityMode = 0;
    int m_playbackAutoTargetFps = 30;
    int m_playbackQualityActiveScale = 1;
    bool m_playbackQualityActiveHq = false;
    bool m_playbackQualityIndicatorVisible = true;
    uint64_t m_playbackQualityFrameCounter = 0;
    PlaybackQualityAutoSampler m_playbackQualitySampler;
    bool m_playbackFrameAdvancePending = false;
    bool m_skipImmediateTimecodeLabel = false;
    bool m_playToFirstFramePending = false;
    bool m_playToFirstFrameTargetFrameValid = false;
    bool m_lastPlayToFirstFrameValid = false;
    bool m_lastPlayStartPrerollRequested = false;
    bool m_inClipDeleteProcess;
    bool m_renderThreadUsing16BitPreview;
    bool m_renderThreadUsingGpuPreviewProcessing;
    bool m_renderThreadUsingGpuBilinearDebayer;
    bool m_renderThreadUsingCpuPreviewProcessing = false;
    int m_playToFirstFrameTargetFrame = -1;
    double m_playToFirstFrameStartSeconds = 0.0;
    double m_lastPlayToFirstFrameMs = 0.0;
    double m_lastDrawFrameReadyQueueMs = 0.0;
    double m_lastDrawFrameReadySceneMs = 0.0;
    double m_lastDrawFrameReadyImageMs = 0.0;
    double m_lastDrawFrameReadyPresentMs = 0.0;
    double m_lastDrawFrameReadyScopesMs = 0.0;
    double m_lastDrawFrameReadyOverlayMs = 0.0;
    double m_lastDrawFrameReadyTotalMs = 0.0;
    bool m_headlessPlaybackProfileUsePlaybackPolicy = false;
    uint64_t m_nextRenderRequestSerial = 1;
    uint64_t m_lastPresentedRequestSerial = 0;
    GpuPreviewProcessingBackendRequest m_gpuPreviewProcessingBackendRequest =
        GpuPreviewProcessingBackendRequest::Auto;
    GpuBilinearDebayerBackendRequest m_gpuBilinearDebayerBackendRequest =
        GpuBilinearDebayerBackendRequest::Auto;
    MainWindowGpuPreviewPolicyState m_lastQueuedGpuPreviewPolicy;
    GpuDisplayViewport::PresentationOptions m_lastQueuedGpuPresentationOptions;
    GpuPreviewProcessingConfig m_lastQueuedGpuPreviewProcessingConfig;
    QString m_lastQueuedPlaybackProcessingReason;
    std::deque<PresentationRequestContext> m_pendingPresentationRequests;
    PresentationRequestContext m_lastPresentedRequestContext;
    bool m_lastPresentedRequestContextValid = false;

    // Playback-prep worker: background image preparation with request-conflated
    // delivery to the UI thread.
    // - `m_latestRequestedSerial` is the staleness baseline: any result whose
    //   `task.requestSerial` != the latest requested is dropped at the
    //   presenter. Accessed from UI and worker threads.
    // - Conflation queue semantics: at most one in-flight task ("current") and
    //   at most one queued task ("pending"). New enqueues replace pending.
    std::atomic<uint64_t> m_latestRequestedSerial{0};
    std::thread m_playbackPrepThread;
    std::mutex m_playbackPrepMutex;
    std::condition_variable m_playbackPrepCv;
    std::atomic<bool> m_playbackPrepStop{false};
    bool m_playbackPrepPendingValid = false;
    PlaybackPrepTask m_playbackPrepPending;
    std::deque<PlaybackPrepResult> m_playbackPrepResults;
    std::atomic<uint64_t> m_playbackPrepStaleDropCount{0};
    std::atomic<uint64_t> m_playbackPrepReplacedBeforeComputeCount{0};
    std::atomic<uint64_t> m_playbackPrepReplacedAfterComputeCount{0};
    bool m_lastPresentedFrameUsedGpuBilinearDebayer = false;
    QString m_lastPresentedGpuBilinearFallbackReason;
    QString m_lastPresentedGpuBilinearRendererDescription;
    double m_lastPresentedDualIsoPreviewHistogramMs = 0.0;
    double m_lastPresentedDualIsoPreviewRegressionMs = 0.0;
    double m_lastPresentedDualIsoPreviewRowscaleMs = 0.0;
    QJsonObject m_lastPresentedStageTimingTelemetry;
    uint32_t m_displayPreviewCacheNextSlot;
    int m_lastDisplaySceneWidth = -1;
    int m_lastDisplaySceneHeight = -1;
    DisplayPreviewCacheEntry m_displayPreviewCache[8];
    QString m_lastExportPath;
    QString m_lastSessionFileName;
    QString m_lastMlvOpenFileName;
    QString m_lastReceiptFileName;
    QString m_lastDarkframeFileName;
    QString m_lastLutFileName;
    QString m_externalApplicationName;
    QString m_sessionFileName;
    QString m_defaultReceiptFileName;
    ReceiptSettings *m_pReceiptClipboard;
    QVector<ReceiptSettings*> m_exportQueue;
    SessionModel* m_pModel;
    QSortFilterProxyModel* m_pProxyModel;
    QItemSelectionModel* m_pSelectionModel;
    int m_lastClipBeforeExport;
    void drawFrame( bool updateTimecodeLabel = true );
    void queuePresentationRequest( const PresentationRequestContext &context );
    bool consumePresentationRequest( uint64_t requestSerial,
                                     PresentationRequestContext *context );
    void computeDisplaySceneGeometry( int sourceWidth,
                                      int sourceHeight,
                                      bool zoomFitEnabled,
                                      double stretchX,
                                      double stretchY,
                                      int *sceneWidth,
                                      int *sceneHeight ) const;
    void recordPresentedFrame( const RenderFrameThread::ReadyFrame &readyFrame,
                               const PresentationRequestContext &requestContext );
    void enqueuePlaybackPrepTask( const PlaybackPrepTask &task );
    PlaybackPrepResult buildPlaybackPrepResult( const PlaybackPrepTask &task );
    void playbackPrepThreadLoop( void );
    void presentPlaybackPreparedFrame( const PlaybackPrepResult &result );
    void finishPresentedFrame( uint64_t displayFrame,
                               const RenderFrameThread::ReadyFrame &readyFrame,
                               const PresentationRequestContext &requestContext,
                               const uint8_t *rgb8DisplaySource,
                               uint8_t underOver,
                               double displayStart );
    bool playbackPolicyActive( void ) const;
    void applyPlaybackDebayerSelection( void );
    void setPlaybackProfileDebayerRequest(
        PlaybackProfileDebayerRequest request );
    void setPlaybackProfileProcessingRequest(
        PlaybackProfileProcessingRequest request );
    void restorePlaybackDebayerSelection( const QString & label );
    QString selectedPlaybackDebayerLabel( void ) const;
    QString playbackDebayerLabel( void ) const;
    QString selectedPlaybackProcessingLabel( void ) const;
    QString playbackProcessingLabel( void ) const;
    void importNewMlv(QString fileName);
    int openMlvForPreview(QString fileName);
    int openMlv(QString fileName);
    void playbackHandling( int timeDiff );
    void initGui( void );
    void initLib( void );
    void readSettings( void );
    void writeSettings( void );
    void startExportPipe( QString fileName );
    void startExportCdng( QString fileName );
    void startExportMlv( QString fileName );
    void startExportAVFoundation( QString fileName );
    void addFileToSession( QString fileName );
    int askToSaveCurrentSession( void );
    void openSession(QString fileNameSession );
    void saveSession( QString fileName );
    void applyEffectiveDualIsoPlaybackSettings( void );
    /* Phase 4E: Playback Quality dial helpers. */
    void initPlaybackQualityFromSettings( void );
    void applyPlaybackQualityMode( int mode, bool persist, bool forceRefresh );
    void applyPlaybackAutoTargetFps( int targetFps, bool persist );
    void setPlaybackQualityIndicatorVisible( bool visible, bool persist );
    void updatePlaybackQualityIndicator( void );
    int  effectivePlaybackScaleFactorForRequest( void ) const;
    static bool dualIsoPlaybackPreferHqMean23GuiFallback( void );
    void beginPlayToFirstFrameMeasurement( void );
    void notePlayToFirstFramePresentation( int presentedFrame );
    bool primePlaybackCacheOnPlayStart( void );
    void invalidateDisplayPreviewCache( void );
    void readXmlElementsFromFile(QXmlStreamReader *Rxml, ReceiptSettings *receipt , int version);
    void writeXmlElementsToFile( QXmlStreamWriter *xmlWriter, ReceiptSettings *receipt );
    void deleteSession( void );
    bool isFileInSession( QString fileName );
    void pasteReceiptFromClipboardTo( int row );
    void setSliders(ReceiptSettings *sliders , bool paste);
    void resetSliders( void );
    void setReceipt( ReceiptSettings *sliders );
    void replaceReceipt(ReceiptSettings *receiptTarget, ReceiptSettings *receiptSource , bool paste);
    void resetReceiptWithDefault( ReceiptSettings *receipt );
    int showFileInEditor(int row);
    void addClipToExportQueue( int row, QString fileName );
    void previewPicture( int row );
    void setPreviewMode( void );
    double getFramerate( void );
    void paintAudioTrack( void );
    uint8_t drawZebras( QImage *image );
    void drawFrameNumberLabel( int frameIndex = -1 );
    void updateTimeCodeLabelForFrame( int frameIndex );
    bool shouldUseGpu16PreviewPath( void ) const;
    bool shouldUseGpuPreviewProcessingPath( void ) const;
    bool shouldUseGpuBilinearDebayerPath( void ) const;
    void setToolButtonFocusPixels( int index );
    void setToolButtonFocusPixelsIntMethod( int index );
    void setToolButtonBadPixels( int index );
    void setToolButtonBadPixelsSearchMethod( int index );
    void setToolButtonBadPixelsIntMethod( int index );
    void setToolButtonChromaSmooth( int index );
    void setToolButtonPatternNoise( int index );
    void setToolButtonUpsideDown( int index );
    void setToolButtonVerticalStripes( int index );
    void setToolButtonDualIso( int index );
    void setToolButtonDualIsoInterpolation( int index );
    void setToolButtonDualIsoAliasMap( int index );
    void setToolButtonDualIsoFullresBlending( int index );
    void setToolButtonDarkFrameSubtraction( int index );
    void setToolButtonGCurves( int index );
    int toolButtonFocusPixelsCurrentIndex( void );
    int toolButtonFocusPixelsIntMethodCurrentIndex( void );
    int toolButtonBadPixelsCurrentIndex( void );
    int toolButtonBadPixelsSearchMethodCurrentIndex( void );
    int toolButtonBadPixelsIntMethodCurrentIndex( void );
    int toolButtonChromaSmoothCurrentIndex( void );
    int toolButtonPatternNoiseCurrentIndex( void );
    int toolButtonUpsideDownCurrentIndex( void );
    int toolButtonVerticalStripesCurrentIndex( void );
    int toolButtonDualIsoCurrentIndex( void );
    int toolButtonDualIsoInterpolationCurrentIndex( void );
    int toolButtonDualIsoAliasMapCurrentIndex( void );
    int toolButtonDualIsoFullresBlendingCurrentIndex( void );
    int toolButtonDarkFrameSubtractionCurrentIndex( void );
    int toolButtonGCurvesCurrentIndex( void );
    void initCutInOut( int frames );
    void initRawBlackAndWhite( void );
    double getHorizontalStretchFactor( bool downScale );
    double getVerticalStretchFactor( bool downScale );
    void setWhiteBalanceFromMlv( ReceiptSettings *sliders );
    void setGradientMask( void );
    uint16_t autoCorrectRawBlackLevel( void );
    bool isRawBlackLevelWrong( void );
    QRecentFilesMenu *m_pRecentFilesMenu;
    void selectDebayerAlgorithm( void );
    void enableCreativeAdjustments( bool enable );
    void resultingResolution( void );
    bool isExportSequence( void );
    void setMarkColor(int clipNr , uint8_t mark);
    void focusPixelCheckAndInstallation( void );
    void checkFocusPixelUpdate( void );
    QModelIndexList selectedClipsList( void );
    void listViewSessionUpdate( void );
    void checkDiskFull( QString path );

signals:
    void exportReady( void );
    void playbackPrepResultReady( void );
};

#endif // MAINWINDOW_H
