# GPU Viewport Architecture

Migrated from `platform/qt/GPUDisplayFoundation.md`. Originally written by the
maintainers; edits tracked via git history.

The Qt preview path in MLV App has a minimal, opt-in OpenGL viewport
foundation that sits alongside the existing `QGraphicsView` pipeline. This
document describes what that foundation does today, how to toggle it at
runtime, why it is intentionally narrow, and where regression coverage lives.

## What it does

- Keeps the current `QGraphicsView` + `QGraphicsPixmapItem` pipeline intact.
- Optionally swaps the `graphicsView` viewport to a `QOpenGLWidget`.
- Keeps default behavior unchanged unless explicitly enabled.
- Accepts a `QImage` frame handoff from `MainWindow::drawFrameReady()`.
- Also accepts a direct 16-bit RGB frame handoff when the viewport is active
  and display-side overlays are disabled.
- Uploads that frame into a persistent `QOpenGLTexture` and draws it directly
  in the viewport before the rest of the scene overlays are painted.
- Supports a first shader-side display-processing step on that path: zebra
  overlays can now stay on the texture presenter instead of forcing an
  unconditional CPU fallback.
- Lets the GPU viewport choose preview sampling per frame: `nearest` for fast
  playback, `linear` for normal smooth scaling, and `bicubic` for the
  higher-quality preview path that previously existed only on the CPU resizer
  side.
- Falls back to the legacy `QGraphicsPixmapItem` path automatically when the
  experimental viewport is not installed.
- Keeps the last handed-off frame queued even if the OpenGL context is
  created after the image arrives, and releases GL resources cleanly if the
  context is torn down.

## Runtime toggle

Set the environment variable below before launching the app:

```
MLVAPP_EXPERIMENTAL_GL_VIEWPORT=1
```

On Windows local GPU verification hosts, prefer desktop OpenGL too when the
caller has not already chosen a Qt GL backend:

```
QT_OPENGL=desktop
```

When enabled, the app logs whether the experimental viewport was installed
and which OpenGL renderer/context was created.

For test and diagnostics coverage, `GpuDisplayViewport` also exposes two
narrow state queries plus one presenter-quality query:

- `hasPresentedImage(...)` reports whether a frame is currently queued for
  the experimental viewport.
- `isTexturePresentationActive(...)` reports whether that queued frame has
  been uploaded and drawn through the OpenGL texture path.
- `samplingModeFor(...)` reports which GPU sampling mode the presenter is
  currently using for preview scaling.

## Why this is intentionally small

This still does **not** move debayer, processing, or scopes into a GPU
renderer. It is still a narrow preview-presentation step, but scaling quality
is now part of that path:

- Proves an OpenGL context can coexist with the current `QGraphicsView` path.
- Centralizes runtime gating in one helper.
- Replaces the last `QPixmap::fromImage(...)` handoff with a direct texture
  upload when the experimental path is enabled.
- Lets the clean preview path skip the CPU 16->8 reduction by uploading the
  processed 16-bit RGB frame directly when the viewport is active and
  scopes/zebras are off.
- Moves the preview scaler choice into the GPU presenter so the GL path can
  perform nearest, linear, or bicubic scaling without routing through the
  CPU pixmap resizer first.
- Keeps the legacy pixmap item visible whenever the GL path is unavailable
  or cleared.
- Leaves picker, gradient, and other scene overlays on the existing
  `QGraphicsView` / `QGraphicsScene` stack.

## Regression coverage

`tests/gui/test_gui_smoke.cpp` verifies:

- Fallback behavior when the GPU viewport is not installed.
- Queued texture presentation when it is installed.
- Queued 16-bit RGB presentation through the same texture-backed path.
- Sampling-mode state transitions between bicubic and nearest on the GPU
  path.
- Zebra-processing parity on the 16-bit presenter path when the GL driver is
  not the local `llvmpipe` software stack.

## Next safe step

- Validate the new 16-bit presenter handoff on all target platforms and Qt
  versions.
- Promote more of the display-side overlays into the GL path so the 16-bit
  presenter path can stay active while zebras/scopes are enabled.
- If that holds up, promote this from an environment-gated preview path into
  the default renderer and then consider moving color processing into GPU
  shader stages.

## Cross-references

- [`docs/10-build-windows.md`](10-build-windows.md) — Windows runtime rules,
  including `QT_OPENGL=desktop` and `windeployqt` recovery.
- [`docs/13-testing-infrastructure.md`](13-testing-infrastructure.md) —
  `gui_tests` coverage for the GPU presenter seam.
- [`docs/14-performance-benchmarking.md`](14-performance-benchmarking.md) —
  headless `--profile-playback` metadata for GPU preview-processing and
  bilinear debayer selectors.
- `platform/qt/GpuDisplayViewport.{h,cpp}` — the runtime helper described
  above.
- `platform/qt/GpuDebayer.{h,cpp}` — the experimental GPU bilinear debayer
  used by the regression shells.
