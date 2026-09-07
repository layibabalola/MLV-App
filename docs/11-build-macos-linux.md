# Build on macOS and Linux

Migrated from the macOS (Intel / Apple Silicon) and Linux sections of
`README.md`, with additions from the official release workflows under
`.github/workflows/`. Originally written by the maintainers; edits tracked via
git history.

This document covers:

1. macOS (Intel) with Qt 5 or Qt 6.
2. macOS (Apple Silicon) with Qt 5 (from source) or Qt 6 (prebuilt).
3. Linux (`apt`-based distributions), including the AppImage packaging flow.

## macOS (Intel)

1. Install **XCode** matching your macOS version. Alternatively, install
   **LLVM via Homebrew** so OpenMP-based multithreading is available:

   ```bash
   brew install llvm
   ```

2. Install **Qt**:
   - Qt 5 `5.6 .. 5.15.2`, or
   - Qt 6 `6.4 or later`.

   The official `macOS-Intel.yml` workflow installs `qt5` via Homebrew:

   ```bash
   brew install llvm qt5 openssl
   ```

3. Open `platform/qt/MLVApp.pro` in Qt Creator (or run `qmake` / `make`
   directly from the shell — see the CLI example below).
4. Build and Start.

Command-line flow matching `.github/workflows/macOS-Intel.yml`:

```bash
mkdir platform/build
cd platform/build
/usr/local/opt/qt@5/bin/qmake -r ../qt/MLVApp.pro
make -j8
/usr/local/opt/qt@5/bin/macdeployqt "MLV App.app" -dmg
```

`macdeployqt` is the macOS counterpart of `windeployqt`: it copies the Qt
runtime into the `.app` bundle and produces a `.dmg`.

## macOS (Apple Silicon, with Qt 6)

For Qt 6 on Apple Silicon the flow is the same as Intel:

1. Install XCode (or Homebrew LLVM for OpenMP).
2. Install Qt 6 (`6.4` or later).
3. Open `platform/qt/MLVApp.pro` in Qt Creator.
4. Build and Start.

The official `macOS-Arm64.yml` workflow mirrors the Intel flow against
`/opt/homebrew/opt/qt@5/bin` and runs on `macos-15`; that automated workflow
still uses Qt 5, independently of the manual Qt 6 instructions above.

## macOS (Apple Silicon, with Qt 5 from source)

Qt 5 does not ship prebuilt for Apple Silicon; it must be built from source.
The one-time setup:

1. Install command-line tools. SDK 11.3 is known to work.
2. Install Homebrew:

   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

3. Install Qt build dependencies:

   ```bash
   brew install pcre2 harfbuzz freetype
   ```

4. Install the pinned compiler and add its entries to `PATH` as the Homebrew
   post-install message instructs:

   ```bash
   brew install llvm@11
   ```

5. Install Qt Creator:

   ```bash
   brew install --cask qt-creator
   ```

6. Clone and check out Qt 5.15 sources, then initialize submodules:

   ```bash
   git clone git://code.qt.io/qt/qt5.git
   cd qt5
   git checkout 5.15
   ./init-repository
   ```

7. Build Qt from source with the Apple Silicon device architecture:

   ```bash
   cd ..
   mkdir qt5-5.15-macOS-release
   cd qt5-5.15-macOS-release
   ../qt5/configure -release -prefix ./qtbase -nomake examples -nomake tests QMAKE_APPLE_DEVICE_ARCHS=arm64 -opensource -confirm-license
   make -j15
   ```

8. In Qt Creator, configure the build kit with the installed `llvm@11` and
   the compiled Qt.
9. Open `platform/qt/MLVApp.pro` in Qt Creator.
10. Uncomment the Apple Silicon section inside `MLVApp.pro`.
11. Build and Start.

