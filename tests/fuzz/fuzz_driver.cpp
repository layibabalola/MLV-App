#include <QCoreApplication>
#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QStringList>
#include <QTextStream>

#include <algorithm>

extern "C" int LLVMFuzzerTestOneInput(const unsigned char * data, unsigned long long size);

static void append_input_path(const QString & path, QStringList * files)
{
    const QFileInfo info(path);
    if (!info.exists()) {
        return;
    }

    if (info.isDir()) {
        QDirIterator it(path,
                        QDir::Files | QDir::NoDotAndDotDot,
                        QDirIterator::Subdirectories);
        while (it.hasNext()) {
            files->append(it.next());
        }
        return;
    }

    if (info.isFile()) {
        files->append(info.absoluteFilePath());
    }
}

static bool feed_file(const QString & path, QTextStream * errors)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        if (errors) {
            *errors << "Could not open fuzz input: " << path << "\n";
        }
        return false;
    }

    const QByteArray bytes = file.readAll();
    LLVMFuzzerTestOneInput(reinterpret_cast<const unsigned char *>(bytes.constData()),
                           static_cast<unsigned long long>(bytes.size()));
    return true;
}

int main(int argc, char ** argv)
{
    QCoreApplication app(argc, argv);

    QTextStream out(stdout);
    QTextStream err(stderr);

    QStringList inputs = app.arguments();
    inputs.removeFirst();
    if (inputs.isEmpty()) {
        out << "Usage: " << QFileInfo(app.applicationFilePath()).fileName()
            << " <file-or-directory> [more paths...]\n";
        return 0;
    }

    QStringList files;
    for (const QString & input : inputs) {
        append_input_path(input, &files);
    }
    files.removeDuplicates();
    std::sort(files.begin(), files.end());

    if (files.isEmpty()) {
        err << "No readable fuzz inputs were found.\n";
        return 1;
    }

    bool success = true;
    for (const QString & file_path : files) {
        if (!feed_file(file_path, &err)) {
            success = false;
        }
    }

    out << "Processed " << files.size() << " fuzz input(s).\n";
    return success ? 0 : 1;
}
