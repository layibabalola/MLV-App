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
`/opt/homebrew/opt/qt@5/bin` and runs on `macos-14`.

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

## Linux (general)

Install Qt (5.6 .. 5.15.2 or 6.4+), FFmpeg (the project targets v3.3.2), and
the other dependencies. The GitHub Linux runner uses:

```bash
sudo apt-get install --no-install-recommends \
  make g++ qt5-qmake qtbase5-dev qtmultimedia5-dev \
  libqt5multimedia5 libqt5multimedia5-plugins \
  libqt5opengl5-dev libqt5designer5 libqt5svg5-dev \
  libfuse2 libxkbcommon-x11-0 appstream
```

Then build:

```bash
cd platform/qt/
qmake MLVApp.pro   # or equivalent (depending on distro, version, etc.)
make -j$(nproc)
./mlvapp
```

A detailed step-by-step guide for compiling MLV App on Linux lives at
[sternenkarten.com/tutorial-englisch](https://sternenkarten.com/tutorial-englisch/)
(courtesy of @seescho).

### AppImage packaging (per `.github/workflows/Linux.yml`)

The official `Linux.yml` workflow produces a portable `.AppImage` on
`ubuntu-22.04`. The full recipe is in the workflow file; the condensed flow
is:

```bash
cd platform/build
qmake ../qt/MLVApp.pro
make -j8
mkdir image
cp ../qt/RetinaIMG/MLVAPP.png image/
cp ../qt/mlvapp.desktop image/

mkdir -p image/usr/bin
tar -C ../qt/FFmpeg/ -xvJf ../qt/FFmpeg/ffmpegLinux.tar.xz --strip=1 --wildcards */ffmpeg
chmod +x ../qt/FFmpeg/ffmpeg
mv ../qt/FFmpeg/ffmpeg image/usr/bin/

tar -C ../qt/raw2mlv/ -xvJf ../qt/raw2mlv/raw2mlvLinux.tar.xz --strip=1 --wildcards */raw2mlv
chmod +x ../qt/raw2mlv/raw2mlv
mv ../qt/raw2mlv/raw2mlv image/usr/bin/

mkdir -p image/usr/plugins/audio
mkdir -p image/usr/plugins/mediaservice
mkdir -p image/usr/plugins/playlistformats
cp -r /usr/lib/x86_64-linux-gnu/qt5/plugins/audio/*          image/usr/plugins/audio/          || true
cp -r /usr/lib/x86_64-linux-gnu/qt5/plugins/mediaservice/*   image/usr/plugins/mediaservice/   || true
cp -r /usr/lib/x86_64-linux-gnu/qt5/plugins/playlistformats/* image/usr/plugins/playlistformats/ || true

export QT_PLUGIN_PATH=/usr/lib/x86_64-linux-gnu/qt5/plugins
linuxdeploy-x86_64.AppImage \
  --desktop-file=image/mlvapp.desktop \
  --executable=mlvapp \
  --appdir=image \
  --plugin=qt \
  --output=appimage \
  --verbosity=3 \
  --icon-file=image/MLVAPP.png
```

The resulting `MLVApp-*.AppImage` is the artifact uploaded by the workflow.

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
