# MLV-App Batch CLI Implementation Spec (CLAUDE.md)

## Purpose
This file guides Claude Code through a surgical modification of MLV-App to add
headless batch CLI mode for Cinema DNG sequence export. Place this file in the
repository root — Claude Code reads it automatically every session.

---

## Agent Bridge — Session Startup (Hook-Driven)

This repo uses an agent-bridge to coordinate with a peer Codex session. The
`SessionStart` hook in `.claude/settings.local.json` runs
`tools/agent-bridge/bootstrap_session.py` automatically at the start of every
session — its stdout is injected into your context. The bootstrap registers
this session as the active Claude bridge owner (superseding any older Claude
session), drains messages from the previous session, sends a HANDSHAKE to
Codex, and updates the watcher config.

After the hook fires, do these in order:
1. **Read `drained_previous_messages`** in the hook output — surface any unread
   messages from the previous session to the user before proceeding.
2. **Use the returned `session_id`** as your active Claude bridge GUID for this session.
3. **If `check_inbox` returns a `SESSION_UPDATE: superseded` control message at any
   point**, stop all bridge sends immediately — a newer Claude session has taken over.
4. **Read `active_session_unread`** in the hook output — these are unread rows
   already sitting in the new active session bucket. Surface them, then mark each
   read by id after handling.
5. **Start the bridge Monitor** — the Monitor is Claude's inbox wake mechanism and does
   NOT survive context compaction. Start it every session, no exceptions:
   ```
   Monitor(persistent=True, command="<python> -u tools/agent-bridge/bridge_monitor_poll.py --state-dir <bridge-state-dir> --agent claude --session-id <active-guid> --project mlv-app --poll-interval-seconds 2")
   ```
   Use `bridge_monitor_poll.py` for the Monitor. Do not substitute
   `probe_server.py`; probes are diagnostics and will not keep Claude's inbox
   wake path armed. Before saying "waiting for Codex," verify the Monitor task
   is active. If no Monitor is running, start one before waiting.

When a Monitor notification fires, call `mcp__agent-bridge__check_inbox` with
`agent=claude`, `session_id=<active-guid-or-mlv-app>`, `mark_read=False`, then mark
each message read explicitly by id.

If the hook output is missing from your session-start context (broken JSON,
deleted file, hook failure), fall back to running `bootstrap_session.py`
manually with the command stored in `.claude/settings.local.json`.

Bridge protocol details: `tools/agent-bridge/BRIDGE_PROTOCOL.md`
Hardening plan and audit log: `tools/agent-bridge/BRIDGE_HARDENING.md`

---

## Architecture (Locked — Do Not Deviate)

- **Fork of MLV-App** — not a rewrite, not a new tool
- **Process-per-batch isolation**: .NET orchestrator launches 10 independent
  MLVApp.exe --batch processes. NO internal multi-threading for v1.
- **Use QApplication even in batch mode** (NOT QCoreApplication) — the export
  code path touches widgets, fonts, signal/slot machinery internally
- **Surgical extraction**: refactor the existing GUI CDNG export handler into a
  callable helper. Do NOT invent new exporter APIs or DNG writing code.
- **CDNG likely does not use FFmpeg** — DNG export probably writes TIFF/DNG
  frame files directly rather than using an FFmpeg pipeline. However, do NOT
  treat this as fact. Phase 0 must PROVE it by quoting actual include
  statements and function calls from the codebase.

## Target Export Format
**Cinema DNG sequences** (.dng files, one per frame)
- This is NOT a single-container format like ProRes or H.264
- Output layout: `<outRoot>/<clipBaseName>/clipBaseName_000001.dng`
- Error handling is per-frame (a corrupt frame can be skipped without losing
  the entire clip)
- DNG sequences are large — disk I/O and output volume matter

## Settings / Receipt Strategy (PHASED — Critical Design Decision)
- **v1 (Phases 0-5)**: Use MLV-App's DEFAULT processing settings when opening
  a file. Do NOT attempt .marxml receipt parsing yet. Get the export loop
  working first with whatever defaults the app applies on file open.
- **v1.1 (Phase 6)**: Add --receipt flag for .marxml loading. Extract receipt
  parsing from MainWindow into a standalone loader. Apply to mlvObject_t.
- **Rationale**: Receipts add complexity. Layering them onto a working export
  pipeline is safer than building both simultaneously. The user needs receipts
  eventually but the export loop must work first.

## Build Environment
- Windows 10
- Qt Creator with Qt 5.15 LTS
- MinGW toolchain
- FFmpeg dev libraries in platform/qt/FFmpeg/ (needed for GUI ProRes/H264,
  but likely NOT used by CDNG export path — confirm in Phase 0)

