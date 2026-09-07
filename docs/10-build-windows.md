# Build on Windows

Migrated from the "Qt App Windows" section of `README.md` and the "Runtime
Execution Rules (Windows)" section of `AGENTS.md`. Originally written by the
maintainers; edits tracked via git history.

This document consolidates the build, deployment, and runtime rules for MLV
App on Windows. It covers the full loop:

1. Install prerequisites.
2. Build from Qt Creator (GUI workflow).
3. Build from the command line (the same flow CI uses).
4. Deploy Qt runtime dependencies with `windeployqt`.
5. Launch the built `MLVApp.exe` with the matching Qt runtime path.
6. Recover from common missing-DLL failures.

## Prerequisites

- **Qt** (one of the following):
  - Qt 5: **Win32** `5.6 .. 5.15.2`, **Win64** `5.13.2 .. 5.15.2`
  - Qt 6: `6.5 or later`
- **MinGW32/64 compiler** that matches the installed Qt kit.
- **FFmpeg for Windows**: unpack `platform/qt/FFmpeg/ffmpegWin64.zip` once, then
  copy its contents into each `release/` build directory so the app can shell
  out to `ffmpeg` at runtime.

The official workflow (`.github/workflows/Windows.yml`) provisions Qt
`6.10.2` with the MinGW 13.1 64-bit kit via `aqtinstall`
(`python -m aqt install-qt ... win64_mingw`, `python -m aqt install-tool ...
tools_mingw1310`), installed under `<workspace>\qt\6.10.2\mingw_64` and
`<workspace>\qt\Tools\mingw1310_64`. The separate regression-test workflow
(`.github/workflows/tests.yml`) provisions the same Qt `6.10.2` / MinGW 13.1
toolchain via `aqtinstall`, so both workflows and local verification in this
workspace use one shared toolchain version. The workflow prints and uploads a
`toolchain-receipt-<run-id>` artifact (`Print toolchain` /
`Upload toolchain receipt` steps) recording the exact Qt and compiler
versions used for that run — no hosted build success is claimed here; use
that receipt to confirm what a given run actually built with.

## Qt Creator path

1. Install the Qt kit listed above (MinGW variant).
2. Unpack `platform/qt/FFmpeg/ffmpegWin64.zip` and copy its contents next to the
   built `MLVApp.exe` once the build has produced a `release/` directory.
3. Open `platform/qt/MLVApp.pro` in Qt Creator.
4. Configure the project with the matching MinGW kit.
5. Build and Start.

## Command-line path

The command-line flow mirrors the official `Windows.yml` release workflow and
the `tests.yml` test workflow.

### Release build (Qt 6.10.2 + MinGW 13.1, per `Windows.yml`)

```powershell
python -m pip install --disable-pip-version-check --no-input --only-binary=:all: --require-hashes -r .github/requirements/aqtinstall.txt
python -m aqt install-qt --outputdir qt windows desktop 6.10.2 win64_mingw -m qtmultimedia
python -m aqt install-tool --outputdir qt windows desktop tools_mingw1310 qt.tools.win64_mingw1310

$toolchainRoot = (Resolve-Path qt).Path
$env:PATH = "$toolchainRoot\6.10.2\mingw_64\bin;$toolchainRoot\Tools\mingw1310_64\bin;" + $env:PATH
mkdir platform\build
cd platform\build
..\..\qt\6.10.2\mingw_64\bin\qmake.exe -r ..\qt\MLVApp.pro
..\..\qt\Tools\mingw1310_64\bin\mingw32-make.exe
```

After build:

```powershell
cd release
..\..\..\qt\6.10.2\mingw_64\bin\windeployqt.exe MLVApp.exe
copy ..\..\..\qt\Tools\mingw1310_64\bin\libgomp-1.dll .
copy ..\..\..\qt\Tools\mingw1310_64\bin\libstdc++-6.dll .
copy ..\..\..\qt\Tools\mingw1310_64\bin\libwinpthread-1.dll .
copy ..\..\..\qt\Tools\mingw1310_64\bin\libgcc_s_seh-1.dll .
copy "C:\Program Files\OpenSSL\bin\libcrypto*" .
copy "C:\Program Files\OpenSSL\bin\libssl*" .
```

Return from `platform/build/release` to the repository root, then use the
same verified payload extractor as CI for FFmpeg and RAW2MLV:

```powershell
Set-Location ..\..\..
python -m tools.repo_hygiene.extract_vendored_native_payload --repo-root . --payload-id ffmpeg-windows-x86_64 --archive-reference ffmpegWin64.zip --output-dir platform/build/release --verify-installed
python -m tools.repo_hygiene.extract_vendored_native_payload --repo-root . --payload-id raw2mlv-windows-x86-compat --archive-reference raw2mlvWin64.zip --output-dir platform/build/release --verify-installed
```

### Test-harness build (Qt 6.10.2 + MinGW 13.1, per `tests.yml`)

This is the build recipe used by `.github/workflows/tests.yml`:

```powershell
python -m pip install --upgrade pip
python -m pip install "aqtinstall==3.3.*"
python -m aqt install-qt --outputdir qt windows desktop 6.10.2 win64_mingw
python -m aqt install-tool --outputdir qt windows desktop tools_mingw1310 qt.tools.win64_mingw1310
```

Then build any of the test subtrees with:

