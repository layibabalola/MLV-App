# MLV App — Documentation Overview

> **MLV App** is a cross-platform Qt-based editor and transcoder for Magic
> Lantern **MLV raw video**. Think *Lightroom, but for raw video from
> Magic-Lantern-modified Canon DSLRs*.
>
> Repo: <https://github.com/ilia3101/MLV-App> · Website: <https://mlv.app> ·
> License: GPL-3.0 (see [LICENSE](../LICENSE)).

This folder is the canonical source of truth for MLV App. Everything outside
of README stubs and vendored-library READMEs lives here.

## Reading order

The numeric prefix is the intended reading order for a newcomer.

| # | Document | For | Time |
|---|---|---|---|
| [00](00-overview.md) | **Overview** (this file) | everyone | 5 min |
| [01](01-user-guide.md) | **User Guide** — install, import a clip, grade, export | end users, colorists, editors | 20 min |
| [02](02-developer-guide.md) | **Developer Guide** — clone, build, test, contribute | contributors, packagers | 30 min |
| [03](03-technical-specification.md) | **Technical Specification** — architecture, data flow, APIs, threading | engineers, LLMs, reviewers | 60 min |
| [03b](03b-technical-specification-algorithms.md) | **Algorithms & Binary Layouts** — MLV/MAPP byte layouts, LJ92 codec, LLRAWPROC math, 9-stage pipeline pseudocode, receipt schema, CDNG IFDs, profile JSON | engineers, LLMs reconstructing the engine | 60 min |
| [04](04-external-auditor-guide.md) | **External Auditor Guide** — orient yourself, reproduce, verify without prior context | code reviewers, security auditors, due-diligence readers | 45 min |

Supporting / migrated docs (reference after 01–04):

| # | Document | Topic |
|---|---|---|
| [10](10-build-windows.md) | Build on Windows | Qt Creator, MinGW, `windeployqt`, ffmpeg |
| [11](11-build-macos-linux.md) | Build on macOS & Linux | Homebrew, `apt`, Qt sources, AppImage |
| [12](12-gpu-viewport-architecture.md) | GPU viewport architecture | experimental presenter and debayer seams |
| [13](13-testing-infrastructure.md) | Testing infrastructure | `console_tests`, `pipeline_tests`, `gui_tests`, fixtures, CI |
| [14](14-performance-benchmarking.md) | Performance benchmarking | `perf_tests`, `--profile-playback`, baselines |
| [15](15-test-fixtures.md) | Test fixtures | committed MLVs, receipts, golden manifests |
| [16](16-fuzz-testing.md) | Fuzz testing | `fuzz_receipt_loader`, `fuzz_lj92`, `fuzz_mlv_open` |

Diagrams (ASCII + Mermaid) live under [`diagrams/`](diagrams/) and are
referenced from the four headline docs.

## Document conventions

- **File references** use `path:line` form, e.g. `src/mlv/video_mlv.c:4312`.
- **Commands**: `bash` syntax for macOS/Linux; `PowerShell` for Windows.
  Prompts (`$`, `PS>`) are omitted.
- **Environment variables**: `UPPER_SNAKE` (e.g. `MLVAPP_ENABLE_AVX2=1`).
- **Version pinned at the time of writing**: MLV App `1.15.0.0`
  ([platform/qt/MLVApp.pro](../platform/qt/MLVApp.pro)).
- **CI targets**: Qt 6.10.2 + MinGW 13.1 on `windows-latest` ([`.github/workflows/tests.yml`](../.github/workflows/tests.yml)).
- **Sensitive folders**: `.claude/` is a curated agent-scratch directory and
  is never touched by normal documentation updates; new scratch always lives
  in `.claude-state/` per [AGENTS.md](../AGENTS.md).

## What lives where in the repository

```
MLV-App/
  src/                 Core engine (C + small C++): MLV I/O, llrawproc, debayer, processing, dng, batch
  platform/qt/         Qt 5/6 GUI app (primary)
  platform/cocoa/      Legacy Cocoa app (deprecated)
  platform/binning_test/  Downscaling experiment
  tests/               console + pipeline + gui + perf + fuzz
  .github/workflows/   CI (Linux / Windows / macOS Intel / macOS Arm64 / tests)
  osx_installer/       create-dmg wrapper
  pixel_maps/          Focus-pixel & bad-pixel maps shipped with the app
  receipts/            Sample .marxml receipt presets
  docs/                ← you are here
```

Fuller module-by-module breakdown is in [03 — Technical Specification](03-technical-specification.md).

## Status at a glance

| Area | State |
|---|---|
| Qt 5 (5.6 – 5.15.2) support | **Supported** |
| Qt 6 (6.4+; 6.5+ on Windows) support | **Supported** |
| Windows build | **Supported** (MinGW; CI `windows-latest`) |
| macOS Intel build | **Supported** (`macos-13` + Homebrew LLVM) |
| macOS Apple Silicon build | **Supported** (`macos-14` + Homebrew LLVM; Qt 5 from source or Qt 6) |
| Linux build | **Supported** (`ubuntu-22.04`; `.AppImage`) |
| Cocoa app | **Deprecated** |
| GPU viewport | **Experimental** (env-gated; CPU presenter is default) |
| GPU bilinear debayer | **Experimental** (env-gated; CPU is default) |
| GPU preview processing | **Experimental** (env-gated; CPU is default) |
| `raw uint16` decode-ahead prefetch | **Default-on** (disable with `MLVAPP_DISABLE_RAW_UINT16_PREFETCH=1`) |
| AVX2 direct-8-bit fast path | **Build-time opt-in** (`MLVAPP_ENABLE_AVX2=1` at `qmake`) |

## Changelog policy

This folder is kept in sync with the `master` branch state. Supporting docs
(10+) are updated when the behavior they describe changes; the four headline
docs (01–04) are **stable entry points** and should be updated whenever a
user- or engineer-visible interface changes.
