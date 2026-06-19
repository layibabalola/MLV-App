#ifndef BATCHCONTEXT_H
#define BATCHCONTEXT_H

#include <QString>
#include <cstdint>

/* Static singleton holding batch-mode flags.
 * Set once at startup from CLI args, read throughout the codebase.
 * Intentionally uses a separate .cpp for static member definitions
 * to avoid MinGW linker issues with inline statics. */
class BatchContext
{
public:
    static void setBatchMode(bool enabled);
    static bool isBatchMode();

    static void setSkipErrors(bool skip);
    static bool skipErrors();

    static void setVerbose(bool verbose);
    static bool isVerbose();

    static void setLogPath(const QString &path);
    static QString logPath();

    static void setReceiptPath(const QString &path);
    static QString receiptPath();

    static void setUseDefaultReceipt(bool use);
    static bool useDefaultReceipt();

    static void setResumeEnabled(bool enabled);
    static bool resumeEnabled();

    static void setMaxFrames(uint32_t frames);
    static uint32_t maxFrames();

    static void setCdngCodecOffset(int offset);
    static int cdngCodecOffset();

private:
    BatchContext() = delete; /* Pure static — no instances */

    static bool s_batchMode;
    static bool s_skipErrors;
    static bool s_verbose;
    static bool s_useDefaultReceipt;
    static bool s_resumeEnabled;
    static uint32_t s_maxFrames;
    static int s_cdngCodecOffset;
    static QString s_logPath;
    static QString s_receiptPath;
};

#endif // BATCHCONTEXT_H
