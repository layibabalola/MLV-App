# GPU Lane — Playback & Export Roadmap + UX (plan of record)

Status: 2026-06-19. Lane A E0-E2 export, P-pre GPU AMaZE/processing
parity, and Lane B P1-P3 are now through their scoped proof gates. P3 is
honest-scoped, not universal: the RTX 4090 FastProxy proof validates the
raw-fixes-enabled HQ Dual ISO no-readback CUDA-to-GL R16 texture path with
GL/backend/oracle parity; unsupported states still fail closed to CPU readback
or CPU presentation. The remaining priority order is P4 adaptive-quality polish,
then Lane A E3/E4 export pipeline/rendered export, then Lane C portable GPU
backends.

Update 2026-06-19: P-pre **processing parity** has progressed beyond the
original curve-first `allow_creative_adjustments` plan: creative slices 1-6 are
ported, and the later scoping pass records the creative family plus tractable
non-creative per-pixel stages as RTX 4090 validated (see §8.1-§8.2). Remaining
P-pre work is the explicitly scoped spatial/sequential stage decision path, not
the old Slice 1 contrast-curve start. Note also that Lane A **E1** is currently
realized as an off-by-default *shadow validator*
(`MLVAPP_GPU_EXPORT`): `llrawproc` runs the CUDA recon into a scratch buffer and
copies it over the CPU output only when byte-identical, so the CPU path stays
authoritative until the E2 parity gate promotes it.

Update 2026-06-18: Lane B **P1/P2** has an experimental readback bridge behind
`MLVAPP_GPU_PLAYBACK_RECON=1`. Playback render/recon threads opt in explicitly,
then `llrawproc` prepares only the CPU-side Dual-ISO match/LUT state and runs
`igpu_recon` to `IGPU_OUT_CPU16` in a temporary output buffer that is copied
back only after success; on missing DLL, unsupported config, invalid state, or
backend error it falls back to the existing CPU `diso_get_full20bit`.
The bridge is limited to the already-proven v1 recon shape
(`MEAN23 + alias_map ON + fullres ON + chroma OFF`) and is telemetry-only: it
does not add a GUI quality claim, no-readback present path, or adaptive mode.
Unlike export's CPU-authoritative shadow replacement, playback P2 deliberately
trusts a successful backend `rc==0` and does not run a per-frame CPU memcmp;
that trust boundary is accepted only for this experimental, env-gated bridge
after the 4090 parity pass, with any future shadow-verify mode tracked as a
canary/follow-up rather than the normal playback path.

Update 2026-06-18 P3 surface: `MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT=1`
is now an explicit request surface. In the narrow experimental x1 GPU preview
processing + `Decode/Reconstruct/Process` playback shape, the Qt GL viewport can
present the P2 reconstructed Bayer frame through a `GL_R16`/Bayer16 texture and
shader-side bilinear debayer/display processing. Telemetry labels this as
`source=cpu16_readback_reconstructed_bayer` and records
`gpu_playback_recon_texture_present_no_readback_active=false`; it is a presenter
surface, not the final P3 win. Activating true P3 still requires a GUI-thread-safe
CUDA `IGPU_OUT_GL_TEXTURE` producer (or final RGB CUDA-to-GL backend) so the
recon output reaches the display without the CPU16 readback.

Update 2026-06-18 P4 status/telemetry slice: the existing Playback Quality
UI/status surface now classifies the presented pipeline as `CPU`, `GPU Preview`,
`GPU RB`, `GPU Tex RB`, or `GPU Tex NR`. Playback smoke logs emit matching
machine-readable tokens (`cpu`, `gpu_preview`, `gpu_recon_readback`,
`gpu_texture_readback`, `gpu_texture_no_readback`) on per-frame GPU telemetry
and a session summary. The 2026-06-19 scoped P3 producer may report `GPU Tex NR`
/ `gpu_texture_no_readback` only when the validated CUDA-to-GL no-readback path
actually presents the frame; readback or unsupported fallback remains reported
as `GPU Tex RB`, `GPU RB`, or `CPU`.

Update 2026-06-19 P3 proof: `MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT=1`
can now present the proven raw-fixes-enabled HQ Dual ISO shape through
`source=cuda_gl_r16_texture` with
`gpu_playback_recon_texture_present_no_readback_active=true`. The accepted
UltraMagnus RTX 4090 run (`20260619T002209`, release executable SHA256
`F70CE56F8418E4107D1AF502F31A3B94399E92253016EC4950E145AB59922CAE`) reported
`correctnessValidated=true`, `gpu_texture_no_readback_frames=94`,
`glParityMatchCount=10`, `glMismatchTotal=0`, and advancing GL texture hashes.
The raw-fixes-off control receipt remains non-proof by design and must not arm
no-readback.

Update 2026-06-19 P4 default slice: clean playback settings now default to
`Auto` instead of `Fast`, matching the user-facing mode plan below while still
round-tripping explicit `Fast` selections. This is only the first adaptive
quality polish step. The Auto sampler also keeps headroom-based sharpening at
HQ x4 until the caller has observed a validated no-readback presentation path;
capability-aware promotion/demotion remains scoped by the P3 proof gate and
must keep unsupported states on readback/CPU paths. Paired GUI-smoke A/B review
now has a durable comparator (`tools/profiling/compare-release-gui-smoke-ab.ps1`)
that reports screenshot pixel deltas, GUI/presented/timeline FPS deltas, and an
optional screenshot-drift failure verdict from two `run-release-gui-smoke.ps1`
JSON outputs.
The Auto tooltip and playback smoke summary now report both milliseconds and
FPS-equivalent cadence for the latest Auto decision (`auto_avg_fps_equivalent`
and `auto_budget_fps_equivalent`), so adaptive decisions can be read without
manual conversion.
The visible playback quality menu/status now uses the roadmap vocabulary:
`Auto`, `Prioritize Quality`, and `Prioritize Smoothness`. The underlying mode
ids and legacy `fast`/`hq` automation names remain supported; the parser also
accepts `prioritize-smoothness` and `prioritize_quality`.
The GUI smoke wrapper now defaults its validation gate to deterministic Auto
mode (`quality_mode=2`) and x4 scale request by forcing those env selectors,
so persisted GUI settings cannot create stale false failures. Smokes that
intentionally exercise saved GUI state can pass `-UsePersistedPlaybackSettings`,
and explicit forced-mode probes can still pass `-QualityMode`, `-ScaleFactor`,
and `-Expected*` overrides.

