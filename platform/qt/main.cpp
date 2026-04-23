/*!
 * \file main.cpp
 * \author masc4ii
 * \copyright 2017
 * \brief The main... the start of the horror
 */

#include "MainWindow.h"
#include "MyApplication.h"
#include "../../src/batch/BatchContext.h"
#include "../../src/batch/BatchRunner.h"
#include "../../src/batch/BatchLogger.h"
#include "../../src/batch/MlvTrim.h"

#include <QCommandLineParser>
#include <QDir>
#include <QFileInfo>
#include <QTextStream>
#include <cstring>

/* Raw argv scan for "--batch" BEFORE QApplication is constructed.
 * QCommandLineParser needs QApplication, but we need to know the
 * mode early to skip MainWindow creation entirely in batch mode. */
static bool hasBatchFlag(int argc, char *argv[])
{
    for (int i = 1; i < argc; ++i)
    {
        if (std::strcmp(argv[i], "--batch") == 0) return true;
    }
    return false;
}

static bool hasTrimMlvFlag(int argc, char *argv[])
{
    for (int i = 1; i < argc; ++i)
    {
        if (std::strcmp(argv[i], "--trim-mlv") == 0) return true;
    }
    return false;
}

static bool hasPlaybackProfileFlag(int argc, char *argv[])
{
    for (int i = 1; i < argc; ++i)
    {
        if (std::strcmp(argv[i], "--profile-playback") == 0) return true;
    }
    return false;
}

static bool hasGpuRelatedFlag(int argc, char *argv[])
{
    for (int i = 1; i < argc; ++i)
    {
        if (std::strcmp(argv[i], "--gpu-viewport") == 0) return true;
        if (std::strcmp(argv[i], "--gpu-preview-processing") == 0) return true;
        if (std::strcmp(argv[i], "--gpu-bilinear-debayer") == 0) return true;
    }
    return false;
}

static bool shouldPreferDesktopOpenGl(int argc,
                                      char *argv[],
                                      bool batch,
                                      bool trim_mlv,
                                      bool profile_playback)
{
#ifdef Q_OS_WIN
    if (!qEnvironmentVariableIsEmpty("QT_OPENGL")) return false;
    if (batch || trim_mlv) return false;
    if (profile_playback) return true;
    if (hasGpuRelatedFlag(argc, argv)) return true;
    if (qEnvironmentVariableIsSet("MLVAPP_EXPERIMENTAL_GL_VIEWPORT")) return true;
    if (qEnvironmentVariableIsSet("MLVAPP_EXPERIMENTAL_GPU_PROCESSING")) return true;
    if (qEnvironmentVariableIsSet("MLVAPP_EXPERIMENTAL_GPU_DEBAYER")) return true;
#else
    Q_UNUSED(argc)
    Q_UNUSED(argv)
    Q_UNUSED(batch)
    Q_UNUSED(trim_mlv)
    Q_UNUSED(profile_playback)
#endif
    return false;
}

static MainWindow::PlaybackProfileScope parsePlaybackProfileScope(const QString & value, bool * ok)
{
    if (ok) *ok = true;

    const QString normalized = value.trimmed().toLower();
    if (normalized.isEmpty() || normalized == QStringLiteral("none")) return MainWindow::PlaybackProfileScope::None;
    if (normalized == QStringLiteral("histogram")) return MainWindow::PlaybackProfileScope::Histogram;
    if (normalized == QStringLiteral("waveform")) return MainWindow::PlaybackProfileScope::Waveform;
    if (normalized == QStringLiteral("parade")) return MainWindow::PlaybackProfileScope::Parade;
    if (normalized == QStringLiteral("vectorscope")) return MainWindow::PlaybackProfileScope::Vectorscope;

    if (ok) *ok = false;
    return MainWindow::PlaybackProfileScope::None;
}

