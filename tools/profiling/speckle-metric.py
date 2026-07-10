#!/usr/bin/env python3
# speckle-metric.py -- OBJECTIVE high-spatial-frequency CHROMA-SPECKLE metric for the
# reviewer (Claude) lane grading x2 dual-ISO playback (b)-defect candidates.
#
# WHY THIS EXISTS
#   The held cliff-fix candidate is GLOBALLY NEUTRAL (greenMagenta gm ~ 0) yet still
#   carries a RESIDUAL LOCALIZED CHROMA SPECKLE (magenta/green per-pixel noise).
#   Mean-color metrics (gm/blueAmber, as in frame-color-map.py) report it as "fixed"
#   while the frame is heavily speckled -- the recurring overfit trap. This tool
#   measures the PHYSICALLY DEFINING property of the (b) defect: isotropic, per-pixel,
#   sign-alternating CHROMA noise on flat-luma regions. It must NOT fire on legitimate
#   colored CONTENT (a red shirt, a yellow jersey) or on LUMA edges/detail.
#
# METHOD -- CLSE (Chroma-Laplacian Speckle Energy) with a luma-Laplacian guard, plus
# a smooth-luma-gated companion (best ideas from the CLSE/LCV/CSI proposals combined):
#
#   Channels (luma-free by construction, reused from frame-color-map.py):
#     C1 = G - (R+B)/2   (green<->magenta)
#     C2 = B - R         (blue<->amber)
#     Y  = 0.299R+0.587G+0.114B
#
#   The presented PNGs have stretch_x=3 baked in, so display speckle is 1px TALL but
#   ~3px WIDE. The VERTICAL Laplacian is therefore the stretch-invariant primary probe
#   (1px sign-flip detector; ~0 on smooth gradients AND linear edges):
#     Lv(C)[y,x] = C[y-1,x] - 2*C[y,x] + C[y+1,x]      (edge-replicated borders)
#     che_v      = sqrt( Lv(C1)^2 + Lv(C2)^2 )         (isotropic chroma 2nd-deriv mag)
#
#   PRIMARY scalars:
#     CLSE_v   = sqrt(mean(che_v^2))                    RMS chroma-Laplacian energy
#     CLSE_v95 = 95th percentile of che_v               sparse-speckle tail sensitivity
#
#   DECISIVE GUARD (separates speckle from legitimate colored content / luma detail):
#     Llum = |Lv(Y)|
#     C/L  = sqrt(mean(che_v^2)) / sqrt(mean(Llum^2))   chroma-/luma-Laplacian ratio
#       Legit content carries CORRELATED luma structure (a jersey edge is both a color
#       edge and a luma edge) -> C/L stays LOW (~<1). Speckle injects chroma high-freq
#       energy with NO luma counterpart -> C/L SPIKES (>>1).
#
#   COMPANION (CSI idea -- absolute, smooth-luma-gated, native-collapsed to kill the
#   stretch anisotropy so a symmetric high-pass responds isotropically):
#     CSI      = RMS of native-collapsed chromaHF over smooth-luma pixels (lumaHF<6)
#     CSI_dens = % of smooth-luma pixels whose chromaHF > 10 (isotropic speckle coverage)
#
#   ISOTROPY CHECK (LCV idea): horizontal Laplacian at the stretch scale (+/-3 px).
#   True speckle -> che_h3 ~ che_v (isotropic). A smooth cast -> both low.
#
# IMPORTANT -- THIS IS A RELATIVE, SAME-FRAME GRADER, *NOT* an absolute classifier
# (adversarially hardened, Claude SEQ158). The headline SPECKLE/CLSE_v95 number is RAW
# (NOT C/L-gated): on a SINGLE frame, legitimate colored CONTENT/texture can read 50-350
# (a yellow-jersey mesh ~349) -- far above the real speckle band (14-42) -- and a pure
# brighten/darken shifts it +-25% with ZERO speckle change. So NEVER threshold an absolute
# score to call one frame "clean" vs "speckled". Use it ONLY to grade a candidate vs the
# HELD BASELINE on the SAME clip/frame/timepoint with identical params.
#
# GRADING A (b) CANDIDATE -- credit a speckle REDUCTION only when ALL hold:
#   (1) CLSE_v drops >=20-25%  AND  CLSE_v95/SPECKLE also drops;
#   (2) C/L drops by >=~0.8 toward the frame's CLEAN-CONTENT FLOOR and ends BELOW ~3.0
#       (genuine fixes land C/L ~1.9-3.1; every luma-only decoy keeps C/L >=4.5) --
#       "toward the content floor", NOT "toward 1.0";
#   (3) CSI_dens (CSId%) also drops;
#   (4) ANTI-BLUR GUARD (load-bearing): lumaLap (clse_lum) stays within ~+-10% -- a CLSE
#       drop WITH a lumaLap drop means the candidate just SOFTENED detail/debayer, NOT a
#       speckle fix -> rule INCONCLUSIVE, do not credit;
#   (5) CO-GATE: gm/blueAmber (frame-color-map.py) must not drift -> don't trade speckle
#       for a new low-frequency cast.
#   A CLSE drop with C/L FLAT (e.g. the darken decoy: -25% CLSE, C/L unchanged) is NOT a
#   fix. Before trusting the 20% bar, capture the baseline 2-3x to confirm pipeline jitter
#   is well under 20% on CLSE_v and <~0.3 on C/L.
#
# Pillow + numpy only (no scipy -- matches project constraint). Does NOT modify any
# source/recon file or frame-color-map.py.
#
# Usage:
#   py -3 tools/profiling/speckle-metric.py <frame.png> [<frame2.png> ...]
#         [--crop L T R B]    strip a UI border before measuring (image region only)
#         [--json]            emit a machine-readable JSON array as well as the table
#         [--selftest]        run synthetic ground-truth validation and exit