Update 2026-06-19 P4 capability-telemetry slice: Auto sampler decisions now
carry the exact `sharperHeadroomScaleAllowed` gate that decides whether
headroom may promote a non-Dual-ISO clip from `HQ x4` to `HQ x2`. The status
tooltip, playback-profile frame JSON, and playback smoke summary expose this as
`auto_headroom_capability_last`, so reviewers can tell whether Auto sharpened
because the scoped true no-readback texture path was actually observed or held
back for lack of capability proof. This is evidence/control polish only; it
does not widen the P3 no-readback scope.
The follow-on P4 control slice latches a session-scoped
`auto_validated_no_readback_capability_observed` flag only after an accepted
presented frame reports the validated `GPU Tex NR` pipeline. Auto headroom
promotion now uses that latched capability rather than the current frame alone,
so a real no-readback proof can inform later Auto decisions in the same session
without treating mere FPS headroom or GPU presence as proof.
If a later frame is still a no-readback candidate but falls back before
presenting `GPU Tex NR`, the latch is cleared and
`auto_validated_no_readback_capability_demoted_last` reports the demotion. That
keeps Auto's capability-aware promotion/demotion tied to actual presentation
truth instead of stale optimism.
The latch is playback-run scoped: clip opens, play stop/start, quality-mode,
preview-mode, preview-resolution, scale override, and Auto target-FPS changes
all reset it, so a later context must present `GPU Tex NR` again before Auto
uses no-readback capability to sharpen quality decisions.
The same reset path now also reseeds the active Auto scale/HQ decision to the
current mode's initial state, and clip open reseeds after the new object becomes
current, so stale x2/headroom or Fast-demotion decisions cannot leak into a new
run before the sampler and capability gate observe that context.
The default/fallback path now uses the same configured `Auto` default for
missing, invalid, corrupt, or unavailable hidden Phase3 quality settings instead
of silently falling back to legacy `Fast`, with console coverage in
`PlaybackQualitySettings.RoundTripQualityMode`.
The adjacent preview-mode override parser now accepts the same natural
case-insensitive forms for `MLVAPP_PLAYBACK_AGGRESSIVE_PREVIEW` and
`MLVAPP_PLAYBACK_PREVIEW_MODE` (`Aggressive`, `AGGRESSIVE-PERFORMANCE`,
`Sharp-Smooth`, `ON`, `Off`, etc.) while preserving the existing
`MLVAPP_PLAYBACK_AGGRESSIVE_PREVIEW` precedence and invalid-value fallback, with
console coverage in `PlaybackPreviewModeOverride.ParsesCaseInsensitiveEnvNames`.

Update 2026-06-19 Lane A E3 prep: the export-stage profiler now records
`queue_idle_ms` as a supported stage. The first frame has no prior handoff gap,
while later frames measure the elapsed time between the previous profiled frame
finishing and the next `saveDngFrame` call beginning. This keeps current serial
exports byte-inert while giving future pipelined export experiments a scheduler
starvation/overlap signal before CPU decode workers, the single GPU recon queue,
or CPU compress/write workers are promoted. Async-writer experiments now also
record `producer_frame_ms`, the caller-side time from frame-save entry until the
serial path finishes or the async path enqueues the immutable payload, plus
`producer_queue_idle_ms`, the gap between the previous caller-side return and
the next frame-save entry. The older `queue_idle_ms` remains previous profiled
frame completion to next frame-save entry, which means async profiles can show
writer-thread completion lag there even after the producer has returned.
`writer_completion_lag_ms` is derived as `frame_total_ms - producer_frame_ms`,
making that post-producer writer lag explicit in release-tree profiles. Real
release-tree baseline and candidate runs should use
`tools/profiling/run-release-cdng-export-profile.ps1`, which launches the
current `MLVApp.exe --batch` CDNG export path with
`MLVAPP_EXPORT_STAGE_PROFILER=1` and writes the profile JSON next to the exported
DNG output bundle. The runner can pass a bounded `-MaxFrames` cap through to
batch `--max-frames`, so real-footage probes can avoid unbounded DNG output
before full benchmark matrices are intentional. Headless batch export now also
accepts `--cdng-codec uncompressed|lossless|fast-pass`, and the profiling
wrappers expose that as `-CdngCodec` / per-case `cdngCodec`, so E3 can run a
representative lossless-DNG writer-heavy matrix without changing the default
uncompressed batch path. `tools/profiling/compare-export-stage-profiles.ps1` now
summarizes frame-total avg/p95 deltas plus queue-idle avg/p95 deltas in stdout,
and `-FailOnRegression` gates both avg and p95 frame-total regressions.
`tools/profiling/run-release-cdng-export-profile-matrix.ps1` wraps the paired
A/B runner across named cases and repeats, writing a single
`matrix-summary.json` with per-run frame-total, producer-frame, queue-idle,
writer-completion-lag, payload handoff (`payload_clone_ms`), writer-queue-wait, wrapper wall-clock
elapsed-time deltas, async queue capacity, and async max-queued fields. Tiny
fixture matrix runs are smoke tests only; E3 promotion still requires a bounded
real-footage matrix with receipts/frame caps that match the export scenario
under review. A/B and matrix summaries now also label the comparison as
`feature-ab` or `identity-aa`, so baseline-vs-baseline calibration runs cannot
be mistaken for feature promotion or regression evidence. The A/B runner also
supports explicit `BaselineFirst` or `CandidateFirst` execution, and the matrix
runner can alternate run order across repeats with `-AlternateRunOrder`, so
future promotion packets can reduce warm-cache/order bias without changing the
baseline/candidate output locations or comparator contract.

Update 2026-06-19 Lane A E3 payload contract: `dngFramePayload_t`,
`buildDngFramePayload`, `writeDngFramePayload`, `saveDngFrameViaPayload`, and
`freeDngFramePayload` now provide an immutable header+image handoff for a built
DNG frame. The default GUI/batch export path remains serial through
`saveDngFrame`, but setting `MLVAPP_CDNG_EXPORT_PAYLOAD_HANDOFF=1` or passing
`-UsePayloadHandoff` to `tools/profiling/run-release-cdng-export-profile.ps1`
routes CDNG export through a serial build-payload/write-payload boundary. The
same payload contract now also has an opt-in single writer-worker path behind
`MLVAPP_CDNG_EXPORT_ASYNC_WRITER=1` / `-UseAsyncWriter`; the queue defaults to
one in-flight payload so current behavior remains bounded, but
`MLVAPP_CDNG_EXPORT_ASYNC_WRITER_QUEUE_DEPTH` / `-AsyncWriterQueueDepth` can
raise that bounded depth for release-tree experiments. The profiler JSON
records `payload_handoff_env_enabled`, `async_writer_env_enabled`,
`async_writer_queue_capacity`, and `async_writer_max_queued`, and the pipeline
test suite verifies byte-for-byte parity for uncompressed and compressed tiny
Dual-ISO DNG exports, including the async writer path. `tools/profiling/run-release-cdng-export-profile-ab.ps1` runs
paired release-tree baseline/candidate exports and writes both profiles plus
`compare.json`/`summary.json`, so E3 experiments have one repeatable promotion
packet. Payload-handoff profiles now expose `payload_clone_ms`, the historical
field name for the small header copy plus large image-buffer ownership
handoff/replacement cost, and async writer profiles expose
`writer_queue_wait_ms`, which separates bounded writer-queue backpressure from
real `disk_write_ms`. Paired A/B summaries include producer-frame and
producer-queue-idle deltas plus writer-completion-lag deltas so larger
decode/GPU/write scheduler work is not promoted on a blended completion signal.
This is scheduler prep, not a throughput claim.