## Key Technical Constraints
- Always `QApplication`, never `QCoreApplication`
- `app.setQuitOnLastWindowClosed(false)` in batch mode
- All new files go in `src/batch/`
- Modified files: `platform/qt/main.cpp`, `platform/qt/MLVApp.pro`, and
  targeted patches in the CDNG export path
- Circular includes between MainWindow.h and batch headers are FORBIDDEN —
  use BatchTypes.h as the shared type header
- Use BatchPrompts helper class for dialog replacement — no inline if/else

---

## File Structure

### New Files (create these)
```
src/batch/
  BatchTypes.h        — Shared structs (ProcessingProfile, ProcessResult)
                        NOTE: ProcessingProfile fields are TBD until Phase 0
                        discovers the real internal setting names/types.
                        Do NOT pre-specify fields like "debayerAlgorithm = 4"
                        or "whiteBalanceKelvin" — these are guesses that may
                        not match MLV-App's actual API.
  BatchContext.h       — Static singleton for batch mode flags
  BatchContext.cpp     — Static member definitions
  BatchPrompts.h      — Helper class for dialog replacement in batch mode
  BatchPrompts.cpp    — shouldSkipFrame(), shouldContinue() implementations
  BatchRunner.h       — CLI batch orchestration class
  BatchRunner.cpp     — Enumerate MLVs, call export helper, log results
```

### Modified Files (surgical patches only)
```
platform/qt/main.cpp       — CLI/GUI branching before MainWindow creation
platform/qt/MLVApp.pro     — Add new HEADERS and SOURCES entries
platform/qt/MainWindow.cpp — Extract CDNG export into callable helper
                            — Replace QMessageBox calls with BatchPrompts
platform/qt/MainWindow.h   — Declare new static/public export helper method
```

---

## Implementation Phases (Execute In Order — Do Not Skip Ahead)

### Phase 0: Recon — Map the Real Code
Before writing ANY new code:
1. Find the CDNG export QAction handler in MainWindow.cpp
2. Trace the FULL call chain down to DNG file writing
3. **Confirm whether this path uses FFmpeg or direct TIFF/DNG writing**
4. Find every QMessageBox, QProgressDialog, QFileDialog, and ui-> reference
5. Find how output folder and frame filenames are determined
6. Identify the per-frame export loop structure
7. Report: file paths, function names, line numbers, call graph

### Phase 1: Foundation Files (No Export Logic Yet)
Create BatchTypes.h, BatchContext.h/.cpp, BatchPrompts.h/.cpp (stubs).
Update MLVApp.pro.
Verify: compiles clean, GUI still launches normally.

### Phase 2: CLI Entry Point
Modify main.cpp:
- Early --batch detection via raw argv scan
- QCommandLineParser for: --input, --output, --skip-errors, --log, --verbose
- Note: --receipt is NOT included yet (deferred to Phase 6)
- BatchContext flags set before BatchRunner call
- Stub BatchRunner that prints args and exits
Verify: `MLVApp --batch --help` shows usage, no GUI window appears.

### Phase 3: CDNG Export Helper Extraction (THE HARD PART)
Refactor the GUI CDNG export handler into two layers:
1. Original UI handler remains (calls helper internally)
2. New helper callable from batch mode:
   - Opens MLV with default processing settings (no receipt yet)
   - Creates `<outDir>/<clipBaseName>/` subfolder automatically
   - Writes frame sequence: clipBaseName_NNNNNN.dng
   - Uses the EXACT same DNG writing code as GUI
   - No dialogs, no progress UI
   - Returns ProcessResult with frames exported/skipped/errors
Verify: single MLV file exports to DNG sequence from CLI with defaults.

### Phase 4: Dialog/Prompt Patching via BatchPrompts
Create BatchPrompts utility class:
```cpp
class BatchPrompts {
public:
    // Returns true = skip and continue, false = abort
    static bool shouldSkipFrame(const QString& clipName, int frameIndex,
                                const QString& errorDetail);
    // Returns true = continue processing, false = abort
    static bool shouldContinue(const QString& context,
                               const QString& message);
};
```
Implementation logic:
- BatchContext::isBatchMode() && skipErrors → log warning, return true
- BatchContext::isBatchMode() && !skipErrors → log error, return false
- !BatchContext::isBatchMode() → show original QMessageBox, return user choice

Replace ONLY the QMessageBox calls in the CDNG export path.
Replace QProgressDialog with stdout logging in batch mode.
Do NOT globally disable all message boxes.
Verify: corrupt frame is skipped or causes exit based on --skip-errors flag.

