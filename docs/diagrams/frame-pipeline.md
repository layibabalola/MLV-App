# MLV App — Frame Pipeline (disk to RGB)

Cross-links: [00 Overview](../00-overview.md) | [01 Src Architecture](../../.claude-state/docs-audit/01-src-architecture.md) | [02 Platform UI](../../.claude-state/docs-audit/02-platform-ui.md) | [03 Build & CI](../../.claude-state/docs-audit/03-build-and-ci.md) | [04 Tests & Fixtures](../../.claude-state/docs-audit/04-tests-and-fixtures.md)

## How to read this

The main diagram tracks one frame from the MLV file on disk to a displayable RGB surface. Every stage mutates `uint16` Bayer in place until debayer dispatch, then downstream stages work on RGB planar. The sub-diagram at the bottom shows the Qt-side handoff: `RenderFrameThread` builds a `ReadyFrame`, queues it, and hands over an immutable `PresentationContext` to `MainWindow::drawFrameReady` on the GUI thread.

## Main pipeline (ASCII)

```
 +--------+     +--------------+     +----------------------+     +-------------------+
 | MLV on |     | video_index  |     | Prefetch probe       |     | VIDF read +       |
 | disk   |---> | lookup:      |---> | 4 slots              |---> | LJ92 decompress   |
 | (.mlv) |     | frame_index_t|     | HIT -> raw_uint16    |     | pred 1 / 6 /      |
 +--------+     +--------------+     | MISS -> read block   |     | generic           |
                                     +-----+----------------+     +---------+---------+
                                           | HIT                            |
                                           +----+---------------+-----------+
                                                v               v
                                   +---------------------------------------+
                                   | llrawproc.c (in-place uint16)         |
                                   |   1. Dark frame subtraction           |
                                   |   2. Focus pixel remap                |
                                   |   3. Bad pixel remap (user + auto)    |
                                   |   4. Vertical stripe correction       |
                                   |   5. Dual ISO (20-bit full / preview) |
                                   |   6. Pattern noise removal            |
                                   |   7. Chroma smooth (2x2/3x3/5x5)      |
                                   +------------------+--------------------+
                                                      v
                                   +---------------------------------------+
                                   | debayer.c dispatcher                  |
                                   |   None | Basic | Bilinear | AMaZe    |
                                   |   AHD  | DCB   | RCD | IGV | LMMSE   |
                                   |   (vertical strips, one worker each)  |
                                   +------------------+--------------------+
                                                      v
                                   +---------------------------------------+
                                   | raw_processing.c - 9 stages           |
                                   |  S1 Setup (rebuild 65536 LUTs)        |
                                   |  S2 Shadows/Highlights prep (blur)    |
                                   |  S3 Highest-green hi-light recovery   |
                                   |  S4 Core: Levels -> WB/Color matrix   |
                                   |           -> Creative -> Output mat   |
                                   |  S5 Denoise (2D median)               |
                                   |  S6 RBF edge-aware / clarity / sharp  |
                                   |  S7 Chromatic aberration correction   |
                                   |  S8 Gamma / tonemap / transfer / gamut|
                                   |  S9 Optional direct 8-bit (AVX2)      |
                                   +------------------+--------------------+
                                                      v
                              +------------------------------------------+
                              |                Output routing            |
                              +-----+-------------+----------------+-----+
                                    v             v                v
                           +-------------+ +-------------+ +-------------+
                           | Processed8  | | Processed16 | | DNG writer  |
                           | -> QImage   | | -> ffmpeg / | | saveDngFrame|
                           | -> display  | | pipeline    | | -> .dng seq |
                           +-------------+ +-------------+ +-------------+
```

## Main pipeline (Mermaid)

```mermaid
flowchart LR
    Disk[MLV on disk] --> Idx[video_index lookup]
    Idx --> Probe{Prefetch probe - 4 slots}
    Probe -- HIT --> Llrp
    Probe -- MISS --> Read[VIDF read + LJ92 decompress]
    Read --> Llrp[llrawproc in-place uint16]
    Llrp --> Deb[debayer dispatch - 9 algorithms]
    Deb --> Proc[raw_processing - 9 stages]
    Proc --> Out{Output mode}
    Out -- Processed8 --> Disp[QImage - display]
    Out -- Processed16 --> Ff[ffmpeg / pipeline]
    Out -- Debayered16 --> Dng[DNG writer]
```

## RenderFrameThread handoff sub-diagram

The presentation handoff guarantees the GUI thread reads a consistent snapshot. `RenderFrameThread` allocates a `ReadyFrame`, does all decode/debayer/processing, then packages an immutable `PresentationContext` alongside and emits `drawFrameReady`. `MainWindow` only reads from that context — no back-reference into the worker.

### ASCII