static MainWindow::PlaybackProfileDebayerRequest parsePlaybackProfileDebayerRequest(
    const QString & value,
    bool * ok)
{
    if (ok) *ok = true;

    const QString normalized = value.trimmed().toLower();
    if (normalized.isEmpty() || normalized == QStringLiteral("auto"))
    {
        return MainWindow::PlaybackProfileDebayerRequest::Auto;
    }
    if (normalized == QStringLiteral("receipt"))
    {
        return MainWindow::PlaybackProfileDebayerRequest::Receipt;
    }
    if (normalized == QStringLiteral("none"))
    {
        return MainWindow::PlaybackProfileDebayerRequest::None;
    }
    if (normalized == QStringLiteral("simple"))
    {
        return MainWindow::PlaybackProfileDebayerRequest::Simple;
    }
    if (normalized == QStringLiteral("bilinear"))
    {
        return MainWindow::PlaybackProfileDebayerRequest::Bilinear;
    }
    if (normalized == QStringLiteral("lmmse"))
    {
        return MainWindow::PlaybackProfileDebayerRequest::LMMSE;
    }
    if (normalized == QStringLiteral("igv"))
    {
        return MainWindow::PlaybackProfileDebayerRequest::IGV;
    }
    if (normalized == QStringLiteral("amaze"))
    {
        return MainWindow::PlaybackProfileDebayerRequest::AMaZE;
    }
    if (normalized == QStringLiteral("ahd"))
    {
        return MainWindow::PlaybackProfileDebayerRequest::AHD;
    }
    if (normalized == QStringLiteral("rcd"))
    {
        return MainWindow::PlaybackProfileDebayerRequest::RCD;
    }
    if (normalized == QStringLiteral("dcb"))
    {
        return MainWindow::PlaybackProfileDebayerRequest::DCB;
    }
    if (normalized == QStringLiteral("amaze-cached"))
    {
        return MainWindow::PlaybackProfileDebayerRequest::AmazeCached;
    }

    if (ok) *ok = false;
    return MainWindow::PlaybackProfileDebayerRequest::Auto;
}

static MainWindow::PlaybackProfileProcessingRequest parsePlaybackProfileProcessingRequest(
    const QString & value,
    bool * ok)
{
    if (ok) *ok = true;

    const QString normalized = value.trimmed().toLower();
    if (normalized.isEmpty() || normalized == QStringLiteral("auto"))
    {
        return MainWindow::PlaybackProfileProcessingRequest::Auto;
    }
    if (normalized == QStringLiteral("receipt"))
    {
        return MainWindow::PlaybackProfileProcessingRequest::Receipt;
    }
    if (normalized == QStringLiteral("subset"))
    {
        return MainWindow::PlaybackProfileProcessingRequest::Subset;
    }

    if (ok) *ok = false;
    return MainWindow::PlaybackProfileProcessingRequest::Auto;
}

static GpuPreviewProcessingBackendRequest parsePlaybackProfileGpuPreviewProcessingBackend(
    const QString & value,
    bool * ok)
{
    if (ok) *ok = true;

    const QString normalized = value.trimmed().toLower();
    if (normalized.isEmpty() || normalized == QStringLiteral("auto"))
    {
        return GpuPreviewProcessingBackendRequest::Auto;
    }
    if (normalized == QStringLiteral("cpu"))
    {
        return GpuPreviewProcessingBackendRequest::Cpu;
    }
    if (normalized == QStringLiteral("gpu"))
    {
        return GpuPreviewProcessingBackendRequest::Gpu;
    }

    if (ok) *ok = false;
    return GpuPreviewProcessingBackendRequest::Auto;
}

