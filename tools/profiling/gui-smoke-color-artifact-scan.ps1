function Add-ColorArtifactScannerType {
    Add-Type -AssemblyName System.Drawing
    if ("MlvGuiSmokeColorArtifactScanner" -as [type]) {
        return
    }

    Add-Type -ReferencedAssemblies @(
        "System.Drawing",
        "System.Drawing.Common",
        "System.Drawing.Primitives",
        "System.Private.Windows.GdiPlus",
        "System.Private.Windows.Core"
    ) -TypeDefinition @"
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;

public sealed class MlvGuiSmokeColorArtifactScanResult
{
    public string Path;
    public int Width;
    public int Height;
    public int Step;
    public int VisibleSamples;
    public double MagentaRatio;
    public double GreenRatio;
    public double TopBandMagentaRatio;
    public double TopBandGreenRatio;
    public double BottomBandMagentaRatio;
    public double BottomBandGreenRatio;
    public double MaxTileMagentaRatio;
    public double MaxTileGreenRatio;
    public double MeanMagentaAxis;
    public double MeanGreenAxis;
    public string Verdict;
    public string CaptureInvalidReason;
}

public static class MlvGuiSmokeColorArtifactScanner
{
    public static MlvGuiSmokeColorArtifactScanResult Scan(string path)
    {
        using (var source = new Bitmap(path))
        using (var bitmap = new Bitmap(source.Width, source.Height, PixelFormat.Format24bppRgb))
        using (var graphics = Graphics.FromImage(bitmap))
        {
            graphics.DrawImage(source, 0, 0, source.Width, source.Height);
            int width = bitmap.Width;
            int height = bitmap.Height;

            const int MinValidWidth = 320;
            const int MinValidHeight = 180;
            if (width < MinValidWidth || height < MinValidHeight)
            {
                return new MlvGuiSmokeColorArtifactScanResult
                {
                    Path = path,
                    Width = width,
                    Height = height,
                    Verdict = "capture-invalid",
                    CaptureInvalidReason = string.Format(
                        "capture {0}x{1} is smaller than the minimum sane size {2}x{3}",
                        width, height, MinValidWidth, MinValidHeight)
                };
            }

            int step = Math.Max(1, Math.Min(width, height) / 512);
            if (step < 2) step = 2;

            const int tileCols = 16;
            const int tileRows = 12;
            int[,] tileTotal = new int[tileRows, tileCols];
            int[,] tileMagenta = new int[tileRows, tileCols];
            int[,] tileGreen = new int[tileRows, tileCols];

            int visible = 0;
            int magenta = 0;
            int green = 0;
            int topVisible = 0;
            int topMagenta = 0;
            int topGreen = 0;
            int bottomVisible = 0;
            int bottomMagenta = 0;
            int bottomGreen = 0;
            double magentaAxisSum = 0.0;
            double greenAxisSum = 0.0;
            int sampleCount = 0;
            byte minR = 255, maxR = 0, minG = 255, maxG = 0, minB = 255, maxB = 0;

            Rectangle rect = new Rectangle(0, 0, width, height);
            BitmapData data = bitmap.LockBits(rect, ImageLockMode.ReadOnly, PixelFormat.Format24bppRgb);
            try
            {
                int stride = data.Stride;
                int bytes = Math.Abs(stride) * height;
                byte[] buffer = new byte[bytes];
                Marshal.Copy(data.Scan0, buffer, 0, bytes);

                for (int y = 0; y < height; y += step)
                {
                    int row = y * stride;
                    bool topBand = y < height / 10;
                    bool bottomBand = y >= (height * 9) / 10;
                    int tileY = Math.Min(tileRows - 1, (int)((long)y * tileRows / Math.Max(1, height)));
                    for (int x = 0; x < width; x += step)
                    {
                        int offset = row + x * 3;
                        if (offset < 0 || offset + 2 >= buffer.Length) continue;
                        byte b = buffer[offset + 0];
                        byte g = buffer[offset + 1];
                        byte r = buffer[offset + 2];
                        sampleCount++;
                        if (r < minR) minR = r;
                        if (r > maxR) maxR = r;
                        if (g < minG) minG = g;
                        if (g > maxG) maxG = g;
                        if (b < minB) minB = b;
                        if (b > maxB) maxB = b;
                        double luminance = (r + g + b) / 3.0;
                        if (luminance < 24.0) continue;

                        double magentaAxis = ((r + b) * 0.5) - g;
                        double greenAxis = g - ((r + b) * 0.5);
                        bool magentaHit = magentaAxis > 45.0 && r > 80 && b > 80 && (r - g) > 35 && (b - g) > 20;
                        bool greenHit = greenAxis > 45.0 && g > 80 && (g - r) > 35 && (g - b) > 20;

                        visible++;
                        magentaAxisSum += magentaAxis;
                        greenAxisSum += greenAxis;
                        if (magentaHit) magenta++;
                        if (greenHit) green++;

                        if (topBand)
                        {
                            topVisible++;
                            if (magentaHit) topMagenta++;
                            if (greenHit) topGreen++;
                        }
                        if (bottomBand)
                        {
                            bottomVisible++;
                            if (magentaHit) bottomMagenta++;
                            if (greenHit) bottomGreen++;
                        }

                        int tileX = Math.Min(tileCols - 1, (int)((long)x * tileCols / Math.Max(1, width)));
                        tileTotal[tileY, tileX]++;
                        if (magentaHit) tileMagenta[tileY, tileX]++;
                        if (greenHit) tileGreen[tileY, tileX]++;
                    }
                }
            }
            finally
            {
                bitmap.UnlockBits(data);
            }

            double maxTileMagenta = 0.0;
            double maxTileGreen = 0.0;
            for (int ty = 0; ty < tileRows; ++ty)
            {
                for (int tx = 0; tx < tileCols; ++tx)
                {
                    int total = tileTotal[ty, tx];
                    if (total <= 0) continue;
                    maxTileMagenta = Math.Max(maxTileMagenta, (double)tileMagenta[ty, tx] / total);
                    maxTileGreen = Math.Max(maxTileGreen, (double)tileGreen[ty, tx] / total);
                }
            }

            double magentaRatio = visible > 0 ? (double)magenta / visible : 0.0;
            double greenRatio = visible > 0 ? (double)green / visible : 0.0;
            double topMagentaRatio = topVisible > 0 ? (double)topMagenta / topVisible : 0.0;
            double topGreenRatio = topVisible > 0 ? (double)topGreen / topVisible : 0.0;
            double bottomMagentaRatio = bottomVisible > 0 ? (double)bottomMagenta / bottomVisible : 0.0;
            double bottomGreenRatio = bottomVisible > 0 ? (double)bottomGreen / bottomVisible : 0.0;

            const double BandRatioThreshold = 0.12;
            const double TileRatioThreshold = 0.35;
            const double TileSupportRatioThreshold = 0.10;
            const double GlobalRatioThreshold = 0.12;
            const double GlobalArtifactRatioThreshold = 0.18;
            bool barSuspect = topMagentaRatio > BandRatioThreshold || topGreenRatio > BandRatioThreshold ||
                              bottomMagentaRatio > BandRatioThreshold || bottomGreenRatio > BandRatioThreshold;
            bool tileSuspect = maxTileMagenta > TileRatioThreshold || maxTileGreen > TileRatioThreshold;
            bool globalArtifactSuspect = magentaRatio > GlobalArtifactRatioThreshold ||
                                         greenRatio > GlobalArtifactRatioThreshold;
            bool globalSuspect = magentaRatio > GlobalRatioThreshold || greenRatio > GlobalRatioThreshold;
            bool tileSupportedByFrame = globalSuspect ||
                                        topMagentaRatio > TileSupportRatioThreshold ||
                                        topGreenRatio > TileSupportRatioThreshold ||
                                        bottomMagentaRatio > TileSupportRatioThreshold ||
                                        bottomGreenRatio > TileSupportRatioThreshold;
            bool blockSuspect = tileSuspect && tileSupportedByFrame;

            // A capture whose sampled pixels never move (a flat clear color, a blank
            // placeholder) is not evidence of a clean frame -- it is evidence nothing was
            // actually read back. Guard this ahead of the artifact heuristics so a uniform
            // image can never fall through to "clear-heuristic".
            const int UniformChannelRangeThreshold = 2;
            bool uniformImage = sampleCount > 0 &&
                (maxR - minR) <= UniformChannelRangeThreshold &&
                (maxG - minG) <= UniformChannelRangeThreshold &&
                (maxB - minB) <= UniformChannelRangeThreshold;

            string verdict;
            string captureInvalidReason = null;
            if (uniformImage)
            {
                verdict = "capture-invalid";
                captureInvalidReason = string.Format(
                    "capture is uniform: sampled channel ranges R={0} G={1} B={2} over {3} samples",
                    maxR - minR, maxG - minG, maxB - minB, sampleCount);
            }
            else
            {
                verdict = (barSuspect || blockSuspect || globalArtifactSuspect) ? "suspect-block-or-bar" :
                          globalSuspect ? "global-color-axis-present" :
                          tileSuspect ? "localized-color-axis-present" :
                          "clear-heuristic";
            }

            return new MlvGuiSmokeColorArtifactScanResult {
                Path = path,
                Width = width,
                Height = height,
                Step = step,
                VisibleSamples = visible,
                MagentaRatio = Math.Round(magentaRatio, 6),
                GreenRatio = Math.Round(greenRatio, 6),
                TopBandMagentaRatio = Math.Round(topMagentaRatio, 6),
                TopBandGreenRatio = Math.Round(topGreenRatio, 6),
                BottomBandMagentaRatio = Math.Round(bottomMagentaRatio, 6),
                BottomBandGreenRatio = Math.Round(bottomGreenRatio, 6),
                MaxTileMagentaRatio = Math.Round(maxTileMagenta, 6),
                MaxTileGreenRatio = Math.Round(maxTileGreen, 6),
                MeanMagentaAxis = visible > 0 ? Math.Round(magentaAxisSum / visible, 3) : 0.0,
                MeanGreenAxis = visible > 0 ? Math.Round(greenAxisSum / visible, 3) : 0.0,
                CaptureInvalidReason = captureInvalidReason,
                Verdict = verdict
            };
        }
    }
}
"@
}