Update 2026-06-19 Lane A E3 matrix finding: the first bounded real-footage
matrix with wrapper elapsed timing is
`.claude-state/profiling/2026-06-19-cdng-e3-real-matrix-elapsed/matrix-summary.json`.
It used `C:\temp\MLV\M16-1210.MLV`, `M16-1327.MLV`, and `M16-1347.MLV` with
`C:\temp\MLV\master.marxml`, `maxFrames=8`, `repeats=2`, payload handoff,
async writer, queue depth 2, and frame-total regression gates enabled. Verdict:
FAIL, with 4/6 runs passing and 2/6 failing. Overall wrapper elapsed delta
averaged -118.551 ms, but `m16-1210-master` averaged +160.28 ms, and the matrix
still recorded frame-total avg/p95 gate failures on `m16-1210-master` repeat 1
and `m16-1327-master` repeat 1. The async writer therefore remains
non-promoted: queue wait stayed 0.0 ms, async max queued stayed 1, average
writer-completion lag was 3.379 ms, and average payload handoff cost was
2.089 ms. Next E3 work should either reduce/avoid the payload copy or run a
serial payload-only matrix to separate payload overhead from writer-thread
overlap before any broader scheduler rewrite.

Follow-up payload-only matrix:
`.claude-state/profiling/2026-06-19-cdng-e3-payload-only-matrix/matrix-summary.json`
used the same three clips, receipt, `maxFrames=8`, and `repeats=2`, but enabled
only `-CandidateUsePayloadHandoff` without async writer. Verdict: FAIL, with
4/6 runs passing and 2/6 failing. Overall wrapper elapsed delta averaged
-38.813 ms, but `m16-1210-master` averaged +204.40 ms and still produced one
avg/p95 frame-total gate failure; `m16-1327-master` produced one p95-only gate
failure despite elapsed improvement. Writer-completion lag was effectively zero
(0.000208 ms average), while payload handoff cost averaged 2.143 ms. This
pointed the next E3 implementation step at avoiding the extra large image
payload copy, while retaining the small header copy, instead of widening async
writer concurrency.

Image-buffer handoff follow-up:
`.claude-state/profiling/2026-06-19-cdng-payload-move-final-two-frame/profile.json`
proved the real compressed-input/uncompressed-output two-frame crash repro clean
on committed build `c5d92baf` after the payload boundary was narrowed to a small
header copy plus large image-buffer ownership move. The profile wrote two
`M16-1210` DNGs at 8,202,254 bytes each and reported `payload_clone_ms`
averaging 0.0147 ms. The committed-build bounded real-footage payload-only
matrix
`.claude-state/profiling/2026-06-19-cdng-e3-payload-move-payload-only-matrix-final/matrix-summary.json`
then completed without crashes on the same three clips, receipt, `maxFrames=8`,
and `repeats=2`; all 6/6 runs passed, all six baseline/candidate run outputs
matched with zero DNG SHA256 mismatches, payload handoff cost averaged
0.01515 ms, wrapper elapsed averaged -182.774 ms, and frame-total average delta
averaged -11.402 ms. This promotes the serial payload handoff as the bounded E3
candidate slice, while preserving the scope boundary: it is still a 3-clip,
8-frame, 2-repeat release-tree matrix with one receipt set, not a broad export
benchmark. The matching committed-build async-writer matrix
`.claude-state/profiling/2026-06-19-cdng-e3-payload-move-async-matrix-final/matrix-summary.json`
also completed with zero DNG SHA256 mismatches and 0.01469 ms average payload
handoff cost, but remained non-promoted at 3/6 PASS and 3/6 FAIL. Queue wait
stayed 0.0 ms, async max queued stayed 1, and writer-completion lag averaged
3.291 ms. Next E3 work should keep serial payload handoff gated for broader
promotion proof, and investigate why the async path never exceeds one queued
payload and does not convert writer lag into stable frame-total gains before any
async scheduler rewrite is promoted.

Broader serial-payload promotion-gate follow-up:
`.claude-state/profiling/2026-06-19-cdng-e3-payload-promotion-matrix/matrix-summary.json`
reran the committed serial payload handoff on the same three clips and receipt
with `maxFrames=24` and `repeats=3`. The matrix completed byte-correctly: all
nine baseline/candidate DNG output sets matched with zero SHA256 mismatches, the
average payload handoff cost stayed at 0.015337 ms, wrapper elapsed averaged
-120.410 ms, writer-completion lag averaged 0.000141 ms, and writer queue wait
stayed 0.0 ms. Verdict: FAIL, with 7/9 runs passing and two narrow timing-gate
misses: `m16-1210-master` repeat 3 tripped the frame-total p95 gate by 10.371%
despite -269.677 ms elapsed improvement, and `m16-1327-master` repeat 3 tripped
the frame-total average gate by 5.402% despite -107.357 ms elapsed improvement.
This keeps the serial payload handoff as a correct, low-cost E3 candidate, but
does not promote it beyond the earlier bounded 8-frame gate. Next E3 work should
calibrate the promotion gate with an A/A timing-variance matrix or equivalent
methodology proof before treating marginal 24-frame frame-total deltas as a
feature blocker or broad promotion signal.

A/A gate-calibration follow-up:
`.claude-state/profiling/2026-06-19-cdng-e3-aa-promotion-matrix/matrix-summary.json`
reran the same 24-frame, 3-repeat, 3-clip matrix with identical serial export
settings on both sides (`comparisonMode=identity-aa`, no payload handoff, no
async writer). The outputs again matched with zero DNG SHA256 mismatches, but
the strict 5% average / 10% p95 frame-total gates still reported Verdict: FAIL,
with 5/9 runs passing and 4/9 runs failing. Overall elapsed averaged -82.335 ms,
frame-total average delta averaged +0.897 ms, frame-total p95 delta averaged
+8.060 ms, writer-completion lag averaged 0.000070 ms, and writer queue wait
stayed 0.0 ms. The identity failures were
`m16-1210-master` repeat 1 p95 +10.952%,
`m16-1327-master` repeat 1 p95 +15.254%,
`m16-1347-master` repeat 1 average +5.827%, and
`m16-1347-master` repeat 2 average +8.389% plus p95 +13.915%. This confirms the
24-frame gate is currently a timing-variance detector, not a reliable standalone
promotion/blocker oracle for the serial payload handoff. Future E3 promotion
should either use a variance-adjusted criterion, longer/stratified samples, or
an explicit A/A companion threshold before broadening the serial payload handoff
claim.

A/A-envelope serial-payload rerun:
`.claude-state/profiling/2026-06-19-cdng-e3-payload-aa-calibrated-matrix/matrix-summary.json`
then reran the feature comparison with rounded companion thresholds from the
identity matrix (`maxFrameTotalRegressionPercent=8.5`,
`maxFrameTotalP95RegressionPercent=15.5`). The comparison metadata correctly
reported `comparisonMode=feature-ab`, all nine DNG output sets again matched
with zero SHA256 mismatches, average payload handoff cost was 0.015769 ms,
wrapper elapsed averaged -110.241 ms, frame-total average delta averaged
-0.639 ms, frame-total p95 delta averaged -0.886 ms, writer-completion lag
averaged 0.000178 ms, and writer queue wait stayed 0.0 ms. Verdict still
remained FAIL at 7/9 PASS and 2/9 FAIL: `m16-1210-master` repeat 1 exceeded the
p95 companion gate at +22.514%, and `m16-1347-master` repeat 2 exceeded both
average (+9.638%) and p95 (+20.599%) companion gates. This does not point back
to payload-copy cost; it shows the current short real-footage matrix is still
order/noise sensitive enough that broad serial-payload promotion should remain
held at the earlier bounded 8-frame claim until E3 has a stronger methodology
such as longer samples, randomized ordering, or paired A/A-per-feature runs.

