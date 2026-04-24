/*!
 * \file CrashForensics.h
 * \brief Crash forensics / logging bundle: rotating qInstallMessageHandler
 *        sink, optional Windows minidump handler, and run-metadata stamp
 *        tying logs to dumps to profile JSONs.
 *
 * The always-on rotating log is written to
 *   <AppDataLocation>/logs/mlvapp-YYYYMMDD.log
 * with the 5 most recent date-stamped files kept on startup.
 *
 * On Windows, the unhandled-exception filter writes
 *   <AppDataLocation>/logs/mlvapp-YYYYMMDD-HHMMSS.dmp
 * and appends a "CRASH:" line to today's rotating log.
 *
 * The BatchLogger --log mechanism (src/batch/BatchLogger.cpp) is NOT
 * replaced; it remains the flag-driven batch log. CrashForensics is
 * the always-on durable sink, complementary to it.
 */

#ifndef CRASH_FORENSICS_H
#define CRASH_FORENSICS_H

#include <QString>

namespace CrashForensics {

/*!
 * Install the rotating qInstallMessageHandler file sink and, on Windows,
 * the SetUnhandledExceptionFilter minidump hook.  Idempotent — safe to
 * call once at process start; subsequent calls are no-ops.
 *
 * \param argc, argv  Original command line.  Captured for the
 *                    run-metadata JSON stamp.
 * \return absolute path to the rotating log file opened for this run,
 *         or an empty QString if initialization failed (e.g. the logs
 *         directory could not be created).
 */
QString install(int argc, char * argv[]);

/*!
 * Absolute path of the rotating log file for today.  Returns the same
 * path install() returned (or empty if install() wasn't called yet).
 */
QString currentLogFilePath();

/*!
 * Absolute path of the logs directory (the parent of the rotating log).
 */
QString logsDirectoryPath();

/*!
 * Build the run-metadata JSON object (as a compact JSON string) shared
 * between the rotating log's startup stamp and the --profile-playback
 * JSON output.
 *
 * Keys: build_sha, app_version, qt_version, os, cpu_features, command_line.
 */
QString runMetadataJson();

/*!
 * Emit the startup metadata line through qInfo() so it is captured in
 * the rotating log.  Call this immediately after install().
 */
void logStartupMetadata();

} // namespace CrashForensics

#endif // CRASH_FORENSICS_H
