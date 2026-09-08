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