Alternative: download the easy-to-use
[compiler app from @dannephoto](https://bitbucket.org/Dannephoto/mlv_app_compiler-git/downloads/mlv_app_compiler_arm64.dmg)
and double-click to build.

## Linux (Qt 6.10.2 / GCC 13)

The official release workflow uses Ubuntu 24.04, GCC/G++ 13, and the Qt
6.10.2 `gcc_64` kit including `qtmultimedia`. Qt's prebuilt Linux binaries
require glibc 2.39; building this AppImage on Ubuntu 24.04 does not establish
compatibility with older distributions. See the [Qt 6.10 Linux requirements](https://doc.qt.io/qt-6.10/linux.html).

From the repository root, install the workflow dependencies:

```bash
sudo apt-get update
sudo apt-get install -y make gcc-13 g++-13 libgl1-mesa-dev libegl1 libpulse-dev \
  libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
  libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-xinerama0 \
  libxcb-xfixes0 libxcb-shape0 libfuse2t64 appstream
python -m pip install --disable-pip-version-check --no-input --only-binary=:all: \
  --require-hashes -r .github/requirements/pip.txt
python -m pip install --disable-pip-version-check --no-input --only-binary=:all: \
  --require-hashes -r .github/requirements/aqtinstall.txt
python -m pip check
python -m aqt install-qt --outputdir qt linux desktop 6.10.2 linux_gcc_64 -m qtmultimedia
export QT_ROOT_DIR="$PWD/qt/6.10.2/gcc_64"
export PATH="$QT_ROOT_DIR/bin:$PATH"
mkdir -p platform/build
cd platform/build
qmake ../qt/MLVApp.pro QMAKE_CC=gcc-13 QMAKE_CXX=g++-13 QMAKE_LINK=g++-13
make -j"$(nproc)"
```

The workflow selects GCC 13 before building, verifies the resolved compiler
and Qt versions, and uploads a `toolchain-receipt-<run-id>` artifact. Read the
receipt for the particular run before claiming hosted build success.

The Qt 6.10 multimedia kit links PulseAudio on this runner; `libpulse-dev`
supplies its link and runtime libraries. The workflow checks the installed
multimedia library and FFmpeg plugin with `ldd` before compilation, and fails
on a missing shared library. See [Qt Multimedia on Linux](https://doc.qt.io/qt-6.10/qtmultimedia-linux.html)
for the Linux audio requirements.

### AppImage packaging (per `.github/workflows/Linux.yml`)

Use the complete workflow for packaging. It verifies and extracts the
vendored FFmpeg and RAW2MLV payloads, then copies the Qt 6 multimedia plugins
from `$(qmake -query QT_INSTALL_PLUGINS)/multimedia`. It requires
`libffmpegmediaplugin.so` both before and after copying. Missing plugins fail
the build.

The workflow downloads version-pinned linuxdeploy and its Qt/AppImage
plugins, verifies their SHA-256 hashes, and packages `mlvapp` with
`--plugin=qt --output=appimage`. It uploads the resulting `MLVApp.AppImage`
and a separate release-evidence inventory. The AppImage still requires a
compatible host glibc and graphics stack; packaging alone does not prove
runtime compatibility.

### Debian / Arch / NixOS packages

Third-party packages also exist:

- Debian packages: <http://sid.ethz.ch/debian/mlv-app/> (courtesy of
  @alexmyczko).
- Arch Linux: <https://aur.archlinux.org/packages/mlv.app/> (courtesy of
  davvore33).
- NixOS: <https://search.nixos.org/packages?show=mlv-app>.

## Cross-references

- [`docs/10-build-windows.md`](10-build-windows.md) — Windows build
  instructions and runtime rules.
- [`docs/12-gpu-viewport-architecture.md`](12-gpu-viewport-architecture.md) —
  the experimental OpenGL viewport.
- [`docs/13-testing-infrastructure.md`](13-testing-infrastructure.md) — how
  to build and run regression-test executables.
- [`.github/workflows/macOS-Intel.yml`](../.github/workflows/macOS-Intel.yml)
  and [`macOS-Arm64.yml`](../.github/workflows/macOS-Arm64.yml) — official
  macOS release workflows.
- [`.github/workflows/Linux.yml`](../.github/workflows/Linux.yml) — official
  Linux release workflow and AppImage recipe.
