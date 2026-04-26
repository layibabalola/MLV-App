#include "StageTimingCsvSink.h"

#include <QDir>
#include <QFileInfo>

#include <mutex>
#include <cstdio>

namespace {

std::mutex g_sinkMutex;
FILE * g_sinkFile = nullptr;

void csvWriteEscaped(FILE * file, const char * text)
{
    if (!text) text = "";
    bool quote = false;
    for (const char * p = text; *p; ++p)
    {
        if (*p == ',' || *p == '"' || *p == '\n' || *p == '\r')
        {
            quote = true;
            break;
        }
    }
    if (!quote)
    {
        std::fputs(text, file);
        return;
    }
    std::fputc('"', file);
    for (const char * p = text; *p; ++p)
    {
        if (*p == '"') std::fputc('"', file);
        std::fputc(*p, file);
    }
    std::fputc('"', file);
}

} // namespace

extern "C" int stage_timing_csv_sink_open(const char * path)
{
    std::lock_guard<std::mutex> lock(g_sinkMutex);
    if (g_sinkFile)
    {
        std::fclose(g_sinkFile);
        g_sinkFile = nullptr;
    }
    if (!path || !path[0])
    {
        return 1;
    }

    const QString qpath = QString::fromLocal8Bit(path);
    const QFileInfo info(qpath);
    const QDir parent = info.dir();
    if (!parent.exists() && !QDir().mkpath(parent.absolutePath()))
    {
        return 0;
    }

    g_sinkFile = std::fopen(path, "wb");
    if (!g_sinkFile)
    {
        return 0;
    }
    std::fputs("frame_idx,request_serial,slot,stage,event,ns,phase3_mode,clip_generation\n",
               g_sinkFile);
    std::fflush(g_sinkFile);
    return 1;
}

extern "C" void stage_timing_csv_sink_write_event(uint32_t frame_idx,
                                                   uint64_t request_serial,
                                                   uint8_t slot,
                                                   const char * stage,
                                                   const char * event,
                                                   uint64_t ns,
                                                   uint8_t phase3_mode,
                                                   uint32_t clip_generation)
{
    std::lock_guard<std::mutex> lock(g_sinkMutex);
    if (!g_sinkFile) return;
    std::fprintf(g_sinkFile,
                 "%u,%llu,%u,",
                 static_cast<unsigned int>(frame_idx),
                 static_cast<unsigned long long>(request_serial),
                 static_cast<unsigned int>(slot));
    csvWriteEscaped(g_sinkFile, stage);
    std::fputc(',', g_sinkFile);
    csvWriteEscaped(g_sinkFile, event);
    std::fprintf(g_sinkFile,
                 ",%llu,%u,%u\n",
                 static_cast<unsigned long long>(ns),
                 static_cast<unsigned int>(phase3_mode),
                 static_cast<unsigned int>(clip_generation));
    std::fflush(g_sinkFile);
}

extern "C" void stage_timing_csv_sink_close(void)
{
    std::lock_guard<std::mutex> lock(g_sinkMutex);
    if (!g_sinkFile) return;
    std::fclose(g_sinkFile);
    g_sinkFile = nullptr;
}

extern "C" int stage_timing_csv_sink_enabled(void)
{
    std::lock_guard<std::mutex> lock(g_sinkMutex);
    return g_sinkFile ? 1 : 0;
}
