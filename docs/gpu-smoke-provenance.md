# GPU screenshot evidence

Explicit GUI screenshot smokes capture actual viewport pixels before releasing
each presented frame and emit a separate gpu_present_content event. The evidence
consumer binds its sampled hash, serial, generation and dimensions to the same
request/ready/frame chain as the final screenshot. Raw Bayer parity hashes remain
a separate stream; they do not establish what the viewport displayed.

This instrumentation adds a viewport readback to screenshot smokes. Their timing
is not evidence of no-readback playback performance. Ordinary playback and smoke
runs without screenshot capture retain their existing behavior. The separate GPU
display window currently refuses fresh screenshot capture explicitly because the
graphics-view viewport does not represent that window's displayed surface.

Validation: run tools/profiling/test-gui-smoke-screenshot-provenance.ps1, then the
GUI smoke on a CUDA host with RequireFreshScreenshotRender and the existing
parity and artifact checks. The regression fixtures reject missing hashes, wrong
frame/serial/generation/source/dimensions and later unassociated presentations.
Output-validation mode deliberately disables prepare-only; prove application
async-H2D acceptance/use/exact-match in a separate configuration. A DLL harness
pass alone does not establish application frame-ID plumbing, and the async leg
does not replace the output-parity leg.

Frame IDs remain zero-based in application state. Both upload and reconstruction
convert them with llrpGpuPlaybackReconFrameToken; frame zero is valid, and the
unrepresentable UINT64_MAX value falls back to synchronous work. Both display
adapters use llrpGpuPlaybackReconCombineTiming so reconstruction status survives
independently of scalar timer availability. Retained-device presentation that
does not reconstruct in that call reports no new preupload status. Check every
frame in the async leg for positive accepted/used/exact-match counts; a successful
smoke or zero counters alone cannot establish async operation.

Preupload is submitted from the admitted prepare-only RAW processing path after
bit-depth expansion and other RAW corrections, using the reconstruction input
snapshot. Decoded input can have different bytes for the same frame; the backend
keeps its exact token and byte comparison and safely falls back on a mismatch.
The pipeline regression expands a synthetic 12-bit frame and observes the real
submission boundary for frames zero and one. Disabled and ineligible paths must
not submit. That observer proves the input contract, while GPU smoke telemetry
must independently prove that the application actually consumed async uploads.