Alternating-order methodology follow-up:
`tools/profiling/run-release-cdng-export-profile-ab.ps1` and
`tools/profiling/run-release-cdng-export-profile-matrix.ps1` now record
`runOrder` and can alternate baseline/candidate execution across matrix repeats.
The committed-tooling A/A alternating matrix
`.claude-state/profiling/2026-06-19-cdng-e3-aa-alternating-matrix/matrix-summary.json`
reported `comparisonMode=identity-aa`, `alternateRunOrder=true`, zero DNG
SHA256 mismatches, and Verdict: FAIL at 5/9 PASS and 4/9 FAIL. Aggregate elapsed
delta averaged +58.827 ms, frame-total average delta averaged -0.033 ms, and
frame-total p95 delta averaged +7.724 ms; strict-gate failures still reached
avg +10.638%, p95 +11.543%, avg +10.308% plus p95 +14.586%, and avg +7.841%
plus p95 +48.743%. The matching serial-payload alternating matrix
`.claude-state/profiling/2026-06-19-cdng-e3-payload-alternating-matrix/matrix-summary.json`
reported `comparisonMode=feature-ab`, `alternateRunOrder=true`, zero DNG SHA256
mismatches, and Verdict: FAIL at 7/9 PASS and 2/9 FAIL. Aggregate elapsed
averaged -64.279 ms, frame-total average delta averaged -1.700 ms, frame-total
p95 delta averaged -0.284 ms, and payload handoff averaged 0.016170 ms, but two
candidate-first repeats still exceeded the p95 gate (+14.454% and +18.477%).
This reinforces the current E3 status: serial payload handoff is correct and
low-cost, but broad promotion still needs longer/stratified sampling or a
statistical matrix comparator; alternating order alone is a useful control, not
a sufficient promotion oracle.
That comparator now exists as
`tools/profiling/compare-cdng-export-matrices.ps1`. The first calibration packet
at
`.claude-state/profiling/2026-06-19-cdng-e3-payload-alternating-calibration/calibration.json`
compared the alternating identity matrix against the alternating serial-payload
matrix and reported `verdict=WITHIN_IDENTITY_ENVELOPE` while preserving
`identityRawGateUnstable=true`. Frame-total average stayed inside the identity
positive envelope (identity max +12.456658 ms, feature max +6.042370 ms), and
frame-total p95 also stayed inside it (identity max +64.821900 ms, feature max
+27.284600 ms). Payload handoff and tiny writer-lag deltas are expected feature
costs, not frame-envelope regressions. This is enough to say the serial payload
handoff's 24-frame alternating evidence is not worse than measured A/A jitter,
but the roadmap still does not promote it as a broad export-throughput win until
the sampling strategy itself is stronger.
The comparator now writes
`release-cdng-export-matrix-calibration.v3`, fails closed on non-identity
calibration inputs, non-feature candidate inputs, alternate-run-order mismatch,
or case/repeat/run-order key mismatch, and includes per-case envelopes plus
median/p95/positive-max summaries and stage-attribution metrics from each
run's `compare.json`. Its `stageAttribution` object also names the dominant
positive feature-average stage and the dominant positive-max identity-envelope
excess stage, so E3 promotion/blocker packets can cite the leading stage driver
without hand-parsing the metrics array. Validation against the same alternating A/A and
serial-payload matrices produced
`.claude-state/profiling/2026-06-19-cdng-e3-payload-alternating-calibration-v2/calibration.json`
with `verdict=WITHIN_IDENTITY_ENVELOPE`, `compatible_keys=True`, and
`modes_ok=True`; negative smokes under
`.claude-state/profiling/2026-06-19-cdng-e3-calibration-negative-smoke/`
blocked an identity-as-feature input and a mismatched lossless feature matrix as
`INCOMPATIBLE_MATRICES`. This upgrades the E3 methodology guardrail: future
promotion packets must compare matching identity and feature matrices before a
noisy raw gate is interpreted as either a blocker or a throughput win.

Async-writer queue-depth investigation:
the current real-footage matrices exercise the batch CDNG receipt/default path,
where producer-frame work is dominated by decode/recon/pack at roughly
hundred-millisecond scale while `disk_write_ms` is only a few milliseconds. The
single writer worker therefore drains each payload before the next payload is
usually ready, so `async_writer_max_queued=1` is expected even when the configured
queue capacity is 2. That makes the async writer non-promotable for this
workload, but it is not by itself a queue-capacity bug. Next async E3 proof
should use a deliberately writer-heavy/compressed-output scenario or a targeted
stress harness before changing scheduler policy; the current matrices mainly
show that serial payload handoff is cheap and that write overlap is not the
bottleneck for the measured default path.

Targeted async-writer stress control:
`MLVAPP_CDNG_EXPORT_ASYNC_WRITER_DEBUG_DELAY_MS` and
`-AsyncWriterDebugDelayMs` now provide a test/profiling-only writer-thread delay
that is inert unless explicitly opted in. The pipeline regression
`DualIsoPipeline.DngFrameAsyncWriterDebugDelayCanFillConfiguredQueue` verifies
that a queue depth of 2 can report `async_writer_max_queued=2`, and the
release-tree smoke packet at
`.claude-state/profiling/2026-06-19-cdng-async-delay-release-smoke-18118e78/profile.json`
exported two `M16-1210` frames on build
`18118e7893f43174ffc275020a53c27c0e1fbc87` with
`async_writer_queue_capacity=2`,
`async_writer_debug_delay_ms=2000`, and `async_writer_max_queued=2`. This
validates queue-capacity mechanics under synthetic writer backpressure, not a
real throughput win; async writer promotion still needs a representative
writer-heavy workload or scheduler result that beats the serial payload boundary
without DNG mismatches.