static GpuBilinearDebayerBackendRequest parsePlaybackProfileGpuBilinearDebayerBackend(
    const QString & value,
    bool * ok)
{
    if (ok) *ok = true;

    const QString normalized = value.trimmed().toLower();
    if (normalized.isEmpty() || normalized == QStringLiteral("auto"))
    {
        return GpuBilinearDebayerBackendRequest::Auto;
    }
    if (normalized == QStringLiteral("cpu"))
    {
        return GpuBilinearDebayerBackendRequest::Cpu;
    }
    if (normalized == QStringLiteral("gpu"))
    {
        return GpuBilinearDebayerBackendRequest::Gpu;
    }

    if (ok) *ok = false;
    return GpuBilinearDebayerBackendRequest::Auto;
}

static int runBatch(QCoreApplication &app)
{
    QCommandLineParser parser;
    parser.setApplicationDescription(
        QStringLiteral("MLVApp batch mode — headless Cinema DNG export"));

    /* Do NOT call parser.addHelpOption() — on Windows/Qt it triggers a
     * QMessageBox.  We handle -h/--help manually below. */
    QCommandLineOption helpOpt(
        QStringList() << QStringLiteral("h") << QStringLiteral("help"),
        QStringLiteral("Show this help text and exit."));
    parser.addOption(helpOpt);

    /* --batch is already consumed by hasBatchFlag(); add it here so
     * QCommandLineParser doesn't complain about an unknown option. */
    QCommandLineOption batchOpt(
        QStringLiteral("batch"),
        QStringLiteral("Run in headless batch export mode."));
    parser.addOption(batchOpt);

    QCommandLineOption inputOpt(
        QStringList() << QStringLiteral("i") << QStringLiteral("input"),
        QStringLiteral("Input MLV file or folder path."),
        QStringLiteral("path"));
    parser.addOption(inputOpt);

    QCommandLineOption outputOpt(
        QStringList() << QStringLiteral("o") << QStringLiteral("output"),
        QStringLiteral("Output directory for exported DNG sequences."),
        QStringLiteral("path"));
    parser.addOption(outputOpt);

    QCommandLineOption skipErrorsOpt(
        QStringLiteral("skip-errors"),
        QStringLiteral("Skip corrupt frames instead of aborting."));
    parser.addOption(skipErrorsOpt);

    QCommandLineOption logOpt(
        QStringLiteral("log"),
        QStringLiteral("Mirror log output to file."),
        QStringLiteral("file"));
    parser.addOption(logOpt);

    QCommandLineOption verboseOpt(
        QStringLiteral("verbose"),
        QStringLiteral("Enable detailed per-frame logging."));
    parser.addOption(verboseOpt);

    QCommandLineOption receiptOpt(
        QStringList() << QStringLiteral("r") << QStringLiteral("receipt"),
        QStringLiteral("Apply .marxml receipt settings to export."),
        QStringLiteral("file"));
    parser.addOption(receiptOpt);

    QCommandLineOption defaultReceiptOpt(
        QStringLiteral("default-receipt"),
        QStringLiteral("Use the GUI-configured default receipt."));
    parser.addOption(defaultReceiptOpt);

    QCommandLineOption resumeOpt(
        QStringLiteral("resume"),
        QStringLiteral("Skip clips whose DNG output already matches expected frame count."));
    parser.addOption(resumeOpt);

    parser.process(app);

    /* Init log file mirror as early as possible so that --help and
     * missing-arg errors are captured in the log file too. */
    QString logPath = parser.value(logOpt);
    BatchLogger::init(logPath);

    /* --help: print to stdout (+ log), exit 0.  No QMessageBox. */
    if( parser.isSet(helpOpt) )
    {
        BatchLogger::out(parser.helpText() + QStringLiteral("\n"));
        BatchLogger::shutdown();
        return 0;
    }

    /* --input and --output are required in batch mode */
    if( !parser.isSet(inputOpt) || !parser.isSet(outputOpt) )
    {
        BatchLogger::err(QStringLiteral("[BATCH] ERROR: --input and --output are required.\n\n"));
        BatchLogger::err(parser.helpText() + QStringLiteral("\n"));
        BatchLogger::shutdown();
        return 2;
    }

    QString inputPath   = parser.value(inputOpt);
    QString outputPath  = parser.value(outputOpt);
    bool skipErrors     = parser.isSet(skipErrorsOpt);
    bool verbose        = parser.isSet(verboseOpt);
    QString receiptPath     = parser.value(receiptOpt);
    bool useDefaultReceipt  = parser.isSet(defaultReceiptOpt);

    bool resume         = parser.isSet(resumeOpt);

    /* Store in BatchContext for global access */
    BatchContext::setBatchMode(true);
    BatchContext::setSkipErrors(skipErrors);
    BatchContext::setVerbose(verbose);
    BatchContext::setLogPath(logPath);
    BatchContext::setReceiptPath(receiptPath);
    BatchContext::setUseDefaultReceipt(useDefaultReceipt);
    BatchContext::setResumeEnabled(resume);

    int exitCode = BatchRunner::run(inputPath, outputPath);
    BatchLogger::shutdown();
    return exitCode;
}