### Phase 5: BatchRunner + Logging + Exit Codes
Complete BatchRunner:
1. Enumerate *.mlv files in input path (single file or folder)
2. For each file: open, export CDNG with defaults, log result
3. Structured stdout logging (parseable by .NET orchestrator):
   [BATCH] START input=<path> output=<path>
   [BATCH] FILE <filename> frames=<N>
   [BATCH] SKIP <filename> frame=<N> error=<description>
   [BATCH] DONE <filename> exported=<N> skipped=<N> elapsed=<seconds>
   [BATCH] COMPLETE files=<N> succeeded=<N> failed=<N> total_elapsed=<seconds>
4. Optional --log <file> mirrors stdout to file
5. Exit codes (see table below)
Verify: full batch run, parseable log, correct exit codes, .dng files exist.

### Phase 6: Receipt Loading (v1.1 — AFTER export loop is proven stable)
NOW add --receipt flag, in two sub-steps with separate gates:

**6A — Parse receipt headlessly:**
1. Find the .marxml parsing code in MainWindow
2. Extract into standalone ReceiptLoader function (no MainWindow dependency)
3. Add --receipt (-r) to QCommandLineParser
4. In BatchRunner, load receipt and PRINT parsed settings to stdout
5. Do NOT apply settings to export yet — just verify parsing works
Gate 6A: receipt loads, settings print correctly, bad XML returns error

**6B — Apply parsed settings to export:**
1. Apply loaded settings to mlvObject_t / processingObject_t before export
2. Use the EXACT same setter functions the GUI uses (discovered in Phase 0)
3. If --receipt not provided: use defaults (current v1 behavior preserved)
4. If --receipt provided: load, apply, then export
Gate 6B: export WITH receipt produces visibly different .dng output than
WITHOUT receipt (compare file sizes, visual appearance, or metadata).
Additionally, log a "settings fingerprint" after applying receipt — read back
actual processing state values (exposure, WB, dual ISO, debayer) from the
mlvObject_t/processingObject_t and print them. This proves settings reached
the pipeline, not just the parser.
This split prevents the classic trap of "receipt parsed but never applied."

---

## Exit Code Reference
| Code | Meaning                                        | .NET Orchestrator Action    |
|------|------------------------------------------------|-----------------------------|
| 0    | All files exported successfully                 | Mark batch as complete      |
| 1    | Some failures occurred (with --skip-errors)     | Log warnings, review output |
| 2    | Bad arguments / usage error                     | Fix command and retry       |
| 3    | Cannot open input file or folder                | Check paths, retry          |
| 4    | Export failure (without --skip-errors, fatal)   | Investigate, manual retry   |
| 5    | Receipt file not found or invalid (Phase 6+)    | Check receipt path/format   |

---

## Behavioral Rules for Claude Code

1. **No speculation** — search the repo and quote exact code before writing
2. **No new exporter APIs** — reuse the existing CDNG export code path
3. **Smallest diff possible** — surgical changes only
4. **Show full diffs** before applying to any existing file
5. **One phase per response** — do not jump ahead
6. **Compile after every change** — `cd platform/qt && qmake && mingw32-make -j8`
7. **Never use QCoreApplication** — always QApplication
8. **Never create circular includes** — BatchTypes.h is the shared type header
9. **Treat CDNG as frame-sequence** — per-frame error handling, subfolder output
10. **CDNG likely does NOT use FFmpeg** — Phase 0 must prove this with evidence
11. **No receipt parsing until Phase 6** — use defaults for Phases 0-5
12. **Use BatchPrompts helper class** — no inline if/else for dialog replacement
13. **Patch only CDNG export path dialogs** — do NOT globally disable message boxes

---

## CLI Usage (Target)

```bash
# v1: Single file with defaults (Phases 0-5)
MLVApp.exe --batch --input "C:/footage/clip.mlv" --output "C:/exports" --skip-errors

# v1: Folder of MLVs with defaults
MLVApp.exe --batch --input "C:/temp/batch_01/" --output "C:/exports" --skip-errors --log "batch_01.log"

# v1: Verbose logging
MLVApp.exe --batch --input "C:/footage/" --output "C:/exports" --skip-errors --verbose

# v1.1: With receipt (Phase 6, after export loop is stable)
MLVApp.exe --batch --input "C:/footage/clip.mlv" --output "C:/exports" --receipt "settings.marxml" --skip-errors
```

## .NET Orchestrator Integration (Later — Not Claude Code's Job)
- Hardlink .mlv files to temp batch folders (same NTFS volume required)
- Launch N processes with ProcessStartInfo + CreateNoWindow
- Parse [BATCH] log lines from stdout for progress monitoring
- Read exit codes to determine per-batch success/failure
- Track exact batch folders created in List<string>, clean up only those