Batch CDNG codec selector:
`MLVApp --batch` still defaults to uncompressed CDNG, but `--cdng-codec
lossless` now selects the existing lossless-JPEG DNG path and `--cdng-codec
fast-pass` selects the existing pass-through mode. The release profiling
wrappers carry the same selector into single, A/B, and matrix runs. This is
tooling for the next E3 proof: it makes a representative lossless-output,
writer/heavier-compress matrix possible without broadening the production
default or changing GUI export behavior.
The committed-build lossless smoke at
`.claude-state/profiling/2026-06-19-cdng-codec-lossless-smoke-037d3d59/profile.json`
ran build `037d3d5940e4989eac5ba6f3d74b289225819af0`, logged
`cdng-codec=lossless`, exported two `compressed_raw` frames, and recorded two
`dng_compress_ms` samples (average 58.84905 ms). The matching lossless
payload-handoff A/B smoke at
`.claude-state/profiling/2026-06-19-cdng-codec-lossless-payload-ab-037d3d59/summary.json`
reported `cdngCodec=lossless`, `verdict=PASS`, and the two baseline/candidate
DNG pairs matched byte-for-byte
(`A4356D40D811982CF08D0900F5AC68B5C7A76B0918B614AA71E63DAD9F9838A1` and
`7D6D00C078EAE41326F1801DFBCD3A5872D7DFDADE962CBC90E71616EF758D51`). This
proves the lossless profiling surface and serial payload boundary work together
on a two-frame smoke; it is not a throughput promotion or scheduler claim.
The follow-up lossless real-footage matrix at
`.claude-state/profiling/2026-06-19-cdng-e3-lossless-payload-matrix-74b52e39/matrix-summary.json`
ran build `74b52e3900d7230ba806165937c3489057df2b5c` on the same three clips
and receipt with `--cdng-codec lossless`, `maxFrames=8`, `repeats=2`,
`-AlternateRunOrder`, and serial payload handoff. Verdict: PASS, 6/6 runs. The
separate hash sweep at
`.claude-state/profiling/2026-06-19-cdng-e3-lossless-payload-matrix-74b52e39/dng-hash-comparison.json`
reported 48/48 baseline-vs-payload DNG pairs matched by length and SHA256, with
0 mismatches and 0 missing files. Aggregate payload handoff cost averaged
0.011327 ms, while wrapper elapsed averaged +515.825 ms and frame-total average
delta averaged +61.088788 ms (p95 delta averaged -133.297083 ms). This broadens
the serial payload boundary's correctness evidence to lossless-output export,
but it still does not promote a throughput win: the matrix was short, timings
remain order/noise sensitive, and `dng_compress_ms` still runs producer-side
before any async writer handoff. Next E3 promotion work should therefore focus
on stronger sampling/statistical methodology or an explicit writer-heavy
scheduler result, not on treating this lossless PASS as broad export throughput
proof.
The matching lossless identity A/A matrix at
`.claude-state/profiling/2026-06-19-cdng-e3-lossless-identity-matrix-e41e7de1/matrix-summary.json`
also passed 6/6, and its hash sweep reported 48/48 DNG pairs matched with 0
mismatches. The v2 calibration packet at
`.claude-state/profiling/2026-06-19-cdng-e3-lossless-calibration-e41e7de1/calibration.json`
first compared that identity matrix against the earlier lossless payload matrix
and reported `verdict=EXCEEDS_IDENTITY_ENVELOPE`; profile review showed that
earlier payload matrix carried multi-second `disk_write_ms` outliers in both
baseline and candidate runs, while the fresh identity run stayed near 1-2 ms.
A same-condition payload rerun at
`.claude-state/profiling/2026-06-19-cdng-e3-lossless-payload-matrix-rerun-e41e7de1/matrix-summary.json`
passed 6/6 and its hash sweep again reported 48/48 matched DNG pairs with 0
mismatches. The calibrated rerun at
`.claude-state/profiling/2026-06-19-cdng-e3-lossless-calibration-rerun-e41e7de1/calibration.json`
still reported `verdict=EXCEEDS_IDENTITY_ENVELOPE`, but the boundary narrowed:
frame-total average positive max was inside the identity envelope
(33.859437 ms feature versus 35.658738 ms identity), while frame-total p95
positive max still exceeded it (67.1932 ms feature versus 43.6239 ms identity).
Payload handoff cost averaged 0.01451 ms. This keeps the lossless payload result
strictly in the correctness/tooling bucket: byte output is stable and the
handoff itself is tiny, but the current short lossless timing envelope still
blocks throughput promotion for that path.
The broader lossless 16-frame x 3-repeat matrices at
`.claude-state/profiling/2026-06-19-cdng-e3-lossless-identity-matrix-16x3-e41e7de1/matrix-summary.json`
and
`.claude-state/profiling/2026-06-19-cdng-e3-lossless-payload-matrix-16x3-e41e7de1/matrix-summary.json`
both passed 9/9; their hash sweeps each reported 144/144 matched DNG pairs with
0 mismatches. The v3 calibration with stage attribution at
`.claude-state/profiling/2026-06-19-cdng-e3-lossless-calibration-16x3-e41e7de1/calibration-with-stage-attribution.json`
still reported `verdict=EXCEEDS_IDENTITY_ENVELOPE`: frame-total p95 positive
max was inside identity (81.4423 ms feature versus 95.7494 ms identity), but
frame-total average positive max exceeded it (43.329938 ms feature versus
23.927175 ms identity). Stage attribution puts the feature average miss mostly
in `llrawproc_ms` (+8.180042 ms average), while `dng_compress_ms` and
`disk_write_ms` stayed inside identity and payload handoff averaged 0.016103 ms.
This strengthens the hold without pointing at payload-copy cost: the lossless
payload boundary is byte-correct, but current timing evidence still needs either
a stronger same-stage/noise model or a real scheduler win before promotion.
The matching current-release async-writer lossless matrix at
`.claude-state/profiling/2026-06-19-cdng-e3-lossless-async-matrix-16x3-current/matrix-summary.json`
used serial payload handoff plus `-CandidateUseAsyncWriter`,
`-CandidateAsyncWriterQueueDepth 2`, `--cdng-codec lossless`, the same three
clips/receipt, 16 frames, and 3 repeats. Its hash sweep at
`.claude-state/profiling/2026-06-19-cdng-e3-lossless-async-matrix-16x3-current/dng-hash-comparison.json`
reported 144/144 baseline-vs-candidate DNG pairs matched by length and SHA256,
with 0 mismatches and 0 missing files, and all cases reached
`async_writer_max_queued=2`. The calibrated identity-vs-async packet at
`.claude-state/profiling/2026-06-19-cdng-e3-lossless-async-calibration-16x3-current/calibration.json`
reported `verdict=EXCEEDS_IDENTITY_ENVELOPE` with
`blockingReasons=feature_exceeds_identity_frameTotalAvgDeltaMs,feature_exceeds_identity_frameTotalP95DeltaMs`.
Wrapper elapsed improved strongly (`elapsedDeltaMs` feature average
-7214.264222 ms, inside the identity envelope), but frame-total average and p95
deltas failed the envelope (`frameTotalAvgDeltaMs` feature average
+1043.311510 ms, feature positive max +3852.859176 ms; `frameTotalP95DeltaMs`
feature average +1612.453544 ms, feature positive max +6837.739600 ms). Stage
attribution names `writerCompletionLagAvgDeltaMs` as both the dominant positive
feature-average stage (+1565.818632 ms) and dominant positive-max excess stage
(+4281.253201 ms), with `writerQueueWaitAvgDeltaMs` also high (+876.454149 ms
feature average). This is a useful async result but still a HOLD, not a
promotion: the bytes are stable and the writer can overlap enough to improve
wrapper elapsed, but the current completion gate says backlog/lag is part of
the frame cost. Next E3 async work should either reduce queue wait/completion
lag with scheduler policy or define a separately justified async-aware
promotion gate; it should not claim success from elapsed-only improvement.

