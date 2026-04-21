#include "../common/repo_paths.h"
#include "../common/test_runtime.h"
#include "../../platform/qt/ReceiptSettings.h"
#include "../../src/batch/ReceiptApplier.h"
#include "../../src/batch/ReceiptLoader.h"

extern "C" {
#include "../../src/mlv/video_mlv.h"
#include "../../src/mlv/macros.h"
}

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonArray>
#include <QJsonObject>
#include <QSysInfo>
#include <QTextStream>
#include <QVector>

#include <algorithm>
#include <vector>

namespace {

struct PerfMetric
{
    double average_ms = 0.0;
    double median_ms = 0.0;
    double min_ms = 0.0;
    double max_ms = 0.0;
    double fps = 0.0;
    int frames = 0;
    int iterations = 0;
};

struct BaselineContext
{
    QJsonObject absolute_guards;
    QJsonObject relative_guards;
    QJsonObject profile_object;
    QJsonObject profile_results;
    QString profile_key;
    QString profile_label;
    double regression_pct = 15.0;
    bool profile_found = false;
    bool profile_threads_match = true;
    int profile_threads = 0;
};

struct ProfileMetricSpec
{
    QString metric_key;
    QString field_key;
    QString label;
};

struct FixtureSpec
{
    QString key;
    QString label;
    QString clip_path;
    QString receipt_path;
    int sample_frames = 2;
};

struct FixtureRun
{
    FixtureSpec spec;
    PerfMetric full16;
    PerfMetric preview16;
    PerfMetric full8;
    PerfMetric preview8;
    double preview16_speedup = 0.0;
    double preview8_speedup = 0.0;
    int total_frames = 0;
    int sample_frames_used = 0;
};

class PerfPipelineFixture
{
public:
    PerfPipelineFixture()
        : m_video(nullptr)
        , m_processing(nullptr)
    {
    }

    ~PerfPipelineFixture()
    {
        if( m_video )
        {
            freeMlvObject(m_video);
            m_video = nullptr;
        }
        if( m_processing )
        {
            freeProcessingObject(m_processing);
            m_processing = nullptr;
        }
    }

    bool openClip(const QString & clip_path, QString * error_message)
    {
        QByteArray clip_bytes = clip_path.toLocal8Bit();
        char open_error[256] = { 0 };
        int open_code = MLV_ERR_NONE;

        m_video = initMlvObjectWithClip(clip_bytes.data(), MLV_OPEN_FULL, &open_code, open_error);
        if( open_code != MLV_ERR_NONE || !m_video )
        {
            if( error_message )
            {
                *error_message = QStringLiteral("Failed to open clip %1: %2")
                    .arg(clip_path, QString::fromLocal8Bit(open_error));
            }
            return false;
        }

        m_processing = initProcessingObject();
        setMlvProcessing(m_video, m_processing);
        resetSingleThreadedRuntime();
        return true;
    }

    bool loadReceipt(const QString & receipt_path, QString * error_message)
    {
        return ReceiptLoader::loadFromFile(receipt_path, &m_receipt, error_message);
    }

    bool applyReceipt(QString * error_message)
    {
        if( !m_video || !m_processing )
        {
            if( error_message ) *error_message = QStringLiteral("Fixture is not open.");
            return false;
        }

        ReceiptApplier::applyToMlv(&m_receipt, m_video, m_processing);
        applyDebayerSelection();
        resetSingleThreadedRuntime();
        return true;
    }

    std::vector<uint16_t> renderFrame16(uint64_t frame_index, int threads) const
    {
        std::vector<uint16_t> frame(static_cast<std::size_t>(width()) * static_cast<std::size_t>(height()) * 3u);
        getMlvProcessedFrame16(m_video, frame_index, frame.data(), threads);
        return frame;
    }

    std::vector<uint8_t> renderFrame8(uint64_t frame_index, int threads) const
    {
        std::vector<uint8_t> frame(static_cast<std::size_t>(width()) * static_cast<std::size_t>(height()) * 3u);
        getMlvProcessedFrame8(m_video, frame_index, frame.data(), threads);
        return frame;
    }

    ReceiptSettings & receipt() { return m_receipt; }
    int frameCount() const { return static_cast<int>(getMlvFrames(m_video)); }
    int width() const { return getMlvWidth(m_video); }
    int height() const { return getMlvHeight(m_video); }

private:
    void applyDebayerSelection()
    {
        switch( m_receipt.debayer() )
        {
        case ReceiptSettings::None:
            setMlvUseNoneDebayer(m_video);
            break;
        case ReceiptSettings::Simple:
            setMlvUseSimpleDebayer(m_video);
            break;
        case ReceiptSettings::Bilinear:
            setMlvDontAlwaysUseAmaze(m_video);
            break;
        case ReceiptSettings::LMMSE:
            setMlvUseLmmseDebayer(m_video);
            break;
        case ReceiptSettings::IGV:
            setMlvUseIgvDebayer(m_video);
            break;
        case ReceiptSettings::AMaZE:
            setMlvAlwaysUseAmaze(m_video);
            break;
        case ReceiptSettings::AHD:
            setMlvUseAhdDebayer(m_video);
            break;
        case ReceiptSettings::RCD:
            setMlvUseRcdDebayer(m_video);
            break;
        case ReceiptSettings::DCB:
            setMlvUseDcbDebayer(m_video);
            break;
        }
    }

