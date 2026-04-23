#include "MlvTrim.h"

extern "C" {
#include "../mlv/video_mlv.h"
}

#include <QCommandLineOption>
#include <QCommandLineParser>
#include <QDir>
#include <QFileInfo>
#include <QTextStream>

namespace {

bool parse_positive_int(const QString & text, uint32_t * value)
{
    bool ok = false;
    const uint value64 = text.toUInt(&ok);
    if (!ok || value64 == 0) {
        return false;
    }

    *value = value64;
    return true;
}

} // namespace

int MlvTrim::run(QCoreApplication &app)
{
    QCommandLineParser parser;
    parser.setApplicationDescription(
        QStringLiteral("MLVApp trim mode - copy a frame range from an MLV into a smaller MLV."));

    QCommandLineOption helpOpt(
        QStringList() << QStringLiteral("h") << QStringLiteral("help"),
        QStringLiteral("Show this help text and exit."));
    parser.addOption(helpOpt);

    parser.addOption(QCommandLineOption(
        QStringLiteral("trim-mlv"),
        QStringLiteral("Run in headless MLV trimming mode.")));

    const QCommandLineOption inputOpt(
        QStringList() << QStringLiteral("i") << QStringLiteral("input"),
        QStringLiteral("Input MLV file path."),
        QStringLiteral("path"));
    parser.addOption(inputOpt);

    const QCommandLineOption outputOpt(
        QStringList() << QStringLiteral("o") << QStringLiteral("output"),
        QStringLiteral("Output MLV file path."),
        QStringLiteral("path"));
    parser.addOption(outputOpt);

    const QCommandLineOption cutInOpt(
        QStringLiteral("cut-in"),
        QStringLiteral("1-based first frame to keep."),
        QStringLiteral("frame"),
        QStringLiteral("1"));
    parser.addOption(cutInOpt);

    const QCommandLineOption cutOutOpt(
        QStringLiteral("cut-out"),
        QStringLiteral("1-based last frame to keep."),
        QStringLiteral("frame"));
    parser.addOption(cutOutOpt);

    const QCommandLineOption frameCountOpt(
        QStringLiteral("frame-count"),
        QStringLiteral("Number of frames to keep, starting at --cut-in."),
        QStringLiteral("count"));
    parser.addOption(frameCountOpt);

    const QCommandLineOption describeInputOpt(
        QStringLiteral("describe-input"),
        QStringLiteral("Print input clip metadata and exit without writing a trimmed clip."));
    parser.addOption(describeInputOpt);

    const QCommandLineOption withAudioOpt(
        QStringLiteral("with-audio"),
        QStringLiteral("Preserve audio blocks inside the trimmed MLV when present."));
    parser.addOption(withAudioOpt);

    parser.process(app);

    QTextStream out(stdout);
    QTextStream err(stderr);

    if (parser.isSet(helpOpt)) {
        out << parser.helpText() << "\n";
        return 0;
    }

    if (!parser.isSet(inputOpt)) {
        err << "[TRIM] ERROR: --input is required.\n\n";
        err << parser.helpText() << "\n";
        return 2;
    }

    const bool describe_only = parser.isSet(describeInputOpt);
    const bool has_cut_out = parser.isSet(cutOutOpt);
    const bool has_frame_count = parser.isSet(frameCountOpt);

    if (has_cut_out && has_frame_count) {
        err << "[TRIM] ERROR: Use either --cut-out or --frame-count, not both.\n";
        return 2;
    }

    if (!describe_only && (!parser.isSet(outputOpt) || (!has_cut_out && !has_frame_count))) {
        err << "[TRIM] ERROR: --output and either --cut-out or --frame-count are required unless --describe-input is set.\n\n";
        err << parser.helpText() << "\n";
        return 2;
    }

    uint32_t cut_in = 0;
    uint32_t cut_out = 0;
    uint32_t frame_count = 0;
    if (!parse_positive_int(parser.value(cutInOpt), &cut_in)) {
        err << "[TRIM] ERROR: Invalid --cut-in/--cut-out range.\n";
        return 2;
    }

    if (has_cut_out) {
        if (!parse_positive_int(parser.value(cutOutOpt), &cut_out) || cut_in > cut_out) {
            err << "[TRIM] ERROR: Invalid --cut-in/--cut-out range.\n";
            return 2;
        }
    } else if (has_frame_count) {
        if (!parse_positive_int(parser.value(frameCountOpt), &frame_count)) {
            err << "[TRIM] ERROR: Invalid --frame-count value.\n";
            return 2;
        }
        cut_out = cut_in + frame_count - 1;
        if (cut_out < cut_in) {
            err << "[TRIM] ERROR: --frame-count caused integer overflow.\n";
            return 2;
        }
    }

    const QString input_path = parser.value(inputOpt);
    const QString output_path = parser.value(outputOpt);
    const bool with_audio = parser.isSet(withAudioOpt);

    const QFileInfo input_info(input_path);
    if (!input_info.exists() || !input_info.isFile()) {
        err << "[TRIM] ERROR: Input path is not a file: " << input_path << "\n";
        return 3;
    }

    int mlv_error = MLV_ERR_NONE;
    char mlv_error_message[256] = {0};
    const QByteArray input_bytes = QDir::toNativeSeparators(input_path).toLocal8Bit();
    mlvObject_t * video = initMlvObjectWithClip(const_cast<char *>(input_bytes.constData()),
                                                MLV_OPEN_FULL,
                                                &mlv_error,
                                                mlv_error_message);
    if (mlv_error || !video) {
        err << "[TRIM] ERROR: Could not open input MLV: " << input_path << "\n";
        if (mlv_error_message[0] != '\0') {
            err << "[TRIM] DETAIL: " << mlv_error_message << "\n";
        }
        if (video) {
            freeMlvObject(video);
        }
        return 4;
    }

    const uint32_t total_frames = getMlvFrames(video);
    if (describe_only) {
        out << "[TRIM] INFO input=" << input_path << "\n";
        out << "[TRIM] INFO frames=" << total_frames
            << " width=" << getMlvWidth(video)
            << " height=" << getMlvHeight(video)
            << " fps=" << QString::number(getMlvFramerate(video), 'f', 3)
            << " audio=" << (doesMlvHaveAudio(video) ? "true" : "false") << "\n";
        out << "[TRIM] INFO example="
            << "MLVApp.exe --trim-mlv --input \"" << input_path
            << "\" --output tests/fixtures/clips/large_dual_iso.mlv --cut-in 1 --frame-count 8\n";
        freeMlvObject(video);
        return 0;
    }

    if (cut_out > total_frames) {
        err << "[TRIM] ERROR: Requested frame range " << cut_in << "-" << cut_out
            << " exceeds clip frame count " << total_frames << ".\n";
        freeMlvObject(video);
        return 4;
    }

    const QFileInfo output_info(output_path);
    if (!output_info.dir().exists() && !QDir().mkpath(output_info.dir().absolutePath())) {
        err << "[TRIM] ERROR: Failed to create output directory: "
            << output_info.dir().absolutePath() << "\n";
        freeMlvObject(video);
        return 3;
    }

    const QByteArray output_bytes = QDir::toNativeSeparators(output_path).toLocal8Bit();
    FILE * output_mlv = fopen(output_bytes.constData(), "wb");
    if (!output_mlv) {
        err << "[TRIM] ERROR: Could not open output file for writing: " << output_path << "\n";
        freeMlvObject(video);
        return 5;
    }

    const int export_audio = with_audio && doesMlvHaveAudio(video);
    char save_error_message[256] = {0};
    int save_error = saveMlvHeaders(video,
                                    output_mlv,
                                    export_audio,
                                    MLV_FAST_PASS,
                                    cut_in,
                                    cut_out,
                                    "fixture-trim",
                                    save_error_message);

    for (uint32_t frame = cut_in - 1; frame < cut_out && !save_error; ++frame) {
        save_error = saveMlvAVFrame(video,
                                    output_mlv,
                                    export_audio,
                                    MLV_FAST_PASS,
                                    cut_in,
                                    cut_out,
                                    frame,
                                    nullptr,
                                    save_error_message);
    }

    fclose(output_mlv);

    if (save_error) {
        QFile::remove(output_path);
        err << "[TRIM] ERROR: Failed while writing trimmed MLV.\n";
        if (save_error_message[0] != '\0') {
            err << "[TRIM] DETAIL: " << save_error_message << "\n";
        }
        freeMlvObject(video);
        return 5;
    }

    out << "[TRIM] DONE input=" << input_path
        << " output=" << output_path
        << " frames=" << cut_in << "-" << cut_out
        << " frame_count=" << (cut_out - cut_in + 1)
        << " audio=" << (export_audio ? "true" : "false") << "\n";

    freeMlvObject(video);
    return 0;
}