Evidence (detail): `.claude-state/profiling/20260614-tier2-cuda/` (SUMMARY, tier2-findings,
recon-algorithm-map, recon-exact-constants, parity / parity-breadth / amaze-parity /
glinterop / optimization / full-pipeline results, integration-blueprint) and
`.claude-state/profiling/20260613-gpu-lane-x1/findings.md` (Tier 1 + x1 CPU breakdown).
Code: `tools/gpu/` (probes, parity, oracle, `igpu_recon.h`, `igpu_recon_cuda.dll`).

Proven so far: recon 0-LSB (mean23 + AMaZE dual-ISO logic), bilinear debayer 0-LSB,
CUDA AMaZE debayer parity through the DLL-gated/non-default production seam,
zero-readback CUDA->GL present (~0.1 ms), deployable ABI-validated `igpu_recon_cuda.dll`,
full pipeline ~1 ms/frame @ 4.1 MP (~988 fps) / ~9 ms @ 8K, parity across 8K/clipped/ISO.

---

## 1. Goals and non-goals

**Goals**
- Full-quality **x1 realtime** playback (the original moonshot) — no forced scale/quality compromises on capable hardware.
- **Faster CDNG export** (the repo's primary mission) with byte-exact output.
- One **backend contract** (`igpu_recon` C-ABI) so CUDA never leaks into the app; portable backends slot behind it.
- **Trustworthy UX**: quality labels mean exactly what they say; the app always shows its true active path.
- A permanent **CPU floor** — the app is fully usable with no GPU.

**Non-goals**
- Replacing the CPU path (it is the floor, not deprecated).
- Requiring a GPU.
- OpenGL-compute as the strategic portable target (it's frozen/deprecated on macOS).
- Rendered-video GPU export before processing parity lands.
- Bit-exactness *beyond* the engine's own float tolerance (its SSE2-vs-scalar variance is the reference, not a stricter bar).

## 2. Backend ladder and fallback rules

Ladder behind the single `igpu_recon` ABI:
```
CUDA      NVIDIA fast path (proven reference)
Vulkan    Win/Linux all-vendors + macOS via MoltenVK   (later)
Metal     macOS / Apple Silicon native                  (later)
CPU       universal fallback (permanent floor)
```
**Capability query** gates GPU use: *can this device do full recon + texture present with no per-frame readback?* — not merely "is a GPU present" (a weak iGPU can pass the latter and still not be worth it; the Tier 1 GL seam had a GPU and lost on readback).

**Fallback rules (all non-fatal):** missing DLL → CPU · no NVIDIA → CPU · GPU init failure → CPU · device/parity mismatch → CPU · **user selects Software → never touch the GPU** · **export always has a CPU correctness path**. No alarming modal unless the user explicitly requested GPU-only.

## 3. Lane A — GPU CDNG export (first; lowest risk, on-mission)

CDNG stores **post-recon Bayer** (debayer/processing happen later in the user's NLE), so only the **recon** stage needs the GPU — exactly the proven, ABI-wrapped, 0-LSB stage. Offline + readback-tolerant + file-diff-verifiable ⇒ the safest first production integration.
- **E0** export-stage profiler: decode / Dual-ISO recon / DNG pack / DNG compress / disk write / queue idle. Also includes an intentional CPU-export focal-plane resolution stability guard for same-process multi-frame DNG exports: frame 1 remains legacy byte-identical, while affected frames 2+ stop compounding the EXIF focal-plane denominator.
- **E1** GPU CDNG recon: CPU decode/unpack → CUDA recon (`IGPU_OUT_CPU16`) → read back Bayer16 → existing DNG writer unchanged. Behind `MLVAPP_GPU_EXPORT` / setting.
- **E2** export parity gate: CPU vs GPU exported DNGs match image payload + metadata (Look Assist defaults, resume, Dual-ISO pattern mapping, compressed + uncompressed). CPU fallback always.
- **E2 batch telemetry:** when a batch export actually attempts the CUDA shadow
  path and the backend exposes its optional VRAM query, stdout emits
  `[BATCH] GPU ... vramAllocatedMB=...` once per clip/resolution. The value is a
  backend working-set budget (tracked CUDA buffers plus the measured context
  reserve), not a WDDM per-PID reading; CPU-only and old-DLL runs stay silent.
- **E3** pipelined export: CPU decode workers → one GPU recon queue → CPU compress/write workers (never N processes fighting one GPU). A comparator for E0 export-stage profile JSONs now exists at `tools/profiling/compare-export-stage-profiles.ps1`, the profiler emits supported `queue_idle_ms`, `producer_queue_idle_ms`, `producer_frame_ms`, and `writer_completion_lag_ms` samples, `tools/profiling/run-release-cdng-export-profile.ps1` produces release-tree batch export profiles, `tools/profiling/run-release-cdng-export-profile-ab.ps1` bundles paired baseline/candidate profiles with a compare summary, `tools/profiling/run-release-cdng-export-profile-matrix.ps1` repeats those paired profiles across named cases into one matrix summary, and `dngFramePayload_t` now backs both the opt-in `MLVAPP_CDNG_EXPORT_PAYLOAD_HANDOFF=1` serial boundary and the opt-in `MLVAPP_CDNG_EXPORT_ASYNC_WRITER=1` single writer-worker boundary. Async writer queue depth defaults to 1 and can be raised only by opt-in env/script parameter for bounded release-tree experiments, while the profiling-only async-writer debug delay can prove queue-capacity mechanics under synthetic writer backpressure. Candidate pipeline experiments can report per-stage avg/p50/p95 deltas, scheduler idle/gap avg/p95 deltas, caller-side producer occupancy, post-producer writer lag, payload handoff cost, wrapper wall-clock elapsed-time deltas, writer queue capacity/max-queued/debug-delay, and avg/p95 frame-total regression gates across a real-footage matrix before any scheduler rewrite is promoted.
- **E4** rendered-video export: later, only after processing parity; hardware encoders (NVENC/AMF/QSV) a separate lane.

## 4. Lane B — CUDA playback

- **P-pre (quality completion):** GPU **AMaZE debayer** parity (landed behind the
  DLL gate) + GPU **processing** parity + clean x1 CPU-vs-GPU frame diff. Required
  before the GUI may claim "GPU Full Quality AMaZE" (see §8).
- **P1** loader/fallback: load `igpu_recon_cuda.dll` if present + capable, else CPU. No hard dependency. Experimental playback bridge present behind `MLVAPP_GPU_PLAYBACK_RECON=1`.
- **P2** GPU recon + CPU readback: CUDA recon → Bayer16 readback → existing CPU debayer/process/present. Integration bridge, not final UX. Implemented for the v1 proven config only; missing/unsupported backend falls back to CPU.
- **P3** no-readback playback: CPU decode/prefetch -> CUDA recon -> CUDA-to-GL R16 texture present (no per-frame CPU readback for the displayed Bayer frame) is implemented and RTX 4090-validated for the scoped raw-fixes-enabled HQ Dual ISO shape. Readback-backed Bayer16 GL presentation remains the fallback presenter for P2 output; unsupported or non-proof states stay CPU/readback.
- **P4** adaptive quality + polish: hardware-capability-driven auto quality/scale, visible A/B + frame diffs, status UI, telemetry. Status/telemetry now distinguishes CPU, GPU preview, GPU recon readback, readback-backed texture present, and true no-readback texture present; the remaining adaptive work is to promote capability-aware defaults and quality decisions without exceeding the scoped P3 gate.
- Decode (LJ92, CPU, overlapped via prefetch ~7-9 ms @ 4.1 MP) is the steady-state gate once recon is on GPU — tune the overlap.

## 5. Lane C — portable GPU (later)

CUDA stays the reference. Add backends behind the same ABI: **Vulkan** (strategic Win/Linux all-vendors + Mac via MoltenVK), **Metal** (strategic macOS). **OpenGL** = presentation (the viewport is GL; CUDA→GL present proven) + optional *tactical* Win/Linux compute bridge — not the strategic compute target. Sequenced after Lanes A/B so effort isn't fragmented; the CPU oracle validates every new backend identically (0-LSB).

## 6. Quality modes and Expert controls

**Default UI — three modes (no jargon):**
```
Playback Mode:  Auto  |  Prioritize Quality  |  Prioritize Smoothness
```
- **Auto** (default): CUDA full-quality when validated + available; CPU full-quality when paused/scrubbing/exporting if needed; reduced-scale preview only when necessary to hold cadence; shows a small status (`GPU` / `CPU` / `Preview`).
- **Prioritize Quality:** true x1, selected/Advanced debayer, **no substitution**; if CUDA can't satisfy it, fall back to **software and say so** — never silently drop to bilinear or x4.
- **Prioritize Smoothness:** reduced-scale / faster debayer allowed to keep editing responsive; clearly preview-only — paused inspection and export stay full quality unless explicitly opted out.

**Expert Playback Settings (advanced, opt-in):**
```
Playback Engine:    Auto / GPU / Software
Preview Resolution: Auto / Full 1x / 1/2 / 1/4 / 1/8
Debayer Quality:    Auto / AMaZE / RCD / Bilinear / Basic   (labeled by intent — see §7)
Dual ISO Preview:   Auto / Full HQ / Fast Preview
Fallback Behavior:  Allow automatic fallback · Warn when quality is reduced
```
Advanced users can force `Full 1x · AMaZE` and accept dropped frames (the status shows the honest fps). The main UI never forces anyone to understand CUDA, recon, readback, or x4/x8.

## 7. Debayer / scale policy

Rank by **quality tier**, not a fake exact speed ladder (the advanced demosaics are quality *tradeoffs*). Label by intent:
```
Advanced  — AMaZE (Maximum detail) · RCD/LMMSE/AHD (High quality)
Fast      — Bilinear (Fast preview)
Minimal   — Basic / None (Fastest, last-resort cadence rescue)
```
- **Auto** moves between *tiers* by sustained cadence; it does **not** micromanage AMaZE-vs-RCD (no evidence to; revisit only if that changes).
- **Prioritize Quality / Expert-forced algorithm:** use it, or fall back to **CPU** for that algorithm — never silent-substitute bilinear under a "Full Quality" label.
- **Dual-ISO interpolation** folds into the same intent: Full Quality → AMaZE/HQ dual-ISO; Performance → mean23 / reduced. Don't expose "mean23" to normal users.
- **Scale** is **graceful auto-degradation** (invisible rungs Auto uses to hold cadence on weak HW), not a chore — though Expert can pin it. On capable GPUs the cost gap nearly vanishes (even AMaZE debayer ~1-2 ms), so the tradeoff usually disappears and full quality just plays.

## 8. Validation gates

- **Recon:** 0 LSB vs CPU oracle (mean23 + AMaZE dual-ISO logic) — done. AMaZE dual-ISO's shared float demosaic core is ±1-2 LSB = the engine's own SSE2-vs-scalar variance (policy: keep that subpath on CPU for "legacy-exact," or require explicit tolerance opt-in).
- **Debayer:** bilinear 0 LSB — done; **AMaZE debayer parity** landed as a
  DLL-gated/non-default production seam and remains non-GUI until the rest of
  P-pre is reviewed.
- **Processing:** **parity = P-pre**. The current gate covers the supported
  preview-processing subset (levels / matrix / camera matrix / gamut compression
  / gamma LUT path) through CPU-vs-GPU frame diffs; broader unsupported features
  still fail closed instead of silently using GPU.
- **Export (Lane A E2):** per-frame DNG image-payload + metadata byte-diff, compressed
  + uncompressed — **implemented + RTX-4090-validated** (CUDA `igpu_recon`). The GPU
  export shadow path engages *only* for the base HQ dual-ISO config (MEAN23 + alias-map
  ON + full-res ON + chroma OFF): there it replaces the CPU output byte-identically
  (`replaced==1`, SHA256 match) across {tiny,large} × {Look-Assist off/on} ×
  {uncompressed,compressed}. Every other config (alias/full-res OFF, AMAZE, chroma-smooth
  ON) is GPU-ineligible and falls back to CPU cleanly (`run_attempted==0`, `replaced==0`,
  CPU authoritative, DNG still byte-equal). Resume/partial export is byte-identical to a
  full run (per-frame export carries no cross-frame state). Tests: `DualIsoPipeline.
  GpuExport*` in `tests/pipeline/test_dual_iso_pipeline.cpp` — eligible matrix, ineligible
  fallback, resume proxy, and missing-DLL byte-inert; the GPU-engaging cases are gated on
  `MLVAPP_GPU_EXPORT_TEST_DLL` (skip on llvmpipe, run on the 4090).
- **Playback truth:** validate by *pixels* (PrintWindow / frame diff), never FPS alone — cadence can read perfect over a frozen frame.

**Parity-coupling (the honesty linchpin):** a quality option appears in the GUI **only after its parity gate passes** — so the UX rollout is staged with the engineering:

| Stage | Engineering done | GUI may honestly offer |
|---|---|---|
| 1 | P2 / E1 (recon + bilinear, 0-LSB) | `GPU` engine; Full-Quality-AMaZE routes to **CPU**; GPU bilinear only under a labeled *Performance* mode |
| 2 | P-pre passes (AMaZE debayer + processing parity) | `GPU · Full Quality · AMaZE` becomes a true explicit path, with CPU AMaZE fallback reported instead of silent bilinear substitution |

P-pre is therefore both the engineering gate and the GUI-claim gate — it's what prevents a "Full Quality" toggle that silently isn't.

### 8.1 Processing-parity slices (extending P-pre to `allow_creative_adjustments`)

The GPU preview-processing shader today reproduces only the levels / matrix /
camera-matrix WB / gamut / gamma subset and fails closed on
`allow_creative_adjustments` (the creative grade). Extending it to full parity —
so a normally-graded clip can use the GPU path instead of falling back to CPU —
is staged as curve-first slices, because at the default image profile the
creative **contrast curve** (`pre_calc_curve_r`, built from the non-zero base
contrast params) is the only creative-family stage that is both active and
non-identity; gradation and toning execute but are identity, and
shadows/highlights and clarity are inert by default.

The post-gamma creative pipeline is ported as bit-aligned slices, in the exact
order `raw_processing.c` applies them (gamma → hue-vs → vibrance → saturation →
toning → contrast curve → gradation):

- **Slice 1 (DONE, `d77a26c6`):** creative contrast-curve LUT (`pre_calc_curve_r`)
  + gradation curves (`gcurve_*`) — 1D 16-bit LUT lookups, no spatial pass.
- **Slice 2 (DONE, `7c59d699`):** toning (per-channel `toning_dry + toning_wet`).
- **Slice 3 (DONE, `2e04516b`):** saturation (`Y1 + trunc((pix-Y1)*sat)`).
- **Slice 4 (DONE, `6ee4b4f3`):** vibrance (saturation-weighted blend).
- **Slice 5 (DONE):** hue-vs / luma-vs curves — RGB→HSV, four signed-`float[36000]`
  curve adjustments indexed by hue (`H*100`) and luma (`V*36000`), HSV→RGB. The
  curves are carried as **R32F** textures (units 11-14) so the GPU, the CPU
  reference and the production `float` curves stay bit-aligned (the uint16 LUT
  path would quantize the [-1,1] curve to ~3e-5 and break parity).
  - **Parity caveat (OOB clamp):** `hue_vs_luma` can push `V` to ≥ 1.0, after
    which `(uint16)(V*36000)` indexes `luma_vs_saturation[]` (exactly 36000
    entries) out of bounds — `V == 1.0` alone already yields index 36000. That
    read is undefined on the production CPU path, so both the GPU shader and the
    CPU reference **clamp the luma index to 35999** (the last valid sample)
    instead of reproducing undefined behaviour. This diverges from production
    only for boosted highlights (`V ≥ 1.0`) when a non-neutral
    `luma_vs_saturation` curve is set; clamping is the correct, deterministic
    behaviour and the production CPU path should adopt the same clamp (tracked
    separately — out of GPU-lane scope).
- **Slice 6 (DONE):** in-loop **simple-contrast factor** (`processing->contrast`,
  a per-pixel luma-dependent exposure multiply via `contrast_curve[cval]`,
  `raw_processing.c:2941-2954`). `cval` is the integer luma `(4R+11G+B)>>4` of the
  matrix-applied (pre camera-WB) pixel; the value is multiplied by
  `contrast_curve[cval]`. Because the factor is luma-dependent it cannot be folded
  into the per-channel matrix/gamma LUTs, so it is applied in-shader after the
  matrix sample and before the camera matrix/gamma (the scalar commutes with the
  linear WB). `contrast_curve` (`double[65536]`) is narrowed to `float` and carried
  as an R32F texture (unit 15).
- **Later slices (non-creative features, gated independently of the creative flag):**
  shadows/highlights + clarity (spatial RBF blur-mask pre-pass), then 1D/3D LUT,
  creative filter, AgX, median/RBF denoise, grain, CA correction, sharpen,
  vignette, non-Rec709 gamut.

**After slices 1-6 the creative-adjustments family is fully ported.**
`gpuPreviewProcessingIsSupported` no longer rejects `allow_creative_adjustments`
on its own — it accepts any grade (default or hand-graded: in-loop contrast +
hue-vs/luma-vs + vibrance + saturation + toning + contrast curve + gradation) and
fails closed only on the non-creative features listed above, which are gated
independently of the creative flag. The P-pre creative-parity goal (a normally
graded clip uses the GPU path instead of the CPU fallback) is met for the
creative-grade family, pending the RTX 4090 GL frame-diff that validates the
shader against this CPU reference.

Each slice keeps the CPU reference (`applyPreviewProcessingPixel`) and the GPU
subset shader bit-aligned, adds unit parity tests (the CPU reference is the local
bit-exact oracle), and is validated by a CPU-vs-GPU frame diff on the RTX 4090
before the support gate relaxes for that stage. P-pre — and the honest GUI
"GPU · Full Quality · AMaZE" claim per §8 stage 2 — completes when every creative
stage reaches parity.

### 8.2 Spatial-stages phase (completing P-pre beyond the creative family)

Update 2026-06-17: the creative family + the tractable non-creative per-pixel
stages (gamut / AgX / vignette / 1D-3D LUT) are ported and 4090-validated. The
remaining gate rejects were recon'd stage-by-stage and scoped in
[`gpu-lane-spatial-stages-scoping.md`](gpu-lane-spatial-stages-scoping.md). Key
correction to the earlier "the rest all need a blur pre-pass" assumption: most
remaining rejects are actually **per-pixel** (highlight reconstruction, gradient,
grain, creative filter) and only **chroma blur / sharpen / median** genuinely
need a neighborhood pass; **shadows/highlights, clarity, RBF denoise and CA** are
inherently **sequential** (a recursive bilateral filter / edge-window scan) and
are recommended to stay **CPU-fallback by design** (roadmap §9 honesty), not
bit-exact GPU ports. Two hard constraints drive the ranking: the shader is
**GLSL 110** (no bitwise ops/`uint`/`%` — all integer math float-emulated, which
blocks bit-exact grain and the NN sigmoid) and the **strict parity gate**
(≤16 LSB). The box blur (`blur_image`) is a separable integer box, bit-exactly
reproducible and validated in isolation before any consumer is wired. Order:
A) per-pixel bit-exact (highlight-recon → gradient); B) box-blur FBO infra +
chroma/sharpen/median; C) explicit CPU-fallback decision for the recursive
stages. See the scoping doc for the per-stage primitive table, apply-order map,
and the box-blur bit-exactness analysis.