    void resetSingleThreadedRuntime()
    {
        disableMlvCaching(m_video);
        setMlvCpuCores(m_video, 1);
        m_video->cache_next = 0;
    }

    mlvObject_t * m_video;
    processingObject_t * m_processing;
    ReceiptSettings m_receipt;
};

bool write_json(const QString & path, const QJsonObject & object, QString * error_message)
{
    QFile output(path);
    if( !output.open(QIODevice::WriteOnly | QIODevice::Truncate) )
    {
        if( error_message ) *error_message = QStringLiteral("Failed to open %1 for writing").arg(path);
        return false;
    }

    output.write(QJsonDocument(object).toJson(QJsonDocument::Indented));
    return true;
}

QJsonObject load_json_object(const QString & path)
{
    QFile input(path);
    if( !input.open(QIODevice::ReadOnly) ) return {};

    const QJsonDocument doc = QJsonDocument::fromJson(input.readAll());
    if( !doc.isObject() ) return {};
    return doc.object();
}

QString sanitize_key_fragment(const QString & value)
{
    QString sanitized;
    sanitized.reserve(value.size());
    for( const QChar ch : value )
    {
        if( ch.isLetterOrNumber() ) sanitized.append(ch.toLower());
        else if( !sanitized.endsWith(QLatin1Char('_')) ) sanitized.append(QLatin1Char('_'));
    }

    while( sanitized.endsWith(QLatin1Char('_')) ) sanitized.chop(1);
    if( sanitized.isEmpty() ) sanitized = QStringLiteral("unknown");
    return sanitized;
}

QString default_profile_key()
{
    const QByteArray machine_name = qEnvironmentVariable("COMPUTERNAME").toUtf8();
    const QByteArray processor_id = qEnvironmentVariable("PROCESSOR_IDENTIFIER").toUtf8();
    QByteArray fingerprint;
    fingerprint += QSysInfo::productType().toUtf8();
    fingerprint += '|';
    fingerprint += QSysInfo::currentCpuArchitecture().toUtf8();
    fingerprint += '|';
    fingerprint += QByteArray(qVersion());
    fingerprint += '|';
    fingerprint += machine_name;
    fingerprint += '|';
    fingerprint += processor_id;

    const QByteArray digest = QCryptographicHash::hash(fingerprint, QCryptographicHash::Sha256).toHex().left(12);
    return QStringLiteral("auto.%1.%2.%3")
        .arg(sanitize_key_fragment(QSysInfo::productType()))
        .arg(sanitize_key_fragment(QSysInfo::currentCpuArchitecture()))
        .arg(QString::fromLatin1(digest));
}

QString default_profile_label()
{
    return QStringLiteral("Auto local baseline (%1, %2, Qt %3)")
        .arg(QSysInfo::prettyProductName())
        .arg(QSysInfo::currentCpuArchitecture())
        .arg(QString::fromLatin1(qVersion()));
}

double average_of_samples(const QVector<double> & samples)
{
    if( samples.isEmpty() ) return 0.0;

    double total = 0.0;
    for( const double sample : samples ) total += sample;
    return total / static_cast<double>(samples.size());
}

double median_of_samples(QVector<double> samples)
{
    if( samples.isEmpty() ) return 0.0;

    std::sort(samples.begin(), samples.end());
    const int middle = samples.size() / 2;
    if( (samples.size() % 2) == 0 ) return (samples[middle - 1] + samples[middle]) / 2.0;
    return samples[middle];
}

QString resolve_perf_path(const QString & path)
{
    if( path.isEmpty() ) return {};
    const QFileInfo info(path);
    if( info.isAbsolute() ) return QDir::cleanPath(path);
    return repo_file_path(path);
}

QString default_fixture_label_for_path(const QString & clip_path)
{
    return sanitize_key_fragment(QFileInfo(clip_path).completeBaseName());
}

QVector<ProfileMetricSpec> profile_metric_specs_for_fixtures(const QVector<FixtureSpec> & fixtures)
{
    QVector<ProfileMetricSpec> specs;
    specs.reserve(fixtures.size() * 2);
    for( const FixtureSpec & fixture : fixtures )
    {
        specs.push_back({fixture.key + QStringLiteral(".full16"), QStringLiteral("median_ms"), fixture.key + QStringLiteral(".full16.median_ms")});
        specs.push_back({fixture.key + QStringLiteral(".preview16"), QStringLiteral("median_ms"), fixture.key + QStringLiteral(".preview16.median_ms")});
    }
    return specs;
}

QString conventional_extra_clip_path()
{
    const QString path = repo_file_path(QStringLiteral("tests/fixtures/clips/large_dual_iso.mlv"));
    return QFileInfo::exists(path) ? path : QString();
}

QString conventional_extra_receipt_path(const QString & label)
{
    const QString preferred = repo_file_path(QStringLiteral("tests/fixtures/receipts/%1_hq.marxml").arg(label));
    if( QFileInfo::exists(preferred) ) return preferred;

    const QString tiny = repo_file_path(QStringLiteral("tests/fixtures/receipts/tiny_dual_iso_hq.marxml"));
    return QFileInfo::exists(tiny) ? tiny : QString();
}

bool fixture_exists(const FixtureSpec & spec)
{
    return QFileInfo::exists(spec.clip_path) && QFileInfo::exists(spec.receipt_path);
}

PerfMetric benchmark_spec(const FixtureSpec & spec,
                          bool preview_mode,
                          bool render_8bit,
                          int iterations,
                          int threads,
                          int * total_frames_out,
                          int * sample_frames_used_out,
                          QString * error_message)
{
    PerfPipelineFixture fixture;
    if( !fixture.openClip(spec.clip_path, error_message) ) return {};
    if( !fixture.loadReceipt(spec.receipt_path, error_message) ) return {};

    if( preview_mode )
    {
        fixture.receipt().setDualIso(2);
        fixture.receipt().setDualIsoInterpolation(1);
        fixture.receipt().setDualIsoAliasMap(0);
        fixture.receipt().setDualIsoFrBlending(0);
    }

    if( !fixture.applyReceipt(error_message) ) return {};

    const int total_frames = std::max(1, fixture.frameCount());
    const int sample_frames = std::max(1, std::min(spec.sample_frames, total_frames));
    if( total_frames_out ) *total_frames_out = total_frames;
    if( sample_frames_used_out ) *sample_frames_used_out = sample_frames;

    for( int warmup = 0; warmup < sample_frames; ++warmup )
    {
        if( render_8bit ) fixture.renderFrame8(static_cast<uint64_t>(warmup), threads);
        else fixture.renderFrame16(static_cast<uint64_t>(warmup), threads);
    }

    QVector<double> samples_ms;
    samples_ms.reserve(iterations);
    for( int i = 0; i < iterations; ++i )
    {
        const uint64_t frame_index = static_cast<uint64_t>(i % sample_frames);
        QElapsedTimer timer;
        timer.start();
        if( render_8bit ) fixture.renderFrame8(frame_index, threads);
        else fixture.renderFrame16(frame_index, threads);
        samples_ms.push_back(static_cast<double>(timer.nsecsElapsed()) / 1000000.0);
    }

    PerfMetric metric;
    metric.iterations = iterations;
    metric.frames = iterations;
    metric.average_ms = average_of_samples(samples_ms);
    metric.median_ms = median_of_samples(samples_ms);
    if( !samples_ms.isEmpty() )
    {
        const auto bounds = std::minmax_element(samples_ms.begin(), samples_ms.end());
        metric.min_ms = *bounds.first;
        metric.max_ms = *bounds.second;
    }
    metric.fps = metric.average_ms > 0.0 ? 1000.0 / metric.average_ms : 0.0;
    return metric;
}

bool prime_benchmark_modes(const FixtureSpec & spec, int threads, QString * error_message)
{
    const int prime_iterations = 2;
    if( !error_message ) return false;
    error_message->clear();

    int total_frames = 0;
    int sample_frames = 0;

    benchmark_spec(spec, false, false, prime_iterations, threads, &total_frames, &sample_frames, error_message);
    if( !error_message->isEmpty() ) return false;
    benchmark_spec(spec, true, false, prime_iterations, threads, &total_frames, &sample_frames, error_message);
    if( !error_message->isEmpty() ) return false;
    benchmark_spec(spec, false, true, prime_iterations, threads, &total_frames, &sample_frames, error_message);
    if( !error_message->isEmpty() ) return false;
    benchmark_spec(spec, true, true, prime_iterations, threads, &total_frames, &sample_frames, error_message);
    return error_message->isEmpty();
}

void record_metric(QJsonObject * target, const QString & key, const PerfMetric & metric)
{
    QJsonObject value;
    value.insert(QStringLiteral("average_ms"), metric.average_ms);
    value.insert(QStringLiteral("median_ms"), metric.median_ms);
    value.insert(QStringLiteral("min_ms"), metric.min_ms);
    value.insert(QStringLiteral("max_ms"), metric.max_ms);
    value.insert(QStringLiteral("fps"), metric.fps);
    value.insert(QStringLiteral("iterations"), metric.iterations);
    value.insert(QStringLiteral("frames"), metric.frames);
    target->insert(key, value);
}

QJsonObject extract_relative_guards(const QJsonObject & root)
{
    const QJsonValue explicit_guards = root.value(QStringLiteral("relative_guards"));
    if( explicit_guards.isObject() ) return explicit_guards.toObject();

    QJsonObject guards;
    for( auto it = root.begin(); it != root.end(); ++it )
    {
        if( it.value().isDouble() ) guards.insert(it.key(), it.value());
    }
    return guards;
}

QJsonObject extract_absolute_guards(const QJsonObject & root)
{
    const QJsonValue explicit_guards = root.value(QStringLiteral("absolute_guards"));
    if( explicit_guards.isObject() ) return explicit_guards.toObject();
    return {};
}

BaselineContext load_baseline_context(const QString & path, const QString & profile_key, int threads)
{
    BaselineContext context;
    context.profile_key = profile_key;

    const QJsonObject root = load_json_object(path);
    if( root.isEmpty() ) return context;

    if( root.contains(QStringLiteral("default_regression_pct")) && root.value(QStringLiteral("default_regression_pct")).isDouble() )
    {
        context.regression_pct = root.value(QStringLiteral("default_regression_pct")).toDouble();
    }
    context.absolute_guards = extract_absolute_guards(root);
    context.relative_guards = extract_relative_guards(root);

    const QJsonValue profiles_value = root.value(QStringLiteral("profiles"));
    if( !profiles_value.isObject() ) return context;

    const QJsonObject profiles = profiles_value.toObject();
    const QJsonValue profile_value = profiles.value(profile_key);
    if( !profile_value.isObject() ) return context;

    context.profile_object = profile_value.toObject();
    context.profile_label = context.profile_object.value(QStringLiteral("label")).toString();
    context.profile_results = context.profile_object.value(QStringLiteral("results")).toObject();
    context.profile_found = !context.profile_results.isEmpty();

    if( context.profile_object.contains(QStringLiteral("threads")) && context.profile_object.value(QStringLiteral("threads")).isDouble() )
    {
        context.profile_threads = context.profile_object.value(QStringLiteral("threads")).toInt();
        context.profile_threads_match = (context.profile_threads == threads);
    }

    return context;
}

bool check_baseline_metric(const QJsonObject & baseline_results,
                           const QString & metric_key,
                           const QString & field_key,
                           const QString & label,
                           double current_value,
                           double regression_pct,
                           QTextStream * out,
                           QJsonObject * checks)
{
    const QJsonObject baseline_metric = baseline_results.value(metric_key).toObject();
    if( !baseline_metric.contains(field_key) || !baseline_metric.value(field_key).isDouble() ) return true;

    const double baseline_value = baseline_metric.value(field_key).toDouble();
    if( baseline_value <= 0.0 ) return true;

    const double limit = baseline_value * (1.0 + (regression_pct / 100.0));
    const double delta_pct = ((current_value - baseline_value) / baseline_value) * 100.0;
    const bool okay = current_value <= limit;

    if( out )
    {
        *out << (okay ? "[PASS] " : "[FAIL] ")
             << label
             << " current=" << current_value
             << "ms baseline=" << baseline_value
             << "ms limit=" << limit
             << "ms delta=" << delta_pct << "%\n";
    }

    if( checks )
    {
        QJsonObject check;
        check.insert(QStringLiteral("kind"), QStringLiteral("baseline_max"));
        check.insert(QStringLiteral("status"), okay ? QStringLiteral("pass") : QStringLiteral("fail"));
        check.insert(QStringLiteral("current"), current_value);
        check.insert(QStringLiteral("baseline"), baseline_value);
        check.insert(QStringLiteral("limit"), limit);
        check.insert(QStringLiteral("delta_pct"), delta_pct);
        checks->insert(label, check);
    }

    return okay;
}

bool check_minimum(const QJsonObject & baselines,
                   const QString & key,
                   double current_value,
                   QTextStream * out,
                   QJsonObject * checks)
{
    if( !baselines.contains(key) || !baselines.value(key).isDouble() ) return true;

    const double minimum = baselines.value(key).toDouble();
    const bool okay = current_value >= minimum;
    if( out )
    {
        *out << (okay ? "[PASS] " : "[FAIL] ")
             << key << " current=" << current_value
             << " required=" << minimum << '\n';
    }

    if( checks )
    {
        QJsonObject check;
        check.insert(QStringLiteral("kind"), QStringLiteral("minimum"));
        check.insert(QStringLiteral("status"), okay ? QStringLiteral("pass") : QStringLiteral("fail"));
        check.insert(QStringLiteral("current"), current_value);
        check.insert(QStringLiteral("required"), minimum);
        checks->insert(key, check);
    }
    return okay;
}

bool check_maximum(const QJsonObject & baselines,
                   const QString & key,
                   double current_value,
                   QTextStream * out,
                   QJsonObject * checks)
{
    if( !baselines.contains(key) || !baselines.value(key).isDouble() ) return true;

    const double maximum = baselines.value(key).toDouble();
    const bool okay = current_value <= maximum;
    if( out )
    {
        *out << (okay ? "[PASS] " : "[FAIL] ")
             << key << " current=" << current_value
             << " maximum=" << maximum << '\n';
    }

    if( checks )
    {
        QJsonObject check;
        check.insert(QStringLiteral("kind"), QStringLiteral("maximum"));
        check.insert(QStringLiteral("status"), okay ? QStringLiteral("pass") : QStringLiteral("fail"));
        check.insert(QStringLiteral("current"), current_value);
        check.insert(QStringLiteral("maximum"), maximum);
        checks->insert(key, check);
    }
    return okay;
}

bool write_baseline_file(const QString & path,
                         const QString & profile_key,
                         const QString & profile_label,
                         double regression_pct,
                         int threads,
                         int iterations,
                         const QJsonObject & results,
                         QString * error_message)
{
    const QJsonObject current_root = load_json_object(path);
    QJsonObject root;
    root.insert(QStringLiteral("schema"), 2);
    root.insert(QStringLiteral("default_regression_pct"), regression_pct);

    QJsonObject absolute_guards = extract_absolute_guards(current_root);
    if( absolute_guards.isEmpty() )
    {
        absolute_guards.insert(QStringLiteral("tiny_dual_iso.full16.average_ms.max"), 5000.0);
        absolute_guards.insert(QStringLiteral("tiny_dual_iso.preview16.average_ms.max"), 5000.0);
        absolute_guards.insert(QStringLiteral("tiny_dual_iso.full8.average_ms.max"), 1000.0);
        absolute_guards.insert(QStringLiteral("tiny_dual_iso.preview8.average_ms.max"), 1000.0);
        absolute_guards.insert(QStringLiteral("large_dual_iso.full16.average_ms.max"), 15000.0);
        absolute_guards.insert(QStringLiteral("large_dual_iso.preview16.average_ms.max"), 15000.0);
        absolute_guards.insert(QStringLiteral("large_dual_iso.full8.average_ms.max"), 5000.0);
        absolute_guards.insert(QStringLiteral("large_dual_iso.preview8.average_ms.max"), 5000.0);
    }
    root.insert(QStringLiteral("absolute_guards"), absolute_guards);

    QJsonObject relative_guards = extract_relative_guards(current_root);
    if( relative_guards.isEmpty() )
    {
        relative_guards.insert(QStringLiteral("tiny_dual_iso.preview16_speedup_vs_full16.min"), 1.05);
        relative_guards.insert(QStringLiteral("large_dual_iso.preview16_speedup_vs_full16.min"), 1.05);
    }
    root.insert(QStringLiteral("relative_guards"), relative_guards);

    QJsonObject profiles = current_root.value(QStringLiteral("profiles")).toObject();
    QJsonObject profile;
    profile.insert(QStringLiteral("label"), profile_label);
    profile.insert(QStringLiteral("captured_at_utc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODate));
    profile.insert(QStringLiteral("threads"), threads);
    profile.insert(QStringLiteral("iterations"), iterations);
    profile.insert(QStringLiteral("results"), results);
    profiles.insert(profile_key, profile);
    root.insert(QStringLiteral("profiles"), profiles);

    return write_json(path, root, error_message);
}

QString metric_line(const QString & label, const PerfMetric & metric)
{
    return QStringLiteral("%1 avg=%2ms median=%3ms min=%4ms max=%5ms fps=%6")
        .arg(label, -10)
        .arg(metric.average_ms, 0, 'f', 3)
        .arg(metric.median_ms, 0, 'f', 3)
        .arg(metric.min_ms, 0, 'f', 3)
        .arg(metric.max_ms, 0, 'f', 3)
        .arg(metric.fps, 0, 'f', 3);
}

void print_usage(QTextStream * out)
{
    if( !out ) return;
    *out
        << "Usage: perf_tests [options]\n"
        << "  --iterations <n>         Number of measured iterations per mode (default: 10)\n"
        << "  --threads <n>            Pipeline thread count (default: 1)\n"
        << "  --json-output <path>     Write a JSON artifact with metrics and checks\n"
        << "  --baseline <path>        Baseline file path (default: tests/perf/baselines.json)\n"
        << "  --baseline-profile <id>  Override the local baseline profile key\n"
        << "  --extra-clip <path>      Optional larger clip to benchmark in addition to tiny_dual_iso\n"
        << "  --extra-receipt <path>   Receipt for the optional larger clip\n"
        << "  --extra-label <id>       Result key prefix for the optional larger clip\n"
        << "  --extra-sample-frames <n> Distinct frames to cycle for the optional larger clip (default: 8)\n"
        << "  --regression-pct <pct>   Allowed slowdown versus a profile baseline\n"
        << "  --update-baseline        Replace or create the selected baseline profile\n"
        << "  --require-baseline       Fail when the selected baseline profile is missing or incompatible\n"
        << "  --stage-timing           Enable per-stage timing logs during rendering\n"
        << "  --help                   Show this help text\n";
}

} // namespace

int main(int argc, char * argv[])
{
    QCoreApplication app(argc, argv);

    int iterations = 10;
    int threads = 1;
    QString json_output;
    QString baseline_path = repo_file_path(QStringLiteral("tests/perf/baselines.json"));
    QString profile_key = default_profile_key();
    QString profile_label = default_profile_label();
    QString extra_clip;
    QString extra_receipt;
    QString extra_label;
    int extra_sample_frames = 8;
    bool extra_requested = false;
    double regression_pct_override = -1.0;
    bool update_baseline = false;
    bool require_baseline = false;

    QTextStream out(stdout);
    const QStringList args = app.arguments();
    for( int i = 1; i < args.size(); ++i )
    {
        if( args[i] == QStringLiteral("--iterations") && i + 1 < args.size() )
        {
            iterations = args[++i].toInt();
        }
        else if( args[i] == QStringLiteral("--threads") && i + 1 < args.size() )
        {
            threads = args[++i].toInt();
        }
        else if( args[i] == QStringLiteral("--json-output") && i + 1 < args.size() )
        {
            json_output = args[++i];
        }
        else if( args[i] == QStringLiteral("--baseline") && i + 1 < args.size() )
        {
            baseline_path = args[++i];
        }
        else if( args[i] == QStringLiteral("--baseline-profile") && i + 1 < args.size() )
        {
            profile_key = args[++i];
            profile_label = QStringLiteral("User-selected baseline (%1)").arg(profile_key);
        }
        else if( args[i] == QStringLiteral("--extra-clip") && i + 1 < args.size() )
        {
            extra_clip = args[++i];
            extra_requested = true;
        }
        else if( args[i] == QStringLiteral("--extra-receipt") && i + 1 < args.size() )
        {
            extra_receipt = args[++i];
            extra_requested = true;
        }
        else if( args[i] == QStringLiteral("--extra-label") && i + 1 < args.size() )
        {
            extra_label = args[++i];
            extra_requested = true;
        }
        else if( args[i] == QStringLiteral("--extra-sample-frames") && i + 1 < args.size() )
        {
            extra_sample_frames = args[++i].toInt();
            extra_requested = true;
        }
        else if( args[i] == QStringLiteral("--regression-pct") && i + 1 < args.size() )
        {
            regression_pct_override = args[++i].toDouble();
        }
        else if( args[i] == QStringLiteral("--update-baseline") )
        {
            update_baseline = true;
        }
        else if( args[i] == QStringLiteral("--require-baseline") )
        {
            require_baseline = true;
        }
        else if( args[i] == QStringLiteral("--stage-timing") )
        {
            qputenv("MLVAPP_STAGE_TIMING", QByteArrayLiteral("1"));
        }
        else if( args[i] == QStringLiteral("--help") || args[i] == QStringLiteral("-h") )
        {
            print_usage(&out);
            return 0;
        }
        else
        {
            out << "Unknown argument: " << args[i] << '\n';
            print_usage(&out);
            return 1;
        }
    }

    if( iterations <= 0 )
    {
        out << "--iterations must be greater than 0\n";
        return 1;
    }
    if( threads <= 0 )
    {
        out << "--threads must be greater than 0\n";
        return 1;
    }

    if( threads <= 1 )
    {
        test_runtime::force_single_threaded_pipeline();
        threads = 1;
    }

    if( extra_clip.isEmpty() ) extra_clip = qEnvironmentVariable("MLVAPP_PERF_EXTRA_CLIP");
    if( extra_receipt.isEmpty() ) extra_receipt = qEnvironmentVariable("MLVAPP_PERF_EXTRA_RECEIPT");
    if( extra_label.isEmpty() ) extra_label = qEnvironmentVariable("MLVAPP_PERF_EXTRA_LABEL");
    if( !extra_requested )
    {
        extra_requested = !extra_clip.isEmpty() || !extra_receipt.isEmpty() || !extra_label.isEmpty()
            || !qEnvironmentVariable("MLVAPP_PERF_EXTRA_SAMPLE_FRAMES").isEmpty();
    }
    if( extra_sample_frames <= 0 )
    {
        const QString env_extra_frames = qEnvironmentVariable("MLVAPP_PERF_EXTRA_SAMPLE_FRAMES");
        if( !env_extra_frames.isEmpty() ) extra_sample_frames = env_extra_frames.toInt();
    }
    if( extra_sample_frames <= 0 ) extra_sample_frames = 8;

    QVector<FixtureSpec> fixtures;
    fixtures.push_back({
        QStringLiteral("tiny_dual_iso"),
        QStringLiteral("tiny Dual ISO"),
        repo_file_path(QStringLiteral("tests/fixtures/clips/tiny_dual_iso.mlv")),
        repo_file_path(QStringLiteral("tests/fixtures/receipts/tiny_dual_iso_hq.marxml")),
        2
    });

    if( extra_clip.isEmpty() ) extra_clip = conventional_extra_clip_path();
    if( !extra_clip.isEmpty() )
    {
        FixtureSpec extra_fixture;
        extra_fixture.clip_path = resolve_perf_path(extra_clip);
        extra_fixture.key = extra_label.isEmpty()
            ? default_fixture_label_for_path(extra_fixture.clip_path)
            : sanitize_key_fragment(extra_label);
        extra_fixture.label = extra_label.isEmpty()
            ? QStringLiteral("extra fixture (%1)").arg(QFileInfo(extra_fixture.clip_path).fileName())
            : extra_label;

        if( extra_receipt.isEmpty() ) extra_receipt = conventional_extra_receipt_path(extra_fixture.key);
        extra_fixture.receipt_path = resolve_perf_path(extra_receipt);
        extra_fixture.sample_frames = extra_sample_frames;

        if( extra_fixture.receipt_path.isEmpty() )
        {
            out << "Optional extra fixture requested but no receipt path was provided or discovered.\n";
            out << "Pass --extra-receipt <path> or set MLVAPP_PERF_EXTRA_RECEIPT.\n";
            return 1;
        }

        if( fixture_exists(extra_fixture) ) fixtures.push_back(extra_fixture);
        else
        {
            if( extra_requested )
            {
                out << "Optional extra fixture was requested, but the clip or receipt does not exist:\n";
                out << "  clip: " << extra_fixture.clip_path << '\n';
                out << "  receipt: " << extra_fixture.receipt_path << '\n';
                return 1;
            }

            out << "Skipping optional extra fixture because the clip or receipt does not exist:\n";
            out << "  clip: " << extra_fixture.clip_path << '\n';
            out << "  receipt: " << extra_fixture.receipt_path << "\n\n";
        }
    }

    QString error_message;
    QJsonObject results;

    QVector<FixtureRun> fixture_runs;
    fixture_runs.reserve(fixtures.size());
    for( const FixtureSpec & fixture : fixtures )
    {
        out << "priming " << fixture.label << " paths before measurement...\n";
        if( !prime_benchmark_modes(fixture, threads, &error_message) )
        {
            out << error_message << '\n';
            return 1;
        }
        out << '\n';

        FixtureRun run;
        run.spec = fixture;

        run.full16 = benchmark_spec(fixture, false, false, iterations, threads, &run.total_frames, &run.sample_frames_used, &error_message);
        if( !error_message.isEmpty() )
        {
            out << error_message << '\n';
            return 1;
        }
        record_metric(&results, fixture.key + QStringLiteral(".full16"), run.full16);

        run.preview16 = benchmark_spec(fixture, true, false, iterations, threads, nullptr, nullptr, &error_message);
        if( !error_message.isEmpty() )
        {
            out << error_message << '\n';
            return 1;
        }
        record_metric(&results, fixture.key + QStringLiteral(".preview16"), run.preview16);

        run.full8 = benchmark_spec(fixture, false, true, iterations, threads, nullptr, nullptr, &error_message);
        if( !error_message.isEmpty() )
        {
            out << error_message << '\n';
            return 1;
        }
        record_metric(&results, fixture.key + QStringLiteral(".full8"), run.full8);

        run.preview8 = benchmark_spec(fixture, true, true, iterations, threads, nullptr, nullptr, &error_message);
        if( !error_message.isEmpty() )
        {
            out << error_message << '\n';
            return 1;
        }
        record_metric(&results, fixture.key + QStringLiteral(".preview8"), run.preview8);

        run.preview16_speedup = run.preview16.average_ms > 0.0 ? (run.full16.average_ms / run.preview16.average_ms) : 0.0;
        run.preview8_speedup = run.preview8.average_ms > 0.0 ? (run.full8.average_ms / run.preview8.average_ms) : 0.0;
        results.insert(fixture.key + QStringLiteral(".preview16_speedup_vs_full16"), run.preview16_speedup);
        results.insert(fixture.key + QStringLiteral(".preview8_speedup_vs_full8"), run.preview8_speedup);
        fixture_runs.push_back(run);
    }

    results.insert(QStringLiteral("threads"), threads);

    BaselineContext baseline = load_baseline_context(baseline_path, profile_key, threads);
    if( regression_pct_override >= 0.0 ) baseline.regression_pct = regression_pct_override;

    out << "Dual ISO perf harness\n";
    out << "  profile: " << profile_key << '\n';
    out << "  baseline: " << baseline_path << '\n';
    out << "  iterations: " << iterations << "  threads: " << threads << '\n';
    out << "  allowed slowdown: " << baseline.regression_pct << "%\n";
    out << '\n';
    for( const FixtureRun & run : fixture_runs )
    {
        out << run.spec.label << " [" << run.spec.key << "]\n";
        out << "  clip: " << run.spec.clip_path << '\n';
        out << "  receipt: " << run.spec.receipt_path << '\n';
        out << "  total_frames: " << run.total_frames << "  sampled_frames: " << run.sample_frames_used << '\n';
        out << "  " << metric_line(QStringLiteral("full16"), run.full16) << '\n';
        out << "  " << metric_line(QStringLiteral("preview16"), run.preview16) << '\n';
        out << "  " << metric_line(QStringLiteral("full8"), run.full8) << '\n';
        out << "  " << metric_line(QStringLiteral("preview8"), run.preview8) << '\n';
        out << "  preview16 speedup vs full16=" << QString::number(run.preview16_speedup, 'f', 3) << "x\n";
        out << "  preview8 speedup vs full8=" << QString::number(run.preview8_speedup, 'f', 3) << "x\n";
        out << '\n';
    }

    QJsonObject checks;
    bool profile_ok = true;
    if( baseline.profile_found && baseline.profile_threads_match )
    {
        out << "local baseline profile: " << (baseline.profile_label.isEmpty() ? profile_key : baseline.profile_label) << '\n';
        const QVector<ProfileMetricSpec> profile_specs = profile_metric_specs_for_fixtures(fixtures);
        for( const ProfileMetricSpec & spec : profile_specs )
        {
            const QJsonObject current_metric = results.value(spec.metric_key).toObject();
            profile_ok = check_baseline_metric(baseline.profile_results,
                                               spec.metric_key,
                                               spec.field_key,
                                               spec.label,
                                               current_metric.value(spec.field_key).toDouble(),
                                               baseline.regression_pct,
                                               &out,
                                               &checks) && profile_ok;
        }
    }
    else
    {
        if( baseline.profile_found && !baseline.profile_threads_match )
        {
            out << "local baseline profile found but captured with threads=" << baseline.profile_threads
                << "; skipping absolute gates for current threads=" << threads
                << ". Re-capture with --update-baseline to gate this config.\n";
        }
        else
        {
            out << "no matching local baseline profile in " << baseline_path
                << "; run with --update-baseline to seed one for this machine.\n";
        }
        if( require_baseline ) profile_ok = false;
    }

    bool relative_ok = true;
    bool absolute_ok = true;
    for( const FixtureRun & run : fixture_runs )
    {
        relative_ok = check_minimum(baseline.relative_guards,
                                    run.spec.key + QStringLiteral(".preview16_speedup_vs_full16.min"),
                                    run.preview16_speedup,
                                    &out,
                                    &checks) && relative_ok;
        relative_ok = check_minimum(baseline.relative_guards,
                                    run.spec.key + QStringLiteral(".preview8_speedup_vs_full8.min"),
                                    run.preview8_speedup,
                                    &out,
                                    &checks) && relative_ok;

        absolute_ok = check_maximum(baseline.absolute_guards,
                                    run.spec.key + QStringLiteral(".full16.average_ms.max"),
                                    run.full16.average_ms,
                                    &out,
                                    &checks) && absolute_ok;
        absolute_ok = check_maximum(baseline.absolute_guards,
                                    run.spec.key + QStringLiteral(".preview16.average_ms.max"),
                                    run.preview16.average_ms,
                                    &out,
                                    &checks) && absolute_ok;
        absolute_ok = check_maximum(baseline.absolute_guards,
                                    run.spec.key + QStringLiteral(".full8.average_ms.max"),
                                    run.full8.average_ms,
                                    &out,
                                    &checks) && absolute_ok;
        absolute_ok = check_maximum(baseline.absolute_guards,
                                    run.spec.key + QStringLiteral(".preview8.average_ms.max"),
                                    run.preview8.average_ms,
                                    &out,
                                    &checks) && absolute_ok;
    }

    if( update_baseline )
    {
        if( !write_baseline_file(baseline_path,
                                 profile_key,
                                 profile_label,
                                 baseline.regression_pct,
                                 threads,
                                 iterations,
                                 results,
                                 &error_message) )
        {
            out << error_message << '\n';
            return 1;
        }
        out << "updated baseline profile " << profile_key << " in " << baseline_path << '\n';
    }

    const bool baseline_ok = profile_ok && relative_ok && absolute_ok;
    const bool exit_ok = update_baseline ? true : baseline_ok;
    QString summary = baseline_ok ? QStringLiteral("SUMMARY: PASS")
                                  : QStringLiteral("SUMMARY: FAIL");
    if( update_baseline && baseline_ok ) summary = QStringLiteral("SUMMARY: UPDATED");
    else if( update_baseline ) summary = QStringLiteral("SUMMARY: UPDATED (see failing checks above)");
    out << '\n' << summary << '\n';

    if( !json_output.isEmpty() )
    {
        QJsonObject metadata;
        metadata.insert(QStringLiteral("captured_at_utc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODate));
        metadata.insert(QStringLiteral("baseline_path"), baseline_path);
        metadata.insert(QStringLiteral("baseline_profile"), profile_key);
        metadata.insert(QStringLiteral("baseline_profile_found"), baseline.profile_found);
        metadata.insert(QStringLiteral("baseline_profile_threads_match"), baseline.profile_threads_match);
        metadata.insert(QStringLiteral("baseline_regression_pct"), baseline.regression_pct);
        metadata.insert(QStringLiteral("iterations"), iterations);
        metadata.insert(QStringLiteral("threads"), threads);
        metadata.insert(QStringLiteral("updated_baseline"), update_baseline);
        if( !baseline.profile_label.isEmpty() ) metadata.insert(QStringLiteral("baseline_profile_label"), baseline.profile_label);

        QJsonArray fixture_array;
        for( const FixtureRun & run : fixture_runs )
        {
            QJsonObject fixture_object;
            fixture_object.insert(QStringLiteral("key"), run.spec.key);
            fixture_object.insert(QStringLiteral("label"), run.spec.label);
            fixture_object.insert(QStringLiteral("clip_path"), run.spec.clip_path);
            fixture_object.insert(QStringLiteral("receipt_path"), run.spec.receipt_path);
            fixture_object.insert(QStringLiteral("total_frames"), run.total_frames);
            fixture_object.insert(QStringLiteral("sample_frames"), run.sample_frames_used);
            fixture_array.append(fixture_object);
        }
        metadata.insert(QStringLiteral("fixtures"), fixture_array);

        QJsonObject status;
        status.insert(QStringLiteral("profile_ok"), profile_ok);
        status.insert(QStringLiteral("relative_ok"), relative_ok);
        status.insert(QStringLiteral("absolute_ok"), absolute_ok);
        status.insert(QStringLiteral("overall_ok"), baseline_ok);
        status.insert(QStringLiteral("exit_ok"), exit_ok);

        QJsonObject artifact;
        artifact.insert(QStringLiteral("metadata"), metadata);
        artifact.insert(QStringLiteral("results"), results);
        artifact.insert(QStringLiteral("checks"), checks);
        artifact.insert(QStringLiteral("status"), status);

        if( !write_json(json_output, artifact, &error_message) )
        {
            out << error_message << '\n';
            return 1;
        }
    }

    return exit_ok ? 0 : 2;
}