function Get-ScreenshotColorArtifactScan {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{
            requested = $false
            path = $Path
            verdict = "not-captured"
            error = $null
        }
    }

    try {
        Add-ColorArtifactScannerType
        $resolvedPath = (Resolve-Path -LiteralPath $Path).Path
        $scan = [MlvGuiSmokeColorArtifactScanner]::Scan($resolvedPath)
        [pscustomobject]@{
            requested = $true
            path = $scan.Path
            width = $scan.Width
            height = $scan.Height
            step = $scan.Step
            visibleSamples = $scan.VisibleSamples
            verdict = $scan.Verdict
            captureInvalidReason = $scan.CaptureInvalidReason
            magentaRatio = $scan.MagentaRatio
            greenRatio = $scan.GreenRatio
            topBandMagentaRatio = $scan.TopBandMagentaRatio
            topBandGreenRatio = $scan.TopBandGreenRatio
            bottomBandMagentaRatio = $scan.BottomBandMagentaRatio
            bottomBandGreenRatio = $scan.BottomBandGreenRatio
            maxTileMagentaRatio = $scan.MaxTileMagentaRatio
            maxTileGreenRatio = $scan.MaxTileGreenRatio
            meanMagentaAxis = $scan.MeanMagentaAxis
            meanGreenAxis = $scan.MeanGreenAxis
            thresholds = [pscustomobject]@{
                bandRatio = 0.12
                tileRatio = 0.35
                tileSupportRatio = 0.10
                globalRatio = 0.12
                globalArtifactRatio = 0.18
                verdictsThatFailWhenRequested = @("suspect-block-or-bar", "scan-error", "capture-invalid")
            }
            note = "Sampled presented-frame screenshot scan for magenta/pink/green bars, tinted blocks, and severe global color-axis spikes; isolated high-saturation tiles are informational unless supported by band/global evidence."
            error = $null
        }
    }
    catch {
        [pscustomobject]@{
            requested = $true
            path = $Path
            verdict = "scan-error"
            error = $_.Exception.Message
        }
    }
}