static int runPlaybackProfile(QApplication &app)
{
    QCommandLineParser parser;
    parser.setApplicationDescription(
        QStringLiteral("MLVApp headless playback profiler - steps frames through the real Qt playback path and writes JSON timings."));

    QCommandLineOption helpOpt(
        QStringList() << QStringLiteral("h") << QStringLiteral("help"),
        QStringLiteral("Show this help text and exit."));
    parser.addOption(helpOpt);

    parser.addOption(QCommandLineOption(
        QStringLiteral("profile-playback"),
        QStringLiteral("Run in headless playback profiling mode.")));

    const QCommandLineOption inputOpt(
        QStringList() << QStringLiteral("i") << QStringLiteral("input"),
        QStringLiteral("Input MLV file path."),
        QStringLiteral("path"));
    parser.addOption(inputOpt);

    const QCommandLineOption outputOpt(
        QStringList() << QStringLiteral("o") << QStringLiteral("output"),
        QStringLiteral("Output JSON file path."),
        QStringLiteral("path"));
    parser.addOption(outputOpt);

    const QCommandLineOption receiptOpt(
        QStringList() << QStringLiteral("r") << QStringLiteral("receipt"),
        QStringLiteral("Optional .marxml receipt to apply before profiling."),
        QStringLiteral("file"));
    parser.addOption(receiptOpt);

    const QCommandLineOption framesOpt(
        QStringLiteral("frames"),
        QStringLiteral("Number of frames to step through."),
        QStringLiteral("count"),
        QStringLiteral("16"));
    parser.addOption(framesOpt);

    const QCommandLineOption startFrameOpt(
        QStringLiteral("start-frame"),
        QStringLiteral("Zero-based first frame to render."),
        QStringLiteral("frame"),
        QStringLiteral("0"));
    parser.addOption(startFrameOpt);

    const QCommandLineOption scopeOpt(
        QStringLiteral("scope"),
        QStringLiteral("Optional live scope during profiling: none, histogram, waveform, parade, vectorscope."),
        QStringLiteral("mode"),
        QStringLiteral("none"));
    parser.addOption(scopeOpt);

    const QCommandLineOption playbackDebayerOpt(
        QStringLiteral("playback-debayer"),
        QStringLiteral("Playback debayer policy for profiling: auto, receipt, none, simple, bilinear, lmmse, igv, amaze, ahd, rcd, dcb, amaze-cached."),
        QStringLiteral("mode"),
        QStringLiteral("auto"));
    parser.addOption(playbackDebayerOpt);

    const QCommandLineOption playbackProcessingOpt(
        QStringLiteral("playback-processing"),
        QStringLiteral("Playback processing policy for profiling: auto, receipt, subset."),
        QStringLiteral("mode"),
        QStringLiteral("auto"));
    parser.addOption(playbackProcessingOpt);

    const QCommandLineOption zebrasOpt(
        QStringLiteral("zebras"),
        QStringLiteral("Enable zebra overlay during profiling."));
    parser.addOption(zebrasOpt);

    const QCommandLineOption rawCacheOpt(
        QStringLiteral("raw-cache-mb"),
        QStringLiteral("Enable raw caching with this many MiB."),
        QStringLiteral("mebibytes"),
        QStringLiteral("0"));
    parser.addOption(rawCacheOpt);

    const QCommandLineOption cacheCpuOpt(
        QStringLiteral("cache-cpu-cores"),
        QStringLiteral("Cache worker cores when raw caching is enabled."),
        QStringLiteral("count"),
        QStringLiteral("1"));
    parser.addOption(cacheCpuOpt);

    const QCommandLineOption threadsOpt(
        QStringLiteral("threads"),
        QStringLiteral("Force MLVApp worker thread count via MLVAPP_FORCE_THREADS, or use auto to leave the worker count unforced."),
        QStringLiteral("count"),
        QStringLiteral("1"));
    parser.addOption(threadsOpt);

    const QCommandLineOption fastOpenOpt(
        QStringLiteral("fast-open"),
        QStringLiteral("Use the preview/open-for-preview path instead of a full open."));
    parser.addOption(fastOpenOpt);

    const QCommandLineOption gpuViewportOpt(
        QStringLiteral("gpu-viewport"),
        QStringLiteral("Enable the experimental OpenGL viewport path while profiling."));
    parser.addOption(gpuViewportOpt);

    const QCommandLineOption gpuPreviewProcessingOpt(
        QStringLiteral("gpu-preview-processing"),
        QStringLiteral("Preview-processing backend selection for the 16-bit GPU viewport path: auto, cpu, gpu. Selecting gpu implies --gpu-viewport."),
        QStringLiteral("backend"),
        QStringLiteral("auto"));
    parser.addOption(gpuPreviewProcessingOpt);

    const QCommandLineOption gpuBilinearDebayerOpt(
        QStringLiteral("gpu-bilinear-debayer"),
        QStringLiteral("Experimental bilinear debayer backend selection for the GPU preview-processing path: auto, cpu, gpu. Selecting gpu implies --gpu-viewport."),
        QStringLiteral("backend"),
        QStringLiteral("auto"));
    parser.addOption(gpuBilinearDebayerOpt);

    const QCommandLineOption showWindowOpt(
        QStringLiteral("show-window"),
        QStringLiteral("Show the main window while profiling instead of keeping it hidden."));
    parser.addOption(showWindowOpt);

    const QCommandLineOption waitForPaintOpt(
        QStringLiteral("wait-for-paint"),
        QStringLiteral("After each frameReady(), wait for the graphics viewport to receive a paint event and record paint latency. Implies --show-window."));
    parser.addOption(waitForPaintOpt);

    const QCommandLineOption stageLogOpt(
        QStringLiteral("stage-log"),
        QStringLiteral("Optional stage timing log path. Also enables MLVAPP_STAGE_TIMING."),
        QStringLiteral("file"));
    parser.addOption(stageLogOpt);

    parser.process(app);

    QTextStream out(stdout);
    QTextStream err(stderr);

    if (parser.isSet(helpOpt))
    {
        out << parser.helpText() << "\n";
        return 0;
    }

    if (!parser.isSet(inputOpt) || !parser.isSet(outputOpt))
    {
        err << "[PROFILE] ERROR: --input and --output are required.\n\n";
        err << parser.helpText() << "\n";
        return 2;
    }

    bool ok = false;
    const int frameCount = parser.value(framesOpt).toInt(&ok);
    if (!ok || frameCount <= 0)
    {
        err << "[PROFILE] ERROR: --frames must be greater than 0.\n";
        return 2;
    }

    const int startFrame = parser.value(startFrameOpt).toInt(&ok);
    if (!ok || startFrame < 0)
    {
        err << "[PROFILE] ERROR: --start-frame must be 0 or greater.\n";
        return 2;
    }

    const uint64_t rawCacheMB = parser.value(rawCacheOpt).toULongLong(&ok);
    if (!ok)
    {
        err << "[PROFILE] ERROR: invalid --raw-cache-mb value.\n";
        return 2;
    }

    const int cacheCpuCores = parser.value(cacheCpuOpt).toInt(&ok);
    if (!ok || cacheCpuCores <= 0)
    {
        err << "[PROFILE] ERROR: --cache-cpu-cores must be greater than 0.\n";
        return 2;
    }

    const QString threadsValue = parser.value(threadsOpt).trimmed();
    const bool autoThreads = threadsValue.compare(QStringLiteral("auto"), Qt::CaseInsensitive) == 0
        || threadsValue == QStringLiteral("0");
    int forcedThreads = 1;
    if (!autoThreads)
    {
        forcedThreads = threadsValue.toInt(&ok);
        if (!ok || forcedThreads <= 0)
        {
            err << "[PROFILE] ERROR: --threads must be greater than 0 or set to auto.\n";
            return 2;
        }
    }

    bool scopeOk = false;
    const MainWindow::PlaybackProfileScope scope =
        parsePlaybackProfileScope(parser.value(scopeOpt), &scopeOk);
    if (!scopeOk)
    {
        err << "[PROFILE] ERROR: --scope must be one of none, histogram, waveform, parade, vectorscope.\n";
        return 2;
    }

    bool playbackDebayerOk = false;
    const MainWindow::PlaybackProfileDebayerRequest playbackDebayer =
        parsePlaybackProfileDebayerRequest(
            parser.value(playbackDebayerOpt),
            &playbackDebayerOk);
    if (!playbackDebayerOk)
    {
        err << "[PROFILE] ERROR: --playback-debayer must be one of auto, receipt, none, simple, bilinear, lmmse, igv, amaze, ahd, rcd, dcb, amaze-cached.\n";
        return 2;
    }

    bool playbackProcessingOk = false;
    const MainWindow::PlaybackProfileProcessingRequest playbackProcessing =
        parsePlaybackProfileProcessingRequest(
            parser.value(playbackProcessingOpt),
            &playbackProcessingOk);
    if (!playbackProcessingOk)
    {
        err << "[PROFILE] ERROR: --playback-processing must be one of auto, receipt, subset.\n";
        return 2;
    }

    bool gpuPreviewProcessingOk = false;
    const GpuPreviewProcessingBackendRequest gpuPreviewProcessingBackend =
        parsePlaybackProfileGpuPreviewProcessingBackend(
            parser.value(gpuPreviewProcessingOpt),
            &gpuPreviewProcessingOk);
    if (!gpuPreviewProcessingOk)
    {
        err << "[PROFILE] ERROR: --gpu-preview-processing must be one of auto, cpu, gpu.\n";
        return 2;
    }

    bool gpuBilinearDebayerOk = false;
    const GpuBilinearDebayerBackendRequest gpuBilinearDebayerBackend =
        parsePlaybackProfileGpuBilinearDebayerBackend(
            parser.value(gpuBilinearDebayerOpt),
            &gpuBilinearDebayerOk);
    if (!gpuBilinearDebayerOk)
    {
        err << "[PROFILE] ERROR: --gpu-bilinear-debayer must be one of auto, cpu, gpu.\n";
        return 2;
    }

    if (autoThreads)
    {
        qunsetenv("MLVAPP_FORCE_THREADS");
    }
    else
    {
        qputenv("MLVAPP_FORCE_THREADS", QByteArray::number(forcedThreads));
    }
    if (parser.isSet(gpuViewportOpt)
        || gpuPreviewProcessingBackend == GpuPreviewProcessingBackendRequest::Gpu)
    {
        qputenv("MLVAPP_EXPERIMENTAL_GL_VIEWPORT", QByteArrayLiteral("1"));
    }
    if (gpuBilinearDebayerBackend == GpuBilinearDebayerBackendRequest::Gpu)
    {
        qputenv("MLVAPP_EXPERIMENTAL_GL_VIEWPORT", QByteArrayLiteral("1"));
    }
    const QString inputPath = QFileInfo(parser.value(inputOpt)).absoluteFilePath();
    const QString outputPath = QFileInfo(parser.value(outputOpt)).absoluteFilePath();
    const QString receiptPath = parser.value(receiptOpt).isEmpty()
        ? QString()
        : QFileInfo(parser.value(receiptOpt)).absoluteFilePath();
    const QString stageLogPath = parser.value(stageLogOpt).isEmpty()
        ? QString()
        : QFileInfo(parser.value(stageLogOpt)).absoluteFilePath();

    if (parser.isSet(stageLogOpt))
    {
        qputenv("MLVAPP_STAGE_TIMING", QByteArrayLiteral("1"));
        qputenv("MLVAPP_STAGE_TIMING_FILE", QDir::toNativeSeparators(stageLogPath).toLocal8Bit());
    }

    MainWindow::PlaybackProfileOptions options;
    options.inputPath = inputPath;
    options.receiptPath = receiptPath;
    options.outputPath = outputPath;
    options.startFrame = startFrame;
    options.frameCount = frameCount;
    options.workerThreads = forcedThreads;
    options.forceWorkerThreads = !autoThreads;
    options.rawCacheMB = rawCacheMB;
    options.cacheCpuCores = cacheCpuCores;
    options.zebras = parser.isSet(zebrasOpt);
    options.fastOpen = parser.isSet(fastOpenOpt);
    options.showWindow = parser.isSet(showWindowOpt) || parser.isSet(waitForPaintOpt);
    options.waitForPaint = parser.isSet(waitForPaintOpt);
    options.scope = scope;
    options.playbackDebayer = playbackDebayer;
    options.playbackProcessing = playbackProcessing;
    options.gpuPreviewProcessingBackend = gpuPreviewProcessingBackend;
    options.gpuBilinearDebayerBackend = gpuBilinearDebayerBackend;

    QByteArray appName = QCoreApplication::applicationFilePath().toLocal8Bit();
    char *profileArgv[] = { appName.data(), nullptr };
    int profileArgc = 1;

    MainWindow window(profileArgc, profileArgv);
    window.hide();
    return window.runHeadlessPlaybackProfile(options);
}