```
   RenderFrameThread (worker)                    MainWindow (GUI thread)
   +-----------------------------+               +----------------------------+
   | 1. Pop request off queue    |               |                            |
   | 2. Wait on queue cond var   |<---- queue ---| requestFrame(N, options)   |
   | 3. getMlvProcessedFrame8/16 |               |                            |
   | 4. Debayer + processing     |               |                            |
   | 5. Build ReadyFrame         |               |                            |
   | 6. Build PresentationContext|               |                            |
   |    (immutable snapshot)     |               |                            |
   | 7. emit drawFrameReady(rf)  |--- signal --->| drawFrameReady(ReadyFrame) |
   +-----------------------------+   (queued)    | 8. Update scopes           |
                                                 | 9. Upload to viewport      |
                                                 |10. Post repaint            |
                                                 +----------------------------+
```

### Mermaid

```mermaid
sequenceDiagram
    participant GUI as MainWindow (GUI thread)
    participant Q as Request queue
    participant RT as RenderFrameThread
    participant Eng as Engine core

    GUI->>Q: requestFrame(N, options)
    RT->>Q: wait on cond var
    Q-->>RT: pop request
    RT->>Eng: getMlvProcessedFrame8/16
    Eng-->>RT: processed pixels
    RT->>RT: build ReadyFrame + immutable PresentationContext
    RT-->>GUI: emit drawFrameReady (queued signal)
    GUI->>GUI: update scopes, upload to viewport, repaint
```

## Playback Resolution Overlay

Playback scale is a pipeline contract, not just a presentation resize. Let
`W x H` be the source frame and `N = W * H`.

| Stage | Domain | x1 | x2 | x4 | x8 |
| --- | --- | --- | --- | --- | --- |
| Raw read/decode/unpack | raw Bayer | `W x H`, `N` | `W x H`, `N` | `W x H`, `N` | `W x H`, `N` |
| Bayer-domain reduction | raw Bayer | none | none today | path 3: `W/4 x floor(H/16)*4`, about `N/16`; path 2: `W/4 x H`, `N/4`; fallback none | path 8: `W/8 x floor(H/32)*4`, about `N/64`; fallback none |
| LLRawProc / Dual ISO | reconstructed Bayer | `W x H`, `N` | `W x H`, `N` | default HQ/Auto mean23 fallback: `W x H`, `N`; path 3: about `N/16`; path 2: `N/4` | path 8: about `N/64`; fallback `N` |
| RGB producer | RGB | debayer `W x H`, `N` | post-recon Bayer-to-RGB block average to `W/2 x H/2`, `N/4` | final `W/4 x H/4`, `N/16` | final `floor(W/8) x floor(H/8)`, about `N/64` |
| Processing / 16-to-8 | processed RGB | `N` | `N/4` | `N/16` | about `N/64` |
| Presentation | display RGB | viewport | viewport | viewport | viewport |

The current x8 breakthrough is path 8: full raw decode, then Bayer-domain
reduction before LLRawProc/Dual ISO and before debayer. If `phase4b_path=0`
at a reduced playback scale, the frame used the late full-recon fallback and
the scale happened after LLRawProc. The profile JSON exposes this through
`render_thread_phase4b_path`, `render_thread_phase4b_fallback_reason`, and
the `render_thread_stage_*_{width,height,pixels}` fields.

Playback preview mode controls how aggressively the app applies this contract:

- `Sharp / Smooth Preview` is the default quality-first policy. It keeps the
  conservative x4 HQ mean23 full-recon fallback when that is the safer visual
  choice.
- `Aggressive Performance Preview` opts into faster preview work, including
  x4 HQ mean23 early reconstruction where the Phase 4B gates pass and raw
  uint16 decode-ahead overlap for compatible reduced Dual ISO x4/x8 previews.
- x2 is still a late-scale mode today: it pays full raw decode and full
  LLRawProc/Dual ISO before reducing RGB work. Treat x2 as less hardened for
  fastest playback until a real early x2 Bayer reduction path exists.

## Notes

- Prefetch telemetry: `raw_uint16_prefetch_hit`, `raw_uint16_prefetch_decode_failures`. Disable with `MLVAPP_DISABLE_RAW_UINT16_PREFETCH=1` to restore thread-local decode telemetry.
- Preview-mode telemetry: `render_thread_preview_mode`,
  `render_thread_aggressive_preview`, `playback_preview_mode`, and
  `playback_aggressive_preview`.
- Stage 9 (direct 8-bit fast path) is gated by `MLVAPP_ENABLE_AVX2=1` at `qmake` time plus runtime dispatch via `processingFastPathAvx2Active`.
- Dual ISO fixtures exercise LJ92 predictor 1, not pred6 — optimise the generic path.