import sys, argparse, json
try:
    from PIL import Image
    import numpy as np
except Exception as e:
    print("NEED Pillow+numpy: pip install pillow numpy  (error: %s)" % e); sys.exit(2)

EPS = 1e-6

def load_rgb(path, crop):
    img = Image.open(path).convert('RGB')
    if crop:
        l, t, r, b = crop
        img = img.crop((l, t, img.width - r, img.height - b))
    return np.asarray(img).astype(np.float64)

def channels(a):
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    C1 = G - (R + B) / 2.0          # green<->magenta
    C2 = B - R                      # blue<->amber
    Y  = 0.299 * R + 0.587 * G + 0.114 * B
    return R, G, B, C1, C2, Y

def lap_v(C):
    """Vertical 1px Laplacian, edge-replicated borders (stretch_x=3 invariant)."""
    up   = np.pad(C, ((1, 0), (0, 0)), mode='edge')[:-1, :]
    down = np.pad(C, ((0, 1), (0, 0)), mode='edge')[1:, :]
    return up - 2.0 * C + down

def lap_h3(C):
    """Horizontal Laplacian at the stretch scale (+/-3 px), edge-replicated."""
    left  = np.pad(C, ((0, 0), (3, 0)), mode='edge')[:, :-3]
    right = np.pad(C, ((0, 0), (0, 3)), mode='edge')[:, 3:]
    return left - 2.0 * C + right

