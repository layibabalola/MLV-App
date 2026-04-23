#include "../common/minitest.h"
#include "../common/hash_helpers.h"
#include "../common/repo_paths.h"

#include <QByteArray>
#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLibraryInfo>
#include <QProcess>
#include <QProcessEnvironment>
#include <QTemporaryDir>

#include <iostream>
#include <map>

static QString clip_fixture_path()
{
    return repo_file_path(QStringLiteral("tests/fixtures/clips/tiny_dual_iso.mlv"));
}

static QString clip_receipt_path()
{
    return repo_file_path(QStringLiteral("tests/fixtures/receipts/tiny_dual_iso_hq.marxml"));
}

static QString clip_preview_receipt_path()
{
    return repo_file_path(QStringLiteral("tests/fixtures/receipts/tiny_dual_iso_preview.marxml"));
}

static QString clip_manifest_path()
{
    return repo_file_path(QStringLiteral("tests/fixtures/golden/tiny_dual_iso_hq_dng_hashes.json"));
}

static QString batch_executable_path()
{
    const QString env_path = qEnvironmentVariable("MLVAPP_BATCH_EXE");
    if (!env_path.isEmpty()) {
        return env_path;
    }
    return QString();
}

static QString app_executable_path()
{
    const QString profile_path = qEnvironmentVariable("MLVAPP_PROFILE_EXE");
    if (!profile_path.isEmpty()) {
        return profile_path;
    }

    const QString batch_path = batch_executable_path();
    if (!batch_path.isEmpty()) {
        return batch_path;
    }

    return QString();
}

static void configure_qt_subprocess_environment(QProcessEnvironment & environment);

static QProcessEnvironment playback_profile_environment()
{
    QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
    environment.insert(QStringLiteral("MLVAPP_FORCE_THREADS"), QStringLiteral("1"));
    environment.insert(QStringLiteral("OMP_NUM_THREADS"), QStringLiteral("1"));
    environment.insert(QStringLiteral("OMP_DYNAMIC"), QStringLiteral("FALSE"));
    configure_qt_subprocess_environment(environment);
    return environment;
}

static void configure_playback_profile_process(QProcess * process,
                                               const QString & app_exe,
                                               const QString & repo_root,
                                               const QStringList & arguments,
                                               const QList<QPair<QString, QString>> & extra_environment = {})
{
    if (!process) return;
    QProcessEnvironment environment = playback_profile_environment();
    for (const auto & entry : extra_environment) {
        environment.insert(entry.first, entry.second);
    }
    process->setProcessEnvironment(environment);
    process->setWorkingDirectory(repo_root);
    process->setProgram(app_exe);
    process->setArguments(arguments);
}

static void configure_qt_subprocess_environment(QProcessEnvironment & environment)
{
    const QString qt_bin = QLibraryInfo::path(QLibraryInfo::BinariesPath);
    const QString qt_plugins = QLibraryInfo::path(QLibraryInfo::PluginsPath);
    const QString qt_platform_plugins =
        QDir(qt_plugins).filePath(QStringLiteral("platforms"));

    QString path = environment.value(QStringLiteral("PATH"));
    if (!qt_bin.isEmpty()) {
        path = qt_bin + QDir::listSeparator() + path;
        environment.insert(QStringLiteral("PATH"), path);
    }
    if (!qt_plugins.isEmpty()) {
        environment.insert(QStringLiteral("QT_PLUGIN_PATH"), qt_plugins);
    }
    if (!qt_platform_plugins.isEmpty()) {
        environment.insert(QStringLiteral("QT_QPA_PLATFORM_PLUGIN_PATH"), qt_platform_plugins);
    }
    if (environment.value(QStringLiteral("QT_OPENGL")).isEmpty()) {
        environment.insert(QStringLiteral("QT_OPENGL"), QStringLiteral("desktop"));
    }
    // This workspace's local playback-profile seam currently assumes the native
    // Windows plugin is deployed. If Linux CI is ever added for this test, use
    // an Xvfb-backed run or deploy/allow the offscreen platform plugin there.
    environment.insert(QStringLiteral("QT_QPA_PLATFORM"), QStringLiteral("windows"));
}

static QJsonObject first_frame_with_raw_decode_telemetry(const QJsonArray & frames)
{
    for (const QJsonValue & value : frames) {
        if (!value.isObject()) continue;
        const QJsonObject sample = value.toObject();
        if (sample.value(QStringLiteral("raw_uint16_lj92_predictor")).toInt(-1) >= 0) {
            return sample;
        }
        if (sample.value(QStringLiteral("raw_uint16_ms")).toDouble() > 0.0) {
            return sample;
        }
        if (sample.value(QStringLiteral("raw_uint16_lj92_pred6_split_requested")).toBool()) {
            return sample;
        }
        if (sample.value(QStringLiteral("raw_uint16_lj92_generic_split_requested")).toBool()) {
            return sample;
        }
        if (sample.value(QStringLiteral("raw_uint16_lj92_pred1_fast_path_measurement_requested")).toBool()) {
            return sample;
        }
    }
    return QJsonObject();
}

static std::map<std::string, std::string> load_expected_hashes(const QString & path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        return {};
    }

    const QJsonDocument document = QJsonDocument::fromJson(file.readAll());
    if (!document.isObject()) {
        return {};
    }

    std::map<std::string, std::string> hashes;
    const QJsonObject object = document.object();
    for (auto it = object.begin(); it != object.end(); ++it) {
        hashes.emplace(it.key().toStdString(), it.value().toString().toStdString());
    }
    return hashes;
}

