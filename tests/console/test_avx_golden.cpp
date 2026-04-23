#include "../common/minitest.h"
#include "../common/repo_paths.h"

#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QProcessEnvironment>
#include <QStandardPaths>
#include <QStringList>

static bool running_on_x86()
{
#if defined(__i386__) || defined(__x86_64__) || defined(_M_IX86) || defined(_M_X64)
    return true;
#else
    return false;
#endif
}

static QString find_tool(const QString & env_name, const QStringList & candidates)
{
    const QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
    if (environment.contains(env_name)) {
        const QString from_env = environment.value(env_name);
        if (!from_env.isEmpty()) {
            return from_env;
        }
    }

    for (const QString & candidate : candidates) {
        const QString executable = QStandardPaths::findExecutable(candidate);
        if (!executable.isEmpty()) {
            return executable;
        }
    }

#ifdef Q_OS_WIN
    for (const QString & candidate : candidates) {
        const QString windows_candidate = candidate.endsWith(QStringLiteral(".exe"))
            ? candidate
            : candidate + QStringLiteral(".exe");
        QDirIterator qt_installations(QStringLiteral("C:/Qt"),
                                      QStringList() << windows_candidate,
                                      QDir::Files,
                                      QDirIterator::Subdirectories);
        if (qt_installations.hasNext()) {
            return qt_installations.next();
        }
    }
#endif

    return QString();
}

static void prepend_to_path(QProcessEnvironment * environment, const QStringList & directories)
{
    QStringList parts = environment->value(QStringLiteral("PATH")).split(QDir::listSeparator(), Qt::SkipEmptyParts);
    for (auto it = directories.crbegin(); it != directories.crend(); ++it) {
        if (!it->isEmpty() && !parts.contains(*it, Qt::CaseInsensitive)) {
            parts.prepend(*it);
        }
    }
    environment->insert(QStringLiteral("PATH"), parts.join(QDir::listSeparator()));
}

static bool run_process(const QString & program,
                        const QStringList & arguments,
                        const QString & working_directory,
                        const QProcessEnvironment & environment,
                        QString * combined_output)
{
    QProcess process;
    process.setProgram(program);
    process.setArguments(arguments);
    process.setWorkingDirectory(working_directory);
    process.setProcessEnvironment(environment);
    process.setProcessChannelMode(QProcess::MergedChannels);
    process.start();
    if (!process.waitForStarted()) {
        if (combined_output) {
            *combined_output = process.errorString();
        }
        return false;
    }
    if (!process.waitForFinished(30 * 60 * 1000)) {
        process.kill();
        if (combined_output) {
            *combined_output = QStringLiteral("Timed out waiting for process: %1").arg(program);
        }
        return false;
    }
    if (combined_output) {
        *combined_output = QString::fromLocal8Bit(process.readAllStandardOutput());
    }
    return process.exitStatus() == QProcess::NormalExit && process.exitCode() == 0;
}

static QString find_built_helper(const QString & build_directory)
{
#ifdef Q_OS_WIN
    const QString helper_name = QStringLiteral("avx_parity_helper.exe");
#else
    const QString helper_name = QStringLiteral("avx_parity_helper");
#endif

    QDirIterator it(build_directory,
                    QStringList() << helper_name,
                    QDir::Files,
                    QDirIterator::Subdirectories);
    if (it.hasNext()) {
        return it.next();
    }
    return QString();
}

static QJsonObject load_json_file(const QString & path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        return QJsonObject();
    }
    return QJsonDocument::fromJson(file.readAll()).object();
}

static QString build_helper(bool enable_avx)
{
    const QString root = find_repo_root();
    const QString qmake = find_tool(QStringLiteral("QMAKE"),
                                    {QStringLiteral("qmake6"), QStringLiteral("qmake"), QStringLiteral("qmake-qt5")});
    const QString make_tool = find_tool(QStringLiteral("MAKE"),
                                        {QStringLiteral("mingw32-make"), QStringLiteral("jom"), QStringLiteral("nmake"), QStringLiteral("make")});

    if (qmake.isEmpty()) {
        SKIP_TEST("qmake was not found; skipping AVX parity build test");
    }
    if (make_tool.isEmpty()) {
        SKIP_TEST("make tool was not found; skipping AVX parity build test");
    }

    const QString flavor = enable_avx ? QStringLiteral("on") : QStringLiteral("off");
    const QString build_directory = QDir(root).filePath(QStringLiteral("tests/build-avx-parity/%1").arg(flavor));
    QDir().mkpath(build_directory);

    const QString pro_path = QDir(root).filePath(QStringLiteral("tests/console/avx_parity_helper.pro"));
    QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
    prepend_to_path(&environment,
                    {QFileInfo(qmake).absolutePath(), QFileInfo(make_tool).absolutePath()});
    if (enable_avx) {
        environment.insert(QStringLiteral("MLVAPP_ENABLE_AVX"), QStringLiteral("1"));
    } else {
        environment.remove(QStringLiteral("MLVAPP_ENABLE_AVX"));
    }

    QString output;
    ASSERT_TRUE(run_process(qmake,
                            {pro_path, QStringLiteral("CONFIG+=debug")},
                            build_directory,
                            environment,
                            &output));
    ASSERT_TRUE(run_process(make_tool,
                            {},
                            build_directory,
                            environment,
                            &output));

    const QString helper_path = find_built_helper(build_directory);
    ASSERT_FALSE(helper_path.isEmpty());
    return helper_path;
}

static QJsonObject run_helper_and_collect(const QString & helper_path)
{
    const QString output_path = QDir(QFileInfo(helper_path).absolutePath()).filePath(QStringLiteral("avx_parity_hashes.json"));
    QFile::remove(output_path);

    QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
    const QString qmake = find_tool(QStringLiteral("QMAKE"),
                                    {QStringLiteral("qmake6"), QStringLiteral("qmake"), QStringLiteral("qmake-qt5")});
    const QString make_tool = find_tool(QStringLiteral("MAKE"),
                                        {QStringLiteral("mingw32-make"), QStringLiteral("jom"), QStringLiteral("nmake"), QStringLiteral("make")});
    prepend_to_path(&environment,
                    {QFileInfo(qmake).absolutePath(), QFileInfo(make_tool).absolutePath()});
    environment.insert(QStringLiteral("MLVAPP_FORCE_THREADS"), QStringLiteral("1"));
    environment.insert(QStringLiteral("OMP_NUM_THREADS"), QStringLiteral("1"));
    environment.insert(QStringLiteral("OMP_DYNAMIC"), QStringLiteral("FALSE"));

    QString output;
    ASSERT_TRUE(run_process(helper_path,
                            {QStringLiteral("--output"), output_path},
                            QFileInfo(helper_path).absolutePath(),
                            environment,
                            &output));

    const QJsonObject object = load_json_file(output_path);
    ASSERT_FALSE(object.isEmpty());
    return object;
}

TEST(AvxParity, DefaultAndAvxBuildsProduceMatchingFrameHashes)
{
    if (!running_on_x86()) {
        SKIP_TEST("AVX parity only applies to x86/x86_64 hosts");
    }

    const QString default_helper = build_helper(false);
    const QString avx_helper = build_helper(true);

    QJsonObject default_output = run_helper_and_collect(default_helper);
    QJsonObject avx_output = run_helper_and_collect(avx_helper);

    default_output.remove(QStringLiteral("build_avx"));
    avx_output.remove(QStringLiteral("build_avx"));

    ASSERT_TRUE(default_output == avx_output);
}