## 9. UI truth / status language

Always surface the active path, quietly (no fake green lights, no alarms unless the user asked for GPU-only):
```
Playback: Full Quality · GPU · 1x · AMaZE
Playback: Full Quality · Software · 1x · AMaZE
Playback: Performance Preview · GPU · 1/4 · Bilinear
GPU unavailable: using software
```
Principles: **(1)** quality labels mean what they say; **(2)** preview compromises are allowed but *named* (reduced-scale / bilinear / mean23 = preview/performance); **(3)** Auto is smart but not mysterious — the status reveals the chosen path; **(4)** **export is sacred** — default deterministic + legacy-equivalent, any tolerance-based GPU path opt-in until proven; **(5)** fallback is a feature, not a failure (accelerator, not requirement); **(6)** Advanced controls exist without cluttering the main workflow.

North star: *"I can trust what I'm seeing and exporting,"* while the app quietly uses every bit of GPU speed it can safely use.

---

## Execution order (recommended)
1. **Lane A E0–E2** (GPU CDNG export, byte-exact) — lowest-risk first production use; serves the batch-export mission.
2. **P-pre** quality completion (AMaZE debayer + processing parity) — unlocks honest "GPU Full Quality" routing.
3. **Lane B P1–P4** (CUDA playback) — first explicit AMaZE playback routing, then the no-readback/user-facing realtime wins.
4. **Lane A E3–E4** (export pipeline + rendered/NVENC) and **Lane C** (Vulkan/Metal) as parallel/later tracks.

Supervised items (touch shipping `src/` or protected branch): all `src/` wiring in §3-4, and merging the work-block branch. The backend, parity harness, and this plan are ready.