```powershell
$env:PATH = "C:\Qt\6.10.2\mingw_64\bin;C:\Qt\Tools\mingw1310_64\bin;" + $env:PATH
New-Item -ItemType Directory -Force tests\build-ci-console | Out-Null
Push-Location tests\build-ci-console
& "C:\Qt\6.10.2\mingw_64\bin\qmake.exe" "..\console\console_tests.pro"
& "C:\Qt\Tools\mingw1310_64\bin\mingw32-make.exe" -j2
Pop-Location
```

The same pattern builds `pipeline_tests`, `gui_tests`, and `perf_tests` by
pointing `qmake` at the matching `.pro` file. See
[`docs/13-testing-infrastructure.md`](13-testing-infrastructure.md) for the
full list.

## Deploying Qt runtime dependencies with `windeployqt`

Run `windeployqt` against the built `MLVApp.exe` to copy the Qt runtime DLLs
and plugins next to the executable:

```powershell
C:\Qt\6.10.2\mingw_64\bin\windeployqt.exe <path-to-MLVApp.exe> --release --no-translations --no-compiler-runtime
```

Use the same flags when the app reports missing `Qt6Core.dll` / `Qt6Network.dll`
or entry-point lookup failures at launch time — `windeployqt` is how those
recover cleanly.

## Runtime execution rules (Windows)

Before running any `MLVApp.exe` binary directly, always use a Qt runtime path
that matches the binary and force it for that launch. Do not mix a Qt 5
runtime against a Qt 6 binary, or vice versa.

Required shell pattern before launch:

- Set `QT_OPENGL=desktop`.
- Set `PATH` so the active Qt runtime comes first, then the active MinGW
  toolchain, then the exe folder:
  - `C:\Qt\6.10.2\mingw_64\bin`
  - `C:\Qt\Tools\mingw1310_64\bin`
  - `<directory containing MLVApp.exe>`
- Launch from the exe directory (or pass absolute paths).

Do not mix `C:\Qt\6.10.2\mingw_64` runtime binaries with a different Qt
runtime in the same launch session.

For profile/test runs, prefer:

```powershell
Set-Location <build-root>\release
$env:QT_OPENGL = 'desktop'
$env:PATH = 'C:\Qt\6.10.2\mingw_64\bin;C:\Qt\Tools\mingw1310_64\bin;' + (Get-Location) + ';' + $env:PATH
.\MLVApp.exe ...
```

If the system reports missing `Qt6Core.dll` / `Qt6Network.dll` or entry-point
lookup failures, rerun:

```powershell
C:\Qt\6.10.2\mingw_64\bin\windeployqt.exe <path-to-MLVApp.exe> --release --no-translations --no-compiler-runtime
```

For a repeatable launch with less chance of error, use the helper script:

```powershell
pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .claude-state\scripts\run-mlvapp.ps1 -ExePath <path-to-MLVApp.exe> -Arguments '--help'
```

If you changed Qt paths, pass `-QtBin ...` and `-MingwBin ...` to the helper.

### Runtime helper

Use `.claude-state\scripts\run-mlvapp.ps1` for deterministic launches. It:

- Prepends the correct Qt and toolchain `bin` directories.
- Sets `QT_OPENGL=desktop`.
- Optionally runs `windeployqt` in-place.
- Launches `MLVApp.exe` with supplied arguments.

## Troubleshooting

- **Missing `Qt6Core.dll` / `Qt6Network.dll` or entry-point lookup failures.**
  Re-run `windeployqt` against the exact `MLVApp.exe` you are launching. Do
  not hand-copy Qt DLLs from a different Qt installation.

- **App launches but immediately exits on a fresh user account.**
  The Qt platform plugins (`platforms\qwindows.dll`) were not deployed next to
  the exe. Re-run `windeployqt` without `--no-translations`-style flags that
  would strip them, or verify the `platforms\` subfolder exists.

- **OpenGL context creation failure on the experimental GPU viewport.**
  Make sure `QT_OPENGL=desktop` is set before launch; the Angle/Direct3D
  fallback paths are not exercised by this project.

- **Runtime DLL mismatch (e.g., `libgomp-1.dll` not found) after moving the
  binary.** Copy `libgomp-1.dll` from the MinGW toolchain next to the exe. The
  official Windows release workflow does this explicitly; see
  `.github/workflows/Windows.yml`.

- **Spanned MLV files (`.m00`, `.m01`, ...) do not open.** Ensure all span
  parts live next to the main `.MLV` file; MLV App expects a flat directory.

- **FFmpeg export fails with "ffmpeg not found".** FFmpeg is not bundled at
  build time. Copy the contents of `platform\qt\FFmpeg\ffmpegWin64.zip` into the
  same `release\` directory as the built `MLVApp.exe`.

## Cross-references

- [`docs/11-build-macos-linux.md`](11-build-macos-linux.md) — macOS and Linux
  build instructions.
- [`docs/12-gpu-viewport-architecture.md`](12-gpu-viewport-architecture.md) —
  the experimental OpenGL viewport this runtime rule enables.
- [`docs/13-testing-infrastructure.md`](13-testing-infrastructure.md) — how
  to build and run the regression-test executables that share this toolchain.
- [`docs/14-performance-benchmarking.md`](14-performance-benchmarking.md) —
  `perf_tests` and the headless `--profile-playback` flow that depend on the
  same Qt runtime path.
- [`AGENTS.md`](../AGENTS.md) — original runtime execution rules and
  workspace-policy notes.
- [`.github/workflows/Windows.yml`](../.github/workflows/Windows.yml) — the
  official Windows release workflow.
- [`.github/workflows/tests.yml`](../.github/workflows/tests.yml) — the CI
  test workflow that provisions the Qt 6.10.2 + MinGW 13.1 toolchain.