static std::map<std::string, std::string> collect_dng_hashes(const QString & directory)
{
    std::map<std::string, std::string> hashes;
    QDirIterator it(directory,
                    QStringList() << QStringLiteral("*.dng") << QStringLiteral("*.DNG"),
                    QDir::Files,
                    QDirIterator::Subdirectories);
    const QDir base_dir(directory);
    while (it.hasNext()) {
        const QString absolute_path = it.next();
        const QString relative_path =
            QDir::fromNativeSeparators(base_dir.relativeFilePath(absolute_path));
        hashes.emplace(relative_path.toStdString(), sha256_file(absolute_path));
    }
    return hashes;
}

static void require_processing_timing_field(const QJsonObject & sample,
                                            const char * key,
                                            double * max_ms)
{
    const QString field_name = QString::fromLatin1(key);
    ASSERT_TRUE(sample.contains(field_name));

    const QJsonValue value = sample.value(field_name);
    ASSERT_TRUE(value.isDouble());

    const double field_ms = value.toDouble();
    ASSERT_TRUE(field_ms >= 0.0);
    if (max_ms && field_ms > *max_ms) {
        *max_ms = field_ms;
    }
}

TEST(ClipGolden, TinyDualIsoBatchExportMatchesGolden)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString receipt_path = clip_receipt_path();
    if (!QFileInfo::exists(receipt_path)) {
        SKIP_TEST("Missing fixture receipt tests/fixtures/receipts/tiny_dual_iso_hq.marxml");
    }

    const QString manifest_path = clip_manifest_path();
    if (!QFileInfo::exists(manifest_path)) {
        SKIP_TEST("Missing golden manifest tests/fixtures/golden/tiny_dual_iso_hq_dng_hashes.json");
    }

    const QString batch_exe = batch_executable_path();
    if (batch_exe.isEmpty() || !QFileInfo::exists(batch_exe)) {
        SKIP_TEST("Set MLVAPP_BATCH_EXE to a built MLVApp binary with batch support");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    const std::map<std::string, std::string> expected_hashes = load_expected_hashes(manifest_path);
    if (expected_hashes.empty()) {
        SKIP_TEST("Golden manifest is empty or invalid");
    }

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_dir = temp_dir.filePath(QStringLiteral("out"));
    ASSERT_TRUE(QDir().mkpath(output_dir));

    QProcess process;
    QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
    environment.insert(QStringLiteral("MLVAPP_FORCE_THREADS"), QStringLiteral("1"));
    environment.insert(QStringLiteral("OMP_NUM_THREADS"), QStringLiteral("1"));
    environment.insert(QStringLiteral("OMP_DYNAMIC"), QStringLiteral("FALSE"));
    process.setProcessEnvironment(environment);
    process.setWorkingDirectory(repo_root);
    process.setProgram(batch_exe);
    process.setArguments(QStringList()
                         << QStringLiteral("--batch")
                         << QStringLiteral("--input") << fixture_path
                         << QStringLiteral("--output") << output_dir
                         << QStringLiteral("--receipt") << receipt_path);
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(0, process.exitCode());

    const std::map<std::string, std::string> actual_hashes = collect_dng_hashes(output_dir);
    ASSERT_EQ(expected_hashes.size(), actual_hashes.size());
    if (expected_hashes != actual_hashes) {
        std::cerr << "[ClipGolden] Expected hashes:\n";
        for (const auto & entry : expected_hashes) {
            std::cerr << "  " << entry.first << " " << entry.second << "\n";
        }
        std::cerr << "[ClipGolden] Actual hashes:\n";
        for (const auto & entry : actual_hashes) {
            std::cerr << "  " << entry.first << " " << entry.second << "\n";
        }
    }
    ASSERT_TRUE(expected_hashes == actual_hashes);
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfileProducesJson)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString receipt_path = clip_receipt_path();
    if (!QFileInfo::exists(receipt_path)) {
        SKIP_TEST("Missing fixture receipt tests/fixtures/receipts/tiny_dual_iso_hq.marxml");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile.json"));
    const QString stage_log = temp_dir.filePath(QStringLiteral("playback-stage.log"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--receipt") << receipt_path
            << QStringLiteral("--frames") << QStringLiteral("2")
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--stage-log") << stage_log
            << QStringLiteral("--threads") << QStringLiteral("1"));
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(0, process.exitCode());

    QFile json_file(output_json);
    ASSERT_TRUE(json_file.open(QIODevice::ReadOnly | QIODevice::Text));
    const QJsonDocument document = QJsonDocument::fromJson(json_file.readAll());
    ASSERT_TRUE(document.isObject());

    const QJsonObject root = document.object();
    const QJsonArray frames = root.value(QStringLiteral("frames")).toArray();
    const QJsonObject metadata = root.value(QStringLiteral("metadata")).toObject();
    ASSERT_EQ(2, frames.size());
    ASSERT_EQ(2, metadata.value(QStringLiteral("measured_frames")).toInt());
    ASSERT_TRUE(metadata.value(QStringLiteral("scope")).toString() == QStringLiteral("none"));
    ASSERT_TRUE(!metadata.value(QStringLiteral("window_visible")).toBool());
    ASSERT_TRUE(!metadata.value(QStringLiteral("wait_for_paint")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("average_latency_ms")).toDouble() > 0.0);
    ASSERT_TRUE(metadata.value(QStringLiteral("measurement_model")).toString().contains(QStringLiteral("frameReady")));
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_policy_active")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_debayer_request")).toString()
                == QStringLiteral("auto"));
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_debayer_effective")).toString()
                == QStringLiteral("bilinear"));
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_processing_request")).toString()
                == QStringLiteral("auto"));
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_processing_selected")).toString()
                == QStringLiteral("receipt"));
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_processing_effective")).toString()
                == QStringLiteral("receipt"));
    ASSERT_TRUE(!metadata.value(QStringLiteral("playback_processing_supported")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_debayer_receipt")).toString()
                == QStringLiteral("Bilinear"));
    ASSERT_TRUE(!metadata.value(QStringLiteral("playback_debayer_uses_caching")).toBool());
    ASSERT_EQ(0, metadata.value(QStringLiteral("playback_debayer_engine_mode")).toInt());
    ASSERT_TRUE(metadata.value(QStringLiteral("gpu_preview_processing_backend_request")).toString()
                == QStringLiteral("auto"));
    ASSERT_TRUE(!metadata.value(QStringLiteral("gpu_preview_processing_environment_requested")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("gpu_bilinear_debayer_backend_request")).toString()
                == QStringLiteral("auto"));
    ASSERT_TRUE(!metadata.value(QStringLiteral("gpu_bilinear_debayer_environment_requested")).toBool());
    ASSERT_TRUE(metadata.contains(QStringLiteral("gpu_bilinear_debayer_probe_available")));
    ASSERT_TRUE(metadata.contains(QStringLiteral("gpu_bilinear_debayer_probe_reason")));
    ASSERT_TRUE(metadata.contains(QStringLiteral("gpu_bilinear_debayer_probe_renderer")));
    ASSERT_EQ(1, metadata.value(QStringLiteral("dual_iso_mode_selected")).toInt());
    ASSERT_EQ(2, metadata.value(QStringLiteral("dual_iso_mode_effective")).toInt());
    ASSERT_TRUE(metadata.value(QStringLiteral("dual_iso_preview_runtime_active")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("dual_iso_preview_override_active")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("qt_opengl_environment")).toString()
                == QStringLiteral("desktop"));
    ASSERT_TRUE(metadata.value(QStringLiteral("qt_qpa_platform_environment")).toString()
                == QStringLiteral("windows"));
    ASSERT_TRUE(!metadata.value(QStringLiteral("play_start_preroll_active")).toBool());
    ASSERT_TRUE(!metadata.value(QStringLiteral("play_start_preroll_eligible")).toBool());
    ASSERT_TRUE(!metadata.value(QStringLiteral("play_start_preroll_disabled_by_environment")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("play_to_first_frame_measured")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("play_to_first_frame_ms")).toDouble() > 0.0);
    const QJsonObject first_frame = frames.at(0).toObject();
    ASSERT_TRUE(!first_frame.value(QStringLiteral("raw_uint16_lj92_pred6_split_active")).toBool());
    ASSERT_TRUE(!first_frame.value(QStringLiteral("raw_uint16_lj92_pred6_split_requested")).toBool());
    ASSERT_TRUE(!first_frame.value(QStringLiteral("raw_uint16_lj92_generic_split_active")).toBool());
    ASSERT_TRUE(!first_frame.value(QStringLiteral("raw_uint16_lj92_generic_split_requested")).toBool());
    ASSERT_TRUE(!first_frame.value(QStringLiteral("raw_uint16_lj92_pred1_fast_path_measurement_requested")).toBool());
    ASSERT_TRUE(!first_frame.value(QStringLiteral("raw_uint16_lj92_pred1_fast_path_measurement_active")).toBool());
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_predictor")).toInt() >= -1);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred6_total_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred6_bitstream_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred6_predictor_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred6_other_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_generic_total_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_generic_bitstream_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_generic_predictor_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_generic_other_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred1_fast_path_total_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred1_fast_path_bitstream_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred1_fast_path_predictor_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred1_fast_path_other_ms")).toDouble() >= 0.0);

    bool saw_preview_rowscale_timing = false;
    for (const QJsonValue & value : frames) {
        ASSERT_TRUE(value.isObject());
        const QJsonObject sample = value.toObject();
        ASSERT_TRUE(sample.value(QStringLiteral("latency_ms")).toDouble() > 0.0);
        ASSERT_TRUE(sample.contains(QStringLiteral("engine_latency_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("presentation_overhead_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("draw_frame_ready_queue_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("draw_frame_ready_scene_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("draw_frame_ready_image_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("draw_frame_ready_present_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("draw_frame_ready_scopes_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("draw_frame_ready_overlay_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("draw_frame_ready_total_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("dual_iso_preview_histogram_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("dual_iso_preview_regression_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("dual_iso_preview_rowscale_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("raw_uint16_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("raw_uint16_disk_read_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("raw_uint16_decompress_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("raw_uint16_decompress_prepare_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("raw_uint16_decompress_execute_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("raw_uint16_unpack_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("raw_uint16_copy_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("raw_uint16_prefetch_hit")));
        ASSERT_TRUE(sample.contains(QStringLiteral("raw_uint16_other_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("llrawproc_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("llrawproc_total_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("llrawproc_dark_frame_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("llrawproc_vertical_stripes_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("llrawproc_focus_pixels_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("llrawproc_bad_pixels_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("llrawproc_pattern_noise_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("llrawproc_dual_iso_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("llrawproc_chroma_smooth_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("llrawproc_other_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("dual_iso_preview_total_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("debayered_frame_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("raw_float_convert_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("debayer_exclusive_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("debayer_wb_prepare_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("debayer_ca_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("debayer_kernel_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("debayer_wb_undo_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("debayer_pipeline_other_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("processing_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("processing_other_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("processing_core_other_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("processed16_total_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("processed16_for_8bit_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("processed16_to_8bit_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("processed8_total_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("processed8_direct_path_active")));
        ASSERT_TRUE(sample.contains(QStringLiteral("processed8_prefetch_hit")));
        ASSERT_TRUE(sample.contains(QStringLiteral("render_thread_queue_wait_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("render_thread_work_ms")));
        ASSERT_TRUE(sample.contains(QStringLiteral("render_thread_total_ms")));
        ASSERT_TRUE(sample.value(QStringLiteral("dual_iso_preview_histogram_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("dual_iso_preview_regression_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("dual_iso_preview_rowscale_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("raw_uint16_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("raw_uint16_disk_read_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("raw_uint16_decompress_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("raw_uint16_decompress_prepare_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("raw_uint16_decompress_execute_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("raw_uint16_unpack_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("raw_uint16_copy_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("raw_uint16_prefetch_hit")).isBool());
        ASSERT_TRUE(sample.value(QStringLiteral("raw_uint16_other_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("llrawproc_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("llrawproc_total_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("llrawproc_dark_frame_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("llrawproc_vertical_stripes_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("llrawproc_focus_pixels_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("llrawproc_bad_pixels_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("llrawproc_pattern_noise_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("llrawproc_dual_iso_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("llrawproc_chroma_smooth_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("llrawproc_other_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("dual_iso_preview_total_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("debayered_frame_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("raw_float_convert_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("debayer_exclusive_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("debayer_wb_prepare_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("debayer_ca_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("debayer_kernel_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("debayer_wb_undo_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("debayer_pipeline_other_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("processing_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("processed16_total_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("processed16_for_8bit_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("processed16_to_8bit_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("processed8_total_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("draw_frame_ready_queue_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("draw_frame_ready_scene_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("draw_frame_ready_image_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("draw_frame_ready_present_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("draw_frame_ready_scopes_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("draw_frame_ready_overlay_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("draw_frame_ready_total_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("processed8_direct_path_active")).isBool());
        ASSERT_TRUE(sample.value(QStringLiteral("processed8_prefetch_hit")).isBool());
        ASSERT_TRUE(sample.value(QStringLiteral("render_thread_queue_wait_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("render_thread_work_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("render_thread_total_ms")).toDouble() >= 0.0);
        double processing_max_substage_ms = 0.0;
        require_processing_timing_field(sample, "processing_setup_ms", &processing_max_substage_ms);
        require_processing_timing_field(sample, "processing_shadows_highlights_prep_ms", &processing_max_substage_ms);
        require_processing_timing_field(sample, "processing_highest_green_ms", &processing_max_substage_ms);
        require_processing_timing_field(sample, "processing_core_ms", &processing_max_substage_ms);
        require_processing_timing_field(sample, "processing_denoise_ms", &processing_max_substage_ms);
        require_processing_timing_field(sample, "processing_rbf_ms", &processing_max_substage_ms);
        require_processing_timing_field(sample, "processing_ca_ms", &processing_max_substage_ms);
        require_processing_timing_field(sample, "processing_core_levels_ms", &processing_max_substage_ms);
        require_processing_timing_field(sample, "processing_core_color_ms", &processing_max_substage_ms);
        require_processing_timing_field(sample, "processing_core_creative_ms", &processing_max_substage_ms);
        require_processing_timing_field(sample, "processing_core_output_ms", &processing_max_substage_ms);
        require_processing_timing_field(sample, "processing_other_ms", &processing_max_substage_ms);
        require_processing_timing_field(sample, "processing_core_other_ms", &processing_max_substage_ms);
        ASSERT_TRUE(sample.value(QStringLiteral("processing_ms")).toDouble() >= 0.0);
        ASSERT_TRUE(sample.value(QStringLiteral("processing_ms")).toDouble() >= processing_max_substage_ms);
        ASSERT_TRUE(sample.value(QStringLiteral("processed16_total_ms")).toDouble()
                    >= sample.value(QStringLiteral("processing_ms")).toDouble());
        ASSERT_TRUE(sample.value(QStringLiteral("llrawproc_total_ms")).toDouble()
                    >= sample.value(QStringLiteral("llrawproc_dual_iso_ms")).toDouble());
        ASSERT_TRUE(sample.value(QStringLiteral("debayered_frame_ms")).toDouble()
                    >= sample.value(QStringLiteral("raw_uint16_ms")).toDouble());
        ASSERT_TRUE(sample.value(QStringLiteral("raw_uint16_ms")).toDouble()
                    >= sample.value(QStringLiteral("raw_uint16_disk_read_ms")).toDouble());
        ASSERT_TRUE(sample.value(QStringLiteral("raw_uint16_ms")).toDouble()
                    >= sample.value(QStringLiteral("raw_uint16_decompress_ms")).toDouble());
        ASSERT_TRUE(sample.value(QStringLiteral("raw_uint16_ms")).toDouble()
                    >= sample.value(QStringLiteral("raw_uint16_unpack_ms")).toDouble());
        ASSERT_TRUE(sample.value(QStringLiteral("raw_uint16_ms")).toDouble()
                    >= sample.value(QStringLiteral("raw_uint16_copy_ms")).toDouble());
        ASSERT_TRUE(sample.value(QStringLiteral("debayered_frame_ms")).toDouble()
                    >= sample.value(QStringLiteral("llrawproc_ms")).toDouble());
        ASSERT_TRUE(sample.value(QStringLiteral("debayer_exclusive_ms")).toDouble()
                    >= sample.value(QStringLiteral("debayer_kernel_ms")).toDouble());
        ASSERT_TRUE(sample.value(QStringLiteral("processing_core_ms")).toDouble()
                    >= sample.value(QStringLiteral("processing_core_color_ms")).toDouble());
        ASSERT_TRUE(sample.value(QStringLiteral("processing_core_ms")).toDouble()
                    >= sample.value(QStringLiteral("processing_core_levels_ms")).toDouble());
        ASSERT_TRUE(sample.value(QStringLiteral("render_thread_total_ms")).toDouble()
                    >= sample.value(QStringLiteral("render_thread_work_ms")).toDouble());
        saw_preview_rowscale_timing = saw_preview_rowscale_timing
            || sample.value(QStringLiteral("dual_iso_preview_rowscale_ms")).toDouble() > 0.0;
        ASSERT_TRUE(sample.contains(QStringLiteral("playback_processing_subset_active")));
        ASSERT_TRUE(!sample.value(QStringLiteral("playback_processing_subset_active")).toBool());
        ASSERT_TRUE(sample.contains(QStringLiteral("gpu16_preview_active")));
        ASSERT_TRUE(sample.contains(QStringLiteral("gpu_preview_processing_active")));
        ASSERT_TRUE(sample.contains(QStringLiteral("gpu_bilinear_debayer_active")));
    }
    ASSERT_TRUE(saw_preview_rowscale_timing);

    ASSERT_TRUE(QFileInfo::exists(stage_log));
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfilePreviewReceiptStaysPreviewAtRuntime)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString receipt_path = clip_preview_receipt_path();
    if (!QFileInfo::exists(receipt_path)) {
        SKIP_TEST("Missing fixture receipt tests/fixtures/receipts/tiny_dual_iso_preview.marxml");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile-preview.json"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--receipt") << receipt_path
            << QStringLiteral("--frames") << QStringLiteral("2")
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--threads") << QStringLiteral("1"));
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(0, process.exitCode());

    QFile json_file(output_json);
    ASSERT_TRUE(json_file.open(QIODevice::ReadOnly | QIODevice::Text));
    const QJsonDocument document = QJsonDocument::fromJson(json_file.readAll());
    ASSERT_TRUE(document.isObject());

    const QJsonObject metadata = document.object().value(QStringLiteral("metadata")).toObject();
    ASSERT_EQ(2, metadata.value(QStringLiteral("dual_iso_mode_selected")).toInt());
    ASSERT_EQ(2, metadata.value(QStringLiteral("dual_iso_mode_effective")).toInt());
    ASSERT_TRUE(metadata.value(QStringLiteral("dual_iso_preview_runtime_active")).toBool());
    ASSERT_TRUE(!metadata.value(QStringLiteral("dual_iso_preview_override_active")).toBool());

    const QJsonArray frames = document.object().value(QStringLiteral("frames")).toArray();
    ASSERT_EQ(2, frames.size());
    for (const QJsonValue & value : frames) {
        const QJsonObject sample = value.toObject();
        ASSERT_TRUE(sample.contains(QStringLiteral("processed8_direct_path_active")));
        ASSERT_TRUE(sample.value(QStringLiteral("processed8_direct_path_active")).toBool());
    }
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfileCpuBackendProducesJson)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString receipt_path = clip_receipt_path();
    if (!QFileInfo::exists(receipt_path)) {
        SKIP_TEST("Missing fixture receipt tests/fixtures/receipts/tiny_dual_iso_hq.marxml");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile-cpu.json"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--receipt") << receipt_path
            << QStringLiteral("--frames") << QStringLiteral("2")
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--threads") << QStringLiteral("1")
            << QStringLiteral("--gpu-preview-processing") << QStringLiteral("cpu"));
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(0, process.exitCode());

    QFile json_file(output_json);
    ASSERT_TRUE(json_file.open(QIODevice::ReadOnly | QIODevice::Text));
    const QJsonDocument document = QJsonDocument::fromJson(json_file.readAll());
    ASSERT_TRUE(document.isObject());

    const QJsonObject metadata = document.object().value(QStringLiteral("metadata")).toObject();
    ASSERT_TRUE(metadata.value(QStringLiteral("gpu_preview_processing_backend_request")).toString()
                == QStringLiteral("cpu"));

    const QJsonArray frames = document.object().value(QStringLiteral("frames")).toArray();
    ASSERT_EQ(2, frames.size());
    for (const QJsonValue & value : frames) {
        const QJsonObject sample = value.toObject();
        ASSERT_TRUE(sample.contains(QStringLiteral("gpu_preview_processing_active")));
        ASSERT_TRUE(!sample.value(QStringLiteral("gpu_preview_processing_active")).toBool());
    }
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfileGpuBilinearDebayerCpuBackendProducesJson)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString receipt_path = clip_receipt_path();
    if (!QFileInfo::exists(receipt_path)) {
        SKIP_TEST("Missing fixture receipt tests/fixtures/receipts/tiny_dual_iso_hq.marxml");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile-debayer-cpu.json"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--receipt") << receipt_path
            << QStringLiteral("--frames") << QStringLiteral("2")
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--threads") << QStringLiteral("1")
            << QStringLiteral("--gpu-viewport")
            << QStringLiteral("--gpu-preview-processing") << QStringLiteral("gpu")
            << QStringLiteral("--gpu-bilinear-debayer") << QStringLiteral("cpu"));
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(0, process.exitCode());

    QFile json_file(output_json);
    ASSERT_TRUE(json_file.open(QIODevice::ReadOnly | QIODevice::Text));
    const QJsonDocument document = QJsonDocument::fromJson(json_file.readAll());
    ASSERT_TRUE(document.isObject());

    const QJsonObject metadata = document.object().value(QStringLiteral("metadata")).toObject();
    ASSERT_TRUE(metadata.value(QStringLiteral("gpu_preview_processing_backend_request")).toString()
                == QStringLiteral("gpu"));
    ASSERT_TRUE(metadata.value(QStringLiteral("gpu_bilinear_debayer_backend_request")).toString()
                == QStringLiteral("cpu"));
    ASSERT_TRUE(!metadata.value(QStringLiteral("gpu_bilinear_debayer_environment_requested")).toBool());

    const QJsonArray frames = document.object().value(QStringLiteral("frames")).toArray();
    ASSERT_EQ(2, frames.size());
    for (const QJsonValue & value : frames) {
        const QJsonObject sample = value.toObject();
        ASSERT_TRUE(sample.contains(QStringLiteral("gpu_bilinear_debayer_active")));
        ASSERT_TRUE(!sample.value(QStringLiteral("gpu_bilinear_debayer_active")).toBool());
    }
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfileGpuBilinearDebayerGpuBackendProducesJson)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString receipt_path = clip_receipt_path();
    if (!QFileInfo::exists(receipt_path)) {
        SKIP_TEST("Missing fixture receipt tests/fixtures/receipts/tiny_dual_iso_hq.marxml");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile-debayer-gpu.json"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--receipt") << receipt_path
            << QStringLiteral("--frames") << QStringLiteral("2")
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--threads") << QStringLiteral("1")
            << QStringLiteral("--gpu-viewport")
            << QStringLiteral("--gpu-preview-processing") << QStringLiteral("gpu")
            << QStringLiteral("--gpu-bilinear-debayer") << QStringLiteral("gpu"));
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(0, process.exitCode());

    QFile json_file(output_json);
    ASSERT_TRUE(json_file.open(QIODevice::ReadOnly | QIODevice::Text));
    const QJsonDocument document = QJsonDocument::fromJson(json_file.readAll());
    ASSERT_TRUE(document.isObject());

    const QJsonObject metadata = document.object().value(QStringLiteral("metadata")).toObject();
    ASSERT_TRUE(metadata.value(QStringLiteral("gpu_preview_processing_backend_request")).toString()
                == QStringLiteral("gpu"));
    ASSERT_TRUE(metadata.value(QStringLiteral("gpu_bilinear_debayer_backend_request")).toString()
                == QStringLiteral("gpu"));
    ASSERT_TRUE(!metadata.value(QStringLiteral("gpu_bilinear_debayer_environment_requested")).toBool());
    ASSERT_TRUE(metadata.contains(QStringLiteral("gpu_bilinear_debayer_probe_available")));
    ASSERT_TRUE(metadata.contains(QStringLiteral("gpu_bilinear_debayer_probe_reason")));
    ASSERT_TRUE(metadata.contains(QStringLiteral("gpu_bilinear_debayer_probe_renderer")));
    ASSERT_TRUE(metadata.value(QStringLiteral("qt_opengl_environment")).toString()
                == QStringLiteral("desktop"));
    ASSERT_TRUE(metadata.value(QStringLiteral("qt_qpa_platform_environment")).toString()
                == QStringLiteral("windows"));

    const QJsonArray frames = document.object().value(QStringLiteral("frames")).toArray();
    ASSERT_EQ(2, frames.size());
    for (const QJsonValue & value : frames) {
        const QJsonObject sample = value.toObject();
        ASSERT_TRUE(sample.contains(QStringLiteral("gpu_bilinear_debayer_active")));
        if (sample.contains(QStringLiteral("gpu_bilinear_debayer_fallback_reason"))) {
            ASSERT_TRUE(sample.contains(QStringLiteral("gpu_bilinear_debayer_renderer")));
        }
    }
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfileAmazeCachedDebayerProducesJson)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString receipt_path = clip_receipt_path();
    if (!QFileInfo::exists(receipt_path)) {
        SKIP_TEST("Missing fixture receipt tests/fixtures/receipts/tiny_dual_iso_hq.marxml");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile-amaze-cached.json"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--receipt") << receipt_path
            << QStringLiteral("--frames") << QStringLiteral("2")
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--threads") << QStringLiteral("1")
            << QStringLiteral("--raw-cache-mb") << QStringLiteral("128")
            << QStringLiteral("--cache-cpu-cores") << QStringLiteral("1")
            << QStringLiteral("--playback-debayer") << QStringLiteral("amaze-cached"));
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(0, process.exitCode());

    QFile json_file(output_json);
    ASSERT_TRUE(json_file.open(QIODevice::ReadOnly | QIODevice::Text));
    const QJsonDocument document = QJsonDocument::fromJson(json_file.readAll());
    ASSERT_TRUE(document.isObject());

    const QJsonObject metadata = document.object().value(QStringLiteral("metadata")).toObject();
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_policy_active")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_debayer_request")).toString()
                == QStringLiteral("amaze-cached"));
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_debayer_effective")).toString()
                == QStringLiteral("amaze-cached"));
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_debayer_uses_caching")).toBool());
    ASSERT_EQ(1, metadata.value(QStringLiteral("playback_debayer_engine_mode")).toInt());
    ASSERT_EQ(128, metadata.value(QStringLiteral("raw_cache_mb")).toInt());
    ASSERT_TRUE(metadata.value(QStringLiteral("play_start_preroll_active")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("play_start_preroll_eligible")).toBool());
    ASSERT_TRUE(!metadata.value(QStringLiteral("play_start_preroll_disabled_by_environment")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("play_to_first_frame_measured")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("play_to_first_frame_ms")).toDouble() > 0.0);
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfileAmazeCachedCanDisablePlayStartPrerollViaEnvironment)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString receipt_path = clip_receipt_path();
    if (!QFileInfo::exists(receipt_path)) {
        SKIP_TEST("Missing fixture receipt tests/fixtures/receipts/tiny_dual_iso_hq.marxml");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile-amaze-cached-preroll-disabled.json"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--receipt") << receipt_path
            << QStringLiteral("--frames") << QStringLiteral("2")
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--threads") << QStringLiteral("1")
            << QStringLiteral("--raw-cache-mb") << QStringLiteral("128")
            << QStringLiteral("--cache-cpu-cores") << QStringLiteral("1")
            << QStringLiteral("--playback-debayer") << QStringLiteral("amaze-cached"),
        {{QStringLiteral("MLVAPP_DISABLE_PLAY_START_PREROLL"), QStringLiteral("1")}});
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(0, process.exitCode());

    QFile json_file(output_json);
    ASSERT_TRUE(json_file.open(QIODevice::ReadOnly | QIODevice::Text));
    const QJsonDocument document = QJsonDocument::fromJson(json_file.readAll());
    ASSERT_TRUE(document.isObject());

    const QJsonObject metadata = document.object().value(QStringLiteral("metadata")).toObject();
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_policy_active")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_debayer_effective")).toString()
                == QStringLiteral("amaze-cached"));
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_debayer_uses_caching")).toBool());
    ASSERT_TRUE(!metadata.value(QStringLiteral("play_start_preroll_active")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("play_start_preroll_eligible")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("play_start_preroll_disabled_by_environment")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("play_to_first_frame_measured")).toBool());
    ASSERT_TRUE(metadata.value(QStringLiteral("play_to_first_frame_ms")).toDouble() > 0.0);
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfileCanEnableLj92Pred6SplitViaEnvironment)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString receipt_path = clip_receipt_path();
    if (!QFileInfo::exists(receipt_path)) {
        SKIP_TEST("Missing fixture receipt tests/fixtures/receipts/tiny_dual_iso_hq.marxml");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile-lj92-pred6-split.json"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--receipt") << receipt_path
            << QStringLiteral("--frames") << QStringLiteral("2")
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--threads") << QStringLiteral("1"),
        {{QStringLiteral("MLVAPP_PROFILE_LJ92_PRED6_SPLIT"), QStringLiteral("1")}});
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(0, process.exitCode());

    QFile json_file(output_json);
    ASSERT_TRUE(json_file.open(QIODevice::ReadOnly | QIODevice::Text));
    const QJsonDocument document = QJsonDocument::fromJson(json_file.readAll());
    ASSERT_TRUE(document.isObject());

    const QJsonArray frames = document.object().value(QStringLiteral("frames")).toArray();
    ASSERT_TRUE(!frames.isEmpty());
    const QJsonObject first_frame = first_frame_with_raw_decode_telemetry(frames);
    ASSERT_TRUE(!first_frame.isEmpty());
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred6_split_requested")).toBool());
    const int predictor = first_frame.value(QStringLiteral("raw_uint16_lj92_predictor")).toInt(-1);
    ASSERT_TRUE(predictor >= 0);
    ASSERT_TRUE(predictor <= 7);
    if (predictor == 6) {
        ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred6_split_active")).toBool());
    } else {
        ASSERT_TRUE(!first_frame.value(QStringLiteral("raw_uint16_lj92_pred6_split_active")).toBool());
    }
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred6_total_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred6_bitstream_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred6_predictor_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred6_other_ms")).toDouble() >= 0.0);
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfileCanEnableLj92GenericSplitViaEnvironment)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString receipt_path = clip_receipt_path();
    if (!QFileInfo::exists(receipt_path)) {
        SKIP_TEST("Missing fixture receipt tests/fixtures/receipts/tiny_dual_iso_hq.marxml");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile-lj92-generic-split.json"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--receipt") << receipt_path
            << QStringLiteral("--frames") << QStringLiteral("2")
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--threads") << QStringLiteral("1"),
        {{QStringLiteral("MLVAPP_PROFILE_LJ92_GENERIC_SPLIT"), QStringLiteral("1")}});
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(0, process.exitCode());

    QFile json_file(output_json);
    ASSERT_TRUE(json_file.open(QIODevice::ReadOnly | QIODevice::Text));
    const QJsonDocument document = QJsonDocument::fromJson(json_file.readAll());
    ASSERT_TRUE(document.isObject());

    const QJsonArray frames = document.object().value(QStringLiteral("frames")).toArray();
    ASSERT_TRUE(!frames.isEmpty());
    const QJsonObject first_frame = first_frame_with_raw_decode_telemetry(frames);
    ASSERT_TRUE(!first_frame.isEmpty());
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_generic_split_requested")).toBool());
    const int predictor = first_frame.value(QStringLiteral("raw_uint16_lj92_predictor")).toInt(-1);
    ASSERT_TRUE(predictor >= 0);
    ASSERT_TRUE(predictor <= 7);
    if (predictor == 6) {
        ASSERT_TRUE(!first_frame.value(QStringLiteral("raw_uint16_lj92_generic_split_active")).toBool());
    } else {
        ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_generic_split_active")).toBool());
    }
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_generic_total_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_generic_bitstream_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_generic_predictor_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_generic_other_ms")).toDouble() >= 0.0);
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfileReceiptProcessingProducesJson)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString receipt_path = clip_receipt_path();
    if (!QFileInfo::exists(receipt_path)) {
        SKIP_TEST("Missing fixture receipt tests/fixtures/receipts/tiny_dual_iso_hq.marxml");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile-processing-receipt.json"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--receipt") << receipt_path
            << QStringLiteral("--frames") << QStringLiteral("2")
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--threads") << QStringLiteral("1")
            << QStringLiteral("--playback-processing") << QStringLiteral("receipt"));
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(0, process.exitCode());

    QFile json_file(output_json);
    ASSERT_TRUE(json_file.open(QIODevice::ReadOnly | QIODevice::Text));
    const QJsonDocument document = QJsonDocument::fromJson(json_file.readAll());
    ASSERT_TRUE(document.isObject());

    const QJsonObject metadata = document.object().value(QStringLiteral("metadata")).toObject();
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_processing_request")).toString()
                == QStringLiteral("receipt"));
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_processing_selected")).toString()
                == QStringLiteral("receipt"));
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_processing_effective")).toString()
                == QStringLiteral("receipt"));

    const QJsonArray frames = document.object().value(QStringLiteral("frames")).toArray();
    ASSERT_EQ(2, frames.size());
    for (const QJsonValue & value : frames) {
        const QJsonObject sample = value.toObject();
        ASSERT_TRUE(sample.contains(QStringLiteral("playback_processing_subset_active")));
        ASSERT_TRUE(!sample.value(QStringLiteral("playback_processing_subset_active")).toBool());
    }
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfilePred1FastPathMeasurementProducesJson)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString receipt_path = clip_preview_receipt_path();
    if (!QFileInfo::exists(receipt_path)) {
        SKIP_TEST("Missing receipt tests/fixtures/receipts/tiny_dual_iso_preview.marxml");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile-pred1-fast-path-measurement.json"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--receipt") << receipt_path
            << QStringLiteral("--frames") << QStringLiteral("2")
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--threads") << QStringLiteral("1"),
        {{QStringLiteral("MLVAPP_PRED1_FASTPATH_MEASUREMENT"), QStringLiteral("1")}});
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(0, process.exitCode());

    QFile json_file(output_json);
    ASSERT_TRUE(json_file.open(QIODevice::ReadOnly | QIODevice::Text));
    const QJsonDocument document = QJsonDocument::fromJson(json_file.readAll());
    ASSERT_TRUE(document.isObject());

    const QJsonArray frames = document.object().value(QStringLiteral("frames")).toArray();
    ASSERT_TRUE(!frames.isEmpty());
    const QJsonObject first_frame = first_frame_with_raw_decode_telemetry(frames);
    ASSERT_TRUE(!first_frame.isEmpty());
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred1_fast_path_measurement_requested")).toBool());
    ASSERT_TRUE(!first_frame.value(QStringLiteral("raw_uint16_lj92_generic_split_requested")).toBool());
    const int predictor = first_frame.value(QStringLiteral("raw_uint16_lj92_predictor")).toInt(-1);
    ASSERT_TRUE(predictor >= 0);
    ASSERT_TRUE(predictor <= 7);
    const bool fast_path_active =
        first_frame.value(QStringLiteral("raw_uint16_lj92_pred1_fast_path_active")).toBool();
    const bool measurement_active =
        first_frame.value(QStringLiteral("raw_uint16_lj92_pred1_fast_path_measurement_active")).toBool();
    ASSERT_EQ(1, predictor);
    ASSERT_TRUE(fast_path_active);
    ASSERT_TRUE(measurement_active);
    ASSERT_TRUE(measurement_active == fast_path_active);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred1_fast_path_total_ms")).toDouble() > 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred1_fast_path_bitstream_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred1_fast_path_predictor_ms")).toDouble() >= 0.0);
    ASSERT_TRUE(first_frame.value(QStringLiteral("raw_uint16_lj92_pred1_fast_path_other_ms")).toDouble() >= 0.0);
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfileSubsetProcessingProducesJson)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString receipt_path = clip_receipt_path();
    if (!QFileInfo::exists(receipt_path)) {
        SKIP_TEST("Missing fixture receipt tests/fixtures/receipts/tiny_dual_iso_hq.marxml");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile-processing-subset.json"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--receipt") << receipt_path
            << QStringLiteral("--frames") << QStringLiteral("2")
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--threads") << QStringLiteral("1")
            << QStringLiteral("--playback-processing") << QStringLiteral("subset"));
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(0, process.exitCode());

    QFile json_file(output_json);
    ASSERT_TRUE(json_file.open(QIODevice::ReadOnly | QIODevice::Text));
    const QJsonDocument document = QJsonDocument::fromJson(json_file.readAll());
    ASSERT_TRUE(document.isObject());

    const QJsonObject metadata = document.object().value(QStringLiteral("metadata")).toObject();
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_processing_request")).toString()
                == QStringLiteral("subset"));
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_processing_selected")).toString()
                == QStringLiteral("subset"));
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_processing_effective")).toString()
                == QStringLiteral("subset"));
    ASSERT_TRUE(metadata.value(QStringLiteral("playback_processing_supported")).toBool());

    const QJsonArray frames = document.object().value(QStringLiteral("frames")).toArray();
    ASSERT_EQ(2, frames.size());
    for (const QJsonValue & value : frames) {
        const QJsonObject sample = value.toObject();
        ASSERT_TRUE(sample.contains(QStringLiteral("playback_processing_subset_active")));
        ASSERT_TRUE(sample.value(QStringLiteral("playback_processing_subset_active")).toBool());
    }
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfileRejectsInvalidGpuPreviewProcessingBackend)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile-invalid.json"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--gpu-preview-processing") << QStringLiteral("bogus"));
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(2, process.exitCode());

    const QString stderr_output = QString::fromLocal8Bit(process.readAllStandardError());
    ASSERT_TRUE(stderr_output.contains(
        QStringLiteral("--gpu-preview-processing must be one of auto, cpu, gpu")));
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfileRejectsInvalidGpuBilinearDebayerBackend)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile-invalid-debayer.json"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--gpu-bilinear-debayer") << QStringLiteral("bogus"));
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(2, process.exitCode());

    const QString stderr_output = QString::fromLocal8Bit(process.readAllStandardError());
    ASSERT_TRUE(stderr_output.contains(
        QStringLiteral("--gpu-bilinear-debayer must be one of auto, cpu, gpu")));
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfileRejectsInvalidPlaybackDebayer)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile-invalid-playback-debayer.json"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--playback-debayer") << QStringLiteral("bogus"));
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(2, process.exitCode());

    const QString stderr_output = QString::fromLocal8Bit(process.readAllStandardError());
    ASSERT_TRUE(stderr_output.contains(
        QStringLiteral("--playback-debayer must be one of auto, receipt, none, simple, bilinear, lmmse, igv, amaze, ahd, rcd, dcb, amaze-cached")));
}

TEST(ClipGolden, TinyDualIsoHeadlessPlaybackProfileRejectsInvalidPlaybackProcessing)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString app_exe = app_executable_path();
    if (app_exe.isEmpty() || !QFileInfo::exists(app_exe)) {
        SKIP_TEST("Set MLVAPP_PROFILE_EXE or MLVAPP_BATCH_EXE to a built MLVApp binary");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_json = temp_dir.filePath(QStringLiteral("playback-profile-invalid-processing.json"));

    QProcess process;
    configure_playback_profile_process(
        &process,
        app_exe,
        repo_root,
        QStringList()
            << QStringLiteral("--profile-playback")
            << QStringLiteral("--input") << fixture_path
            << QStringLiteral("--output") << output_json
            << QStringLiteral("--playback-processing") << QStringLiteral("bogus"));
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(2, process.exitCode());

    const QString stderr_output = QString::fromLocal8Bit(process.readAllStandardError());
    ASSERT_TRUE(stderr_output.contains(
        QStringLiteral("--playback-processing must be one of auto, receipt, subset")));
}