def to_native(ch):
    """Native-collapse stretch_x=3: average non-overlapping 3-column groups."""
    H, W = ch.shape
    Wt = (W // 3) * 3
    return ch[:, :Wt].reshape(H, Wt // 3, 3).mean(axis=2)

def lap4(c):
    """4-neighbour 2D Laplacian on the native-collapsed grid (border dropped)."""
    return (-4.0 * c + np.roll(c, 1, 0) + np.roll(c, -1, 0)
            + np.roll(c, 1, 1) + np.roll(c, -1, 1))[1:-1, 1:-1]

def measure(a):
    R, G, B, C1, C2, Y = channels(a)

    # --- frame-color-map context (means) ---
    mR, mG, mB = R.mean(), G.mean(), B.mean()
    gm   = mG - (mR + mB) / 2.0
    ba   = mB - mR
    luma = 0.299 * mR + 0.587 * mG + 0.114 * mB

    # --- PRIMARY: vertical chroma-Laplacian speckle energy ---
    lvC1, lvC2 = lap_v(C1), lap_v(C2)
    che_v   = np.sqrt(lvC1 * lvC1 + lvC2 * lvC2)
    clse_v  = float(np.sqrt(np.mean(che_v * che_v)))
    clse_v95 = float(np.percentile(che_v, 95))

    # --- DECISIVE GUARD: chroma- vs luma-Laplacian ratio ---
    llum = np.abs(lap_v(Y))
    clse_lum = float(np.sqrt(np.mean(llum * llum)))
    cl_ratio = clse_v / (clse_lum + EPS)

    # --- ISOTROPY CHECK: horizontal-at-stretch-scale chroma Laplacian ---
    lh1, lh2 = lap_h3(C1), lap_h3(C2)
    che_h3  = np.sqrt(lh1 * lh1 + lh2 * lh2)
    clse_h3 = float(np.sqrt(np.mean(che_h3 * che_h3)))
    iso = clse_h3 / (clse_v + EPS)         # ~1 isotropic speckle; <<1 vertical-only

    # --- COMPANION: absolute smooth-luma-gated chroma-HF (native-collapsed) ---
    Yn  = to_native(Y); C1n = to_native(C1); C2n = to_native(C2)
    hY  = np.abs(lap4(Yn))
    hC  = np.sqrt(lap4(C1n) ** 2 + lap4(C2n) ** 2)
    smooth = hY < 6.0
    if smooth.any():
        csi      = float(np.sqrt(np.mean(hC[smooth] ** 2)))
        csi_dens = float(((hC > 10.0) & smooth).mean() * 100.0)
    else:
        csi, csi_dens = 0.0, 0.0

    return dict(
        # headline speckle score = RAW CLSE_v95 (NOT C/L-gated) -- relative-grade only
        speckle_score = clse_v95,
        clse_v   = clse_v,   clse_v95 = clse_v95,
        cl_ratio = cl_ratio, clse_lum = clse_lum,
        clse_h3  = clse_h3,  iso = iso,
        csi      = csi,      csi_dens = csi_dens,
        gm = gm, blueAmber = ba, luma = luma,
    )

def fmt_row(label, m):
    return (f"{label:<26} "
            f"SPECKLE={m['speckle_score']:6.2f}  "
            f"CLSE_v={m['clse_v']:6.2f}  CLSE_v95={m['clse_v95']:6.2f}  "
            f"C/L={m['cl_ratio']:5.2f}  lumaLap={m['clse_lum']:6.2f}  iso={m['iso']:4.2f}  "
            f"CSI={m['csi']:6.2f}  CSId={m['csi_dens']:5.2f}%  "
            f"gm={m['gm']:+6.1f}  blueAmber={m['blueAmber']:+6.1f}  luma={m['luma']:5.1f}")

def selftest():
    rng = np.random.default_rng(0)
    H, W = 240, 1853  # mimic stretch_x=3 width parity

    def score(img):
        return measure(img.astype(np.float64))['speckle_score'], measure(img.astype(np.float64))

    print("=== SELF-TEST (synthetic ground truth) ===")
    # 1) Flat saturated RED content -> must be ~0
    red = np.zeros((H, W, 3)); red[..., 0] = 180.0
    s, m = score(red)
    print(fmt_row("flat-red-content", m), "   EXPECT ~0")

    # 2) Smooth red+yellow + luma gradient (content, no speckle) -> must be ~0
    grad = np.linspace(20, 200, W)[None, :]
    cont = np.zeros((H, W, 3))
    cont[:, :W // 2, 0] = 170; cont[:, :W // 2, 1] = 30; cont[:, :W // 2, 2] = 30      # red
    cont[:, W // 2:, 0] = 190; cont[:, W // 2:, 1] = 180; cont[:, W // 2:, 2] = 40     # yellow
    cont += grad[..., None] * 0.3
    s, m = score(np.clip(cont, 0, 255))
    print(fmt_row("smooth-content+lumagrad", m), "   EXPECT ~0")

    # 3) Pure luma B/W checker (luma detail, no chroma) -> must be ~0
    chk = np.indices((H, W)).sum(axis=0) % 2
    bw = np.repeat((chk * 200)[..., None], 3, axis=2).astype(np.float64)
    s, m = score(bw)
    print(fmt_row("luma-checker (no chroma)", m), "   EXPECT ~0")

    # 4) Flat gray + injected per-pixel magenta/green chroma speckle -> must be HIGH
    spk = np.full((H, W, 3), 120.0)
    noise = rng.integers(0, 2, size=(H, W)) * 2 - 1   # +/-1
    spk[..., 0] += noise * 40   # R up/down
    spk[..., 1] -= noise * 40   # G opposite -> magenta<->green per pixel
    spk[..., 2] += noise * 40   # B up/down
    s, m = score(np.clip(spk, 0, 255))
    print(fmt_row("gray+chroma-speckle", m), "   EXPECT HIGH")
    print("Pass criteria: first three near 0; last one large. The C/L ratio should be")
    print("near 0 for content/luma cases and large for the speckle case.")

def main():
    ap = argparse.ArgumentParser(description="Chroma-speckle metric (CLSE + guards) for the reviewer lane.")
    ap.add_argument("frames", nargs="*")
    ap.add_argument("--crop", type=int, nargs=4, default=None, metavar=("L", "T", "R", "B"),
                    help="crop a UI border before measuring")
    ap.add_argument("--json", action="store_true", help="also emit a JSON array")
    ap.add_argument("--selftest", action="store_true", help="run synthetic validation and exit")
    args = ap.parse_args()

    if args.selftest:
        selftest(); return
    if not args.frames:
        ap.error("provide one or more PNG paths (or --selftest)")

    print("SPECKLE = RAW CLSE_v95 (NOT C/L-gated). Absolute value is CONTENT-DEPENDENT (content can read 50-350)")
    print("  -> do NOT classify a single frame by it. RELATIVE, SAME-FRAME grading ONLY.")
    print("Credit a (b) reduction vs the HELD BASELINE (same frame) only when ALL hold: CLSE_v -25% & CLSE_v95 down,")
    print("  C/L drops >=0.8 to BELOW ~3.0 (content floor, not 1.0), CSId down, lumaLap within +-10% (anti-blur),")
    print("  and gm/blueAmber (frame-color-map) do not drift. C/L FLAT with CLSE down = luma change, NOT a fix.")
    print("-" * 150)
    out = []
    for fr in args.frames:
        a = load_rgb(fr, args.crop)
        m = measure(a)
        H, W, _ = a.shape
        label = fr.replace("\\", "/").split("/")[-1]
        print(fmt_row(f"{label} ({W}x{H})", m))
        rec = {"file": fr}; rec.update(m); out.append(rec)
    if args.json:
        print("-" * 150)
        print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