int main(int argc, char *argv[])
{
    bool batch = hasBatchFlag(argc, argv);
    bool trim_mlv = hasTrimMlvFlag(argc, argv);
    bool profile_playback = hasPlaybackProfileFlag(argc, argv);

    if (shouldPreferDesktopOpenGl(argc, argv, batch, trim_mlv, profile_playback))
    {
        qputenv("QT_OPENGL", QByteArrayLiteral("desktop"));
    }

    MyApplication a(argc, argv);
#if QT_VERSION < QT_VERSION_CHECK(6, 0, 0)
    a.setAttribute(Qt::AA_UseHighDpiPixmaps);
#endif
#ifdef Q_OS_WIN
    a.setAttribute(Qt::AA_Use96Dpi);
#endif

    if (batch)
    {
        /* Batch mode — no GUI window, but QApplication stays alive
         * because internal export code may touch widgets/fonts. */
        a.setQuitOnLastWindowClosed(false);
        return runBatch(a);
    }

    if (trim_mlv)
    {
        a.setQuitOnLastWindowClosed(false);
        return MlvTrim::run(a);
    }

    if (profile_playback)
    {
        a.setQuitOnLastWindowClosed(false);
        return runPlaybackProfile(a);
    }

    /* Normal GUI mode — unchanged */
    MainWindow w(argc, argv);
    w.show();

    return a.exec();
}
